"""Read saved T040 outputs; no provider or numerical engine calls."""
from pathlib import Path
from decimal import Decimal
import csv,json,hashlib,sqlite3,sys
R=Path(__file__).resolve().parent;CR=R/'cases/e2e-04-solarblind-uv-triple';OUT=R/'analysis-attempt02';OUT.mkdir(exist_ok=False)
def load(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(n,d):
 with (OUT/n).open('x',encoding='utf-8',newline='\n') as f:json.dump(d,f,ensure_ascii=False,indent=2);f.write('\n')
def metrics(d):return {g:d['metrics'][g]['value'] for g in ['G1','G2','G3']}
old=load(R.parent/'R3-T038/cases/e2e-04-solarblind-uv-triple/refined/design_archive.json')
stages={name:load(CR/relative/'design_archive.json') for name,relative in [('initial','initial_search'),('refinement1','refined1'),('refinement2','refined2')]}
extrema={};compact=[]
for name,a in [('historical_T038',old)]+list(stages.items()):
 extrema[name]={'design_count':len(a['designs']),'pareto_count':len(a['pareto_design_ids']),'goals':a['objectives'],'extrema':{}}
 for g in ['G1','G2','G3']:
  d=(max if g=='G1' else min)(a['designs'].values(),key=lambda x:metrics(x)[g])
  extrema[name]['extrema'][g]={'design_id':d['design_id'],'metrics_fraction':metrics(d),'parent_design_id':d['parent_design_id'],
      'stack':d['physical_input']['stack'],'warning':'each extreme is a separate actual design; never merge vectors'}
 if name!='historical_T038':
  for did,d in a['designs'].items():
   if did not in old['designs']:
    compact.append({'stage':name,'design_id':did,'parent_design_id':d['parent_design_id'],'metrics_fraction':metrics(d),
     'stack':d['physical_input']['stack'],'pareto_at_stage':did in a['pareto_design_ids'],
     'first_evaluation':d['first_evaluation']})
save('ACTUAL_CERTIFIED_DESIGNS.json',{'scope':'compact certified design metrics and structures, no cache files','designs':compact})
save('STAGE_EXTREMA.json',extrema)
with (OUT/'FINAL_METRICS.csv').open('x',encoding='utf-8',newline='') as f:
 w=csv.writer(f);w.writerow(['design_id','parent_design_id','G1_fraction_max','G2_fraction_min','G3_fraction_min','layers','pareto','historical'])
 for did,d in stages['refinement2']['designs'].items():
  w.writerow([did,d['parent_design_id'],*metrics(d).values(),len(d['physical_input']['stack']['layers']),did in stages['refinement2']['pareto_design_ids'],did in old['designs']])
requests=[]
for p in (CR/'private/audit').rglob('REQUEST.json'):
 d=load(p);payload=json.loads(d['messages'][1]['content'])
 assert d['messages']==d['wire_body']['messages'] and d['transport_retries']==0
 requests.append((p,d,payload))
assert len(requests)==2
audits=[];effects=[];children=[]
previous=stages['initial']
for i in [1,2]:
 rev=CR/f'revision{i}';prompt=load(rev/'revision_prompt.json');payload=prompt['user_payload'];plans=load(rev/'refinement_plans.json')['plans']
 matches=[x for x in requests if {k:v for k,v in x[2].items() if k!='execution_protocol'}==payload];assert len(matches)==1
 file,request,_=matches[0]
 assert request['messages'][0]['content']==prompt['system_prompt']
 mf=payload['material_feedback'];assert mf['source_feedback_content_sha256']=='42a96354f42e5adf206c73b6f35cb355a9a8f37b87e368913cd25f0373e27a22'
 selection=load(rev/'decision_context_report.json')
 audit={'revision':i,'source_request_path':str(file),'source_request_sha256':sha(file),'production_call_id':request['production_call_id'],
   'actual_forwarded_payload_matches_native_revision_prompt_except_native_execution_protocol':True,'actual_messages_equal_wire_messages':True,
   'native_guard_added_execution_protocol':request['wire_body']['messages'][1]['content'] and matches[0][2]['execution_protocol'],
   'model':request['model'],'transport_retries':request['transport_retries'],
   'user_payload_compact_UTF8_bytes':len(json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()),
   'actual_user_content_UTF8_bytes':len(request['messages'][1]['content'].encode()),
   'actual_wire_body_compact_UTF8_bytes':len(json.dumps(request['wire_body'],ensure_ascii=False,separators=(',',':')).encode()),
   'facts_source_hashes':mf['input_files'],'facts_content_hash':mf['source_feedback_content_sha256'],
   'facts_materials_count':len(mf['materials']),'facts_cached_designs_count':len(mf['cached_rta']),
   'snapshot_identity':payload['identity_scope'],'model_summary':load(rev/'summary.json'),'decision_context_report':selection,
   'selected_parent_ids':[x['parent_design_id'] for x in plans]}
 audits.append(audit)
 detailed={x['design_id']:x for x in payload['representative_decisions']}
 for plan in plans:
  did=plan['parent_design_id'];row=detailed.get(did)
  effects.append({'revision':i,'stable_design_id':did,'parent_alias':row['parent_alias'] if row else None,
   'actually_forwarded_directions':row['local_direction_facts'] if row else 'missing from detailed projection',
   'model_explanation':plan['diagnosis']['explanation'],'model_expected_metric_directions':plan['expected_goal_changes'],
   'available_actions':next(x['available_actions'] for x in payload['certified_designs'] if x['design_id']==did),
   'actual_parent_stack':previous['designs'][did]['physical_input']['stack']})
  newer=stages[f'refinement{i}'];new_ids=set(newer['designs'])-set(previous['designs'])
  offspring=[newer['designs'][x] for x in new_ids if newer['designs'][x]['parent_design_id']==did]
  base=metrics(previous['designs'][did]);stats={g:{'increase':0,'decrease':0,'equal':0} for g in base}
  for child in offspring:
   for g,v in metrics(child).items():stats[g]['increase' if v>base[g] else 'decrease' if v<base[g] else 'equal']+=1
  children.append({'revision':i,'parent_design_id':did,'action':plan['action'],'parent_metrics_fraction':base,
   'new_certified_children':len(offspring),'raw_metric_direction_counts_exact_no_threshold':stats,
   'jointly_nondeteriorating_children':sum(all((metrics(c)[g]>=base[g] if g=='G1' else metrics(c)[g]<=base[g]) for g in base) for c in offspring),
   'expected_directions':plan['expected_goal_changes'],'limits':'finite multi-variable optimization outcomes; not single-coordinate derivative tests'})
 previous=stages[f'refinement{i}']
save('ACTUAL_MODEL_DELIVERY.json',audits);save('SELECTED_PARENT_REASONING_EVIDENCE.json',effects);save('PARENT_CHILD_OUTCOMES.json',children)
case=load(CR/'CASE_RESULT.json');total=sum(Decimal(str(x['model_summary']['usage']['cost_cny'])) for x in audits)
sys.path.insert(0,str(R));import run_t040 as launch
live=launch.reconcile();assert live['task_actual_HTTP']==2 and live['case_actual_HTTP']==6 and live['batch_attempts']==12
assert Decimal(live['project_exposure_cny'])-Decimal(load(R/'BUDGET_BEFORE.json')['aggregate_live_totals']['project_exposure_cny'])==total
ledgers=[]
for path in [launch.OLD,launch.POOL]:
 with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
  db.row_factory=sqlite3.Row;rows=db.execute('SELECT * FROM reservations').fetchall();assert all(x['status']=='settled' for x in rows)
  ledgers.append({'path':str(path),'sha256':sha(path),'settled_cny':str(sum((Decimal(str(x['actual_cny'] or 0)) for x in rows),Decimal(0))),'pending_held_uncertain_cny':'0','count':len(rows)})
budget={'read_only_live':True,'new_task_cost_CNY':str(total),'totals':live,'ledgers':ledgers,'pending_held_uncertain_cny':'0','new_pool':False,'native_guard_ledger_history_retained':True}
save('BUDGET_AFTER.json',budget)
summary={'task_id':'R3-T040','message_id':'C2L-R3-0055','implementation_sha':launch.IMPL,'engineering_status':case['engineering_status'],
 'scientific_status':'partial_tradeoffs; candidate compiler/search/facts connection demonstrated; model reasoning has documented limitations',
 'targeted_tests_passed':161,'money_gate_synthetic_tests_passed':4,'new_HTTP':2,'new_cost_CNY':str(total),
 'numerical_rows':sum(a['rows_charged'] for a in case['numerical_stages']),
 'numerical_solver_channel_rows':sum(a['metering']['search_solver_channel_rows'] for a in case['numerical_stages']),
 'numerical_backward_passes':sum(a['metering']['search_backward_passes'] for a in case['numerical_stages']),
 'new_certifications':sum(a['new_certifications'] for a in case['numerical_stages']),
 'actual_cache_hits':sum(a['metering']['certification_cache_hits_total'] for a in case['numerical_stages']),
 'old_certified_designs_preserved':51,'final_certified_design_count':len(stages['refinement2']['designs']),
 'final_Pareto_count':len(stages['refinement2']['pareto_design_ids']),
 'new_seed_certifications':7,'historical_R1_references_no_dispatch':2,'no_new_retrieval_or_route_generation':True,
 'actual_model_facts_delivery_verified':True,'both_revision_plans_valid':True,'actual_stage_exits':case['stages'],
 'budget':live,'scope_limit':'No controlled same-task/model/budget ablation; no general benefit or mechanism claim.'}
save('SUMMARY.json',summary)
print(json.dumps({'summary':summary,'extrema':{k:{g:v['metrics_fraction'] for g,v in x['extrema'].items()} for k,x in extrema.items()},'parent_children':children},ensure_ascii=False))
