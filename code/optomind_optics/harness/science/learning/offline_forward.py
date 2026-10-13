"""A real C04 controller forward runner over an evaluator-owned scenario.

The callable is a trusted evaluation fixture, not a security sandbox. Policy
payloads are data-only; hidden outcomes never enter their ActionInput.
"""
from dataclasses import asdict
from ..controller import decide_next,execute_round
from .learning_runner import ForwardResult

class ScientificForwardRunner:
    def __init__(self,problem_factory,actions_factory,probabilities,acquire_factory,score):
        self.problem_factory=problem_factory;self.actions_factory=actions_factory
        self.probabilities=probabilities;self.acquire_factory=acquire_factory;self.score=score
    def __call__(self,view,policy,meter):
        problem=self.problem_factory(view);actions=tuple(self.actions_factory(view))
        if policy.action_ids:
            if set(policy.action_ids)-{a.action_id for a in actions}: raise ValueError("unknown action")
            actions=tuple(a for a in actions if a.action_id in policy.action_ids)
        acquire=self.acquire_factory(view)
        def charged(action):
            meter.charge("measurement",action.resource_cost)
            return acquire(action)
        if policy.continuation_mode == "adaptive":
            from ..rollout import plan_adaptive, execute_adaptive
            decision=plan_adaptive(problem,self.probabilities,actions,available_budget=view.available_budget,final_validation_reserve=view.final_validation_reserve,depth=policy.depth)
            receipt=execute_adaptive(problem,self.probabilities,actions,decision,charged)
            selected=tuple(a for a,_ in receipt.observations)
            cost=receipt.resource_cost
        else:
            decision=decide_next(problem,self.probabilities,actions,available_budget=view.available_budget,final_validation_reserve=view.final_validation_reserve,depth=policy.depth)
            receipt=execute_round(problem,self.probabilities,actions,decision,charged,current_context_hash=decision.context_hash)
            selected=decision.selected_actions
            cost=decision.reserved_resource_cost
        score=float(self.score(view,receipt))
        return ForwardResult({"decision":receipt.selected_design_id,"actions":selected},score,{"scientific_score":score,"measurement_cost":cost},asdict(receipt))
