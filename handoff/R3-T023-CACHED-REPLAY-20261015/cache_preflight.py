"""Read-only cached-input inventory and ledger reconciliation; no project clients."""
import datetime
import hashlib
import json
import sqlite3
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OLD = ROOT.parent / 'R3-T022'
ARTICLE = Path(r'D:\OptoMind-Article-3\r3t023a')
REVIEW = Path(r'D:\OptoMind-Article-3\r3t022r3')
LEDGERS = [Path(r'D:\OptoMind-Article-3\local-functional-trial\20261013\budget.sqlite'),
    ROOT.parent / 'R3-T019-DEDICATED20/private/budget.sqlite']

def load(p):
    return json.loads(p.read_text(encoding='utf-8-sig'))

def sha(p):
    with p.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def save(name, value):
    (ROOT/name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

def inventory():
    required = ['runs/fulltext/FULLTEXT_RESULT.json', 'runs/routes/task_sheet.json',
        'runs/routes/evidence_items.json', 'runs/routes/design_constraints.json',
        'runs/routes/research_routes.json', 'runs/routes/generation_outcome.json',
        'runs/material-view/selected_evidence_items.json', 'ROUTE_PROMPT_PREFLIGHT.json']
    for rel in required:
        assert (OLD/rel).is_file(), 'Missing cached input: '+rel
    full = load(OLD/required[0])
    assert len(full['records']) == 5
    snapshots=[]
    for row in full['records']:
        acquisition=row.get('docling_acquisition') or row['acquisition']
        path=Path(acquisition['snapshot_path'])
        assert path.is_dir(), 'Missing cached snapshot: '+str(path)
        snapshots.append({'paper':row['record']['canonical_paper_id'],
            'snapshot_id':acquisition['snapshot_id'], 'path':str(path), 'parser':acquisition['parser']})
    # Includes original raw responses, selection, exports and native snapshots;
    # no file under T022 is written by this task.
    files={str(p.relative_to(OLD)):sha(p) for p in sorted(OLD.rglob('*')) if p.is_file()}
    return {'file_count':len(files), 'files':files, 'snapshots':snapshots}

def budget():
    rows=[]
    for p in LEDGERS:
        with sqlite3.connect(p.as_uri()+'?mode=ro', uri=True) as db:
            reservations=db.execute('SELECT status,amount_cny,actual_cny FROM reservations').fetchall()
            cap=Decimal(dict(db.execute('SELECT key,value FROM budget_meta'))['limit_cny'])
        settled=sum((Decimal(str(r[2] or 0)) for r in reservations if r[0]=='settled'),Decimal(0))
        held=sum((Decimal(str(r[1] or 0)) for r in reservations if r[0]!='settled'),Decimal(0))
        rows.append({'path':str(p),'cap_cny':str(cap),'settled_cny':str(settled),'held_cny':str(held),
            'unresolved_count':sum(r[0]!='settled' for r in reservations),'sha256':sha(p)})
    total=sum((Decimal(r['settled_cny']) for r in rows),Decimal(0))
    return {'ledgers':rows,'aggregate_settled_cny':str(total),'aggregate_held_cny':str(sum((Decimal(r['held_cny']) for r in rows),Decimal(0))),
        'global_cap_cny':140,'global_remaining_cny':str(Decimal(140)-total),
        'task_cap_cny':0,'new_cost_cny':0,'model_http_attempts':0,
        'cost_basis':'Existing supplier-usage estimate, not invoice reconciliation; ledger opened read-only.'}

def git(p,*args):
    return subprocess.check_output(['git','-C',str(p),*args],text=True).strip()

phase=sys.argv[1]
assert phase in ('before','after')
inv=inventory()
cost=budget()
if phase=='before':
    assert git(ARTICLE,'rev-parse','HEAD')=='812a4bfad559a1522e973a3e68e7481293e2242a'
    assert git(REVIEW,'rev-parse','HEAD')=='d2deb4dbe18b23837d949822ad731afbaba9414b'
    assert not git(ARTICLE,'status','--porcelain=v1')
    assert not git(REVIEW,'status','--porcelain=v1')
    save('SOURCE_PREFLIGHT.json',{'article':{'path':str(ARTICLE),'sha':git(ARTICLE,'rev-parse','HEAD'),
        'clean':True,'branch':git(ARTICLE,'branch','--show-current'),
        'core_autocrlf':git(ARTICLE,'config','--get','core.autocrlf'),
        'files':{str(p.relative_to(ARTICLE)):sha(p) for p in (ARTICLE/'focused_research').glob('*.py')}},
        'review':{'path':str(REVIEW),'sha':git(REVIEW,'rev-parse','HEAD'),'clean':True},
        'python':sys.executable,'version':sys.version})
else:
    before=load(ROOT/'INPUT_HASHES_BEFORE.json')
    old_cost=load(ROOT/'BUDGET_BEFORE.json')
    assert inv==before, 'T022 input inventory/hash changed'
    assert cost==old_cost, 'Original ledger hash or cost changed'
    save('PRESERVATION_CHECK.json',{'all_t022_files_unchanged':True,'file_count':inv['file_count'],
        'original_ledgers_unchanged':True,'new_cost_cny':0})
save('INPUT_HASHES_'+phase.upper()+'.json',inv)
save('BUDGET_'+phase.upper()+'.json',cost)
print(json.dumps({'phase':phase,'cached_files':inv['file_count'],'snapshots':len(inv['snapshots']),
    'aggregate_settled_cny':cost['aggregate_settled_cny'],'new_cost_cny':0}))
