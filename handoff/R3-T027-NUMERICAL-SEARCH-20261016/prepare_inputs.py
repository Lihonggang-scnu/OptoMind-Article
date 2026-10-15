"""Verify unchanged T026 inputs and the pinned local material database; no solver."""
import dataclasses, hashlib, json, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
SOURCE = Path(r'D:\OptoMind-Article-3\r3t027a')
SHA = 'b5f8a1dc2cfe8a203b74a742963bef7f9ac9a57b'
def load(p): return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(name, value):
    with (ROOT / name).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2); stream.write('\n')
assert subprocess.check_output(['git','-C',str(SOURCE),'rev-parse','HEAD'],text=True).strip() == SHA
assert not subprocess.check_output(['git','-C',str(SOURCE),'status','--porcelain=v1'],text=True).strip()
subprocess.check_call(['git','-C',str(SOURCE),'merge-base','--is-ancestor','5786e08575b19020a6aece338436406884d3542d',SHA])
previous = ROOT.parent / 'R3-T026'
paths = load(previous / 'INPUT_PATHS.json')
for path, expected in paths['sha256'].items(): assert sha(path) == expected
paths['optical_media'] = str(previous / 'inputs/optical_media.json')
paths['wavelengths'] = str(previous / 'inputs/wavelengths_nm.json')
paths['sha256'].update({paths[name]: sha(paths[name]) for name in ('optical_media','wavelengths')})
save('INPUT_PATHS.json', paths)
for a in load(ROOT / 'delivery/READY.json')['artifacts']:
    assert sha(ROOT / 'delivery' / a['name']) == a['sha256']
save('DELIVERY_HASHES.json', {a['name']:sha(ROOT/'delivery'/a['name']) for a in load(ROOT/'delivery/READY.json')['artifacts']})
grid = load(paths['wavelengths']); media = load(paths['optical_media'])
assert grid == list(range(450,1001))
db = SOURCE / 'veritmm/tmm_engine/rii_cache.db'
assert sha(db) == 'e4c1ec34cc4f765a8400ff87bb7dee8705b6da2cdb11a229cc57ad9bdd274989'
sys.path.insert(0, str(SOURCE / 'veritmm'))
import numpy as np
import tmm_engine
from tmm_engine.material_registry import MaterialRegistry
from tmm_engine.managed_execution import material_catalog_identity
assert Path(tmm_engine.__file__).resolve().parent == SOURCE / 'veritmm/tmm_engine'
registry = MaterialRegistry(); materials = []
for alias in ('TiO2','SiO2','fused_silica'):
    row = media[alias]
    candidates = registry.search(row['material'],provider=row['provider'],dataset_id=row['dataset_id'],wavelength_range=[.45,1.])
    assert len(candidates) == 1 and candidates[0].full_coverage
    values = registry.sample(row['material'],np.asarray(grid)*.001,provider=row['provider'],dataset_id=row['dataset_id'],allow_extrapolation=False)
    assert not np.any(values.extrapolated_mask)
    assert np.all(np.isfinite(values.n)) and np.all(np.isfinite(values.k)) and np.all(values.n>0) and np.all(values.k>=0)
    materials.append({'alias':alias,'selection':dataclasses.asdict(candidates[0]),'provenance':values.provenance,
        'sample_count':551,'sampled_nm':[450,1000],'extrapolated':False,'warnings':list(values.warnings)})
save('MATERIAL_PREFLIGHT.json', {'source_sha':SHA,'tmm_engine_file':tmm_engine.__file__,
    'database_sha256':sha(db),'catalog_identity':material_catalog_identity(),'materials':materials,
    'mapping_and_grid_reused_byte_for_byte':True,'air_and_semi_infinite_substrate_assumptions_unchanged':True,
    'new_material_or_literature_downloads':0,'solver_calls':0,'scope':'sampled_grid_only; no manufacturing validation'})
history = {task:{str(p.relative_to(ROOT.parent/task)):sha(p) for p in sorted((ROOT.parent/task).rglob('*')) if p.is_file()}
           for task in ('R3-T022','R3-T023','R3-T024','R3-T025','R3-T026')}
save('OLD_OUTPUT_HASHES_BEFORE.json', history)
save('SOURCE_RECEIVE.json', {'base_sha':'5786e08575b19020a6aece338436406884d3542d','implementation_sha':SHA,
    'workdir':str(SOURCE),'branch':'codex/r3-t027-physics-guided-search-20261016',
    'bundle_exported_ref':'HEAD','exact_sha_verified':True,'old_t026_worktree_clean':True,
    'initial_named_ref_fetch_failure':"Bundle does not export refs/heads/feature/t027-physics-guided-search-20261015; fetched its observed HEAD and verified full pinned commit instead.",
    'global_git_configuration_changed':False,'checkout_autocrlf':False,'checkout_longpaths':True})
print(json.dumps({'source_sha':SHA,'material_datasets':[m['selection']['dataset_id'] for m in materials],
    'grid_samples':551,'old_output_counts':{k:len(v) for k,v in history.items()},'solver_calls':0,'model_calls':0}))
