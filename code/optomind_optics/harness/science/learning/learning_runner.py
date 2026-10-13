"""Fresh forward evaluation only: no replay scoring and no production writes."""
from dataclasses import dataclass, field
import json
import math
from typing import Literal
from pydantic import Field, model_validator
from ..contracts import StrictModel, canonical_hash
from ..method_assets import PolicyPayload

class ActionInput(StrictModel):
    task_id: str
    initial_snapshot: str
    library_snapshot: str
    observations: tuple[float,...] = ()
    available_budget: float = Field(ge=0, allow_inf_nan=False)
    final_validation_reserve: float = Field(ge=0, allow_inf_nan=False)
    @model_validator(mode="after")
    def bounded(self):
        if self.final_validation_reserve>self.available_budget or len(self.observations)>4096 or not all(math.isfinite(x) for x in self.observations): raise ValueError("invalid bounded action input")
        return self

class LearningTask(StrictModel):
    task_id: str
    provenance_group: str
    split: Literal["T","V","P","A"]
    action_input: ActionInput
    @model_validator(mode="after")
    def identity(self):
        if self.task_id != self.action_input.task_id: raise ValueError("task identity mismatch")
        return self

class PolicyVersion(StrictModel):
    experiment_policy: str
    version_hash: str = ""
    @model_validator(mode="after")
    def validate(self):
        PolicyPayload.model_validate_json(self.experiment_policy)
        digest=canonical_hash(json.loads(self.experiment_policy))
        if self.version_hash and self.version_hash != digest: raise ValueError("policy hash mismatch")
        object.__setattr__(self,"version_hash",digest)
        return self

class ReflectionFeedback(StrictModel):
    action_input: ActionInput
    policy_hash: str
    score: float = Field(allow_inf_nan=False)
    status: Literal["completed","failed"]

@dataclass
class CostMeter:
    limit: float
    entries: list = field(default_factory=list)
    @property
    def total(self): return sum(v for _,v in self.entries)
    def charge(self, kind, amount):
        if not math.isfinite(amount) or amount<0: raise ValueError("invalid cost")
        self.entries.append((kind,float(amount)))
        if self.total>self.limit: raise ValueError("outer learning budget exceeded")

@dataclass(frozen=True)
class ForwardResult:
    output: dict
    score: float
    objective_scores: dict
    trajectory: dict
    valid: bool = True

class LearningRunManifest(StrictModel):
    candidate_hashes: tuple[str,...]
    initial_snapshot: str
    library_snapshot: str
    splits: dict[str,tuple[str,...]]
    budget: float
    actual_cost: float
    adapter_version: str
    costs: tuple[tuple[str,float],...]

class LearningRunner:
    def __init__(self,tasks,forward,*,budget,attempt_cost=1.,cache_build_cost=0.):
        if not math.isfinite(budget) or budget<0 or not math.isfinite(attempt_cost) or attempt_cost<=0: raise ValueError("finite budget and positive attempt cost required")
        self.tasks={t.task_id:t for t in tasks}
        if len(self.tasks)!=len(tasks): raise ValueError("duplicate task")
        groups={}
        for t in tasks:
            if t.provenance_group in groups and groups[t.provenance_group]!=t.split: raise ValueError("split provenance overlap")
            groups[t.provenance_group]=t.split
        self.forward=forward; self.cost=CostMeter(budget); self.attempt_cost=attempt_cost
        self.cost.charge("cache_build",cache_build_cost)
        self.candidates=[]; self.frozen=None; self.promotion_passed=False
        self.results=[]
    def evaluate(self,task_ids,policy,*,stage="search"):
        allowed={"search":{"T","V"},"promotion":{"P"},"audit":{"A"}}[stage]
        if stage!="search" and self.frozen!=policy.version_hash: raise ValueError("freeze exact policy before held-out evaluation")
        if stage=="audit" and not self.promotion_passed: raise ValueError("promotion must precede audit")
        tasks=[self.tasks[x] for x in task_ids]
        if any(t.split not in allowed for t in tasks): raise ValueError("split not available in this stage")
        results=[]
        for task in tasks:
            # Even a failing callback is an attempted, charged forward execution.
            self.cost.charge("forward_attempt",self.attempt_cost)
            view=ActionInput.model_validate_json(task.action_input.model_dump_json())
            try:
                result=self.forward(view,PolicyPayload.model_validate_json(policy.experiment_policy),self.cost)
                if not isinstance(result,ForwardResult) or not result.valid or not math.isfinite(result.score) or not all(math.isfinite(v) for v in result.objective_scores.values()):
                    raise ValueError("invalid or contaminated forward result")
                if self.cost.total>self.cost.limit: raise ValueError("outer learning budget exceeded")
            except Exception as exc:
                result=ForwardResult({"status":"failed","error_type":type(exc).__name__},-1e30,{"valid":0.},{"status":"failed"},False)
            results.append(result)
            self.results.append((task.task_id,policy.version_hash,result))
        self.candidates.append(policy.version_hash)
        return results
    def freeze(self,policy):
        if policy.version_hash not in self.candidates: raise ValueError("cannot freeze an unevaluated policy")
        if not any(h==policy.version_hash and r.valid and self.tasks[t].split=="V" for t,h,r in self.results): raise ValueError("freeze requires valid validation forward evaluation")
        self.frozen=policy.version_hash
        self.promotion_passed=False
    def evaluate_promotion(self,task_ids,policy,*,minimum_score):
        results=self.evaluate(task_ids,policy,stage="promotion")
        self.promotion_passed=bool(results) and all(r.valid and r.score>=minimum_score for r in results)
        return self.promotion_passed,results
    def manifest(self):
        views=[t.action_input for t in self.tasks.values()]
        return LearningRunManifest(candidate_hashes=tuple(self.candidates),initial_snapshot=canonical_hash([v.initial_snapshot for v in views]),library_snapshot=canonical_hash([v.library_snapshot for v in views]),splits={s:tuple(t.task_id for t in self.tasks.values() if t.split==s) for s in "TVPA"},budget=self.cost.limit,actual_cost=self.cost.total,adapter_version="gepa-462e437a-thin.v1",costs=tuple(self.cost.entries))

def optimize_offline(runner,seed,propose,*,train_ids,validation_ids,rounds=1,proposal_cost=0.):
    best=seed
    base=runner.evaluate(validation_ids,best)
    best_score=sum(r.score for r in base)
    for _ in range(rounds):
        feedback=runner.evaluate(train_ids,best)
        runner.cost.charge("proposal",proposal_cost)
        # Proposal gets only training feedback, never P/A data or the evaluator.
        reflection=tuple(ReflectionFeedback(action_input=runner.tasks[task_id].action_input,policy_hash=best.version_hash,score=result.score,status="completed" if result.valid else "failed") for task_id,result in zip(train_ids,feedback))
        candidate=PolicyVersion(experiment_policy=propose(best.experiment_policy,reflection))
        scored=runner.evaluate(validation_ids,candidate)
        score=sum(r.score for r in scored)
        if all(r.valid for r in scored) and score>best_score: best,best_score=candidate,score
    runner.freeze(best)
    return best,runner.manifest()
