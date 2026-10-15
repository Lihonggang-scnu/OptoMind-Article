"""One free discovery pass through the pinned Review providers; no model or parser."""
import importlib.util
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
REVIEW = Path(r'D:\OptoMind-Article-3\r3t022r3')
SOURCE = Path(r'D:\OptoMind-Article-3\r3t022a\focused_research')
os.environ['OPENALEX_API_KEYS_FILE'] = r'F:\OptoMind-Review-2\api_keys\openalex.txt'
os.environ['CORE_API_KEYS_FILE'] = r'F:\OptoMind-Review-2\api_keys\core_api.txt'
# Contact is loaded only in-process, never logged or persisted into a repository.
contact_file = Path(r'F:\OptoMind-Review-2\api_keys\Unpaywall.txt')
if contact_file.is_file():
    contact = next((x.strip() for x in contact_file.read_text(encoding='utf-8-sig').splitlines()
                    if x.strip() and '@' in x), '')
    if contact:
        os.environ['UNPAYWALL_EMAIL'] = contact
        os.environ['OPENALEX_EMAIL'] = contact
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
sys.path.insert(0, str(REVIEW))
spec = importlib.util.spec_from_file_location('_t022_fulltext_worker', SOURCE/'review_fulltext_worker.py')
worker = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = worker
spec.loader.exec_module(worker)
worker.verify_review(REVIEW)
providers, audit = worker.provider_instances(['openalex', 'core'])
queries = json.loads((ROOT/'runs/plan/queries.json').read_text(encoding='utf-8'))
out = ROOT/'runs/fulltext-discovery'
out.mkdir(parents=True, exist_ok=True)
assert not (out/'DISCOVERY_RESULT.json').exists(), 'Do not repeat completed discovery'
rows, query_audit = worker.discover(queries, providers, out, 5, True)
worker.save(out/'DISCOVERY_RESULT.json', dict(records=rows, provider_audit=audit,
    query_audit=query_audit, review_sha=worker.PIN,
    selection_note='Operator selects at most five relevant actual records before acquisition; no query expansion'))
print(json.dumps({'records': len(rows), 'providers': audit, 'queries': query_audit}, ensure_ascii=False))
