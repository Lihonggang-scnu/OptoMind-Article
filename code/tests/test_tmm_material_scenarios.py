from __future__ import annotations

import json

from optomind_optics.harness import (
    EngineMode,
    HarnessBudgetPolicy,
    OpticalDesignTask,
    TMMExperimentSpec,
    TMMHarnessConfig,
    TMMHarnessOrchestrator,
    UncertaintyPolicy,
)
from optomind_optics.harness.material_scenarios import (
    enumerate_material_scenarios,
)
from optomind_optics.harness.material_service import MaterialResolutionService
from tmm_engine import (
    LayerSpec,
    MaterialRegistry,
    MediumSpec,
    SimulationTask,
    SpectralGrid,
    StackSpec,
)
from tmm_engine.schemas import dataclass_to_dict


def _ambiguous_alumina_task() -> SimulationTask:
    return SimulationTask(
        stack=StackSpec(
            layers=(
                LayerSpec(
                    material="alumina",
                    provider="rii",
                    thickness_nm=100.0,
                ),
            ),
            incident=MediumSpec(constant_n=1.0),
            exit=MediumSpec(constant_n=1.5),
        ),
        spectrum=SpectralGrid(start_nm=450.0, stop_nm=700.0, points=31),
    )


def test_equal_ranked_material_datasets_become_bounded_explicit_scenarios() -> None:
    task = _ambiguous_alumina_task()
    service = MaterialResolutionService(MaterialRegistry())

    scenarios, uncapped = enumerate_material_scenarios(
        task, service, maximum_scenarios=8
    )

    assert uncapped == 2
    assert len(scenarios) == 2
    assert sum(item.is_primary for item in scenarios) == 1
    assert {item.assignments[0].dataset_id for item in scenarios} == {355, 356}
    assert all(service.resolve(item.task).resolved for item in scenarios)


def test_repeated_material_positions_share_one_dataset_branch() -> None:
    base = _ambiguous_alumina_task()
    task = SimulationTask(
        stack=StackSpec(
            layers=(
                base.stack.layers[0],
                LayerSpec(material="alumina", provider="rii", thickness_nm=120.0),
            ),
            incident=base.stack.incident,
            exit=base.stack.exit,
        ),
        spectrum=base.spectrum,
    )
    service = MaterialResolutionService(MaterialRegistry())

    scenarios, uncapped = enumerate_material_scenarios(
        task, service, maximum_scenarios=8
    )

    assert uncapped == 2
    assert len(scenarios) == 2
    assert all(len(item.assignments) == 2 for item in scenarios)
    assert all(
        len({assignment.dataset_id for assignment in item.assignments}) == 1
        for item in scenarios
    )
    assert all(service.resolve(item.task).resolved for item in scenarios)


def test_material_dataset_uncertainty_is_a_report_not_an_admission_gate(
    tmp_path,
) -> None:
    simulation = _ambiguous_alumina_task()
    task = OpticalDesignTask(
        task_id="material_uncertainty_forward",
        user_request_original="Compare eligible alumina optical datasets.",
        normalized_request_english="Compare eligible alumina optical datasets.",
        experiments=(
            TMMExperimentSpec(
                experiment_id="alumina_forward",
                mode=EngineMode.simulate,
                tmm_task=dataclass_to_dict(simulation),
            ),
        ),
        uncertainty=UncertaintyPolicy(
            material_dataset_policy="evaluate_all_eligible",
            maximum_material_scenarios=4,
            thickness_samples=2,
        ),
        budget=HarnessBudgetPolicy(maximum_forward_evaluations=100),
    )

    result = TMMHarnessOrchestrator(
        tmp_path,
        run_id="material_scenarios",
        config=TMMHarnessConfig(enable_global_optimizer=False),
    ).run(task)

    assert result.status == "completed"
    plan = json.loads(
        (tmp_path / "MATERIAL_SCENARIOS.json").read_text(encoding="utf-8")
    )
    assert plan["experiments"][0]["retained_scenario_count"] == 2
    report = json.loads(
        (
            tmp_path
            / "experiments"
            / "alumina_forward"
            / "candidates"
            / "alumina_forward__baseline"
            / "MATERIAL_DATASET_UNCERTAINTY.json"
        ).read_text(encoding="utf-8")
    )
    assert report["scenario_count"] == 2
    assert report["completed_scenarios"] == 2
    assert report["admission_role"] == "ranking_only"
    assert result.experiment_results[0]["physically_valid_candidate_count"] == 1
