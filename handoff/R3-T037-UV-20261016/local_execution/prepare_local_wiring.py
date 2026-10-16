"""Adapt only task-local execution bindings, retaining all original counters."""
from pathlib import Path
import shutil,json,hashlib,sqlite3,importlib.util,sys,ast
from decimal import Decimal
R=Path(__file__).resolve().parent;P=R.parent/'R3-T036';L=R/'local_tools';L.mkdir(exist_ok=True)
shutil.copyfile(P/'local_tools/task_budget_guard_220.py',L/'task_budget_guard_220.py')
shutil.copyfile(P/'CASE_HISTORY_FOR_GUARD.json',R/'CASE_HISTORY_FOR_GUARD.json')
s=(P/'local_tools/aggregate_budget.py').read_text()
anchor="TASK_IDS['R3-T036:'+CASES[1]]=CASES[1]"
assert s.count(anchor)==1
s=s.replace(anchor,anchor+"\nTASK_IDS['R3-T037:'+CASES[1]]=CASES[1]").replace('shared T032/T036 original','shared T032/T036/T037 original')
(L/'aggregate_budget.py').write_text(s,encoding='utf-8')
s=(P/'run_t036.py').read_text(encoding='utf-8').replace('r3t036a','r3t037a').replace('408fef514d4ed156ffde03c868a26cea3aba9dfa','49861e169084976069d9ef1008e4fafc84b86bb8').replace('t036_','t037_').replace('T036_LOCAL_ACCEPTANCE','T037_LOCAL_ACCEPTANCE').replace("'R3-T036:'+case","'R3-T037:'+case").replace('authorized forT036','authorized forT037')
s=s.replace("assert state['processed']['R3-T035::C2L-R3-0050']['status']=='completed'","assert state['processed']['R3-T035::C2L-R3-0050']['status']=='completed'\n assert state['processed']['R3-T036::C2L-R3-0051']['status']=='blocked'\n assert state['processed']['R3-T037::C2L-R3-0052']['claim_readback_verified'] is True")
s=s.replace("assert sha(R/'SOURCE_BLOCKER.json')==sha(R.parent/'R3-T032/CASE04_BLOCKED_PROGRESS.json'),'original blocker receipt changed'","assert sha(R.parent/'R3-T036/SOURCE_BLOCKER.json')==sha(R.parent/'R3-T032/CASE04_BLOCKED_PROGRESS.json'),'original blocker receipt changed'\n assert load(R.parent/'R3-T036/RESULT.json')['new_model_http']==0 if 'new_model_http' in load(R.parent/'R3-T036/RESULT.json') else load(R.parent/'R3-T036/BUDGET_AFTER.json')['new_task_settled_cny']=='0'")
old="assert not db.execute('SELECT 1 FROM r3_task_calls WHERE task_id=?',('R3-T032:'+case,)).fetchone(),'unexpected prior04HTTP; reconcile beforeproceeding'"
new="assert not db.execute('SELECT 1 FROM r3_task_calls WHERE task_id IN (?,?,?)',tuple(task+':'+case for task in ['R3-T032','R3-T036','R3-T037'])).fetchone(),'unexpected prior04HTTP; reconcile beforeproceeding'"
assert s.count(old)==1;s=s.replace(old,new)
s=s.replace(" assert load(R.parent/'R3-T036/RESULT.json')['new_model_http']==0 if 'new_model_http' in load(R.parent/'R3-T036/RESULT.json') else load(R.parent/'R3-T036/BUDGET_AFTER.json')['new_task_settled_cny']=='0'", " assert load(R.parent/'R3-T036/BUDGET_AFTER.json')['new_task_settled_cny']=='0'\n assert load(R.parent/'R3-T036/BUDGET_AFTER.json')['task_calls']==[]")
s=s.replace("['new_task_settled_cny']=='0'", "['original_case04_settled_cny']=='0'").replace("assert load(R.parent/'R3-T036/BUDGET_AFTER.json')['task_calls']==[]", "assert load(R.parent/'R3-T036/BUDGET_AFTER.json')['original_case04_http_across_T032_T036']==0")
s=s.replace(" inventory=load(R/'material-preflight/execution_materials.json')", " inventory_envelope=load(R/'material-preflight/execution_materials.json')\n assert inventory_envelope['inventory_is_user_permission'] is False and inventory_envelope['allowed_materials'] is None\n assert inventory_envelope['source_commit']==IMPL\n inventory=inventory_envelope['execution_materials']")
s=s.replace("media['fused_silica']['dataset_id']=='835'", "media['fused_silica']['dataset_id']==835")
ast.parse(s);(R/'run_t037.py').write_text(s,encoding='utf-8')
for name in ['probe.py','record_case_progress.py']:
 s=(P/name).read_text().replace('R3-T036::C2L-R3-0051','R3-T037::C2L-R3-0052').replace('run_t036.py','run_t037.py').replace('r3t036a','r3t037a').replace('R3-T036:','R3-T037:');ast.parse(s);(R/name).write_text(s,encoding='utf-8')
s=(P/'local_tools/test_continuation_gate.py').read_text().replace('t036_existing_fixture','t037_existing_fixture').replace("'R3-T036'","'R3-T037'").replace("range(2):settled(old,b,str(i))","range(1):settled(old,b,str(i))\n middle=guard(a,b,h,tmp_path/'middle-audit','R3-T036')\n settled(middle,b,'1')").replace('shared T032/T036 original case4','shared T032/T036/T037 original case4')
ast.parse(s);(L/'test_continuation_gate.py').write_text(s,encoding='utf-8')
old=Path(r'D:\OptoMind-Article-3\local-functional-trial\20261013\budget.sqlite');pool=R.parent/'R3-T019-DEDICATED20/private/budget.sqlite'
spec=importlib.util.spec_from_file_location('t037_money_preflight',L/'aggregate_budget.py');m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
with sqlite3.connect(old.as_uri()+'?mode=ro',uri=True) as a,sqlite3.connect(pool.as_uri()+'?mode=ro',uri=True) as b:
 a.row_factory=b.row_factory=sqlite3.Row;x=m.checked_totals(a,b,Decimal(0),json.loads((R/'CASE_HISTORY_FOR_GUARD.json').read_text()),'R3-T037:'+m.CASES[1],admission=False)
 calls=b.execute("SELECT task_id,request_key,reservation_id,call_id FROM r3_task_calls WHERE task_id LIKE 'R3-T032:%' OR task_id LIKE 'R3-T036:%' OR task_id LIKE 'R3-T037:%'").fetchall()
 assert not any(row['task_id'].endswith(m.CASES[1]) for row in calls),'unexpected priorcase04 paidstage; no freshguard'
 hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [old,pool]}
result={'live_read_only':True,'budget':{**x,'both_ledgers_locked_across_check_and_reservation':False},'same_original_T032_task_calls':[dict(row) for row in calls],'history_copy_byte_identical':(P/'CASE_HISTORY_FOR_GUARD.json').read_bytes()==(R/'CASE_HISTORY_FOR_GUARD.json').read_bytes(),'original_case04_actual_http':0,'same_original_batch_actual_http':len(calls),'ledger_sha256_before':hashes,'new_pool':False,'no_ledger_write':True}
(R/'BUDGET_BEFORE.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8');print(json.dumps(x))
