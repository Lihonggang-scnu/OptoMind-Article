"""Optional GEPA protocol adapter, not a replacement agent framework.

Source reviewed 2026-10-12: gepa-ai/gepa commit
462e437a09be67d2acb59564cc0ea59132f5777c, src/gepa/core/adapter.py.
License at the same commit is MIT. No upstream code vendored, dependency
installed, teacher model selected, or paid model invoked here.
"""
from dataclasses import dataclass
from .learning_runner import PolicyVersion
PINNED_COMMIT="462e437a09be67d2acb59564cc0ea59132f5777c"

@dataclass
class EvaluationBatch:
    outputs: list
    scores: list
    trajectories: list | None = None
    objective_scores: list | None = None
    num_metric_calls: int | None = None

class GEPAAdapter:
    propose_new_texts=None
    def __init__(self,runner): self.runner=runner
    def evaluate(self,batch,candidate,capture_traces=False):
        if set(candidate)!={"experiment_policy"}: raise ValueError("only experiment_policy is evolvable")
        policy=PolicyVersion(**candidate)
        results=self.runner.evaluate(batch,policy)
        return EvaluationBatch([r.output for r in results],[r.score for r in results],
            [{"task_id":task,"candidate_hash":policy.version_hash,"status":"completed" if r.valid else "failed","score":r.score} for task,r in zip(batch,results)] if capture_traces else None,
            [r.objective_scores for r in results],len(results))
    def make_reflective_dataset(self,candidate,eval_batch,components_to_update):
        if components_to_update != ["experiment_policy"]: raise ValueError("immutable scientific kernel")
        policy=PolicyVersion(**candidate)
        rows=[]
        for trace in eval_batch.trajectories or ():
            if trace["candidate_hash"]!=policy.version_hash: raise ValueError("stale candidate trajectory")
            task=self.runner.tasks[trace["task_id"]]
            if task.split!="T": raise ValueError("only training feedback may guide reflection")
            rows.append({"Inputs":task.action_input.model_dump(),"Generated Outputs":policy.experiment_policy,"Feedback":{"status":trace["status"],"score":trace["score"]}})
        return {"experiment_policy":rows}
