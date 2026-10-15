"""Capture only the relevant transport checks in a clean offline process."""
import json
import os
from pathlib import Path
import subprocess
import sys
ROOT = Path(__file__).resolve().parent
out = ROOT / 'offline'
out.mkdir(exist_ok=True)
home = ROOT / 'isolated-home'
home.mkdir(exist_ok=True)
env = {k: v for k, v in os.environ.items() if k.upper() in {
    'SYSTEMROOT','WINDIR','PATH','COMSPEC','PATHEXT','TEMP','TMP',
    'NUMBER_OF_PROCESSORS','PROCESSOR_ARCHITECTURE'}}
env.update(HOME=str(home),USERPROFILE=str(home),PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',
    PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PYTEST_PLUGINS='pytest_socket')
argv = [sys.executable,'-X','utf8','-m','pytest','-q','--disable-socket',
    str(ROOT/'transport/test_dedicated_qwen_adapter.py'),
    '--basetemp',str(out/'parent-rerun1-temp'),'-o','cache_dir='+str(out/'pytest-cache')]
result = subprocess.run(argv,cwd=ROOT,env=env,capture_output=True,timeout=90)
for name,data in [('transport-tests.stdout.txt',result.stdout),('transport-tests.stderr.txt',result.stderr)]:
    with (out/name).open('xb') as handle:handle.write(data)
record = {'argv':argv,'exit_code':result.returncode,'network_disabled':True,
          'credentials_inherited':False,'original_ledger_accessed':False,
          'real_dedicated_ledger_accessed':False,
          'initial_setup_error': 'Prior command failed7 tmp_path setups because offline parent directory did not exist; no test body or HTTP ran. Parent directory created for this fresh rerun.',
          'parent_review_changes':['Removed unknown-cost guard exception suppression; preserve original request_not_settled rejection',
              'Removed unnecessary planner constructor monkeypatch from original-main regression',
              'Assert reservation exists before injected HTTP and add balance-exhaustion admission test']}
with (out/'VERIFICATION.json').open('x',encoding='utf-8',newline='\n') as handle:
    json.dump(record,handle,ensure_ascii=False,indent=2);handle.write('\n')
print(result.stdout.decode('utf-8',errors='replace'))
if result.returncode:print(result.stderr.decode('utf-8',errors='replace'))
sys.exit(result.returncode)
