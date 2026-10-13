"""Host-frozen public reference protocols for restricted new observation laws.

Proposers supply expressions and a protocol ID, never expected answers or their
own admission verdict. Protocols contain static source-bound data, no callable,
network, credential or hidden-world service. Admission is tentative execution;
fresh conditions are still required for scoped scientific support.
"""
from dataclasses import dataclass, asdict, replace
import math
import numpy as np
from .contracts import canonical_hash
from .operator_registry import OperatorRegistry
from .operator_sandbox import evaluate_expression, validate_expression
from .model_search import append_registered_operator
from .compute_ledger import computational_event, compute_scope


@dataclass(frozen=True)
class OperatorReferenceCase:
    x: tuple[float, ...]
    wavelength_nm: tuple[float, ...]
    expected: tuple[float, ...]
    absolute_tolerance: float = 1e-9

    def __post_init__(self):
        for name in ('x','wavelength_nm','expected'):object.__setattr__(self,name,tuple(float(v) for v in getattr(self,name)))
        if not self.x or not len(self.x)==len(self.wavelength_nm)==len(self.expected) or len(self.x)>4096:
            raise ValueError('aligned bounded reference arrays required')
        if not np.isfinite([self.x,self.wavelength_nm,self.expected]).all() or np.any(np.diff(self.wavelength_nm)<=0):
            raise ValueError('finite ordered reference coordinates required')
        if not math.isfinite(self.absolute_tolerance) or self.absolute_tolerance<0:raise ValueError('finite nonnegative tolerance required')


@dataclass(frozen=True)
class OperatorReferenceProtocol:
    protocol_id: str
    source_refs: tuple[str, ...]
    source_digest: str
    wavelength_domain: tuple[float,float]
    input_domain: tuple[float,float]
    reference_cases: tuple[OperatorReferenceCase, ...]
    limit_cases: tuple[OperatorReferenceCase, ...]
    gradient_cases: tuple[OperatorReferenceCase, ...]
    refinement_cases: tuple[OperatorReferenceCase, ...]
    gradient_step: float = 1e-5
    semantic_scope: str = 'pointwise dimensionless observation law; no new constitutive or geometry solver'

    def __post_init__(self):
        for key in ('source_refs','wavelength_domain','input_domain','reference_cases','limit_cases','gradient_cases','refinement_cases'):
            object.__setattr__(self,key,tuple(getattr(self,key)))
        if not self.protocol_id or not self.source_refs or any(not s for s in self.source_refs):raise ValueError('named public reference provenance required')
        if len(self.source_digest)!=64 or any(c not in '0123456789abcdef' for c in self.source_digest):raise ValueError('source content digest required')
        if not 0<self.wavelength_domain[0]<self.wavelength_domain[1] or not self.input_domain[0]<self.input_domain[1]:raise ValueError('finite nonempty operator domains required')
        if not np.isfinite([*self.wavelength_domain,*self.input_domain,self.gradient_step]).all() or self.gradient_step<=0:raise ValueError('finite domain and positive gradient step required')
        for key in ('reference_cases','limit_cases','gradient_cases','refinement_cases'):
            cases=getattr(self,key)
            if not cases or any(not isinstance(c,OperatorReferenceCase) for c in cases):raise ValueError('reference, limit, gradient and refinement checks required')
            for c in cases:
                if min(c.x)<self.input_domain[0] or max(c.x)>self.input_domain[1] or min(c.wavelength_nm)<self.wavelength_domain[0] or max(c.wavelength_nm)>self.wavelength_domain[1]:raise ValueError('reference outside frozen domain')
                if key=='gradient_cases' and (min(c.x)-self.gradient_step<self.input_domain[0] or max(c.x)+self.gradient_step>self.input_domain[1]):raise ValueError('gradient probe exceeds frozen input domain')

    @property
    def protocol_hash(self):return canonical_hash(asdict(self))


class OperatorAdmissionService:
    def __init__(self, protocols, *, registry=None):
        self.protocols=tuple(protocols)
        if len({p.protocol_id for p in self.protocols})!=len(self.protocols):raise ValueError('duplicate operator validation protocol')
        self.registry=registry or OperatorRegistry()
        self.protocol_id='source-bound-operator-admission:'+canonical_hash([p.protocol_hash for p in self.protocols])

    def public_protocols(self):
        return tuple({'protocol_id':p.protocol_id,'protocol_hash':p.protocol_hash,'source_refs':p.source_refs,
                      'domain_nm':p.wavelength_domain,'input_domain':p.input_domain,'scope':p.semantic_scope} for p in self.protocols)

    def admit(self, request, parent):
        if set(request)!={'expression','reference_protocol_id','expected_difference','task_impact'}:
            raise ValueError('operator proposal cannot supply tests, expected answers or approval')
        protocol=next((p for p in self.protocols if p.protocol_id==request['reference_protocol_id']),None)
        if protocol is None:raise ValueError('no host-trusted reference protocol for proposed operator')
        expression=request['expression'];validate_expression(expression)
        numerical=[]
        def predict(x,wl):
            with computational_event('model_predictions'):return evaluate_expression(expression,x,wl)
        with compute_scope('operator_admission'):
            for kind in ('reference_cases','limit_cases','refinement_cases'):
                for index,case in enumerate(getattr(protocol,kind)):
                    values=predict(case.x,case.wavelength_nm)
                    error=float(np.max(np.abs(values-np.asarray(case.expected))))
                    if error>case.absolute_tolerance:raise ValueError(f'{kind} independent check failed')
                    numerical.append({'kind':kind,'case':index,'maximum_error':error,'tolerance':case.absolute_tolerance})
            h=protocol.gradient_step
            for index,case in enumerate(protocol.gradient_cases):
                x=np.asarray(case.x);values=(predict(x+h,case.wavelength_nm)-predict(x-h,case.wavelength_nm))/(2*h)
                error=float(np.max(np.abs(values-np.asarray(case.expected))))
                if error>case.absolute_tolerance:raise ValueError('independent gradient check failed')
                numerical.append({'kind':'gradient_cases','case':index,'maximum_error':error,'tolerance':case.absolute_tolerance})
            to_case=lambda c:(c.x,c.wavelength_nm,c.expected,c.absolute_tolerance)
            report=self.registry.register_expression(expression,source_refs=protocol.source_refs+(protocol.source_digest,protocol.protocol_hash),
                domain=protocol.wavelength_domain,reference_cases=tuple(to_case(c) for c in protocol.reference_cases),
                limit_cases=tuple(to_case(c) for c in protocol.limit_cases),input_domain=protocol.input_domain)
            report=replace(report,numerical_tests=tuple(f"{r['kind']}:{r['case']}" for r in numerical))
            self.registry._entries[report.operator_id]=(expression,report)
        proposal=append_registered_operator(parent,self.registry,report.operator_id,source_refs=protocol.source_refs+(protocol.protocol_hash,))
        receipt={'schema_version':'operator-admission/1','protocol_id':protocol.protocol_id,'protocol_hash':protocol.protocol_hash,
            'source_digest':protocol.source_digest,'operator_id':report.operator_id,'numerical_results':numerical,
            'status':'tentative_executable','scientific_support':'requires independently purchased fresh prediction evidence',
            'sandbox':'restricted non-eval AST; no external IO or credentials','semantic_scope':protocol.semantic_scope}
        return replace(proposal,expected_difference=request['expected_difference'],task_impact=request['task_impact']),receipt
