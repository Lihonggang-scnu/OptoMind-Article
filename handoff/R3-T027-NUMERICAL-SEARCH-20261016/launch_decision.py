"""T027 one parent/refinement decision through the unchanged native metered adapter."""
from __future__ import annotations
import argparse, datetime, hashlib, importlib, importlib.util, json, sqlite3, subprocess, sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = Path(r'D:\OptoMind-Article-3\r3t027a')
SHA = 'b5f8a1dc2cfe8a203b74a742963bef7f9ac9a57b'
D20 = ROOT.parent / 'R3-T019-DEDICATED20'
OLD = Path(r'D:\OptoMind-Article-3\local-functional-trial\20261013\budget.sqlite')
LEDGER = D20 / 'private/budget.sqlite'
TOOLS = ROOT.parent / 'R3-T001-INTEGRATE-OFFLINE/release/local_tools'
TASK = 'R3-T027'
MAX_TOKENS = 16384

def load(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def sha(path):
    with Path(path).open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()
def digest(value): return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
def write(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2); stream.write('\n')
def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec); sys.modules[name] = result; spec.loader.exec_module(result); return result
def git(*args): return subprocess.check_output(['git', '-C', str(SOURCE), *args], text=True).strip()

def reservation_hashes():
    with sqlite3.connect(LEDGER.as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        return {row['reservation_id']: digest(dict(row)) for row in db.execute('SELECT * FROM reservations')}

def reconcile(*, strict=True):
    ledgers = []
    for path, expected in ((OLD, 140), (LEDGER, 20)):
        assert path.is_file()
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
            rows = db.execute('SELECT status,amount_cny,actual_cny FROM reservations').fetchall()
            cap = Decimal(dict(db.execute('SELECT key,value FROM budget_meta'))['limit_cny'])
        assert cap == expected, 'Existing budget cap changed'
        settled = sum((Decimal(str(r[2] or 0)) for r in rows if r[0] == 'settled'), Decimal(0))
        held = sum((Decimal(str(r[1] or 0)) for r in rows if r[0] != 'settled'), Decimal(0))
        ledgers.append({'path': str(path), 'cap_cny': str(cap), 'settled_cny': str(settled),
                        'held_cny': str(held), 'unresolved_count': sum(r[0] != 'settled' for r in rows), 'sha256': sha(path)})
    if strict:
        assert all(x['unresolved_count'] == 0 for x in ledgers), 'Unknown/unsettled exposure; do not dispatch'
    with sqlite3.connect(LEDGER.as_uri() + '?mode=ro', uri=True) as db:
        rows = db.execute('SELECT r.status,r.amount_cny,r.actual_cny FROM reservations r JOIN r3_task_calls c USING(reservation_id) WHERE c.task_id IN (?,?,?,?)',
                          ('R3-T022', 'R3-T025', 'R3-T026', TASK)).fetchall()
    chain = sum((Decimal(str(r[2] if r[0] == 'settled' else r[1])) for r in rows), Decimal(0))
    total = sum((Decimal(x['settled_cny']) + Decimal(x['held_cny']) for x in ledgers), Decimal(0))
    assert chain <= 15 and total <= 140
    return {'ledgers': ledgers, 'aggregate_exposure_cny': str(total), 'chain_exposure_cny': str(chain),
            'chain_tasks': ['R3-T022', 'R3-T025', 'R3-T026', TASK], 'chain_cap_cny': 15, 'task_cap_cny': 3, 'subpool_cap_cny': 20, 'global_cap_cny': 140}

def observation_gate():
    assert load(ROOT / 'TORCH_GATE.json')['exit_code'] == 0
    summary = load(ROOT / 'search/summary.json')
    archive = load(ROOT / 'search/design_archive.json')
    searches = load(ROOT / 'search/numerical_search.json')
    diagnostics = load(ROOT / 'search/physics_diagnostics.json')
    assert archive and archive['designs'] and diagnostics, 'No certified observations; do not call model'
    assert searches and any(s['search']['backend'] == 'torch' and any(r['status'] == 'evaluated' and r['source'] != 'original_seed' for r in s['search']['evaluations']) for s in searches), 'All numerical searches failed; do not call model'
    assert all(s['search']['backend'] == 'torch' for s in searches), 'No silent backend replacement'
    assert summary['metering']['search_forward_design_rows'] + summary['metering']['search_failed_job_reserved_rows'] <= 4096
    assert summary['metering']['certification_new_evaluations'] <= 64
    return digest(load(ROOT / 'search/manifest.json'))

def main():
    parser = argparse.ArgumentParser(); parser.add_argument('stage', choices=['preflight', 'revise']); parser.add_argument('--execute', action='store_true'); args = parser.parse_args()
    assert git('rev-parse', 'HEAD') == SHA and not git('status', '--porcelain=v1')
    expected = {
        D20 / 'transport/dedicated_qwen_adapter.py': 'b2a52f685d6d0c85abd56a5cdada9fd7eb787c7d2e53f2383e9a86431aee811d',
        TOOLS / 'budgeted_entry.py': '1a6832ade3f4b05d24a31102d106894d97e8e1f4a7af24883349783c4f88d512',
        TOOLS / 'task_budget_guard.py': 'd09c24cb6895790dc1c53a6807e743b09a8737135d65ec081c9b7c206060c461'}
    for path, expected_hash in expected.items(): assert sha(path) == expected_hash
    paths = load(ROOT / 'INPUT_PATHS.json')
    for filename, expected_hash in paths['sha256'].items(): assert sha(filename) == expected_hash
    observation_sha = observation_gate()
    config_old = load(D20 / 'RUN_CONFIG.local-only.json'); native_path = Path(config_old['paths']['review_runtime'])
    assert sha(native_path) == '7fa510a1b9da12a4403868259bb2bd124e36b0d2b28718226af485d74c00c005'
    before = reconcile()
    protected_old_rows = reservation_hashes()
    module(SOURCE / 'focused_research/run.py', '_t027_runner_imports_only')
    planner_module = importlib.import_module('optomind_research.query_planner')
    adapter_module = module(D20 / 'transport/dedicated_qwen_adapter.py', '_t027_original_d20_adapter')
    native = adapter_module.load_native_runtime(native_path)
    pricing = native.model_pricing('qwen3.5-plus')
    assert pricing['max_output_tokens'] >= MAX_TOKENS and pricing['tiers'] == ((128000, .8, 4.8), (256000, 2., 12.), (1000000, 4., 24.))
    factory, guard_module = adapter_module._load_budget_factory(TOOLS / 'budgeted_entry.py', TOOLS / 'task_budget_guard.py')
    ledger = native.GlobalBudgetLedger(limit_cny=20, path=LEDGER)
    guard = guard_module.TaskBudgetGuard(ledger, task_id=TASK, lane='certified_parent_refinement_decision', task_limit_cny=3, max_calls=1, checkpoint_cny=None, global_limit_cny=20)
    snapshot = guard.as_dict()['task_budget']
    assert snapshot['limit_cny'] == 3 and snapshot['max_calls'] == 1 and snapshot['unresolved_count'] == 0
    config = adapter_module.Dedicated20Config(ledger_path=LEDGER, audit_root=ROOT / 'private/audit',
        key_file=Path(config_old['paths']['key_file']), task_id=TASK, model='qwen3.5-plus', repair_model='qwen3.7-flash',
        task_limit_cny=3, global_limit_cny=20, max_calls=1, max_retries=0, max_keys=1, max_tokens=MAX_TOKENS)
    resolver, _ = adapter_module._load_vendored_model_resolver(Path(planner_module.__file__))
    audited = factory.make_audited_client(native, guard, config.factory_config())
    adapter = adapter_module.FocusedQwenCallAdapter(audited_client_factory=audited, runtime=native, config=config, guard=guard, model_resolver=resolver)
    origins = {name: str(Path(importlib.import_module(name).__file__).resolve()) for name in
               ('config.qwen_config', 'llm.qwen_chat_client', 'optomind_research.query_planner', 'research_routes', 'search_stage')}
    assert all(Path(origins[n]).is_relative_to(SOURCE / 'focused_research/vendor/review') for n in ('config.qwen_config', 'llm.qwen_chat_client', 'optomind_research.query_planner'))
    assert all(Path(origins[n]).is_relative_to(SOURCE / 'focused_research') for n in ('research_routes', 'search_stage'))
    if args.stage == 'preflight':
        assert not args.execute and snapshot['attempted_calls'] == 0
        write(ROOT / 'REVISION_PREFLIGHT.json', {'source_sha': SHA, 'python': sys.executable, 'origins': origins,
            'budget_before': before, 'task_guard': snapshot, 'protected_old_reservations': protected_old_rows,
            'max_output_tokens': MAX_TOKENS, 'native_model_pricing': pricing,
            'official_pricing_url': 'https://help.aliyun.com/zh/model-studio/qwen3-5-plus',
            'official_pricing_checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'transport_retries': 0, 'key_slots': 1, 'credentials_loaded': False,
            'observation_sha256': observation_sha, 'new_budget_pool': False,
            'reservation_rule': 'Unchanged native serialized request, UTF8 conservative prompt bound and output bound; highest native tier.'})
        print('T027 native-wrapper preflight passed; no HTTP calls'); return 0
    assert args.execute
    assert not (ROOT / 'REVISION_STARTED.json').exists(), 'Already started; no repeated paid execution'
    assert not (ROOT / 'revision').exists(), 'Preserve outputs'
    prior = load(ROOT / 'REVISION_PREFLIGHT.json')
    assert prior['source_sha'] == SHA and prior['observation_sha256'] == observation_sha
    assert all(protected_old_rows.get(key) == value for key, value in prior['protected_old_reservations'].items())
    assert sha(OLD) == prior['budget_before']['ledgers'][0]['sha256']
    assert snapshot['attempted_calls'] == 0
    write(ROOT / 'REVISION_STARTED.json', {'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'budget_before': before, 'guard': snapshot})
    calls = 0
    def protected_call(agent_name, messages, **kwargs):
        nonlocal calls
        status = reconcile(); assert calls < 1, 'T027 permits one actual model HTTP and no format-repair HTTP'
        model = adapter_module._model_for_tier(kwargs.get('model_tier'), agent_name, config.model, config.repair_model, resolver)
        out = kwargs['max_tokens']; assert out == (1800 if model == config.repair_model else MAX_TOKENS)
        body_bytes, body = native.build_qwen_wire_body(messages, model=model, max_output_tokens=out, thinking=False, thinking_budget=0, json_mode=True, stream=False, temperature=kwargs['temperature'])
        assert model == config.model, 'No alternate repair-model HTTP is authorized'
        payload = json.loads(next(m['content'] for m in messages if m['role'] == 'user'))
        assert payload['certified_designs'] and payload['physics_diagnostics'] and payload['family_catalog']
        assert payload['objectives'] == load(ROOT / 'search/design_archive.json')['objectives']
        assert payload['task_sheet'] == load(paths['task_sheet'])
        assert payload['constraints'] == load(paths['constraints'])
        assert all(d.get('thicknesses_nm') and d.get('metrics') for d in payload['certified_designs'])
        assert payload['route_rationales'], 'Original route reasoning missing'
        estimate = native.estimate_prompt_tokens(body_bytes, messages, prompt_token_counter=None)
        prompt_upper = estimate['prompt_tokens']
        reservation = native.estimated_cost_cny({'prompt_tokens': prompt_upper, 'completion_tokens': out}, model=model, conservative=True)
        caps = native.model_pricing(model)
        assert prompt_upper <= caps['max_input_tokens'] and prompt_upper + out <= caps['context_window']
        current = guard.as_dict()['task_budget']
        assert Decimal(str(current['exposure_cny'])) + Decimal(str(reservation)) <= 3
        assert Decimal(status['chain_exposure_cny']) + Decimal(str(reservation)) <= 15
        assert Decimal(status['ledgers'][1]['settled_cny']) + Decimal(status['ledgers'][1]['held_cny']) + Decimal(str(reservation)) <= 20
        assert Decimal(status['aggregate_exposure_cny']) + Decimal(str(reservation)) <= 140
        calls += 1
        write(ROOT / f'CALL_{calls}_PREFLIGHT.json', {'model': model, 'max_output_tokens': out,
            'prompt_upper_bound': prompt_upper, 'wire_body_sha256': hashlib.sha256(body_bytes).hexdigest(), 'wire_body_bytes': len(body_bytes),
            'reservation_cny': reservation, 'native_estimator': estimate, 'budget': status, 'guard': current,
            'transport_retries': 0, 'key_slots': 1, 'observation_sha256': observation_sha,
            'native_completion_token_parameter': caps['completion_token_parameter']})
        result = adapter(agent_name, messages, **kwargs)
        if isinstance(result.get('_llm_usage'), dict): result['_llm_usage']['module'] = TASK
        return result
    planner_module.call_qwen_chat = protected_call
    code = 2
    try:
        planner = planner_module.QueryPlannerAgent(max_tokens=MAX_TOKENS)
        stage = importlib.import_module('search_stage')
        code = stage.main(['revise', '--previous-search', str(ROOT / 'search'),
            '--execute', '--output', str(ROOT / 'revision')], planner=planner)
        return code
    finally:
        after_rows = reservation_hashes()
        preserved = all(after_rows.get(key) == value for key, value in prior['protected_old_reservations'].items())
        write(ROOT / 'REVISION_ACCOUNTING.json', {'exit_code': code, 'stage_model_invocations': calls,
            'task_guard_after': guard.as_dict()['task_budget'], 'budget_after': reconcile(strict=False),
            'original_140_ledger_sha256': sha(OLD), 'prior_reservations_unchanged': preserved})
        assert preserved and sha(OLD) == prior['budget_before']['ledgers'][0]['sha256']

if __name__ == '__main__': raise SystemExit(main())
