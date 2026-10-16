"""Export a stopped T038 run without executing models or numerical work."""
from pathlib import Path
from decimal import Decimal
import json,hashlib,shutil,subprocess,sys
R=Path(__file__).resolve().parent;S=Path(r'D:\OptoMind-Article-3\r3t038a');O=S/'handoff/R3-T038-UV-20261016';CASE='e2e-04-solarblind-uv-triple';C=R/'cases'/CASE
load=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
def cp(p,q):q.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,q)
assert not O.exists(),'immutable previous packet'
actual=load(C/'CASE_RESULT.json')
live=json.loads(subprocess.check_output([sys.executable,'-B','-X','utf8',str(R/'probe.py')],text=True,encoding='utf-8'))
save(R/'BUDGET_AFTER.json',live)
O.mkdir(parents=True);(O/'.gitattributes').write_text('* -text\n',encoding='ascii')
refs=[];stages={};certified={}
for sub in ['recovered_round1','recovered','retrieval','routes','search','revision','refined']:
 d=C/sub
 if not d.exists():continue
 if (d/'summary.json').exists():stages[sub]=load(d/'summary.json')
 for p in sorted(d.rglob('*')):
  if not p.is_file() or any(x in p.parts for x in ['cache','private','__pycache__']):continue
  excluded=p.name in {'design_archive.json','design_envelopes.json','execution_inputs.json','execution_results.json','numerical_search.json','candidate_ancestry.json','evidence_items.full.json','evidence_items.source.json'}
  export=not excluded and p.suffix in {'.json','.txt','.log','.md','.csv'}
  if export:cp(p,O/'case'/sub/p.relative_to(d))
  refs.append({'local_path':str(p),'sha256':sha(p),'size_bytes':p.stat().st_size,'export_present':export})
 if (d/'design_archive.json').exists():
  a=load(d/'design_archive.json');ds=a['designs'];certified[sub]=a
  flat={k:{'design_id':k,'parent_design_id':v.get('parent_design_id'),'metrics':v['metrics'],'physical_input':v['physical_input'],'layer_diff':v.get('layer_diff'),'first_evaluation':v.get('first_evaluation')} for k,v in ds.items()}
  save(O/'science'/(sub+'_CERTIFIED_DESIGNS.json'),{'objectives':a['objectives'],'conditions':a['conditions'],'context_id':a['context_id'],'claim_scope':a['claim_scope'],'pareto_design_ids':a['pareto_design_ids'],'designs':flat})
  chosen=set()
  for goal in a['objectives']:
   if ds:chosen.add((min if goal['sense']=='minimize' else max)(ds,key=lambda k:ds[k]['metrics'][goal['objective_id']]['value']))
  chosen.update(v.get('parent_design_id') for k,v in ds.items() if k in chosen and v.get('parent_design_id') in ds)
  save(O/'science'/(sub+'_REPRESENTATIVE_CERTIFIED_RESULTS.json'),{'selection':'Actual per-goal extrema and parents; separate vectors, no combined fictitious optimum.','designs':{k:ds[k] for k in sorted(chosen)}})
if (C/'routes/evidence_items.json').exists():
 selected=load(C/'routes/evidence_items.json')
 for stage in ['search','refined']:
  p=C/stage/'evidence_items.json'
  if p.exists():assert load(p)==selected,'downstream selected evidence differs'
for p in C.glob('*'):
 if p.is_file() and p.suffix in {'.json','.log'}:cp(p,O/'case'/p.name)
for folder in ['guard_audit','atomic_reservations']:
 for p in (C/folder).glob('*.json'):cp(p,O/'case'/folder/p.name)
for name in ['REQUEST.json','RESPONSE.json']:
 for p in (C/'private/audit').rglob(name):cp(p,O/'model'/(p.parent.name+'_'+name))
for p in (C/'private/audit').rglob('*.raw'):
 cp(p,O/'model'/'provider_raw'/p.name)
 refs.append({'local_path':str(p),'sha256':sha(p),'size_bytes':p.stat().st_size,'export_present':True})
for folder in ['preflight','material-preflight','local_tools','quality-review']:
 for p in (R/folder).rglob('*'):
  if p.is_file() and p.suffix in {'.json','.log','.md','.txt','.py'} and '__pycache__' not in p.parts:cp(p,O/p.relative_to(R))
for p in R.glob('*'):
 if p.is_file() and p.suffix in {'.json','.log','.py','.md','.txt'} and not p.name.endswith('.local-only') and p.name not in {'RESULT.json','COMPLETED.json','BLOCKED.json','DELIVERY_VERIFIED.json'}:cp(p,O/'local_execution'/p.name)
save(O/'FULL_LOCAL_ARCHIVE_REFERENCES.json',refs)
rows=sum(x.get('metering',{}).get('search_forward_design_rows',0) for x in stages.values());newcert=sum(x.get('metering',{}).get('certification_new_evaluations',0) for x in stages.values())
assert rows<=4096 and newcert<=64 and len(live.get('task_calls',[]))<=4
atomic=[load(p) for p in (C/'atomic_reservations').glob('*.json')]
assert len(atomic)==len(live.get('task_calls',[])) and all(x['both_ledgers_locked_across_check_and_reservation'] for x in atomic)
project=sum((Decimal(x['settled_cny']) for x in live['ledgers']),Decimal(0));exposure=sum((Decimal(x['pending_held_uncertain_cny']) for x in live['ledgers']),Decimal(0))
summary={'task_id':'R3-T038','in_reply_to':'C2L-R3-0053','case_id':CASE,'continuation_of':['R3-T032','R3-T036','R3-T037'],'status':'blocked' if actual['engineering_status'].startswith('blocked') or exposure else 'completed','implementation_sha':'366f3faa70217b6209afb1162bac6759765cd628','source_core_modified':False,'actual_result':actual,'targeted_tests_passed':126,'local_budget_fixture_tests_passed':2,'new_actual_http_attempts':len(live.get('task_calls',[])),'new_task_settled_cny':live['new_task_settled_cny'],'project_settled_cny':str(project),'pending_held_uncertain_cny':str(exposure),'stages':stages,'search_forward_design_rows':rows,'new_certifications':newcert,'actual_atomic_native_reservations_verified':len(atomic),'same_original_case_cap_cny':8,'same_original_T032_batch_cap_cny':24,'same_original_T032_batch_http_cap':12,'project_cap_cny':220,'limits_scope':'All original case history and liabilities, not a new allowance.','complete_originals':str(R),'compact_export_is_not_full_read_search_snapshot':True,'scientific_scope':'Finite sampled-grid certified observations; no global optimum, mechanism, fabrication or controlled benefit proof.'}
save(R/'RUN_SUMMARY.json',summary);save(O/'RUN_SUMMARY.json',summary)
summary.update(same_original_case_cumulative_HTTP=2+len(live.get('task_calls',[])),same_original_case_cumulative_cost_cny=actual['budget_after']['case_exposure_cny'],same_original_T032_batch_cumulative_HTTP=actual['budget_after']['batch_attempts'],same_original_T032_batch_cumulative_cost_cny=actual['budget_after']['three_case_exposure_cny'],original_planning_reused_without_new_HTTP=True)
save(R/'RUN_SUMMARY.json',summary);save(O/'RUN_SUMMARY.json',summary)
cp(R/'REPORT.md',O/'REPORT.md')
save(O/'FILE_SHA256.json',{str(p.relative_to(O)).replace('\\','/'):{'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(O.rglob('*')) if p.is_file()})
print(json.dumps({'packet':str(O),'status':summary['status'],'HTTP':summary['new_actual_http_attempts'],'search_rows':rows,'new_certifications':newcert,'new_cost':summary['new_task_settled_cny'],'project':str(project),'outstanding':str(exposure)},ensure_ascii=False))
