"""Bounded Scientific Director for the Article Scientific Harness.

The director turns one problem analysis and its method-research evidence into
an auditable Research Charter, capability decision, candidate hypotheses,
coverage matrix, and multi-stage research plan.  It reuses the existing
contracts (``ResearchCharter``, ``CoverageMatrix``/``CoverageRow``,
``ArticleStage``, ``OpticalProblemAnalysis``, ``MethodResearchReport``,
``QwenFlashOnlyClient``) and never duplicates them.

Boundaries:
- Capability is classified deterministically from
  ``OpticalProblemAnalysis.compatibility``; Qwen cannot override it.
- Qwen (locked to ``qwen3.7-flash``) drafts only hypotheses, research
  influence, and unresolved decisions.  The program creates all fixed
  contract fields locally: IDs, schema markers, capability decision, charter,
  coverage rows, stage plan, statuses, model name, and usage telemetry.
- Hypothesis evidence IDs must belong to the supplied method research; a
  theory-only candidate is allowed when no evidence exists.
- The original question is preserved exactly (even non-English); the charter
  never invents wavelengths, angles, materials, thresholds, or results.
- Qwen unavailability or malformed output yields an honest invalid/unavailable
  result with usage telemetry; there is no fallback model and no silent
  synthesis.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Mapping, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from optomind_optics.harness.article_contracts import (
    ArticleStage,
    CoverageMatrix,
    CoverageRow,
    CoverageStatus,
    ObservationCard,
    ResearchCharter,
)
from optomind_optics.harness.method_research import (
    MethodEvidence,
    MethodResearchReport,
    MethodResearchStatus,
)
from optomind_optics.harness.problem_analyzer import (
    OpticalProblemAnalysis,
    TMMCompatibility,
)
from optomind_optics.harness.qwen_policy import QWEN_POLICY_MODEL, QwenFlashOnlyClient


DIRECTOR_MODEL_NAME = QWEN_POLICY_MODEL
DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "optical_harness"
    / "Article Scientific Director.txt"
)

# Documented compact-view bounds for the Qwen payload (not a tiny arbitrary cap).
EVIDENCE_EXCERPT_CHARS = 600
MAX_EVIDENCE_ITEMS = 40
MAX_FINDING_ITEMS = 20
MAX_PRIOR_OBSERVATIONS = 20
MAX_HYPOTHESES = 8


# ---------------------------------------------------------------------------
# Director-specific strict models (literal versions, frozen, forbid extra)
# ---------------------------------------------------------------------------


class _DirectorModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CapabilityDecision(_DirectorModel):
    schema_version: Literal["capability-decision.v1"] = "capability-decision.v1"
    capability_id: str
    status: TMMCompatibility
    supported_scope: str
    unsupported_requirements: List[str] = Field(default_factory=list)
    accepted_assumptions: List[str] = Field(default_factory=list)
    clarification_questions: List[str] = Field(default_factory=list)
    recommended_next_action: str


class HypothesisCandidate(_DirectorModel):
    schema_version: Literal["hypothesis-candidate.v1"] = "hypothesis-candidate.v1"
    hypothesis_id: str
    statement: str
    falsifiable_prediction: str
    expected_observations: List[str] = Field(default_factory=list)
    disconfirming_observations: List[str] = Field(default_factory=list)
    evidence_ids: List[str] = Field(default_factory=list)
    theory_basis: str
    route_kind: str
    parent_hypothesis_id: Optional[str] = None
    novelty_rationale: str = ""
    risk_notes: str = ""

    @field_validator("statement", "falsifiable_prediction", "theory_basis", "route_kind")
    @classmethod
    def _required_text(cls, value: str, info: Any) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{info.field_name} must be non-empty")
        return text

    @field_validator("evidence_ids")
    @classmethod
    def _unique_evidence_ids(cls, values: List[str]) -> List[str]:
        cleaned = [str(item).strip() for item in values if str(item).strip()]
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("evidence_ids must be unique")
        return cleaned


class EvidenceIdentityManifest(_DirectorModel):
    """Deterministic local identity of one method evidence record.

    The director binds this manifest into the plan identity; downstream
    stages compare every cited MethodEvidence against it exactly so an
    attacker cannot swap paper/text under the same evidence_id.
    """

    schema_version: Literal["evidence-identity-manifest.v1"] = (
        "evidence-identity-manifest.v1"
    )
    evidence_id: str
    paper_id: str
    doi: str = ""
    title: str
    year: Optional[int] = None
    source_route: str
    content_depth: str
    allowed_use: str
    text_sha256: str


class DirectorStagePlanItem(_DirectorModel):
    schema_version: Literal["director-stage-plan-item.v1"] = (
        "director-stage-plan-item.v1"
    )
    item_id: str
    stage: ArticleStage
    objective: str
    required_input_domains: List[str] = Field(default_factory=list)
    outputs: List[str] = Field(default_factory=list)
    stop_conditions: List[str] = Field(default_factory=list)
    depends_on: List[str] = Field(default_factory=list)
    status: Literal["planned", "not_run"]


class ArticleDirectorPlan(_DirectorModel):
    schema_version: Literal["article-director-plan.v1"] = "article-director-plan.v1"
    plan_id: str
    question: str
    charter: ResearchCharter
    capability: CapabilityDecision
    hypotheses: List[HypothesisCandidate]
    coverage_matrix: CoverageMatrix
    stage_plan: List[DirectorStagePlanItem]
    research_influence: List[str] = Field(default_factory=list)
    unresolved_decisions: List[str] = Field(default_factory=list)
    evidence_identity: List[EvidenceIdentityManifest] = Field(
        default_factory=list
    )


class ArticleDirectorResult(_DirectorModel):
    schema_version: Literal["article-director-result.v1"] = "article-director-result.v1"
    status: Literal["planned", "invalid", "unavailable"]
    plan: Optional[ArticleDirectorPlan] = None
    attempts: int = 0
    validation_errors: List[str] = Field(default_factory=list)
    normalization_warnings: List[str] = Field(default_factory=list)
    usage: Dict[str, Any] = Field(default_factory=dict)
    model_name: Literal["qwen3.7-flash"] = DIRECTOR_MODEL_NAME


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class DirectorDraftUnavailableError(RuntimeError):
    """Qwen was unreachable or its response was not parseable."""

    def __init__(
        self,
        message: str,
        *,
        usage: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.usage = dict(usage or {})


class DirectorDraftInvalidError(ValueError):
    """Qwen returned JSON that violates the director contract."""


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


def _stable_digest(*parts: Any) -> str:
    canonical = json.dumps(
        [str(part) for part in parts], sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[:16]


def _unique_texts(values: Iterable[Any]) -> List[str]:
    result: List[str] = []
    for item in values:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


_STAGE_PLAN_SPECS: Tuple[Tuple[ArticleStage, str, List[str], List[str], List[str]], ...] = (
    (
        ArticleStage.charter_locked,
        "Lock the research charter and capability boundary.",
        ["research_charter", "capability_decision"],
        ["coverage_matrix", "stage_plan"],
        ["charter locked and capability classified"],
    ),
    (
        ArticleStage.capability_classified,
        "Classify the TMM capability boundary from the problem analysis.",
        ["research_charter", "problem_analysis"],
        ["capability_decision"],
        ["capability classified; unsupported requirements recorded"],
    ),
    (
        ArticleStage.literature_integrated,
        "Integrate bounded method evidence into hypotheses and route rationale.",
        ["method_evidence", "method_findings"],
        ["literature_context", "evidence_allowlist"],
        ["evidence allowlist bounded and grounded"],
    ),
    (
        ArticleStage.coverage_matrix_locked,
        "Lock the planned experiment coverage matrix.",
        ["charter", "hypotheses", "capability_decision"],
        ["coverage_matrix"],
        ["coverage rows planned or not_run with reasons"],
    ),
    (
        ArticleStage.hypotheses_formed,
        "Finalize candidate hypotheses with falsifiable predictions.",
        ["capability_decision", "literature_context", "coverage_matrix"],
        ["hypothesis_candidates"],
        ["hypotheses falsifiable and evidence-bound or theory-based"],
    ),
    (
        ArticleStage.baseline_experiments,
        "Run bounded baseline experiments for every planned route.",
        ["charter", "hypotheses", "coverage_matrix"],
        ["observation_cards", "baseline_candidates"],
        ["baseline candidates verified or budget/route exhausted"],
    ),
    (
        ArticleStage.exploration,
        "Explore bounded topology/material/parameter variants.",
        ["baseline_observations", "hypotheses"],
        ["exploration_observations"],
        ["route exhaustion or marginal improvement"],
    ),
    (
        ArticleStage.controlled_improvement,
        "Apply single-variable atomic improvements against a fixed evaluator.",
        ["exploration_observations", "hypotheses"],
        ["improvement_observations"],
        ["no atomic improvement within budget"],
    ),
    (
        ArticleStage.discriminative_experiments,
        "Run experiments whose expected discriminator distinguishes hypotheses.",
        ["hypotheses", "expected_discriminators"],
        ["discriminative_observations"],
        ["hypotheses updated or discriminating experiment impossible"],
    ),
    (
        ArticleStage.robustness_ablation,
        "Evaluate robustness, ablations, repeats, and uncertainty.",
        ["candidate_observations"],
        ["robustness_observations", "ablation_observations"],
        ["robustness and ablation coverage complete or budget exhausted"],
    ),
    (
        ArticleStage.hypothesis_update,
        "Update hypothesis statuses from real observations only.",
        ["observations"],
        ["hypothesis_status_updates"],
        ["hypotheses updated or evidence insufficient"],
    ),
    (
        ArticleStage.claim_ledger,
        "Compile evidence-bound claim cards and fact registry entries.",
        ["facts", "observations"],
        ["claim_cards", "fact_registry"],
        ["claims evidence-bound or explicitly unsupported"],
    ),
    (
        ArticleStage.figure_first_planning,
        "Plan the article story and figures from verified claims.",
        ["claims", "figure_cards"],
        ["figure_cards", "story_plan"],
        ["figure contract locked"],
    ),
    (
        ArticleStage.section_writing,
        "Draft all article sections bound to facts and figures.",
        ["claims", "figure_cards"],
        ["section_drafts"],
        ["all sections drafted"],
    ),
    (
        ArticleStage.fact_audit,
        "Audit drafts for unknown facts and references.",
        ["section_drafts", "fact_registry"],
        ["fact_audit_report"],
        ["no unknown facts or references"],
    ),
    (
        ArticleStage.scientific_review,
        "Review scientific claims, evidence, and scope.",
        ["section_drafts", "claims"],
        ["scientific_review_cards"],
        ["scientific review resolved or waived"],
    ),
    (
        ArticleStage.expression_review,
        "Review expression and presentation without blocking physics.",
        ["section_drafts"],
        ["expression_review_cards"],
        ["expression review resolved or waived"],
    ),
    (
        ArticleStage.author_revision,
        "Apply bounded author revisions from review cards.",
        ["review_cards"],
        ["revised_draft"],
        ["revisions applied"],
    ),
    (
        ArticleStage.fresh_replay,
        "Fresh-process replay of the final deterministic run.",
        ["final_run", "artifacts"],
        ["replay_manifest"],
        ["fresh replay matches or failure recorded honestly"],
    ),
    (
        ArticleStage.publication_package,
        "Produce Markdown, LaTeX, PDF, and arXiv package.",
        ["revised_draft", "figures", "replay_manifest"],
        ["markdown", "latex", "pdf", "arxiv_source_package"],
        ["publication integrity checks pass or blockers recorded"],
    ),
)


_COVERAGE_ROWS: Tuple[Tuple[str, str], ...] = (
    ("baseline", "Baseline experiments"),
    ("exploration", "Exploration of topology/material/parameter variants"),
    ("controlled_improvement", "Controlled single-variable improvements"),
    ("discriminative_experiments", "Discriminative experiments between hypotheses"),
    ("robustness_ablation", "Robustness and ablation experiments"),
    ("fresh_replay", "Fresh-process replay of the final run"),
)


def _usage_payload(
    *,
    mock: bool,
    call_count: int,
    attempts: Sequence[Mapping[str, Any]],
    input_tokens: int = 0,
    output_tokens: int = 0,
    estimated_cost_cny: float = 0.0,
) -> Dict[str, Any]:
    return {
        "model_name": DIRECTOR_MODEL_NAME,
        "mock_llm": bool(mock),
        "call_count": int(call_count),
        "attempts": [dict(item) for item in attempts],
        "estimated_input_tokens": int(input_tokens),
        "estimated_output_tokens": int(output_tokens),
        "estimated_cost_cny": float(estimated_cost_cny),
    }


def _aggregate_director_usage(
    attempt_rows: Sequence[Mapping[str, Any]],
    *,
    mock: bool,
) -> Dict[str, Any]:
    """Aggregate per-attempt provider usage without losing telemetry."""

    def tokens(row: Mapping[str, Any], keys: Sequence[str]) -> int:
        for key in keys:
            value = row.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return int(value)
        return 0

    input_tokens = sum(
        tokens(row, ("input_tokens", "estimated_input_tokens"))
        for row in attempt_rows
    )
    output_tokens = sum(
        tokens(row, ("output_tokens", "estimated_output_tokens"))
        for row in attempt_rows
    )
    provider_costs = [
        row.get("estimated_list_price_cost_cny")
        for row in attempt_rows
        if isinstance(row.get("estimated_list_price_cost_cny"), (int, float))
        and not isinstance(row.get("estimated_list_price_cost_cny"), bool)
    ]
    if provider_costs and any(value != 0 for value in provider_costs):
        estimated_cost_cny = round(
            sum(float(value) for value in provider_costs),
            8,
        )
    elif input_tokens or output_tokens:
        from optomind_research.runtime.cost_ledger import (
            estimate_call_cost_cny,
        )

        model_name = next(
            (
                str(row.get("model_name") or "")
                for row in attempt_rows
                if row.get("model_name")
            ),
            DIRECTOR_MODEL_NAME,
        )
        estimated_cost_cny = round(
            float(
                estimate_call_cost_cny(
                    model_name,
                    input_tokens,
                    output_tokens,
                )
            ),
            8,
        )
    else:
        estimated_cost_cny = 0.0
    return _usage_payload(
        mock=mock,
        call_count=len(attempt_rows) or 1,
        attempts=attempt_rows,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_cny=estimated_cost_cny,
    )


# ---------------------------------------------------------------------------
# Scientific Director
# ---------------------------------------------------------------------------


class ArticleDirector:
    """Bounded director: analysis + method evidence -> auditable research plan."""

    def __init__(
        self,
        *,
        prompt_path: str | Path = DEFAULT_PROMPT_PATH,
        client: QwenFlashOnlyClient | None = None,
    ) -> None:
        self.prompt_path = Path(prompt_path)
        self.client = client or QwenFlashOnlyClient(agent_name="ArticleScientificDirector")

    # -- public API ---------------------------------------------------------

    def plan(
        self,
        question: str,
        analysis: OpticalProblemAnalysis | Mapping[str, Any],
        method_research: MethodResearchReport | Mapping[str, Any],
        prior_observations: Iterable[Any] = (),
        force_mock: bool | None = None,
    ) -> ArticleDirectorResult:
        """Plan one research run; see module docstring for boundaries."""

        if not isinstance(question, str) or not question.strip():
            return self._invalid(
                ["question must be a non-empty string"],
                usage=_usage_payload(mock=bool(force_mock), call_count=0, attempts=[]),
            )
        try:
            analysis_model = (
                analysis
                if isinstance(analysis, OpticalProblemAnalysis)
                else OpticalProblemAnalysis.model_validate(analysis)
            )
        except ValidationError as exc:
            return self._invalid(
                [f"analysis is invalid: {exc}"],
                usage=_usage_payload(mock=bool(force_mock), call_count=0, attempts=[]),
            )
        try:
            report = (
                method_research
                if isinstance(method_research, MethodResearchReport)
                else MethodResearchReport.model_validate(method_research)
            )
        except ValidationError as exc:
            return self._invalid(
                [f"method_research is invalid: {exc}"],
                usage=_usage_payload(mock=bool(force_mock), call_count=0, attempts=[]),
            )

        warnings: List[str] = []
        if report.status == MethodResearchStatus.unavailable:
            warnings.append(
                "method research unavailable; hypotheses must use theory_basis"
            )

        capability = self._capability_decision(analysis_model)
        if capability.status == TMMCompatibility.incompatible:
            errors = [
                "capability incompatible: " + analysis_model.compatibility_reason,
                "unsupported requirements: "
                + ", ".join(capability.unsupported_requirements),
                "recommended next action: stop_capability_boundary",
            ]
            return self._invalid(
                errors,
                usage=_usage_payload(mock=bool(force_mock), call_count=0, attempts=[]),
                warnings=warnings,
            )

        visible_evidence = self._visible_evidence(report)
        if len(visible_evidence) < len(report.evidence):
            warnings.append(
                f"method evidence truncated to {len(visible_evidence)} records; "
                "hypotheses may cite only visible evidence"
            )
        allowed_evidence_ids = [item.evidence_id for item in visible_evidence]
        alias_to_evidence = {
            f"E{index:02d}": item.evidence_id
            for index, item in enumerate(visible_evidence, start=1)
        }
        usage: Dict[str, Any] = _usage_payload(
            mock=bool(force_mock), call_count=1, attempts=[]
        )
        try:
            raw_hypotheses, influence, unresolved, usage = self._draft(
                question=question,
                analysis=analysis_model,
                report=report,
                visible_evidence=visible_evidence,
                prior_observations=prior_observations,
                force_mock=force_mock,
                alias_to_evidence=alias_to_evidence,
            )
        except DirectorDraftUnavailableError as exc:
            retained_usage = exc.usage or _usage_payload(
                mock=bool(force_mock),
                call_count=1,
                attempts=[{"error": str(exc)}],
            )
            return ArticleDirectorResult(
                status="unavailable",
                attempts=int(retained_usage.get("call_count") or 1),
                validation_errors=[str(exc)],
                normalization_warnings=warnings,
                usage=retained_usage,
            )
        except DirectorDraftInvalidError as exc:
            return self._invalid(
                [str(exc)],
                usage=usage,
                warnings=warnings,
                attempts=int(usage.get("call_count") or 1),
            )

        try:
            hypotheses = self._build_hypotheses(
                raw_hypotheses,
                allowed_evidence_ids,
                alias_to_evidence,
            )
        except DirectorDraftInvalidError as exc:
            return self._invalid(
                [str(exc)],
                usage=usage,
                warnings=warnings,
                attempts=int(usage.get("call_count") or 1),
            )

        plan = self._assemble_plan(
            question=question,
            analysis=analysis_model,
            capability=capability,
            report=report,
            hypotheses=hypotheses,
            influence=influence,
            unresolved=unresolved,
        )
        return ArticleDirectorResult(
            status="planned",
            plan=plan,
            attempts=int(usage.get("call_count") or 1),
            normalization_warnings=warnings,
            usage=usage,
        )

    # -- local deterministic decision layer ---------------------------------

    def _capability_decision(self, analysis: OpticalProblemAnalysis) -> CapabilityDecision:
        capability_id = "capability-" + _stable_digest(
            analysis.problem_id, analysis.compatibility.value, analysis.compatibility_reason
        )
        common = dict(
            schema_version="capability-decision.v1",
            capability_id=capability_id,
            status=analysis.compatibility,
            accepted_assumptions=list(analysis.assumptions),
        )
        if analysis.compatibility == TMMCompatibility.compatible:
            return CapabilityDecision(
                **common,
                supported_scope=analysis.normalized_request_english,
                unsupported_requirements=[],
                clarification_questions=[],
                recommended_next_action="proceed_with_planning",
            )
        if analysis.compatibility == TMMCompatibility.ambiguous:
            questions = _unique_texts(analysis.ambiguities) or [
                analysis.compatibility_reason
            ]
            return CapabilityDecision(
                **common,
                supported_scope=analysis.normalized_request_english,
                unsupported_requirements=[],
                clarification_questions=questions,
                recommended_next_action="clarify_before_experiments",
            )
        return CapabilityDecision(
            **common,
            supported_scope="",
            unsupported_requirements=[analysis.compatibility_reason],
            clarification_questions=[],
            recommended_next_action="stop_capability_boundary",
        )

    def _charter_constraints(self, analysis: OpticalProblemAnalysis) -> List[str]:
        constraints = _unique_texts(analysis.manufacturing_constraints)
        if analysis.wavelengths_nm:
            constraints.append(
                "wavelength ranges (nm): "
                + ", ".join(
                    f"{float(lo)}-{float(hi)}" for lo, hi in analysis.wavelengths_nm
                )
            )
        if analysis.angles_deg:
            constraints.append(
                "incidence angles (deg): " + ", ".join(str(float(v)) for v in analysis.angles_deg)
            )
        if analysis.polarizations:
            constraints.append("polarizations: " + ", ".join(analysis.polarizations))
        if analysis.known_stack_materials:
            constraints.append(
                "candidate/known materials: " + ", ".join(analysis.known_stack_materials)
            )
        if analysis.secondary_intents:
            constraints.append(
                "secondary intents: "
                + ", ".join(item.value for item in analysis.secondary_intents)
            )
        if analysis.design_variables:
            constraints.append(
                "design variables: " + ", ".join(analysis.design_variables)
            )
        if analysis.suppressed_behaviors:
            constraints.append(
                "suppressed behaviors: " + ", ".join(analysis.suppressed_behaviors)
            )
        if analysis.ambiguities:
            constraints.append("ambiguities: " + ", ".join(analysis.ambiguities))
        constraints.extend(analysis.assumptions)
        constraints.append(
            "TMM capability: "
            + analysis.compatibility.value
            + " ("
            + analysis.compatibility_reason
            + ")"
        )
        return _unique_texts(constraints)

    def _assemble_plan(
        self,
        *,
        question: str,
        analysis: OpticalProblemAnalysis,
        capability: CapabilityDecision,
        report: MethodResearchReport,
        hypotheses: List[HypothesisCandidate],
        influence: List[str],
        unresolved: List[str],
    ) -> ArticleDirectorPlan:
        evidence_identity = [
            EvidenceIdentityManifest(
                evidence_id=item.evidence_id,
                paper_id=item.paper_id,
                doi=item.doi,
                title=item.title,
                year=item.year,
                source_route=item.source_route,
                content_depth=item.content_depth.value,
                allowed_use=item.allowed_use.value,
                text_sha256=hashlib.sha256(
                    item.text.encode("utf-8")
                ).hexdigest(),
            )
            for item in report.evidence
        ]
        evidence_salt = json.dumps(
            [entry.model_dump(mode="json") for entry in evidence_identity],
            sort_keys=True,
            separators=(",", ":"),
        )
        plan_salt = "|".join(
            (question, analysis.problem_id, capability.status.value, evidence_salt)
        )
        plan_id = "plan-" + _stable_digest(plan_salt)
        charter_id = "charter-" + _stable_digest(plan_salt)
        matrix_id = "matrix-" + _stable_digest(plan_salt)

        goals = _unique_texts(analysis.target_observables) or [
            analysis.normalized_request_english
        ]
        charter = ResearchCharter(
            schema_version="research-charter.v1",
            charter_id=charter_id,
            question=question,
            scope=analysis.normalized_request_english,
            goals=goals,
            success_criteria=_unique_texts(analysis.preferred_behaviors),
            constraints=self._charter_constraints(analysis),
            budget={},
            deliverables=[
                "research_charter",
                "candidate_hypotheses",
                "coverage_matrix",
                "stage_plan",
            ],
            stage=ArticleStage.charter_locked,
        )

        ambiguous = capability.status == TMMCompatibility.ambiguous
        not_run_reason = (
            "capability ambiguous; clarification required before experiments"
            if ambiguous
            else ""
        )
        rows = [
            CoverageRow(
                schema_version="coverage-row.v1",
                route_id=route_id,
                title=title,
                coverage_status=(
                    CoverageStatus.not_run if ambiguous else CoverageStatus.planned
                ),
                evidence_artifact_ids=[],
                not_run_reason=not_run_reason,
            )
            for route_id, title in _COVERAGE_ROWS
        ]
        coverage_matrix = CoverageMatrix(
            schema_version="coverage-matrix.v1",
            matrix_id=matrix_id,
            rows=rows,
        )

        stage_plan: List[DirectorStagePlanItem] = []
        for index, (stage, objective, inputs, outputs, stop_conditions) in enumerate(
            _STAGE_PLAN_SPECS, start=1
        ):
            depends = [f"stage-{index - 1:02d}"] if index > 1 else []
            stops = list(stop_conditions)
            if ambiguous:
                stops.append(not_run_reason)
            stage_plan.append(
                DirectorStagePlanItem(
                    schema_version="director-stage-plan-item.v1",
                    item_id=f"stage-{index:02d}",
                    stage=stage,
                    objective=objective,
                    required_input_domains=inputs,
                    outputs=outputs,
                    stop_conditions=stops,
                    depends_on=depends,
                    status="not_run" if ambiguous else "planned",
                )
            )

        return ArticleDirectorPlan(
            schema_version="article-director-plan.v1",
            plan_id=plan_id,
            question=question,
            charter=charter,
            capability=capability,
            hypotheses=hypotheses,
            coverage_matrix=coverage_matrix,
            stage_plan=stage_plan,
            research_influence=influence,
            unresolved_decisions=unresolved,
            evidence_identity=evidence_identity,
        )

    # -- Qwen drafting (bounded, contract-validated) -------------------------

    @staticmethod
    def _visible_evidence(report: MethodResearchReport) -> List[MethodEvidence]:
        """The bounded evidence set actually included in the Qwen prompt."""

        return list(report.evidence[:MAX_EVIDENCE_ITEMS])

    def _draft(
        self,
        *,
        question: str,
        analysis: OpticalProblemAnalysis,
        report: MethodResearchReport,
        visible_evidence: Sequence[MethodEvidence],
        prior_observations: Iterable[Any],
        force_mock: bool | None,
        alias_to_evidence: Mapping[str, str],
    ) -> Tuple[List[Dict[str, Any]], List[str], List[str], Dict[str, Any]]:
        payload = self._prompt_payload(
            question,
            analysis,
            report,
            visible_evidence,
            prior_observations,
            alias_to_evidence,
        )
        if force_mock:
            hypotheses, influence, unresolved = self._mock_draft(
                analysis, visible_evidence
            )
            usage = _usage_payload(
                mock=True,
                call_count=1,
                attempts=[{"mode": "deterministic_mock", "mock_llm": True}],
                input_tokens=len(json.dumps(payload, ensure_ascii=False)),
            )
            return hypotheses, influence, unresolved, usage

        prompt = self.prompt_path.read_text(encoding="utf-8")
        messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            },
        ]
        attempt_rows: List[Dict[str, Any]] = []

        def record_attempt(response: Mapping[str, Any]) -> None:
            attempt_rows.append(dict(response.get("_llm_usage") or {}))

        try:
            response = self.client.call(messages, max_tokens=2200, force_mock=False)
        except Exception as exc:  # network, policy lock, transport errors
            raise DirectorDraftUnavailableError(f"Qwen unavailable: {exc}") from exc
        record_attempt(response)
        parsed = _safe_json(str(response.get("content") or ""))
        if not parsed:
            repair_messages = [
                *messages,
                {
                    "role": "user",
                    "content": (
                        "The previous response was empty or not valid JSON. "
                        "Return ONLY a JSON object with exactly these keys: "
                        '"hypotheses" (non-empty array), "research_influence" '
                        '(array), "unresolved_decisions" (array). No prose.'
                    ),
                },
            ]
            try:
                response = self.client.call(
                    repair_messages,
                    max_tokens=2200,
                    force_mock=False,
                )
            except Exception as exc:
                raise DirectorDraftUnavailableError(
                    f"Qwen format repair unavailable: {exc}",
                    usage=_aggregate_director_usage(
                        attempt_rows, mock=False
                    ),
                ) from exc
            record_attempt(response)
            parsed = _safe_json(str(response.get("content") or ""))
            if not parsed:
                raise DirectorDraftUnavailableError(
                    "Qwen response was empty or not JSON after one "
                    "format-repair retry; no fallback model used",
                    usage=_aggregate_director_usage(
                        attempt_rows, mock=False
                    ),
                )
        usage = _aggregate_director_usage(attempt_rows, mock=False)
        raw_hypotheses = parsed.get("hypotheses")
        if not isinstance(raw_hypotheses, list) or not raw_hypotheses:
            raise DirectorDraftInvalidError("Qwen returned no hypotheses")
        influence = _unique_texts(parsed.get("research_influence") or [])
        unresolved = _unique_texts(parsed.get("unresolved_decisions") or [])
        return raw_hypotheses, influence, unresolved, usage

    def _build_hypotheses(
        self,
        raw_hypotheses: Sequence[Any],
        allowed_evidence_ids: Sequence[str],
        alias_to_evidence: Optional[Mapping[str, str]] = None,
    ) -> List[HypothesisCandidate]:
        allowed = set(allowed_evidence_ids)
        alias_map = dict(alias_to_evidence or {})
        hypotheses: List[HypothesisCandidate] = []
        errors: List[str] = []
        for index, item in enumerate(raw_hypotheses[:MAX_HYPOTHESES]):
            if not isinstance(item, Mapping):
                errors.append(f"hypotheses[{index}] must be an object")
                continue
            statement = str(item.get("statement") or "").strip()
            prediction = str(item.get("falsifiable_prediction") or "").strip()
            theory = str(item.get("theory_basis") or "").strip()
            evidence_ids = _unique_texts(item.get("evidence_ids") or [])
            evidence_ids = _unique_texts(
                alias_map.get(ref, ref) for ref in evidence_ids
            )
            if not statement:
                errors.append(f"hypotheses[{index}].statement is empty")
            if not prediction:
                errors.append(f"hypotheses[{index}].falsifiable_prediction is missing")
            unknown = sorted(set(evidence_ids) - allowed)
            if unknown:
                errors.append(
                    f"hypotheses[{index}] references unknown evidence ids: {unknown}"
                )
            if not evidence_ids and not theory:
                errors.append(
                    f"hypotheses[{index}] has no evidence_ids and no theory_basis"
                )
            parent = str(item.get("parent_hypothesis_id") or "").strip() or None
            known_ids = {candidate.hypothesis_id for candidate in hypotheses}
            if parent is not None and parent not in known_ids:
                errors.append(
                    f"hypotheses[{index}].parent_hypothesis_id is unknown: {parent}"
                )
            try:
                candidate = HypothesisCandidate(
                    schema_version="hypothesis-candidate.v1",
                    hypothesis_id=f"hyp-{index + 1:02d}",
                    statement=statement,
                    falsifiable_prediction=prediction,
                    expected_observations=_unique_texts(
                        item.get("expected_observations") or []
                    ),
                    disconfirming_observations=_unique_texts(
                        item.get("disconfirming_observations") or []
                    ),
                    evidence_ids=evidence_ids,
                    theory_basis=theory,
                    route_kind=str(item.get("route_kind") or "baseline_experiments"),
                    parent_hypothesis_id=parent,
                    novelty_rationale=str(item.get("novelty_rationale") or "").strip(),
                    risk_notes=str(item.get("risk_notes") or "").strip(),
                )
            except ValidationError as exc:
                errors.append(f"hypotheses[{index}] invalid: {exc}")
                continue
            hypotheses.append(candidate)
        if errors:
            raise DirectorDraftInvalidError("; ".join(errors))
        if not hypotheses:
            raise DirectorDraftInvalidError("no valid hypotheses were supplied")
        return hypotheses

    def _mock_draft(
        self,
        analysis: OpticalProblemAnalysis,
        visible_evidence: Sequence[MethodEvidence],
    ) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
        allowed = [item.evidence_id for item in visible_evidence]
        first_evidence = allowed[0] if allowed else None
        scope = analysis.normalized_request_english
        base = {
            "expected_observations": [
                "declared soft observables improve relative to the baseline"
            ],
            "disconfirming_observations": [
                "passivity or energy audit fails",
                "worst-case observable worsens under declared uncertainty",
            ],
            "route_kind": "baseline_experiments",
            "novelty_rationale": "Offline mock mode: exercises the director contract only.",
            "risk_notes": "Mock: not a scientific claim.",
        }
        first = {
            **base,
            "statement": (
                f"Over the declared scope ({scope}) a bounded multilayer topology "
                "can meet the soft observables within declared tolerances."
            ),
            "falsifiable_prediction": (
                "Simulated R/T/A over the declared bands, angles, and polarizations "
                "will pass the deterministic solver audit within tolerance."
            ),
            "evidence_ids": [first_evidence] if first_evidence else [],
            "theory_basis": (
                "Offline mock mode: deterministic theory-based candidate; "
                "literature influence is not synthesized."
            ),
            "parent_hypothesis_id": None,
        }
        second = {
            **base,
            "route_kind": "controlled_improvement",
            "statement": (
                f"Within the declared scope ({scope}) a refined topology can "
                "improve the best verified baseline without violating constraints."
            ),
            "falsifiable_prediction": (
                "A controlled single-variable change improves the verified "
                "objective score while preserving physical validity."
            ),
            "evidence_ids": [first_evidence] if first_evidence else [],
            "theory_basis": (
                "Offline mock mode: deterministic refinement hypothesis; "
                "no literature synthesis claimed."
            ),
            "parent_hypothesis_id": "hyp-01",
        }
        influence = ["Offline mock mode: no literature influence claimed."]
        unresolved = ["Offline mock mode: unresolved decisions are not inferred."]
        return [first, second], influence, unresolved

    # -- bounded prompt payload ----------------------------------------------

    def _prompt_payload(
        self,
        question: str,
        analysis: OpticalProblemAnalysis,
        report: MethodResearchReport,
        visible_evidence: Sequence[MethodEvidence],
        prior_observations: Iterable[Any],
        alias_to_evidence: Mapping[str, str],
    ) -> Dict[str, Any]:
        evidence_to_alias = {
            evidence_id: alias
            for alias, evidence_id in alias_to_evidence.items()
        }
        evidence = []
        for alias, item in zip(
            sorted(alias_to_evidence),
            visible_evidence,
        ):
            evidence.append(
                {
                    "alias": alias,
                    "source_route": item.source_route,
                    "paper_id": item.paper_id,
                    "title": item.title,
                    "content_depth": item.content_depth.value,
                    "allowed_use": item.allowed_use.value,
                    "excerpt": item.text[:EVIDENCE_EXCERPT_CHARS],
                }
            )
        findings = []
        for item in report.method_findings[:MAX_FINDING_ITEMS]:
            findings.append(
                {
                    "design_family": item.design_family,
                    "method_name": item.method_name,
                    "reusable_principle": item.reusable_principle,
                    "applicability": item.applicability,
                    "limitations": item.limitations,
                    "evidence_ids": [
                        evidence_to_alias.get(evidence_id, evidence_id)
                        for evidence_id in item.evidence_ids
                    ],
                }
            )
        prior_list = list(prior_observations)[:MAX_PRIOR_OBSERVATIONS]
        observations = []
        for index, raw in enumerate(prior_list):
            if isinstance(raw, ObservationCard):
                payload = raw.model_dump(mode="json")
            elif isinstance(raw, Mapping):
                payload = dict(raw)
            else:
                payload = {"summary": str(raw)}
            observations.append(
                {
                    "observation_id": str(
                        payload.get("observation_id") or f"prior-{index + 1:02d}"
                    ),
                    "experiment_id": str(payload.get("experiment_id") or ""),
                    "status": str(payload.get("status") or ""),
                    "summary": str(payload.get("summary") or "")[:600],
                    "artifact_ids": [
                        str(item) for item in (payload.get("artifact_ids") or [])
                    ][:16],
                }
            )
        return {
            "task": "Scientific director drafting: hypotheses, research influence, unresolved decisions",
            "original_question": question,
            "analysis": {
                "problem_id": analysis.problem_id,
                "normalized_request_english": analysis.normalized_request_english,
                "primary_intent": analysis.primary_intent.value,
                "compatibility": analysis.compatibility.value,
                "compatibility_reason": analysis.compatibility_reason,
                "wavelengths_nm": [
                    [float(lo), float(hi)] for lo, hi in analysis.wavelengths_nm
                ],
                "angles_deg": [float(v) for v in analysis.angles_deg],
                "polarizations": list(analysis.polarizations),
                "target_observables": list(analysis.target_observables),
                "preferred_behaviors": list(analysis.preferred_behaviors),
                "suppressed_behaviors": list(analysis.suppressed_behaviors),
                "known_stack_materials": list(analysis.known_stack_materials),
                "design_variables": list(analysis.design_variables),
                "manufacturing_constraints": list(analysis.manufacturing_constraints),
                "assumptions": list(analysis.assumptions),
                "ambiguities": list(analysis.ambiguities),
            },
            "method_evidence": evidence,
            "method_findings": findings,
            "allowed_evidence_refs": sorted(alias_to_evidence),
            "prior_observations": observations,
            "output_contract": {
                "keys": ["hypotheses", "research_influence", "unresolved_decisions"],
                "hypothesis_fields": [
                    "statement",
                    "falsifiable_prediction",
                    "expected_observations",
                    "disconfirming_observations",
                    "evidence_ids",
                    "theory_basis",
                    "route_kind",
                    "parent_hypothesis_id",
                    "novelty_rationale",
                    "risk_notes",
                ],
            },
        }

    # -- result helpers ------------------------------------------------------

    @staticmethod
    def _invalid(
        errors: Sequence[str],
        *,
        usage: Dict[str, Any],
        warnings: Sequence[str] = (),
        attempts: int = 0,
    ) -> ArticleDirectorResult:
        return ArticleDirectorResult(
            status="invalid",
            attempts=attempts,
            validation_errors=[str(item) for item in errors],
            normalization_warnings=[str(item) for item in warnings],
            usage=usage,
        )


__all__ = [
    "ArticleDirector",
    "ArticleDirectorPlan",
    "ArticleDirectorResult",
    "CapabilityDecision",
    "DIRECTOR_MODEL_NAME",
    "DirectorStagePlanItem",
    "EvidenceIdentityManifest",
    "HypothesisCandidate",
]
