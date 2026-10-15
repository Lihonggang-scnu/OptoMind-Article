import json
import os
from pathlib import Path
import subprocess
import sys
from copy import deepcopy
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from route_consumer import prepare_tmm_inputs


def instance():
    sheet=json.loads((ROOT/'examples/rgb_nir_task_sheet.json').read_text())
    return {'status':'prepared','route_id':'R1','instance_id':'I1',
        'incident_medium':'air','substrate':'fused_silica',
        'layers':[{'material':'TiO2','thickness_nm':60},{'material':'SiO2','thickness_nm':100}],
        'objectives':[g['executable_metric'] for g in sheet['subgoals']]}


def media():
    return {'air':{'constant_n':1.0},'fused_silica':{'material':'sio2'},
        'TiO2':{'material':'tio2'},'SiO2':{'material':'sio2'}}


def test_materialized_shape_is_accepted_by_existing_tmm_parser(tmp_path):
    row=instance()
    out=prepare_tmm_inputs(row,optical_media=media(),wavelengths_nm=[450,550,650,700,850,1000])
    assert out['objectives']==row['objectives']
    assert all('weight' not in o and 'target' not in o for o in out['objectives'])
    assert out['simulation_performed'] is False
    assert out['tmm_task']['allow_material_extrapolation'] is False
    path=tmp_path/'prepared.json';path.write_text(json.dumps(out))
    repo=ROOT.parent
    env=dict(os.environ)
    env['PYTHONPATH']=str(repo/'veritmm')+os.pathsep+str(repo/'code')+os.pathsep+env.get('PYTHONPATH','')
    code='''import json,sys
from tmm_engine.task_io import simulation_task_from_dict
from optomind_optics.harness.design_task import ObjectivePreference
v=json.load(open(sys.argv[1]))
t=simulation_task_from_dict(v['tmm_task'])
assert len(t.stack.layers)==2
assert t.illumination.angles_deg==(45.0,)
assert t.illumination.polarizations==('unpolarized',)
for o in v['objectives']: ObjectivePreference(**o)
print('consumer-contract-ok; solver-not-run')
'''
    result=subprocess.run([sys.executable,'-c',code,str(path)],env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert 'solver-not-run' in result.stdout


@pytest.mark.parametrize('grid',[[460,550,650,700,850,1000],[450,650,700,1000],[450,550,650,700,850,float('nan')],[450,550,550,1000]])
def test_invalid_grid_not_silently_repaired(grid):
    with pytest.raises(ValueError):
        prepare_tmm_inputs(instance(),optical_media=media(),wavelengths_nm=grid)


def test_no_assumed_optical_constants_or_missing_materials():
    m=media();del m['TiO2']
    with pytest.raises(ValueError,match='explicit optical mapping'):
        prepare_tmm_inputs(instance(),optical_media=m,wavelengths_nm=[450,550,650,700,850,1000])
    m=media();m['TiO2']={'material':'tio2','constant_n':2.4}
    with pytest.raises(ValueError,match='exactly one'):
        prepare_tmm_inputs(instance(),optical_media=m,wavelengths_nm=[450,550,650,700,850,1000])


@pytest.mark.parametrize('value',['1.5',True,1.5])
def test_mixed_material_and_constant_is_rejected_before_consumer(value):
    m=media();m['SiO2']['constant_n']=value
    with pytest.raises(ValueError,match='exactly one'):
        prepare_tmm_inputs(instance(),optical_media=m,wavelengths_nm=[450,550,650,700,850,1000])


@pytest.mark.parametrize('value',[12,'   ',False,'sio2'])
def test_material_presence_is_not_bypassed_by_invalid_type(value):
    m=media();m['SiO2']={'material':value,'constant_n':1.5}
    with pytest.raises(ValueError,match='exactly one'):
        prepare_tmm_inputs(instance(),optical_media=m,wavelengths_nm=[450,550,650,700,850,1000])


@pytest.mark.parametrize('mode', ['external_evidence', 'model_knowledge', 'hybrid'])
def test_consumer_preserves_route_basis(mode):
    row = instance()
    row.update(basis_mode=mode, model_knowledge_rationale=None if mode == 'external_evidence' else 'Remembered interference hypothesis; not externally verified.',
        evidence_ids=[] if mode == 'model_knowledge' else ['E1'],
        clue_ids=[] if mode == 'model_knowledge' else ['D1'], targeted_goal_ids=['G1'])
    output = prepare_tmm_inputs(row, optical_media=media(), wavelengths_nm=[450,550,650,700,850,1000])
    for key in ['basis_mode', 'model_knowledge_rationale', 'evidence_ids', 'clue_ids', 'targeted_goal_ids']:
        assert output[key] == row[key]
    assert output['simulation_performed'] is False
