"""External-process adapter. All scientific extraction is delegated to Review."""
from __future__ import annotations
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

PIN = 'd2deb4dbe18b23837d949822ad731afbaba9414b'
PROVIDERS = {'openalex':'OpenAlexBackend', 'unpaywall':'UnpaywallBackend',
             'core':'CoreBackend', 'crossref':'CrossrefBackend', 'arxiv':'ArxivBackend',
             'semantic_scholar':'SemanticScholarBackend'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    path=Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    tmp.replace(path)


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def verify_review(root, requested=PIN):
    root=Path(root).resolve()
    if requested != PIN: raise ValueError('unsupported_review_commit')
    head=subprocess.run(['git','-C',str(root),'rev-parse','HEAD'],capture_output=True,text=True,check=True).stdout.strip()
    if head != PIN: raise ValueError('review_commit_mismatch')
    dirty=subprocess.run(['git','-C',str(root),'status','--porcelain','--untracked-files=all','--',
        'optomind_research','tools','scripts/upgrade3'],capture_output=True,text=True,check=True).stdout
    if dirty.strip(): raise ValueError('review_runtime_has_local_changes')
    required=['scripts/upgrade3/parse_docling_batch.py','optomind_research/runtime/upgrade3/local_materials.py']
    if any(not (root/p).is_file() for p in required): raise ValueError('review_runtime_missing')
    return root


def provider_instances(names):
    unknown=set(names)-set(PROVIDERS)
    if unknown: raise ValueError('unknown_acquisition_provider')
    # Explicit None prevents MaterialAcquirer silently loading other providers.
    result={key:None for key in PROVIDERS}; result['grobid']=None
    audit=[]
    for name in names:
        try:
            module=importlib.import_module('tools.academic_backends.'+name+'_backend')
            provider=getattr(module,PROVIDERS[name])()
            enabled=bool(getattr(provider,'enabled',True))
            result[name]=provider if enabled else None
            audit.append({'provider':name,'status':'configured' if enabled else 'not_configured'})
        except Exception as exc:
            audit.append({'provider':name,'status':'unavailable','error':type(exc).__name__})
    return result,audit


def normalize_record(record):
    record=dict(record)
    paper=record.get('paper')
    if isinstance(paper,dict): record={**paper,**record}
    ids=record.get('externalIds') or {}
    record.setdefault('doi',ids.get('DOI',''))
    record.setdefault('semantic_scholar_paper_id',record.get('paperId',''))
    record.setdefault('corpus_id',str(record.get('corpusId') or ''))
    if isinstance(record.get('openAccessPdf'),dict):
        record.setdefault('pdf_url',record['openAccessPdf'].get('url',''))
        record.setdefault('is_oa',True)
    record.setdefault('canonical_paper_id',record.get('doi') or record.get('openalex_id') or
                      record.get('semantic_scholar_paper_id') or record.get('source_id') or digest(record)[:20])
    return record


def discover(queries, providers, root, limit, resume):
    candidates=[]; audit=[]
    for query in queries:
        if isinstance(query,str): query={'query_text':query}
        text=str(query.get('query_text') or query.get('query') or '').strip()
        if not text: continue
        for name in ('openalex','core'):
            provider=providers.get(name)
            if provider is None: continue
            key=digest({'pin':PIN,'provider':name,'query':text,'limit':limit})
            path=root/'query_cache'/(key+'.json')
            try:
                cached=read(path) if resume and path.is_file() else None
                if cached and cached.get('sha256') != digest(cached['rows']): raise ValueError('query_cache_corrupt')
                if cached:
                    rows=cached['rows']
                else:
                    rows=provider.search(text,max_results=limit)
                    # Do not cache transient errors as successful empty searches.
                    if not getattr(provider,'last_error',''):
                        save(path,{'sha256':digest(rows),'rows':rows})
                audit.append({'provider':name,'query_id':query.get('query_id',''),
                    'query':text,'returned':len(rows),'cached':bool(cached),
                    'status':str(getattr(provider,'last_status','ready'))})
                for row in rows:
                    task_query=query.get('task_query') or query
                    query_id=task_query.get('id') or query.get('query_id')
                    candidates.append({**row,'query_ids':[query_id] if query_id else [],
                        'goal_ids':list(task_query.get('subgoal_ids') or query.get('subgoal_ids') or []),
                        'execution_query_id':query.get('query_id','')})
            except Exception as exc:
                audit.append({'provider':name,'query':text,'status':'failed','error':type(exc).__name__})
    return candidates,audit


def eligible(result, snapshot):
    # A fallback published snapshot is not equivalent to full-text acquisition.
    return result.get('parser') in {'jats','tei','docling'} and snapshot.reading_policy().get('material_scope') in {'fulltext','structured_partial'}


def enrich_existing(request):
    """Immutable follow-on phase: validate existing sources, then local hook only."""
    from optomind_research.runtime.upgrade3.local_materials import PreparedSnapshotProvider
    from optomind_research.runtime.upgrade3.local_formula_materials import make_local_formula_enhancer,load_formula_materials,load_region_index
    source_result=Path(request['enrich_result']).resolve()
    root=Path(request['output_root']).resolve()
    if root==source_result.parent: raise ValueError('enrichment_output_must_be_new')
    previous=read(source_result)
    if previous.get('review_commit')!=PIN: raise ValueError('enrichment_review_pin_mismatch')
    selected=set(request.get('formula_selected_ids',[]))
    if not selected or not request.get('local_formula_config'):
        raise ValueError('enrichment_requires_selected_ids_and_config')
    hook_config=read(request['local_formula_config'])
    if hook_config.get('command'): raise ValueError('custom_formula_commands_not_allowed')
    driver=Path(hook_config['driver_config'])
    if not driver.is_absolute(): driver=Path(request['local_formula_config']).parent/driver
    if not 0 < read(driver).get('gpu_limit_mib',8192) <= 8192: raise ValueError('formula_gpu_ceiling_invalid')
    hook=make_local_formula_enhancer(request['local_formula_config'],cache_root=source_result.parent/'formula_cache')
    exports=[]; rows=[]; matched=set()
    for source_path in previous.get('exports',[]):
        row={'source_export_path':str(source_path)}
        try:
            envelope=read(source_path)
            if envelope.get('verified') is not True or envelope.get('review_commit')!=PIN:
                raise ValueError('unverified_enrichment_export')
            snapshot=PreparedSnapshotProvider(envelope['snapshot_path']).load()
            if digest(envelope['material'])!=digest(snapshot.as_material()):
                raise ValueError('enrichment_export_snapshot_mismatch')
            identifiers={snapshot.snapshot_id,str(snapshot.manifest.get('canonical_paper_id') or '')}
            chosen=identifiers & selected
            row.update(snapshot_id=snapshot.snapshot_id,formula_status='not_selected')
            if chosen:
                matched.update(chosen)
                try:
                    sidecar=hook(snapshot_dir=snapshot.root,output_dir=root/'formula_materials')
                    if sidecar:
                        envelope['local_formula_materials']=load_formula_materials(snapshot.root,sidecar)
                        envelope['region_index']=load_region_index(snapshot.root)[1]
                        row['formula_status']='recognized'
                    else: row['formula_status']='no_formula_regions'
                except Exception as exc:
                    row.update(formula_status='blocked',error=type(exc).__name__)
            path=root/'exports'/(snapshot.snapshot_id+'.json')
            save(path,envelope); exports.append(str(path)); row['export_path']=str(path)
        except Exception as exc:
            row.update(formula_status='blocked',error=type(exc).__name__)
        rows.append(row)
    unmatched=sorted(selected-matched)
    return {'schema_version':'focused_fulltext_result_v1','review_commit':PIN,'phase':'selected_formula_enrichment',
        'status':'complete' if not unmatched and all(r.get('formula_status')!='blocked' for r in rows) else 'partial',
        'exports':exports,'records':rows,'unmatched_selected_ids':unmatched,
        'source_result':str(source_result),'paid_model_calls':0,'firecrawl_enabled':False,
        'discovery_calls':0,'basic_parse_calls':0}


def execute(request):
    review=verify_review(request['review_root'],request.get('review_commit',PIN))
    sys.path.insert(0,str(review))
    if request.get('enrich_result'):
        return enrich_existing(request)
    from optomind_research.runtime.upgrade3.material_acquisition import MaterialAcquirer, AcquisitionConfig
    from optomind_research.runtime.upgrade3.local_materials import PreparedSnapshotProvider
    from optomind_research.runtime.upgrade3.docling_adapter import DoclingBatchAdapter
    root=Path(request['output_root']).resolve(); root.mkdir(parents=True,exist_ok=True)
    providers,audit=provider_instances(request.get('providers',[]))
    class PdfQueue:
        # Acquisition owns HTTP/identity handling. This bridge only retains its
        # original bytes until the canonical resident batch can parse them.
        def __init__(self): self.pending=[]
        def prepare_document(self,pdf_bytes,**kwargs):
            self.pending.append(bytes(pdf_bytes))
            raise ValueError('queued_for_resident_docling_batch')
    pdf_queue=PdfQueue()
    providers['docling']=pdf_queue
    config=AcquisitionConfig(cache_root=root/'acquisition_cache',deadline_seconds=request['deadline_seconds'],
        network_workers=1,rescue_enabled=False,rescue_max_search_calls=0,rescue_max_extract_calls=0,
        docling_enabled=True,docling_primary=True)
    acquirer=MaterialAcquirer(root/'acquired',config=config,providers=providers,reader=None)
    found,query_audit=discover(request.get('queries',[]),providers,root,request['max_papers'],request.get('resume',True))
    # Interleave independent discovery with supplied candidates so S2 cannot consume the entire cap.
    supplied=[normalize_record(r) for r in request.get('records',[])]
    discovered=[normalize_record(r) for r in found]
    candidates=[]; seen={}
    for i in range(max(len(supplied),len(discovered))):
        for group in (supplied,discovered):
            if i >= len(group): continue
            record=group[i]; key=str(record.get('doi') or record['canonical_paper_id']).casefold()
            if key in seen:
                old=seen[key]
                for field in ('query_ids','goal_ids'):
                    old[field]=list(dict.fromkeys([*(old.get(field) or []),*(record.get(field) or [])]))
                continue
            seen[key]=record; candidates.append(record)
    save(root/'CANDIDATES.json',{'records':candidates,'query_audit':query_audit})
    selected=candidates[:request['max_papers']]; rows=[]; pdf_records=[]; prepared={}
    for index,record in enumerate(selected):
        local_hashes={}
        for field in ('local_xml','local_pdf'):
            if record.get(field):
                path=Path(record[field])
                local_hashes[field]=hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else 'missing'
        key=digest({'pin':PIN,'record':record,'providers':request.get('providers',[]),'local_hashes':local_hashes})
        receipt=root/'acquisition_receipts'/(key+'.json')
        try:
            prior=read(receipt) if request.get('resume') and receipt.is_file() else None
            if prior:
                snapshot=PreparedSnapshotProvider(prior['acquisition']['snapshot_path']).load() if prior['acquisition'].get('snapshot_path') else None
                acquisition=prior['acquisition']; pdf_path=prior.get('local_pdf')
                if pdf_path and hashlib.sha256(Path(pdf_path).read_bytes()).hexdigest()!=prior.get('pdf_sha256'):
                    raise ValueError('acquisition_pdf_cache_corrupt')
            else:
                pdf_queue.pending.clear()
                local_xml=Path(record['local_xml']) if record.get('local_xml') else None
                local_pdf=Path(record['local_pdf']) if record.get('local_pdf') else None
                if local_xml or local_pdf:
                    outcome=acquirer.acquire_local(record,xml_bytes=local_xml.read_bytes() if local_xml else None,
                        pdf_bytes=local_pdf.read_bytes() if local_pdf else None,
                        source_name=(local_xml or local_pdf).name)
                else:
                    outcome=acquirer.acquire(record)
                acquisition=outcome.to_dict(); snapshot=outcome.snapshot; pdf_path=None; pdf_sha=''
                pdf=next((a for a in outcome.artifacts if a.kind=='pdf'),None)
                pdf_bytes=pdf.data if pdf is not None else (pdf_queue.pending[-1] if pdf_queue.pending else None)
                if pdf_bytes is not None:
                    pdf_sha=hashlib.sha256(pdf_bytes).hexdigest(); target=root/'pdfs'/(pdf_sha+'.pdf')
                    target.parent.mkdir(exist_ok=True); target.write_bytes(pdf_bytes); pdf_path=str(target)
                if pdf_path or (snapshot is not None and eligible(acquisition,snapshot)):
                    save(receipt,{'acquisition':acquisition,'local_pdf':pdf_path,'pdf_sha256':pdf_sha})
            row={'record':record,'acquisition':acquisition,'status':'no_verified_body'}; rows.append(row)
            if snapshot is not None and eligible(acquisition,snapshot):
                prepared[index]=(snapshot,record,row)
            elif pdf_path:
                pdf_records.append({**record,'label':'paper_'+str(index),'local_pdf':pdf_path,'_index':index})
                row['status']='pdf_pending_docling'
        except Exception as exc:
            rows.append({'record':record,'status':'failed','error':type(exc).__name__})
    batch=request.get('docling_batch_index')
    batch_status='not_needed'
    try:
        if pdf_records and not batch:
            if request.get('docling_python') and request.get('docling_config'):
                profile=read(request['docling_config'])
                pipeline=profile.get('pipeline_options',{})
                if not (profile.get('safe_parser') is True and profile.get('max_documents_per_worker')==20
                        and profile.get('backend_options',{}).get('parser_threads')==1
                        and 0 < profile.get('gpu_limit_mib',0) <= 8192
                        and pipeline.get('accelerator_options',{}).get('num_threads')==4
                        and all(pipeline.get(name)==4 for name in ('ocr_batch_size','layout_batch_size','table_batch_size'))):
                    profile_valid=False
                else: profile_valid=True
                if not profile_valid:
                    batch_status='blocked_invalid_docling_profile'
                    pdf_records_to_run=[]
                else: pdf_records_to_run=pdf_records
                manifest=root/'PDF_MANIFEST.json'; save(manifest,{'records':pdf_records_to_run})
                command=[request['docling_python'],'-B','-X','utf8',str(review/'scripts/upgrade3/parse_docling_batch.py'),
                    '--mode','basic','--manifest',str(manifest),'--config',request['docling_config'],
                    '--output-root',str(root/'docling_batch'),'--workers','3','--resume']
                with (root/'docling.stdout.log').open('wb') as stdout,(root/'docling.stderr.log').open('wb') as stderr:
                    completed=subprocess.run(command,cwd=review,stdout=stdout,stderr=stderr) if profile_valid else None
                if completed is not None: batch_status='completed' if completed.returncode==0 else 'partial_or_failed'
                possible=root/'docling_batch'/'BATCH_RESULT.json'
                if profile_valid and possible.is_file(): batch=str(possible)
            else:
                batch_status='blocked_missing_local_docling_runtime'
    except Exception as exc:
        batch_status='blocked_local_docling:'+type(exc).__name__
    if pdf_records and batch:
        try:
            adapter=DoclingBatchAdapter(batch,cache_root=root/'docling_cache',render_crops=False)
            config.docling_enabled=config.docling_primary=True
            config.docling_config_sha256=hashlib.sha256(Path(batch).read_bytes()).hexdigest()
            parser=MaterialAcquirer(root/'docling_materials',config=config,providers={**providers,'docling':adapter},reader=None)
            for record in pdf_records:
                index=record['_index']; row=next(r for r in rows if r['record']['canonical_paper_id']==record['canonical_paper_id'])
                try:
                    outcome=parser.acquire_local(record,pdf_bytes=Path(record['local_pdf']).read_bytes(),source_name=Path(record['local_pdf']).name)
                    row['docling_acquisition']=outcome.to_dict()
                    if outcome.snapshot and outcome.parser=='docling' and eligible(outcome.to_dict(),outcome.snapshot):
                        prepared[index]=(outcome.snapshot,record,row)
                    else: row['status']='docling_failed'
                except Exception as exc:
                    row.update(status='docling_failed',error=type(exc).__name__)
        except Exception as exc:
            batch_status='batch_import_failed:'+type(exc).__name__
    exports=[]
    selected_formulas=set(request.get('formula_selected_ids',[]))
    for index,(snapshot,record,row) in sorted(prepared.items()):
        snapshot=PreparedSnapshotProvider(snapshot.root).load()
        envelope={'schema_version':'review_material_export_v1','verified':True,'review_commit':PIN,
            'snapshot_path':str(snapshot.root),'material':snapshot.as_material(),'source_record':record}
        if request.get('local_formula_config') and (snapshot.snapshot_id in selected_formulas or record['canonical_paper_id'] in selected_formulas):
            try:
                from optomind_research.runtime.upgrade3.local_formula_materials import make_local_formula_enhancer,load_formula_materials,load_region_index
                hook_config=read(request['local_formula_config'])
                if hook_config.get('command'): raise ValueError('custom_formula_commands_not_allowed')
                driver=Path(hook_config['driver_config'])
                if not driver.is_absolute(): driver=Path(request['local_formula_config']).parent/driver
                if not 0 < read(driver).get('gpu_limit_mib',8192) <= 8192: raise ValueError('formula_gpu_ceiling_invalid')
                hook=make_local_formula_enhancer(request['local_formula_config'],cache_root=root/'formula_cache')
                sidecar=hook(snapshot_dir=snapshot.root,output_dir=root/'formula_materials')
                if sidecar:
                    envelope['local_formula_materials']=load_formula_materials(snapshot.root,sidecar)
                    envelope['region_index']=load_region_index(snapshot.root)[1]
                    row['formula_status']='recognized'
                else: row['formula_status']='no_formula_regions'
            except Exception as exc:
                row.update(formula_status='blocked',formula_error=type(exc).__name__)
        else: row['formula_status']='not_selected'
        path=root/'exports'/(snapshot.snapshot_id+'.json'); save(path,envelope)
        exports.append(str(path)); row.update(status='verified_material',export_path=str(path),snapshot_id=snapshot.snapshot_id)
    return {'schema_version':'focused_fulltext_result_v1','review_commit':PIN,
        'status':'complete' if exports and len(exports)==len(selected) else ('partial' if exports else 'empty'),
        'exports':exports,'records':rows,'provider_audit':audit,'query_audit':query_audit,
        'candidate_count':len(candidates),'selected_count':len(selected),'docling_status':batch_status,
        'paid_model_calls':0,'firecrawl_enabled':False}


def main():
    p=argparse.ArgumentParser(); p.add_argument('--request',required=True); args=p.parse_args()
    request=read(args.request)
    try: result=execute(request)
    except Exception as exc:
        result={'schema_version':'focused_fulltext_result_v1','status':'blocked','exports':[],
                'error':type(exc).__name__}
        if isinstance(exc,ModuleNotFoundError): result['missing_dependency']=exc.name
    result.update(execution_id=request.get('execution_id'),request_identity=request.get('request_identity'))
    save(Path(request['output_root'])/'FULLTEXT_RESULT.json',result)
    return 0

if __name__=='__main__': raise SystemExit(main())
