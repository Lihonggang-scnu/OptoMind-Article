"""Offline checks for program-owned native input and strict analysis validation."""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'vendor' / 'review'))
import task_sheet as ts


def example():
    return json.loads((ROOT / 'examples' / 'rgb_nir_task_sheet.json').read_text(encoding='utf-8'))


class OfflinePlanner:
    """Synthetic transport fixture; no provider client or network access."""
    def __init__(self, analysis, *, usage=None, transport=None):
        self.raw = json.dumps(analysis, ensure_ascii=False, indent=2)
        self.last_usage = usage or {}
        self.last_transport = transport or {}
        self.calls = []

    def _call_query_plan_model(self, prompt):
        self.calls.append(prompt)
        return self.raw


def test_analysis_without_echo_succeeds_and_keeps_raw_unchanged():
    analysis = example()
    question = analysis.pop('original_question')
    planner = OfflinePlanner(analysis)
    result = ts.generate(question, planner)
    assert result['status'] == 'ok' and result['first_hit']
    assert len(planner.calls) == 1
    assert result['task_sheet']['original_question'] == question
    assert result['plan']['task_sheet'] == result['task_sheet']
    record = result['rounds'][0]
    assert record['raw_response'] == planner.raw
    assert record['raw_task_sheet'] == analysis
    assert 'original_question' not in record['raw_task_sheet']
    assert record['ignored_model_fields'] == []
    assert record['raw_task_sheet'] is not result['task_sheet']


@pytest.mark.parametrize('echo', ['altered native question', None, {'unexpected': 'object'}])
def test_model_echo_is_ignored_and_recorded(echo):
    analysis = example()
    question = analysis['original_question']
    analysis['original_question'] = echo
    planner = OfflinePlanner(analysis)
    result = ts.generate(question, planner)
    assert result['status'] == 'ok' and result['revision_rounds'] == 0
    assert result['task_sheet']['original_question'] == question
    record = result['rounds'][0]
    assert record['ignored_model_fields'] == ['original_question']
    assert record['raw_task_sheet'] == analysis
    assert record['raw_response'] == planner.raw


def test_native_unicode_and_whitespace_are_preserved_exactly():
    analysis = example()
    native = analysis.pop('original_question')
    question = '\t\r\n  ' + native + '\n\n附注：保留  两个空格、\t制表符，e\u0301 / é，🙂。\r\n '
    planner = OfflinePlanner(analysis)
    result = ts.generate(question, planner)
    assert result['status'] == 'ok'
    assert result['task_sheet']['original_question'].encode('utf-8') == question.encode('utf-8')
    assert planner.calls[0]['user_payload']['original_question'] == question
    assert ts.validate(result['task_sheet'], question=question) == result['task_sheet']


@pytest.mark.parametrize('field', ['subgoals', 'application_intents', 'explicit_constraints', 'unsupported_requirements'])
def test_invalid_original_spans_still_fail_closed(field):
    analysis = example()
    question = analysis['original_question']
    invalid_span = 'this text exists only in the model echo'
    # Even a matching span in the ignored echo cannot establish provenance.
    analysis['original_question'] = question + invalid_span
    if field == 'unsupported_requirements':
        analysis[field] = [{'id': 'U1', 'requirement': 'Unsupported requirement',
            'original_span': invalid_span, 'reason': 'Unsupported metric'}]
        analysis['readiness'] = 'needs_revision'
    else:
        analysis[field][0]['original_span'] = invalid_span
    planner = OfflinePlanner(analysis)
    result = ts.generate(question, planner)
    assert result['status'] == 'invalid' and result['plan'] == {}
    assert result['task_sheet'] == {}
    assert len(planner.calls) == 2
    assert all('must be an exact span' in row['error'] for row in result['rounds'])
    assert all(row['raw_task_sheet'] == analysis for row in result['rounds'])


def test_prompt_separates_native_input_from_analysis_example():
    sheet = example()
    prompt = ts.render_prompt('本次输入  exact\n')
    assert prompt['user_payload'] == {'original_question': '本次输入  exact\n'}
    system = prompt['system_prompt']
    assert '\nWORKED EXAMPLE INPUT:\n' + sheet['original_question'] in system
    output = json.loads(system.split('\nWORKED EXAMPLE ANALYSIS OUTPUT (all shown fields required):\n', 1)[1])
    del sheet['original_question']
    assert output == sheet


@pytest.mark.parametrize('usage,transport,status', [
    ({'mock_llm': True}, {}, 'mock_rejected'),
    ({'mock': True}, {}, 'mock_rejected'),
    ({}, {'fallback_used': True}, 'transport_rejected'),
    ({}, {'transport_error': 'offline fixture'}, 'transport_rejected'),
])
def test_transport_guards_still_reject_before_assembly(usage, transport, status):
    analysis = example()
    question = analysis.pop('original_question')
    planner = OfflinePlanner(analysis, usage=usage, transport=transport)
    result = ts.generate(question, planner)
    assert result['status'] == status and result['plan'] == {}
    assert len(planner.calls) == 1
    assert 'raw_task_sheet' not in result['rounds'][0]
