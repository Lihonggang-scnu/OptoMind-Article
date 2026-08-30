"""T-08 tests: deterministic failure rules, stagnation, global actions."""

from __future__ import annotations

import pytest

from optomind_optics.harness.feedback_rule_table import (
    apply_failure_rules,
    apply_feedback,
    detect_stagnation,
)


def test_known_failure_code_recoverable():
    feedback = apply_failure_rules("CONVERGENCE_FAILURE", route_id="r1")
    assert feedback.recoverability == "recoverable"
    assert feedback.failure_code == "CONVERGENCE_FAILURE"
    assert feedback.route_id == "r1"


def test_known_failure_code_terminal():
    assert apply_failure_rules("MATERIAL_NOT_FOUND").recoverability == "terminal"
    assert apply_failure_rules("CHARTER_DRIFT_ERROR").recoverability == "terminal"


def test_unknown_failure_code_route_suspended():
    feedback = apply_failure_rules("TOTALLY_NOVEL_CODE", route_id="rx")
    assert feedback.recoverability == "unknown"
    assert "route_suspended" in feedback.message
    assert "retry_same_params" in feedback.message


def test_detect_stagnation_true():
    assert detect_stagnation(["sha1", "sha1"]) is True
    assert detect_stagnation(["sha0", "sha1", "sha1"], consecutive_threshold=2) is True
    assert detect_stagnation(["s"] * 3, consecutive_threshold=3) is True


def test_detect_stagnation_false():
    assert detect_stagnation(["sha1", "sha2"]) is False
    assert detect_stagnation(["sha1"]) is False
    assert detect_stagnation([]) is False
    assert detect_stagnation(["sha1", "sha2", "sha1"]) is False


def test_all_terminal_global_stop():
    decision = apply_feedback(
        [
            {"route_id": "r1", "failure_code": "MATERIAL_NOT_FOUND"},
            {"route_id": "r2", "failure_code": "CHARTER_DRIFT_ERROR"},
        ]
    )
    assert decision.global_action == "stop_no_valid_candidate"
    assert [item.recoverability for item in decision.route_feedbacks] == [
        "terminal",
        "terminal",
    ]


def test_budget_exhausted_global_stop():
    decision = apply_feedback(
        [{"route_id": "r1", "failure_code": "CONVERGENCE_FAILURE"}],
        budget_exhausted=True,
    )
    assert decision.global_action == "stop_budget_exhausted"


def test_stagnant_route_flagged_and_skipped():
    decision = apply_feedback(
        [
            {
                "route_id": "stuck",
                "task_sha256_history": ["same", "same"],
                "failure_code": "CONVERGENCE_FAILURE",
            },
            {"route_id": "fresh", "failure_code": None},
        ]
    )
    assert decision.stagnant_route_ids == ["stuck"]
    stuck = next(f for f in decision.route_feedbacks if f.route_id == "stuck")
    assert stuck.failure_code == "STAGNANT_ROUTE_WARNING"
    assert stuck.recoverability == "unknown"
    # one recoverable passing route remains -> continue despite the stagnant one
    assert decision.global_action == "continue"
