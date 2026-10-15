"""Provider completion metadata survives the original client/planner boundary."""
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'vendor' / 'review'))
from llm import qwen_chat_client as client
from optomind_research import query_planner


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('finish_reason', ['stop', 'length'])
def test_client_preserves_finish_reason_without_changing_token_cap(monkeypatch, stream, finish_reason):
    monkeypatch.setattr(client, 'get_qwen_client_config', lambda tier: {
        'model': 'offline-fixture', 'mock_llm': False,
        'api_key': 'not-a-real-key', 'base_url': 'https://offline.invalid'})
    if stream:
        events = [
            {'choices': [{'delta': {'content': '{}'}, 'finish_reason': None}]},
            {'choices': [{'delta': {}, 'finish_reason': finish_reason}]},
            {'choices': [], 'usage': {'prompt_tokens': 10, 'completion_tokens': 20}}]
        raw = ''.join('data: ' + json.dumps(event) + '\n\n' for event in events) + 'data: [DONE]\n\n'
    else:
        raw = json.dumps({'choices': [{'message': {'content': '{}'}, 'finish_reason': finish_reason}],
                          'usage': {'prompt_tokens': 10, 'completion_tokens': 20}})
    calls = []
    def offline_request(request, timeout):
        calls.append(json.loads(request.data))
        return io.BytesIO(raw.encode())
    monkeypatch.setattr(client, '_open_qwen_request', offline_request)
    result = client.call_qwen_chat('offline', [{'role': 'user', 'content': 'Return JSON'}],
                                   stream=stream, max_tokens=12345, max_retries=0)
    assert result['content'] == '{}'
    assert result['finish_reason'] == finish_reason
    assert result['_llm_usage']['finish_reason'] == finish_reason
    assert result['_llm_usage']['output_tokens'] == 20
    assert len(calls) == 1
    assert calls[0]['max_tokens'] == 12345


def test_planner_propagates_completion_and_partial_stream_metadata(monkeypatch):
    monkeypatch.setattr(query_planner, 'call_qwen_chat', lambda *a, **kw: {
        'content': '{}', 'finish_reason': 'length',
        '_llm_usage': {'partial_stream': True, 'stream_interrupted_error': 'TimeoutError'}})
    planner = query_planner.QueryPlannerAgent()
    assert planner._call_query_plan_model({'system_prompt': 'JSON', 'user_payload': {}}) == '{}'
    assert planner.last_transport['finish_reason'] == 'length'
    assert planner.last_transport['partial_stream'] is True
    assert planner.last_transport['stream_interrupted_error'] == 'TimeoutError'
