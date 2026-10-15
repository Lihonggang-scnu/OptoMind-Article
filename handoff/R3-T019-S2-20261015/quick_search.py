"""Exactly five S2 diagnostic queries, no planner acceptance/model/physics."""
from __future__ import annotations
from dataclasses import asdict
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
REPO = Path(r'D:\OptoMind-Article-3\r3t019g')
SOURCE = Path(r'D:\OptoMind-Article-3\r3t019a')
COMMIT = 'ba2bb8ff2183b478fb8bc8591fcedc8f07bbebd4'
FILE = 'handoff/R3-T019-D20/rejected-drafts/round-1.task_sheet.json'
assert not (ROOT / 'SEARCH_SUMMARY.json').exists(), 'One diagnostic only; do not repeat'
ROOT.mkdir(parents=True, exist_ok=True)
raw_draft = subprocess.check_output(['git', '-C', str(REPO), 'show', COMMIT + ':' + FILE])
with (ROOT / 'SOURCE_REJECTED_DRAFT.json').open('xb') as f:
    f.write(raw_draft)
draft = json.loads(raw_draft)
queries = draft['search_queries']
assert len(queries) == 5 and {q['id'] for q in queries} == {'Q1','Q2','Q3','Q4','Q5'}
assert all(q['endpoint'] in {'paper_search', 'snippet_search'} for q in queries)
sys.path.insert(0, str(SOURCE / 'focused_research/vendor/review'))
from optomind_research.s2_intelligence_gateway import S2Transport, S2IntelligenceGateway
from tools.academic_backends import semantic_scholar_backend as backend
assert Path(backend.__file__).resolve().is_relative_to(SOURCE / 'focused_research/vendor/review')

# A path to the already configured pool; no key values are printed or copied.
existing = os.environ.get('SEMANTIC_SCHOLAR_API_KEYS_FILE')
if not existing:
    existing = r'F:\OptoMind-Review-2\api_keys\semantic-scholar-api-key.txt'
assert Path(existing).is_file(), 'Existing S2 configuration is required'
os.environ['SEMANTIC_SCHOLAR_API_KEYS_FILE'] = existing
active_id = ''
raw_captures = []
class CapturedResponse(io.BytesIO):
    def __init__(self, data, status, headers):
        super().__init__(data)
        self.status, self.headers = status, headers

def scrub_known_secrets(raw):
    # Provider error bodies should not echo keys; keep any such value local.
    for key in transport.keys:
        if key:
            raw = raw.replace(key.encode(), b'[REDACTED-CREDENTIAL]')
    return raw

def capture_open(request, timeout=None):
    index = sum(r['query_id'] == active_id for r in raw_captures) + 1
    path = ROOT / 'raw' / (active_id + '-http-' + str(index) + '.response.json')
    path.parent.mkdir(exist_ok=True)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
            status, headers = response.status, response.headers
        path.write_bytes(scrub_known_secrets(data))
        raw_captures.append({'query_id': active_id, 'status': status, 'request_url': request.full_url,
            'raw_file': str(path.relative_to(ROOT)), 'elapsed_seconds': round(time.monotonic()-started, 3)})
        return CapturedResponse(data, status, headers)
    except urllib.error.HTTPError as exc:
        data = exc.read()
        path.write_bytes(scrub_known_secrets(data))
        raw_captures.append({'query_id': active_id, 'status': exc.code, 'request_url': request.full_url,
            'raw_file': str(path.relative_to(ROOT)), 'elapsed_seconds': round(time.monotonic()-started, 3)})
        raise urllib.error.HTTPError(exc.url, exc.code, exc.reason, exc.headers, io.BytesIO(data))
    except Exception as exc:
        raw_captures.append({'query_id': active_id, 'status': 0, 'request_url': request.full_url,
            'error_type': type(exc).__name__, 'elapsed_seconds': round(time.monotonic()-started, 3)})
        raise

transport = S2Transport(cache_path=ROOT/'private/s2-cache.sqlite', timeout_seconds=25,
                        min_interval_seconds=1.1, max_attempts=1, opener=capture_open)
assert transport.keys, 'No existing S2 key loaded; no substitute provider/account'
gateway = S2IntelligenceGateway(transport)
def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, ensure_ascii=False, indent=2, default=str).encode()
    with path.open('xb') as f:
        f.write(scrub_known_secrets(raw) + b'\n')

save(ROOT/'DIAGNOSTIC_SCOPE.json', {'source_commit':COMMIT, 'source_file':FILE,
    'source_file_sha256':hashlib.sha256(raw_draft).hexdigest(),
    'search_queries':queries, 'limit_each':10, 'one_attempt_each':True,
    'program_source_sha':subprocess.check_output(['git','-C',str(SOURCE),'rev-parse','HEAD'],text=True).strip(),
    'existing_S2_configuration_reused':True, 'task_sheet_acceptance_required':False,
    'rejected_draft_still_rejected':True, 'production_code_changed':False,
    'llm_calls':0, 'paid_calls':0, 'enrichment_calls':0, 'fulltext_downloads':0,
    'unit_tests':0, 'physics':0, 'pdf':0})
summary = []
for query in queries:
    active_id = query['id']
    started = time.monotonic()
    response = None
    rows = []
    error = None
    try:
        if query['endpoint'] == 'paper_search':
            papers, response = gateway.search_papers(query['query'], limit=10, enrich_limit=0,
                deadline=time.monotonic()+30, fast_fail_retryable=True)
            raw_rows = (response.payload or {}).get('data', [])
        else:
            raw_rows, response = gateway.search_snippets(query['query'], limit=10, max_workers=1,
                deadline=time.monotonic()+30, fast_fail_retryable=True)
        for rank, original_row in enumerate(raw_rows[:10],1):
            paper = original_row if query['endpoint']=='paper_search' else original_row.get('paper',{})
            snippet = original_row.get('snippet') or {}
            if not isinstance(paper,dict):paper={}
            external = paper.get('externalIds') or {}
            pid, cid = paper.get('paperId'), paper.get('corpusId')
            doi = external.get('DOI')
            link = paper.get('url') or ('https://doi.org/'+doi if doi else None)
            if not link and pid:link='https://www.semanticscholar.org/paper/'+str(pid)
            if not link and cid:link='https://www.semanticscholar.org/paper/CorpusID:'+str(cid)
            rows.append({'rank':rank,'title':paper.get('title'),'year':paper.get('year'),
                'paper_id':pid,'corpus_id':cid,'doi':doi,'source_link':link,
                'abstract':paper.get('abstract'),'snippet_text':snippet.get('text'),
                'snippet_kind':snippet.get('snippetKind'),'snippet_section':snippet.get('section'),
                'content_kind':'snippet' if snippet else ('abstract' if paper.get('abstract') else 'metadata'),
                'raw_row':original_row})
    except Exception as exc:
        error={'type':type(exc).__name__,'message':str(exc)}
    run = {'query_id':query['id'],'endpoint':query['endpoint'],'query':query['query'],
           'returned_count':len(rows),'ok':bool(response and response.ok),
           'response':asdict(response) if response else None,'exception':error,
           'elapsed_seconds':round(time.monotonic()-started,3),'results':rows}
    save(ROOT/'queries'/(query['id']+'.json'),run)
    summary.append({k:run[k] for k in ('query_id','endpoint','query','returned_count','ok','exception','elapsed_seconds')})
    print(query['id']+': '+str(len(rows))+' results; '+str(response.status_code if response else 'exception'),flush=True)
save(ROOT/'HTTP_CALLS.json', raw_captures)
save(ROOT/'SEARCH_SUMMARY.json', {'queries':summary,'http_calls':len(raw_captures),
    'llm_calls':0,'paid_calls':0,'enrichment_calls':0,'fulltext_downloads':0,
    'finished_at':datetime.datetime.now(datetime.timezone.utc).isoformat()})
print('Exactly five diagnostic queries finished; no further retrieval.',flush=True)
