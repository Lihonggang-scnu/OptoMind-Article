"""Isolated offline checks of the existing objective consumer, not a new solver.

Run only via the subprocess test, avoiding the retrieval vendor's generic modules.
"""
import json
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

from optomind_optics.harness.design_task import ObjectivePreference
from optomind_optics.harness.objectives import evaluate_declared_objectives
from tmm_engine.workbench import ForwardSimulationResult


@pytest.mark.parametrize('metric,observable', [
    ('mean_reflectance','R'),('mean_transmittance','T'),('mean_absorption','A')])
def test_exact_metric_object_reaches_existing_consumer(metric,observable):
    sheet=json.loads((Path(__file__).resolve().parents[1]/'examples/rgb_nir_task_sheet.json').read_text())
    spec=deepcopy(sheet['subgoals'][0]['executable_metric'])
    spec['metric']=metric
    lo,hi=spec['region']['wavelength_nm']
    # Uneven sample spacing distinguishes arithmetic sample mean (.3) from
    # wavelength-integral mean (.36). This is a scalar-consumer fixture only.
    wavelengths=np.asarray([lo,lo+0.2*(hi-lo),hi],dtype=float)
    signal=np.asarray([0.,0.,.9])
    payload={key:np.zeros(3) for key in ['R','T','A']}
    payload[observable]=signal
    result=ForwardSimulationResult(wavelengths_nm=wavelengths,
        channels={'angle=45|pol=unpolarized':payload,
                  'angle=0|pol=unpolarized':{key:np.ones(3) for key in payload},
                  'angle=45|pol=s':{key:np.ones(3) for key in payload}},
        material_provenance=[],solver='smatrix')
    observations={}
    for reduction,expected in [('sample_mean',.3),('wavelength_integral_mean',.36)]:
        spec['mean_reduction']=reduction
        preference=ObjectivePreference(**spec)
        report=evaluate_declared_objectives([preference],result)
        row=report.target_attainment[spec['objective_id']]
        assert row['observed']==pytest.approx(expected)
        assert row['mean_reduction']==reduction
        assert list(row['channel_observations'])==['angle=45|pol=unpolarized']
        observations[reduction]=row['observed']
    assert observations['sample_mean']!=observations['wavelength_integral_mean']
    # Never silently fall back to an available but different channel.
    spec['region']['polarization']='p'
    with pytest.raises(ValueError,match='matched no simulation channels'):
        evaluate_declared_objectives([ObjectivePreference(**spec)],result)


def test_missing_band_endpoints_are_not_implicitly_filled_by_consumer():
    spec={'objective_id':'G1','metric':'mean_reflectance','sense':'maximize',
          'mean_reduction':'wavelength_integral_mean',
          'region':{'wavelength_nm':[450,650],'angle_deg':45,'polarization':'unpolarized'}}
    wavelengths=np.asarray([460.,500.,640.])
    result=ForwardSimulationResult(wavelengths_nm=wavelengths,
        channels={'angle=45|pol=unpolarized':{'R':np.asarray([0.,0.,.9])}},
        material_provenance=[],solver='smatrix')
    observed=evaluate_declared_objectives([ObjectivePreference(**spec)],result).target_attainment['G1']['observed']
    # Current consumer averages the available 460–640 span: 63 / 180 = .35.
    # It does NOT validate/fill declared 450–650 endpoints. A future forward
    # caller must refuse whole-band acceptance until that prerequisite holds.
    assert observed==pytest.approx(.35)
    assert not set(spec['region']['wavelength_nm']).issubset(set(wavelengths))
    assert observed!=pytest.approx(63/200)
