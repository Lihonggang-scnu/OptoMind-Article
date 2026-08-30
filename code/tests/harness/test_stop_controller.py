"""T-09 tests: StopDecision v0.8 gates, priorities, deterministic scoring."""

from __future__ import annotations

import pytest

from optomind_optics.harness.feedback_rule_table import (
    FeedbackDecision,
    RouteFeedback,
)
from optomind_optics.harness.stop_controller import (
    InvalidStopDecisionError,
    StopDecision,
    compute_objective_score,
    make_stop_decision,
    validate_stop_decision,
)

CHARTER = {
    "wavelength_range_nm": [450.0, 800.0],
    "angle_range_deg": [0.0, 30.0],
    "polarization": "unpolarized",
    "objectives": [
        {"name": "reflectivity", "weight": 1.0},
        {"name": "absorption", "weight": 1.0},
    ],
    "material_whitelist": ["SiO2", "TiO2"],
    "layer_count_bounds": {"min": 1, "max": 8},
}


def _feedback(action="continue", recoverabilities=("recoverable",)):
    return FeedbackDecision(
        route_feedbacks=[
            RouteFeedback(
                route_id=f"r{i + 1}",
                failure_code=None if rec == "recoverable" else "MATERIAL_NOT_FOUND" if rec == "terminal" else "WEIRD",
                message="synthetic",
                recoverability=rec,  # type: ignore[arg-type]
            )
            for i, rec in enumerate(recoverabilities)
        ],
        global_action=action,  # type: ignore[arg-type]
        stagnant_route_ids=[],
    )


def _candidate(margin=0.42, per=(0.5,), cost=3.0):
    return {
        "route_id": "route_A",
        "accepted": True,
        "tightest_margin": margin,
        "per_objective_margins": list(per),
        "cost": cost,
    }


def _budget():
    from config.qwen_config import CostTracker

    return CostTracker().get_budget_snapshot()


def test_stop_completed():
    decision = make_stop_decision(
        _feedback(),
        round_k=2,
        n_max_rounds=4,
        certified_candidates=[_candidate(0.42), _candidate(0.61)],
        charter=CHARTER,
        budget=_budget(),
        mandatory_validation_complete=True,
    )
    assert decision.action == "stop"
    assert decision.reason == "stop_completed"
    assert decision.mandatory_validation_pending is False
    scores = [c["objective_score"] for c in decision.best_candidates]
    assert scores == sorted(scores, reverse=True)


def test_stop_round_limit():
    decision = make_stop_decision(
        _feedback(),
        round_k=4,
        n_max_rounds=4,
        certified_candidates=[],
        charter=CHARTER,
        budget=_budget(),
    )
    assert decision.action == "stop"
    assert decision.reason == "stop_round_limit"


def test_stop_budget_exhausted():
    decision = make_stop_decision(
        _feedback(action="stop_budget_exhausted"),
        round_k=1,
        n_max_rounds=4,
        certified_candidates=[],
        charter=CHARTER,
        budget=_budget(),
    )
    assert decision.action == "stop"
    assert decision.reason == "stop_budget_exhausted"


def test_stop_no_valid_candidate():
    decision = make_stop_decision(
        _feedback(action="stop_no_valid_candidate", recoverabilities=("terminal",)),
        round_k=2,
        n_max_rounds=4,
        certified_candidates=[],
        charter=CHARTER,
        budget=_budget(),
    )
    assert decision.reason == "stop_no_valid_candidate"


def test_continue_recoverable():
    decision = make_stop_decision(
        _feedback(),
        round_k=1,
        n_max_rounds=4,
        certified_candidates=[],
        charter=CHARTER,
        budget=_budget(),
    )
    assert decision.action == "continue"
    assert decision.reason == "recoverable_failure"


def test_validate_illegal_continue_with_stop_reason():
    decision = StopDecision(action="continue", reason="stop_completed")
    with pytest.raises(InvalidStopDecisionError):
        validate_stop_decision(decision)


def test_validate_illegal_stop_no_reason():
    decision = StopDecision(action="stop", reason="recoverable_failure")
    with pytest.raises(InvalidStopDecisionError):
        validate_stop_decision(decision)


def test_stop_completed_blocked_when_barely_passed():
    # barely-passed candidate keeps pending=True; without external completion
    # confirmation the controller must NOT emit stop_completed.
    decision = make_stop_decision(
        _feedback(),
        round_k=1,
        n_max_rounds=4,
        certified_candidates=[_candidate(0.10)],
        charter=CHARTER,
        budget=_budget(),
        mandatory_validation_complete=False,
    )
    assert decision.mandatory_validation_pending is True
    assert not (decision.action == "stop" and decision.reason == "stop_completed")

    # constructing the forbidden combo directly fails validation
    illegal = StopDecision(
        action="stop",
        reason="stop_completed",
        mandatory_validation_pending=True,
    )
    with pytest.raises(InvalidStopDecisionError, match="mandatory_validation_complete"):
        validate_stop_decision(illegal)


def test_objective_score_single():
    single_charter = dict(CHARTER, objectives=[{"name": "reflectivity"}])
    score = compute_objective_score(_candidate(0.37), single_charter)
    assert score == 0.37


def test_objective_score_multi():
    weighted = dict(
        CHARTER,
        objectives=[
            {"name": "a", "weight": 2.0},
            {"name": "b", "weight": 1.0},
        ],
    )
    score = compute_objective_score(
        _candidate(0.9, per=(0.6, 0.3)), weighted
    )
    assert abs(score - 0.5) < 1e-12


def test_best_candidates_sort():
    decision = make_stop_decision(
        _feedback(),
        round_k=1,
        n_max_rounds=4,
        certified_candidates=[
            _candidate(0.20, per=(0.20,), cost=1.0),
            _candidate(0.90, per=(0.90,), cost=9.0),
            _candidate(0.50, per=(0.50,), cost=5.0),
        ],
        charter=CHARTER,
        budget=_budget(),
        mandatory_validation_complete=True,
    )
    scores = [round(c["objective_score"], 6) for c in decision.best_candidates]
    assert scores == sorted(scores, reverse=True)
    assert decision.best_candidates[0]["tightest_margin"] == 0.90
