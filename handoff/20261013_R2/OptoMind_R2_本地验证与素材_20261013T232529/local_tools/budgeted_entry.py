"""Invoke unchanged formal CLIs with only a shared, audited API transport.

No prompt, solver configuration, compiled task or result is rewritten here.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parent
REPOS = Path('D:/OptoMind-Article-3/repos')
LEDGER_PATH = Path('D:/OptoMind-Article-3/local-functional-trial/20261013/budget.sqlite')
KEY_FILE = Path('F:/OptoMind-Review-2/api_keys/qwen-api-key.txt')
MODELS = {'qwen3.5-plus', 'qwen3.7-flash'}
LOCK = threading.RLock()


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')


def load_runtime(lane):
    if lane == 'review':
        from optomind_research.runtime.upgrade3.module4 import runtime
        return runtime
    source = REPOS / 'OptoMInd-Review/optomind_research/runtime/upgrade3/module4/runtime.py'
    spec = importlib.util.spec_from_file_location('local_shared_budget_runtime', source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main():
    lane, *args = sys.argv[1:]
    os.environ['OPTOMIND_ECONOMY_TEXT_CEILING'] = '0'
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    runtime = load_runtime(lane)
    if lane == 'authorize':
        with sqlite3.connect(LEDGER_PATH, timeout=30) as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute("SELECT value FROM budget_meta WHERE key='limit_cny'").fetchone()[0]
            pending = db.execute("SELECT COUNT(*) FROM reservations WHERE status IN ('reserved','uncertain')").fetchone()[0]
            if pending:
                raise RuntimeError('Unresolved prior spend: no authorization migration or new calls')
            if float(previous) not in (15.0, 80.0):
                raise RuntimeError('Unexpected shared budget ceiling')
            db.execute("UPDATE budget_meta SET value='80.0' WHERE key='limit_cny'")
            db.commit()
        ledger = runtime.GlobalBudgetLedger(limit_cny=80.0, path=LEDGER_PATH)
        snapshot = ledger.as_dict()
        dump(ROOT / 'BUDGET_AUTHORIZATION.json', {
            'authority': 'User request 2026-10-13: aggregate ceiling increased to 80 CNY; review at 60; no upload of credentials or ledger databases',
            'previous_limit_cny': float(previous), 'budget': snapshot,
            'stage_estimates_cny': {'local_checks': 0, 'article_loop': 12, 'review_loop': 18,
                                    'targeted_repairs_and_retries': 20, 'small_comparison': 10,
                                    'protected_remaining_at_60': 20},
            'pricing_source': 'https://help.aliyun.com/zh/model-studio/model-pricing',
            'pricing_policy': 'existing China tier rates verified 2026-10-13; byte upper bound reservation; unknown spend blocks new calls',
        })
        dump(ROOT / 'BUDGET_STATUS.json', snapshot)
        print(json.dumps({'limit_cny': 80, 'actual_cny': snapshot['actual_cny'], 'held_cny': snapshot['reserved_cny']}))
        return 0
    if lane not in ('article', 'review'):
        raise ValueError('Expected authorize, article or review')
    ledger = runtime.GlobalBudgetLedger(limit_cny=80, path=LEDGER_PATH)
    native = runtime.QwenDirectClient

    class AuditedClient(native):
        def __init__(self, **config):
            if config.get('model', 'qwen3.7-flash') not in MODELS:
                raise ValueError('Model outside configured allowlist')
            config.update(key_file=KEY_FILE, key_index=1, max_keys=1, max_retries=0,
                          budget_ledger=ledger)
            # Review supplies its verified local Qwen tokenizer and margin.
            # A byte count remains the conservative fallback, but cannot
            # replace a valid token counter for a large Chinese payload:
            # bytes are not model-context tokens.
            config.setdefault('prompt_token_counter', lambda body, messages: len(body) + 4096)
            super().__init__(**config)

        def __call__(self, messages, **kwargs):
            if kwargs.get('force_mock'):
                raise ValueError('Mock calls forbidden in real trial')
            with LOCK:
                status = ledger.as_dict()
                if status['reserved_cny']:
                    raise RuntimeError('Unresolved spend blocks new calls')
                if status['actual_cny'] >= 60 and not (ROOT / 'BUDGET_60_REVIEW_APPROVED.json').exists():
                    raise RuntimeError('60 CNY checkpoint reached; root must review remaining scope')
                call_id = lane + '-' + uuid.uuid4().hex[:16]
                folder = ROOT / 'calls' / call_id
                dump(folder / 'REQUEST.json', {'messages': messages, 'model': self.model,
                    'max_output_tokens': self.max_output_tokens, 'thinking': self.thinking,
                    'thinking_budget': self.thinking_budget, 'call_parameters': kwargs,
                    'transport_retries': 0, 'key_slots': 1, 'production_call_id': kwargs.get('call_id')})
                self.raw_response_dir = folder / 'raw'
                try:
                    result = super().__call__(messages, **{**kwargs, 'call_id': call_id})
                    dump(folder / 'RESPONSE.json', result)
                    return result
                except Exception as exc:
                    dump(folder / 'ERROR.json', {'type': type(exc).__name__,
                        'code': getattr(exc, 'code', None), 'record': getattr(exc, 'record', {})})
                    raise
                finally:
                    dump(ROOT / 'BUDGET_STATUS.json', ledger.as_dict())

    if lane == 'article':
        from optomind_optics.harness.problem_analyzer import ArticlePlusQwenClient
        from optomind_optics.harness.qwen_policy import QwenFlashOnlyClient

        def initialize(client, *, role='plus'):
            client.role = role

        def invoke(client, messages, *, max_tokens=4000, force_mock=None):
            if force_mock:
                raise ValueError('Mock calls forbidden')
            result = AuditedClient(model=client.model_name, thinking=False, thinking_budget=0,
                max_output_tokens=max_tokens, timeout_seconds=300, json_mode=True)(messages)
            usage = dict(result.get('usage') or {})
            return {**result, '_llm_usage': {**usage, 'model_name': client.model_name, 'mock_llm': False,
                'cost_cny': runtime.estimated_cost_cny(usage, model=client.model_name)}}

        ArticlePlusQwenClient.__init__ = initialize
        ArticlePlusQwenClient.call = invoke
        QwenFlashOnlyClient.call = invoke
        from scripts.run_tmm_research_harness import main as entry
    else:
        runtime.QwenDirectClient = AuditedClient
        from scripts.upgrade3.progressive_review_plan import main as entry

    started = time.time()
    receipt = ROOT / 'entries' / (lane + '-' + uuid.uuid4().hex[:12] + '.json')
    record = {'lane': lane, 'argv': args, 'python': sys.executable, 'started_at': started,
        'transport_only': True, 'scientific_prompts_or_tasks_rewritten': False,
        'source_heads': {repo: subprocess.check_output(['git', '-C', str(REPOS / repo), 'rev-parse', 'HEAD'], text=True).strip()
                         for repo in ('OptoMind-Article', 'OptoMInd-Review', 'VeriTMM')}}
    dump(receipt, record)
    try:
        code = entry(args)
        record['exit_code'] = code
        return code
    except Exception as exc:
        record.update(exit_code=1, error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        record.update(finished_at=time.time(), budget=ledger.as_dict())
        dump(receipt, record)


if __name__ == '__main__':
    raise SystemExit(main())
