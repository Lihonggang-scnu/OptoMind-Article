"""Start an isolated process; default prepares only. --execute is opt-in."""
import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--execute', action='store_true')
args = parser.parse_args()
python = r'D:\OptoMind-Article-3\local-integration\20261013\envs\tensor\Scripts\python.exe'
home = ROOT / 'isolated-home'
home.mkdir(exist_ok=True)
env = {k: v for k, v in os.environ.items() if k.upper() in {
    'SYSTEMROOT', 'WINDIR', 'PATH', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP',
    'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE'}}
env.update(HOME=str(home), USERPROFILE=str(home),
           APPDATA=str(home / 'AppData/Roaming'), LOCALAPPDATA=str(home / 'AppData/Local'),
           PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
argv = [python, '-X', 'utf8', str(ROOT / 'launch_dedicated_plan.py')]
if args.execute:
    argv.append('--execute')
name = 'live-entry' if args.execute else 'prepare-entry'
log = ROOT / 'logs'
log.mkdir(exist_ok=True)
assert not (log / (name + '.command.json')).exists(), 'Preserve previous command and never blindly repeat'
with (log / (name + '.command.json')).open('x', encoding='utf-8', newline='\n') as handle:
    json.dump({'argv': argv, 'cwd': r'D:\OptoMind-Article-3\r3t019a',
               'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
               'credential_environment_inherited': False}, handle, ensure_ascii=False, indent=2)
    handle.write('\n')
with (log / (name + '.stdout.txt')).open('xb') as out, (log / (name + '.stderr.txt')).open('xb') as err:
    process = subprocess.run(argv, cwd=r'D:\OptoMind-Article-3\r3t019a', env=env,
                             stdout=out, stderr=err, timeout=660 if args.execute else 45)
with (log / (name + '.exit.json')).open('x', encoding='utf-8', newline='\n') as handle:
    json.dump({'exit_code': process.returncode,
               'finished_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}, handle)
    handle.write('\n')
print(name + ': exit=' + str(process.returncode))
print((log / (name + '.stdout.txt')).read_text(encoding='utf-8', errors='replace')[-5000:])
if process.returncode:
    print((log / (name + '.stderr.txt')).read_text(encoding='utf-8', errors='replace')[-4000:])
sys.exit(process.returncode)
