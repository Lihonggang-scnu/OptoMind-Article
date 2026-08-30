"""Deterministic loader for the frozen TMM Harness benchmark split.

This module intentionally has no solver, model, or network dependency. Importing it
only defines immutable schemas and path constants; benchmark files are read solely
when :func:`load_benchmark_tasks` is called.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


_DEFAULT_BENCHMARK_ROOT = (
    Path(__file__).resolve().parents[2] / "benchmarks" / "tmm_harness_v1"
)
BENCHMARK_ROOT = _DEFAULT_BENCHMARK_ROOT
DEFAULT_HOLDOUT_AUDIT_LOG = (
    Path(__file__).resolve().parents[2]
    / "outputs"
    / "tmm_harness_holdout_audit"
    / "HOLDOUT_ACCESS.jsonl"
)
_HOLDOUT_ENV = "OPTOMIND_ALLOW_TMM_HOLDOUT"
_EXPECTED_SPLIT_IDS: dict[str, tuple[str, ...]] = {
    "dev": ("DEV01", "DEV02", "DEV03", "DEV04", "DEV05"),
    "holdout": ("HOLDOUT06", "HOLDOUT07", "HOLDOUT08", "HOLDOUT09", "HOLDOUT10"),
}
_EXPECTED_SPLIT_FILES = {
    "dev": "dev_tasks.json",
    "holdout": "holdout_tasks.json",
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class BenchmarkIntegrityError(ValueError):
    """Raised when frozen benchmark metadata or task-file bytes do not match."""


class EvaluationContract(BaseModel):
    """Admission and scoring policy shared by every frozen benchmark task."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    performance_targets: Literal["soft_scores"]
    admission_gate: Literal["deterministic_physics_validity_only"]
    hard_gates: tuple[str, ...] = Field(default_factory=tuple)
    statement: str

    @field_validator("hard_gates")
    @classmethod
    def _reject_performance_hard_gates(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value:
            raise ValueError("benchmark evaluation contracts cannot contain hard gates")
        return value

    @field_validator("statement")
    @classmethod
    def _require_explicit_soft_score_statement(cls, value: str) -> str:
        required = "Performance targets are soft scores; deterministic physics validity is the only admission gate."
        if value != required:
            raise ValueError("evaluation contract must state the soft-score-only admission policy")
        return value


class BenchmarkTask(BaseModel):
    """Immutable public representation of one benchmark question."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    split: Literal["dev", "holdout"]
    domain: Literal["TMM"]
    title: str
    natural_language_question: str
    task_family: str
    capability_axes: tuple[str, ...]
    expected_artifacts: tuple[str, ...]
    evaluation_contract: EvaluationContract


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BenchmarkIntegrityError(f"unable to read UTF-8 JSON benchmark file: {path}") from exc


def _safe_child(root: Path, filename: str) -> Path:
    root_resolved = root.resolve()
    candidate = (root / filename).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise BenchmarkIntegrityError("benchmark manifest contains a path outside its directory") from exc
    return candidate


def _append_holdout_audit(path: Path, payload: dict[str, Any]) -> None:
    """Append one fail-closed audit event before/after sealed-file access."""

    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "schema_version": "tmm-holdout-access-event.v1",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "process_id": os.getpid(),
        **payload,
    }
    line = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
    try:
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise BenchmarkIntegrityError(
            f"unable to persist holdout access audit before reading sealed data: {target}"
        ) from exc


def holdout_access_events(
    audit_log_path: str | Path = DEFAULT_HOLDOUT_AUDIT_LOG,
) -> tuple[dict[str, Any], ...]:
    """Read recorded real-holdout access events without touching task content."""

    path = Path(audit_log_path)
    if not path.exists():
        return ()
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BenchmarkIntegrityError(f"invalid holdout access audit JSONL: {path}") from exc
        if not isinstance(item, dict):
            raise BenchmarkIntegrityError(f"invalid holdout access audit event: {path}")
        events.append(item)
    return tuple(events)


def _validate_manifest(manifest: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(manifest, dict):
        raise BenchmarkIntegrityError("split_manifest.json must contain an object")
    if manifest.get("schema_version") != "tmm_harness_v1.split_manifest.v1":
        raise BenchmarkIntegrityError("unsupported TMM Harness split manifest schema")
    if manifest.get("benchmark_id") != "tmm_harness_v1":
        raise BenchmarkIntegrityError("split manifest benchmark_id is not tmm_harness_v1")

    splits = manifest.get("splits")
    if not isinstance(splits, dict) or set(splits) != set(_EXPECTED_SPLIT_IDS):
        raise BenchmarkIntegrityError("split manifest must declare exactly dev and holdout")

    validated: dict[str, dict[str, Any]] = {}
    for split_name, expected_ids in _EXPECTED_SPLIT_IDS.items():
        spec = splits.get(split_name)
        if not isinstance(spec, dict):
            raise BenchmarkIntegrityError(f"manifest entry for {split_name} is not an object")
        if spec.get("file") != _EXPECTED_SPLIT_FILES[split_name]:
            raise BenchmarkIntegrityError(f"manifest file for {split_name} is not frozen")
        ids = spec.get("ids")
        if ids != list(expected_ids):
            raise BenchmarkIntegrityError(f"manifest IDs for {split_name} do not match the frozen split")
        digest = spec.get("sha256")
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise BenchmarkIntegrityError(f"manifest SHA-256 digest for {split_name} is invalid")
        validated[split_name] = spec
    return validated


def load_benchmark_tasks(
    split: Literal["dev", "holdout"] = "dev",
    *,
    allow_holdout: bool = False,
    benchmark_dir: str | Path | None = None,
    holdout_audit_log: str | Path | None = None,
    requested_holdout_id: str | None = None,
) -> tuple[BenchmarkTask, ...]:
    """Load one frozen split after checking its manifest IDs and SHA-256 digest.

    ``dev`` is the safe default. Loading ``holdout`` requires both
    ``allow_holdout=True`` and ``OPTOMIND_ALLOW_TMM_HOLDOUT=1``. Every
    authorized holdout read is recorded before the sealed file is opened. The
    optional ``benchmark_dir`` and ``holdout_audit_log`` are intended for
    deterministic tests using synthetic benchmark data, never the real split.
    """

    if split not in _EXPECTED_SPLIT_IDS:
        raise ValueError(f"unknown TMM Harness benchmark split: {split!r}")
    if split == "holdout" and not (allow_holdout and os.getenv(_HOLDOUT_ENV) == "1"):
        raise PermissionError(
            "holdout loading requires allow_holdout=True and "
            f"{_HOLDOUT_ENV}=1"
        )

    root = Path(benchmark_dir) if benchmark_dir is not None else BENCHMARK_ROOT
    audit_path: Path | None = None
    if split == "holdout":
        if benchmark_dir is None:
            # Callers cannot redirect the real audit trail to an unobserved path.
            audit_path = DEFAULT_HOLDOUT_AUDIT_LOG
        elif holdout_audit_log is None:
            raise PermissionError("synthetic holdout tests require holdout_audit_log")
        else:
            audit_path = Path(holdout_audit_log)
        _append_holdout_audit(
            audit_path,
            {
                "event": "holdout_read_started",
                "benchmark_root": str(root.resolve()),
                "requested_holdout_id": requested_holdout_id,
                "access_scope": "entire_holdout_file",
            },
        )

    try:
        manifest = _validate_manifest(_read_json(_safe_child(root, "split_manifest.json")))
        spec = manifest[split]
        task_path = _safe_child(root, str(spec["file"]))
        task_bytes = task_path.read_bytes()
        actual_digest = hashlib.sha256(task_bytes).hexdigest()
        expected_digest = str(spec["sha256"])
        if actual_digest != expected_digest:
            raise BenchmarkIntegrityError(
                f"SHA-256 digest mismatch for {spec['file']}: "
                f"expected {expected_digest}, got {actual_digest}"
            )

        raw_tasks = json.loads(task_bytes.decode("utf-8"))
        if not isinstance(raw_tasks, list):
            raise BenchmarkIntegrityError(f"{spec['file']} must contain a JSON array")
        try:
            tasks = tuple(BenchmarkTask.model_validate(item) for item in raw_tasks)
        except Exception as exc:
            raise BenchmarkIntegrityError(f"invalid task entry in {spec['file']}") from exc
    except Exception as exc:
        if audit_path is not None:
            _append_holdout_audit(
                audit_path,
                {
                    "event": "holdout_read_failed",
                    "benchmark_root": str(root.resolve()),
                    "requested_holdout_id": requested_holdout_id,
                    "error_type": type(exc).__name__,
                },
            )
        raise

    expected_ids = tuple(spec["ids"])
    actual_ids = tuple(task.id for task in tasks)
    if actual_ids != expected_ids:
        raise BenchmarkIntegrityError(
            f"task IDs in {spec['file']} do not match split_manifest.json"
        )
    if any(task.split != split for task in tasks):
        raise BenchmarkIntegrityError(f"task split labels in {spec['file']} are inconsistent")
    if audit_path is not None:
        _append_holdout_audit(
            audit_path,
            {
                "event": "holdout_read_completed",
                "benchmark_root": str(root.resolve()),
                "requested_holdout_id": requested_holdout_id,
                "file_sha256": actual_digest,
                "loaded_task_count": len(tasks),
            },
        )
    return tasks


def load_benchmark_task(
    benchmark_id: str,
    *,
    allow_holdout: bool = False,
    benchmark_dir: str | Path | None = None,
    holdout_audit_log: str | Path | None = None,
) -> BenchmarkTask:
    """Load one benchmark, with mandatory auditing for a selected holdout ID."""

    task_id = str(benchmark_id).strip().upper()
    if task_id in _EXPECTED_SPLIT_IDS["dev"]:
        tasks = load_benchmark_tasks("dev", benchmark_dir=benchmark_dir)
    elif task_id in _EXPECTED_SPLIT_IDS["holdout"]:
        tasks = load_benchmark_tasks(
            "holdout",
            allow_holdout=allow_holdout,
            benchmark_dir=benchmark_dir,
            holdout_audit_log=holdout_audit_log,
            requested_holdout_id=task_id,
        )
    else:
        raise KeyError(f"unknown frozen TMM Harness benchmark ID: {task_id!r}")
    return next(task for task in tasks if task.id == task_id)


__all__ = [
    "BENCHMARK_ROOT",
    "DEFAULT_HOLDOUT_AUDIT_LOG",
    "BenchmarkIntegrityError",
    "BenchmarkTask",
    "EvaluationContract",
    "holdout_access_events",
    "load_benchmark_task",
    "load_benchmark_tasks",
]
