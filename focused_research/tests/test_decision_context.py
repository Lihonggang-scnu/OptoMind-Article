"""No solver/model calls: real T027 derivative arithmetic and prompt fixtures."""
import copy
import json
import math
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from focused_research.decision_context import local_direction_facts, build_decision_context

GOALS = [{'objective_id':'G1','sense':'maximize'}, {'objective_id':'G2','sense':'maximize'}]


def summary(gradients, values=(50.,115.), bounds=((40.,80.),(70.,130.))):
    return {'status':'local_derivative_uncertified','goal_gradient_conflicts':{'variable_count':2},
        'goal_sensitivities':[{'objective_id':g['objective_id'],'top_variables':[
            {'name':n,'bounds_nm':list(b),'current_value_nm':v,'derivative_per_nm':d}
            for n,b,v,d in zip(('dH','dL'),bounds,values,ds)]} for g,ds in zip(GOALS,gradients)]}


def test_t027_p025_shared_direction_is_not_misreported_as_conflict():
    s=summary([[.001121475255596885,-.0002996934959851491],[-.004182752071748066,-.0003912799832644642]])
    before=copy.deepcopy(s); f=local_direction_facts(s,GOALS)
    assert s==before
    assert any(x['variable']=='dL' and x['direction']=='decrease' for x in f['same_direction_opportunities'])
    assert f['joint_direction']['status']=='local_common_ascent_candidate'
    assert f['additional_forward_design_rows']==0
    assert 'not independently certified' in ' '.join(f['limits'])


def test_t027_p013_joint_direction_exists_despite_coordinate_conflicts():
    f=local_direction_facts(summary([[.00462245383443984,.0014625564553968037],[-.006309235063629175,-.0015324245025003388]]),GOALS)
    assert not f['same_direction_opportunities']
    d=f['joint_direction']
    assert d['status']=='local_common_ascent_candidate'
    assert all(v>0 for v in d['improvement_rates_per_epsilon'].values())
    assert d['nm_per_unit_epsilon']['dH']<0 and d['nm_per_unit_epsilon']['dL']>0


def test_bounds_minimize_sign_and_missing_coordinates():
    s=summary([[-1.,1.],[-2.,1.]],values=(40.,130.))
    f=local_direction_facts(s,GOALS)
    assert not f['same_direction_opportunities']
    assert f['joint_direction']['status']=='not_found'
    minimize=[dict(g,sense='minimize') for g in GOALS]
    f=local_direction_facts(s,minimize)
    assert len(f['same_direction_opportunities'])==2
    s['goal_sensitivities'][1]['top_variables'].pop()
    f=local_direction_facts(s,GOALS)
    assert f['derivative_coverage']['available_complete_coordinates']==1
    assert f['variables'][1]['missing_goal_derivatives']==['G2']
    assert not any(x['variable']=='dL' for x in f['same_direction_opportunities'])


def test_unknown_invalid_and_no_direction_never_imply_impossibility():
    assert local_direction_facts({},GOALS)['status']=='unknown'
    f=local_direction_facts(summary([[1.,1.],[-1.,-1.]]),GOALS)
    assert f['joint_direction']['status']=='not_found'
    assert 'does not prove' in ' '.join(f['limits'])
    s=summary([[1.,float('nan')],[1.,2.]])
    f=local_direction_facts(s,GOALS)
    assert f['warnings']
    json.dumps(f,allow_nan=False)


def realistic_previous(count=62):
    """62 certified designs, 12 repeated layers, two gradients and s/p/subbands."""
    family={'route_id':'R1','parameterization':'family','family':{'layers':[
        {'material':'TiO2' if i%2==0 else 'SiO2','thickness_nm':'dH' if i%2==0 else 'dL'} for i in range(12)],
        'thickness_variables_nm':{'dH':[40,80],'dL':[70,130]}}}
    designs={}; diagnostics=[]; envelopes={}
    for i in range(count):
        did='design:'+format(i,'064x'); base='design:'+format(0,'064x')
        values=(49.5+i*.04,112.+i*.07)
        designs[did]={'family':{'family_key':'family:'+('a'*64),'specification':copy.deepcopy(family)},
            'physical_input':{'stack':{'layers':[{'thickness_nm':values[j%2]} for j in range(12)]}},
            'metrics':{'G1':{'value':.88+i*.0002},'G2':{'value':.84-i*.0003}},'parent_design_id':base if i else None}
        envelopes[did]=summary([[.004,.001],[-.006,-.0015]],values)
        goals=[]
        for gid in ('G1','G2'):
            goals.append({'observation_id':'goal:'+gid,'goal_id':gid,'status':'observed','baseline':.8,'candidate':.85,
                'delta':.05,'improvement':.05,'objective_direction':'gain',
                'subbands':[{'subband':n,'status':'observed','baseline':.8,'candidate':.85,'delta':.05,'direction':'gain','sampled_range_nm':[450,500]} for n in ('blue','middle','red')],
                'polarization':{'status':'observed','baseline':{'s_mean':.9,'p_mean':.7},'candidate':{'s_mean':.95,'p_mean':.75},'s_delta':.05,'p_delta':.05}})
        diagnostics.append({'diagnosis_id':'diagnosis:'+format(i,'064x'),'comparison_baseline_design_id':base,'design_id':did,'diagnostics':{'goals':goals}})
    previous={'archive':{'objectives':GOALS,'designs':designs,'pareto_design_ids':list(designs)},
        'envelopes':envelopes,'diagnostics':diagnostics,'accepted':{'routes':[{'id':'R1','claim':'A testable stack.'}], 'decision_clues':[]},
        'context':{'sheet':{'user_objective':'Maximize both original goals.','search_queries':['irrelevant prompt bulk']*5000},'constraints':{'max_layers':24}}}
    aliases={'parents':{'P%03d'%(i+1):d for i,d in enumerate(sorted(designs))},
        'diagnostics':{'D%03d'%(i+1):d['diagnosis_id'] for i,d in enumerate(diagnostics)}}
    return previous,aliases


def test_context_retains_every_parent_goal_and_binding_without_repeated_detail():
    p,a=realistic_previous(); before=copy.deepcopy(p)
    r=build_decision_context(p,a,lambda envelope,did: envelope)
    u=r['user_payload']
    assert p==before
    assert len(u['certified_designs'])==62 and len(u['family_catalog'])==1
    assert len(u['representative_decisions'])==8 and len(u['physics_diagnostics'])==8
    assert len(u['diagnostic_index'])==62
    assert u['objectives']==GOALS
    assert set(x['design_id'] for x in u['certified_designs'])==set(p['archive']['designs'])
    for d in u['physics_diagnostics']:
        assert a['diagnostics'][d['diagnosis_alias']]==d['diagnosis_id']
        assert {g['goal_id'] for g in d['goals']}=={'G1','G2'}
    assert len(json.dumps(u).encode('utf-8'))<70000
    assert all('thicknesses_nm' in d for d in u['representative_decisions'])
    assert all('thickness_reference' in d for d in u['certified_designs'])
    assert 'search_queries' not in u['task_sheet']
    assert build_decision_context(p,a,lambda e,d:e)==r


def test_126_parent_context_has_explicit_byte_budget_and_no_fabricated_tokens():
    p,a=realistic_previous(126)
    r=build_decision_context(p,a,lambda e,d:e)
    assert len(r['user_payload']['certified_designs'])==126
    assert len(json.dumps(r['user_payload']).encode('utf-8'))<105000
    assert 'token_count' not in r['selection_report']


def test_overflowed_normalized_derivative_is_omitted_without_nan():
    s=summary([[1e308,1.],[1.,1.]])
    f=local_direction_facts(s,GOALS)
    assert any('nonfinite normalized' in w for w in f['warnings'])
    assert all(v['name']!='dH' for v in f['variables'])
    json.dumps(f,allow_nan=False)


def test_original_question_and_extremes_survive_many_families():
    p,a=realistic_previous(20)
    p['context']['sheet']['original_question']='原始问题，保留全部条件。'
    for i,d in enumerate(p['archive']['designs'].values()):
        d['family']['family_key']='family%02d'%i
    r=build_decision_context(p,a,lambda e,d:e,detail_budget=2)
    selected=r['selection_report']['representative_design_ids']
    ids=list(p['archive']['designs'])
    assert ids[0] in selected and ids[-1] in selected
    assert r['user_payload']['task_sheet']['original_question']=='原始问题，保留全部条件。'
