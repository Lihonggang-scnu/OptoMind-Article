from __future__ import annotations

from optomind_optics.harness import ActionType, TMMFailureDiagnoser
from tmm_engine.capabilities import FailureCode, FailureRecord


def test_outside_domain_stops_instead_of_routing_another_solver() -> None:
    diagnosis = TMMFailureDiagnoser().diagnose(
        FailureRecord(FailureCode.UNSUPPORTED_GEOMETRY, "periodic grating", False)
    )
    assert diagnosis.category == "outside_tmm_domain"
    assert diagnosis.allowed_actions == [ActionType.stop]
    assert not diagnosis.recoverable_with_tmm


def test_material_range_error_never_suggests_silent_extrapolation() -> None:
    diagnosis = TMMFailureDiagnoser().diagnose(
        FailureRecord(FailureCode.MATERIAL_RANGE_ERROR, "out of range", True)
    )
    assert ActionType.switch_material_dataset in diagnosis.allowed_actions
    assert "extrapolat" in diagnosis.explanation


def test_objective_shortfall_is_not_a_physics_failure() -> None:
    diagnosis = TMMFailureDiagnoser().diagnose_search_progress(
        optimizer_stagnated=False,
        objective_shortfall=0.07,
        budget_available=False,
        alternative_optimizer_available=True,
    )
    assert diagnosis.category == "objective_shortfall"
    assert diagnosis.allowed_actions == [ActionType.stop]
    assert "retain" in diagnosis.explanation


def test_stagnation_can_switch_optimizer_with_remaining_budget() -> None:
    diagnosis = TMMFailureDiagnoser().diagnose_search_progress(
        optimizer_stagnated=True,
        objective_shortfall=0.2,
        budget_available=True,
        alternative_optimizer_available=True,
    )
    assert diagnosis.allowed_actions[:2] == [ActionType.switch_optimizer, ActionType.fork_experiment]
    assert diagnosis.recoverable_with_tmm


def test_optimizer_failure_prefers_registered_optimizer_recovery() -> None:
    diagnosis = TMMFailureDiagnoser().diagnose(
        FailureRecord(FailureCode.OPTIMIZER_FAILURE, "optimizer crashed", True)
    )
    assert diagnosis.category == "search_progress"
    assert diagnosis.allowed_actions == [ActionType.switch_optimizer, ActionType.stop]


def test_budget_exhaustion_returns_best_effort_stop_only() -> None:
    diagnosis = TMMFailureDiagnoser().diagnose(
        FailureRecord(FailureCode.BUDGET_EXHAUSTED, "budget used", False)
    )
    assert diagnosis.allowed_actions == [ActionType.stop]
