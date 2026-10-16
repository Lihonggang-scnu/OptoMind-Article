"""Read-only extension of the original audited expense identity map for T042."""
from pathlib import Path
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
import collections, hashlib, json, sqlite3

R = Path(__file__).resolve().parent
T = R.parent
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p): return json.loads(Path(p).read_text(encoding='utf-8'))
hpath = T/'R3-T040/CASE_HISTORY_FOR_GUARD.json'
h = read(hpath)
assert h['attribution_complete']
evidence = list(h['evidence'])
for item in evidence:
    assert sha(item['path']) == item['sha256'], item['path']
classification = dict(h['classification_by_reservation_id'])
assert set(classification) == set(h['all_reservation_ids_at_audit'])
extra = {
    'R3-T019-DEDICATED20/rejected-drafts/round-1.task_sheet.json': 'RGB/NIR, TiO2/SiO2, fused silica, 45 degrees',
    'R3-T022/runs/plan/task_sheet.json': 'same RGB/NIR retrieval/candidate chain',
    'R3-T025/runs/routes/task_sheet.json': 'same RGB/NIR candidate chain',
    'R3-T028/refined/task_sheet.json': 'same RGB/NIR numerical continuation',
}
for rel, scope in extra.items():
    p = T/rel; obj = read(p); question = obj.get('original_question', obj.get('question',''))
    assert 'TiO2' in question and 'SiO2' in question and '450-650' in question and '700-1000' in question
    evidence.append({'path': str(p), 'sha256': sha(p), 'case_scope': scope,
                     'original_question_sha256': hashlib.sha256(question.encode()).hexdigest()})
evidence.append({'path': str(hpath), 'sha256': sha(hpath), 'purpose': 'prior complete identity classification; all 132 supporting files rehashed'})
paths = [Path(r'D:\OptoMind-Article-3\local-functional-trial\20261013\budget.sqlite'), T/'R3-T019-DEDICATED20/private/budget.sqlite']
unique = {}; links = []; ledger_receipts = []
for path in paths:
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(r) for r in db.execute('SELECT * FROM reservations')]
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='r3_task_calls'").fetchone():
            links.extend(dict(r) for r in db.execute('SELECT reservation_id,task_id FROM r3_task_calls'))
        for row in rows:
            k = row['reservation_id']
            assert k not in unique or unique[k] == row
            assert row['status'] == 'settled', 'unresolved exposure; no paid admission'
            unique[k] = row
    ledger_receipts.append({'path': str(path), 'sha256': sha(path), 'reservations': len(rows)})
expected_new_tasks = {'R3-T032:e2e-01-methane-swir-window', 'R3-T032:e2e-05-visual-absorber-cr',
                     'R3-T037:e2e-04-solarblind-uv-triple', 'R3-T038:e2e-04-solarblind-uv-triple',
                     'R3-T040:e2e-04-solarblind-uv-triple'}
for item in links:
    if item['reservation_id'] not in classification:
        assert item['task_id'] in expected_new_tasks, item['task_id']
        classification[item['reservation_id']] = 'native_task:'+item['task_id']
assert set(classification) == set(unique), 'unattributed expense blocks admission'
assert len(unique) == 358
assert not any('lwir' in v.lower() or 'case12' in v.lower() for v in classification.values())
total = sum((Decimal(str(r['actual_cny'] or 0)) for r in unique.values()), Decimal(0))
assert total < 220
result = {'attribution_complete': True, 'checked_at': datetime.now(timezone.utc).isoformat(),
          'all_reservation_ids_at_audit': sorted(unique), 'case_reservation_ids': {'e2e-12-lwir-ge-window': []},
          'classification_by_reservation_id': classification, 'classification_counts': dict(collections.Counter(classification.values())),
          'case12_historical_exposure_cny': '0', 'project_settled_cny': str(total), 'unresolved_count': 0,
          'basis': 'Complete original 346-identity audit and 132 unchanged supporting files; 12 subsequent identities directly linked to cases01/05/04; RGB/NIR chain question bytes separately checked. Review-only requests and visible two-layer/metalens trials retain original attribution, not LWIR Ge cold start.',
          'evidence': evidence, 'ledgers': ledger_receipts, 'ledger_writes': 0, 'model_calls': 0,
          'not_original_T032_three_case_batch': True, 'original_case_cap_cny': 8, 'project_cap_cny': 220,
          'task_id': 'R3-T042:e2e-12-lwir-ge-window', 'task_max_http': 4}
with (R/'CASE_HISTORY_FOR_GUARD.json').open('x', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2); f.write('\n')
print(json.dumps({k:result[k] for k in ['attribution_complete','case12_historical_exposure_cny','project_settled_cny','unresolved_count','ledger_writes']}))
