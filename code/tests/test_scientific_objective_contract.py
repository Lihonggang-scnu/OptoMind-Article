from copy import deepcopy
import json
from pathlib import Path

import pytest

from optomind_optics.harness.metric_catalog import canonical_metric_id, verify_metric_reference
from optomind_optics.harness.metric_reduction import requested_mean_reduction
from optomind_optics.harness.objective_intent import explicit_objective_intents, objective_intent_errors
from optomind_optics.harness.research_charter import build_research_charter
from optomind_optics.harness.scoring_standard import FixedScoreMetric, QwenScoringStandardBuilder, ScoringStandard
from optomind_optics.harness.task_compiler import (
    CompileFailure, QwenTMMTaskCompiler, TMMTaskDraft,
    _align_targets_with_scoring_standard, _check_task_spec_charter_drift,
)


FIXTURE = Path(__file__).parent / "fixtures/tmm_harness/formal01_controls.json"


class RecordedClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def call(self, messages, **kwargs):
        self.requests.append(json.loads(messages[-1]["content"]))
        return {"content": json.dumps(self.responses.pop(0))}


def saved():
    return json.loads(FIXTURE.read_text())


def metric(sense="minimize", band=(450, 700), **changes):
    return {
        "metric": "mean_reflectance", "sense": sense,
        "region": {"wavelength_nm": list(band), "angle_deg": 0.0, "polarization": "s"},
        **changes,
    }


def standard(*rows):
    fixed = []
    for index, row in enumerate(rows):
        fixed.append(FixedScoreMetric(variable=f"m{index}", canonical_id=canonical_metric_id(row["metric"], row["region"]), **row))
    formula = "+".join(("-" if row.sense == "minimize" else "") + row.variable for row in fixed)
    return ScoringStandard(metrics=tuple(fixed), formula=formula)


def test_recorded_wrong_direction_is_rejected_before_formula_call():
    client = RecordedClient({"metrics": [metric("maximize")]})
    result = QwenScoringStandardBuilder(client, maximum_attempts=1).build(saved()["original_request"])
    assert result.status != "standardized"
    assert any("direction" in error for error in result.validation_errors)
    assert len(client.requests) == 1


def test_recorded_wrong_direction_can_repair_then_compile_original_contract():
    correct = metric()
    variable = verify_metric_reference(correct).variable
    client = RecordedClient({"metrics": [metric("maximize")]}, {"metrics": [correct]}, {"formula": f"-{variable}"})
    result = QwenScoringStandardBuilder(client).build(saved()["original_request"])
    assert result.status == "standardized", result.validation_errors
    assert "direction" in " ".join(client.requests[1]["repair_request"]["validation_errors"])
    compiled = QwenTMMTaskCompiler(client=RecordedClient(saved()["compiler_draft"]), scoring_standard=result.standard).compile(saved()["original_request"])
    assert compiled.status == "compiled", compiled.validation_errors
    _check_task_spec_charter_drift({"task": compiled.task.model_dump(mode="json")}, build_research_charter(saved()["original_request"], {}))
    assert compiled.task.experiments[0].tmm_task["targets"][0]["constraint"] == "at_most"


def test_supplied_wrong_standard_cannot_override_original_request():
    result = QwenTMMTaskCompiler(client=RecordedClient(saved()["compiler_draft"]), scoring_standard=standard(metric("maximize", mean_reduction="sample_mean")), maximum_attempts=1).compile(saved()["original_request"])
    assert result.status == "invalid"
    assert any("direction" in error for error in result.validation_errors)


def test_original_charter_rejects_direction_drift_even_with_correct_report_objective():
    raw = QwenTMMTaskCompiler(client=RecordedClient(saved()["compiler_draft"])).compile(saved()["original_request"])
    assert raw.status == "compiled"
    task = raw.task.model_dump(mode="json")
    target = task["experiments"][0]["tmm_task"]["targets"][0]
    target.update(constraint="at_least", target=1.0, mean_reduction="sample_mean")
    with pytest.raises(CompileFailure, match="direction"):
        _check_task_spec_charter_drift({"task": task}, build_research_charter(saved()["original_request"], {}))


@pytest.mark.parametrize("change", [
    {"metric": "worst_case_reflectance"},
    {"region": {"wavelength_nm": [500, 600], "angle_deg": 0.0, "polarization": "s"}},
    {"region": {"wavelength_nm": [450, 700], "angle_deg": 45.0, "polarization": "s"}},
    {"region": {"wavelength_nm": [450, 700], "angle_deg": 0.0, "polarization": "p"}},
])
def test_original_metric_statistic_band_and_channel_cannot_be_substituted(change):
    result = QwenScoringStandardBuilder(RecordedClient({"metrics": [metric(**change)]}), maximum_attempts=1).build(saved()["original_request"])
    assert result.status != "standardized"
    assert any("statistic/region/conditions" in error for error in result.validation_errors)


def test_opposite_band_directions_are_not_last_writer_wins():
    draft = deepcopy(saved()["compiler_draft"])
    targets = draft["experiments"][0]["tmm_task"]["targets"]
    targets[0].update(wavelength_min_nm=450, wavelength_max_nm=550, constraint="at_least", target=0.9)
    targets.append({**targets[0], "wavelength_min_nm": 600, "wavelength_max_nm": 700, "constraint": "at_most", "target": 0.03})
    frozen = standard(metric("maximize", (450, 550)), metric("minimize", (600, 700)))
    aligned, changes = _align_targets_with_scoring_standard(TMMTaskDraft.model_validate(draft), frozen)
    assert not changes
    assert [(row["constraint"], row["target"]) for row in aligned.experiments[0].tmm_task["targets"]] == [("at_least", 0.9), ("at_most", 0.03)]
    reverse, _ = _align_targets_with_scoring_standard(TMMTaskDraft.model_validate(draft), standard(*reversed([metric("maximize", (450, 550)), metric("minimize", (600, 700))])))
    assert aligned == reverse


def test_same_band_opposite_channels_have_distinct_identity_and_no_cross_alignment():
    left = metric("maximize")
    right = metric("minimize", region={"wavelength_nm": [450, 700], "angle_deg": 0, "polarization": "p"})
    assert verify_metric_reference(left).variable != verify_metric_reference(right).variable
    draft = deepcopy(saved()["compiler_draft"])
    draft["experiments"][0]["tmm_task"]["targets"][0].update(constraint="at_least", target=0.9)
    _, changes = _align_targets_with_scoring_standard(TMMTaskDraft.model_validate(draft), standard(left, right))
    assert not changes


def test_overlapping_conflicting_regions_are_rejected_without_guessing():
    frozen = standard(metric("maximize", (400, 800)), metric("minimize", (450, 700)))
    with pytest.raises(ValueError, match="ambiguous frozen directions"):
        _align_targets_with_scoring_standard(TMMTaskDraft.model_validate(saved()["compiler_draft"]), frozen)


def test_multiple_explicit_objectives_bind_direction_to_band_and_statistic():
    question = "Maximize mean reflectance over 450-550 nm and minimize worst-case reflectance over 600-700 nm."
    rows = [metric("maximize", (450, 550)), metric("minimize", (600, 700), metric="worst_case_reflectance")]
    assert not objective_intent_errors(question, rows)
    rows[0]["sense"] = "minimize"
    assert any("direction" in error for error in objective_intent_errors(question, rows))


def test_mean_reduction_is_bound_to_channel_when_band_is_identical():
    question = "Minimize arithmetic mean reflectance over 450-700 nm with s polarization and maximize wavelength-integral mean reflectance over 450-700 nm with p polarization."
    for pol, expected in (("s", "sample_mean"), ("p", "wavelength_integral_mean")):
        assert requested_mean_reduction(question, "mean_reflectance", {"wavelength_nm": [450, 700], "polarization": pol}) == expected


def test_vague_or_report_only_requests_do_not_invent_objectives():
    assert explicit_objective_intents("Find a useful coating; report R and T.") == ()
    assert len(explicit_objective_intents("Minimize mean reflectance over 450-700 nm and report absorption.")) == 1


def test_report_only_cannot_replace_explicit_directional_objective():
    assert objective_intent_errors("Minimize mean reflectance over 450-700 nm.", [metric("report")])


def test_negated_direction_is_not_a_second_objective():
    intents = explicit_objective_intents("Do not maximize mean reflectance over 450-700 nm; minimize mean reflectance over 450-700 nm.")
    assert len(intents) == 1
    assert intents[0]["sense"] == "minimize"


@pytest.mark.parametrize("wrapper", ["variance", "error", "uncertainty", "contrast", "difference"])
def test_derived_quantity_is_not_treated_as_direct_reflectance(wrapper):
    assert explicit_objective_intents(f"Minimize {wrapper} of reflectance over 450-700 nm.") == ()


@pytest.mark.parametrize("angle", ["not-an-angle", float("nan"), float("inf"), True])
def test_metric_identity_rejects_invalid_numeric_channel_without_crashing(angle):
    assert not verify_metric_reference(metric(region={"wavelength_nm": [450, 700], "angle_deg": angle})).ok


def test_orchestrator_observation_reflection_and_belief_use_frozen_scores(tmp_path):
    from optomind_optics.harness.research_orchestrator import RouteTrack, TMMResearchHarness, TMMResearchHarnessConfig
    frozen = standard(metric(mean_reduction="sample_mean"))
    harness = TMMResearchHarness(
        tmp_path, problem_analyzer=object(), method_researcher=object(),
        strategy_planner=object(), task_compiler=object(),
        config=TMMResearchHarnessConfig(qwen_force_mock=True),
    )
    harness.scoring_standard = frozen
    harness._write("REQUEST.json", {"question": saved()["original_request"]})
    route = {"route_id": "r1", "title": "AR", "execution_request": saved()["original_request"]}
    track = RouteTrack(route_id="r1", source="planned", current_route=route)
    rows = []
    for index, (raw_score, observed) in enumerate(((0.2, 0.05), (0.9, 0.2)), 1):
        directory = tmp_path / "iterations" / f"iteration_{index:02}"
        directory.mkdir(parents=True)
        (directory / "ROUTE.ATTESTATION.json").write_text("{}")
        report = {"target_attainment": {frozen.metrics[0].objective_id: {
            "metric": "mean_reflectance", "region": frozen.metrics[0].region,
            "mean_reduction": "sample_mean", "observed": observed,
        }}}
        outcome = {"ok": True, "run_payload": {"status": "completed", "experiment_results": [{
            "experiment_id": "e1", "mode": "optimize", "physically_valid_candidate_count": 1,
            "portfolio": {"candidates": [{"candidate_id": f"c{index}", "physically_admissible": True,
                "target_score": raw_score, "metadata": {"objective_report": report}}],
                "selected_roles": {"best_target_score": f"c{index}"}},
        }]}}
        ctx = harness._observe_track_round({"track": track, "route": route, "route_id": "r1",
            "iteration_id": f"iteration_{index:02}", "iteration_dir": directory,
            "compilation_status": "compiled", "compilation_payload": {}}, outcome, index, rows)
    assert track.score_history == pytest.approx([-0.05, -0.2])
    assert rows[-1].best_target_score == 0.9
    assert rows[-1].decision_score == -0.2
    client = RecordedClient({"observed_vs_expected": "Regression", "deviation_mechanism": "Unresolved",
        "continue_recommended": True, "continue_rationale": "Try another bounded initialization",
        "insight_for_next": "Change initialization", "insight_grounding": "Measured regression"})
    track.executed_history = tuple(json.dumps({"iteration_id": f"iteration_{i:02}", "execution_facts": {"iteration_id": f"iteration_{i:02}", "physics_executed": True, "optimizer_performed": False, "source": "synthetic_observation_fixture"}}) for i in (1, 2))
    harness._reflection_client = client
    reflection = harness._reflect_track(ctx, {})
    payload = client.requests[-1]
    assert payload["observed_metrics"]["decision_score"] == -0.2
    assert payload["score_history"] == pytest.approx([-0.05, -0.2])
    assert payload["observed_metrics"]["rounds_executed"] == 2
    verdict = harness._resolve_track_verdict(ctx, reflection, True)
    assert verdict["decision"].action == "refine_route"
    assert harness._track_confidence == {}


def test_thickness_range_and_angle_uncertainty_do_not_become_metric_region():
    question = "Minimize reflectance. Layer thickness must be 40-160 nm. Angle uncertainty is bounded by 2 degrees."
    assert explicit_objective_intents(question)[0]["region"] == {}


def test_charter_allows_requested_bands_in_separate_experiments():
    from optomind_optics.harness.research_charter import check_frozen_task_constraints
    question = "Maximize mean reflectance over 450-550 nm and minimize mean reflectance over 600-700 nm."
    experiments = []
    for sense, band in (("maximize", (450, 550)), ("minimize", (600, 700))):
        experiments.append({"tmm_task": {"targets": [{"observable": "R", "aggregation": "mean",
            "constraint": "at_least" if sense == "maximize" else "at_most", "target": 0.5,
            "wavelength_min_nm": band[0], "wavelength_max_nm": band[1]}]}})
    check_frozen_task_constraints({"experiments": experiments}, build_research_charter(question, {}))
    with pytest.raises(ValueError, match="missing"):
        check_frozen_task_constraints({"experiments": experiments[:1]}, build_research_charter(question, {}))


def test_insight_appendage_uses_frozen_scale_without_inventing_a_bound():
    from optomind_optics.harness.action_ledger import build_insight_appendage, update_belief
    rows = [{"score_basis": "frozen_standard", "score_available": True, "score_basis_fingerprint": "frozen",
        "best_frozen_score": frozen, "best_target_score": raw} for frozen, raw in ((-0.05, 0.2), (-0.2, 0.9))]
    text = build_insight_appendage(route_id="r1", observations=rows, directives=[])
    assert "delta -0.1500" in text
    assert "headroom to 1.0" not in text
    assert update_belief(0.5, -0.15, None).after is None
    assert update_belief(0.5, -0.15, 0.1).after is None
    assert update_belief(0.5, -0.15, 0.1).status == "unresolved"
