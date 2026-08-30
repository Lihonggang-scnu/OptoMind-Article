"""Typed scientific data contracts for the Article Scientific Harness.

This module defines versioned, serializable contracts for the scientific
workflow that will eventually drive the Experiment Graph as the single
authoritative state source.  Nothing in this module changes TMM solver,
compiler, or research-orchestrator behavior; it only adds typed vocabulary
for the Article layer.

Design invariants:
- Every card is a pydantic v2 model with a literal ``schema_version``.
- Required fields reject malformed input; optional fields default to empty
  containers so they survive JSON round-trips.
- ``model_dump_json()`` output is deterministic for identical inputs.
- Unknown extra fields are ignored (forward tolerance), but unknown enum
  values, missing required fields, and mismatched schema versions fail.
- Article graph events carry ``schema_version="article-event.v1"`` and are
  validated by ``validate_article_event`` before they are persisted.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .contracts import ActionType, ExperimentStatus


# ---------------------------------------------------------------------------
# Typed enums
# ---------------------------------------------------------------------------


class ArticleStage(str, Enum):
    charter_draft = "charter_draft"
    charter_locked = "charter_locked"
    capability_classified = "capability_classified"
    literature_integrated = "literature_integrated"
    coverage_matrix_locked = "coverage_matrix_locked"
    hypotheses_formed = "hypotheses_formed"
    baseline_experiments = "baseline_experiments"
    exploration = "exploration"
    controlled_improvement = "controlled_improvement"
    discriminative_experiments = "discriminative_experiments"
    robustness_ablation = "robustness_ablation"
    hypothesis_update = "hypothesis_update"
    claim_ledger = "claim_ledger"
    figure_first_planning = "figure_first_planning"
    section_writing = "section_writing"
    fact_audit = "fact_audit"
    scientific_review = "scientific_review"
    expression_review = "expression_review"
    author_revision = "author_revision"
    fresh_replay = "fresh_replay"
    publication_package = "publication_package"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class ArticleDecision(str, Enum):
    continue_run = "continue_run"
    try_next_route = "try_next_route"
    refine_hypothesis = "refine_hypothesis"
    run_discriminative_experiment = "run_discriminative_experiment"
    run_robustness_experiment = "run_robustness_experiment"
    stop_completed = "stop_completed"
    stop_budget_exhausted = "stop_budget_exhausted"
    stop_no_progress = "stop_no_progress"
    stop_route_exhausted = "stop_route_exhausted"
    stop_capability_boundary = "stop_capability_boundary"
    stop_hard_blocker = "stop_hard_blocker"
    request_author_revision = "request_author_revision"
    request_expression_revision = "request_expression_revision"
    publish_ready = "publish_ready"


class HypothesisStatus(str, Enum):
    proposed = "proposed"
    active = "active"
    under_test = "under_test"
    partially_supported = "partially_supported"
    confirmed = "confirmed"
    refuted = "refuted"
    superseded = "superseded"
    retired = "retired"


class ClaimStatus(str, Enum):
    draft = "draft"
    evidence_bound = "evidence_bound"
    partially_supported = "partially_supported"
    supported = "supported"
    contested = "contested"
    refuted = "refuted"
    withdrawn = "withdrawn"


class ClaimStrength(str, Enum):
    unrated = "unrated"
    low = "low"
    medium = "medium"
    high = "high"


class ReviewKind(str, Enum):
    scientific = "scientific"
    expression = "expression"
    fact = "fact"
    integrity = "integrity"
    safety = "safety"


class ReviewSeverity(str, Enum):
    info = "info"
    minor = "minor"
    major = "major"
    blocking = "blocking"


class ReviewStatus(str, Enum):
    open = "open"
    resolved = "resolved"
    waived = "waived"
    blocking = "blocking"


class FigureStatus(str, Enum):
    planned = "planned"
    draft = "draft"
    generated = "generated"
    verified = "verified"
    rejected = "rejected"


class CoverageStatus(str, Enum):
    planned = "planned"
    executed = "executed"
    completed = "completed"
    failed = "failed"
    not_run = "not_run"
    superseded = "superseded"


class ArticleEventType(str, Enum):
    stage = "article.stage"
    decision = "article.decision"
    hypothesis_update = "article.hypothesis_update"
    observation = "article.observation"
    coverage = "article.coverage"
    claim = "article.claim"
    figure = "article.figure"
    section = "article.section"
    review = "article.review"
    charter = "article.charter"


# ---------------------------------------------------------------------------
# Scientific cards
# ---------------------------------------------------------------------------


class _ArticleModel(BaseModel):
    """Common base: tolerant of forward fields, strict on enums/literals."""

    model_config = ConfigDict(extra="ignore")


class ResearchCharter(_ArticleModel):
    schema_version: Literal["research-charter.v1"] = "research-charter.v1"
    charter_id: str
    question: str
    scope: str
    goals: List[str] = Field(min_length=1)
    success_criteria: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    budget: Dict[str, Any] = Field(default_factory=dict)
    deliverables: List[str] = Field(default_factory=list)
    stage: ArticleStage = ArticleStage.charter_draft
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class HypothesisCard(_ArticleModel):
    schema_version: Literal["hypothesis-card.v1"] = "hypothesis-card.v1"
    hypothesis_id: str
    statement: str
    status: HypothesisStatus = HypothesisStatus.proposed
    parent_hypothesis_id: Optional[str] = None
    evidence_ids: List[str] = Field(default_factory=list)
    experiment_ids: List[str] = Field(default_factory=list)
    updated_from_status: Optional[HypothesisStatus] = None
    update_reason: str = ""
    created_at: Optional[str] = None


class ExperimentCard(_ArticleModel):
    schema_version: Literal["experiment-card.v1"] = "experiment-card.v1"
    experiment_id: str
    hypothesis_ids: List[str] = Field(default_factory=list)
    action_type: ActionType
    task_hash: str
    stage: Optional[ArticleStage] = None
    status: ExperimentStatus = ExperimentStatus.proposed
    parent_experiment_ids: List[str] = Field(default_factory=list)
    atomic_change: Dict[str, Any] = Field(default_factory=dict)
    expected_discriminator: Dict[str, Any] = Field(default_factory=dict)
    budget_lease_id: Optional[str] = None
    artifact_ids: List[str] = Field(default_factory=list)
    created_at: Optional[str] = None


class ObservationCard(_ArticleModel):
    schema_version: Literal["observation-card.v1"] = "observation-card.v1"
    observation_id: str
    experiment_id: str
    status: ExperimentStatus
    metrics: Dict[str, Any] = Field(default_factory=dict)
    artifact_ids: List[str] = Field(default_factory=list)
    failure_records: List[Dict[str, Any]] = Field(default_factory=list)
    failure_diagnosis: Dict[str, Any] = Field(default_factory=dict)
    hypothesis_updates: List[Dict[str, Any]] = Field(default_factory=list)
    summary: str = ""
    created_at: Optional[str] = None


class ClaimCard(_ArticleModel):
    schema_version: Literal["claim-card.v1"] = "claim-card.v1"
    claim_id: str
    statement: str
    strength: ClaimStrength = ClaimStrength.unrated
    scope: str = ""
    status: ClaimStatus = ClaimStatus.draft
    evidence_ids: List[str] = Field(default_factory=list)
    counter_evidence_ids: List[str] = Field(default_factory=list)
    source_artifact_ids: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None


class FigureCard(_ArticleModel):
    schema_version: Literal["figure-card.v1"] = "figure-card.v1"
    figure_id: str
    story_role: str
    chart_spec: Dict[str, Any] = Field(default_factory=dict)
    data_source_artifact_ids: List[str] = Field(default_factory=list)
    caption: str = ""
    status: FigureStatus = FigureStatus.planned
    created_at: Optional[str] = None


class ReviewCard(_ArticleModel):
    schema_version: Literal["review-card.v1"] = "review-card.v1"
    review_id: str
    kind: ReviewKind
    severity: ReviewSeverity = ReviewSeverity.info
    findings: List[str] = Field(default_factory=list)
    decision: Optional[ArticleDecision] = None
    status: ReviewStatus = ReviewStatus.open
    claim_ids: List[str] = Field(default_factory=list)
    figure_ids: List[str] = Field(default_factory=list)
    created_at: Optional[str] = None


class CoverageRow(_ArticleModel):
    schema_version: Literal["coverage-row.v1"] = "coverage-row.v1"
    route_id: str
    title: str = ""
    coverage_status: CoverageStatus
    executed_iteration: Optional[str] = None
    evidence_artifact_ids: List[str] = Field(default_factory=list)
    not_run_reason: str = ""


class CoverageMatrix(_ArticleModel):
    schema_version: Literal["coverage-matrix.v1"] = "coverage-matrix.v1"
    matrix_id: str
    rows: List[CoverageRow] = Field(min_length=1)
    updated_at: Optional[str] = None


class ArticleNodePayload(_ArticleModel):
    """Versioned payload carried by an article node in the Experiment Graph.

    The node payload is the versioned snapshot of scientific intent; events
    record every later transition.  All fields except the schema/kind markers
    are optional so the payload remains tolerant of evolving use.
    """

    schema_version: Literal["article-node.v1"] = "article-node.v1"
    kind: Literal["article"] = "article"
    task_hash: Optional[str] = None
    branch_id: Optional[str] = None
    stage: Optional[ArticleStage] = None
    hypothesis_ids: List[str] = Field(default_factory=list)
    atomic_change: Dict[str, Any] = Field(default_factory=dict)
    expected_discriminator: Dict[str, Any] = Field(default_factory=dict)
    observation_ids: List[str] = Field(default_factory=list)
    artifact_ids: List[str] = Field(default_factory=list)
    hypothesis_update: Dict[str, Any] = Field(default_factory=dict)
    budget_lease_id: Optional[str] = None
    failure_diagnosis: Dict[str, Any] = Field(default_factory=dict)
    stop_decision: Optional[ArticleDecision] = None
    decision_reason: str = ""
    card_refs: Dict[str, List[str]] = Field(default_factory=dict)
    summary: str = ""
    created_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Article event validation
# ---------------------------------------------------------------------------

ARTICLE_EVENT_SCHEMA_VERSION = "article-event.v1"


class ArticleEventValidationError(ValueError):
    """Raised when an article event type or payload is invalid."""


class _ArticleStageEvent(_ArticleModel):
    schema_version: Literal["article-event.v1"] = "article-event.v1"
    stage: ArticleStage
    reason: str = ""


class _ArticleDecisionEvent(_ArticleModel):
    schema_version: Literal["article-event.v1"] = "article-event.v1"
    decision: ArticleDecision
    reason: str = ""


class _HypothesisUpdateEvent(_ArticleModel):
    schema_version: Literal["article-event.v1"] = "article-event.v1"
    hypothesis_id: str
    from_status: HypothesisStatus
    to_status: HypothesisStatus
    reason: str = ""


class _ObservationEvent(_ArticleModel):
    schema_version: Literal["article-event.v1"] = "article-event.v1"
    observation_id: str
    experiment_id: str = ""
    artifact_ids: List[str] = Field(default_factory=list)
    summary: str = ""


class _CoverageEvent(_ArticleModel):
    schema_version: Literal["article-event.v1"] = "article-event.v1"
    route_id: str
    coverage_status: CoverageStatus
    reason: str = ""


class _ClaimEvent(_ArticleModel):
    schema_version: Literal["article-event.v1"] = "article-event.v1"
    claim_id: str
    status: ClaimStatus = ClaimStatus.draft


class _FigureEvent(_ArticleModel):
    schema_version: Literal["article-event.v1"] = "article-event.v1"
    figure_id: str
    status: FigureStatus = FigureStatus.planned


class _SectionEvent(_ArticleModel):
    schema_version: Literal["article-event.v1"] = "article-event.v1"
    section_id: str
    status: Literal["publishable", "needs_revision", "blocked"]
    story_id: str = ""


class _ReviewEvent(_ArticleModel):
    schema_version: Literal["article-event.v1"] = "article-event.v1"
    review_id: str
    severity: ReviewSeverity = ReviewSeverity.info
    decision: Optional[ArticleDecision] = None


class _CharterEvent(_ArticleModel):
    schema_version: Literal["article-event.v1"] = "article-event.v1"
    charter_id: str
    stage: ArticleStage
    reason: str = ""


_ARTICLE_EVENT_MODELS: Dict[str, type[BaseModel]] = {
    ArticleEventType.stage.value: _ArticleStageEvent,
    ArticleEventType.decision.value: _ArticleDecisionEvent,
    ArticleEventType.hypothesis_update.value: _HypothesisUpdateEvent,
    ArticleEventType.observation.value: _ObservationEvent,
    ArticleEventType.coverage.value: _CoverageEvent,
    ArticleEventType.claim.value: _ClaimEvent,
    ArticleEventType.figure.value: _FigureEvent,
    ArticleEventType.section.value: _SectionEvent,
    ArticleEventType.review.value: _ReviewEvent,
    ArticleEventType.charter.value: _CharterEvent,
}


def validate_article_event(
    event_type: str, payload: Mapping[str, Any]
) -> Dict[str, Any]:
    """Validate an article event type/payload and return normalized JSON data.

    Raises ``ArticleEventValidationError`` for unknown event types, missing or
    mismatched schema versions, and malformed payloads.  The returned dict is
    deterministic (enum values as strings, only known fields, sortable JSON).
    """

    if not isinstance(event_type, str) or event_type not in _ARTICLE_EVENT_MODELS:
        raise ArticleEventValidationError(f"Unknown article event type: {event_type!r}")
    if not isinstance(payload, Mapping):
        raise ArticleEventValidationError(
            f"Article event payload for {event_type!r} must be a mapping"
        )
    data = dict(payload)
    version = data.get("schema_version")
    if version != ARTICLE_EVENT_SCHEMA_VERSION:
        raise ArticleEventValidationError(
            f"Article event {event_type!r} requires schema_version="
            f"{ARTICLE_EVENT_SCHEMA_VERSION!r}, got {version!r}"
        )
    try:
        model = _ARTICLE_EVENT_MODELS[event_type].model_validate(data)
    except ValidationError as exc:
        raise ArticleEventValidationError(
            f"Invalid payload for article event {event_type!r}: {exc}"
        ) from exc
    return model.model_dump(mode="json")


__all__ = [
    "ARTICLE_EVENT_SCHEMA_VERSION",
    "ArticleDecision",
    "ArticleEventType",
    "ArticleEventValidationError",
    "ArticleNodePayload",
    "ArticleStage",
    "ClaimCard",
    "ClaimStatus",
    "ClaimStrength",
    "CoverageMatrix",
    "CoverageRow",
    "CoverageStatus",
    "ExperimentCard",
    "FigureCard",
    "FigureStatus",
    "HypothesisCard",
    "HypothesisStatus",
    "ObservationCard",
    "ResearchCharter",
    "ReviewCard",
    "ReviewKind",
    "ReviewSeverity",
    "ReviewStatus",
    "validate_article_event",
]
