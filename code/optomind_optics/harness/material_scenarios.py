"""Deterministic material-dataset scenario planning for the TMM Harness.

The planner never treats an optical-constant table as ground truth.  When a
task explicitly requests ``evaluate_all_eligible``, equal-ranked registry
datasets become bounded uncertainty scenarios.  The first deterministic
scenario is used for search, while every retained scenario can be verified
later with the same design.  This is not a performance admission gate.
"""

from __future__ import annotations

import itertools
from dataclasses import replace
from typing import Any, Iterable, Sequence

from pydantic import BaseModel, ConfigDict

from tmm_engine import OptimizationTask, SimulationTask

from .material_service import MaterialDatasetChoice, MaterialResolutionService


class MaterialAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    position: str
    material: str
    provider: str | None = None
    dataset_id: Any = None


class MaterialScenario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    scenario_id: str
    assignments: tuple[MaterialAssignment, ...] = ()
    is_primary: bool = False
    task: SimulationTask | OptimizationTask

    def audit_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "assignments": [item.model_dump(mode="json") for item in self.assignments],
            "is_primary": self.is_primary,
        }


def _pin_simulation(
    simulation: SimulationTask,
    assignments: Iterable[MaterialAssignment],
) -> SimulationTask:
    incident = simulation.stack.incident
    exit_medium = simulation.stack.exit
    layers = list(simulation.stack.layers)
    for assignment in assignments:
        provider = assignment.provider
        dataset_id = None if assignment.dataset_id is None else str(assignment.dataset_id)
        if assignment.position == "incident":
            incident = replace(incident, provider=provider, dataset_id=dataset_id)
        elif assignment.position == "exit":
            exit_medium = replace(exit_medium, provider=provider, dataset_id=dataset_id)
        elif assignment.position.startswith("layer[") and assignment.position.endswith("]"):
            index = int(assignment.position[6:-1])
            layers[index] = replace(
                layers[index], provider=provider, dataset_id=dataset_id
            )
        else:
            raise ValueError(f"unknown material position: {assignment.position!r}")
    return replace(
        simulation,
        stack=replace(
            simulation.stack,
            incident=incident,
            exit=exit_medium,
            layers=tuple(layers),
        ),
    )


def apply_material_assignments(
    task: SimulationTask | OptimizationTask,
    assignments: Sequence[MaterialAssignment],
) -> SimulationTask | OptimizationTask:
    if isinstance(task, OptimizationTask):
        return replace(
            task,
            simulation=_pin_simulation(task.simulation, assignments),
        )
    if isinstance(task, SimulationTask):
        return _pin_simulation(task, assignments)
    raise TypeError("material scenarios require SimulationTask or OptimizationTask")


def enumerate_material_scenarios(
    task: SimulationTask | OptimizationTask,
    service: MaterialResolutionService,
    *,
    maximum_scenarios: int,
) -> tuple[tuple[MaterialScenario, ...], int]:
    """Return bounded deterministic combinations and the uncapped count."""

    if int(maximum_scenarios) < 1:
        raise ValueError("maximum_scenarios must be positive")
    choices = service.list_eligible_dataset_choices(task)
    grouped: dict[str, dict[str, list[MaterialDatasetChoice]]] = {}
    for choice in choices:
        grouped.setdefault(choice.material, {}).setdefault(choice.position, []).append(
            choice
        )
    ambiguous_groups: list[
        tuple[tuple[str, ...], tuple[MaterialDatasetChoice, ...]]
    ] = []
    for material in sorted(grouped):
        by_position = grouped[material]
        positions = tuple(sorted(by_position))
        candidate_maps: list[dict[tuple[str, str], MaterialDatasetChoice]] = []
        for position in positions:
            candidate_maps.append(
                {
                    (choice.provider or "", str(choice.dataset_id)): choice
                    for choice in by_position[position]
                }
            )
        common_keys = set(candidate_maps[0])
        for candidate_map in candidate_maps[1:]:
            common_keys.intersection_update(candidate_map)
        options = tuple(
            candidate_maps[0][key]
            for key in sorted(common_keys, key=lambda item: (item[0], item[1]))
        )
        if len(options) > 1:
            # One physical material dataset is applied consistently to every
            # occurrence of that material.  Branching independently per layer
            # would create an exponential set of internally inconsistent
            # deposition scenarios (for example 2^10 choices for one film).
            ambiguous_groups.append((positions, options))

    if not ambiguous_groups:
        return (
            MaterialScenario(
                scenario_id="materials_primary",
                assignments=(),
                is_primary=True,
                task=task,
            ),
        ), 1

    total = 1
    for _, options in ambiguous_groups:
        total *= len(options)
    scenarios: list[MaterialScenario] = []
    for index, combination in enumerate(
        itertools.islice(
            itertools.product(*(options for _, options in ambiguous_groups)),
            int(maximum_scenarios),
        ),
        1,
    ):
        assignments = tuple(
            MaterialAssignment(
                position=position,
                material=choice.material,
                provider=choice.provider,
                dataset_id=choice.dataset_id,
            )
            for (positions, _), choice in zip(ambiguous_groups, combination)
            for position in positions
        )
        scenarios.append(
            MaterialScenario(
                scenario_id=f"materials_{index:03d}",
                assignments=assignments,
                is_primary=index == 1,
                task=apply_material_assignments(task, assignments),
            )
        )
    return tuple(scenarios), int(total)


__all__ = [
    "MaterialAssignment",
    "MaterialScenario",
    "apply_material_assignments",
    "enumerate_material_scenarios",
]
