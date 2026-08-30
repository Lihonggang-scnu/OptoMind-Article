from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from optomind_optics.harness.article_contracts import (
    ArticleStage,
    CoverageStatus,
    ObservationCard,
)
from optomind_optics.harness.article_director import (
    ArticleDirector,
    ArticleDirectorResult,
    DIRECTOR_MODEL_NAME,
)
from optomind_optics.harness.contracts import ExperimentStatus
from optomind_optics.harness.method_research import (
    MethodAllowedUse,
    MethodContentDepth,
    MethodEvidence,
    MethodFinding,
    MethodResearchReport,
    MethodResearchStatus,
)
from optomind_optics.harness.problem_analyzer import (
    OpticalProblemAnalysis,
    ResearchIntent,
    TMMCompatibility,
)


def _analysis(compatibility: str = "compatible", **overrides) -> OpticalProblemAnalysis:
    reasons = {
        "compatible": "planar multilayer stack within the TMM domain",
        "ambiguous": "layer count range is not specified",
        "incompatible": "lateral periodic grating requires RCWA, outside TMM",
    }
    fields = dict(
        problem_id="problem-1",
        original_request=(
            "Design a broadband antireflection coating over 450-700 nm at "
            "0, 30, 45 degrees for TE/TM using MgF2/SiO2/Ta2O5."
        ),
        normalized_request_english=(
            "Design a broadband one-dimensional antireflection coating for "
            "fused silica in air over 450-700 nm, evaluated at 0, 30, and 45 "
            "degrees for TE and TM polarization, using MgF2, SiO2, and Ta2O5."
        ),
        primary_intent=ResearchIntent.design,
        compatibility=TMMCompatibility(compatibility),
        compatibility_reason=reasons[compatibility],
        needs_method_research=True,
        wavelengths_nm=[(450.0, 700.0)],
        angles_deg=[0.0, 30.0, 45.0],
        polarizations=["TE", "TM"],
        target_observables=["mean reflectance", "worst-case reflectance"],
        preferred_behaviors=[
            "mean reflectance below 0.8 percent",
            "worst-case reflectance below 3 percent",
        ],
        suppressed_behaviors=["high reflectance"],
        known_stack_materials=["MgF2", "SiO2", "Ta2O5"],
        design_variables=["layer thicknesses"],
        manufacturing_constraints=[
            "minimum layer thickness above 10 nm",
            "thickness tolerance 2 nm",
        ],
        assumptions=["materials are isotropic"],
        ambiguities=["layer count range"] if compatibility == "ambiguous" else [],
    )
    fields.update(overrides)
    return OpticalProblemAnalysis(**fields)


def _evidence(evidence_id: str, paper_id: str, text: str = "evidence text") -> MethodEvidence:
    return MethodEvidence(
        evidence_id=evidence_id,
        paper_id=paper_id,
        title=f"Paper {paper_id}",
        source_route="s2_snippet",
        content_depth=MethodContentDepth.s2_snippet,
        text=text,
        allowed_use=MethodAllowedUse.method_guidance,
    )


def _report(
    evidence_ids: tuple[str, ...] = ("ev-1", "ev-2"),
    *,
    status: MethodResearchStatus = MethodResearchStatus.completed,
    long_text: bool = False,
) -> MethodResearchReport:
    text = "x" * 2000 if long_text else "Quarter-wave AR design guidance."
    evidence = [
        _evidence(item, f"P{index}", text=text)
        for index, item in enumerate(evidence_ids, start=1)
    ]
    findings = [
        MethodFinding(
            design_family="multilayer AR",
            method_name="needle optimization",
            reusable_principle="iterative layer insertion improves bandwidth",
            applicability="dielectric multilayers",
            limitations="requires solver verification",
            evidence_ids=list(evidence_ids[:1]),
        )
    ]
    return MethodResearchReport(
        problem_id="problem-1",
        evidence=evidence,
        method_findings=findings,
        status=status,
    )


class FakeClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.messages: list[dict[str, str]] = []
        self.calls = 0

    def call(self, messages, **kwargs) -> dict:
        self.calls += 1
        self.messages = messages
        return {
            "content": self.content,
            "_llm_usage": {
                "model_name": DIRECTOR_MODEL_NAME,
                "mock_llm": False,
                "task_type": "article_director",
                "estimated_input_tokens": 10,
                "estimated_output_tokens": 20,
                "estimated_cost_cny": 0.0,
            },
        }


class SequenceFakeClient:
    def __init__(
        self,
        contents: list[str],
        *,
        usage: dict | None = None,
    ) -> None:
        self.contents = list(contents)
        self.usage = usage if usage is not None else {
            "model_name": DIRECTOR_MODEL_NAME,
            "mock_llm": False,
            "task_type": "article_director",
            "estimated_input_tokens": 10,
            "estimated_output_tokens": 20,
            "estimated_cost_cny": 0.0,
        }
        self.messages: list[dict[str, str]] = []
        self.calls = 0

    def call(self, messages, **kwargs) -> dict:
        self.calls += 1
        self.messages = messages
        return {
            "content": self.contents.pop(0) if self.contents else "",
            "_llm_usage": dict(self.usage),
        }


def _hyp(**overrides) -> dict:
    base = dict(
        statement="A bounded multilayer stack can meet the soft observables.",
        falsifiable_prediction=(
            "Simulated R/T/A over declared bands and angles will pass the "
            "deterministic solver audit."
        ),
        expected_observations=["observable improves"],
        disconfirming_observations=["passivity audit fails"],
        evidence_ids=["ev-1"],
        theory_basis="quarter-wave interference",
        route_kind="baseline_experiments",
        parent_hypothesis_id=None,
        novelty_rationale="candidate framing",
        risk_notes="requires solver verification",
    )
    base.update(overrides)
    return base


def _draft_json(*hypotheses: dict, influence=(), unresolved=()) -> str:
    return json.dumps(
        {
            "hypotheses": list(hypotheses),
            "research_influence": list(influence),
            "unresolved_decisions": list(unresolved),
        }
    )


def _observation(summary: str = "baseline verified", text_len: int = 1200) -> ObservationCard:
    return ObservationCard(
        observation_id="obs-1",
        experiment_id="exp-1",
        status=ExperimentStatus.physically_valid,
        summary=summary + ("x" * text_len),
        artifact_ids=["SIMULATION_RESULT.json"],
    )


def test_compatible_analysis_produces_planned_plan() -> None:
    question = "设计一个宽带减反膜，波长 450-700 nm"
    result = ArticleDirector().plan(
        question, _analysis(), _report(), force_mock=True
    )
    assert result.status == "planned"
    assert result.model_name == DIRECTOR_MODEL_NAME
    assert result.attempts == 1
    assert result.usage["mock_llm"] is True
    plan = result.plan
    assert plan is not None
    assert plan.question == question
    assert plan.charter.question == question
    assert plan.capability.status == TMMCompatibility.compatible
    assert plan.capability.recommended_next_action == "proceed_with_planning"

    coverage_route_ids = [row.route_id for row in plan.coverage_matrix.rows]
    assert coverage_route_ids == [
        "baseline",
        "exploration",
        "controlled_improvement",
        "discriminative_experiments",
        "robustness_ablation",
        "fresh_replay",
    ]
    assert all(
        row.coverage_status == CoverageStatus.planned
        for row in plan.coverage_matrix.rows
    )
    assert all(row.evidence_artifact_ids == [] for row in plan.coverage_matrix.rows)

    stages = [item.stage for item in plan.stage_plan]
    assert ArticleStage.charter_locked in stages
    assert ArticleStage.baseline_experiments in stages
    assert ArticleStage.exploration in stages
    assert ArticleStage.controlled_improvement in stages
    assert ArticleStage.discriminative_experiments in stages
    assert ArticleStage.robustness_ablation in stages
    assert ArticleStage.hypothesis_update in stages
    assert ArticleStage.claim_ledger in stages
    assert ArticleStage.figure_first_planning in stages
    assert ArticleStage.section_writing in stages
    assert ArticleStage.fact_audit in stages
    assert ArticleStage.scientific_review in stages
    assert ArticleStage.expression_review in stages
    assert ArticleStage.author_revision in stages
    assert ArticleStage.fresh_replay in stages
    assert ArticleStage.publication_package in stages
    planning_prefix = [
        ArticleStage.charter_locked,
        ArticleStage.capability_classified,
        ArticleStage.literature_integrated,
        ArticleStage.coverage_matrix_locked,
        ArticleStage.hypotheses_formed,
    ]
    assert stages[:5] == planning_prefix
    assert stages.index(ArticleStage.baseline_experiments) == 5
    assert len(stages) == 20
    assert stages[-1] == ArticleStage.publication_package
    assert all(item.status == "planned" for item in plan.stage_plan)
    assert plan.stage_plan[0].depends_on == []
    assert all(
        item.depends_on == [f"stage-{index:02d}"]
        for index, item in enumerate(plan.stage_plan[1:], start=1)
    )

    assert [item.hypothesis_id for item in plan.hypotheses] == ["hyp-01", "hyp-02"]
    assert plan.hypotheses[1].parent_hypothesis_id == "hyp-01"


def test_ambiguous_analysis_produces_clarification_and_not_run_routes() -> None:
    result = ArticleDirector().plan(
        "Which layer count should be used?",
        _analysis("ambiguous"),
        _report(),
        force_mock=True,
    )
    assert result.status == "planned"
    plan = result.plan
    assert plan is not None
    assert plan.capability.status == TMMCompatibility.ambiguous
    assert plan.capability.clarification_questions == ["layer count range"]
    assert plan.capability.recommended_next_action == "clarify_before_experiments"
    assert all(
        row.coverage_status == CoverageStatus.not_run
        and "clarification required" in row.not_run_reason
        for row in plan.coverage_matrix.rows
    )
    assert all(item.status == "not_run" for item in plan.stage_plan)
    assert all(
        any("clarification required" in stop for stop in item.stop_conditions)
        for item in plan.stage_plan
    )


def test_incompatible_analysis_is_invalid_and_not_overridable() -> None:
    director = ArticleDirector(
        client=FakeClient(_draft_json(_hyp(statement="compatible-looking draft")))
    )
    result = director.plan(
        "Design a grating.",
        _analysis("incompatible"),
        _report(),
        force_mock=True,
    )
    assert result.status == "invalid"
    assert result.plan is None
    assert any("capability incompatible" in item for item in result.validation_errors)
    assert any("stop_capability_boundary" in item for item in result.validation_errors)
    assert result.attempts == 0
    assert director.client.calls == 0
    assert result.model_name == DIRECTOR_MODEL_NAME


def test_evidence_ids_strictly_validated_unknown_id() -> None:
    content = _draft_json(_hyp(evidence_ids=["ev-999"]))
    director = ArticleDirector(client=FakeClient(content))
    result = director.plan("question", _analysis(), _report(), force_mock=None)
    assert result.status == "invalid"
    assert any("unknown evidence ids" in item for item in result.validation_errors)
    assert director.client.calls == 1


def test_evidence_ids_strictly_validated_missing_prediction() -> None:
    content = _draft_json(
        _hyp(statement="", falsifiable_prediction="")
    )
    director = ArticleDirector(client=FakeClient(content))
    result = director.plan("question", _analysis(), _report(), force_mock=None)
    assert result.status == "invalid"
    assert any("statement is empty" in item for item in result.validation_errors)
    assert any("falsifiable_prediction is missing" in item for item in result.validation_errors)


def test_theory_only_candidate_without_evidence_is_valid() -> None:
    content = _draft_json(
        _hyp(
            evidence_ids=[],
            theory_basis="analytical quarter-wave impedance matching",
        )
    )
    director = ArticleDirector(client=FakeClient(content))
    result = director.plan(
        "question", _analysis(), _report(evidence_ids=()), force_mock=None
    )
    assert result.status == "planned"
    plan = result.plan
    assert plan is not None
    assert plan.hypotheses[0].evidence_ids == []
    assert "quarter-wave" in plan.hypotheses[0].theory_basis


def test_malformed_qwen_output_is_unavailable_without_fallback() -> None:
    director = ArticleDirector(
        client=SequenceFakeClient(["this is not json", "still not json"])
    )
    result = director.plan("question", _analysis(), _report(), force_mock=None)
    assert result.status == "unavailable"
    assert result.plan is None
    assert any("not JSON" in item for item in result.validation_errors)
    assert result.attempts == 2
    assert director.client.calls == 2
    assert result.usage["call_count"] == 2
    assert len(result.usage["attempts"]) == 2
    assert result.usage["estimated_input_tokens"] == 20
    assert result.usage["estimated_output_tokens"] == 40


def test_empty_then_valid_json_retries_and_preserves_usage() -> None:
    director = ArticleDirector(
        client=SequenceFakeClient(["", _draft_json(_hyp())])
    )
    result = director.plan("question", _analysis(), _report(), force_mock=None)
    assert result.status == "planned"
    assert result.plan is not None
    assert result.attempts == 2
    assert director.client.calls == 2
    assert result.usage["call_count"] == 2
    assert len(result.usage["attempts"]) == 2
    assert result.usage["estimated_input_tokens"] == 20
    assert result.usage["estimated_output_tokens"] == 40
    assert any(
        "Return ONLY" in message.get("content", "")
        for message in director.client.messages
    )


def test_always_empty_retains_usage_and_attempts() -> None:
    director = ArticleDirector(client=SequenceFakeClient(["", ""]))
    result = director.plan("question", _analysis(), _report(), force_mock=None)
    assert result.status == "unavailable"
    assert result.plan is None
    assert director.client.calls == 2
    assert result.attempts == 2
    assert result.usage["call_count"] == 2
    assert len(result.usage["attempts"]) == 2
    assert result.usage["estimated_input_tokens"] == 20
    assert result.usage["estimated_output_tokens"] == 40
    assert any(
        "format-repair retry" in item for item in result.validation_errors
    )


def test_provider_tokens_yield_positive_estimated_cost() -> None:
    director = ArticleDirector(
        client=SequenceFakeClient(
            [_draft_json(_hyp())],
            usage={
                "model_name": DIRECTOR_MODEL_NAME,
                "input_tokens": 100,
                "output_tokens": 50,
            },
        )
    )
    result = director.plan("question", _analysis(), _report(), force_mock=None)
    assert result.status == "planned"
    assert result.usage["estimated_input_tokens"] == 100
    assert result.usage["estimated_output_tokens"] == 50
    assert result.usage["estimated_cost_cny"] > 0


def test_provider_reported_cost_is_preserved() -> None:
    director = ArticleDirector(
        client=SequenceFakeClient(
            [_draft_json(_hyp())],
            usage={
                "model_name": DIRECTOR_MODEL_NAME,
                "input_tokens": 100,
                "output_tokens": 50,
                "estimated_list_price_cost_cny": 0.5,
            },
        )
    )
    result = director.plan("question", _analysis(), _report(), force_mock=None)
    assert result.status == "planned"
    assert result.usage["estimated_cost_cny"] == 0.5


def test_zero_token_usage_reports_zero_cost() -> None:
    director = ArticleDirector(
        client=SequenceFakeClient([_draft_json(_hyp())], usage={})
    )
    result = director.plan("question", _analysis(), _report(), force_mock=None)
    assert result.status == "planned"
    assert result.usage["estimated_cost_cny"] == 0.0


def test_empty_qwen_hypotheses_is_invalid() -> None:
    director = ArticleDirector(client=FakeClient(_draft_json()))
    result = director.plan("question", _analysis(), _report(), force_mock=None)
    assert result.status == "invalid"
    assert any("no hypotheses" in item for item in result.validation_errors)
    assert director.client.calls == 1


def test_charter_preserves_numerical_constraints_and_materials_without_invention() -> None:
    result = ArticleDirector().plan(
        "question", _analysis(), _report(), force_mock=True
    )
    plan = result.plan
    assert plan is not None
    constraints = " | ".join(plan.charter.constraints)
    assert "450.0-700.0" in constraints
    assert "0.0" in constraints and "30.0" in constraints and "45.0" in constraints
    assert "TE" in constraints and "TM" in constraints
    assert "MgF2" in constraints and "SiO2" in constraints and "Ta2O5" in constraints
    assert "minimum layer thickness above 10 nm" in constraints
    assert "suppressed behaviors: high reflectance" in constraints
    assert "design variables: layer thicknesses" in constraints
    assert "TMM capability: compatible" in constraints
    assert "999" not in constraints
    assert plan.charter.goals == ["mean reflectance", "worst-case reflectance"]
    assert plan.charter.success_criteria == [
        "mean reflectance below 0.8 percent",
        "worst-case reflectance below 3 percent",
    ]


def test_charter_preserves_secondary_intents_design_variables_and_ambiguities() -> None:
    analysis = _analysis(
        secondary_intents=[ResearchIntent.optimize, ResearchIntent.robustness],
        design_variables=["layer thicknesses", "material order"],
        suppressed_behaviors=["high reflectance", "scattering"],
        ambiguities=["layer count range"],
    )
    result = ArticleDirector().plan("question", analysis, _report(), force_mock=True)
    plan = result.plan
    assert plan is not None
    constraints = " | ".join(plan.charter.constraints)
    assert "secondary intents: optimize, robustness" in constraints
    assert "design variables: layer thicknesses, material order" in constraints
    assert "suppressed behaviors: high reflectance, scattering" in constraints
    assert "ambiguities: layer count range" in constraints
    assert "999" not in constraints


def test_qwen_prompt_is_bounded_and_asks_for_hypotheses_only() -> None:
    content = _draft_json(_hyp())
    director = ArticleDirector(client=FakeClient(content))
    result = director.plan(
        "question",
        _analysis(),
        _report(long_text=True),
        force_mock=None,
    )
    assert result.status == "planned"
    messages = director.client.messages
    system_prompt = messages[0]["content"]
    payload = json.loads(messages[1]["content"])
    assert "hypotheses" in system_prompt
    assert "allowed_evidence_ids" in system_prompt
    assert payload["original_question"] == "question"
    assert "telemetry" not in payload
    assert "queries" not in payload
    assert len(payload["method_evidence"]) == 2
    assert all(len(item["excerpt"]) <= 600 for item in payload["method_evidence"])
    assert [item["alias"] for item in payload["method_evidence"]] == [
        "E01",
        "E02",
    ]
    assert "evidence_id" not in payload["method_evidence"][0]
    assert payload["allowed_evidence_refs"] == ["E01", "E02"]
    assert payload["output_contract"]["keys"] == [
        "hypotheses",
        "research_influence",
        "unresolved_decisions",
    ]
    assert set(payload.keys()) == {
        "task",
        "original_question",
        "analysis",
        "method_evidence",
        "method_findings",
        "allowed_evidence_refs",
        "prior_observations",
        "output_contract",
    }


def test_prior_observations_compacted_and_cannot_change_capability() -> None:
    observation = _observation(summary="baseline verified; grating required")
    content = _draft_json(_hyp())
    director = ArticleDirector(client=FakeClient(content))
    result = director.plan(
        "question",
        _analysis(),
        _report(),
        prior_observations=[observation],
        force_mock=None,
    )
    assert result.status == "planned"
    plan = result.plan
    assert plan is not None
    assert plan.capability.status == TMMCompatibility.compatible
    payload = json.loads(director.client.messages[1]["content"])
    assert payload["prior_observations"][0]["observation_id"] == "obs-1"
    assert payload["prior_observations"][0]["experiment_id"] == "exp-1"
    assert len(payload["prior_observations"][0]["summary"]) <= 600
    assert payload["prior_observations"][0]["artifact_ids"] == [
        "SIMULATION_RESULT.json"
    ]


def test_prior_observations_generator_is_normalized_to_list() -> None:
    content = _draft_json(_hyp())
    director = ArticleDirector(client=FakeClient(content))
    observations = (
        _observation(summary=f"summary-{index}")
        for index in range(1, 4)
    )
    result = director.plan(
        "question",
        _analysis(),
        _report(),
        prior_observations=observations,
        force_mock=None,
    )
    assert result.status == "planned"
    payload = json.loads(director.client.messages[1]["content"])
    assert len(payload["prior_observations"]) == 3
    assert payload["prior_observations"][0]["summary"].startswith("summary-1")
    assert payload["prior_observations"][2]["summary"].startswith("summary-3")


def test_evidence_allowlist_truncation_matches_visible_prompt() -> None:
    ids = tuple(f"ev-{index}" for index in range(1, 46))
    report = _report(evidence_ids=ids)

    hidden = _hyp(evidence_ids=["ev-45"])
    hidden_director = ArticleDirector(client=FakeClient(_draft_json(hidden)))
    hidden_result = hidden_director.plan(
        "question", _analysis(), report, force_mock=None
    )
    assert hidden_result.status == "invalid"
    assert any("unknown evidence ids" in item for item in hidden_result.validation_errors)
    assert any("truncated" in item for item in hidden_result.normalization_warnings)
    payload = json.loads(hidden_director.client.messages[1]["content"])
    assert len(payload["method_evidence"]) == 40
    assert payload["allowed_evidence_refs"] == [
        f"E{index:02d}" for index in range(1, 41)
    ]
    assert "E45" not in payload["allowed_evidence_refs"]

    visible = _hyp(evidence_ids=["ev-1"])
    visible_director = ArticleDirector(client=FakeClient(_draft_json(visible)))
    visible_result = visible_director.plan(
        "question", _analysis(), report, force_mock=None
    )
    assert visible_result.status == "planned"
    assert any("truncated" in item for item in visible_result.normalization_warnings)
    visible_payload = json.loads(visible_director.client.messages[1]["content"])
    assert visible_payload["allowed_evidence_refs"] == [
        f"E{index:02d}" for index in range(1, 41)
    ]


def test_qwen_short_alias_maps_back_to_canonical_evidence_id() -> None:
    director = ArticleDirector(
        client=FakeClient(_draft_json(_hyp(evidence_ids=["E01"])))
    )
    result = director.plan(
        "question",
        _analysis(),
        _report(evidence_ids=("ev-1",)),
        force_mock=None,
    )
    assert result.status == "planned"
    assert result.plan.hypotheses[0].evidence_ids == ["ev-1"]


def test_qwen_typo_alias_still_fails_closed() -> None:
    director = ArticleDirector(
        client=FakeClient(_draft_json(_hyp(evidence_ids=["E0l"])))
    )
    result = director.plan(
        "question",
        _analysis(),
        _report(evidence_ids=("ev-1",)),
        force_mock=None,
    )
    assert result.status == "invalid"
    assert any("unknown evidence ids" in item for item in result.validation_errors)


def test_deterministic_ids_and_serialization() -> None:
    director = ArticleDirector()
    first = director.plan("question", _analysis(), _report(), force_mock=True)
    second = director.plan("question", _analysis(), _report(), force_mock=True)
    assert first.plan is not None and second.plan is not None
    assert first.plan.plan_id == second.plan.plan_id
    assert first.plan.charter.charter_id == second.plan.charter.charter_id
    assert first.plan.capability.capability_id == second.plan.capability.capability_id
    assert first.plan.coverage_matrix.matrix_id == second.plan.coverage_matrix.matrix_id
    assert first.plan.hypotheses[0].hypothesis_id == second.plan.hypotheses[0].hypothesis_id
    assert json.dumps(first.model_dump(mode="json"), sort_keys=True) == json.dumps(
        second.model_dump(mode="json"), sort_keys=True
    )
    assert first.model_name == "qwen3.7-flash"


def test_plan_accepts_mappings() -> None:
    analysis = _analysis().model_dump(mode="json")
    report = _report().model_dump(mode="json")
    result = ArticleDirector().plan("question", analysis, report, force_mock=True)
    assert result.status == "planned"
    plan = result.plan
    assert plan is not None
    assert plan.charter.question == "question"
    assert plan.capability.status == TMMCompatibility.compatible


def test_model_name_is_locked_literal() -> None:
    result = ArticleDirector().plan(
        "question", _analysis(), _report(), force_mock=True
    )
    assert result.model_name == "qwen3.7-flash"
    with pytest.raises(ValidationError):
        ArticleDirectorResult.model_validate(
            {
                "status": "planned",
                "attempts": 0,
                "validation_errors": [],
                "normalization_warnings": [],
                "usage": {},
                "model_name": "qwen3.5-plus",
            }
        )
