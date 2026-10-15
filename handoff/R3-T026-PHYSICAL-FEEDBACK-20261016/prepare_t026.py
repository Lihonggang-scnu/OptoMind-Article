"""Read-only input/material provenance preflight for the authorized T026 trial."""
import dataclasses,hashlib,json,sqlite3,subprocess,sys
from decimal import Decimal
from pathlib import Path
ROOT=Path(__file__).resolve().parent
SOURCE=Path(r'D:\OptoMind-Article-3\r3t026a')
SHA='5786e08575b19020a6aece338436406884d3542d'
def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def write(p,o):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    if p.exists():
        assert read(p)==o,'Preserve existing preflight artifact: '+str(p)
        return
    with p.open('x',encoding='utf-8',newline='\n') as f:json.dump(o,f,ensure_ascii=False,indent=2);f.write('\n')
assert subprocess.check_output(['git','-C',str(SOURCE),'rev-parse','HEAD'],text=True).strip()==SHA
assert not subprocess.check_output(['git','-C',str(SOURCE),'status','--porcelain=v1'],text=True).strip()
old=ROOT.parent/'R3-T025'
routes=old/'rejected-drafts/round-2.research_routes.json'
published=subprocess.check_output(['git','-C',str(SOURCE),'cat-file','blob',
    '5ea9c3bab6a61c42821b4aca52470aa966bb9217:handoff/R3-T025-CANDIDATE-GENERATION-20261015/rejected-drafts/round-2.research_routes.json'])
assert routes.read_bytes()==published,'Local T025 input differs from its fixed published Git bytes'
original=read(old/'runs/routes/generation_outcome.json')['rounds'][1]['raw_research_routes']
assert read(routes)==original
write(ROOT/'INPUT_HASH_RECONCILIATION.json',{'local_raw_file_sha256':sha(routes),
    'published_T025_git_blob_sha256':hashlib.sha256(published).hexdigest(),
    'local_bytes_equal_published_T025':True,'object_equal_original_generation_round2':True,
    'cloud_replay_raw_file_sha256':'7a90bf7b132eeca78f54b690ed64e1a9cf2a6504d5783aca33615e68dd99f211',
    'cloud_replay_raw_file_hash_differs':True,
    'resolution':'Use unchanged authoritative T025 bytes verified against fixed result commit and original model object; cloud replay file serialization/source not established. No local data rewritten.'})
sheet=ROOT.parent/'R3-T022/runs/routes/task_sheet.json'
evidence=old/'inputs/evidence_items.json'
constraints=ROOT.parent/'R3-T022/runs/routes/design_constraints.json'
write(ROOT/'INPUT_PATHS.json',{'routes':str(routes),'task_sheet':str(sheet),'evidence':str(evidence),'constraints':str(constraints),
    'sha256':{str(p):sha(p) for p in (routes,sheet,evidence,constraints)},'old_raw_contract_unchanged':True})
grid=list(range(450,1001))
media={'air':{'constant_n':1.0},'fused_silica':{'material':'fused_silica','provider':'rii_sqlite','dataset_id':'835'},
    'TiO2':{'material':'TiO2','provider':'rii_sqlite','dataset_id':'421'},
    'SiO2':{'material':'SiO2','provider':'rii_sqlite','dataset_id':'410'}}
sys.path.insert(0,str(SOURCE/'veritmm'))
import torch
import numpy as np
from tmm_engine.material_registry import MaterialRegistry
from tmm_engine.managed_execution import material_catalog_identity
import tmm_engine
assert Path(tmm_engine.__file__).resolve().parent==SOURCE/'veritmm/tmm_engine'
registry=MaterialRegistry();catalog=registry.catalog_status();material_rows=[]
for alias in ('TiO2','SiO2','fused_silica'):
    row=media[alias]
    candidate=registry.search(row['material'],provider=row['provider'],dataset_id=row['dataset_id'],wavelength_range=[.45,1.0])
    assert len(candidate)==1 and candidate[0].full_coverage
    sampled=registry.sample(row['material'],np.array(grid)*.001,provider=row['provider'],dataset_id=row['dataset_id'],allow_extrapolation=False)
    assert not np.any(sampled.extrapolated_mask)
    assert np.all(np.isfinite(sampled.n)) and np.all(np.isfinite(sampled.k)) and np.all(sampled.n>0) and np.all(sampled.k>=0)
    material_rows.append({'alias':alias,'selection':dataclasses.asdict(candidate[0]),'provenance':sampled.provenance,
        'n_minmax':[float(np.min(sampled.n)),float(np.max(sampled.n))],
        'k_minmax':[float(np.min(sampled.k)),float(np.max(sampled.k))],
        'sampled_coverage_nm':[450,1000],'sample_count':len(grid),'extrapolation':False,
        'warnings':list(sampled.warnings)})
write(ROOT/'inputs/optical_media.json',media);write(ROOT/'inputs/wavelengths_nm.json',grid)
write(ROOT/'MATERIAL_PREFLIGHT.json',{'source_sha':SHA,'catalog':catalog,'catalog_identity':material_catalog_identity(),
    'materials':material_rows,'tmm_engine_file':tmm_engine.__file__,'python':sys.executable,
    'torch_file':torch.__file__,'torch_version':torch.__version__,
    'air_assumption':'Retain prior explicit air incidence n=1.0; not a newly inferred original-user constraint.',
    'substrate_assumption':'Semi-infinite fused silica, RII glass/fused_silica/Malitson835; n-only dataset, native zero-k assumption retained and reported.',
    'film_model_limits':'Existing RII Siefke TiO2 and Gao SiO2 dispersive datasets; film preparation/thickness transfer and manufacturing validity not established.',
    'data_acquired_this_task':False,'solver_calls':0,'grid_nm':{'start':450,'stop':1000,'step':1,'count':551},
    'grid_scope':'Sampled spectral validation only; no continuum/manufacturing guarantee.'})
history={name:{str(p.relative_to(ROOT.parent/name)):sha(p) for p in sorted((ROOT.parent/name).rglob('*')) if p.is_file()} for name in ('R3-T022','R3-T023','R3-T024','R3-T025')}
write(ROOT/'OLD_OUTPUT_HASHES_BEFORE.json',history)
cost=[]
for path in (Path(r'D:\OptoMind-Article-3\local-functional-trial\20261013\budget.sqlite'),ROOT.parent/'R3-T019-DEDICATED20/private/budget.sqlite'):
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        rows=db.execute('SELECT status,amount_cny,actual_cny FROM reservations').fetchall();cap=dict(db.execute('SELECT key,value FROM budget_meta'))['limit_cny']
    cost.append({'path':str(path),'sha256':sha(path),'cap_cny':cap,
        'settled_cny':str(sum((Decimal(str(x[2] or 0)) for x in rows if x[0]=='settled'),Decimal(0))),
        'held_cny':str(sum((Decimal(str(x[1] or 0)) for x in rows if x[0]!='settled'),Decimal(0))),
        'unresolved_count':sum(x[0]!='settled' for x in rows)})
assert all(x['unresolved_count']==0 for x in cost)
write(ROOT/'BUDGET_INITIAL.json',{'ledgers':cost,'global_cap_cny':140,'task_cap_cny':3,'new_cost_cny':0})
write(ROOT/'NUMERICAL_BUDGET_INITIAL.json',{'cap_unique_new_certified_evaluations':64,'max_workers':2,
    'round1_dispatch_cap':14,'new_evaluations_started_before_task':0,
    'counting_rule':'Sum all cache/evaluations/*.reservation.json reserved_simulation_keys lengths, including failures/repeated failed dispatches; subtract before round2.'})
print(json.dumps({'source_sha':SHA,'old_raw_routes':len(original['routes']),'material_dataset_ids':[x['selection']['dataset_id'] for x in material_rows],
    'grid_samples':len(grid),'old_output_counts':{k:len(v) for k,v in history.items()},'new_model_calls':0,'new_solver_calls':0}))
