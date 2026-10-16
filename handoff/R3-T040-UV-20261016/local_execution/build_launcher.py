from pathlib import Path
import shutil,ast
R=Path(__file__).resolve().parent;OLD=R.parent/'R3-T038';L=R/'local_tools';L.mkdir(exist_ok=True)
for name in ['task_budget_guard_220.py']:
 shutil.copyfile(OLD/'local_tools'/name,L/name)
shutil.copyfile(OLD/'CASE_HISTORY_FOR_GUARD.json',R/'CASE_HISTORY_FOR_GUARD.json')
s=(OLD/'local_tools/aggregate_budget.py').read_text(encoding='utf-8')
anchor="TASK_IDS['R3-T038:'+CASES[1]]=CASES[1]";assert s.count(anchor)==1
s=s.replace(anchor,anchor+"\nTASK_IDS['R3-T040:'+CASES[1]]=CASES[1]")
anchor="    if admission and sum(len(ids) for key,ids in task_ids.items() if TASK_IDS[key]==TASK_IDS[task_id])>=4:raise RuntimeError('shared T032/T036/T037/T038 original case4 actual reservation attempts exhausted')"
assert s.count(anchor)==1
s=s.replace(anchor,"    case_http_cap=6 if task_id=='R3-T040:'+CASES[1] else 4\n    if admission and sum(len(ids) for key,ids in task_ids.items() if TASK_IDS[key]==TASK_IDS[task_id])>=case_http_cap:raise RuntimeError('shared original case HTTP ceiling exhausted')")
s=s.replace("'native_task_http_cap':4","'native_task_http_cap':case_http_cap")
ast.parse(s);(L/'aggregate_budget.py').write_text(s,encoding='utf-8')
source=(OLD/'run_t038.py').read_text(encoding='utf-8');f=source[source.index('def audited_guard('):source.index('def faithful(')]
f=f.replace("'R3-T038:'+case","'R3-T040:'+case").replace('t032_original_qwen_adapter','t040_original_qwen_adapter').replace('T038 two continuation HTTP ceiling','T040 two revision HTTP ceiling')
anchor='guard=cg.RecoveredCampaignPlannerGuard(planner,case_id=case,audit_sink=persist,recovered_task_sheet_binding=verify_recovery(case)[1],prior_dispatch_attempts=2,cumulative_dispatch_limit=4)'
assert f.count(anchor)==1
f=f.replace(anchor,"guard=cg.RecoveredRevisionPlannerGuard(planner,case_id=case,audit_sink=persist,recovered_task_sheet_binding=verify_recovery(case)[1],continuation_task_id='R3-T040',prior_dispatch_attempts=4,cumulative_dispatch_limit=6)")
ast.parse(f);(R/'audited_guard_fragment.py').write_text(f,encoding='utf-8')
print('Prepared task-local aggregate gate and unchanged original transport fragment; no native call/client instantiated')
