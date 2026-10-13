"""Execute the unchanged formal Review CLI argv using the shared audit transport."""
from pathlib import Path
import json
import subprocess
import sys

root = Path(__file__).resolve().parent
args = json.loads((root / 'review/RUN_ARGV.json').read_text(encoding='utf-8'))
if sys.argv[1:] == ['preflight']:
    args[args.index('--run')] = '--preflight'
raise SystemExit(subprocess.call([sys.executable, '-B', '-X', 'utf8', '-P', str(root / 'budgeted_entry.py'), 'review', *args]))
