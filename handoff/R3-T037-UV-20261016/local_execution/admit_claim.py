from pathlib import Path
import json,datetime,subprocess
R=Path(__file__).resolve().parent; H=R.parent.parent
now=datetime.datetime.now(datetime.timezone.utc).isoformat()
s=json.loads((H/'state.json').read_text(encoding='utf-8')); key='R3-T037::C2L-R3-0052'
assert key not in s['processed'] and s['active_task'] is None
claim={'schema_version':1,'message_id':'RCP-LOCAL-T037-CLAIMED-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'),'task_id':'R3-T037','in_reply_to':'C2L-R3-0052','direction':'local_to_cloud','kind':'receipt','status':'claimed','created_at':now,'implementation_sha':'49861e169084976069d9ef1008e4fafc84b86bb8','base_sha':'408fef514d4ed156ffde03c868a26cea3aba9dfa','case_id':'e2e-04-solarblind-uv-triple','continuation_of':['R3-T032','R3-T036'],'dedup_checked':'Drive receipts/localstate/GitHub relay 0687ebe0c5fbc0de35a75547ceb55c0829d41f61: no local T037 claim; cloud development claim is not local execution','verified_delivery_files':8,'budget_admission':'offline first, live native ledgers recomputed before execution; no extra allowance or new pool','same_case_http_cap':4,'same_original_batch_http_cap':12,'same_case_cumulative_cny_cap':8,'same_original_batch_cny_cap':24,'project_cumulative_cny_cap':220,'paid_before_claim_verified':False}
(R/'CLAIMED.json').write_text(json.dumps(claim,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
event={'trigger':'scheduled','trigger_iso':'2026-10-16T00:45:09.889Z','checked_at':now,'new_message_ids':['C2L-R3-0052'],'channels':['Drive','GitHub_relay','local_state'],'status':'new_ready_T037_claim_pending_readback'}
with (H/'poll_events.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(event)+'\n')
s['last_checked_at']=s['last_automatic_poll_at']=now;s['automation']['last_actual_trigger_iso']=s['automation']['latest_automatic_tick_observed_at']=event['trigger_iso'];s['pending_ready']['R3-T037::C2L-R3-0052']={'task_id':'R3-T037','message_id':'C2L-R3-0052','claim_message_id':claim['message_id'],'status':'claim_pending_readback'}
tmp=H/'state.t037.tmp';tmp.write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');tmp.replace(H/'state.json')
print(claim['message_id'])
