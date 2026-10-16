"""T039: explicit local n/k sampling and extraction of immutable cached spectra only."""
import dataclasses
import hashlib
import json
import pathlib
import socket
import sys
import numpy as np

ROOT = pathlib.Path(r'D:\OptoMind-Article-3')
TASK = ROOT / 'local-r3-handoff/tasks/R3-T039'
SOURCE = ROOT / 'r3t039a'
OLD = ROOT / 'r3t038a/veritmm/tmm_engine'
CASE = TASK.parent / 'R3-T038/cases/e2e-04-solarblind-uv-triple'
OUT = TASK / 'saved-uv-audit-attempt02'
IMPL = '19387a35a9c2a43ee2aaea286becc7c18ebbb040'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))

def clean(obj):
    if dataclasses.is_dataclass(obj): return clean(dataclasses.asdict(obj))
    if isinstance(obj, np.ndarray): return obj.tolist()
    if isinstance(obj, np.generic): return obj.item()
    if isinstance(obj, pathlib.Path): return str(obj)
    if isinstance(obj, dict): return {str(k): clean(v) for k,v in obj.items()}
    if isinstance(obj, (list,tuple)): return [clean(v) for v in obj]
    return obj

def write(name, data):
    with (OUT/name).open('x', encoding='utf-8', newline='\n') as f:
        json.dump(clean(data), f, indent=2, ensure_ascii=False, allow_nan=False)
        f.write('\n')

def blocked_network(*a, **k):
    raise RuntimeError('T039 is offline: network disabled')

assert load(TASK/'preflight/T039_LOCAL_ACCEPTANCE.json')['global_pass'] is True
OUT.mkdir(exist_ok=False)
socket.create_connection = blocked_network
socket.socket.connect = blocked_network
sys.path.insert(0, str(SOURCE/'veritmm'))
from tmm_engine import material_registry as mr
assert pathlib.Path(mr.__file__).resolve().is_relative_to(SOURCE.resolve())
records = load(TASK/'INPUT_PROVENANCE.json')['source_material_records_for_UV_sampling']
source_paths = {pathlib.Path(x['source_path']) for x in records.values()}
before = {str(p):sha(p) for p in source_paths}
registry = mr.MaterialRegistry(materials_dir=OLD/'materials', rii_db_path=OLD/'rii_cache.db')
grid = np.arange(255.,281.)
samples = {}
prior = load(TASK/'saved-uv-audit/FIVE_MATERIAL_UV.json')
assert prior['sources_unchanged'] is True
for label, record in records.items():
    source = pathlib.Path(record['source_path'])
    assert sha(source) == record['source_sha256'], label
    samples[label] = prior['samples'][label]
    assert samples[label]['source_binding']==record
    samples[label]['interpolation_method'] = 'numpy.interp piecewise linear, as implemented in material_registry._interpolate_with_endpoint_audit'
    samples[label]['reused_completed_sampling_attempt01'] = True
after = {str(p):sha(p) for p in source_paths}
assert before == after, 'material source changed'
write('FIVE_MATERIAL_UV.json', {'task_id':'R3-T039','implementation':IMPL,
      'module':mr.__file__,'module_sha256':sha(pathlib.Path(mr.__file__)),
      'scope':'read-only local optical constants; not a thin-film solve',
      'units':{'wavelengths_nm':'nm','wavelengths_um':'um','n':'dimensionless','k':'dimensionless'},
      'arithmetic_mean_is_not_goal_metric':True,'samples':samples,'sources_unchanged':True})

archive_path = CASE/'search/design_archive.json'
archive = load(archive_path)
sys.path.insert(0,str(SOURCE/'focused_research'))
import design_archive as da
assert pathlib.Path(da.__file__).resolve().is_relative_to(SOURCE.resolve())
probe_path = CASE/'search/rediscovery_cache_probe.json'
probe = load(probe_path)
p003_id = 'design:21f966c45f861391bf67a17593e8971a17a8f2c4a868774a7f75731254d25c36'
p003_file = CASE/'cache/evaluations/a582b7a25cb3482fb10120601578e96d.json'
assert sha(p003_file) == '925625d09b9acc0bf52976f2b3ed69cde932daa226a72e62a72214ac528a2264'
selection = [('P003',p003_id,load(p003_file),p003_file)]
for i, embedded in enumerate(probe['results'],1):
    ref = embedded['full_result_ref']
    file = pathlib.Path(ref['path'])
    assert sha(file) == ref['sha256']
    cached = load(file)
    assert cached['raw_result'] == embedded['raw_result']
    assert cached['physical_input'] == embedded['physical_input']
    matches = [e for e in archive['evaluations'] if e['evaluation_key']==cached['evaluation_key']]
    assert matches and len({m['design_id'] for m in matches}) == 1
    selection.append((f'cached_seed_{i}',matches[0]['design_id'],cached,file))

extractions = []
for alias,did,cached,path in selection:
    matches = [e for e in archive['evaluations'] if e['design_id']==did and
               e['evaluation_key']==cached['evaluation_key']]
    assert matches
    assert any(e['full_result_ref']['sha256']==sha(path) for e in matches)
    design = archive['designs'][did]
    canonical = da._physical(cached['physical_input'])
    assert design['physical_input'] == canonical
    assert did == 'design:'+da._hash({'context_id':archive['context_id'],'physical_input':canonical})
    raw = cached['raw_result']
    wavelengths = np.asarray(raw['wavelengths_nm'], dtype=float)
    indices = np.flatnonzero((wavelengths>=255)&(wavelengths<=280))
    assert np.array_equal(wavelengths[indices],grid)
    channels = {}
    for channel, quantities in raw['channels'].items():
        uv = {name:np.asarray(quantities[name],dtype=float)[indices] for name in ['R','T','A']}
        assert all(np.isfinite(a).all() for a in uv.values())
        means = {name:{'arithmetic_sample_mean':float(a.mean()),
                       'wavelength_integral_mean_trapezoid':float(np.trapezoid(a,grid)/(grid[-1]-grid[0]))}
                 for name,a in uv.items()}
        channels[channel] = {'UV_arrays_fraction':uv,'UV_means_fraction':means,
                             'max_abs_R_plus_T_plus_A_minus_1':float(np.max(np.abs(uv['R']+uv['T']+uv['A']-1)))}
    extraction = {'alias':alias,'design_id':did,'instance_id':cached['instance_id'],
      'route_id':cached['route_id'],'evaluation_key':cached['evaluation_key'],
      'simulation_key':cached['simulation_key'],'source_cache_path':str(path),'source_cache_sha256':sha(path),
      'identity_binding':'native design_archive._physical canonical input, native stable ID hash, evaluation key and cache SHA matched',
      'identity_normalization':'native canonicalizer removes presentation stack.name; raw cache unchanged',
      'archive_sha256':sha(archive_path),'cache_probe_sha256':sha(probe_path) if alias!='P003' else None,
      'physical_stack':cached['physical_input']['stack'],'illumination':cached['physical_input']['illumination'],
      'saved_objectives':cached['objectives'],'wavelengths_nm':grid,'full_saved_grid_count':len(wavelengths),
      'channels':channels,'saved_absorptance_definition':raw['audit'].get('absorptance_definition'),
      'saved_energy_accounting':cached['certificate'].get('energy_accounting'),
      'saved_material_provenance':raw.get('material_provenance'),
      'per_layer_absorption':{'status':'missing','reason':'saved raw_result.extras is empty; no new solve permitted'},
      'interpretation_limits':['cached observations only','no material/layer absorption attribution',
                               'not a generalization to all 51 structures','arithmetic sample mean is descriptive']}
    assert raw.get('extras') == {}, 'inspect existing extras before asserting missing'
    if alias=='P003':
        t=channels['angle=0|pol=unpolarized']['UV_means_fraction']['T']['wavelength_integral_mean_trapezoid']
        target=4.584452109734316e-05
        assert abs(t-target)<=1e-18, (t,target)
        extraction['P003_G1_integral_reproduction']={'actual':t,'saved':target,'abs_difference':abs(t-target)}
    extractions.append(extraction)
write('CACHED_UV_RTA.json', {'task_id':'R3-T039','scope':'existing immutable cached spectra only',
      'units':'R/T/A fractions; percent = fraction * 100; wavelength nm',
      'averaging':'26 samples arithmetic; trapezoid integral over 255..280 nm divided by 25 nm',
      'samples':extractions,'new_model_http':0,'new_design_simulations':0,
      'new_gradients':0,'new_certifications':0,'cost_CNY':'0'})
print(json.dumps({'materials':{k:v['statistics_dimensionless'] for k,v in samples.items()},
      'cached_UV_integral':{x['alias']:{'design_id':x['design_id'],
      'RTA':x['channels']['angle=0|pol=unpolarized']['UV_means_fraction']} for x in extractions}},ensure_ascii=False))
