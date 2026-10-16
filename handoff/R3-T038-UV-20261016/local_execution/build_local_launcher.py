"""Original audited flow, with explicit recovered-sheet and cumulative bindings."""
from pathlib import Path
import shutil,ast,json,sqlite3,importlib.util,sys,hashlib
from decimal import Decimal
R=Path(__file__).resolve().parent;P=R.parent/'R3-T037';L=R/'local_tools';L.mkdir(exist_ok=True)
shutil.copyfile(P/'local_tools/task_budget_guard_220.py',L/'task_budget_guard_220.py');shutil.copyfile(P/'CASE_HISTORY_FOR_GUARD.json',R/'CASE_HISTORY_FOR_GUARD.json')
s=(P/'local_tools/aggregate_budget.py').read_text();anchor="TASK_IDS['R3-T037:'+CASES[1]]=CASES[1]";assert s.count(anchor)==1
s=s.replace(anchor,anchor+"\nTASK_IDS['R3-T038:'+CASES[1]]=CASES[1]").replace('shared T032/T036/T037 original','shared T032/T036/T037/T038 original');(L/'aggregate_budget.py').write_text(s,encoding='utf-8')
s=(P/'run_t037.py').read_text(encoding='utf-8').replace('r3t037a','r3t038a').replace('49861e169084976069d9ef1008e4fafc84b86bb8','366f3faa70217b6209afb1162bac6759765cd628').replace('t037_','t038_').replace("'R3-T037:'+case","'R3-T038:'+case")
start=s.index('def preflight(case):');end=s.index('def audited_guard(',start)
s=s[:start]+'''def verify_recovery(case):
 st=importlib.import_module('focused_research.search_stage');d=R/'cases'/case/'recovered'
 for name,h in load(d/'manifest.json').items():assert st.digest(st.load(d/name))==h,'recovery manifest mismatch'
 binding=load(d/'recovery.json');sheet=load(d/'task_sheet.json');planning=R.parent/'R3-T037/cases'/case/'plan/planning.json'
 assert sha(planning)=='04d04afb21e081d0493b95af3e5f25449f45a0bbad2aa558680baac60a6d26e2'
 row=[x for x in load(planning)['rounds'] if x['round']==2];assert len(row)==1
 assert binding['case_id']==case and binding['source_commit']=='028b4955f75fb6e421e0e727c7451d38431b7970' and binding['source_round']==2
 assert binding['source_planning_sha256']==sha(planning) and binding['source_response_sha256']==st.digest(row[0]['raw_response'])
 assert load(d/'raw_response.json')==row[0]['raw_response']
 assert binding['task_sheet_sha256']==st.digest(sheet)=='70754cccfea49e884680ec01c7c3012db6d68862a8428ec646aa34a113a90893'
 assert st.digest(load(d/'query_plan.json'))=='e8e0d0e716e49f048d3313d9317fc3020921379df6eecfce09719522b4d2ef7e'
 assert binding['original_question_sha256']==hashlib.sha256(sheet['original_question'].encode()).hexdigest()=='a072e2f7732cc73738382bb04f916bcd0a1f4814c526cb9c022290cd6bd73d12'
 assert binding['new_model_calls']==0 and binding['physics_executed'] is False
 return sheet,binding

def preflight(case):
 assert case==CASES[1],'Only original case04 continuation is authorized'
 runner,origins=reused.bind();state=load(R.parents[1]/'state.json')
 assert state['processed']['R3-T037::C2L-R3-0052']['status']=='blocked'
 assert state['processed']['R3-T038::C2L-R3-0053']['claim_readback_verified'] is True
 assert load(R/'preflight/T038_LOCAL_ACCEPTANCE.json')['global_pass'] is True
 for rel,h in load(R/'OFFLINE_VALIDATION.json')['implementation_file_sha256'].items():assert sha(SOURCE/rel)==h,'tested source changed'
 for name in ['material_capabilities','route_consumer','research_routes']:
  mod=importlib.import_module('focused_research.'+name);origins[name]=str(Path(mod.__file__).resolve());assert Path(mod.__file__).resolve().is_relative_to(SOURCE)
 assert (R/'local_tools/task_budget_guard_220.py').read_bytes()==(R.parent/'R3-T030/local_tools/task_budget_guard_220.py').read_bytes()
 cfg=module(R.parent/'R3-T030/run_t030.py','t038_original_billing').original_billing()
 oldcase=R.parent/'R3-T037/cases'/case
 assert load(oldcase/'CASE_RESULT.json')['engineering_status']=='blocked'
 assert not (oldcase/'retrieval').exists() and not (oldcase/'routes').exists() and not (oldcase/'search').exists()
 prior=load(oldcase/'GUARD_CURRENT.json');assert prior['dispatch_attempts']==2 and prior['stage_counts']=={'task_sheet':2,'route_generation':0,'search_revision':0} and prior['shared_contract_repair_used'] is True
 with sqlite3.connect(POOL.as_uri()+'?mode=ro',uri=True) as db:
  calls=db.execute('SELECT task_id FROM r3_task_calls WHERE task_id IN (?,?,?,?)',tuple(task+':'+case for task in ['R3-T032','R3-T036','R3-T037','R3-T038'])).fetchall()
  assert len(calls)==2 and all(x[0]=='R3-T037:'+case for x in calls),'original case04 history changed; no automatic resume'
 sheet,binding=verify_recovery(case);question=load(SOURCE/'focused_research/campaigns/t032/inputs'/case/'FRESH_REQUEST.json')['question'];faithful(sheet,question,case)
 assert sha(SOURCE/'focused_research/campaigns/t032/inputs'/case/'FRESH_REQUEST.json')=='ef00e448f4c376618e9cdbf451d04b4a6736ff91bd91b6f6af9dd16d7c746638'
 assert sha(SOURCE/'veritmm/tmm_engine/rii_cache.db')=='e4c1ec34cc4f765a8400ff87bb7dee8705b6da2cdb11a229cc57ad9bdd274989'
 configuration(sheet,case)
 return runner,cfg,question,{'source_sha':IMPL,'origins':origins,'interpreter':sys.executable,'budget_before':reconcile(case),'launcher_sha256':sha(__file__),'aggregate_gate_sha256':sha(R/'local_tools/aggregate_budget.py'),'recovered_task_sheet_binding':binding,'prior_dispatch_attempts':2,'cumulative_dispatch_limit':4,'new_HTTP_max':2,'same_original_batch_HTTP_ceiling':12}

''' + s[end:]
s=s.replace('max_calls=4','max_calls=2').replace("if calls>=4:raise RuntimeError('case4 actualHTTP ceiling')","if calls>=2:raise RuntimeError('T038 two continuation HTTP ceiling')")
old="guard=cg.CampaignPlannerGuard(planner,case_id=case,audit_sink=persist)"
new="guard=cg.RecoveredCampaignPlannerGuard(planner,case_id=case,audit_sink=persist,recovered_task_sheet_binding=verify_recovery(case)[1],prior_dispatch_attempts=2,cumulative_dispatch_limit=4)"
assert old in s;s=s.replace(old,new)
start=s.index("  guard.set_stage('task_sheet')");end=s.index("  for p,x in [('design_constraints.json'",start)
s=s[:start]+"  sheet,binding=verify_recovery(case);faithful(sheet,question,case);constraints,media,grid=configuration(sheet,case)\n  status['stages']['planning']='reused_verified_T037_round2_zero_HTTP'\n"+s[end:]
s=s.replace("str(cr/'plan/query_plan.json')","str(cr/'recovered/query_plan.json')").replace("str(cr/'plan/task_sheet.json')","str(cr/'recovered/task_sheet.json')")
s=s.replace("lane='fresh_bounded_cycle'", "lane='recovered_continuation'")
ast.parse(s);(R/'run_t038.py').write_text(s,encoding='utf-8')
for name in ['probe.py','record_case_progress.py']:
 x=(P/name).read_text().replace('R3-T037::C2L-R3-0052','R3-T038::C2L-R3-0053').replace('run_t037.py','run_t038.py').replace('r3t037a','r3t038a').replace('R3-T037:','R3-T038:');ast.parse(x);(R/name).write_text(x,encoding='utf-8')
x=(P/'local_tools/test_continuation_gate.py').read_text().replace('t037_existing_fixture','t038_existing_fixture').replace('shared T032/T036/T037 original case4','shared T032/T036/T037/T038 original case4')
x=x.replace("new=guard(a,b,h,tmp_path/'new-audit','R3-T037')\n for i in range(2,4):settled(new,b,str(i))", "prior=guard(a,b,h,tmp_path/'prior-audit','R3-T037')\n settled(prior,b,'2')\n new=guard(a,b,h,tmp_path/'new-audit','R3-T038')\n settled(new,b,'3')").replace("new=guard(a,b,h,tmp_path/'new-audit','R3-T037')", "new=guard(a,b,h,tmp_path/'new-audit','R3-T038')")
ast.parse(x);(L/'test_continuation_gate.py').write_text(x,encoding='utf-8')
spec=importlib.util.spec_from_file_location('t038_money_preflight',L/'aggregate_budget.py');m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
old=Path(r'D:\OptoMind-Article-3\local-functional-trial\20261013\budget.sqlite');pool=R.parent/'R3-T019-DEDICATED20/private/budget.sqlite'
with sqlite3.connect(old.as_uri()+'?mode=ro',uri=True) as a,sqlite3.connect(pool.as_uri()+'?mode=ro',uri=True) as b:
 a.row_factory=b.row_factory=sqlite3.Row;v=m.checked_totals(a,b,Decimal(0),json.loads((R/'CASE_HISTORY_FOR_GUARD.json').read_text()),'R3-T038:'+m.CASES[1],admission=False)
 calls=b.execute("SELECT task_id,reservation_id,call_id FROM r3_task_calls WHERE task_id LIKE 'R3-T032:%' OR task_id LIKE 'R3-T036:%' OR task_id LIKE 'R3-T037:%' OR task_id LIKE 'R3-T038:%'").fetchall()
 prior=[dict(row) for row in calls if row['task_id'].endswith(m.CASES[1])];assert len(prior)==2 and all(row['task_id']=='R3-T037:'+m.CASES[1] for row in prior)
 hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [old,pool]}
v['both_ledgers_locked_across_check_and_reservation']=False
out={'live_read_only':True,'budget':v,'same_original_case_prior_calls':prior,'same_original_T032_batch_calls':[dict(row) for row in calls],'same_case_prior_actual_HTTP':2,'same_batch_prior_actual_HTTP':len(calls),'same_case_prior_cost_cny':v['case_exposure_cny'],'new_pool':False,'no_ledger_write':True,'history_copy_byte_identical':(R/'CASE_HISTORY_FOR_GUARD.json').read_bytes()==(P/'CASE_HISTORY_FOR_GUARD.json').read_bytes(),'ledger_sha256_before':hashes}
(R/'BUDGET_BEFORE.json').write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8');print(json.dumps(v))
