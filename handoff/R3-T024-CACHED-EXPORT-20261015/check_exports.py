"""Compare the five new cached exports with the fixed native snapshot loader."""
import hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
OLD=ROOT.parent/'R3-T022'
sys.path.insert(0,r'D:\OptoMind-Article-3\r3t022r3')
from optomind_research.runtime.upgrade3.local_materials import PreparedSnapshotProvider
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def digest(obj):return hashlib.sha256(json.dumps(obj,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()
full=read(OLD/'runs/fulltext/FULLTEXT_RESULT.json')
result=read(ROOT/'reexport/FULLTEXT_RESULT.json')
assert result['phase']=='offline_reexport'
assert result['selected_count']==5
for k in ('paid_model_calls','discovery_calls','acquisition_calls','basic_parse_calls','formula_calls'):
    assert result[k]==0,k
rows=[]
for old,new in zip(full['records'],result['records']):
    assert old['record']==new['record']
    row={'paper':old['record']['canonical_paper_id'],'status':new['status']}
    rows.append(row)
    if new['status']!='verified_material':
        row['error']=new.get('error') or new.get('eligibility_reason')
        continue
    export=read(Path(new['export_path']))
    assert export['source_record']==old['record']
    acquisition=old.get('docling_acquisition') or old['acquisition']
    native=PreparedSnapshotProvider(acquisition['snapshot_path']).load()
    material=native.as_material()
    assert export['material']==material
    assert export['material']['snapshot_id']==acquisition['snapshot_id']
    policy=material['reading_policy']
    row.update(parser=acquisition['parser'],snapshot_id=native.snapshot_id)
    row.update(source_record_exact=True,no_added_batch_fields=True,
        native_loader='passed',material_full_equal=True,
        native_material_sha256=digest(material),export_material_sha256=digest(export['material']),
        identity_status=policy['identity_status'],reading_policy_sha256=digest(policy),
        original_identity_and_usage_limits_retained=True,full_material_blocks=len(material['blocks']))
    if old['record']['canonical_paper_id']=='core:4662953':
        assert policy['identity_status']=='input_attributed'
        assert material['manifest']['identity_check']['status']=='input_attributed'
        (ROOT/'CORE_RESTRICTIONS.json').write_text(json.dumps({
            'snapshot_id':native.snapshot_id,'identity_check':material['manifest']['identity_check'],
            'reading_policy':policy,'independent_identity_verified':False},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
check={'status':'passed' if result['status']=='complete' and len(result['exports'])==5 and all(r['status']=='verified_material' for r in rows) else 'blocked',
    'source_commit':'3eb51424985402a3e9e79bdb872a002d9f5b6e45',
    'review_commit':'d2deb4dbe18b23837d949822ad731afbaba9414b',
    'native_loader_file':sys.modules[PreparedSnapshotProvider.__module__].__file__,
    'review_python':sys.executable,'records':rows,'exported_count':len(result['exports']),
    'model_calls':0,'retrieval_calls':0,'parsing_calls':0,'simulation_calls':0,
    'evidence_projection_rerun':False,'route_diagnostics_rerun':False}
(ROOT/'EXPORT_CHECK.json').write_text(json.dumps(check,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'status':check['status'],'exports':len(result['exports']),'records':rows},ensure_ascii=False))
raise SystemExit(0 if check['status']=='passed' else 2)
