from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from optomind_optics.harness.research_feedback import ResearchFeedbackDecision, ResearchIterationObservation
from optomind_optics.harness.research_report import DeterministicTMMResearchReporter
from optomind_optics.harness.route_reflection import DEFAULT_REFLECTION_PROMPT, RouteReflection, _build_reflection_payload, reflect_on_route
from optomind_optics.harness.scoring_standard import ScoringStandard


RECORDED_RUN = Path(__file__).resolve().parents[3] / "local-validation/20261013/runs/formal-04"


def _reflection(**changes):
    return {
        "observed_vs_expected": "The finite search did not meet the requested target.",
        "deviation_mechanism": "The cause is unresolved.",
        "continue_recommended": False,
        "continue_rationale": "Two rounds did not find a candidate meeting the target.",
        "insight_for_next": "",
        "insight_grounding": "Recorded rounds 1 and 2.",
        "stop_basis": "physically_infeasible",
        **changes,
    }


class RecordedResponseClient:
    def __init__(self, response):
        self.response = response
        self.messages = []

    def call(self, messages, **kwargs):
        self.messages.append(messages)
        return {"content": json.dumps(self.response)}


def _load(name):
    return json.loads((RECORDED_RUN / name).read_text(encoding="utf-8"))


def test_prose_only_infeasibility_is_unresolved_and_retains_model_claim():
    original = _reflection()
    result = RouteReflection.model_validate(original)
    assert result.stop_basis == "unresolved"
    assert result.reported_stop_basis == "physically_infeasible"
    assert "no machine-checkable" in result.stop_basis_diagnostic
    assert result.continue_rationale == original["continue_rationale"]
    assert result.deviation_mechanism == original["deviation_mechanism"]
    assert not result.degraded_reason
    assert RouteReflection.model_validate(result.model_dump()) == result


@pytest.mark.parametrize("basis", ["search_exhausted", "marginal_gains_too_low", "unresolved", ""])
def test_nonphysical_stop_bases_remain_distinct(basis):
    result = RouteReflection.model_validate(_reflection(stop_basis=basis))
    assert result.stop_basis == basis
    assert result.reported_stop_basis == ""
    assert result.stop_basis_diagnostic == ""


def test_unrecognized_proof_field_cannot_authorize_infeasibility():
    client = RecordedResponseClient(_reflection(infeasibility_certificate={"verified": True}))
    result = reflect_on_route(client, pre_declarations={}, observation={}, score_history=[0.1, 0.1])
    assert result.degraded_reason.startswith("Validation failed:")
    assert result.stop_basis != "physically_infeasible"


def test_reflection_prompt_allows_unknown_mechanism_and_disallows_physical_stop():
    prompt = DEFAULT_REFLECTION_PROMPT.read_text(encoding="utf-8")
    assert "Never invent a physical cause" in prompt
    assert 'Never emit "physically_infeasible"' in prompt
    assert 'NOT ACCEPTED: "optimizer did not find"' not in prompt
    assert "Legacy target scores are diagnostics" in prompt


@pytest.mark.parametrize("raw", [0.99, 0.01])
def test_frozen_reflection_score_never_uses_mutable_target_score(raw):
    payload = _build_reflection_payload({}, {
        "score_basis": "frozen_standard",
        "score_basis_fingerprint": "frozen-v1",
        "score_available": True,
        "best_frozen_score": -0.035,
        "best_target_score": raw,
    }, [-0.04, -0.035], 1e-4)
    metrics = payload["observed_metrics"]
    assert metrics["decision_score"] == -0.035
    assert metrics["score_basis"] == "frozen_standard"
    assert metrics["score_basis_fingerprint"] == "frozen-v1"
    assert metrics["legacy_target_score_diagnostic"] == raw
    assert "best_target_score" not in metrics
    assert payload["score_history_basis"] == "frozen_standard"


def test_missing_frozen_measurement_is_unknown_not_legacy_fallback():
    payload = _build_reflection_payload({}, {
        "score_basis": "frozen_standard",
        "score_basis_fingerprint": "frozen-v1",
        "score_available": False,
        "best_frozen_score": None,
        "best_target_score": 1.0,
    }, [], 1e-4)
    assert payload["observed_metrics"]["decision_score"] is None


def test_recorded_formal04_infeasibility_response_replays_as_unresolved():
    path = RECORDED_RUN / "iterations/iteration_02/ROUTE.REFLECTION.json"
    digest_before = hashlib.sha256(path.read_bytes()).hexdigest()
    saved = json.loads(path.read_text(encoding="utf-8"))
    response = {key: saved[key] for key in RouteReflection.model_fields if key in saved}
    client = RecordedResponseClient(response)
    result = reflect_on_route(
        client,
        pre_declarations={},
        observation=_load("ITERATION_HISTORY.json")[-1],
        score_history=[0.9646375913435794, 0.529945171199669],
        charter=_load("RESEARCH_CHARTER.json"),
    )
    assert response["stop_basis"] == "physically_infeasible"
    assert result.stop_basis == "unresolved"
    assert result.reported_stop_basis == response["stop_basis"]
    assert result.continue_rationale == response["continue_rationale"]
    assert len(client.messages) == 1
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest_before


def _report(problem, plan, observations, standard=None):
    return DeterministicTMMResearchReporter().build(
        problem_analysis=problem,
        method_research={},
        strategy_plan=plan,
        iterations=observations,
        stop_decision=ResearchFeedbackDecision(action="stop_completed", reason="Reached the recorded round limit."),
        status="completed",
        scoring_standard=standard,
    )


def test_recorded_formal04_report_uses_full_history_and_original_target():
    problem = _load("PROBLEM_ANALYSIS.json")["analysis"]
    plan = _load("STRATEGY_PLAN.json")["plan"]
    assert "0.05" in plan["unresolved_decisions"][0]
    observations = [ResearchIterationObservation.model_validate(row) for row in _load("ITERATION_HISTORY.json")]
    answer = _report(problem, plan, observations, ScoringStandard.model_validate(_load("SCORING_STANDARD.json")["standard"]))
    limitations = "\n".join(answer.limitations)
    assert plan["unresolved_decisions"][0] not in limitations
    assert answer.initial_plan_open_questions == tuple(plan["unresolved_decisions"])
    assert "0.05" not in limitations
    assert "iteration_01" in limitations and "iteration_02" in limitations
    assert "n=1.46 / n=2.4" in limitations
    assert "n=2.4 / n=1.46" in limitations
    assert answer.original_request == problem["original_request"]
    assert "A mean reflectance below 0.03 is the stated target." in answer.markdown
    assert "Finite optimizer results do not certify physical infeasibility" in limitations
    assert answer.recommended_candidates[0]["iteration_id"] == "iteration_01"
    assert answer.recommended_candidates[0]["frozen_score"] == pytest.approx(-0.03536240865642059)


def test_report_uses_charter_question_before_model_interpretation():
    original = "Minimize mean R. The stated target is below 0.03."
    answer = _report(
        {"problem_id": "p", "original_request": "A changed target of 0.05.", "normalized_request_english": "A changed target of 0.05."},
        {"research_charter": {"question": original}},
        [],
    )
    assert answer.original_request == original
    assert original in answer.markdown


def test_report_does_not_infer_executed_topology_from_proposal():
    observation = ResearchIterationObservation(
        iteration_id="iteration_02", route_id="r", route_title="Reverse-order proposal",
        route_snapshot={"proposed_topology": "Air / TiO2 / SiO2 / Glass"},
        compilation_status="failed", run_status="not_run", work_dir="iteration_02",
    )
    answer = _report({"problem_id": "p", "original_request": "Target below 0.03."}, {}, [observation])
    assert answer.route_summaries[0]["verified_layer_orders"] == []
    assert "verified layer-order evidence unavailable" in " ".join(answer.limitations)
    assert "verified layer orders: Air / TiO2" not in answer.markdown


def test_reflection_round_count_does_not_drop_unscored_executions():
    payload = _build_reflection_payload({}, {"rounds_executed": 3}, [0.1], 1e-4)
    assert payload["observed_metrics"]["rounds_executed"] == 3


def test_report_does_not_promote_unverified_candidate_layer_order():
    observation = ResearchIterationObservation(
        iteration_id="i", route_id="r", route_title="Rejected run",
        compilation_status="compiled", run_status="rejected_physics", work_dir="i",
        candidate_summaries=({"candidate_id": "c", "layer_materials": ["unverified"]},),
    )
    answer = _report({"problem_id": "p"}, {}, [observation])
    assert answer.route_summaries[0]["verified_layer_orders"] == []


@pytest.mark.parametrize("maximum_rounds, expected_status", [(5, None), (2, "stopped_round_limit")])
def test_recorded_physical_claim_cannot_stop_real_scheduler_without_hard_limit(tmp_path, maximum_rounds, expected_status):
    from types import SimpleNamespace
    from optomind_optics.harness.research_orchestrator import RouteTrack, TMMResearchHarness

    saved = _load("iterations/iteration_02/ROUTE.REFLECTION.json")
    response = {key: saved[key] for key in RouteReflection.model_fields if key in saved}
    reflection = RouteReflection.model_validate(response)
    observation = ResearchIterationObservation.model_validate(_load("ITERATION_HISTORY.json")[-1])
    track = RouteTrack(route_id=observation.route_id, source="planned", rounds_used=2, score_history=[0.96, 0.53])
    harness = object.__new__(TMMResearchHarness)
    harness.config = SimpleNamespace(max_rounds_per_route=maximum_rounds, minimum_rounds_before_llm_stop=2)
    harness._track_confidence = {}
    harness._track_directives = {}
    harness._event = lambda *args, **kwargs: None
    verdict = harness._resolve_track_verdict(
        {"track": track, "observation": observation, "run_payload": {}, "iteration_dir": tmp_path, "iteration_id": observation.iteration_id},
        {"reflection": reflection, "reflection_available": True, "epsilon": 1e-4},
        True,
    )
    assert verdict["status"] == expected_status
    assert verdict["disagreement"]["llm_stop_policy"]["stop_basis"] == "unresolved"
    assert not verdict["disagreement"]["llm_stop_policy"]["explicit_no_benefit_stop"]
    assert "physically_infeasible" not in verdict["termination_reason"]


def test_explicit_search_exhaustion_remains_a_nonphysical_stop(tmp_path):
    from types import SimpleNamespace
    from optomind_optics.harness.research_orchestrator import RouteTrack, TMMResearchHarness

    observation = ResearchIterationObservation.model_validate(_load("ITERATION_HISTORY.json")[-1])
    track = RouteTrack(route_id=observation.route_id, source="planned", rounds_used=2, score_history=[0.96, 0.53])
    track.executed_history = tuple(json.dumps({
        "iteration_id": f"iteration_{number:02d}",
        "execution_facts": {"schema_version": "execution-facts.v1",
            "iteration_id": f"iteration_{number:02d}", "physics_executed": True,
            "run_status": "completed", "budget_usage": {"forward_evaluations": 1}},
    }) for number in (1, 2))
    harness = object.__new__(TMMResearchHarness)
    harness.config = SimpleNamespace(max_rounds_per_route=5, minimum_rounds_before_llm_stop=2)
    harness._track_confidence = {}
    harness._track_directives = {}
    harness._event = lambda *args, **kwargs: None
    verdict = harness._resolve_track_verdict(
        {"track": track, "observation": observation, "run_payload": {}, "iteration_dir": tmp_path, "iteration_id": observation.iteration_id},
        {"reflection": RouteReflection.model_validate(_reflection(stop_basis="search_exhausted")), "reflection_available": True, "epsilon": 1e-4},
        True,
    )
    assert verdict["status"] == "stopped_llm_advice"
    assert "search_exhausted" in verdict["termination_reason"]
