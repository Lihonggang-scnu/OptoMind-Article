"""Archive contract fixtures are synthetic; the final test uses real VeriTMM."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from focused_research.design_archive import create_design_archive, add_certified_design


def fixture(thickness=50, r=.4, t=.5, name='one'):
    goals = [{'objective_id': 'G1', 'metric': 'mean_reflectance', 'sense': 'maximize',
              'mean_reduction': 'wavelength_integral_mean',
              'region': {'wavelength_nm': [450, 650], 'angle_deg': 45, 'polarization': 'unpolarized'}},
             {'objective_id': 'G2', 'metric': 'mean_transmittance', 'sense': 'maximize',
              'mean_reduction': 'wavelength_integral_mean',
              'region': {'wavelength_nm': [700, 1000], 'angle_deg': 45, 'polarization': 'unpolarized'}}]
    task = {'stack': {'name': name, 'incident': {'constant_n': 1}, 'exit': {'material': 'sio2'},
                      'layers': [{'material': 'tio2', 'thickness_nm': thickness}]},
            'spectrum': {'values_nm': [450, 500, 650, 700, 850, 1000]},
            'illumination': {'angles_deg': [45], 'polarizations': ['unpolarized']},
            'requested_outputs': ['R', 'T', 'A']}
    result = {'instance_id': name, 'route_id': 'r1', 'status': 'completed',
              'physics_accepted': True, 'certificate': {'accepted': True}, 'envelope': {'ok': True},
              'cache_hit': False, 'simulation_key': 'simulation-' + str(thickness),
              'full_result_ref': {'path': name + '.json', 'sha256': 'a' * 64}, 'objectives': goals,
              'parameters': {'thicknesses_nm': [thickness], 'layer_materials': ['tio2']},
              'raw_result': {'wavelengths_nm': task['spectrum']['values_nm'], 'channels': {
                  'angle=45|pol=unpolarized': {'R': [r] * 6, 'T': [t] * 6, 'A': [.1] * 6}}}}
    return task, result


def seeded():
    task, result = fixture()
    empty = create_design_archive(result['objectives'], task)
    archive = add_certified_design(empty, result, physical_input=task, role='baseline_reference',
                                   family={'family_id': 'quarter_wave', 'parameterization': 'tied'})
    return archive, archive['latest_event']['design_id']


def test_history_pareto_tradeoff_and_worse_new_design_retained():
    archive, baseline_id = seeded()
    original = deepcopy(archive)
    task, result = fixture(60, .5, .4, 'tradeoff')
    archive = add_certified_design(archive, result, physical_input=task, parent_design_id=baseline_id)
    tradeoff_id = archive['latest_event']['design_id']
    assert set(archive['pareto_design_ids']) == {baseline_id, tradeoff_id}
    assert set(archive['designs'][tradeoff_id]['metrics']) == {'G1', 'G2'}
    assert archive['designs'][tradeoff_id]['parent_design_id'] == baseline_id
    assert archive['latest_event']['layer_diff']['changed_layers'][0]['delta_nm'] == 10
    task, result = fixture(70, .3, .3, 'worse')
    archive = add_certified_design(archive, result, physical_input=task, parent_design_id=tradeoff_id)
    assert len(archive['designs']) == 3
    assert set(archive['pareto_design_ids']) == {baseline_id, tradeoff_id}
    assert original['designs'][baseline_id] == archive['designs'][baseline_id]
    task, result = fixture(80, .6, .6, 'dominates')
    archive = add_certified_design(archive, result, physical_input=task, parent_design_id=baseline_id)
    assert archive['pareto_design_ids'] == [archive['latest_event']['design_id']]
    assert len(archive['designs']) == 4
    assert archive['effective_intervention_count'] == 3
    assert json.loads(json.dumps(archive)) == archive


def test_cache_repeats_and_baseline_are_not_effective_interventions():
    archive, baseline_id = seeded()
    assert archive['effective_intervention_count'] == 0
    task, result = fixture(60, .5, .4)
    result['cache_hit'] = True
    archive = add_certified_design(archive, result, physical_input=task, parent_design_id=baseline_id)
    assert archive['latest_event']['new_design']
    assert not archive['latest_event']['new_evidence']
    assert not archive['latest_event']['effective_intervention']
    result['cache_hit'] = False  # Even a wrongly marked replay cannot create evidence.
    archive = add_certified_design(archive, result, physical_input=task, parent_design_id=baseline_id)
    assert len(archive['designs']) == 2 and len(archive['evaluations']) == 3
    assert archive['new_evidence_count'] == 1
    task, result = fixture(70)
    archive = add_certified_design(archive, result, physical_input=task,
                                   parent_design_id=baseline_id, role='baseline_reference')
    assert archive['effective_intervention_count'] == 0


@pytest.mark.parametrize('field,value', [('physics_accepted', False), ('status', 'failed'),
    ('certificate', {'accepted': False}), ('envelope', {'ok': False}), ('cache_hit', None),
    ('simulation_key', None), ('full_result_ref', None)])
def test_reject_uncertified_or_missing_provenance_without_mutation(field, value):
    archive, parent = seeded()
    before = deepcopy(archive)
    task, result = fixture(60)
    result[field] = value
    with pytest.raises(ValueError):
        add_certified_design(archive, result, physical_input=task, parent_design_id=parent)
    assert archive == before


@pytest.mark.parametrize('change', ['sense', 'missing_goal', 'angle', 'polarization', 'band',
                                    'reduction', 'substrate', 'grid', 'solver', 'provider'])
def test_objectives_and_complete_conditions_never_mix(change):
    archive, parent = seeded()
    task, result = fixture(60)
    if change == 'sense': result['objectives'][0]['sense'] = 'minimize'
    elif change == 'missing_goal': result['objectives'].pop()
    elif change == 'angle': task['illumination']['angles_deg'] = [0]
    elif change == 'polarization': task['illumination']['polarizations'] = ['s']
    elif change == 'band': result['objectives'][1]['region']['wavelength_nm'] = [700, 900]
    elif change == 'reduction': result['objectives'][0]['mean_reduction'] = 'sample_mean'
    elif change == 'substrate': task['stack']['exit']['material'] = 'tio2'
    elif change == 'grid': task['spectrum']['values_nm'][1] = 510
    elif change == 'solver': task['solver'] = 'different'
    elif change == 'provider': task['stack']['exit']['dataset_id'] = 'other'
    with pytest.raises(ValueError, match='mismatch'):
        add_certified_design(archive, result, physical_input=task, parent_design_id=parent)


@pytest.mark.parametrize('quantity', ['R', 'T', 'A'])
@pytest.mark.parametrize('stat', ['mean', 'min', 'max'])
@pytest.mark.parametrize('sense', ['maximize', 'minimize'])
def test_arbitrary_quantity_stat_and_direction(quantity, stat, sense):
    task, result = fixture()
    goal = result['objectives'][0]
    goal.update(metric=quantity + '_' + stat, sense=sense)
    result['objectives'] = [goal]
    result['raw_result']['channels']['angle=45|pol=unpolarized'][quantity] = [.1, .7, .4, .2, .2, .2]
    archive = create_design_archive([goal], task)
    archive = add_certified_design(archive, result, physical_input=task)
    first_id = archive['latest_event']['design_id']
    expected = {'min': .1, 'max': .7, 'mean': .5125}[stat]
    assert archive['designs'][first_id]['metrics']['G1']['value'] == pytest.approx(expected)
    task['stack']['layers'][0]['thickness_nm'] = 60
    result['parameters']['thicknesses_nm'] = [60]
    result['simulation_key'] = 'next'
    values = result['raw_result']['channels']['angle=45|pol=unpolarized'][quantity]
    result['raw_result']['channels']['angle=45|pol=unpolarized'][quantity] = [v + .05 for v in values]
    archive = add_certified_design(archive, result, physical_input=task, parent_design_id=first_id)
    assert archive['pareto_design_ids'] == ([archive['latest_event']['design_id']] if sense == 'maximize' else [first_id])


def test_family_parameterization_changes_and_actual_layer_diff():
    archive, parent = seeded()
    task, result = fixture(60)
    archive = add_certified_design(archive, result, physical_input=task, parent_design_id=parent,
        family={'family_id': 'quarter_wave', 'parameterization': 'per_layer_free'})
    event = archive['latest_event']
    assert event['family_state'] == 'new'
    assert event['family_changed_from_parent'] and event['parameterization_changed_from_parent']
    parent = event['design_id']
    task, result = fixture(70)
    task['stack']['layers'].append({'material': 'sio2', 'thickness_nm': 90})
    result['parameters'] = {'thicknesses_nm': [70, 90], 'layer_materials': ['tio2', 'sio2']}
    archive = add_certified_design(archive, result, physical_input=task, parent_design_id=parent)
    changes = archive['latest_event']['layer_diff']['changed_layers']
    assert changes[0]['delta_nm'] == 10
    assert changes[1]['change'] == 'added' and changes[1]['after_nm'] == 90
    assert changes[1]['delta_nm'] is None


def test_display_name_not_physics_and_duplicate_keeps_alternate_ancestry():
    archive, parent = seeded()
    task, result = fixture()
    task['stack']['name'] = 'renamed'
    result['instance_id'] = 'new_label'
    result['cache_hit'] = True
    archive = add_certified_design(archive, result, physical_input=task, parent_design_id=parent,
                                   family={'parameterization': 'free'})
    assert len(archive['designs']) == 1
    assert archive['latest_event']['design_id'] == parent
    assert not archive['latest_event']['effective_intervention']
    assert archive['designs'][parent]['parent_design_id'] is None
    assert archive['latest_event']['parent_design_id'] == parent


@pytest.mark.parametrize('change', ['thickness', 'material', 'nan', 'unknown_parent', 'conflict'])
def test_reject_inconsistent_results(change):
    archive, parent = seeded()
    task, result = fixture(60)
    if change == 'thickness': result['parameters']['thicknesses_nm'] = [999]
    elif change == 'material': result['parameters']['layer_materials'] = ['other']
    elif change == 'nan': result['raw_result']['channels']['angle=45|pol=unpolarized']['R'][0] = float('nan')
    elif change == 'unknown_parent': parent = 'missing'
    elif change == 'conflict': task, result = fixture(50, .9, .9)
    with pytest.raises(ValueError):
        add_certified_design(archive, result, physical_input=task, parent_design_id=parent)


def test_real_certified_executor_result(tmp_path):
    from focused_research.research_execution import execute_prepared_batch
    from focused_research.route_consumer import prepare_tmm_inputs
    task, template = fixture()
    instance = {'status': 'prepared', 'route_id': 'r1', 'instance_id': 'actual',
                'incident_medium': 'air', 'substrate': 'glass',
                'layers': [{'material': 'film', 'thickness_nm': 50}], 'objectives': template['objectives']}
    prepared = prepare_tmm_inputs(instance, optical_media={'air': {'constant_n': 1.0},
        'glass': {'material': 'sio2'}, 'film': {'material': 'tio2'}},
        wavelengths_nm=task['spectrum']['values_nm'])
    actual = execute_prepared_batch([prepared], cache_dir=tmp_path, max_new_evals=1)['results'][0]
    assert actual['physics_accepted'], actual
    archive = create_design_archive(prepared['objectives'], prepared['tmm_task'])
    archive = add_certified_design(archive, actual, physical_input=prepared['tmm_task'], role='baseline_reference')
    assert len(archive['designs']) == 1
    assert set(next(iter(archive['designs'].values()))['metrics']) == {'G1', 'G2'}


def test_same_simulation_cannot_be_rebound_to_new_physics():
    archive, parent = seeded()
    task, result = fixture(60)
    result['simulation_key'] = 'simulation-50'
    with pytest.raises(ValueError, match='already bound'):
        add_certified_design(archive, result, physical_input=task, parent_design_id=parent)


def test_no_implicit_candidate_cap_and_incremental_frontier_matches_full():
    from focused_research.design_archive import pareto_design_ids
    archive, parent = seeded()
    for index in range(101):
        task, result = fixture(100 + index, .45 + index / 1000, .55 + index / 1000)
        archive = add_certified_design(archive, result, physical_input=task, parent_design_id=parent)
    assert len(archive['designs']) == 102
    assert len(archive['evaluations']) == 102
    assert archive['effective_intervention_count'] == 101
    assert archive['pareto_design_ids'] == pareto_design_ids(archive)


@pytest.mark.parametrize('field', ['provider', 'dataset_id'])
def test_executor_complete_physical_input_rejects_rebound_material_source(field):
    archive, parent = seeded()
    task, result = fixture(60)
    result['physical_input'] = deepcopy(task)
    task['stack']['layers'][0][field] = 'different-source'
    with pytest.raises(ValueError, match='physical_input mismatch'):
        add_certified_design(archive, result, physical_input=task, parent_design_id=parent)


def test_complete_and_legacy_input_binding_are_explicit():
    archive, parent = seeded()
    assert archive['latest_event']['input_binding'] == 'caller_contract_partial'
    task, result = fixture(60)
    result['physical_input'] = deepcopy(task)
    result['physical_input']['stack']['name'] = 'display-only'
    archive = add_certified_design(archive, result, physical_input=task, parent_design_id=parent)
    assert archive['latest_event']['input_binding'] == 'executor_physical_input_matched'


def test_numeric_spellings_share_full_physical_and_goal_identity():
    archive, parent = seeded()
    task, result = fixture(50.0)
    task['stack']['incident']['constant_n'] = 1.0
    task['illumination']['angles_deg'] = [45.0]
    task['spectrum']['values_nm'] = [float(x) for x in task['spectrum']['values_nm']]
    for goal in result['objectives']:
        goal['region']['angle_deg'] = 45.0
        goal['region']['wavelength_nm'] = [float(x) for x in goal['region']['wavelength_nm']]
    result['physical_input'] = deepcopy(task)
    result['simulation_key'] = 'simulation-50'
    result['cache_hit'] = True
    archive = add_certified_design(archive, result, physical_input=task, parent_design_id=parent)
    assert archive['latest_event']['design_id'] == parent
    assert len(archive['designs']) == 1
    assert not archive['latest_event']['new_evidence']
    rebuilt = create_design_archive(result['objectives'], task)
    assert rebuilt['context_id'] == archive['context_id']


def test_numeric_canonicalization_preserves_boolean_physics_flags():
    task, result = fixture()
    task['allow_material_extrapolation'] = False
    archive = create_design_archive(result['objectives'], task)
    archive = add_certified_design(archive, result, physical_input=task)
    stored = next(iter(archive['designs'].values()))['physical_input']
    assert stored['allow_material_extrapolation'] is False
    assert type(stored['stack']['layers'][0]['thickness_nm']) is float
