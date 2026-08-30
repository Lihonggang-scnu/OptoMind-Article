"""Solver adapters keep the experiment kernel independent of solver APIs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

from tmm_engine import MaterialRegistry, SimulationTask, TMMWorkbench
from tmm_engine.acceptance import AcceptanceSettings, CertifiedSimulation, certify_simulation
from tmm_engine.capabilities import CapabilityAssessment, assess_tmm_capability


@dataclass(frozen=True)
class SolverDescriptor:
    solver_id: str
    solver_family: str
    fidelity_rank: int
    supports_gradients: bool
    typical_cost: str
    geometry_classes: tuple[str, ...] = ()
    material_classes: tuple[str, ...] = ()
    excitation_classes: tuple[str, ...] = ()
    supported_outputs: tuple[str, ...] = ()
    supports_batch: bool = False
    execution_modes: tuple[str, ...] = ("local_native",)
    convergence_dimensions: tuple[str, ...] = ()
    independent_of: tuple[str, ...] = ()


class TMMAdapter:
    descriptor = SolverDescriptor(
        solver_id="optomind_tmm",
        solver_family="scalar_tmm",
        fidelity_rank=10,
        supports_gradients=True,
        typical_cost="very_low",
        geometry_classes=("layered_planar",),
        material_classes=("isotropic",),
        excitation_classes=("plane_wave",),
        supported_outputs=("R", "T", "A", "amplitudes", "ellipsometry", "layer_absorption", "system_emissivity", "phase_dispersion"),
        supports_batch=True,
        convergence_dimensions=("wavelength",),
        independent_of=("byrnes_tmm",),
    )

    def __init__(self, registry: MaterialRegistry | None = None) -> None:
        self.material_registry = registry or MaterialRegistry()
        self.workbench = TMMWorkbench(self.material_registry)

    def assess(self, task: SimulationTask) -> CapabilityAssessment:
        return assess_tmm_capability(task)

    def run(
        self, task: SimulationTask, settings: AcceptanceSettings | None = None
    ) -> CertifiedSimulation:
        return certify_simulation(self.workbench, task, settings)


class SolverRegistry:
    def __init__(self, adapters: Iterable[Any] = ()) -> None:
        self._adapters: Dict[str, Any] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: Any) -> None:
        solver_id = str(adapter.descriptor.solver_id)
        if solver_id in self._adapters:
            raise ValueError(f"Duplicate solver_id: {solver_id}")
        self._adapters[solver_id] = adapter

    def get(self, solver_id: str) -> Any:
        try:
            return self._adapters[str(solver_id)]
        except KeyError as exc:
            raise KeyError(f"Solver is not registered: {solver_id}") from exc

    def select(self, task: SimulationTask) -> Optional[Any]:
        eligible = []
        for adapter in self._adapters.values():
            assessment = adapter.assess(task)
            if assessment.supported:
                eligible.append((int(adapter.descriptor.fidelity_rank), adapter))
        if not eligible:
            return None
        return sorted(eligible, key=lambda item: item[0])[0][1]

    def descriptors(self) -> list[Dict[str, Any]]:
        return [adapter.descriptor.__dict__.copy() for adapter in self._adapters.values()]


__all__ = ["SolverDescriptor", "SolverRegistry", "TMMAdapter"]
