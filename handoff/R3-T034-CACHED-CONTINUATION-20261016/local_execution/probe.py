"""Read current durable progress and original money ledgers; no execution or SQL writes."""
import json,sqlite3
from decimal import Decimal
from pathlib import Path
from datetime import datetime,timezone
r=Path(__file__).resolve().parent
load=lambda p:json.loads(p.read_text(encoding='utf-8'))
cases=load(r/'C2L-R3-0049.json')['budget_details']['case_order']
result={'checked_at':datetime.now(timezone.utc).isoformat(),'read_only':True,'cases':[],'ledgers':[]}
for c in cases:
    cr=r/'cases'/c;row={'case_id':c}
    for file in ['CASE_RESULT.json','GUARD_CURRENT.json','BUDGET_STATUS.json']:
        p=cr/file
        if p.exists():
            x=load(p)
            row[file]=({k:x.get(k) for k in ['dispatch_attempts','stage_counts','repair_used','failed_closed']} if file=='GUARD_CURRENT.json' else x)
    for sub in ['routes/summary.json','search/summary.json','revision/summary.json','refined/summary.json']:
        p=cr/sub
        if p.exists():row[sub]=load(p)
    result['cases'].append(row)
for path in [Path(r'D:\OptoMind-Article-3\local-functional-trial\20261013\budget.sqlite'),r.parent/'R3-T019-DEDICATED20/private/budget.sqlite']:
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        rows=db.execute('SELECT * FROM reservations').fetchall()
        result['ledgers'].append({'path':str(path),'settled_cny':str(sum((Decimal(str(x['actual_cny'] or 0)) for x in rows if x['status']=='settled'),Decimal(0))),
            'pending_held_uncertain_cny':str(sum((Decimal(str(x['amount_cny'] or 0)) for x in rows if x['status']!='settled'),Decimal(0))),
            'unresolved_count':sum(x['status']!='settled' for x in rows)})
        if path.name=='budget.sqlite' and 'DEDICATED20' in str(path):
            calls=db.execute("SELECT c.task_id,c.request_key,c.call_id,r.status,r.amount_cny,r.actual_cny FROM r3_task_calls c JOIN reservations r ON r.reservation_id=c.reservation_id WHERE c.task_id LIKE 'R3-T034:%'").fetchall()
            result['task_calls']=[dict(x) for x in calls]
result['new_task_settled_cny']=str(sum((Decimal(str(x['actual_cny'] or 0)) for x in result.get('task_calls',[]) if x['status']=='settled'),Decimal(0)))
print(json.dumps(result,ensure_ascii=False,indent=2))
