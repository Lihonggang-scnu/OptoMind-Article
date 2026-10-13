import json
import numpy as np
from optomind_optics.harness.science.contracts import ResearchTask, ModelCandidate, PredictionSpec
from optomind_optics.harness.science.repository import ScienceRepository
from optomind_optics.harness.science.jobs import ExecutionGate, ToolCapability
from optomind_optics.harness.science.terminal_problem import TerminalDecisionProblem
from optomind_optics.harness.science.controller import MeasurementAction
from optomind_optics.harness.science.pipeline_adapter import SciencePipelinePolicy


def _policy(tmp_path, *, unknown=False):
    from test_article_execution import _request, _run_result, _stack, _task, FakeHarness
    request=_request();harness=FakeHarness(_run_result());stack=_stack(tmp_path,harness=harness)
    stack["registry"].register(request.task_hash,_task())
    coordinator=stack["coordinator"]
    coordinator.execution_gate=ExecutionGate(tmp_path/"gate.db",limits=dict(request.requested_budget),capability=ToolCapability(effect_class="compute",domain="TMM"))
    repo=ScienceRepository(tmp_path/"science");coordinator.science_repository=repo
    task=ResearchTask(object_id="task",source_run_id="run-1",question="choose coating")
    candidates=[ModelCandidate(object_id=key,source_run_id="run-1",model_ir={"kind":"finite_fixture","name":key}) for key in ("A","B")]
    repo.commit([task,*candidates],task_ref=task.ref(),evidence_cutoff="prior")
    coordinator.science_task_ref=task.ref()
    problem=TerminalDecisionProblem(("w0","w1"),("A","B"),np.array([[0,1],[1,0]]),{key:"fixture" for key in ("task","objective","model","sample","material","solver","fidelity","loss","split")},"loss")
    action=MeasurementAction("measure",("left","right"),((1.,0.),(0.,1.)),.01,1)
    policy=SciencePipelinePolicy(problem=problem,probabilities=(.5,.5),actions=() if unknown else (action,),action_request_ids={} if unknown else {"measure":request.request_id},design_candidate_refs={c.object_id:c.ref() for c in candidates},coordinator=coordinator,classify_outcome=lambda action,result:"right",classifier_protocol_id="fixture_binary_outcome",classifier_protocol_version="1",available_budget=3,final_validation_reserve=1,unknown_action_ids=("unvalued",) if unknown else ())
    return policy,request,harness


def test_scientific_policy_actual_gateway_freezes_prediction_then_selects(tmp_path):
    policy,request,harness=_policy(tmp_path)
    selected=policy.plan([request],tmp_path)
    assert selected==(request.request_id,)
    repo=policy.coordinator.science_repository
    assert all(isinstance(repo.load(ref),PredictionSpec) for ref in policy.prediction_refs)
    prediction_sequence=max(repo.object_commit_sequence(ref) for ref in policy.prediction_refs)
    result=policy.execute(request)
    selected=policy.finalize([(request,result)],tmp_path)
    assert selected.candidate_ref.object_id=="B" and not selected.claim_refs
    assert selected.prediction_refs==policy.prediction_refs
    obs_ref=next(ref for ref in repo.latest_snapshot().object_refs if ref.object_id==result.observation.observation_id)
    assert repo.object_commit_sequence(obs_ref)>prediction_sequence
    assert json.loads((tmp_path/"SCIENCE_ITERATION_RECEIPT.json").read_text())["prediction_evaluation"]["state"]=="not_evaluable"
    policy.plan([request],tmp_path);policy.execute(request);policy.finalize([(request,result)],tmp_path)
    assert len(harness.calls)==1


def test_unknown_action_stays_pending_without_selected_scientific_result(tmp_path):
    policy,request,harness=_policy(tmp_path,unknown=True)
    assert policy.plan([request],tmp_path)==()
    assert policy.finalize([],tmp_path) is None and not harness.calls
    assert json.loads((tmp_path/"SCIENCE_ITERATION_RECEIPT.json").read_text())["outcome"]=="pending_unknown_value"
    assert not (tmp_path/"SELECTED_RESEARCH_RESULT.json").exists()


def test_existing_pipeline_execution_stage_calls_opt_in_policy(tmp_path):
    from test_article_pipeline import _happy_stack, _request
    pipeline,calls,request=_happy_stack(tmp_path)
    class Policy:
        def __init__(self):self.events=[]
        def plan(self,requests,work_dir):self.events.append("prediction_committed");return [r.request_id for r in requests]
        def execute(self,request):self.events.append("execute");return pipeline.execute(request)
        def finalize(self,executions,work_dir):self.events.append("selected");assert executions
    policy=Policy();pipeline.science_policy=policy
    result=pipeline.run(_request(str(tmp_path/"pipeline")))
    assert result.status != "failed",result.validation_errors
    assert policy.events==["prediction_committed","execute","selected"]
    assert len(calls["execute"])==1
