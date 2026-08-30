"""Stage 17A: thin production checkpoint/resume ledger for ArticlePipeline.

This module is not a second scientific state machine.  It is a thin,
tamper-evident recovery ledger that indexes the pipeline's own
``StageReceipt``s, stage snapshot hashes, event-log prefixes, and
execution/asset route progress so a crashed or interrupted run can be
resumed exactly once per committed artifact.

Hard rules:
- ``ArticlePipeline.run`` remains the write-once new-run entry; ``resume`` is
  the only continuation entry and accepts the same immutable request.
- Committed checkpoint records are immutable per-stage files; only the
  ``RECOVERY_LEDGER.json`` latest pointer may be atomically overwritten.
- No wall-clock time, credentials, lock tokens, or ``work_dir`` paths enter
  checkpoint IDs or result IDs.
- Resume fails closed before any adapter runs on request/runtime-fingerprint/
  chain/hash/identity mismatches; committed adapters are never called again.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Tuple

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from optomind_optics.harness.article_pipeline import (
    PIPELINE_STAGE_ORDER,
    ArticlePipelineRequest,
    StageReceipt,
)
from optomind_optics.harness.article_runtime import article_runtime_fingerprint
from optomind_research.runtime.artifact_store import (
    atomic_write_json,
    atomic_write_text,
)


CHECKPOINT_SCHEMA_VERSION = "pipeline-checkpoint-record.v1"
LEDGER_SCHEMA_VERSION = "pipeline-recovery-ledger.v1"
ROUTE_PROGRESS_SCHEMA_VERSION = "pipeline-route-progress.v1"
EXECUTION_ROUTE_SCHEMA_VERSION = "execution-route-progress.v1"
ASSET_ROUTE_SCHEMA_VERSION = "asset-route-progress.v1"

LEDGER_FILENAME = "RECOVERY_LEDGER.json"
ROUTE_PROGRESS_FILENAME = "ROUTE_PROGRESS.json"
LOCK_FILENAME = "PIPELINE_RUNTIME.lock"
REQUEST_FILENAME = "REQUEST.json"
EVENTS_FILENAME = "PIPELINE_EVENTS.jsonl"
FINAL_RESULT_FILENAME = "FINAL_PIPELINE_RESULT.json"
CHECKPOINT_PREFIX = "checkpoint-"
EXECUTION_ROUTE_PREFIX = "route-execution-"
ASSET_ROUTE_PREFIX = "route-asset-"

_KNOWN_ARTIFACTS = frozenset(
    {
        REQUEST_FILENAME,
        EVENTS_FILENAME,
        LEDGER_FILENAME,
        ROUTE_PROGRESS_FILENAME,
        LOCK_FILENAME,
        FINAL_RESULT_FILENAME,
    }
)


class RecoveryIntegrityError(ValueError):
    """A recovery ledger is missing, inconsistent, or tampered."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(*parts: Any) -> str:
    payload = [
        part if isinstance(part, (dict, list, tuple)) else str(part)
        for part in parts
    ]
    return hashlib.sha256(
        _canonical_json(payload).encode("utf-8")
    ).hexdigest()


def _short_digest(*parts: Any) -> str:
    return _digest(*parts)[:16]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_hex(value: str, field: str) -> str:
    text = str(value or "").strip()
    if text and (
        len(text) != 64
        or any(char not in "0123456789abcdef" for char in text)
    ):
        raise ValueError(
            f"{field} must be empty or a 64-character lowercase hex digest"
        )
    return text


class PipelineCheckpointRecord(_StrictModel):
    """One immutable per-stage checkpoint in the recovery chain."""

    schema_version: Literal["pipeline-checkpoint-record.v1"] = (
        "pipeline-checkpoint-record.v1"
    )
    request_digest: str
    runtime_fingerprint: str
    stage_sequence: int
    stage: str
    stage_status: str
    receipt: StageReceipt
    snapshot_filename: str
    snapshot_sha256: str
    payload_digest: str
    event_prefix_digest: str
    route_progress_digest: str = ""
    previous_checkpoint_id: str = ""
    checkpoint_id: str = ""
    hard_failure: bool = False

    @field_validator("stage_sequence")
    @classmethod
    def _positive_sequence(cls, value: int) -> int:
        if int(value) < 1:
            raise ValueError("stage_sequence must be positive")
        return int(value)

    @field_validator(
        "request_digest",
        "runtime_fingerprint",
        "snapshot_sha256",
        "payload_digest",
        "event_prefix_digest",
        "route_progress_digest",
        "previous_checkpoint_id",
        "checkpoint_id",
    )
    @classmethod
    def _hex_digest_fields(cls, value: str, info: Any) -> str:
        return _validate_hex(value, info.field_name)


class ExecutionRouteProgress(_StrictModel):
    """One committed execution route for stage 7."""

    schema_version: Literal["execution-route-progress.v1"] = (
        "execution-route-progress.v1"
    )
    request_id: str
    task_hash: str
    run_id: str
    branch_id: str
    route_id: str
    snapshot_filename: str
    snapshot_sha256: str
    payload_digest: str
    warnings: Tuple[str, ...] = Field(default_factory=tuple)


class AssetRouteProgress(_StrictModel):
    """One committed asset route for stage 8."""

    schema_version: Literal["asset-route-progress.v1"] = (
        "asset-route-progress.v1"
    )
    request_id: str
    task_hash: str
    run_id: str
    branch_id: str
    asset_result_id: str
    execution_snapshot_filename: str
    snapshot_filename: str
    snapshot_sha256: str
    payload_digest: str
    warnings: Tuple[str, ...] = Field(default_factory=tuple)


class PipelineRouteProgress(_StrictModel):
    """Committed execution/asset route progress under one work_dir."""

    schema_version: Literal["pipeline-route-progress.v1"] = (
        "pipeline-route-progress.v1"
    )
    execution: Tuple[ExecutionRouteProgress, ...] = Field(
        default_factory=tuple
    )
    asset: Tuple[AssetRouteProgress, ...] = Field(default_factory=tuple)


def request_digest(request: ArticlePipelineRequest) -> str:
    """Deterministic identity digest of the immutable pipeline request."""

    payload = request.model_dump(mode="json")
    payload.pop("work_dir", None)
    return _digest(payload)


def compute_checkpoint_id(record: PipelineCheckpointRecord) -> str:
    """Canonical content ID of one checkpoint (excluding checkpoint_id)."""

    payload = record.model_dump(mode="json")
    payload.pop("checkpoint_id", None)
    return _digest(payload)


def compute_route_progress_digest(
    progress: PipelineRouteProgress | Mapping[str, Any] | None,
) -> str:
    model = (
        progress
        if isinstance(progress, PipelineRouteProgress)
        else PipelineRouteProgress.model_validate(progress or {})
    )
    return _digest(model.model_dump(mode="json"))


def compute_execution_progress_digest(
    progress: PipelineRouteProgress | Mapping[str, Any] | None,
) -> str:
    model = (
        progress
        if isinstance(progress, PipelineRouteProgress)
        else PipelineRouteProgress.model_validate(progress or {})
    )
    return _digest(
        [entry.model_dump(mode="json") for entry in model.execution]
    )


def compute_asset_progress_digest(
    progress: PipelineRouteProgress | Mapping[str, Any] | None,
) -> str:
    model = (
        progress
        if isinstance(progress, PipelineRouteProgress)
        else PipelineRouteProgress.model_validate(progress or {})
    )
    return _digest(
        [entry.model_dump(mode="json") for entry in model.asset]
    )


def compute_event_prefix_digest(events: Sequence[Mapping[str, Any]]) -> str:
    """Canonical digest over an ordered event prefix."""

    return _digest([dict(item) for item in events])


def checkpoint_filename(sequence: int, stage: str) -> str:
    return f"{CHECKPOINT_PREFIX}{sequence:02d}-{stage}.json"


def _ledger_path(work_dir: Path) -> Path:
    return work_dir / LEDGER_FILENAME


def _route_progress_path(work_dir: Path) -> Path:
    return work_dir / ROUTE_PROGRESS_FILENAME


def load_route_progress(
    work_dir: str | Path,
) -> PipelineRouteProgress:
    path = _route_progress_path(Path(work_dir))
    if not path.is_file():
        return PipelineRouteProgress()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RecoveryIntegrityError(
            f"route progress is invalid: {exc}"
        ) from exc
    try:
        return PipelineRouteProgress.model_validate(payload)
    except ValidationError as exc:
        raise RecoveryIntegrityError(
            f"route progress is invalid: {exc}"
        ) from exc


def write_checkpoint(work_dir: str | Path, record: PipelineCheckpointRecord) -> None:
    """Commit one immutable checkpoint record and update the latest pointer."""

    root = Path(work_dir)
    root.mkdir(parents=True, exist_ok=True)
    filename = checkpoint_filename(record.stage_sequence, record.stage)
    path = root / filename
    if path.exists():
        raise RecoveryIntegrityError(
            f"committed checkpoint record already exists: {filename}"
        )
    expected = compute_checkpoint_id(record)
    if record.checkpoint_id != expected:
        raise RecoveryIntegrityError(
            "refusing to commit a checkpoint with a mismatched checkpoint_id"
        )
    atomic_write_json(path, record.model_dump(mode="json"))
    ledger = _load_ledger_payload(root)
    committed = list(ledger.get("committed_checkpoints") or [])
    if filename in committed:
        raise RecoveryIntegrityError(
            f"checkpoint {filename} already listed in the ledger"
        )
    committed.append(filename)
    atomic_write_json(
        _ledger_path(root),
        {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "request_digest": record.request_digest,
            "runtime_fingerprint": record.runtime_fingerprint,
            "latest_checkpoint_id": record.checkpoint_id,
            "latest_sequence": record.stage_sequence,
            "committed_checkpoints": committed,
        },
    )


def write_execution_route(
    work_dir: str | Path,
    request: Any,
    execution: Any,
    *,
    route_id: str,
    warnings: Sequence[str] = (),
) -> None:
    """Persist one committed execution route (idempotent, conflict-safe).

    ``ROUTE_PROGRESS.json`` is the atomic route commit authority: a snapshot
    not referenced by progress is uncommitted and may be deterministically
    overwritten when the route reruns.  A repeat write for an already
    committed route with byte/semantic-identical snapshot and entry is a
    no-op; different content/identity/warnings for the same route key is a
    fail-closed conflict that leaves the original unchanged.
    """

    root = Path(work_dir)
    root.mkdir(parents=True, exist_ok=True)
    progress = load_route_progress(root)
    same_request = [
        entry
        for entry in progress.execution
        if entry.request_id == request.request_id
    ]
    if len(same_request) > 1:
        raise RecoveryIntegrityError(
            "duplicate execution route progress entries for "
            f"{request.request_id}"
        )
    filename = (
        f"{EXECUTION_ROUTE_PREFIX}{_short_digest(request.request_id)}.json"
    )
    payload_digest = _digest(execution.model_dump(mode="json"))
    prospective = ExecutionRouteProgress(
        request_id=request.request_id,
        task_hash=request.task_hash,
        run_id=request.run_id,
        branch_id=request.branch_id,
        route_id=route_id,
        snapshot_filename=filename,
        snapshot_sha256="",
        payload_digest=payload_digest,
        warnings=tuple(dict.fromkeys(warnings)),
    )
    if same_request:
        entry = same_request[0]
        if entry.task_hash != request.task_hash:
            raise RecoveryIntegrityError(
                f"execution route request_id {request.request_id} is "
                "already committed with a different task_hash"
            )
        if entry.model_dump(
            exclude={"snapshot_sha256"}, mode="json"
        ) == prospective.model_dump(
            exclude={"snapshot_sha256"}, mode="json"
        ) and _route_snapshot_ok(
            root / filename,
            entry.snapshot_sha256,
            execution.model_dump(mode="json"),
        ):
            return
        raise RecoveryIntegrityError(
            f"execution route {request.request_id} already committed with "
            "conflicting "
            "content or identity"
        )
    snapshot = _canonical_json(execution.model_dump(mode="json"))
    atomic_write_text(root / filename, snapshot)
    snapshot_sha = _sha256_bytes((root / filename).read_bytes())
    entry = prospective.model_copy(
        update={"snapshot_sha256": snapshot_sha}
    )
    progress = progress.model_copy(
        update={
            "execution": progress.execution
            + (
                entry,
            )
        }
    )
    atomic_write_json(
        _route_progress_path(root),
        progress.model_dump(mode="json"),
    )


def write_asset_route(
    work_dir: str | Path,
    request: Any,
    execution: Any,
    asset: Any,
    *,
    warnings: Sequence[str] = (),
) -> None:
    """Persist one committed asset route (idempotent, conflict-safe)."""

    root = Path(work_dir)
    root.mkdir(parents=True, exist_ok=True)
    progress = load_route_progress(root)
    same_request = [
        entry
        for entry in progress.asset
        if entry.request_id == request.request_id
    ]
    if len(same_request) > 1:
        raise RecoveryIntegrityError(
            "duplicate asset route progress entries for "
            f"{request.request_id}"
        )
    execution_filename = (
        f"{EXECUTION_ROUTE_PREFIX}{_short_digest(request.request_id)}.json"
    )
    execution_entries = [
        entry
        for entry in progress.execution
        if entry.request_id == request.request_id
    ]
    if len(execution_entries) != 1:
        raise RecoveryIntegrityError(
            "asset route requires exactly one committed execution route "
            f"for {request.request_id}"
        )
    execution_entry = execution_entries[0]
    if (
        execution_entry.task_hash != request.task_hash
        or execution_entry.run_id != request.run_id
        or execution_entry.branch_id != request.branch_id
    ):
        raise RecoveryIntegrityError(
            f"asset route execution identity does not match the committed "
            f"execution route for {request.request_id}"
        )
    if not _route_snapshot_ok(
        root / execution_entry.snapshot_filename,
        execution_entry.snapshot_sha256,
        execution.model_dump(mode="json"),
    ):
        raise RecoveryIntegrityError(
            "asset route execution snapshot does not match the committed "
            "execution progress"
        )
    filename = (
        f"{ASSET_ROUTE_PREFIX}{_short_digest(request.request_id)}.json"
    )
    payload_digest = _digest(asset.model_dump(mode="json"))
    prospective = AssetRouteProgress(
        request_id=request.request_id,
        task_hash=request.task_hash,
        run_id=request.run_id,
        branch_id=request.branch_id,
        asset_result_id=asset.result_id,
        execution_snapshot_filename=execution_filename,
        snapshot_filename=filename,
        snapshot_sha256="",
        payload_digest=payload_digest,
        warnings=tuple(dict.fromkeys(warnings)),
    )
    if same_request:
        entry = same_request[0]
        if entry.task_hash != request.task_hash:
            raise RecoveryIntegrityError(
                f"asset route request_id {request.request_id} is already "
                "committed with a different task_hash"
            )
        if entry.model_dump(
            exclude={"snapshot_sha256"}, mode="json"
        ) == prospective.model_dump(
            exclude={"snapshot_sha256"}, mode="json"
        ) and _route_snapshot_ok(
            root / filename,
            entry.snapshot_sha256,
            asset.model_dump(mode="json"),
        ):
            return
        raise RecoveryIntegrityError(
            f"asset route {request.request_id} already committed with "
            "conflicting content or identity"
        )
    snapshot = _canonical_json(asset.model_dump(mode="json"))
    atomic_write_text(root / filename, snapshot)
    snapshot_sha = _sha256_bytes((root / filename).read_bytes())
    entry = prospective.model_copy(
        update={"snapshot_sha256": snapshot_sha}
    )
    progress = progress.model_copy(
        update={
            "asset": progress.asset
            + (
                entry,
            )
        }
    )
    atomic_write_json(
        _route_progress_path(root),
        progress.model_dump(mode="json"),
    )


def _route_snapshot_ok(
    path: Path,
    snapshot_sha256: str,
    model_dump: Mapping[str, Any],
) -> bool:
    """True only when canonical bytes, stored SHA, and content all match."""

    canonical = _canonical_json(model_dump)
    if snapshot_sha256 != _sha256_bytes(canonical.encode("utf-8")):
        return False
    if not path.is_file():
        return False
    actual = path.read_bytes()
    if _sha256_bytes(actual) != snapshot_sha256:
        return False
    try:
        return json.loads(actual.decode("utf-8")) == dict(model_dump)
    except (ValueError, UnicodeDecodeError):
        return False


def _load_ledger_payload(work_dir: Path) -> Dict[str, Any]:
    path = _ledger_path(work_dir)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RecoveryIntegrityError(
            f"RECOVERY_LEDGER.json is invalid: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise RecoveryIntegrityError(
            "RECOVERY_LEDGER.json must be a JSON object"
        )
    return dict(payload)


def _load_events(work_dir: Path, errors: List[str]) -> List[Dict[str, Any]]:
    events_path = work_dir / "PIPELINE_EVENTS.jsonl"
    events: List[Dict[str, Any]] = []
    if not events_path.is_file():
        return events
    for line_number, line in enumerate(
        events_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except ValueError as exc:
            errors.append(
                f"PIPELINE_EVENTS.jsonl line {line_number} is invalid: {exc}"
            )
            continue
        if not isinstance(payload, Mapping):
            errors.append(
                f"PIPELINE_EVENTS.jsonl line {line_number} is not an object"
            )
            continue
        events.append(dict(payload))
    return events


def _has_only_known_artifacts(root: Path) -> bool:
    for path in root.iterdir():
        if path.name in _KNOWN_ARTIFACTS:
            continue
        if path.name.startswith(CHECKPOINT_PREFIX) and path.suffix == ".json":
            continue
        if (
            len(path.name) >= 7
            and path.name[:2].isdigit()
            and path.name[2] == "-"
            and path.suffix == ".json"
        ):
            continue
        if path.name.startswith(EXECUTION_ROUTE_PREFIX) or path.name.startswith(
            ASSET_ROUTE_PREFIX
        ):
            continue
        return False
    return True


def _validate_checkpoint_record(
    record: PipelineCheckpointRecord,
    *,
    index: int,
    previous_id: str,
    request_digest_value: str,
    fingerprint: str,
    events: Sequence[Mapping[str, Any]],
    errors: List[str],
) -> bool:
    filename = checkpoint_filename(record.stage_sequence, record.stage)
    if record.checkpoint_id != compute_checkpoint_id(record):
        errors.append(
            f"checkpoint {filename} checkpoint_id does not match its "
            "recomputed content"
        )
        return False
    if record.previous_checkpoint_id != previous_id:
        errors.append(
            f"checkpoint {filename} breaks the previous-ID chain"
        )
        return False
    if record.request_digest != request_digest_value:
        errors.append(
            f"checkpoint {filename} request_digest does not match"
        )
        return False
    if record.runtime_fingerprint != fingerprint:
        errors.append(
            f"checkpoint {filename} runtime fingerprint does not match"
        )
        return False
    expected_sequence = index + 1
    if record.stage_sequence != expected_sequence:
        errors.append(
            f"checkpoint {filename} breaks the stage sequence order"
        )
        return False
    if record.stage != PIPELINE_STAGE_ORDER[record.stage_sequence - 1]:
        errors.append(
            f"checkpoint {filename} stage does not match the pipeline "
            "stage order"
        )
        return False
    if (
        record.receipt.sequence != record.stage_sequence
        or record.receipt.stage != record.stage
        or record.receipt.status != record.stage_status
        or record.receipt.payload_digest != record.payload_digest
    ):
        errors.append(
            f"checkpoint {filename} receipt is inconsistent with the "
            "checkpoint fields"
        )
        return False
    return True


def _pending_orphan_checkpoint(
    root: Path,
    committed: List[str],
    expected_next: str,
    request_digest_value: str,
    fingerprint: str,
    events: Sequence[Mapping[str, Any]],
    errors: List[str],
) -> Optional[PipelineCheckpointRecord]:
    """Structurally validate the exactly-next uncommitted checkpoint.

    This never mutates the ledger: inclusion is provisional until the
    pipeline has revalidated the strict models and cross-stage identities.
    """

    previous_id = ""
    for filename in committed:
        try:
            payload = json.loads(
                (root / filename).read_text(encoding="utf-8")
            )
            previous = PipelineCheckpointRecord.model_validate(payload)
        except (OSError, ValueError, ValidationError) as exc:
            errors.append(f"checkpoint {filename} is invalid: {exc}")
            return None
        previous_id = previous.checkpoint_id
    try:
        payload = json.loads(
            (root / expected_next).read_text(encoding="utf-8")
        )
        record = PipelineCheckpointRecord.model_validate(payload)
    except (OSError, ValueError, ValidationError) as exc:
        errors.append(f"uncommitted checkpoint {expected_next} is invalid: {exc}")
        return None
    if not _validate_checkpoint_record(
        record,
        index=len(committed),
        previous_id=previous_id,
        request_digest_value=request_digest_value,
        fingerprint=fingerprint,
        events=events,
        errors=errors,
    ):
        return None
    snapshot_path = root / record.snapshot_filename
    if not snapshot_path.is_file():
        errors.append(
            f"uncommitted checkpoint {expected_next} snapshot is missing: "
            f"{record.snapshot_filename}"
        )
        return None
    snapshot_bytes = snapshot_path.read_bytes()
    if _sha256_bytes(snapshot_bytes) != record.snapshot_sha256:
        errors.append(
            f"uncommitted checkpoint {expected_next} snapshot sha256 does "
            "not match"
        )
        return None
    try:
        snapshot_payload = json.loads(snapshot_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        errors.append(
            f"uncommitted checkpoint {expected_next} snapshot is invalid "
            f"JSON: {exc}"
        )
        return None
    if _digest(snapshot_payload) != record.payload_digest:
        errors.append(
            f"uncommitted checkpoint {expected_next} snapshot payload "
            "digest does not match"
        )
        return None
    if len(events) < record.stage_sequence:
        errors.append(
            f"uncommitted checkpoint {expected_next} event log is shorter "
            "than its stage"
        )
        return None
    prefix = events[: record.stage_sequence]
    if compute_event_prefix_digest(prefix) != record.event_prefix_digest:
        errors.append(
            f"uncommitted checkpoint {expected_next} event prefix digest "
            "does not match"
        )
        return None
    progress = load_route_progress(root)
    if record.stage == "execution":
        if record.route_progress_digest != (
            compute_execution_progress_digest(progress)
        ):
            errors.append(
                f"uncommitted checkpoint {expected_next} execution route "
                "progress digest does not match ROUTE_PROGRESS.json"
            )
            return None
    elif record.stage == "asset_compilation":
        if record.route_progress_digest != (
            compute_asset_progress_digest(progress)
        ):
            errors.append(
                f"uncommitted checkpoint {expected_next} asset route "
                "progress digest does not match ROUTE_PROGRESS.json"
            )
            return None
    return record


def _reconcile_committed_checkpoints(
    root: Path,
    ledger: Dict[str, Any],
    committed: List[str],
    on_disk_checkpoints: List[str],
    request_digest_value: str,
    fingerprint: str,
    events: List[Dict[str, Any]],
    errors: List[str],
) -> Optional[Tuple[List[str], Optional[PipelineCheckpointRecord]]]:
    """Recover crash-consistent stage commits before strict validation.

    Returns ``(committed, pending)``; a structurally valid orphan is
    returned as ``pending`` without mutating the ledger.
    """

    if not ledger:
        if not _has_only_known_artifacts(root):
            errors.append(
                "directory contains unrecognized files; refusing to resume"
            )
            return None
        atomic_write_json(
            _ledger_path(root),
            {
                "schema_version": LEDGER_SCHEMA_VERSION,
                "request_digest": request_digest_value,
                "runtime_fingerprint": fingerprint,
                "latest_checkpoint_id": "",
                "latest_sequence": 0,
                "committed_checkpoints": [],
            },
        )
        ledger = {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "request_digest": request_digest_value,
            "runtime_fingerprint": fingerprint,
            "committed_checkpoints": [],
        }
    if on_disk_checkpoints == sorted(committed):
        return committed, None
    expected_next = ""
    if len(committed) < len(PIPELINE_STAGE_ORDER):
        expected_next = checkpoint_filename(
            len(committed) + 1,
            PIPELINE_STAGE_ORDER[len(committed)],
        )
    if (
        expected_next
        and on_disk_checkpoints == sorted(committed + [expected_next])
    ):
        pending = _pending_orphan_checkpoint(
            root,
            committed,
            expected_next,
            request_digest_value,
            fingerprint,
            events,
            errors,
        )
        if pending is not None:
            return committed, pending
        return None
    errors.append(
        "committed checkpoint file set does not match the ledger (missing, "
        "extra, or out-of-order files)"
    )
    return None


def promote_pending_checkpoint(
    work_dir: str | Path,
    state: Dict[str, Any],
) -> None:
    """Atomically promote a validated pending checkpoint under the lock.

    Re-checks the current ledger still matches the expected pre-promotion
    state and re-validates the pending checkpoint structurally before the
    atomic ledger append.  A ``BaseException`` after this call is safe on the
    next resume because the checkpoint is then listed as committed.
    """

    pending = state.get("pending")
    if pending is None:
        return
    root = Path(work_dir)
    ledger = _load_ledger_payload(root)
    committed = [
        str(item)
        for item in (ledger.get("committed_checkpoints") or [])
        if str(item).strip()
    ]
    expected_committed = [
        str(item) for item in state.get("pre_promotion_committed") or []
    ]
    if committed != expected_committed:
        raise RecoveryIntegrityError(
            "cannot promote pending checkpoint: the ledger committed list "
            "changed since validation"
        )
    if (
        str(ledger.get("latest_checkpoint_id") or "")
        != str(state.get("pre_promotion_latest_id") or "")
    ):
        raise RecoveryIntegrityError(
            "cannot promote pending checkpoint: the ledger latest pointer "
            "changed since validation"
        )
    if int(ledger.get("latest_sequence") or 0) != int(
        state.get("pre_promotion_latest_sequence") or 0
    ):
        raise RecoveryIntegrityError(
            "cannot promote pending checkpoint: the ledger latest sequence "
            "changed since validation"
        )
    filename = checkpoint_filename(
        pending.stage_sequence, pending.stage
    )
    if filename in committed:
        raise RecoveryIntegrityError(
            f"pending checkpoint {filename} is already committed"
        )
    errors: List[str] = []
    events = _load_events(root, errors)
    if errors:
        raise RecoveryIntegrityError(
            "cannot promote pending checkpoint: " + "; ".join(errors)
        )
    revalidated = _pending_orphan_checkpoint(
        root,
        committed,
        filename,
        str(state.get("request_digest") or ""),
        str(state.get("runtime_fingerprint") or ""),
        events,
        errors,
    )
    if revalidated is None or errors:
        raise RecoveryIntegrityError(
            "cannot promote pending checkpoint: "
            + "; ".join(errors or ["structural revalidation failed"])
        )
    new_committed = committed + [filename]
    atomic_write_json(
        _ledger_path(root),
        {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "request_digest": revalidated.request_digest,
            "runtime_fingerprint": revalidated.runtime_fingerprint,
            "latest_checkpoint_id": revalidated.checkpoint_id,
            "latest_sequence": revalidated.stage_sequence,
            "committed_checkpoints": new_committed,
        },
    )


def validate_recovery_state(
    work_dir: str | Path,
    request: ArticlePipelineRequest,
    errors: List[str],
    warnings: List[str],
) -> Optional[Dict[str, Any]]:
    """Fail-closed validation of one work_dir recovery ledger.

    Verifies request identity, runtime fingerprint, the immutable checkpoint
    chain (order, previous IDs, recomputed checkpoint IDs), snapshot files,
    event prefixes, and route progress files.  Returns a raw recovery state
    or ``None``; nothing here trusts a merely re-hashed JSON payload.
    """

    root = Path(work_dir)
    request_path = root / "REQUEST.json"
    if not request_path.is_file():
        errors.append("REQUEST.json is missing; cannot resume")
        return None
    try:
        stored_request = json.loads(request_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        errors.append(f"REQUEST.json is invalid: {exc}")
        return None
    expected_request = request.model_dump(mode="json")
    if stored_request != expected_request:
        errors.append(
            "REQUEST.json does not match the immutable resume request"
        )
        return None
    request_digest_value = request_digest(request)

    ledger = _load_ledger_payload(root)
    fingerprint = article_runtime_fingerprint()
    if ledger:
        if str(ledger.get("schema_version") or "") != LEDGER_SCHEMA_VERSION:
            errors.append("RECOVERY_LEDGER.json has an unsupported schema")
            return None
        if str(ledger.get("request_digest") or "") != request_digest_value:
            errors.append(
                "RECOVERY_LEDGER.json request_digest does not match the "
                "request"
            )
            return None
        if str(ledger.get("runtime_fingerprint") or "") != fingerprint:
            errors.append(
                "runtime fingerprint does not match the recovery ledger"
            )
            return None
    committed = [
        str(item)
        for item in (ledger.get("committed_checkpoints") or [])
        if str(item).strip()
    ]
    if len(committed) > len(PIPELINE_STAGE_ORDER):
        errors.append("recovery ledger lists more checkpoints than stages")
        return None
    on_disk_checkpoints = sorted(
        path.name
        for path in root.glob(f"{CHECKPOINT_PREFIX}*.json")
        if path.is_file()
    )
    events = _load_events(root, errors)
    reconciled = _reconcile_committed_checkpoints(
        root,
        ledger,
        committed,
        on_disk_checkpoints,
        request_digest_value,
        fingerprint,
        events,
        errors,
    )
    if reconciled is None:
        return None
    committed, pending = reconciled
    effective_count = len(committed) + (1 if pending is not None else 0)
    if len(events) > effective_count:
        prefix_events = events[:effective_count]
        events_path = root / EVENTS_FILENAME
        atomic_write_text(
            events_path,
            "\n".join(
                json.dumps(item, sort_keys=True)
                for item in prefix_events
            )
            + ("\n" if prefix_events else ""),
        )
        events = prefix_events
    ledger = _load_ledger_payload(root)
    records: List[PipelineCheckpointRecord] = []
    previous_id = ""
    for index, filename in enumerate(committed):
        path = root / filename
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            record = PipelineCheckpointRecord.model_validate(payload)
        except (OSError, ValueError, ValidationError) as exc:
            errors.append(f"checkpoint {filename} is invalid: {exc}")
            return None
        if not _validate_checkpoint_record(
            record,
            index=index,
            previous_id=previous_id,
            request_digest_value=request_digest_value,
            fingerprint=fingerprint,
            events=events,
            errors=errors,
        ):
            return None
        snapshot_path = root / record.snapshot_filename
        if not snapshot_path.is_file():
            errors.append(
                f"checkpoint {filename} snapshot is missing: "
                f"{record.snapshot_filename}"
            )
            return None
        snapshot_bytes = snapshot_path.read_bytes()
        if _sha256_bytes(snapshot_bytes) != record.snapshot_sha256:
            errors.append(
                f"checkpoint {filename} snapshot sha256 does not match"
            )
            return None
        try:
            snapshot_payload = json.loads(
                snapshot_bytes.decode("utf-8")
            )
        except (ValueError, UnicodeDecodeError) as exc:
            errors.append(
                f"checkpoint {filename} snapshot is invalid JSON: {exc}"
            )
            return None
        if _digest(snapshot_payload) != record.payload_digest:
            errors.append(
                f"checkpoint {filename} snapshot payload digest does not "
                "match"
            )
            return None
        if len(events) < record.stage_sequence:
            errors.append(
                f"checkpoint {filename} event log is shorter than the "
                "committed stage"
            )
            return None
        prefix = events[: record.stage_sequence]
        if compute_event_prefix_digest(prefix) != record.event_prefix_digest:
            errors.append(
                f"checkpoint {filename} event prefix digest does not match"
            )
            return None
        records.append(record)
        previous_id = record.checkpoint_id

    if records:
        if (
            str(ledger.get("latest_checkpoint_id") or "")
            != records[-1].checkpoint_id
        ):
            errors.append(
                "RECOVERY_LEDGER.json latest checkpoint does not match the "
                "final committed checkpoint"
            )
            return None
        if int(ledger.get("latest_sequence") or 0) != records[-1].stage_sequence:
            errors.append(
                "RECOVERY_LEDGER.json latest sequence does not match the "
                "final committed checkpoint"
            )
            return None

    progress = load_route_progress(root)
    for record in records:
        if record.stage == "execution":
            if record.route_progress_digest != (
                compute_execution_progress_digest(progress)
            ):
                errors.append(
                    "checkpoint execution route progress digest does not "
                    "match ROUTE_PROGRESS.json execution entries"
                )
                return None
        elif record.stage == "asset_compilation":
            if record.route_progress_digest != (
                compute_asset_progress_digest(progress)
            ):
                errors.append(
                    "checkpoint asset route progress digest does not match "
                    "ROUTE_PROGRESS.json asset entries"
                )
                return None
    route_valid = _validate_route_progress_files(root, progress, errors)
    if not route_valid:
        return None
    if errors:
        return None

    payload_snapshots: Dict[str, Any] = {}
    for record in records:
        snapshot_path = root / record.snapshot_filename
        payload_snapshots[record.stage] = json.loads(
            snapshot_path.read_text(encoding="utf-8")
        )
    pending_payload = None
    if pending is not None:
        snapshot_path = root / pending.snapshot_filename
        pending_payload = json.loads(
            snapshot_path.read_text(encoding="utf-8")
        )
    return {
        "request_digest": request_digest_value,
        "runtime_fingerprint": fingerprint,
        "records": tuple(records),
        "events": tuple(events),
        "route_progress": progress,
        "payload_snapshots": payload_snapshots,
        "pending": pending,
        "pending_payload": pending_payload,
        "pre_promotion_committed": tuple(committed),
        "pre_promotion_latest_id": str(
            ledger.get("latest_checkpoint_id") or ""
        ),
        "pre_promotion_latest_sequence": int(
            ledger.get("latest_sequence") or 0
        ),
    }


def _validate_route_progress_files(
    root: Path,
    progress: PipelineRouteProgress,
    errors: List[str],
) -> bool:
    execution_request_ids = [entry.request_id for entry in progress.execution]
    if len(execution_request_ids) != len(set(execution_request_ids)):
        errors.append(
            "ROUTE_PROGRESS.json contains duplicate execution request_id "
            "entries"
        )
    asset_request_ids = [entry.request_id for entry in progress.asset]
    if len(asset_request_ids) != len(set(asset_request_ids)):
        errors.append(
            "ROUTE_PROGRESS.json contains duplicate asset request_id "
            "entries"
        )
    for entry in progress.execution:
        if not entry.request_id or not entry.task_hash:
            errors.append(
                "execution route progress entry lacks request/task identity"
            )
            continue
        path = root / entry.snapshot_filename
        if not path.is_file():
            errors.append(
                f"execution route snapshot is missing: {entry.snapshot_filename}"
            )
            continue
        snapshot = path.read_bytes()
        if _sha256_bytes(snapshot) != entry.snapshot_sha256:
            errors.append(
                f"execution route snapshot sha256 does not match "
                f"{entry.snapshot_filename}"
            )
            continue
        try:
            payload = json.loads(snapshot.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            errors.append(
                f"execution route snapshot is invalid JSON "
                f"{entry.snapshot_filename}: {exc}"
            )
            continue
        if _digest(payload) != entry.payload_digest:
            errors.append(
                f"execution route snapshot payload digest does not match "
                f"{entry.snapshot_filename}"
            )
    for entry in progress.asset:
        if not entry.request_id or not entry.task_hash:
            errors.append(
                "asset route progress entry lacks request/task identity"
            )
            continue
        if not (root / entry.execution_snapshot_filename).is_file():
            errors.append(
                "asset route progress references a missing execution "
                f"snapshot {entry.execution_snapshot_filename}"
            )
        path = root / entry.snapshot_filename
        if not path.is_file():
            errors.append(
                f"asset route snapshot is missing: {entry.snapshot_filename}"
            )
            continue
        snapshot = path.read_bytes()
        if _sha256_bytes(snapshot) != entry.snapshot_sha256:
            errors.append(
                f"asset route snapshot sha256 does not match "
                f"{entry.snapshot_filename}"
            )
            continue
        try:
            payload = json.loads(snapshot.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            errors.append(
                f"asset route snapshot is invalid JSON "
                f"{entry.snapshot_filename}: {exc}"
            )
            continue
        if _digest(payload) != entry.payload_digest:
            errors.append(
                f"asset route snapshot payload digest does not match "
                f"{entry.snapshot_filename}"
            )
    return not errors


__all__ = [
    "AssetRouteProgress",
    "ExecutionRouteProgress",
    "PipelineCheckpointRecord",
    "PipelineRouteProgress",
    "RecoveryIntegrityError",
    "compute_asset_progress_digest",
    "compute_checkpoint_id",
    "compute_event_prefix_digest",
    "compute_execution_progress_digest",
    "compute_route_progress_digest",
    "load_route_progress",
    "promote_pending_checkpoint",
    "request_digest",
    "validate_recovery_state",
    "write_asset_route",
    "write_checkpoint",
    "write_execution_route",
]
