from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import math
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from .contracts import StrictModel, canonical_hash
from .learning.learning_runner import CostMeter


class MethodGenerationPolicy(StrictModel):
    grid_rule: Literal['uniform', 'mean_pair_greedy', 'worst_pair_greedy'] = 'uniform'
    points: int = Field(default=4, ge=2, le=64)
    candidate_limit: int = Field(default=128, ge=2, le=4096)
    nuisance_rule: Literal['closest_pair', 'mean_prediction'] = 'closest_pair'
    version: Literal['bounded-method-generation.v1'] = 'bounded-method-generation.v1'

    @property
    def version_hash(self):
        return canonical_hash(self.model_dump(mode='json'))


@dataclass(frozen=True)
class GeneratedMethodPlan:
    asset_ref: object
    policy_hash: str
    library_snapshot: str
    wavelengths_nm: tuple[float, ...]
    candidate_indices: tuple[int, ...]
    predicted_separation: float
    evaluated_subsets: int
    resource_cost: float


def _pair_distances(predictions, indices, noise_std, nuisance_rule):
    distances=[]
    for left,right in combinations(predictions,2):
        a=np.asarray(left,float)[:,indices]
        b=np.asarray(right,float)[:,indices]
        if nuisance_rule=='mean_prediction':
            a=a.mean(axis=0,keepdims=True);b=b.mean(axis=0,keepdims=True)
        squared=((a[:,None,:]-b[None,:,:])/noise_std)**2
        distances.append(float(np.min(squared.sum(axis=2))))
    return np.asarray(distances)


def generate_method_plan(provider, policy, *, wavelengths_nm, prediction_sets, noise_std, available_budget):
    policy=MethodGenerationPolicy.model_validate(policy.model_dump(mode='json'))
    grid=np.asarray(wavelengths_nm,float)
    if (grid.ndim!=1 or not policy.points<=len(grid)<=policy.candidate_limit or not np.isfinite(grid).all()
            or np.any(grid<=0) or np.any(np.diff(grid)<=0) or not math.isfinite(noise_std) or noise_std<=0
            or not math.isfinite(available_budget) or available_budget<0):
        raise ValueError('invalid bounded public method planning domain')
    arrays=[np.asarray(values,float) for values in prediction_sets]
    if len(arrays)<2 or any(a.ndim!=2 or not 1<=len(a)<=128 or a.shape[1]!=len(grid) or not np.isfinite(a).all() for a in arrays):
        raise ValueError('at least two bounded candidate prediction sets are required')
    prepared=provider.prepare('program')
    assets=[]
    from .method_assets import ResearchMethodAsset
    for method in prepared:
        asset=provider.registry.repository.load(method.asset_ref)
        if not isinstance(asset,ResearchMethodAsset) or asset.cost>available_budget:
            continue
        slot=next((s for s in asset.parameter_slots if s.name=='wavelengths_nm'),None)
        if slot is not None and slot.max_points>=policy.points and slot.lower<=grid[0] and grid[-1]<=slot.upper:
            assets.append(asset)
    if not assets:
        raise ValueError('no qualified applicable method within fixed resource budget')
    asset=min(assets,key=lambda item:(item.cost,item.content_hash))
    evaluated=0
    if policy.grid_rule=='uniform':
        chosen=list(np.rint(np.linspace(0,len(grid)-1,policy.points)).astype(int))
    else:
        chosen=[]
        for _ in range(policy.points):
            ranked=[]
            for index in range(len(grid)):
                if index in chosen:
                    continue
                distances=_pair_distances(arrays,chosen+[index],noise_std,policy.nuisance_rule)
                value=float(distances.min() if policy.grid_rule=='worst_pair_greedy' else distances.mean())
                ranked.append((value,-index,index));evaluated+=1
            chosen.append(max(ranked)[2])
    chosen=sorted(chosen)
    score=float(_pair_distances(arrays,chosen,noise_std,'closest_pair').min())
    return GeneratedMethodPlan(asset.ref(),policy.version_hash,provider.library_snapshot,
        tuple(map(float,grid[chosen])),tuple(map(int,chosen)),score,evaluated,asset.cost)


class MethodPolicyTask(StrictModel):
    task_id: str = Field(min_length=1)
    provenance_group: str = Field(min_length=1)
    split: Literal['T','V','P','A']
    budget: float = Field(gt=0,allow_inf_nan=False)


class MethodPolicyRun(StrictModel):
    task_id: str
    run_id: str
    policy_hash: str
    library_snapshot: str
    executed_program_hash: str
    score: float = Field(allow_inf_nan=False)
    actual_cost: float = Field(ge=0,allow_inf_nan=False)
    valid: bool
    output: dict


class BoundedMethodPolicySearch:
    def __init__(self, tasks, forward, *, library_snapshot, total_budget, max_forward_calls):
        self.tasks={task.task_id:task for task in tasks}
        if len(self.tasks)!=len(tasks) or not isinstance(max_forward_calls,int) or max_forward_calls<=0:
            raise ValueError('distinct tasks and finite forward-call bound required')
        groups={}
        for task in tasks:
            if task.provenance_group in groups and groups[task.provenance_group]!=task.split:
                raise ValueError('train/selection/holdout provenance overlap')
            groups[task.provenance_group]=task.split
        if not math.isfinite(total_budget) or total_budget<=0:
            raise ValueError('finite positive policy search budget required')
        self.forward=forward;self.library_snapshot=library_snapshot
        self.meter=CostMeter(total_budget);self.max_forward_calls=max_forward_calls
        self.rows=[];self.run_ids=set();self.frozen=None;self.training_rank=();self.validation_rank=()

    def _evaluate(self, policy, ids, split):
        if split in {'P','A'} and (self.frozen is None or policy.version_hash not in self.frozen):
            raise ValueError('holdout requires frozen baseline and selected policy')
        if any(self.tasks[name].split!=split for name in ids):
            raise ValueError('task unavailable in requested split')
        result=[]
        for name in ids:
            task=self.tasks[name]
            if len(self.rows)>=self.max_forward_calls or self.meter.total+task.budget>self.meter.limit:
                raise ValueError('bounded policy search budget exhausted before execution')
            row=MethodPolicyRun.model_validate(self.forward(task,policy).model_dump(mode='json'))
            self.meter.charge('actual_fresh_method_execution',row.actual_cost)
            if (row.task_id!=task.task_id or row.policy_hash!=policy.version_hash
                    or row.library_snapshot!=self.library_snapshot or row.run_id in self.run_ids
                    or row.actual_cost>task.budget or not row.executed_program_hash):
                raise ValueError('forward result changed task/library/policy/budget or reused execution')
            self.run_ids.add(row.run_id);self.rows.append((split,row));result.append(row)
        return tuple(result)

    def select(self, baseline, candidates, *, train_ids, validation_ids, shortlist=2):
        if self.frozen is not None or not train_ids or not validation_ids:
            raise ValueError('one search with separate nonempty training and selection tasks required')
        policies={p.version_hash:p for p in (baseline,*candidates)}
        if not 1<=shortlist<=len(policies)<=32:
            raise ValueError('invalid bounded candidate population')
        ranked=[]
        for key,policy in policies.items():
            rows=self._evaluate(policy,train_ids,'T')
            score=sum(row.score for row in rows)/len(rows) if all(row.valid for row in rows) else -1e30
            ranked.append((score,key))
        self.training_rank=tuple(sorted(ranked,reverse=True))
        chosen=[key for _,key in self.training_rank[:shortlist]]
        if baseline.version_hash not in chosen:
            chosen.append(baseline.version_hash)
        ranked=[]
        for key in chosen:
            rows=self._evaluate(policies[key],validation_ids,'V')
            score=sum(row.score for row in rows)/len(rows) if all(row.valid for row in rows) else -1e30
            ranked.append((score,key))
        self.validation_rank=tuple(sorted(ranked,reverse=True))
        best=policies[self.validation_rank[0][1]]
        baseline_score=next(score for score,key in ranked if key==baseline.version_hash)
        if self.validation_rank[0][0]<=baseline_score:
            best=baseline
        self.frozen=(baseline.version_hash,best.version_hash)
        return best

    def compare_holdout(self, baseline, evolved, *, task_ids, split='P'):
        if split not in {'P','A'} or self.frozen!=(baseline.version_hash,evolved.version_hash):
            raise ValueError('holdout cannot choose or modify the policy')
        return {'fixed':self._evaluate(baseline,task_ids,split),
                'evolved':self._evaluate(evolved,task_ids,split)}

    def manifest(self):
        return {'version':'bounded-method-policy-search.v1','library_snapshot':self.library_snapshot,
                'splits':{s:[t.task_id for t in self.tasks.values() if t.split==s] for s in 'TVPA'},
                'training_rank':self.training_rank,'validation_rank':self.validation_rank,
                'frozen_policy_hashes':self.frozen,'actual_cost':self.meter.total,'budget':self.meter.limit,
                'forward_calls':len(self.rows),'max_forward_calls':self.max_forward_calls,
                'runs':[{'split':s,**row.model_dump(mode='json')} for s,row in self.rows],
                'scope':'finite data-only grid generation and selection; no arbitrary code or recursive self-improvement claim'}
