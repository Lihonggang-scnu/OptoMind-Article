"""Unknown premises are failures, not inferred permission."""
from dataclasses import dataclass

@dataclass(frozen=True)
class ApplicabilityCheck:
    allowed: bool
    reasons: tuple[str, ...]

def check_applicability(asset, facts):
    reasons=[]
    if asset.state in ("quarantined", "retired", "proposed"): reasons.append("inactive_state")
    for key, expected in asset.applicability_predicates.items():
        if key not in facts: reasons.append("unknown:"+key)
        elif facts[key] != expected: reasons.append("mismatch:"+key)
    return ApplicabilityCheck(not reasons, tuple(reasons))
