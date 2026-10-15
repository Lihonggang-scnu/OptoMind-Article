"""Actual spectral arrays, including counterexamples to thickness heuristics."""
from copy import deepcopy
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from physics_diagnostics import build_physics_diagnostics


def spectra():
    grid = list(range(400, 721, 40))
    goals = [{'objective_id': 'reflection', 'metric': 'mean_reflectance', 'sense': 'maximize',
              'mean_reduction': 'wavelength_integral_mean',
              'region': {'wavelength_nm': [400, 720], 'angle_deg': 45, 'polarization': 'unpolarized'}},
             {'objective_id': 'transmission', 'metric': 'mean_transmittance', 'sense': 'maximize',
              'mean_reduction': 'wavelength_integral_mean',
              'region': {'wavelength_nm': [400, 720], 'angle_deg': 45, 'polarization': 'unpolarized'}}]
    b = {'route_id': 'route', 'instance_id': 'parent', 'physics_accepted': True,
         'parameters': {'thicknesses_nm': [50, 100], 'layer_materials': ['H', 'L']},
         'objectives': goals, 'full_result_ref': {'path': 'parent.json', 'sha256': 'a'*64},
         'raw_result': {'wavelengths_nm': grid, 'channels': {}}}
    c = deepcopy(b)
    c.update(instance_id='child', full_result_ref={'path': 'child.json', 'sha256': 'b'*64})
    c['parameters']['thicknesses_nm'] = [55, 110]
    # A band moves red while gaining area; blue loses and red gains.
    for r, ys in [(b, [.1,.7,.8,.8,.7,.1,.1,.1,.1]),
                  (c, [.1,.1,.1,.7,.8,.8,.8,.7,.1])]:
        r['raw_result']['channels']['angle=45|pol=unpolarized'] = {'R': ys, 'T': [1-y for y in ys]}
    return b, c


def test_actual_red_band_and_competing_goals_exposed():
    b,c = spectra()
    d = build_physics_diagnostics(b,c,reflection_threshold=.5)
    r,t = d['goals']
    assert d['all_goals_retained'] and len(d['goals']) == 2
    assert r['subbands'][0]['delta'] == pytest.approx(-1.3/3)
    assert r['subbands'][2]['delta'] == pytest.approx(1.3/3)
    assert r['red_gain_blue_loss'] is True
    assert r['whole_thickening_tradeoff']['red_gain_blue_loss'] is True
    assert t['red_gain_blue_loss'] is False
    edges = r['reflection_bands']['edge_motion']
    assert edges['both_sample_edges_moved_red']
    assert edges['blue_sample_edge_delta_nm'] == 80
    assert edges['red_sample_edge_delta_nm'] == 120
    assert d['next_iteration_evidence']['negative_goal_ids'] == ['transmission']
    assert d['next_iteration_evidence']['observed_objective_tradeoff']
    assert d['full_result_refs'] == {'baseline': b['full_result_ref'], 'candidate': c['full_result_ref']}


def test_no_assumed_polarization_or_threshold():
    d = build_physics_diagnostics(*spectra())
    assert d['goals'][0]['polarization']['status'] == 'unknown'
    assert d['goals'][0]['reflection_bands']['status'] == 'unknown'


def test_sp_means_and_split_come_from_raw_data():
    b,c = spectra()
    for result,s,p in [(b,.8,.4),(c,.7,.6)]:
        for pol,y in [('s',s),('p',p)]:
            result['raw_result']['channels']['angle=45|pol='+pol] = {'R': [y]*9, 'T': [1-y]*9}
    pol = build_physics_diagnostics(b,c)['goals'][0]['polarization']
    assert pol['baseline']['s_minus_p_mean'] == pytest.approx(.4)
    assert pol['candidate']['s_minus_p_mean'] == pytest.approx(.1)
    assert pol['s_delta'] == pytest.approx(-.1)
    assert pol['p_delta'] == pytest.approx(.2)
    assert pol['s_minus_p_delta'] == pytest.approx(-.3)


def test_thickening_is_not_automatically_a_red_gain():
    b,c = spectra()
    c['raw_result'] = deepcopy(b['raw_result'])
    r = build_physics_diagnostics(b,c,reflection_threshold=.5)['goals'][0]
    assert r['red_gain_blue_loss'] is False
    assert r['objective_direction'] == 'unchanged'
    assert r['reflection_bands']['edge_motion']['both_sample_edges_moved_red'] is False


def test_nonisolated_or_nonuniform_thickness_is_not_whole_stack_scaling():
    b,c = spectra()
    c['parameters']['layer_materials'][0] = 'different'
    d = build_physics_diagnostics(b,c)
    assert d['whole_stack_scaling']['status'] == 'unknown'
    assert d['goals'][0]['whole_thickening_tradeoff']['status'] == 'unknown'
    c['parameters']['layer_materials'] = b['parameters']['layer_materials']
    c['parameters']['thicknesses_nm'] = [55,100]
    assert not build_physics_diagnostics(b,c)['whole_stack_scaling']['uniform_scaling']


@pytest.mark.parametrize('mutation', ['certificate','route','grid','nan','missing'])
def test_unusable_evidence_preserves_every_unknown_goal(mutation):
    b,c = spectra()
    if mutation == 'certificate': c['physics_accepted'] = False
    if mutation == 'route': c['route_id'] = 'other'
    if mutation == 'grid': c['raw_result']['wavelengths_nm'][1] = 441
    if mutation == 'missing': c['raw_result']['channels'] = {}
    if mutation == 'nan':
        for q in ['R','T']: c['raw_result']['channels']['angle=45|pol=unpolarized'][q][1] = float('nan')
    d = build_physics_diagnostics(b,c)
    assert [r['status'] for r in d['goals']] == ['unknown','unknown']
    assert d['next_iteration_evidence']['unknown_goal_ids'] == ['reflection','transmission']


def test_multiple_bands_and_grid_clipping_do_not_get_invented_matching():
    b,c = spectra()
    c['raw_result']['channels']['angle=45|pol=unpolarized']['R'] = [.8,.1,.8,.8,.1,.1,.1,.1,.1]
    bands = build_physics_diagnostics(b,c,reflection_threshold=.5)['goals'][0]['reflection_bands']
    assert len(bands['candidate']) == 2
    assert bands['candidate'][0]['blue_crossing_bracket_nm'] is None
    assert bands['edge_motion']['status'] == 'unknown'


def test_sample_means_do_not_replace_integral_or_min_objective():
    b,c = spectra()
    c['objectives'][0]['metric'] = 'min_reflectance'
    d = build_physics_diagnostics(b,c)
    assert d['goals'][0]['delta'] == pytest.approx(0)
    assert d['goals'][0]['objective_direction'] == 'unchanged'
    assert d['goals'][0]['red_gain_blue_loss'] is True


def test_empty_thirds_explicitly_unknown():
    b,c = spectra()
    for result in (b,c):
        result['raw_result']['wavelengths_nm'] = [400,401,720]
        result['raw_result']['channels']['angle=45|pol=unpolarized'] = {'R': [.1,.2,.3], 'T': [.9,.8,.7]}
    row = build_physics_diagnostics(b,c)['goals'][0]
    assert row['subbands'][1]['status'] == 'unknown'
    assert row['subbands'][1]['sample_count'] == 0


@pytest.mark.parametrize('threshold',[float('nan'),True,-.1,1.1])
def test_invalid_threshold_rejected(threshold):
    with pytest.raises(ValueError): build_physics_diagnostics(*spectra(),reflection_threshold=threshold)
