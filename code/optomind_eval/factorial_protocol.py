"""Reusable, sealed task-level M x E evaluation contracts; no policy imports."""
from dataclasses import dataclass, asdict
import json
from pathlib import Path
import math
from .protocols import digest

FACTORIAL_ARMS=('M0E0','M1E0','M0E1','M1E1')
FACTOR_FLAGS={'M0E0':(False,False),'M1E0':(True,False),'M0E1':(False,True),'M1E1':(True,True)}

@dataclass(frozen=True)
class BaselineParityManifest:
    resource_budget: float
    observation_permissions: tuple[str,...]
    grammar: tuple[str,...]
    terminal_pool: tuple[float,...]
    shared_information: tuple[str,...]
    baseline_registry: tuple[tuple[str,str,str],...]
    fixed_arms_fit_and_actively_select: bool=True
    channel_access_is_separate_factor: bool=True
    tuning_budget: int=0

    def __post_init__(self):
        if not math.isfinite(self.resource_budget) or self.resource_budget<=0 or not self.observation_permissions:
            raise ValueError('positive common resource budget and observation permissions required')
        if (len(set(self.observation_permissions))!=len(self.observation_permissions) or not self.terminal_pool
            or not all(math.isfinite(x) for x in self.terminal_pool) or len(set(self.terminal_pool))!=len(self.terminal_pool)):
            raise ValueError('unique finite shared pool and channels required')
        if not self.fixed_arms_fit_and_actively_select or not self.channel_access_is_separate_factor or self.tuning_budget<0:
            raise ValueError('fixed arms must still fit/select; channel factor separate; nonnegative tuning budget')
        allowed={'executed_factorial','available_separate_audit','pending_unavailable','evaluator_oracle'}
        names=[]
        for name,status,evidence in self.baseline_registry:
            if status not in allowed or not name or not evidence:raise ValueError('baseline status and evidence required')
            names.append(name)
        if len(set(names))!=len(names):raise ValueError('duplicate baseline registry entry')

@dataclass(frozen=True)
class FactorialProtocol:
    protocol_id: str
    task_ids: tuple[str,...]
    task_strata: tuple[str,...]
    parity: BaselineParityManifest
    source_hashes: tuple[tuple[str,str],...]
    comparisons: tuple[tuple[str,str],...]=(('M1E1','M0E0'),('M1E0','M0E0'),('M0E1','M0E0'))
    failure_loss: float=1.
    minimum_important_difference: float=.001
    alpha: float=.05
    bootstrap_replicates: int=5000
    bootstrap_seed: int=8263000
    tail_fraction: float=.2
    loss_cost_per_resource: float=.0001
    statistical_unit: str='independent_task'
    phase: str='exploratory_audit'
    preregistration_hash: str=''

    def __post_init__(self):
        if not self.protocol_id or len(self.task_ids)<2 or len(self.task_ids)!=len(self.task_strata) or len(set(self.task_ids))!=len(self.task_ids):
            raise ValueError('unique independent task IDs and matched strata required')
        if self.statistical_unit!='independent_task':raise ValueError('wavelengths and rounds are not independent tasks')
        if not 0<self.alpha<1 or not 0<self.tail_fraction<=1 or self.bootstrap_replicates<100:
            raise ValueError('invalid statistical configuration')
        if not all(math.isfinite(x) and x>=0 for x in (self.failure_loss,self.minimum_important_difference,self.loss_cost_per_resource)):
            raise ValueError('finite nonnegative penalties and margin required')
        if any(a not in FACTORIAL_ARMS or b not in FACTORIAL_ARMS or a==b for a,b in self.comparisons):
            raise ValueError('invalid paired comparisons')
        if len(set(self.comparisons))!=len(self.comparisons):raise ValueError('duplicate comparisons')
        if any(len(value)!=64 for _,value in self.source_hashes):raise ValueError('source SHA256 required')
        if self.preregistration_hash and len(self.preregistration_hash)!=64:raise ValueError('preregistration SHA256 required')

    @property
    def seal(self):return digest(asdict(self))

    def freeze(self,path):
        _new(path,{'protocol':asdict(self),'protocol_seal':self.seal,'arms':FACTOR_FLAGS})

@dataclass(frozen=True)
class FactorialResult:
    protocol_seal: str
    task_id: str
    arm: str
    model_expansion_enabled: bool
    program_composition_enabled: bool
    terminal_loss: float|None
    prediction_loss: float|None
    false_acceptance: bool|None
    constraint_violation: bool|None
    resource_cost: float
    declared_loss_cost: float
    status: str
    observation_permissions: tuple[str,...]
    trace_hash: str
    oracle_terminal_loss: float|None=None
    input_tokens: int=0
    output_tokens: int=0
    paid_calls: int=0
    wall_seconds: float|None=None
    memory_bytes: int|None=None


def _new(path,value):
    with Path(path).open('x') as stream:json.dump(value,stream,sort_keys=True,allow_nan=False,indent=2)


def seal_factorial_results(protocol,rows,path):
    expected={(task,arm) for task in protocol.task_ids for arm in FACTORIAL_ARMS}
    if {(r.task_id,r.arm) for r in rows}!=expected or len(rows)!=len(expected):
        raise ValueError('all independent tasks and all four arms including failures required exactly once')
    for row in rows:
        if row.protocol_seal!=protocol.seal or tuple(row.observation_permissions)!=protocol.parity.observation_permissions:
            raise ValueError('protocol or observation parity mismatch')
        if (row.model_expansion_enabled,row.program_composition_enabled)!=FACTOR_FLAGS[row.arm]:
            raise ValueError('factor flags do not match assigned arm')
        if row.status not in ('completed','failed','abstained','budget_exceeded'):raise ValueError('unknown status')
        if len(row.trace_hash)!=64:raise ValueError('trace SHA256 required')
        if any(not math.isfinite(x) or x<0 for x in (row.resource_cost,row.declared_loss_cost)):
            raise ValueError('finite actual costs required, including failed tasks')
        if not math.isclose(row.declared_loss_cost,row.resource_cost*protocol.loss_cost_per_resource,abs_tol=1e-12,rel_tol=0):
            raise ValueError('loss-cost conversion differs from frozen protocol')
        for value in (row.terminal_loss,row.prediction_loss,row.oracle_terminal_loss,row.wall_seconds):
            if value is not None and (not math.isfinite(value) or value<0):raise ValueError('metrics must be finite nonnegative or unavailable')
        if row.status=='completed' and (row.terminal_loss is None or row.resource_cost>protocol.parity.resource_budget+1e-10):
            raise ValueError('completed task must have loss and fit common resource budget')
        if any(not isinstance(x,int) or x<0 for x in (row.input_tokens,row.output_tokens,row.paid_calls)):
            raise ValueError('nonnegative metered token/call counts required')
        if row.paid_calls:raise ValueError('this offline protocol forbids paid calls')
        if row.memory_bytes is not None and (type(row.memory_bytes) is not int or row.memory_bytes<0):
            raise ValueError('memory must be nonnegative measured bytes or unavailable')
        for value in (row.false_acceptance,row.constraint_violation):
            if value is not None and type(value) is not bool:raise ValueError('indicator must be Boolean or unavailable')
    payload={'protocol':asdict(protocol),'protocol_seal':protocol.seal,'factor_flags':FACTOR_FLAGS,'rows':[asdict(r) for r in rows]}
    payload['seal']=digest(payload);_new(path,payload);return payload['seal']


def read_factorial_results(path):
    payload=json.loads(Path(path).read_text());seal=payload.pop('seal')
    if digest(payload)!=seal or digest(payload['protocol'])!=payload['protocol_seal']:
        raise ValueError('sealed factorial results modified')
    return payload,seal
