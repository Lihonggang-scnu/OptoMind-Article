"""R-02 tests: stagnation criterion uses epsilon and max - first semantics.

Before R-02, ``make_stop_decision`` required a strictly non-increasing sequence
to trigger ``stop_no_progress``, meaning numerical noise (0.9000001) would
prevent stopping forever. The fix uses ``max(window) - window[0] <= epsilon``.
"""

from __future__ import annotations

from optomind_optics.harness.stop_controller import make_stop_decision


class _FakeFeedback:
    global_action = "continue"
    route_feedbacks = []


def _decision(
    scores: list[float],
    patience: int = 3,
    epsilon: float = 1e-4,
    round_k: int = 5,
):
    return make_stop_decision(
        feedback=_FakeFeedback(),
        round_k=round_k,
        n_max_rounds=10,
        certified_candidates=[{"candidate_id": "c1"}],
        charter=None,
        budget=None,
        patience_rounds=patience,
        objective_score_history=scores,
        minimum_score_improvement=epsilon,
    )


def test_noise_level_gain_counts_as_stagnation():
    """Noise-level improvement must not prevent stopping."""
    scores = [0.90, 0.9000001, 0.9000002]
    decision = _decision(scores, patience=3, epsilon=1e-4)
    assert decision.action == "stop"
    assert decision.reason == "stop_no_progress"


def test_meaningful_gain_continues():
    scores = [0.90, 0.91, 0.92]
    decision = _decision(scores, patience=3, epsilon=1e-4)
    assert decision.action == "continue"


def test_regression_within_window_does_not_mask_stagnation():
    """[0.90, 0.80, 0.905]: max-first=0.005 < 0.01, stops despite the dip."""
    scores = [0.90, 0.80, 0.905]
    decision = _decision(scores, patience=3, epsilon=1e-2)
    assert decision.action == "stop"
    assert decision.reason == "stop_no_progress"


def test_stagnation_requires_full_window():
    """Two scores in a patience=3 window must continue."""
    scores = [0.90, 0.9000001]
    decision = _decision(scores, patience=3, epsilon=1e-4)
    assert decision.action == "continue"


def test_empty_history_continues():
    decision = _decision([], patience=3, epsilon=1e-4)
    assert decision.action == "continue"


def test_epsilon_is_absolute_not_relative():
    """Epsilon is an absolute threshold, not a percentage."""
    scores_high = [0.90, 0.90005, 0.90010]
    decision_high = _decision(scores_high, patience=3, epsilon=1e-4)
    assert decision_high.action == "stop"

    scores_low = [0.0001, 0.00015, 0.00020]
    decision_low = _decision(scores_low, patience=3, epsilon=1e-4)
    # gain = 0.0002 - 0.0001 = 0.0001 = epsilon, stops
    assert decision_low.action == "stop"


def test_gain_exactly_epsilon_stops():
    scores = [0.90, 0.90005, 0.90010]
    decision = _decision(scores, patience=3, epsilon=1e-4)
    assert decision.action == "stop"


def test_window_shorter_than_patience_continues():
    scores = [0.90, 0.90]
    decision = _decision(scores, patience=3, epsilon=1e-4)
    assert decision.action == "continue"
