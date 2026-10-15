"""Offline bridge contracts; no providers or model calls."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

ROOT=Path(__file__).resolve().parents[1]

def load(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/(name+'.py'))
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module

w=load('review_fulltext_worker')
f=load('fulltext_stage')

def test_no_metadata_or_fitz_promoted_to_body():
    snapshot=SimpleNamespace(reading_policy=lambda:{'material_scope':'fulltext'})
    assert w.eligible({'parser':'docling'},snapshot)
    assert w.eligible({'parser':'jats'},snapshot)
    assert not w.eligible({'parser':'fitz'},snapshot)
    assert not w.eligible({'parser':'grobid'},snapshot)
    snapshot.reading_policy=lambda:{'material_scope':'abstract_only'}
    assert not w.eligible({'parser':'jats'},snapshot)


def test_non_s2_discovery_and_resume(tmp_path):
    calls=[]
    provider=SimpleNamespace(last_error='',last_status='ready',search=lambda query, max_results:
        calls.append(query) or [{'doi':'10.1/a','title':'Native OA source','backend':'openalex'}])
    query=[{'query_id':'Q1','facet_id':'R1','query_text':'optical device','task_query':{'id':'Q1','subgoal_ids':['G1']}}]
    rows,audit=w.discover(query,{'openalex':provider},tmp_path,5,True)
    assert rows[0]['query_ids']==['Q1'] and rows[0]['goal_ids']==['G1']
    assert audit[0]['provider']=='openalex' and calls==['optical device']
    again,audit=w.discover(query,{'openalex':provider},tmp_path,5,True)
    assert again==rows and audit[0]['cached'] and len(calls)==1
    cached=next((tmp_path/'query_cache').glob('*.json'))
    value=json.loads(cached.read_text());value['rows'][0]['title']='tampered';cached.write_text(json.dumps(value))
    rows,audit=w.discover(query,{'openalex':provider},tmp_path,5,True)
    assert not rows and audit[0]['status']=='failed'


def test_partial_provider_failure_does_not_erase_other_sources(tmp_path):
    def fail(*a,**kw): raise RuntimeError('secret must not leak')
    rows,audit=w.discover([{'query_text':'optics'}],{
        'openalex':SimpleNamespace(search=fail),
        'core':SimpleNamespace(search=lambda *a,**k:[{'title':'core result'}],last_error='')},tmp_path,5,True)
    assert len(rows)==1 and rows[0]['title']=='core result'
    assert 'secret' not in json.dumps(audit)


def test_pin_and_paper_cap_rejected_before_work(tmp_path):
    with pytest.raises(ValueError,match='unsupported_review_commit'): w.verify_review(tmp_path,'bad')
    with pytest.raises(ValueError,match='max_papers'):
        f.run_fulltext_stage([],[],tmp_path,review_root=tmp_path,review_python='python',max_papers=6)


def test_wrapper_rejects_stale_result_and_changed_request(tmp_path,monkeypatch):
    class Process:
        def __init__(self,*a,**kw): pass
        def wait(self,timeout=None): return 0
    monkeypatch.setattr(f.subprocess,'Popen',Process)
    (tmp_path/'FULLTEXT_RESULT.json').write_text(json.dumps({'status':'complete','exports':['stale.json']}))
    result=f.run_fulltext_stage([],[],tmp_path,review_root=tmp_path,review_python='python')
    assert result['error']=='stale_or_mismatched_fulltext_result'
    with pytest.raises(ValueError,match='different fulltext request'):
        f.run_fulltext_stage([{'doi':'new'}],[],tmp_path,review_root=tmp_path,review_python='python')


def test_s2_record_normalization_preserves_original():
    value=w.normalize_record({'paper':{'paperId':'s2id','externalIds':{'DOI':'10.1/abc'},
        'openAccessPdf':{'url':'https://example.org/p.pdf'}},'snippet':{'text':'exact'}})
    assert value['doi']=='10.1/abc' and value['semantic_scholar_paper_id']=='s2id'
    assert value['pdf_url']=='https://example.org/p.pdf' and value['snippet']['text']=='exact'


def test_download_queue_without_fitz_reaches_batch_manifest_and_resumes(tmp_path,monkeypatch):
    import sys
    from types import ModuleType
    pdf=tmp_path/'input.pdf'; pdf.write_bytes(b'%PDF-fixture-original-bytes')
    calls=[]
    class Acquirer:
        def __init__(self,output_root,**kwargs): self.providers=kwargs['providers']
        def acquire_local(self,record,**kwargs):
            calls.append('acquire')
            with pytest.raises(ValueError,match='queued_for_resident'):
                self.providers['docling'].prepare_document(kwargs['pdf_bytes'])
            # Deliberately no fitz, no fallback snapshot, no successful artifact.
            return SimpleNamespace(artifacts=[],snapshot=None,to_dict=lambda:{'snapshot_path':'','parser':'fallback'})
    module=ModuleType('material_acquisition');module.MaterialAcquirer=Acquirer
    module.AcquisitionConfig=lambda **kwargs:SimpleNamespace(**kwargs)
    snapshots=ModuleType('local_materials');snapshots.PreparedSnapshotProvider=lambda *a:None
    adapters=ModuleType('docling_adapter');adapters.DoclingBatchAdapter=lambda *a,**k:None
    for name,value in [('material_acquisition',module),('local_materials',snapshots),('docling_adapter',adapters)]:
        monkeypatch.setitem(sys.modules,'optomind_research.runtime.upgrade3.'+name,value)
    monkeypatch.setattr(w,'verify_review',lambda *a:tmp_path)
    profile=tmp_path/'config.json';profile.write_text(json.dumps({'safe_parser':True,'max_documents_per_worker':20,
        'backend_options':{'parser_threads':1},'gpu_limit_mib':8192,'pipeline_options':{
            'accelerator_options':{'num_threads':4},'ocr_batch_size':4,'layout_batch_size':4,'table_batch_size':4}}))
    commands=[]
    monkeypatch.setattr(w.subprocess,'run',lambda cmd,**kwargs:commands.append(cmd) or SimpleNamespace(returncode=1))
    request={'review_root':str(tmp_path),'output_root':str(tmp_path/'output'),'providers':[],
        'queries':[],'records':[{'canonical_paper_id':'paper','local_pdf':str(pdf)}],
        'max_papers':5,'deadline_seconds':60,'resume':True,'docling_python':'python','docling_config':str(profile)}
    result=w.execute(request)
    assert not result['exports'] and result['records'][0]['status']=='pdf_pending_docling'
    manifest=json.loads((tmp_path/'output/PDF_MANIFEST.json').read_text())
    assert Path(manifest['records'][0]['local_pdf']).read_bytes()==pdf.read_bytes()
    assert '--workers' in commands[0] and commands[0][commands[0].index('--workers')+1]=='3'
    w.execute(request)
    assert calls==['acquire']  # Download receipt survives even without a fallback snapshot.


def test_followon_formula_enrichment_preserves_original_without_retrieval(tmp_path,monkeypatch):
    import sys
    from types import ModuleType
    original=tmp_path/'original';original.mkdir()
    snapshot=SimpleNamespace(root=original/'snapshot',snapshot_id='S1',manifest={'canonical_paper_id':'paper1'},
        as_material=lambda:{'snapshot_id':'S1','blocks':[{'text_raw':'raw formula'}]})
    export=original/'export.json'
    export.write_text(json.dumps({'verified':True,'review_commit':w.PIN,'snapshot_path':str(snapshot.root),
        'material':snapshot.as_material(),'source_record':{'canonical_paper_id':'paper1'}}))
    before=export.read_bytes()
    result=original/'FULLTEXT_RESULT.json'
    result.write_text(json.dumps({'review_commit':w.PIN,'exports':[str(export)]}))
    config=tmp_path/'formula.json';driver=tmp_path/'driver.json'
    config.write_text(json.dumps({'python':'local-python','driver_config':str(driver)}))
    driver.write_text(json.dumps({'gpu_limit_mib':8192}))
    calls=[]
    snapshots=ModuleType('local_materials'); snapshots.PreparedSnapshotProvider=lambda path:SimpleNamespace(load=lambda:snapshot)
    formulas=ModuleType('local_formula_materials')
    formulas.make_local_formula_enhancer=lambda *a,**k: lambda **kw:calls.append(kw) or 'sidecar'
    formulas.load_formula_materials=lambda *a:{'regions':[{'region_id':'r1','latex':'T=1','status':'recognized'}]}
    formulas.load_region_index=lambda *a:(snapshot,{'regions':[{'region_id':'r1','page':1}]},None)
    monkeypatch.setitem(sys.modules,'optomind_research.runtime.upgrade3.local_materials',snapshots)
    monkeypatch.setitem(sys.modules,'optomind_research.runtime.upgrade3.local_formula_materials',formulas)
    monkeypatch.setattr(w,'verify_review',lambda *a:tmp_path)
    monkeypatch.setattr(w,'provider_instances',lambda *a:pytest.fail('must not initialize providers'))
    request={'review_root':str(tmp_path),'output_root':str(tmp_path/'enhanced'),'enrich_result':str(result),
        'local_formula_config':str(config),'formula_selected_ids':['paper1']}
    outcome=w.execute(request)
    assert outcome['status']=='complete' and outcome['discovery_calls']==outcome['basic_parse_calls']==0
    assert len(calls)==1 and export.read_bytes()==before
    enhanced=json.loads(Path(outcome['exports'][0]).read_text())
    assert enhanced['local_formula_materials']['regions'][0]['latex']=='T=1'
    assert enhanced['material']==snapshot.as_material()
