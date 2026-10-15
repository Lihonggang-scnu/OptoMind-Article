"""Read-only T024 provenance, old-file hashes and existing ledger checks."""
import hashlib,json,sqlite3,subprocess,sys
from decimal import Decimal
from pathlib import Path
ROOT=Path(__file__).resolve().parent
ARTICLE=Path(r'D:\OptoMind-Article-3\r3t024a')
REVIEW=Path(r'D:\OptoMind-Article-3\r3t022r3')
OLD=[ROOT.parent/'R3-T022',ROOT.parent/'R3-T023']
LEDGERS=[Path(r'D:\OptoMind-Article-3\local-functional-trial\20261013\budget.sqlite'),
    ROOT.parent/'R3-T019-DEDICATED20/private/budget.sqlite']
def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(name,obj):
    (ROOT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def git(root,*args):return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()
phase=sys.argv[1]
assert phase in ('before','after')
inventory={p.name:{str(f.relative_to(p)):sha(f) for f in sorted(p.rglob('*')) if f.is_file()} for p in OLD}
budget=[]
for p in LEDGERS:
    with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as db:
        rows=db.execute('SELECT status,amount_cny,actual_cny FROM reservations').fetchall()
        cap=Decimal(dict(db.execute('SELECT key,value FROM budget_meta'))['limit_cny'])
    budget.append({'path':str(p),'sha256':sha(p),'cap_cny':str(cap),
        'settled_cny':str(sum((Decimal(str(r[2] or 0)) for r in rows if r[0]=='settled'),Decimal(0))),
        'held_cny':str(sum((Decimal(str(r[1] or 0)) for r in rows if r[0]!='settled'),Decimal(0))),
        'unresolved_count':sum(r[0]!='settled' for r in rows)})
total=sum((Decimal(x['settled_cny']) for x in budget),Decimal(0))
cost={'ledgers':budget,'aggregate_settled_cny':str(total),
    'aggregate_held_cny':str(sum((Decimal(x['held_cny']) for x in budget),Decimal(0))),
    'global_cap_cny':140,'task_cap_cny':0,'new_cost_cny':0,
    'cost_basis':'Prior supplier usage estimates; opened existing ledgers read-only, no invoice reconciliation.'}
if phase=='before':
    assert git(ARTICLE,'rev-parse','HEAD')=='3eb51424985402a3e9e79bdb872a002d9f5b6e45'
    assert git(REVIEW,'rev-parse','HEAD')=='d2deb4dbe18b23837d949822ad731afbaba9414b'
    assert not git(ARTICLE,'status','--porcelain=v1')
    assert not git(REVIEW,'status','--porcelain=v1')
    full=json.loads((OLD[0]/'runs/fulltext/FULLTEXT_RESULT.json').read_text(encoding='utf-8'))
    assert len(full['records'])==5
    for row in full['records']:
        acq=row.get('docling_acquisition') or row['acquisition']
        assert Path(acq['snapshot_path']).is_dir(),'Missing cached snapshot: '+acq['snapshot_path']
    guard=(OLD[1]/'offline_guard.py').read_text(encoding='utf-8')
    guard=guard.replace('r3t023a','r3t024a').replace("target in (ROOT/'audit_cached_results.py',ROOT/'audit_native_snapshots.py')","target==ROOT/'check_exports.py'")
    (ROOT/'offline_guard.py').write_text(guard,encoding='utf-8')
    save('SOURCE_PREFLIGHT.json',{'article_path':str(ARTICLE),'article_sha':git(ARTICLE,'rev-parse','HEAD'),
        'article_branch':git(ARTICLE,'branch','--show-current'),'review_path':str(REVIEW),
        'review_sha':git(REVIEW,'rev-parse','HEAD'),'both_clean':True,'python':sys.executable,
        'production_files':{str(p.relative_to(ARTICLE)):sha(p) for p in [ARTICLE/'focused_research/fulltext_stage.py',ARTICLE/'focused_research/review_fulltext_worker.py']},
        'guard_reused_from':'R3-T023/offline_guard.py; source path and allowed checker updated only',
        'checkout_line_endings':'git worktree add invoked with core.autocrlf=false; no global config changed.'})
else:
    assert inventory==json.loads((ROOT/'OLD_FILE_HASHES_BEFORE.json').read_text(encoding='utf-8-sig')),'Old T022/T023 files changed'
    assert cost==json.loads((ROOT/'BUDGET_BEFORE.json').read_text(encoding='utf-8-sig')),'Ledger hash or cost changed'
    save('PRESERVATION_CHECK.json',{'old_files_unchanged':True,'counts':{k:len(v) for k,v in inventory.items()},
        'ledger_hashes_unchanged':True,'new_cost_cny':0})
save('OLD_FILE_HASHES_'+phase.upper()+'.json',inventory)
save('BUDGET_'+phase.upper()+'.json',cost)
print(json.dumps({'phase':phase,'old_files':{k:len(v) for k,v in inventory.items()},'cumulative_estimate_cny':str(total),'new_cost_cny':0}))
