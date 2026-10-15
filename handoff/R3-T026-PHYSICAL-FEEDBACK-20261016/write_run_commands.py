"""Write the actual bounded commands from persisted invocation records."""
import json, shlex
from pathlib import Path
ROOT = Path(__file__).resolve().parent
def load(p): return json.loads(p.read_text(encoding='utf-8-sig'))
py = r'D:\OptoMind-Article-3\local-integration\20261013\envs\tensor\Scripts\python.exe'
source = r'D:\OptoMind-Article-3\r3t026a'
inputs = load(ROOT / 'INPUT_PATHS.json')
def ps_command(parts): return '& ' + ' '.join("'" + str(s).replace("'", "''") + "'" for s in parts)
compile_command = [py, '-B', '-X', 'utf8', source + r'\focused_research\route_stage.py', 'validate',
    '--task-sheet', inputs['task_sheet'], '--evidence-file', inputs['evidence'],
    '--constraints-file', inputs['constraints'], '--routes-file', inputs['routes'], '--output', str(ROOT / 'compile')]
rows = [('A: compile original T025 draft (exit 0)', compile_command)]
rows += [('B: first real physical round (exit 0)', load(ROOT / 'ROUND1_STARTED.json')['command']),
         ('One zero-evaluation cache probe (exit 0)', load(ROOT / 'CACHEPROBE_STARTED.json')['command']),
         ('Native metering preflight; no HTTP (exit 0)', [py, '-B', '-X', 'utf8', str(ROOT / 'launch_revision.py'), 'preflight']),
         ('C: one observation-fed revision (exit 0; one actual HTTP)', [py, '-B', '-X', 'utf8', str(ROOT / 'launch_revision.py'), 'revise', '--execute']),
         ('D: second real physical round (exit 0)', load(ROOT / 'ROUND2_STARTED.json')['command'])]
text = '# T026 actual run commands\n\nSource: `5786e08575b19020a6aece338436406884d3542d`. Python 3.12 native Windows; no environment mixing. These are execution records, not permission to rerun or spend again. All output directories are immutable and must remain preserved.\n\n'
text += '```powershell\n$env:PYTHONDONTWRITEBYTECODE=\'1\'\n$env:PYTHONUTF8=\'1\'\n```\n\n'
for title, command in rows:
    text += '## ' + title + '\n\n```powershell\n' + ps_command(command) + '\n```\n\n'
text += 'The physical launcher `run_physical_stage.py` recorded each command, exit, timing and durable reservation count. Round1 reserved 12 new evaluations; cacheprobe reserved 0; round2 had cap 52 and reserved 5. Model revision used the unchanged existing native adapter/factory, one new T026 task scope on the existing ledger, no transport retry.\n'
with (ROOT / 'RUN_COMMANDS.md').open('x', encoding='utf-8', newline='\n') as stream: stream.write(text)
