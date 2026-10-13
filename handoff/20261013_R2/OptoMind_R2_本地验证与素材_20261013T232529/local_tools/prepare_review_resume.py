"""Clone the authentic prior checkpoint; retain every original byte at its source."""
from pathlib import Path
import hashlib
import json
import shutil

root = Path(__file__).resolve().parent
prior = Path('D:/OptoMind-Article-3/local-repair-validation/20261013')
source = prior / 'review/formal-01'
target = root / 'review/real-01'
assert not target.exists()
hashes = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
          for p in source.rglob('*') if p.is_file()}
shutil.copytree(source, target)
formula = root / 'review/formula-cache'
shutil.copytree(prior / 'review/formula-cache', formula)
index = root / 'review/planning_material_index.sqlite'
shutil.copyfile(prior / 'review/planning_material_index.sqlite', index)
assert all(hashlib.sha256((source / name).read_bytes()).hexdigest() == digest for name, digest in hashes.items())
(root / 'review/ORIGINAL_CHECKPOINT_SHA256.json').write_text(json.dumps(hashes, indent=2) + '\n', encoding='utf-8')
receipts = [json.loads(p.read_text(encoding='utf-8')) for p in (prior / 'entries').glob('review-*.json')]
receipt = max((x for x in receipts if '--editorial-feedback' in x['argv']), key=lambda x: x['started_at'])
args = list(receipt['argv'])
for option, value in (('--output-dir', target), ('--local-formula-cache-root', formula), ('--local-material-index', index)):
    args[args.index(option) + 1] = str(value)
args += ['--claim-scope-review']
(root / 'review/RUN_ARGV.json').write_text(json.dumps(args, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'source': str(source), 'new_checkpoint': str(target), 'source_files_preserved': len(hashes),
                  'invoice_ledger_copied': False, 'new_material_or_scientific_answer_supplied': False}, ensure_ascii=False, indent=2))
