from __future__ import annotations

import cmath
import json
import socket
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from optomind_optics.harness.design_task import OpticalDesignTask
from optomind_optics.harness.objectives import evaluate_optimization_objectives
from optomind_optics.harness.optimizer_registry import _objective_from_forward
from optomind_optics.harness.orchestrator import TMMHarnessConfig, TMMHarnessOrchestrator
from optomind_optics.harness.science.contracts import ModelCandidate, ResearchTask
from optomind_optics.harness.science.gradient_binding import adopted_gradient_summary, make_gradient_binding
from optomind_optics.harness.research_orchestrator import _compute_gradient_summary
from optomind_optics.harness.scoring_standard import FixedScoreMetric, ScoringStandard
from tmm_engine import MaterialRegistry, TMMWorkbench
from tmm_engine.optimization import DifferentiableThicknessOptimizer
from tmm_engine.task_io import optimization_task_from_dict

FIXTURE = Path(__file__).parent / "fixtures" / "article_r3_returned_task.json"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("offline regression attempted network access")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def task_payload():
    return json.loads(FIXTURE.read_text())


def fixed_standard(reduction="sample_mean", formula="-r"):
    return ScoringStandard(metrics=(FixedScoreMetric(variable="r", canonical_id="mean_reflectance@450-700nm@0deg@s",
        metric="mean_reflectance", sense="minimize", target=.03, mean_reduction=reduction,
        region={"wavelength_nm": [450., 700.], "angle_deg": 0., "polarization": "s"}),), formula=formula)


def independent_reflectance(simulation, thicknesses, wavelengths):
    layers = simulation["stack"]["layers"]
    incident = simulation["stack"]["incident"]["constant_n"]
    substrate = simulation["stack"]["exit"]["constant_n"]
    output = []
    for wavelength in wavelengths:
        a, b, c, d = 1 + 0j, 0j, 0j, 1 + 0j
        for layer, thickness in zip(layers, thicknesses):
            n = layer["constant_n"]
            phase = 2 * cmath.pi * n * thickness / wavelength
            co, si = cmath.cos(phase), cmath.sin(phase)
            a, b, c, d = a * co + b * 1j * n * si, a * 1j * si / n + b * co, c * co + d * 1j * n * si, c * 1j * si / n + d * co
        admittance = (c + substrate * d) / (a + substrate * b)
        output.append(abs((incident - admittance) / (incident + admittance)) ** 2)
    return np.asarray(output)


@pytest.mark.parametrize("reduction", ["sample_mean", "wavelength_integral_mean"])
@pytest.mark.parametrize("constraint", ["at_most", "at_least", "match"])
def test_requested_statistic_is_identical_for_gradient_global_and_reporting(reduction, constraint):
    torch = pytest.importorskip("torch")
    native = optimization_task_from_dict(task_payload()["experiments"][0]["tmm_task"])
    native = replace(native, simulation=replace(native.simulation,
        spectrum=replace(native.simulation.spectrum, values_nm=(450., 500., 700.))))
    native = replace(native, targets=(replace(native.targets[0], mean_reduction=reduction, constraint=constraint),))
    optimizer = DifferentiableThicknessOptimizer(MaterialRegistry())
    values = [layer.thickness_nm for layer in native.simulation.stack.layers]
    differentiable, _ = optimizer.evaluate(native, values)
    forward = TMMWorkbench(MaterialRegistry()).simulate(native.simulation)
    global_loss, _ = _objective_from_forward(native, forward)
    report = evaluate_optimization_objectives(native, forward)
    observed = np.mean(forward.channel(0., "s")["R"]) if reduction == "sample_mean" else np.trapezoid(forward.channel(0., "s")["R"], forward.wavelengths_nm) / 250.
    expected = (observed - .03) ** 2 if constraint == "match" else 1 - observed if constraint == "at_least" else observed
    assert differentiable == pytest.approx(expected, abs=1e-12)
    assert global_loss == pytest.approx(expected, abs=1e-12)
    assert report.weighted_directional_loss == pytest.approx(expected, abs=1e-12)
    assert report.target_attainment[native.targets[0].name]["loss_semantics"] == "requested_statistic"


def test_full_returned_task_reaches_original_goal_inside_actual_64_budget(tmp_path, monkeypatch):
    pytest.importorskip("torch")
    from tmm_engine.differentiable import DifferentiableTMM
    counter = {"verification_and_uncertainty": 0, "search_design_rows": 0}
    original_simulate = TMMWorkbench.simulate
    original_forward = DifferentiableTMM.forward
    def simulate(self, *args, **kwargs):
        counter["verification_and_uncertainty"] += 1
        return original_simulate(self, *args, **kwargs)
    def forward(self, thicknesses, *args, **kwargs):
        counter["search_design_rows"] += int(thicknesses.shape[0])
        return original_forward(self, thicknesses, *args, **kwargs)
    monkeypatch.setattr(TMMWorkbench, "simulate", simulate)
    monkeypatch.setattr(DifferentiableTMM, "forward", forward)
    task = OpticalDesignTask.model_validate(task_payload())
    orchestrator = TMMHarnessOrchestrator(tmp_path, run_id="r3_actual64", config=TMMHarnessConfig(use_qwen_policy=False))
    result = orchestrator.run(task)
    assert result.status == "completed"
    assert result.budget["usage"]["forward_evaluations"] == sum(counter.values()) <= 64
    assert not result.budget["overrun"]
    assert not result.qwen_usage
    experiment = result.experiment_results[0]
    portfolio = experiment["portfolio"]
    candidates = [row for row in portfolio["candidates"] if row["physically_admissible"]]
    standard = fixed_standard()
    winner = max(candidates, key=lambda row: standard.score(row["metadata"]["objective_report"]).value)
    thicknesses = winner["metadata"]["thicknesses_nm"]
    assert all(40 <= thickness <= 160 for thickness in thicknesses)
    assert winner["metadata"]["robustness_report"]["sample_soft_scores"]
    simulation = task_payload()["experiments"][0]["tmm_task"]["simulation"]
    independent = independent_reflectance(simulation, thicknesses, np.arange(450, 701, 5))
    adopted_score = standard.score(winner["metadata"]["objective_report"]).value
    assert -adopted_score == pytest.approx(float(independent.mean()), abs=1e-12)
    assert independent.mean() < .03
    experiment_dir = orchestrator._experiment_directory(experiment["experiment_id"])
    allocation = json.loads((experiment_dir / "SEARCH_ALLOCATION.json").read_text())
    assert allocation["survivor_limit"] <= 2
    assert allocation["primary_allocated"] > 21
    assert allocation["global_allocated"] == 0 or allocation["global_allocated"] >= allocation["global_minimum_useful_evaluations"]
    optimizer = json.loads((experiment_dir / "OPTIMIZER_gradient_thickness.json").read_text())
    assert optimizer["audit"]["raw_optimizer_audit"]["local_finish_forward_evaluations"] > 0
    assert optimizer["audit"]["seed"] == 17
    portfolio["selected_roles"]["best_target_score"] = portfolio["candidates"][0]["candidate_id"]
    gradient = adopted_gradient_summary(task.model_dump(mode="json"), result.model_dump(mode="json"), tmp_path, scoring_standard=standard)
    assert gradient["status"] == "available", gradient
    assert gradient["selection_role"] == "best_frozen_score"
    assert gradient["candidate_id"] == winner["candidate_id"]
    assert gradient["metric_value"] == pytest.approx(adopted_score, abs=1e-12)
    assert gradient["verification"]["passed"]
    assert gradient["score_basis_fingerprint"]
    assert gradient["combined_round_forward_evaluations"] <= 64
    assert gradient["compute_cost"]["differentiable_forward_calls"] == 5
    before = dict(counter)
    blocked = adopted_gradient_summary(task.model_dump(mode="json"), result.model_dump(mode="json"), tmp_path,
        scoring_standard=standard, maximum_forward_evaluations=4)
    assert blocked["status"] == "unavailable"
    assert blocked["compute_cost"]["differentiable_forward_calls"] == 0
    assert counter == before
    winner["metadata"]["objective_report"]["target_attainment"]["mean_reflectance_target"]["observed"] = -1.
    rejected = adopted_gradient_summary(task.model_dump(mode="json"), result.model_dump(mode="json"), tmp_path, scoring_standard=standard)
    assert rejected["status"] == "unavailable"


def test_frozen_formula_kink_and_unsupported_metrics_fail_closed():
    pytest.importorskip("torch")
    simulation = task_payload()["experiments"][0]["tmm_task"]["simulation"]
    candidate = ModelCandidate(object_id="adopted", source_run_id="offline", model_ir={"simulation": simulation})
    standard = fixed_standard(formula="min(r,r)")
    task = ResearchTask(object_id="objective", source_run_id="offline", question="frozen objective",
        objective={"kind": "optomind_frozen_score", "scoring_standard": standard.model_dump(mode="json")})
    request = make_gradient_binding(simulation, candidate=candidate, objective_task=task, sample={"state": "nominal"})
    result = _compute_gradient_summary({"gradient_binding": request})
    assert result["status"] == "unavailable"
    assert "nondifferentiable_objective" in result["reason"]


def test_failed_gradient_dispatch_remains_charged_and_recovery_stays_bounded(tmp_path, monkeypatch):
    pytest.importorskip("torch")
    from tmm_engine.differentiable import DifferentiableTMM
    attempted = []
    def fail(self, thicknesses, *args, **kwargs):
        attempted.append(int(thicknesses.shape[0]))
        raise RuntimeError("synthetic solver failure after dispatch")
    monkeypatch.setattr(DifferentiableTMM, "forward", fail)
    result = TMMHarnessOrchestrator(tmp_path, run_id="failed_dispatch", config=TMMHarnessConfig(use_qwen_policy=False)).run(
        OpticalDesignTask.model_validate(task_payload()))
    assert sum(attempted) == 1
    commits = {row["action_id"]: row["actual_usage"]["forward_evaluations"] for row in result.budget["events"] if row["event_type"] == "commit"}
    assert commits["optimizer:opt_ar_coating_sio2_tio2:primary"] == 1
    assert result.budget["usage"]["forward_evaluations"] <= 64
    assert not result.budget["overrun"]
    assert result.experiment_results[0]["physically_valid_candidate_count"] > 0


@pytest.mark.parametrize("reduction", ["sample_mean", "wavelength_integral_mean"])
def test_frozen_signed_derivative_matches_independent_characteristic_difference(reduction):
    pytest.importorskip("torch")
    simulation = task_payload()["experiments"][0]["tmm_task"]["simulation"]
    simulation["spectrum"] = {"values_nm": [450., 500., 700.]}
    candidate = ModelCandidate(object_id="initial", source_run_id="offline", model_ir={"simulation": simulation})
    standard = fixed_standard(reduction=reduction)
    task = ResearchTask(object_id="objective", source_run_id="offline", question="frozen signed derivative",
        objective={"kind": "optomind_frozen_score", "scoring_standard": standard.model_dump(mode="json")})
    request = make_gradient_binding(simulation, candidate=candidate, objective_task=task, sample={"state": "nominal"})
    result = _compute_gradient_summary({"gradient_binding": request})
    assert result["status"] == "available", result
    wavelengths = np.array([450., 500., 700.])
    def score(thicknesses):
        values = independent_reflectance(simulation, thicknesses, wavelengths)
        return -float(values.mean() if reduction == "sample_mean" else np.trapezoid(values, wavelengths) / 250.)
    assert result["metric_value"] == pytest.approx(score([80., 80.]), abs=1e-12)
    for row in result["layer_gradients"]:
        plus, minus = [80., 80.], [80., 80.]
        plus[row["layer_index"]] += .001
        minus[row["layer_index"]] -= .001
        reference = (score(plus) - score(minus)) / .002
        assert row["gradient_per_nm"] == pytest.approx(reference, rel=1e-5, abs=1e-9)
