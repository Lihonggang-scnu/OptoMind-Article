"""T036 task-local wrapper adaptation; preserves native accounting and all history."""
from pathlib import Path
import shutil,json,hashlib,sqlite3
from decimal import Decimal
R=Path(__file__).resolve().parent;T032=R.parent/'R3-T032';L=R/'local_tools';L.mkdir(exist_ok=True)
for file in ['task_budget_guard_220.py']:
 shutil.copyfile(T032/'local_tools'/file,L/file)
shutil.copyfile(T032/'CASE_HISTORY_FOR_GUARD.json',R/'CASE_HISTORY_FOR_GUARD.json')
s=(T032/'local_tools/aggregate_budget.py').read_text()
s=s.replace("TASK_IDS = {task+':'+c:c for task in ('R3-T032',) for c in CASES}","TASK_IDS = {'R3-T032:'+c:c for c in CASES}\nTASK_IDS['R3-T036:'+CASES[1]]=CASES[1]")
s=s.replace("selected={c:set(history['case_reservation_ids'][c])|task_ids['R3-T032:'+c] for c in CASES}","selected={c:set(history['case_reservation_ids'][c])|set().union(*(ids for key,ids in task_ids.items() if TASK_IDS[key]==c)) for c in CASES}")
s=s.replace("len(task_ids['R3-T032:'+TASK_IDS[task_id]])>=4","sum(len(ids) for key,ids in task_ids.items() if TASK_IDS[key]==TASK_IDS[task_id])>=4")
s=s.replace('T032 original case4','shared T032/T036 original case4')
(L/'aggregate_budget.py').write_text(s,encoding='utf-8')
old=Path(r'D:\OptoMind-Article-3\local-functional-trial\20261013\budget.sqlite');pool=R.parent/'R3-T019-DEDICATED20/private/budget.sqlite'
import importlib.util,sys
spec=importlib.util.spec_from_file_location('t036_money_preflight',L/'aggregate_budget.py');m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
with sqlite3.connect(old.as_uri()+'?mode=ro',uri=True) as a,sqlite3.connect(pool.as_uri()+'?mode=ro',uri=True) as b:
 a.row_factory=b.row_factory=sqlite3.Row;x=m.checked_totals(a,b,Decimal(0),json.loads((R/'CASE_HISTORY_FOR_GUARD.json').read_text()),'R3-T036:'+m.CASES[1],admission=False)
 calls=b.execute("SELECT task_id,request_key,reservation_id,call_id FROM r3_task_calls WHERE task_id LIKE 'R3-T032:%' OR task_id LIKE 'R3-T036:%'").fetchall()
 assert not any(row['task_id'].endswith(m.CASES[1]) for row in calls),'unexpected priorcase04 paidstage; no freshguard'
 ledgerhashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [old,pool]}
result={'live_read_only':True,'budget':{**x,'both_ledgers_locked_across_check_and_reservation':False},'same_original_T032_task_calls':[dict(row) for row in calls],'original_history_sha256':hashlib.sha256((T032/'CASE_HISTORY_FOR_GUARD.json').read_bytes()).hexdigest(),'history_copy_byte_identical':(T032/'CASE_HISTORY_FOR_GUARD.json').read_bytes()==(R/'CASE_HISTORY_FOR_GUARD.json').read_bytes(),'original_case04_actual_http':0,'same_original_batch_actual_http':len(calls),'ledger_sha256_before':ledgerhashes,'no_ledger_write':True,'new_pool':False}
(R/'BUDGET_BEFORE.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8');print(json.dumps(x))
