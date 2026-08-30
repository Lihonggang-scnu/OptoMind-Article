"""Production-oriented resumable continuation of an accepted Article run.

The continuation consumes a completed eight-stage ``ArticlePipeline`` work
directory (read-only) and runs the next bounded milestone in fixed order:
result synthesis, architecture, section writing, review/revision, and
manuscript assembly.  It calls the accepted existing APIs only and never
copies their scientific logic.

Trust boundaries:
- The source pipeline directory is immutable evidence.  All route snapshots,
  hashes, and identities are re-verified before any provider is called.
- The continuation work directory is write-once for a new run and supports
  resume through a tamper-evident checkpoint chain and an append-only attempt
  audit.
- Accepted stage checkpoints carry only strict stage models.  Failed or
  unavailable attempts are recorded in the append-only attempt log and are
  retryable on resume; they never become terminal scientific state.
- Ordinary provider unavailability/malformed responses fail open per stage;
  source/identity/hash/path conflicts fail closed.
- Qwen (locked to ``qwen3.7-flash``) fills only semantic content.  Local code
  owns schemas, IDs, provenance, story selection, and state.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from optomind_research.runtime.artifact_store import (
    atomic_write_json,
    atomic_write_text,
)

from optomind_optics.harness.article_assets import (
    ArticleAssetCompilationResult,
    compute_asset_compilation_result_id,
)
from optomind_optics.harness.article_architecture import (
    ArtifactDescriptor,
    ArticleArchitectureResult,
    build_article_architecture,
    value_field_shapes,
)
from optomind_optics.harness.article_claims import ClaimLedgerResult
from optomind_optics.harness.article_director import ArticleDirectorPlan
from optomind_optics.harness.article_execution import ArticleExecutionResult
from optomind_optics.harness.article_literature import (
    LiteratureSupplement,
    build_literature_provider_context,
    load_literature_supplement,
)
from optomind_optics.harness.article_experiment_planning import (
    ArticleExperimentPlanningResult,
)
from optomind_optics.harness.article_manuscript import (
    ArticleManuscriptPackage,
    build_article_manuscript,
)
from optomind_optics.harness.article_pipeline import (
    ArticlePipelineRequest,
    ArticlePipelineResult,
    compute_pipeline_result_id,
)
from optomind_optics.harness.article_pipeline_recovery import (
    PipelineRouteProgress,
)
from optomind_optics.harness.article_result_synthesis import (
    ArticleResultSynthesisResult,
    ResultSynthesisProvider,
    synthesize_article_results,
)
from optomind_optics.harness.article_review import (
    ArticleReviewResult,
    build_article_review,
)
from optomind_optics.harness.article_runtime import (
    RuntimeLock,
    RuntimeLockError,
    article_runtime_fingerprint,
)
from optomind_optics.harness.article_writing import (
    ArticleDraftBundle,
    TrustedValueRecord,
    build_article_draft_bundle,
)
from optomind_research.runtime.cost_ledger import estimate_call_cost_cny


CONTINUATION_SCHEMA_VERSION = "article-continuation.v1"
CONTINUATION_REQUEST_SCHEMA_VERSION = "article-continuation-request.v1"
CONTINUATION_RECEIPT_SCHEMA_VERSION = "continuation-stage-receipt.v1"
CONTINUATION_EVENT_SCHEMA_VERSION = "continuation-event.v1"
CONTINUATION_ATTEMPT_SCHEMA_VERSION = "continuation-attempt.v1"
CONTINUATION_CHECKPOINT_SCHEMA_VERSION = "continuation-checkpoint.v1"
CONTINUATION_LEDGER_SCHEMA_VERSION = "continuation-ledger.v1"

REQUEST_FILENAME = "REQUEST.json"
FINAL_RESULT_FILENAME = "FINAL_PIPELINE_RESULT.json"
ROUTE_PROGRESS_FILENAME = "ROUTE_PROGRESS.json"
CONTINUATION_REQUEST_FILENAME = "CONTINUATION_REQUEST.json"
CONTINUATION_EVENTS_FILENAME = "CONTINUATION_EVENTS.jsonl"
CONTINUATION_ATTEMPTS_FILENAME = "CONTINUATION_ATTEMPTS.jsonl"
CONTINUATION_VERSIONS_FILENAME = "CONTINUATION_VERSIONS.jsonl"
CONTINUATION_LEDGER_FILENAME = "CONTINUATION_LEDGER.json"
FINAL_CONTINUATION_RESULT_FILENAME = "FINAL_CONTINUATION_RESULT.json"
CONTINUATION_LOCK_FILENAME = "CONTINUATION_RUNTIME_LOCK.json"
MANUSCRIPT_DIRECTORY = "manuscript"

EXECUTION_ROUTE_PREFIX = "route-execution-"
ASSET_ROUTE_PREFIX = "route-asset-"
CHECKPOINT_PREFIX = "checkpoint-"
CONTINUATION_STAGE_ORDER = (
    "result_synthesis",
    "architecture",
    "writing",
    "review",
    "manuscript",
)

_STAGE_PAYLOAD_MODELS = {
    "result_synthesis": ArticleResultSynthesisResult,
    "architecture": ArticleArchitectureResult,
    "writing": ArticleDraftBundle,
    "review": ArticleReviewResult,
    "manuscript": ArticleManuscriptPackage,
}


class ContinuationIntegrityError(ValueError):
    """Source or continuation integrity failed closed."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ContinuationRequest(_StrictModel):
    schema_version: Literal["article-continuation-request.v1"] = (
        "article-continuation-request.v1"
    )
    run_id: str
    branch_id: str = "root"
    source_pipeline_dir: str
    work_dir: str
    selected_story_id: str = ""
    literature_supplement_path: str = ""

    @field_validator("run_id", "source_pipeline_dir", "work_dir")
    @classmethod
    def _non_empty_text(cls, value: str, info: Any) -> str:
        if not str(value or "").strip():
            raise ValueError(f"{info.field_name} must be non-empty")
        return str(value).strip()


class ContinuationStageReceipt(_StrictModel):
    schema_version: Literal["continuation-stage-receipt.v1"] = (
        "continuation-stage-receipt.v1"
    )
    sequence: int
    stage: str
    status: Literal["completed", "partial", "unavailable", "failed", "skipped"]
    input_ids: Tuple[str, ...] = Field(default_factory=tuple)
    output_ids: Tuple[str, ...] = Field(default_factory=tuple)
    warnings: Tuple[str, ...] = Field(default_factory=tuple)
    errors: Tuple[str, ...] = Field(default_factory=tuple)
    payload_digest: str = ""
    hard_failure: bool = False


class ContinuationEvent(_StrictModel):
    schema_version: Literal["continuation-event.v1"] = "continuation-event.v1"
    sequence: int
    stage: str
    status: Literal["completed", "partial", "unavailable", "failed", "skipped"]
    input_ids: Tuple[str, ...] = Field(default_factory=tuple)
    output_ids: Tuple[str, ...] = Field(default_factory=tuple)
    warnings: Tuple[str, ...] = Field(default_factory=tuple)
    errors: Tuple[str, ...] = Field(default_factory=tuple)
    payload_digest: str = ""
    hard_failure: bool = False
    event_id: str = ""


class ContinuationAttemptRecord(_StrictModel):
    schema_version: Literal["continuation-attempt.v1"] = "continuation-attempt.v1"
    attempt_id: str
    sequence: int
    stage: str
    status: Literal["unavailable", "failed"]
    payload_digest: str = ""
    warnings: Tuple[str, ...] = Field(default_factory=tuple)
    errors: Tuple[str, ...] = Field(default_factory=tuple)
    usage: Any = Field(default_factory=dict)


class ContinuationCheckpointRecord(_StrictModel):
    schema_version: Literal["continuation-checkpoint.v1"] = "continuation-checkpoint.v1"
    request_digest: str
    source_digest: str
    runtime_fingerprint: str
    stage_sequence: int
    stage: str
    receipt: ContinuationStageReceipt
    snapshot_filename: str
    snapshot_sha256: str
    payload_digest: str
    event_prefix_digest: str
    previous_checkpoint_id: str
    checkpoint_id: str


class ContinuationLedger(_StrictModel):
    schema_version: Literal["continuation-ledger.v1"] = "continuation-ledger.v1"
    request_digest: str
    source_digest: str
    runtime_fingerprint: str
    latest_checkpoint_id: str = ""
    latest_sequence: int = 0
    committed_checkpoints: Tuple[str, ...] = Field(default_factory=tuple)


class ContinuationStagePayloads(_StrictModel):
    result_synthesis: Optional[ArticleResultSynthesisResult] = None
    architecture: Optional[ArticleArchitectureResult] = None
    writing: Optional[ArticleDraftBundle] = None
    review: Optional[ArticleReviewResult] = None
    manuscript: Optional[ArticleManuscriptPackage] = None


class ContinuationResult(_StrictModel):
    schema_version: Literal["article-continuation.v1"] = "article-continuation.v1"
    status: Literal["completed", "partial", "unavailable", "failed"]
    result_id: str = ""
    run_id: str
    branch_id: str = ""
    source_pipeline_dir: str
    source_result_id: str = ""
    receipts: Tuple[ContinuationStageReceipt, ...] = Field(default_factory=tuple)
    selected_story_id: str = ""
    story_selection_rationale: str = ""
    story_candidates: Tuple[Dict[str, Any], ...] = Field(default_factory=tuple)
    stage_payloads: ContinuationStagePayloads = Field(
        default_factory=ContinuationStagePayloads
    )
    counts: Dict[str, Any] = Field(default_factory=dict)
    usage: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


@dataclass(frozen=True)
class SourcePipelineBundle:
    request: ArticlePipelineRequest
    result: ArticlePipelineResult
    plan: ArticleDirectorPlan
    planning: ArticleExperimentPlanningResult
    executions: Tuple[ArticleExecutionResult, ...]
    assets: Tuple[ArticleAssetCompilationResult, ...]
    execution_by_request: Mapping[str, ArticleExecutionResult]
    asset_by_request: Mapping[str, ArticleAssetCompilationResult]
    source_digest: str
    literature_supplement: Optional[LiteratureSupplement] = None
    literature_supplement_digest: str = ""


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
        part if isinstance(part, (dict, list, tuple)) else str(part) for part in parts
    ]
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _short_digest(*parts: Any) -> str:
    return _digest(*parts)[:16]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path, label: str) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except ValueError as exc:
            raise ContinuationIntegrityError(
                f"{label} line {line_number} is malformed: {exc}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise ContinuationIntegrityError(
                f"{label} line {line_number} is not an object"
            )
        rows.append(dict(payload))
    return rows


def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    lines: List[str] = []
    if path.is_file():
        lines.extend(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    lines.append(json.dumps(dict(payload), sort_keys=True))
    atomic_write_text(path, "\n".join(lines) + "\n")


def continuation_request_digest(request: ContinuationRequest) -> str:
    payload = request.model_dump(mode="json")
    payload.pop("work_dir", None)
    return _digest(payload)


def _payload_dump(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, Mapping):
        return dict(payload)
    if hasattr(payload, "model_dump"):
        return payload.model_dump(mode="json")
    return {"value": str(payload)}


def _load_strict(path: Path, model_type: Any, label: str) -> Any:
    try:
        payload = _read_json(path)
    except (OSError, ValueError) as exc:
        raise ContinuationIntegrityError(
            f"{label} is unreadable: {type(exc).__name__}: {exc}"
        ) from exc
    try:
        return model_type.model_validate(payload)
    except ValidationError as exc:
        raise ContinuationIntegrityError(f"{label} is invalid: {exc}") from exc


def _event_id(event: ContinuationEvent) -> str:
    return _digest(event.model_dump(exclude={"event_id"}, mode="json"))


def _event_from_receipt(receipt: ContinuationStageReceipt) -> ContinuationEvent:
    model = ContinuationEvent(
        sequence=receipt.sequence,
        stage=receipt.stage,
        status=receipt.status,
        input_ids=receipt.input_ids,
        output_ids=receipt.output_ids,
        warnings=receipt.warnings,
        errors=receipt.errors,
        payload_digest=receipt.payload_digest,
        hard_failure=receipt.hard_failure,
        event_id="",
    )
    return model.model_copy(update={"event_id": _event_id(model)})


def _verify_snapshot(
    root: Path,
    filename: str,
    *,
    expected_sha256: str,
    expected_payload_digest: str,
    model_type: Any,
    label: str,
) -> Any:
    path = root / filename
    if not path.is_file():
        raise ContinuationIntegrityError(f"{label} snapshot {filename} is missing")
    raw_bytes = path.read_bytes()
    if _sha256_bytes(raw_bytes) != expected_sha256:
        raise ContinuationIntegrityError(
            f"{label} snapshot {filename} SHA256 does not match route progress"
        )
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ContinuationIntegrityError(
            f"{label} snapshot {filename} is not valid JSON: {exc}"
        ) from exc
    model = model_type.model_validate(payload)
    if _digest(model.model_dump(mode="json")) != expected_payload_digest:
        raise ContinuationIntegrityError(
            f"{label} snapshot {filename} payload digest does not match "
            "route progress"
        )
    if _canonical_json(payload) != _canonical_json(model.model_dump(mode="json")):
        raise ContinuationIntegrityError(
            f"{label} snapshot {filename} content is not canonical"
        )
    return model


def _stage_input_ids(bundle: SourcePipelineBundle) -> Tuple[str, ...]:
    return (
        f"pipeline:{bundle.result.result_id}",
        f"planning:{_short_digest(bundle.planning.model_dump(mode='json'))}",
    )


def _stage_output_id(stage: str, payload: Any) -> Tuple[str, ...]:
    if payload is None:
        return ()
    return (f"{stage}:{_short_digest(_payload_dump(payload))}",)


def load_source_pipeline(source_dir: str | Path) -> SourcePipelineBundle:
    """Read-only, fail-closed load and verification of one pipeline run."""

    root = Path(source_dir).resolve()
    if not root.is_dir():
        raise ContinuationIntegrityError(
            f"source pipeline directory is not a directory: {root}"
        )
    request = _load_strict(
        root / REQUEST_FILENAME, ArticlePipelineRequest, "REQUEST.json"
    )
    result = _load_strict(
        root / FINAL_RESULT_FILENAME,
        ArticlePipelineResult,
        "FINAL_PIPELINE_RESULT.json",
    )
    if result.result_id != compute_pipeline_result_id(result):
        raise ContinuationIntegrityError(
            "FINAL_PIPELINE_RESULT.json result_id does not match recomputed content"
        )
    if result.run_id != request.run_id:
        raise ContinuationIntegrityError(
            "FINAL_PIPELINE_RESULT.json run_id does not match REQUEST.json"
        )
    if result.question != request.question:
        raise ContinuationIntegrityError(
            "FINAL_PIPELINE_RESULT.json question does not match REQUEST.json"
        )
    if result.experiment_planning is None:
        raise ContinuationIntegrityError(
            "FINAL_PIPELINE_RESULT.json has no experiment planning payload"
        )
    if result.director_plan is None or result.director_plan.plan is None:
        raise ContinuationIntegrityError(
            "FINAL_PIPELINE_RESULT.json has no director plan"
        )
    progress = _load_strict(
        root / ROUTE_PROGRESS_FILENAME,
        PipelineRouteProgress,
        "ROUTE_PROGRESS.json",
    )

    planning = result.experiment_planning
    ready_rows = [
        row
        for row in planning.rows
        if row.status == "ready" and row.request is not None
    ]
    ready_keys = {(row.request.request_id, row.request.task_hash) for row in ready_rows}
    ready_by_request = {row.request.request_id: row for row in ready_rows}
    if len(ready_by_request) != len(ready_rows):
        raise ContinuationIntegrityError(
            "experiment planning contains duplicate ready request_id values"
        )

    execution_ids = [entry.request_id for entry in progress.execution]
    asset_ids = [entry.request_id for entry in progress.asset]
    if len(execution_ids) != len(set(execution_ids)):
        raise ContinuationIntegrityError(
            "ROUTE_PROGRESS.json contains duplicate execution request_id values"
        )
    if len(asset_ids) != len(set(asset_ids)):
        raise ContinuationIntegrityError(
            "ROUTE_PROGRESS.json contains duplicate asset request_id values"
        )
    if result.execution_count != len(progress.execution):
        raise ContinuationIntegrityError(
            "FINAL_PIPELINE_RESULT.json execution_count does not match "
            "committed execution progress"
        )
    execution_keys = {
        (entry.request_id, entry.task_hash) for entry in progress.execution
    }
    asset_keys = {(entry.request_id, entry.task_hash) for entry in progress.asset}
    if not execution_keys <= ready_keys:
        raise ContinuationIntegrityError(
            "route execution progress references rows that are not ready in "
            f"experiment planning: {sorted(execution_keys - ready_keys)}"
        )
    if not asset_keys <= execution_keys:
        raise ContinuationIntegrityError(
            "route asset progress references executions that are not "
            f"committed: {sorted(asset_keys - execution_keys)}"
        )
    for entry in progress.execution:
        if entry.run_id != request.run_id or entry.branch_id != request.branch_id:
            raise ContinuationIntegrityError(
                f"execution route {entry.request_id} run/branch does not "
                "match source REQUEST"
            )
        row = ready_by_request.get(entry.request_id)
        if row is None or row.request.task_hash != entry.task_hash:
            raise ContinuationIntegrityError(
                f"execution route {entry.request_id} does not match a ready "
                "planning row"
            )
        if entry.route_id != row.route_id:
            raise ContinuationIntegrityError(
                f"execution route {entry.request_id} route_id does not match "
                "its planning row"
            )

    referenced_snapshots = {entry.snapshot_filename for entry in progress.execution} | {
        entry.snapshot_filename for entry in progress.asset
    }
    for path in sorted(root.iterdir()):
        name = path.name
        if not name.startswith(EXECUTION_ROUTE_PREFIX) and not name.startswith(
            ASSET_ROUTE_PREFIX
        ):
            continue
        if not name.endswith(".json") or name not in referenced_snapshots:
            raise ContinuationIntegrityError(
                f"source directory contains extra or unreferenced route "
                f"snapshot: {name}"
            )

    executions: List[ArticleExecutionResult] = []
    assets: List[ArticleAssetCompilationResult] = []
    asset_result_ids: Dict[str, str] = {}
    for entry in progress.execution:
        row = ready_by_request.get(entry.request_id)
        if row is None or row.request.task_hash != entry.task_hash:
            raise ContinuationIntegrityError(
                f"execution route {entry.request_id} does not match a ready "
                "planning row"
            )
        execution = _verify_snapshot(
            root,
            entry.snapshot_filename,
            expected_sha256=entry.snapshot_sha256,
            expected_payload_digest=entry.payload_digest,
            model_type=ArticleExecutionResult,
            label="execution route",
        )
        if (
            execution.request_id != entry.request_id
            or execution.task_hash != entry.task_hash
            or execution.observation.experiment_id
            != row.request.experiment.experiment_id
        ):
            raise ContinuationIntegrityError(
                f"execution route {entry.request_id} identity mismatch"
            )
        executions.append(execution)
    for entry in progress.asset:
        if entry.run_id != request.run_id or entry.branch_id != request.branch_id:
            raise ContinuationIntegrityError(
                f"asset route {entry.request_id} run/branch does not "
                "match source REQUEST"
            )
        execution_entry = next(
            item for item in progress.execution if item.request_id == entry.request_id
        )
        if entry.execution_snapshot_filename != execution_entry.snapshot_filename:
            raise ContinuationIntegrityError(
                f"asset route {entry.request_id} execution snapshot filename "
                "does not match its execution route"
            )
        asset = _verify_snapshot(
            root,
            entry.snapshot_filename,
            expected_sha256=entry.snapshot_sha256,
            expected_payload_digest=entry.payload_digest,
            model_type=ArticleAssetCompilationResult,
            label="asset route",
        )
        if asset.result_id != entry.asset_result_id:
            raise ContinuationIntegrityError(
                f"asset route {entry.request_id} result_id does not match "
                "route progress"
            )
        if compute_asset_compilation_result_id(asset) != asset.result_id:
            raise ContinuationIntegrityError(
                f"asset route {entry.request_id} result_id does not match "
                "recomputed content"
            )
        execution = next(
            item for item in executions if item.request_id == entry.request_id
        )
        if (
            asset.request_id != entry.request_id
            or asset.task_hash != entry.task_hash
            or asset.run_id != entry.run_id
            or asset.observation.observation_id != execution.observation.observation_id
        ):
            raise ContinuationIntegrityError(
                f"asset route {entry.request_id} identity does not match "
                "its execution route"
            )
        assets.append(asset)
        asset_result_ids[entry.request_id] = asset.result_id

    pipeline_assets = {item.request_id: item for item in result.asset_compilations}
    if set(pipeline_assets) != set(asset_result_ids):
        raise ContinuationIntegrityError(
            "FINAL_PIPELINE_RESULT.json asset compilations do not match "
            "route progress"
        )
    for entry in progress.asset:
        pipeline_asset = pipeline_assets.get(entry.request_id)
        if pipeline_asset is None or (
            compute_asset_compilation_result_id(pipeline_asset) != entry.asset_result_id
        ):
            raise ContinuationIntegrityError(
                f"asset route {entry.request_id} does not match "
                "FINAL_PIPELINE_RESULT.json asset compilations"
            )

    executions_sorted = tuple(sorted(executions, key=lambda item: item.request_id))
    assets_sorted = tuple(sorted(assets, key=lambda item: item.request_id))
    source_request_payload = request.model_dump(mode="json")
    source_request_payload.pop("work_dir", None)
    source_digest = _digest(
        _digest(source_request_payload),
        result.result_id,
        [entry.model_dump(mode="json") for entry in progress.execution],
        [entry.model_dump(mode="json") for entry in progress.asset],
    )
    return SourcePipelineBundle(
        request=request,
        result=result,
        plan=result.director_plan.plan,
        planning=planning,
        executions=executions_sorted,
        assets=assets_sorted,
        execution_by_request={item.request_id: item for item in executions_sorted},
        asset_by_request={item.request_id: item for item in assets_sorted},
        source_digest=source_digest,
    )


def _contracted_inventory(
    synthesis: ArticleResultSynthesisResult,
    bundle: SourcePipelineBundle,
) -> Tuple[Tuple[ArtifactDescriptor, ...], Tuple[TrustedValueRecord, ...]]:
    asset_by_observation: Dict[str, ArticleAssetCompilationResult] = {}
    for asset in bundle.assets:
        observation_id = str(
            asset.observation.observation_id
            if asset.observation is not None
            else asset.observation_id or ""
        )
        if observation_id in asset_by_observation:
            raise ContinuationIntegrityError(
                f"duplicate source asset observation_id {observation_id!r} "
                "across routes"
            )
        asset_by_observation[observation_id] = asset

    descriptor_by_artifact: Dict[str, ArtifactDescriptor] = {}
    value_by_key: Dict[Tuple[str, str], TrustedValueRecord] = {}
    missing_observations: List[str] = []
    for observation in synthesis.observations:
        observation_id = observation.observation_id
        asset = asset_by_observation.get(observation_id)
        if asset is None:
            missing_observations.append(observation_id)
            continue
        descriptor_map = {item.artifact_id: item for item in asset.descriptors}
        retained_artifact_ids = set(observation.artifact_ids)
        for artifact_id in observation.artifact_ids:
            descriptor = descriptor_map.get(artifact_id)
            if descriptor is None:
                raise ContinuationIntegrityError(
                    f"retained observation {observation_id!r} references "
                    f"missing descriptor {artifact_id!r} in its source asset"
                )
            existing = descriptor_by_artifact.get(artifact_id)
            if existing is not None and existing != descriptor:
                raise ContinuationIntegrityError(
                    f"ambiguous retained artifact_id {artifact_id!r} across "
                    "observations with conflicting provenance"
                )
            descriptor_by_artifact[artifact_id] = descriptor
        for value in asset.trusted_values:
            if value.artifact_id not in retained_artifact_ids:
                continue
            key = (value.artifact_id, value.field)
            existing = value_by_key.get(key)
            if existing is not None and existing != value:
                raise ContinuationIntegrityError(
                    "ambiguous duplicate trusted value across observations: "
                    f"{key[0]}:{key[1]}"
                )
            value_by_key[key] = value
    if missing_observations:
        raise ContinuationIntegrityError(
            "synthesis observations have no matching source asset: "
            f"{sorted(set(missing_observations))}"
        )
    descriptors = tuple(
        sorted(descriptor_by_artifact.values(), key=lambda item: item.artifact_id)
    )
    values = tuple(
        value_by_key[key]
        for key in sorted(value_by_key, key=lambda item: (item[0], item[1]))
    )
    return descriptors, values


def _scoped_story_values(
    architecture: ArticleArchitectureResult,
    selected_story_id: str,
    values: Sequence[TrustedValueRecord],
    ledger: Optional[Any] = None,
) -> Tuple[TrustedValueRecord, ...]:
    """Restrict trusted values to figure or claim-lineage bindings."""

    story = next(
        (item for item in architecture.stories if item.story_id == selected_story_id),
        None,
    )
    if story is None:
        raise ContinuationIntegrityError(
            f"selected story {selected_story_id!r} is not present in the "
            "architecture result"
        )
    value_by_key: Dict[Tuple[str, str], TrustedValueRecord] = {}
    for value in values:
        key = (value.artifact_id, value.field)
        if key in value_by_key:
            raise ContinuationIntegrityError(
                f"duplicate trusted value record for {key[0]!r}:{key[1]!r}"
            )
        value_by_key[key] = value
    bound_keys: Dict[Tuple[str, str], bool] = {}
    for figure in story.figure_contracts:
        for binding in figure.artifact_bindings:
            for field in binding.selected_fields:
                bound_keys[(binding.artifact_id, field)] = True
    if ledger is not None:
        claims_by_id = {claim.claim_id: claim for claim in ledger.claims}
        for section in story.section_contracts:
            for placement in section.claim_bindings:
                claim = claims_by_id.get(placement.claim_id)
                if claim is None:
                    continue
                for ref in claim.metadata.get("value_lineage") or []:
                    artifact_id = str(ref.get("artifact_id") or "")
                    field = str(ref.get("field") or "")
                    if not (artifact_id and field):
                        continue
                    key = (artifact_id, field)
                    bound_keys[key] = True
                    if key not in value_by_key:
                        raise ContinuationIntegrityError(
                            f"claim {claim.claim_id} value lineage "
                            f"{artifact_id!r}:{field!r} has no matching "
                            "trusted value record in the contracted inventory"
                        )
    return tuple(
        value for value in values if (value.artifact_id, value.field) in bound_keys
    )


def _select_story(
    architecture: ArticleArchitectureResult,
    selected_story_id: str,
    errors: List[str],
) -> Tuple[str, str, Tuple[Dict[str, Any], ...]]:
    candidates = tuple(
        {
            "story_id": story.story_id,
            "recommendation_score": float(story.recommendation_score),
            "assigned_claim_count": len(story.claim_assignments),
            "omitted_claim_count": len(story.omitted_claims),
            "coverage_fraction": (
                round(
                    len(story.claim_assignments)
                    / (len(story.claim_assignments) + len(story.omitted_claims)),
                    6,
                )
                if story.claim_assignments or story.omitted_claims
                else 0.0
            ),
        }
        for story in sorted(
            architecture.stories,
            key=lambda item: (
                -(
                    len(item.claim_assignments)
                    / (len(item.claim_assignments) + len(item.omitted_claims))
                    if item.claim_assignments or item.omitted_claims
                    else 0.0
                ),
                -len(item.claim_assignments),
                -float(item.recommendation_score),
                str(item.story_id),
            ),
        )
    )
    if not candidates:
        errors.append("architecture produced no story candidates")
        return "", "", candidates
    if selected_story_id:
        if selected_story_id not in {item["story_id"] for item in candidates}:
            errors.append(
                f"selected story {selected_story_id!r} is not in the "
                "architecture story candidates"
            )
            return "", "", candidates
        chosen = selected_story_id
        rationale = f"explicit selected_story_id {selected_story_id} was used"
    else:
        chosen = str(candidates[0]["story_id"])
        rationale = (
            "coverage-aware deterministic default: highest assigned-claim "
            "coverage, then most assigned claims, then highest "
            f"recommendation_score with story_id tie-break selected {chosen}"
        )
    return chosen, rationale, candidates


def _stage_usage_rows(
    stage: str,
    usage: Mapping[str, Any] | Sequence[Mapping[str, Any]],
) -> List[Tuple[str, Dict[str, Any]]]:
    if isinstance(usage, Sequence) and not isinstance(usage, (str, bytes)):
        return [
            (stage, dict(item)) for item in usage if isinstance(item, Mapping) and item
        ]
    if isinstance(usage, Mapping) and usage:
        return [(stage, dict(usage))]
    return []


def _aggregate_usage(
    stages: Sequence[Tuple[str, Any]],
    attempts: Sequence[ContinuationAttemptRecord | Mapping[str, Any]] = (),
) -> Dict[str, Any]:
    rows: List[Tuple[str, Dict[str, Any]]] = []
    for stage, payload in stages:
        if payload is None:
            continue
        if stage == "result_synthesis":
            rows.extend(_stage_usage_rows(stage, payload.provider_usage))
        elif stage == "architecture" and payload.usage:
            rows.append((stage, dict(payload.usage)))
        elif stage == "writing" and payload.usage:
            rows.append((stage, dict(payload.usage)))
        elif stage == "review" and payload.usage:
            rows.append((stage, dict(payload.usage)))
    for raw in attempts:
        try:
            attempt = (
                raw
                if isinstance(raw, ContinuationAttemptRecord)
                else ContinuationAttemptRecord.model_validate(raw)
            )
        except ValidationError:
            continue
        if attempt.status not in {"unavailable", "failed"}:
            continue
        rows.extend(_stage_usage_rows(attempt.stage, attempt.usage))
    totals = {
        "logical_call_count": 0,
        "request_attempt_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated_cost_cny": 0.0,
    }
    for stage, row in rows:
        input_tokens = int(
            row.get("input_tokens") or row.get("estimated_input_tokens") or 0
        )
        output_tokens = int(
            row.get("output_tokens") or row.get("estimated_output_tokens") or 0
        )
        total = int(row.get("total_tokens") or 0) or (input_tokens + output_tokens)
        model = str(row.get("model_name") or "qwen3.7-flash")
        cost = float(
            row.get("estimated_list_price_cost_cny")
            or row.get("estimated_cost_cny")
            or 0.0
        )
        if not cost and (input_tokens or output_tokens):
            cost = float(estimate_call_cost_cny(model, input_tokens, output_tokens))
        totals["logical_call_count"] += 1
        totals["request_attempt_count"] += (
            int(row.get("request_attempt_count") or row.get("call_count") or 0) or 1
        )
        totals["input_tokens"] += input_tokens
        totals["output_tokens"] += output_tokens
        totals["total_tokens"] += total
        totals["estimated_cost_cny"] = round(totals["estimated_cost_cny"] + cost, 8)
    return {
        "rows": [{"stage": stage, **dict(row)} for stage, row in rows],
        "totals": totals,
    }


def compute_continuation_result_id(
    result: ContinuationResult | Mapping[str, Any],
) -> str:
    model = (
        result
        if isinstance(result, ContinuationResult)
        else ContinuationResult.model_validate(result)
    )
    payload = model.model_dump(mode="json")
    payload.pop("result_id", None)
    return _digest(payload)


class _ContinuationRunner:
    def __init__(
        self,
        continuation: "ArticleContinuation",
        request: ContinuationRequest,
        work_dir: Path,
        bundle: SourcePipelineBundle,
        resume_state: Optional[Dict[str, Any]] = None,
        fault_hook: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.continuation = continuation
        self.request = request
        self.work_dir = work_dir
        self.bundle = bundle
        self.fault_hook = fault_hook
        self.receipts: List[ContinuationStageReceipt] = []
        self.payloads = ContinuationStagePayloads()
        self.warnings: List[str] = []
        self.errors: List[str] = []
        self.last_checkpoint_id = ""
        self.last_sequence = 0
        self.selected_story_id = request.selected_story_id
        self.story_selection_rationale = ""
        self.story_candidates: Tuple[Dict[str, Any], ...] = ()
        self._input_ids = _stage_input_ids(bundle)
        if resume_state is not None:
            self.receipts = list(resume_state["receipts"])
            self.payloads = resume_state["payloads"]
            self.last_checkpoint_id = resume_state["last_checkpoint_id"]
            self.last_sequence = resume_state["last_sequence"]
            self.warnings = list(resume_state.get("warnings") or ())
            self.selected_story_id = resume_state.get(
                "selected_story_id", request.selected_story_id
            )
            self.story_selection_rationale = str(
                resume_state.get("story_selection_rationale") or ""
            )
            self.story_candidates = tuple(resume_state.get("story_candidates") or ())

    def _events_path(self) -> Path:
        return self.work_dir / CONTINUATION_EVENTS_FILENAME

    def _attempts_path(self) -> Path:
        return self.work_dir / CONTINUATION_ATTEMPTS_FILENAME

    def _versions_path(self) -> Path:
        return self.work_dir / CONTINUATION_VERSIONS_FILENAME

    def _record_attempt(
        self,
        sequence: int,
        stage: str,
        status: Literal["unavailable", "failed"],
        *,
        input_ids: Sequence[str],
        warnings: Sequence[str],
        errors: Sequence[str],
        payload: Any,
        usage: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    ) -> None:
        payload_dump = _payload_dump(payload)
        payload_digest = _digest(payload_dump)
        existing = _read_jsonl(self._attempts_path(), "continuation attempts")
        prior = [
            row
            for row in existing
            if row.get("stage") == stage and row.get("sequence") == sequence
        ]
        attempt_id = f"attempt-{_short_digest(stage, sequence, status, payload_digest, len(prior) + 1)}"
        if isinstance(usage, Sequence) and not isinstance(usage, (str, bytes)):
            stored_usage: Any = tuple(
                dict(item) for item in usage if isinstance(item, Mapping) and item
            )
        else:
            stored_usage = dict(usage or {})
        record = ContinuationAttemptRecord(
            attempt_id=attempt_id,
            sequence=sequence,
            stage=stage,
            status=status,
            payload_digest=payload_digest,
            warnings=tuple(dict.fromkeys(warnings)),
            errors=tuple(dict.fromkeys(errors)),
            usage=stored_usage,
        )
        _append_jsonl(self._attempts_path(), record.model_dump(mode="json"))
        self.receipts.append(
            ContinuationStageReceipt(
                sequence=sequence,
                stage=stage,
                status=status,
                input_ids=tuple(input_ids),
                output_ids=(),
                warnings=tuple(dict.fromkeys(warnings)),
                errors=tuple(dict.fromkeys(errors)),
                payload_digest=payload_digest,
                hard_failure=status == "failed",
            )
        )

    def _commit_stage(
        self,
        sequence: int,
        stage: str,
        status: Literal["completed", "partial"],
        *,
        input_ids: Sequence[str],
        output_ids: Sequence[str],
        warnings: Sequence[str],
        errors: Sequence[str],
        payload: Any,
    ) -> None:
        payload_dump = _payload_dump(payload)
        payload_digest = _digest(payload_dump)
        receipt = ContinuationStageReceipt(
            sequence=sequence,
            stage=stage,
            status=status,
            input_ids=tuple(input_ids),
            output_ids=tuple(output_ids),
            warnings=tuple(dict.fromkeys(warnings)),
            errors=tuple(dict.fromkeys(errors)),
            payload_digest=payload_digest,
            hard_failure=False,
        )
        snapshot_name = f"{sequence:02d}-{stage}.json"
        snapshot_path = self.work_dir / snapshot_name
        snapshot_text = _canonical_json(payload_dump) + "\n"
        if snapshot_path.exists():
            existing = snapshot_path.read_text(encoding="utf-8")
            if existing != snapshot_text:
                raise ContinuationIntegrityError(
                    f"refusing to overwrite conflicting continuation "
                    f"snapshot {snapshot_name}"
                )
        else:
            atomic_write_text(snapshot_path, snapshot_text)
        if self.fault_hook is not None:
            self.fault_hook("snapshot")
        snapshot_sha256 = _sha256_bytes(snapshot_path.read_bytes())

        event = _event_from_receipt(receipt)
        events = _read_jsonl(self._events_path(), "continuation events")
        if event.event_id not in {str(item.get("event_id") or "") for item in events}:
            _append_jsonl(self._events_path(), event.model_dump(mode="json"))
        if self.fault_hook is not None:
            self.fault_hook("events")
        events = _read_jsonl(self._events_path(), "continuation events")
        event_prefix_digest = _digest(events)

        record = ContinuationCheckpointRecord(
            request_digest=continuation_request_digest(self.request),
            source_digest=self.bundle.source_digest,
            runtime_fingerprint=article_runtime_fingerprint(),
            stage_sequence=sequence,
            stage=stage,
            receipt=receipt,
            snapshot_filename=snapshot_name,
            snapshot_sha256=snapshot_sha256,
            payload_digest=payload_digest,
            event_prefix_digest=event_prefix_digest,
            previous_checkpoint_id=self.last_checkpoint_id,
            checkpoint_id="",
        )
        record = record.model_copy(
            update={
                "checkpoint_id": _digest(
                    record.model_dump(exclude={"checkpoint_id"}, mode="json")
                )
            }
        )
        checkpoint_name = f"{CHECKPOINT_PREFIX}{sequence:02d}-{stage}.json"
        checkpoint_path = self.work_dir / checkpoint_name
        expected_checkpoint_text = (
            _canonical_json(record.model_dump(mode="json")) + "\n"
        )
        if checkpoint_path.exists():
            existing = checkpoint_path.read_text(encoding="utf-8")
            if existing != expected_checkpoint_text:
                raise ContinuationIntegrityError(
                    f"committed continuation checkpoint conflict: " f"{checkpoint_name}"
                )
        else:
            atomic_write_text(checkpoint_path, expected_checkpoint_text)
        if self.fault_hook is not None:
            self.fault_hook("checkpoint")

        ledger_path = self.work_dir / CONTINUATION_LEDGER_FILENAME
        committed: List[str] = []
        if ledger_path.is_file():
            ledger = ContinuationLedger.model_validate(_read_json(ledger_path))
            committed = list(ledger.committed_checkpoints)
        if checkpoint_name not in committed:
            committed.append(checkpoint_name)
        ledger = ContinuationLedger(
            request_digest=record.request_digest,
            source_digest=record.source_digest,
            runtime_fingerprint=record.runtime_fingerprint,
            latest_checkpoint_id=record.checkpoint_id,
            latest_sequence=sequence,
            committed_checkpoints=tuple(committed),
        )
        atomic_write_json(ledger_path, ledger.model_dump(mode="json"))
        if self.fault_hook is not None:
            self.fault_hook("ledger")
        self.receipts.append(receipt)
        self.last_checkpoint_id = record.checkpoint_id
        self.last_sequence = sequence

    def _mark_skipped_from(self, sequence: int, cause: str) -> None:
        for later in CONTINUATION_STAGE_ORDER[sequence:]:
            receipt = ContinuationStageReceipt(
                sequence=sequence + 1,
                stage=later,
                status="skipped",
                input_ids=(),
                output_ids=(),
                warnings=(),
                errors=(cause,),
                payload_digest=_digest({"status": "skipped", "cause": cause}),
                hard_failure=False,
            )
            self.receipts.append(receipt)
            sequence += 1
        if cause not in self.warnings:
            self.warnings.append(cause)

    def _run_stage_result_synthesis(self) -> bool:
        if any(item.stage == "result_synthesis" for item in self.receipts):
            return True
        input_ids = self._input_ids
        try:
            synthesis = synthesize_article_results(
                self.bundle.plan,
                self.bundle.planning,
                self.bundle.assets,
                provider=self.continuation.result_synthesis_provider,
                run_id=self.request.run_id,
                literature_supplement=self.bundle.literature_supplement,
            )
        except Exception as exc:  # noqa: BLE001 - provider soft failure
            self._record_attempt(
                1,
                "result_synthesis",
                "unavailable",
                input_ids=input_ids,
                warnings=(),
                errors=(f"provider unavailable: {type(exc).__name__}",),
                payload={"status": "unavailable", "error": str(exc)[:400]},
                usage={},
            )
            self._mark_skipped_from(1, "result synthesis unavailable")
            return False
        if synthesis.status == "invalid":
            self._record_attempt(
                1,
                "result_synthesis",
                "failed",
                input_ids=input_ids,
                warnings=synthesis.warnings,
                errors=synthesis.validation_errors,
                payload=synthesis,
                usage=synthesis.provider_usage,
            )
            self._mark_skipped_from(1, "result synthesis failed closed")
            return False
        if synthesis.status == "unavailable":
            self._record_attempt(
                1,
                "result_synthesis",
                "unavailable",
                input_ids=input_ids,
                warnings=synthesis.warnings,
                errors=(),
                payload=synthesis,
                usage=synthesis.provider_usage,
            )
            self._mark_skipped_from(1, "result synthesis produced no usable output")
            return False
        status = "partial" if synthesis.status == "partial" else "completed"
        self.payloads = self.payloads.model_copy(update={"result_synthesis": synthesis})
        self._commit_stage(
            1,
            "result_synthesis",
            status,
            input_ids=input_ids,
            output_ids=_stage_output_id("result_synthesis", synthesis),
            warnings=synthesis.warnings,
            errors=synthesis.validation_errors,
            payload=synthesis,
        )
        return True

    def _run_stage_architecture(self) -> bool:
        if any(item.stage == "architecture" for item in self.receipts):
            return True
        synthesis = self.payloads.result_synthesis
        if (
            synthesis is None
            or synthesis.derived_plan is None
            or synthesis.ledger is None
        ):
            self._mark_skipped_from(2, "architecture requires synthesis output")
            return False
        try:
            descriptors, values = _contracted_inventory(synthesis, self.bundle)
        except ContinuationIntegrityError as exc:
            self._record_attempt(
                2,
                "architecture",
                "failed",
                input_ids=_stage_output_id("result_synthesis", synthesis),
                warnings=(),
                errors=(str(exc),),
                payload={"error": str(exc)},
                usage={},
            )
            self._mark_skipped_from(2, "contracted inventory failed closed")
            return False
        try:
            architecture = build_article_architecture(
                synthesis.derived_plan,
                synthesis.ledger,
                descriptors,
                architecture_provider=self.continuation.architecture_provider,
                value_shapes=value_field_shapes(values),
            )
        except Exception as exc:  # noqa: BLE001 - provider soft failure
            self._record_attempt(
                2,
                "architecture",
                "unavailable",
                input_ids=_stage_output_id("result_synthesis", synthesis),
                warnings=(),
                errors=(f"provider unavailable: {type(exc).__name__}",),
                payload={"status": "unavailable", "error": str(exc)[:400]},
                usage={},
            )
            self._mark_skipped_from(2, "architecture provider unavailable")
            return False
        if architecture.validation_errors:
            self._record_attempt(
                2,
                "architecture",
                "failed",
                input_ids=_stage_output_id("result_synthesis", synthesis),
                warnings=architecture.warnings,
                errors=architecture.validation_errors,
                payload=architecture,
                usage=architecture.usage,
            )
            self._mark_skipped_from(2, "architecture failed closed")
            return False
        if not architecture.stories:
            self._record_attempt(
                2,
                "architecture",
                "unavailable",
                input_ids=_stage_output_id("result_synthesis", synthesis),
                warnings=architecture.warnings,
                errors=(),
                payload=architecture,
                usage=architecture.usage,
            )
            self._mark_skipped_from(2, "architecture produced no stories")
            return False
        selection_errors: List[str] = []
        chosen, rationale, candidates = _select_story(
            architecture, self.selected_story_id, selection_errors
        )
        if not chosen:
            self._record_attempt(
                2,
                "architecture",
                "failed",
                input_ids=_stage_output_id("result_synthesis", synthesis),
                warnings=architecture.warnings,
                errors=selection_errors,
                payload=architecture,
                usage=architecture.usage,
            )
            self._mark_skipped_from(2, "no selectable architecture story")
            return False
        self.selected_story_id = chosen
        self.story_selection_rationale = rationale
        self.story_candidates = candidates
        self.payloads = self.payloads.model_copy(update={"architecture": architecture})
        status = (
            "partial"
            if architecture.model_status != "available" or architecture.warnings
            else "completed"
        )
        self._commit_stage(
            2,
            "architecture",
            status,
            input_ids=_stage_output_id("result_synthesis", synthesis),
            output_ids=_stage_output_id("architecture", architecture),
            warnings=architecture.warnings,
            errors=architecture.validation_errors,
            payload=architecture,
        )
        return True

    def _run_stage_writing(self) -> bool:
        if any(item.stage == "writing" for item in self.receipts):
            return True
        synthesis = self.payloads.result_synthesis
        architecture = self.payloads.architecture
        if (
            synthesis is None
            or synthesis.derived_plan is None
            or synthesis.ledger is None
            or architecture is None
        ):
            self._mark_skipped_from(3, "writing requires synthesis/architecture")
            return False
        try:
            _, values = _contracted_inventory(synthesis, self.bundle)
            values = _scoped_story_values(
                architecture, self.selected_story_id, values, synthesis.ledger
            )
        except ContinuationIntegrityError as exc:
            self._record_attempt(
                3,
                "writing",
                "failed",
                input_ids=_stage_output_id("architecture", architecture),
                warnings=(),
                errors=(str(exc),),
                payload={"error": str(exc)},
                usage={},
            )
            self._mark_skipped_from(3, "contracted inventory failed closed")
            return False
        try:
            bundle = build_article_draft_bundle(
                synthesis.derived_plan,
                synthesis.ledger,
                architecture,
                self.selected_story_id,
                values,
                section_writer=self.continuation.section_writer,
                format_repair=self.continuation.format_repair,
                literature_context=(
                    build_literature_provider_context(self.bundle.literature_supplement)
                    if self.bundle.literature_supplement is not None
                    else None
                ),
                literature_evidence_alias_map=(
                    dict(self.bundle.literature_supplement.evidence_aliases)
                    if self.bundle.literature_supplement is not None
                    else None
                ),
            )
        except Exception as exc:  # noqa: BLE001 - provider soft failure
            self._record_attempt(
                3,
                "writing",
                "unavailable",
                input_ids=_stage_output_id("architecture", architecture),
                warnings=(),
                errors=(f"provider unavailable: {type(exc).__name__}",),
                payload={"status": "unavailable", "error": str(exc)[:400]},
                usage={},
            )
            self._mark_skipped_from(3, "writing provider unavailable")
            return False
        if bundle.errors:
            self._record_attempt(
                3,
                "writing",
                "failed",
                input_ids=_stage_output_id("architecture", architecture),
                warnings=bundle.warnings,
                errors=bundle.errors,
                payload=bundle,
                usage=bundle.usage,
            )
            self._mark_skipped_from(3, "writing failed closed")
            return False
        if not bundle.sections:
            self._record_attempt(
                3,
                "writing",
                "unavailable",
                input_ids=_stage_output_id("architecture", architecture),
                warnings=bundle.warnings,
                errors=(),
                payload=bundle,
                usage=bundle.usage,
            )
            self._mark_skipped_from(3, "writing produced no sections")
            return False
        self.payloads = self.payloads.model_copy(update={"writing": bundle})
        status = (
            "partial"
            if not bundle.publishable
            or bundle.model_status != "available"
            or bundle.warnings
            else "completed"
        )
        self._commit_stage(
            3,
            "writing",
            status,
            input_ids=_stage_output_id("architecture", architecture),
            output_ids=_stage_output_id("writing", bundle),
            warnings=bundle.warnings,
            errors=bundle.errors,
            payload=bundle,
        )
        return True

    def _run_stage_review(self) -> bool:
        if any(item.stage == "review" for item in self.receipts):
            return True
        synthesis = self.payloads.result_synthesis
        architecture = self.payloads.architecture
        bundle = self.payloads.writing
        if (
            synthesis is None
            or synthesis.derived_plan is None
            or synthesis.ledger is None
            or architecture is None
            or bundle is None
        ):
            self._mark_skipped_from(4, "review requires upstream stages")
            return False
        try:
            _, values = _contracted_inventory(synthesis, self.bundle)
            values = _scoped_story_values(
                architecture, self.selected_story_id, values, synthesis.ledger
            )
        except ContinuationIntegrityError as exc:
            self._record_attempt(
                4,
                "review",
                "failed",
                input_ids=_stage_output_id("writing", bundle),
                warnings=(),
                errors=(str(exc),),
                payload={"error": str(exc)},
                usage={},
            )
            self._mark_skipped_from(4, "contracted inventory failed closed")
            return False
        try:
            review = build_article_review(
                synthesis.derived_plan,
                synthesis.ledger,
                architecture,
                bundle,
                self.selected_story_id,
                values,
                scientific_reviewer=self.continuation.scientific_reviewer,
                expression_reviewer=self.continuation.expression_reviewer,
                global_consistency_reviewer=(
                    self.continuation.global_consistency_reviewer
                ),
                global_advice_router=self.continuation.global_advice_router,
                author_reviser=self.continuation.author_reviser,
            )
        except Exception as exc:  # noqa: BLE001 - provider soft failure
            self._record_attempt(
                4,
                "review",
                "unavailable",
                input_ids=_stage_output_id("writing", bundle),
                warnings=(),
                errors=(f"provider unavailable: {type(exc).__name__}",),
                payload={"status": "unavailable", "error": str(exc)[:400]},
                usage={},
            )
            self._mark_skipped_from(4, "review provider unavailable")
            return False
        if not review.sections:
            self._record_attempt(
                4,
                "review",
                "unavailable",
                input_ids=_stage_output_id("writing", bundle),
                warnings=review.warnings,
                errors=(),
                payload=review,
                usage=review.usage,
            )
            self._mark_skipped_from(4, "review produced no reviewed sections")
            return False
        self.payloads = self.payloads.model_copy(update={"review": review})
        status = (
            "partial"
            if review.model_status != "available"
            or review.status in {"ready_with_findings", "partial"}
            or review.warnings
            else "completed"
        )
        self._commit_stage(
            4,
            "review",
            status,
            input_ids=_stage_output_id("writing", bundle),
            output_ids=_stage_output_id("review", review),
            warnings=review.warnings,
            errors=(),
            payload=review,
        )
        return True

    def _run_stage_manuscript(self) -> bool:
        if any(item.stage == "manuscript" for item in self.receipts):
            return True
        synthesis = self.payloads.result_synthesis
        architecture = self.payloads.architecture
        review = self.payloads.review
        if (
            synthesis is None
            or synthesis.derived_plan is None
            or synthesis.ledger is None
            or architecture is None
            or review is None
        ):
            self._mark_skipped_from(5, "manuscript requires upstream stages")
            return False
        try:
            _, values = _contracted_inventory(synthesis, self.bundle)
            values = _scoped_story_values(
                architecture, self.selected_story_id, values, synthesis.ledger
            )
        except ContinuationIntegrityError as exc:
            self._record_attempt(
                5,
                "manuscript",
                "failed",
                input_ids=_stage_output_id("review", review),
                warnings=(),
                errors=(str(exc),),
                payload={"error": str(exc)},
                usage={},
            )
            return False
        manuscript_dir = self.work_dir / MANUSCRIPT_DIRECTORY
        package = build_article_manuscript(
            synthesis.derived_plan,
            synthesis.ledger,
            architecture,
            review,
            self.selected_story_id,
            values,
            output_dir=manuscript_dir,
        )
        if package.errors:
            self._record_attempt(
                5,
                "manuscript",
                "failed",
                input_ids=_stage_output_id("review", review),
                warnings=package.warnings,
                errors=package.errors,
                payload=package,
                usage={},
            )
            return False
        self.payloads = self.payloads.model_copy(update={"manuscript": package})
        status = (
            "partial" if package.blocked_handoff or package.warnings else "completed"
        )
        self._commit_stage(
            5,
            "manuscript",
            status,
            input_ids=_stage_output_id("review", review),
            output_ids=_stage_output_id("manuscript", package),
            warnings=package.warnings,
            errors=package.errors,
            payload=package,
        )
        return True

    def run(self) -> "ContinuationResult":
        if self.payloads.result_synthesis is None:
            if not self._run_stage_result_synthesis():
                return self.assemble_terminal_result()
        if self.payloads.architecture is None:
            if not self._run_stage_architecture():
                return self.assemble_terminal_result()
        if self.payloads.writing is None:
            if not self._run_stage_writing():
                return self.assemble_terminal_result()
        if self.payloads.review is None:
            if not self._run_stage_review():
                return self.assemble_terminal_result()
        if self.payloads.manuscript is None:
            if not self._run_stage_manuscript():
                return self.assemble_terminal_result()
        return self.assemble_terminal_result()

    def assemble_terminal_result(self) -> "ContinuationResult":
        manuscript = self.payloads.manuscript
        review = self.payloads.review
        ledger = (
            self.payloads.result_synthesis.ledger
            if self.payloads.result_synthesis is not None
            else None
        )
        counts: Dict[str, Any] = {
            "sections": 0,
            "paragraphs": 0,
            "words": 0,
            "facts": len(ledger.facts) if ledger is not None else 0,
            "claims": len(ledger.claims) if ledger is not None else 0,
            "reviewer_findings": 0,
            "blockers": 0,
        }
        if manuscript is not None:
            counts["sections"] = len(manuscript.body.sections)
            counts["paragraphs"] = sum(
                len(section.paragraphs) for section in manuscript.body.sections
            )
            counts["words"] = len(
                re.findall(r"\b[A-Za-z][A-Za-z'-]*\b", manuscript.body_markdown)
            )
            counts["blockers"] = len(manuscript.blocked_handoff)
        if review is not None:
            counts["reviewer_findings"] = len(review.scientific_findings) + len(
                review.expression_findings
            )
            counts["blockers"] += len(review.hard_blockers)
        try:
            attempt_records = _read_jsonl(
                self._attempts_path(), "continuation attempts"
            )
        except ContinuationIntegrityError:
            attempt_records = []
        usage = _aggregate_usage(
            [
                ("result_synthesis", self.payloads.result_synthesis),
                ("architecture", self.payloads.architecture),
                ("writing", self.payloads.writing),
                ("review", self.payloads.review),
            ],
            attempts=attempt_records,
        )
        statuses = {item.status for item in self.receipts}
        if "failed" in statuses:
            status: Literal["completed", "partial", "unavailable", "failed"] = "failed"
        elif "unavailable" in statuses:
            status = "unavailable"
        elif "partial" in statuses or self.warnings:
            status = "partial"
        else:
            status = "completed"
        if len(self.receipts) != len(CONTINUATION_STAGE_ORDER):
            status = "unavailable"
        result_warnings = list(dict.fromkeys(self.warnings))
        result_errors = list(dict.fromkeys(self.errors))
        for receipt in self.receipts:
            if receipt.status == "failed":
                result_errors.extend(receipt.errors)
            elif receipt.status in {"partial", "unavailable"}:
                result_warnings.extend(receipt.warnings)
        model = ContinuationResult(
            status=status,
            result_id="",
            run_id=self.request.run_id,
            branch_id=self.request.branch_id,
            source_pipeline_dir=str(Path(self.request.source_pipeline_dir).resolve()),
            source_result_id=self.bundle.result.result_id,
            receipts=tuple(self.receipts),
            selected_story_id=self.selected_story_id,
            story_selection_rationale=self.story_selection_rationale,
            story_candidates=self.story_candidates,
            stage_payloads=self.payloads,
            counts=counts,
            usage=usage,
            warnings=list(dict.fromkeys(result_warnings)),
            errors=list(dict.fromkeys(result_errors)),
        )
        return model.model_copy(
            update={"result_id": compute_continuation_result_id(model)}
        )


class ArticleContinuation:
    """Additive continuation shell with injectable stage providers."""

    def __init__(
        self,
        *,
        result_synthesis_provider: Optional[ResultSynthesisProvider] = None,
        architecture_provider: Optional[Callable[[Any], Any]] = None,
        section_writer: Optional[Callable[[Any], Any]] = None,
        format_repair: Optional[Callable[[Any], Any]] = None,
        scientific_reviewer: Optional[Callable[[Any], Any]] = None,
        expression_reviewer: Optional[Callable[[Any], Any]] = None,
        global_consistency_reviewer: Optional[Callable[[Any], Any]] = None,
        global_advice_router: Optional[Callable[[Any], Any]] = None,
        author_reviser: Optional[Callable[[Any], Any]] = None,
        fault_hook: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.result_synthesis_provider = result_synthesis_provider
        self.architecture_provider = architecture_provider
        self.section_writer = section_writer
        self.format_repair = format_repair
        self.scientific_reviewer = scientific_reviewer
        self.expression_reviewer = expression_reviewer
        self.global_consistency_reviewer = global_consistency_reviewer
        self.global_advice_router = global_advice_router
        self.author_reviser = author_reviser
        self.fault_hook = fault_hook

    def run(
        self, request: ContinuationRequest | Mapping[str, Any]
    ) -> "ContinuationResult":
        request_model = (
            request
            if isinstance(request, ContinuationRequest)
            else ContinuationRequest.model_validate(request)
        )
        source_dir = Path(request_model.source_pipeline_dir).resolve()
        work_dir = Path(request_model.work_dir).resolve()
        try:
            _reject_overlap(source_dir, work_dir)
        except ContinuationIntegrityError as exc:
            return self._failed_result(request_model, [str(exc)])
        if work_dir.exists() and any(work_dir.iterdir()):
            return self._failed_result(
                request_model,
                [
                    "continuation work directory is not empty; use resume "
                    "with the same immutable request"
                ],
            )
        work_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            work_dir / CONTINUATION_REQUEST_FILENAME,
            request_model.model_dump(mode="json"),
        )
        return self._execute(request_model, source_dir, work_dir, resume=False)

    def resume(
        self, request: ContinuationRequest | Mapping[str, Any]
    ) -> "ContinuationResult":
        request_model = (
            request
            if isinstance(request, ContinuationRequest)
            else ContinuationRequest.model_validate(request)
        )
        source_dir = Path(request_model.source_pipeline_dir).resolve()
        work_dir = Path(request_model.work_dir).resolve()
        try:
            _reject_overlap(source_dir, work_dir)
        except ContinuationIntegrityError as exc:
            return self._failed_result(request_model, [str(exc)])
        if not work_dir.is_dir():
            return self._failed_result(
                request_model,
                ["continuation work directory does not exist; nothing to resume"],
            )
        return self._execute(request_model, source_dir, work_dir, resume=True)

    def _execute(
        self,
        request_model: ContinuationRequest,
        source_dir: Path,
        work_dir: Path,
        *,
        resume: bool,
    ) -> "ContinuationResult":
        lock = RuntimeLock(work_dir / CONTINUATION_LOCK_FILENAME)
        try:
            token = lock.acquire(request_model.run_id, request_model.branch_id)
        except RuntimeLockError as exc:
            return self._failed_result(
                request_model, [f"cannot acquire continuation lock: {exc}"]
            )
        try:
            try:
                bundle = load_source_pipeline(source_dir)
            except ContinuationIntegrityError as exc:
                return self._failed_result(
                    request_model, [f"source pipeline validation failed: {exc}"]
                )
            if request_model.literature_supplement_path:
                supplement_dir = Path(
                    request_model.literature_supplement_path
                ).resolve()
                try:
                    supplement = load_literature_supplement(
                        supplement_dir / "METHOD_RESEARCH_REPORT.json",
                        supplement_dir / "ARTICLE_DIRECTOR_SUPPLEMENT_ALIAS_FINAL.json",
                        expected_source_pipeline_result_id=(bundle.result.result_id),
                        expected_old_director_plan_id=bundle.plan.plan_id,
                    )
                    source_problem_id = str(
                        getattr(
                            bundle.result.problem_analysis,
                            "problem_id",
                            "",
                        )
                        or ""
                    )
                    if (
                        source_problem_id
                        and supplement.report_identity != source_problem_id
                    ):
                        raise ContinuationIntegrityError(
                            "literature supplement report_identity does not "
                            "match the source pipeline problem_id"
                        )
                except (
                    ContinuationIntegrityError,
                    ValueError,
                ) as exc:
                    return self._failed_result(
                        request_model,
                        ["literature supplement validation failed: " f"{exc}"],
                    )
                supplement_digest = _digest(
                    supplement.report_sha256,
                    supplement.supplement_sha256,
                    supplement.metadata_sha256,
                )
                bundle = replace(
                    bundle,
                    literature_supplement=supplement,
                    literature_supplement_digest=supplement_digest,
                    source_digest=_digest(
                        bundle.source_digest,
                        supplement_digest,
                    ),
                )
            errors: List[str] = []
            warnings: List[str] = []
            state = None
            if resume:
                state = validate_continuation_state(
                    work_dir, request_model, bundle, errors, warnings
                )
                if state is None:
                    return self._failed_result(
                        request_model,
                        errors or ["continuation state validation failed"],
                        warnings=warnings,
                    )
                if state.get("pending") is not None:
                    try:
                        _promote_pending(work_dir, state, errors)
                    except ContinuationIntegrityError as exc:
                        return self._failed_result(
                            request_model,
                            errors or [f"cannot promote pending checkpoint: {exc}"],
                            warnings=warnings,
                        )
                    if errors:
                        return self._failed_result(
                            request_model, errors, warnings=warnings
                        )
                committed_count = len(state["records"]) + (
                    1 if state.get("pending") is not None else 0
                )
                if committed_count == len(CONTINUATION_STAGE_ORDER):
                    final_path = work_dir / FINAL_CONTINUATION_RESULT_FILENAME
                    if final_path.is_file():
                        final = self._validate_final_result(
                            final_path, request_model, bundle, state
                        )
                        if final is not None:
                            return final
                        return self._failed_result(
                            request_model,
                            ["final continuation result is invalid"],
                            warnings=warnings,
                        )
            runner = _ContinuationRunner(
                self,
                request_model,
                work_dir,
                bundle,
                resume_state=state,
                fault_hook=self.fault_hook,
            )
            result = runner.run()
            final_path = work_dir / FINAL_CONTINUATION_RESULT_FILENAME
            atomic_write_json(final_path, result.model_dump(mode="json"))
            _append_version(work_dir, result)
            return result
        finally:
            try:
                lock.release(token)
            except RuntimeLockError:
                pass

    def _validate_final_result(
        self,
        path: Path,
        request_model: ContinuationRequest,
        bundle: SourcePipelineBundle,
        state: Mapping[str, Any],
    ) -> Optional["ContinuationResult"]:
        try:
            final = ContinuationResult.model_validate(_read_json(path))
        except (OSError, ValueError, ValidationError):
            return None
        if final.result_id != compute_continuation_result_id(final):
            return None
        if final.run_id != request_model.run_id:
            return None
        if final.source_result_id != bundle.result.result_id:
            return None
        try:
            reconstructed = _reconstruct_final(request_model, bundle, state)
        except ContinuationIntegrityError:
            return None
        if final.model_dump(mode="json") != reconstructed.model_dump(mode="json"):
            return None
        return final

    def _failed_result(
        self,
        request_model: ContinuationRequest,
        errors: Sequence[str],
        *,
        warnings: Sequence[str] = (),
    ) -> "ContinuationResult":
        model = ContinuationResult(
            status="failed",
            result_id="",
            run_id=request_model.run_id,
            branch_id=request_model.branch_id,
            source_pipeline_dir=str(Path(request_model.source_pipeline_dir).resolve()),
            source_result_id="",
            warnings=list(warnings),
            errors=list(errors),
        )
        return model.model_copy(
            update={"result_id": compute_continuation_result_id(model)}
        )


def _reject_overlap(source_dir: Path, work_dir: Path) -> None:
    source_text = os.path.normcase(str(source_dir))
    work_text = os.path.normcase(str(work_dir))
    if work_text == source_text or work_text.startswith(source_text + os.sep):
        raise ContinuationIntegrityError(
            "continuation work_dir must not be equal to or nested under "
            "source_pipeline_dir"
        )


def _checkpoint_record_valid(
    record: ContinuationCheckpointRecord,
    *,
    request_digest: str,
    source_digest: str,
    fingerprint: str,
    previous_checkpoint_id: str,
    events: Sequence[Mapping[str, Any]],
    expected_event_index: int,
    errors: List[str],
) -> bool:
    if record.checkpoint_id != _digest(
        record.model_dump(exclude={"checkpoint_id"}, mode="json")
    ):
        errors.append(
            f"continuation checkpoint {record.stage} checkpoint_id does not "
            "match recomputed content"
        )
        return False
    if record.previous_checkpoint_id != previous_checkpoint_id:
        errors.append(
            f"continuation checkpoint {record.stage} breaks the previous-ID chain"
        )
        return False
    if record.request_digest != request_digest:
        errors.append(f"continuation checkpoint {record.stage} request digest mismatch")
        return False
    if record.source_digest != source_digest:
        errors.append(f"continuation checkpoint {record.stage} source digest mismatch")
        return False
    if record.runtime_fingerprint != fingerprint:
        errors.append(
            f"continuation checkpoint {record.stage} runtime fingerprint mismatch"
        )
        return False
    if expected_event_index >= len(events):
        errors.append(f"continuation checkpoint {record.stage} has no matching event")
        return False
    expected_event = _event_from_receipt(record.receipt)
    actual_event = events[expected_event_index]
    if actual_event != expected_event.model_dump(mode="json"):
        errors.append(
            f"continuation checkpoint {record.stage} event does not match "
            "its receipt"
        )
        return False
    prefix = events[: expected_event_index + 1]
    if record.event_prefix_digest != _digest(prefix):
        errors.append(
            f"continuation checkpoint {record.stage} event prefix digest "
            "does not match the event log"
        )
        return False
    return True


def _load_payload_for_stage(
    work_dir: Path,
    stage: str,
    snapshot_filename: str,
    payload_digest: str,
    expected_snapshot_sha256: str = "",
) -> Any:
    path = work_dir / snapshot_filename
    if not path.is_file():
        raise ContinuationIntegrityError(
            f"continuation checkpoint {stage} snapshot is missing"
        )
    if expected_snapshot_sha256 and (
        _sha256_bytes(path.read_bytes()) != expected_snapshot_sha256
    ):
        raise ContinuationIntegrityError(
            f"continuation checkpoint {stage} snapshot SHA256 mismatch"
        )
    try:
        payload = _read_json(path)
    except (OSError, ValueError) as exc:
        raise ContinuationIntegrityError(
            f"continuation checkpoint {stage} snapshot is invalid: {exc}"
        ) from exc
    if _digest(payload) != payload_digest:
        raise ContinuationIntegrityError(
            f"continuation checkpoint {stage} payload digest mismatch"
        )
    model_type = _STAGE_PAYLOAD_MODELS.get(stage)
    if model_type is None:
        raise ContinuationIntegrityError(
            f"continuation checkpoint has unknown stage {stage!r}"
        )
    return model_type.model_validate(payload)


def validate_continuation_state(
    work_dir: str | Path,
    request: ContinuationRequest,
    bundle: SourcePipelineBundle,
    errors: List[str],
    warnings: List[str],
) -> Optional[Dict[str, Any]]:
    """Verify committed chain, event log, attempts, and typed payloads."""

    root = Path(work_dir)
    ledger_path = root / CONTINUATION_LEDGER_FILENAME
    fingerprint = article_runtime_fingerprint()
    ledger: Optional[ContinuationLedger] = None
    if not ledger_path.is_file():
        request_path = root / CONTINUATION_REQUEST_FILENAME
        if not request_path.is_file():
            errors.append("continuation request is missing")
            return None
        try:
            stored_request = ContinuationRequest.model_validate(
                _read_json(request_path)
            )
        except (OSError, ValueError, ValidationError) as exc:
            errors.append(f"continuation request is invalid: {exc}")
            return None
        if continuation_request_digest(stored_request) != (
            continuation_request_digest(request)
        ):
            errors.append("continuation request does not match the immutable request")
            return None
    else:
        try:
            ledger = ContinuationLedger.model_validate(_read_json(ledger_path))
        except (OSError, ValueError, ValidationError) as exc:
            errors.append(f"continuation ledger is invalid: {exc}")
            return None
        if ledger.request_digest != continuation_request_digest(request):
            errors.append("continuation ledger request digest does not match")
            return None
        if ledger.source_digest != bundle.source_digest:
            errors.append("continuation ledger source digest does not match")
            return None
        if ledger.runtime_fingerprint != fingerprint:
            errors.append("continuation ledger runtime fingerprint does not match")
            return None

    try:
        events = _read_jsonl(root / CONTINUATION_EVENTS_FILENAME, "continuation events")
    except ContinuationIntegrityError as exc:
        errors.append(str(exc))
        return None
    parsed_events: List[ContinuationEvent] = []
    seen_event_ids: set[str] = set()
    for index, raw in enumerate(events):
        try:
            event = ContinuationEvent.model_validate(raw)
        except ValidationError as exc:
            errors.append(f"continuation event {index + 1} is malformed: {exc}")
            return None
        if event.event_id != _event_id(event):
            errors.append(f"continuation event {index + 1} event_id mismatch")
            return None
        if event.event_id in seen_event_ids:
            errors.append(f"continuation event {index + 1} duplicates an earlier event")
            return None
        seen_event_ids.add(event.event_id)
        parsed_events.append(event)

    try:
        attempts = _read_jsonl(
            root / CONTINUATION_ATTEMPTS_FILENAME, "continuation attempts"
        )
    except ContinuationIntegrityError as exc:
        errors.append(str(exc))
        return None
    seen_attempt_ids: set[str] = set()
    for index, raw in enumerate(attempts):
        try:
            attempt = ContinuationAttemptRecord.model_validate(raw)
        except ValidationError as exc:
            errors.append(f"continuation attempt {index + 1} is malformed: {exc}")
            return None
        if attempt.attempt_id in seen_attempt_ids:
            errors.append(
                f"continuation attempt {index + 1} duplicates an earlier attempt"
            )
            return None
        seen_attempt_ids.add(attempt.attempt_id)

    records: List[ContinuationCheckpointRecord] = []
    previous_id = ""
    committed_checkpoints: Tuple[str, ...] = (
        ledger.committed_checkpoints if ledger is not None else ()
    )
    for index, filename in enumerate(committed_checkpoints):
        path = root / filename
        if not path.is_file():
            errors.append(f"committed continuation checkpoint is missing: {filename}")
            return None
        try:
            record = ContinuationCheckpointRecord.model_validate(_read_json(path))
        except (OSError, ValueError, ValidationError) as exc:
            errors.append(f"continuation checkpoint {filename} is invalid: {exc}")
            return None
        if record.stage not in _STAGE_PAYLOAD_MODELS:
            errors.append(
                f"continuation checkpoint {filename} has unknown stage "
                f"{record.stage!r}"
            )
            return None
        if record.receipt.status not in {"completed", "partial"}:
            errors.append(
                f"continuation checkpoint {filename} has non-accepted status "
                f"{record.receipt.status!r}"
            )
            return None
        if index >= len(parsed_events):
            errors.append(f"continuation checkpoint {filename} has no matching event")
            return None
        if not _checkpoint_record_valid(
            record,
            request_digest=(
                ledger.request_digest
                if ledger is not None
                else (continuation_request_digest(request))
            ),
            source_digest=(
                ledger.source_digest if ledger is not None else bundle.source_digest
            ),
            fingerprint=fingerprint,
            previous_checkpoint_id=previous_id,
            events=[item.model_dump(mode="json") for item in parsed_events],
            expected_event_index=index,
            errors=errors,
        ):
            return None
        try:
            _load_payload_for_stage(
                root,
                record.stage,
                record.snapshot_filename,
                record.payload_digest,
                record.snapshot_sha256,
            )
        except ContinuationIntegrityError as exc:
            errors.append(str(exc))
            return None
        records.append(record)
        previous_id = record.checkpoint_id
    if records:
        if ledger is not None and (
            ledger.latest_checkpoint_id != records[-1].checkpoint_id
        ):
            errors.append("continuation ledger latest checkpoint id mismatch")
            return None
        if ledger is not None and (
            ledger.latest_sequence != records[-1].stage_sequence
        ):
            errors.append("continuation ledger latest sequence mismatch")
            return None

    pending: Optional[ContinuationCheckpointRecord] = None
    remaining_events = parsed_events[len(records) :]
    if len(remaining_events) > 1:
        errors.append("continuation event log contains multiple uncommitted events")
        return None
    next_sequence = records[-1].stage_sequence + 1 if records else 1
    if remaining_events:
        pending_event = remaining_events[0]
        if pending_event.sequence != next_sequence:
            errors.append("uncommitted continuation event sequence mismatch")
            return None
        if pending_event.stage != CONTINUATION_STAGE_ORDER[len(records)]:
            errors.append("uncommitted continuation event stage mismatch")
            return None
        pending_filename = (
            f"{CHECKPOINT_PREFIX}{pending_event.sequence:02d}-"
            f"{pending_event.stage}.json"
        )
        pending_path = root / pending_filename
        if pending_path.is_file():
            try:
                pending = ContinuationCheckpointRecord.model_validate(
                    _read_json(pending_path)
                )
            except (OSError, ValueError, ValidationError) as exc:
                errors.append(f"pending continuation checkpoint is invalid: {exc}")
                return None
            if not _checkpoint_record_valid(
                pending,
                request_digest=(
                    ledger.request_digest
                    if ledger is not None
                    else continuation_request_digest(request)
                ),
                source_digest=(
                    ledger.source_digest if ledger is not None else bundle.source_digest
                ),
                fingerprint=fingerprint,
                previous_checkpoint_id=previous_id,
                events=[item.model_dump(mode="json") for item in parsed_events],
                expected_event_index=len(records),
                errors=errors,
            ):
                return None
        else:
            receipt = ContinuationStageReceipt(
                sequence=pending_event.sequence,
                stage=pending_event.stage,
                status=pending_event.status,
                input_ids=pending_event.input_ids,
                output_ids=pending_event.output_ids,
                warnings=pending_event.warnings,
                errors=pending_event.errors,
                payload_digest=pending_event.payload_digest,
                hard_failure=pending_event.hard_failure,
            )
            if receipt.status not in {"completed", "partial"}:
                errors.append("pending continuation event is not accepted")
                return None
            snapshot_filename = (
                f"{pending_event.sequence:02d}-{pending_event.stage}.json"
            )
            snapshot_path = root / snapshot_filename
            if not snapshot_path.is_file():
                errors.append("pending continuation event snapshot is missing")
                return None
            snapshot_sha = _sha256_bytes(snapshot_path.read_bytes())
            try:
                _load_payload_for_stage(
                    root,
                    pending_event.stage,
                    snapshot_filename,
                    pending_event.payload_digest,
                    snapshot_sha,
                )
            except ContinuationIntegrityError as exc:
                errors.append(str(exc))
                return None
            pending = ContinuationCheckpointRecord(
                request_digest=(
                    ledger.request_digest
                    if ledger is not None
                    else continuation_request_digest(request)
                ),
                source_digest=(
                    ledger.source_digest if ledger is not None else bundle.source_digest
                ),
                runtime_fingerprint=fingerprint,
                stage_sequence=pending_event.sequence,
                stage=pending_event.stage,
                receipt=receipt,
                snapshot_filename=snapshot_filename,
                snapshot_sha256=snapshot_sha,
                payload_digest=pending_event.payload_digest,
                event_prefix_digest=_digest(
                    [item.model_dump(mode="json") for item in parsed_events]
                ),
                previous_checkpoint_id=previous_id,
                checkpoint_id="",
            )
            pending = pending.model_copy(
                update={
                    "checkpoint_id": _digest(
                        pending.model_dump(exclude={"checkpoint_id"}, mode="json")
                    )
                }
            )
    else:
        for path in sorted(root.iterdir()):
            if not path.name.startswith(CHECKPOINT_PREFIX):
                continue
            if path.name in ledger.committed_checkpoints:
                continue
            errors.append(f"extra unreferenced continuation checkpoint: {path.name}")
            return None
    if errors:
        return None

    payloads = ContinuationStagePayloads()
    receipts: List[ContinuationStageReceipt] = []
    all_records = list(records)
    if pending is not None:
        all_records.append(pending)
    for record in all_records:
        payload = _load_payload_for_stage(
            root,
            record.stage,
            record.snapshot_filename,
            record.payload_digest,
            record.snapshot_sha256,
        )
        if record.stage == "result_synthesis":
            payloads = payloads.model_copy(update={"result_synthesis": payload})
        elif record.stage == "architecture":
            payloads = payloads.model_copy(update={"architecture": payload})
        elif record.stage == "writing":
            payloads = payloads.model_copy(update={"writing": payload})
        elif record.stage == "review":
            payloads = payloads.model_copy(update={"review": payload})
        elif record.stage == "manuscript":
            payloads = payloads.model_copy(update={"manuscript": payload})
        receipts.append(record.receipt)

    selected_story_id = request.selected_story_id
    story_selection_rationale = ""
    story_candidates: Tuple[Dict[str, Any], ...] = ()
    if payloads.architecture is not None:
        selection_errors: List[str] = []
        chosen, rationale, candidates = _select_story(
            payloads.architecture,
            request.selected_story_id,
            selection_errors,
        )
        if not chosen:
            errors.extend(selection_errors)
            return None
        selected_story_id = chosen
        story_selection_rationale = rationale
        story_candidates = candidates
    return {
        "records": records,
        "pending": pending,
        "payloads": payloads,
        "receipts": receipts,
        "last_checkpoint_id": records[-1].checkpoint_id if records else "",
        "last_sequence": records[-1].stage_sequence if records else 0,
        "selected_story_id": selected_story_id,
        "story_selection_rationale": story_selection_rationale,
        "story_candidates": story_candidates,
        "warnings": tuple(warnings),
    }


def _promote_pending(
    work_dir: str | Path,
    state: Mapping[str, Any],
    errors: List[str],
) -> None:
    pending = state.get("pending")
    if pending is None:
        return
    root = Path(work_dir)
    ledger_path = root / CONTINUATION_LEDGER_FILENAME
    ledger: Optional[ContinuationLedger] = None
    if ledger_path.is_file():
        ledger = ContinuationLedger.model_validate(_read_json(ledger_path))
        if ledger.request_digest != pending.request_digest:
            raise ContinuationIntegrityError(
                "pending checkpoint request digest mismatch"
            )
        if ledger.source_digest != pending.source_digest:
            raise ContinuationIntegrityError(
                "pending checkpoint source digest mismatch"
            )
    filename = f"{CHECKPOINT_PREFIX}{pending.stage_sequence:02d}-{pending.stage}.json"
    committed = list(ledger.committed_checkpoints) if ledger is not None else []
    if filename in committed:
        return
    committed.append(filename)
    atomic_write_json(
        ledger_path,
        ContinuationLedger(
            request_digest=pending.request_digest,
            source_digest=pending.source_digest,
            runtime_fingerprint=pending.runtime_fingerprint,
            latest_checkpoint_id=pending.checkpoint_id,
            latest_sequence=pending.stage_sequence,
            committed_checkpoints=tuple(committed),
        ).model_dump(mode="json"),
    )


def _reconstruct_final(
    request: ContinuationRequest,
    bundle: SourcePipelineBundle,
    state: Mapping[str, Any],
) -> ContinuationResult:
    runner = _ContinuationRunner(
        ArticleContinuation(),
        request,
        Path(request.work_dir).resolve(),
        bundle,
        resume_state=state,
    )
    return runner.assemble_terminal_result()


def _append_version(work_dir: Path, result: ContinuationResult) -> None:
    path = work_dir / CONTINUATION_VERSIONS_FILENAME
    existing = _read_jsonl(path, "continuation versions")
    _append_jsonl(
        path,
        {
            "schema_version": "continuation-version.v1",
            "version": len(existing) + 1,
            "result_id": result.result_id,
            "status": result.status,
            "committed_prefix_digest": _digest(
                [item.model_dump(mode="json") for item in result.receipts]
            ),
        },
    )


__all__ = [
    "ArticleContinuation",
    "ContinuationAttemptRecord",
    "ContinuationCheckpointRecord",
    "ContinuationEvent",
    "ContinuationIntegrityError",
    "ContinuationLedger",
    "ContinuationRequest",
    "ContinuationResult",
    "ContinuationStagePayloads",
    "ContinuationStageReceipt",
    "SourcePipelineBundle",
    "compute_continuation_result_id",
    "continuation_request_digest",
    "load_source_pipeline",
    "validate_continuation_state",
]
