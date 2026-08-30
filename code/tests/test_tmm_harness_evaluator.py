from __future__ import annotations

import json

import numpy as np

from optomind_optics.harness.evaluator import TMMResultEvaluator
from tmm_engine.workbench import ForwardSimulationResult


def _result() -> ForwardSimulationResult:
    wavelengths = np.linspace(500.0, 600.0, 101)
    dip = 0.8 - 0.7 * np.exp(-((wavelengths - 550.0) / 4.0) ** 2)
    channels = {}
    for pol, offset in (("s", 0.0), ("p", 0.05)):
        r = np.clip(dip + offset, 0.0, 1.0)
        t = 1.0 - r
        channels[f"angle=30|pol={pol}"] = {"R": r, "T": t, "A": np.zeros_like(r)}
    return ForwardSimulationResult(
        wavelengths_nm=wavelengths,
        channels=channels,
        material_provenance=[],
        solver="smatrix",
        extras={"phase_dispersion|angle=30|pol=s": {"group_delay_s": np.linspace(-1, 1, 101)}},
    )


def test_evaluator_extracts_generic_spectral_and_polarization_information(tmp_path) -> None:
    report = TMMResultEvaluator().evaluate(_result(), work_dir=tmp_path)
    assert report.spectral_points == 101
    assert report.energy_conservation["maximum_absolute_residual"] < 1e-12
    split = report.polarization_splitting["30"]["R"]
    assert split["maximum_absolute_difference"] > 0.04
    dips = [item for item in report.spectral_features if item.feature_type == "local_minimum"]
    assert dips
    assert abs(dips[0].wavelength_nm - 550.0) <= 1.0
    assert dips[0].q_estimate is not None
    payload = json.loads((tmp_path / "ANALYSIS_REPORT.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == "tmm-analysis-report.v1"


def test_evaluator_is_measurement_only_and_has_no_target_pass_flag() -> None:
    payload = TMMResultEvaluator().evaluate(_result()).model_dump(mode="json")
    encoded = json.dumps(payload).lower()
    assert "target_pass" not in encoded
    assert "hard_gate" not in encoded


def test_evaluator_does_not_report_floating_point_noise_as_high_q_feature() -> None:
    wavelengths = np.linspace(400.0, 800.0, 321)
    absorption = np.linspace(1e-3, 0.0, wavelengths.size)
    absorption[270:] = 0.0
    absorption[280:284] = np.asarray([2e-15, -1e-15, 1e-15, 0.0])
    result = ForwardSimulationResult(
        wavelengths_nm=wavelengths,
        channels={
            "angle=0|pol=unpolarized": {
                "R": 0.2 * np.ones_like(wavelengths),
                "T": 0.8 * np.ones_like(wavelengths) - absorption,
                "A": absorption,
            }
        },
        material_provenance=[],
        solver="smatrix",
    )
    report = TMMResultEvaluator().evaluate(result)
    assert not [
        feature
        for feature in report.spectral_features
        if feature.observable == "A" and 740.0 <= feature.wavelength_nm <= 760.0
    ]
