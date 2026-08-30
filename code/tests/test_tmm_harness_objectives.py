from __future__ import annotations

import numpy as np

from optomind_optics.harness.objectives import (
    TMMRobustnessEvaluator,
    evaluate_declared_objectives,
    evaluate_optimization_objectives,
)
from optomind_optics.harness.design_task import ObjectivePreference
from tmm_engine import (
    IlluminationSpec,
    LayerSpec,
    MaterialRegistry,
    MediumSpec,
    OptimizationTask,
    OptimizerSpec,
    SimulationTask,
    SpectralGrid,
    SpectralTarget,
    StackSpec,
    TMMWorkbench,
)


def _task() -> OptimizationTask:
    simulation = SimulationTask(
        stack=StackSpec(
            layers=(LayerSpec(None, 110.0, constant_n=1.23, min_thickness_nm=40.0, max_thickness_nm=220.0),),
            incident=MediumSpec.air(),
            exit=MediumSpec(constant_n=1.52),
        ),
        spectrum=SpectralGrid(start_nm=500.0, stop_nm=600.0, points=31),
        illumination=IlluminationSpec((0.0,), ("unpolarized",)),
    )
    return OptimizationTask(
        simulation=simulation,
        targets=(SpectralTarget("R", 0.01, 500.0, 600.0, constraint="at_most", name="band_R"),),
        optimizer=OptimizerSpec(max_steps=2, starts=1, seed=9),
    )


def test_objective_report_is_continuous_ranking_not_gate() -> None:
    task = _task()
    workbench = TMMWorkbench(MaterialRegistry())
    report = evaluate_optimization_objectives(task, workbench.simulate(task.simulation))
    assert 0.0 < report.aggregate_soft_score <= 1.0
    assert report.admission_role == "ranking_only"
    assert "accepted" not in report.model_dump(mode="json")


def test_robustness_is_reproducible_and_never_claims_physics_acceptance(tmp_path) -> None:
    task = _task()
    evaluator = TMMRobustnessEvaluator(TMMWorkbench(MaterialRegistry()))
    first = evaluator.evaluate(task, [110.0], candidate_id="c1", sigma_nm=2.0, samples=8, random_seed=7, work_dir=tmp_path)
    second = evaluator.evaluate(task, [110.0], candidate_id="c1", sigma_nm=2.0, samples=8, random_seed=7)
    np.testing.assert_allclose(first.sample_soft_scores, second.sample_soft_scores)
    assert 0.0 <= first.robustness_score <= 1.0
    assert first.admission_role == "ranking_only"
    assert (tmp_path / "ROBUSTNESS.json").exists()
    assert "target_domain_mean_R" in first.nominal_spectral_metrics
    assert "target_domain_max_R" in first.nominal_spectral_metrics
    assert "target_domain_mean_R" in first.spectral_metric_summary
    assert first.spectral_metric_summary["target_domain_mean_R"]["standard_deviation"] >= 0


def test_robustness_applies_declared_angle_uncertainty_without_gating() -> None:
    task = _task()
    evaluator = TMMRobustnessEvaluator(TMMWorkbench(MaterialRegistry()))
    first = evaluator.evaluate(
        task,
        [110.0],
        candidate_id="angle_robustness",
        sigma_nm=0.0,
        samples=5,
        random_seed=7,
        angle_perturbation_deg=5.0,
    )
    second = evaluator.evaluate(
        task,
        [110.0],
        candidate_id="angle_robustness",
        sigma_nm=0.0,
        samples=5,
        random_seed=7,
        angle_perturbation_deg=5.0,
    )
    assert first == second
    assert len(first.sample_angle_offsets_deg) == 5
    assert any(abs(value) > 0 for value in first.sample_angle_offsets_deg)
    assert all(abs(value) <= 5.0 for value in first.sample_angle_offsets_deg)
    assert first.failed_simulations == 0
    assert first.sample_failure_reasons == ()
    assert "target_domain_mean_R" in first.spectral_metric_summary
    assert first.admission_role == "ranking_only"


def test_relative_uniform_thickness_error_is_bounded_and_reproducible() -> None:
    task = _task()
    evaluator = TMMRobustnessEvaluator(TMMWorkbench(MaterialRegistry()))
    first = evaluator.evaluate(
        task,
        [110.0],
        candidate_id="relative_error",
        sigma_nm=0.0,
        thickness_error_model="relative_uniform",
        relative_fraction=0.02,
        samples=12,
        random_seed=11,
    )
    second = evaluator.evaluate(
        task,
        [110.0],
        candidate_id="relative_error",
        sigma_nm=0.0,
        thickness_error_model="relative_uniform",
        relative_fraction=0.02,
        samples=12,
        random_seed=11,
    )

    assert first == second
    assert first.perturbation_model["distribution"] == "relative_uniform"
    assert first.perturbation_model["relative_fraction"] == 0.02


def test_absolute_uniform_thickness_error_is_supported() -> None:
    task = _task()
    report = TMMRobustnessEvaluator(TMMWorkbench(MaterialRegistry())).evaluate(
        task,
        [110.0],
        candidate_id="absolute_uniform",
        sigma_nm=2.0,
        thickness_error_model="absolute_uniform",
        relative_fraction=0.0,
        samples=8,
        random_seed=7,
    )
    assert report.perturbation_model["distribution"] == "absolute_uniform"
    assert report.failed_simulations == 0


def test_forward_band_preferences_are_materialized_for_every_angle() -> None:
    wavelengths = np.linspace(3000.0, 13000.0, 101)
    channels = {}
    for angle, high, low in ((0.0, 0.80, 0.10), (60.0, 0.70, 0.15)):
        absorption = np.full(wavelengths.shape, 0.3)
        absorption[(wavelengths >= 8000.0) & (wavelengths <= 13000.0)] = high
        absorption[(wavelengths >= 3000.0) & (wavelengths <= 5000.0)] = low
        channels[f"angle={angle:g}|pol=unpolarized"] = {
            "R": 1.0 - absorption,
            "T": np.zeros_like(absorption),
            "A": absorption,
        }
    from tmm_engine.workbench import ForwardSimulationResult

    result = ForwardSimulationResult(
        wavelengths_nm=wavelengths,
        channels=channels,
        material_provenance=[],
        solver="smatrix",
    )
    preferences = (
        ObjectivePreference(
            objective_id="thermal_contrast",
            metric="band_emissivity_contrast",
            sense="maximize",
            region={
                "preferred_wavelength_nm": [8000.0, 13000.0],
                "suppressed_wavelength_nm": [3000.0, 5000.0],
            },
        ),
        ObjectivePreference(
            objective_id="spectrum_report",
            metric="emissivity_spectrum",
            sense="report",
            region={"wavelength_nm": [3000.0, 13000.0]},
        ),
    )

    report = evaluate_declared_objectives(preferences, result)

    contrast = report.target_attainment["thermal_contrast"]
    assert set(contrast["channel_observations"]) == set(channels)
    assert contrast["observed"] > 0.60
    assert contrast["soft_score"] > 0.60
    assert report.target_attainment["spectrum_report"]["role"] == "report_only"


def test_declared_worst_case_objectives_use_band_extremum_not_mean() -> None:
    wavelengths = np.linspace(450.0, 700.0, 6)
    reflectance = np.array([0.004, 0.01, 0.02, 0.03, 0.05, 0.012])
    transmittance = np.array([0.90, 0.95, 0.98, 0.70, 0.96, 0.94])
    absorptance = np.array([0.20, 0.05, 0.15, 0.30, 0.02, 0.10])
    from tmm_engine.workbench import ForwardSimulationResult

    result = ForwardSimulationResult(
        wavelengths_nm=wavelengths,
        channels={
            "angle=0|pol=s": {
                "R": reflectance,
                "T": transmittance,
                "A": absorptance,
            }
        },
        material_provenance=[],
        solver="smatrix",
    )
    preferences = (
        ObjectivePreference(
            objective_id="mean_R",
            metric="mean_reflectance",
            sense="minimize",
            target=0.008,
            region={"wavelength_nm": [450.0, 700.0]},
        ),
        ObjectivePreference(
            objective_id="worst_R",
            metric="worst_case_reflectance",
            sense="minimize",
            target=0.03,
            region={"wavelength_nm": [450.0, 700.0]},
        ),
        ObjectivePreference(
            objective_id="worst_T",
            metric="worst_case_transmittance",
            sense="maximize",
            target=0.90,
            region={"wavelength_nm": [450.0, 700.0]},
        ),
        ObjectivePreference(
            objective_id="worst_A",
            metric="worst_case_absorption",
            sense="match",
            target=0.10,
            region={"wavelength_nm": [450.0, 700.0]},
        ),
    )

    report = evaluate_declared_objectives(preferences, result)

    mean_row = report.target_attainment["mean_R"]
    expected_band_mean = float(np.trapezoid(reflectance, wavelengths) / 250.0)
    np.testing.assert_allclose(mean_row["observed"], expected_band_mean)
    assert mean_row["aggregation"] == "mean"

    worst_r = report.target_attainment["worst_R"]
    np.testing.assert_allclose(worst_r["observed"], float(np.max(reflectance)))
    assert worst_r["aggregation"] == "worst_case"
    assert worst_r["observed"] > mean_row["observed"]

    worst_t = report.target_attainment["worst_T"]
    np.testing.assert_allclose(worst_t["observed"], float(np.min(transmittance)))
    assert worst_t["observed"] < float(np.mean(transmittance))

    worst_a = report.target_attainment["worst_A"]
    expected_match_extreme = float(
        absorptance[np.argmax(np.abs(absorptance - 0.10))]
    )
    np.testing.assert_allclose(worst_a["observed"], expected_match_extreme)
