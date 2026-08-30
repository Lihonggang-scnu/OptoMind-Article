from __future__ import annotations

import pytest

from optomind_optics.harness.dev_fixtures import build_dev_optical_design_task
from tmm_engine.task_io import optimization_task_from_dict, simulation_task_from_dict


@pytest.mark.parametrize("benchmark_id", ["DEV01", "DEV02", "DEV03", "DEV04", "DEV05"])
def test_all_development_fixtures_validate_and_are_tmm_only(benchmark_id: str) -> None:
    task = build_dev_optical_design_task(benchmark_id)
    assert task.benchmark_id == benchmark_id
    for experiment in task.experiments:
        if experiment.mode.value == "simulate":
            parsed = simulation_task_from_dict(experiment.tmm_task)
        else:
            parsed = optimization_task_from_dict(experiment.tmm_task)
        physics = parsed.physics if hasattr(parsed, "physics") else parsed.simulation.physics
        assert physics.geometry_class == "layered_planar"


def test_development_factory_refuses_holdout_access() -> None:
    with pytest.raises(KeyError):
        build_dev_optical_design_task("HOLDOUT06")


def test_all_declared_preferences_are_scoring_only() -> None:
    for benchmark_id in ("DEV01", "DEV02", "DEV03", "DEV04", "DEV05"):
        task = build_dev_optical_design_task(benchmark_id)
        assert all(
            objective.admission_role == "score_only"
            for experiment in task.experiments
            for objective in experiment.objectives
        )
