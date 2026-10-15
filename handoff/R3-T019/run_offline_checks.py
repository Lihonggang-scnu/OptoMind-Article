"""Task-owned offline verification. Does not dispatch models or edit source."""
from __future__ import annotations

import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
SOURCE = Path(r'D:\OptoMind-Article-3\r3t019a')
PYTHON = Path(r'D:\OptoMind-Article-3\local-integration\20261013\envs\tensor\Scripts\python.exe')
EVIDENCE = ROOT / 'offline'
EVIDENCE.mkdir(exist_ok=True)
assert not (EVIDENCE / 'RESULTS.json').exists(), 'Preserve previous offline evidence'
HOME = ROOT / 'offline-home'
HOME.mkdir(exist_ok=True)
BOOT = ROOT / 'offline-bootstrap'
BOOT.mkdir(exist_ok=True)

# No credentials, project API environment, or inherited Python source path.
env = {k: v for k, v in os.environ.items()
       if k.upper() in {'SYSTEMROOT', 'WINDIR', 'PATH', 'COMSPEC', 'PATHEXT',
                        'TEMP', 'TMP', 'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS'}}
env.update(HOME=str(HOME), USERPROFILE=str(HOME),
           APPDATA=str(HOME / 'AppData' / 'Roaming'),
           LOCALAPPDATA=str(HOME / 'AppData' / 'Local'),
           PYTHONPATH=str(BOOT), PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1',
           HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
           PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
(BOOT / 'sitecustomize.py').write_text('''import socket
def _offline_denied(*args, **kwargs):
    raise RuntimeError("T019 offline process: networking forbidden")
socket.socket.connect = _offline_denied
socket.socket.connect_ex = _offline_denied
socket.create_connection = _offline_denied
socket.getaddrinfo = _offline_denied
''', encoding='utf-8', newline='\n')
# Explicitly load pytest-socket in both this process and the test's subprocess.
env['PYTEST_PLUGINS'] = 'pytest_socket'

probe = r'''
import importlib, importlib.metadata, importlib.util, json, pathlib, sys
source = pathlib.Path.cwd().resolve()
focus = source / 'focused_research'
spec = importlib.util.spec_from_file_location('focused_runner', focus / 'run.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
modules = {}
for name in ('task_sheet', 'config', 'llm.qwen_chat_client', 'optomind_research.query_planner'):
    module = importlib.import_module(name)
    actual = pathlib.Path(module.__file__).resolve()
    allowed = focus if name == 'task_sheet' else focus / 'vendor' / 'review'
    assert actual.is_relative_to(allowed), (name, str(actual), str(allowed))
    modules[name] = str(actual)
assert 'optomind_optics' not in sys.modules
assert 'tmm_engine' not in sys.modules
print(json.dumps({'python': sys.executable, 'version': sys.version,
                 'modules': modules,
                 'dependencies': {n: importlib.metadata.version(n)
                    for n in ('ftfy', 'PyYAML', 'pytest', 'pytest-socket', 'numpy')},
                 'no_physics_modules_imported': True}, indent=2))
'''
commands = [
    ('module-origins', [str(PYTHON), '-X', 'utf8', '-c', probe]),
    ('focused-tests', [str(PYTHON), '-X', 'utf8', '-m', 'pytest', '-q',
         '--disable-socket', 'focused_research/tests',
         '--basetemp', str(EVIDENCE / 'pytest-temp'),
         '-o', 'cache_dir=' + str(EVIDENCE / 'pytest-cache')]),
    ('cli-help', [str(PYTHON), '-X', 'utf8', 'focused_research/run.py', '--help']),
]
results = []
for name, argv in commands:
    before = datetime.datetime.now(datetime.timezone.utc).isoformat()
    process = subprocess.run(argv, cwd=SOURCE, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             timeout=120)
    out = EVIDENCE / (name + '.stdout.txt')
    err = EVIDENCE / (name + '.stderr.txt')
    out.write_bytes(process.stdout)
    err.write_bytes(process.stderr)
    row = dict(name=name, argv=argv, cwd=str(SOURCE), started_at=before,
               finished_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
               exit_code=process.returncode, stdout_file=out.name, stderr_file=err.name,
               stdout_sha256=hashlib.sha256(process.stdout).hexdigest(),
               stderr_sha256=hashlib.sha256(process.stderr).hexdigest())
    results.append(row)
    print(name + ': exit=' + str(process.returncode), flush=True)
    print(process.stdout.decode('utf-8', errors='replace')[-2000:], flush=True)
    if process.returncode:
        print(process.stderr.decode('utf-8', errors='replace')[-3000:], flush=True)

result = {'task_id': 'R3-T019-FOCUSED-TASK-SHEET', 'message_id': 'C2L-R3-0032',
          'source_commit': '713799fb55899b78427aed56cb643bc72526c19d',
          'commands': results, 'all_passed': all(r['exit_code'] == 0 for r in results),
          'live_model_calls': 0, 'real_retrieval_calls': 0,
          'physical_computations': 0, 'pdf_executions': 0,
          'credentials_inherited': False, 'dependencies_installed': False,
          'nested_consumer_fixture': 'four scalar-reduction checks using synthetic arrays; no forward solver'}
(EVIDENCE / 'RESULTS.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n',
                                      encoding='utf-8', newline='\n')
with (ROOT / 'WORK_LOG.jsonl').open('a', encoding='utf-8', newline='\n') as handle:
    handle.write(json.dumps({'at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            'event': 'offline_verification',
                            'all_passed': result['all_passed'], 'paid_calls': 0}) + '\n')
sys.exit(0 if result['all_passed'] else 1)
