"""Audit completed native Review runs without further model calls."""
from pathlib import Path
import hashlib
import json

ROOT=Path(__file__).resolve().parent
run=ROOT/'review/real-01'
def load(path): return json.loads(path.read_text(encoding='utf-8'))
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

plan=load(run/'DETAILED_REVIEW_PLAN.json')
report=load(run/'PLANNING_RUN_REPORT.json')
assert report['handoff_validation']==plan['handoff_validation']
chapters=[]
for row in plan['chapters']:
    review=row['claim_scope_review']
    assert review['owner_revision_attempts']<=1 and len(review.get('checks',[]))<=2
    adopted=row['source_adoption']
    assert row['chapter']['source_handles']==adopted['source_handles']
    assert row['chapter']['source_ids']==adopted['source_ids']
    supplied={(s.get('source_handle'),s.get('paper_id')) for s in row['source_materials']}
    for handle,paper in zip(adopted['source_handles'],adopted['source_ids']):
        assert (handle,paper) in supplied
    chapters.append({'chapter_id':row['chapter']['chapter_id'], 'title':row['chapter'].get('title'),
        'source_adoption':adopted, 'handoff_validation':row['handoff_validation'],
        'claim_scope_status':review['status'], 'owner_revision_attempts':review['owner_revision_attempts'],
        'owner_status':review.get('owner_status'), 'owner_structural_errors':review.get('owner_structural_errors'),
        'checks':[{'status':c['status'],'errors':c.get('errors'),
            'valid_issue_count':len(c.get('issues',[])), 'input_signature':c.get('input_signature')}
            for c in review.get('checks',[])], 'scope':row['chapter'].get('scope'),
        'unit_count':len((row.get('chapter_plan') or {}).get('units',[]))})
old=Path('D:/OptoMind-Article-3/local-repair-validation/20261013/review/formal-01')
hashes=load(ROOT/'review/ORIGINAL_CHECKPOINT_SHA256.json')
changed=[name for name,digest in hashes.items() if sha(old/name)!=digest]
assert not changed, changed
current_unchanged=sha(run/'CURRENT_PLAN.json')==hashes['CURRENT_PLAN.json']
if plan['handoff_validation']['status']!='complete':
    assert report['handoff_blocked'] is True
    assert current_unchanged, 'Partial run must not replace an earlier valid CURRENT_PLAN'
else:
    assert not current_unchanged
receipt={'native_cli_exit_code':0 if report['status']=='complete' else 2,
    'planning_status':plan['status'], 'handoff_validation':plan['handoff_validation'],
    'handoff_blocked':report.get('handoff_blocked',False), 'chapters':chapters,
    'current_pointer_unchanged_from_old_valid_plan':current_unchanged,
    'current_pointer':load(run/'CURRENT_PLAN.json'),
    'old_checkpoint_files_verified_unchanged':len(hashes),
    'claim_review_bound':'At most one owner revision and one recheck per chapter, unchanged',
    'scientific_quality_certified':False,
    'scope':'Authentic reused two-paper metalens materials; no new external search, BODY or Article decision benefit proof',
    'latest_source_commit':'4552848 (full commit in SOURCE_STATE.json)',
    'limitations':['Changed-request cache protection additionally covered by offline tests, not a separate new paid ablation',
                   'Old RUN_FAILURE remains a historical diagnostic file; latest PLANNING_RUN_REPORT governs current status']}
(ROOT/'review/LIVE_ACCEPTANCE.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in receipt.items() if k not in ('current_pointer','chapters')},ensure_ascii=False,indent=2))
