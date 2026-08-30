"""Run one user-selected sealed TMM benchmark through the complete Harness.

The real holdout file is read only after explicit command-line and environment
authorization.  A persistent audit event is written before the file is opened.
Interrupted runs resume from the saved selected benchmark and compiled task,
without reading the sealed split again.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optomind_research.runtime.artifact_store import atomic_write_json  # noqa: E402
from optomind_research.runtime.cost_ledger import estimate_call_cost_cny  # noqa: E402
from optomind_optics.harness.benchmarks import (  # noqa: E402
    DEFAULT_HOLDOUT_AUDIT_LOG,
    BenchmarkTask,
    holdout_access_events,
    load_benchmark_task,
)
from optomind_optics.harness.benchmark_delivery import (  # noqa: E402
    audit_benchmark_delivery,
)
from optomind_optics.harness.design_task import OpticalDesignTask  # noqa: E402
from optomind_optics.harness.orchestrator import (  # noqa: E402
    TMMHarnessConfig,
    TMMHarnessOrchestrator,
)
from optomind_optics.harness.replay import (  # noqa: E402
    reassess_existing_replay,
    replay_completed_run,
)
from optomind_optics.harness.task_compiler import QwenTMMTaskCompiler  # noqa: E402


AUTHORIZATION_TEXT = "I_AUTHORIZE_ONE_BLIND_TMM_HOLDOUT"
HOLDOUT_IDS = tuple(f"HOLDOUT{index:02d}" for index in range(6, 11))


def _normalized_blind_lock(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    normalized.pop("resume", None)
    return normalized


def _has_harness_run_state(simulation_dir: Path) -> bool:
    return (simulation_dir / "RUN_STATE.json").is_file()


def _usage_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    input_tokens = sum(int(row.get("estimated_input_tokens") or 0) for row in rows)
    output_tokens = sum(int(row.get("estimated_output_tokens") or 0) for row in rows)
    estimated_cost = sum(
        estimate_call_cost_cny(
            str(row.get("model_name") or "unknown"),
            int(row.get("estimated_input_tokens") or 0),
            int(row.get("estimated_output_tokens") or 0),
        )
        for row in rows
    )
    return {
        "call_count": len(rows),
        "estimated_input_tokens": input_tokens,
        "estimated_output_tokens": output_tokens,
        "estimated_cost_cny": estimated_cost,
        "models": sorted({str(row.get("model_name")) for row in rows if row.get("model_name")}),
        "fallback_used": any(bool(row.get("fallback_used") or row.get("model_fallback_used")) for row in rows),
    }


def _load_or_select_benchmark(root: Path, holdout_id: str, resume: bool) -> BenchmarkTask:
    saved = root / "SELECTED_BENCHMARK.json"
    if resume:
        if not saved.exists():
            raise FileNotFoundError("resume requires SELECTED_BENCHMARK.json")
        benchmark = BenchmarkTask.model_validate_json(saved.read_text(encoding="utf-8"))
        if benchmark.id != holdout_id:
            raise ValueError("resume holdout ID does not match saved benchmark")
        return benchmark
    benchmark = load_benchmark_task(holdout_id, allow_holdout=True)
    atomic_write_json(saved, benchmark.model_dump(mode="json"))
    return benchmark


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--holdout-id", required=True, choices=HOLDOUT_IDS)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--confirm", required=True, help=f"Must equal {AUTHORIZATION_TEXT}")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--disable-qwen-policy", action="store_true")
    parser.add_argument("--skip-fresh-replay", action="store_true")
    args = parser.parse_args()

    if args.confirm != AUTHORIZATION_TEXT:
        raise PermissionError("blind holdout confirmation text is incorrect")
    if os.getenv("OPTOMIND_ALLOW_TMM_HOLDOUT") != "1":
        raise PermissionError("set OPTOMIND_ALLOW_TMM_HOLDOUT=1 for one authorized blind run")

    root = args.output_dir.resolve()
    if root.exists() and not args.resume:
        raise FileExistsError("blind output directory already exists; use --resume, never overwrite")
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / "BLIND_RUN_LOCK.json"
    lock_payload = {
        "schema_version": "tmm-blind-run-lock.v1",
        "holdout_id": args.holdout_id,
        "authorization": AUTHORIZATION_TEXT,
        "audit_log": str(DEFAULT_HOLDOUT_AUDIT_LOG.resolve()),
    }
    if args.resume:
        if not lock_path.exists():
            raise FileNotFoundError("resume requires BLIND_RUN_LOCK.json")
        existing_lock = json.loads(lock_path.read_text(encoding="utf-8"))
        # The first released runner recorded the invocation-only ``resume``
        # flag in the immutable authorization lock.  It is not part of the
        # scientific or authorization identity, so accept and ignore it when
        # resuming an already-authorized one-shot blind run.
        if _normalized_blind_lock(existing_lock) != lock_payload:
            raise ValueError("resume authorization does not match BLIND_RUN_LOCK.json")
    else:
        atomic_write_json(lock_path, lock_payload)

    started = time.perf_counter()
    benchmark = _load_or_select_benchmark(root, args.holdout_id, bool(args.resume))
    compiled_path = root / "COMPILED_TASK.json"
    compilation_path = root / "TASK_COMPILATION.json"
    if args.resume and compiled_path.exists() and compilation_path.exists():
        task = OpticalDesignTask.model_validate_json(compiled_path.read_text(encoding="utf-8"))
        compilation_payload = json.loads(compilation_path.read_text(encoding="utf-8"))
    else:
        compilation = QwenTMMTaskCompiler().compile(
            benchmark.natural_language_question,
            benchmark=benchmark,
        )
        compilation_payload = compilation.model_dump(mode="json")
        atomic_write_json(compilation_path, compilation_payload)
        if compilation.status != "compiled" or compilation.task is None:
            report = {
                "schema_version": "tmm-holdout-acceptance.v1",
                "holdout_id": args.holdout_id,
                "status": compilation.status,
                "passed": False,
                "reason": compilation.rationale,
                "validation_errors": list(compilation.validation_errors),
                "wall_seconds": time.perf_counter() - started,
                "holdout_execution_started": False,
                "performance_targets_used_as_gates": False,
                "diversity_required": False,
            }
            atomic_write_json(root / "HOLDOUT_ACCEPTANCE.json", report)
            print(json.dumps(report, indent=2))
            return 3
        task = compilation.task
        atomic_write_json(compiled_path, task.model_dump(mode="json"))

    simulation_dir = root / "harness_run"
    result = TMMHarnessOrchestrator(
        simulation_dir,
        run_id=f"blind_{args.holdout_id.casefold()}",
        resume=bool(args.resume and _has_harness_run_state(simulation_dir)),
        config=TMMHarnessConfig(
            enable_global_optimizer=True,
            use_qwen_policy=not bool(args.disable_qwen_policy),
        ),
    ).run(task)
    delivery_audit = audit_benchmark_delivery(benchmark, task, simulation_dir)
    atomic_write_json(
        root / "BENCHMARK_DELIVERY_AUDIT.json",
        delivery_audit.model_dump(mode="json"),
    )
    replay = None if args.skip_fresh_replay else replay_completed_run(simulation_dir)
    replay_reassessed = False
    if replay is not None and not replay.success:
        replay = reassess_existing_replay(simulation_dir)
        replay_reassessed = True
    valid_candidates = sum(
        int(item.get("physically_valid_candidate_count", 0))
        for item in result.experiment_results
    )
    compiler_usage = list(compilation_payload.get("usage") or [])
    harness_usage = [dict(item) for item in result.qwen_usage]
    audit_events = [
        event
        for event in holdout_access_events()
        if event.get("requested_holdout_id") == args.holdout_id
    ]
    checks = {
        "completed_or_bounded_physics_outcome": result.status.startswith("completed"),
        "verified_candidate_exists": valid_candidates > 0,
        "performance_targets_are_ranking_only": all(
            "Target attainment affects ranking only" in note
            for item in result.experiment_results
            for note in item.get("portfolio", {}).get("notes", [])[:1]
        ),
        "qwen_model_lock": set(_usage_summary(compiler_usage + harness_usage)["models"])
        <= {"qwen3.7-flash"},
        "no_model_fallback": not _usage_summary(compiler_usage + harness_usage)["fallback_used"],
        "fresh_scientific_replay": replay is None or replay.success,
        "holdout_access_audited": any(
            event.get("event") == "holdout_read_completed" for event in audit_events
        ),
        "benchmark_semantics_delivered": delivery_audit.passed,
    }
    report = {
        "schema_version": "tmm-holdout-acceptance.v1",
        "holdout_id": args.holdout_id,
        "status": result.status,
        "passed": all(checks.values()),
        "checks": checks,
        "wall_seconds": time.perf_counter() - started,
        "valid_candidates": valid_candidates,
        "performance_targets_used_as_gates": False,
        "diversity_required": False,
        "qwen_usage": _usage_summary(compiler_usage + harness_usage),
        "budget": result.budget,
        "stop_decision": result.stop_decision,
        "fresh_replay": None if replay is None else replay.model_dump(mode="json"),
        "replay_reassessed_without_physics_rerun": replay_reassessed,
        "resumed_from_saved_task": bool(args.resume),
        "holdout_audit_event_count_for_selected_id": len(audit_events),
        "artifacts": {
            "selected_benchmark": "SELECTED_BENCHMARK.json",
            "compiled_task": "COMPILED_TASK.json",
            "task_compilation": "TASK_COMPILATION.json",
            "benchmark_delivery_audit": "BENCHMARK_DELIVERY_AUDIT.json",
            "harness_result": "harness_run/FINAL_RESULT.json",
            "original_replay_manifest": (
                None if replay is None else "harness_run/REPLAY_MANIFEST.json"
            ),
            "replay_reassessment": (
                "harness_run/REPLAY_REASSESSMENT.json" if replay_reassessed else None
            ),
        },
    }
    report_path = (
        root / "HOLDOUT_ACCEPTANCE_REASSESSED.json"
        if args.resume and (root / "HOLDOUT_ACCEPTANCE.json").exists()
        else root / "HOLDOUT_ACCEPTANCE.json"
    )
    atomic_write_json(report_path, report)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
