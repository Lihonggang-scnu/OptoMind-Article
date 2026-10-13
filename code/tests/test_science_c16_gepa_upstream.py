"""Optional real pinned GEPA smoke. Run with its verified checkout on PYTHONPATH."""
import importlib.util
import json
import numpy as np
import pytest
from optomind_optics.harness.science.learning.gepa_engine import optimize_with_gepa,verify_upstream_pin
from optomind_optics.harness.science.learning.learning_runner import ActionInput,LearningTask,LearningRunner,PolicyVersion
from optomind_optics.harness.science.learning.offline_forward import ScientificForwardRunner
from optomind_optics.harness.science.terminal_problem import TerminalDecisionProblem
from optomind_optics.harness.science.controller import MeasurementAction


@pytest.mark.skipif(importlib.util.find_spec('gepa') is None,reason='optional pinned upstream GEPA absent')
def test_real_gepa_optimizes_real_scientific_forward_runs_without_llm():
    assert verify_upstream_pin()=='462e437a09be67d2acb59564cc0ea59132f5777c'
    context={k:'x' for k in ('task','objective','model','sample','material','solver','fidelity','loss','split')}
    tasks=[LearningTask(task_id=s,provenance_group=s,split=s,action_input=ActionInput(task_id=s,initial_snapshot='frozen',library_snapshot='same-library',available_budget=10,final_validation_reserve=1)) for s in 'TVPA']
    calls=[]
    def problem(view):
        calls.append(view.task_id)
        return TerminalDecisionProblem(('w0','w1'),('d0','d1'),np.array([[0,10],[10,0]]),context,'loss')
    def actions(view):return (MeasurementAction('good',('o0','o1'),((1,0),(0,1)),.1,1),MeasurementAction('bad',('o0','o1'),((.5,.5),(.5,.5)),.1,1))
    forward=ScientificForwardRunner(problem,actions,(.5,.5),lambda v:lambda a:'o1',lambda v,r:float(r.selected_design_id=='d1'))
    runner=LearningRunner(tasks,forward,budget=100,cache_build_cost=2)
    proposals=[]
    def proposer(candidate,feedback,components):
        assert components==['experiment_policy']
        assert all(row['Inputs']['task_id']=='T' for row in feedback['experiment_policy'])
        proposals.append(feedback)
        return {'experiment_policy':json.dumps({'depth':1,'action_ids':['good']})}
    seed=PolicyVersion(experiment_policy=json.dumps({'depth':1,'action_ids':['bad']}))
    winner,manifest,result=optimize_with_gepa(runner,seed,proposer,train_ids=['T'],validation_ids=['V'],max_metric_calls=4,proposal_cost=3)
    assert winner.version_hash!=seed.version_hash
    assert len(proposals)==1 and calls==['V','T','T','V']
    assert len(runner.results)==4 and manifest.actual_cost==11
    assert result.val_aggregate_scores==[0.,1.]
    assert runner.evaluate_promotion(['P'],winner,minimum_score=.5)[0]
    assert runner.evaluate(['A'],winner,stage='audit')[0].score==1
