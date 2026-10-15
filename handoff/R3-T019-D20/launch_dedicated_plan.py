"""Opt-in dedicated20 launcher for unchanged focused main; plan stage only."""
from __future__ import annotations
import argparse
import datetime
from decimal import Decimal
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
SOURCE_SHA = '713799fb55899b78427aed56cb643bc72526c19d'
RUNTIME_SHA = '7fa510a1b9da12a4403868259bb2bd124e36b0d2b28718226af485d74c00c005'
TOOLS = ROOT.parent / 'R3-T001-INTEGRATE-OFFLINE/release/local_tools'
ORIGINAL = Path(r'D:\OptoMind-Article-3\local-functional-trial\20261013\budget.sqlite')
def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))
def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def new(name, data):
    path = ROOT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result

args = argparse.ArgumentParser(description=__doc__)
args.add_argument('--execute', action='store_true', help='Require reviewed admission; may dispatch paid Qwen')
args = args.parse_args()
config = read(ROOT / 'RUN_CONFIG.local-only.json')
authorization = read(ROOT / 'AUTHORIZATION.json')
source = Path(config['paths']['source_repo'])
runtime_path = Path(config['paths']['review_runtime'])
ledger = Path(config['paths']['ledger'])
assert config['budget'] == {'limit_cny': 20, 'mode': 'dedicated', 'max_http_attempts': 3}
assert config['task']['task_id'] == 'R3-T019-DEDICATED20'
assert ledger.resolve() == (ROOT / 'private/budget.sqlite').resolve()
assert ledger.resolve() != ORIGINAL.resolve() and ledger.is_file()
assert sha(ORIGINAL) == authorization['original_accounting_summary']['sha256']
assert sha(runtime_path) == RUNTIME_SHA
assert sha(TOOLS / 'budgeted_entry.py') == '1a6832ade3f4b05d24a31102d106894d97e8e1f4a7af24883349783c4f88d512'
assert sha(TOOLS / 'task_budget_guard.py') == 'd09c24cb6895790dc1c53a6807e743b09a8737135d65ec081c9b7c206060c461'
assert subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip() == SOURCE_SHA
assert not subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain=v1'], text=True).strip()
qfile = Path(config['question_file'])
assert sha(qfile) == config['question_file_sha256']
question = read(qfile)['original_question']
output = Path(config['output'])
assert output.resolve() == (ROOT / 'runs/real-plan-01').resolve()
assert not output.exists() or not any(output.iterdir())
assert read(ROOT / 'GITHUB_CLAIM_REFERENCES.json')['remote_verified']
if args.execute:
    admission = read(ROOT / 'ADMISSION.json')
    assert admission['execution_authorized'] and admission['offline_checks_passed']
    assert admission['source_sha'] == SOURCE_SHA
    assert admission['adapter_sha256'] == sha(ROOT / 'transport/dedicated_qwen_adapter.py')
    assert admission['launcher_sha256'] == sha(Path(__file__))
    assert not (ROOT / 'RUN_STARTED.json').exists(), 'Do not repeat a started paid run'

# Same isolated interpreter and source loading as the admitted offline entry.
runner = module(source / 'focused_research/run.py', '_dedicated20_focused_runner')
planner = importlib.import_module('optomind_research.query_planner')
vendor = (source / 'focused_research/vendor/review').resolve()
origins = {}
for name in ('config', 'config.qwen_config', 'config.secret_pool', 'llm.qwen_chat_client',
             'optomind_research.query_planner'):
    loaded = importlib.import_module(name)
    actual = Path(loaded.__file__).resolve()
    assert actual.is_relative_to(vendor), (name, str(actual))
    origins[name] = str(actual)
assert 'optomind_optics' not in sys.modules and 'tmm_engine' not in sys.modules
from config.qwen_config import get_model_name
model_name = get_model_name('QueryPlannerAgent')
assert model_name == 'qwen3.5-plus'
from transport.dedicated_qwen_adapter import install_focused_transport, load_native_runtime, run_plan_only
runtime = load_native_runtime(runtime_path)
guard_module = module(TOOLS / 'task_budget_guard.py', '_dedicated20_parent_guard')
native = runtime.GlobalBudgetLedger(limit_cny=20, path=ledger)
guard = guard_module.TaskBudgetGuard(native, task_id='R3-T019-DEDICATED20', lane='focused_plan',
    task_limit_cny=20, max_calls=3, checkpoint_cny=None, global_limit_cny=20)
before = guard.as_dict()['task_budget']
assert before['attempted_calls'] == 0 and before['unresolved_count'] == 0
adapter_config = {'ledger_path': str(ledger), 'audit_root': config['paths']['audit_root'],
    'key_file': config['paths']['key_file'], 'task_id': config['task']['task_id'],
    'model': model_name, 'task_limit_cny': 20, 'global_limit_cny': 20,
    'max_calls': 3, 'max_retries': 0, 'max_keys': 1, 'checkpoint_cny': None,
    'max_tokens': 7000, 'json_mode': True}
argv = ['plan', '--question', question, '--output', str(output)]
with install_focused_transport(planner, runtime, guard, adapter_config) as installed:
    prepared = {'task_id': config['task']['task_id'], 'source_sha': SOURCE_SHA,
        'source_repo': str(source), 'python': sys.executable, 'python_version': sys.version,
        'actual_module_origins': origins, 'accounting_source_origins': installed.source_origins,
        'model': model_name, 'temperature': 0.1, 'max_output_tokens': 7000,
        'json_mode': True, 'thinking': False, 'native_retries': 0, 'key_slots': 1,
        'stage': 'plan', 'budget_before': before, 'ledger_mode': 'dedicated20',
        'question_sha256': hashlib.sha256(question.encode()).hexdigest(),
        'adapter_sha256': sha(ROOT / 'transport/dedicated_qwen_adapter.py')}
    if not args.execute:
        new('PREPARED_ENVIRONMENT.json', prepared)
        print(json.dumps({'prepared': True, 'model': model_name, 'budget': 20,
                          'http_attempts': 0, 'old_ledger_unchanged': sha(ORIGINAL) == authorization['original_accounting_summary']['sha256']}))
        raise SystemExit(0)
    new('RUN_STARTED.json', {**prepared, 'argv': argv,
        'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'admission_reference': 'ADMISSION.json'})
    state_path = ROOT.parent.parent / 'state.json'
    state = read(state_path)
    assert state['active_task']['task_id'] == config['task']['task_id']
    state['active_task']['phase'] = 'one_real_plan_running_dedicated20'
    temporary = state_path.with_name('state.d20.running.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    os.replace(temporary, state_path)
    exit_code = 2
    exception_summary = None
    try:
        exit_code = run_plan_only(runner, argv)
    except BaseException as exc:
        exception_summary = {'type': type(exc).__name__,
                             'reason_code': getattr(exc, 'reason_code', None),
                             'budget_code': getattr(exc, 'code', None)}
        # Retain native diagnostic evidence locally without printing credentials.
        new('private/RUNTIME_EXCEPTION.json', {**exception_summary,
            'message': str(exc), 'record': getattr(exc, 'record', {})})
    finally:
        snapshot = guard.as_dict()['task_budget']
        with sqlite3.connect(ledger.as_uri() + '?mode=ro', uri=True) as db:
            rows = db.execute('SELECT status,amount_cny,actual_cny FROM reservations').fetchall()
        held = sum((Decimal(str(r[1] or 0)) for r in rows if r[0] != 'settled'), Decimal(0))
        settled = sum((Decimal(str(r[2] or 0)) for r in rows if r[0] == 'settled'), Decimal(0))
        result = {'task_id': config['task']['task_id'], 'source_sha': SOURCE_SHA,
            'finished_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'entry_exit_code': exit_code, 'exception': exception_summary,
            'budget_mode': 'dedicated', 'budget_limit_cny': 20,
            'task_wide_attempt_reservations': len(rows),
            'settled_usage_cost_cny': float(settled), 'pending_held_uncertain_cny': float(held),
            'unresolved_count': sum(r[0] != 'settled' for r in rows),
            'remaining_conservative_cny': float(Decimal(20) - settled - held),
            'guard_after': snapshot,
            'cost_basis': 'Provider usage counters priced by unchanged original native runtime; not provider invoice reconciliation',
            'original_ledger_bytes_unchanged': sha(ORIGINAL) == authorization['original_accounting_summary']['sha256'],
            'raw_local_audit_root': config['paths']['audit_root'],
            'output': str(output), 'real_retrieval_calls': 0,
            'physical_computations': 0, 'pdf_runs': 0}
        new('REAL_RUN_RESULT.json', result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
sys.exit(exit_code)
