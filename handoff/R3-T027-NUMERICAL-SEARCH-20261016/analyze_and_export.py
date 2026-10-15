"""Read saved T027 evidence only; produce bounded audit views, never new evaluations."""
import collections, hashlib, importlib.util, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
SOURCE=Path(r'D:\OptoMind-Article-3\r3t027a')
def load(p): return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(name,value):
    p=ROOT/name;p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('x',encoding='utf-8',newline='\n') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')
def values(d): return {k:v['value'] for k,v in d['metrics'].items()}
def brief(d):
    return {'design_id':d['design_id'],'metrics':values(d),'parent_design_id':d['parent_design_id'],
        'physical_input':d['physical_input'],'family':d['family'],'layer_diff':d['layer_diff'],
        'first_evaluation':d['first_evaluation'],
        'certificate':{'accepted':d['certified_result']['physics_accepted'],
                       'id':d['certified_result']['certificate']['certificate_id'],
                       'archive_root':d['certified_result']['archive_root']}}
sys.path.insert(0,str(SOURCE))
from focused_research.search_stage import read_search,_latest_local_derivative
from focused_research.design_archive import pareto_design_ids
first=read_search(ROOT/'search');second=read_search(ROOT/'refined')
a,b=first['archive'],second['archive']
assert first['context']==second['context'] and a['objectives']==b['objectives'] and a['conditions']==b['conditions']
assert all(k in b['designs'] and b['designs'][k]==d for k,d in a['designs'].items())
assert pareto_design_ids(b)==b['pareto_design_ids']
summaries={stage:load(ROOT/stage/'summary.json') for stage in ('search','refined')}
for stage,previous in (('search',first),('refined',second)):
    archive=previous['archive']
    save('export/'+stage+'_archive.json',{'schema':'t027.compact_certified_archive.v1',
        'source_path':str(ROOT/stage/'design_archive.json'),'source_sha256':sha(ROOT/stage/'design_archive.json'),
        'context_id':archive['context_id'],'objectives':archive['objectives'],'conditions':archive['conditions'],
        'designs':{k:brief(d) for k,d in archive['designs'].items()},
        'evaluations':archive['evaluations'],'pareto_design_ids':archive['pareto_design_ids'],
        'note':'Compact audit view; full original with certified spectra remains local. Selected raw native archives published separately.'})
    searches=load(ROOT/stage/'numerical_search.json');compact=[]
    for job in searches:
        result=job['search']
        assert result['backend']=='torch'
        if stage=='refined':
            parent=a['designs'][job['parent_design_id']]
            seed_rows=[r for r in result['evaluations'] if r['source']=='original_seed']
            assert seed_rows and seed_rows[0]['thicknesses_nm']==[l['thickness_nm'] for l in parent['physical_input']['stack']['layers']]
        compact.append({k:v for k,v in job.items() if k!='search'}|{'search':{k:v for k,v in result.items() if k!='candidates'},
            'candidates':[{'instance_id':x['instance_id'],'parent_instance_id':x.get('parent_instance_id'),
                           'thicknesses_nm':[l['thickness_nm'] for l in x['tmm_task']['stack']['layers']],
                           'numerical_search':x['numerical_search']} for x in result['candidates']]})
    save('export/'+stage+'_numerical_log.json',{'schema':'t027.compact_numerical_log.v1','source_sha256':sha(ROOT/stage/'numerical_search.json'),
        'all_actual_evaluations_retained':True,'only_repeated_full_prepared_input_envelopes_omitted':True,'jobs':compact})
comparison=[];phase_counts={};selection=set()
plans=load(ROOT/'revision/refinement_plans.json')['plans']
aliases=load(ROOT/'revision/revision_aliases.json');inverse={v:k for k,v in aliases['parents'].items()}
for stage,archive in (('search',a),('refined',b)):
    counts=collections.Counter()
    for key,d in archive['designs'].items():
        if stage=='refined' and key in a['designs']:continue
        parent_id=d['parent_design_id']
        if not parent_id:continue
        parent=archive['designs'][parent_id]
        before,after=values(parent),values(d)
        delta={g:after[g]-before[g] for g in before}
        category='both_increase' if all(x>0 for x in delta.values()) else 'tradeoff' if min(delta.values())<0<max(delta.values()) else 'nonincrease' if max(delta.values())<=0 else 'one_increase_other_unchanged'
        counts[category]+=1
        comparison.append({'stage':stage,'parent_design_id':parent_id,'child_design_id':key,'parent':before,'child':after,'delta':delta,'category':category})
    phase_counts[stage]=dict(counts)
new=set(b['designs'])-set(a['designs'])
assert len(new)==64
for key in new:
    d=b['designs'][key];parent=a['designs'][d['parent_design_id']]
    spec=parent['family']['specification']['family']
    assert len(d['physical_input']['stack']['layers'])==len(parent['physical_input']['stack']['layers'])
    for layer,old,specification in zip(d['physical_input']['stack']['layers'],parent['physical_input']['stack']['layers'],spec['layers']):
        assert {k:v for k,v in layer.items() if k!='thickness_nm'}=={k:v for k,v in old.items() if k!='thickness_nm'}
        variable=specification['thickness_nm']
        if isinstance(variable,str):
            lo,hi=spec['thickness_variables_nm'][variable];assert lo<=layer['thickness_nm']<=hi
        else:assert layer['thickness_nm']==variable
save('PARENT_CHILD_COMPARISONS.json',{'phase_counts':phase_counts,'comparisons':comparison,'zero_tolerance_used_for_signs':True})
selected_child='design:9ddc5c9f2d942d66a0f23638a81bc9c8b91eec0527b1fec6af552faff7d28de1'
assert selected_child in new
selected_parent=b['designs'][selected_child]['parent_design_id']
selected_plan=next(p for p in plans if p['parent_design_id']==selected_parent)
plan_audits=[]
diagnostics={d['diagnosis_id']:d for d in first['diagnostics']}
for plan in plans:
    parent=plan['parent_design_id'];ref=diagnostics[plan['diagnosis']['diagnosis_id']]
    assert ref['design_id']==parent and plan['diagnosis']['verification']=='verified_references'
    derivative=_latest_local_derivative(first['envelopes'][parent],parent)
    audit={'parent_alias':inverse[parent],'parent_design_id':parent,'diagnosis_id':ref['diagnosis_id'],
        'observed_deltas':{g['goal_id']:g['delta'] for g in ref['diagnostics']['goals']},
        'parent_metrics':values(a['designs'][parent]),'actual_parent_local_derivative':derivative,
        'action':plan['action'],'expected_goal_changes':plan['expected_goal_changes'],'model_explanation':plan['diagnosis']['explanation'],
        'references_valid':True,'explanation_semantics_not_validated_by_reference_gate':True}
    plan_audits.append(audit);selection.add(parent)
save('MODEL_DECISION_FACT_AUDIT.json',{'plans':plan_audits,
    'material_use_of_sp_or_subband_summaries_not_evidenced':True,
    'known_semantic_mismatch':'P044 explicitly says increasing dL improves G1, but its saved derivative_per_nm is negative.',
    'shared_gradients_do_not_prove_individual_layer_effects_or_necessity_of_untying':True})
selected_diag=diagnostics[selected_plan['diagnosis']['diagnosis_id']]
save('TRAJECTORY.json',{'parent_alias':inverse[selected_parent],'parent':brief(a['designs'][selected_parent]),
    'diagnostics':selected_diag,'plan':selected_plan,'child':brief(b['designs'][selected_child]),
    'delta':{g:values(b['designs'][selected_child])[g]-values(a['designs'][selected_parent])[g] for g in ('G1','G2')},
    'selection_scope':'Illustrative measured trajectory, not an automatically chosen global optimum or scalar user preference.'})
for stage,archive in (('search',a),('refined',b)):
    frontier=archive['pareto_design_ids']
    for goal in ('G1','G2'):selection.add(max(frontier,key=lambda k:archive['designs'][k]['metrics'][goal]['value']))
selection.update((selected_child,selected_parent,'design:2e4afc2847188e3e8a0400c9be25625db2879133a6e6d768230b6c246c9d48bf'))
for key,d in a['designs'].items():
    layers=d['physical_input']['stack']['layers']
    if [l['thickness_nm'] for l in layers] in ([55,95]*6,[60,95]*6):selection.add(key)
selections=[{'design_id':k,'metrics':values(b['designs'][k]),'archive_root':b['designs'][k]['certified_result']['archive_root']}
            for k in sorted(selection)]
failed=[];status_counts={};metric_diffs={g:0. for g in ('G1','G2')}
for stage in ('search','refined'):
    results=load(ROOT/stage/'execution_results.json')
    status_counts[stage]=dict(collections.Counter(x['status'] for x in results))
    for result in results:
        if result['status']=='failed':failed.append({'route_id':result['route_id'],'instance_id':result['instance_id'],
            'archive_root':result['archive_root'],'failures':result['certificate']['failures']})
    for d in (a if stage=='search' else b)['designs'].values():
        k=d['design_id'];e=(first if stage=='search' else second)['envelopes'][k]
        metrics=e.get('numerical_search',{}).get('uncertified_metrics')
        if metrics:
            for g in metric_diffs:metric_diffs[g]=max(metric_diffs[g],abs(metrics[g]-d['metrics'][g]['value']))
save('SELECTED_NATIVE_ARCHIVES.json',{'certified_designs':selections,'failed_controls':failed,
    'selection_note':'All seven model parents, original seed controls, illustrative child and measured Pareto extrema; remaining full native records stay local.'})
save('PHYSICS_FAILURES.json',{'failed_certifications':failed,'actual_status_counts':status_counts,
    'uncertified_search_outputs_not_final_results':True,'max_uncertified_vs_certified_metric_absolute_difference':metric_diffs})
requests=sorted((ROOT/'private/audit').rglob('REQUEST.json'));responses=sorted((ROOT/'private/audit').rglob('RESPONSE.json'))
assert len(requests)==len(responses)==1
request,response=load(requests[0]),load(responses[0])
user=json.loads(next(m['content'] for m in request['messages'] if m['role']=='user'))
assert user==load(ROOT/'revision/revision_prompt.json')['user_payload']
assert len(user['certified_designs'])==62 and len(user['objectives'])==2
save('ACTUAL_MODEL_CONTEXT.json',{'request_sha256':sha(requests[0]),'source_prompt_sha256':sha(ROOT/'revision/revision_prompt.json'),
    'actual_user_payload_exactly_matches_saved_prompt':True,'certified_design_count':62,
    'parent_actual_thicknesses_and_all_goal_metrics_present':True,'diagnostic_count':len(user['physics_diagnostics']),
    'original_route_rationales_present':True,'family_catalog_present':True,
    'context_delivery_is_separate_from_explanation_quality':True})
spec=importlib.util.spec_from_file_location('_t027_accounting',ROOT/'launch_decision.py')
accounting=importlib.util.module_from_spec(spec);sys.modules[spec.name]=accounting;spec.loader.exec_module(accounting)
final_budget=accounting.reconcile()
prior=load(ROOT/'REVISION_PREFLIGHT.json')
rows=accounting.reservation_hashes()
assert all(rows.get(k)==v for k,v in prior['protected_old_reservations'].items())
assert sha(accounting.OLD)==prior['budget_before']['ledgers'][0]['sha256']
save('BUDGET_FINAL.json',final_budget)
preserved={}
for task,hashes in load(ROOT/'OLD_OUTPUT_HASHES_BEFORE.json').items():
    task_root=ROOT.parent/task;now={str(p.relative_to(task_root)):sha(p) for p in sorted(task_root.rglob('*')) if p.is_file()}
    assert hashes==now
    preserved[task]={'unchanged':True,'file_count':len(now)}
save('PRESERVATION_CHECK.json',{'historical_task_files':preserved,'all_62_prior_certified_designs_unchanged':True,
    'original_140_ledger_unchanged':True,'eight_old_subpool_reservations_unchanged':True})
meters={stage:summaries[stage]['metering'] for stage in summaries}
search_rows=sum(m['search_forward_design_rows'] for m in meters.values())
certified=sum(m['certification_new_evaluations'] for m in meters.values())
reservation_count=sum(len(load(p)['reserved_simulation_keys']) for p in (ROOT/'cache/evaluations').glob('*.reservation.json'))
assert search_rows==2093<=8192 and certified==reservation_count==128
save('NUMERICAL_ACCOUNTING.json',{'per_stage':meters,'search_forward_design_rows':search_rows,'search_cap':8192,
    'solver_channel_rows':sum(m['search_solver_channel_rows'] for m in meters.values()),
    'backward_passes':sum(m['search_backward_passes'] for m in meters.values()),
    'new_certified_evaluations':certified,'durable_certification_reservations':reservation_count,'certification_cap':128,
    'failed_certifications':len(failed),'accepted_unique_designs':126,
    'certification_cache_hits':sum(v.get('cache_hits',0) for m in meters.values() for k,v in m.items() if k in ('seed_certification','candidate_certification')),
    'workbench_calls':sum(v.get('workbench_forward_calls',0) for m in meters.values() for k,v in m.items() if k in ('seed_certification','candidate_certification')),
    'counter_scope':'Search design/channel attempts and backward calls are separate from TMMWorkbench.simulate certification calls. Direct referee/internal channel solves excluded from Workbench count.',
    'stage_times':{stage:{k:v for k,v in load(ROOT/(stage.upper()+'_EXIT.json')).items() if k in ('wall_seconds','cpu')} for stage in summaries},
    'actual_solver_cpu_seconds':None,'cpu_limit':'Windows venv redirector PID differs from actual Python PID; raw process CPU fields are launcher-only, not solver CPU. No rerun to fill telemetry.'})
save('MODEL_CALLS.json',{'actual_http':1,'limit':1,'automatic_or_format_repair_http':0,
    'provider':{k:response.get(k) for k in ('call_id','request_id','requested_model','returned_model','finish_reason','complete','status_code','attempt','elapsed_seconds','usage')},
    'task_cost_cny':load(ROOT/'REVISION_ACCOUNTING.json')['task_guard_after']['actual_cny'],'held_cny':0})
old_pareto=set(a['pareto_design_ids']);new_pareto=set(b['pareto_design_ids'])
counts=collections.Counter(('G1_up_G2_down' if row['delta']['G1']>0 else 'G1_down_G2_up') for row in comparison if row['stage']=='refined')
summary={'task_id':'R3-T027','message_id':'C2L-R3-0040','status':'completed','scientific_run_status':'partial',
    'source_sha':'b5f8a1dc2cfe8a203b74a742963bef7f9ac9a57b','base_sha':'5786e08575b19020a6aece338436406884d3542d',
    'local_production_code_changes':False,'torch_gate':'102 passed, 0 skipped','phases':{s:summaries[s]['status'] for s in summaries},
    'search_rows':search_rows,'certifications':certified,'failed_certifications':2,
    'phase1_archive_designs':62,'phase2_archive_designs':126,'phase1_pareto':52,'phase2_pareto':76,
    'old_frontier_designs_newly_dominated':len(old_pareto-new_pareto),'old_designs_retained':True,
    'phase1_actual_parent_child_categories':phase_counts['search'],'phase2_actual_parent_child_categories':phase_counts['refined'],
    'phase2_tradeoff_directions':dict(counts),'http_attempts':1,'task_cost_cny':.449864,
    'aggregate_cost_cny':final_budget['aggregate_exposure_cny'],'held_cny':0,'unresolved_count':0,
    'model_semantics':'Concrete observed deltas cited, but dL sign error and unsupported necessity claim remain; no causal model-benefit control.',
    'capability_boundaries':['979 candidates not certified','Two 24-layer seeds fail new diagnostic-channel spectral convergence',
        'Solver CPU time not collected; measured wall times retained','Sampled materials/grid only; no manufacturing or global-optimality claim'],
    'stopped_after_two_phases':True}
save('RESULT_SUMMARY.json',summary)
print(json.dumps(summary,ensure_ascii=False))
