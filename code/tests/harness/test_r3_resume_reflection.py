from copy import deepcopy
import json
import socket
import time
from types import SimpleNamespace

import pytest

import optomind_optics.harness.research_orchestrator as research
from optomind_optics.harness.design_task import OpticalDesignTask
from optomind_optics.harness.research_feedback import ResearchFeedbackDecision
from optomind_optics.harness.route_reflection import RouteReflection, _build_reflection_payload, reflect_on_route
from optomind_optics.harness.scoring_standard import FixedScoreMetric, ScoringStandard


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Network access is forbidden in offline regressions")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(research, "probe_engine_capabilities", lambda: SimpleNamespace(intent=False, gradient=False))


def task_payload():
    return OpticalDesignTask.model_validate({
        "task_id": "task", "user_request_original": "Explore a coating.",
        "normalized_request_english": "Explore a coating.",
        "experiments": [{"experiment_id": "e1", "mode": "optimize", "tmm_task": {
            "simulation": {"stack": {"name": "stack", "incident": {"constant_n": 1.0},
                "exit": {"constant_n": 1.52}, "layers": [
                    {"material": None, "constant_n": 1.46, "thickness_nm": 80.0,
                     "min_thickness_nm": 40, "max_thickness_nm": 160, "label": "first"},
                    {"material": None, "constant_n": 2.4, "thickness_nm": 80.0,
                     "min_thickness_nm": 40, "max_thickness_nm": 160, "label": "second"}]},
                "spectrum": {"start_nm": 450, "stop_nm": 700, "points": 11},
                "illumination": {"angles_deg": [0], "polarizations": ["s"]}},
            "optimizer": {"seed": 17, "starts": 3, "method": "adam_lbfgs"},
            "targets": [{"observable": "R", "target": 0.03, "constraint": "at_most",
                         "wavelength_min_nm": 450, "wavelength_max_nm": 700,
                         "polarization": "s", "name": "mean_r", "mean_reduction": "sample_mean"}]},
            "objectives": [{"objective_id": "r", "metric": "mean_reflectance", "sense": "minimize",
                            "mean_reduction": "sample_mean", "region": {"wavelength_nm": [450, 700]}}]}],
        "budget": {"maximum_forward_evaluations": 64},
    }).model_dump(mode="json")


def response(**changes):
    return {"observed_vs_expected": "The target was missed.", "deviation_mechanism": "Unresolved.",
            "continue_recommended": True, "continue_rationale": "The bounded optimizer stopped after 20 calls.",
            "insight_for_next": "Change the first layer initialization to 90 nm.",
            "insight_grounding": "The first layer was initialized at 80 nm.", "stop_basis": "", **changes}


class Client:
    def __init__(self, payload):
        self.payload = payload
        self.messages = []

    def call(self, messages, **kwargs):
        self.messages.append(deepcopy(messages))
        return {"content": json.dumps(self.payload)}


def route(**changes):
    return {"route_id": "r", "title": "Coating", "priority": 1,
            "execution_request_english": "Explore a coating from an 80 nm initialization.", **changes}


def standard():
    return ScoringStandard(metrics=(FixedScoreMetric(variable="r", canonical_id="mean_reflectance@450-700nm",
        metric="mean_reflectance", sense="minimize", region={"wavelength_nm": [450, 700]}),), formula="-r")


def harness(path, task=None, *, reflection=None):
    payload = task or task_payload()
    calls = []
    def compile_task(*args, **kwargs):
        parsed = OpticalDesignTask.model_validate(payload)
        return SimpleNamespace(task=parsed, model_dump=lambda **kw: {
            "status": "compiled", "task": parsed.model_dump(mode="json"), "usage": [], "validation_errors": []})
    def factory(directory, run_id):
        def run(task):
            calls.append(task.model_dump(mode="json"))
            directory.mkdir(parents=True, exist_ok=True)
            data = {"status": "completed", "experiment_results": [], "budget": {"forward_evaluations": 53}}
            (directory / "FINAL_RESULT.json").write_text(json.dumps(data))
            (directory / "BUDGET.json").write_text(json.dumps({"limits": {"forward_evaluations": 64}, "events": [
                {"event_type": "commit", "action_id": "optimizer:e1:primary",
                 "reserved_usage": {"forward_evaluations": 21}, "actual_usage": {"forward_evaluations": 20}}]}))
            optimizer = directory / "experiments/e1"
            optimizer.mkdir(parents=True)
            (optimizer / "SEARCH_ALLOCATION.json").write_text(json.dumps({"primary_allocated": 21, "primary_used": 20, "global_allocated": 0}))
            (optimizer / "OPTIMIZER_gradient_thickness.json").write_text(json.dumps({
                "optimizer_id": "gradient_thickness", "status": "best_effort", "evaluation_count": 20,
                "stop_reason": "maximum_forward_evaluations", "audit": {"seed": 17,
                "raw_optimizer_audit": {"maximum_forward_evaluations": 21, "best_step": 6, "budget_exhausted": True}}}))
            return data
        return SimpleNamespace(run=run)
    result = research.TMMResearchHarness(path, problem_analyzer=object(), method_researcher=object(),
        strategy_planner=object(), task_compiler=SimpleNamespace(compile=compile_task), tmm_harness_factory=factory,
        config=research.TMMResearchHarnessConfig(maximum_iterations=2, max_rounds_per_route=2,
            per_route_round_quota_enabled=False, route_planning_enabled=False, confirmation_gates=(), qwen_force_mock=True))
    result._started = time.perf_counter()
    result.scoring_standard = standard()
    result._reflection_client = Client(reflection or response())
    result.solver_calls = calls
    return result


def checkpoint(path, *, legacy=False, degraded=False):
    parent = harness(path, reflection=response(observed_vs_expected={"observed": 0.035}) if degraded else None)
    parent._write("REQUEST.json", {"run_id": "parent", "question": "Explore a coating."})
    track = research.RouteTrack("r", "literature_planned", current_route=route(), version_hashes={research._route_hash(route())})
    parent.tournament_tracks = {"r": track}
    observations = []
    ctx = parent._execute_track_round(track, 1, {}, observations)
    reflected = parent._reflect_track(ctx, {})
    verdict = parent._resolve_track_verdict(ctx, reflected, False)
    track.status = verdict["status"]
    track.termination_reason = verdict["termination_reason"]
    parent._write_tournament_state([track], 1, 1)
    files = {"PROBLEM_ANALYSIS.json": {"analysis": {"problem_id": "p", "original_request": "Explore a coating."}},
             "METHOD_RESEARCH.json": {"report": {}}, "STRATEGY_PLAN.json": {"plan": {"routes": [route()]}},
             "SCORING_STANDARD.json": {"standard": parent.scoring_standard.model_dump(mode="json")},
             "ITERATION_HISTORY.json": [row.model_dump(mode="json") for row in observations],
             "FEEDBACK_HISTORY.json": [verdict["decision"].model_dump(mode="json")]}
    for name, value in files.items():
        parent._write(name, value)
    if legacy:
        (path / "CONTINUATION_STATE.json").unlink()
        (path / "iterations/iteration_01/EXECUTION_RECORD.json").unlink()
        state = json.loads((path / "TOURNAMENT_STATE.json").read_text())
        for row in state["tracks"]:
            for key in ("phase", "pending_iteration_id", "executed_hashes", "execution_signatures"):
                row.pop(key, None)
        (path / "TOURNAMENT_STATE.json").write_text(json.dumps(state))
    return parent


def test_signature_ignores_presentation_but_preserves_seed_and_physics():
    original = task_payload()
    relabeled = deepcopy(original)
    relabeled.update(task_id="different", normalized_request_english="Different labels", metadata={"compiler": "new"})
    experiment = relabeled["experiments"][0]
    experiment["experiment_id"] = "renamed"
    experiment["objectives"][0]["objective_id"] = "renamed_r"
    experiment["tmm_task"]["simulation"]["stack"]["name"] = "new stack"
    experiment["tmm_task"]["simulation"]["stack"]["layers"][0]["label"] = "new first"
    experiment["tmm_task"]["targets"][0]["name"] = "new target"
    assert research.canonical_execution_signature(original) == research.canonical_execution_signature(relabeled)
    assert original["experiments"][0]["tmm_task"]["optimizer"]["seed"] == 17


@pytest.mark.parametrize("change", ["seed", "initializer", "optimizer", "objective", "budget", "physics"])
def test_signature_includes_every_execution_input(change):
    original = task_payload()
    changed = deepcopy(original)
    engine = changed["experiments"][0]["tmm_task"]
    if change == "seed":
        engine["optimizer"]["seed"] = 18
    elif change == "initializer":
        engine["simulation"]["stack"]["layers"][0]["thickness_nm"] = 90
    elif change == "optimizer":
        engine["optimizer"]["learning_rate"] = 0.1
    elif change == "objective":
        engine["targets"][0]["mean_reduction"] = "wavelength_integral_mean"
    elif change == "budget":
        changed["budget"]["maximum_forward_evaluations"] = 65
    else:
        engine["simulation"]["stack"]["layers"].reverse()
    assert research.canonical_execution_signature(original) != research.canonical_execution_signature(changed)


@pytest.mark.parametrize("changes", [
    {"observed_vs_expected": {"observed": 0.035}},
    {"continue_recommended": True, "stop_basis": "marginal_gains_too_low", "insight_for_next": ""},
    {"continue_recommended": False, "stop_basis": "search_exhausted"},
    {"continue_recommended": "true"},
    {"insight_for_next": ""},
    {"observed_vs_expected": "x" * 8001},
    {"deviation_mechanism": "The search reached a local optimum."},
])
def test_bad_reflection_degrades_without_additional_model_calls(changes):
    client = Client(response(**changes))
    result = reflect_on_route(client, pre_declarations={}, observation={}, score_history=[-0.035])
    assert result.degraded_reason
    assert not result.insight_for_next
    assert len(client.messages) == 1
    assert '"maxLength": 8000' in client.messages[0][0]["content"] or '"type": "string"' in client.messages[0][0]["content"]


def test_repeat_history_cannot_support_search_exhaustion():
    client = Client(response(continue_recommended=False, stop_basis="search_exhausted", insight_for_next=""))
    history = [{"execution_signature": "same"}, {"execution_signature": "same"}]
    result = reflect_on_route(client, pre_declarations={}, observation={}, score_history=[-0.035, -0.035], executed_history=history)
    assert "deterministic repeats" in result.degraded_reason


@pytest.mark.parametrize("legacy", [False, True])
def test_resume_replans_before_solver_and_preserves_executed_artifacts(tmp_path, legacy):
    source = tmp_path / "parent"
    checkpoint(source, legacy=legacy, degraded=True)
    before = {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    changed = task_payload()
    changed["experiments"][0]["tmm_task"]["simulation"]["stack"]["layers"][0]["thickness_nm"] = 90
    child = harness(tmp_path / "child", changed)
    order = []
    def improve(track, *args):
        assert not child.solver_calls
        assert len(track.executed_history) == 1
        order.append("replan")
        revised = route(execution_request_english="Explore a coating from a 90 nm initialization.")
        return {"ok": True, "revised": revised, "digest": research._route_hash(revised)}
    child._improve_track = improve
    result = child.resume_from_checkpoint(source, route_ids=["r"])
    assert order == ["replan"]
    assert len(child.solver_calls) == 1
    assert len(result.iterations) == 2
    assert result.iterations[0].model_dump(mode="json") == json.loads((source / "ITERATION_HISTORY.json").read_text())[0]
    assert child.solver_calls[0]["experiments"][0]["tmm_task"]["optimizer"]["seed"] == 17
    assert child.tournament_tracks["r"].rounds_used == 2
    for relative, value in before.items():
        assert (source / relative).read_bytes() == value
        if str(relative).startswith("iterations/"):
            assert (child.work_dir / relative).read_bytes() == value
    payload = json.loads(child._reflection_client.messages[0][-1]["content"])
    record = payload["executed_history"][0]
    assert record["evidence"]["optimizers"][0]["maximum_forward_evaluations"] == 21
    assert record["evidence"]["optimizers"][0]["evaluation_count"] == 20
    assert record["evidence"]["optimizers"][0]["stop_reason"] == "maximum_forward_evaluations"
    assert record["evidence"]["allocation_events"][0]["actual"] == 20
    assert record["evidence"]["search_allocations"][0]["allocation"]["primary_allocated"] == 21
    assert record["execution"]["experiments"][0]["tmm_task"]["simulation"]["stack"]["layers"][0]["thickness_nm"] == 80


@pytest.mark.parametrize("replicate", [False, True])
def test_resume_rejects_cosmetic_replay_but_allows_explicit_replicate(tmp_path, replicate):
    source = tmp_path / "parent"
    checkpoint(source)
    child = harness(tmp_path / "child")
    revised = route(execution_request_english="Execute the same physics with renamed presentation.",
                    revision_reason="replicate: verify deterministic reproducibility" if replicate else "Rename output labels")
    child._improve_track = lambda *args: {"ok": True, "revised": revised, "digest": research._route_hash(revised)}
    result = child.resume_from_checkpoint(source, route_ids=["r"])
    assert len(child.solver_calls) == int(replicate)
    assert len(result.iterations) == 1 + int(replicate)
    assert child.tournament_tracks["r"].rounds_used == 1 + int(replicate)
    if replicate:
        history = [json.loads(row) for row in child.tournament_tracks["r"].executed_history]
        assert history[0]["execution_signature"] == history[1]["execution_signature"]
        assert history[1]["replicate_reason"] == "verify deterministic reproducibility"
    else:
        assert child.tournament_tracks["r"].status == research.TRACK_ERROR_UNRECOVERABLE
        rejected = json.loads(next(child.work_dir.glob("iterations/*/REJECTED_EXECUTION.json")).read_text())
        assert rejected["scientific_round_consumed"] is False


def test_failed_pending_replan_spends_no_solver_round(tmp_path):
    source = tmp_path / "parent"
    checkpoint(source)
    child = harness(tmp_path / "child")
    child._improve_track = lambda *args: {"ok": False, "reason": "duplicate proposal"}
    result = child.resume_from_checkpoint(source, route_ids=["r"])
    assert child.solver_calls == []
    assert len(result.iterations) == 1
    assert child.tournament_tracks["r"].phase == "replan"
    assert "Pending replan" in child.tournament_tracks["r"].termination_reason


def test_ready_revision_resumes_without_replanning_or_old_declarations(tmp_path):
    source = tmp_path / "parent"
    parent = checkpoint(source)
    track = parent.tournament_tracks["r"]
    track.current_route = route(execution_request_english="Explore a coating from a 90 nm initialization.")
    track.pending_declarations = {"expected_observations": ["The changed initialization may improve the score."], "stop_conditions": []}
    parent._set_track_phase(track, "ready")
    changed = task_payload()
    changed["experiments"][0]["tmm_task"]["simulation"]["stack"]["layers"][0]["thickness_nm"] = 90
    child = harness(tmp_path / "child", changed)
    def unexpected(*args):
        raise AssertionError("A ready revision must not be replanned")
    child._improve_track = unexpected
    child.resume_from_checkpoint(source, route_ids=["r"])
    assert len(child.solver_calls) == 1
    attestation = json.loads((child.work_dir / "iterations/iteration_02/ROUTE.ATTESTATION.json").read_text())
    assert attestation["expected_observations"] == track.pending_declarations["expected_observations"]


def test_budgeted_gradient_forwards_the_exact_frozen_standard(tmp_path, monkeypatch):
    from optomind_optics.harness.science import gradient_binding
    frozen = standard()
    received = []
    def adopted(compiled_payload, run_payload, execution_dir, *, scoring_standard=None):
        received.append(scoring_standard)
        return {"status": "unavailable", "reason": "offline spy"}
    monkeypatch.setattr(gradient_binding, "adopted_gradient_summary", adopted)
    result = research._compute_budgeted_gradient({}, {}, tmp_path, tmp_path / "compute.sqlite", {"solver_points": 100}, scoring_standard=frozen)
    assert result["status"] == "unavailable"
    assert received == [frozen]
    assert received[0] is frozen


def test_deeply_nested_malformed_reflection_is_bounded():
    client = SimpleNamespace(call=lambda *args, **kwargs: {"content": "{" + '"nested":[' + "[" * 2000 + "0" + "]" * 2001 + "}"})
    result = reflect_on_route(client, pre_declarations={}, observation={}, score_history=[])
    assert result.degraded_reason


def test_unused_metadata_cannot_disguise_a_duplicate():
    original = task_payload()
    changed = deepcopy(original)
    changed["metadata"].update(optimizer_state={"step": 50}, rng_state="different", initializer="claimed")
    changed["experiments"][0]["tmm_task"]["optimizer_state"] = {"step": 50}
    assert research.canonical_execution_signature(original) == research.canonical_execution_signature(changed)


def test_frozen_objective_labels_and_prose_do_not_change_execution():
    original = standard()
    changed = original.model_dump(mode="json")
    changed.update(formula="-renamed", question_digest="changed", metric_rationale="new prose", formula_rationale="other")
    changed["metrics"][0].update(variable="renamed", canonical_id="presentation", rationale="new explanation")
    assert research.canonical_execution_signature(task_payload(), scoring_standard=original) == research.canonical_execution_signature(task_payload(), scoring_standard=changed)


def test_explicit_and_generated_equal_grids_have_one_signature():
    original = task_payload()
    changed = deepcopy(original)
    changed["experiments"][0]["tmm_task"]["simulation"]["spectrum"] = {"values_nm": list(range(450, 701, 25))}
    assert research.canonical_execution_signature(original) == research.canonical_execution_signature(changed)


def test_repeated_gradient_calls_cannot_reset_the_round_budget(tmp_path, monkeypatch):
    from optomind_optics.harness.science import gradient_binding
    solver_calls = []
    def adopted(*args, maximum_forward_evaluations=None, **kwargs):
        calls = 5 if maximum_forward_evaluations >= 5 else 0
        solver_calls.extend(range(calls))
        return {"status": "available" if calls else "unavailable", "compute_cost": {"differentiable_forward_calls": calls}}
    monkeypatch.setattr(gradient_binding, "adopted_gradient_summary", adopted)
    compiled = {"budget": {"maximum_forward_evaluations": 64}}
    run = {"budget": {"usage": {"forward_evaluations": 52}}}
    results = [research._compute_budgeted_gradient(compiled, run, tmp_path / "run", tmp_path / "compute.sqlite",
               {"solver_points": 10000}, scoring_standard=standard()) for _ in range(4)]
    assert len(solver_calls) == 10
    assert results[-1]["round_budget"]["usage"]["forward_evaluations"] == 62
    assert results[-1]["round_budget"]["remaining"]["forward_evaluations"] == 2


def test_interrupted_gradient_reservation_survives_restart(tmp_path, monkeypatch):
    from optomind_optics.harness.science import gradient_binding
    calls = []
    def interrupted(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("interrupted after reservation")
    monkeypatch.setattr(gradient_binding, "adopted_gradient_summary", interrupted)
    compiled = {"budget": {"maximum_forward_evaluations": 64}}
    run = {"budget": {"usage": {"forward_evaluations": 52}}}
    args = (compiled, run, tmp_path / "run", tmp_path / "compute.sqlite", {"solver_points": 10000})
    with pytest.raises(RuntimeError, match="interrupted"):
        research._compute_budgeted_gradient(*args)
    result = research._compute_budgeted_gradient(*args)
    assert len(calls) == 1
    assert result["status"] == "unavailable"
    assert result["round_budget"]["reserved"]["forward_evaluations"] == 12
    assert result["round_budget"]["remaining"]["forward_evaluations"] == 0
