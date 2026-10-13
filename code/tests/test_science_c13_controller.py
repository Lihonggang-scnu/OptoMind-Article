import numpy as np
import pytest
from optomind_optics.harness.science.controller import MeasurementAction, decide_next, execute_round
from test_science_c09_c10_decision import problem


def actions(cost=.05):
    return [MeasurementAction('bit0', ('0','1'), ((1,0),(1,0),(0,1),(0,1)),cost,1,'conditionally_independent_bits'),
            MeasurementAction('bit1', ('0','1'), ((1,0),(0,1),(1,0),(0,1)),cost,1,'conditionally_independent_bits')]


def test_complementarity_and_real_committed_consumer():
    p=problem([[0,1],[1,0],[1,0],[0,1]])
    a=actions()
    decision=decide_next(p,[.25]*4,a,available_budget=3,final_validation_reserve=1)
    assert decision.selected_actions==('bit0','bit1')
    assert all(r.value_interval[0]==pytest.approx(0) for ids,r in decision.estimates if len(ids)==1)
    calls=[]
    def acquire(action):
        calls.append(action.action_id)
        return {'bit0':'0','bit1':'1'}[action.action_id]
    receipt=execute_round(p,[.25]*4,a,decision,acquire,current_context_hash=decision.context_hash)
    assert calls==['bit0','bit1']
    assert receipt.selected_design_id=='d1'


def test_stop_reserve_unknown_and_stale():
    p=problem([[0,1],[1,0],[1,0],[0,1]])
    d=decide_next(p,[.25]*4,actions(),available_budget=2,final_validation_reserve=1)
    assert not d.selected_actions
    unknown=decide_next(p,[.25]*4,[],available_budget=3,final_validation_reserve=1,unknown_action_ids=['new-channel'])
    assert unknown.reason=='unvalued_actions_require_bounded_exploration'
    with pytest.raises(ValueError,match='stale'):
        execute_round(p,[.25]*4,actions(),d,lambda a:'0',current_context_hash='changed')
    expensive=decide_next(p,[.25]*4,actions(.4),available_budget=3,final_validation_reserve=1)
    assert not expensive.selected_actions
