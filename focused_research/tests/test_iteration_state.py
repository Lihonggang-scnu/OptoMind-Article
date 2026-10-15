import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from iteration_state import (create_prediction_record,begin_execution,record_observation,
    record_next_action,read_record,build_revision_prompt)
from physical_observations import build_physical_observations
from test_physical_observations import fixture


def proposal():
    return {'proposal_id':'P1','proposal_version':1,'route_id':'R1','baseline_instance_id':'baseline',
        'candidate_instance_id':'candidate','claim':'Uncertain interference shift',
        'predictions':[{'goal_id':'G1','direction':'increase'},{'goal_id':'G2','direction':'increase'}]}


def test_frozen_before_execution_and_next_action_versioned(tmp_path):
    p=proposal(); frozen=create_prediction_record(tmp_path,p)
    original=(tmp_path/'prediction.json').read_bytes()
    p['predictions'][0]['direction']='decrease'
    start=begin_execution(tmp_path)
    assert start['previous_sha256']==frozen['sha256']
    obs=build_physical_observations(*fixture())
    record=record_observation(tmp_path,obs)
    assert [r['status'] for r in record['payload']['prediction_comparison']]==['supports','challenged']
    action={'proposal_version':2,'status':'proposed_not_executed','proposed_edit':{'thicknesses_nm':[55,90]},
        'physical_basis_refs':['goal:G2'],'predictions':[{'goal_id':'G1','direction':'increase'},{'goal_id':'G2','direction':'increase'}]}
    next_record=record_next_action(tmp_path,action)
    assert next_record['previous_sha256']==record['sha256']
    assert (tmp_path/'prediction.json').read_bytes()==original
    assert read_record(tmp_path,'prediction')['payload']['predictions'][0]['direction']=='increase'
    with pytest.raises(FileExistsError):create_prediction_record(tmp_path,proposal())
    with pytest.raises(FileExistsError):record_observation(tmp_path,obs)
    assert 'goal:G2' in build_revision_prompt(proposal(),obs)


def test_cannot_observe_without_prior_execution_record(tmp_path):
    create_prediction_record(tmp_path,proposal())
    with pytest.raises(FileNotFoundError):record_observation(tmp_path,build_physical_observations(*fixture()))


def test_tampering_is_detected_before_observation(tmp_path):
    create_prediction_record(tmp_path,proposal());begin_execution(tmp_path)
    path=tmp_path/'prediction.json';v=json.loads(path.read_text());v['payload']['claim']='rewritten';path.write_text(json.dumps(v))
    with pytest.raises(ValueError,match='integrity'):record_observation(tmp_path,build_physical_observations(*fixture()))


def test_identity_mismatch_rejected(tmp_path):
    create_prediction_record(tmp_path,proposal());begin_execution(tmp_path)
    o=build_physical_observations(*fixture());o['candidate_instance_id']='wrong'
    with pytest.raises(ValueError,match='identity'):record_observation(tmp_path,o)


@pytest.mark.parametrize('field,value',[('proposal_version',1),('status','executed'),('physical_basis_refs',['made-up']),('proposed_edit',{})])
def test_invalid_new_action_not_persisted(tmp_path,field,value):
    create_prediction_record(tmp_path,proposal());begin_execution(tmp_path)
    record_observation(tmp_path,build_physical_observations(*fixture()))
    a={'proposal_version':2,'status':'proposed_not_executed','proposed_edit':{'dH':60},'physical_basis_refs':['goal:G1'],
       'predictions':[{'goal_id':'G1','direction':'increase'}]};a[field]=value
    with pytest.raises(ValueError):record_next_action(tmp_path,a)
    assert not (tmp_path/'next_action.json').exists()


def test_multi_route_next_action_keeps_same_goal_scoped(tmp_path):
    create_prediction_record(tmp_path,proposal());begin_execution(tmp_path)
    record_observation(tmp_path,build_physical_observations(*fixture()))
    rows=[{'goal_id':'G1','direction':'increase','route_id':route,
           'baseline_instance_id':'baseline','candidate_instance_id':'candidate'} for route in ('R1','R2')]
    action={'proposal_version':2,'status':'proposed_not_executed','proposed_edit':{'contract':'new'},
        'route_ids':['R1','R2'],'physical_basis_refs':['goal:G1'],'predictions':rows}
    result=record_next_action(tmp_path,action)
    assert len(result['payload']['predictions'])==2


@pytest.mark.parametrize('problem',['duplicate','missing_identity','wrong_route'])
def test_scoped_next_action_rejects_invalid_identity(tmp_path,problem):
    create_prediction_record(tmp_path,proposal());begin_execution(tmp_path)
    record_observation(tmp_path,build_physical_observations(*fixture()))
    row={'goal_id':'G1','direction':'increase','route_id':'R1','baseline_instance_id':'baseline','candidate_instance_id':'candidate'}
    rows=[row]
    if problem=='duplicate':rows.append(dict(row))
    if problem=='missing_identity':del row['baseline_instance_id']
    if problem=='wrong_route':row['route_id']='R3'
    action={'proposal_version':2,'status':'proposed_not_executed','proposed_edit':{'contract':'new'},
        'route_ids':['R1','R2'],'physical_basis_refs':['goal:G1'],'predictions':rows}
    with pytest.raises(ValueError):record_next_action(tmp_path,action)


def test_initial_freeze_keeps_single_pair_goal_uniqueness(tmp_path):
    p=proposal();p['predictions'].append(dict(p['predictions'][0],route_id='R2'))
    with pytest.raises(ValueError,match='duplicate'):create_prediction_record(tmp_path,p)
