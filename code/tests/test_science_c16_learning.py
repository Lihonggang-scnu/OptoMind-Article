import json
import numpy as np
import pytest
from optomind_optics.harness.science.learning.learning_runner import *
from optomind_optics.harness.science.learning.gepa_adapter import GEPAAdapter
from optomind_optics.harness.science.learning.offline_forward import ScientificForwardRunner
from optomind_optics.harness.science.learning.context_function import build_context,mce_trigger
from optomind_optics.harness.science.learning.headroom import diagnose_headroom
from optomind_optics.harness.science.terminal_problem import TerminalDecisionProblem
from optomind_optics.harness.science.controller import MeasurementAction


def tasks():
    return [LearningTask(task_id=s,provenance_group=s,split=s,action_input=ActionInput(task_id=s,initial_snapshot='frozen',library_snapshot='library',available_budget=10,final_validation_reserve=1)) for s in 'TVPA']


def test_forward_candidates_reexecute_real_controller_and_split_gates():
    calls=[]
    context={key:'x' for key in ('task','objective','model','sample','material','solver','fidelity','loss','split')}
    problem=lambda view:TerminalDecisionProblem(('w0','w1'),('d0','d1'),np.array([[0,10],[10,0]]),context,'loss')
    actions=lambda view:(MeasurementAction('good',('o0','o1'),((1,0),(0,1)),.1,1),MeasurementAction('bad',('o0','o1'),((.5,.5),(.5,.5)),.1,1))
    def acquire_factory(view):
        calls.append(view.task_id)
        return lambda a:'o1'
    forward=ScientificForwardRunner(problem,actions,(.5,.5),acquire_factory,lambda v,r:float(r.selected_design_id=='d1'))
    runner=LearningRunner(tasks(),forward,budget=100,cache_build_cost=2)
    seed=PolicyVersion(experiment_policy=json.dumps({'depth':1,'action_ids':['bad']}))
    winner,manifest=optimize_offline(runner,seed,lambda p,f:json.dumps({'depth':1,'action_ids':['good']}),train_ids=['T'],validation_ids=['V'],proposal_cost=3)
    assert winner!=seed and calls==['V','T','V']
    assert manifest.actual_cost==9 # cache 2 + attempts 3 + proposal 3 + measurement 1
    with pytest.raises(ValueError,match='split'):runner.evaluate(['A'],winner)
    passed,_=runner.evaluate_promotion(['P'],winner,minimum_score=.5)
    assert passed
    assert runner.evaluate(['A'],winner,stage='audit')[0].score==1


def test_failed_attempt_cost_and_isolated_typed_inputs():
    seen=[]
    def fail(view,policy,meter):
        seen.append(view)
        meter.charge('failed_resource',2)
        raise RuntimeError('failure')
    runner=LearningRunner(tasks(),fail,budget=100)
    p=PolicyVersion(experiment_policy='{"depth":1}')
    assert not runner.evaluate(['T'],p)[0].valid
    assert not runner.evaluate(['T'],p)[0].valid
    assert seen[0] is not seen[1] and seen[0]==seen[1]
    assert runner.cost.total==6
    with pytest.raises(ValueError):ActionInput(task_id='x',initial_snapshot='s',library_snapshot='l',available_budget=1,final_validation_reserve=0,target='secret')
    with pytest.raises(ValueError):PolicyVersion(experiment_policy='{"depth":1,"evaluator":"rewrite"}')


def test_gepa_reflection_training_only_and_component_locked():
    runner=LearningRunner(tasks(),lambda v,p,m:ForwardResult({},1,{},{}),budget=100)
    adapter=GEPAAdapter(runner);candidate={'experiment_policy':'{"depth":1}'}
    batch=adapter.evaluate(['T'],candidate,True)
    assert adapter.make_reflective_dataset(candidate,batch,['experiment_policy'])
    assert len(batch.outputs)==len(batch.scores)==len(batch.trajectories)==len(batch.objective_scores)==1
    batch=adapter.evaluate(['V'],candidate,True)
    with pytest.raises(ValueError,match='training'):adapter.make_reflective_dataset(candidate,batch,['experiment_policy'])
    with pytest.raises(ValueError):adapter.evaluate(['T'],{**candidate,'acceptance':'anything'})


def test_split_overlap_mce_disabled_and_injection_refused():
    ts=tasks(); ts[1]=LearningTask(task_id='V',provenance_group='T',split='V',action_input=ts[1].action_input)
    with pytest.raises(ValueError,match='overlap'):LearningRunner(ts,None,budget=1)
    report=diagnose_headroom(baseline=0,reference=1,comparison='informed_high_budget_reference',context_bottleneck=True,forward_verified=True,evidence=('r',))
    assert not mce_trigger(report)
    with pytest.raises(ValueError):build_context(tasks()[0].action_input,source_records=[],cutoff=1,max_records=1,report=report)
    report=diagnose_headroom(baseline=0,reference=1,comparison='legal_history_reaccess',context_bottleneck=True,forward_verified=True,evidence=('r',))
    with pytest.raises(ValueError,match='whitelisted'):build_context(tasks()[0].action_input,source_records=[{'source_id':'x','sequence':1,'observations':[],'ground_truth':3}],cutoff=1,max_records=1,report=report)


def test_proposal_feedback_never_receives_hidden_forward_trace():
    runner=LearningRunner(tasks(),lambda v,p,m:ForwardResult({'target':3},1,{}, {'ground_truth':'secret','credentials':'secret'}),budget=100)
    def propose(policy,feedback):
        payload=feedback[0].model_dump_json()
        assert 'secret' not in payload and 'ground_truth' not in payload and 'target' not in payload
        return policy
    optimize_offline(runner,PolicyVersion(experiment_policy='{"depth":1}'),propose,train_ids=['T'],validation_ids=['V'])


def test_real_adaptive_policy_reexecutes_branch_and_charges_only_taken_path():
    from test_science_c13_adaptive_rollout import fixture
    problem,q,actions=fixture()
    seen=[]
    def acquire_factory(view):
        def acquire(action):
            seen.append(action.action_id)
            return {'screen':'cd','resolve_cd':'d'}[action.action_id]
        return acquire
    forward=ScientificForwardRunner(lambda v:problem,lambda v:actions,q,acquire_factory,lambda v,r:float(r.selected_design_id=='D'))
    view=ActionInput(task_id='T',initial_snapshot='frozen',library_snapshot='library',available_budget=3,final_validation_reserve=1)
    task=LearningTask(task_id='T',provenance_group='T',split='T',action_input=view)
    runner=LearningRunner([task],forward,budget=10)
    policy=PolicyVersion(experiment_policy=json.dumps({'depth':2,'continuation_mode':'adaptive'}))
    result=runner.evaluate(['T'],policy)[0]
    assert result.valid and result.score==1
    assert seen==['screen','resolve_cd']
    assert result.objective_scores['measurement_cost']==2
    assert runner.cost.total==3  # attempt + two actual purchases; no unchosen branch charge
