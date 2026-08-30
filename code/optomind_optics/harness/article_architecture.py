"""Stage 9: Figure-first and whole-article architecture planning.

The architecture layer turns the Stage 8 Claim Ledger plus caller-supplied
trusted artifact descriptors into multiple auditable story candidates with
ordered section contracts, figure contracts, claim/fact assignments, and
structured gaps/omissions.

Trust boundary: artifact descriptors carry semantic metadata (type, summary,
field descriptions, caller-asserted hashes, source experiment/observation
IDs).  The descriptor is caller-asserted; this module does not verify the
underlying file and never receives chart values.

Local-form/model-fill: local code owns schemas, IDs, allowlists, fixed
fields, statuses, validation, usage, and persistence.  Qwen
(``qwen3.7-flash``, via a concrete adapter) is organization-only and receives
one bounded payload; it returns a top-level JSON object ``{"stories": [...]}``.
Provider results carry a truthful ``provider_model`` and usage telemetry, so a
fake provider is never labeled ``qwen3.7-flash``.

Hard-integrity failures (fail closed): plan/ledger identity mismatch, ledger
validation errors, unknown/cross-wired claim/fact/artifact IDs, quantitative
figures without trusted artifact bindings or with undeclared fields,
unrelated trusted artifacts attached to a figure, negative claims used as
positive support, invented measurement-like numeric tokens in model-authored
text, and persistence conflicts.  Soft failures (fail open): malformed or
partial semantic candidates, provider unavailability, duplicate figure roles,
unassigned claims, omitted+assigned conflicts, non-distinct stories, and
plain structural integers not present in verified text are warnings/handoffs
and never delete Stage 8 facts.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from dataclasses import dataclass
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

from optomind_optics.harness.article_claims import ClaimLedgerResult
from optomind_optics.harness.article_contracts import (
    ARTICLE_EVENT_SCHEMA_VERSION,
    ArticleNodePayload,
    ArticleStage,
    ClaimCard,
    ClaimStatus,
    validate_article_event,
)
from optomind_optics.harness.article_director import ArticleDirectorPlan
from optomind_optics.harness.article_memory import (
    ArticleMemoryStore,
    DuplicateRecordError,
    RunMemoryRecord,
)
from optomind_optics.harness.experiment_graph import ExperimentGraph
from optomind_optics.harness.qwen_policy import QwenFlashOnlyClient
from optomind_research.runtime.artifact_store import atomic_write_json


ARCHITECTURE_SCHEMA_VERSION = "article-architecture-result.v1"
QUANTITATIVE_KINDS = frozenset({"quantitative", "table"})
POSITIVE_CLAIM_STATUSES = frozenset(
    {ClaimStatus.partially_supported, ClaimStatus.supported}
)
MODEL_NAME = "qwen3.7-flash"
DEFAULT_MAX_TOKENS = 24000
TARGET_STORY_COVERAGE_FRACTION = 0.95
MAX_FORMAT_REPAIR_CALLS = 2
MAX_STORY_COMPLETION_REPAIRS = 2
DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "optical_harness"
    / "Article Architecture Planner.txt"
)
_PLAIN_INTEGER_RE = re.compile(r"(?<![A-Za-z0-9])\d+(?![\d.])")
_MEASUREMENT_NUMBER_RE = re.compile(
    r"(?:"
    r"\d+\.\d+(?:[eE][+-]?\d+)?"
    r"|\d+[eE][+-]?\d+"
    r"|\d+(?:\.\d+)?\s*%"
    r"|\d+(?:\.\d+)?\s*percent\b"
    r"|(?:[<>]=?|[=])\s*\d+(?:\.\d+)?"
    r"|\b(?:exceeds?|below|under|above|greater than|less than|at least|"
    r"at most|up to|no more than|no less than)\s+\d+(?:\.\d+)?"
    r"|\d+(?:\.\d+)?\s*(?:nm|um|mm|cm|km|kg|g|mg|s|ms|us|ns|"
    r"Hz|kHz|MHz|GHz|THz|W|mW|uW|kW|V|mV|uV|kV|A|mA|uA|kA|"
    r"K|deg|degC|J|kJ|mol|dB|eV|keV|MeV)\b"
    r")"
)
_SOURCE_UNIT_LITERAL_RE = re.compile(
    r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\s*"
    r"(?:%|percent\b|nm|um|mm|cm|km|kg|g|mg|s|ms|us|ns|Hz|kHz|MHz|GHz|THz|"
    r"W|mW|uW|kW|V|mV|uV|kV|A|mA|uA|kA|K|deg|degC|J|kJ|mol|dB|eV|keV|MeV)\b"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ArticleArchitectureError(ValueError):
    """Base error for architecture failures."""


class ArticleArchitectureIntegrityError(ArticleArchitectureError):
    """Unknown/cross-wired provenance or conflicting persistence content."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _ProviderDraftModel(BaseModel):
    """Tolerant boundary for model-filled forms before strict assembly."""

    model_config = ConfigDict(extra="ignore", frozen=True)


class ArtifactDescriptor(_StrictModel):
    """Caller-asserted artifact metadata for planning; never chart values."""

    schema_version: Literal["artifact-descriptor.v1"] = "artifact-descriptor.v1"
    artifact_id: str
    path: str
    fields: List[str] = Field(min_length=1)
    artifact_type: str = "artifact"
    media_type: str = ""
    content_summary: str = ""
    field_descriptions: Dict[str, str] = Field(default_factory=dict)
    sha256: Optional[str] = None
    source_experiment_ids: List[str] = Field(default_factory=list)
    source_observation_ids: List[str] = Field(default_factory=list)

    @field_validator("artifact_id")
    @classmethod
    def _non_empty_id(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("artifact_id must be a non-empty string")
        return text

    @field_validator("fields")
    @classmethod
    def _fields_non_empty_unique(cls, value: List[str]) -> List[str]:
        cleaned = [str(item or "").strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("artifact fields must be non-empty strings")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("artifact fields must be unique")
        return cleaned

    @field_validator("sha256")
    @classmethod
    def _validate_sha256(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip().lower()
        if not _SHA256_RE.match(text):
            raise ValueError("sha256 must be a 64-character lowercase hex digest")
        return text


class ArtifactFieldBinding(_StrictModel):
    schema_version: Literal["artifact-field-binding.v1"] = "artifact-field-binding.v1"
    artifact_id: str
    selected_fields: List[str] = Field(min_length=1)


class MissingWorkHandoff(_StrictModel):
    schema_version: Literal["missing-work-handoff.v1"] = "missing-work-handoff.v1"
    goal_id: str
    goal_label: str
    kind: Literal["goal", "success_criterion"]
    coverage: Literal["covered", "partial", "gap", "unknown", "not_applicable"]
    claim_ids: List[str] = Field(default_factory=list)
    unique_contribution: str
    expected_value_of_more_work: str
    stop_reason: str
    rationale: str


class ArchitectureProviderResult(_StrictModel):
    """Envelope returned by any architecture provider (real or fake)."""

    schema_version: Literal["architecture-provider-result.v1"] = (
        "architecture-provider-result.v1"
    )
    stories: List[Dict[str, Any]]
    usage: Dict[str, Any] = Field(default_factory=dict)
    provider_model: str = "unknown"
    mock_llm: bool = False


ArchitectureProvider = Callable[
    [Sequence[Mapping[str, Any]]], Sequence[ArchitectureProviderResult]
]


class _ModelClaimBinding(_ProviderDraftModel):
    claim_id: str
    role: Literal["positive", "limitation", "counterevidence"]


class _ModelArtifactBinding(_ProviderDraftModel):
    artifact_id: str
    selected_fields: List[str] = Field(min_length=1)


def _coerce_single_text_list(value: Any) -> Any:
    """Accept a model's one-item string as the equivalent text-list shape."""

    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    return value


class _ModelSectionDraft(_ProviderDraftModel):
    heading: str
    purpose: str
    key_messages: List[str] = Field(default_factory=list)
    transitions: List[str] = Field(default_factory=list)
    claim_bindings: List[_ModelClaimBinding] = Field(default_factory=list)
    figure_roles: List[str] = Field(default_factory=list)

    @field_validator("key_messages", "transitions", "figure_roles", mode="before")
    @classmethod
    def _single_text_is_one_item_list(cls, value: Any) -> Any:
        return _coerce_single_text_list(value)

    @field_validator("heading", "purpose")
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        if not str(value or "").strip():
            raise ValueError("section heading/purpose must be non-empty")
        return value


class _ModelFigureDraft(_ProviderDraftModel):
    role_key: str
    kind: Literal["quantitative", "table", "conceptual", "workflow", "mechanism"]
    story_role: str
    panel_intents: List[str] = Field(default_factory=list)
    caption_intent: str = ""
    claim_bindings: List[_ModelClaimBinding] = Field(default_factory=list)
    fact_ids: List[str] = Field(default_factory=list)
    artifact_bindings: List[_ModelArtifactBinding] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)

    @field_validator("panel_intents", "fact_ids", "limitations", mode="before")
    @classmethod
    def _single_text_is_one_item_list(cls, value: Any) -> Any:
        return _coerce_single_text_list(value)


class _ModelOmittedClaim(_ProviderDraftModel):
    claim_id: str
    reason: str


class _ModelStoryDraft(_ProviderDraftModel):
    story_shape: str
    central_thesis: str
    sections: List[_ModelSectionDraft] = Field(min_length=1)
    figures: List[_ModelFigureDraft] = Field(min_length=1)
    omitted_claims: List[_ModelOmittedClaim] = Field(default_factory=list)
    exclusions: List[str] = Field(default_factory=list)
    strengths: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    recommendation_rationale: str
    recommendation_score: float = Field(ge=0.0, le=1.0)

    @field_validator("exclusions", "strengths", "risks", mode="before")
    @classmethod
    def _single_text_is_one_item_list(cls, value: Any) -> Any:
        return _coerce_single_text_list(value)

    @field_validator("story_shape", "central_thesis", "recommendation_rationale")
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        if not str(value or "").strip():
            raise ValueError(
                "story_shape/central_thesis/recommendation_rationale required"
            )
        return value


def _provider_story_extra_paths(raw_story: Mapping[str, Any]) -> List[str]:
    """Report, but do not reject, redundant columns in a model-filled form."""

    extras: List[str] = []

    def visit(value: Any, model_type: type[BaseModel], path: str) -> None:
        if not isinstance(value, Mapping):
            return
        allowed = set(model_type.model_fields)
        extras.extend(
            f"{path}.{key}" if path else str(key)
            for key in sorted(set(value) - allowed)
        )

    visit(raw_story, _ModelStoryDraft, "")
    for index, section in enumerate(raw_story.get("sections") or ()):
        section_path = f"sections[{index}]"
        visit(section, _ModelSectionDraft, section_path)
        if not isinstance(section, Mapping):
            continue
        for binding_index, binding in enumerate(section.get("claim_bindings") or ()):
            visit(
                binding,
                _ModelClaimBinding,
                f"{section_path}.claim_bindings[{binding_index}]",
            )
    for index, figure in enumerate(raw_story.get("figures") or ()):
        figure_path = f"figures[{index}]"
        visit(figure, _ModelFigureDraft, figure_path)
        if not isinstance(figure, Mapping):
            continue
        for binding_index, binding in enumerate(figure.get("claim_bindings") or ()):
            visit(
                binding,
                _ModelClaimBinding,
                f"{figure_path}.claim_bindings[{binding_index}]",
            )
        for binding_index, binding in enumerate(figure.get("artifact_bindings") or ()):
            visit(
                binding,
                _ModelArtifactBinding,
                f"{figure_path}.artifact_bindings[{binding_index}]",
            )
    for index, omitted in enumerate(raw_story.get("omitted_claims") or ()):
        visit(omitted, _ModelOmittedClaim, f"omitted_claims[{index}]")
    return extras


def _normalize_provider_story_bindings(
    raw_story: Mapping[str, Any],
) -> Tuple[Dict[str, Any], List[str]]:
    """Repair one unambiguous model form inversion at the provider boundary."""

    story = dict(raw_story)
    notes: List[str] = []
    allowed_roles = {"positive", "limitation", "counterevidence"}
    for container_key in ("sections", "figures"):
        normalized_containers: List[Any] = []
        for container_index, raw_container in enumerate(story.get(container_key) or ()):
            if not isinstance(raw_container, Mapping):
                normalized_containers.append(raw_container)
                continue
            container = dict(raw_container)
            normalized_bindings: List[Any] = []
            for binding_index, raw_binding in enumerate(
                container.get("claim_bindings") or ()
            ):
                if not isinstance(raw_binding, Mapping):
                    normalized_bindings.append(raw_binding)
                    continue
                binding = dict(raw_binding)
                if (
                    "claim_id" not in binding
                    and "role" not in binding
                    and len(binding) == 1
                ):
                    claim_id, role = next(iter(binding.items()))
                    if str(role) in allowed_roles:
                        binding = {
                            "claim_id": str(claim_id),
                            "role": str(role),
                        }
                        notes.append(
                            f"normalized {container_key}[{container_index}]."
                            f"claim_bindings[{binding_index}] from a "
                            "single-entry claim_id-to-role mapping"
                        )
                normalized_bindings.append(binding)
            container["claim_bindings"] = normalized_bindings
            normalized_containers.append(container)
        story[container_key] = normalized_containers
    return story, notes


class ClaimPlacement(_StrictModel):
    schema_version: Literal["claim-placement.v1"] = "claim-placement.v1"
    claim_id: str
    role: Literal["positive", "limitation", "counterevidence"]


class SectionContract(_StrictModel):
    schema_version: Literal["section-contract.v1"] = "section-contract.v1"
    section_id: str
    heading: str
    purpose: str
    claim_bindings: List[ClaimPlacement] = Field(default_factory=list)
    figure_ids: List[str] = Field(default_factory=list)
    transitions: List[str] = Field(default_factory=list)
    key_messages: List[str] = Field(default_factory=list)


class FigureContract(_StrictModel):
    schema_version: Literal["figure-contract.v1"] = "figure-contract.v1"
    figure_id: str
    role_key: str
    story_role: str
    section_target: str
    kind: Literal["quantitative", "table", "conceptual", "workflow", "mechanism"]
    panel_intents: List[str] = Field(default_factory=list)
    caption_intent: str = ""
    claim_bindings: List[ClaimPlacement] = Field(default_factory=list)
    claim_ids: List[str] = Field(default_factory=list)
    fact_ids: List[str] = Field(default_factory=list)
    artifact_bindings: List[ArtifactFieldBinding] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)
    source_mode: Literal["trusted_artifact", "synthesized_claims", "conceptual"]
    conceptual: bool


class ClaimAssignment(_StrictModel):
    schema_version: Literal["claim-assignment.v1"] = "claim-assignment.v1"
    claim_id: str
    fact_id: Optional[str] = None
    section_ids: List[str] = Field(default_factory=list)
    figure_ids: List[str] = Field(default_factory=list)
    role: Literal["positive", "limitation", "counterevidence"]
    reason: str


class OmittedClaim(_StrictModel):
    schema_version: Literal["omitted-claim.v1"] = "omitted-claim.v1"
    claim_id: str
    reason: str


class StoryCandidate(_StrictModel):
    schema_version: Literal["story-candidate.v1"] = "story-candidate.v1"
    story_id: str
    story_shape: str
    central_thesis: str
    section_contracts: List[SectionContract] = Field(min_length=1)
    figure_contracts: List[FigureContract] = Field(min_length=1)
    claim_assignments: List[ClaimAssignment] = Field(default_factory=list)
    omitted_claims: List[OmittedClaim] = Field(default_factory=list)
    exclusions: List[str] = Field(default_factory=list)
    strengths: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    recommendation_rationale: str
    recommendation_score: float = Field(ge=0.0, le=1.0)


class ArticleArchitectureResult(_StrictModel):
    schema_version: Literal["article-architecture-result.v1"] = (
        "article-architecture-result.v1"
    )
    architecture_id: str
    source_plan_id: Optional[str] = None
    source_ledger_id: Optional[str] = None
    stories: List[StoryCandidate]
    artifact_inventory: List[ArtifactDescriptor] = Field(default_factory=list)
    deterministic_inventory: Dict[str, Any]
    missing_work_handoffs: List[MissingWorkHandoff] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    validation_errors: List[str] = Field(default_factory=list)
    model_status: Literal["available", "partial", "unavailable"]
    usage: Dict[str, Any] = Field(default_factory=dict)
    semantic_model: str = "none"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(*parts: Any) -> str:
    return hashlib.sha256(
        _canonical_json([str(part) for part in parts]).encode("utf-8")
    ).hexdigest()[:16]


def compute_architecture_id(
    plan_id: str,
    ledger_id: str,
    artifact_manifest: Sequence[ArtifactDescriptor | Mapping[str, Any]],
    missing_work_handoffs: Sequence[MissingWorkHandoff | Mapping[str, Any]],
    stories: Sequence[StoryCandidate | Mapping[str, Any]],
) -> str:
    """Content-addressed architecture identity (public and deterministic).

    Hashes the plan/ledger identity, the full canonical artifact inventory,
    the structured missing-work handoffs, and the full canonical story
    content.  Stage 9 uses this directly; downstream stages recompute it to
    verify that a persisted architecture was not tampered with.
    """

    manifest = [
        (
            item
            if isinstance(item, ArtifactDescriptor)
            else ArtifactDescriptor.model_validate(item)
        )
        for item in artifact_manifest
    ]
    handoffs = [
        (
            item
            if isinstance(item, MissingWorkHandoff)
            else MissingWorkHandoff.model_validate(item)
        )
        for item in missing_work_handoffs
    ]
    story_models = [
        (
            item
            if isinstance(item, StoryCandidate)
            else StoryCandidate.model_validate(item)
        )
        for item in stories
    ]
    return _digest(
        str(plan_id),
        str(ledger_id),
        [_canonical_json(item.model_dump(mode="json")) for item in manifest],
        [_canonical_json(item.model_dump(mode="json")) for item in handoffs],
        [_canonical_json(item.model_dump(mode="json")) for item in story_models],
    )


def _safe_json(text: str) -> Dict[str, Any]:
    text = str(text or "").strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                value = json.loads(text[start : end + 1])
                return value if isinstance(value, dict) else {}
            except json.JSONDecodeError:
                return {}
    return {}


def _positive_claims(ledger: ClaimLedgerResult) -> List[ClaimCard]:
    return [
        claim
        for claim in ledger.claims
        if claim.status in POSITIVE_CLAIM_STATUSES and claim.source_artifact_ids
    ]


def _limitation_claims(ledger: ClaimLedgerResult) -> List[ClaimCard]:
    return [
        claim
        for claim in ledger.claims
        if claim.status
        in {ClaimStatus.refuted, ClaimStatus.withdrawn, ClaimStatus.draft}
    ]


def _validate_inputs(
    plan: ArticleDirectorPlan,
    ledger: ClaimLedgerResult,
    manifest: Sequence[ArtifactDescriptor],
    errors: List[str],
) -> Dict[str, Any]:
    """Validate plan/ledger/manifest identity and provenance."""

    if ledger.validation_errors:
        errors.append(
            "ledger is not valid planning input (it carries validation errors)"
        )
    if ledger.source_plan_id is not None and ledger.source_plan_id != plan.plan_id:
        errors.append(
            f"ledger source_plan_id {ledger.source_plan_id!r} does not match "
            f"plan {plan.plan_id!r}"
        )
    plan_hypotheses = {item.hypothesis_id: item for item in plan.hypotheses}
    claims_by_id = {claim.claim_id: claim for claim in ledger.claims}
    if len(claims_by_id) != len(ledger.claims):
        errors.append("ledger contains duplicate claim IDs")
    fact_ids = {fact.fact_id for fact in ledger.facts}
    if len(fact_ids) != len(ledger.facts):
        errors.append("ledger contains duplicate fact IDs")
    for claim in ledger.claims:
        hypothesis_id = claim.metadata.get("hypothesis_id")
        hypothesis = plan_hypotheses.get(hypothesis_id)
        if hypothesis is None:
            errors.append(
                f"claim {claim.claim_id!r} references unknown plan hypothesis "
                f"{hypothesis_id!r}"
            )
            continue
        if claim.statement != hypothesis.statement:
            errors.append(
                f"claim {claim.claim_id!r} statement does not match plan "
                f"hypothesis {hypothesis_id!r}"
            )
    fact_by_claim: Dict[str, Any] = {}
    for fact in ledger.facts:
        owners = [
            item
            for item in ledger.claims
            if item.metadata.get("fact_id") == fact.fact_id
            and item.metadata.get("claim_id") == item.claim_id
        ]
        if len(owners) != 1:
            if not owners:
                errors.append(f"fact {fact.fact_id!r} has no matching Stage 8 claim")
            else:
                errors.append(
                    f"fact {fact.fact_id!r} has ambiguous claim ownership "
                    "(multiple claims)"
                )
            continue
        claim = owners[0]
        if fact.source_artifact_ids != claim.source_artifact_ids:
            errors.append(
                f"fact {fact.fact_id!r} source artifacts do not match claim "
                f"{claim.claim_id!r}"
            )
        if fact.metadata.get("hypothesis_id") != claim.metadata.get("hypothesis_id"):
            errors.append(
                f"fact {fact.fact_id!r} hypothesis does not match claim "
                f"{claim.claim_id!r}"
            )
        if fact.metadata.get("scope") != claim.metadata.get("scope"):
            errors.append(
                f"fact {fact.fact_id!r} scope does not match claim {claim.claim_id!r}"
            )
        if claim.claim_id in fact_by_claim:
            errors.append(f"claim {claim.claim_id!r} owns multiple Stage 8 facts")
            continue
        fact_by_claim[claim.claim_id] = fact
    manifest_by_id = {item.artifact_id: item for item in manifest}
    if len(manifest_by_id) != len(manifest):
        errors.append("artifact manifest contains duplicate artifact IDs")
    for item in manifest:
        undeclared = sorted(set(item.field_descriptions) - set(item.fields))
        if undeclared:
            errors.append(
                f"artifact {item.artifact_id!r} field_descriptions reference "
                f"undeclared fields: {undeclared}"
            )
    for claim in ledger.claims:
        unknown = sorted(set(claim.source_artifact_ids) - set(manifest_by_id))
        if unknown:
            errors.append(
                f"claim {claim.claim_id!r} references unknown artifacts: {unknown}"
            )
    for fact in ledger.facts:
        unknown = sorted(set(fact.source_artifact_ids) - set(manifest_by_id))
        if unknown:
            errors.append(
                f"fact {fact.fact_id!r} references unknown artifacts: {unknown}"
            )
    missing_work_handoffs = [
        MissingWorkHandoff(
            goal_id=row.goal_id,
            goal_label=row.goal_label,
            kind=row.kind,
            coverage=row.coverage,
            claim_ids=list(row.claim_ids),
            unique_contribution=row.unique_contribution,
            expected_value_of_more_work=row.expected_value_of_more_work,
            stop_reason=row.stop_reason,
            rationale=row.rationale,
        )
        for row in ledger.audit.rows
        if row.coverage in {"partial", "gap", "unknown"}
    ]
    inventory = {
        "positive_claim_count": len(_positive_claims(ledger)),
        "fact_count": len(ledger.facts),
        "limitation_claim_count": len(_limitation_claims(ledger)),
        "artifact_count": len(manifest_by_id),
        "charter_goal_count": len(plan.charter.goals),
        "success_criterion_count": len(plan.charter.success_criteria),
    }
    return {
        "claims_by_id": claims_by_id,
        "fact_by_claim": fact_by_claim,
        "manifest_by_id": manifest_by_id,
        "missing_work_handoffs": missing_work_handoffs,
        "inventory": inventory,
    }


def build_architecture_payload(
    plan: ArticleDirectorPlan,
    ledger: ClaimLedgerResult,
    manifest: Sequence[ArtifactDescriptor],
    value_shapes: Optional[Mapping[str, Mapping[str, str]]] = None,
) -> List[Dict[str, Any]]:
    """Locally prepared bounded payload for the organization-only provider."""

    positive = _positive_claims(ledger)
    limitation = _limitation_claims(ledger)
    value_shapes = value_shapes or {}
    claims_by_id = {claim.claim_id: claim for claim in ledger.claims}
    fact_by_claim: Dict[str, Any] = {}
    for claim in ledger.claims:
        fact_id = claim.metadata.get("fact_id")
        if fact_id:
            fact_by_claim[claim.claim_id] = next(
                (fact for fact in ledger.facts if fact.fact_id == fact_id),
                None,
            )
    missing_work = [
        MissingWorkHandoff(
            goal_id=row.goal_id,
            goal_label=row.goal_label,
            kind=row.kind,
            coverage=row.coverage,
            claim_ids=list(row.claim_ids),
            unique_contribution=row.unique_contribution,
            expected_value_of_more_work=row.expected_value_of_more_work,
            stop_reason=row.stop_reason,
            rationale=row.rationale,
        )
        for row in ledger.audit.rows
        if row.coverage in {"partial", "gap", "unknown"}
    ]
    positive_claim_count = len(positive)
    minimum_assigned_claim_count = (
        min(
            positive_claim_count,
            max(
                1,
                math.ceil(positive_claim_count * TARGET_STORY_COVERAGE_FRACTION),
            ),
        )
        if positive_claim_count
        else 0
    )
    return [
        {
            "task": "Propose multiple whole-article story candidates with "
            "ordered section contracts and figure contracts. Organization-only.",
            "target_story_count": 3,
            "story_completion_contract": {
                "writable_positive_claim_count": positive_claim_count,
                "minimum_assigned_claim_count": minimum_assigned_claim_count,
                "maximum_omitted_claim_count": (
                    positive_claim_count - minimum_assigned_claim_count
                ),
                "target_coverage_fraction": TARGET_STORY_COVERAGE_FRACTION,
                "policy": (
                    "Each candidate is a complete Article architecture. Group "
                    "compatible claims inside shared sections rather than "
                    "narrowing the paper to one candidate or route. Omission is "
                    "reserved for a genuinely duplicate, scientifically unsafe, "
                    "or non-contributory claim; calling a verified result "
                    "secondary, from another route, or outside a preferred "
                    "narrative is not sufficient."
                ),
            },
            "question": plan.charter.question,
            "charter_scope": plan.charter.scope,
            "positive_claims": [
                {
                    "claim_id": claim.claim_id,
                    "statement": claim.statement,
                    "scope": claim.scope,
                    "strength": claim.strength.value,
                    "status": claim.status.value,
                    "fact_id": claim.metadata.get("fact_id"),
                    "synthesis_contract": dict(
                        claim.metadata.get("synthesis_contract") or {}
                    ),
                    "source_count": len(claim.source_artifact_ids),
                    "authorized_artifact_ids": _claim_authorized_artifacts(
                        claims_by_id,
                        fact_by_claim,
                        claim.claim_id,
                    ),
                    "authorized_value_fields": _claim_authorized_value_fields(claim),
                }
                for claim in positive
            ],
            "limitation_claims": [
                {
                    "claim_id": claim.claim_id,
                    "statement": claim.statement,
                    "status": claim.status.value,
                    "fact_id": claim.metadata.get("fact_id"),
                    "synthesis_contract": dict(
                        claim.metadata.get("synthesis_contract") or {}
                    ),
                    "authorized_artifact_ids": _claim_authorized_artifacts(
                        claims_by_id,
                        fact_by_claim,
                        claim.claim_id,
                    ),
                    "authorized_value_fields": _claim_authorized_value_fields(claim),
                }
                for claim in limitation
            ],
            "artifacts": [
                {
                    "artifact_id": item.artifact_id,
                    "artifact_type": item.artifact_type,
                    "media_type": item.media_type,
                    "content_summary": item.content_summary,
                    "field_descriptions": dict(item.field_descriptions),
                    "field_shapes": {
                        field: value_shapes.get(item.artifact_id, {}).get(
                            field, "auxiliary"
                        )
                        for field in item.fields
                    },
                    "allowed_fields": list(item.fields),
                    "source_experiment_ids": list(item.source_experiment_ids),
                    "source_observation_ids": list(item.source_observation_ids),
                }
                for item in manifest
            ],
            "missing_work_handoffs": [
                {
                    "goal_id": item.goal_id,
                    "goal_label": item.goal_label,
                    "kind": item.kind,
                    "coverage": item.coverage,
                    "claim_ids": list(item.claim_ids),
                    "unique_contribution": item.unique_contribution,
                    "expected_value_of_more_work": item.expected_value_of_more_work,
                    "stop_reason": item.stop_reason,
                    "rationale": item.rationale,
                }
                for item in missing_work
            ],
        }
    ]


def _bound_verified_text(
    ledger: ClaimLedgerResult,
    claims_by_id: Mapping[str, ClaimCard],
    fact_by_claim: Mapping[str, Any],
    claim_ids: Sequence[str],
    fact_ids: Sequence[str],
) -> str:
    parts: List[str] = []
    for claim_id in claim_ids:
        claim = claims_by_id.get(claim_id)
        if claim is not None:
            parts.extend([claim.statement, claim.scope])
    for fact_id in fact_ids:
        for claim_id, fact in fact_by_claim.items():
            if fact.fact_id == fact_id:
                parts.append(fact.statement)
    return " ".join(parts)


def _claim_authorized_artifacts(
    claims_by_id: Mapping[str, ClaimCard],
    fact_by_claim: Mapping[str, Any],
    claim_id: str,
) -> List[str]:
    fact = fact_by_claim.get(claim_id)
    if fact is not None:
        return sorted(set(fact.source_artifact_ids))
    claim = claims_by_id.get(claim_id)
    if claim is not None:
        return sorted(set(claim.source_artifact_ids))
    return []


def _claim_authorized_value_fields(claim: Any) -> List[Dict[str, Any]]:
    by_artifact: Dict[str, set[str]] = {}
    for ref in claim.metadata.get("value_lineage") or []:
        artifact_id = str(ref.get("artifact_id") or "")
        field = str(ref.get("field") or "")
        if artifact_id and field:
            by_artifact.setdefault(artifact_id, set()).add(field)
    return [
        {
            "artifact_id": artifact_id,
            "fields": sorted(fields),
        }
        for artifact_id, fields in sorted(by_artifact.items())
    ]


def _claim_organization_aliases(claim: ClaimCard) -> set[str]:
    contract = claim.metadata.get("synthesis_contract") or {}
    if not isinstance(contract, Mapping):
        return set()
    aliases = {
        str(alias).strip()
        for alias in contract.get("subject_aliases") or ()
        if str(alias).strip()
    }
    route_alias = str(contract.get("route_alias") or "").strip()
    if route_alias:
        aliases.add(route_alias)
    for subject in contract.get("subject_candidates") or ():
        if not isinstance(subject, Mapping):
            continue
        alias = str(subject.get("alias") or "").strip()
        if alias:
            aliases.add(alias)
    return {alias for alias in aliases if len(alias) >= 2}


def _text_names_alias(text: str, alias: str) -> bool:
    return bool(
        re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(alias)}(?![A-Za-z0-9_])",
            str(text or ""),
            flags=re.IGNORECASE,
        )
    )


def value_field_shapes(
    values: Sequence[Any],
) -> Dict[str, Dict[str, str]]:
    """Deterministic scalar/series classification from trusted value records.

    Derived from the artifact's trusted value metadata, never from field
    names: a prose-safe record (finite scalar literal) marks the field
    ``scalar``; a non-prose-safe record (array/series) marks it ``series``.
    Fields without a trusted value record are auxiliary plotting/structural
    fields and are intentionally absent from the returned map.
    """

    shapes: Dict[str, Dict[str, str]] = {}
    for value in values:
        artifact_id = str(getattr(value, "artifact_id", "") or "")
        field = str(getattr(value, "field", "") or "")
        if not artifact_id or not field:
            continue
        shapes.setdefault(artifact_id, {})[field] = (
            "scalar" if bool(getattr(value, "prose_safe", False)) else "series"
        )
    return shapes


def _architecture_source_literals(
    plan: ArticleDirectorPlan,
) -> frozenset[str]:
    texts = [
        plan.question,
        plan.charter.question,
        plan.charter.scope,
        *plan.charter.goals,
        *plan.charter.success_criteria,
        *plan.charter.constraints,
        plan.capability.supported_scope,
        *plan.capability.accepted_assumptions,
    ]
    literals = {
        str(token)
        for text in texts
        for token in _MEASUREMENT_NUMBER_RE.findall(str(text or ""))
    }
    literals.update(
        str(match.group(0))
        for text in texts
        for match in _SOURCE_UNIT_LITERAL_RE.finditer(str(text or ""))
    )
    return frozenset(literals)


def _verify_no_invented_numbers(
    story_context: str,
    model_texts: Sequence[str],
    errors: List[str],
    warnings: List[str],
    label: str,
    source_literals: frozenset[str] = frozenset(),
) -> None:
    for text in model_texts:
        text = str(text or "")
        for token in _MEASUREMENT_NUMBER_RE.findall(text):
            if token in source_literals:
                continue
            if token not in story_context:
                errors.append(
                    f"{label} contains invented numeric content {token!r} not "
                    "present in the bound verified claim/fact text "
                    "(measurement-like number)"
                )
        for token in _PLAIN_INTEGER_RE.findall(text):
            if token in source_literals:
                continue
            if token not in story_context:
                warnings.append(
                    f"{label} contains structural integer {token!r} not "
                    "present in the bound verified claim/fact text"
                )


def _assemble_story(
    plan: ArticleDirectorPlan,
    ledger: ClaimLedgerResult,
    context: Mapping[str, Any],
    draft: _ModelStoryDraft,
    story_index: int,
    errors: List[str],
    warnings: List[str],
) -> Optional[StoryCandidate]:
    claims_by_id = context["claims_by_id"]
    fact_by_claim = context["fact_by_claim"]
    manifest_by_id = context["manifest_by_id"]
    positive_ids = {claim.claim_id for claim in _positive_claims(ledger)}
    limitation_ids = {claim.claim_id for claim in _limitation_claims(ledger)}
    all_claim_ids = set(claims_by_id)
    alias_claim_ids: Dict[str, Tuple[str, set[str]]] = {}
    for claim in claims_by_id.values():
        for alias in _claim_organization_aliases(claim):
            normalized_alias = alias.casefold()
            if normalized_alias not in alias_claim_ids:
                alias_claim_ids[normalized_alias] = (alias, set())
            alias_claim_ids[normalized_alias][1].add(claim.claim_id)
    source_literals = _architecture_source_literals(plan)
    story_id = f"story-{story_index:02d}"

    figure_by_role: Dict[str, str] = {}
    figures: List[FigureContract] = []
    for figure_index, model_figure in enumerate(draft.figures, start=1):
        figure_id = f"{story_id}-figure-{figure_index:02d}"
        if model_figure.role_key in figure_by_role:
            warnings.append(
                f"{story_id} duplicate figure role_key {model_figure.role_key!r}"
            )
        figure_by_role[model_figure.role_key] = figure_id
        figure_placements: List[ClaimPlacement] = []
        for binding in model_figure.claim_bindings:
            if binding.claim_id not in all_claim_ids:
                errors.append(
                    f"{story_id} figure {model_figure.role_key!r} references "
                    f"unknown claim {binding.claim_id!r}"
                )
                continue
            if binding.role == "positive" and binding.claim_id not in positive_ids:
                errors.append(
                    f"{story_id} figure {model_figure.role_key!r} uses "
                    f"non-positive claim {binding.claim_id!r} as positive support"
                )
            if binding.role in {"limitation", "counterevidence"}:
                if binding.claim_id in positive_ids:
                    warnings.append(
                        f"{story_id} figure {model_figure.role_key!r} uses "
                        f"positive claim {binding.claim_id!r} in a "
                        "limitation/counterevidence role; role retained as "
                        "organizational framing"
                    )
                elif binding.claim_id not in limitation_ids:
                    errors.append(
                        f"{story_id} figure {model_figure.role_key!r} uses "
                        f"claim {binding.claim_id!r} in a "
                        "limitation/counterevidence role but the claim is "
                        "not a limitation claim"
                    )
            figure_placements.append(
                ClaimPlacement(claim_id=binding.claim_id, role=binding.role)
            )
        figure_claim_ids = sorted({item.claim_id for item in figure_placements})
        fact_owners = {
            fact.fact_id: claim_id for claim_id, fact in fact_by_claim.items()
        }
        effective_fact_ids = {
            fact_id
            for fact_id in model_figure.fact_ids
            if fact_owners.get(fact_id) in figure_claim_ids
        }
        removed_fact_ids = sorted(
            set(model_figure.fact_ids) - effective_fact_ids
        )
        if removed_fact_ids:
            warnings.append(
                f"{story_id} figure {model_figure.role_key!r} removed "
                f"orphan fact_ids {removed_fact_ids} that do not belong to "
                "its bound claims"
            )
        for placement in figure_placements:
            if placement.role != "positive":
                continue
            fact = fact_by_claim.get(placement.claim_id)
            if fact is not None and fact.fact_id not in effective_fact_ids:
                effective_fact_ids.add(fact.fact_id)
                warnings.append(
                    f"{story_id} figure {model_figure.role_key!r} restored "
                    f"fact_id {fact.fact_id!r} for positive claim "
                    f"{placement.claim_id!r}"
                )
        bindings: List[ArtifactFieldBinding] = []
        for binding in model_figure.artifact_bindings:
            descriptor = manifest_by_id.get(binding.artifact_id)
            if descriptor is None:
                errors.append(
                    f"{story_id} figure {model_figure.role_key!r} references "
                    f"unknown artifact {binding.artifact_id!r}"
                )
                continue
            undeclared = sorted(set(binding.selected_fields) - set(descriptor.fields))
            if undeclared:
                errors.append(
                    f"{story_id} figure {model_figure.role_key!r} selects "
                    f"undeclared fields {undeclared} for artifact "
                    f"{binding.artifact_id!r}"
                )
            bindings.append(
                ArtifactFieldBinding(
                    artifact_id=binding.artifact_id,
                    selected_fields=sorted(set(binding.selected_fields)),
                )
            )
        quantitative = model_figure.kind in QUANTITATIVE_KINDS
        authorized_artifacts = sorted(
            {
                artifact_id
                for placement in figure_placements
                for artifact_id in _claim_authorized_artifacts(
                    claims_by_id, fact_by_claim, placement.claim_id
                )
            }
        )
        selected_artifacts = sorted({item.artifact_id for item in bindings})
        if selected_artifacts and not set(selected_artifacts) <= set(
            authorized_artifacts
        ):
            errors.append(
                f"{story_id} figure {model_figure.role_key!r} attaches "
                f"unrelated artifacts {sorted(set(selected_artifacts) - set(authorized_artifacts))} "
                "not authorized by the bound claims/facts"
            )
        bound_pairs = {
            (binding.artifact_id, field)
            for binding in bindings
            for field in binding.selected_fields
        }
        if quantitative:
            if not bindings:
                errors.append(
                    f"{story_id} figure {model_figure.role_key!r} is quantitative "
                    "but has no trusted artifact bindings"
                )
            for placement in figure_placements:
                claim = claims_by_id.get(placement.claim_id)
                if claim is None:
                    continue
                lineage = claim.metadata.get("value_lineage") or []
                authorized_pairs = {
                    (
                        str(ref.get("artifact_id") or ""),
                        str(ref.get("field") or ""),
                    )
                    for ref in lineage
                    if ref.get("artifact_id") and ref.get("field")
                }
                if not authorized_pairs:
                    continue
                if not (authorized_pairs & bound_pairs):
                    errors.append(
                        f"{story_id} figure {model_figure.role_key!r} numeric "
                        f"claim {placement.claim_id!r} is not bound to any "
                        "authorized value field; authorized pairs "
                        f"{sorted(authorized_pairs)}"
                    )
            lineage_bound = [
                placement.claim_id
                for placement in figure_placements
                if placement.claim_id in claims_by_id
                and claims_by_id[placement.claim_id].metadata.get("value_lineage")
            ]
            if lineage_bound:
                authorized_pairs = {
                    (
                        str(ref.get("artifact_id") or ""),
                        str(ref.get("field") or ""),
                    )
                    for claim_id in lineage_bound
                    for ref in (
                        claims_by_id[claim_id].metadata.get("value_lineage") or []
                    )
                    if ref.get("artifact_id") and ref.get("field")
                }
                value_shapes = context.get("value_shapes") or {}
                scalar_by_artifact = {
                    artifact_id: {
                        field for field, kind in shapes.items() if kind == "scalar"
                    }
                    for artifact_id, shapes in value_shapes.items()
                }
                unauthorized: List[Tuple[str, str]] = []
                for binding in bindings:
                    scalar_fields = scalar_by_artifact.get(binding.artifact_id, set())
                    for field in binding.selected_fields:
                        pair = (binding.artifact_id, field)
                        if field in scalar_fields and pair not in authorized_pairs:
                            unauthorized.append(pair)
                if unauthorized:
                    errors.append(
                        f"{story_id} figure {model_figure.role_key!r} selects "
                        f"unauthorized scalar value field(s) "
                        f"{sorted(unauthorized)}; every scalar value field "
                        "must be an exact value-lineage pair of a claim bound "
                        "to this figure; authorized pairs "
                        f"{sorted(authorized_pairs)}"
                    )
            for placement in figure_placements:
                if placement.role != "positive":
                    continue
                fact = fact_by_claim.get(placement.claim_id)
                if fact is None:
                    errors.append(
                        f"{story_id} figure {model_figure.role_key!r} positive "
                        f"claim {placement.claim_id!r} has no Stage 8 FactRecord"
                    )
                elif fact.fact_id not in effective_fact_ids:
                    errors.append(
                        f"{story_id} figure {model_figure.role_key!r} positive "
                        f"claim {placement.claim_id!r} is missing its fact_id "
                        f"{fact.fact_id!r} in fact_ids"
                    )
            source_mode = "trusted_artifact"
        else:
            source_mode = "trusted_artifact" if bindings else "synthesized_claims"
        figures.append(
            FigureContract(
                figure_id=figure_id,
                role_key=model_figure.role_key,
                story_role=model_figure.story_role,
                section_target="",
                kind=model_figure.kind,
                panel_intents=list(model_figure.panel_intents),
                caption_intent=model_figure.caption_intent,
                claim_bindings=figure_placements,
                claim_ids=figure_claim_ids,
                fact_ids=sorted(effective_fact_ids),
                artifact_bindings=bindings,
                limitations=list(model_figure.limitations),
                source_mode=source_mode,
                conceptual=not quantitative,
            )
        )
        _verify_no_invented_numbers(
            _bound_verified_text(
                ledger,
                claims_by_id,
                fact_by_claim,
                figure_claim_ids,
                sorted(effective_fact_ids),
            ),
            [
                model_figure.story_role,
                model_figure.caption_intent,
                *model_figure.panel_intents,
            ],
            errors,
            warnings,
            f"{story_id} figure {model_figure.role_key!r}",
            source_literals,
        )

    sections: List[SectionContract] = []
    for section_index, model_section in enumerate(draft.sections, start=1):
        section_id = f"{story_id}-section-{section_index:02d}"
        placements: List[ClaimPlacement] = []
        for binding in model_section.claim_bindings:
            if binding.claim_id not in all_claim_ids:
                errors.append(
                    f"{story_id} section {model_section.heading!r} references "
                    f"unknown claim {binding.claim_id!r}"
                )
                continue
            if binding.role == "positive" and binding.claim_id not in positive_ids:
                errors.append(
                    f"{story_id} section {model_section.heading!r} uses "
                    f"non-positive claim {binding.claim_id!r} as positive support"
                )
            if binding.role in {"limitation", "counterevidence"}:
                if binding.claim_id in positive_ids:
                    warnings.append(
                        f"{story_id} section {model_section.heading!r} uses "
                        f"positive claim {binding.claim_id!r} in a "
                        "limitation/counterevidence role; role retained as "
                        "organizational framing"
                    )
                elif binding.claim_id not in limitation_ids:
                    errors.append(
                        f"{story_id} section {model_section.heading!r} uses "
                        f"claim {binding.claim_id!r} in a "
                        "limitation/counterevidence role but the claim is "
                        "not a limitation claim"
                    )
            placements.append(
                ClaimPlacement(claim_id=binding.claim_id, role=binding.role)
            )
        bound_claim_ids = {item.claim_id for item in placements}
        section_text = " ".join(
            [
                model_section.heading,
                model_section.purpose,
                *model_section.key_messages,
            ]
        )
        for alias, supporting_claim_ids in alias_claim_ids.values():
            if _text_names_alias(section_text, alias) and not (
                bound_claim_ids & supporting_claim_ids
            ):
                errors.append(
                    f"{story_id} section {model_section.heading!r} names "
                    f"result subject {alias!r} but binds no Claim for that "
                    "subject; synthesis and conclusion sections may reuse "
                    "Claims already assigned earlier in the story"
                )
        figure_ids = []
        for role_key in model_section.figure_roles:
            if role_key not in figure_by_role:
                errors.append(
                    f"{story_id} section {model_section.heading!r} references "
                    f"unknown figure role {role_key!r}"
                )
                continue
            figure_ids.append(figure_by_role[role_key])
        sections.append(
            SectionContract(
                section_id=section_id,
                heading=model_section.heading,
                purpose=model_section.purpose,
                claim_bindings=placements,
                figure_ids=figure_ids,
                transitions=list(model_section.transitions),
                key_messages=list(model_section.key_messages),
            )
        )
        _verify_no_invented_numbers(
            _bound_verified_text(
                ledger,
                claims_by_id,
                fact_by_claim,
                [item.claim_id for item in placements],
                [],
            ),
            [
                model_section.heading,
                model_section.purpose,
                *model_section.key_messages,
            ],
            errors,
            warnings,
            f"{story_id} section {model_section.heading!r}",
            source_literals,
        )
    for figure_index, figure in enumerate(figures):
        section_target = next(
            (
                section.section_id
                for section in sections
                if figure.figure_id in section.figure_ids
            ),
            "",
        )
        figures[figure_index] = figure.model_copy(
            update={"section_target": section_target}
        )
        if not section_target:
            warnings.append(
                f"{story_id} figure {figure.role_key!r} has no section target"
            )

    referenced_claims = set()
    for section in sections:
        referenced_claims.update(item.claim_id for item in section.claim_bindings)
    for figure in figures:
        referenced_claims.update(item.claim_id for item in figure.claim_bindings)
    story_context = _bound_verified_text(
        ledger,
        claims_by_id,
        fact_by_claim,
        sorted(referenced_claims),
        [],
    )
    for claim_id in sorted(referenced_claims):
        fact = fact_by_claim.get(claim_id)
        if fact is not None:
            story_context = f"{story_context} {fact.statement}"
    _verify_no_invented_numbers(
        story_context,
        [
            draft.story_shape,
            draft.central_thesis,
            draft.recommendation_rationale,
            *draft.exclusions,
            *draft.strengths,
            *draft.risks,
        ],
        errors,
        warnings,
        f"{story_id} story",
        source_literals,
    )
    assignments: List[ClaimAssignment] = []
    for claim_id in sorted(referenced_claims):
        claim = claims_by_id[claim_id]
        roles = set()
        for section in sections:
            roles.update(
                item.role
                for item in section.claim_bindings
                if item.claim_id == claim_id
            )
        for figure in figures:
            roles.update(
                item.role for item in figure.claim_bindings if item.claim_id == claim_id
            )
        figure_ids = [
            figure.figure_id for figure in figures if claim_id in figure.claim_ids
        ]
        role = (
            "positive"
            if "positive" in roles
            else "limitation" if "limitation" in roles else "counterevidence"
        )
        assignments.append(
            ClaimAssignment(
                claim_id=claim_id,
                fact_id=claim.metadata.get("fact_id"),
                section_ids=[
                    section.section_id
                    for section in sections
                    if any(item.claim_id == claim_id for item in section.claim_bindings)
                ],
                figure_ids=figure_ids,
                role=role,
                reason=f"assigned by {story_id}",
            )
        )

    omitted: List[OmittedClaim] = []
    for item in draft.omitted_claims:
        if item.claim_id not in positive_ids:
            warnings.append(
                f"{story_id} omitted claim {item.claim_id!r} is not a "
                "writable positive claim; ignored as organization-only "
                "metadata"
            )
            continue
        if item.claim_id in referenced_claims:
            warnings.append(
                f"{story_id} claim {item.claim_id!r} is both assigned and omitted; "
                "assignment wins"
            )
            continue
        omitted.append(OmittedClaim(claim_id=item.claim_id, reason=item.reason))
    unassigned = sorted(positive_ids - referenced_claims)
    for claim_id in unassigned:
        if claim_id not in {item.claim_id for item in omitted}:
            omitted.append(
                OmittedClaim(
                    claim_id=claim_id,
                    reason=(
                        "Planner left this writable claim unaccounted for; "
                        "retained as an explicit coverage gap."
                    ),
                )
            )
            warnings.append(
                f"{story_id} unassigned positive claim {claim_id!r}; "
                "materialized as an explicit coverage gap"
            )

    if errors:
        return None
    return StoryCandidate(
        story_id=story_id,
        story_shape=draft.story_shape,
        central_thesis=draft.central_thesis,
        section_contracts=sections,
        figure_contracts=figures,
        claim_assignments=assignments,
        omitted_claims=omitted,
        exclusions=list(draft.exclusions),
        strengths=list(draft.strengths),
        risks=list(draft.risks),
        recommendation_rationale=draft.recommendation_rationale,
        recommendation_score=draft.recommendation_score,
    )


def _structure_signature(story: StoryCandidate) -> str:
    return _canonical_json(
        {
            "story_shape": story.story_shape,
            "section_purposes": [item.purpose for item in story.section_contracts],
            "section_claim_roles": [
                sorted((item.role, item.claim_id) for item in section.claim_bindings)
                for section in story.section_contracts
            ],
            "claim_distribution": sorted(
                [
                    (
                        item.role,
                        item.claim_id,
                    )
                    for item in story.claim_assignments
                ]
            ),
            "figure_roles": sorted(item.role_key for item in story.figure_contracts),
        }
    )


@dataclass(frozen=True)
class _RejectedCandidate:
    index: int
    raw_payload: Dict[str, Any]
    errors: Tuple[str, ...]
    recommendation_score: float


def _apply_story_bounds_and_status(
    stories: List[StoryCandidate],
    warnings: List[str],
) -> Literal["available", "partial", "unavailable"]:
    if len(stories) > 5:
        warnings.append(
            f"architecture provider returned {len(stories)} valid story "
            "candidates; kept the first 5"
        )
        del stories[5:]
        return "available"
    if len(stories) < 2:
        warnings.append(
            "architecture provider returned fewer than 2 valid story candidates"
        )
        return "partial" if stories else "unavailable"
    return "available"


def _process_architecture_results(
    plan_model: ArticleDirectorPlan,
    ledger_model: ClaimLedgerResult,
    context: Mapping[str, Any],
    results: Sequence[ArchitectureProviderResult],
    *,
    stories: List[StoryCandidate],
    errors: List[str],
    warnings: List[str],
    candidate_rejections: List[_RejectedCandidate],
    accepted_raw_payloads: Optional[Dict[str, Dict[str, Any]]] = None,
    repair: bool = False,
) -> None:
    """Validate provider story candidates and collect accepted stories."""

    prefix = "repaired " if repair else ""
    malformed_envelope = (
        "architecture repair provider returned a malformed envelope"
        if repair
        else "architecture provider returned a malformed envelope"
    )
    for result in results:
        if not isinstance(result, ArchitectureProviderResult):
            warnings.append(malformed_envelope)
            continue
        for raw_story in result.stories:
            normalized_story, normalization_notes = _normalize_provider_story_bindings(
                raw_story
            )
            for note in normalization_notes:
                warnings.append(f"{prefix}story candidate {note}")
            extra_paths = _provider_story_extra_paths(normalized_story)
            if extra_paths:
                warnings.append(
                    f"{prefix}story candidate ignored redundant provider "
                    f"fields: {extra_paths}"
                )
            try:
                draft = _ModelStoryDraft.model_validate(normalized_story)
            except ValidationError as exc:
                warnings.append(f"malformed {prefix}story candidate skipped: {exc}")
                if isinstance(normalized_story, Mapping):
                    try:
                        recommendation_score = float(
                            normalized_story.get("recommendation_score") or 0.0
                        )
                    except (TypeError, ValueError):
                        recommendation_score = 0.0
                    candidate_rejections.append(
                        _RejectedCandidate(
                            index=(len(stories) + len(candidate_rejections) + 1),
                            raw_payload=dict(normalized_story),
                            errors=(f"provider form is malformed: {exc}",),
                            recommendation_score=max(
                                0.0, min(1.0, recommendation_score)
                            ),
                        )
                    )
                continue
            candidate_index = len(stories) + len(candidate_rejections) + 1
            error_start = len(errors)
            story = _assemble_story(
                plan_model,
                ledger_model,
                context,
                draft,
                candidate_index,
                errors,
                warnings,
            )
            if story is not None:
                stories.append(story)
                if accepted_raw_payloads is not None:
                    accepted_raw_payloads[story.story_id] = dict(normalized_story)
                continue
            candidate_errors = [str(item) for item in errors[error_start:]]
            del errors[error_start:]
            if candidate_errors:
                warnings.append(
                    f"{prefix}story candidate {candidate_index} rejected: "
                    + "; ".join(candidate_errors)
                )
                candidate_rejections.append(
                    _RejectedCandidate(
                        index=candidate_index,
                        raw_payload=dict(normalized_story),
                        errors=tuple(candidate_errors),
                        recommendation_score=float(draft.recommendation_score),
                    )
                )


def _select_repair_candidate(
    candidate_rejections: Sequence[_RejectedCandidate],
    eligible_claim_ids: Optional[set[str]] = None,
) -> Optional[_RejectedCandidate]:
    if not candidate_rejections:
        return None

    eligible = set(eligible_claim_ids or ())

    def coverage_count(item: _RejectedCandidate) -> int:
        claim_ids: set[str] = set()
        for container_key in ("sections", "figures"):
            for container in item.raw_payload.get(container_key) or ():
                if not isinstance(container, Mapping):
                    continue
                for binding in container.get("claim_bindings") or ():
                    if not isinstance(binding, Mapping):
                        continue
                    claim_id = str(binding.get("claim_id") or "")
                    if claim_id and (not eligible or claim_id in eligible):
                        claim_ids.add(claim_id)
        return len(claim_ids)

    return min(
        candidate_rejections,
        key=lambda item: (
            -coverage_count(item),
            len(item.errors),
            -float(item.recommendation_score),
            int(item.index),
        ),
    )


def _build_repair_request(target: _RejectedCandidate) -> Dict[str, Any]:
    if target is None:
        raise ValueError("no rejected story candidate is available for repair")
    return {
        "purpose": (
            "Bounded repair round: correct only the organization of the "
            "single rejected story candidate below. Do not add, remove, or "
            "change scientific content, values, claims, facts, or IDs."
        ),
        "constraints": [
            "Do not emit measurement-like numbers unless the exact expression "
            "appears verbatim in a bound claim or fact statement in the "
            "architecture payload.",
            "omitted_claims may name only writable positive claims.",
            "artifact_bindings must use only artifacts authorized by the "
            "bound claims/facts.",
            "Use only claim_ids, fact_ids, and artifact_ids supplied in the "
            "architecture payload.",
        ],
        "candidate_index": target.index,
        "candidate": target.raw_payload,
        "errors": list(target.errors),
    }


def _positive_assignment_count(
    story: StoryCandidate,
    positive_claim_ids: set[str],
) -> int:
    return len(
        {
            item.claim_id
            for item in story.claim_assignments
            if item.claim_id in positive_claim_ids
        }
    )


def _build_completion_repair_request(
    story: StoryCandidate,
    raw_payload: Mapping[str, Any],
    *,
    positive_claim_ids: set[str],
    positive_claim_count: int,
    minimum_assigned_claim_count: int,
) -> Dict[str, Any]:
    assigned_count = _positive_assignment_count(story, positive_claim_ids)
    return {
        "purpose": (
            "The candidate is structurally valid but is not yet a complete "
            "whole-Article architecture. Reorganize this candidate so it "
            "meets the supplied story completion contract. This is an "
            "organization-only repair: group compatible verified Claims into "
            "sections and figures without changing their scientific content."
        ),
        "constraints": [
            f"Assign at least {minimum_assigned_claim_count} of the "
            f"{positive_claim_count} writable positive Claims to a section "
            "or figure.",
            f"Omit at most {positive_claim_count - minimum_assigned_claim_count} "
            "writable positive Claim(s).",
            "A result is not omittable merely because it belongs to another "
            "route or candidate, is secondary to the preferred narrative, is "
            "a negative result, or complicates the central thesis.",
            "Combine compatible Claims in shared comparative sections rather "
            "than creating one mini-section per Claim.",
            "Do not invent, strengthen, weaken, delete, or relabel scientific "
            "content, values, Claims, facts, artifacts, or IDs.",
            "Use only claim_ids, fact_ids, and artifact_ids supplied in the "
            "architecture payload.",
        ],
        "candidate_index": int(story.story_id.rsplit("-", 1)[-1]),
        "candidate": dict(raw_payload),
        "errors": [
            f"candidate assigned {assigned_count} of {positive_claim_count} "
            f"writable positive Claims; at least {minimum_assigned_claim_count} "
            "are required for a complete-paper candidate",
            *[
                f"omitted {item.claim_id}: {item.reason}"
                for item in story.omitted_claims
            ],
        ],
        "story_completion_contract": {
            "writable_positive_claim_count": positive_claim_count,
            "minimum_assigned_claim_count": minimum_assigned_claim_count,
            "maximum_omitted_claim_count": (
                positive_claim_count - minimum_assigned_claim_count
            ),
            "target_coverage_fraction": TARGET_STORY_COVERAGE_FRACTION,
        },
    }


def _merge_provider_usage(
    usage_rows: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    rows = [row for row in (dict(item or {}) for item in usage_rows) if row]
    if not rows:
        return {}
    if len(rows) == 1 and len(usage_rows) == 1:
        return rows[0]
    merged: Dict[str, Any] = {
        "rows": rows,
        "request_attempt_count": len(usage_rows),
    }
    input_key = (
        "input_tokens"
        if any("input_tokens" in row for row in rows)
        else "estimated_input_tokens"
    )
    output_key = (
        "output_tokens"
        if any("output_tokens" in row for row in rows)
        else "estimated_output_tokens"
    )
    merged[input_key] = sum(
        int(row.get("input_tokens") or row.get("estimated_input_tokens") or 0)
        for row in rows
    )
    merged[output_key] = sum(
        int(row.get("output_tokens") or row.get("estimated_output_tokens") or 0)
        for row in rows
    )
    if any("total_tokens" in row for row in rows):
        merged["total_tokens"] = sum(int(row.get("total_tokens") or 0) for row in rows)
    for cost_key in ("estimated_list_price_cost_cny", "estimated_cost_cny"):
        if any(cost_key in row for row in rows):
            merged[cost_key] = round(
                sum(
                    float(
                        row.get("estimated_list_price_cost_cny")
                        or row.get("estimated_cost_cny")
                        or 0.0
                    )
                    for row in rows
                ),
                8,
            )
            break
    model = next(
        (row.get("model_name") for row in rows if row.get("model_name")),
        "",
    )
    if model:
        merged["model_name"] = model
    return merged


class QwenArticleArchitecturePlanner:
    """Concrete qwen3.7-flash organization-only planner adapter."""

    def __init__(
        self,
        *,
        prompt_path: str | Path = DEFAULT_PROMPT_PATH,
        client: QwenFlashOnlyClient | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self.prompt_path = Path(prompt_path)
        self.max_tokens = int(max_tokens)
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be at least 1")
        self.client = client or QwenFlashOnlyClient(
            agent_name="ArticleArchitecturePlanner"
        )

    def __call__(
        self, requests: Sequence[Mapping[str, Any]]
    ) -> List[ArchitectureProviderResult]:
        results: List[ArchitectureProviderResult] = []
        for request in requests:
            messages = [
                {
                    "role": "system",
                    "content": self.prompt_path.read_text(encoding="utf-8"),
                },
                {
                    "role": "user",
                    "content": json.dumps(request, ensure_ascii=False),
                },
            ]
            response = self.client.call(
                messages, max_tokens=self.max_tokens, force_mock=False
            )
            raw_content = str(response.get("content") or "")
            parsed = _safe_json(raw_content)
            stories = parsed.get("stories")
            if not isinstance(stories, list):
                usage = dict(response.get("_llm_usage") or {})
                raise ValueError(
                    "architecture provider response must be a JSON object "
                    "with a 'stories' array "
                    f"(content_chars={len(raw_content)}, "
                    f"output_tokens={int(usage.get('output_tokens') or 0)}, "
                    f"partial_stream={bool(usage.get('partial_stream'))})"
                )
            usage = dict(response.get("_llm_usage") or {})
            results.append(
                ArchitectureProviderResult(
                    stories=stories,
                    usage=usage,
                    provider_model=MODEL_NAME,
                    mock_llm=bool(usage.get("mock_llm")),
                )
            )
        return results

    def repair(
        self,
        requests: Sequence[Mapping[str, Any]],
        repair_request: Mapping[str, Any],
    ) -> List[ArchitectureProviderResult]:
        """Bounded repair round over the same payloads (organization only)."""

        results: List[ArchitectureProviderResult] = []
        for request in requests:
            messages = [
                {
                    "role": "system",
                    "content": self.prompt_path.read_text(encoding="utf-8"),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "architecture_payload": request,
                            "repair_request": dict(repair_request),
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
            response = self.client.call(
                messages, max_tokens=self.max_tokens, force_mock=False
            )
            parsed = _safe_json(str(response.get("content") or ""))
            stories = parsed.get("stories")
            if not isinstance(stories, list) or len(stories) != 1:
                raise ValueError(
                    "architecture repair response must be a JSON object with "
                    "exactly one story in the 'stories' array"
                )
            usage = dict(response.get("_llm_usage") or {})
            results.append(
                ArchitectureProviderResult(
                    stories=stories,
                    usage=usage,
                    provider_model=MODEL_NAME,
                    mock_llm=bool(usage.get("mock_llm")),
                )
            )
        return results


def build_article_architecture(
    plan: ArticleDirectorPlan | Mapping[str, Any],
    ledger: ClaimLedgerResult | Mapping[str, Any],
    artifact_manifest: Sequence[ArtifactDescriptor | Mapping[str, Any]],
    *,
    architecture_provider: Optional[ArchitectureProvider] = None,
    value_shapes: Optional[Mapping[str, Mapping[str, str]]] = None,
    memory_store: ArticleMemoryStore | None = None,
    graph: ExperimentGraph | None = None,
    run_id: Optional[str] = None,
    journal_path: str | Path | None = None,
) -> ArticleArchitectureResult:
    errors: List[str] = []
    warnings: List[str] = []
    if (memory_store is not None or graph is not None) and not run_id:
        errors.append("run_id is required when memory_store or graph is provided")
    try:
        plan_model = (
            plan
            if isinstance(plan, ArticleDirectorPlan)
            else ArticleDirectorPlan.model_validate(plan)
        )
    except ValidationError as exc:
        errors.append(f"plan is invalid: {exc}")
        return _hard_blocker(errors, warnings)
    try:
        ledger_model = (
            ledger
            if isinstance(ledger, ClaimLedgerResult)
            else ClaimLedgerResult.model_validate(ledger)
        )
    except ValidationError as exc:
        errors.append(f"ledger is invalid: {exc}")
        return _hard_blocker(errors, warnings)
    manifest: List[ArtifactDescriptor] = []
    for index, raw in enumerate(artifact_manifest):
        try:
            manifest.append(
                raw
                if isinstance(raw, ArtifactDescriptor)
                else ArtifactDescriptor.model_validate(raw)
            )
        except ValidationError as exc:
            errors.append(f"artifact_manifest[{index}] is invalid: {exc}")
    if errors:
        return _hard_blocker(errors, warnings)

    context = _validate_inputs(plan_model, ledger_model, manifest, errors)
    if errors:
        return _hard_blocker(errors, warnings)
    context["value_shapes"] = dict(value_shapes or {})

    stories: List[StoryCandidate] = []
    model_status: Literal["available", "partial", "unavailable"] = "unavailable"
    usage_rows: List[Mapping[str, Any]] = []
    semantic_model = "none"
    candidate_rejections: List[_RejectedCandidate] = []
    accepted_raw_payloads: Dict[str, Dict[str, Any]] = {}
    completion_repair_count = 0
    if architecture_provider is not None:
        try:
            payloads = build_architecture_payload(
                plan_model,
                ledger_model,
                manifest,
                value_shapes=context["value_shapes"],
            )
            results = list(architecture_provider(payloads))
            if len(results) != len(payloads):
                warnings.append(
                    "architecture provider returned the wrong number of payloads"
                )
            else:
                semantic_model = results[0].provider_model
                usage_rows.append(dict(results[0].usage or {}))
                _process_architecture_results(
                    plan_model,
                    ledger_model,
                    context,
                    results,
                    stories=stories,
                    errors=errors,
                    warnings=warnings,
                    candidate_rejections=candidate_rejections,
                    accepted_raw_payloads=accepted_raw_payloads,
                )
                if errors:
                    pass  # hard fail below
                else:
                    repair = getattr(architecture_provider, "repair", None)
                    provider_envelopes_ok = all(
                        isinstance(item, ArchitectureProviderResult) for item in results
                    )
                    if (
                        len(stories) < 2
                        and candidate_rejections
                        and provider_envelopes_ok
                        and callable(repair)
                    ):
                        warnings.append(
                            "architecture repair rounds used for rejected "
                            "story candidates"
                        )
                        repair_target = _select_repair_candidate(
                            candidate_rejections,
                            eligible_claim_ids={
                                claim.claim_id
                                for claim in _positive_claims(ledger_model)
                            },
                        )
                        for repair_round in range(1, MAX_FORMAT_REPAIR_CALLS + 1):
                            if repair_target is None:
                                break
                            repair_request = _build_repair_request(repair_target)
                            try:
                                repaired_results = list(
                                    repair(payloads, repair_request)
                                )
                            except Exception as exc:
                                warnings.append(
                                    "architecture repair provider unavailable: "
                                    f"{exc}"
                                )
                                break
                            if repaired_results and isinstance(
                                repaired_results[0],
                                ArchitectureProviderResult,
                            ):
                                usage_rows.append(dict(repaired_results[0].usage or {}))
                            if len(repaired_results) != len(payloads):
                                warnings.append(
                                    "architecture repair provider returned the "
                                    "wrong number of payloads"
                                )
                                break
                            if not all(
                                isinstance(item, ArchitectureProviderResult)
                                and len(item.stories) == 1
                                for item in repaired_results
                            ):
                                warnings.append(
                                    "architecture repair provider returned an "
                                    "invalid envelope; expected exactly one "
                                    "story candidate"
                                )
                                break
                            previous_error_count = len(repair_target.errors)
                            rejection_start = len(candidate_rejections)
                            story_count_before = len(stories)
                            _process_architecture_results(
                                plan_model,
                                ledger_model,
                                context,
                                repaired_results,
                                stories=stories,
                                errors=errors,
                                warnings=warnings,
                                candidate_rejections=candidate_rejections,
                                accepted_raw_payloads=accepted_raw_payloads,
                                repair=True,
                            )
                            if errors:
                                break  # hard fail below
                            if len(stories) > story_count_before:
                                break
                            new_rejections = candidate_rejections[rejection_start:]
                            if (
                                repair_round == MAX_FORMAT_REPAIR_CALLS
                                or not new_rejections
                                or len(new_rejections[0].errors) == 0
                                or len(new_rejections[0].errors) >= previous_error_count
                            ):
                                break
                            repair_target = new_rejections[0]

                    positive_claim_ids = {
                        claim.claim_id for claim in _positive_claims(ledger_model)
                    }
                    minimum_assigned_claim_count = (
                        min(
                            len(positive_claim_ids),
                            max(
                                1,
                                math.ceil(
                                    len(positive_claim_ids)
                                    * TARGET_STORY_COVERAGE_FRACTION
                                ),
                            ),
                        )
                        if positive_claim_ids
                        else 0
                    )
                    undercovered_stories = sorted(
                        (
                            story
                            for story in stories
                            if _positive_assignment_count(story, positive_claim_ids)
                            < minimum_assigned_claim_count
                        ),
                        key=lambda story: (
                            -_positive_assignment_count(story, positive_claim_ids),
                            -float(story.recommendation_score),
                            story.story_id,
                        ),
                    )
                    if (
                        undercovered_stories
                        and provider_envelopes_ok
                        and callable(repair)
                    ):
                        warnings.append(
                            "architecture completion repair used for "
                            "structurally valid but undercovered story "
                            "candidates"
                        )
                        for story in undercovered_stories:
                            if completion_repair_count >= MAX_STORY_COMPLETION_REPAIRS:
                                break
                            raw_payload = accepted_raw_payloads.get(story.story_id)
                            if raw_payload is None:
                                warnings.append(
                                    f"completion repair skipped {story.story_id!r}: "
                                    "accepted provider form is unavailable"
                                )
                                continue
                            try:
                                candidate_index = int(story.story_id.rsplit("-", 1)[-1])
                            except (TypeError, ValueError):
                                warnings.append(
                                    f"completion repair skipped {story.story_id!r}: "
                                    "story ID has no candidate index"
                                )
                                continue
                            original_count = _positive_assignment_count(
                                story, positive_claim_ids
                            )
                            completion_request = _build_completion_repair_request(
                                story,
                                raw_payload,
                                positive_claim_ids=positive_claim_ids,
                                positive_claim_count=len(positive_claim_ids),
                                minimum_assigned_claim_count=(
                                    minimum_assigned_claim_count
                                ),
                            )
                            try:
                                completion_repair_count += 1
                                completion_results = list(
                                    repair(payloads, completion_request)
                                )
                            except Exception as exc:
                                warnings.append(
                                    f"completion repair unavailable for "
                                    f"{story.story_id}: {exc}"
                                )
                                continue
                            if completion_results and isinstance(
                                completion_results[0], ArchitectureProviderResult
                            ):
                                usage_rows.append(
                                    dict(completion_results[0].usage or {})
                                )
                            if (
                                len(completion_results) != 1
                                or not isinstance(
                                    completion_results[0],
                                    ArchitectureProviderResult,
                                )
                                or len(completion_results[0].stories) != 1
                                or not isinstance(
                                    completion_results[0].stories[0], Mapping
                                )
                            ):
                                warnings.append(
                                    f"completion repair for {story.story_id} "
                                    "returned an invalid envelope"
                                )
                                continue
                            repaired_raw, normalization_notes = (
                                _normalize_provider_story_bindings(
                                    completion_results[0].stories[0]
                                )
                            )
                            for note in normalization_notes:
                                warnings.append(
                                    f"completion-repaired {story.story_id} " f"{note}"
                                )
                            extra_paths = _provider_story_extra_paths(repaired_raw)
                            if extra_paths:
                                warnings.append(
                                    f"completion-repaired {story.story_id} ignored "
                                    f"redundant provider fields: {extra_paths}"
                                )
                            try:
                                repaired_draft = _ModelStoryDraft.model_validate(
                                    repaired_raw
                                )
                            except ValidationError as exc:
                                warnings.append(
                                    f"completion repair for {story.story_id} "
                                    f"returned a malformed story: {exc}"
                                )
                                continue
                            completion_errors: List[str] = []
                            repaired_story = _assemble_story(
                                plan_model,
                                ledger_model,
                                context,
                                repaired_draft,
                                candidate_index,
                                completion_errors,
                                warnings,
                            )
                            if repaired_story is None or completion_errors:
                                warnings.append(
                                    f"completion repair for {story.story_id} "
                                    "failed integrity validation: "
                                    + "; ".join(completion_errors)
                                )
                                continue
                            repaired_count = _positive_assignment_count(
                                repaired_story, positive_claim_ids
                            )
                            if repaired_count <= original_count:
                                warnings.append(
                                    f"completion repair for {story.story_id} made "
                                    f"no coverage progress ({original_count} -> "
                                    f"{repaired_count}); original retained"
                                )
                                continue
                            stories[stories.index(story)] = repaired_story
                            accepted_raw_payloads[story.story_id] = dict(repaired_raw)
                            warnings.append(
                                f"completion repair improved {story.story_id} "
                                f"positive-Claim coverage from {original_count} "
                                f"to {repaired_count} of {len(positive_claim_ids)}"
                            )
                    model_status = _apply_story_bounds_and_status(stories, warnings)
        except Exception as exc:
            warnings.append(f"architecture provider unavailable: {exc}")

    usage = _merge_provider_usage(usage_rows)
    if errors or (candidate_rejections and not stories):
        blocked_errors = list(errors)
        for rejected in candidate_rejections:
            blocked_errors.extend(rejected.errors)
        return _hard_blocker(
            blocked_errors,
            warnings,
            usage=usage,
            semantic_model=semantic_model,
        )

    signatures = [_structure_signature(story) for story in stories]
    if len(signatures) != len(set(signatures)):
        warnings.append("structurally duplicate story candidates detected")

    missing_work = context["missing_work_handoffs"]
    architecture_id = compute_architecture_id(
        plan_model.plan_id,
        ledger_model.ledger_id,
        manifest,
        missing_work,
        stories,
    )
    result = ArticleArchitectureResult(
        architecture_id=architecture_id,
        source_plan_id=plan_model.plan_id,
        source_ledger_id=ledger_model.ledger_id,
        stories=stories,
        artifact_inventory=list(manifest),
        deterministic_inventory=context["inventory"],
        missing_work_handoffs=missing_work,
        warnings=warnings,
        model_status=model_status,
        usage=usage,
        semantic_model=semantic_model,
    )
    if memory_store is not None or graph is not None or journal_path is not None:
        _persist(
            architecture_id=architecture_id,
            result=result,
            memory_store=memory_store,
            graph=graph,
            run_id=str(run_id or ""),
            journal_path=journal_path,
        )
    return result


def _hard_blocker(
    errors: Sequence[str],
    warnings: Sequence[str],
    *,
    usage: Optional[Mapping[str, Any]] = None,
    semantic_model: Optional[str] = None,
) -> ArticleArchitectureResult:
    return ArticleArchitectureResult(
        architecture_id=f"architecture-{_digest('invalid')}",
        stories=[],
        deterministic_inventory={},
        warnings=[str(item) for item in warnings],
        validation_errors=[str(item) for item in errors],
        model_status="unavailable",
        semantic_model=(semantic_model if semantic_model is not None else "none"),
        usage=dict(usage or {}),
    )


def _read_journal(path: str | Path) -> Dict[str, Any]:
    journal_path = Path(path)
    if not journal_path.exists():
        return {}
    try:
        payload = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArticleArchitectureError(
            f"architecture journal is unreadable: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise ArticleArchitectureError("architecture journal must be a JSON object")
    return {
        str(key): dict(value)
        for key, value in payload.items()
        if isinstance(value, Mapping)
    }


def _write_journal(
    path: str | Path,
    journal: Mapping[str, Any],
    architecture_id: str,
    state: Mapping[str, Any],
) -> None:
    payload = dict(journal)
    payload[str(architecture_id)] = dict(state)
    atomic_write_json(Path(path), payload)


def _expected_figure_events(
    result: ArticleArchitectureResult,
) -> List[Tuple[str, Mapping[str, Any]]]:
    events: List[Tuple[str, Mapping[str, Any]]] = []
    for story in result.stories:
        for figure in story.figure_contracts:
            events.append(
                (
                    "article.figure",
                    validate_article_event(
                        "article.figure",
                        {
                            "schema_version": ARTICLE_EVENT_SCHEMA_VERSION,
                            "figure_id": figure.figure_id,
                            "status": "planned",
                        },
                    ),
                )
            )
    return events


def _persist(
    *,
    architecture_id: str,
    result: ArticleArchitectureResult,
    memory_store: Optional[ArticleMemoryStore],
    graph: Optional[ExperimentGraph],
    run_id: str,
    journal_path: Optional[str | Path],
) -> None:
    if journal_path is None:
        if graph is not None:
            _persist_graph(graph, architecture_id, result)
        if memory_store is not None:
            _persist_memory(memory_store, architecture_id, result, run_id)
        return
    journal = _read_journal(journal_path)
    state = journal.get(architecture_id)
    if state is not None and state.get("status") == "completed":
        return
    if state is None:
        state = {
            "status": "in_progress",
            "graph_written": graph is None,
            "memory_written": memory_store is None,
        }
    try:
        if graph is not None and not state.get("graph_written"):
            _persist_graph(graph, architecture_id, result)
            state["graph_written"] = True
            _write_journal(journal_path, journal, architecture_id, state)
        if memory_store is not None and not state.get("memory_written"):
            _persist_memory(memory_store, architecture_id, result, run_id)
            state["memory_written"] = True
            _write_journal(journal_path, journal, architecture_id, state)
        state["status"] = "completed"
        _write_journal(journal_path, journal, architecture_id, state)
    except Exception as exc:
        _write_journal(journal_path, journal, architecture_id, state)
        raise ArticleArchitectureError(
            f"architecture persistence failed: {exc}"
        ) from exc


def _persist_graph(
    graph: ExperimentGraph,
    architecture_id: str,
    result: ArticleArchitectureResult,
) -> None:
    node_id = f"architecture-{architecture_id}"
    summary = f"architecture-{architecture_id}"
    payload = ArticleNodePayload(
        stage=ArticleStage.figure_first_planning,
        hypothesis_ids=[],
        summary=summary,
    )
    expected_events = _expected_figure_events(result)
    created = False
    try:
        graph.create_article_node(payload, node_id=node_id)
        created = True
    except sqlite3.IntegrityError:
        existing = graph.article_node(node_id)
        if existing.get("payload", {}).get("summary") != summary:
            raise ArticleArchitectureIntegrityError(
                f"architecture node {node_id!r} already exists with different content"
            )
    if created:
        for event_type, event_payload in expected_events:
            graph.record_article_event(node_id, event_type, event_payload)
        return
    existing = graph.article_node(node_id)
    seen = {
        (item["event_type"], _canonical_json(item["payload"]))
        for item in existing["history"]
    }
    by_identity: Dict[str, Tuple[str, str]] = {}
    for item in existing["history"]:
        if item["event_type"] == "article.figure":
            identity = f"figure:{item['payload'].get('figure_id')}"
            by_identity[identity] = (
                item["event_type"],
                _canonical_json(item["payload"]),
            )
    for event_type, event_payload in expected_events:
        canonical = _canonical_json(event_payload)
        identity = f"figure:{event_payload.get('figure_id')}"
        if identity in by_identity and by_identity[identity] != (event_type, canonical):
            raise ArticleArchitectureIntegrityError(
                f"architecture node {node_id!r} has conflicting figure event "
                f"for {identity}"
            )
        if (event_type, canonical) in seen:
            continue
        graph.record_article_event(node_id, event_type, event_payload)
        seen.add((event_type, canonical))
        by_identity[identity] = (event_type, canonical)


def _persist_memory(
    memory_store: ArticleMemoryStore,
    architecture_id: str,
    result: ArticleArchitectureResult,
    run_id: str,
) -> None:
    records: List[RunMemoryRecord] = [
        RunMemoryRecord(
            memory_id=f"architecture-{architecture_id}",
            run_id=run_id,
            event_type="article_architecture",
            graph_node_id=f"architecture-{architecture_id}",
            artifact_ids=[],
            operational_note=_canonical_json(result.model_dump(mode="json")),
        )
    ]
    for story in result.stories:
        records.append(
            RunMemoryRecord(
                memory_id=f"story-{architecture_id}-{story.story_id}",
                run_id=run_id,
                event_type="article_story",
                graph_node_id=f"architecture-{architecture_id}",
                artifact_ids=[],
                operational_note=_canonical_json(story.model_dump(mode="json")),
            )
        )
        for figure in story.figure_contracts:
            records.append(
                RunMemoryRecord(
                    memory_id=f"figure-{architecture_id}-{figure.figure_id}",
                    run_id=run_id,
                    event_type="article_figure",
                    graph_node_id=f"architecture-{architecture_id}",
                    artifact_ids=[
                        item.artifact_id for item in figure.artifact_bindings
                    ],
                    operational_note=_canonical_json(figure.model_dump(mode="json")),
                )
            )
    for record in records:
        try:
            memory_store.add_run_memory(record)
        except DuplicateRecordError:
            existing = memory_store.get_run_memory(record.memory_id)
            if existing.model_dump(mode="json") != record.model_dump(mode="json"):
                raise ArticleArchitectureIntegrityError(
                    f"memory record {record.memory_id!r} already exists with "
                    "different content"
                ) from None


__all__ = [
    "ARCHITECTURE_SCHEMA_VERSION",
    "ArchitectureProvider",
    "ArchitectureProviderResult",
    "ArtifactDescriptor",
    "ArtifactFieldBinding",
    "ArticleArchitectureError",
    "ArticleArchitectureIntegrityError",
    "ArticleArchitectureResult",
    "ClaimAssignment",
    "ClaimPlacement",
    "DEFAULT_MAX_TOKENS",
    "FigureContract",
    "MissingWorkHandoff",
    "MODEL_NAME",
    "OmittedClaim",
    "QwenArticleArchitecturePlanner",
    "SectionContract",
    "StoryCandidate",
    "build_architecture_payload",
    "build_article_architecture",
    "compute_architecture_id",
]
