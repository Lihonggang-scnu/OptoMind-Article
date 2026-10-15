"""Read settled native records and preserve previous outputs; no model calls."""
import hashlib,json,sqlite3
from pathlib import Path
from launch_t025 import ROOT,OLD,LEDGER,TASK,load,sha,reconcile,write
accounting=load(ROOT/'GENERATE_ACCOUNTING.json')
status=reconcile(strict=False)
previous=load(ROOT/'PRIOR_RESERVATION_HASHES.json')
with sqlite3.connect(LEDGER.as_uri()+'?mode=ro',uri=True) as db:
    records=db.execute('SELECT * FROM reservations').fetchall()
    calls=db.execute('SELECT r.reservation_id,r.amount_cny,r.actual_cny,r.status,r.returned_model,r.finish_reason FROM reservations r JOIN r3_task_calls c USING(reservation_id) WHERE c.task_id=?',(TASK,)).fetchall()
current={str(row[0]):hashlib.sha256(json.dumps(row,ensure_ascii=False,sort_keys=True).encode()).hexdigest() for row in records}
assert all(current.get(k)==v for k,v in previous.items()),'Previous native reservations changed'
assert len(calls)<=2
assert accounting['task_guard_after']['actual_cny']<=3
assert status['chain_exposure_cny'].__class__ is str
original=load(ROOT/'OLD_OUTPUT_HASHES_BEFORE.json')
after={name:{str(p.relative_to(ROOT.parent/name)):sha(p) for p in sorted((ROOT.parent/name).rglob('*')) if p.is_file()} for name in original}
assert after==original,'Prior T022/T023/T024 output changed'
assert sha(OLD)==load(ROOT/'BUDGET_BEFORE.json')['ledgers'][0]['sha256']
write(ROOT/'PRESERVATION_CHECK.json',{'previous_outputs_unchanged':True,'counts':{k:len(v) for k,v in after.items()},
    'original_140_ledger_unchanged':True,'prior_subpool_reservations_unchanged':True,
    'old_subpool_reservation_count':len(previous),'only_new_task_records_appended':True})
write(ROOT/'BUDGET_AFTER.json',{'reconciled':status,'task':accounting['task_guard_after'],
    'new_cost_cny':accounting['task_guard_after']['actual_cny'],
    'task_held_cny':accounting['task_guard_after']['reserved_cny'],
    'new_native_attempts':len(calls),'cost_basis':'Existing native supplier-usage estimate; no supplier invoice reconciliation.'})
model_calls=[]
for request in sorted((ROOT/'private/audit').rglob('REQUEST.json')):
    req=load(request);response=request.with_name('RESPONSE.json')
    res=load(response) if response.exists() else {}
    body=req['wire_body'];usage=res.get('usage',{})
    model_calls.append({'call_id':request.parent.name,'model':req['model'],'transport_retries':req['transport_retries'],
        'key_slots':req['key_slots'],'max_output_tokens':body.get('max_completion_tokens',body.get('max_tokens')),
        'finish_reason':res.get('finish_reason'),'provider_usage':usage,
        'request_sha256':sha(request),'response_sha256':sha(response) if response.exists() else None,
        'response_recorded':response.exists(),'wire_temperature':body['temperature']})
assert len(model_calls)==len(calls)
write(ROOT/'MODEL_CALLS.json',{'actual_native_attempts':len(calls),'calls':model_calls,
    'native_reservations':[{'reservation_id':c[0],'reserved_cny':c[1],'actual_cny':c[2],
        'status':c[3],'returned_model':c[4],'finish_reason':c[5]} for c in calls]})
print(json.dumps({'calls':len(calls),'new_cost_cny':accounting['task_guard_after']['actual_cny'],
    'cumulative_exposure_cny':status['aggregate_exposure_cny'],'held_cny':status['ledgers'][1]['held_cny'],
    'prior_outputs_and_reservations_preserved':True},ensure_ascii=False))
