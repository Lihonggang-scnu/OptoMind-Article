from dataclasses import asdict, replace
import numpy as np
import pytest
from optomind_optics.harness.science.controller import MeasurementAction
from optomind_optics.harness.science.terminal_problem import TerminalDecisionProblem
from optomind_optics.harness.science.rollout import plan_adaptive, execute_adaptive, adaptive_plan_from_dict


def fixture():
    context = {k: 'frozen' for k in ('task','objective','model','sample','material','solver','fidelity','loss','split')}
    problem = TerminalDecisionProblem(tuple('abcde'), tuple('ABCDE'), 1-np.eye(5), context, 'loss')
    a = MeasurementAction('screen', ('ab','cd','e'), ((1,0,0),(1,0,0),(0,1,0),(0,1,0),(0,0,1)), .02, 1, 'world_independent')
    b = MeasurementAction('resolve_ab', ('a','b','other'), ((1,0,0),(0,1,0),(0,0,1),(0,0,1),(0,0,1)), .1, 1, 'world_independent')
    c = MeasurementAction('resolve_cd', ('c','d','other'), ((0,0,1),(0,0,1),(1,0,0),(0,1,0),(0,0,1)), .1, 1, 'world_independent')
    return problem, (.2,)*5, (a,b,c)


def test_first_result_changes_second_action_or_stops():
    problem,q,actions=fixture()
    plan=plan_adaptive(problem,q,actions,available_budget=3,final_validation_reserve=1)
    assert plan.root.action_id=='screen'
    assert plan.choose((('screen','ab'),)).action_id=='resolve_ab'
    assert plan.choose((('screen','cd'),)).action_id=='resolve_cd'
    assert plan.choose((('screen','e'),)).action_id is None
    assert plan.root.expected_loss==pytest.approx(.1)
    assert plan.root.expected_resource_cost==pytest.approx(1.8)
    assert plan.root.maximum_resource_cost==2
    calls=[]
    answers={'screen':'cd','resolve_cd':'d'}
    receipt=execute_adaptive(problem,q,actions,plan,lambda a:(calls.append(a.action_id),answers[a.action_id])[1])
    assert calls==['screen','resolve_cd'] and receipt.selected_design_id=='D'
    assert receipt.resource_cost==2
    stop=execute_adaptive(problem,q,actions,plan,lambda a:'e')
    assert stop.selected_design_id=='E' and stop.resource_cost==1


def test_snapshot_replay_partial_recovery_and_unknown_dispatch():
    problem,q,actions=fixture()
    plan=plan_adaptive(problem,q,actions,available_budget=3,final_validation_reserve=1)
    restored=adaptive_plan_from_dict(asdict(plan))
    assert restored.policy_hash==plan.policy_hash
    calls=[]; checkpoints=[]
    result=execute_adaptive(problem,q,actions,restored,lambda a:(calls.append(a.action_id),'b')[1],
                            completed_observations=(('screen','ab'),),checkpoint=checkpoints.append)
    assert calls==['resolve_ab'] and result.selected_design_id=='B'
    assert any(x['state']=='dispatching' and x['action_id']=='resolve_ab' for x in checkpoints)
    replay=execute_adaptive(problem,q,actions,restored,lambda a:pytest.fail('no repurchase'),
                            completed_observations=result.observations)
    assert replay==result
    with pytest.raises(RuntimeError,match='no automatic retry'):
        execute_adaptive(problem,q,actions,restored,lambda a:pytest.fail('no retry'),unresolved_action_id='screen')
    with pytest.raises(ValueError,match='prefix'):
        execute_adaptive(problem,q,actions,restored,lambda a:pytest.fail('no purchase'),completed_observations=(('resolve_ab','a'),))


def test_forged_stale_trees_and_budget_fail_closed():
    problem,q,actions=fixture()
    plan=plan_adaptive(problem,q,actions,available_budget=3,final_validation_reserve=1)
    forged=replace(plan,root=replace(plan.root,action_id='resolve_ab'))
    for changed, candidate in [(actions,forged),(tuple(replace(a,loss_cost=a.loss_cost*2) for a in actions),plan)]:
        with pytest.raises(ValueError,match='stale or altered'):
            execute_adaptive(problem,q,changed,candidate,lambda a:pytest.fail('no stale action'))
    small=plan_adaptive(problem,q,actions,available_budget=2,final_validation_reserve=1)
    assert small.root.maximum_resource_cost<=1
    none=plan_adaptive(problem,q,actions,available_budget=1,final_validation_reserve=1)
    assert none.root.action_id is None
    costly=tuple(replace(a,loss_cost=2) for a in actions)
    assert plan_adaptive(problem,q,costly,available_budget=3,final_validation_reserve=1).root.action_id is None


def test_no_implicit_independence_or_impossible_observation():
    problem,q,actions=fixture()
    unbound=tuple(replace(a,conditional_independence_group=None) for a in actions)
    plan=plan_adaptive(problem,q,unbound,available_budget=3,final_validation_reserve=1)
    assert all(child.action_id is None for _,_,child in plan.root.branches)
    plan=plan_adaptive(problem,q,actions,available_budget=3,final_validation_reserve=1)
    with pytest.raises(ValueError,match='outside committed support'):
        plan.choose((('screen','invented'),))
    with pytest.raises(ValueError):
        plan_adaptive(problem,q,actions,available_budget=float('inf'),final_validation_reserve=1)
