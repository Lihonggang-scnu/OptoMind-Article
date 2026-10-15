"""Real offline two-round numerical closure; fake audited planner only for plans."""
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from focused_research import search_stage as st

ROOT = Path(__file__).resolve().parents[2]


def inputs():
    p = ROOT/'focused_research/examples'
    return dict(routes=st.load(p/'research_routes_example.json'),
        sheet=st.load(p/'rgb_nir_task_sheet.json'),
        evidence=st.load(p/'research_routes_evidence_fixture.json'),
        constraints=st.load(p/'research_routes_constraints.json'),
        optical_media={'air':{'constant_n':1},'fused_silica':{'constant_n':1.46},
                       'TiO2':{'constant_n':2.3},'SiO2':{'constant_n':1.46}},
        wavelengths_nm=list(range(450,1001,25)))


def run(tmp_path, **kwargs):
    options = dict(output=tmp_path/'first',cache_dir=tmp_path/'cache',
                   max_forward_evaluations=12,max_new_evals=20,backend='numpy')
    options.update(kwargs)
    return st.run_search(**inputs(),**options)


def plan(previous, *, action='refine_family', parent=None):
    parent = parent or previous['archive']['pareto_design_ids'][0]
    diagnosis = next(d for d in previous['diagnostics'] if parent in (d['comparison_baseline_design_id'],d['design_id']))
    return {'plans':[{'parent_design_id':parent,'action':action,
        'diagnosis':{'diagnosis_id':diagnosis['diagnosis_id'],
            'observation_ids':[g['observation_id'] for g in diagnosis['diagnostics']['goals'] if g['status']=='observed'],
            'explanation':'Measured objective values differ across the supplied designs; test a bounded refinement.'},
        'expected_goal_changes':{g['objective_id']:'unknown' for g in previous['archive']['objectives']}}]}


class Planner:
    last_usage={'cost_cny':0}
    last_transport={'finish_reason':'stop'}
    def __init__(self,response): self.response=response; self.calls=[]
    def _call_query_plan_model(self,prompt):
        self.calls.append(copy.deepcopy(prompt))
        return self.response


def test_true_search_revision_refinement_and_cache(tmp_path):
    summary = run(tmp_path)
    assert summary['status']=='search_complete',st.load(tmp_path/'first/input_failures.json')
    assert summary['metering']['search_forward_design_rows']==12
    assert summary['metering']['search_solver_channel_rows']>=24
    assert summary['metering']['certification_new_evaluations']>=2
    previous = st.read_search(tmp_path/'first')
    assert len(previous['archive']['designs'])>2
    assert len(previous['archive']['objectives'])==2
    assert summary['diagnostic_polarization_channels']==['p','s','unpolarized']
    assert all(g['polarization']['status']=='observed' for d in previous['diagnostics'] for g in d['diagnostics']['goals'])
    seeds = {e['design_id'] for e in previous['archive']['evaluations'] if e['role']=='baseline_reference'}
    assert len(seeds)==2
    candidates = [e for e in previous['archive']['evaluations'] if e['role']=='candidate']
    assert any(e['parent_design_id'] in seeds for e in candidates)
    assert all(e['parent_design_id'] is None or e['parent_design_id'] in seeds for e in candidates)
    payload=plan(previous)
    planner=Planner(json.dumps(payload))
    result=st.revise(tmp_path/'first',output=tmp_path/'revision',planner=planner)
    assert result['status']=='plans_recorded' and len(planner.calls)==1
    assert st.load(tmp_path/'revision/raw_response.json')['response']==json.dumps(payload)
    assert not result['next_action_executed']
    assert planner.calls[0]['user_payload']['objectives']==previous['archive']['objectives']
    result=st.run_search(output=tmp_path/'second',cache_dir=tmp_path/'cache',
        previous_search=tmp_path/'first',plans=payload,max_forward_evaluations=8,max_new_evals=20,backend='numpy')
    assert result['status']=='search_complete',st.load(tmp_path/'second/input_failures.json')
    second=st.read_search(tmp_path/'second')
    assert set(previous['archive']['designs']).issubset(second['archive']['designs'])
    assert second['archive']['conditions']==previous['archive']['conditions']
    parent=payload['plans'][0]['parent_design_id']
    new_events=second['archive']['evaluations'][len(previous['archive']['evaluations']):]
    assert new_events and all(e['parent_design_id']==parent for e in new_events)
    rerun=run(tmp_path,output=tmp_path/'cached',max_forward_evaluations=0,max_new_evals=0)
    assert rerun['status']=='search_complete'
    assert rerun['metering']['certification_new_evaluations']==0
    assert rerun['metering']['seed_certification']['cache_hits']==2


def test_small_certification_budget_keeps_all_uncertified_candidates(tmp_path):
    result=run(tmp_path,max_new_evals=2)
    assert result['status']=='partial'
    assert result['metering']['certification_new_evaluations']==2
    assert result['uncertified_candidate_count']>0
    assert result['candidate_count']>2
    archive=st.load(tmp_path/'first/design_archive.json')
    assert len(archive['designs'])==2
    results=st.load(tmp_path/'first/execution_results.json')
    assert any(r['status']=='budget_exhausted' for r in results)
    assert len(st.load(tmp_path/'first/execution_inputs.json'))==2+result['candidate_count']


def test_zero_budget_does_not_claim_certification(tmp_path):
    result=run(tmp_path,max_new_evals=0,max_forward_evaluations=0)
    assert result['status']=='partial'
    assert result['certified_design_count']==0
    assert st.load(tmp_path/'first/design_archive.json') is None


def test_revision_invalid_response_saved_once_without_retry(tmp_path):
    run(tmp_path,max_forward_evaluations=0,max_new_evals=2)
    planner=Planner('{bad json')
    result=st.revise(tmp_path/'first',output=tmp_path/'revision',planner=planner)
    assert result['status']=='revision_failed'
    assert len(planner.calls)==1
    assert st.load(tmp_path/'revision/raw_response.json')['response']=='{bad json'
    assert not (tmp_path/'revision/refinement_plans.json').exists()


def test_tampered_diagnostics_prevents_model_call(tmp_path):
    run(tmp_path,max_forward_evaluations=0,max_new_evals=2)
    p=tmp_path/'first/physics_diagnostics.json'
    data=st.load(p); data[0]['diagnostics']['goals'][0]['delta']=123
    p.write_text(json.dumps(data))
    planner=Planner('{}')
    with pytest.raises(ValueError,match='binding mismatch'):
        st.revise(tmp_path/'first',output=tmp_path/'revision',planner=planner)
    assert not planner.calls


def test_plans_tolerate_imperfect_reasoning_but_preserve_hard_gates(tmp_path):
    run(tmp_path,max_forward_evaluations=0,max_new_evals=2)
    previous=st.read_search(tmp_path/'first')
    good=plan(previous)
    missing=copy.deepcopy(good);missing['plans'][0]['expected_goal_changes'].pop('G2')
    normalized,report=st._compile_plans(missing,previous)
    assert normalized['plans'][0]['expected_goal_changes']['G2']=='unknown'
    assert report['normalization_audit'][0]['warnings']
    bad=copy.deepcopy(good);bad['plans'][0]['parent_design_id']='invented'
    assert st._compile_plans(bad,previous)[1]['rejected_count']==1
    bad=copy.deepcopy(good);bad['plans'][0]['layers']=[]
    assert st._compile_plans(bad,previous)[1]['rejected_count']==1
    missing=copy.deepcopy(good);missing['plans'][0]['diagnosis']['observation_ids']=['goal:invented']
    normalized,report=st._compile_plans(missing,previous)
    assert normalized['plans'][0]['diagnosis']['verification']=='unverified_reasoning'
    assert normalized['plans'][0]['parent_design_id']==good['plans'][0]['parent_design_id']


def test_explicit_layerwise_branch_retains_parent(tmp_path):
    run(tmp_path,max_forward_evaluations=0,max_new_evals=2)
    previous=st.read_search(tmp_path/'first'); payload=plan(previous,action='refine_layers')
    result=st.run_search(output=tmp_path/'second',cache_dir=tmp_path/'cache',previous_search=tmp_path/'first',
        plans=payload,max_forward_evaluations=8,max_new_evals=20,backend='numpy')
    assert result['status']=='search_complete'
    archive=st.load(tmp_path/'second/design_archive.json')
    new_events=archive['evaluations'][len(previous['archive']['evaluations']):]
    assert all(e['family']['specification']['parameterization']=='layerwise_refinement' for e in new_events)
    assert all(e['parent_design_id']==payload['plans'][0]['parent_design_id'] for e in new_events)


def test_rank_diversifies_objective_extremes_and_preserves_all():
    objectives=[{'objective_id':'a','sense':'maximize'},{'objective_id':'b','sense':'maximize'}]
    candidates=[{'instance_id':str(i),'objectives':objectives,
        'numerical_search':{'uncertified_metrics':{'a':a,'b':b}}} for i,(a,b) in enumerate([(5,5),(10,0),(0,10),(1,1),(4,6)])]
    ranked=st.rank_candidates(candidates)
    assert [r['instance_id'] for r in ranked[:2]]==['1','2']
    assert ranked[-1]['instance_id']=='3'
    assert len(ranked)==len(candidates)


def test_cli_requires_explicit_execute_and_separate_budgets(tmp_path):
    assert st.main(['search','--output',str(tmp_path/'no')])==2
    assert not (tmp_path/'no').exists()
    assert st.main(['search','--execute','--output',str(tmp_path/'no')])==2


def test_refine_refuses_changed_inputs_and_polarization(tmp_path):
    run(tmp_path,max_forward_evaluations=0,max_new_evals=2)
    previous=st.read_search(tmp_path/'first'); payload=plan(previous)
    options=dict(output=tmp_path/'second',cache_dir=tmp_path/'cache',previous_search=tmp_path/'first',
                 plans=payload,max_forward_evaluations=8,max_new_evals=2,backend='numpy')
    with pytest.raises(ValueError,match='retain original inputs'):
        st.run_search(**options,wavelengths_nm=[450,550,650])
    with pytest.raises(ValueError,match='retain original diagnostic'):
        st.run_search(**options,include_polarization_channels=False)
    assert not (tmp_path/'second').exists()


def test_failure_reservation_is_explicit_without_unmetered_retry(tmp_path,monkeypatch):
    calls=[]
    def failed(*args,**kwargs):
        calls.append(kwargs['max_forward_evaluations'])
        raise RuntimeError('failed after unknown numerical work')
    monkeypatch.setattr(st.fo,'optimize_family',failed)
    result=run(tmp_path)
    assert result['status']=='partial' and calls==[12]
    assert result['metering']['search_metering_complete'] is False
    assert result['metering']['search_failed_job_reserved_rows']==12
    assert len(st.load(tmp_path/'first/design_archive.json')['designs'])==2


def test_mixed_plans_keep_valid_alias_and_unchosen_designs(tmp_path):
    run(tmp_path,max_forward_evaluations=0,max_new_evals=2)
    previous=st.read_search(tmp_path/'first')
    aliases=st._aliases(previous)
    parent_alias=next(iter(aliases['parents']))
    payload={'plans':[{'parent_design_id':'invented','action':'refine_family'},
                     {'parent_design_id':parent_alias,'action':'refine_family',
                      'diagnosis':'Look at measured spectral tradeoffs.'}]}
    planner=Planner(json.dumps(payload))
    result=st.revise(tmp_path/'first',output=tmp_path/'revision',planner=planner)
    assert result['status']=='plans_partial'
    plans=st.load(tmp_path/'revision/refinement_plans.json')
    assert len(plans['plans'])==1
    assert plans['plans'][0]['parent_design_id']==aliases['parents'][parent_alias]
    assert st.load(tmp_path/'revision/plan_report.json')['rejected_count']==1
    prompt=planner.calls[0]['user_payload']
    assert prompt['family_catalog'] and prompt['route_rationales'] and prompt['decision_clues']
    assert all('thicknesses_nm' in d for d in prompt['certified_designs'])
    result=st.run_search(output=tmp_path/'second',cache_dir=tmp_path/'cache',previous_search=tmp_path/'first',
        plans=plans,max_forward_evaluations=4,max_new_evals=10,backend='numpy')
    assert result['status']=='search_complete'
    after=st.read_search(tmp_path/'second')['archive']
    assert set(previous['archive']['designs']).issubset(after['designs'])


def test_candidate_cannot_change_physics_or_family_bounds_before_dispatch(tmp_path):
    run(tmp_path,max_forward_evaluations=0,max_new_evals=2)
    previous=st.read_search(tmp_path/'first')
    template=next(iter(previous['envelopes'].values()))
    route=previous['accepted']['routes'][0]
    bad=copy.deepcopy(template);bad['tmm_task']['stack']['layers'][0]['constant_n']=9
    with pytest.raises(ValueError,match='material identities'):
        st._validate_candidate(bad,template,route,'family')
    bad=copy.deepcopy(template);bad['tmm_task']['stack']['layers'][0]['thickness_nm']=100000
    with pytest.raises(ValueError,match='bounds'):
        st._validate_candidate(bad,template,route,'family')
    bad=copy.deepcopy(template);bad['tmm_task']['stack']['layers'][0]['thickness_nm']+=1
    with pytest.raises(ValueError,match='shared family'):
        st._validate_candidate(bad,template,route,'family')
    st._validate_candidate(bad,template,route,'layerwise_refinement')


def test_cross_route_certified_context_diagnostics_preserve_original_ids(tmp_path):
    run(tmp_path,max_forward_evaluations=0,max_new_evals=2)
    previous=st.read_search(tmp_path/'first')
    archive=copy.deepcopy(previous['archive'])
    ids=list(archive['designs'])
    archive['designs'][ids[1]]['certified_result']['route_id']='rediscovered_from_other_route'
    before=copy.deepcopy(archive)
    diagnostic=st._diagnostic(archive,ids[0],ids[1],inputs()['sheet']['subgoals'])
    assert archive==before
    assert diagnostic['original_route_ids']['candidate']=='rediscovered_from_other_route'
    assert diagnostic['parent_design_id'] is None
    assert diagnostic['comparison_baseline_design_id']==ids[0]
    assert all(g['status']=='observed' for g in diagnostic['diagnostics']['goals'])


def test_cli_revision_directory_roundtrip(tmp_path):
    run(tmp_path,max_forward_evaluations=0,max_new_evals=2)
    previous=st.read_search(tmp_path/'first')
    planner=Planner(json.dumps(plan(previous)))
    assert st.main(['revise','--previous-search',str(tmp_path/'first'),
                    '--output',str(tmp_path/'revision'),'--execute'],planner=planner)==0
    assert st.main(['refine','--previous-search',str(tmp_path/'first'),
                   '--revision-dir',str(tmp_path/'revision'),'--cache-dir',str(tmp_path/'cache'),
                   '--max-forward-evaluations','2','--max-new-evals','4','--backend','numpy',
                   '--execute','--output',str(tmp_path/'second')])==0
    assert len(planner.calls)==1
    assert st.load(tmp_path/'second/summary.json')['live_model_called'] is False


def test_rediscovered_seed_retains_latest_same_design_derivative(tmp_path,monkeypatch):
    run(tmp_path,max_forward_evaluations=0,max_new_evals=2)
    previous=st.read_search(tmp_path/'first')
    archive=previous['archive']; envelopes=previous['envelopes']
    design_id, other_id=list(archive['designs'])
    original=copy.deepcopy(envelopes[design_id]); other=copy.deepcopy(envelopes[other_id])
    result=copy.deepcopy(archive['designs'][design_id]['certified_result'])
    result['cache_hit']=True
    for index in (1,2):
        rediscovered=copy.deepcopy(original)
        rediscovered['instance_id']='rediscovered_'+str(index)
        rediscovered['numerical_search']={'parameterization':'family','evaluation_index':index,
            'local_derivative_summary':{'status':'local_derivative_uncertified',
                'goal_sensitivities':{'G1':{'fixture_derivative_per_nm':index}},
                'additional_forward_design_rows':0}}
        archive, actual_id=st._record(archive,envelopes,result,rediscovered,parent=design_id,
            family=archive['designs'][design_id]['family']['specification'],role='candidate')
        assert actual_id==design_id
    assert envelopes[design_id]['instance_id']==original['instance_id']
    assert envelopes[design_id]['tmm_task']==original['tmm_task']
    assert archive['designs'][design_id]['certified_result']==previous['archive']['designs'][design_id]['certified_result']
    assert envelopes[other_id]==other
    assert len(envelopes[design_id]['numeric_search_observations'])==2
    # Even copied seed-history metadata cannot be applied to a different design.
    copied=copy.deepcopy(envelopes[design_id])
    assert st._latest_local_derivative(copied,other_id)['status']=='unknown'
    previous['archive']=archive
    monkeypatch.setattr(st,'read_search',lambda directory:copy.deepcopy(previous))
    planner=Planner('{"plans":[]}')
    assert st.revise(tmp_path/'first',output=tmp_path/'revision',planner=planner)['status']=='plans_recorded'
    brief=next(d for d in planner.calls[0]['user_payload']['certified_designs'] if d['design_id']==design_id)
    summary=brief['local_derivative_summary']
    assert summary['source_instance_id']=='rediscovered_2'
    assert summary['observation_design_id']==design_id
    assert summary['goal_sensitivities']['G1']['fixture_derivative_per_nm']==2


def test_incomplete_model_finish_is_partial_without_retry(tmp_path):
    run(tmp_path)
    previous=st.read_search(tmp_path/'first')
    planner=Planner(plan(previous))
    planner.last_transport={'finish_reason':'length'}
    summary=st.revise(tmp_path/'first',output=tmp_path/'revision',planner=planner)
    assert summary['status']=='plans_partial'
    assert summary['generation_complete'] is False
    assert len(planner.calls)==1
    assert st.load(tmp_path/'revision/refinement_plans.json')['plans']
