"""Isolated bridge to the pinned, mature Review full-text implementation.

No parser/model/provider implementation lives here. The external interpreter owns
Review imports and its local cached Docling models; no paid reader is enabled.
"""
from __future__ import annotations
import argparse
import hashlib
import signal
import uuid
import json
import os
from pathlib import Path
import subprocess

REVIEW_COMMIT = 'd2deb4dbe18b23837d949822ad731afbaba9414b'
HELPER = Path(__file__).with_name('review_fulltext_worker.py')


def run_fulltext_stage(records, queries, output_root, *, review_root, review_python,
                       docling_python=None, docling_config=None, docling_batch_index=None,
                       providers=('openalex', 'unpaywall', 'core', 'crossref', 'arxiv'),
                       max_papers=5, deadline_seconds=180, local_formula_config=None,
                       formula_selected_ids=(), resume=True, enrich_result=None):
    if not 1 <= int(max_papers) <= 5:
        raise ValueError('fulltext max_papers must be 1..5')
    if not 1 <= float(deadline_seconds) <= 1800:
        raise ValueError('deadline_seconds must be 1..1800')
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    request = dict(records=list(records), queries=list(queries), output_root=str(root),
        review_root=str(Path(review_root).resolve()), review_commit=REVIEW_COMMIT,
        providers=list(providers), max_papers=int(max_papers), deadline_seconds=float(deadline_seconds),
        resume=bool(resume), formula_selected_ids=list(formula_selected_ids))
    for key, value in [('docling_python', docling_python), ('docling_config', docling_config),
                       ('docling_batch_index', docling_batch_index), ('local_formula_config', local_formula_config)]:
        if value:
            request[key] = str(Path(value).resolve()) if key != 'docling_python' else str(value)
    if enrich_result:
        request['enrich_result']=str(Path(enrich_result).resolve())
        if not local_formula_config or not formula_selected_ids:
            raise ValueError('formula enrichment requires config and selected IDs')
        if root == Path(enrich_result).resolve().parent:
            raise ValueError('formula enrichment requires a new output directory')
    request_path = root / 'FULLTEXT_REQUEST.json'
    identity = hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()
    if request_path.is_file():
        previous=json.loads(request_path.read_text(encoding='utf-8'))
        if previous.get('request_identity') != identity:
            raise ValueError('output root belongs to a different fulltext request')
    request.update(request_identity=identity, execution_id=uuid.uuid4().hex)
    request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding='utf-8')
    env = {**os.environ, 'HF_HUB_OFFLINE':'1', 'TRANSFORMERS_OFFLINE':'1', 'HF_DATASETS_OFFLINE':'1'}
    # Never let the focused runner's older vendored Review leak into the child.
    env.pop('PYTHONPATH', None)
    with (root / 'worker.stdout.log').open('wb') as stdout, (root / 'worker.stderr.log').open('wb') as stderr:
        command=[str(review_python), '-B', '-X', 'utf8', str(HELPER), '--request', str(request_path)]
        options={'start_new_session':True} if os.name != 'nt' else {'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP}
        process=subprocess.Popen(command,stdout=stdout,stderr=stderr,env=env,**options)
        # Budget includes provider discovery, every paper deadline, and the bounded local model lanes.
        timeout=(len(request['queries'])*60 + max_papers*deadline_seconds*2 + 1800 + len(formula_selected_ids)*660)
        try:
            returncode=process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == 'nt':
                subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True)
            else:
                os.killpg(process.pid,signal.SIGKILL)
            process.wait()
            return {'schema_version':'focused_fulltext_result_v1','status':'blocked','exports':[],
                    'error':'external_review_timeout','request_identity':identity}

    result_path = root / 'FULLTEXT_RESULT.json'
    if returncode or not result_path.is_file():
        return {'schema_version':'focused_fulltext_result_v1', 'status':'blocked', 'exports':[],
                'error':'external_review_worker_failed', 'returncode':returncode,
                'stderr_path':str(root / 'worker.stderr.log')}
    result=json.loads(result_path.read_text(encoding='utf-8'))
    if result.get('execution_id') != request['execution_id'] or result.get('request_identity') != identity:
        return {'schema_version':'focused_fulltext_result_v1','status':'blocked','exports':[],
                'error':'stale_or_mismatched_fulltext_result'}
    return result


def enrich_selected_materials(result_path, output_root, *, review_root, review_python,
                              local_formula_config, formula_selected_ids):
    """Enhance explicitly selected existing snapshots; no discovery or basic parse."""
    return run_fulltext_stage([],[],output_root,review_root=review_root,review_python=review_python,
        providers=(),local_formula_config=local_formula_config,
        formula_selected_ids=formula_selected_ids,enrich_result=result_path)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--enrich-result', type=Path, help='Existing FULLTEXT_RESULT.json; selected formula-only phase')
    p.add_argument('--records', type=Path)
    p.add_argument('--queries', type=Path)
    p.add_argument('--output-root', required=True)
    p.add_argument('--review-root', required=True)
    p.add_argument('--review-python', required=True)
    p.add_argument('--docling-python')
    p.add_argument('--docling-config')
    p.add_argument('--docling-batch-index')
    p.add_argument('--providers', default='openalex,unpaywall,core,crossref,arxiv')
    p.add_argument('--max-papers', type=int, default=5)
    p.add_argument('--deadline-seconds', type=float, default=180)
    p.add_argument('--local-formula-config')
    p.add_argument('--formula-selected-id', action='append', default=[])
    p.add_argument('--no-resume', action='store_true')
    a=p.parse_args()
    records=json.loads(a.records.read_text(encoding='utf-8')) if a.records else []
    queries=json.loads(a.queries.read_text(encoding='utf-8')) if a.queries else []
    if isinstance(records,dict): records=records.get('records',[])
    if isinstance(queries,dict): queries=queries.get('queries',[])
    result=run_fulltext_stage(records,queries,a.output_root,review_root=a.review_root,
        review_python=a.review_python,docling_python=a.docling_python,docling_config=a.docling_config,
        docling_batch_index=a.docling_batch_index,providers=[x for x in a.providers.split(',') if x],
        max_papers=a.max_papers,deadline_seconds=a.deadline_seconds,
        local_formula_config=a.local_formula_config,formula_selected_ids=a.formula_selected_id,resume=not a.no_resume,enrich_result=a.enrich_result)
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result['status'] != 'blocked' else 2

if __name__ == '__main__':
    raise SystemExit(main())
