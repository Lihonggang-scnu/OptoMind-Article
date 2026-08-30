"""Trusted TMM-to-Article asset compiler (verified artifact bridge).

Stage 6.5 asset compiler: converts an already-completed
``ArticleExecutionResult`` plus its matching ``CompiledExperimentRequest``
and an immutable TMM run directory into verified ``ArtifactDescriptor``
records, ``TrustedValueRecord`` records, and one enriched
``ObservationCard`` for the existing Claim Ledger, Figure-first, writing,
replay, and presentation layers.

Hard rules:
- The compiler never calls a model, never creates derivative scientific data
  files, and never mutates the TMM run.
- Provenance, permission, physics, task/request identity and hashes fail
  closed: a missing/tampered manifest, path escape, duplicate identity, hash
  mismatch, wrong task/run/request, malformed certificate, or cross-wired
  candidate never yields trusted assets.
- ``artifact_id`` from ``ARTIFACT_MANIFEST.json`` is the stable logical
  identity and ``relative_path`` is the physical location.
  ``ARTIFACT_PATH_INDEX.json`` may be used for long-path layouts; the code
  never assumes ``experiments/<id>`` is the only layout.
- Candidate selection starts from ``selected_roles`` plus
  ``pareto_candidate_ids``, deduplicated; a baseline is a valid candidate.
- ``TrustedValueRecord`` is emitted only from finite scalar values actually
  present in verified artifacts; arrays, spectra, hashes, statuses, booleans
  and opaque identifiers stay descriptor fields and are never prose-safe.
- Optional ROBUSTNESS absence is an explicit warning/partial condition and
  never silently drops a candidate.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Tuple

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from optomind_optics.harness.article_architecture import ArtifactDescriptor
from optomind_optics.harness.article_contracts import (
    ObservationCard,
)
from optomind_optics.harness.article_execution import (
    ArticleExecutionResult,
    normalize_observation_status,
    required_action_for_task,
)
from optomind_optics.harness.article_proposals import (
    ArticleCompilationAuthority,
    CompiledExperimentRequest,
    compute_optical_design_task_digest,
    compute_request_id,
    compute_task_hash,
)
from optomind_optics.harness.article_writing import TrustedValueRecord
from optomind_optics.harness.contracts import ExperimentStatus
from optomind_optics.harness.design_task import OpticalDesignTask
from optomind_optics.harness.provenance import ArtifactLineageStore


ASSET_COMPILATION_SCHEMA_VERSION = "article-asset-compilation-result.v1"
ASSET_COMPILER_VERSION = "article-asset-compiler.v1"
CANDIDATE_RECORD_SCHEMA_VERSION = "verified-candidate-record.v1"

_SUCCESS_STATUSES = frozenset(
    {
        ExperimentStatus.physically_valid,
        ExperimentStatus.physically_valid_with_limits,
    }
)
# ``needs_higher_fidelity`` is a completed-but-limited result: when the
# FINAL_RESULT itself reports that status and the run still contains verified
# usable assets, the compilation is an explicit partial (never caller-chosen).
_LIMITED_STATUSES = frozenset({ExperimentStatus.needs_higher_fidelity})
_FAILED_STATUSES = frozenset(
    {
        ExperimentStatus.failed,
        ExperimentStatus.rejected_physics,
        ExperimentStatus.cancelled,
    }
)
_ALLOWED_FINAL_STATUSES = frozenset({"completed", "needs_higher_fidelity"})
_PHYSICS_STATUSES = frozenset(
    {"physically_valid", "physically_valid_with_limits"}
)
_MANIFEST_FILENAME = "ARTIFACT_MANIFEST.json"
_PATH_INDEX_FILENAME = "ARTIFACT_PATH_INDEX.json"


class ArticleAssetCompilationError(ValueError):
    """Base error for trusted asset compilation failures."""


class AssetIdentityError(ArticleAssetCompilationError):
    """Request/task/run/experiment identity mismatch."""


class AssetIntegrityError(ArticleAssetCompilationError):
    """Manifest, hash, path, or candidate integrity violation."""


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


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


def _finite_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


class VerifiedCandidateRecord(_StrictModel):
    """One verified selected/Pareto candidate with its manifest artifacts."""

    schema_version: Literal["verified-candidate-record.v1"] = (
        "verified-candidate-record.v1"
    )
    candidate_id: str
    experiment_id: str
    role_keys: List[str] = Field(default_factory=list)
    is_pareto: bool
    is_baseline: bool
    physics_status: str
    certificate_id: str
    certificate_artifact_id: str
    objective_artifact_id: str
    robustness_artifact_id: str = ""
    simulation_artifact_id: str = ""
    identity_artifact_id: str = ""
    artifact_ids: List[str] = Field(default_factory=list)
    target_score: Optional[float] = None
    robustness_score: Optional[float] = None
    simplicity_score: Optional[float] = None
    distinctiveness_score: Optional[float] = None

    @field_validator("candidate_id", "experiment_id", "certificate_id")
    @classmethod
    def _non_empty_ids(cls, value: str, info: Any) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return text

    @field_validator(
        "target_score",
        "robustness_score",
        "simplicity_score",
        "distinctiveness_score",
    )
    @classmethod
    def _finite_scores(cls, value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("candidate scores must be finite when present")
        return number

    @field_validator("role_keys", "artifact_ids")
    @classmethod
    def _ordered_unique_lists(cls, value: List[str]) -> List[str]:
        return sorted(set(item for item in value if item))


class ArticleAssetCompilationResult(_StrictModel):
    """Strict envelope for one trusted asset compilation attempt."""

    schema_version: Literal["article-asset-compilation-result.v1"] = (
        "article-asset-compilation-result.v1"
    )
    status: Literal["ready", "partial", "unavailable", "invalid"]
    result_id: str = ""
    request_id: str
    task_hash: str
    task_digest: str
    run_id: str
    experiment_id: str
    observation_id: str
    manifest_head_hash: str = ""
    manifest_sha256: str = ""
    validation_errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    descriptors: List[ArtifactDescriptor] = Field(default_factory=list)
    trusted_values: List[TrustedValueRecord] = Field(default_factory=list)
    candidates: List[VerifiedCandidateRecord] = Field(default_factory=list)
    observation: ObservationCard


def compute_asset_compilation_result_id(
    result: ArticleAssetCompilationResult | Mapping[str, Any],
) -> str:
    """Deterministic public content ID over the compilation result."""

    model = (
        result
        if isinstance(result, ArticleAssetCompilationResult)
        else ArticleAssetCompilationResult.model_validate(result)
    )
    payload = model.model_dump(mode="json")
    payload.pop("result_id", None)
    return _sha256_text(_canonical_json(payload))


def _resolve_artifact_path(run_root: Path, relative_path: str) -> Path:
    """Resolve a manifest-relative artifact path safely under run_root."""

    if not isinstance(relative_path, str) or not relative_path.strip():
        raise AssetIntegrityError("artifact relative_path must be non-empty")
    text = str(relative_path).replace("\\", "/")
    if "\x00" in text:
        raise AssetIntegrityError("artifact relative_path must not contain NUL")
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise AssetIntegrityError(
            f"unsafe artifact relative_path: {relative_path!r}"
        )
    candidate = run_root / Path(*parts)
    try:
        resolved = candidate.resolve(strict=False)
    except OSError as exc:
        raise AssetIntegrityError(
            f"cannot resolve artifact path {relative_path!r}: {exc}"
        ) from exc
    try:
        resolved.relative_to(run_root.resolve(strict=False))
    except ValueError as exc:
        raise AssetIntegrityError(
            f"artifact path escapes the run root: {relative_path!r}"
        ) from exc
    return resolved


def _load_json_file(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise AssetIntegrityError(f"cannot read JSON artifact {path}: {exc}") from exc


def _validate_path_index(payload: Any, errors: List[str]) -> Dict[str, str]:
    """Validate ARTIFACT_PATH_INDEX.json into experiment_id -> directory."""

    mapping: Dict[str, str] = {}
    if not isinstance(payload, Mapping):
        errors.append("ARTIFACT_PATH_INDEX.json must be a JSON object")
        return mapping
    experiments = payload.get("experiments")
    if not isinstance(experiments, list):
        errors.append("ARTIFACT_PATH_INDEX.json experiments must be a list")
        return mapping
    for row in experiments:
        if not isinstance(row, Mapping):
            errors.append("ARTIFACT_PATH_INDEX.json rows must be objects")
            continue
        experiment_id = str(row.get("experiment_id") or "").strip()
        physical = str(row.get("physical_directory") or "").strip()
        if not experiment_id or not physical:
            errors.append(
                "ARTIFACT_PATH_INDEX.json row must have non-empty "
                "experiment_id and physical_directory"
            )
            continue
        physical_text = physical.replace("\\", "/")
        if (
            physical_text.startswith("/")
            or ":" in physical_text
            or any(part in {"", ".", ".."} for part in physical_text.split("/"))
        ):
            errors.append(
                f"unsafe physical_directory {physical!r} in path index"
            )
            continue
        if experiment_id in mapping and mapping[experiment_id] != physical:
            errors.append(
                f"duplicate experiment_id {experiment_id!r} with conflicting "
                "physical directories in path index"
            )
            continue
        if physical in mapping.values() and mapping[experiment_id] != physical:
            errors.append(
                f"duplicate physical_directory {physical!r} assigned to "
                "different experiment ids in path index"
            )
            continue
        mapping[experiment_id] = physical
    return mapping


def _load_run_context(
    run_root: str | Path,
    request: CompiledExperimentRequest,
    errors: List[str],
    warnings: List[str],
) -> Optional[Dict[str, Any]]:
    root = Path(run_root)
    try:
        resolved_root = root.resolve(strict=False)
    except OSError as exc:
        errors.append(f"cannot resolve run root: {exc}")
        return None
    if not resolved_root.is_dir():
        errors.append(f"run root is not a directory: {root}")
        return None
    for name in (
        "TASK.json",
        "FINAL_RESULT.json",
        _MANIFEST_FILENAME,
    ):
        if not (resolved_root / name).is_file():
            errors.append(f"run root is missing required file {name}")
            return None

    manifest_path = resolved_root / _MANIFEST_FILENAME
    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError as exc:
        errors.append(f"cannot read artifact manifest: {exc}")
        return None
    manifest_sha256 = _sha256_bytes(manifest_bytes)

    try:
        store = ArtifactLineageStore.from_disk(resolved_root)
        if not store.verify_all():
            errors.append("artifact manifest verification failed")
            return None
        records = store.records
    except Exception as exc:  # noqa: BLE001 - captured as a validation error
        errors.append(f"artifact manifest verification failed: {exc}")
        return None
    if errors:
        return None

    by_id: Dict[str, Any] = {}
    by_path: Dict[str, Any] = {}
    for record in records:
        if record.artifact_id in by_id:
            errors.append(
                f"duplicate artifact_id in manifest: {record.artifact_id!r}"
            )
            continue
        by_id[record.artifact_id] = record
        if record.relative_path in by_path:
            errors.append(
                f"duplicate relative_path in manifest: "
                f"{record.relative_path!r}"
            )
            continue
        by_path[record.relative_path] = record
    if errors:
        return None

    try:
        manifest_payload = json.loads(manifest_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        errors.append(f"artifact manifest is not valid JSON: {exc}")
        return None
    if not isinstance(manifest_payload, Mapping):
        errors.append("artifact manifest must be a JSON object")
        return None
    head_hash = str(manifest_payload.get("head_hash") or "")
    if not head_hash:
        errors.append("artifact manifest is missing head_hash")
        return None

    task_path = resolved_root / "TASK.json"
    try:
        task_payload = json.loads(task_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        errors.append(f"TASK.json is invalid: {exc}")
        return None
    try:
        task_model = (
            task_payload
            if isinstance(task_payload, OpticalDesignTask)
            else OpticalDesignTask.model_validate(task_payload)
        )
        computed_digest = compute_optical_design_task_digest(task_model)
    except (ValidationError, ValueError) as exc:
        errors.append(f"TASK.json does not encode a valid OpticalDesignTask: {exc}")
        return None
    if computed_digest != request.task_digest:
        errors.append(
            "TASK.json canonical digest does not match "
            f"CompiledExperimentRequest.task_digest "
            f"({computed_digest} != {request.task_digest})"
        )
    task_record = by_id.get("TASK.json") or by_path.get("TASK.json")
    if task_record is None or task_record.artifact_type != "task_contract":
        errors.append("manifest is missing a task_contract record for TASK.json")
    else:
        task_provenance = task_record.scientific_provenance or {}
        if str(task_provenance.get("task_sha256") or "") != computed_digest:
            errors.append(
                "manifest TASK.json scientific_provenance.task_sha256 does "
                "not match the canonical task digest"
            )
        task_actual = _sha256_bytes(task_path.read_bytes())
        if task_actual != task_record.sha256:
            errors.append("manifest TASK.json sha256 does not match file bytes")

    try:
        final_payload = json.loads(
            (resolved_root / "FINAL_RESULT.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        errors.append(f"FINAL_RESULT.json is invalid: {exc}")
        return None
    if not isinstance(final_payload, Mapping):
        errors.append("FINAL_RESULT.json must be a JSON object")
        return None
    final_record = by_id.get("FINAL_RESULT.json") or by_path.get(
        "FINAL_RESULT.json"
    )
    if final_record is None or final_record.artifact_type != "final_result":
        errors.append(
            "manifest is missing a final_result record for FINAL_RESULT.json"
        )
    else:
        final_actual = _sha256_bytes(
            (resolved_root / "FINAL_RESULT.json").read_bytes()
        )
        if final_actual != final_record.sha256:
            errors.append(
                "manifest FINAL_RESULT.json sha256 does not match file bytes"
            )

    final_run_id = str(final_payload.get("run_id") or "")
    final_task_id = str(final_payload.get("task_id") or "")
    task_id = str(task_payload.get("task_id") or "")
    if final_run_id != request.run_id:
        errors.append(
            "FINAL_RESULT.json run_id does not match "
            f"CompiledExperimentRequest.run_id ({final_run_id!r} != "
            f"{request.run_id!r})"
        )
    if final_task_id != task_id:
        errors.append(
            "FINAL_RESULT.json task_id does not match TASK.json task_id"
        )
    final_status = str(final_payload.get("status") or "")
    if final_status not in _ALLOWED_FINAL_STATUSES:
        errors.append(
            "FINAL_RESULT.json status is not completed or "
            "needs_higher_fidelity; a failed or rejected run cannot produce "
            "trusted assets"
        )
    if errors:
        return None

    path_index: Dict[str, str] = {}
    index_record = by_id.get(_PATH_INDEX_FILENAME) or by_path.get(
        _PATH_INDEX_FILENAME
    )
    index_path = resolved_root / _PATH_INDEX_FILENAME
    if index_record is not None and index_path.is_file():
        try:
            index_payload = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"ARTIFACT_PATH_INDEX.json is invalid: {exc}")
            return None
        path_index = _validate_path_index(index_payload, errors)
        if errors:
            return None
    elif index_path.is_file():
        try:
            index_payload = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"ARTIFACT_PATH_INDEX.json is invalid: {exc}")
            return None
        path_index = _validate_path_index(index_payload, errors)
        if errors:
            return None
    elif index_record is not None:
        errors.append("manifest references ARTIFACT_PATH_INDEX.json but it is missing")
        return None

    return {
        "root": resolved_root,
        "records": tuple(by_id.values()),
        "by_id": by_id,
        "by_path": by_path,
        "manifest_sha256": manifest_sha256,
        "manifest_head_hash": head_hash,
        "task_payload": task_payload,
        "task_digest": computed_digest,
        "task_id": task_id,
        "final_payload": final_payload,
        "final_run_id": final_run_id,
        "path_index": path_index,
    }


def _verify_request_identity(
    request: CompiledExperimentRequest,
    execution: ArticleExecutionResult,
    authority: Optional[ArticleCompilationAuthority],
    errors: List[str],
) -> None:
    if request.task_hash != execution.task_hash:
        errors.append(
            "request.task_hash does not match execution task_hash "
            f"({request.task_hash!r} != {execution.task_hash!r})"
        )
    if request.request_id != execution.request_id:
        errors.append(
            "request.request_id does not match execution request_id "
            f"({request.request_id!r} != {execution.request_id!r})"
        )
    if not request.task_digest:
        errors.append(
            "CompiledExperimentRequest.task_digest is empty; an execution "
            "bound request must carry the canonical task identity"
        )
    if request.task_hash != compute_task_hash(request):
        errors.append("CompiledExperimentRequest.task_hash is not self-consistent")
    expected_request_id = compute_request_id(request.task_hash, request.proposal_id)
    if request.request_id != expected_request_id:
        errors.append(
            "CompiledExperimentRequest.request_id is not derived from "
            "task_hash/proposal_id"
        )
    if str(execution.outcome or "") != execution.observation.status.value:
        errors.append(
            "ArticleExecutionResult.outcome does not match "
            "observation.status"
        )
    if authority is not None:
        if authority.authority_id != request.authority_id:
            errors.append(
                "supplied ArticleCompilationAuthority does not match "
                "request.authority_id"
            )
        elif not authority.verify(request):
            errors.append("compiler attestation verification failed")
    receipt = execution.receipt if isinstance(execution.receipt, Mapping) else {}
    if str(receipt.get("status") or "") != "adapter_completed":
        errors.append(
            "execution receipt does not indicate adapter_completed status; a "
            "failed/rejected execution cannot produce trusted assets"
        )


def _experiment_directory(
    context: Dict[str, Any], experiment_id: str
) -> Optional[str]:
    path_index = context["path_index"]
    if experiment_id in path_index:
        return path_index[experiment_id]
    return f"experiments/{experiment_id}"


def _baseline_directory(context: Dict[str, Any], experiment_id: str) -> str:
    """Resolve the baseline directory for legacy or compact TMM layouts."""

    experiment_dir = str(_experiment_directory(context, experiment_id))
    parts = Path(experiment_dir).parts
    segment = "baseline" if parts and parts[0] == "experiments" else "b"
    return f"{experiment_dir}/{segment}"


def _select_candidates(
    portfolio: Mapping[str, Any],
    experiment_id: str,
    pareto_ids: Sequence[str],
    errors: List[str],
    warnings: List[str],
) -> List[Dict[str, Any]]:
    selected_roles = portfolio.get("selected_roles")
    if not isinstance(selected_roles, Mapping):
        errors.append(
            f"DESIGN_PORTFOLIO for {experiment_id} has no selected_roles mapping"
        )
        return []
    candidates = portfolio.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        errors.append(f"DESIGN_PORTFOLIO for {experiment_id} has no candidates")
        return []
    by_id: Dict[str, List[Dict[str, Any]]] = {}
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            errors.append("DESIGN_PORTFOLIO candidate rows must be objects")
            continue
        candidate_id = str(candidate.get("candidate_id") or "").strip()
        if not candidate_id:
            errors.append("DESIGN_PORTFOLIO candidate has empty candidate_id")
            continue
        by_id.setdefault(candidate_id, []).append(candidate)
    for candidate_id, matches in by_id.items():
        if len(matches) > 1:
            errors.append(
                f"ambiguous candidate mapping: candidate_id {candidate_id!r} "
                "appears more than once in DESIGN_PORTFOLIO"
            )
    selected: List[str] = []
    role_map: Dict[str, str] = {}
    for role_key, candidate_id in selected_roles.items():
        text = str(candidate_id or "").strip()
        if not text:
            continue
        role_map[str(role_key)] = text
        selected.append(text)
    selected.extend(str(item or "").strip() for item in pareto_ids)
    selected = sorted(set(item for item in selected if item))
    if not selected:
        errors.append(
            f"DESIGN_PORTFOLIO for {experiment_id} selects no candidates"
        )
        return []
    rows: List[Dict[str, Any]] = []
    for candidate_id in selected:
        matches = by_id.get(candidate_id)
        if not matches:
            errors.append(
                f"selected candidate {candidate_id!r} is missing from "
                "DESIGN_PORTFOLIO candidates"
            )
            continue
        if len(matches) > 1:
            continue
        row = dict(matches[0])
        row["__roles__"] = sorted(
            role for role, value in role_map.items() if value == candidate_id
        )
        row["__is_pareto__"] = candidate_id in set(pareto_ids)
        rows.append(row)
    return rows


def _certificate_record(
    by_id: Mapping[str, Any], candidate: Mapping[str, Any]
) -> Optional[Tuple[Any, str]]:
    artifact_ids = candidate.get("artifact_ids")
    if not isinstance(artifact_ids, list):
        return None
    certificates = [
        record
        for record in (
            by_id.get(str(artifact_id))
            for artifact_id in artifact_ids
            if isinstance(artifact_id, str)
        )
        if record is not None
        and getattr(record, "artifact_type", "") == "physics_acceptance_certificate"
    ]
    if len(certificates) != 1:
        return None
    return certificates[0], certificates[0].artifact_id


def _verify_candidate(
    candidate: Mapping[str, Any],
    experiment_id: str,
    context: Dict[str, Any],
    errors: List[str],
    warnings: List[str],
) -> Optional[VerifiedCandidateRecord]:
    by_id = context["by_id"]
    root = context["root"]
    candidate_id = str(candidate.get("candidate_id") or "").strip()
    physics_status = str(candidate.get("physics_status") or "").strip()
    if not candidate_id:
        errors.append("candidate has empty candidate_id")
        return None
    if candidate.get("physically_admissible") is not True:
        errors.append(
            f"selected candidate {candidate_id!r} is not physically admissible"
        )
        return None
    if physics_status not in _PHYSICS_STATUSES:
        errors.append(
            f"selected candidate {candidate_id!r} has physics_status "
            f"{physics_status!r}; rejected candidates cannot be trusted"
        )
        return None
    certificate_pair = _certificate_record(by_id, candidate)
    if certificate_pair is None:
        errors.append(
            f"candidate {candidate_id!r} must resolve to exactly one "
            "physics_acceptance_certificate manifest artifact"
        )
        return None
    certificate_record, certificate_artifact_id = certificate_pair
    certificate_path = _resolve_artifact_path(
        root, certificate_record.relative_path
    )
    if not certificate_path.is_file():
        errors.append(
            f"certificate artifact is missing on disk: {certificate_path}"
        )
        return None
    certificate_payload = _load_json_file(certificate_path)
    if not isinstance(certificate_payload, Mapping):
        errors.append(f"certificate for {candidate_id!r} is not a JSON object")
        return None
    expected_certificate_id = str(candidate.get("certificate_id") or "").strip()
    actual_certificate_id = str(
        certificate_payload.get("certificate_id") or ""
    ).strip()
    if (
        not expected_certificate_id
        or expected_certificate_id != actual_certificate_id
    ):
        errors.append(
            f"candidate {candidate_id!r} certificate_id does not match the "
            "certificate artifact content"
        )
        return None
    if certificate_payload.get("accepted") is not True:
        errors.append(
            f"certificate for candidate {candidate_id!r} is not accepted"
        )
        return None
    certificate_dir = certificate_path.parent
    cert_parent_relative = Path(
        certificate_record.relative_path
    ).parent.as_posix()
    experiment_dir = _experiment_directory(context, experiment_id)
    baseline_dir = _baseline_directory(context, experiment_id)
    if not cert_parent_relative.startswith(experiment_dir + "/"):
        errors.append(
            f"candidate {candidate_id!r} certificate directory "
            f"{cert_parent_relative!r} is outside the experiment directory "
            f"{experiment_dir!r}"
        )
    metadata = candidate.get("metadata")
    source_is_baseline = bool(
        isinstance(metadata, Mapping)
        and str(metadata.get("source") or "") == "initial_baseline"
    )
    is_baseline = source_is_baseline and cert_parent_relative == baseline_dir
    if source_is_baseline and cert_parent_relative != baseline_dir:
        errors.append(
            f"candidate {candidate_id!r} claims initial_baseline source but "
            "its certificate is not in the experiment baseline directory"
        )
    if cert_parent_relative == baseline_dir and not source_is_baseline:
        errors.append(
            f"candidate {candidate_id!r} certificate is in the experiment "
            "baseline directory but the candidate is not verified as the "
            "baseline source"
        )
    if candidate_id.endswith("__baseline") and not is_baseline:
        errors.append(
            f"candidate {candidate_id!r} claims a __baseline identity but is "
            "not the actual experiment baseline"
        )

    artifact_ids = [
        str(item)
        for item in (candidate.get("artifact_ids") or [])
        if isinstance(item, str) and str(item).strip()
    ]
    objective_artifact_id = ""
    for artifact_id in artifact_ids:
        record = by_id.get(artifact_id)
        if record is None:
            errors.append(
                f"candidate {candidate_id!r} references unknown manifest "
                f"artifact {artifact_id!r}"
            )
            continue
        if record.artifact_type == "objective_report":
            if objective_artifact_id:
                errors.append(
                    f"candidate {candidate_id!r} has multiple objective reports"
                )
            if (
                Path(record.relative_path).parent.as_posix()
                != cert_parent_relative
            ):
                errors.append(
                    f"candidate {candidate_id!r} objective report is "
                    "cross-wired: its physical directory differs from the "
                    "certificate directory"
                )
            objective_artifact_id = artifact_id
    if not objective_artifact_id:
        errors.append(
            f"candidate {candidate_id!r} has no objective_report artifact"
        )
        return None

    simulation_artifact_id = ""
    for input_id in certificate_record.input_artifact_ids:
        record = by_id.get(str(input_id))
        if record is not None and record.artifact_type == "simulation_result":
            if simulation_artifact_id:
                errors.append(
                    f"candidate {candidate_id!r} certificate has multiple "
                    "simulation_result inputs"
                )
            if (
                Path(record.relative_path).parent.as_posix()
                != cert_parent_relative
            ):
                errors.append(
                    f"candidate {candidate_id!r} simulation result is "
                    "cross-wired: its physical directory differs from the "
                    "certificate directory"
                )
            simulation_artifact_id = record.artifact_id
    if not simulation_artifact_id:
        same_dir = sorted(
            record.artifact_id
            for record in by_id.values()
            if record.artifact_type == "simulation_result"
            and Path(record.relative_path).parent
            == Path(certificate_record.relative_path).parent
        )
        if len(same_dir) == 1:
            simulation_artifact_id = same_dir[0]
        elif len(same_dir) > 1:
            errors.append(
                f"candidate {candidate_id!r} has multiple simulation_result "
                "artifacts in its physical directory"
            )
    if not simulation_artifact_id:
        errors.append(
            f"candidate {candidate_id!r} has no simulation_result artifact"
        )
        return None

    robustness_artifact_id = ""
    same_dir_robust = sorted(
        record.artifact_id
        for record in by_id.values()
        if record.artifact_type == "robustness_report"
        and Path(record.relative_path).parent.as_posix()
        == cert_parent_relative
    )
    for artifact_id in artifact_ids:
        record = by_id.get(artifact_id)
        if record is not None and record.artifact_type == "robustness_report":
            if (
                Path(record.relative_path).parent.as_posix()
                != cert_parent_relative
            ):
                errors.append(
                    f"candidate {candidate_id!r} robustness report is "
                    "cross-wired: its physical directory differs from the "
                    "certificate directory"
                )
            elif artifact_id not in same_dir_robust:
                same_dir_robust.append(artifact_id)
    if len(same_dir_robust) > 1:
        errors.append(
            f"candidate {candidate_id!r} has ambiguous robustness_report "
            "artifacts"
        )
    elif len(same_dir_robust) == 1:
        robustness_artifact_id = same_dir_robust[0]
    else:
        warnings.append(
            f"candidate {candidate_id!r} has no robustness_report; "
            "robustness metrics are omitted (partial coverage)"
        )

    identity_artifact_id = ""
    if not is_baseline:
        identity_relative = (
            Path(certificate_record.relative_path).parent / "IDENTITY.json"
        ).as_posix()
        identity_record = context["by_path"].get(identity_relative)
        if (
            identity_record is None
            or identity_record.artifact_type != "candidate_identity"
        ):
            errors.append(
                f"candidate {candidate_id!r} has no candidate_identity "
                "manifest record"
            )
            return None
        if (
            Path(identity_record.relative_path).parent.as_posix()
            != cert_parent_relative
        ):
            errors.append(
                f"candidate {candidate_id!r} identity file is cross-wired: "
                "its physical directory differs from the certificate "
                "directory"
            )
        identity_path = _resolve_artifact_path(root, identity_relative)
        if not identity_path.is_file():
            errors.append(
                f"candidate identity artifact is missing on disk: {identity_path}"
            )
            return None
        identity_payload = _load_json_file(identity_path)
        if not isinstance(identity_payload, Mapping):
            errors.append(f"identity for {candidate_id!r} is not a JSON object")
            return None
        if (
            str(identity_payload.get("candidate_id") or "") != candidate_id
            or str(identity_payload.get("experiment_id") or "") != experiment_id
        ):
            errors.append(
                f"candidate {candidate_id!r} identity file is cross-wired "
                "with a different experiment/candidate"
            )
            return None
        declared_dir = str(
            identity_payload.get("physical_directory") or ""
        ).strip()
        if declared_dir and _normalize_relative_dir(declared_dir) != (
            _normalize_relative_dir(
                Path(identity_record.relative_path).parent.as_posix()
            )
        ):
            errors.append(
                f"candidate {candidate_id!r} identity physical_directory "
                "does not match its manifest parent directory"
            )
        identity_artifact_id = identity_record.artifact_id

    all_artifact_ids = sorted(
        set(
            [
                certificate_artifact_id,
                objective_artifact_id,
                simulation_artifact_id,
                identity_artifact_id,
            ]
            + ([robustness_artifact_id] if robustness_artifact_id else [])
        )
    )
    target_score = _finite_number(candidate.get("target_score"))
    robustness_score = _finite_number(candidate.get("robustness_score"))
    simplicity_score = _finite_number(candidate.get("simplicity_score"))
    distinctiveness_score = _finite_number(candidate.get("distinctiveness_score"))
    verified = VerifiedCandidateRecord(
        candidate_id=candidate_id,
        experiment_id=experiment_id,
        role_keys=list(candidate.get("__roles__") or []),
        is_pareto=bool(candidate.get("__is_pareto__")),
        is_baseline=is_baseline,
        physics_status=physics_status,
        certificate_id=expected_certificate_id,
        certificate_artifact_id=certificate_artifact_id,
        objective_artifact_id=objective_artifact_id,
        robustness_artifact_id=robustness_artifact_id,
        simulation_artifact_id=simulation_artifact_id,
        identity_artifact_id=identity_artifact_id,
        artifact_ids=all_artifact_ids,
        target_score=target_score,
        robustness_score=robustness_score,
        simplicity_score=simplicity_score,
        distinctiveness_score=distinctiveness_score,
    )
    _verify_candidate_relationships(
        verified, experiment_id, context, errors, warnings
    )
    return verified


def _normalize_relative_dir(value: str) -> str:
    """Normalize a relative directory for stable physical-dir comparison."""

    parts = [
        part
        for part in str(value).replace("\\", "/").split("/")
        if part not in {"", "."}
    ]
    return "/".join(parts)


def _verify_candidate_relationships(
    candidate: VerifiedCandidateRecord,
    experiment_id: str,
    context: Dict[str, Any],
    errors: List[str],
    warnings: List[str],
) -> None:
    """Recheck candidate/manifest physical relationships (validator path).

    Every companion artifact must live in the exact same physical candidate
    directory as the certificate; the certificate directory must be inside
    the experiment directory (baseline directory for a verified baseline);
    and the identity file, when present, must bind the candidate/experiment
    and its declared physical_directory must match its manifest parent.
    """

    by_id = context["by_id"]
    root = context["root"]
    experiment_dir = _experiment_directory(context, experiment_id)
    baseline_dir = _baseline_directory(context, experiment_id)
    cert_record = by_id.get(candidate.certificate_artifact_id)
    if (
        cert_record is None
        or cert_record.artifact_type != "physics_acceptance_certificate"
    ):
        errors.append(
            f"candidate {candidate.candidate_id!r} certificate artifact is "
            "missing or not a physics_acceptance_certificate manifest record"
        )
        return
    cert_parent = Path(cert_record.relative_path).parent.as_posix()
    if cert_parent == baseline_dir:
        if not candidate.is_baseline:
            errors.append(
                f"candidate {candidate.candidate_id!r} certificate lives in "
                "the experiment baseline directory but the candidate is not "
                "verified as baseline"
            )
    elif not cert_parent.startswith(experiment_dir + "/"):
        errors.append(
            f"candidate {candidate.candidate_id!r} certificate directory "
            f"{cert_parent!r} is outside the experiment directory "
            f"{experiment_dir!r}"
        )
    elif candidate.is_baseline:
        errors.append(
            f"candidate {candidate.candidate_id!r} is verified baseline but "
            "its certificate is not in the experiment baseline directory"
        )

    cert_path = _resolve_artifact_path(root, cert_record.relative_path)
    if cert_path.is_file():
        try:
            cert_payload = _load_json_file(cert_path)
        except ArticleAssetCompilationError as exc:
            errors.append(str(exc))
        else:
            if isinstance(cert_payload, Mapping):
                if (
                    str(cert_payload.get("certificate_id") or "")
                    != candidate.certificate_id
                ):
                    errors.append(
                        f"candidate {candidate.candidate_id!r} certificate "
                        "content certificate_id does not match the verified "
                        "candidate record"
                    )
                if cert_payload.get("accepted") is not True:
                    errors.append(
                        f"candidate {candidate.candidate_id!r} certificate "
                        "is not accepted"
                    )

    companions = (
        ("objective report", candidate.objective_artifact_id, "objective_report"),
        ("simulation result", candidate.simulation_artifact_id, "simulation_result"),
        ("robustness report", candidate.robustness_artifact_id, "robustness_report"),
        ("identity", candidate.identity_artifact_id, "candidate_identity"),
    )
    for label, artifact_id, expected_type in companions:
        if not artifact_id:
            continue
        record = by_id.get(artifact_id)
        if record is None or record.artifact_type != expected_type:
            errors.append(
                f"candidate {candidate.candidate_id!r} {label} artifact is "
                "missing or has the wrong manifest type"
            )
            continue
        if Path(record.relative_path).parent.as_posix() != cert_parent:
            errors.append(
                f"candidate {candidate.candidate_id!r} {label} is "
                "cross-wired: its physical directory differs from the "
                "certificate directory"
            )

    if candidate.identity_artifact_id:
        identity_record = by_id.get(candidate.identity_artifact_id)
        if identity_record is None:
            return
        identity_path = _resolve_artifact_path(
            root, identity_record.relative_path
        )
        if not identity_path.is_file():
            errors.append(
                f"candidate {candidate.candidate_id!r} identity artifact is "
                "missing on disk"
            )
            return
        try:
            identity_payload = _load_json_file(identity_path)
        except ArticleAssetCompilationError as exc:
            errors.append(str(exc))
            return
        if not isinstance(identity_payload, Mapping):
            errors.append(
                f"identity for {candidate.candidate_id!r} is not a JSON object"
            )
            return
        if (
            str(identity_payload.get("candidate_id") or "")
            != candidate.candidate_id
            or str(identity_payload.get("experiment_id") or "")
            != experiment_id
        ):
            errors.append(
                f"candidate {candidate.candidate_id!r} identity file is "
                "cross-wired with a different experiment/candidate"
            )
        declared_dir = str(
            identity_payload.get("physical_directory") or ""
        ).strip()
        if declared_dir and _normalize_relative_dir(declared_dir) != (
            _normalize_relative_dir(
                Path(identity_record.relative_path).parent.as_posix()
            )
        ):
            errors.append(
                f"candidate {candidate.candidate_id!r} identity "
                "physical_directory does not match its manifest parent "
                "directory"
            )


def _validation_context(
    records: Sequence[Any],
    resolved_root: Path,
    errors: List[str],
) -> Dict[str, Any]:
    """Build a read-only manifest context for public validation."""

    by_id: Dict[str, Any] = {}
    by_path: Dict[str, Any] = {}
    for record in records:
        by_id.setdefault(record.artifact_id, record)
        by_path.setdefault(record.relative_path, record)
    path_index: Dict[str, str] = {}
    index_path = resolved_root / _PATH_INDEX_FILENAME
    if index_path.is_file():
        try:
            index_payload = json.loads(
                index_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            errors.append(f"ARTIFACT_PATH_INDEX.json is invalid: {exc}")
        else:
            path_index = _validate_path_index(index_payload, errors)
    final_payload: Dict[str, Any] = {}
    final_path = resolved_root / "FINAL_RESULT.json"
    if final_path.is_file():
        try:
            payload = json.loads(final_path.read_text(encoding="utf-8"))
            if isinstance(payload, Mapping):
                final_payload = dict(payload)
        except (OSError, ValueError) as exc:
            errors.append(f"FINAL_RESULT.json is invalid: {exc}")
    return {
        "root": resolved_root,
        "by_id": by_id,
        "by_path": by_path,
        "path_index": path_index,
        "final_payload": final_payload,
    }


def _spectrum_fields(payload: Mapping[str, Any]) -> List[str]:
    channels = payload.get("channels")
    wavelengths = payload.get("wavelengths_nm")
    if not isinstance(wavelengths, list) or not isinstance(channels, Mapping):
        return []
    fields = ["wavelengths_nm"]
    for channel_key in sorted(channels):
        channel = channels[channel_key]
        if not isinstance(channel, Mapping):
            continue
        for observable in sorted(channel):
            fields.append(f"channels.{channel_key}.{observable}")
    return fields


def _spectrum_summary(payload: Mapping[str, Any]) -> str:
    channels = payload.get("channels")
    wavelengths = payload.get("wavelengths_nm")
    if isinstance(wavelengths, list) and isinstance(channels, Mapping):
        return (
            f"spectrum: {len(wavelengths)} wavelengths, "
            f"{len(channels)} channels"
        )
    return "JSON object"


def _declared_scalar_fields(
    artifact_type: str, payload: Mapping[str, Any]
) -> List[str]:
    """Derived trusted-value field names declared by this artifact's content.

    These names are the exact ``TrustedValueRecord.field`` values emitted by
    ``_emit_trusted_values``, so the writing/presentation layers can verify
    every trusted value against the artifact descriptor.
    """

    fields: List[str] = []
    if artifact_type == "design_portfolio":
        candidates = payload.get("candidates")
        if isinstance(candidates, list):
            for candidate in candidates:
                if not isinstance(candidate, Mapping):
                    continue
                candidate_id = str(candidate.get("candidate_id") or "").strip()
                if not candidate_id:
                    continue
                for scalar in (
                    "target_score",
                    "robustness_score",
                    "simplicity_score",
                    "distinctiveness_score",
                ):
                    fields.append(f"{candidate_id}.{scalar}")
                objective_scores = candidate.get("objective_scores")
                if isinstance(objective_scores, Mapping):
                    for key in sorted(objective_scores):
                        fields.append(
                            f"{candidate_id}.objective_scores.{key}"
                        )
    elif artifact_type == "objective_report":
        fields.extend(
            [
                "objective_report.aggregate_soft_score",
                "objective_report.weighted_directional_loss",
            ]
        )
        attainment = payload.get("target_attainment")
        if isinstance(attainment, Mapping):
            for canonical in sorted(attainment):
                for sub_key in ("observed", "target", "soft_score"):
                    fields.append(
                        f"objective_report.target_attainment."
                        f"{canonical}.{sub_key}"
                    )
    elif artifact_type == "physics_acceptance_certificate":
        fields.extend(
            [
                "physics_certificate.physics_audit.energy_conservation_max_abs_error",
                "physics_certificate.physics_audit.minimum_observable",
                "physics_certificate.physics_audit.maximum_observable",
                "physics_certificate.physics_audit.nonfinite_value_count",
                "physics_certificate.spectral_convergence.final_points",
            ]
        )
    elif artifact_type == "robustness_report":
        fields.extend(
            [
                "robustness_report.nominal_soft_score",
                "robustness_report.mean_soft_score",
                "robustness_report.worst_soft_score",
                "robustness_report.p10_soft_score",
                "robustness_report.robustness_score",
            ]
        )
    return sorted(set(fields))


def _build_descriptors(
    context: Dict[str, Any],
    experiment_id: str,
    observation_id: str,
    errors: List[str],
) -> List[ArtifactDescriptor]:
    root = context["root"]
    by_id = context["by_id"]
    experiment_dir = _experiment_directory(context, experiment_id)
    all_experiments = sorted(
        str(item.get("experiment_id") or "")
        for item in context["final_payload"].get("experiment_results") or []
        if isinstance(item, Mapping) and item.get("experiment_id")
    )
    descriptors: List[ArtifactDescriptor] = []
    for artifact_id in sorted(by_id):
        record = by_id[artifact_id]
        relative_path = record.relative_path
        if not relative_path.lower().endswith(".json"):
            continue
        path = _resolve_artifact_path(root, relative_path)
        if not path.is_file():
            errors.append(
                f"manifest artifact {artifact_id!r} is missing on disk: {path}"
            )
            continue
        actual_sha = _sha256_bytes(path.read_bytes())
        if actual_sha != record.sha256:
            errors.append(
                f"manifest artifact {artifact_id!r} sha256 does not match "
                "file bytes"
            )
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(
                f"manifest artifact {artifact_id!r} is not valid JSON: {exc}"
            )
            continue
        if not isinstance(payload, Mapping):
            continue
        fields = _spectrum_fields(payload) or sorted(
            str(key) for key in payload.keys()
        )
        if not fields:
            continue
        declared = _declared_scalar_fields(record.artifact_type, payload)
        if declared:
            fields = sorted(set(fields) | set(declared))
        if _spectrum_fields(payload):
            channels = payload.get("channels")
            field_descriptions: Dict[str, str] = {
                "wavelengths_nm": "wavelength grid in nanometers",
            }
            for channel_key in sorted(channels):
                channel = channels[channel_key]
                if not isinstance(channel, Mapping):
                    continue
                for observable in sorted(channel):
                    field_descriptions[
                        f"channels.{channel_key}.{observable}"
                    ] = f"{observable} spectrum for channel {channel_key}"
        else:
            field_descriptions = {}
        experiment_dir_text = experiment_dir.replace("\\", "/")
        relative_text = relative_path.replace("\\", "/")
        if relative_text.startswith(experiment_dir_text + "/"):
            source_experiments = [experiment_id]
        elif any(
            relative_text.startswith(directory.replace("\\", "/") + "/")
            for directory in context["path_index"].values()
        ):
            source_experiments = sorted(
                experiment
                for experiment, directory in context["path_index"].items()
                if relative_text.startswith(
                    directory.replace("\\", "/") + "/"
                )
            )
        else:
            source_experiments = all_experiments
        descriptors.append(
            ArtifactDescriptor(
                artifact_id=artifact_id,
                path=relative_path,
                fields=fields,
                artifact_type=record.artifact_type,
                media_type="application/json",
                content_summary=_spectrum_summary(payload)[:240],
                field_descriptions=field_descriptions,
                sha256=record.sha256,
                source_experiment_ids=source_experiments,
                source_observation_ids=(
                    [observation_id] if source_experiments else []
                ),
            )
        )
    return sorted(descriptors, key=lambda item: item.artifact_id)


def _emit_trusted_values(
    candidate: VerifiedCandidateRecord,
    context: Dict[str, Any],
    errors: List[str],
) -> List[TrustedValueRecord]:
    root = context["root"]
    by_id = context["by_id"]
    values: List[TrustedValueRecord] = []
    portfolio_relative = _experiment_directory(context, candidate.experiment_id)
    portfolio_id = f"{portfolio_relative}/DESIGN_PORTFOLIO.json"
    portfolio_record = context["by_path"].get(portfolio_id)
    if portfolio_record is None:
        portfolio_record = next(
            (
                record
                for record in by_id.values()
                if record.artifact_type == "design_portfolio"
                and Path(record.relative_path).parent.as_posix()
                == portfolio_relative
            ),
            None,
        )
    if portfolio_record is not None:
        portfolio_path = _resolve_artifact_path(root, portfolio_record.relative_path)
        try:
            portfolio_payload = json.loads(
                portfolio_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            errors.append(f"cannot read DESIGN_PORTFOLIO.json: {exc}")
            portfolio_payload = None
        if isinstance(portfolio_payload, Mapping):
            candidate_rows = portfolio_payload.get("candidates")
            if isinstance(candidate_rows, list):
                row = next(
                    (
                        item
                        for item in candidate_rows
                        if isinstance(item, Mapping)
                        and str(item.get("candidate_id") or "")
                        == candidate.candidate_id
                    ),
                    None,
                )
                if isinstance(row, Mapping):
                    _push_scalar(
                        values,
                        artifact_id=portfolio_record.artifact_id,
                        field=f"{candidate.candidate_id}.target_score",
                        value=row.get("target_score"),
                        label="Target score",
                        candidate_id=candidate.candidate_id,
                        relative_path=portfolio_record.relative_path,
                        source_hash=portfolio_record.sha256,
                        errors=errors,
                    )
                    _push_scalar(
                        values,
                        artifact_id=portfolio_record.artifact_id,
                        field=f"{candidate.candidate_id}.robustness_score",
                        value=row.get("robustness_score"),
                        label="Robustness score",
                        candidate_id=candidate.candidate_id,
                        relative_path=portfolio_record.relative_path,
                        source_hash=portfolio_record.sha256,
                        errors=errors,
                    )
                    _push_scalar(
                        values,
                        artifact_id=portfolio_record.artifact_id,
                        field=f"{candidate.candidate_id}.simplicity_score",
                        value=row.get("simplicity_score"),
                        label="Simplicity score",
                        candidate_id=candidate.candidate_id,
                        relative_path=portfolio_record.relative_path,
                        source_hash=portfolio_record.sha256,
                        errors=errors,
                    )
                    _push_scalar(
                        values,
                        artifact_id=portfolio_record.artifact_id,
                        field=f"{candidate.candidate_id}.distinctiveness_score",
                        value=row.get("distinctiveness_score"),
                        label="Distinctiveness score",
                        candidate_id=candidate.candidate_id,
                        relative_path=portfolio_record.relative_path,
                        source_hash=portfolio_record.sha256,
                        errors=errors,
                    )
                    objective_scores = row.get("objective_scores")
                    if isinstance(objective_scores, Mapping):
                        for key in sorted(objective_scores):
                            _push_scalar(
                                values,
                                artifact_id=portfolio_record.artifact_id,
                                field=(
                                    f"{candidate.candidate_id}."
                                    f"objective_scores.{key}"
                                ),
                                value=objective_scores[key],
                                label=f"Objective score {key}",
                                candidate_id=candidate.candidate_id,
                                relative_path=portfolio_record.relative_path,
                                source_hash=portfolio_record.sha256,
                                errors=errors,
                            )

    report_plan = (
        (
            candidate.objective_artifact_id,
            "objective_report",
            {
                "aggregate_soft_score": "Aggregate soft score",
                "weighted_directional_loss": "Weighted directional loss",
            },
        ),
        (
            candidate.certificate_artifact_id,
            "physics_certificate",
            {
                "physics_audit.energy_conservation_max_abs_error": (
                    "Energy conservation max abs error"
                ),
                "physics_audit.minimum_observable": "Minimum observable",
                "physics_audit.maximum_observable": "Maximum observable",
                "physics_audit.nonfinite_value_count": (
                    "Non-finite value count"
                ),
                "spectral_convergence.final_points": (
                    "Spectral convergence final points"
                ),
            },
        ),
    )
    for artifact_id, kind, scalar_map in report_plan:
        record = by_id.get(artifact_id)
        if record is None:
            errors.append(
                f"candidate {candidate.candidate_id!r} missing {kind} artifact"
            )
            continue
        path = _resolve_artifact_path(root, record.relative_path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"cannot read {kind} artifact: {exc}")
            continue
        for field, label in scalar_map.items():
            value = _lookup_path(payload, field.split("."))
            _push_scalar(
                values,
                artifact_id=record.artifact_id,
                field=f"{kind}.{field}",
                value=value,
                label=label,
                candidate_id=candidate.candidate_id,
                relative_path=record.relative_path,
                source_hash=record.sha256,
                errors=errors,
            )
        if kind == "objective_report" and isinstance(payload, Mapping):
            attainment = payload.get("target_attainment")
            if isinstance(attainment, Mapping):
                for canonical in sorted(attainment):
                    entry = attainment[canonical]
                    if not isinstance(entry, Mapping):
                        continue
                    for sub_key, sub_label in (
                        ("observed", "Observed"),
                        ("target", "Target"),
                        ("soft_score", "Soft score"),
                    ):
                        _push_scalar(
                            values,
                            artifact_id=record.artifact_id,
                            field=(
                                f"objective_report.target_attainment."
                                f"{canonical}.{sub_key}"
                            ),
                            value=entry.get(sub_key),
                            label=f"{sub_label} {canonical}",
                            candidate_id=candidate.candidate_id,
                            relative_path=record.relative_path,
                            source_hash=record.sha256,
                            errors=errors,
                        )

    if candidate.robustness_artifact_id:
        robustness_record = by_id.get(candidate.robustness_artifact_id)
        if robustness_record is None:
            errors.append(
                f"candidate {candidate.candidate_id!r} robustness artifact "
                "is missing from the manifest"
            )
        else:
            path = _resolve_artifact_path(root, robustness_record.relative_path)
            try:
                robustness_payload = json.loads(
                    path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError) as exc:
                errors.append(f"cannot read robustness artifact: {exc}")
                robustness_payload = None
            if isinstance(robustness_payload, Mapping):
                for field, label in (
                    ("nominal_soft_score", "Nominal soft score"),
                    ("mean_soft_score", "Mean soft score"),
                    ("worst_soft_score", "Worst soft score"),
                    ("p10_soft_score", "P10 soft score"),
                    ("robustness_score", "Robustness score"),
                ):
                    _push_scalar(
                        values,
                        artifact_id=robustness_record.artifact_id,
                        field=f"robustness_report.{field}",
                        value=robustness_payload.get(field),
                        label=label,
                        candidate_id=candidate.candidate_id,
                        relative_path=robustness_record.relative_path,
                        source_hash=robustness_record.sha256,
                        errors=errors,
                    )
    return values


def _lookup_path(payload: Any, parts: Sequence[str]) -> Any:
    current = payload
    for part in parts:
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _push_scalar(
    values: List[TrustedValueRecord],
    *,
    artifact_id: str,
    field: str,
    value: Any,
    label: str,
    candidate_id: str,
    relative_path: str,
    source_hash: str,
    errors: Optional[List[str]] = None,
) -> None:
    if value is None:
        return
    number = _finite_number(value)
    if number is None:
        if errors is not None:
            errors.append(
                f"non-finite or malformed scalar {field!r} in artifact "
                f"{artifact_id!r}"
            )
        return
    values.append(
        TrustedValueRecord(
            artifact_id=artifact_id,
            field=field,
            rendered_value=json.dumps(number, allow_nan=False),
            unit="",
            source_hash=source_hash,
            derivation=(
                f"{candidate_id} {field} from {relative_path} "
                f"(sha256 {source_hash[:12]})"
            ),
            label=label,
            prose_safe=True,
        )
    )


def _dedupe_trusted_values(
    values: Sequence[TrustedValueRecord],
) -> List[TrustedValueRecord]:
    seen: set = set()
    result: List[TrustedValueRecord] = []
    for value in sorted(values, key=lambda item: (item.artifact_id, item.field)):
        key = (
            value.artifact_id,
            value.field,
            value.rendered_value,
            value.source_hash,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _expected_scalar(field: str, payload: Any) -> Optional[float]:
    """Deterministically re-derive one trusted scalar from artifact content."""

    if not isinstance(payload, Mapping):
        return None
    if field.startswith("objective_report."):
        key = field[len("objective_report.") :]
        if key in {"aggregate_soft_score", "weighted_directional_loss"}:
            return _finite_number(payload.get(key))
        if key.startswith("target_attainment."):
            rest = key[len("target_attainment.") :]
            if "." in rest:
                canonical, sub_key = rest.rsplit(".", 1)
                attainment = payload.get("target_attainment")
                if isinstance(attainment, Mapping):
                    entry = attainment.get(canonical)
                    if isinstance(entry, Mapping):
                        return _finite_number(entry.get(sub_key))
        return None
    if field.startswith("physics_certificate."):
        key = field[len("physics_certificate.") :]
        return _finite_number(_lookup_path(payload, key.split(".")))
    if field.startswith("robustness_report."):
        key = field[len("robustness_report.") :]
        return _finite_number(payload.get(key))
    if "." in field:
        candidate_id, rest = field.split(".", 1)
        rows = payload.get("candidates")
        if not isinstance(rows, list):
            return None
        row = next(
            (
                item
                for item in rows
                if isinstance(item, Mapping)
                and str(item.get("candidate_id") or "") == candidate_id
            ),
            None,
        )
        if not isinstance(row, Mapping):
            return None
        if rest.startswith("objective_scores."):
            key = rest[len("objective_scores.") :]
            scores = row.get("objective_scores")
            if isinstance(scores, Mapping):
                return _finite_number(scores.get(key))
            return None
        if rest in {
            "target_score",
            "robustness_score",
            "simplicity_score",
            "distinctiveness_score",
        }:
            return _finite_number(row.get(rest))
    return None


def _enrich_observation(
    observation: ObservationCard,
    *,
    candidates: Sequence[VerifiedCandidateRecord],
    roles: Mapping[str, str],
    pareto_ids: Sequence[str],
    descriptors: Sequence[ArtifactDescriptor],
    status: str,
    warnings: Sequence[str],
) -> ObservationCard:
    metrics = dict(observation.metrics)
    metrics["verified_candidate_ids"] = sorted(
        candidate.candidate_id for candidate in candidates
    )
    metrics["selected_roles"] = dict(sorted(roles.items()))
    metrics["pareto_candidate_ids"] = sorted(pareto_ids)
    metrics["verified_artifact_ids"] = sorted(
        descriptor.artifact_id for descriptor in descriptors
    )
    metrics["robustness_covered_candidate_ids"] = sorted(
        candidate.candidate_id
        for candidate in candidates
        if candidate.robustness_artifact_id
    )
    metrics["asset_compilation_status"] = status
    metrics["asset_compilation_warnings"] = sorted(warnings)
    artifact_ids = list(observation.artifact_ids)
    verified_ids = sorted(
        descriptor.artifact_id for descriptor in descriptors
    )
    for artifact_id in verified_ids:
        if artifact_id not in artifact_ids:
            artifact_ids.append(artifact_id)
    return observation.model_copy(
        update={"metrics": metrics, "artifact_ids": artifact_ids}
    )


def _placeholder_observation(experiment_id: str = "") -> ObservationCard:
    return ObservationCard(
        observation_id="",
        experiment_id=experiment_id,
        status=ExperimentStatus.failed,
        metrics={"run_status": "failed"},
        artifact_ids=[],
        failure_records=[
            {"error": "asset compilation inputs were invalid"}
        ],
        failure_diagnosis={"run_status": "failed"},
        summary="No trusted assets were compiled.",
    )


def _request_source_experiment_id(
    request: CompiledExperimentRequest | Mapping[str, Any],
) -> str:
    """Physical/source TMM experiment id from the compiled request."""

    if isinstance(request, CompiledExperimentRequest):
        return str(
            request.parameters.get("experiment_id")
            or request.experiment.experiment_id
        )
    parameters = request.get("parameters") if isinstance(request, Mapping) else None
    experiment = request.get("experiment") if isinstance(request, Mapping) else None
    card_id = ""
    if isinstance(experiment, Mapping):
        card_id = str(experiment.get("experiment_id") or "")
    if isinstance(parameters, Mapping):
        return str(parameters.get("experiment_id") or card_id)
    return card_id


def _invalid_result(
    errors: Sequence[str],
    warnings: Sequence[str],
    *,
    request_id: str = "",
    task_hash: str = "",
    task_digest: str = "",
    run_id: str = "",
    experiment_id: str = "",
    observation_id: str = "",
    observation: Optional[ObservationCard] = None,
) -> ArticleAssetCompilationResult:
    model = ArticleAssetCompilationResult(
        status="invalid",
        result_id="",
        request_id=request_id,
        task_hash=task_hash,
        task_digest=task_digest,
        run_id=run_id,
        experiment_id=experiment_id,
        observation_id=observation_id,
        validation_errors=sorted(set(errors)),
        warnings=sorted(set(warnings)),
        observation=observation or _placeholder_observation(experiment_id),
    )
    return model.model_copy(
        update={"result_id": compute_asset_compilation_result_id(model)}
    )


def compile_article_assets(
    request: CompiledExperimentRequest | Mapping[str, Any],
    execution_result: ArticleExecutionResult | Mapping[str, Any],
    run_root: str | Path,
    *,
    authority: Optional[ArticleCompilationAuthority] = None,
    observation: Optional[ObservationCard | Mapping[str, Any]] = None,
) -> ArticleAssetCompilationResult:
    """Compile one completed TMM run into verified Article assets.

    Returns an ``ArticleAssetCompilationResult`` with status ``ready``,
    ``partial``, ``unavailable``, or ``invalid``.  Identity/provenance
    failures never yield trusted assets; optional ROBUSTNESS absence yields
    warnings and a ``partial`` status.
    """

    errors: List[str] = []
    warnings: List[str] = []
    try:
        request_model = (
            request
            if isinstance(request, CompiledExperimentRequest)
            else CompiledExperimentRequest.model_validate(request)
        )
    except ValidationError as exc:
        errors.append(f"CompiledExperimentRequest is invalid: {exc}")
        request_model = None
    try:
        execution_model = (
            execution_result
            if isinstance(execution_result, ArticleExecutionResult)
            else ArticleExecutionResult.model_validate(execution_result)
        )
    except ValidationError as exc:
        errors.append(f"ArticleExecutionResult is invalid: {exc}")
        execution_model = None
    if request_model is None or execution_model is None:
        return _invalid_result(
            errors,
            warnings,
            experiment_id=_request_source_experiment_id(request),
        )

    _verify_request_identity(request_model, execution_model, authority, errors)
    if errors:
        return _invalid_result(
            errors,
            warnings,
            request_id=request_model.request_id,
            task_hash=request_model.task_hash,
            task_digest=request_model.task_digest,
            run_id=request_model.run_id,
            experiment_id=_request_source_experiment_id(request_model),
            observation_id=execution_model.observation.observation_id,
            observation=execution_model.observation,
        )

    context = _load_run_context(
        run_root, request_model, errors, warnings
    )
    if context is None:
        return _invalid_result(
            errors,
            warnings,
            request_id=request_model.request_id,
            task_hash=request_model.task_hash,
            task_digest=request_model.task_digest,
            run_id=request_model.run_id,
            experiment_id=_request_source_experiment_id(request_model),
            observation_id=execution_model.observation.observation_id,
            observation=execution_model.observation,
        )

    if Path(execution_model.run_dir).resolve(strict=False) != context["root"]:
        errors.append(
            "ArticleExecutionResult.run_dir does not resolve to the run root"
        )
    observation_model = execution_model.observation
    if observation is not None:
        try:
            provided = (
                observation
                if isinstance(observation, ObservationCard)
                else ObservationCard.model_validate(observation)
            )
        except ValidationError as exc:
            errors.append(f"provided observation is invalid: {exc}")
            provided = None
        if provided is not None and provided.model_dump(
            mode="json"
        ) != observation_model.model_dump(mode="json"):
            errors.append(
                "provided observation is not canonical-content equivalent "
                "to ArticleExecutionResult.observation; observation status/"
                "metrics/failures cannot be caller-controlled"
            )
    article_experiment_id = observation_model.experiment_id
    if article_experiment_id != request_model.experiment.experiment_id:
        errors.append(
            "observation.experiment_id does not match the compiled request "
            "experiment (Article experiment identity)"
        )
    # The physical/source TMM experiment id (request task experiment id)
    # locates FINAL_RESULT rows, artifact directories, and candidate records.
    source_experiment_id = _request_source_experiment_id(request_model)
    experiment_id = source_experiment_id
    expected_status = normalize_observation_status(context["final_payload"])
    if observation_model.status != expected_status:
        errors.append(
            "observation status does not match the FINAL_RESULT-derived "
            f"status {expected_status.value!r}"
        )
    expected_action = required_action_for_task(
        context["task_payload"]
        if isinstance(context["task_payload"], OpticalDesignTask)
        else OpticalDesignTask.model_validate(context["task_payload"])
    )
    if request_model.allowed_action != expected_action:
        errors.append(
            "request.allowed_action does not match the whole-task required "
            f"action {expected_action.value}"
        )
    experiment_rows = context["final_payload"].get("experiment_results")
    row = next(
        (
            item
            for item in experiment_rows or []
            if isinstance(item, Mapping)
            and str(item.get("experiment_id") or "") == experiment_id
        ),
        None,
    )
    if row is None:
        errors.append(
            f"FINAL_RESULT.json has no experiment_results row for "
            f"{experiment_id!r}"
        )
    if observation_model.status in _FAILED_STATUSES:
        errors.append(
            "observation status indicates a failed/rejected run; it cannot "
            "be promoted to trusted assets"
        )
    elif observation_model.status in _LIMITED_STATUSES:
        warnings.append(
            "observation status is needs_higher_fidelity; assets are "
            "compiled with an explicit fidelity caveat"
        )
    if observation_model.status not in _SUCCESS_STATUSES.union(_LIMITED_STATUSES):
        errors.append(
            "observation status is not a completed result; no trusted "
            "assets can be compiled"
        )
    if errors:
        return _invalid_result(
            errors,
            warnings,
            request_id=request_model.request_id,
            task_hash=request_model.task_hash,
            task_digest=request_model.task_digest,
            run_id=request_model.run_id,
            experiment_id=experiment_id,
            observation_id=observation_model.observation_id,
            observation=observation_model,
        )

    portfolio_relative = _experiment_directory(context, experiment_id)
    portfolio_id = f"{portfolio_relative}/DESIGN_PORTFOLIO.json"
    portfolio_record = context["by_path"].get(portfolio_id)
    if portfolio_record is None:
        portfolio_record = next(
            (
                record
                for record in context["by_id"].values()
                if record.artifact_type == "design_portfolio"
                and Path(record.relative_path).parent.as_posix()
                == portfolio_relative
            ),
            None,
        )
    if portfolio_record is None:
        errors.append(
            f"manifest has no design_portfolio record for {experiment_id!r}"
        )
        return _invalid_result(
            errors,
            warnings,
            request_id=request_model.request_id,
            task_hash=request_model.task_hash,
            task_digest=request_model.task_digest,
            run_id=request_model.run_id,
            experiment_id=experiment_id,
            observation_id=observation_model.observation_id,
            observation=observation_model,
        )
    portfolio_path = _resolve_artifact_path(
        context["root"], portfolio_record.relative_path
    )
    try:
        portfolio_payload = json.loads(
            portfolio_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        errors.append(f"DESIGN_PORTFOLIO.json is invalid: {exc}")
        portfolio_payload = None
    if not isinstance(portfolio_payload, Mapping):
        errors.append("DESIGN_PORTFOLIO.json must be a JSON object")
        return _invalid_result(
            errors,
            warnings,
            request_id=request_model.request_id,
            task_hash=request_model.task_hash,
            task_digest=request_model.task_digest,
            run_id=request_model.run_id,
            experiment_id=experiment_id,
            observation_id=observation_model.observation_id,
            observation=observation_model,
        )
    pareto_ids = [
        str(item)
        for item in (portfolio_payload.get("pareto_candidate_ids") or [])
        if isinstance(item, str) and str(item).strip()
    ]
    selected_rows = _select_candidates(
        portfolio_payload,
        experiment_id,
        pareto_ids,
        errors,
        warnings,
    )
    if errors:
        return _invalid_result(
            errors,
            warnings,
            request_id=request_model.request_id,
            task_hash=request_model.task_hash,
            task_digest=request_model.task_digest,
            run_id=request_model.run_id,
            experiment_id=experiment_id,
            observation_id=observation_model.observation_id,
            observation=observation_model,
        )
    candidates: List[VerifiedCandidateRecord] = []
    for candidate_row in selected_rows:
        verified = _verify_candidate(
            candidate_row,
            experiment_id,
            context,
            errors,
            warnings,
        )
        if verified is not None:
            candidates.append(verified)
    if errors:
        return _invalid_result(
            errors,
            warnings,
            request_id=request_model.request_id,
            task_hash=request_model.task_hash,
            task_digest=request_model.task_digest,
            run_id=request_model.run_id,
            experiment_id=experiment_id,
            observation_id=observation_model.observation_id,
            observation=observation_model,
        )
    candidates = sorted(candidates, key=lambda item: item.candidate_id)

    descriptors = _build_descriptors(
        context,
        experiment_id,
        observation_model.observation_id,
        errors,
    )
    if errors:
        return _invalid_result(
            errors,
            warnings,
            request_id=request_model.request_id,
            task_hash=request_model.task_hash,
            task_digest=request_model.task_digest,
            run_id=request_model.run_id,
            experiment_id=experiment_id,
            observation_id=observation_model.observation_id,
            observation=observation_model,
        )

    trusted_values: List[TrustedValueRecord] = []
    for candidate in candidates:
        trusted_values.extend(
            _emit_trusted_values(candidate, context, errors)
        )
    trusted_values = _dedupe_trusted_values(trusted_values)
    if errors:
        return _invalid_result(
            errors,
            warnings,
            request_id=request_model.request_id,
            task_hash=request_model.task_hash,
            task_digest=request_model.task_digest,
            run_id=request_model.run_id,
            experiment_id=experiment_id,
            observation_id=observation_model.observation_id,
            observation=observation_model,
        )

    if errors:
        status: Literal[
            "ready", "partial", "unavailable", "invalid"
        ] = "invalid"
    elif not descriptors or not candidates or not trusted_values:
        status = "unavailable"
    elif warnings:
        status = "partial"
    else:
        status = "ready"

    roles: Dict[str, str] = {}
    for candidate in candidates:
        for role_key in candidate.role_keys:
            roles[role_key] = candidate.candidate_id
    enriched = _enrich_observation(
        observation_model,
        candidates=candidates,
        roles=roles,
        pareto_ids=pareto_ids,
        descriptors=descriptors,
        status=status,
        warnings=warnings,
    )
    envelope = ArticleAssetCompilationResult(
        status=status,
        result_id="",
        request_id=request_model.request_id,
        task_hash=request_model.task_hash,
        task_digest=request_model.task_digest,
        run_id=request_model.run_id,
        experiment_id=experiment_id,
        observation_id=enriched.observation_id,
        manifest_head_hash=context["manifest_head_hash"],
        manifest_sha256=context["manifest_sha256"],
        validation_errors=sorted(set(errors)),
        warnings=sorted(set(warnings)),
        descriptors=descriptors,
        trusted_values=trusted_values,
        candidates=candidates,
        observation=enriched,
    )
    return envelope.model_copy(
        update={"result_id": compute_asset_compilation_result_id(envelope)}
    )


def validate_asset_compilation_result(
    result: ArticleAssetCompilationResult | Mapping[str, Any],
    errors: List[str],
    warnings: List[str],
    *,
    run_root: Optional[str | Path] = None,
    request: Optional[
        CompiledExperimentRequest | Mapping[str, Any]
    ] = None,
    execution_result: Optional[
        ArticleExecutionResult | Mapping[str, Any]
    ] = None,
    authority: Optional[ArticleCompilationAuthority] = None,
) -> Optional[ArticleAssetCompilationResult]:
    """Deterministic public validation of an asset compilation result.

    Recomputes the content ID and re-checks status derivation, identity
    fields, descriptor hashes against the run root (when supplied), trusted
    value source hashes, candidate/artifact relationships, and the enriched
    observation status.  Invalid results are reported through ``errors`` and
    return ``None``; they never raise on tampering.
    """

    try:
        model = (
            result
            if isinstance(result, ArticleAssetCompilationResult)
            else ArticleAssetCompilationResult.model_validate(result)
        )
    except ValidationError as exc:
        errors.append(f"asset compilation result is invalid: {exc}")
        return None
    expected_id = compute_asset_compilation_result_id(model)
    if model.result_id != expected_id:
        errors.append(
            "asset compilation result_id does not match recomputed content"
        )
    for field_name in (
        "request_id",
        "task_hash",
        "task_digest",
        "run_id",
        "experiment_id",
        "observation_id",
    ):
        if not str(getattr(model, field_name) or "").strip():
            errors.append(f"asset compilation result has empty {field_name}")

    if model.validation_errors:
        if model.status != "invalid":
            errors.append(
                "asset compilation result has validation errors but status "
                f"is {model.status!r}"
            )
    else:
        if not model.descriptors or not model.candidates or not model.trusted_values:
            if model.status != "unavailable":
                errors.append(
                    "asset compilation result has no usable assets but "
                    f"status is {model.status!r}"
                )
        elif model.warnings:
            if model.status != "partial":
                errors.append(
                    "asset compilation result has warnings but status is "
                    f"{model.status!r}"
                )
        else:
            if model.status != "ready":
                errors.append(
                    "asset compilation result has no errors/warnings and "
                    f"usable assets but status is {model.status!r}"
                )

    if model.status != "invalid" and (
        model.observation.status in _FAILED_STATUSES
    ):
        errors.append(
            "enriched observation indicates a failed/rejected run but the "
            "result claims usable status"
        )

    descriptor_ids = {descriptor.artifact_id for descriptor in model.descriptors}
    seen_descriptor_ids: set = set()
    for descriptor in model.descriptors:
        if descriptor.artifact_id in seen_descriptor_ids:
            errors.append(
                f"duplicate descriptor artifact_id {descriptor.artifact_id!r}"
            )
        seen_descriptor_ids.add(descriptor.artifact_id)

    descriptor_by_id = {item.artifact_id: item for item in model.descriptors}
    value_keys: set = set()
    for value in model.trusted_values:
        key = (value.artifact_id, value.field)
        if key in value_keys:
            errors.append(
                f"duplicate trusted value ({value.artifact_id}, "
                f"{value.field})"
            )
        value_keys.add(key)
        descriptor = descriptor_by_id.get(value.artifact_id)
        if descriptor is None:
            errors.append(
                f"trusted value references unknown artifact "
                f"{value.artifact_id!r}"
            )
        elif value.source_hash != descriptor.sha256:
            errors.append(
                f"trusted value {value.artifact_id}/{value.field} source_hash "
                "does not match its descriptor sha256"
            )

    candidate_ids: set = set()
    for candidate in model.candidates:
        if candidate.candidate_id in candidate_ids:
            errors.append(
                f"duplicate verified candidate {candidate.candidate_id!r}"
            )
        candidate_ids.add(candidate.candidate_id)
        if candidate.experiment_id != model.experiment_id:
            errors.append(
                f"candidate {candidate.candidate_id!r} experiment_id does "
                "not match the compilation experiment"
            )
        for artifact_id in candidate.artifact_ids:
            if artifact_id not in descriptor_ids:
                errors.append(
                    f"candidate {candidate.candidate_id!r} references "
                    f"undescribed artifact {artifact_id!r}"
                )

    if run_root is not None:
        root = Path(run_root)
        try:
            resolved_root = root.resolve(strict=False)
        except OSError as exc:
            errors.append(f"cannot resolve run root: {exc}")
            resolved_root = None
        if resolved_root is not None:
            store = None
            try:
                reopened = ArtifactLineageStore.from_disk(resolved_root)
                if not reopened.verify_all():
                    errors.append("artifact manifest verification failed")
                else:
                    store = reopened
            except Exception as exc:  # noqa: BLE001 - validation error
                errors.append(f"artifact manifest verification failed: {exc}")
            if store is not None:
                records = store.records
                manifest_path = resolved_root / _MANIFEST_FILENAME
                try:
                    manifest_bytes = manifest_path.read_bytes()
                except OSError as exc:
                    errors.append(f"cannot read artifact manifest: {exc}")
                    manifest_bytes = None
                if manifest_bytes is not None:
                    if _sha256_bytes(manifest_bytes) != model.manifest_sha256:
                        errors.append(
                            "manifest_sha256 does not match the on-disk "
                            "ARTIFACT_MANIFEST.json"
                        )
                    try:
                        manifest_payload = json.loads(
                            manifest_bytes.decode("utf-8")
                        )
                    except (ValueError, UnicodeDecodeError) as exc:
                        errors.append(
                            f"artifact manifest is not valid JSON: {exc}"
                        )
                    else:
                        if (
                            isinstance(manifest_payload, Mapping)
                            and str(manifest_payload.get("head_hash") or "")
                            != model.manifest_head_hash
                        ):
                            errors.append(
                                "manifest_head_hash does not match the "
                                "on-disk ARTIFACT_MANIFEST.json"
                            )
                records_by_id = {
                    record.artifact_id: record for record in records
                }
                for descriptor in model.descriptors:
                    record = records_by_id.get(descriptor.artifact_id)
                    if (
                        record is None
                        or record.relative_path != descriptor.path
                        or record.sha256 != descriptor.sha256
                        or record.artifact_type != descriptor.artifact_type
                    ):
                        errors.append(
                            f"descriptor {descriptor.artifact_id!r} has no "
                            "exact matching artifact manifest record"
                        )
                validation_context = _validation_context(
                    records, resolved_root, errors
                )
                for candidate in model.candidates:
                    _verify_candidate_relationships(
                        candidate,
                        model.experiment_id,
                        validation_context,
                        errors,
                        warnings,
                    )
                if validation_context["final_payload"]:
                    expected_status = normalize_observation_status(
                        validation_context["final_payload"]
                    )
                    if model.observation.status != expected_status:
                        errors.append(
                            "enriched observation status does not match the "
                            "FINAL_RESULT-derived status"
                        )
            for descriptor in model.descriptors:
                try:
                    path = _resolve_artifact_path(
                        resolved_root, descriptor.path
                    )
                except ArticleAssetCompilationError as exc:
                    errors.append(str(exc))
                    continue
                if not path.is_file():
                    errors.append(
                        f"descriptor artifact is missing on disk: {path}"
                    )
                    continue
                actual = _sha256_bytes(path.read_bytes())
                if actual != descriptor.sha256:
                    errors.append(
                        f"descriptor {descriptor.artifact_id!r} sha256 does "
                        "not match file bytes"
                    )
            for value in model.trusted_values:
                descriptor = descriptor_by_id.get(value.artifact_id)
                if descriptor is None:
                    continue
                try:
                    path = _resolve_artifact_path(
                        resolved_root, descriptor.path
                    )
                except ArticleAssetCompilationError as exc:
                    errors.append(str(exc))
                    continue
                if not path.is_file():
                    continue
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    errors.append(
                        f"cannot re-read source artifact for trusted value "
                        f"{value.artifact_id}:{value.field}: {exc}"
                    )
                    continue
                expected = _expected_scalar(value.field, payload)
                if expected is None:
                    errors.append(
                        f"trusted value {value.artifact_id}:{value.field} "
                        "cannot be re-derived from its source artifact"
                    )
                elif value.rendered_value != json.dumps(
                    expected, allow_nan=False
                ):
                    errors.append(
                        f"trusted value {value.artifact_id}:{value.field} "
                        "does not match its source artifact content"
                    )

    if request is not None and execution_result is not None:
        try:
            request_model = (
                request
                if isinstance(request, CompiledExperimentRequest)
                else CompiledExperimentRequest.model_validate(request)
            )
            execution_model = (
                execution_result
                if isinstance(execution_result, ArticleExecutionResult)
                else ArticleExecutionResult.model_validate(execution_result)
            )
        except ValidationError as exc:
            errors.append(f"upstream validation inputs are invalid: {exc}")
            request_model = None
            execution_model = None
        if request_model is not None and execution_model is not None:
            identity_errors: List[str] = []
            _verify_request_identity(
                request_model, execution_model, authority, identity_errors
            )
            if identity_errors:
                errors.extend(identity_errors)
            else:
                if (
                    execution_model.observation.experiment_id
                    != request_model.experiment.experiment_id
                ):
                    errors.append(
                        "execution observation experiment_id does not match "
                        "the compiled request experiment (Article experiment "
                        "identity)"
                    )
                source_experiment_id = str(
                    request_model.parameters.get("experiment_id")
                    or request_model.experiment.experiment_id
                )
                for field_name, value in (
                    ("request_id", request_model.request_id),
                    ("task_hash", request_model.task_hash),
                    ("task_digest", request_model.task_digest),
                    ("run_id", request_model.run_id),
                    ("experiment_id", source_experiment_id),
                    (
                        "observation_id",
                        execution_model.observation.observation_id,
                    ),
                ):
                    if str(getattr(model, field_name) or "") != str(value):
                        errors.append(
                            f"asset compilation result {field_name} does not "
                            "match the supplied upstream request/execution"
                        )

    return model if not errors else None


__all__ = [
    "ArticleAssetCompilationError",
    "ArticleAssetCompilationResult",
    "AssetIdentityError",
    "AssetIntegrityError",
    "VerifiedCandidateRecord",
    "compile_article_assets",
    "compute_asset_compilation_result_id",
    "validate_asset_compilation_result",
]
