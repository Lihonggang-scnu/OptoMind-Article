"""Saved-Jacobian replay only: no new model, solver, or certified observation."""
import copy
import json

import pytest

from focused_research import decision_context as dc, family_optimization as fo, search_stage as st
from focused_research.tests.test_decision_context import GOALS, realistic_previous


def t034_p002_summary():
    # T034 case02, REQUEST r3-a6875425ad8d42ba803c, parent P002:
    # design:0dfa0da6805d956226e86249295b9c269130a4ed0247971be4bbc9df5382a2ea.
    # Complete normalized Jacobian: CASE02_QUALITY_REVIEW.json,
    # parent_action_child_audit[0]. Values/bounds: the actual request above.
    # This replays those saved numbers, not the historical certified archive.
    names = ['dH', 'dL', 'dC1', 'dC2']
    row = {'parameters_nm': dict(zip(names, [85., 148.30703694325155, 550., 550.])),
        'metric_jacobian_wrt_normalized_parameters': [
            [0.0013870135047967156, 0.0005033902132708982,
             0.005985350898379451, 0.0062055534841278116],
            [-0.10327362026277613, -0.041632459628660946,
             -0.01083076901431185, -0.003215842098587008]]}
    bounds = [[85., 95.], [135., 155.], [500., 550.], [500., 550.]]
    before = copy.deepcopy(row)
    summary = fo.summarize_local_derivatives(row, names, bounds, GOALS)
    assert row == before
    return summary


def by_name(facts):
    return {v['name']: v for v in facts['variables']}


def test_t034_replay_retains_both_goal_signs_and_correct_units():
    summary = t034_p002_summary()
    rankings = [[v['name'] for v in g['top_variables']] for g in summary['goal_sensitivities']]
    assert rankings == [['dC2', 'dC1', 'dH'], ['dH', 'dL', 'dC1']]
    legacy = copy.deepcopy(summary)
    legacy.pop('selected_variable_derivatives')
    old = dc.local_direction_facts(legacy, GOALS)
    assert by_name(old)['dL']['derivative_per_nm'] == {'G2': -0.0020816229814330472}
    assert by_name(old)['dL']['missing_goal_derivatives'] == ['G1']
    assert by_name(old)['dC2']['missing_goal_derivatives'] == ['G2']
    before = copy.deepcopy(summary)
    facts = dc.local_direction_facts(summary, GOALS)
    assert summary == before
    assert set(by_name(facts)) == set(by_name(old)) == {'dH', 'dL', 'dC1', 'dC2'}
    dl = by_name(facts)['dL']
    assert dl['derivative_per_nm']['G1'] == 0.0005033902132708982 / 20.
    assert dl['derivative_per_nm']['G2'] == -0.0020816229814330472
    assert dl['improvement_rate_normalized']['G1'] == pytest.approx(0.0005033902132708982)
    assert dl['improvement_rate_normalized']['G2'] == pytest.approx(-0.041632459628660946)
    assert not dl['missing_goal_derivatives']
    assert by_name(facts)['dC2']['derivative_per_nm']['G2'] == -0.003215842098587008 / 50.
    assert facts['derivative_coverage']['available_complete_coordinates'] == 4
    assert not facts['same_direction_opportunities']
    assert facts['additional_forward_design_rows'] == summary['additional_forward_design_rows'] == 0
    json.dumps(facts, allow_nan=False)


def test_top_k_union_stays_compact_and_preserves_zero_and_minimizing_goals():
    names = ['a', 'b', 'c', 'd', 'unselected', 'fixed']
    goals = [GOALS[0], {'objective_id': 'G2', 'sense': 'minimize'}]
    row = {'parameters_nm': dict.fromkeys(names, 15.),
        'metric_jacobian_wrt_normalized_parameters': [
            [8., 7., 0., 1., .1, 99.], [0., -1., -9., -10., -.1, 99.]]}
    summary = fo.summarize_local_derivatives(row, names, [[10., 20.]] * 5 + [[15., 15.]],
                                           goals, top_k=2)
    assert [[v['name'] for v in g['top_variables']] for g in summary['goal_sensitivities']] == [
        ['a', 'b'], ['d', 'c']]
    assert [v['name'] for v in summary['selected_variable_derivatives']] == ['a', 'b', 'c', 'd']
    facts = dc.local_direction_facts(summary, goals)
    assert len(facts['variables']) == 4
    assert all(set(v['derivative_per_nm']) == {'G1', 'G2'} for v in facts['variables'])
    assert by_name(facts)['a']['derivative_per_nm']['G2'] == 0.
    assert by_name(facts)['c']['derivative_per_nm']['G1'] == 0.
    assert by_name(facts)['d']['improvement_rate_normalized']['G2'] == 10.
    assert all(not v['missing_goal_derivatives'] for v in facts['variables'])


def test_absent_saved_values_remain_missing_and_unselected_rows_are_not_added():
    summary = t034_p002_summary()
    dl = next(v for v in summary['selected_variable_derivatives'] if v['name'] == 'dL')
    del dl['derivative_per_nm']['G1']
    unselected = copy.deepcopy(dl)
    unselected['name'] = 'not_top_k'
    unselected['derivative_per_nm']['G1'] = 0.
    summary['selected_variable_derivatives'].append(unselected)
    facts = dc.local_direction_facts(summary, GOALS)
    assert set(by_name(facts)) == {'dH', 'dL', 'dC1', 'dC2'}
    assert by_name(facts)['dL']['missing_goal_derivatives'] == ['G1']
    assert 'G1' not in by_name(facts)['dL']['derivative_per_nm']
    assert facts['derivative_coverage']['available_complete_coordinates'] == 3
    unavailable = fo.summarize_local_derivatives({'parameters_nm': {'dL': 145.}},
                                                ['dL'], [[135., 155.]], GOALS)
    assert unavailable['status'] == 'unknown'
    assert 'selected_variable_derivatives' not in unavailable
    assert dc.local_direction_facts(unavailable, GOALS)['variables'] == []


@pytest.mark.parametrize('field,value', [('current_value_nm', 149.), ('bounds_nm', [130., 155.]),
                                       ('derivative_per_nm', {'G1': 0., 'G2': 1.})])
def test_inconsistent_saved_vectors_are_not_silently_merged(field, value):
    summary = t034_p002_summary()
    dl = next(v for v in summary['selected_variable_derivatives'] if v['name'] == 'dL')
    dl[field] = value
    facts = dc.local_direction_facts(summary, GOALS)
    assert 'dL' not in by_name(facts)
    assert any('inconsistent saved coordinate' in w for w in facts['warnings'])


def test_nonfinite_supplement_is_not_converted_to_zero():
    summary = t034_p002_summary()
    dl = next(v for v in summary['selected_variable_derivatives'] if v['name'] == 'dL')
    dl['derivative_per_nm']['G1'] = float('nan')
    facts = dc.local_direction_facts(summary, GOALS)
    assert by_name(facts)['dL']['missing_goal_derivatives'] == ['G1']
    assert facts['warnings']
    json.dumps(facts, allow_nan=False)


@pytest.mark.parametrize('malformed', [None, {}, 'invalid', [None],
    [{'name': 'dL', 'derivative_per_nm': None}]])
def test_malformed_optional_vectors_preserve_legacy_unknowns(malformed):
    summary = t034_p002_summary()
    summary['selected_variable_derivatives'] = malformed
    facts = dc.local_direction_facts(summary, GOALS)
    assert facts['warnings']
    assert by_name(facts)['dL']['missing_goal_derivatives'] == ['G1']
    assert by_name(facts)['dC2']['missing_goal_derivatives'] == ['G2']
    assert facts['derivative_coverage']['available_complete_coordinates'] == 2


def test_revision_payload_only_changes_local_derivative_facts(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('No solver or additional observation is allowed')
    monkeypatch.setattr(fo, '_modules', forbidden)
    monkeypatch.setattr(fo, 'optimize_family', forbidden)
    previous, aliases = realistic_previous(1)
    previous['manifest_sha256'] = 'saved-snapshot'
    did = next(iter(previous['archive']['designs']))
    design = previous['archive']['designs'][did]
    previous['accepted']['routes'][0]['family'] = copy.deepcopy(design['family']['specification']['family'])
    previous['envelopes'][did] = {'route_id': 'R1',
        'tmm_task': copy.deepcopy(design['physical_input']),
        'numerical_search': {'local_derivative_summary': t034_p002_summary()}}
    before = copy.deepcopy(previous)
    current = st._revision_decision_view(previous, aliases)
    assert previous == before
    legacy = copy.deepcopy(previous)
    legacy['envelopes'][did]['numerical_search']['local_derivative_summary'].pop('selected_variable_derivatives')
    old = st._revision_decision_view(legacy, aliases)
    payload = current['user_payload']
    facts = payload['representative_decisions'][0]['local_direction_facts']
    assert by_name(facts)['dL']['missing_goal_derivatives'] == []
    comparable = copy.deepcopy(payload)
    comparable['representative_decisions'][0]['local_direction_facts'] = old['user_payload']['representative_decisions'][0]['local_direction_facts']
    assert comparable == old['user_payload']
    encoded = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    assert current['selection_report']['payload_utf8_bytes'] == len(encoded.encode('utf-8'))
    assert current['selection_report']['additional_forward_design_rows'] == 0
