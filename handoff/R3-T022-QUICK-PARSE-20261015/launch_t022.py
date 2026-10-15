"""T022 operator launcher; reuse native D20 accounting with one new bounded task identity."""
from __future__ import annotations
import argparse
import datetime
from decimal import Decimal
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
SOURCE = Path(r'D:\OptoMind-Article-3\r3t022a')
SHA = '5ef850832c221e7f6444195ac8b948117ec1c8aa'
D20 = ROOT.parent / 'R3-T019-DEDICATED20'
OLD = Path(r'D:\OptoMind-Article-3\local-functional-trial\20261013\budget.sqlite')
LEDGER = D20 / 'private/budget.sqlite'
TOOLS = ROOT.parent / 'R3-T001-INTEGRATE-OFFLINE/release/local_tools'
TASK_ID = 'R3-T022'

def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')

def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result

def reconcile():
    result = []
    for path in (OLD, LEDGER):
        assert path.is_file()
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
            rows = db.execute('SELECT status,amount_cny,actual_cny FROM reservations').fetchall()
            cap = Decimal(dict(db.execute('SELECT key,value FROM budget_meta'))['limit_cny'])
        settled = sum((Decimal(str(r[2] or 0)) for r in rows if r[0] == 'settled'), Decimal(0))
        held = sum((Decimal(str(r[1] or 0)) for r in rows if r[0] != 'settled'), Decimal(0))
        result.append(dict(path=str(path), cap_cny=str(cap), settled_cny=str(settled),
                           held_cny=str(held), unresolved_count=sum(r[0] != 'settled' for r in rows)))
    assert all(r['unresolved_count'] == 0 for r in result), 'Uncertain spend blocks further calls'
    # The native independent cap is 20, not a reset. Even its complete remaining
    # capacity plus current original-ledger exposure must fit the global 140.
    assert Decimal(result[0]['settled_cny']) + Decimal(result[0]['held_cny']) + Decimal(result[1]['cap_cny']) <= 140
    assert Decimal(result[1]['cap_cny']) == 20
    return result

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage', choices=['preflight', 'plan', 'routes'])
    p.add_argument('--execute', action='store_true')
    p.add_argument('--evidence-file', type=Path)
    p.add_argument('--constraints-file', type=Path)
    a = p.parse_args()
    assert subprocess.check_output(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], text=True).strip() == SHA
    assert not subprocess.check_output(['git', '-C', str(SOURCE), 'status', '--porcelain=v1'], text=True).strip()
    assert hashlib.sha256((D20/'transport/dedicated_qwen_adapter.py').read_bytes()).hexdigest() == 'b2a52f685d6d0c85abd56a5cdada9fd7eb787c7d2e53f2383e9a86431aee811d'
    old_config = load(D20/'RUN_CONFIG.local-only.json')
    native_path = Path(old_config['paths']['review_runtime'])
    assert hashlib.sha256(native_path.read_bytes()).hexdigest() == '7fa510a1b9da12a4403868259bb2bd124e36b0d2b28718226af485d74c00c005'
    assert hashlib.sha256((TOOLS/'budgeted_entry.py').read_bytes()).hexdigest() == '1a6832ade3f4b05d24a31102d106894d97e8e1f4a7af24883349783c4f88d512'
    assert hashlib.sha256((TOOLS/'task_budget_guard.py').read_bytes()).hexdigest() == 'd09c24cb6895790dc1c53a6807e743b09a8737135d65ec081c9b7c206060c461'
    before = reconcile()
    runner = module(SOURCE/'focused_research/run.py', '_t022_focused_runner')
    planner_module = importlib.import_module('optomind_research.query_planner')
    adapter_module = module(D20/'transport/dedicated_qwen_adapter.py', '_t022_original_d20_adapter')
    native = adapter_module.load_native_runtime(native_path)
    factory, guard_module = adapter_module._load_budget_factory(TOOLS/'budgeted_entry.py', TOOLS/'task_budget_guard.py')
    ledger = native.GlobalBudgetLedger(limit_cny=20, path=LEDGER)
    guard = guard_module.TaskBudgetGuard(ledger, task_id=TASK_ID, lane='focused_chain',
        task_limit_cny=15, max_calls=4, checkpoint_cny=None, global_limit_cny=20)
    snapshot = guard.as_dict()['task_budget']
    assert snapshot['limit_cny'] == 15 and snapshot['max_calls'] == 4
    assert snapshot['task_id'] == TASK_ID and snapshot['unresolved_count'] == 0
    config = adapter_module.Dedicated20Config(ledger_path=LEDGER,
        audit_root=ROOT/'private/audit', key_file=Path(old_config['paths']['key_file']),
        task_id=TASK_ID, model='qwen3.5-plus', repair_model='qwen3.7-flash',
        task_limit_cny=15, global_limit_cny=20, max_calls=4,
        max_retries=0, max_keys=1, max_tokens=7000)
    # The old installer is intentionally T019/20/3-specific. Reuse its original
    # callable/factory under the explicit T022/15/4 contract enforced above by
    # the unchanged native TaskBudgetGuard, rather than changing the old task.
    resolver, _ = adapter_module._load_vendored_model_resolver(Path(planner_module.__file__))
    audited = factory.make_audited_client(native, guard, config.factory_config())
    adapter = adapter_module.FocusedQwenCallAdapter(audited_client_factory=audited,
        runtime=native, config=config, guard=guard, model_resolver=resolver)
    origins = {n: str(Path(importlib.import_module(n).__file__).resolve()) for n in
               ('config.qwen_config', 'llm.qwen_chat_client', 'optomind_research.query_planner')}
    assert all(Path(v).is_relative_to(SOURCE/'focused_research/vendor/review') for v in origins.values())
    if a.stage == 'preflight':
        assert not a.execute
        write(ROOT/'LAUNCHER_PREFLIGHT.json', dict(source_sha=SHA, python=sys.executable,
            origins=origins, budget_before=before, shared_guard=snapshot,
            transport_retries=0, key_slots=1, model='qwen3.5-plus',
            task_cap_cny=15, chain_http_attempt_cap=4, credentials_loaded=False,
            native_factory_reused=True, production_source_unchanged=True))
        print('T022 preflight passed; no HTTP calls')
        return 0
    assert a.execute, 'Explicit execute flag required'
    output = ROOT/'runs'/a.stage
    assert not output.exists(), 'Preserve started/completed outputs; do not repeat'
    marker = ROOT/(a.stage.upper()+'_STARTED.json')
    write(marker, dict(started_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                       source_sha=SHA, budget_before=before, shared_guard=snapshot))
    stage_calls = 0
    def protected_call(*args, **kwargs):
        nonlocal stage_calls
        reconcile()
        assert stage_calls < 2, 'At most one generation and one format repair per stage'
        stage_calls += 1
        result = adapter(*args, **kwargs)
        # Labels only: accounting/request identities remain the native T022 ones.
        if isinstance(result.get('_llm_usage'), dict):
            result['_llm_usage']['module'] = TASK_ID
        return result
    planner_module.call_qwen_chat = protected_call
    code = 2
    try:
        if a.stage == 'plan':
            question = load(SOURCE/'focused_research/examples/rgb_nir_question.v2.json')['original_question']
            code = runner.main(['plan', '--question', question, '--output', str(output)])
        else:
            assert a.evidence_file and a.constraints_file
            route_stage = importlib.import_module('route_stage')
            planner = planner_module.QueryPlannerAgent(max_tokens=7000)
            code = route_stage.main(['generate', '--task-sheet', str(ROOT/'runs/plan/task_sheet.json'),
                '--evidence-file', str(a.evidence_file), '--constraints-file', str(a.constraints_file),
                '--output', str(output)], planner=planner)
        return code
    finally:
        write(ROOT/(a.stage.upper()+'_ACCOUNTING.json'), dict(exit_code=code,
            stage_model_invocations=stage_calls, guard_after=guard.as_dict()['task_budget'],
            original_ledger_sha256=hashlib.sha256(OLD.read_bytes()).hexdigest()))

if __name__ == '__main__':
    raise SystemExit(main())
