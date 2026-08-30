"""Post-experiment result-claim synthesis bridge for Article assets.

Stage 6.5/7 boundary helper: converts verified ``ArticleAssetCompilationResult``
records plus their matching experiment-planning rows into source-bound writable
findings, a derived ``ArticleDirectorPlan`` with result-grounded hypotheses,
enriched ``ObservationCard`` records, an ``ArticleFeedbackResult``, and a
``ClaimLedgerResult``.  It never rewrites existing validation and never claims
confirmation: synthesized findings enter only as ``partial_support`` with a
medium ceiling.

Trust boundaries:
- Local code owns IDs, schemas, aliases, statuses, plan identity, observation
  updates, warnings/errors, and orchestration.  The provider fills only the
  small semantic finding form.
- Source/identity integrity (join ambiguity, mismatched request/task/proposal,
  unknown or cross-route aliases, invented numeric values, unsupported
  provenance) fails closed per finding and per join.
- Provider unavailability or malformed rows fail open per route: valid sibling
  findings survive and no fabricated facts are written.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
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

from optomind_optics.harness.article_claims import (
    ClaimLedgerResult,
    build_claim_ledger,
)
from optomind_optics.harness.article_contracts import (
    ArticleStage,
    ObservationCard,
)
from optomind_optics.harness.article_director import (
    ArticleDirectorPlan,
    CoverageMatrix,
    EvidenceIdentityManifest,
    HypothesisCandidate,
)
from optomind_optics.harness.article_literature import (
    build_literature_provider_context,
)
from optomind_optics.harness.article_experiment_planning import (
    ArticleExperimentPlanningResult,
    PlannedRouteResult,
)
from optomind_optics.harness.article_feedback import (
    ArticleFeedbackController,
    ArticleFeedbackResult,
)
from optomind_optics.harness.article_assets import ArticleAssetCompilationResult
from optomind_optics.harness.qwen_policy import QWEN_POLICY_MODEL, QwenFlashOnlyClient
from optomind_research.runtime.cost_ledger import estimate_call_cost_cny


RESULT_SYNTHESIS_SCHEMA_VERSION = "article-result-synthesis.v1"
SYNTHESIS_FINDING_SCHEMA_VERSION = "synthesis-finding.v1"
PROVIDER_INPUT_SCHEMA_VERSION = "result-synthesis-provider-input.v1"
PROVIDER_RESULT_SCHEMA_VERSION = "result-synthesis-provider-result.v1"
SYNTHESIS_MODEL_NAME = QWEN_POLICY_MODEL

DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "optical_harness"
    / "Article Result Claim Synthesizer.txt"
)

FINDING_ROLES = frozenset({"method", "result", "limitation", "robustness"})
_SCORE_ROLE_BY_FIELD = {
    "target_score": "best_target_score",
    "simplicity_score": "simplest_fabrication",
    "robustness_score": "most_robust",
    "distinctiveness_score": "structurally_distinctive",
}
MAX_FINDINGS_PER_ROUTE = 12
MAX_SOURCE_ALIASES_PER_FINDING = 12
MAX_STATEMENT_CHARS = 900
MAX_RATIONALE_CHARS = 500
MAX_SCOPE_CHARS = 300

_ALIAS_TOKEN_RE = re.compile(r"\{([A-Z]{2}\d{2,})\}")
_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
)
_TARGET_VALUE_FIELD_RE = re.compile(r"^(?P<base>.+)\.(?P<kind>observed|target)$")
_CANONICAL_TARGET_RE = re.compile(
    r"^objective_report\.target_attainment\.canonical_"
    r"(?P<observable>[art])_"
    r"(?P<wavelength_min>[^_]+)_(?P<wavelength_max>[^_]+)_"
    r"(?P<constraint>at_least|at_most)_"
    r"(?P<aggregation>mean|worst_case)_"
    r"(?P<angle>[^_]+)_(?P<polarization>[^_]+)(?:_.+)?$"
)
_COVERAGE_ROUTE_BY_STAGE: Dict[ArticleStage, str] = {
    ArticleStage.baseline_experiments: "baseline",
    ArticleStage.exploration: "exploration",
    ArticleStage.controlled_improvement: "controlled_improvement",
    ArticleStage.discriminative_experiments: "discriminative_experiments",
    ArticleStage.robustness_ablation: "robustness_ablation",
    ArticleStage.fresh_replay: "fresh_replay",
}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SynthesisAliasRecord(_StrictModel):
    """Local semantic alias bound to one verified record for one route."""

    schema_version: Literal["synthesis-alias-record.v1"] = "synthesis-alias-record.v1"
    alias: str
    kind: Literal[
        "route", "hypothesis", "artifact", "trusted_value", "candidate", "observation"
    ]
    route_id: str = ""
    observation_id: str = ""
    artifact_id: str = ""
    field: str = ""
    label: str = ""
    rendered_value: str = ""
    unit: str = ""
    prose_safe: bool = False
    candidate_id: str = ""
    candidate_alias: str = ""
    role_keys: List[str] = Field(default_factory=list)
    route_role_keys: List[str] = Field(default_factory=list)
    global_role_keys: List[str] = Field(default_factory=list)
    artifact_aliases: List[str] = Field(default_factory=list)
    value_aliases: List[str] = Field(default_factory=list)
    statement: str = ""


class HypothesisView(_StrictModel):
    schema_version: Literal["synthesis-hypothesis-view.v1"] = (
        "synthesis-hypothesis-view.v1"
    )
    alias: str
    statement: str
    falsifiable_prediction: str = ""


class ProposalView(_StrictModel):
    schema_version: Literal["synthesis-proposal-view.v1"] = "synthesis-proposal-view.v1"
    stage: str = ""
    atomic_change: Dict[str, Any] = Field(default_factory=dict)
    expected_discriminator: Dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    uncertainty: str = ""


class CandidateView(_StrictModel):
    schema_version: Literal["synthesis-candidate-view.v1"] = (
        "synthesis-candidate-view.v1"
    )
    alias: str
    candidate_id: str = ""
    role_keys: List[str] = Field(default_factory=list)
    route_role_keys: List[str] = Field(default_factory=list)
    global_role_keys: List[str] = Field(default_factory=list)
    artifact_aliases: List[str] = Field(default_factory=list)
    value_aliases: List[str] = Field(default_factory=list)
    is_baseline: bool = False
    is_pareto: bool = False
    target_score: Optional[float] = None
    robustness_score: Optional[float] = None
    simplicity_score: Optional[float] = None
    distinctiveness_score: Optional[float] = None


class ArtifactView(_StrictModel):
    schema_version: Literal["synthesis-artifact-view.v1"] = "synthesis-artifact-view.v1"
    alias: str
    artifact_type: str = ""
    content_summary: str = ""
    fields: List[str] = Field(default_factory=list)
    candidate_alias: str = ""


class TrustedValueView(_StrictModel):
    schema_version: Literal["synthesis-trusted-value-view.v1"] = (
        "synthesis-trusted-value-view.v1"
    )
    alias: str
    field: str = ""
    artifact_alias: str = ""
    candidate_alias: str = ""
    label: str = ""
    rendered_value: str = ""
    unit: str = ""
    prose_safe: bool = False


class ResultSynthesisProviderInput(_StrictModel):
    """One bounded per-route provider payload; aliases only, no secrets."""

    schema_version: Literal["result-synthesis-provider-input.v1"] = (
        "result-synthesis-provider-input.v1"
    )
    route_alias: str
    question: str
    charter_scope: str = ""
    charter_goals: List[str] = Field(default_factory=list)
    capability_scope: str = ""
    hypotheses: List[HypothesisView] = Field(default_factory=list)
    proposal: ProposalView = Field(default_factory=ProposalView)
    observation_status: str = ""
    observation_summary: str = ""
    candidates: List[CandidateView] = Field(default_factory=list)
    artifacts: List[ArtifactView] = Field(default_factory=list)
    trusted_values: List[TrustedValueView] = Field(default_factory=list)
    literature_context: Optional[Dict[str, Any]] = None


class ResultSynthesisProviderResult(_StrictModel):
    """Provider envelope: findings rows plus truthful telemetry."""

    schema_version: Literal["result-synthesis-provider-result.v1"] = (
        "result-synthesis-provider-result.v1"
    )
    findings: List[Dict[str, Any]] = Field(default_factory=list)
    usage: Dict[str, Any] = Field(default_factory=dict)
    provider_model: str = "unknown"
    mock_llm: bool = False
    validation_errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class SynthesisFinding(_StrictModel):
    schema_version: Literal["synthesis-finding.v1"] = "synthesis-finding.v1"
    finding_id: str
    observation_id: str
    route_id: str
    role: Literal["method", "result", "limitation", "robustness"]
    statement_with_value_tokens: str
    source_value_aliases: List[str] = Field(min_length=1)
    subject_aliases: List[str] = Field(default_factory=list)
    comparison_scope: Literal["none", "route", "global"] = "none"
    rationale: str = ""
    scope_limits: str = ""
    target_comparison: Dict[str, Any] = Field(default_factory=dict)
    synthesized_hypothesis_id: str = ""

    @field_validator("finding_id", "observation_id", "route_id")
    @classmethod
    def _non_empty_ids(cls, value: str, info: Any) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{info.field_name} must be non-empty")
        return text


class ArticleResultSynthesisResult(_StrictModel):
    schema_version: Literal["article-result-synthesis.v1"] = (
        "article-result-synthesis.v1"
    )
    status: Literal["ready", "partial", "unavailable", "invalid"]
    result_id: str = ""
    source_plan_id: str = ""
    derived_plan: Optional[ArticleDirectorPlan] = None
    observations: Tuple[ObservationCard, ...] = Field(default_factory=tuple)
    feedback_results: Tuple[ArticleFeedbackResult, ...] = Field(default_factory=tuple)
    ledger: Optional[ClaimLedgerResult] = None
    alias_manifest: Dict[str, SynthesisAliasRecord] = Field(default_factory=dict)
    findings: Tuple[SynthesisFinding, ...] = Field(default_factory=tuple)
    provider_usage: Tuple[Dict[str, Any], ...] = Field(default_factory=tuple)
    validation_errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


ResultSynthesisProvider = Callable[
    [ResultSynthesisProviderInput | Mapping[str, Any]],
    ResultSynthesisProviderResult | Mapping[str, Any],
]


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _stable_digest(*parts: Any) -> str:
    return hashlib.sha256(
        _canonical_json([str(part) for part in parts]).encode("utf-8")
    ).hexdigest()[:16]


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


def _finite(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _target_attainment_comparison(
    numeric_sources: Sequence[str],
    manifest: Mapping[str, SynthesisAliasRecord],
) -> Optional[Dict[str, Any]]:
    """Resolve one exact observed/target pair and its declared inequality.

    Target attainment is arithmetic over verified structured fields, not a
    prose interpretation.  Returning ``None`` means the cited scalars do not
    form exactly one complete target-attainment pair.
    """

    by_base: Dict[str, Dict[str, Tuple[str, SynthesisAliasRecord]]] = {}
    for alias in numeric_sources:
        record = manifest.get(alias)
        if record is None or record.kind != "trusted_value":
            continue
        match = _TARGET_VALUE_FIELD_RE.match(record.field)
        if match is None or "target_attainment." not in match.group("base"):
            continue
        by_base.setdefault(match.group("base"), {})[match.group("kind")] = (
            alias,
            record,
        )
    complete = [
        (base, records)
        for base, records in by_base.items()
        if set(records) == {"observed", "target"}
    ]
    if len(complete) != 1:
        return None
    base, records = complete[0]
    observed_alias, observed_record = records["observed"]
    target_alias, target_record = records["target"]
    observed = _finite(observed_record.rendered_value)
    target = _finite(target_record.rendered_value)
    if observed is None or target is None:
        return None
    if observed_record.candidate_alias != target_record.candidate_alias:
        return None
    if "_at_least_" in base:
        constraint = "at_least"
        met = observed >= target
    elif "_at_most_" in base:
        constraint = "at_most"
        met = observed <= target
    else:
        return None
    return {
        "base_field": base,
        "constraint": constraint,
        "observed_alias": observed_alias,
        "target_alias": target_alias,
        "observed": observed,
        "target": target,
        "met": met,
        "candidate_alias": observed_record.candidate_alias,
    }


def _target_objective_description(base_field: str) -> str:
    match = _CANONICAL_TARGET_RE.match(base_field)
    if match is None:
        return "the recorded objective"
    observable = {
        "a": "absorptance",
        "r": "reflectance",
        "t": "transmittance",
    }.get(match.group("observable"), "optical response")
    aggregation = match.group("aggregation").replace("_", "-")
    polarization = {"s": "s-polarized", "p": "p-polarized"}.get(
        match.group("polarization"),
        f"{match.group('polarization')}-polarized",
    )
    return (
        f"the {aggregation} {observable} objective over "
        f"{match.group('wavelength_min')}-{match.group('wavelength_max')} nm "
        f"at {match.group('angle')}-degree {polarization} incidence"
    )


def _render_target_attainment_statement(
    comparison: Mapping[str, Any],
    subjects: Sequence[str],
) -> str:
    subject = (
        f"Candidate {{{subjects[0]}}}" if len(subjects) == 1 else "The recorded result"
    )
    outcome = "met" if comparison["met"] else "did not meet"
    constraint = str(comparison["constraint"]).replace("_", "-")
    description = _target_objective_description(str(comparison["base_field"]))
    return (
        f"{subject} {outcome} the {constraint} target for {description}: "
        f"observed {{{comparison['observed_alias']}}} versus target "
        f"{{{comparison['target_alias']}}}."
    )


def _usage_with_cost(usage: Mapping[str, Any]) -> Dict[str, Any]:
    """Add local estimated list-price cost using the established helper."""

    result = dict(usage or {})
    model = str(result.get("model_name") or SYNTHESIS_MODEL_NAME)
    input_tokens = int(
        result.get("input_tokens") or result.get("estimated_input_tokens") or 0
    )
    output_tokens = int(
        result.get("output_tokens") or result.get("estimated_output_tokens") or 0
    )
    if (input_tokens or output_tokens) and not result.get(
        "estimated_list_price_cost_cny"
    ):
        result["estimated_list_price_cost_cny"] = round(
            estimate_call_cost_cny(model, input_tokens, output_tokens),
            8,
        )
    return result


def _structural_number_allowlist(
    plan: ArticleDirectorPlan,
    row: PlannedRouteResult,
) -> set[float]:
    texts: list[str] = [
        plan.question,
        plan.charter.scope,
        plan.charter.goals,
        plan.charter.success_criteria,
        plan.charter.constraints,
        plan.capability.supported_scope,
    ]
    for item in plan.hypotheses:
        texts.extend(
            [
                item.statement,
                item.falsifiable_prediction,
                item.theory_basis,
                item.risk_notes,
            ]
        )
    proposal = row.proposal
    if proposal is not None:
        texts.extend(
            [
                proposal.rationale,
                proposal.uncertainty,
                _canonical_json(proposal.atomic_change),
                _canonical_json(proposal.expected_discriminator),
                _canonical_json(proposal.parameters),
            ]
        )
    if row.request is not None:
        texts.append(_canonical_json(row.request.parameters))
    allowed: set[float] = set()
    for text in texts:
        for match in _NUMBER_RE.finditer(str(text or "")):
            try:
                allowed.add(float(match.group(0)))
            except ValueError:
                continue
    return allowed


def _resolve_tokens(
    statement: str,
    manifest: Mapping[str, SynthesisAliasRecord],
) -> str:
    def replace(match: re.Match[str]) -> str:
        record = manifest.get(match.group(1))
        if record is None:
            return match.group(0)
        if record.kind == "trusted_value":
            value = record.rendered_value
            return f"{value} {record.unit}".strip() if record.unit else value
        if record.kind == "artifact":
            return record.label or record.artifact_id
        return record.label or record.alias

    return _ALIAS_TOKEN_RE.sub(replace, statement)


def _global_candidate_aliases(
    assets: Mapping[str, ArticleAssetCompilationResult],
) -> Dict[str, str]:
    """Assign stable, human-readable aliases across every usable route."""

    candidate_ids = sorted(
        {
            candidate.candidate_id
            for asset in assets.values()
            for candidate in asset.candidates
        }
    )
    width = max(2, len(str(len(candidate_ids))))
    return {
        candidate_id: f"GC{index:0{width}d}"
        for index, candidate_id in enumerate(candidate_ids, start=1)
    }


def _global_candidate_roles(
    assets: Mapping[str, ArticleAssetCompilationResult],
) -> Dict[str, List[str]]:
    """Recompute only roles whose scores have run-global meaning."""

    candidates_by_id: Dict[str, List[Any]] = {}
    for asset in assets.values():
        for candidate in asset.candidates:
            candidates_by_id.setdefault(candidate.candidate_id, []).append(candidate)
    roles: Dict[str, set[str]] = {
        candidate_id: set() for candidate_id in candidates_by_id
    }
    axes = (
        ("best_target_score", "target_score", False),
        ("simplest_fabrication", "simplicity_score", False),
        ("most_robust", "robustness_score", True),
    )
    for role_key, field, require_complete in axes:
        scored: Dict[str, float] = {}
        for candidate_id, records in candidates_by_id.items():
            values = [
                value
                for value in (_finite(getattr(record, field)) for record in records)
                if value is not None
            ]
            if values:
                scored[candidate_id] = max(values)
        if require_complete and len(scored) != len(candidates_by_id):
            continue
        if not scored:
            continue
        winning_score = max(scored.values())
        for candidate_id, score in scored.items():
            if score == winning_score:
                roles[candidate_id].add(role_key)
    return {
        candidate_id: sorted(role_keys)
        for candidate_id, role_keys in sorted(roles.items())
    }


def _build_alias_manifest(
    plan: ArticleDirectorPlan,
    rows: Sequence[PlannedRouteResult],
    observations: Mapping[str, ObservationCard],
    assets: Mapping[str, ArticleAssetCompilationResult],
    literature_context: Optional[Dict[str, Any]] = None,
) -> tuple[Dict[str, SynthesisAliasRecord], Dict[str, ResultSynthesisProviderInput]]:
    manifest: Dict[str, SynthesisAliasRecord] = {}
    payloads: Dict[str, ResultSynthesisProviderInput] = {}
    candidate_aliases = _global_candidate_aliases(assets)
    global_roles = _global_candidate_roles(assets)
    for row_index, row in enumerate(rows, start=1):
        if row.status != "ready" or row.request is None:
            continue
        asset = assets.get(row.request.request_id)
        if asset is None:
            continue
        observation = observations.get(asset.observation.observation_id)
        if observation is None:
            continue
        route_alias = row.route_alias or f"R{row_index:02d}"
        manifest[route_alias] = SynthesisAliasRecord(
            alias=route_alias,
            kind="route",
            route_id=row.route_id,
            observation_id=observation.observation_id,
        )
        value_records = {
            (item.artifact_id, item.field): item for item in asset.trusted_values
        }
        candidate_by_alias = {
            candidate_aliases[candidate.candidate_id]: candidate
            for candidate in asset.candidates
        }
        artifact_owners: Dict[str, str] = {}
        for descriptor in asset.descriptors:
            owners = [
                alias
                for alias, candidate in candidate_by_alias.items()
                if descriptor.artifact_id in candidate.artifact_ids
            ]
            if len(owners) == 1:
                artifact_owners[descriptor.artifact_id] = owners[0]
        artifact_alias_by_id: Dict[str, str] = {}
        artifact_aliases_by_candidate: Dict[str, List[str]] = {
            alias: [] for alias in candidate_by_alias
        }
        value_aliases_by_candidate: Dict[str, List[str]] = {
            alias: [] for alias in candidate_by_alias
        }
        artifact_views: List[ArtifactView] = []
        value_views: List[TrustedValueView] = []
        for index, descriptor in enumerate(
            sorted(asset.descriptors, key=lambda item: item.artifact_id),
            1,
        ):
            alias = f"AV{index:02d}"
            artifact_alias_by_id[descriptor.artifact_id] = alias
            owner_alias = artifact_owners.get(descriptor.artifact_id, "")
            if owner_alias:
                artifact_aliases_by_candidate[owner_alias].append(alias)
            manifest[f"{route_alias}.{alias}"] = SynthesisAliasRecord(
                alias=alias,
                kind="artifact",
                route_id=row.route_id,
                observation_id=observation.observation_id,
                artifact_id=descriptor.artifact_id,
                label=descriptor.content_summary or descriptor.artifact_id,
                field="",
                candidate_alias=owner_alias,
            )
            artifact_views.append(
                ArtifactView(
                    alias=alias,
                    artifact_type=descriptor.artifact_type,
                    content_summary=descriptor.content_summary,
                    fields=list(descriptor.fields),
                    candidate_alias=owner_alias,
                )
            )
        for index, (key, value) in enumerate(
            sorted(value_records.items(), key=lambda item: (item[0][0], item[0][1])),
            1,
        ):
            alias = f"TV{index:02d}"
            owner_alias = artifact_owners.get(key[0], "")
            if not owner_alias:
                field_owners = [
                    candidate_alias
                    for candidate_alias, candidate in candidate_by_alias.items()
                    if key[1].startswith(f"{candidate.candidate_id}.")
                ]
                if len(field_owners) == 1:
                    owner_alias = field_owners[0]
            if owner_alias:
                value_aliases_by_candidate[owner_alias].append(alias)
            manifest[f"{route_alias}.{alias}"] = SynthesisAliasRecord(
                alias=alias,
                kind="trusted_value",
                route_id=row.route_id,
                observation_id=observation.observation_id,
                artifact_id=key[0],
                field=key[1],
                label=value.label,
                rendered_value=value.rendered_value,
                unit=value.unit,
                prose_safe=value.prose_safe,
                candidate_alias=owner_alias,
            )
            value_views.append(
                TrustedValueView(
                    alias=alias,
                    field=key[1],
                    artifact_alias=artifact_alias_by_id.get(key[0], ""),
                    candidate_alias=owner_alias,
                    label=value.label,
                    rendered_value=value.rendered_value,
                    unit=value.unit,
                    prose_safe=value.prose_safe,
                )
            )
        candidate_views: List[CandidateView] = []
        for candidate in sorted(asset.candidates, key=lambda item: item.candidate_id):
            alias = candidate_aliases[candidate.candidate_id]
            route_role_keys = sorted(set(candidate.role_keys))
            global_role_keys = global_roles.get(candidate.candidate_id, [])
            manifest[f"{route_alias}.{alias}"] = SynthesisAliasRecord(
                alias=alias,
                kind="candidate",
                route_id=row.route_id,
                observation_id=observation.observation_id,
                candidate_id=candidate.candidate_id,
                role_keys=route_role_keys,
                route_role_keys=route_role_keys,
                global_role_keys=global_role_keys,
                artifact_aliases=sorted(artifact_aliases_by_candidate[alias]),
                value_aliases=sorted(value_aliases_by_candidate[alias]),
            )
            candidate_views.append(
                CandidateView(
                    alias=alias,
                    candidate_id=candidate.candidate_id,
                    role_keys=route_role_keys,
                    route_role_keys=route_role_keys,
                    global_role_keys=global_role_keys,
                    artifact_aliases=sorted(artifact_aliases_by_candidate[alias]),
                    value_aliases=sorted(value_aliases_by_candidate[alias]),
                    is_baseline=candidate.is_baseline,
                    is_pareto=candidate.is_pareto,
                    target_score=candidate.target_score,
                    robustness_score=candidate.robustness_score,
                    simplicity_score=candidate.simplicity_score,
                    distinctiveness_score=candidate.distinctiveness_score,
                )
            )
        proposal = row.proposal
        proposal_view = ProposalView()
        if proposal is not None:
            proposal_view = ProposalView(
                stage=proposal.stage.value,
                atomic_change=dict(proposal.atomic_change),
                expected_discriminator=dict(proposal.expected_discriminator),
                rationale=proposal.rationale,
                uncertainty=proposal.uncertainty,
            )
        payloads[row.request.request_id] = ResultSynthesisProviderInput(
            route_alias=route_alias,
            question=plan.question,
            charter_scope=plan.charter.scope,
            charter_goals=list(plan.charter.goals),
            capability_scope=plan.capability.supported_scope,
            hypotheses=[
                HypothesisView(
                    alias=f"H{index:02d}",
                    statement=item.statement,
                    falsifiable_prediction=item.falsifiable_prediction,
                )
                for index, item in enumerate(
                    sorted(plan.hypotheses, key=lambda item: item.hypothesis_id),
                    1,
                )
            ],
            proposal=proposal_view,
            observation_status=observation.status.value,
            observation_summary=observation.summary,
            candidates=candidate_views,
            artifacts=artifact_views,
            trusted_values=value_views,
            literature_context=literature_context,
        )
    return manifest, payloads


def _local_manifest(
    manifest: Mapping[str, SynthesisAliasRecord],
    route_alias: str,
) -> Dict[str, SynthesisAliasRecord]:
    prefix = f"{route_alias}."
    return {
        key[len(prefix) :]: record
        for key, record in manifest.items()
        if key.startswith(prefix)
    }


def _route_alias_by_route_id(
    manifest: Mapping[str, SynthesisAliasRecord],
) -> Dict[str, str]:
    return {
        record.route_id: record.alias
        for record in manifest.values()
        if record.kind == "route"
    }


def _validated_findings(
    raw_rows: Sequence[Mapping[str, Any]],
    *,
    observation: ObservationCard,
    route_id: str,
    manifest: Mapping[str, SynthesisAliasRecord],
    descriptor_ids: set[str],
    allowlist: set[float],
    warnings: List[str],
) -> list[Dict[str, Any]]:
    findings: list[Dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_rows):
        prefix = f"route {route_id} finding {index + 1}"
        if not isinstance(raw, Mapping):
            warnings.append(f"{prefix}: malformed finding row; skipped")
            continue
        role = str(raw.get("role") or "").strip()
        if role not in FINDING_ROLES:
            warnings.append(f"{prefix}: unknown role {role!r}; skipped")
            continue
        statement = str(raw.get("statement_with_value_tokens") or "").strip()
        if not statement or len(statement) > MAX_STATEMENT_CHARS:
            warnings.append(f"{prefix}: statement missing or too long; skipped")
            continue
        rationale = str(raw.get("rationale") or "")[:MAX_RATIONALE_CHARS]
        scope_limits = str(raw.get("scope_limits") or "")[:MAX_SCOPE_CHARS]
        aliases = [
            str(item).strip()
            for item in (raw.get("source_value_aliases") or ())
            if str(item).strip()
        ]
        token_alias_list = _ALIAS_TOKEN_RE.findall(statement)
        recovered_aliases = [
            alias
            for alias in token_alias_list
            if alias in manifest and alias not in aliases
        ]
        aliases = list(dict.fromkeys([*aliases, *recovered_aliases]))[
            :MAX_SOURCE_ALIASES_PER_FINDING
        ]
        if not aliases:
            warnings.append(f"{prefix}: no source aliases; skipped")
            continue
        subjects = [
            str(item).strip()
            for item in (raw.get("subject_aliases") or ())
            if str(item).strip()
        ]
        recovered_subjects = [
            alias
            for alias in token_alias_list
            if alias in manifest
            and manifest[alias].kind == "candidate"
            and alias not in subjects
        ]
        subjects = list(dict.fromkeys([*subjects, *recovered_subjects]))[
            :MAX_SOURCE_ALIASES_PER_FINDING
        ]
        if recovered_aliases or recovered_subjects:
            warnings.append(
                f"{prefix}: recovered explicit statement-token aliases omitted "
                "from redundant source/subject fields"
            )
        comparison_scope = str(raw.get("comparison_scope") or "none").strip()
        if comparison_scope not in {"none", "route", "global"}:
            warnings.append(
                f"{prefix}: invalid comparison_scope {comparison_scope!r}; skipped"
            )
            continue
        token_aliases = set(token_alias_list)
        unknown_tokens = sorted(token_aliases - set(aliases))
        if unknown_tokens:
            warnings.append(
                f"{prefix}: statement tokens not in source aliases "
                f"{unknown_tokens}; skipped"
            )
            continue
        route_aliases = {
            key for key, record in manifest.items() if record.route_id == route_id
        }
        cross_route = sorted(set(aliases) - route_aliases)
        if cross_route:
            warnings.append(
                f"{prefix}: cross-route or unknown aliases {cross_route}; skipped"
            )
            continue
        invalid_subjects = sorted(
            subject
            for subject in subjects
            if subject not in manifest
            or manifest[subject].kind != "candidate"
            or subject not in aliases
            or subject not in token_aliases
        )
        if invalid_subjects:
            warnings.append(
                f"{prefix}: subject aliases must be local GC aliases cited "
                f"as statement tokens {invalid_subjects}; skipped"
            )
            continue
        cited_candidates = {
            alias
            for alias in aliases
            if alias in manifest and manifest[alias].kind == "candidate"
        }
        if cited_candidates != set(subjects):
            warnings.append(
                f"{prefix}: candidate aliases must exactly match subject_aliases; skipped"
            )
            continue
        if comparison_scope != "none" and not subjects:
            warnings.append(
                f"{prefix}: comparison_scope requires explicit subject_aliases; skipped"
            )
            continue
        if len(subjects) > 1 and comparison_scope == "none":
            warnings.append(
                f"{prefix}: multiple subjects require route or global comparison_scope; skipped"
            )
            continue
        numeric_sources: list[str] = []
        artifact_bound = False
        evidence_owners: set[str] = set()
        for alias in aliases:
            record = manifest.get(alias)
            if record is None:
                warnings.append(f"{prefix}: unknown alias {alias!r}; skipped")
                break
            if record.kind == "trusted_value":
                if not record.prose_safe or _finite(record.rendered_value) is None:
                    warnings.append(
                        f"{prefix}: trusted value {alias} is not a finite "
                        "prose-safe scalar; skipped"
                    )
                    break
                numeric_sources.append(alias)
            if (
                record.kind in {"trusted_value", "artifact"}
                and record.artifact_id not in descriptor_ids
            ):
                warnings.append(
                    f"{prefix}: alias {alias} backs an artifact absent from "
                    "the verified descriptor inventory; skipped"
                )
                break
            if record.kind in {"trusted_value", "artifact"} and record.artifact_id:
                artifact_bound = True
                if record.candidate_alias:
                    evidence_owners.add(record.candidate_alias)
        else:
            if not artifact_bound:
                warnings.append(f"{prefix}: no artifact-bound source alias; skipped")
                continue
            if subjects:
                wrong_owners = sorted(evidence_owners - set(subjects))
                if wrong_owners:
                    warnings.append(
                        f"{prefix}: candidate-specific evidence belongs to "
                        f"non-subject candidates {wrong_owners}; skipped"
                    )
                    continue
                missing_evidence = sorted(set(subjects) - evidence_owners)
                if missing_evidence:
                    warnings.append(
                        f"{prefix}: subjects lack owner-bound TV/AV evidence "
                        f"{missing_evidence}; skipped"
                    )
                    continue
            elif role != "method" and len(evidence_owners) > 1:
                warnings.append(
                    f"{prefix}: candidate-specific multi-owner evidence "
                    "requires explicit subject_aliases; skipped"
                )
                continue
            score_role_keys: set[str] = set()
            for alias in numeric_sources:
                source_record = manifest.get(alias)
                if source_record is None:
                    continue
                role_key = _SCORE_ROLE_BY_FIELD.get(
                    source_record.field.rsplit(".", 1)[-1]
                )
                if role_key is not None:
                    score_role_keys.add(role_key)
            if comparison_scope == "global" and len(subjects) != 1:
                warnings.append(
                    f"{prefix}: global role statements require exactly one "
                    "globally ranked subject; skipped"
                )
                continue
            if len(subjects) == 1 and comparison_scope in {"route", "global"}:
                subject_record = manifest[subjects[0]]
                available_roles = set(
                    subject_record.global_role_keys
                    if comparison_scope == "global"
                    else subject_record.route_role_keys
                )
                missing_roles = sorted(score_role_keys - available_roles)
                if missing_roles:
                    warnings.append(
                        f"{prefix}: {comparison_scope} score role not granted to "
                        f"subject {subjects[0]} ({missing_roles}); skipped"
                    )
                    continue
            target_comparison = _target_attainment_comparison(numeric_sources, manifest)
            if target_comparison is not None:
                normalized_statement = _render_target_attainment_statement(
                    target_comparison, subjects
                )
                normalized_role = "result" if target_comparison["met"] else "limitation"
                if statement != normalized_statement or role != normalized_role:
                    warnings.append(
                        f"{prefix}: target-attainment prose and role were "
                        "normalized from verified structured values"
                    )
                statement = normalized_statement
                role = normalized_role
            for match in _NUMBER_RE.finditer(statement):
                try:
                    number = float(match.group(0))
                except ValueError:
                    continue
                if not any(abs(number - allowed) <= 1e-9 for allowed in allowlist):
                    warnings.append(
                        f"{prefix}: invented numeric value {match.group(0)}; skipped"
                    )
                    break
            else:
                key = (
                    observation.observation_id,
                    role,
                    re.sub(r"\s+", " ", statement).casefold(),
                )
                if key in seen:
                    warnings.append(f"{prefix}: duplicate finding; skipped")
                    continue
                seen.add(key)
                findings.append(
                    {
                        "observation_id": observation.observation_id,
                        "route_id": route_id,
                        "role": role,
                        "statement_with_value_tokens": statement,
                        "source_value_aliases": aliases,
                        "subject_aliases": subjects,
                        "comparison_scope": comparison_scope,
                        "rationale": rationale,
                        "scope_limits": scope_limits,
                        "numeric_sources": numeric_sources,
                        "target_comparison": target_comparison or {},
                    }
                )
    return findings


def _augment_derived_plan_with_literature(
    plan: ArticleDirectorPlan,
    supplement: Any,
) -> Tuple[ArticleDirectorPlan, List[str]]:
    def identity_key(entry: Any) -> Tuple[Any, ...]:
        return (
            str(getattr(entry, "paper_id", "") or ""),
            str(getattr(entry, "doi", "") or "").strip().lower(),
            str(getattr(entry, "title", "") or "").strip(),
            getattr(entry, "year", None),
            str(getattr(entry, "source_route", "") or ""),
            str(getattr(entry, "content_depth", "") or ""),
            str(getattr(entry, "allowed_use", "") or ""),
            str(getattr(entry, "text_sha256", "") or ""),
        )

    warnings: List[str] = []
    report_evidence_ids = {item.evidence_id for item in supplement.evidence}
    manifest_ids = {item.evidence_id for item in supplement.evidence_identity}
    merged_identity = {item.evidence_id: item for item in plan.evidence_identity}
    for entry in supplement.evidence_identity:
        if entry.evidence_id in report_evidence_ids:
            existing = merged_identity.get(entry.evidence_id)
            if existing is not None and identity_key(existing) != identity_key(entry):
                raise ValueError(
                    "old plan evidence identity collision for " f"{entry.evidence_id!r}"
                )
            merged_identity[entry.evidence_id] = EvidenceIdentityManifest(
                evidence_id=entry.evidence_id,
                paper_id=entry.paper_id,
                doi=entry.doi,
                title=entry.title,
                year=entry.year,
                source_route=entry.source_route,
                content_depth=entry.content_depth,
                allowed_use=entry.allowed_use,
                text_sha256=entry.text_sha256,
            )
        else:
            warnings.append(
                f"supplement evidence identity {entry.evidence_id!r} is not "
                "present in the method report; omitted"
            )
    supplement_by_hypothesis = {
        item.hypothesis_id: item for item in supplement.new_plan_hypotheses
    }
    hypotheses: List[HypothesisCandidate] = []
    for hypothesis in plan.hypotheses:
        supplement_hypothesis = supplement_by_hypothesis.get(hypothesis.hypothesis_id)
        added: List[str] = []
        if supplement_hypothesis is not None:
            for evidence_id in supplement_hypothesis.evidence_ids:
                if (
                    evidence_id in report_evidence_ids
                    and evidence_id in manifest_ids
                    and evidence_id not in hypothesis.evidence_ids
                ):
                    added.append(evidence_id)
                else:
                    warnings.append(
                        f"supplement hypothesis {hypothesis.hypothesis_id!r} "
                        f"references unverified evidence {evidence_id!r}; "
                        "omitted"
                    )
        hypotheses.append(
            hypothesis.model_copy(
                update={
                    "evidence_ids": [
                        *hypothesis.evidence_ids,
                        *sorted(set(added)),
                    ]
                }
            )
        )
    old_ids = {item.hypothesis_id for item in plan.hypotheses}
    unmatched = sorted(set(supplement_by_hypothesis) - old_ids)
    if unmatched:
        warnings.append(
            "unmatched supplemental hypotheses remain advisory context only: "
            + ", ".join(unmatched)
        )
    influence = list(
        dict.fromkeys([*plan.research_influence, *supplement.research_influence])
    )
    influence.append(
        f"literature supplement plan {supplement.new_plan_id} bound to "
        f"source {supplement.source_pipeline_result_id}"
    )
    derived_id = _stable_digest(
        plan.plan_id,
        supplement.new_plan_id,
        supplement.metadata_sha256,
        sorted(merged_identity),
        [list(item.evidence_ids) for item in hypotheses],
    )
    augmented = plan.model_copy(
        update={
            "plan_id": f"plan-{derived_id}",
            "hypotheses": hypotheses,
            "evidence_identity": list(merged_identity.values()),
            "research_influence": influence,
        }
    )
    return augmented, warnings


def _derive_plan(
    plan: ArticleDirectorPlan,
    findings: Sequence[SynthesisFinding],
    manifest: Mapping[str, SynthesisAliasRecord],
    literature_supplement: Optional[Any] = None,
) -> Tuple[ArticleDirectorPlan, List[str]]:
    hypotheses = list(plan.hypotheses)
    route_alias_by_route = _route_alias_by_route_id(manifest)
    for finding in findings:
        hypothesis_id = finding.synthesized_hypothesis_id
        route_alias = route_alias_by_route.get(finding.route_id, "")
        local_manifest = _local_manifest(manifest, route_alias)
        hypotheses.append(
            HypothesisCandidate(
                hypothesis_id=hypothesis_id,
                statement=_resolve_tokens(
                    finding.statement_with_value_tokens, local_manifest
                ),
                falsifiable_prediction=(
                    "Repeating the route reproduces the verified metric "
                    "bounds recorded in the cited artifacts."
                ),
                expected_observations=[finding.observation_id],
                evidence_ids=[],
                theory_basis="result-grounded synthesis from verified artifacts",
                route_kind="result_synthesis",
                parent_hypothesis_id=None,
                novelty_rationale="synthesized finding; partial support only",
                risk_notes=finding.scope_limits,
            )
        )
    derived_id = _stable_digest(
        plan.plan_id,
        [item.finding_id for item in findings],
        [item.hypothesis_id for item in hypotheses],
    )
    matrix = CoverageMatrix(
        matrix_id=f"matrix-{_stable_digest(plan.coverage_matrix.matrix_id, derived_id)}",
        rows=list(plan.coverage_matrix.rows),
    )
    derived = plan.model_copy(
        update={
            "plan_id": f"plan-{derived_id}",
            "hypotheses": hypotheses,
            "coverage_matrix": matrix,
            "research_influence": [
                *plan.research_influence,
                (
                    f"result synthesis derived from plan {plan.plan_id} with "
                    f"{len(findings)} result-grounded hypotheses"
                ),
            ],
        }
    )
    if literature_supplement is None:
        return derived, []
    return _augment_derived_plan_with_literature(
        derived,
        literature_supplement,
    )


def _synthesis_value_lineage(
    findings: Sequence[SynthesisFinding],
    manifest: Mapping[str, SynthesisAliasRecord],
) -> Dict[str, List[Dict[str, Any]]]:
    route_alias_by_route = _route_alias_by_route_id(manifest)
    lineage: Dict[str, List[Dict[str, Any]]] = {}
    for finding in findings:
        route_alias = route_alias_by_route.get(finding.route_id, "")
        local = _local_manifest(manifest, route_alias)
        refs: List[Dict[str, Any]] = []
        for alias in finding.source_value_aliases:
            record = local.get(alias)
            if (
                record is None
                or record.kind != "trusted_value"
                or not record.prose_safe
                or not record.artifact_id
                or not record.field
            ):
                continue
            refs.append(
                {
                    "artifact_id": record.artifact_id,
                    "field": record.field,
                    "label": record.label,
                    "unit": record.unit,
                    "source_alias": alias,
                }
            )
        if refs:
            existing = lineage.setdefault(finding.synthesized_hypothesis_id, [])
            for ref in refs:
                if ref not in existing:
                    existing.append(ref)
    return lineage


def _synthesis_contracts(
    findings: Sequence[SynthesisFinding],
    manifest: Mapping[str, SynthesisAliasRecord],
) -> Dict[str, Dict[str, Any]]:
    """Carry the finding's semantic boundary alongside its claim.

    The generic claim ledger intentionally remains source-agnostic.  This
    small, read-only envelope preserves the additional result-synthesis
    semantics that downstream organization models need: candidate ownership,
    route/global comparison scope, exact metric fields, and the limits the
    synthesizer stated.  It grants no new evidence or write permission.
    """

    route_alias_by_route = _route_alias_by_route_id(manifest)
    contracts: Dict[str, Dict[str, Any]] = {}
    for finding in findings:
        route_alias = route_alias_by_route.get(finding.route_id, "")
        local = _local_manifest(manifest, route_alias)
        subject_candidates: List[Dict[str, Any]] = []
        for alias in finding.subject_aliases:
            record = local.get(alias)
            if record is None or record.kind != "candidate":
                continue
            subject_candidates.append(
                {
                    "alias": alias,
                    "candidate_id": record.candidate_id,
                    "route_role_keys": list(record.route_role_keys),
                    "global_role_keys": list(record.global_role_keys),
                }
            )
        metric_bindings: List[Dict[str, Any]] = []
        for alias in finding.source_value_aliases:
            record = local.get(alias)
            if record is None or record.kind != "trusted_value":
                continue
            metric_bindings.append(
                {
                    "source_alias": alias,
                    "artifact_id": record.artifact_id,
                    "field": record.field,
                    "label": record.label,
                    "unit": record.unit,
                    "candidate_alias": record.candidate_alias,
                }
            )
        aggregate_score = any(
            "score" in str(item.get("field") or "").casefold()
            or "score" in str(item.get("label") or "").casefold()
            for item in metric_bindings
        )
        contract: Dict[str, Any] = {
            "kind": "result_synthesis",
            "route_id": finding.route_id,
            "route_alias": route_alias,
            "finding_role": finding.role,
            "comparison_scope": finding.comparison_scope,
            "subject_aliases": list(finding.subject_aliases),
            "subject_candidates": subject_candidates,
            "metric_bindings": metric_bindings,
            "scope_limits": finding.scope_limits,
            "rationale": finding.rationale,
            "source_value_aliases": list(finding.source_value_aliases),
            "target_comparison": dict(finding.target_comparison),
            "interpretation_policy": (
                "Report only the recorded metric under its supplied conditions; "
                "an aggregate score does not establish mechanism, structural "
                "integrity, manufacturing feasibility, installation reliability, "
                "or preservation of another metric."
                if aggregate_score or finding.role == "robustness"
                else (
                    "Keep the statement bounded by the exact metric fields and "
                    "conditions listed here. Do not widen a route-local result "
                    "to a global or all-condition conclusion."
                )
            ),
        }
        contracts[finding.synthesized_hypothesis_id] = contract
    return contracts


def _clone_observations(
    observations: Sequence[ObservationCard],
    findings_by_observation: Mapping[str, Sequence[Mapping[str, Any]]],
    manifest: Mapping[str, SynthesisAliasRecord],
    coverage_route_by_observation: Mapping[str, str],
) -> Tuple[ObservationCard, ...]:
    cloned: List[ObservationCard] = []
    route_alias_by_route = _route_alias_by_route_id(manifest)
    for observation in observations:
        findings = findings_by_observation.get(observation.observation_id, ())
        metrics = dict(observation.metrics)
        coverage_route = coverage_route_by_observation.get(
            observation.observation_id, ""
        )
        if coverage_route:
            metrics["route_id"] = coverage_route
        bound_artifact_ids: set[str] = set()
        for item in findings:
            route_alias = route_alias_by_route.get(str(item.get("route_id") or ""), "")
            local = _local_manifest(manifest, route_alias)
            for alias in item.get("source_value_aliases") or ():
                record = local.get(str(alias))
                if (
                    record is not None
                    and record.kind in {"trusted_value", "artifact"}
                    and record.artifact_id
                ):
                    bound_artifact_ids.add(record.artifact_id)
        artifact_ids = sorted(bound_artifact_ids)
        metrics["synthesis_omitted_artifact_ids"] = sorted(
            set(observation.artifact_ids) - bound_artifact_ids
        )
        entries = [
            {
                "hypothesis_id": str(item["synthesized_hypothesis_id"]),
                "to_status": "partially_supported",
                "evidence_kind": "partial_support",
                "reason": _resolve_tokens(
                    str(item["statement_with_value_tokens"]),
                    _local_manifest(
                        manifest,
                        route_alias_by_route.get(str(item.get("route_id") or ""), ""),
                    ),
                )[:500],
            }
            for item in findings
        ]
        cloned.append(
            observation.model_copy(
                update={
                    "metrics": metrics,
                    "hypothesis_updates": entries,
                    "artifact_ids": artifact_ids,
                }
            )
        )
    return tuple(cloned)


def _invalid_result(
    errors: Sequence[str],
    warnings: Sequence[str],
    *,
    source_plan_id: str = "",
) -> ArticleResultSynthesisResult:
    model = ArticleResultSynthesisResult(
        status="invalid",
        result_id="",
        source_plan_id=source_plan_id,
        validation_errors=sorted(set(errors)),
        warnings=sorted(set(warnings)),
    )
    return model.model_copy(update={"result_id": compute_result_synthesis_id(model)})


def compute_result_synthesis_id(
    result: ArticleResultSynthesisResult | Mapping[str, Any],
) -> str:
    """Deterministic content ID over the synthesis result (excluding id)."""

    model = (
        result
        if isinstance(result, ArticleResultSynthesisResult)
        else ArticleResultSynthesisResult.model_validate(result)
    )
    payload = model.model_dump(mode="json")
    payload.pop("result_id", None)
    return _stable_digest(_canonical_json(payload))


def synthesize_article_results(
    plan: ArticleDirectorPlan | Mapping[str, Any],
    planning: ArticleExperimentPlanningResult | Mapping[str, Any],
    assets: Sequence[ArticleAssetCompilationResult | Mapping[str, Any]],
    *,
    provider: Optional[ResultSynthesisProvider] = None,
    run_id: str = "",
    literature_supplement: Optional[Any] = None,
) -> ArticleResultSynthesisResult:
    """Synthesize source-bound findings and run feedback/claims over them."""

    errors: List[str] = []
    warnings: List[str] = []
    try:
        plan_model = (
            plan
            if isinstance(plan, ArticleDirectorPlan)
            else ArticleDirectorPlan.model_validate(plan)
        )
        planning_model = (
            planning
            if isinstance(planning, ArticleExperimentPlanningResult)
            else ArticleExperimentPlanningResult.model_validate(planning)
        )
    except ValidationError as exc:
        return _invalid_result([f"plan/planning is invalid: {exc}"], warnings)
    asset_models: List[ArticleAssetCompilationResult] = []
    for index, raw in enumerate(assets):
        try:
            asset_models.append(
                raw
                if isinstance(raw, ArticleAssetCompilationResult)
                else ArticleAssetCompilationResult.model_validate(raw)
            )
        except ValidationError as exc:
            errors.append(f"assets[{index}] is invalid: {exc}")
    if errors:
        return _invalid_result(errors, warnings, source_plan_id=plan_model.plan_id)

    ready_rows: Dict[Tuple[str, str], PlannedRouteResult] = {}
    for row in planning_model.rows:
        if row.status != "ready" or row.request is None:
            continue
        key = (row.request.request_id, row.request.task_hash)
        if key in ready_rows:
            return _invalid_result(
                [
                    "experiment planning contains duplicate ready rows for "
                    f"request/task {key}"
                ],
                warnings,
                source_plan_id=plan_model.plan_id,
            )
        ready_rows[key] = row

    plan_hypothesis_ids = {item.hypothesis_id for item in plan_model.hypotheses}
    coverage_route_ids = {item.route_id for item in plan_model.coverage_matrix.rows}
    observations: Dict[str, ObservationCard] = {}
    usable: Dict[str, Tuple[ArticleAssetCompilationResult, PlannedRouteResult]] = {}
    unusable_asset_ids: List[str] = []
    for asset in asset_models:
        if asset.status in {"invalid", "unavailable"}:
            unusable_asset_ids.append(asset.request_id)
            warnings.append(
                f"asset compilation {asset.request_id} is {asset.status}; "
                "skipped (no writable facts)"
            )
            continue
        key = (asset.request_id, asset.task_hash)
        row = ready_rows.get(key)
        if row is None:
            errors.append(
                f"asset compilation {asset.request_id} has no matching ready "
                "planning row (request/task identity)"
            )
            continue
        if (
            row.request.task_digest != asset.task_digest
            or row.request.run_id != asset.run_id
            or asset.observation.experiment_id != row.request.experiment.experiment_id
        ):
            errors.append(
                f"asset compilation {asset.request_id} identity fields do not "
                "match its planning row request"
            )
            continue
        if row.proposal is None:
            errors.append(
                f"planning row {row.route_id} is ready without a proposal; "
                "cannot derive canonical coverage route"
            )
            continue
        coverage_route = _COVERAGE_ROUTE_BY_STAGE.get(row.proposal.stage)
        if coverage_route is None or coverage_route not in coverage_route_ids:
            errors.append(
                f"planning row route {row.route_id!r} proposal stage "
                f"{row.proposal.stage.value!r} does not map to a coverage "
                "matrix route"
            )
            continue
        if row.proposal is not None and not (
            set(row.proposal.hypothesis_ids) <= plan_hypothesis_ids
        ):
            errors.append(
                f"planning row {row.route_id} proposal references unknown "
                "plan hypotheses"
            )
            continue
        if row.proposal is not None and (
            set(row.request.experiment.hypothesis_ids)
            != set(row.proposal.hypothesis_ids)
        ):
            errors.append(
                f"planning row {row.route_id} request experiment hypotheses "
                "do not match its proposal hypotheses"
            )
            continue
        if (
            row.cells is not None
            and row.proposal is not None
            and (row.cells.stage != row.proposal.stage)
        ):
            errors.append(
                f"planning row {row.route_id} cells stage does not match "
                "proposal stage"
            )
            continue
        if asset.observation.observation_id in observations:
            errors.append(
                f"duplicate observation_id "
                f"{asset.observation.observation_id!r} across assets"
            )
            continue
        descriptor_ids = {item.artifact_id for item in asset.descriptors}
        unknown_value_artifacts = sorted(
            {item.artifact_id for item in asset.trusted_values} - descriptor_ids
        )
        if unknown_value_artifacts:
            errors.append(
                f"asset compilation {asset.request_id} trusted values "
                "reference artifacts absent from its verified descriptor "
                f"inventory: {unknown_value_artifacts}"
            )
            continue
        if asset.warnings:
            warnings.extend(str(item) for item in asset.warnings)
        observations[asset.observation.observation_id] = asset.observation
        usable[asset.request_id] = (asset, row)
    if errors:
        return _invalid_result(errors, warnings, source_plan_id=plan_model.plan_id)
    if not usable:
        unavailable = ArticleResultSynthesisResult(
            status="unavailable",
            result_id="",
            source_plan_id=plan_model.plan_id,
            validation_errors=[],
            warnings=sorted(
                set(
                    [
                        *warnings,
                        "no usable asset compilation could be joined to a "
                        "ready planning row",
                    ]
                )
            ),
        )
        return unavailable.model_copy(
            update={"result_id": compute_result_synthesis_id(unavailable)}
        )

    ordered_rows = [
        row
        for row in planning_model.rows
        if row.status == "ready"
        and row.request is not None
        and row.request.request_id in usable
    ]
    coverage_route_by_observation = {
        asset.observation.observation_id: _COVERAGE_ROUTE_BY_STAGE[row.proposal.stage]
        for _, (asset, row) in usable.items()
    }
    literature_context = (
        build_literature_provider_context(literature_supplement)
        if literature_supplement is not None
        else None
    )
    manifest, payloads = _build_alias_manifest(
        plan_model,
        ordered_rows,
        observations,
        {request_id: item for request_id, (item, _) in usable.items()},
        literature_context=literature_context,
    )
    if provider is None:
        unavailable = ArticleResultSynthesisResult(
            status="unavailable",
            result_id="",
            source_plan_id=plan_model.plan_id,
            validation_errors=[],
            warnings=sorted(
                set(
                    [
                        *warnings,
                        "no synthesis provider supplied; no writable facts "
                        "were fabricated",
                    ]
                )
            ),
        )
        return unavailable.model_copy(
            update={"result_id": compute_result_synthesis_id(unavailable)}
        )

    findings: List[SynthesisFinding] = []
    usage_rows: List[Dict[str, Any]] = []
    findings_by_observation: Dict[str, List[Dict[str, Any]]] = {}
    route_unavailable: List[str] = []
    for request_id, (asset, row) in sorted(usable.items()):
        payload = payloads.get(request_id)
        if payload is None:
            route_unavailable.append(row.route_id)
            warnings.append(
                f"route {row.route_id}: no provider payload was built; skipped"
            )
            continue
        try:
            raw_result = provider(payload)
            provider_result = (
                raw_result
                if isinstance(raw_result, ResultSynthesisProviderResult)
                else ResultSynthesisProviderResult.model_validate(raw_result)
            )
        except Exception as exc:  # noqa: BLE001 - soft provider failure
            route_unavailable.append(row.route_id)
            warnings.append(
                f"route {row.route_id}: synthesis provider unavailable "
                f"({type(exc).__name__}); no facts written for this route"
            )
            continue
        if provider_result.validation_errors:
            route_unavailable.append(row.route_id)
            warnings.append(
                f"route {row.route_id}: provider reported "
                f"{'; '.join(provider_result.validation_errors)}"
            )
        usage_rows.append(dict(provider_result.usage or {}))
        allowlist = _structural_number_allowlist(plan_model, row)
        local_manifest = _local_manifest(manifest, payload.route_alias)
        descriptor_ids = {item.artifact_id for item in asset.descriptors}
        validated = _validated_findings(
            provider_result.findings,
            observation=asset.observation,
            route_id=row.route_id,
            manifest=local_manifest,
            descriptor_ids=descriptor_ids,
            allowlist=allowlist,
            warnings=warnings,
        )
        for index, item in enumerate(validated, start=1):
            finding_id = _stable_digest(
                asset.observation.observation_id,
                row.route_id,
                item["role"],
                item["statement_with_value_tokens"],
                item["source_value_aliases"],
                item["subject_aliases"],
                item["comparison_scope"],
            )
            hypothesis_id = f"hyp-{_stable_digest(finding_id)}"
            finding = SynthesisFinding(
                finding_id=f"finding-{finding_id}",
                observation_id=asset.observation.observation_id,
                route_id=row.route_id,
                role=item["role"],
                statement_with_value_tokens=item["statement_with_value_tokens"],
                source_value_aliases=list(item["source_value_aliases"]),
                subject_aliases=list(item["subject_aliases"]),
                comparison_scope=item["comparison_scope"],
                rationale=item["rationale"],
                scope_limits=item["scope_limits"],
                target_comparison=dict(item.get("target_comparison") or {}),
                synthesized_hypothesis_id=hypothesis_id,
            )
            findings.append(finding)
            findings_by_observation.setdefault(
                asset.observation.observation_id, []
            ).append(
                {
                    **item,
                    "finding_id": finding.finding_id,
                    "synthesized_hypothesis_id": hypothesis_id,
                }
            )

    if not findings:
        unavailable = ArticleResultSynthesisResult(
            status="unavailable",
            result_id="",
            source_plan_id=plan_model.plan_id,
            validation_errors=[],
            warnings=sorted(
                set(
                    [
                        *warnings,
                        "no validated findings survived synthesis; no writable "
                        "facts were fabricated",
                    ]
                )
            ),
            provider_usage=tuple(usage_rows),
        )
        return unavailable.model_copy(
            update={"result_id": compute_result_synthesis_id(unavailable)}
        )

    derived_plan, plan_warnings = _derive_plan(
        plan_model,
        findings,
        manifest,
        literature_supplement=literature_supplement,
    )
    warnings.extend(plan_warnings)
    cloned_observations = _clone_observations(
        [
            observations[observation_id]
            for observation_id in dict.fromkeys(
                item.observation_id for item in findings
            )
        ],
        findings_by_observation,
        manifest,
        {
            observation_id: coverage_route_by_observation[observation_id]
            for observation_id in dict.fromkeys(
                item.observation_id for item in findings
            )
        },
    )
    feedback = ArticleFeedbackController().update(
        derived_plan,
        cloned_observations,
        run_id=run_id,
    )
    if feedback.validation_errors:
        return _invalid_result(
            [*feedback.validation_errors, "feedback rejected synthesized updates"],
            warnings,
            source_plan_id=plan_model.plan_id,
        )
    ledger = build_claim_ledger(
        derived_plan,
        [feedback],
        cloned_observations,
        run_id=run_id,
    )
    if ledger.validation_errors:
        return _invalid_result(
            [*ledger.validation_errors, "claim ledger rejected synthesized inputs"],
            warnings,
            source_plan_id=plan_model.plan_id,
        )
    value_lineage = _synthesis_value_lineage(findings, manifest)
    synthesis_contracts = _synthesis_contracts(findings, manifest)
    enriched_claims = []
    for claim in ledger.claims:
        hypothesis_id = claim.metadata.get("hypothesis_id")
        refs = value_lineage.get(hypothesis_id) or []
        contract = synthesis_contracts.get(hypothesis_id)
        metadata = dict(claim.metadata)
        if refs and not metadata.get("value_lineage"):
            metadata["value_lineage"] = refs
        if contract is not None:
            metadata["synthesis_contract"] = contract
        if metadata != claim.metadata:
            claim = claim.model_copy(
                update={
                    "metadata": metadata,
                }
            )
        enriched_claims.append(claim)
    if enriched_claims != list(ledger.claims):
        ledger = ledger.model_copy(update={"claims": enriched_claims})
    lineage_by_claim = {
        claim.claim_id: claim.metadata["value_lineage"]
        for claim in ledger.claims
        if claim.metadata.get("value_lineage")
    }
    claims_by_id = {claim.claim_id: claim for claim in ledger.claims}
    enriched_facts = []
    for fact in ledger.facts:
        refs = lineage_by_claim.get(fact.metadata.get("claim_id")) or []
        claim = claims_by_id.get(fact.metadata.get("claim_id"))
        metadata = dict(fact.metadata)
        if refs and not metadata.get("value_lineage"):
            metadata["value_lineage"] = refs
        if claim is not None and claim.metadata.get("synthesis_contract"):
            metadata["synthesis_contract"] = claim.metadata["synthesis_contract"]
        if metadata != fact.metadata:
            fact = fact.model_copy(
                update={
                    "metadata": metadata,
                }
            )
        enriched_facts.append(fact)
    if enriched_facts != list(ledger.facts):
        ledger = ledger.model_copy(update={"facts": enriched_facts})
    status: Literal["ready", "partial", "unavailable", "invalid"] = (
        "partial" if route_unavailable or warnings else "ready"
    )
    model = ArticleResultSynthesisResult(
        status=status,
        result_id="",
        source_plan_id=plan_model.plan_id,
        derived_plan=derived_plan,
        observations=cloned_observations,
        feedback_results=(feedback,),
        ledger=ledger,
        alias_manifest=manifest,
        findings=tuple(findings),
        provider_usage=tuple(usage_rows),
        validation_errors=[],
        warnings=sorted(set(warnings)),
    )
    return model.model_copy(update={"result_id": compute_result_synthesis_id(model)})


class QwenArticleResultClaimSynthesizer:
    """Concrete locked qwen3.7-flash provider for one route's findings."""

    def __init__(
        self,
        *,
        client: QwenFlashOnlyClient | None = None,
        prompt_path: str | Path = DEFAULT_PROMPT_PATH,
        max_tokens: int = 6000,
    ) -> None:
        self.client = client or QwenFlashOnlyClient(
            agent_name="ArticleResultClaimSynthesizer"
        )
        self.prompt_path = Path(prompt_path)
        self.max_tokens = int(max_tokens)
        declared_model = getattr(self.client, "model_name", None)
        if declared_model is not None and str(declared_model) != SYNTHESIS_MODEL_NAME:
            raise ValueError(
                "result synthesis model lock violation: client declared "
                f"{declared_model!r}"
            )

    def __call__(
        self,
        payload: ResultSynthesisProviderInput | Mapping[str, Any],
    ) -> ResultSynthesisProviderResult:
        model = (
            payload
            if isinstance(payload, ResultSynthesisProviderInput)
            else ResultSynthesisProviderInput.model_validate(payload)
        )
        system_prompt = self.prompt_path.read_text(encoding="utf-8")
        response = self.client.call(
            [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        model.model_dump(mode="json"), ensure_ascii=False
                    ),
                },
            ],
            max_tokens=self.max_tokens,
        )
        usage = _usage_with_cost(response.get("_llm_usage") or {})
        parsed = _safe_json(str(response.get("content") or ""))
        raw_findings = parsed.get("findings")
        findings = list(raw_findings) if isinstance(raw_findings, list) else []
        return ResultSynthesisProviderResult(
            findings=findings,
            usage=usage,
            provider_model=str(usage.get("model_name") or SYNTHESIS_MODEL_NAME),
            mock_llm=bool(usage.get("mock_llm")),
        )


__all__ = [
    "ArticleResultSynthesisResult",
    "ArtifactView",
    "CandidateView",
    "HypothesisView",
    "ProposalView",
    "QwenArticleResultClaimSynthesizer",
    "ResultSynthesisProvider",
    "ResultSynthesisProviderInput",
    "ResultSynthesisProviderResult",
    "SynthesisAliasRecord",
    "SynthesisFinding",
    "TrustedValueView",
    "compute_result_synthesis_id",
    "synthesize_article_results",
]
