"""Small real-solver tests use explicit synthetic constants, not real-material claims."""
import copy
import json
import sys
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import family_optimization as fo
import research_routes as rr
import route_consumer as rc


def inputs():
    def read(name):
        return json.loads((ROOT/'examples'/name).read_text())
    sheet = read('rgb_nir_task_sheet.json')
    evidence = read('research_routes_evidence_fixture.json')
    constraints = read('research_routes_constraints.json')
    routes = read('research_routes_example.json')
    route = routes['routes'][0]
    instances = rr.materialize(routes, route['id'], sheet, evidence, design_constraints=constraints)
    media = {'air': {'constant_n': 1.}, 'fused_silica': {'constant_n': 1.46}, 'TiO2': {'constant_n': 2.3}, 'SiO2': {'constant_n': 1.46}}
    envelopes = [rc.prepare_tmm_inputs(x, optical_media=media, wavelengths_nm=list(range(450, 1001, 25))) for x in instances]
    return route, envelopes


def test_real_numpy_search_preserves_ties_budget_and_improves():
    route, envelopes = inputs()
    old = copy.deepcopy(envelopes)
    result = fo.optimize_family(route, envelopes, max_forward_evaluations=90, backend='numpy')
    assert envelopes == old
    assert result['backend'] == 'numpy'
    assert 0 < result['metering']['forward_design_rows'] <= 90
    assert result['metering']['forward_design_rows'] == len(result['evaluations'])
    assert result['candidates']
    seeds = [r for r in result['evaluations'] if r['source'] == 'original_seed']
    valid = [r for r in result['evaluations'] if r['status'] == 'evaluated']
    assert max(sum(r['metrics']) for r in valid) > max(sum(r['metrics']) for r in seeds) + 1e-5
    for row in valid:
        by_var = {}
        for layer, value in zip(route['family']['layers'], row['thicknesses_nm']):
            name = layer['thickness_nm']
            if isinstance(name, str):
                assert name not in by_var or by_var[name] == value
                by_var[name] = value
                lo, hi = route['family']['thickness_variables_nm'][name]
                assert lo <= value <= hi
            else:
                assert value == name
    assert all(not e['numerical_search']['physics_accepted'] for e in result['candidates'])
    assert all(e['material_template_instance_id'] == envelopes[0]['instance_id'] for e in result['candidates'])
    assert all(e['parent_instance_id'] is None or e['parent_instance_id'] in [x['instance_id'] for x in envelopes] for e in result['candidates'])


@pytest.mark.parametrize('budget', [0, 1, 2, 7])
def test_tiny_hard_budget(budget):
    route, envelopes = inputs()
    result = fo.optimize_family(route, envelopes, max_forward_evaluations=budget, backend='numpy')
    assert result['metering']['forward_design_rows'] <= budget
    if budget == 0:
        assert not result['candidates'] and not result['evaluations']


def test_explicit_layerwise_branch_and_invalid_shared_seed():
    route, envelopes = inputs()
    result = fo.optimize_family(route, envelopes, max_forward_evaluations=2, backend='numpy', parameterization='layerwise_refinement')
    assert len(result['variable_names']) == len(route['family']['layers'])
    envelopes[0]['tmm_task']['stack']['layers'][2]['thickness_nm'] += 1
    with pytest.raises(ValueError, match='shared variable'):
        fo.optimize_family(route, envelopes, max_forward_evaluations=2, backend='numpy')


def test_failed_solver_attempt_is_charged(monkeypatch):
    route, envelopes = inputs()
    _, _, Workbench, _, _ = fo._modules()
    def fail(*args, **kwargs):
        raise RuntimeError('controlled solver failure')
    monkeypatch.setattr(Workbench, 'simulate', fail)
    result = fo.optimize_family(route, envelopes, max_forward_evaluations=10, backend='numpy')
    assert 1 <= result['metering']['forward_design_rows'] <= 10
    assert result['metering']['failed_forward_design_rows'] == result['metering']['forward_design_rows']
    assert result['evaluations'][0]['status'] == 'failed'
    assert not result['candidates']


def test_torch_real_search_when_available():
    pytest.importorskip('torch')
    route, envelopes = inputs()
    result = fo.optimize_family(route, envelopes, max_forward_evaluations=40, backend='torch')
    assert result['backend'] == 'torch'
    assert result['candidates']
    assert result['metering']['forward_design_rows'] <= 40
    assert not result['metering']['failed_forward_design_rows']
    assert not any(s.get('status') == 'failed' for s in result['optimizer_stops'])
    reference = fo.optimize_family(route, envelopes, max_forward_evaluations=2, backend='numpy')
    assert result['evaluations'][0]['metrics'] == pytest.approx(reference['evaluations'][0]['metrics'], abs=1e-8)
    seed_best = max(sum(r['metrics']) for r in result['evaluations'] if r['source'] == 'original_seed')
    assert max(sum(r['metrics']) for r in result['evaluations'] if r['status'] == 'evaluated') > seed_best + 1e-6


def test_generic_goal_statistics_and_senses():
    route, envelopes = inputs()
    for envelope in envelopes:
        envelope['objectives'][0].update(metric='min_reflectance', sense='maximize')
        envelope['objectives'][0].pop('mean_reduction', None)
        envelope['objectives'][1].update(metric='max_transmittance', sense='minimize')
        envelope['objectives'][1].pop('mean_reduction', None)
    result = fo.optimize_family(route, envelopes, max_forward_evaluations=12, backend='numpy')
    assert result['candidates'] and result['metering']['forward_design_rows'] <= 12
    assert not result['metering']['failed_forward_design_rows']


def test_fixed_layers_and_determinism():
    route, envelopes = inputs()
    route['family']['layers'][0]['thickness_nm'] = 77.
    for e in envelopes:
        e['tmm_task']['stack']['layers'][0]['thickness_nm'] = 77.
    a = fo.optimize_family(route, envelopes, max_forward_evaluations=14, backend='numpy', seed=17)
    b = fo.optimize_family(route, envelopes, max_forward_evaluations=14, backend='numpy', seed=17)
    assert a['evaluations'] == b['evaluations']
    assert all(r['thicknesses_nm'][0] == 77. for r in a['evaluations'])


def test_torch_shared_variable_gradient_matches_numpy_finite_difference():
    pytest.importorskip('torch')
    import numpy as np
    route, envelopes = inputs()
    seed = envelopes[0]
    result = fo.optimize_family(route, [seed], max_forward_evaluations=1, backend='torch')
    assert result['metering']['forward_design_rows'] == 1
    row = result['evaluations'][0]
    assert row['status'] == 'evaluated'
    jacobian = np.asarray(row['metric_jacobian_wrt_normalized_parameters'])
    eps = 1e-5
    reference = fo.optimize_family(route, [seed], max_forward_evaluations=1, backend='numpy')
    assert row['metrics'] == pytest.approx(reference['evaluations'][0]['metrics'], abs=1e-7)
    for j, name in enumerate(result['variable_names']):
        lower, upper = route['family']['thickness_variables_nm'][name]
        h_nm = eps * (upper-lower)
        if h_nm == 0:
            assert jacobian[:, j] == pytest.approx(np.zeros(len(row['metrics'])), abs=1e-10)
            continue
        metrics = []
        for direction in (-1, 1):
            probe = copy.deepcopy(seed)
            for layer, specification in zip(probe['tmm_task']['stack']['layers'], route['family']['layers']):
                if specification['thickness_nm'] == name:
                    layer['thickness_nm'] += direction*h_nm
            evaluated = fo.optimize_family(route, [probe], max_forward_evaluations=1, backend='numpy')
            assert evaluated['metering']['forward_design_rows'] == 1
            assert evaluated['evaluations'][0]['status'] == 'evaluated'
            metrics.append(np.asarray(evaluated['evaluations'][0]['metrics']))
        finite_difference = (metrics[1]-metrics[0])/(2*eps)
        assert jacobian[:, j] == pytest.approx(finite_difference, rel=2e-4, abs=2e-6)


def test_torch_failed_solver_attempts_are_charged(monkeypatch):
    pytest.importorskip('torch')
    route, envelopes = inputs()
    fo._modules()
    from tmm_engine.differentiable import DifferentiableTMM
    def fail(*args, **kwargs):
        raise RuntimeError('controlled torch solver failure')
    monkeypatch.setattr(DifferentiableTMM, 'forward', fail)
    result = fo.optimize_family(route, envelopes, max_forward_evaluations=5, backend='torch')
    assert 0 < result['metering']['forward_design_rows'] <= 5
    assert result['metering']['forward_design_rows'] == len(result['evaluations'])
    assert result['metering']['failed_forward_design_rows'] == result['metering']['forward_design_rows']
    assert not result['candidates']


def test_saved_derivative_summary_units_conflicts_and_bounds():
    row = {'evaluation_index': 7, 'parameters_nm': {'shared': 20., 'other': 15., 'fixed': 5.},
           'metric_jacobian_wrt_normalized_parameters': [[2., 1., 0.], [4., -3., 0.]]}
    goals = [{'objective_id': 'G1', 'sense': 'maximize'}, {'objective_id': 'G2', 'sense': 'minimize'}]
    summary = fo.summarize_local_derivatives(row, ['shared', 'other', 'fixed'], [[10., 20.], [10., 30.], [5., 5.]], goals)
    assert summary['status'] == 'local_derivative_uncertified'
    assert summary['additional_forward_design_rows'] == 0
    first = summary['goal_sensitivities'][0]['top_variables'][0]
    assert first['name'] == 'shared' and first['at_bound'] == 'upper'
    assert first['derivative_per_nm'] == pytest.approx(.2)
    assert first['derivative_per_normalized_variable'] == 2.
    conflict = summary['goal_gradient_conflicts']
    assert conflict['conflicting_variable_count'] == 1
    assert conflict['variables'][0]['name'] == 'shared'
    assert conflict['variables'][0]['increasing_variable_improves_goals'] == ['G1']
    assert conflict['variables'][0]['increasing_variable_worsens_goals'] == ['G2']
    assert all(v['name'] != 'fixed' for g in summary['goal_sensitivities'] for v in g['top_variables'])


def test_numpy_derivative_summary_is_explicitly_unknown():
    route, envelopes = inputs()
    result = fo.optimize_family(route, envelopes, max_forward_evaluations=2, backend='numpy')
    assert result['metering']['forward_design_rows'] == 2
    assert result['candidates']
    for candidate in result['candidates']:
        summary = candidate['numerical_search']['local_derivative_summary']
        assert summary['status'] == 'unknown'
        assert summary['additional_forward_design_rows'] == 0
        assert 'goal_sensitivities' not in summary


def test_saved_derivative_summary_top_three_and_stationary():
    names = ['a', 'b', 'c', 'd']
    row = {'evaluation_index': 1, 'parameters_nm': dict.fromkeys(names, 1.5),
        'metric_jacobian_wrt_normalized_parameters': [[0., 0., 0., 0.], [1., 2., 3., 4.]]}
    goals = [{'objective_id': 'G1', 'sense': 'maximize'}, {'objective_id': 'G2', 'sense': 'maximize'}]
    summary = fo.summarize_local_derivatives(row, names, [[1., 2.]]*4, goals)
    assert [v['name'] for v in summary['goal_sensitivities'][1]['top_variables']] == ['d', 'c', 'b']
    assert summary['goal_gradient_conflicts']['status'] == 'none_above_threshold'


def test_provided_only_uses_concrete_seeds_without_latin_hypercube(monkeypatch):
    from scipy.stats import qmc
    route, envelopes = inputs()
    original = copy.deepcopy((route, envelopes))
    def forbidden(*args, **kwargs):
        raise AssertionError('provided_only must not construct global LHS')
    monkeypatch.setattr(qmc, 'LatinHypercube', forbidden)
    result = fo.optimize_family(route, envelopes, max_forward_evaluations=24,
        backend='numpy', initialization_policy='provided_only')
    assert (route, envelopes) == original
    assert not any(s.get('status') == 'failed' for s in result['optimizer_stops'])
    assert not any(r['source'] == 'space_filling_start' for r in result['evaluations'])
    assert [r['source_instance_ids'] for r in result['evaluations'][:2]] == [[e['instance_id']] for e in envelopes]
    assert all(r.get('parent_evaluation_index') for r in result['evaluations'][2:])
    assert result['metering']['forward_design_rows'] <= 24
    assert result['metering']['forward_design_rows'] == len(result['evaluations'])
    assert result['search_policy']['search_bounds_nm'] == route['family']['thickness_variables_nm']
    assert result['search_policy']['global_space_filling_initialization_enabled'] is False
    assert result['candidates']
    assert all(c['parent_instance_id'] in [e['instance_id'] for e in envelopes] for c in result['candidates'])


def test_narrow_search_bounds_are_policy_only_and_all_rows_respect_them():
    route, envelopes = inputs()
    requested = {'dH': [54., 66.]}
    original = copy.deepcopy((route, envelopes, requested))
    result = fo.optimize_family(route, envelopes, max_forward_evaluations=24,
        backend='numpy', initialization_policy='provided_only', search_bounds_nm=requested)
    assert (route, envelopes, requested) == original
    assert result['search_policy']['search_bounds_nm']['dH'] == [54., 66.]
    assert result['search_policy']['search_bounds_nm']['dL'] == route['family']['thickness_variables_nm']['dL']
    assert result['search_policy']['original_family_bounds_nm'] == route['family']['thickness_variables_nm']
    assert result['search_policy']['bounds_role'] == 'numerical_search_policy_not_new_user_physical_constraints'
    assert all(54. <= r['parameters_nm']['dH'] <= 66. for r in result['evaluations'])
    assert all(c['numerical_search']['search_policy'] == result['search_policy'] for c in result['candidates'])


@pytest.mark.parametrize('override', [
    {'unknown': [1, 2]}, {'dH': [44, 70]}, {'dH': [50, 86]}, {'dH': [66, 54]},
    {'dH': [float('nan'), 70]}, {'dH': [50, float('inf')]}, {'dH': [True, 70]},
    {'dH': ['54', 66]}, {'dH': [54]}, [54, 66], {'dH': [54, 60]},
])
def test_invalid_search_bounds_or_any_seed_outside_bounds_rejected_before_solver(monkeypatch, override):
    route, envelopes = inputs()
    _, _, Workbench, _, _ = fo._modules()
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid search policy must not dispatch solver')
    monkeypatch.setattr(Workbench, 'simulate', forbidden)
    with pytest.raises(ValueError):
        fo.optimize_family(route, envelopes, max_forward_evaluations=10, backend='numpy', search_bounds_nm=override)


def test_layerwise_bound_keys_and_zero_budget_metadata():
    route, envelopes = inputs()
    result = fo.optimize_family(route, envelopes, max_forward_evaluations=0, backend='numpy',
        parameterization='layerwise_refinement', initialization_policy='provided_only',
        search_bounds_nm={'layer_0:dH': [54, 66]})
    assert result['metering']['forward_design_rows'] == 0 and not result['evaluations']
    assert result['search_policy']['search_bounds_nm']['layer_0:dH'] == [54, 66]
    assert result['search_policy']['search_bounds_nm']['layer_2:dH'] == [45, 85]
    with pytest.raises(ValueError, match='existing variable names'):
        fo.optimize_family(route, envelopes, max_forward_evaluations=0, backend='numpy',
            parameterization='layerwise_refinement', search_bounds_nm={'dH': [54, 66]})


def test_default_initialization_remains_space_filling_and_cache_is_per_call():
    route, envelopes = inputs()
    a = fo.optimize_family(route, envelopes, max_forward_evaluations=18, backend='numpy')
    b = fo.optimize_family(route, envelopes, max_forward_evaluations=18, backend='numpy',
        initialization_policy='space_filling', search_bounds_nm=None)
    assert a['evaluations'] == b['evaluations']
    assert any(r['source'] == 'space_filling_start' for r in a['evaluations'])
    assert a['metering']['forward_design_rows'] == b['metering']['forward_design_rows'] > 2
    with pytest.raises(ValueError, match='initialization_policy'):
        fo.optimize_family(route, envelopes, max_forward_evaluations=0, initialization_policy='unknown')


def test_torch_provided_only_narrow_bounds_when_available():
    pytest.importorskip('torch')
    route, envelopes = inputs()
    result = fo.optimize_family(route, envelopes, max_forward_evaluations=12, backend='torch',
        initialization_policy='provided_only', search_bounds_nm={'dH': [54, 66]})
    assert result['candidates'] and not result['metering']['failed_forward_design_rows']
    assert result['metering']['forward_design_rows'] <= 12
    assert not any(r['source'] == 'space_filling_start' for r in result['evaluations'])
    assert all(54 <= r['parameters_nm']['dH'] <= 66 for r in result['evaluations'])
    reference = fo.optimize_family(route, envelopes, max_forward_evaluations=1, backend='numpy',
        initialization_policy='provided_only', search_bounds_nm={'dH': [54, 66]})
    assert result['evaluations'][0]['metrics'] == pytest.approx(reference['evaluations'][0]['metrics'], abs=1e-7)


def test_narrow_window_bound_is_not_original_physical_bound():
    row = {'evaluation_index': 1, 'parameters_nm': {'dH': 60.},
           'metric_jacobian_wrt_normalized_parameters': [[2.]]}
    summary = fo.summarize_local_derivatives(row, ['dH'], [[50., 60.]],
        [{'objective_id': 'G1', 'sense': 'maximize'}],
        original_family_bounds_nm={'dH': [45., 85.]}, bounds_role='search_policy_bounds')
    assert summary['bounds_role'] == 'search_policy_bounds'
    assert summary['original_family_bounds_nm'] == {'dH': [45., 85.]}
    assert 'not a new user physical constraint' in summary['at_bound_scope']
    variable = summary['goal_sensitivities'][0]['top_variables'][0]
    assert variable['at_bound'] == 'upper' and variable['bounds_nm'] == [50., 60.]
    assert variable['current_value_nm'] < summary['original_family_bounds_nm']['dH'][1]
    assert variable['derivative_per_nm'] == pytest.approx(.2)
    route, envelopes = inputs()
    narrowed = fo.optimize_family(route, envelopes[:1], max_forward_evaluations=1, backend='numpy',
        initialization_policy='provided_only', search_bounds_nm={'dH': [55., 65.]})
    delivered = narrowed['candidates'][0]['numerical_search']['local_derivative_summary']
    assert delivered['status'] == 'unknown'
    assert delivered['bounds_role'] == 'search_policy_bounds'
    assert delivered['original_family_bounds_nm'] == route['family']['thickness_variables_nm']
    default = fo.optimize_family(route, envelopes[:1], max_forward_evaluations=1, backend='numpy')
    assert default['candidates'][0]['numerical_search']['local_derivative_summary']['bounds_role'] == 'original_family_bounds'
