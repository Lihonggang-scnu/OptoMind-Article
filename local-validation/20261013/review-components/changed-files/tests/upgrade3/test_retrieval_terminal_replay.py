"""Resume preserves cumulative material status and the original stop decision."""
import json

import pytest

from optomind_research.runtime.upgrade3.planning_material_search import PlanningMaterialIndex
from optomind_research.runtime.upgrade3.planning_retrieval_loop import (
    InformationNeed, LoopConfig, RetrievalJournal, _need_signature, run_retrieval_loop,
)


@pytest.fixture
def case(tmp_path):
    config = LoopConfig(index_path=tmp_path / 'index.sqlite', journal_path=tmp_path / 'journal.jsonl', max_rounds=3)
    with PlanningMaterialIndex(config.index_path) as index:
        index.commit()
    need = InformationNeed('bounded-need', 'Which conditions remain unknown?', owners=('CH1',))
    return config, need


@pytest.mark.parametrize('stop', ['stopped_no_query', 'stopped_same_query', 'stopped_budget', 'stopped_no_new_content'])
@pytest.mark.parametrize('content', ['', 'A supplied partial measurement.'])
def test_terminal_journal_replay_preserves_material_status_and_stop(case, stop, content):
    config, need = case
    journal = RetrievalJournal(config.journal_path)
    journal.record({'need_id': need.need_id, 'need_signature': _need_signature(need), 'round': 1,
                    'status': 'partial', 'action': 'external_research', 'usable_content': content,
                    'still_missing': need.question})
    journal.record({'need_id': need.need_id, 'need_signature': _need_signature(need), 'round': 2,
                    'status': stop, 'action': 'stop', 'usable_content': content,
                    'still_missing': need.question, 'empty_rounds': 1})
    original = config.journal_path.read_bytes()

    def unexpected(*args, **kwargs):
        pytest.fail('Ordinary terminal resume must not request another scientific or model round')

    result = run_retrieval_loop([need], config, local_triage=unexpected, external_closure=unexpected, refine_queries=unexpected)
    expected = 'partial' if content and stop != 'stopped_no_new_content' else 'stopped'
    state = result['needs'][0]
    assert state['status'] == expected
    assert state['action'] == 'stop'
    assert state['usable_content'] == content
    assert state['still_missing'] == need.question
    assert state['rounds'] == 2
    assert state['empty_rounds'] == 1
    assert result['owner_content'][need.need_id]['CH1']['status'] == expected
    assert config.journal_path.read_bytes() == original


def test_fresh_no_query_stop_and_resume_return_same_partial_material(case):
    config, original = case
    need = InformationNeed(original.need_id, original.question, owners=original.owners, round_specs=(
        {'round': 1, 'targeted_queries': [{'query_type': 'keyword', 'query_text': 'measurement conditions'}]},
    ))
    calls = []

    def external(**kwargs):
        calls.append(kwargs)
        return {'status': 'partial', 'usable_content': 'A supplied partial measurement.', 'still_missing': need.question}

    first = run_retrieval_loop([need], config, external_closure=external)
    saved = config.journal_path.read_bytes()
    assert json.loads(saved.splitlines()[-1])['status'] == 'stopped_no_query'
    second = run_retrieval_loop([need], config, external_closure=external)
    for key in ('status', 'action', 'usable_content', 'still_missing', 'rounds', 'empty_rounds'):
        assert first['needs'][0][key] == second['needs'][0][key]
    assert first['needs'][0]['status'] == 'partial'
    assert len(calls) == 1
    assert config.journal_path.read_bytes() == saved
