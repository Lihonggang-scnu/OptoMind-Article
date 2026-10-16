from pathlib import Path
from datetime import datetime,timezone
import json,sqlite3,hashlib
from decimal import Decimal
R=Path(__file__).resolve().parent;H=R.parent.parent;now=datetime.now(timezone.utc);s=json.loads((H/'state.json').read_text(encoding='utf-8'));key='R3-T038::C2L-R3-0053'
assert key not in s['processed'] and s['active_task'] is None
assert s['processed']['R3-T037::C2L-R3-0052']['status']=='blocked'
ledgers=[]
for path in [Path(r'D:\OptoMind-Article-3\local-functional-trial\20261013\budget.sqlite'),R.parent/'R3-T019-DEDICATED20/private/budget.sqlite']:
 with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
  db.row_factory=sqlite3.Row;rows=db.execute('SELECT * FROM reservations').fetchall();ledgers.append({'path':str(path),'settled_cny':str(sum((Decimal(str(x['actual_cny'] or 0)) for x in rows if x['status']=='settled'),Decimal(0))),'pending_held_uncertain_cny':str(sum((Decimal(str(x['amount_cny'] or 0)) for x in rows if x['status']!='settled'),Decimal(0))),'unresolved_count':sum(x['status']!='settled' for x in rows)})
assert all(x['unresolved_count']==0 for x in ledgers)
claim={'schema_version':1,'message_id':'RCP-LOCAL-T038-CLAIMED-'+now.strftime('%Y%m%dT%H%M%SZ'),'task_id':'R3-T038','in_reply_to':'C2L-R3-0053','direction':'local_to_cloud','kind':'receipt','status':'claimed','created_at':now.isoformat(),'base_sha':'49861e169084976069d9ef1008e4fafc84b86bb8','implementation_sha':'366f3faa70217b6209afb1162bac6759765cd628','continuation_of':['R3-T032','R3-T036','R3-T037'],'case_id':'e2e-04-solarblind-uv-triple','dedup_checked':'Drive receipts/localstate/GitHub relay0687ebe0c5fbc0de35a75547ceb55c0829d41f61; only cloud development/publish receipts, no local T038 execution','verified_artifact_hashes':7,'paid_calls_before_claim_readback':0,'scope':'Zero-model recovery of original saved round2; then at most1route+1revision after full admission, no repair/retry','same_case_cumulative_http_cap':4,'same_case_prior_http':2,'same_T032_batch_http_cap':12,'same_case_cny_cap':8,'same_T032_batch_cny_cap':24,'project_cny_cap':220,'new_budget_pool':False,'live_ledger_snapshot':ledgers}
(R/'CLAIMED.json').write_text(json.dumps(claim,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
event={'trigger':'scheduled','trigger_iso':'2026-10-16T02:15:11.226Z','checked_at':now.isoformat(),'new_message_ids':['C2L-R3-0053'],'channels':['Drive','GitHub_relay','local_state'],'status':'new_ready_T038_claim_pending_readback'}
with (H/'poll_events.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(event)+'\n')
s['last_checked_at']=s['last_automatic_poll_at']=now.isoformat();s['automation']['last_actual_trigger_iso']=s['automation']['latest_automatic_tick_observed_at']=event['trigger_iso'];s['pending_ready'][key]={'task_id':'R3-T038','message_id':'C2L-R3-0053','claim_message_id':claim['message_id'],'status':'claim_pending_readback'}
tmp=H/'state.t038.tmp';tmp.write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');tmp.replace(H/'state.json')
print(json.dumps({'claim_message_id':claim['message_id'],'ledgers':ledgers}))
