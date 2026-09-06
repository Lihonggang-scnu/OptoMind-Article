"""Declarative OptimizationProblem: metric mapping, constraint boundaries,
and the compile → optimize → certify end-to-end chain.

The declarative layer adds no optimizer: every test compares compiled output
against hand-built equivalent OptimizationTask objects, and the end-to-end
case runs the existing optimize chain with its existing acceptance
certificate.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tmm_engine import (
    OptimizationTask,
    SpectralTarget,
)
from tmm_engine.analytic_oracle import quarter_wave_stack_band_edges
from tmm_engine.design_problem import (
    DesignProblemError,
    OptimizationProblemModel,
    compile_design_problem,
)
from tmm_engine.execution import ExecutionSettings
from tmm_engine.managed_execution import execute_managed_task


def _problem(**overrides) -> dict:
    problem = {
        "schema_version": "veritmm-design-problem-v1",
        "stack": {
            "layers": [
                {"constant_n": 2.4, "thickness_nm": 62.5},
                {"constant_n": 1.46, "thickness_nm": 102.7},
            ],
            "incident": {"constant_n": 1.0},
            "exit": {"constant_n": 1.52},
        },
        "objectives": [
            {
                "metric": "MeanReflectance",
                "band_nm": [500.0, 700.0],
                "pol": "s",
                "direction": "maximize",
                "weight": 2.0,
            }
        ],
        "parameters": [{"layer_selector": "all", "bounds_nm": [30.0, 160.0]}],
        "solver": {"max_steps": 3, "starts": 1},
    }
    problem.update(overrides)
    return problem


def _target(task: OptimizationTask, name_prefix: str) -> SpectralTarget:
    return next(t for t in task.targets if t.name.startswith(name_prefix))


def test_mean_metric_maps_to_a_single_at_least_target() -> None:
    task = compile_design_problem(
        OptimizationProblemModel.model_validate(_problem())
    )
    target = task.targets[0]
    assert target.observable == "R"
    assert target.target == 1.0
    assert target.constraint == "at_least"
    assert target.aggregation == "mean"
    assert target.weight == 2.0
    assert (target.wavelength_min_nm, target.wavelength_max_nm) == (500.0, 700.0)
    assert target.polarization == "s"


@pytest.mark.parametrize(
    "metric,observable",
    [
        ("MeanReflectance", "R"),
        ("MeanTransmittance", "T"),
        ("MeanAbsorption", "A"),
    ],
)
def test_mean_metric_observable_mapping(metric: str, observable: str) -> None:
    problem = _problem()
    problem["objectives"][0]["metric"] = metric
    task = compile_design_problem(OptimizationProblemModel.model_validate(problem))
    assert task.targets[0].observable == observable


def test_minimize_maps_to_drive_to_zero() -> None:
    problem = _problem()
    problem["objectives"][0]["direction"] = "minimize"
    task = compile_design_problem(OptimizationProblemModel.model_validate(problem))
    assert task.targets[0].target == 0.0
    assert task.targets[0].constraint == "match"


def test_stopband_ripple_maps_to_worst_case_aggregation() -> None:
    problem = _problem()
    problem["objectives"][0]["metric"] = "StopbandRipple"
    problem["objectives"][0]["band_nm"] = [560.0, 640.0]
    task = compile_design_problem(OptimizationProblemModel.model_validate(problem))
    assert task.targets[0].aggregation == "worst_case"
    assert task.targets[0].constraint == "at_least"
    assert task.targets[0].target == 1.0
    # minimize direction suppresses the worst-case R toward zero instead.
    problem["objectives"][0]["direction"] = "minimize"
    task = compile_design_problem(OptimizationProblemModel.model_validate(problem))
    assert task.targets[0].constraint == "match"
    assert task.targets[0].target == 0.0


def test_band_contrast_expands_into_dual_band_composite() -> None:
    problem = _problem()
    problem["objectives"][0] = {
        "metric": "BandContrast",
        "band_nm": [580.0, 620.0],
        "contrast_band_nm": [730.0, 770.0],
        "pol": "s",
        "direction": "maximize",
        "weight": 3.0,
    }
    task = compile_design_problem(OptimizationProblemModel.model_validate(problem))
    assert len(task.targets) == 2
    in_band = _target(task, "BandContrast:in_band")
    contrast = _target(task, "BandContrast:contrast_band")
    assert in_band.constraint == "at_least" and in_band.target == 1.0
    assert contrast.constraint == "match" and contrast.target == 0.0
    assert (in_band.wavelength_min_nm, in_band.wavelength_max_nm) == (580.0, 620.0)
    assert (contrast.wavelength_min_nm, contrast.wavelength_max_nm) == (730.0, 770.0)
    # even split of the objective weight across the two bands
    assert in_band.weight == pytest.approx(1.5)
    assert contrast.weight == pytest.approx(1.5)
    assert in_band.polarization == "s"


def test_band_contrast_minimize_inverts_the_bands() -> None:
    problem = _problem()
    problem["objectives"][0] = {
        "metric": "BandContrast",
        "band_nm": [580.0, 620.0],
        "contrast_band_nm": [730.0, 770.0],
        "direction": "minimize",
    }
    task = compile_design_problem(OptimizationProblemModel.model_validate(problem))
    in_band = _target(task, "BandContrast:in_band")
    contrast = _target(task, "BandContrast:contrast_band")
    assert in_band.constraint == "match" and in_band.target == 0.0
    assert contrast.constraint == "at_least" and contrast.target == 1.0


def test_band_contrast_directionality_against_oracle_stopband() -> None:
    """The in-band/contrast bands are chosen from the exact stopband edges:
    inside the stopband R is high, beyond the long edge R rolls off, so a
    maximize-contrast problem must push R up in-band and down outside."""

    short_edge, long_edge = quarter_wave_stack_band_edges(2.4, 1.46, 600.0)
    assert short_edge < 600.0 < long_edge
    problem = _problem()
    problem["objectives"][0] = {
        "metric": "BandContrast",
        "band_nm": [590.0, 610.0],  # deep inside the stopband
        "contrast_band_nm": [long_edge + 20.0, long_edge + 60.0],  # beyond it
        "direction": "maximize",
    }
    task = compile_design_problem(OptimizationProblemModel.model_validate(problem))
    in_band = _target(task, "BandContrast:in_band")
    contrast = _target(task, "BandContrast:contrast_band")
    assert in_band.constraint == "at_least" and in_band.target == 1.0
    assert contrast.target == 0.0
    # the derived grid must cover both bands
    wavelengths = task.simulation.spectrum.wavelengths_nm()
    assert wavelengths[0] <= 590.0 and wavelengths[-1] >= long_edge + 60.0


def test_structural_constraints_are_compile_time_rejections() -> None:
    problem = _problem()
    problem["constraints"] = [{"kind": "layer_count_max", "value": 1}]
    with pytest.raises(DesignProblemError) as excinfo:
        compile_design_problem(OptimizationProblemModel.model_validate(problem))
    assert excinfo.value.code == "constraint_violated"

    problem = _problem()
    problem["constraints"] = [
        {"kind": "forbidden_materials", "forbidden_materials": ["TiO2"]}
    ]
    problem["stack"]["layers"][0] = {"material": "tio2", "thickness_nm": 60.0}
    with pytest.raises(DesignProblemError) as excinfo:
        compile_design_problem(OptimizationProblemModel.model_validate(problem))
    assert excinfo.value.code == "constraint_violated"

    problem = _problem()
    problem["constraints"] = [{"kind": "material_count_max", "value": 1}]
    with pytest.raises(DesignProblemError) as excinfo:
        compile_design_problem(OptimizationProblemModel.model_validate(problem))
    assert excinfo.value.code == "constraint_violated"


def test_thickness_bounds_and_total_thickness_cap() -> None:
    problem = _problem()
    problem["constraints"] = [
        {"kind": "layer_thickness_min", "value": 40.0},
        {"kind": "total_thickness_max", "value": 240.0},
    ]
    problem["parameters"] = [{"layer_selector": "all", "bounds_nm": [30.0, 160.0]}]
    task = compile_design_problem(OptimizationProblemModel.model_validate(problem))
    layers = task.simulation.stack.layers
    # the constraint raises the floor; where the parameter default is already
    # tighter (layer 2: th/2 = 51.35), the tighter bound wins
    assert layers[0].min_thickness_nm == 40.0
    assert layers[1].min_thickness_nm >= 40.0
    assert sum(layer.max_thickness_nm for layer in layers) <= 240.0 + 1e-9
    for layer in layers:
        assert layer.min_thickness_nm <= layer.thickness_nm <= layer.max_thickness_nm


def test_infeasible_total_thickness_is_rejected() -> None:
    problem = _problem()
    problem["constraints"] = [{"kind": "total_thickness_max", "value": 45.0}]
    problem["parameters"] = [{"layer_selector": "all", "bounds_nm": [30.0, 160.0]}]
    with pytest.raises(DesignProblemError) as excinfo:
        compile_design_problem(OptimizationProblemModel.model_validate(problem))
    assert excinfo.value.code == "constraint_infeasible"


def test_selectors_select_layers() -> None:
    problem = _problem()
    problem["parameters"] = [{"layer_selector": "alternating", "bounds_nm": [30.0, 160.0]}]
    task = compile_design_problem(OptimizationProblemModel.model_validate(problem))
    assert [i for i, layer in enumerate(task.simulation.stack.layers) if layer.optimizable] == [0]

    problem["stack"]["layers"].append({"constant_n": 2.4, "thickness_nm": 62.5})
    problem["parameters"] = [{"layer_selector": "cavity", "bounds_nm": [30.0, 160.0]}]
    task = compile_design_problem(OptimizationProblemModel.model_validate(problem))
    assert [i for i, layer in enumerate(task.simulation.stack.layers) if layer.optimizable] == [1]

    problem["stack"]["layers"].append({"constant_n": 1.46, "thickness_nm": 102.7})
    problem["parameters"] = [{"layer_selector": "cavity", "bounds_nm": [30.0, 160.0]}]
    with pytest.raises(DesignProblemError) as excinfo:
        compile_design_problem(OptimizationProblemModel.model_validate(problem))
    assert excinfo.value.code == "no_cavity_layer"


def test_strict_schema_rejects_unknown_fields_and_missing_contrast() -> None:
    problem = _problem()
    problem["unknown_field"] = 1
    with pytest.raises(ValidationError):
        OptimizationProblemModel.model_validate(problem)

    problem = _problem()
    problem["objectives"][0] = {
        "metric": "BandContrast",
        "band_nm": [580.0, 620.0],
    }
    with pytest.raises(ValidationError):
        OptimizationProblemModel.model_validate(problem)


def test_derived_and_explicit_spectra() -> None:
    task = compile_design_problem(OptimizationProblemModel.model_validate(_problem()))
    wavelengths = task.simulation.spectrum.wavelengths_nm()
    assert wavelengths[0] <= 500.0 and wavelengths[-1] >= 700.0

    problem = _problem()
    problem["spectrum"] = [480.0, 720.0, 101]
    task = compile_design_problem(OptimizationProblemModel.model_validate(problem))
    wavelengths = task.simulation.spectrum.wavelengths_nm()
    assert wavelengths[0] == 480.0 and wavelengths[-1] == 720.0


def test_end_to_end_compile_optimize_and_certify(tmp_path) -> None:
    problem = OptimizationProblemModel.model_validate(
        {
            "schema_version": "veritmm-design-problem-v1",
            "stack": {
                "layers": [
                    {"constant_n": 2.4, "thickness_nm": 62.5},
                    {"constant_n": 1.46, "thickness_nm": 102.7},
                    {"constant_n": 2.4, "thickness_nm": 62.5},
                    {"constant_n": 1.46, "thickness_nm": 102.7},
                ],
                "incident": {"constant_n": 1.0},
                "exit": {"constant_n": 1.52},
            },
            "objectives": [
                {
                    "metric": "MeanReflectance",
                    "band_nm": [500.0, 700.0],
                    "pol": "s",
                    "direction": "maximize",
                }
            ],
            "constraints": [{"kind": "total_thickness_max", "value": 420.0}],
            "parameters": [{"layer_selector": "all", "bounds_nm": [30.0, 160.0]}],
            "solver": {"max_steps": 5, "starts": 1, "seed": 7},
        }
    )
    task = compile_design_problem(problem)
    assert isinstance(task, OptimizationTask)
    envelope = execute_managed_task(
        "optimize",
        task,
        tmp_path / "run",
        execution_settings=ExecutionSettings(),
    )
    assert envelope["status"] == "completed"
    assert envelope.get("certificate_id")
    assert envelope.get("task_sha256")

    import json as jsonlib
    from pathlib import Path

    cert_path = Path(tmp_path / "run" / "PHYSICS_ACCEPTANCE_CERTIFICATE.json")
    assert cert_path.is_file()
    certificate_document = jsonlib.loads(cert_path.read_text(encoding="utf-8"))
    assert certificate_document["accepted"] is True
    assert certificate_document["certificate_id"] == envelope["certificate_id"]


def test_design_problem_schema_is_exportable() -> None:
    from tmm_engine.protocol.schema_export import export_schema

    schema = export_schema("design-problem")
    assert schema["$schema"].startswith("https://json-schema.org/draft/2020-12")
    assert "OptimizationProblemModel" in schema.get("$title", "") or (
        "properties" in schema
    )


def test_polarization_extinction_metric_maps_to_dual_polarization_targets() -> None:
    """V-13 generalization drill (b): a new metric registered through the
    V-06 pattern (Literal + mapping) compiles into the wanted/orthogonal
    polarization target pair with an even weight split."""

    problem = _problem()
    problem["objectives"] = [
        {
            "metric": "PolarizationExtinction",
            "band_nm": [550.0, 650.0],
            "pol": "p",
            "direction": "maximize",
            "weight": 4.0,
        }
    ]
    task = compile_design_problem(OptimizationProblemModel.model_validate(problem))
    assert len(task.targets) == 2
    wanted = _target(task, "PolarizationExtinction:wanted")
    orthogonal = _target(task, "PolarizationExtinction:orthogonal")
    assert wanted.observable == "T" and wanted.polarization == "p"
    assert wanted.constraint == "at_least" and wanted.target == 1.0
    assert orthogonal.polarization == "s"
    assert orthogonal.constraint == "match" and orthogonal.target == 0.0
    assert wanted.weight == orthogonal.weight == 2.0

    # unpolarized would be meaningless for an extinction ratio: rejected
    problem["objectives"][0]["pol"] = "unpolarized"
    with pytest.raises(ValidationError):
        compile_design_problem(
            OptimizationProblemModel.model_validate(problem)
        )
