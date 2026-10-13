"""Replay authentic journal copies through the production loop without API calls."""
from dataclasses import fields
from pathlib import Path
import hashlib
import json
import shutil
import sys

from optomind_research.runtime.upgrade3.planning_retrieval_loop import (
    InformationNeed, LoopConfig, _need_signature, _signature_matches, run_retrieval_loop,
)

root = Path(__file__).resolve().parent
case = root / 'review/formal-01'
destination = root / 'review/terminal-replay' / sys.argv[1]
destination.mkdir(parents=True, exist_ok=True)
records = []


def unexpected(*args, **kwargs):
    raise AssertionError('Recorded terminal need attempted a new callback on ordinary resume')


for phase in ('level1', 'level2', 'chapters'):
    stage = case / 'stages' / (phase + '_tools.json')
    journal_source = case / phase / 'retrieval_loop.jsonl'
    if not stage.is_file() or not journal_source.is_file():
        continue
    result = json.loads(stage.read_text(encoding='utf-8'))
    prior = result['retrieval_loop']
    journal_rows = [json.loads(line) for line in journal_source.read_text(encoding='utf-8').splitlines() if line.strip()]
    candidates = [InformationNeed(**x['need']) for x in prior['needs']]
    needs = [need for need in candidates if any(
        row.get('need_id') == need.need_id and row.get('action') == 'stop'
        and row.get('status') in ('stopped_no_query', 'stopped_same_query', 'stopped_budget', 'stopped_no_new_content')
        and _signature_matches(row.get('need_signature'), _need_signature(need)) for row in journal_rows)]
    if not needs:
        continue
    journal = destination / (phase + '.jsonl')
    shutil.copyfile(journal_source, journal)
    original = journal.read_bytes()
    cfg = {k: v for k, v in prior['config'].items() if k in {f.name for f in fields(LoopConfig)}}
    cfg.update(index_path=Path(cfg['index_path']), journal_path=journal)
    config = LoopConfig(**cfg)
    after = run_retrieval_loop(needs, config, local_triage=unexpected, external_closure=unexpected,
                               refine_queries=unexpected, prior_consumed_paper_ids=prior['consumed_paper_ids'])
    assert journal.read_bytes() == original
    second = run_retrieval_loop(needs, config, local_triage=unexpected, external_closure=unexpected,
                                refine_queries=unexpected, prior_consumed_paper_ids=prior['consumed_paper_ids'])
    projected = lambda data: [{k: row[k] for k in ('need_id', 'status', 'action', 'rounds', 'empty_rounds', 'usable_content', 'still_missing')} for row in data['needs']]
    assert projected(after) == projected(second)
    assert journal.read_bytes() == original
    rows = []
    for state in after['needs']:
        rows.append({'need_id': state['need_id'], 'status': state['status'], 'action': state['action'],
                     'rounds': state['rounds'], 'usable_chars': len(state['usable_content']),
                     'usable_sha256': hashlib.sha256(state['usable_content'].encode()).hexdigest(),
                     'still_missing': state['still_missing']})
    records.append({'phase': phase, 'excluded_unrelated_needs': len(candidates) - len(needs),
                    'journal_sha256': hashlib.sha256(original).hexdigest(),
                    'journal_unchanged': True, 'identical_replay_same_material_status': True,
                    'needs': rows})
assert records
report = {'status': 'passed', 'production_function': 'run_retrieval_loop', 'source_head': __import__('subprocess').check_output(
    ['git', '-C', 'D:/OptoMind-Article-3/repos/OptoMInd-Review', 'rev-parse', 'HEAD'], text=True).strip(),
    'new_callbacks': 0, 'new_provider_calls': 0, 'fee_cny': 0, 'phases': records,
    'scope': 'Actual real-run journals copied outside repos; affected production replay only, not an entire fresh planning CLI run.'}
(destination / 'ACCEPTANCE.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2))
