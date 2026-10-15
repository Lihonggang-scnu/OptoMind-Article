import copy
import json
import sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'vendor/review')]
import scientific_stage as st


def inputs(tmp_path):
    ex=ROOT/'examples'
    paths={k:ex/v for k,v in {'sheet':'rgb_nir_task_sheet.json','evidence':'research_routes_evidence_fixture.json','constraints':'research_routes_constraints.json','routes':'research_routes_example.json'}.items()}
    media=tmp_path/'media.json'; media.write_text(json.dumps({'air':{'constant_n':1},'fused_silica':{'constant_n':1.46},'TiO2':{'constant_n':2.3},'SiO2':{'constant_n':1.46}}))
    grid=tmp_path/'grid.json';grid.write_text(json.dumps(list(range(450,1001,25))))
    common=['--task-sheet',str(paths['sheet']),'--evidence-file',str(paths['evidence']),'--constraints-file',str(paths['constraints'])]
    run=['run',*common,'--routes-file',str(paths['routes']),'--optical-media',str(media),'--wavelengths',str(grid),'--cache-dir',str(tmp_path/'cache'),'--max-new-evals','2','--execute','--output',str(tmp_path/'run')]
    return paths,common,run


class Planner:
    last_usage={}
    last_transport={'finish_reason':'stop'}
    def __init__(self, response):self.response=response;self.calls=[]
    def _call_query_plan_model(self,prompt):
        self.calls.append(copy.deepcopy(prompt));return self.response


def test_real_run_cache_and_injected_revision(tmp_path):
    paths,common,run=inputs(tmp_path)
    assert st.main(run)==0
    out=tmp_path/'run'
    summary=st.load(out/'summary.json')
    assert summary['metering']['new_evaluations']==2
    obs=st.load(out/'physical_observations.json')
    assert len(obs)==1 and len(obs[0]['observation']['goals'])==2
    assert all(g['status']=='observed' for g in obs[0]['observation']['goals'])
    assert (Path(obs[0]['iteration_path'])/'prediction.json').is_file()
    second=list(run);second[-1]=str(tmp_path/'rerun');second[second.index('--max-new-evals')+1]='0'
    assert st.main(second)==0
    assert st.load(tmp_path/'rerun/summary.json')['metering']['workbench_forward_calls']==0
    planner=Planner(paths['routes'].read_text())
    revise=['revise',*common,'--previous-run',str(out),'--output',str(tmp_path/'revision'),'--execute']
    assert st.main(revise,planner=planner)==0
    assert len(planner.calls)==1
    assert planner.calls[0]['user_payload']['previous_physical_iteration'][0]['observation']['goals']
    assert (Path(obs[0]['iteration_path'])/'next_action.json').is_file()
    assert st.load(tmp_path/'revision/revision_parent.json')['next_action_executed'] is False
    revise[revise.index('--output')+1]=str(tmp_path/'duplicate_revision')
    assert st.main(revise,planner=planner)==2
    assert len(planner.calls)==1


def test_explicit_execution_and_failed_budget_are_not_success(tmp_path):
    _,_,run=inputs(tmp_path)
    without=[v for v in run if v!='--execute']
    assert st.main(without)==2
    assert not (tmp_path/'run').exists()
    run[run.index('--max-new-evals')+1]='0'
    assert st.main(run)==2
    assert st.load(tmp_path/'run/summary.json')['status']=='partial'
    assert st.load(tmp_path/'run/execution_results.json')['metering']['new_evaluations']==0


def test_modified_observation_blocks_model_dispatch(tmp_path):
    paths,common,run=inputs(tmp_path)
    assert st.main(run)==0
    p=tmp_path/'run/physical_observations.json'; content=st.load(p)
    content[0]['observation']['goals'][0]['delta']=99
    p.write_text(json.dumps(content))
    planner=Planner(paths['routes'].read_text())
    assert st.main(['revise',*common,'--previous-run',str(tmp_path/'run'),'--output',str(tmp_path/'revision'),'--execute'],planner=planner)==2
    assert planner.calls==[]


def test_bad_prediction_pair_is_local_not_batch_abort(tmp_path):
    paths,common,run=inputs(tmp_path)
    routes=st.load(paths['routes'])
    bad=copy.deepcopy(routes['routes'][0]); bad['id']='duplicate_expectation'
    bad['expectations'].append(copy.deepcopy(bad['expectations'][0]))
    routes['routes'].append(bad)
    path=tmp_path/'routes.json';path.write_text(json.dumps(routes))
    run[run.index('--routes-file')+1]=str(path)
    assert st.main(run)==2
    summary=st.load(tmp_path/'run/summary.json')
    assert summary['observation_pairs']==1
    assert summary['input_failure_count']==1
    assert summary['metering']['new_evaluations']==2
