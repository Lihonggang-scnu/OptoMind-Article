"""Fresh-process scientific replay for completed TMM Harness runs.

Reconciliation uses a stable logical identity rather than raw physical
relative paths: each scientific file maps to ``experiment:<experiment_id>:
<normalized-subpath>`` (top-level files map to their filename).  The logical
experiment id comes from the run's own ``ARTIFACT_PATH_INDEX.json`` and the
subpath normalizes the reversible segment aliases (``b``/``baseline``,
``c``/``candidates``, ``m``/``material_scenarios``), so a source run in the
legacy ``experiments/<id>/baseline/...`` layout and a fresh replay in the
compact ``x/e_<sha>/b/...`` layout reconcile to the same key.  Embedded
artifact path references inside scientific JSON (for example portfolio
artifact ids) are canonicalized to the same logical form before hashing.
Exact content/scientific hashing is unchanged: tampered or missing replay
artifacts are still reported and fail the manifest.

Reconciliation is one-to-one and fails closed: duplicate experiment ids with
conflicting physical directories, duplicate physical directories assigned to
different experiment ids, unsafe/non-relative physical paths, and two
physical files collapsing to the same logical key raise before any success
can be reported, so a corrupt or rehashed run package never gets an
arbitrary winner.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from optomind_research.runtime.artifact_store import atomic_write_json

from .design_task import OpticalDesignTask
from .provenance import ArtifactLineageStore


_TOP_LEVEL_SCIENTIFIC = {
    "TASK.json",
    "HARNESS_CONFIG.json",
    "RUNTIME_LOCK.json",
    "MATERIAL_SCENARIOS.json",
    "MATERIAL_MANIFESTS.json",
    "DESIGN_PORTFOLIOS.json",
}
_EXPERIMENT_SCIENTIFIC = {
    "MATERIAL_MANIFEST.json",
    "SIMULATION_RESULT.json",
    "ANALYSIS_REPORT.json",
    "PHYSICS_ACCEPTANCE_CERTIFICATE.json",
    "OBJECTIVE_REPORT.json",
    "ROBUSTNESS.json",
    "MATERIAL_DATASET_UNCERTAINTY.json",
    "DESIGN_PORTFOLIO.json",
}
_VOLATILE_KEYS = {
    "wall_seconds",
    "wall_time_seconds",
    "elapsed_wall_time_seconds",
    "created_at",
    "created_at_unix",
    "updated_at_unix",
    "use_qwen_policy",
    "qwen_force_mock",
    "database_mtime_epoch",
    "database_path",
    "materials_dir",
    "python_executable",
    "platform",
}

_REPLAY_WORKER_ENV = "TMM_FRESH_REPLAY_WORKER"
_REPO_ROOT = Path(__file__).resolve().parents[3]


class ReplayArtifactCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    relative_path: str
    logical_path: str = ""
    source_sha256: str | None = None
    replay_sha256: str | None = None
    matched: bool
    reason: str


class ReplayManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "tmm-fresh-replay-manifest.v1"
    source_run_id: str
    replay_run_id: str
    scientific_source_relative: str = "."
    source_task_sha256: str
    replay_task_sha256: str
    qwen_disabled_for_replay: bool = True
    checks: tuple[ReplayArtifactCheck, ...] = ()
    matched_artifacts: int = 0
    total_artifacts: int = 0
    success: bool = False
    notes: tuple[str, ...] = Field(default_factory=tuple)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _scrub_volatile(value: Any, path: tuple[str, ...] = ()) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _scrub_volatile(item, path + (str(key),))
            for key, item in value.items()
            if str(key) not in _VOLATILE_KEYS
            and not (path and path[-1] == "qwen_policy" and str(key) == "enabled")
        }
    if isinstance(value, list):
        return [_scrub_volatile(item, path + ("[]",)) for item in value]
    return value


def _scientific_digest(
    path: Path,
    *,
    root: Path | None = None,
    path_index: dict[str, str] | None = None,
    candidate_identity: dict[str, tuple[str, str]] | None = None,
) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if root is not None and path_index is not None:
        payload = _canonicalize_path_references(
            payload, root, path_index, candidate_identity
        )
    canonical = json.dumps(
        _scrub_volatile(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return _sha256_bytes(canonical)


_SUBSEGMENT_ALIASES = {
    "b": "baseline",
    "c": "candidates",
    "m": "material_scenarios",
}


def _normalize_segment(segment: str) -> str:
    return _SUBSEGMENT_ALIASES.get(segment, segment)


def _validate_relative_physical(physical: str, experiment_id: str) -> None:
    if "\x00" in physical:
        raise ValueError(
            f"ARTIFACT_PATH_INDEX.json {experiment_id} physical_directory "
            "contains NUL"
        )
    if physical.startswith(("/", "\\")) or (
        len(physical) >= 2 and physical[1] == ":"
    ):
        raise ValueError(
            f"ARTIFACT_PATH_INDEX.json {experiment_id} physical_directory "
            f"{physical!r} must be relative"
        )
    path = Path(physical)
    if ".." in path.parts or path.is_absolute():
        raise ValueError(
            f"ARTIFACT_PATH_INDEX.json {experiment_id} physical_directory "
            f"{physical!r} must resolve under the run root"
        )


def _load_experiment_path_index(root: Path) -> dict[str, str]:
    """Return experiment_id -> physical relative directory for a run root.

    The orchestrator persists ARTIFACT_PATH_INDEX.json mapping every logical
    experiment id to the reversible on-disk directory (legacy
    ``experiments/<id>`` or compact ``x/e_<sha12>``).  Legacy runs without the
    index are inferred from any ``experiments/*`` directories.
    """

    index: dict[str, str] = {}
    reverse: dict[str, str] = {}
    path_index = root / "ARTIFACT_PATH_INDEX.json"
    if path_index.is_file():
        try:
            payload = json.loads(path_index.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(
                f"ARTIFACT_PATH_INDEX.json is malformed: {exc}"
            ) from exc
        rows = payload.get("experiments")
        if not isinstance(rows, list):
            raise ValueError(
                "ARTIFACT_PATH_INDEX.json experiments must be a list"
            )
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(
                    "ARTIFACT_PATH_INDEX.json experiment rows must be objects"
                )
            experiment_id = str(row.get("experiment_id") or "")
            physical = str(row.get("physical_directory") or "").strip()
            if not experiment_id:
                raise ValueError(
                    "ARTIFACT_PATH_INDEX.json row has empty experiment_id"
                )
            if not physical:
                raise ValueError(
                    f"ARTIFACT_PATH_INDEX.json {experiment_id} has empty "
                    "physical_directory"
                )
            _validate_relative_physical(physical, experiment_id)
            if experiment_id in index:
                if index[experiment_id] != physical:
                    raise ValueError(
                        f"duplicate experiment_id {experiment_id!r} with "
                        "conflicting physical directories"
                    )
                continue
            if physical in reverse and reverse[physical] != experiment_id:
                raise ValueError(
                    f"physical directory {physical!r} assigned to conflicting "
                    "experiment IDs"
                )
            index[experiment_id] = physical
            reverse[physical] = experiment_id
    else:
        # Legacy runs predate the path index: infer experiments/<id>.
        legacy = root / "experiments"
        if legacy.is_dir():
            for path in legacy.iterdir():
                if path.is_dir():
                    index[path.name] = f"experiments/{path.name}"
    return index


_CANDIDATE_IDENTITY_SCHEMA = "tmm-artifact-identity.v1"


def _load_candidate_identity(
    root: Path,
    index: dict[str, str],
) -> dict[str, tuple[str, str]]:
    """Strict one-to-one candidate physical dir -> (experiment_id, candidate_id).

    IDENTITY.json is authoritative only after validation: the declared
    physical_directory must match the file's actual location, the directory
    must belong to exactly one indexed experiment, the declared
    experiment_id must match that owner, and candidate ids must map
    one-to-one to physical directories.  Malformed, unsafe, or conflicting
    identity fails closed with an explicit error.
    """

    physical_to_experiment = {physical: exp for exp, physical in index.items()}
    candidate: dict[str, tuple[str, str]] = {}
    by_candidate_id: dict[str, str] = {}
    identity_paths: list[Path] = []
    for base_name in ("experiments", "x"):
        base = root / base_name
        if base.is_dir():
            identity_paths.extend(base.rglob("IDENTITY.json"))
    for identity_path in sorted(identity_paths):
        relative_dir = identity_path.parent.relative_to(root).as_posix()
        try:
            payload = json.loads(identity_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(
                f"IDENTITY.json is malformed at {relative_dir}: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise ValueError(
                f"IDENTITY.json at {relative_dir} must be a JSON object"
            )
        if payload.get("schema_version") != _CANDIDATE_IDENTITY_SCHEMA:
            raise ValueError(
                f"IDENTITY.json at {relative_dir} has unsupported "
                f"schema_version {payload.get('schema_version')!r}"
            )
        experiment_id = str(payload.get("experiment_id") or "").strip()
        candidate_id = str(payload.get("candidate_id") or "").strip()
        physical = str(payload.get("physical_directory") or "").strip()
        if not experiment_id or not candidate_id or not physical:
            raise ValueError(
                f"IDENTITY.json at {relative_dir} must declare "
                "experiment_id, candidate_id, and physical_directory"
            )
        _validate_relative_physical(physical, experiment_id)
        if physical != relative_dir:
            raise ValueError(
                f"IDENTITY.json at {relative_dir} declares physical_directory "
                f"{physical!r} that does not match its location"
            )
        owners = [
            exp
            for physical_dir, exp in physical_to_experiment.items()
            if relative_dir == physical_dir
            or relative_dir.startswith(physical_dir + "/")
        ]
        if len(owners) != 1:
            raise ValueError(
                f"IDENTITY.json at {relative_dir} has no single owning "
                "experiment in ARTIFACT_PATH_INDEX.json"
            )
        owner = owners[0]
        if owner != experiment_id:
            raise ValueError(
                f"IDENTITY.json at {relative_dir} declares experiment_id "
                f"{experiment_id!r} but its directory belongs to experiment "
                f"{owner!r}"
            )
        existing = candidate.get(relative_dir)
        if existing is not None and existing[1] != candidate_id:
            raise ValueError(
                f"candidate physical directory {relative_dir!r} assigned to "
                f"conflicting candidate ids {existing[1]!r} and "
                f"{candidate_id!r}"
            )
        if candidate_id in by_candidate_id:
            other = by_candidate_id[candidate_id]
            if other != relative_dir:
                raise ValueError(
                    f"duplicate candidate_id {candidate_id!r} with "
                    f"conflicting physical directories {other!r} and "
                    f"{relative_dir!r}"
                )
            continue
        candidate[relative_dir] = (owner, candidate_id)
        by_candidate_id[candidate_id] = relative_dir
    return candidate


def _logical_experiment_key(
    experiment_id: str,
    physical_dir: str,
    relative: str,
) -> str:
    under = relative[len(physical_dir):].lstrip("/")
    normalized = "/".join(_normalize_segment(part) for part in under.split("/"))
    return f"experiment:{experiment_id}:{normalized}"


def _logical_scientific_paths(
    root: Path,
    index: dict[str, str],
    candidate_identity: dict[str, tuple[str, str]] | None = None,
) -> dict[str, tuple[str, Path]]:
    """Map canonical logical key -> (physical relative path, Path).

    The canonical key uses the logical experiment id and normalized segment
    names plus the persisted candidate identity, so legacy
    ``experiments/<id>/baseline/...`` and compact ``x/e_<sha>/b/...``
    layouts of the same artifact produce the same key, and candidate files
    canonicalize to ``experiments/<id>/candidates/<candidate_id>/...`` for
    both the compact ``c/c_<hash>`` and the semantic
    ``candidates/<candidate_id>`` layouts.
    """

    if candidate_identity is None:
        candidate_identity = _load_candidate_identity(root, index)
    paths: dict[str, tuple[str, Path]] = {}
    for filename in _TOP_LEVEL_SCIENTIFIC:
        path = root / filename
        if path.exists():
            paths[filename] = (filename, path)
    physical_to_experiment = {physical: exp for exp, physical in index.items()}
    for base_name in ("experiments", "x"):
        base = root / base_name
        if not base.exists():
            continue
        for path in base.rglob("*.json"):
            if (
                path.name not in _EXPERIMENT_SCIENTIFIC
                and not path.name.startswith("OPTIMIZER_")
            ):
                continue
            relative = path.relative_to(root).as_posix()
            experiment_id = None
            physical_dir = None
            logical_key: str | None = None
            for candidate_dir, (exp_id, candidate_id) in (
                candidate_identity.items()
            ):
                if relative == candidate_dir or relative.startswith(
                    candidate_dir + "/"
                ):
                    under = relative[len(candidate_dir):].lstrip("/")
                    normalized = "/".join(
                        _normalize_segment(part)
                        for part in under.split("/")
                    )
                    logical_key = (
                        f"experiment:{exp_id}:candidates/{candidate_id}/"
                        f"{normalized}".rstrip("/")
                    )
                    experiment_id = exp_id
                    break
            if logical_key is not None:
                key = logical_key
            else:
                for physical, exp_id in physical_to_experiment.items():
                    if relative == physical or relative.startswith(
                        physical + "/"
                    ):
                        experiment_id = exp_id
                        physical_dir = physical
                        break
                if experiment_id is None and relative.startswith(
                    "experiments/"
                ):
                    candidate_exp = relative.split("/")[1]
                    if candidate_exp in index:
                        experiment_id = candidate_exp
                        physical_dir = f"experiments/{candidate_exp}"
                if experiment_id is None:
                    key = f"physical:{relative}"
                else:
                    key = _logical_experiment_key(
                        experiment_id,
                        physical_dir,
                        relative,
                    )
            if key in paths and paths[key][0] != relative:
                raise ValueError(
                    f"duplicate logical scientific artifact {key!r} from "
                    f"{paths[key][0]!r} and {relative!r}"
                )
            paths[key] = (relative, path)
    return paths


def _canonicalize_artifact_path(
    value: str,
    root: Path,
    index: dict[str, str],
    candidate_identity: dict[str, tuple[str, str]] | None = None,
) -> str:
    """Rewrite a physical artifact path reference to the canonical logical form.

    Only strings that begin with one of this run's known experiment physical
    directories (or a legacy ``experiments/<id>`` for a known experiment) are
    rewritten; candidate directories canonicalize to
    ``experiments/<id>/candidates/<candidate_id>/...`` in both layouts.  All
    other strings are returned unchanged.
    """

    if candidate_identity is None:
        candidate_identity = _load_candidate_identity(root, index)
    for candidate_dir, (experiment_id, candidate_id) in (
        candidate_identity.items()
    ):
        if value == candidate_dir or value.startswith(candidate_dir + "/"):
            under = value[len(candidate_dir):].lstrip("/")
            normalized = "/".join(
                _normalize_segment(part) for part in under.split("/")
            )
            return (
                f"experiments/{experiment_id}/candidates/{candidate_id}/"
                f"{normalized}".rstrip("/")
            )
    for experiment_id, physical in index.items():
        if value == physical or value.startswith(physical + "/"):
            under = value[len(physical):].lstrip("/")
            normalized = "/".join(
                _normalize_segment(part) for part in under.split("/")
            )
            return f"experiments/{experiment_id}/{normalized}".rstrip("/")
    if value.startswith("experiments/"):
        parts = value.split("/")
        if len(parts) >= 2 and parts[1] in index:
            under = "/".join(parts[2:])
            normalized = "/".join(
                _normalize_segment(part) for part in under.split("/")
            )
            return f"experiments/{parts[1]}/{normalized}".rstrip("/")
    return value


def _canonicalize_path_references(
    value: Any,
    root: Path,
    index: dict[str, str],
    candidate_identity: dict[str, tuple[str, str]] | None = None,
) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _canonicalize_path_references(
                item, root, index, candidate_identity
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _canonicalize_path_references(
                item, root, index, candidate_identity
            )
            for item in value
        ]
    if isinstance(value, str):
        return _canonicalize_artifact_path(
            value, root, index, candidate_identity
        )
    return value


def _locked_python_executable(source: Path) -> str:
    """Return the source run's frozen interpreter from RUNTIME_LOCK.json."""

    lock_path = source / "RUNTIME_LOCK.json"
    if not lock_path.is_file():
        raise RuntimeError(
            "fresh replay requires RUNTIME_LOCK.json with a frozen "
            "runtime.python_executable"
        )
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"RUNTIME_LOCK.json is not readable JSON: {exc}"
        ) from exc
    runtime = lock.get("runtime") if isinstance(lock, dict) else None
    value = None
    if isinstance(runtime, dict):
        value = runtime.get("python_executable")
    if not value and isinstance(lock, dict):
        value = lock.get("python_executable")
    text = str(value or "").strip().strip("\"'")
    if not text:
        raise RuntimeError(
            "RUNTIME_LOCK.json does not declare a frozen "
            "runtime.python_executable; refusing to replay in an unknown "
            "environment"
        )
    return text


def _normalized_interpreter(value: str) -> str:
    text = str(value or "").strip().strip("\"'")
    if not text:
        return ""
    expanded = os.path.expandvars(text)
    path = Path(expanded).expanduser()
    if path.is_absolute():
        try:
            return os.path.normcase(str(path.resolve(strict=False)))
        except OSError:
            return os.path.normcase(str(path))
    return os.path.normcase(str(path))


def _same_interpreter(current: str, locked: str) -> bool:
    """True when the caller already runs the source's frozen interpreter."""

    current_norm = _normalized_interpreter(current)
    locked_norm = _normalized_interpreter(locked)
    return bool(current_norm) and current_norm == locked_norm


def _validate_locked_interpreter(locked: str, source: Path) -> Path:
    """Fail-closed validation of the source-frozen interpreter path."""

    text = str(locked or "").strip().strip("\"'")
    if not text:
        raise RuntimeError(
            "RUNTIME_LOCK.json python_executable is empty; refusing to "
            "replay in an unknown environment"
        )
    expanded = os.path.expandvars(text)
    candidate = Path(expanded)
    if not candidate.is_absolute():
        candidate = source / candidate
    try:
        resolved = candidate.resolve(strict=False)
    except OSError as exc:
        raise RuntimeError(
            f"locked interpreter {locked!r} cannot be resolved: {exc}"
        ) from exc
    repo = _REPO_ROOT.resolve(strict=False)
    try:
        resolved.relative_to(repo)
    except ValueError as exc:
        raise RuntimeError(
            f"locked interpreter {locked!r} resolves outside the project "
            "root; refusing to execute an unsafe environment"
        ) from exc
    if not resolved.is_file():
        raise RuntimeError(
            f"locked interpreter {locked!r} is missing at {resolved}; "
            "refusing to replay in the caller environment"
        )
    if os.name != "nt" and not os.access(resolved, os.X_OK):
        raise RuntimeError(
            f"locked interpreter {locked!r} is not executable"
        )
    return resolved


def _run_locked_interpreter_replay(
    source: Path,
    replay_subdir: str,
    replace_existing: bool,
    locked_exe: Path,
) -> ReplayManifest:
    """Run the replay in a fresh child process under the frozen interpreter."""

    script = _REPO_ROOT / "code" / "scripts" / "replay_tmm_harness_run.py"
    if not script.is_file():
        raise RuntimeError(f"replay worker script is missing: {script}")
    env = dict(os.environ)
    env[_REPLAY_WORKER_ENV] = "1"
    command = [
        str(locked_exe),
        str(script),
        "--source-run",
        str(source),
        "--replay-subdir",
        replay_subdir,
    ]
    if replace_existing:
        command.append("--replace")
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            env=env,
            timeout=3600,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(
            f"failed to launch fresh replay under locked interpreter "
            f"{locked_exe}: {exc}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"fresh replay under locked interpreter {locked_exe} timed out "
            "before producing a manifest"
        ) from exc
    if completed.returncode not in (0, 2):
        raise RuntimeError(
            f"locked-interpreter replay exited with unexpected code "
            f"{completed.returncode} and must not be accepted; stderr: "
            f"{(completed.stderr or '').strip()[:1000]}"
        )
    manifest_path = source / "REPLAY_MANIFEST.json"
    if not manifest_path.is_file():
        raise RuntimeError(
            f"locked-interpreter replay exited with code "
            f"{completed.returncode} but wrote no REPLAY_MANIFEST.json; "
            f"stderr: {(completed.stderr or '').strip()[:1000]}"
        )
    try:
        manifest = ReplayManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except Exception as exc:  # noqa: BLE001 - manifest must be machine-readable
        raise RuntimeError(
            f"locked-interpreter replay wrote an invalid "
            f"REPLAY_MANIFEST.json: {exc}"
        ) from exc
    if completed.returncode == 0 and not manifest.success:
        raise RuntimeError(
            "locked-interpreter replay exited 0 but reported a failed "
            "manifest; refusing to accept the mismatch"
        )
    if completed.returncode == 2 and manifest.success:
        raise RuntimeError(
            "locked-interpreter replay exited 2 but reported a success "
            "manifest; refusing to accept the inconsistent result"
        )
    return manifest


def replay_completed_run(
    source_run_dir: str | Path,
    *,
    replay_subdir: str = "fresh_replay",
    replace_existing: bool = False,
) -> ReplayManifest:
    """Recompute a completed run and compare deterministic scientific JSON.

    Strategy-model calls are disabled during replay.  The deterministic policy
    therefore exercises the same TMM solvers and optimizer portfolio without
    making reproducibility depend on a new LLM response.
    """

    from .orchestrator import TMMHarnessConfig, TMMHarnessOrchestrator

    source = Path(source_run_dir).resolve()
    task_path = source / "TASK.json"
    final_path = source / "FINAL_RESULT.json"
    config_path = source / "HARNESS_CONFIG.json"
    if not task_path.exists() or not final_path.exists() or not config_path.exists():
        raise FileNotFoundError(
            "fresh replay requires TASK.json, HARNESS_CONFIG.json, and FINAL_RESULT.json"
        )
    source_store = ArtifactLineageStore(source, resume=True)
    source_store.verify_all()
    existing_manifest_path = source / "REPLAY_MANIFEST.json"
    if existing_manifest_path.exists():
        if replace_existing:
            raise ValueError(
                "A registered replay is immutable; create a new source run for a new replay."
            )
        return ReplayManifest.model_validate_json(
            existing_manifest_path.read_text(encoding="utf-8")
        )
    source_final = json.loads(final_path.read_text(encoding="utf-8"))
    if not str(source_final.get("status") or "").startswith("completed"):
        raise ValueError("fresh replay requires a completed source run")
    scientific_source = source
    recovery_report_path = source / "RECOVERY_REPORT.json"
    if recovery_report_path.exists():
        recovery_report = json.loads(
            recovery_report_path.read_text(encoding="utf-8")
        )
        child_artifact = str(
            recovery_report.get("child_result_artifact_id") or ""
        )
        if child_artifact:
            candidate = (source / child_artifact).resolve().parent
            candidate.relative_to(source)
            if (candidate / "TASK.json").exists():
                scientific_source = candidate
                task_path = scientific_source / "TASK.json"
                final_path = scientific_source / "FINAL_RESULT.json"
                config_path = scientific_source / "HARNESS_CONFIG.json"
    locked = _locked_python_executable(source)
    if not _same_interpreter(sys.executable, locked):
        locked_exe = _validate_locked_interpreter(locked, source)
        if os.environ.get(_REPLAY_WORKER_ENV) == "1":
            raise RuntimeError(
                f"fresh replay worker interpreter {sys.executable!r} does "
                f"not match the locked interpreter {locked!r}; refusing to "
                "replay in a mismatched environment"
            )
        return _run_locked_interpreter_replay(
            source,
            replay_subdir,
            replace_existing,
            locked_exe,
        )
    task = OpticalDesignTask.model_validate_json(task_path.read_text(encoding="utf-8"))
    source_config = TMMHarnessConfig.model_validate_json(
        config_path.read_text(encoding="utf-8")
    )
    replay_config = source_config.model_copy(
        update={"use_qwen_policy": False, "qwen_force_mock": None}
    )
    replay_root = source / replay_subdir
    if replay_root.exists():
        if not replace_existing:
            raise FileExistsError(f"replay directory already exists: {replay_root}")
        shutil.rmtree(replay_root)
    replay_result = TMMHarnessOrchestrator(
        replay_root,
        run_id=f"{source_final['run_id']}.fresh_replay",
        config=replay_config,
    ).run(task)

    source_index = _load_experiment_path_index(scientific_source)
    replay_index = _load_experiment_path_index(replay_root)
    source_candidates = _load_candidate_identity(
        scientific_source, source_index
    )
    replay_candidates = _load_candidate_identity(replay_root, replay_index)
    source_logical = _logical_scientific_paths(
        scientific_source, source_index, source_candidates
    )
    replay_logical = _logical_scientific_paths(
        replay_root, replay_index, replay_candidates
    )
    relative_paths = sorted(set(source_logical) | set(replay_logical))
    checks: list[ReplayArtifactCheck] = []
    for logical_path in relative_paths:
        source_entry = source_logical.get(logical_path)
        replay_entry = replay_logical.get(logical_path)
        source_path = source_entry[1] if source_entry is not None else None
        replay_path = replay_entry[1] if replay_entry is not None else None
        relative_path = (
            source_entry[0]
            if source_entry is not None
            else replay_entry[0]
            if replay_entry is not None
            else logical_path
        )
        if source_path is None:
            checks.append(
                ReplayArtifactCheck(
                    relative_path=relative_path,
                    logical_path=logical_path,
                    replay_sha256=_scientific_digest(
                        replay_path,
                        root=replay_root,
                        path_index=replay_index,
                        candidate_identity=replay_candidates,
                    ),
                    matched=False,
                    reason="artifact_only_in_replay",
                )
            )
            continue
        if replay_path is None:
            checks.append(
                ReplayArtifactCheck(
                    relative_path=relative_path,
                    logical_path=logical_path,
                    source_sha256=_scientific_digest(
                        source_path,
                        root=scientific_source,
                        path_index=source_index,
                        candidate_identity=source_candidates,
                    ),
                    matched=False,
                    reason="artifact_missing_from_replay",
                )
            )
            continue
        source_digest = _scientific_digest(
            source_path,
            root=scientific_source,
            path_index=source_index,
            candidate_identity=source_candidates,
        )
        replay_digest = _scientific_digest(
            replay_path,
            root=replay_root,
            path_index=replay_index,
            candidate_identity=replay_candidates,
        )
        checks.append(
            ReplayArtifactCheck(
                relative_path=relative_path,
                logical_path=logical_path,
                source_sha256=source_digest,
                replay_sha256=replay_digest,
                matched=source_digest == replay_digest,
                reason=(
                    "canonical_scientific_json_match"
                    if source_digest == replay_digest
                    else "canonical_scientific_json_mismatch"
                ),
            )
        )
    task_digest = _sha256_bytes(task_path.read_bytes())
    replay_task_digest = _sha256_bytes((replay_root / "TASK.json").read_bytes())
    success = (
        replay_result.status.startswith("completed")
        and task_digest == replay_task_digest
        and bool(checks)
        and all(item.matched for item in checks)
    )
    manifest = ReplayManifest(
        source_run_id=str(source_final["run_id"]),
        replay_run_id=replay_result.run_id,
        scientific_source_relative=scientific_source.relative_to(source).as_posix() or ".",
        source_task_sha256=task_digest,
        replay_task_sha256=replay_task_digest,
        checks=tuple(checks),
        matched_artifacts=sum(item.matched for item in checks),
        total_artifacts=len(checks),
        success=success,
        notes=(
            "Wall-clock and timestamp fields are excluded from scientific comparison.",
            "Performance targets remain ranking preferences during replay.",
        ),
    )
    manifest_path = source / "REPLAY_MANIFEST.json"
    atomic_write_json(manifest_path, manifest.model_dump(mode="json"))

    # Extend, rather than rewrite, the source run lineage after replay.
    store = source_store
    replay_final_id = f"{replay_subdir}/FINAL_RESULT.json"
    replay_lineage_id = f"{replay_subdir}/ARTIFACT_MANIFEST.json"
    store.register_file(
        replay_root / "FINAL_RESULT.json",
        artifact_id=replay_final_id,
        artifact_type="fresh_replay_result",
        producing_action="fresh_process_replay",
        input_artifact_ids=["FINAL_RESULT.json"],
    )
    store.register_file(
        replay_root / "ARTIFACT_MANIFEST.json",
        artifact_id=replay_lineage_id,
        artifact_type="fresh_replay_lineage",
        producing_action="fresh_process_replay",
        input_artifact_ids=[replay_final_id],
    )
    store.register_file(
        manifest_path,
        artifact_id="REPLAY_MANIFEST.json",
        artifact_type="fresh_replay_manifest",
        producing_action="compare_fresh_replay",
        input_artifact_ids=["FINAL_RESULT.json", replay_final_id, replay_lineage_id],
        scientific_provenance={
            "success": success,
            "matched_artifacts": manifest.matched_artifacts,
            "total_artifacts": manifest.total_artifacts,
        },
    )
    store.verify_all()
    return manifest


def reassess_existing_replay(
    source_run_dir: str | Path,
    *,
    replay_subdir: str = "fresh_replay",
    output_filename: str = "REPLAY_REASSESSMENT.json",
) -> ReplayManifest:
    """Re-evaluate existing source/replay files without rerunning physics.

    This is for a comparator bug discovered after a one-shot blind run.  The
    original replay manifest remains immutable; the reassessment is an
    append-only artifact linked to it and to both completed result lineages.
    """

    source = Path(source_run_dir).resolve()
    replay_root = source / replay_subdir
    output_path = source / output_filename
    if output_path.exists():
        return ReplayManifest.model_validate_json(output_path.read_text(encoding="utf-8"))
    required = (
        source / "TASK.json",
        source / "FINAL_RESULT.json",
        source / "REPLAY_MANIFEST.json",
        replay_root / "TASK.json",
        replay_root / "FINAL_RESULT.json",
        replay_root / "ARTIFACT_MANIFEST.json",
    )
    if not all(path.exists() for path in required):
        raise FileNotFoundError("reassessment requires the completed source and existing replay")
    source_store = ArtifactLineageStore(source, resume=True)
    source_store.verify_all()
    replay_store = ArtifactLineageStore(replay_root, resume=True)
    replay_store.verify_all()

    source_final = json.loads((source / "FINAL_RESULT.json").read_text(encoding="utf-8"))
    replay_final = json.loads((replay_root / "FINAL_RESULT.json").read_text(encoding="utf-8"))
    if not str(source_final.get("status") or "").startswith("completed"):
        raise ValueError("source run is not completed")
    if not str(replay_final.get("status") or "").startswith("completed"):
        raise ValueError("existing replay is not completed")

    source_index = _load_experiment_path_index(source)
    replay_index = _load_experiment_path_index(replay_root)
    source_candidates = _load_candidate_identity(source, source_index)
    replay_candidates = _load_candidate_identity(replay_root, replay_index)
    source_logical = _logical_scientific_paths(
        source, source_index, source_candidates
    )
    replay_logical = _logical_scientific_paths(
        replay_root, replay_index, replay_candidates
    )
    relative_paths = sorted(set(source_logical) | set(replay_logical))
    checks: list[ReplayArtifactCheck] = []
    for logical_path in relative_paths:
        source_entry = source_logical.get(logical_path)
        replay_entry = replay_logical.get(logical_path)
        source_path = source_entry[1] if source_entry is not None else None
        replay_path = replay_entry[1] if replay_entry is not None else None
        relative_path = (
            source_entry[0]
            if source_entry is not None
            else replay_entry[0]
            if replay_entry is not None
            else logical_path
        )
        if source_path is None or replay_path is None:
            checks.append(
                ReplayArtifactCheck(
                    relative_path=relative_path,
                    logical_path=logical_path,
                    source_sha256=(
                        None
                        if source_path is None
                        else _scientific_digest(
                            source_path,
                            root=source,
                            path_index=source_index,
                            candidate_identity=source_candidates,
                        )
                    ),
                    replay_sha256=(
                        None
                        if replay_path is None
                        else _scientific_digest(
                            replay_path,
                            root=replay_root,
                            path_index=replay_index,
                            candidate_identity=replay_candidates,
                        )
                    ),
                    matched=False,
                    reason="artifact_missing_from_source_or_replay",
                )
            )
            continue
        source_digest = _scientific_digest(
            source_path,
            root=source,
            path_index=source_index,
            candidate_identity=source_candidates,
        )
        replay_digest = _scientific_digest(
            replay_path,
            root=replay_root,
            path_index=replay_index,
            candidate_identity=replay_candidates,
        )
        checks.append(
            ReplayArtifactCheck(
                relative_path=relative_path,
                logical_path=logical_path,
                source_sha256=source_digest,
                replay_sha256=replay_digest,
                matched=source_digest == replay_digest,
                reason=(
                    "canonical_scientific_json_match"
                    if source_digest == replay_digest
                    else "canonical_scientific_json_mismatch"
                ),
            )
        )
    source_task_sha = _sha256_bytes((source / "TASK.json").read_bytes())
    replay_task_sha = _sha256_bytes((replay_root / "TASK.json").read_bytes())
    success = (
        source_task_sha == replay_task_sha
        and bool(checks)
        and all(item.matched for item in checks)
    )
    manifest = ReplayManifest(
        source_run_id=str(source_final.get("run_id") or "unknown"),
        replay_run_id=str(replay_final.get("run_id") or "unknown"),
        source_task_sha256=source_task_sha,
        replay_task_sha256=replay_task_sha,
        checks=tuple(checks),
        matched_artifacts=sum(item.matched for item in checks),
        total_artifacts=len(checks),
        success=success,
        notes=(
            "Existing physics outputs were reassessed without rerunning the holdout task.",
            "The Qwen policy enabled flag is operational and is ignored because replay disables Qwen by design.",
            "Performance targets remain ranking preferences.",
        ),
    )
    atomic_write_json(output_path, manifest.model_dump(mode="json"))
    source_store.register_file(
        output_path,
        artifact_id=output_filename,
        artifact_type="fresh_replay_reassessment",
        producing_action="reassess_existing_fresh_replay",
        input_artifact_ids=(
            "REPLAY_MANIFEST.json",
            f"{replay_subdir}/FINAL_RESULT.json",
            f"{replay_subdir}/ARTIFACT_MANIFEST.json",
        ),
        scientific_provenance={
            "success": success,
            "matched_artifacts": manifest.matched_artifacts,
            "total_artifacts": manifest.total_artifacts,
            "physics_rerun": False,
        },
    )
    source_store.verify_all()
    return manifest


__all__ = [
    "ReplayArtifactCheck",
    "ReplayManifest",
    "reassess_existing_replay",
    "replay_completed_run",
]
