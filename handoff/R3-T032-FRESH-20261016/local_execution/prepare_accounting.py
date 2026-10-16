"""Read-only original-case attribution; never creates or changes a money pool."""
import json,sqlite3,hashlib
from pathlib import Path
from decimal import Decimal
from datetime import datetime,timezone
R=Path(__file__).resolve().parent;BASE=Path(r'D:\OptoMind-Article-3');T=R.parent
load=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
cases=load(R/'BUDGET.json')['case_order'];proof=[];classified={}
def fileproof(p):
 p=Path(p);assert p.is_file();proof.append({'path':str(p),'sha256':sha(p)});return p
a=load(fileproof(T/'R3-T030/accounting-history/newreports/T030_RGB_NIR_COST_ATTRIBUTION.json'))
b=load(fileproof(T/'R3-T030/accounting-history/PARENT_RESIDUAL_RESOLUTION.json'))
assert b['attribution_complete'] and len(b['identities'])==122
for row in b['identities']:
 assert row['case_family'] in {'two_layer_antireflection_glass','visible_achromatic_metalens'}
 p=fileproof(Path(row['request_path']));assert sha(p)==row['request_sha256'];classified[row['reservation_id']]=row['case_family']
for key,grp in a['case_groups']['original_unlinked_known_sanitized_nonrgb_buckets'].items():
 for p in grp['source_reports']:fileproof(BASE/p)
 assert grp['row_count'] in (61,75)
 for row in grp['identities']:classified[row['reservation_id']]='two_layer_AR_or_metalens_'+key
original=BASE/'local-functional-trial/20261013/budget.sqlite';pool=T/'R3-T019-DEDICATED20/private/budget.sqlite'
oldhash=sha(original);unique={};links=[];ledgers=[]
for p in [original,pool]:
 with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as d:
  d.row_factory=sqlite3.Row;rows=d.execute('SELECT * FROM reservations').fetchall();ll=d.execute('SELECT * FROM r3_task_calls').fetchall()
  assert all(x['status']=='settled' for x in rows),'unresolved liabilities: paid admission forbidden'
  for row in rows:
   x=dict(row);rid=x['reservation_id'];assert rid not in unique or unique[rid]==x;unique[rid]=x
  for row in ll:
   x=dict(row);links.append(x);rid=x['reservation_id'];tid=x['task_id']
   assert not any(c in tid for c in cases+['e2e-03-methane-swir-chirped']),'prior same-case cost requires explicit attribution'
   assert rid not in classified;classified[rid]='native_task:'+tid
  ledgers.append({'path':str(p),'reservation_count':len(rows),'settled_cny':str(sum((Decimal(str(x['actual_cny'] or 0)) for x in rows),Decimal(0))),'unresolved_count':0,'sha256_before':sha(p)})
assert set(classified)==set(unique),'unattributed reservation: no dispatch'
fileproof(T/'R3-T004-ARTICLE-LIVE/REPORT.md');fileproof(T/'R3-T006-REVIEW-LIVE/REPORT.md')
# This audit uses every exact native reservation identity. It does not add old
# unbilled showcase/replay estimates, or infer identity from time proximity.
history={'attribution_complete':True,'checked_at':datetime.now(timezone.utc).isoformat(),'all_reservation_ids_at_audit':sorted(unique),'case_reservation_ids':{c:[] for c in cases},'original_03_alias_of':cases[0],'historical_case_cost_cny':{c:'0' for c in cases},'classification_by_reservation_id':classified,'evidence':proof,'ledgers':ledgers,'project_settled_cny':str(sum((Decimal(str(x['actual_cny'] or 0)) for x in unique.values()),Decimal(0))),'model_calls':0,'ledger_writes':False,'original_ledger_sha256':oldhash}
assert sha(original)==oldhash
with (R/'CASE_HISTORY_FOR_GUARD.json').open('x',encoding='utf-8') as f:json.dump(history,f,ensure_ascii=False,indent=2);f.write('\n')
print(json.dumps({k:history[k] for k in ['attribution_complete','project_settled_cny','historical_case_cost_cny','ledger_writes']}))
