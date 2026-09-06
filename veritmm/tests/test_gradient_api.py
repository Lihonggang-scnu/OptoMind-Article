"""First-class gradient / sensitivity / batch API tests.

Gradients are proposal evidence: these tests pin their numerical correctness
(autodiff vs central differences through the same backend), the physical
sanity of the influence ranking on a quarter-wave DBR, batch-vs-scalar
consistency, and the typed-failure / no-silent-fallback boundaries.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from tmm_engine import (  # noqa: E402
    IlluminationSpec,
    LayerSpec,
    MaterialRegistry,
    MediumSpec,
    SimulationTask,
    SpectralGrid,
    StackSpec,
)
from tmm_engine.gradient_api import (  # noqa: E402
    GradientRequestError,
    batch_simulate,
    compute_gradient,
    compute_sensitivity,
    parse_objective,
)
from tmm_engine.workbench import TMMWorkbench  # noqa: E402


def _quarter_wave_dbr(periods: int = 3, optimizable: bool = True) -> SimulationTask:
    design_nm = 600.0
    return SimulationTask(
        stack=StackSpec(
            layers=tuple(
                LayerSpec(None, design_nm / (4 * n), constant_n=n, optimizable=optimizable)
                for n in (2.4, 1.46) * periods
            ),
            incident=MediumSpec.air(),
            exit=MediumSpec(constant_n=1.52),
        ),
        spectrum=SpectralGrid(500.0, 700.0, 41),
        illumination=IlluminationSpec((0.0,), ("s",)),
    )


def _absorbing_film_task() -> SimulationTask:
    return SimulationTask(
        stack=StackSpec(
            layers=(
                LayerSpec(None, 95.0, constant_n=2.1, optimizable=True),
                LayerSpec(None, 60.0, constant_n=1.7, constant_k=0.15, optimizable=True),
            ),
            incident=MediumSpec.air(),
            exit=MediumSpec(constant_n=1.52),
        ),
        spectrum=SpectralGrid(500.0, 700.0, 31),
        illumination=IlluminationSpec((30.0,), ("s",)),
    )


def _chirped_stack_task() -> SimulationTask:
    return SimulationTask(
        stack=StackSpec(
            layers=tuple(
                LayerSpec(None, thickness, constant_n=n, constant_k=k, optimizable=True)
                for thickness, n, k in (
                    (95.0, 2.4, 0.0),
                    (150.0, 1.46, 0.1),
                    (120.0, 2.4, 0.0),
                )
            ),
            incident=MediumSpec.air(),
            exit=MediumSpec(constant_n=1.52),
        ),
        spectrum=SpectralGrid(500.0, 800.0, 51),
        illumination=IlluminationSpec((15.0,), ("p",)),
    )


@pytest.mark.parametrize(
    "task,objective",
    [
        (_quarter_wave_dbr(), "mean_T:560..640"),
        (_quarter_wave_dbr(), "mean_R:500..700"),
        (_absorbing_film_task(), "mean_A"),
        (_absorbing_film_task(), "mean_T:520..680"),
        (_chirped_stack_task(), "mean_R:600..760"),
        (_chirped_stack_task(), "mean_A"),
    ],
    ids=[
        "dbr-mean_T-band",
        "dbr-mean_R-full",
        "film-mean_A",
        "film-mean_T-band",
        "chirped-mean_R-band",
        "chirped-mean_A",
    ],
)
def test_gradient_matches_central_difference(task: SimulationTask, objective: str) -> None:
    result = compute_gradient(task, objective)
    assert result.method == "autodiff"
    assert result.backend == "torch"
    assert result.finite_difference_max_relative_deviation is not None
    assert result.finite_difference_max_relative_deviation < 1e-4
    for item in result.layer_gradients:
        if item["relative_deviation"] is not None:
            assert item["relative_deviation"] < 1e-4


def test_quarter_wave_dbr_sensitivity_ranking_is_physical() -> None:
    """Physical sanity inside the stopband (mean_T ~ 0): the standing-wave
    field decays into the stack, so the deepest layer carries by far the
    smallest sensitivity, the high-index layers (phase thickness ∝ n) top the
    ranking, and the front half outweighs the back half.  Loose margins —
    this guards against a scrambled or constant ranking, not exact values."""

    task = _quarter_wave_dbr(periods=4)
    result = compute_sensitivity(task, objective="mean_T:580..620")
    assert result.metric_value < 0.2  # stopband regime: transmission is small

    sensitivities = result.layer_sensitivities
    by_index = {item["layer_index"]: abs(item["sensitivity_per_scale_nm"]) for item in sensitivities}
    assert min(by_index, key=by_index.get) == 7  # deepest layer is weakest

    top_two = [item["layer_index"] for item in result.top_influential_layers[:2]]
    assert set(top_two) <= {0, 2, 4, 6}  # high-index layers dominate the ranking

    front = sum(
        abs(item["sensitivity_per_scale_nm"]) for item in sensitivities[:4]
    )
    back = sum(
        abs(item["sensitivity_per_scale_nm"]) for item in sensitivities[4:]
    )
    assert front > back
    assert result.finite_difference_max_relative_deviation < 1e-4


def test_batch_simulate_matches_individual_simulate() -> None:
    tasks = [
        _quarter_wave_dbr(),
        _absorbing_film_task(),
    ]
    batch = batch_simulate(tasks, registry=MaterialRegistry())
    assert batch.backend == "torch"
    assert batch.count == len(tasks)
    workbench = TMMWorkbench(MaterialRegistry())
    for entry, task in zip(batch.entries, tasks):
        assert entry["physics_accepted"] is True
        assert entry["proposal_matches_certified"] is True
        assert entry["certificate_id"]
        channel = workbench.simulate(task).channel(
            task.illumination.angles_deg[0],
            task.illumination.polarizations[0],
        )
        for name in ("R", "T", "A"):
            assert entry["certified_means"][name] == pytest.approx(
                float(np.mean(np.asarray(channel[name], dtype=np.float64)))
            )


def test_batch_of_thickness_candidates_stays_consistent() -> None:
    base = _quarter_wave_dbr()
    candidates = []
    for shift in (0.0, 2.5, -2.5):
        layers = list(base.stack.layers)
        layers[0] = LayerSpec(
            None, layers[0].thickness_nm + shift, constant_n=2.4, optimizable=True
        )
        candidates.append(
            SimulationTask(
                stack=StackSpec(
                    layers=tuple(layers),
                    incident=base.stack.incident,
                    exit=base.stack.exit,
                ),
                spectrum=base.spectrum,
                illumination=base.illumination,
            )
        )
    batch = batch_simulate(candidates, registry=MaterialRegistry())
    assert [entry["index"] for entry in batch.entries] == [0, 1, 2]
    for entry in batch.entries:
        assert entry["proposal_matches_certified"] is True


def test_non_optimizable_task_is_a_typed_failure() -> None:
    task = _quarter_wave_dbr(optimizable=False)
    with pytest.raises(GradientRequestError) as excinfo:
        compute_gradient(task, "mean_R")
    assert excinfo.value.code == "no_optimizable_layers"


def test_missing_torch_backend_is_explicit_not_silently_downgraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tmm_engine.backends.registry as registry

    monkeypatch.setattr(
        registry, "_discovered", {"numpy": lambda: registry}, raising=True
    )
    with pytest.raises(GradientRequestError) as excinfo:
        compute_gradient(_quarter_wave_dbr(), "mean_R")
    assert excinfo.value.code == "backend_unavailable"


def test_variable_selection_and_objective_validation() -> None:
    task = _quarter_wave_dbr()
    result = compute_gradient(task, "mean_R", [0])
    assert result.variables == [0]
    assert len(result.layer_gradients) == 1

    with pytest.raises(GradientRequestError) as excinfo:
        compute_gradient(task, "mean_R", [99])
    assert excinfo.value.code == "unknown_variable"

    with pytest.raises(GradientRequestError) as excinfo:
        compute_gradient(task, "mean_X")
    assert excinfo.value.code == "objective_unsupported"

    with pytest.raises(GradientRequestError) as excinfo:
        compute_gradient(task, "mean_R:700..500")
    assert excinfo.value.code == "objective_unsupported"


def test_custom_objective_is_python_only() -> None:
    task = _quarter_wave_dbr()

    def objective(observables) -> "torch.Tensor":
        return torch.mean(observables["R"] ** 2)

    result = compute_gradient(task, "custom", custom_objective=objective)
    assert result.custom_objective_python_only is True
    assert result.custom_objective_python_only is (
        result.to_dict()["custom_objective_python_only"]
    )
    assert any("proposal evidence" in note for note in result.notes)

    with pytest.raises(GradientRequestError):
        compute_gradient(task, "custom")
    with pytest.raises(GradientRequestError):
        parse_objective("custom")


def test_mean_a_carries_the_closure_independence_hint() -> None:
    result = compute_gradient(_absorbing_film_task(), "mean_A")
    assert result.observable == "A"
    assert any("closure" in note for note in result.notes)


def test_cli_gradient_and_sensitivity_end_to_end(tmp_path) -> None:
    from tmm_engine.cli import main

    task = _quarter_wave_dbr()
    payload = {
        "mode": "simulate",
        "simulation": {
            "stack": {
                "layers": [
                    {
                        "material": None,
                        "thickness_nm": layer.thickness_nm,
                        "constant_n": layer.constant_n,
                        "optimizable": True,
                    }
                    for layer in task.stack.layers
                ],
                "incident": {"material": None, "constant_n": 1.0},
                "exit": {"material": None, "constant_n": 1.52},
            },
            "spectrum": {"start_nm": 500.0, "stop_nm": 700.0, "points": 41},
            "illumination": {"angles_deg": [0.0], "polarizations": ["s"]},
            "solver": "smatrix",
            "requested_outputs": ["R", "T", "A"],
        },
    }
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(payload), encoding="utf-8")

    assert main(["gradient", str(task_path), "--objective", "mean_R:580..620", "--json"]) == 0
    assert main(["sensitivity", str(task_path), "--objective", "mean_R", "--json"]) == 0

    no_optimizable = json.loads(json.dumps(payload))
    for layer in no_optimizable["simulation"]["stack"]["layers"]:
        layer["optimizable"] = False
    blocked = tmp_path / "blocked.json"
    blocked.write_text(json.dumps(no_optimizable), encoding="utf-8")
    assert main(["gradient", str(blocked), "--json"]) == 2
