"""Bounded T026 CLI launcher; never retries an already-started physical stage."""
from __future__ import annotations
import datetime, hashlib, json, os, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = Path(r'D:\OptoMind-Article-3\r3t026a')
SOURCE_SHA = '5786e08575b19020a6aece338436406884d3542d'

def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def save(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')

def reservations():
    rows = []
    for path in sorted((ROOT / 'cache/evaluations').glob('*.reservation.json')):
        data = load(path)
        keys = data['reserved_simulation_keys']
        rows.append({'path': str(path), 'count': len(keys),
                     'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    return {'reservations': rows, 'started_or_reserved_new_evaluations': sum(r['count'] for r in rows),
            'total_cap': 64, 'counting_rule': 'Count every durable reservation, including failed attempts; cache hits reserve zero.'}

def main():
    stage = sys.argv[1]
    assert stage in ('round1', 'cacheprobe', 'round2')
    assert subprocess.check_output(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], text=True).strip() == SOURCE_SHA
    assert not subprocess.check_output(['git', '-C', str(SOURCE), 'status', '--porcelain=v1'], text=True).strip()
    paths = load(ROOT / 'INPUT_PATHS.json')
    for filename, expected in paths['sha256'].items():
        assert hashlib.sha256(Path(filename).read_bytes()).hexdigest() == expected
    meter = reservations()
    used = meter['started_or_reserved_new_evaluations']
    assert used <= 64
    if stage == 'round1':
        assert used == 0
        limit = 14
        routes = paths['routes']
    elif stage == 'cacheprobe':
        assert (ROOT / 'round1/summary.json').is_file()
        limit = 0
        routes = paths['routes']
    else:
        assert (ROOT / 'revision/research_routes.json').is_file()
        assert not load(ROOT / 'revision/revision_parent.json')['action_record_errors']
        limit = 64 - used
        assert limit > 0
        routes = str(ROOT / 'revision/research_routes.json')
    assert not (ROOT / stage).exists()
    command = [sys.executable, '-B', '-X', 'utf8', str(SOURCE / 'focused_research/scientific_stage.py'), 'run',
               '--task-sheet', paths['task_sheet'], '--evidence-file', paths['evidence'],
               '--constraints-file', paths['constraints'], '--routes-file', routes,
               '--optical-media', str(ROOT / 'inputs/optical_media.json'),
               '--wavelengths', str(ROOT / 'inputs/wavelengths_nm.json'),
               '--cache-dir', str(ROOT / 'cache'), '--max-new-evals', str(limit),
               '--max-workers', '2', '--execute', '--output', str(ROOT / stage)]
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    save(ROOT / f'{stage.upper()}_STARTED.json', {'started_at': stamp, 'source_sha': SOURCE_SHA,
         'command': command, 'new_evaluation_limit_this_invocation': limit,
         'numerical_budget_before': meter, 'model_calls': 0})
    state_path = ROOT.parent.parent / 'state.json'
    state = load(state_path)
    assert state['active_task']['task_id'] == 'R3-T026'
    state['active_task']['phase'] = stage + '_running'
    state['active_task']['paid_execution_allowed'] = False
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
    started = time.monotonic()
    with (ROOT / f'{stage.upper()}.stdout.log').open('x', encoding='utf-8') as out, (ROOT / f'{stage.upper()}.stderr.log').open('x', encoding='utf-8') as err:
        completed = subprocess.run(command, cwd=SOURCE, env=env, stdout=out, stderr=err)
    after = reservations()
    assert after['started_or_reserved_new_evaluations'] <= 64
    save(ROOT / f'{stage.upper()}_EXIT.json', {'exit_code': completed.returncode,
         'finished_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
         'elapsed_seconds': time.monotonic() - started, 'numerical_budget_after': after})
    print(json.dumps({'stage': stage, 'exit_code': completed.returncode, 'numerical_budget_after': after}))
    return completed.returncode

if __name__ == '__main__':
    raise SystemExit(main())
