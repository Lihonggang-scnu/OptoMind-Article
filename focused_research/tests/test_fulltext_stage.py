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


def core_snapshot_and_result():
    snapshot = SimpleNamespace(snapshot_id='S1', manifest={'identity_check': {'status': 'input_attributed', 'conflicts': []}},
        reading_policy=lambda: {'material_scope': 'fulltext', 'eligible': True, 'identity_status': 'input_attributed'})
    result = {'parser': 'core_text_jats', 'snapshot_id': 'S1', 'selected_route': 'core_fulltext',
        'identity': {'status': 'conflict', 'conflicts': ['openalex:title', 'crossref:year'],
                     'provider_audit': [{'provider': 'core', 'status': 'matched'}]},
        'attempts': [{'route': 'core_fulltext', 'source': 'core', 'status': 'usable', 'content_kind': 'core_text_jats'}]}
    return snapshot, result


def test_core_rejected_candidate_history_is_not_selected_source_conflict():
    snapshot, result = core_snapshot_and_result()
    assert w.eligible(result, snapshot)
    assert snapshot.manifest['identity_check']['status'] == 'input_attributed'
    assert result['identity']['status'] == 'conflict'  # provenance is never rewritten
    result['identity'] = {'status': 'provisional'}
    assert w.eligible(result, snapshot)


@pytest.mark.parametrize('mutation', ['selected_conflict', 'core_conflict', 'unbound', 'unmatched', 'no_attempt', 'policy_false'])
def test_core_conflicts_and_ineligibility_fail_closed(mutation):
    snapshot, result = core_snapshot_and_result()
    if mutation == 'selected_conflict': snapshot.manifest['identity_check']['conflicts'] = [{'field': 'doi'}]
    elif mutation == 'core_conflict': result['identity']['conflicts'] = ['core:title']
    elif mutation == 'unbound': result['snapshot_id'] = 'another'
    elif mutation == 'unmatched': result['identity']['provider_audit'] = []
    elif mutation == 'no_attempt': result['attempts'] = []
    elif mutation == 'policy_false': snapshot.reading_policy = lambda: {'material_scope': 'fulltext', 'eligible': False}
    assert not w.eligible(result, snapshot)
    assert w.eligibility_reason(result, snapshot)


def test_offline_reexport_reverifies_final_docling_and_core_without_acquisition(tmp_path, monkeypatch):
    import sys, hashlib
    from types import ModuleType
    source = tmp_path / 'old' / 'FULLTEXT_RESULT.json'; source.parent.mkdir()
    snapshot, core = core_snapshot_and_result()
    snapshot.root = tmp_path / 'core_snapshot'
    snapshot.manifest['canonical_paper_id'] = 'core:1'
    snapshot.as_material = lambda: {'snapshot_id': 'S1', 'manifest': snapshot.manifest, 'blocks': []}
    core['snapshot_path'] = str(snapshot.root)
    doc = SimpleNamespace(snapshot_id='D1', root=tmp_path/'doc_snapshot', manifest={'canonical_paper_id': 'paper:doc', 'identity_check': {'status': 'provisional'}},
        reading_policy=lambda: {'material_scope': 'fulltext', 'eligible': True},
        as_material=lambda: {'snapshot_id': 'D1', 'manifest': {}, 'blocks': []})
    export_path = tmp_path/'old'/'doc_export.json'
    record = {'canonical_paper_id': 'paper:doc'}
    export_path.write_text(json.dumps({'review_commit': w.PIN, 'source_record': record,
        'snapshot_path': str(doc.root), 'material': {'snapshot_id': 'D1'}, 'verified': False}))
    payload = {'schema_version': 'focused_fulltext_result_v1', 'review_commit': w.PIN, 'records': [
        {'record': {'canonical_paper_id': 'core:1'}, 'acquisition': core},
        {'record': record, 'export_path': str(export_path), 'acquisition': {'parser': 'fitz', 'snapshot_path': 'DO_NOT_READ'},
         'docling_acquisition': {'parser': 'docling', 'snapshot_id': 'D1', 'snapshot_path': str(doc.root)}}]}
    source.write_text(json.dumps(payload)); original = source.read_bytes()
    loaded = []
    def provider(path):
        loaded.append(str(path))
        return SimpleNamespace(load=lambda: {str(snapshot.root): snapshot, str(doc.root): doc}[str(path)])
    module = ModuleType('local_materials'); module.PreparedSnapshotProvider = provider
    monkeypatch.setitem(sys.modules, 'optomind_research.runtime.upgrade3.local_materials', module)
    monkeypatch.setattr(w, 'verify_review', lambda *a: tmp_path)
    monkeypatch.setattr(w, 'provider_instances', lambda *a: pytest.fail('offline route invoked providers'))
    request = {'review_root': str(tmp_path), 'reexport_result': str(source), 'output_root': str(tmp_path/'new'),
        'reexport_sha256': hashlib.sha256(original).hexdigest()}
    result = w.execute(request)
    assert result['status'] == 'complete' and len(result['exports']) == 2
    assert result['acquisition_calls'] == result['basic_parse_calls'] == result['paid_model_calls'] == 0
    assert loaded == [str(snapshot.root), str(doc.root)]
    assert source.read_bytes() == original
    assert json.loads(Path(result['exports'][0]).read_text())['material']['manifest']['identity_check']['status'] == 'input_attributed'
    with pytest.raises(ValueError, match='mixed'):
        w.execute({**request, 'enrich_result': 'old'})
    with pytest.raises(ValueError, match='source_changed'):
        w.execute({**request, 'reexport_sha256': 'bad'})
    payload['records'][1]['docling_acquisition']['snapshot_id'] = 'wrong'
    source.write_text(json.dumps(payload)); request['reexport_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    request['output_root'] = str(tmp_path/'another_new')
    result = w.execute(request)
    assert result['status'] == 'partial' and len(result['exports']) == 1
    assert result['records'][1]['status'] == 'blocked_snapshot_reverification'


def test_reexport_wrapper_rejects_mixed_modes_and_same_output(tmp_path):
    for kwargs in ({'enrich_result': 'x'}, {'docling_config': 'x'}, {'local_formula_config': 'x'}):
        with pytest.raises(ValueError, match='cannot mix'):
            f.run_fulltext_stage([], [], tmp_path/'new', review_root=tmp_path, review_python='python', reexport_result='old', **kwargs)
    with pytest.raises(ValueError, match='new output'):
        f.run_fulltext_stage([], [], tmp_path, review_root=tmp_path, review_python='python', reexport_result=tmp_path/'FULLTEXT_RESULT.json')


@pytest.mark.parametrize('destination', ['source_child', 'source_ancestor', 'snapshot_child', 'export_snapshot_child', 'occupied'])
def test_reexport_preflight_never_writes_inside_old_materials(tmp_path, destination):
    source = tmp_path/'old'/'FULLTEXT_RESULT.json'; source.parent.mkdir()
    snap = tmp_path/'external_snapshot'; snap.mkdir()
    exported_snap = tmp_path/'exported_snapshot'; exported_snap.mkdir()
    old_export = source.parent/'export.json'
    old_export.write_text(json.dumps({'snapshot_path': str(exported_snap)}))
    source.write_text(json.dumps({'records': [{'acquisition': {'snapshot_path': str(snap)}, 'export_path': str(old_export)}]}))
    output = {'source_child': source.parent/'new', 'source_ancestor': tmp_path,
              'snapshot_child': snap/'new', 'export_snapshot_child': exported_snap/'new',
              'occupied': tmp_path/'occupied'}[destination]
    if destination == 'occupied':
        output.mkdir(); (output/'FULLTEXT_RESULT.json').write_text('old result')
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    with pytest.raises(ValueError, match='output'):
        f.run_fulltext_stage([], [], output, review_root=tmp_path, review_python='python', reexport_result=source)
    assert not (output/'FULLTEXT_REQUEST.json').exists()
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    if destination not in {'source_ancestor', 'occupied'}:
        assert not output.exists()


def test_reexport_native_paper_identity_cannot_be_relabelled():
    snapshot = SimpleNamespace(manifest={'canonical_paper_id': 'paper:REAL', 'identity_check': {}})
    assert not w._reexport_identity_bound(snapshot, {'canonical_paper_id': 'paper:WRONG'}, {})
    assert not w._reexport_identity_bound(snapshot, {'canonical_paper_id': 'paper:REAL'}, {'identity': {'canonical_paper_id': 'paper:WRONG'}})
    assert w._reexport_identity_bound(snapshot, {'canonical_paper_id': 'paper:REAL'}, {})
    snapshot.manifest['canonical_paper_id'] = 'https://doi.org/10.123/example'
    assert w._reexport_identity_bound(snapshot, {'canonical_paper_id': 'doi:10.123/EXAMPLE'}, {})
    snapshot.manifest['identity_check'] = {'provided': {'doi': '10.123/example'}}
    assert not w._reexport_identity_bound(snapshot, {'canonical_paper_id': '10.123/example', 'doi': '10.123/wrong'}, {})


def test_direct_reexport_rejects_existing_output_before_overwrite(tmp_path, monkeypatch):
    import sys, hashlib
    from types import ModuleType
    module = ModuleType('local_materials')
    module.PreparedSnapshotProvider = lambda *a: pytest.fail('must reject before load')
    monkeypatch.setitem(sys.modules, 'optomind_research.runtime.upgrade3.local_materials', module)
    source = tmp_path/'old'/'FULLTEXT_RESULT.json'; source.parent.mkdir()
    source.write_text(json.dumps({'schema_version': 'focused_fulltext_result_v1', 'review_commit': w.PIN,
                                 'records': [{'record': {}, 'acquisition': {}}]}))
    output = tmp_path/'new'; output.mkdir()
    (output/'exports').mkdir(); preserved = output/'exports'/'old.json'; preserved.write_text('original')
    request = {'reexport_result': str(source), 'output_root': str(output),
               'reexport_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
    with pytest.raises(ValueError, match='existing_artifacts'):
        w.reexport_existing(request)
    assert preserved.read_text() == 'original'
