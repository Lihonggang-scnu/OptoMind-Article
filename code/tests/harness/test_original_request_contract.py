from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from optomind_optics.harness.design_task import OpticalDesignTask
from optomind_optics.harness.engine_capabilities import EngineCapabilities
from optomind_optics.harness.research_charter import build_research_charter
from optomind_optics.harness.research_feedback import ResearchFeedbackDecision, ResearchIterationObservation, acceptance_summary
from optomind_optics.harness.research_orchestrator import TMMResearchHarness, TMMResearchHarnessConfig, RouteTrack, _execution_failure_category
from optomind_optics.harness.research_report import DeterministicTMMResearchReporter
from optomind_optics.harness.route_reflection import _build_reflection_payload
from optomind_optics.harness.task_compiler import CompileFailure, _check_task_spec_charter_drift


@pytest.fixture
def saved():
    return json.loads((Path(__file__).parents[1] / "fixtures/tmm_harness/original_request_rounds.json").read_text())


def _charter(saved):
    return build_research_charter(saved["question"], saved["problem"])


def _simulation(task):
    return task["experiments"][0]["tmm_task"]["simulation"]


def test_saved_three_layer_revision_rejected_without_trial_guard(saved):
    charter = _charter(saved)
    _check_task_spec_charter_drift({"task": saved["rounds"][0]["task"]}, charter)
    with pytest.raises(CompileFailure, match="layer count"):
        _check_task_spec_charter_drift({"task": saved["rounds"][1]["task"]}, charter)


@pytest.mark.parametrize("mutation", ["duplicate_material", "changed_index", "changed_loss", "changed_bounds", "changed_thickness", "incident", "exit", "grid", "angle", "polarization"])
def test_frozen_contract_rejects_changed_physics(saved, mutation):
    task = deepcopy(saved["rounds"][0]["task"])
    simulation = _simulation(task)
    layer = simulation["stack"]["layers"][0]
    if mutation == "duplicate_material":
        layer["constant_n"] = 2.4
    elif mutation == "changed_index":
        layer["constant_n"] = 1.47
    elif mutation == "changed_loss":
        layer["constant_k"] = 0.01
    elif mutation == "changed_bounds":
        layer["max_thickness_nm"] = 200
    elif mutation == "changed_thickness":
        layer["thickness_nm"] = 180
    elif mutation in {"incident", "exit"}:
        simulation["stack"][mutation]["constant_n"] = 1.8
    elif mutation == "grid":
        simulation["spectrum"]["points"] = 52
    elif mutation == "angle":
        simulation["illumination"]["angles_deg"] = [10]
    else:
        simulation["illumination"]["polarizations"] = ["p"]
    with pytest.raises(CompileFailure, match="CHARTER_DRIFT_ERROR"):
        _check_task_spec_charter_drift({"task": task}, _charter(saved))


def test_order_thickness_optimizer_and_soft_target_can_change(saved):
    task = deepcopy(saved["rounds"][0]["task"])
    layers = _simulation(task)["stack"]["layers"]
    layers.reverse()
    layers[0]["thickness_nm"] = 120
    raw = task["experiments"][0]["tmm_task"]
    raw["targets"][0]["target"] = 0.015
    raw["optimizer"]["method"] = "differential_evolution"
    _check_task_spec_charter_drift({"task": task}, _charter(saved))


def test_contract_is_not_pilot_specific_or_guessed_from_analysis(saved):
    question = saved["question"].replace("two-layer", "four-layer").replace("one layer of each", "two layers of each")
    charter = build_research_charter(question, saved["problem"])
    assert charter["layer_count_bounds"] == [4, 4]
    task = deepcopy(saved["rounds"][0]["task"])
    _simulation(task)["stack"]["layers"] *= 2
    _check_task_spec_charter_drift({"task": task}, charter)
    unspecified = build_research_charter("Find a planar coating.", saved["problem"])
    assert unspecified["wavelength_range_nm"] is None
    assert unspecified["layer_count_bounds"] is None
    assert unspecified["frozen_constraints"]["angles_deg"] == []


class _Compiler:
    def __init__(self, task):
        self.task = OpticalDesignTask.model_validate(task)
        self.requests = []

    def compile(self, question, **kwargs):
        self.requests.append(question)
        return {"status": "compiled", "task": self.task.model_dump(mode="json"), "usage": []}


def _harness(tmp_path, compiler, factory=None):
    return TMMResearchHarness(tmp_path, problem_analyzer=object(), method_researcher=object(), strategy_planner=object(), task_compiler=compiler, tmm_harness_factory=factory, config=TMMResearchHarnessConfig(qwen_force_mock=True, scoring_standard_enabled=False))


def test_formal_round_rejects_contract_before_solver_dispatch(tmp_path, monkeypatch, saved):
    monkeypatch.setattr("optomind_optics.harness.research_orchestrator.probe_engine_capabilities", lambda: EngineCapabilities())
    compiler = _Compiler(saved["rounds"][1]["task"])
    calls = []
    harness = _harness(tmp_path, compiler, lambda *args: calls.append(args))
    harness.research_charter = _charter(saved)
    harness._write("REQUEST.json", {"question": saved["question"]})
    route = saved["rounds"][0]["route"]
    ctx = harness._execute_track_round(RouteTrack(route_id=route["route_id"], source="planned", current_route=route), 1, {}, [])
    assert not calls
    assert ctx["observation"].compilation_status == "contract_rejected"
    assert ctx["observation"].failure_categories == ("contract_violation",)
    assert not (tmp_path / "iterations/iteration_01/COMPILED_TASK.json").exists()
    assert saved["question"] in compiler.requests[0]


def test_reflection_receives_original_constraints(saved):
    charter = _charter(saved)
    payload = _build_reflection_payload({}, saved["rounds"][0]["observation"], [0.8], 1e-4, charter)
    assert payload["immutable_research_charter"] == charter
    assert payload["immutable_research_charter"]["layer_count_bounds"] == [2, 2]


def test_report_keeps_historical_route_identity_and_partial_acceptance(saved):
    rows = [ResearchIterationObservation.model_validate({**row["observation"], "route_snapshot": row["route"]}) for row in saved["rounds"]]
    answer = DeterministicTMMResearchReporter().build(problem_analysis=saved["problem"], method_research={}, strategy_plan={"routes": [saved["rounds"][1]["route"]]}, iterations=rows, stop_decision=ResearchFeedbackDecision(action="stop_best_effort", reason="round budget"), status="completed")
    assert answer.route_summaries[0]["scientific_hypothesis"] == saved["rounds"][0]["route"]["scientific_hypothesis"]
    assert answer.route_summaries[0]["scientific_hypothesis"] != saved["rounds"][1]["route"]["scientific_hypothesis"]
    assert answer.acceptance_status == "partial"
    assert answer.failed_iteration_ids == ("iteration_02",)
    assert answer.recommended_candidates
    assert "Acceptance: partial" in answer.markdown
    assert "Partial acceptance" in " ".join(answer.limitations)


def test_legacy_repeated_routes_never_borrow_latest_hypothesis(saved):
    rows = [ResearchIterationObservation.model_validate(row["observation"]) for row in saved["rounds"]]
    answer = DeterministicTMMResearchReporter().build(problem_analysis=saved["problem"], method_research={}, strategy_plan={"routes": [saved["rounds"][1]["route"]]}, iterations=rows, stop_decision=ResearchFeedbackDecision(action="stop_best_effort", reason="round budget"), status="completed")
    assert answer.route_summaries[0]["scientific_hypothesis"] == ""
    assert answer.route_summaries[0]["route_binding"] == "unavailable"


def test_resume_binds_immutable_saved_route_with_relative_work_dir(tmp_path, saved):
    harness = _harness(tmp_path, object())
    observation = ResearchIterationObservation.model_validate(saved["rounds"][0]["observation"])
    path = tmp_path / observation.work_dir / "ROUTE.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(saved["rounds"][0]["route"]))
    assert harness._bind_saved_route(observation).route_snapshot == saved["rounds"][0]["route"]


def test_contract_error_is_not_environment_failure():
    assert _execution_failure_category("ValueError", "Proposal failed constant-index, two-layer physical contract") == "contract_violation"
    assert _execution_failure_category("OSError", "worker unavailable") == "runtime_environment"


def test_analyzer_cannot_change_original_layer_count(saved):
    problem = {**saved["problem"], "design_variables": ["layer count between 3 and 5"]}
    assert build_research_charter(saved["question"], problem)["layer_count_bounds"] == [2, 2]


def test_round_limit_does_not_make_material_suggestions_exclusive():
    charter = build_research_charter("Use SiO2 and TiO2 as starting candidates; only run two rounds.", {})
    assert charter["material_whitelist"] == []


def test_lossless_substrate_does_not_forbid_absorbing_layers():
    charter = build_research_charter("Find a coating on a lossless glass substrate (n=1.52). Consider absorbing TiO2 layers.", {})
    assert not charter["frozen_constraints"]["lossless_layers"]


def test_round_limit_and_seed_are_original_dispatch_constraints(saved):
    for change in ("budget", "seed"):
        task = deepcopy(saved["rounds"][0]["task"])
        if change == "budget":
            task["budget"]["maximum_forward_evaluations"] = 20000
        else:
            task["experiments"][0]["tmm_task"]["optimizer"]["seed"] = 18
        with pytest.raises(CompileFailure, match="CHARTER_DRIFT_ERROR"):
            _check_task_spec_charter_drift({"task": task}, _charter(saved))
    task = deepcopy(saved["rounds"][0]["task"])
    task["budget"]["maximum_forward_evaluations"] = 32
    _check_task_spec_charter_drift({"task": task}, _charter(saved))


def test_original_total_round_cap_survives_portfolio_quota(tmp_path, saved):
    harness = _harness(tmp_path, object())
    harness.research_charter = _charter(saved)
    harness._allocate_round_quota(4)
    assert harness._iteration_ceiling == 2


def test_intent_certificate_does_not_drop_integral_reducer(saved):
    from optomind_optics.harness.research_orchestrator import _build_intent_equivalence_certificate
    task = deepcopy(saved["rounds"][0]["task"])
    task["experiments"][0]["tmm_task"]["targets"][0]["mean_reduction"] = "wavelength_integral_mean"
    assert _build_intent_equivalence_certificate(task, source_clause=saved["question"]) is None


def test_explicit_absorbing_medium_is_not_made_lossless():
    charter = build_research_charter("Design a lossless SiO2 layer on a glass substrate (n=1.52, k=0.02).", {})
    assert charter["frozen_constraints"]["media"]["exit"]["constant_k"] == 0.02


def test_illegal_route_is_not_silently_rewritten_into_legal_experiment(tmp_path, saved):
    compiler = _Compiler(saved["rounds"][0]["task"])
    harness = _harness(tmp_path, compiler)
    harness.research_charter = _charter(saved)
    route = saved["rounds"][1]["route"]
    context = harness._prepare_track_round(RouteTrack(route_id=route["route_id"], source="planned", current_route=route), 1, 1, {})
    assert context["compilation_status"] == "contract_rejected"
    assert not compiler.requests


def test_original_round_cap_does_not_expand_tighter_host_limit(tmp_path, saved):
    harness = _harness(tmp_path, object())
    harness.config = harness.config.model_copy(update={"maximum_iterations": 1})
    harness.research_charter = _charter(saved)
    harness._allocate_round_quota(4)
    assert harness._iteration_ceiling == 1


def test_resume_does_not_bind_tampered_route_snapshot(tmp_path, saved):
    harness = _harness(tmp_path, object())
    observation = ResearchIterationObservation.model_validate(saved["rounds"][0]["observation"])
    path = tmp_path / observation.work_dir / "ROUTE.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(saved["rounds"][0]["route"]))
    path.with_name("ROUTE.ATTESTATION.json").write_text(json.dumps({"artifact_sha256": "wrong"}))
    with pytest.raises(ValueError, match="pre-execution attestation"):
        harness._bind_saved_route(observation)


@pytest.mark.parametrize("terminal_replan_failure", [False, True])
def test_saved_legal_route_runs_through_formal_entry_without_external_guard(tmp_path, monkeypatch, saved, terminal_replan_failure):
    monkeypatch.setattr("optomind_optics.harness.research_orchestrator.probe_engine_capabilities", lambda: EngineCapabilities())
    compiler = _Compiler(saved["rounds"][0]["task"])
    observed = saved["rounds"][0]["observation"]
    candidates = [{
        **row,
        "physically_admissible": True,
        "metadata": {key: row[key] for key in ("thicknesses_nm", "objective_report")},
    } for row in observed["candidate_summaries"]]
    calls = []
    def factory(*args):
        def run(task):
            calls.append(task.model_dump(mode="json"))
            return {"status": "completed", "experiment_results": [{
                "experiment_id": task.experiments[0].experiment_id,
                "mode": "optimize", "physically_valid_candidate_count": len(candidates),
                "portfolio": {"candidates": candidates, "selected_roles": {"best": candidates[0]["candidate_id"]}},
            }]}
        return SimpleNamespace(run=run)
    planned = []
    def plan(*args, **kwargs):
        planned.append(True)
        if len(planned) > 1:
            harness._started -= harness.config.wall_time_seconds + 1
            raise ValueError("replanning rejected a change to the original contract")
        return {"status": "planned", "plan": {"routes": [saved["rounds"][0]["route"]]}}
    rounds = 2 if terminal_replan_failure else 1
    harness = TMMResearchHarness(
        tmp_path,
        problem_analyzer=SimpleNamespace(analyze=lambda *args, **kwargs: {"status": "analyzed", "analysis": saved["problem"]}),
        method_researcher=SimpleNamespace(research=lambda *args, **kwargs: {"status": "completed", "report": {}}),
        strategy_planner=SimpleNamespace(plan=plan),
        task_compiler=compiler, tmm_harness_factory=factory,
        config=TMMResearchHarnessConfig(qwen_force_mock=True, scoring_standard_enabled=False,
            route_planning_enabled=False, online_method_research=False,
            maximum_iterations=rounds, max_rounds_per_route=rounds, per_route_round_quota_enabled=False),
    )
    result = harness.run(saved["question"])
    assert len(calls) == 1
    assert len(_simulation(calls[0])["stack"]["layers"]) == 2
    assert result.acceptance_status == ("partial" if terminal_replan_failure else "accepted")
    assert result.final_answer.acceptance_status == result.acceptance_status
    if terminal_replan_failure:
        assert result.failed_iteration_ids == ()
        assert len(result.iterations) == 1
        assert "original contract" in result.route_failures[0]["reason"]
        assert result.final_answer.recommended_candidates
    assert result.final_answer.route_summaries[0]["scientific_hypothesis"] == saved["rounds"][0]["route"]["scientific_hypothesis"]
    assert (tmp_path / "RESEARCH_CHARTER.ATTESTATION.json").is_file()


def test_planner_recognizes_described_bounding_media(saved):
    from optomind_optics.harness.strategy_planner import _check_charter_drift
    route = deepcopy(saved["rounds"][0]["route"])
    route["proposed_materials"] = ["Air incident medium (n=1.0)", "SiO2 (n=1.46)", "TiO2 (n=2.40)", "Glass substrate (n=1.52)"]
    _check_charter_drift({"routes": [route]}, _charter(saved))
