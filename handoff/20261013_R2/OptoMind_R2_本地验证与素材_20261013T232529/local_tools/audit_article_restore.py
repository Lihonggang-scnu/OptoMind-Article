"""Read-only verification of a real frozen-score Windows continuation.

No model calls, checkpoint edits or solver/request correction. Written after
the native run; the comparison is not claimed to be a pre-run source snapshot.
"""
from pathlib import Path
import hashlib
import json
import sqlite3

from optomind_optics.harness.scoring_standard import ScoringStandard

ROOT = Path(__file__).resolve().parent
parent = ROOT / 'article/pause-02'
resumed = ROOT / 'article/resume-02'

def load(path):
    return json.loads(path.read_text(encoding='utf-8'))

before, after = (load(p / 'ITERATION_HISTORY.json') for p in (parent, resumed))
assert len(before) == 1 and len(after) == 2
assert after[:len(before)] == before, 'Observation history was changed during restore'
standard_payload = load(resumed / 'SCORING_STANDARD.json')
assert load(parent / 'SCORING_STANDARD.json') == standard_payload
standard = ScoringStandard.model_validate(standard_payload['standard'])
fingerprint = hashlib.sha256(json.dumps(standard.model_dump(mode='json'), sort_keys=True,
    separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
assert standard.formula == '-mean_reflectance_450_700nm_0deg_s'
assert standard.metrics[0].sense == 'minimize'
assert standard.metrics[0].mean_reduction == 'sample_mean'
assert standard.metrics[0].target == 0.03

round_checks = []
history = []
reflection_receipts = []
for row in after:
    assert row['score_basis'] == 'frozen_standard'
    assert row['score_basis_fingerprint'] == fingerprint and row['score_available']
    scores = []
    for candidate in row['candidate_summaries']:
        scored = standard.score(candidate['objective_report'])
        assert scored.ok and scored.value == candidate['frozen_score']
        assert candidate['score_basis_fingerprint'] == fingerprint
        independent_rows = load(resumed / 'INDEPENDENT_ACCEPTANCE.json')['independently_checked_candidates']
        independent = next(x for x in independent_rows if x['iteration_id'] == row['iteration_id']
                           and x['candidate_id'] == candidate['candidate_id'])
        assert abs(scored.value + independent['sample_mean']) <= 1e-12
        scores.append(scored.value)
    assert row['best_frozen_score'] == max(scores)
    history.append(max(scores))
    context = load(resumed / 'iterations' / row['iteration_id'] / 'DECISION_CONTEXT.json')
    payload = json.loads(context['actual_messages'][1]['content'])
    metrics = payload['observed_metrics']
    assert metrics['decision_score'] == row['best_frozen_score']
    assert metrics['score_basis'] == 'frozen_standard'
    assert metrics['score_basis_fingerprint'] == fingerprint
    assert payload['score_history'] == history
    assert payload['score_history_basis_fingerprint'] == fingerprint
    # Verify these actual messages also reached the paid transport, not only a sidecar.
    receipts = []
    for request in (ROOT / 'calls').glob('article-*/REQUEST.json'):
        if load(request)['messages'] == context['actual_messages']:
            receipts.append(request.parent.name)
    assert receipts, 'No actual transport receipt matches saved reflection context'
    reflection_receipts.append({'iteration_id': row['iteration_id'], 'transport_receipts': receipts})
    round_checks.append({'iteration_id': row['iteration_id'], 'best_frozen_score': max(scores),
        'best_legacy_target_score_diagnostic': row['best_target_score'], 'score_history': list(history)})

events = [json.loads(line) for line in (resumed / 'RESEARCH_EVENTS.jsonl').read_text().splitlines()]
ledgers = [x for x in events if x.get('event_type') == 'action_effect_ledger']
assert len(ledgers) == 2
for i, entry in enumerate(ledgers):
    assert entry['expected_effect']['metric'] == 'frozen_score'
    assert entry['observed_effect']['metric_after'] == history[i]
    if entry['expected_effect']['expected_delta'] is None:
        assert entry['belief_update']['rule'] == 'no_signal'
        assert entry['belief_update']['before'] == entry['belief_update']['after']
    if i:
        assert entry['observed_effect']['metric_before'] == history[i-1]
        assert entry['parent_state_hash'] == ledgers[i-1]['child_state_hash']

databases = []
for source in (parent / 'iterations/iteration_01').rglob('*'):
    if not source.is_file() or source.suffix not in ('.sqlite', '.sqlite3', '.db'):
        continue
    relative = source.relative_to(parent)
    target = resumed / relative
    assert hashlib.sha256(source.read_bytes()).digest() == hashlib.sha256(target.read_bytes()).digest()
    with sqlite3.connect(target.resolve().as_uri() + '?mode=ro', uri=True) as db:
        assert db.execute('PRAGMA quick_check').fetchall() == [('ok',)]
        mode = db.execute('PRAGMA journal_mode').fetchone()[0]
    databases.append({'relative_path': relative.as_posix(), 'byte_equal': True,
                      'quick_check': 'ok', 'journal_mode': mode})

result = load(resumed / 'RESEARCH_RESULT.json')
forward = sum(x['budget_usage'].get('forward_evaluations', 0) for x in after)
assert forward == result['telemetry']['forward_evaluations']
assert all(x['budget_usage']['forward_evaluations'] <= 64 for x in after)
assert before[0]['candidate_summaries'] == after[0]['candidate_summaries']
assert load(parent / 'REQUEST.json')['question'] == load(resumed / 'REQUEST.json')['question']
answer = load(resumed / 'FINAL_ANSWER.json')
assert answer['original_request'] == load(resumed / 'REQUEST.json')['question']
ranking = load(resumed / 'SCORING_RANKING.json')
assert ranking['routes'][0]['representative']['score'] == max(history)
assert '0.03' in answer['markdown'] and 'physical infeasibility' in answer['markdown']
record = {'status': 'passed', 'parent': str(parent), 'resumed': str(resumed),
    'comparison_started_after_native_run': True,
    'history_prefix_exact_equal': True, 'frozen_standard_exact_equal': True,
    'candidate_statistics_exact_equal': True, 'floating_point_differences': [],
    'fingerprint': fingerprint, 'rounds': round_checks, 'reflection_transport_receipts': reflection_receipts,
    'ledger_entries': ledgers, 'sqlite_copies': databases, 'forward_total': forward,
    'production_status': result['status'], 'production_acceptance': result['acceptance_status'],
    'original_target_met': False,
    'scientific_scope': 'Two-layer constant-index synthetic simulation; no infeasibility or optimality certificate',
    'round_policy': 'Host first ceiling1, continuation ceiling2; unchanged original maximum2',
    'spend_ledger': 'Same original external ledger; not copied to repositories or uploaded'}
(ROOT / 'article/RECOVERY_ACCEPTANCE.json').write_text(json.dumps(record, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps({k:v for k,v in record.items() if k not in ('ledger_entries','sqlite_copies')}, ensure_ascii=False, indent=2))
