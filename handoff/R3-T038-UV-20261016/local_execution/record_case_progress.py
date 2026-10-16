import argparse,json,subprocess,sys
from pathlib import Path
from datetime import datetime,timezone
r=Path(__file__).resolve().parent
a=argparse.ArgumentParser();a.add_argument('--case',required=True);a.add_argument('--session',type=int,required=True);args=a.parse_args()
live=json.loads(subprocess.check_output([sys.executable,'-B','-X','utf8',str(r/'probe.py')],text=True,encoding='utf-8'))
statepath=r.parents[1]/'state.json';state=json.loads(statepath.read_text(encoding='utf-8'))
entry=state['processed']['R3-T038::C2L-R3-0053']
entry.update(status='running',phase=args.case,execution_session_id=args.session,
    reservation_attempts=len(live.get('task_calls',[])),new_spend_cny=live['new_task_settled_cny'])
entry.setdefault('case_execution_sessions',{})[args.case]=args.session
entry.pop('process_ids',None)  # Historical case09 PIDs must not identify the new active case.
entry['current_command_argv']=[sys.executable,'-B','-X','utf8',str(r/'run_t038.py'),'case','--case',args.case,'--execute']
state['latest_budget_summary']={'checked_at':live['checked_at'],'read_only':True,'ledgers':live['ledgers']}
temp=statepath.with_suffix('.case-progress.tmp');temp.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');temp.replace(statepath)
p=r/'CASE_EXECUTION_COMMANDS.json';commands=json.loads(p.read_text(encoding='utf-8')) if p.exists() else []
if not any(x['case_id']==args.case for x in commands):
    commands.append({'case_id':args.case,'recorded_at':datetime.now(timezone.utc).isoformat(),'session_id':args.session,'cwd':r'D:\OptoMind-Article-3\r3t038a','argv':entry['current_command_argv']})
    p.write_text(json.dumps(commands,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'active_case':args.case,'session':args.session,'settled':live['new_task_settled_cny']},ensure_ascii=False))
