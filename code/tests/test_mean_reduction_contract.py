from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from optomind_optics.harness.design_task import ObjectivePreference
from optomind_optics.harness.metric_catalog import verify_metric_reference
from optomind_optics.harness.metric_reduction import requested_mean_reduction
from optomind_optics.harness.objectives import evaluate_declared_objectives, evaluate_optimization_objectives
from optomind_optics.harness.scoring_standard import FixedScoreMetric, QwenScoringStandardBuilder, ScoringStandard
from optomind_optics.harness.task_compiler import QwenTMMTaskCompiler
from tmm_engine.spectral_reduction import reduce_spectral_mean
from tmm_engine.task_io import optimization_task_from_dict
from tmm_engine.workbench import ForwardSimulationResult


FIXTURE = Path(__file__).parent / "fixtures" / "mean_reduction_pilot"


class RecordedClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def call(self, messages, **kwargs):
        self.calls.append(messages)
        return {"content": json.dumps(self.responses.pop(0))}


def result_for(wavelengths, reflectance):
    values = np.asarray(reflectance, dtype=float)
    return ForwardSimulationResult(
        wavelengths_nm=np.asarray(wavelengths, dtype=float),
        channels={"angle=0|pol=s": {"R": values, "T": 1.0 - values, "A": np.zeros_like(values)}},
        material_provenance=[], solver="smatrix",
    )


def preference(reduction=None):
    return ObjectivePreference(
        objective_id="mean_R", metric="mean_reflectance", sense="minimize",
        region={"wavelength_nm": [450, 700], "angle_deg": 0, "polarization": "s"},
        mean_reduction=reduction,
    )


@pytest.mark.parametrize("wavelengths,values,sample,integral", [
    ([450, 575, 700], [0.9, 0.0, 0.0], 0.3, 0.225),
    ([450, 500, 700], [0.0, 0.0, 0.9], 0.3, 0.36),
])
def test_explicit_means_and_historical_default(wavelengths, values, sample, integral):
    result = result_for(wavelengths, values)
    for reduction, expected in ((None, integral), ("sample_mean", sample), ("wavelength_integral_mean", integral)):
        report = evaluate_declared_objectives([preference(reduction)], result)
        row = report.target_attainment["mean_R"]
        assert row["observed"] == pytest.approx(expected)
        assert row["mean_reduction"] == (reduction or "wavelength_integral_mean")


def test_reducer_single_point_and_invalid_inputs():
    assert reduce_spectral_mean([500], [0.2], "wavelength_integral_mean") == 0.2
    for wavelengths, values, reduction in (([], [], "sample_mean"), ([500], [float("nan")], "sample_mean"), ([500, 450], [0, 1], "wavelength_integral_mean"), ([500], [1, 2], "sample_mean"), ([500], [1], "not_mean")):
        with pytest.raises(ValueError):
            reduce_spectral_mean(wavelengths, values, reduction)


def test_non_mean_metrics_cannot_claim_mean_reduction():
    with pytest.raises(ValidationError, match="band-mean"):
        ObjectivePreference(objective_id="worst", metric="worst_case_reflectance", sense="minimize", region={"wavelength_nm": [450, 700]}, mean_reduction="sample_mean")
    assert not verify_metric_reference({"metric": "mean_reflectance", "sense": "minimize", "region": {"wavelength_nm": [450, 700]}, "mean_reduction": "invented"}).ok
    assert not verify_metric_reference({"metric": "worst_case_reflectance", "sense": "minimize", "region": {"wavelength_nm": [450, 700]}, "mean_reduction": "sample_mean"}).ok


@pytest.mark.parametrize("separator", ["; use the ", " and ", ", the "])
def test_request_reduction_is_scoped_to_observable_and_band(separator):
    question = f"Use the arithmetic mean reflectance over 450-700 nm{separator}wavelength-integral mean transmittance over 800-900 nm."
    assert requested_mean_reduction(question, "mean_reflectance", {"wavelength_nm": [450, 700]}) == "sample_mean"
    assert requested_mean_reduction(question, "mean_transmittance", {"wavelength_nm": [800, 900]}) == "wavelength_integral_mean"
    assert requested_mean_reduction(question, "mean_reflectance", {"wavelength_nm": [800, 900]}) is None
    assert requested_mean_reduction("Minimize mean reflectance.", "mean_reflectance", {"wavelength_nm": [450, 700]}) is None


def build_pilot():
    request = (FIXTURE / "request.txt").read_text()
    selection = json.loads((FIXTURE / "metric_selection.json").read_text())
    variable = verify_metric_reference(selection["metrics"][0]).variable
    client = RecordedClient(selection, {"formula": f"-{variable}", "rationale": "Lower reflectance ranks higher."})
    standard_result = QwenScoringStandardBuilder(client).build(request)
    assert standard_result.status == "standardized", standard_result.validation_errors
    standard = standard_result.standard
    draft = json.loads((FIXTURE / "compiler_response.json").read_text())
    compiler_client = RecordedClient(draft)
    compiled = QwenTMMTaskCompiler(client=compiler_client, scoring_standard=standard).compile(request)
    assert compiled.status == "compiled", compiled.validation_errors
    assert len(client.calls) == 2
    assert len(compiler_client.calls) == 1
    return standard, compiled.task


def test_original_pilot_request_preserves_arithmetic_through_saved_csv_and_ranking():
    standard, task = build_pilot()
    metric = standard.metrics[0]
    assert metric.mean_reduction == "sample_mean"
    restored = ScoringStandard.model_validate_json(standard.model_dump_json())
    assert restored.metrics[0].mean_reduction == "sample_mean"
    experiment = task.experiments[0]
    assert experiment.tmm_task["targets"][0]["mean_reduction"] == "sample_mean"
    assert all(item.mean_reduction == "sample_mean" for item in experiment.objectives)
    spectrum = np.loadtxt(FIXTURE / "spectrum.csv", delimiter=",", skiprows=1)
    assert spectrum.shape == (51, 3)
    np.testing.assert_allclose(spectrum[:, 1], spectrum[:, 2], atol=1e-14)
    result = result_for(spectrum[:, 0], spectrum[:, 1])
    declared = evaluate_declared_objectives(experiment.objectives, result)
    optimized = evaluate_optimization_objectives(optimization_task_from_dict(experiment.tmm_task), result)
    expected = 0.018799506604644533
    for row in declared.target_attainment.values():
        assert row["observed"] == pytest.approx(expected, abs=1e-15)
        assert row["mean_reduction"] == "sample_mean"
    assert optimized.target_attainment["min_mean_reflectance"]["observed"] == pytest.approx(expected, abs=1e-15)
    assert optimized.target_attainment["min_mean_reflectance"]["loss_semantics"] == "requested_statistic"
    assert restored.score(declared).value == pytest.approx(-expected, abs=1e-15)
    integral = evaluate_declared_objectives([metric.objective_preference().model_copy(update={"mean_reduction": "wavelength_integral_mean"})], result)
    assert integral.target_attainment[metric.objective_id]["observed"] == pytest.approx(0.018838106091412923, abs=1e-15)
    assert not restored.score(integral).ok


def test_optimizer_reporting_uses_explicit_reducer_for_requested_statistic():
    draft = json.loads((FIXTURE / "compiler_response.json").read_text())
    task = optimization_task_from_dict(draft["experiments"][0]["tmm_task"])
    result = result_for([450, 500, 700], [0, 0, 0.9])
    for reduction, expected in ((None, 0.3), ("sample_mean", 0.3), ("wavelength_integral_mean", 0.36)):
        changed = replace(task, targets=(replace(task.targets[0], mean_reduction=reduction),))
        report = evaluate_optimization_objectives(changed, result)
        assert report.target_attainment["min_mean_reflectance"]["observed"] == pytest.approx(expected)
        assert report.weighted_directional_loss == pytest.approx(0.27 if reduction is None else expected)


def test_explicit_reduction_changes_rank_and_rejects_unannotated_measurement():
    metric = FixedScoreMetric(variable="r", canonical_id="mean_reflectance@450-700nm", metric="mean_reflectance", sense="minimize", region={"wavelength_nm": [450, 700]}, mean_reduction="sample_mean")
    results = [result_for([450, 575, 700], [0.9, 0, 0]), result_for([450, 575, 700], [0.27, 0.27, 0.27])]
    for reduction, winner in (("sample_mean", 1), ("wavelength_integral_mean", 0)):
        current = metric.model_copy(update={"mean_reduction": reduction})
        standard = ScoringStandard(metrics=(current,), formula="-r")
        reports = [evaluate_declared_objectives(standard.objective_preferences(), result) for result in results]
        assert standard.rank(reports)[0][0] == winner
        row = reports[0].model_dump(mode="json")
        row["target_attainment"][current.objective_id].pop("mean_reduction")
        assert not standard.score(row).ok
        assert not standard.score({"R_450_700": {"observed": 0.1}}).ok


def test_legacy_weak_match_cannot_accept_known_sample_reduction():
    metric = FixedScoreMetric(variable="r", canonical_id="mean_reflectance@450-700nm", metric="mean_reflectance", sense="minimize", region={"wavelength_nm": [450, 700]})
    standard = ScoringStandard(metrics=(metric,), formula="-r")
    assert standard.score({"R_450_700": {"observed": 0.3}}).ok
    assert standard.score({"R_450_700": {"observed": 0.3, "mean_reduction": "wavelength_integral_mean"}}).ok
    assert not standard.score({"R_450_700": {"observed": 0.3, "mean_reduction": "sample_mean"}}).ok
