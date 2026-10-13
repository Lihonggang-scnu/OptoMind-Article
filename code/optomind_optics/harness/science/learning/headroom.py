from dataclasses import dataclass

@dataclass(frozen=True)
class HeadroomReport:
    comparison: str
    gain: float
    forward_verified: bool
    context_bottleneck: bool
    evidence: tuple[str,...]

def diagnose_headroom(*,baseline,reference,comparison,forward_verified=False,context_bottleneck=False,evidence=()):
    if comparison not in ("legal_history_reaccess","simulator_forward_counterfactual","informed_high_budget_reference"): raise ValueError("unknown headroom reference")
    return HeadroomReport(comparison,reference-baseline,forward_verified,context_bottleneck,tuple(evidence))
