"""Read existing native snapshots to isolate a failed metadata binding; no parse."""
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
OLD=ROOT.parent/'R3-T022'
sys.path.insert(0,r'D:\OptoMind-Article-3\r3t022r3')
from optomind_research.runtime.upgrade3.local_materials import PreparedSnapshotProvider
full=json.loads((OLD/'runs/fulltext/FULLTEXT_RESULT.json').read_text(encoding='utf-8'))
rows=[]
for row in full['records']:
    acquisition=row.get('docling_acquisition') or row['acquisition']
    snapshot=PreparedSnapshotProvider(acquisition['snapshot_path']).load()
    assert snapshot.snapshot_id==acquisition['snapshot_id']
    material=snapshot.as_material()
    policy=material['reading_policy']
    rows.append({'paper':row['record']['canonical_paper_id'],'snapshot_id':snapshot.snapshot_id,
        'native_loader':'passed','parser':acquisition['parser'],'identity_status':policy['identity_status'],
        'eligible':policy['eligible'],'material_scope':policy['material_scope'],'body_blocks':len(material['blocks'])})
    if row['record']['canonical_paper_id']=='core:4662953':
        export=json.loads((ROOT/'reexport-final/exports'/f'{snapshot.snapshot_id}.json').read_text(encoding='utf-8'))
        assert export['material']==material
        assert policy['identity_status']=='input_attributed'
        assert export['material']['manifest']['identity_check']==snapshot.manifest['identity_check']
        (ROOT/'CORE_NATIVE_REPLAY.json').write_text(json.dumps({'status':'passed',
            'snapshot_id':snapshot.snapshot_id,'native_loader_module':sys.modules[PreparedSnapshotProvider.__module__].__file__,
            'identity_check':snapshot.manifest['identity_check'],'reading_policy':policy,
            'export_identical_to_native_material':True,'independent_identity_verified':False},
            ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
(ROOT/'NATIVE_SNAPSHOT_CHECK.json').write_text(json.dumps({'records':rows,
    'parsing_calls':0,'simulation_calls':0},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'existing_native_snapshots_loaded':len(rows),'core_identity':'input_attributed','parsing_calls':0}))
