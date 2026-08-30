"""Stage 8: Claim Ledger and research-question completion audit.

The ledger replays Stage 7 ``ArticleFeedbackResult`` records deterministically
against an ``ArticleDirectorPlan``, validates every referenced hypothesis /
observation / experiment / artifact / route against trusted inputs, and
produces deterministic ``ClaimCard`` + immutable ``FactRecord`` pairs for
writable hypotheses.  Refuted / under-test / proposed hypotheses stay visible
as non-writable or negative state but never become active facts.

Rules:
- A writable claim requires a ``partially_supported`` or ``confirmed``
  hypothesis AND at least one real source artifact bound through validated
  trusted observations.  Artifact IDs must be a subset of the UNION of
  artifacts on the referenced observations; experiment IDs must agree with
  the resolved observations; duplicate observation IDs are rejected.  Missing
  artifacts produce a per-claim non-writable gap, never a fabricated
  ``FactRecord``.
- ``discriminator_confirmed`` evidence is revalidated against the referenced
  ObservationCard metrics (``matched is True``, declared metric keys present,
  ``physically_valid``).  Forged provenance never becomes high strength; it
  hard-blocks.
- All contributing positive evidence (partial-support plus confirmation
  rounds) is preserved in claim/fact metadata: hypothesis, observation,
  experiment, route IDs, evidence kinds, counter-evidence/limits, claim/fact
  IDs, and exact source artifacts.
- Claim/fact/ledger IDs include the plan id plus stable semantic/provenance
  content, so IDs never collide across plans.
- Scope is scientific (``ResearchCharter.scope`` plus route/experiment
  bounds); the writable FactRecord statement itself is scope-bounded.
- Qwen is optional and semantic-only: it receives bounded batches that include
  a local read-only positive claim table (writable/evidence-bound claims only)
  and may map visible goal IDs to existing positive claim IDs.  ``covered`` /
  ``partial`` with empty claim IDs is invalid (unknown).  Unavailable or
  hallucinated rows fail open individually; semantic availability reflects
  whether usable rows exist.
- Every charter goal and success criterion appears in the completion audit.
  Coverage gaps are explicit handoff assets, not blockers, unless a
  source/artifact integrity error exists.
- Optional persistence is append-only, idempotent, journal-recoverable across
  ``ArticleMemoryStore`` and ``ExperimentGraph``.  ``completed`` is written
  only after both stores finish; retries replay missing records/events without
  duplicates and reject conflicting full content.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from optomind_optics.harness.article_contracts import (
    ARTICLE_EVENT_SCHEMA_VERSION,
    ArticleDecision,
    ArticleNodePayload,
    ArticleStage,
    ClaimCard,
    ClaimStatus,
    ClaimStrength,
    HypothesisStatus,
    ObservationCard,
    validate_article_event,
)
from optomind_optics.harness.article_director import ArticleDirectorPlan
from optomind_optics.harness.article_feedback import (
    ArticleFeedbackResult,
    HypothesisUpdateDecision,
    _LEGAL_TRANSITIONS,
)
from optomind_optics.harness.article_memory import (
    ArticleMemoryStore,
    DuplicateRecordError,
    FactRecord,
    FactStatus,
    RunMemoryRecord,
)
from optomind_optics.harness.experiment_graph import ExperimentGraph
from optomind_research.runtime.artifact_store import atomic_write_json


CLAIM_LEDGER_SCHEMA_VERSION = "article-claim-ledger.v1"
COMPLETION_AUDIT_SCHEMA_VERSION = "article-completion-audit.v1"
COVERAGE_BATCH_SIZE = 20
ALLOWED_COVERAGE_LEVELS = frozenset(
    {"covered", "partial", "gap", "unknown", "not_applicable"}
)
POSITIVE_CLAIM_STATUSES = frozenset(
    {ClaimStatus.partially_supported, ClaimStatus.supported}
)


class ClaimLedgerError(ValueError):
    """Base error for claim-ledger failures."""


class ClaimIntegrityError(ClaimLedgerError):
    """Raised for unknown/mismatched provenance or conflicting content."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CompletionAuditRow(_StrictModel):
    schema_version: Literal["completion-audit-row.v1"] = "completion-audit-row.v1"
    goal_id: str
    goal_label: str
    kind: Literal["goal", "success_criterion"]
    coverage: Literal["covered", "partial", "gap", "unknown", "not_applicable"]
    claim_ids: List[str] = Field(default_factory=list)
    unique_contribution: str
    expected_value_of_more_work: str
    stop_reason: str
    rationale: str


class ArticleCompletionAudit(_StrictModel):
    schema_version: Literal["article-completion-audit.v1"] = (
        "article-completion-audit.v1"
    )
    audit_id: str
    rows: List[CompletionAuditRow]
    semantic_coverage_available: bool
    semantic_warnings: List[str] = Field(default_factory=list)


class ClaimLedgerResult(_StrictModel):
    schema_version: Literal["article-claim-ledger.v1"] = "article-claim-ledger.v1"
    ledger_id: str
    claims: List[ClaimCard]
    facts: List[FactRecord]
    audit: ArticleCompletionAudit
    validation_errors: List[str] = Field(default_factory=list)
    normalization_warnings: List[str] = Field(default_factory=list)
    semantic_coverage_available: bool = False
    source_plan_id: Optional[str] = None


SemanticCoverageProvider = Callable[
    [Sequence[Mapping[str, Any]]], Sequence[Mapping[str, Any]]
]


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


def _evidence_kind(decision: HypothesisUpdateDecision) -> str:
    summary = str(decision.evidence_summary or "")
    if summary.startswith("evidence_kind="):
        return summary.split("=", 1)[1]
    return "unknown"


def _unique_texts(values: Sequence[Any]) -> List[str]:
    result: List[str] = []
    for item in values:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def replay_hypothesis_states(
    plan: ArticleDirectorPlan,
    feedback_results: Sequence[ArticleFeedbackResult],
    errors: List[str],
) -> Dict[str, HypothesisStatus]:
    """Deterministically replay Stage 7 hypothesis updates in order."""

    current = {
        item.hypothesis_id: HypothesisStatus.proposed
        for item in plan.hypotheses
    }
    for index, result in enumerate(feedback_results):
        for update in result.hypothesis_updates:
            if update.hypothesis_id not in current:
                errors.append(
                    f"feedback[{index}] references unknown hypothesis "
                    f"{update.hypothesis_id!r}"
                )
                continue
            if update.from_status != current[update.hypothesis_id]:
                errors.append(
                    f"feedback[{index}] hypothesis {update.hypothesis_id!r} "
                    f"from_status {update.from_status.value!r} does not match "
                    f"replayed state {current[update.hypothesis_id].value!r}"
                )
                continue
            if update.to_status != update.from_status:
                allowed = _LEGAL_TRANSITIONS.get(update.from_status, frozenset())
                if update.to_status not in allowed:
                    errors.append(
                        f"feedback[{index}] hypothesis {update.hypothesis_id!r} "
                        f"illegal transition "
                        f"{update.from_status.value} -> {update.to_status.value}"
                    )
                    continue
            current[update.hypothesis_id] = update.to_status
    return current


def _validate_provenance(
    plan: ArticleDirectorPlan,
    feedback_results: Sequence[ArticleFeedbackResult],
    observations: Sequence[ObservationCard],
    errors: List[str],
) -> Dict[str, List[Dict[str, Any]]]:
    """Validate provenance and derive authoritative observation provenance.

    Returns per-hypothesis validated evidence records with authoritative
    observation/experiment IDs and union artifact IDs derived from the
    resolved ObservationCards.
    """

    observation_map = {item.observation_id: item for item in observations}
    coverage_ids = {row.route_id for row in plan.coverage_matrix.rows}
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for result_index, result in enumerate(feedback_results):
        for update in result.hypothesis_updates:
            evidence_kind = _evidence_kind(update)
            observation_ids = [str(item) for item in update.observation_ids]
            duplicates = [
                item for item, count in Counter(observation_ids).items() if count > 1
            ]
            if duplicates:
                errors.append(
                    f"feedback[{result_index}] hypothesis {update.hypothesis_id!r} "
                    f"has duplicate observation IDs: {duplicates}"
                )
            if update.artifact_ids and not observation_ids:
                errors.append(
                    f"feedback[{result_index}] hypothesis {update.hypothesis_id!r} "
                    "has artifact_ids but no observation_ids"
                )
            resolved = []
            for observation_id in observation_ids:
                observation = observation_map.get(observation_id)
                if observation is None:
                    errors.append(
                        f"feedback[{result_index}] hypothesis "
                        f"{update.hypothesis_id!r} references unknown observation "
                        f"{observation_id!r}"
                    )
                    continue
                resolved.append(observation)
            authoritative_experiments = sorted(
                {item.experiment_id for item in resolved}
            )
            if update.experiment_ids and set(update.experiment_ids) != set(
                authoritative_experiments
            ):
                errors.append(
                    f"feedback[{result_index}] hypothesis {update.hypothesis_id!r} "
                    "experiment IDs do not agree with resolved observations"
                )
            union_artifacts = sorted(
                {artifact for item in resolved for artifact in item.artifact_ids}
            )
            unknown_artifacts = sorted(set(update.artifact_ids) - set(union_artifacts))
            if unknown_artifacts:
                errors.append(
                    f"feedback[{result_index}] hypothesis {update.hypothesis_id!r} "
                    f"artifacts {unknown_artifacts} are not in the union of "
                    "referenced observation artifacts"
                )
            for route_id in update.route_ids:
                if route_id and route_id not in coverage_ids:
                    errors.append(
                        f"feedback[{result_index}] hypothesis "
                        f"{update.hypothesis_id!r} references unknown route "
                        f"{route_id!r}"
                    )
            grouped.setdefault(update.hypothesis_id, []).append(
                {
                    "decision": update,
                    "evidence_kind": evidence_kind,
                    "observation_ids": sorted(set(observation_ids)),
                    "experiment_ids": authoritative_experiments,
                    "artifact_ids": union_artifacts,
                    "route_ids": sorted(
                        {item for item in update.route_ids if item}
                    ),
                }
            )
    return grouped


def _revalidate_discriminator(
    plan: ArticleDirectorPlan,
    grouped: Mapping[str, Sequence[Mapping[str, Any]]],
    observations: Sequence[ObservationCard],
    errors: List[str],
) -> None:
    """Revalidate discriminator_confirmed against trusted observation metrics."""

    observation_map = {item.observation_id: item for item in observations}
    for hypothesis_id, records in grouped.items():
        for record in records:
            if record["evidence_kind"] != "discriminator_confirmed":
                continue
            for observation_id in record["observation_ids"]:
                observation = observation_map.get(observation_id)
                if observation is None:
                    errors.append(
                        f"forged discriminator_confirmed provenance for "
                        f"hypothesis {hypothesis_id!r}: unknown observation "
                        f"{observation_id!r}"
                    )
                    continue
                metrics = (
                    observation.metrics
                    if isinstance(observation.metrics, Mapping)
                    else {}
                )
                if observation.status.value != "physically_valid":
                    errors.append(
                        f"forged discriminator_confirmed provenance for "
                        f"hypothesis {hypothesis_id!r}: observation "
                        f"{observation_id!r} is not physically valid"
                    )
                    continue
                discriminator_map = metrics.get("discriminator_match")
                discriminator = (
                    discriminator_map.get(hypothesis_id)
                    if isinstance(discriminator_map, Mapping)
                    else None
                )
                if not isinstance(discriminator, Mapping) or discriminator.get(
                    "matched"
                ) is not True:
                    errors.append(
                        f"forged discriminator_confirmed provenance for "
                        f"hypothesis {hypothesis_id!r}: discriminator not "
                        "represented as matched in observation metrics"
                    )
                    continue
                metric_keys = discriminator.get("metric_keys") or []
                if not metric_keys or not all(
                    key in metrics for key in metric_keys
                ):
                    errors.append(
                        f"forged discriminator_confirmed provenance for "
                        f"hypothesis {hypothesis_id!r}: discriminator metric "
                        "keys not present in observation metrics"
                    )


def _strength_for(
    status: HypothesisStatus,
    evidence_kinds: Sequence[str],
    charter_scope: str,
    scope: str,
) -> ClaimStrength:
    if status == HypothesisStatus.confirmed:
        if (
            charter_scope.strip()
            and scope
            and "discriminator_confirmed" in evidence_kinds
        ):
            return ClaimStrength.high
        return ClaimStrength.medium
    if status == HypothesisStatus.partially_supported:
        return ClaimStrength.medium
    return ClaimStrength.unrated


def _derived_limits(
    plan: ArticleDirectorPlan, hypothesis: Any
) -> List[str]:
    parts = list(plan.charter.constraints)
    risk = str(getattr(hypothesis, "risk_notes", "") or "").strip()
    if risk:
        parts.append(risk)
    parts.extend(plan.capability.accepted_assumptions)
    return _unique_texts(parts)


def _claim_status_for(status: HypothesisStatus, writable: bool) -> ClaimStatus:
    if writable:
        if status == HypothesisStatus.confirmed:
            return ClaimStatus.supported
        return ClaimStatus.partially_supported
    if status == HypothesisStatus.refuted:
        return ClaimStatus.refuted
    if status in {HypothesisStatus.superseded, HypothesisStatus.retired}:
        return ClaimStatus.withdrawn
    return ClaimStatus.draft


def _scientific_scope(
    plan: ArticleDirectorPlan,
    route_ids: Sequence[str],
    experiment_ids: Sequence[str],
) -> str:
    return (
        f"{plan.charter.scope} | routes: {', '.join(route_ids) or 'unspecified'} | "
        f"experiments: {', '.join(experiment_ids) or 'unspecified'}"
    )


def _build_claims_and_facts(
    plan: ArticleDirectorPlan,
    current: Mapping[str, HypothesisStatus],
    grouped: Mapping[str, Sequence[Mapping[str, Any]]],
    warnings: List[str],
) -> Tuple[List[ClaimCard], List[FactRecord]]:
    claims: List[ClaimCard] = []
    facts: List[FactRecord] = []
    charter_scope = str(plan.charter.scope or "").strip()
    for hypothesis in plan.hypotheses:
        status = current[hypothesis.hypothesis_id]
        records = list(grouped.get(hypothesis.hypothesis_id, ()))
        contributing = [
            record
            for record in records
            if record["decision"].to_status
            in {HypothesisStatus.partially_supported, HypothesisStatus.confirmed}
            and record["decision"].to_status != record["decision"].from_status
            and record["observation_ids"]
        ]
        observation_ids = sorted(
            {item for record in contributing for item in record["observation_ids"]}
        )
        experiment_ids = sorted(
            {item for record in contributing for item in record["experiment_ids"]}
        )
        route_ids = sorted(
            {item for record in contributing for item in record["route_ids"]}
        )
        evidence_kinds = sorted(
            {record["evidence_kind"] for record in contributing}
        )
        bound_artifacts = sorted(
            {item for record in contributing for item in record["artifact_ids"]}
        )
        writable = (
            status in {HypothesisStatus.partially_supported, HypothesisStatus.confirmed}
            and bool(bound_artifacts)
            and bool(observation_ids)
        )
        counter_evidence_ids = sorted(
            {
                observation_id
                for record in records
                if record["decision"].to_status == HypothesisStatus.refuted
                for observation_id in record["observation_ids"]
            }
        )
        refuted_records = [
            record
            for record in records
            if record["decision"].to_status == HypothesisStatus.refuted
        ]
        refuted_observations = sorted(
            {
                observation_id
                for record in refuted_records
                for observation_id in record["observation_ids"]
            }
        )
        refuted_experiments = sorted(
            {
                experiment_id
                for record in refuted_records
                for experiment_id in record["experiment_ids"]
            }
        )
        refuted_artifacts = sorted(
            {
                artifact_id
                for record in refuted_records
                for artifact_id in record["artifact_ids"]
            }
        )
        limits = _derived_limits(plan, hypothesis)
        scope = _scientific_scope(plan, route_ids, experiment_ids)
        refutation_salt = sorted(
            refuted_observations + refuted_experiments + refuted_artifacts
        )
        claim_id = f"claim-{_digest(plan.plan_id, hypothesis.hypothesis_id, hypothesis.statement, status.value, scope, bound_artifacts, refutation_salt)}"
        fact_id = f"fact-{_digest(plan.plan_id, hypothesis.hypothesis_id, hypothesis.statement, status.value, scope, bound_artifacts, refutation_salt, 'fact')}"
        if not writable and status in {
            HypothesisStatus.partially_supported,
            HypothesisStatus.confirmed,
        }:
            warnings.append(
                f"hypothesis {hypothesis.hypothesis_id} is {status.value} but has "
                "no source artifact; claim is non-writable"
            )
        metadata: Dict[str, Any] = {
            "hypothesis_id": hypothesis.hypothesis_id,
            "observation_ids": observation_ids,
            "experiment_ids": experiment_ids,
            "route_ids": route_ids,
            "evidence_kinds": evidence_kinds,
            "scope": scope,
            "limits": limits,
            "counterevidence": counter_evidence_ids,
            "counter_evidence_provenance": {
                "observation_ids": refuted_observations,
                "experiment_ids": refuted_experiments,
                "artifact_ids": refuted_artifacts,
            },
            "claim_id": claim_id,
            "fact_id": fact_id if writable else None,
            "writable": writable,
            "negative_state": status.value if not writable else None,
        }
        if status == HypothesisStatus.refuted:
            source_artifacts = refuted_artifacts
        elif writable:
            source_artifacts = list(bound_artifacts)
        else:
            source_artifacts = []
        claims.append(
            ClaimCard(
                claim_id=claim_id,
                statement=hypothesis.statement,
                strength=_strength_for(
                    status, evidence_kinds, charter_scope, scope
                ),
                scope=scope,
                status=_claim_status_for(status, writable),
                evidence_ids=observation_ids,
                counter_evidence_ids=counter_evidence_ids,
                source_artifact_ids=source_artifacts,
                metadata=metadata,
            )
        )
        if writable:
            facts.append(
                FactRecord(
                    fact_id=fact_id,
                    statement=(
                        f"{hypothesis.statement} — scope: {scope}"
                    ),
                    source_artifact_ids=list(bound_artifacts),
                    status=FactStatus.active,
                    metadata={
                        "hypothesis_id": hypothesis.hypothesis_id,
                        "claim_id": claim_id,
                        "observation_ids": observation_ids,
                        "experiment_ids": experiment_ids,
                        "route_ids": route_ids,
                        "evidence_kinds": evidence_kinds,
                        "source_artifact_ids": list(bound_artifacts),
                        "scope": scope,
                        "limits": limits,
                        "counterevidence": counter_evidence_ids,
                    },
                )
            )
    return claims, facts


def _positive_claim_table(claims: Sequence[ClaimCard]) -> List[Dict[str, Any]]:
    """Local read-only positive claim table for the semantic provider."""

    return [
        {
            "claim_id": claim.claim_id,
            "statement": claim.statement,
            "scope": claim.scope,
            "status": claim.status.value,
            "strength": claim.strength.value,
            "source_count": len(claim.source_artifact_ids),
        }
        for claim in claims
        if claim.status in POSITIVE_CLAIM_STATUSES and claim.source_artifact_ids
    ]


def build_coverage_batches(
    plan: ArticleDirectorPlan,
    claims: Sequence[ClaimCard],
    *,
    batch_size: int = COVERAGE_BATCH_SIZE,
) -> List[Dict[str, Any]]:
    """Locally prepared bounded batches; nothing is silently truncated."""

    positive_claims = _positive_claim_table(claims)
    positive_claim_ids = [item["claim_id"] for item in positive_claims]
    goals: List[Dict[str, Any]] = []
    for index, goal in enumerate(plan.charter.goals, start=1):
        goals.append(
            {
                "goal_id": f"goal-{index:02d}",
                "label": goal,
                "kind": "goal",
                "allowed_positive_claim_ids": positive_claim_ids,
                "allowed_coverage_levels": sorted(ALLOWED_COVERAGE_LEVELS),
            }
        )
    for index, criterion in enumerate(plan.charter.success_criteria, start=1):
        goals.append(
            {
                "goal_id": f"criterion-{index:02d}",
                "label": criterion,
                "kind": "success_criterion",
                "allowed_positive_claim_ids": positive_claim_ids,
                "allowed_coverage_levels": sorted(ALLOWED_COVERAGE_LEVELS),
            }
        )
    return [
        {
            "task": "Map charter goals/success criteria to existing positive "
            "claim IDs with a coverage level and concise rationale.",
            "batch_index": index,
            "batch_count": (len(goals) + batch_size - 1) // batch_size,
            "question": plan.charter.question,
            "charter_scope": plan.charter.scope,
            "claims": positive_claims,
            "goals": goals[offset : offset + batch_size],
        }
        for index, offset in enumerate(
            range(0, len(goals), batch_size), start=1
        )
    ]


def apply_semantic_coverage_batch(
    plan: ArticleDirectorPlan,
    claims: Sequence[ClaimCard],
    batch_payload: Mapping[str, Any],
    response: Any,
    warnings: List[str],
) -> List[CompletionAuditRow]:
    """Validate one semantic response; invalid/hallucinated rows fail open."""

    positive_ids = {
        item["claim_id"] for item in _positive_claim_table(claims)
    }
    rows: List[CompletionAuditRow] = []
    if not isinstance(response, Mapping):
        warnings.append("semantic coverage response for a batch is not an object")
        for item in batch_payload.get("goals") or []:
            rows.append(_unknown_audit_row(plan, item["goal_id"], item["label"], item["kind"]))
        return rows
    mapping = response.get("goals") or response
    if not isinstance(mapping, Mapping):
        warnings.append("semantic coverage response has no goal mapping")
        for item in batch_payload.get("goals") or []:
            rows.append(_unknown_audit_row(plan, item["goal_id"], item["label"], item["kind"]))
        return rows
    for item in batch_payload.get("goals") or []:
        goal_id = str(item["goal_id"])
        label = str(item["label"])
        kind = str(item["kind"])
        entry = mapping.get(goal_id)
        if not isinstance(entry, Mapping):
            warnings.append(f"semantic coverage missing goal {goal_id}")
            rows.append(_unknown_audit_row(plan, goal_id, label, kind))
            continue
        claim_ids = [str(value) for value in (entry.get("claim_ids") or [])]
        unknown_claims = sorted(set(claim_ids) - positive_ids)
        coverage = str(entry.get("coverage") or "unknown")
        if coverage not in ALLOWED_COVERAGE_LEVELS:
            warnings.append(f"semantic coverage invalid level for goal {goal_id}")
            coverage = "unknown"
        if unknown_claims:
            warnings.append(
                f"semantic coverage for goal {goal_id} cited unknown claim IDs: "
                f"{unknown_claims}"
            )
            coverage = "unknown"
            claim_ids = []
        if coverage in {"covered", "partial"} and not claim_ids:
            warnings.append(
                f"semantic coverage {coverage} for goal {goal_id} requires claim_ids"
            )
            coverage = "unknown"
        rows.append(
            _completed_audit_row(
                plan,
                claims,
                goal_id,
                label,
                kind,
                coverage,
                claim_ids,
                entry,
            )
        )
    return rows


def _completed_audit_row(
    plan: ArticleDirectorPlan,
    claims: Sequence[ClaimCard],
    goal_id: str,
    label: str,
    kind: str,
    coverage: str,
    claim_ids: Sequence[str],
    entry: Mapping[str, Any],
) -> CompletionAuditRow:
    contribution = str(entry.get("unique_contribution") or "").strip()
    if not contribution and coverage != "unknown":
        contribution = (
            f"claim ledger produced {len(_positive_claim_table(claims))} "
            "source-bound claims"
        )
    rationale = str(entry.get("rationale") or "").strip()
    if not rationale and coverage != "unknown":
        rationale = f"semantic coverage level {coverage} for goal {goal_id}"
    return CompletionAuditRow(
        goal_id=goal_id,
        goal_label=label,
        kind=kind,
        coverage=coverage,
        claim_ids=sorted(set(claim_ids)),
        unique_contribution=contribution,
        expected_value_of_more_work=str(
            entry.get("expected_value_of_more_work")
            or entry.get("missing_work")
            or "unknown"
        ),
        stop_reason=str(entry.get("stop_reason") or "no stop reason"),
        rationale=rationale,
    )


def _unknown_audit_row(
    plan: ArticleDirectorPlan,
    goal_id: str,
    label: str,
    kind: str,
) -> CompletionAuditRow:
    return CompletionAuditRow(
        goal_id=goal_id,
        goal_label=label,
        kind=kind,
        coverage="unknown",
        unique_contribution="claim ledger produced source-bound claims",
        expected_value_of_more_work="unknown",
        stop_reason="semantic coverage unavailable",
        rationale="semantic coverage layer unavailable; deterministic claims unaffected",
    )


def audit_completion(
    plan: ArticleDirectorPlan,
    claims: Sequence[ClaimCard],
    *,
    semantic_rows: Optional[Sequence[CompletionAuditRow]] = None,
    semantic_warnings: Optional[Sequence[str]] = None,
) -> ArticleCompletionAudit:
    """Audit every charter goal and success criterion."""

    warnings = list(semantic_warnings or [])
    row_map: Dict[str, CompletionAuditRow] = {}
    if semantic_rows is not None:
        row_map = {row.goal_id: row for row in semantic_rows}
    rows: List[CompletionAuditRow] = []
    for index, goal in enumerate(plan.charter.goals, start=1):
        goal_id = f"goal-{index:02d}"
        rows.append(row_map.get(goal_id) or _unknown_audit_row(plan, goal_id, goal, "goal"))
    for index, criterion in enumerate(plan.charter.success_criteria, start=1):
        goal_id = f"criterion-{index:02d}"
        rows.append(
            row_map.get(goal_id)
            or _unknown_audit_row(plan, goal_id, criterion, "success_criterion")
        )
    available = bool(semantic_rows) and any(
        row.coverage != "unknown" for row in rows
    )
    audit_id = f"audit-{_digest(plan.plan_id, [_canonical_json(row.model_dump(mode='json')) for row in rows], available)}"
    return ArticleCompletionAudit(
        audit_id=audit_id,
        rows=rows,
        semantic_coverage_available=available,
        semantic_warnings=warnings,
    )


def build_claim_ledger(
    plan: ArticleDirectorPlan | Mapping[str, Any],
    feedback_results: Sequence[ArticleFeedbackResult | Mapping[str, Any]],
    observations: Sequence[ObservationCard | Mapping[str, Any]],
    *,
    semantic_provider: Optional[SemanticCoverageProvider] = None,
    memory_store: ArticleMemoryStore | None = None,
    graph: ExperimentGraph | None = None,
    run_id: Optional[str] = None,
    journal_path: str | Path | None = None,
) -> ClaimLedgerResult:
    """Deterministically build the claim ledger and completion audit."""

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
        return _hard_blocker(errors, warnings, ledger_id="invalid-plan")
    feedback_models: List[ArticleFeedbackResult] = []
    for index, raw in enumerate(feedback_results):
        try:
            item = (
                raw
                if isinstance(raw, ArticleFeedbackResult)
                else ArticleFeedbackResult.model_validate(raw)
            )
        except ValidationError as exc:
            errors.append(f"feedback_results[{index}] is invalid: {exc}")
            continue
        if item.validation_errors or item.stop_decision == ArticleDecision.stop_hard_blocker:
            errors.append(
                f"feedback_results[{index}] is not trusted ledger input "
                "(validation errors or hard blocker)"
            )
            continue
        feedback_models.append(item)
    observation_models: List[ObservationCard] = []
    for index, raw in enumerate(observations):
        try:
            item = (
                raw
                if isinstance(raw, ObservationCard)
                else ObservationCard.model_validate(raw)
            )
        except ValidationError as exc:
            errors.append(f"observations[{index}] is invalid: {exc}")
            continue
        observation_models.append(item)
    duplicate_input_observations = [
        item
        for item, count in Counter(
            [item.observation_id for item in observation_models]
        ).items()
        if count > 1
    ]
    if duplicate_input_observations:
        errors.append(
            f"duplicate ObservationCard IDs in input: "
            f"{duplicate_input_observations}"
        )
    if errors:
        return _hard_blocker(errors, warnings, ledger_id="invalid-input")

    current = replay_hypothesis_states(plan_model, feedback_models, errors)
    grouped = _validate_provenance(
        plan_model, feedback_models, observation_models, errors
    )
    _revalidate_discriminator(plan_model, grouped, observation_models, errors)
    if errors:
        return _hard_blocker(errors, warnings, ledger_id=_digest(plan_model.plan_id))

    claims, facts = _build_claims_and_facts(
        plan_model, current, grouped, warnings
    )
    semantic_rows: Optional[List[CompletionAuditRow]] = None
    semantic_available = False
    semantic_warnings: List[str] = []
    if semantic_provider is not None:
        try:
            batches = build_coverage_batches(plan_model, claims)
            responses = list(semantic_provider(batches))
            if len(responses) != len(batches):
                semantic_warnings.append(
                    f"semantic provider returned {len(responses)} responses for "
                    f"{len(batches)} batches; coverage marked unavailable"
                )
            else:
                semantic_rows = []
                for batch, response in zip(batches, responses):
                    semantic_rows.extend(
                        apply_semantic_coverage_batch(
                            plan_model, claims, batch, response, semantic_warnings
                        )
                    )
        except Exception as exc:
            semantic_warnings.append(f"semantic provider unavailable: {exc}")

    audit = audit_completion(
        plan_model,
        claims,
        semantic_rows=semantic_rows,
        semantic_warnings=semantic_warnings,
    )
    semantic_available = audit.semantic_coverage_available
    ledger_id = _digest(
        plan_model.plan_id,
        [item.controller_id for item in feedback_models],
        [item.observation_id for item in observation_models],
        {key: value.value for key, value in sorted(current.items())},
        [claim.claim_id for claim in claims],
        [fact.fact_id for fact in facts],
    )
    result = ClaimLedgerResult(
        ledger_id=ledger_id,
        claims=claims,
        facts=facts,
        audit=audit,
        normalization_warnings=warnings,
        semantic_coverage_available=semantic_available,
        source_plan_id=plan_model.plan_id,
    )
    if memory_store is not None or graph is not None or journal_path is not None:
        _persist(
            ledger_id=ledger_id,
            claims=claims,
            facts=facts,
            audit=audit,
            memory_store=memory_store,
            graph=graph,
            run_id=str(run_id or ""),
            journal_path=journal_path,
        )
    return result


def _hard_blocker(
    errors: Sequence[str], warnings: Sequence[str], *, ledger_id: str
) -> ClaimLedgerResult:
    return ClaimLedgerResult(
        ledger_id=ledger_id,
        claims=[],
        facts=[],
        audit=ArticleCompletionAudit(
            audit_id=f"audit-{_digest(ledger_id)}",
            rows=[],
            semantic_coverage_available=False,
        ),
        validation_errors=[str(item) for item in errors],
        normalization_warnings=[str(item) for item in warnings],
    )


def _read_journal(path: str | Path) -> Dict[str, Any]:
    journal_path = Path(path)
    if not journal_path.exists():
        return {}
    try:
        payload = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ClaimLedgerError(f"claim journal is unreadable: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ClaimLedgerError("claim journal must be a JSON object")
    return {
        str(key): dict(value)
        for key, value in payload.items()
        if isinstance(value, Mapping)
    }


def _write_journal(
    path: str | Path,
    journal: Mapping[str, Any],
    ledger_id: str,
    state: Mapping[str, Any],
) -> None:
    payload = dict(journal)
    payload[str(ledger_id)] = dict(state)
    atomic_write_json(Path(path), payload)


def _persist(
    *,
    ledger_id: str,
    claims: Sequence[ClaimCard],
    facts: Sequence[FactRecord],
    audit: ArticleCompletionAudit,
    memory_store: Optional[ArticleMemoryStore],
    graph: Optional[ExperimentGraph],
    run_id: str,
    journal_path: Optional[str | Path],
) -> None:
    if journal_path is None:
        if graph is not None:
            _persist_graph(graph, ledger_id, claims)
        if memory_store is not None:
            _persist_memory(memory_store, ledger_id, claims, facts, audit, run_id)
        return
    journal = _read_journal(journal_path)
    state = journal.get(ledger_id)
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
            _persist_graph(graph, ledger_id, claims)
            state["graph_written"] = True
            _write_journal(journal_path, journal, ledger_id, state)
        if memory_store is not None and not state.get("memory_written"):
            _persist_memory(memory_store, ledger_id, claims, facts, audit, run_id)
            state["memory_written"] = True
            _write_journal(journal_path, journal, ledger_id, state)
        state["status"] = "completed"
        _write_journal(journal_path, journal, ledger_id, state)
    except Exception as exc:
        _write_journal(journal_path, journal, ledger_id, state)
        raise ClaimLedgerError(f"claim persistence failed: {exc}") from exc


def _expected_claim_events(
    claims: Sequence[ClaimCard],
) -> List[Tuple[str, Mapping[str, Any]]]:
    return [
        (
            "article.claim",
            validate_article_event(
                "article.claim",
                {
                    "schema_version": ARTICLE_EVENT_SCHEMA_VERSION,
                    "claim_id": claim.claim_id,
                    "status": claim.status.value,
                },
            ),
        )
        for claim in claims
    ]


def _persist_graph(
    graph: ExperimentGraph,
    ledger_id: str,
    claims: Sequence[ClaimCard],
) -> None:
    node_id = f"claims-{ledger_id}"
    summary = f"claims-{ledger_id}"
    payload = ArticleNodePayload(
        stage=ArticleStage.claim_ledger,
        hypothesis_ids=[
            str(claim.metadata.get("hypothesis_id") or "")
            for claim in claims
            if claim.metadata.get("hypothesis_id")
        ],
        summary=summary,
    )
    expected_events = _expected_claim_events(claims)
    created = False
    try:
        graph.create_article_node(payload, node_id=node_id)
        created = True
    except sqlite3.IntegrityError:
        existing = graph.article_node(node_id)
        if existing.get("payload", {}).get("summary") != summary:
            raise ClaimIntegrityError(
                f"claims node {node_id!r} already exists with different content"
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
        if item["event_type"] == "article.claim":
            identity = f"claim:{item['payload'].get('claim_id')}"
            by_identity[identity] = (
                item["event_type"],
                _canonical_json(item["payload"]),
            )
    for event_type, event_payload in expected_events:
        canonical = _canonical_json(event_payload)
        identity = f"claim:{event_payload.get('claim_id')}"
        if identity in by_identity and by_identity[identity] != (event_type, canonical):
            raise ClaimIntegrityError(
                f"claims node {node_id!r} has conflicting claim event for {identity}"
            )
        if (event_type, canonical) in seen:
            continue
        graph.record_article_event(node_id, event_type, event_payload)
        seen.add((event_type, canonical))
        by_identity[identity] = (event_type, canonical)


def _persist_memory(
    memory_store: ArticleMemoryStore,
    ledger_id: str,
    claims: Sequence[ClaimCard],
    facts: Sequence[FactRecord],
    audit: ArticleCompletionAudit,
    run_id: str,
) -> None:
    for fact in facts:
        try:
            memory_store.add_fact(fact)
        except DuplicateRecordError:
            existing = memory_store.get_fact(fact.fact_id)
            if existing.model_dump(mode="json") != fact.model_dump(mode="json"):
                raise ClaimIntegrityError(
                    f"fact {fact.fact_id!r} already exists with different content"
                ) from None
    for claim in claims:
        record = RunMemoryRecord(
            memory_id=f"claim-{claim.claim_id}",
            run_id=run_id,
            event_type="article_claim_ledger",
            graph_node_id=f"claims-{ledger_id}",
            artifact_ids=list(claim.source_artifact_ids),
            operational_note=_canonical_json(claim.model_dump(mode="json")),
        )
        try:
            memory_store.add_run_memory(record)
        except DuplicateRecordError:
            existing = memory_store.get_run_memory(record.memory_id)
            if existing.model_dump(mode="json") != record.model_dump(mode="json"):
                raise ClaimIntegrityError(
                    f"claim memory record {record.memory_id!r} already exists "
                    "with different content"
                ) from None
    audit_record = RunMemoryRecord(
        memory_id=f"audit-{audit.audit_id}",
        run_id=run_id,
        event_type="article_completion_audit",
        graph_node_id=f"claims-{ledger_id}",
        artifact_ids=[],
        operational_note=_canonical_json(audit.model_dump(mode="json")),
    )
    try:
        memory_store.add_run_memory(audit_record)
    except DuplicateRecordError:
        existing = memory_store.get_run_memory(audit_record.memory_id)
        if existing.model_dump(mode="json") != audit_record.model_dump(mode="json"):
            raise ClaimIntegrityError(
                f"audit memory record {audit_record.memory_id!r} already exists "
                "with different content"
            ) from None


__all__ = [
    "ALLOWED_COVERAGE_LEVELS",
    "CLAIM_LEDGER_SCHEMA_VERSION",
    "COVERAGE_BATCH_SIZE",
    "COMPLETION_AUDIT_SCHEMA_VERSION",
    "ArticleCompletionAudit",
    "ClaimIntegrityError",
    "ClaimLedgerError",
    "ClaimLedgerResult",
    "CompletionAuditRow",
    "SemanticCoverageProvider",
    "apply_semantic_coverage_batch",
    "audit_completion",
    "build_claim_ledger",
    "build_coverage_batches",
    "replay_hypothesis_states",
]
