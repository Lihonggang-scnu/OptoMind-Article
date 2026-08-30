"""Deterministic TMM failure diagnosis and legal recovery actions."""

from __future__ import annotations

from typing import Any, Dict, List, Literal

from pydantic import BaseModel, ConfigDict, Field

from tmm_engine.capabilities import FailureCode, FailureRecord

from .contracts import ActionType


class FailureDiagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: Literal[
        "invalid_input",
        "outside_tmm_domain",
        "material_data",
        "numerical_convergence",
        "physics_violation",
        "solver_disagreement",
        "runtime_environment",
        "search_progress",
        "objective_shortfall",
    ]
    recoverable_with_tmm: bool
    allowed_actions: List[ActionType] = Field(default_factory=list)
    explanation: str
    context: Dict[str, Any] = Field(default_factory=dict)


_MAP: Dict[FailureCode, tuple[str, bool, List[ActionType], str]] = {
    FailureCode.INVALID_TASK: ("invalid_input", False, [ActionType.stop], "Correct the task contract before running TMM."),
    FailureCode.UNSUPPORTED_GEOMETRY: ("outside_tmm_domain", False, [ActionType.stop], "The task is outside the configured TMM-only domain."),
    FailureCode.UNSUPPORTED_MATERIAL_MODEL: ("outside_tmm_domain", False, [ActionType.stop], "The material model is outside isotropic TMM."),
    FailureCode.UNSUPPORTED_EXCITATION: ("outside_tmm_domain", False, [ActionType.stop], "The excitation is outside plane-wave TMM."),
    FailureCode.TIME_DOMAIN_REQUIRED: ("outside_tmm_domain", False, [ActionType.stop], "A time-domain request cannot be represented by this TMM Harness."),
    FailureCode.UNSUPPORTED_OUTPUT_COMBINATION: ("invalid_input", True, [ActionType.run_solver, ActionType.stop], "Split incompatible requested outputs into legal TMM experiments."),
    FailureCode.MATERIAL_NOT_FOUND: ("material_data", True, [ActionType.switch_material_dataset, ActionType.stop], "Resolve another explicit material dataset or report the missing material."),
    FailureCode.MATERIAL_AMBIGUITY: ("material_data", True, [ActionType.switch_material_dataset, ActionType.fork_experiment], "Evaluate eligible material datasets as separate provenance branches."),
    FailureCode.MATERIAL_RANGE_ERROR: ("material_data", True, [ActionType.switch_material_dataset, ActionType.stop], "Use a dataset that covers the requested range; do not silently extrapolate."),
    FailureCode.NUMERICAL_NONFINITE: ("physics_violation", True, [ActionType.run_reference_solver, ActionType.stop], "Reject non-finite observables and cross-check the same TMM task."),
    FailureCode.PASSIVITY_VIOLATION: ("physics_violation", True, [ActionType.run_reference_solver, ActionType.stop], "Inspect material signs and solver conventions before accepting any candidate."),
    FailureCode.ENERGY_CONSERVATION_FAILURE: ("physics_violation", True, [ActionType.run_reference_solver, ActionType.run_convergence_audit, ActionType.stop], "Repeat convergence and independent TMM checks."),
    FailureCode.SPECTRAL_CONVERGENCE_FAILURE: ("numerical_convergence", True, [ActionType.run_convergence_audit, ActionType.stop], "Refine the spectral grid within the operational budget."),
    FailureCode.SOLVER_DISAGREEMENT: ("solver_disagreement", True, [ActionType.run_reference_solver, ActionType.stop], "Do not accept the candidate until TMM implementations agree or the discrepancy is explained."),
    FailureCode.OPTIONAL_DEPENDENCY_MISSING: ("runtime_environment", False, [ActionType.stop], "Install the declared dependency in a controlled environment before retrying."),
    FailureCode.OPTIMIZER_FAILURE: ("search_progress", True, [ActionType.switch_optimizer, ActionType.stop], "Preserve verified candidates and switch to another registered TMM optimizer when budget remains."),
    FailureCode.BUDGET_EXHAUSTED: ("search_progress", False, [ActionType.stop], "Stop cleanly and return the best already verified candidates without claiming the search is complete."),
}


class TMMFailureDiagnoser:
    def diagnose(self, failure: FailureRecord) -> FailureDiagnosis:
        category, recoverable, actions, explanation = _MAP.get(
            failure.code,
            ("invalid_input", False, [ActionType.stop], "Unclassified TMM failure; stop without claiming success."),
        )
        return FailureDiagnosis(
            category=category,
            recoverable_with_tmm=recoverable,
            allowed_actions=actions,
            explanation=explanation,
            context={"failure": failure.to_dict()},
        )

    def diagnose_search_progress(
        self,
        *,
        optimizer_stagnated: bool,
        objective_shortfall: float | None,
        budget_available: bool,
        alternative_optimizer_available: bool,
    ) -> FailureDiagnosis:
        if optimizer_stagnated:
            actions = []
            if budget_available and alternative_optimizer_available:
                actions.extend([ActionType.switch_optimizer, ActionType.fork_experiment])
            actions.append(ActionType.stop)
            return FailureDiagnosis(
                category="search_progress",
                recoverable_with_tmm=budget_available and alternative_optimizer_available,
                allowed_actions=actions,
                explanation="The search stagnated; preserve verified candidates and switch strategy only if budget remains.",
            )
        actions = []
        if budget_available and alternative_optimizer_available:
            actions.extend([ActionType.switch_optimizer, ActionType.fork_experiment])
        actions.append(ActionType.stop)
        return FailureDiagnosis(
            category="objective_shortfall",
            recoverable_with_tmm=budget_available and alternative_optimizer_available,
            allowed_actions=actions,
            explanation="Objective shortfall affects ranking only; retain the physically valid best effort.",
            context={"objective_shortfall": objective_shortfall},
        )


__all__ = ["FailureDiagnosis", "TMMFailureDiagnoser"]
