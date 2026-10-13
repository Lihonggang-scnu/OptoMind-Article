"""Audit an actual Windows checkpoint copy and continuation without new calls."""
import hashlib
import json
from pathlib import Path
import sqlite3

root = Path(__file__).resolve().parent
parent = root / 'article/pause-05'
resumed = root / 'article/resume-05'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


before = read(parent / 'ITERATION_HISTORY.json')
after = read(resumed / 'ITERATION_HISTORY.json')
assert len(before) == 1 and len(after) == 2
assert after[:1] == before, 'Restored observation prefix changed'
assert read(parent / 'SCORING_STANDARD.json') == read(resumed / 'SCORING_STANDARD.json')
databases = []
for source in (parent / 'iterations/iteration_01').rglob('*'):
    if not source.is_file() or source.suffix not in ('.sqlite', '.sqlite3', '.db'):
        continue
    relative = source.relative_to(parent)
    target = resumed / relative
    a = hashlib.sha256(source.read_bytes()).hexdigest()
    b = hashlib.sha256(target.read_bytes()).hexdigest()
    assert a == b, str(relative)
    with sqlite3.connect(target.resolve().as_uri() + '?mode=ro', uri=True) as db:
        check = db.execute('PRAGMA quick_check').fetchall()
        assert check == [('ok',)], (str(relative), check)
        journal = db.execute('PRAGMA journal_mode').fetchone()[0]
    databases.append({'relative_path': relative.as_posix(), 'sha256_equal': True,
                      'quick_check': 'ok', 'journal_mode': journal})
result = read(resumed / 'RESEARCH_RESULT.json')
forward_sum = sum(row['budget_usage'].get('forward_evaluations', 0) for row in after)
assert forward_sum == result['telemetry']['forward_evaluations'] == 106
old_candidates = {row['candidate_id']: row for row in before[0]['candidate_summaries']}
new_candidates = {row['candidate_id']: row for row in after[0]['candidate_summaries']}
assert old_candidates == new_candidates
record = {'status': 'passed', 'parent': str(parent), 'resumed': str(resumed),
          'observations_before': 1, 'observations_after': 2, 'prefix_exact_equal': True,
          'frozen_standard_exact_equal': True, 'candidate_statistics_exact_equal': True,
          'floating_point_differences': [], 'forward_total': forward_sum,
          'per_round_forward': [row['budget_usage']['forward_evaluations'] for row in after],
          'sqlite_copies': databases, 'production_status': result['status'],
          'production_acceptance': result['acceptance_status'],
          'spend_ledger': 'shared original external ledger; never copied to repository',
          'round_policy': 'host initial ceiling1 then continuation ceiling2, original maximum2 unchanged'}
(root / 'article/RECOVERY_ACCEPTANCE.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
print(json.dumps(record, indent=2))
