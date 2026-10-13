"""Original request drift must consume the existing bounded compiler repair."""
from copy import deepcopy
import json
from pathlib import Path

from optomind_optics.harness.task_compiler import QwenTMMTaskCompiler


def fixture():
    return json.loads((Path(__file__).parents[1] / 'fixtures/tmm_harness/formal01_controls.json').read_text(encoding='utf-8'))


class RecordedClient:
    model_name = 'qwen3.7-flash'

    def __init__(self, *responses):
        self.responses = list(responses)
        self.messages = []

    def call(self, messages, **kwargs):
        self.messages.append(messages)
        return {'content': json.dumps(self.responses.pop(0))}


def test_constant_index_drift_is_returned_to_model_before_accepting_draft():
    saved = fixture()
    correct = saved['compiler_draft']
    wrong = deepcopy(correct)
    layers = wrong['experiments'][0]['tmm_task']['simulation']['stack']['layers']
    for layer, material in zip(layers, ('sio2', 'tio2')):
        layer['material'] = material
        layer.pop('constant_n')
        layer.pop('constant_k')
    client = RecordedClient(wrong, correct)
    result = QwenTMMTaskCompiler(client=client).compile(saved['original_request'])
    assert result.status == 'compiled' and result.attempts == 2
    assert len(result.raw_response_sha256) == 2
    repair = json.loads(client.messages[1][1]['content'])
    assert any('constant-index layer differs' in error for error in repair['repair_request']['validation_errors'])
    assert repair['original_request_constraints']['frozen_constraints']['material_indices'] == {'sio2': 1.46, 'tio2': 2.4}
    assert all(layer['material'] is None for layer in result.task.experiments[0].tmm_task['simulation']['stack']['layers'])


def test_unrepaired_physical_drift_stays_invalid_and_never_relaxes_contract():
    saved = fixture()
    wrong = deepcopy(saved['compiler_draft'])
    wrong['experiments'][0]['tmm_task']['simulation']['stack']['layers'][0]['constant_n'] = 1.47
    result = QwenTMMTaskCompiler(client=RecordedClient(wrong, wrong)).compile(saved['original_request'])
    assert result.status == 'invalid' and result.task is None and result.attempts == 2
    assert any('CHARTER_DRIFT_ERROR' in error for error in result.validation_errors)


def test_permitted_order_clarification_uses_existing_single_repair():
    saved = fixture()
    clarification = {'status': 'needs_clarification', 'rationale': (
        'The bounded task schema supports one fixed stack sequence per experiment; '
        'please choose between the two layer orders.'), 'experiments': []}
    client = RecordedClient(clarification, saved['compiler_draft'])
    result = QwenTMMTaskCompiler(client=client).compile(saved['original_request'])
    assert result.status == 'compiled' and result.attempts == 2
