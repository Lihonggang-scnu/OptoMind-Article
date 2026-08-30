"""Deterministic failure-rule table for the Article harness (T-08).

Stage 6 turns VeriTMM outcomes into a typed FeedbackDecision -- zero LLM
involvement. Every failure code maps to a recoverability class plus the
adjustment action the next round should take; unknown codes suspend the
route instead of ever retrying identical parameters. Stagnation detection
flags routes whose task fingerprints stopped changing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Literal


@dataclass
class RouteFeedback:
    """Per-route verdict after applying the deterministic rule table."""

    route_id: str
    failure_code: str | None           # None means the route passed
    message: str
    recoverability: Literal["recoverable", "terminal", "unknown"]


@dataclass
class FeedbackDecision:
    """Round-level decision consumed by the stop controller (T-09)."""

    route_feedbacks: list[RouteFeedback] = field(default_factory=list)
    global_action: Literal[
        "continue", "stop_no_valid_candidate", "stop_budget_exhausted"
    ] = "continue"
    stagnant_route_ids: list[str] = field(default_factory=list)


# FailureCode -> recoverability + adjustment action (ARCHITECTURE v0.7 table
# plus the article-branch codes from the T-08 work order).
FAILURE_RULES: dict[str, dict[str, str]] = {
    "CONVERGENCE_FAILURE": {
        "recoverability": "recoverable",
        "adjustment_action": "refine parameters and rerun; convergence is tunable",
    },
    "ENERGY_CONSERVATION_FAILURE": {
        "recoverability": "recoverable",
        "adjustment_action": "densify wavelength grid points; switch reference solver",
    },
    "SOLVER_DISAGREEMENT": {
        "recoverability": "recoverable",
        "adjustment_action": "trigger high-precision referee (mpmath); record closer_solver",
    },
    "MATERIAL_RANGE_ERROR": {
        "recoverability": "recoverable",
        "adjustment_action": "swap material dataset within range (extrapolation forbidden)",
    },
    "TIGHTEST_MARGIN_BELOW_0_20": {
        "recoverability": "recoverable",
        "adjustment_action": "mark barely_passed=true; require an extra validation round",
    },
    "OBJECTIVE_NOT_MET_PHYSICS_VALID": {
        "recoverability": "recoverable",
        "adjustment_action": "widen layer-count range; raise optimization budget",
    },
    "MATERIAL_NOT_FOUND": {
        "recoverability": "terminal",
        "adjustment_action": "material absent from registry/catalog; abandon this route",
    },
    "CHARTER_DRIFT_ERROR": {
        "recoverability": "terminal",
        "adjustment_action": "compiled task violated the immutable ResearchCharter",
    },
    # Triggered separately by detect_stagnation, never by raw engine output.
    "STAGNANT_ROUTE_WARNING": {
        "recoverability": "unknown",
        "adjustment_action": "suspend route until the strategy genuinely changes",
    },
}


def apply_failure_rules(failure_code: str, route_id: str = "") -> RouteFeedback:
    """Map one engine failure code to its RouteFeedback verdict.

    Unknown codes yield recoverability="unknown" and suspend the route
    (route_suspended) -- they must never be retried with identical params.
    """
    code = str(failure_code or "").strip()
    rule = FAILURE_RULES.get(code)
    if rule is None:
        return RouteFeedback(
            route_id=route_id,
            failure_code=code,
            message=(
                f"unknown failure code {code!r}; route status set to "
                "route_suspended (retry_same_params is forbidden)"
            ),
            recoverability="unknown",
        )
    return RouteFeedback(
        route_id=route_id,
        failure_code=code,
        message=f"{code}: {rule['adjustment_action']}",
        recoverability=rule["recoverability"],  # type: ignore[arg-type]
    )


def detect_stagnation(
    task_sha256_history: list[str],
    consecutive_threshold: int = 2,
) -> bool:
    """True when the last N fingerprints are identical (N=threshold)."""

    if consecutive_threshold < 2:
        consecutive_threshold = 2
    if len(task_sha256_history) < consecutive_threshold:
        return False
    tail = task_sha256_history[-consecutive_threshold:]
    return all(
        isinstance(item, str) and item.strip() for item in tail
    ) and len(set(tail)) == 1


def apply_feedback(
    routes: Iterable[dict],
    *,
    budget_exhausted: bool = False,
    consecutive_threshold: int = 2,
) -> FeedbackDecision:
    """Interpret every route outcome into one round FeedbackDecision.

    Each route mapping may carry: route_id, failure_code (None/absent when
    passed), task_sha256_history, optional message override. Routes whose
    fingerprint history shows stagnation are flagged via
    STAGNANT_ROUTE_WARNING, recorded in stagnant_route_ids, and skipped from
    further rule evaluation.

    global_action precedence (P0-01): budget exhaustion first, then
    all-terminal, otherwise continue.
    """
    feedbacks: list[RouteFeedback] = []
    stagnant_route_ids: list[str] = []
    for route in routes:
        route_map = dict(route or {})
        route_id = str(route_map.get("route_id") or "")
        history = list(route_map.get("task_sha256_history") or [])
        if detect_stagnation(history, consecutive_threshold):
            stagnant_route_ids.append(route_id)
            feedbacks.append(
                RouteFeedback(
                    route_id=route_id,
                    failure_code="STAGNANT_ROUTE_WARNING",
                    message=(
                        "STAGNANT_ROUTE_WARNING: task sha256 identical across "
                        f"the last {consecutive_threshold} rounds; route "
                        "suspended pending a genuine strategy change"
                    ),
                    recoverability="unknown",
                )
            )
            continue
        failure_code = route_map.get("failure_code")
        if failure_code:
            feedback = apply_failure_rules(str(failure_code), route_id=route_id)
            override = route_map.get("message")
            if override:
                feedback.message = str(override)
            feedbacks.append(feedback)
        else:
            feedbacks.append(
                RouteFeedback(
                    route_id=route_id,
                    failure_code=None,
                    message=str(route_map.get("message") or "route completed without physics failures"),
                    recoverability="recoverable",
                )
            )
    if budget_exhausted:
        global_action = "stop_budget_exhausted"
    elif feedbacks and all(
        item.recoverability == "terminal" for item in feedbacks
    ):
        global_action = "stop_no_valid_candidate"
    else:
        global_action = "continue"
    return FeedbackDecision(
        route_feedbacks=feedbacks,
        global_action=global_action,  # type: ignore[arg-type]
        stagnant_route_ids=stagnant_route_ids,
    )
