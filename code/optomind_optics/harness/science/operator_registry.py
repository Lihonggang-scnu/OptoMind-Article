"""Tentative executable registration is distinct from independent scientific support."""
from dataclasses import dataclass
import hashlib
import numpy as np
from .operator_sandbox import evaluate_expression, validate_expression


@dataclass(frozen=True)
class OperatorValidationReport:
    operator_id: str
    source_refs: tuple[str,...]
    semantic_domain: tuple[float,float]
    numerical_tests: tuple[str,...]
    sandbox_tests: tuple[str,...]
    independent_prediction: str
    status: str
    input_domain: tuple[float,float] = (0.,1.)


class OperatorRegistry:
    def __init__(self): self._entries={}

    def register_expression(self,expression,*,source_refs,domain,reference_cases,limit_cases=(),input_domain=(0.,1.)):
        validate_expression(expression)
        if not np.isfinite(input_domain).all() or not input_domain[0]<input_domain[1]:raise ValueError("valid input domain required")
        if not source_refs or not reference_cases or not 0 < domain[0] < domain[1]: raise ValueError("source, domain and independent reference cases are required")
        tests=[]
        for label,cases in (("independent_reference",reference_cases),("limit",limit_cases)):
            for i,(x,wl,expected,tolerance) in enumerate(cases):
                wl=np.asarray(wl,float)
                if np.min(wl)<domain[0] or np.max(wl)>domain[1] or tolerance<0: raise ValueError("validation case outside domain")
                from .compute_ledger import computational_event
                with computational_event("model_predictions"):
                    actual=evaluate_expression(expression,x,wl)
                if not np.allclose(actual,expected,rtol=0,atol=tolerance): raise ValueError(f"{label} validation failed")
                tests.append(f"{label}:{i}")
        identity=hashlib.sha256(expression.encode()).hexdigest()
        report=OperatorValidationReport(identity,tuple(source_refs),tuple(domain),tuple(tests),("AST allowlist", "bounded expression and array size", "no IO or name resolution"),"not_yet_tested_on_fresh_conditions","tentative_executable",tuple(input_domain))
        if identity in self._entries:
            old=self._entries[identity][1]
            if (old.source_refs,old.semantic_domain,old.input_domain)!=(report.source_refs,report.semantic_domain,report.input_domain):
                raise ValueError("conflicting operator admission; use a separate explicit registry contract")
            return old
        self._entries[identity]=(expression,report)
        return report

    def expression(self,operator_id): return self._entries[operator_id][0]

    def report(self,operator_id): return self._entries[operator_id][1]

    def support_on_fresh_prediction(self,operator_id,*,prediction_frozen_before_observation,observation_id,training_observation_ids,passed):
        from dataclasses import replace
        expression,report=self._entries[operator_id]
        if not prediction_frozen_before_observation or observation_id in set(training_observation_ids): raise ValueError("old evidence cannot certify a new operator")
        report=replace(report,independent_prediction=observation_id,status="supported_on_tested_domain" if passed else "not_supported")
        self._entries[operator_id]=(expression,report)
        return report
