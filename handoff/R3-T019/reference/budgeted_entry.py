"""Same-ledger task-capped transport for the unchanged Article/Review CLIs."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

from task_budget_guard import TaskBudgetGuard

SCHEMA = 'optomind-r3-budget-entry.v1'
MODELS = {'qwen3.5-plus', 'qwen3.7-flash'}
LOCK = threading.RLock()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f'{name}: finite positive number required')
    return float(value)


def load_config(path):
    path = Path(path).resolve()
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    if config.get('schema_version') != SCHEMA:
        raise ValueError('Unsupported configuration schema')
    paths = config['paths']
    for key in ('ledger', 'article_repo', 'review_repo', 'veritmm_repo', 'audit_root'):
        if not isinstance(paths.get(key), str) or not paths[key].strip():
            raise ValueError(f'Missing paths.{key}')
        paths[key] = str(Path(paths[key]).resolve())
    if not Path(paths['ledger']).is_file():
        raise FileNotFoundError('Existing unique ledger required; creation/copy/reset is forbidden')
    budget = config['budget']
    if positive(budget['aggregate_limit_cny'], 'aggregate_limit_cny') != 140.0:
        raise ValueError('This delivery is bounded by the authorized aggregate 140 CNY ceiling')
    positive(budget['expected_previous_limit_cny'], 'expected_previous_limit_cny')
    if not str(budget.get('authorization_reference') or '').strip():
        raise ValueError('Explicit aggregate authorization reference required')
    checkpoint = positive(budget['checkpoint_cny'], 'checkpoint_cny')
    if checkpoint != 60.0:
        raise ValueError('Existing 60 CNY checkpoint must be retained by this delivery')
    task = config['task']
    if not isinstance(task.get('task_id'), str) or not task['task_id'].strip() or len(task['task_id']) > 120:
        raise ValueError('Stable nonempty task_id required')
    if task.get('lane') not in ('article', 'review'):
        raise ValueError('Task lane must be article or review')
    if positive(task['limit_cny'], 'task.limit_cny') > budget['aggregate_limit_cny']:
        raise ValueError('Task limit exceeds aggregate authorization')
    if isinstance(task['maximum_real_calls'], bool) or not isinstance(task['maximum_real_calls'], int) or task['maximum_real_calls'] < 1:
        raise ValueError('Positive integer maximum_real_calls required')
    if type(config.get('execution_enabled')) is not bool:
        raise ValueError('execution_enabled must be an explicit boolean')
    if not str(task.get('authorization_reference') or '').strip():
        raise ValueError('Task authorization reference required, even for preparation')
    config['_configuration_path'] = str(path)
    config['_configuration_sha256'] = digest(path)
    return config


def _ledger_snapshot_in_connection(db, path):
    limit = db.execute("SELECT value FROM budget_meta WHERE key='limit_cny'").fetchone()
    if not limit:
        raise ValueError('Original ledger has no explicit budget limit')
    rows = db.execute('SELECT status,amount_cny,actual_cny FROM reservations').fetchall()
    return {
        'ledger_path': str(path), 'limit_cny': float(limit[0]),
        'actual_cny': sum(float(row[2] or 0) for row in rows),
        'pending_count': sum(row[0] != 'settled' for row in rows),
        'held_cny': sum(float(row[1]) for row in rows if row[0] != 'settled'),
        'reservation_count': len(rows),
    }


def ledger_snapshot(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError('An existing ledger is required')
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        db.execute('BEGIN')
        return _ledger_snapshot_in_connection(db, path)


def authorize(config):
    budget = config['budget']
    path = Path(config['paths']['ledger'])
    with sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=30) as db:
        db.execute('BEGIN IMMEDIATE')
        before = _ledger_snapshot_in_connection(db, path)
        previous = float(db.execute("SELECT value FROM budget_meta WHERE key='limit_cny'").fetchone()[0])
        pending = db.execute("SELECT COUNT(*) FROM reservations WHERE status != 'settled' OR status IS NULL").fetchone()[0]
        if pending:
            raise RuntimeError('Unresolved or unknown reservation status blocks authorization change')
        if previous not in (float(budget['expected_previous_limit_cny']), float(budget['aggregate_limit_cny'])):
            raise RuntimeError('Existing ledger ceiling does not match the reviewed expected previous ceiling')
        db.execute('CREATE TABLE IF NOT EXISTS r3_budget_authorizations (authorization_sha256 TEXT PRIMARY KEY, previous_limit_cny REAL NOT NULL, limit_cny REAL NOT NULL, audit_json TEXT NOT NULL, created_at REAL NOT NULL)')
        audit = {'ledger_path': str(path), 'previous_limit_cny': previous,
                 'limit_cny': float(budget['aggregate_limit_cny']), 'checkpoint_cny': budget['checkpoint_cny'],
                 'authorization_reference': budget['authorization_reference'],
                 'configuration_sha256': config['_configuration_sha256']}
        key = hashlib.sha256(canonical(audit).encode()).hexdigest()
        db.execute('INSERT OR IGNORE INTO r3_budget_authorizations VALUES (?,?,?,?,?)',
                   (key, previous, audit['limit_cny'], canonical(audit), time.time()))
        db.execute("UPDATE budget_meta SET value=? WHERE key='limit_cny'", (str(audit['limit_cny']),))
        after = _ledger_snapshot_in_connection(db, path)
        if any(before[k] != after[k] for k in ('actual_cny', 'reservation_count', 'pending_count')):
            raise RuntimeError('Unexpected historical ledger mutation during authorization')
        db.commit()
    return {'authorization_sha256': key, 'before': before, 'after': after,
            'snapshot_scope': 'both snapshots inside the same BEGIN IMMEDIATE authorization transaction',
            'authorization_committed': True}


def checkpoint_reference(config):
    settings = config['budget'].get('checkpoint_approval')
    if settings is None:
        return None
    path = Path(settings['path']).resolve()
    expected = str(settings['sha256']).lower()
    if len(expected) != 64 or digest(path) != expected:
        raise ValueError('Checkpoint approval receipt SHA256 mismatch')
    receipt = json.loads(path.read_text(encoding='utf-8-sig'))
    if (receipt.get('schema_version') != 'optomind-r3-checkpoint-approval.v1'
            or receipt.get('task_id') != config['task']['task_id']
            or Path(receipt.get('ledger_path', '')).resolve() != Path(config['paths']['ledger'])
            or receipt.get('checkpoint_cny') != config['budget']['checkpoint_cny']
            or receipt.get('authorized_continue_to_cny') != config['budget']['aggregate_limit_cny']
            or not str(receipt.get('authorization_reference') or '').strip()):
        raise ValueError('Checkpoint approval receipt identity/scope mismatch')
    return f"{receipt['authorization_reference']} sha256:{expected}"


def load_runtime(config):
    paths = config['paths']
    source = Path(paths['review_repo']) / 'optomind_research/runtime/upgrade3/module4/runtime.py'
    if not source.is_file():
        raise FileNotFoundError(f'Required original Review runtime missing: {source}')
    roots = [paths['veritmm_repo'], str(Path(paths['article_repo']) / 'code'), paths['review_repo']]
    if config['task']['lane'] == 'review':
        roots = [paths['review_repo'], paths['veritmm_repo'], str(Path(paths['article_repo']) / 'code')]
    for root in reversed(roots):
        while root in sys.path:
            sys.path.remove(root)
        sys.path.insert(0, root)
    name = 'r3_original_review_runtime'
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def prepare_guard(config, runtime):
    snapshot = ledger_snapshot(config['paths']['ledger'])
    if snapshot['limit_cny'] != config['budget']['aggregate_limit_cny']:
        raise RuntimeError('Ledger ceiling has not been explicitly authorized/prepared; run authorize separately')
    if snapshot['pending_count']:
        raise RuntimeError('Unresolved original ledger reservation blocks preparation')
    native = runtime.GlobalBudgetLedger(limit_cny=config['budget']['aggregate_limit_cny'], path=Path(config['paths']['ledger']))
    return TaskBudgetGuard(native, task_id=config['task']['task_id'], lane=config['task']['lane'],
        task_limit_cny=config['task']['limit_cny'], max_calls=config['task']['maximum_real_calls'],
        checkpoint_cny=config['budget']['checkpoint_cny'], checkpoint_approval_reference=checkpoint_reference(config),
        global_limit_cny=config['budget']['aggregate_limit_cny'])


def make_audited_client(runtime, guard, config):
    native = runtime.QwenDirectClient
    task_id = config['task']['task_id']
    task_slug = hashlib.sha256(task_id.encode()).hexdigest()[:20]
    audit_root = Path(config['paths']['audit_root']) / task_slug

    class AuditedClient(native):
        def __init__(self, **settings):
            if settings.get('model', 'qwen3.7-flash') not in MODELS:
                raise ValueError('Model outside original allowlist')
            settings.update(key_file=config['paths'].get('key_file'), key_index=1, max_keys=1,
                max_retries=0, thinking=False, thinking_budget=0, budget_ledger=None,
                base_url='https://dashscope.aliyuncs.com/compatible-mode/v1')
            settings.update(prompt_token_counter=None, prompt_token_multiplier=1.0, prompt_token_framing_margin=0)
            super().__init__(**settings)

        def _prompt_token_estimate(self, request_bytes, messages):
            return int(runtime.estimate_prompt_tokens(
                request_bytes, messages, prompt_token_counter=None)['prompt_tokens'])

        def __call__(self, messages, **kwargs):
            if not config['execution_enabled']:
                raise RuntimeError('Task execution is disabled in the reviewed configuration')
            if kwargs.get('force_mock') or kwargs.get('thinking') or kwargs.get('thinking_budget', 0):
                raise ValueError('Mock/thinking calls forbidden in this bounded real task')
            if kwargs.get('model', self.model) != self.model:
                raise ValueError('Per-call model override rejected')
            output_tokens = max(64, int(kwargs.get('max_output_tokens') or self.max_output_tokens))
            body_bytes, body = runtime.build_qwen_wire_body(messages, model=self.model,
                max_output_tokens=output_tokens, thinking=False, thinking_budget=0,
                json_mode=self.json_mode, stream=bool(kwargs.get('stream', False)),
                temperature=float(kwargs.get('temperature', 0.1)))
            request_hash = hashlib.sha256(canonical({'endpoint': self.base_url, 'wire_body': body}).encode()).hexdigest()
            request_key = request_hash
            with LOCK:
                cached = guard.lookup_completed(request_key, request_hash)
                if cached:
                    result = json.loads(cached['response_bytes'].decode('utf-8'))
                    dump_new(audit_root / 'replays' / f'{uuid.uuid4().hex}.json', {
                        'task_id': task_id, 'request_key': request_key,
                        'response_sha256': cached['response_sha256'], 'replayed_at': time.time()})
                    original_usage = dict(result.get('usage') or {})
                    return {**result, 'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0},
                            '_r3_transport': {'replayed': True, 'incremental_cost_cny': 0,
                                             'original_usage': original_usage, 'request_key': request_key}}
                call_id = 'r3-' + uuid.uuid4().hex[:20]
                folder = audit_root / 'calls' / call_id
                dump_new(folder / 'REQUEST.json', {'task_id': task_id, 'request_key': request_key,
                    'request_sha256': request_hash, 'messages': messages, 'wire_body': body,
                    'model': self.model, 'transport_retries': 0, 'key_slots': 1,
                    'production_call_id': kwargs.get('call_id'), 'created_at': time.time()})
                self.raw_response_dir = folder / 'raw'
                self.budget_ledger = guard.for_request(request_key, request_hash)
                try:
                    result = super().__call__(messages, **{**kwargs, 'call_id': call_id,
                                                         'thinking': False, 'thinking_budget': 0})
                    response = folder / 'RESPONSE.json'
                    dump_new(response, result)
                    guard.record_response(request_key, response, digest(response))
                    return {**result, '_r3_transport': {'replayed': False, 'request_key': request_key}}
                except Exception as exc:
                    dump_new(folder / 'ERROR.json', {'type': type(exc).__name__,
                        'code': getattr(exc, 'code', None), 'record': getattr(exc, 'record', {})})
                    raise
                finally:
                    dump_new(folder / 'BUDGET_STATUS.json', guard.as_dict())
    return AuditedClient


def run(config, arguments, runtime, guard):
    if not config['execution_enabled']:
        raise RuntimeError('Task execution disabled; preparation does not authorize model dispatch')
    if not config['paths'].get('key_file'):
        raise ValueError('Existing local credential-file path must be configured for the reviewed task')
    lane = config['task']['lane']
    paths = config['paths']
    os.environ['OPTOMIND_ECONOMY_TEXT_CEILING'] = '0'
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    AuditedClient = make_audited_client(runtime, guard, config)
    if lane == 'article':
        from optomind_optics.harness.problem_analyzer import ArticlePlusQwenClient
        from optomind_optics.harness.qwen_policy import QwenFlashOnlyClient
        def initialize(client, *, role='plus'):
            client.role = role
        def invoke(client, messages, *, max_tokens=4000, force_mock=None):
            if force_mock:
                raise ValueError('Mock calls forbidden')
            result = AuditedClient(model=client.model_name, max_output_tokens=max_tokens,
                                   timeout_seconds=300, json_mode=True)(messages)
            usage = dict(result.get('usage') or {})
            return {**result, '_llm_usage': {**usage, 'model_name': client.model_name, 'mock_llm': False,
                'cost_cny': runtime.estimated_cost_cny(usage, model=client.model_name)}}
        ArticlePlusQwenClient.__init__ = initialize
        ArticlePlusQwenClient.call = invoke
        QwenFlashOnlyClient.call = invoke
        from scripts.run_tmm_research_harness import main as entry
    else:
        from optomind_research.runtime.upgrade3.module4 import runtime as production_runtime
        production_runtime.QwenDirectClient = AuditedClient
        for flag, expected in (('--budget-ledger', paths['ledger']), ('--budget-limit-cny', '140')):
            if arguments.count(flag) != 1:
                raise ValueError(f'Review requires exactly one explicit {flag}')
            value = arguments[arguments.index(flag) + 1]
            conflicts = Path(value).resolve() != Path(expected) if flag == '--budget-ledger' else float(value) != 140
            if conflicts:
                raise ValueError(f'Review {flag} conflicts with unique shared ledger configuration')
        from scripts.upgrade3.progressive_review_plan import main as entry
    started = time.time()
    receipt = Path(paths['audit_root']) / 'entries' / f'{lane}-{uuid.uuid4().hex}.json'
    record = {'task_id': config['task']['task_id'], 'lane': lane, 'argv': arguments,
        'python': sys.executable, 'started_at': started,
        'configuration_sha256': config['_configuration_sha256'], 'transport_only': True,
        'scientific_prompts_or_tasks_rewritten': False,
        'source_heads': {key: subprocess.check_output(['git', '-C', paths[key], 'rev-parse', 'HEAD'], text=True).strip()
                         for key in ('article_repo', 'review_repo', 'veritmm_repo')}}
    dump_new(receipt.with_suffix('.START.json'), record)
    try:
        code = entry(arguments)
        record['exit_code'] = code
        return code
    except Exception as exc:
        record.update(exit_code=1, error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        record.update(finished_at=time.time(), budget=guard.as_dict())
        dump_new(receipt, record)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('status', 'authorize', 'prepare', 'run'))
    parser.add_argument('--config', required=True)
    args, arguments = parser.parse_known_args(argv)
    if arguments and arguments[0] == '--':
        arguments = arguments[1:]
    if args.action != 'run' and arguments:
        parser.error('CLI forwarding is only supported for run')
    config = load_config(args.config)
    if args.action == 'status':
        result = ledger_snapshot(config['paths']['ledger'])
    elif args.action == 'authorize':
        result = authorize(config)
        dump_new(Path(config['paths']['audit_root']) / 'authorizations' / f'{uuid.uuid4().hex}.json', result)
    else:
        if args.action == 'run' and not config['execution_enabled']:
            raise RuntimeError('Task execution disabled; run is not a preparation shortcut')
        runtime = load_runtime(config)
        guard = prepare_guard(config, runtime)
        if args.action == 'run':
            return run(config, arguments, runtime, guard)
        result = {'configuration_sha256': config['_configuration_sha256'],
                  'execution_enabled': config['execution_enabled'], 'budget': guard.as_dict()}
        dump_new(Path(config['paths']['audit_root']) / 'preparations' / f'{uuid.uuid4().hex}.json', result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
