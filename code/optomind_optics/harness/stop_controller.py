"""Stopping rules based on budget and frontier progress, never target pass marks."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FrontierObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    round_index: int
    physically_valid_candidates: int
    best_target_score: float
    pareto_candidate_count: int = 0
    portfolio_role_count: int = 0

    @field_validator("best_target_score")
    @classmethod
    def _score(cls, value: float) -> float:
        if not 0.0 <= float(value) <= 1.0:
            raise ValueError("best_target_score must be in [0, 1]")
        return float(value)


class StopDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stop: bool
    reason: Literal[
        "continue_search",
        "frontier_stable",
        "budget_exhausted",
        "strategies_exhausted",
        "portfolio_complete",
        "no_verified_candidate",
    ]
    return_best_effort: bool = False
    diagnostics: Dict[str, Any] = Field(default_factory=dict)


# Frozen reference so TMMStopController keeps constructing the legacy model
# after the article-branch StopDecision (v0.8 dataclass below) shadows the
# module-level name for new importers.
_LEGACY_STOP_DECISION = StopDecision

# Single source of truth for the stagnation epsilon (R-02): "no progress"
# means the objective score gained at most this much across the patience
# window. TMMStopController and make_stop_decision both default to it, and
# downstream consumers (e.g. the research orchestrator's reflection reference
# epsilon, R-04-FIX D-6) must import this constant instead of restating a
# literal so the values cannot silently drift apart.
DEFAULT_MINIMUM_SCORE_IMPROVEMENT: float = 1e-4


class TMMStopController:
    def __init__(self, *, patience_rounds: int = 3, minimum_score_improvement: float = DEFAULT_MINIMUM_SCORE_IMPROVEMENT) -> None:
        if int(patience_rounds) < 2:
            raise ValueError("patience_rounds must be at least 2")
        if float(minimum_score_improvement) < 0:
            raise ValueError("minimum_score_improvement must be non-negative")
        self.patience_rounds = int(patience_rounds)
        self.minimum_score_improvement = float(minimum_score_improvement)
        self._history: List[FrontierObservation] = []

    def observe(self, observation: FrontierObservation) -> None:
        if self._history and observation.round_index <= self._history[-1].round_index:
            raise ValueError("round_index must increase monotonically")
        self._history.append(observation)

    def decide(
        self,
        *,
        budget_snapshot: Dict[str, Any],
        legal_actions: List[str],
        portfolio_written: bool = False,
    ) -> StopDecision:
        latest = self._history[-1] if self._history else None
        verified = int(latest.physically_valid_candidates) if latest else 0
        # Exact consumption of a planned resource at the end of a successful
        # run is not a failure.  Once a physics-verified portfolio exists, the
        # terminal reason is completion; any exhausted resource is retained as
        # an audit detail instead of relabelling the result.
        if portfolio_written and verified > 0:
            diagnostics: Dict[str, Any] = {
                "portfolio_role_count": latest.portfolio_role_count,
            }
            if bool(budget_snapshot.get("exhausted")):
                diagnostics["consumed_budget_at_completion"] = True
            return _LEGACY_STOP_DECISION(
                stop=True,
                reason="portfolio_complete",
                return_best_effort=True,
                diagnostics=diagnostics,
            )
        if bool(budget_snapshot.get("exhausted")) or bool(budget_snapshot.get("overrun")):
            return _LEGACY_STOP_DECISION(
                stop=True,
                reason="budget_exhausted",
                return_best_effort=verified > 0,
                diagnostics={"budget": budget_snapshot, "verified_candidates": verified},
            )
        if not legal_actions:
            return _LEGACY_STOP_DECISION(
                stop=True,
                reason="strategies_exhausted" if verified else "no_verified_candidate",
                return_best_effort=verified > 0,
            )
        if len(self._history) >= self.patience_rounds and verified > 0:
            window = self._history[-self.patience_rounds :]
            score_gain = max(item.best_target_score for item in window) - min(
                item.best_target_score for item in window
            )
            pareto_gain = window[-1].pareto_candidate_count - window[0].pareto_candidate_count
            role_gain = window[-1].portfolio_role_count - window[0].portfolio_role_count
            if (
                score_gain <= self.minimum_score_improvement
                and pareto_gain <= 0
                and role_gain <= 0
            ):
                return _LEGACY_STOP_DECISION(
                    stop=True,
                    reason="frontier_stable",
                    return_best_effort=True,
                    diagnostics={
                        "score_gain": score_gain,
                        "pareto_gain": pareto_gain,
                        "role_gain": role_gain,
                        "window_rounds": self.patience_rounds,
                    },
                )
        return _LEGACY_STOP_DECISION(stop=False, reason="continue_search")



# ===========================================================================
# Article branch additions (T-09): StopDecision v0.8
# ===========================================================================

from dataclasses import dataclass, field  # noqa: E402  (append-only block)

from .feedback_rule_table import FeedbackDecision, RouteFeedback  # noqa: E402
from .strategy_planner import _charter_value  # noqa: E402  (intra-package reuse)


class InvalidStopDecisionError(ValueError):
    """Raised for illegal action/reason combinations or gate violations."""


StopReason = Literal[
    "stop_completed",            # goal met + accepted + validation complete
    "stop_round_limit",          # round_k == N_MAX_ROUNDS with recoverable routes
    "stop_budget_exhausted",     # budget exhausted (signalled by T-08 feedback)
    "stop_no_progress",          # no objective-score improvement for patience rounds
    "stop_no_valid_candidate",   # no accepted candidate at all
    "stop_best_effort",          # routes exhausted + legal candidates exist
    "recoverable_failure",       # continue reason
    "objective_improvable",      # continue reason
]

_CONTINUE_REASONS = frozenset({"recoverable_failure", "objective_improvable"})
_STOP_REASONS = frozenset({
    "stop_completed",
    "stop_round_limit",
    "stop_budget_exhausted",
    "stop_no_progress",
    "stop_no_valid_candidate",
    "stop_best_effort",
})


@dataclass
class StopDecision:
    """Round-level stop/continue verdict (v0.8).

    Shadows the legacy pydantic StopDecision above; TMMStopController keeps
    using _LEGACY_STOP_DECISION internally, so legacy behaviour is frozen.
    """

    action: Literal["continue", "stop"]
    reason: str
    best_candidates: list[dict] = field(default_factory=list)
    round_k: int = 0
    mandatory_validation_pending: bool = False


def validate_stop_decision(decision: StopDecision) -> None:
    """Raise InvalidStopDecisionError for illegal combinations (P0-02)."""
    if decision.action not in ("continue", "stop"):
        raise InvalidStopDecisionError(
            f"illegal StopDecision: unknown action {decision.action!r}"
        )
    if decision.reason not in _CONTINUE_REASONS | _STOP_REASONS:
        raise InvalidStopDecisionError(
            f"illegal StopDecision: unknown reason {decision.reason!r}"
        )
    if decision.action == "continue" and decision.reason not in _CONTINUE_REASONS:
        raise InvalidStopDecisionError(
            f"illegal StopDecision: action=continue cannot carry reason "
            f"{decision.reason!r}; allowed: {sorted(_CONTINUE_REASONS)}"
        )
    if decision.action == "stop" and decision.reason not in _STOP_REASONS:
        raise InvalidStopDecisionError(
            f"illegal StopDecision: action=stop cannot carry reason "
            f"{decision.reason!r}; allowed: {sorted(_STOP_REASONS)}"
        )
    if (
        decision.action == "stop"
        and decision.reason == "stop_completed"
        and decision.mandatory_validation_pending
    ):
        raise InvalidStopDecisionError(
            "stop_completed requires mandatory_validation_complete=True"
        )


def compute_objective_score(candidate, charter) -> float:
    """Deterministic candidate score -- zero Qwen involvement.

    Single objective: tightest_margin. Multi objective:
    sum(weight_i * margin_i) / sum(weight_i), weights from
    charter.objectives[i].weight (default 1.0). When charter.use_pareto
    is set, score = 1 / pareto_rank.
    """
    data = candidate if isinstance(candidate, Mapping) else {}
    tightest = float(data.get("tightest_margin", 0.0) or 0.0)
    if bool(_charter_value(charter, "use_pareto")):
        rank_raw = data.get("pareto_rank")
        try:
            rank = int(rank_raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            rank = 0
        if rank >= 1:
            return 1.0 / rank
    objectives = _charter_value(charter, "objectives") or []
    margins = data.get("per_objective_margins")
    if len(objectives) <= 1 or not margins:
        return tightest
    margin_values = (
        list(margins.values()) if isinstance(margins, Mapping) else list(margins)
    )
    weighted_sum = 0.0
    weight_total = 0.0
    for index, objective in enumerate(objectives):
        if index >= len(margin_values):
            break
        raw_weight = (
            objective.get("weight", 1.0)
            if isinstance(objective, Mapping)
            else getattr(objective, "weight", 1.0)
        )
        try:
            weight = float(raw_weight)
            value = float(margin_values[index])
        except (TypeError, ValueError):
            continue
        weighted_sum += weight * value
        weight_total += weight
    if weight_total <= 0:
        return tightest
    return weighted_sum / weight_total


def evaluate_stagnation(
    score_history: List[float] | None,
    *,
    patience_rounds: int = 3,
    minimum_score_improvement: float = DEFAULT_MINIMUM_SCORE_IMPROVEMENT,
) -> tuple[bool, float | None]:
    """Single source of truth for the R-02 stagnation window rule.

    Returns ``(stagnant, observed_gain)``. ``observed_gain`` is
    ``max(window) - window[0]`` over the last ``patience_rounds`` scores, or
    ``None`` when fewer scores exist (a short history is never stagnant).
    ``make_stop_decision`` and the research orchestrator's dual gate both call
    this helper so the criterion cannot drift apart (R-04-FIX D-7, approved).
    """
    history = list(score_history or [])
    window = history[-(max(int(patience_rounds), 1)):]
    if len(window) >= patience_rounds and window:
        score_gain = max(window) - window[0]
        return score_gain <= minimum_score_improvement, score_gain
    return False, None


def make_stop_decision(
    feedback: FeedbackDecision,
    round_k: int,
    n_max_rounds: int,
    certified_candidates: list[dict],
    charter,
    budget,  # RunBudgetSnapshot: metering only; budget policy lives in T-08
    *,
    mandatory_validation_complete: bool = False,
    patience_rounds: int = 3,
    objective_score_history: list[float] | None = None,
    minimum_score_improvement: float = DEFAULT_MINIMUM_SCORE_IMPROVEMENT,
) -> StopDecision:
    """Six-level deterministic priority -> StopDecision (validated on exit)."""
    candidates = [
        dict(item)
        for item in (certified_candidates or [])
        if isinstance(item, Mapping)
    ]
    barely_passed = any(
        float(item.get("tightest_margin", 1.0) or 0.0) < 0.20
        for item in candidates
    )
    pending = barely_passed and not mandatory_validation_complete

    scored = []
    for item in candidates:
        score = compute_objective_score(item, charter)
        item["objective_score"] = score
        try:
            cost = float(item.get("cost", float("inf")))
        except (TypeError, ValueError):
            cost = float("inf")
        margin = float(item.get("tightest_margin", 0.0) or 0.0)
        scored.append((score, margin, cost, item))
    scored.sort(key=lambda entry: (-entry[0], -entry[1], entry[2]))
    ranked_candidates = [entry[3] for entry in scored]

    has_recoverable_route = any(
        isinstance(fb, RouteFeedback) and fb.recoverability == "recoverable"
        for fb in getattr(feedback, "route_feedbacks", [])
    )
    global_action = str(getattr(feedback, "global_action", "continue"))

    # D-7 (R-04-FIX, approved): delegate to the shared helper so the
    # orchestrator's dual gate and this priority chain cannot diverge.
    no_progress, _stagnation_gain = evaluate_stagnation(
        objective_score_history,
        patience_rounds=patience_rounds,
        minimum_score_improvement=minimum_score_improvement,
    )

    def emit(action: str, reason: str) -> StopDecision:
        decision = StopDecision(
            action=action,  # type: ignore[arg-type]
            reason=reason,
            best_candidates=ranked_candidates,
            round_k=int(round_k),
            mandatory_validation_pending=pending,
        )
        validate_stop_decision(decision)
        return decision

    # Priority 1: completion requires external validation confirmation
    # barely-passed candidates keep pending=True until then (gate below).
    if candidates and mandatory_validation_complete:
        return emit("stop", "stop_completed")
    # Priority 2: round limit is a hard stop regardless of route recoverability.
    # Intentionally does NOT require has_recoverable_route: if all routes are
    # stagnant (recoverability="unknown"), has_recoverable_route would be False
    # and the condition would be bypassed, causing the pipeline to emit
    # "continue" past the round limit indefinitely.
    if int(round_k) >= int(n_max_rounds):
        return emit("stop", "stop_round_limit")
    # Priority 3
    if global_action == "stop_budget_exhausted":
        return emit("stop", "stop_budget_exhausted")
    # Priority 4
    if no_progress:
        return emit("stop", "stop_no_progress")
    # Priority 5
    if not candidates and global_action == "stop_no_valid_candidate":
        return emit("stop", "stop_no_valid_candidate")
    # Priority 6: routes exhausted while legal candidates remain.
    if candidates and global_action == "stop_no_valid_candidate":
        return emit("stop", "stop_best_effort")
    fallback_reason = "objective_improvable" if candidates else "recoverable_failure"
    return emit("continue", fallback_reason)


__all__ = [
    "DEFAULT_MINIMUM_SCORE_IMPROVEMENT",
    "FrontierObservation",
    "InvalidStopDecisionError",
    "StopDecision",
    "StopReason",
    "TMMStopController",
    "compute_objective_score",
    "evaluate_stagnation",
    "make_stop_decision",
    "validate_stop_decision",
]
