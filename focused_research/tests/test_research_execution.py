"""Real, offline VeriTMM coverage: no mocked solver or paid API calls."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from focused_research.research_execution import execute_prepared_batch
from focused_research.route_consumer import prepare_tmm_inputs


def prepared(name='baseline', thickness=100):
    instance = {'status': 'prepared', 'route_id': 'r1', 'instance_id': name,
                'incident_medium': 'air', 'substrate': 'glass',
                'layers': [{'material': 'film', 'thickness_nm': thickness}],
                'objectives': [{'objective_id': 'g1', 'metric': 'mean_reflectance',
                                'sense': 'maximize', 'mean_reduction': 'wavelength_integral',
                                'region': {'wavelength_nm': [450, 650], 'angle_deg': 0,
                                           'polarization': 'unpolarized'}}]}
    # Bundled optical-constant fixtures, with real material coverage checks.
    return prepare_tmm_inputs(instance, optical_media={'air': {'constant_n': 1.0},
                              'glass': {'material': 'sio2'}, 'film': {'material': 'tio2'}},
                              wavelengths_nm=[450, 500, 550, 600, 650])


def test_real_execution_exact_cache_and_rescore(tmp_path):
    first = execute_prepared_batch([prepared()], cache_dir=tmp_path, max_new_evals=1)
    row = first['results'][0]
    assert row['status'] == 'completed', row
    assert row['physics_accepted'] is True
    assert row['raw_result']['channels']['angle=0|pol=unpolarized']['R']
    assert first['metering']['new_evaluations'] == 1
    assert first['metering']['primary_forward_calls'] == 1
    assert first['metering']['verification_forward_calls'] > 0
    assert Path(row['full_result_ref']['path']).is_file()
    second = execute_prepared_batch([prepared()], cache_dir=tmp_path, max_new_evals=0)
    assert second['results'][0]['cache_hit']
    assert second['metering']['workbench_forward_calls'] == 0
    rescore = prepared()
    rescore['objectives'][0]['sense'] = 'minimize'
    third = execute_prepared_batch([rescore], cache_dir=tmp_path, max_new_evals=0)
    assert third['results'][0]['cache_hit']
    assert third['results'][0]['simulation_key'] == row['simulation_key']
    assert third['results'][0]['evaluation_key'] != row['evaluation_key']


def test_dedup_budget_and_independent_input_failures(tmp_path):
    duplicate = prepared('different_name')
    duplicate['route_id'] = 'r2'
    result = execute_prepared_batch([{'bad': True}, prepared(), duplicate, prepared('extra', 110)],
                                    cache_dir=tmp_path, max_new_evals=1)
    assert [row['status'] for row in result['results']] == ['invalid_input', 'completed', 'completed', 'budget_exhausted']
    assert result['metering']['new_evaluations'] == 1
    assert result['metering']['cache_hits'] == 1
    assert result['results'][1]['route_id'] != result['results'][2]['route_id']


def test_conditions_material_settings_and_corruption_invalidate(tmp_path):
    first = execute_prepared_batch([prepared()], cache_dir=tmp_path, max_new_evals=1)
    changed = prepared()
    changed['tmm_task']['illumination']['angles_deg'] = [10]
    changed_material = prepared()
    changed_material['tmm_task']['stack']['layers'][0]['material'] = 'sio2'
    blocked = execute_prepared_batch([changed, changed_material], cache_dir=tmp_path, max_new_evals=0)
    assert all(row['status'] == 'budget_exhausted' for row in blocked['results'])
    raw = Path(first['results'][0]['archive_root']) / 'SIMULATION_RESULT.json'
    raw.write_text('{}')
    corrupt = execute_prepared_batch([prepared()], cache_dir=tmp_path, max_new_evals=0)
    assert corrupt['results'][0]['status'] == 'budget_exhausted'


def test_real_process_parallel_and_per_item_material_failure(tmp_path):
    bad = prepared('missing')
    bad['tmm_task']['stack']['layers'][0]['material'] = 'does_not_exist_fixture'
    result = execute_prepared_batch([prepared(), prepared('second', 110), bad],
                                    cache_dir=tmp_path, max_new_evals=3, max_workers=2)
    assert [r['status'] for r in result['results']] == ['completed', 'completed', 'failed']
    assert result['metering']['executor'] == 'process_pool'
    assert result['metering']['reserved_new_evaluations'] == 3
    assert result['metering']['new_evaluations'] == 3
    assert result['results'][2]['metering']['primary_forward_calls'] == 0


def test_invalid_budget_and_unsafe_cache(tmp_path):
    with pytest.raises(ValueError):
        execute_prepared_batch([], cache_dir=tmp_path, max_new_evals=True)
    target = tmp_path / 'real'
    target.mkdir()
    redirect = tmp_path / 'redirect'
    redirect.symlink_to(target, target_is_directory=True)
    with pytest.raises(Exception):
        execute_prepared_batch([], cache_dir=redirect, max_new_evals=0)


def test_verifier_version_and_catalog_fingerprint_invalidation(tmp_path, monkeypatch):
    from focused_research import research_execution as module
    from tmm_engine.execution import ExecutionSettings
    first = execute_prepared_batch([prepared()], cache_dir=tmp_path, max_new_evals=1)
    changed = execute_prepared_batch([prepared()], cache_dir=tmp_path, max_new_evals=0,
                                    execution_settings=ExecutionSettings(write_plot=False, convergence_max_refinements=5))
    assert changed['results'][0]['status'] == 'budget_exhausted'
    original = module._identity
    for field in ('engine_sources', 'material_catalog', 'engine_version'):
        monkeypatch.setattr(module, '_identity', lambda settings, field=field: {**original(settings), field: 'changed'})
        result = execute_prepared_batch([prepared()], cache_dir=tmp_path, max_new_evals=0)
        assert result['results'][0]['status'] == 'budget_exhausted'
    assert first['results'][0]['physics_accepted']


def test_same_instance_name_different_routes_retains_bindings(tmp_path):
    other = prepared()
    other['route_id'] = 'other-route'
    result = execute_prepared_batch([prepared(), other], cache_dir=tmp_path, max_new_evals=1)
    assert [x['instance_id'] for x in result['results']] == ['baseline', 'baseline']
    assert [x['route_id'] for x in result['results']] == ['r1', 'other-route']
    assert result['results'][0]['full_result_ref'] != result['results'][1]['full_result_ref']


def test_external_csv_selector_rejected_before_solver_and_not_cached(tmp_path):
    external = tmp_path / 'sio2.csv'
    external.write_text('wl_um,n,k\n0.4,1.5,0\n0.8,1.5,0\n')
    item = prepared('external')
    item['tmm_task']['stack']['layers'][0].update(material='sio2', provider='local_csv',
                                                dataset_id=str(external))
    for n in (1.5, 2.0):
        external.write_text(f'wl_um,n,k\n0.4,{n},0\n0.8,{n},0\n')
        result = execute_prepared_batch([item], cache_dir=tmp_path / 'cache', max_new_evals=1)
        assert result['results'][0]['status'] == 'failed'
        assert not result['results'][0]['cache_hit']
        assert not result['results'][0]['physics_accepted']
        assert result['metering']['workbench_forward_calls'] == 0
        assert 'excludes direct high-precision' in result['metering']['call_count_scope']
