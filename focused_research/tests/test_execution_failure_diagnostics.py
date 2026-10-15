"""Preserve the original verifier failure when evidence could not be produced."""
from pathlib import Path
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from focused_research.research_execution import _engine, execute_prepared_batch
from focused_research.route_consumer import prepare_tmm_inputs


def test_missing_verifier_keeps_original_failure(monkeypatch, tmp_path):
    _engine()
    from tmm_engine import acceptance
    def missing(*args, **kwargs):
        raise ImportError("The 'tmm' package is required for this independent solver")
    monkeypatch.setattr(acceptance, 'collect_verification_evidence', missing)
    instance = {'status':'prepared', 'route_id':'R', 'instance_id':'parent',
        'incident_medium':'air', 'substrate':'glass',
        'layers':[{'material':'film','thickness_nm':70}],
        'objectives':[{'objective_id':'G1','metric':'mean_reflectance','sense':'maximize',
            'mean_reduction':'wavelength_integral_mean',
            'region':{'wavelength_nm':[450,650],'angle_deg':45,'polarization':'unpolarized'}}]}
    prepared = prepare_tmm_inputs(instance, optical_media={'air':{'constant_n':1},
        'glass':{'constant_n':1.46}, 'film':{'constant_n':2.3}}, wavelengths_nm=[450,500,550,600,650])
    batch = execute_prepared_batch([prepared], cache_dir=tmp_path, max_new_evals=1)
    result = batch['results'][0]
    assert not result['physics_accepted']
    assert result['physical_input'] == prepared['tmm_task']
    assert result['certificate']['accepted'] is False
    text = json.dumps(result['envelope']['failures'])
    assert 'tmm' in text
    assert 'replace()' not in text
    assert batch['metering']['new_evaluations'] == 1
