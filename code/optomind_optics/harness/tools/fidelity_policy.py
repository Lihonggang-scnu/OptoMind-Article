"""Evidence-bound fidelity choices. Changing physical assumptions is not refinement."""
from __future__ import annotations
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class FidelityDecision:
    domain: str
    precision: str
    error_bound: float | None
    cost: float
    escalation_reason: str | None
    direct_recheck_required: bool


def choose_fidelity(*, domain, precision, comparison_domain, observed_error,
                    objective_gap, constraint_margin, cost=0.):
    """A measured validation envelope is mandatory; two grids alone aren't a bound."""
    if cost < 0 or not math.isfinite(cost):
        raise ValueError("nonnegative finite cost required")
    if domain != comparison_domain:
        return FidelityDecision(domain, precision, None, cost,
                                "physical assumptions differ; no fidelity ordering", True)
    if observed_error is None:
        return FidelityDecision(domain, precision, None, cost,
                                "no validated error envelope", True)
    if not all(math.isfinite(v) for v in (observed_error, objective_gap, constraint_margin)) or observed_error < 0:
        raise ValueError("finite errors/gaps required")
    reason = None
    if 2*observed_error >= abs(objective_gap):
        reason = "error can reverse candidate ranking"
    elif observed_error >= abs(constraint_margin):
        reason = "error can cross constraint boundary"
    return FidelityDecision(domain, precision, observed_error, cost, reason, reason is not None)


@dataclass(frozen=True)
class OverlapValidation:
    solver_a: str
    solver_b: str
    physical_conditions: dict
    observable: str
    absolute_difference: float
    validation_scope: str = "numerical overlap only; no physical experiment evidence"


SURROGATE_POLICY = {"enabled": False, "reason": "no held-out error, OOD, or independent recheck evidence"}
