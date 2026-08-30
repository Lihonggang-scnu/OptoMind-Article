from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from optomind_optics.harness.optimizer_registry import (
    DifferentialEvolutionThicknessAdapter,
    GradientThicknessAdapter,
    OptimizerRegistry,
)
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
)


def _task(
    *,
    layers: tuple[LayerSpec, ...] | None = None,
    optimizer: OptimizerSpec | None = None,
    target: float = 0.08,
) -> OptimizationTask:
    if layers is None:
        layers = (
            LayerSpec(
                None,
                120.0,
                constant_n=2.0,
                min_thickness_nm=70.0,
                max_thickness_nm=170.0,
            ),
            LayerSpec(None, 63.0, constant_n=1.45, optimizable=False),
        )
    simulation = SimulationTask(
        stack=StackSpec(
            layers=layers,
            incident=MediumSpec.air(),
            exit=MediumSpec(constant_n=1.5),
        ),
        spectrum=SpectralGrid(start_nm=500.0, stop_nm=600.0, points=17),
        illumination=IlluminationSpec((0.0,), ("unpolarized",)),
    )
    return OptimizationTask(
        simulation=simulation,
        targets=(SpectralTarget("R", target, 540.0, 560.0),),
        optimizer=optimizer
        or OptimizerSpec(method="adam", max_steps=8, starts=1, seed=19),
    )


def test_de_is_reproducible_respects_bounds_fixed_layers_and_budget() -> None:
    task = _task()
    adapter = DifferentialEvolutionThicknessAdapter(
        MaterialRegistry(), population_size=3, max_iterations=20, candidate_limit=6
    )
    first = adapter.optimize(task, maximum_forward_evaluations=19)
    second = adapter.optimize(task, maximum_forward_evaluations=19)

    assert first.status in {"completed", "best_effort"}
    assert first.evaluation_count <= 19
    assert first.evaluation_count == second.evaluation_count
    assert first.best_thicknesses_nm == pytest.approx(second.best_thicknesses_nm)
    assert first.best_loss == pytest.approx(second.best_loss)
    assert first.audit["seed"] == task.optimizer.seed
    assert first.audit["maximum_forward_evaluations"] == 19
    assert 70.0 <= first.best_thicknesses_nm[0] <= 170.0
    assert first.best_thicknesses_nm[1] == pytest.approx(63.0)
    vectors = {
        tuple(candidate["thicknesses_nm"])
        for candidate in first.candidate_designs
    }
    assert len(vectors) >= 2
    assert all(candidate["thicknesses_nm"][1] == pytest.approx(63.0) for candidate in first.candidate_designs)
    assert first.audit["physics_self_certification"] is False


def test_de_budget_exhaustion_is_honest_best_effort_not_target_gate() -> None:
    task = _task(target=0.0)
    result = DifferentialEvolutionThicknessAdapter(MaterialRegistry()).optimize(
        task, max_forward_evaluations=6
    )

    assert result.evaluation_count <= 6
    assert result.stop_reason == "maximum_forward_evaluations"
    assert result.status == "best_effort"
    assert np.isfinite(result.best_loss)
    assert result.audit["target_attainment_used_as_gate"] is False


def test_registry_prefers_gradient_normally_and_de_for_recovery() -> None:
    task = _task()
    registry = OptimizerRegistry(material_registry=MaterialRegistry())

    normal = registry.select(task)
    recovery = registry.select(task, purpose="recovery")

    if normal is not None and isinstance(normal, GradientThicknessAdapter):
        assert isinstance(recovery, DifferentialEvolutionThicknessAdapter)
    else:
        # This branch keeps the test valid in environments without optional
        # PyTorch while still requiring a usable recovery adapter.
        assert isinstance(normal, DifferentialEvolutionThicknessAdapter)
        assert isinstance(recovery, DifferentialEvolutionThicknessAdapter)

    assert registry.get("differential_evolution_thickness") is recovery
    with pytest.raises(ValueError, match="Duplicate optimizer_id"):
        registry.register(DifferentialEvolutionThicknessAdapter(MaterialRegistry()))


def test_capability_failures_happen_before_optimizer_execution() -> None:
    incoherent = _task(
        layers=(replace(_task().simulation.stack.layers[0], coherence="incoherent"),)
    )
    no_variables = _task(
        layers=(LayerSpec(None, 100.0, constant_n=2.0, optimizable=False),)
    )
    unsupported = _task()
    object.__setattr__(unsupported.optimizer, "variable_types", ("material",))

    adapter = DifferentialEvolutionThicknessAdapter(MaterialRegistry())
    for task, code in (
        (incoherent, "incoherent_stack"),
        (no_variables, "no_optimizable_layers"),
        (unsupported, "unsupported_variable_type"),
    ):
        assessment = adapter.assess(task)
        assert not assessment.supported
        assert code in assessment.failure_codes
        with pytest.raises(ValueError, match=code):
            adapter.optimize(task, maximum_forward_evaluations=8)


def test_gradient_adapter_returns_common_result_or_is_explicitly_unavailable() -> None:
    adapter = GradientThicknessAdapter(MaterialRegistry())
    task = _task(optimizer=OptimizerSpec(method="adam", max_steps=5, starts=1, seed=19))
    assessment = adapter.assess(task)
    if not assessment.supported:
        assert "optional_dependency_missing" in assessment.failure_codes
        return

    result = adapter.optimize(task)
    assert result.optimizer_id == "gradient_thickness"
    assert len(result.best_thicknesses_nm) == 2
    assert result.evaluation_count > 0
    assert result.audit["raw_optimizer_audit"]["evaluation_count"] == result.evaluation_count
    assert result.candidate_designs
    assert result.audit["physics_self_certification"] is False
    assert np.isfinite(result.best_loss)
