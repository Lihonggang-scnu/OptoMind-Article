from __future__ import annotations

from optomind_optics.harness import FrontierObservation, TMMStopController


def _obs(index: int, score: float, pareto: int = 2, roles: int = 3):
    return FrontierObservation(
        round_index=index,
        physically_valid_candidates=3,
        best_target_score=score,
        pareto_candidate_count=pareto,
        portfolio_role_count=roles,
    )


def test_stops_on_frontier_stability_not_target_threshold() -> None:
    controller = TMMStopController(patience_rounds=3, minimum_score_improvement=0.001)
    controller.observe(_obs(1, 0.431))
    controller.observe(_obs(2, 0.4314))
    controller.observe(_obs(3, 0.4315))
    decision = controller.decide(budget_snapshot={"exhausted": False}, legal_actions=["switch_optimizer"])
    assert decision.stop
    assert decision.reason == "frontier_stable"
    assert decision.return_best_effort


def test_continues_when_frontier_is_improving() -> None:
    controller = TMMStopController(patience_rounds=3, minimum_score_improvement=0.001)
    controller.observe(_obs(1, 0.3, pareto=1))
    controller.observe(_obs(2, 0.4, pareto=2))
    controller.observe(_obs(3, 0.5, pareto=3))
    assert not controller.decide(budget_snapshot={}, legal_actions=["run_optimizer"]).stop


def test_budget_exhaustion_returns_verified_best_effort() -> None:
    controller = TMMStopController()
    controller.observe(_obs(1, 0.2))
    decision = controller.decide(budget_snapshot={"exhausted": True}, legal_actions=["run_optimizer"])
    assert decision.reason == "budget_exhausted"
    assert decision.return_best_effort


def test_no_actions_and_no_candidate_is_honest_failure() -> None:
    controller = TMMStopController()
    decision = controller.decide(budget_snapshot={}, legal_actions=[])
    assert decision.stop
    assert decision.reason == "no_verified_candidate"
    assert not decision.return_best_effort


def test_written_portfolio_stops_even_when_score_is_far_from_one() -> None:
    controller = TMMStopController()
    controller.observe(_obs(1, 0.18))
    decision = controller.decide(
        budget_snapshot={"exhausted": False},
        legal_actions=["run_optimizer"],
        portfolio_written=True,
    )
    assert decision.reason == "portfolio_complete"
    assert decision.return_best_effort


def test_completed_portfolio_is_not_relabelled_as_budget_exhaustion() -> None:
    controller = TMMStopController()
    controller.observe(_obs(1, 0.8))
    decision = controller.decide(
        budget_snapshot={"exhausted": True, "overrun": False},
        legal_actions=[],
        portfolio_written=True,
    )
    assert decision.reason == "portfolio_complete"
    assert decision.diagnostics["consumed_budget_at_completion"] is True
