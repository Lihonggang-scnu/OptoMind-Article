"""Offline call-path/retry evidence only. No live client, credentials, or DB."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
SOURCE = Path(r'D:\OptoMind-Article-3\r3t019a')
FOCUS = SOURCE / 'focused_research'
WRAPPER = ROOT.parent / 'R3-T001-INTEGRATE-OFFLINE' / 'release/local_tools/budgeted_entry.py'
EXPECTED_WRAPPER = '1a6832ade3f4b05d24a31102d106894d97e8e1f4a7af24883349783c4f88d512'
assert hashlib.sha256(WRAPPER.read_bytes()).hexdigest() == EXPECTED_WRAPPER
assert not (ROOT / 'offline/BUDGET_COVERAGE.json').exists()
sys.path[:0] = [str(FOCUS / 'vendor/review'), str(FOCUS), str(WRAPPER.parent)]

spec = importlib.util.spec_from_file_location('original_budgeted_entry', WRAPPER)
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)
import task_sheet
import optomind_research.query_planner as planner_module
import llm.qwen_chat_client as transport
from config.qwen_config import get_model_name, get_fallback_model_names

# The original factory subclasses QwenDirectClient. Creating the class does not
# install a hook into call_qwen_chat; no production ledger is instantiated here.
counts = {'wrapped_native_calls': 0, 'guard_for_request_calls': 0}
class NativeSentinel:
    def __init__(self, **kwargs):
        raise AssertionError('Native sentinel must not be instantiated in this probe')
    def __call__(self, *args, **kwargs):
        counts['wrapped_native_calls'] += 1
        raise AssertionError('No real client allowed')
class GuardSentinel:
    def for_request(self, *args, **kwargs):
        counts['guard_for_request_calls'] += 1
        raise AssertionError('No ledger access allowed')

Audited = entry.make_audited_client(
    SimpleNamespace(QwenDirectClient=NativeSentinel), GuardSentinel(),
    {'task': {'task_id': 'R3-T019-FOCUSED-TASK-SHEET'},
     'paths': {'audit_root': str(ROOT / 'unused-audit-sentinel')}})
assert issubclass(Audited, NativeSentinel)
assert planner_module.call_qwen_chat is transport.call_qwen_chat

tree = ast.parse(WRAPPER.read_text(encoding='utf-8'))
run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run')
entry_imports = [{'module': n.module, 'line': n.lineno}
    for n in ast.walk(run) if isinstance(n, ast.ImportFrom)
    and any(x.asname == 'entry' for x in n.names)]
assert {r['module'] for r in entry_imports} == {
    'scripts.run_tmm_research_harness', 'scripts.upgrade3.progressive_review_plan'}

# Resolve public model policy only. Never call the real key/config loader.
primary = get_model_name('QueryPlannerAgent')
fallbacks = get_fallback_model_names('QueryPlannerAgent')
transport.get_qwen_client_config = lambda tier: {
    'model': 'qwen3.5-plus', 'mock_llm': False,
    'fallback_models': [], 'base_url': 'https://offline-budget-probe.invalid/v1',
    'api_key_candidates': [{'api_key': 'OFFLINE_SENTINEL_NOT_A_CREDENTIAL',
                            'api_key_source': 'synthetic_fixture'}]}
transport.time.sleep = lambda seconds: None
attempts = []
class FakeResponse:
    def read(self):
        # Intentionally invalid task sheet, so the real contract loop repairs.
        return json.dumps({'choices': [{'message': {'content': '{}'}}],
                           'usage': {'prompt_tokens': 1, 'completion_tokens': 1}}).encode()
    def close(self):
        pass
def fake_open(request, timeout):
    number = len(attempts) + 1
    body = json.loads(request.data)
    attempts.append({'simulated_attempt': number, 'model': body['model'],
                     'max_tokens': body['max_tokens'],
                     'result': 'connection_reset' if number % 2 else 'invalid_sheet'})
    if number % 2:
        raise ConnectionResetError('Offline synthetic retry fixture')
    return FakeResponse()
transport._open_qwen_request = fake_open
planner = planner_module.QueryPlannerAgent(max_tokens=7000)
outcome = task_sheet.generate('OFFLINE_BUDGET_RETRY_PROBE_ONLY', planner)
assert outcome['status'] == 'invalid'
assert len(outcome['rounds']) == 2
assert len(attempts) == 4
assert all(r['usage']['request_attempt_count'] == 2 for r in outcome['rounds'])
assert counts == {'wrapped_native_calls': 0, 'guard_for_request_calls': 0}

report = {
    'task_id': 'R3-T019-FOCUSED-TASK-SHEET', 'in_reply_to': 'C2L-R3-0032',
    'source_commit': '713799fb55899b78427aed56cb643bc72526c19d',
    'decision': 'blocked_no_paid_dispatch',
    'original_wrapper_sha256': EXPECTED_WRAPPER,
    'original_wrapper_entry_imports': entry_imports,
    'original_wrapper_hook_targets': [
        'optomind_optics.harness.problem_analyzer.ArticlePlusQwenClient.call',
        'optomind_optics.harness.qwen_policy.QwenFlashOnlyClient.call',
        'optomind_research.runtime.upgrade3.module4.runtime.QwenDirectClient'],
    'focused_entry_call_path': [
        'focused_research/run.py:47 generate_plan',
        'focused_research/task_sheet.py:231 generate',
        'focused_research/vendor/review/optomind_research/query_planner.py:421 _call_query_plan_model',
        'focused_research/vendor/review/llm/qwen_chat_client.py:292 call_qwen_chat',
        'focused_research/vendor/review/llm/qwen_chat_client.py:48 _open_qwen_request'],
    'original_wrapper_has_focused_cli_route': False,
    'original_guard_reservation_before_focused_http_verified': False,
    'incremental_2_cny_cap_on_focused_path_verified': False,
    'max_3_attempts_across_transport_and_repair_verified': False,
    'unit_probe_scope': 'Unwrapped focused call path with synthetic transport; not an execution through the original wrapper and not a live result',
    'synthetic_transport_attempts': attempts,
    'synthetic_planner_rounds': len(outcome['rounds']),
    'synthetic_attempts_by_round': [r['usage']['request_attempt_count'] for r in outcome['rounds']],
    'synthetic_guard_spy': counts,
    'actual_http_attempts': 0, 'paid_model_calls': 0, 'new_spend_cny': 0,
    'ledger_instantiated_or_written': False, 'credential_loader_called': False,
    'public_policy_primary_model': primary, 'public_policy_fallbacks': fallbacks,
    'required_next_change': 'Cloud must connect this independent planner transport to the original same-ledger guard, cost reservation and task-wide attempt counter, preserving standalone import protection; then issue a new fixed-SHA task.'}
(ROOT / 'offline/BUDGET_COVERAGE.json').write_text(
    json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
print(json.dumps(report, ensure_ascii=False, indent=2))
