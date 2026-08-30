"""VeriTMM v0.6 call adapter for the Article harness (T-01).

Upper pipeline stages never touch VeriTMM internals directly; every call goes
through VeriTMMAdapter.run_simulation().

ADR (CLI vs Python API): we call the installed Python API
(tmm_engine.managed_execution.execute_managed_task), NOT a subprocess CLI.
Rationale: the shipped CLI itself is a thin wrapper over that exact entry
point (tmm_engine/cli.py), so behaviour is equivalent by construction, while
in-process calls avoid JSON round-trips/codepage issues on Windows and expose
typed exceptions directly. The existing harness modules (orchestrator,
solver_registry, material_service, ...) already import tmm_engine directly,
so this keeps one consistent integration style.

Fail-closed contract: any engine exception, missing certificate, or
non-accepted certificate maps to VeriTMMResult(certified=False,
tightest_margin=-1.0). This module never judges physics -- it only relays
what PHYSICS_ACCEPTANCE_CERTIFICATE.json declares.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

CERTIFICATE_FILENAME = "PHYSICS_ACCEPTANCE_CERTIFICATE.json"
RUN_RESULT_FILENAME = "RUN_RESULT.json"
RESULT_SUMMARY_FILENAME = "RESULT_SUMMARY.json"


@dataclass
class VeriTMMResult:
    """Normalized view of one VeriTMM invocation."""

    certificate_path: Path          # PHYSICS_ACCEPTANCE_CERTIFICATE.json path
    certified: bool                 # certificate["accepted"]
    tightest_margin: float          # (threshold - observed) / threshold
    raw_outputs: dict[str, Any] = field(default_factory=dict)  # passthrough
    cpu_seconds: float = 0.0        # measured wall time of the engine call
    outcome: str = "certified"      # certified | physics_rejected | engine_error | budget_blocked


def _read_json_if_exists(path: Path) -> dict[str, Any] | None:
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return None


def _installed_veritmm_root() -> Path | None:
    """Locate the installed veritmm/ checkout (handoff root sibling)."""
    candidate = Path(__file__).resolve().parents[3] / "veritmm"
    if (candidate / "tmm_engine" / "__init__.py").is_file():
        return candidate
    return None


def _ensure_real_veritmm_import() -> None:
    r"""Force 'tmm_engine' to resolve to the installed veritmm/ package.

    code/tmm_engine/ is a deprecated snapshot, yet because it sits inside the
    working directory it SHADOWS the pip-editable install whenever sys.path
    contains cwd (python -m pytest from code/, plain scripts, ...). The
    warm-up contract mandates the installed package, so this guard prepends
    the veritmm/ root to sys.path and evicts any already-imported stale
    tmm_engine modules so the real engine loads.
    """
    import sys

    root = _installed_veritmm_root()
    if root is None:
        return
    root_str = str(root)
    while root_str in sys.path:
        sys.path.remove(root_str)
    sys.path.insert(0, root_str)

    loaded = sys.modules.get("tmm_engine")
    if loaded is None:
        return
    loaded_file = str(getattr(loaded, "__file__", "") or "")
    if loaded_file and str(root) in str(Path(loaded_file).resolve()):
        return  # already the real installed engine
    stale = [
        name
        for name in list(sys.modules)
        if name == "tmm_engine" or name.startswith("tmm_engine.")
    ]
    for name in stale:
        del sys.modules[name]


def _execute_veritmm(
    mode: str, task_payload: Mapping[str, Any], output_dir: Path
) -> dict[str, Any]:
    """Real engine seam: convert a JSON payload and run one managed task.

    Isolated so tests can monkeypatch this single symbol instead of importing
    the numerical stack.
    """
    _ensure_real_veritmm_import()
    from tmm_engine.managed_execution import execute_managed_task
    from tmm_engine.task_io import (
        optimization_task_from_dict,
        simulation_task_from_dict,
    )

    if mode == "simulate":
        task = simulation_task_from_dict(task_payload)
    elif mode == "optimize":
        task = optimization_task_from_dict(task_payload)
    else:
        raise ValueError(f"Unsupported VeriTMM mode: {mode!r}")
    return execute_managed_task(mode, task, output_dir=output_dir, detail="compact")


def _parse_tightest_margin(certificate: Mapping[str, Any]) -> float:
    raw = certificate.get("tightest_margin")
    if isinstance(raw, Mapping):
        try:
            return float(raw.get("normalized_margin", 1.0))
        except (TypeError, ValueError):
            return -1.0
    if isinstance(raw, (int, float)):
        return float(raw)
    # Key absent: VeriTMM engine convention defaults to 1.0 (full margin).
    # Returning -1.0 here would cause barely_passed to trigger incorrectly
    # for successful runs that omit the field, forcing spurious
    # mandatory_validation_pending=True in the stop controller.
    return 1.0


class VeriTMMAdapter:
    """Wrap every VeriTMM interaction behind one fail-closed interface."""

    def __init__(self, cost_tracker):
        """cost_tracker: CostTracker instance (from get_cost_tracker())."""
        self._tracker = cost_tracker

    def _record_cpu_usage(self, cpu_seconds: float) -> None:
        # T-00 CostTracker exposes record_tmm_usage; tolerate trackers that
        # only implement the alternative record_veritmm_usage spelling.
        recorder = getattr(self._tracker, "record_tmm_usage", None) or getattr(
            self._tracker, "record_veritmm_usage", None
        )
        if recorder is not None:
            recorder(cpu_seconds)

    def run_simulation(self, task_spec: dict, output_dir: Path) -> VeriTMMResult:
        """Run one VeriTMM task and normalize its outputs.

        Writes PHYSICS_ACCEPTANCE_CERTIFICATE.json (produced by the engine),
        records consumed CPU seconds on the CostTracker, and returns a
        VeriTMMResult. Never raises for engine failures -- those become
        certified=False results.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        certificate_path = output_dir / CERTIFICATE_FILENAME

        spec = dict(task_spec or {})
        mode = str(spec.get("mode") or spec.get("operation") or "simulate").lower()
        inner = spec.get("task")
        payload = dict(inner) if isinstance(inner, Mapping) else spec

        started = time.perf_counter()
        try:
            envelope = _execute_veritmm(mode, payload, output_dir)
        except Exception as exc:  # fail-closed: no physics judgement here
            elapsed = time.perf_counter() - started
            self._record_cpu_usage(elapsed)
            return VeriTMMResult(
                certificate_path=certificate_path,
                certified=False,
                tightest_margin=-1.0,
                raw_outputs={
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                cpu_seconds=elapsed,
                outcome="engine_error",
            )
        elapsed = time.perf_counter() - started
        self._record_cpu_usage(elapsed)

        raw_outputs: dict[str, Any] = {"envelope": envelope}
        summary = _read_json_if_exists(output_dir / RESULT_SUMMARY_FILENAME)
        if summary is not None:
            raw_outputs[RESULT_SUMMARY_FILENAME] = summary
        run_result = _read_json_if_exists(output_dir / RUN_RESULT_FILENAME)
        if run_result is not None:
            raw_outputs[RUN_RESULT_FILENAME] = run_result

        certificate = _read_json_if_exists(certificate_path)
        if certificate is None:
            raw_outputs["error"] = (
                f"{CERTIFICATE_FILENAME} was not produced by VeriTMM; "
                "refusing to certify (fail-closed)"
            )
            return VeriTMMResult(
                certificate_path=certificate_path,
                certified=False,
                tightest_margin=-1.0,
                raw_outputs=raw_outputs,
                cpu_seconds=elapsed,
                outcome="engine_error",
            )

        raw_outputs[CERTIFICATE_FILENAME] = certificate
        certified = bool(certificate.get("accepted", False))
        outcome = "certified" if certified else "physics_rejected"
        return VeriTMMResult(
            certificate_path=certificate_path,
            certified=certified,
            tightest_margin=_parse_tightest_margin(certificate),
            raw_outputs=raw_outputs,
            cpu_seconds=elapsed,
            outcome=outcome,
        )


def bounded_run(
    adapter: VeriTMMAdapter,
    task_spec: dict,
    output_dir: Path,
    budget_snapshot,
    *,
    max_cpu_seconds: float | None = None,
) -> VeriTMMResult:
    """Run adapter.run_simulation unless the TMM budget is already exhausted.

    budget_snapshot is a RunBudgetSnapshot-like object exposing either
    tmm_cpu_seconds or veritmm_cpu_seconds. When max_cpu_seconds is provided
    and the recorded usage has reached it, return a fail-closed
    VeriTMMResult without invoking VeriTMM at all.
    """
    output_dir = Path(output_dir)
    used: float | None = None
    for attr in ("tmm_cpu_seconds", "veritmm_cpu_seconds"):
        value = getattr(budget_snapshot, attr, None)
        if value is not None:
            used = float(value)
            break
    if max_cpu_seconds is not None and used is not None and used >= float(max_cpu_seconds):
        return VeriTMMResult(
            certificate_path=output_dir / CERTIFICATE_FILENAME,
            certified=False,
            tightest_margin=-1.0,
            raw_outputs={
                "budget_gate": {
                    "blocked": True,
                    "reason": "veritmm_budget_exhausted",
                    "used_cpu_seconds": used,
                    "max_cpu_seconds": float(max_cpu_seconds),
                }
            },
            cpu_seconds=0.0,
            outcome="budget_blocked",
        )
    return adapter.run_simulation(task_spec, output_dir)


def is_route_eliminable(result: VeriTMMResult) -> bool:
    """Red line 7: only physics_rejected eliminates a route from the tournament.

    engine_error is retriable; budget_blocked means it never ran.
    Neither disqualifies the scientific hypothesis.
    """
    return result.outcome == "physics_rejected"

def _batch_worker(job: tuple[str, str, str]) -> dict[str, Any]:
    """Spawn-safe module-level worker: one pure-data VeriTMM execution.

    Receives (mode, payload_json, output_dir_str); returns a pure dict.
    CPU time uses time.process_time() so the PARENT can sum per-task CPU
    without charging parallel wall clock as cost. Never raises: every
    failure mode lands in the returned dict as engine_error.
    """
    import os
    import time

    mode, payload_json, output_dir_str = job
    output_dir = Path(output_dir_str)
    output_dir.mkdir(parents=True, exist_ok=True)
    certificate_path = output_dir / CERTIFICATE_FILENAME
    # Standalone direct calls get the pinned values too (idempotent); the
    # pool initializer has already set them for pool children.
    for name, value in _BLAS_ENV_VARS:
        os.environ.setdefault(name, value)
    # Diagnostics: prove to callers (and tests) which BLAS pinning the child
    # actually ran under.
    blas_env = {name: os.environ.get(name) for name, _ in _BLAS_ENV_VARS}
    cpu_started = time.process_time()
    wall_started = time.perf_counter()
    try:
        payload = json.loads(payload_json)
        envelope = _execute_veritmm(mode, payload, output_dir)
    except Exception as exc:
        return {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
            "cpu_seconds": time.process_time() - cpu_started,
            "wall_seconds": time.perf_counter() - wall_started,
            "certificate_path": str(certificate_path),
            "blas_env": blas_env,
        }
    cpu_seconds = time.process_time() - cpu_started
    wall_seconds = time.perf_counter() - wall_started
    raw_outputs: dict[str, Any] = {"envelope": envelope}
    summary = _read_json_if_exists(output_dir / RESULT_SUMMARY_FILENAME)
    if summary is not None:
        raw_outputs[RESULT_SUMMARY_FILENAME] = summary
    run_result = _read_json_if_exists(output_dir / RUN_RESULT_FILENAME)
    if run_result is not None:
        raw_outputs[RUN_RESULT_FILENAME] = run_result
    certificate = _read_json_if_exists(certificate_path)
    if certificate is None:
        raw_outputs["error"] = (
            f"{CERTIFICATE_FILENAME} was not produced by VeriTMM; "
            "refusing to certify (fail-closed)"
        )
        return {
            "ok": False,
            "error_type": "MissingCertificate",
            "error": raw_outputs["error"],
            "cpu_seconds": cpu_seconds,
            "wall_seconds": wall_seconds,
            "certificate_path": str(certificate_path),
            "raw_outputs": raw_outputs,
            "blas_env": blas_env,
        }
    return {
        "ok": True,
        "certified": bool(certificate.get("accepted", False)),
        "tightest_margin": _parse_tightest_margin(certificate),
        "cpu_seconds": cpu_seconds,
        "wall_seconds": wall_seconds,
        "certificate_path": str(certificate_path),
        "raw_outputs": {**raw_outputs, CERTIFICATE_FILENAME: certificate},
        "blas_env": blas_env,
    }


def _budget_used_seconds(budget_snapshot: Any) -> float | None:
    used: float | None = None
    if budget_snapshot is not None:
        for attr in ("tmm_cpu_seconds", "veritmm_cpu_seconds"):
            value = getattr(budget_snapshot, attr, None)
            if value is None and isinstance(budget_snapshot, Mapping):
                value = budget_snapshot.get(attr)
            if value is not None:
                used = float(value)
                break
    return used


def batch_run(
    tasks: list[tuple[dict, Path]],
    *,
    budget_snapshot: Any = None,
    max_cpu_seconds: float | None = None,
    _worker_fn: Any = None,
) -> list[VeriTMMResult]:
    """Run N VeriTMM tasks in a process pool; results in INPUT ORDER.

    R-08 primitive. Process isolation is mandatory:
    _ensure_real_veritmm_import() mutates sys.modules, which is unsafe
    across threads, and the numeric core is CPU-bound so threads would not
    help anyway.

    Contract:
      * tasks: [(task_spec_dict, output_dir)]; task_spec mirrors the
        VeriTMMAdapter.run_simulation input ({mode|operation, task}).
      * returns one VeriTMMResult PER TASK, aligned to input order.
      * fewer than two tasks run inline serially (spawn overhead dominates).
      * budget exhausted (bounded_run snapshot protocol) => every task comes
        back budget_blocked and NO child process starts.
      * one crashed task => engine_error for that task; siblings unaffected.
      * BrokenProcessPool => remaining tasks degrade to SERIAL execution;
        tasks whose output_dir already holds a certificate are NOT rerun.
      * children report time.process_time(); the parent sums those into ONE
        record_tmm_usage call on the shared CostTracker. Parallel wall clock
        is NEVER charged as cost (that would understate the budget).
      * _worker_fn replaces the child callable in-process (test seam); the
        degraded-serial path uses it too when injected.
    """
    import os
    from concurrent.futures import ProcessPoolExecutor

    from config.qwen_config import get_cost_tracker

    normalized: list[tuple[str, str, Path]] = []
    for spec, out_dir in tasks:
        spec_dict = dict(spec or {})
        mode = str(spec_dict.get("mode") or spec_dict.get("operation") or "simulate").lower()
        inner = spec_dict.get("task")
        payload = dict(inner) if isinstance(inner, Mapping) else spec_dict
        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        normalized.append((mode, json.dumps(payload, ensure_ascii=False), out_path))

    used = _budget_used_seconds(budget_snapshot)
    if max_cpu_seconds is not None and used is not None and used >= float(max_cpu_seconds):
        blocked: list[VeriTMMResult] = []
        for _mode, _payload_json, out_dir in normalized:
            blocked.append(
                VeriTMMResult(
                    certificate_path=out_dir / CERTIFICATE_FILENAME,
                    certified=False,
                    tightest_margin=-1.0,
                    raw_outputs={
                        "budget_gate": {
                            "blocked": True,
                            "reason": "veritmm_budget_exhausted",
                            "used_cpu_seconds": used,
                            "max_cpu_seconds": float(max_cpu_seconds),
                        }
                    },
                    cpu_seconds=0.0,
                    outcome="budget_blocked",
                )
            )
        return blocked

    worker = _worker_fn if _worker_fn is not None else _batch_worker

    def to_result(payload: dict[str, Any], out_dir: Path) -> VeriTMMResult:
        certificate_path = Path(
            payload.get("certificate_path") or (out_dir / CERTIFICATE_FILENAME)
        )
        wall = float(payload.get("wall_seconds") or 0.0)
        if not payload.get("ok"):
            raw = dict(payload.get("raw_outputs") or {})
            raw["wall_seconds_elapsed"] = wall
            if "blas_env" in payload:
                raw["blas_env"] = payload["blas_env"]
            raw.setdefault("error_type", str(payload.get("error_type") or "engine_error"))
            return VeriTMMResult(
                certificate_path=certificate_path,
                certified=False,
                tightest_margin=-1.0,
                raw_outputs=raw,
                cpu_seconds=float(payload.get("cpu_seconds") or 0.0),
                outcome="engine_error",
            )
        return _normalize_certificate_result(
            payload["raw_outputs"][CERTIFICATE_FILENAME],
            payload["raw_outputs"],
            float(payload.get("cpu_seconds") or 0.0),
            wall,
            certificate_path,
        )

    results: list[VeriTMMResult | None] = [None] * len(normalized)
    cpu_total = 0.0
    pending: list[int] = list(range(len(normalized)))

    use_pool = len(normalized) >= 2 and _worker_fn is None
    if use_pool:
        max_workers = min(
            len(normalized),
            max(1, (os.cpu_count() or 2) - 1),
            MAX_POOL_WORKERS,
        )
        pool_failed = False
        try:
            with ProcessPoolExecutor(
                max_workers=max_workers,
                initializer=_batch_pool_initializer,
            ) as pool:
                futures = [
                    (index, pool.submit(_batch_worker, job))
                    for index, job in enumerate(normalized)
                ]
                for index, future in futures:
                    payload = future.result()
                    results[index] = to_result(payload, normalized[index][2])
                    cpu_total += float(payload.get("cpu_seconds") or 0.0)
        except Exception:
            # BrokenExecutor or any pool-level collapse: whatever results are
            # still missing degrades to SERIAL execution below.
            pool_failed = True
        if pool_failed:
            pending = [i for i, res in enumerate(results) if res is None]
        else:
            pending = []

    for index in pending:
        mode, payload_json, out_dir = normalized[index]
        evidence = _read_outcome_evidence(out_dir)
        if evidence is not None and _worker_fn is None:
            # Completed in an earlier attempt: normalize WITHOUT rerunning.
            results[index] = _normalize_certificate_result(
                evidence["certificate"],
                evidence["raw_outputs"],
                0.0,
                0.0,
                out_dir / CERTIFICATE_FILENAME,
            )
            continue
        try:
            payload = worker((mode, payload_json, str(out_dir)))
        except Exception as exc:
            payload = {
                "ok": False,
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
                "cpu_seconds": 0.0,
                "wall_seconds": 0.0,
                "certificate_path": str(out_dir / CERTIFICATE_FILENAME),
            }
        results[index] = to_result(payload, out_dir)
        cpu_total += float(payload.get("cpu_seconds") or 0.0)

    if cpu_total > 0.0:
        try:
            tracker = get_cost_tracker()
            recorder = getattr(tracker, "record_tmm_usage", None) or getattr(
                tracker, "record_veritmm_usage", None
            )
            if recorder is not None:
                recorder(cpu_total)
        except Exception:
            # Metering must never turn finished compute into a failure.
            pass
    return [res for res in results if res is not None]

# ---------------------------------------------------------------------------
# R-08: process-pool batch execution primitive
# ---------------------------------------------------------------------------

MAX_POOL_WORKERS = 4

# Set in the pool initializer BEFORE the child imports numpy, so BLAS/OpenMP
# backends spawn one thread per PROCESS instead of one per pool worker times
# core count (CPU oversubscription). Windows spawn runs the initializer
# before re-importing the worker module, which is exactly the right order.
_BLAS_ENV_VARS = (
    ("OMP_NUM_THREADS", "1"),
    ("MKL_NUM_THREADS", "1"),
    ("OPENBLAS_NUM_THREADS", "1"),
    ("NUMEXPR_NUM_THREADS", "1"),
)


def _batch_pool_initializer() -> None:
    """Pool initializer: pin BLAS threading before numpy loads."""
    import os

    for name, value in _BLAS_ENV_VARS:
        os.environ[name] = value


def _read_outcome_evidence(output_dir: Path) -> dict[str, Any] | None:
    """Return completed-run evidence for output_dir, or None.

    A run counts as COMPLETE when the engine already produced its artifact
    set in a previous attempt (degraded-serial reruns must not redo work).
    The certificate file is the authoritative completion marker.
    """
    certificate = _read_json_if_exists(Path(output_dir) / CERTIFICATE_FILENAME)
    if certificate is None:
        return None
    raw: dict[str, Any] = {}
    summary = _read_json_if_exists(Path(output_dir) / RESULT_SUMMARY_FILENAME)
    if summary is not None:
        raw[RESULT_SUMMARY_FILENAME] = summary
    run_result = _read_json_if_exists(Path(output_dir) / RUN_RESULT_FILENAME)
    if run_result is not None:
        raw[RUN_RESULT_FILENAME] = run_result
    raw[CERTIFICATE_FILENAME] = certificate
    return {"certificate": certificate, "raw_outputs": raw}


def _normalize_certificate_result(
    certificate: Mapping[str, Any],
    raw_outputs: dict[str, Any],
    cpu_seconds: float,
    wall_seconds: float,
    certificate_path: Path,
) -> VeriTMMResult:
    certified = bool(certificate.get("accepted", False))
    outcome = "certified" if certified else "physics_rejected"
    raw_outputs = dict(raw_outputs)
    raw_outputs["wall_seconds_elapsed"] = wall_seconds
    return VeriTMMResult(
        certificate_path=certificate_path,
        certified=certified,
        tightest_margin=_parse_tightest_margin(certificate),
        raw_outputs=raw_outputs,
        cpu_seconds=cpu_seconds,
        outcome=outcome,
    )
