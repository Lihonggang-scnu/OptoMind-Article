"""Offline concrete-branch numerical execution, including real spawned workers."""
import copy
import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from focused_research import directional_stage as ds, search_stage as st
from focused_research import direction_expansion as de
from focused_research.tests.test_search_stage import inputs, Planner


def setup(tmp_path):
    st.run_search(**inputs(),output=tmp_path/'original',cache_dir=tmp_path/'cache',
                  max_forward_evaluations=0,max_new_evals=2,backend='numpy')
    previous=st.read_search(tmp_path/'original')
    parent=next(iter(previous['archive']['designs']))
    plans={'plans':[{'parent_design_id':parent,'action':'refine_family',
        'diagnosis':{'explanation':'Test different shared high-index thickness starts under unchanged physics.'},
        'expected_goal_changes':{'G1':'unknown','G2':'unknown'}}]}
    branches=[]
    for i,high in enumerate((60,64)):
        branches.append({'branch_id':'branch_'+str(i),'plan_index':0,'hypothesis_id':'plan_0000',
            'parent_design_id':parent,'parameterization':'family',
            'parameters_nm':{'dH':high,'dL':95},
            'search_bounds_nm':{'dH':[58,68],'dL':[92,100]},
            'rationale':'An explicit hypothetical seed from the fake expansion planner.'})
    return previous,plans,{'branches':branches}


def execute(tmp_path,plans,payload,**kwargs):
    options=dict(output=tmp_path/'directional',cache_dir=tmp_path/'cache',max_forward_evaluations=16,
                 max_new_evals=10,search_workers=1,max_workers=1,backend='numpy')
    options.update(kwargs)
    return ds.run_directional_search(tmp_path/'original',payload,plans,**options)


def test_real_branch_search_retains_ancestry_archive_and_global_budget(tmp_path):
    previous,plans,payload=setup(tmp_path)
    summary=execute(tmp_path,plans,payload)
    assert summary['status']=='search_complete',st.load(tmp_path/'directional/input_failures.json')
    assert summary['branch_count']==2
    assert 0<summary['metering']['search_forward_design_rows']<=16
    assert summary['metering']['certification_new_evaluations']<=10
    later=st.read_search(tmp_path/'directional')
    assert set(previous['archive']['designs']).issubset(later['archive']['designs'])
    assert later['archive']['objectives']==previous['archive']['objectives']
    assert later['archive']['conditions']==previous['archive']['conditions']
    workers=st.load(tmp_path/'directional/branch_search_results.json')
    reservations=st.load(tmp_path/'directional/branch_reservations.json')
    assert sum(r['reserved_forward_design_rows'] for r in reservations)==16
    for worker in workers:
        assert worker['search']['search_policy']['initialization_policy']=='provided_only'
        assert worker['search']['evaluations']
        assert all(e['source']!='space_filling' for e in worker['search']['evaluations'])
        assert worker['search']['metering']['forward_design_rows']<=worker['reserved_forward_design_rows']
    ancestry=st.load(tmp_path/'directional/candidate_ancestry.json')
    assert {a['branch_id'] for a in ancestry.values()}=={'branch_0','branch_1'}
    assert all(a['parent_design_id']==plans['plans'][0]['parent_design_id'] for a in ancestry.values())
    assert all(a['expanded_seed_instance_id'] for a in ancestry.values())
    assert all(g['status']=='observed' for d in later['diagnostics'] for g in d['diagnostics']['goals'])
    planner=Planner('{"plans":[]}')
    assert st.revise(tmp_path/'directional',output=tmp_path/'next_revision',planner=planner)['status']=='plans_recorded'
    assert len(planner.calls)==1


def test_two_spawned_search_workers_do_actual_numerical_work(tmp_path):
    previous,plans,payload=setup(tmp_path)
    summary=execute(tmp_path,plans,payload,search_workers=2,max_forward_evaluations=40)
    assert summary['status']=='search_complete'
    assert summary['metering']['search_executor']=='process_pool_spawn'
    workers=st.load(tmp_path/'directional/branch_search_results.json')
    assert all(w['status']=='completed' and w['worker_pid']!=os.getpid() for w in workers)
    assert all(w['search']['metering']['forward_design_rows']>0 for w in workers)
    assert sum(w['search']['metering']['forward_design_rows'] for w in workers)<=40
    assert summary['metering']['search_worker_cpu_seconds']>0


def test_single_global_certification_dispatch_after_all_branch_searches(tmp_path,monkeypatch):
    previous,plans,payload=setup(tmp_path)
    actual=st.ex.execute_prepared_batch
    calls=[]
    def record(envelopes,**kwargs):
        calls.append((kwargs['max_new_evals'],len(envelopes)))
        return actual(envelopes,**kwargs)
    monkeypatch.setattr(st.ex,'execute_prepared_batch',record)
    execute(tmp_path,plans,payload,max_new_evals=3)
    assert [budget for budget,_ in calls]==[0,3]
    assert st.load(tmp_path/'directional/summary.json')['metering']['certification_new_evaluations']<=3


def test_worker_failure_retains_reservation_and_other_branch_result(tmp_path,monkeypatch):
    previous,plans,payload=setup(tmp_path)
    actual=ds.fo.optimize_family
    calls=[]
    def sometimes(route,envelopes,**kwargs):
        calls.append(kwargs['max_forward_evaluations'])
        if len(calls)==1:
            raise RuntimeError('fixture worker failure after unknown work')
        return actual(route,envelopes,**kwargs)
    monkeypatch.setattr(ds.fo,'optimize_family',sometimes)
    result=execute(tmp_path,plans,payload)
    assert result['status']=='partial'
    assert calls==[8,8]
    assert result['metering']['search_metering_complete'] is False
    assert result['metering']['failed_branch_reserved_forward_design_rows']==8
    assert result['metering']['search_forward_design_rows']<=8
    branch=st.load(tmp_path/'directional/branch_feedback.json')['branches'][0]
    assert branch['search_status']=='search_failed'
    assert branch['counts']['search_forward_design_rows'] is None
    assert branch['selected_certified_designs']==[]
    assert set(previous['archive']['designs']).issubset(st.load(tmp_path/'directional/design_archive.json')['designs'])


def test_bad_expanded_branch_is_local_not_batch_rejection(tmp_path):
    previous,plans,payload=setup(tmp_path)
    payload['branches'][0]['parameters_nm']['dH']=100000
    result=execute(tmp_path,plans,payload)
    assert result['status']=='partial' and result['branch_count']==1
    assert st.load(tmp_path/'directional/expansion_report.json')['rejected_count']==1
    assert result['metering']['search_forward_design_rows']<=16


def test_expansion_manifest_and_cli_roundtrip(tmp_path):
    previous,plans,payload=setup(tmp_path)
    planner=Planner(json.dumps(payload))
    result=de.expand_directions(tmp_path/'original',plans,output=tmp_path/'expansion',
                               planner=planner,max_forward_evaluations=16)
    assert result['status']=='branches_recorded'
    assert ds.main(['execute','--previous-search',str(tmp_path/'original'),
        '--expansion-dir',str(tmp_path/'expansion'),'--cache-dir',str(tmp_path/'cache'),
        '--output',str(tmp_path/'directional'),'--max-forward-evaluations','16','--max-new-evals','10',
        '--search-workers','1','--max-workers','1','--backend','numpy','--execute'])==0
    assert len(planner.calls)==1
    p=tmp_path/'expansion/expansion_response.json'
    altered=st.load(p);altered['branches'][0]['parameters_nm']['dH']=63;p.write_text(json.dumps(altered))
    with pytest.raises(ValueError,match='binding mismatch'):
        ds.read_expansion(tmp_path/'expansion',previous)


def test_explicit_budget_and_worker_limits_before_work(tmp_path):
    previous,plans,payload=setup(tmp_path)
    with pytest.raises(ValueError,match='search_workers'):
        execute(tmp_path,plans,payload,search_workers=3)
    with pytest.raises(ValueError,match='nonnegative'):
        execute(tmp_path,plans,payload,max_forward_evaluations=-1)
    assert not (tmp_path/'directional').exists()


def test_uncertified_candidates_stay_explicit_without_losing_old_archive(tmp_path):
    previous,plans,payload=setup(tmp_path)
    result=execute(tmp_path,plans,payload,max_new_evals=0)
    assert result['metering']['certification_new_evaluations']==0
    assert result['uncertified_candidate_count']>0
    assert result['certification_coverage']=='partial'
    assert st.load(tmp_path/'directional/design_archive.json')['designs']==previous['archive']['designs']
    assert any(r['status']=='deferred_unvalidated' for r in st.load(tmp_path/'directional/execution_results.json'))
    branches=st.load(tmp_path/'directional/branch_feedback.json')['branches']
    assert sum(b['counts']['deferred_unvalidated_count'] for b in branches)>0
    assert all(b['counts']['certification_failure_count']==0 for b in branches)
    assert all(not b['selected_certified_designs'] for b in branches)


def test_returned_candidates_cannot_escape_declared_branch_bounds(tmp_path,monkeypatch):
    previous,plans,payload=setup(tmp_path)
    actual=ds.fo.optimize_family
    def bad_candidate(*args,**kwargs):
        search=actual(*args,**kwargs)
        for candidate in search['candidates']:
            for i,layer in enumerate(candidate['tmm_task']['stack']['layers']):
                if i%2==0: layer['thickness_nm']=80  # Original family permits 80, this branch does not.
        return search
    monkeypatch.setattr(ds.fo,'optimize_family',bad_candidate)
    result=execute(tmp_path,plans,payload)
    assert result['status']=='partial' and result['candidate_count']==0
    failures=st.load(tmp_path/'directional/input_failures.json')
    assert failures and all('branch search bounds' in f['error'] for f in failures)
    assert result['metering']['certification_new_evaluations']==0


def test_branch_feedback_is_bound_with_actual_parent_deltas_and_all_goals(tmp_path):
    previous,plans,payload=setup(tmp_path)
    execute(tmp_path,plans,payload)
    root=tmp_path/'directional'
    feedback=st.load(root/'branch_feedback.json')
    archive=st.load(root/'design_archive.json')
    assert st.load(root/'manifest.json')['branch_feedback.json']==st.digest(feedback)
    assert len(feedback['plans'])==1
    assert feedback['plans'][0]['original_coarse_plan']==plans['plans'][0]
    for branch in feedback['branches']:
        assert branch['parameters_nm']==next(b['parameters_nm'] for b in payload['branches'] if b['branch_id']==branch['branch_id'])
        assert len(branch['selected_certified_designs'])<=len(archive['objectives'])
        assert sum(branch['outcome_counts_unique_certified_designs'].values())==branch['counts']['unique_associated_certified_design_count']
        for point in branch['selected_certified_designs']:
            assert set(point['metrics'])=={'G1','G2'}
            for goal,value in point['metrics'].items():
                assert value==archive['designs'][point['design_id']]['metrics'][goal]['value']
                assert point['deltas_from_parent'][goal]==value-branch['parent_metrics'][goal]
    feedback['scope']='tampered'
    (root/'branch_feedback.json').write_text(json.dumps(feedback))
    with pytest.raises(ValueError,match='mismatch'):
        st.read_search(root)


def test_feedback_extremes_are_real_points_and_cache_is_not_new_evidence():
    job={'branch_id':'b','plan_index':0,'hypothesis_id':'h','parent_design_id':'p',
         'parameterization':'family','rationale':'test','parameters_nm':{'d':2},'search_bounds_nm':{}}
    values={'p':(0,0),'a':(2,1),'b':(-1,-3),'c':(1,-1),'d':(-1,1),'e':(0,0)}
    # Maximize G1, minimize G2: a/b trade off, c improves, d worsens, e unchanged.
    archive={'objectives':[{'objective_id':'G1','sense':'maximize'},{'objective_id':'G2','sense':'minimize'}],
        'designs':{d:{'metrics':{'G1':{'value':v[0]},'G2':{'value':v[1]}}} for d,v in values.items()},
        'evaluations':[{'instance_id':d,'design_id':d,'cache_hit':d=='a','new_evidence':d!='a'} for d in values if d!='p']}
    rows=[{'instance_id':d,'directional_attribution':{'branch_id':'b'},'physics_accepted':True,
           'cache_hit':d=='a','metering':{'new_evaluations':int(d!='a')}} for d in values if d!='p']
    workers=[{'status':'completed','reserved_forward_design_rows':8,'search':{'metering':{'forward_design_rows':8,'solver_channel_rows':24}}}]
    result=ds.build_branch_feedback([job],workers,rows,archive,{'plans':[{'diagnosis':'coarse'}]},[])
    branch=result['branches'][0]
    assert branch['outcome_counts_unique_certified_designs']=={'all_improve':1,'all_worse':1,'tradeoff':2,'unchanged':1}
    assert {p['design_id'] for p in branch['selected_certified_designs']}=={'a','b'}
    assert all(p['metrics']!={'G1':2,'G2':-3} for p in branch['selected_certified_designs'])
    assert branch['counts']['cache_hit_count']==1
    assert branch['counts']['new_evidence_count']==4
    assert next(p for p in branch['selected_certified_designs'] if p['design_id']=='a')['new_evidence_count']==0
