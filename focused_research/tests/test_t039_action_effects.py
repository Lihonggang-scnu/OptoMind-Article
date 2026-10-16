"""Pure saved-derivative arithmetic; no solver, model, or new observation."""
import copy
import json

import pytest

from focused_research import decision_context as dc
from focused_research.tests.test_decision_context import realistic_previous


GOALS = [{'objective_id': 'G1', 'sense': 'maximize'},
         {'objective_id': 'G2', 'sense': 'minimize'},
         {'objective_id': 'G3', 'sense': 'minimize'}]


def saved_coordinate(name, derivatives, value, bounds):
    return {'status': 'local_derivative_uncertified',
            'goal_gradient_conflicts': {'variable_count': 1},
            'goal_sensitivities': [
                {'objective_id': gid, 'top_variables': [
                    {'name': name, 'derivative_per_nm': derivative,
                     'current_value_nm': value, 'bounds_nm': bounds}]}
                for gid, derivative in derivatives.items()]}


@pytest.mark.parametrize('derivative', [-2., 2., -1e-30, 1e-30])
@pytest.mark.parametrize('sense', ['maximize', 'minimize'])
@pytest.mark.parametrize('direction,sign', [('decrease', -1), ('increase', 1)])
def test_explicit_coordinate_signs(derivative, sense, direction, sign):
    facts = dc.local_direction_facts(saved_coordinate('d', {'G': derivative}, 5., [1., 9.]),
                                    [{'objective_id': 'G', 'sense': sense}])
    action = facts['variables'][0]['action_effects'][direction]
    assert action['feasible_in_recorded_bounds'] is True
    assert action['goals']['G'] == {
        'metric_delta_direction': 'increase' if sign * derivative > 0 else 'decrease',
        'effect_on_goal': 'helps' if (sign * derivative > 0) == (sense == 'maximize') else 'hurts'}


@pytest.mark.parametrize('zero', [0., -0.])
def test_missing_and_zero_remain_unknown_without_erasing_known_goals(zero):
    facts = dc.local_direction_facts(saved_coordinate('d', {'G1': zero, 'G2': -2.}, 5., [1., 9.]), GOALS)
    row = facts['variables'][0]
    assert row['missing_goal_derivatives'] == ['G3']
    for action in row['action_effects'].values():
        assert action['goals']['G1'] == {'metric_delta_direction': 'zero_first_order', 'effect_on_goal': 'unknown'}
        assert action['goals']['G3'] == {'metric_delta_direction': 'unknown', 'effect_on_goal': 'unknown'}
    assert row['action_effects']['decrease']['goals']['G2']['effect_on_goal'] == 'hurts'


# Exact saved T038 REQUEST r3-2cc300bce79b4054b73c, representative_decisions.
# P002 design:0e933543f2f1eea8951459b223e945999c92de7de7992b8cfb29cd81e17cb3ef
# P008 design:634e8d8320d63d9622e95c14e52dabc38ea0b0ee9f44e7d095f3e891e828cd23
@pytest.mark.parametrize('parent,name,derivatives,value,bounds,directions,effects,increase_feasible', [
    ('P002', 'dH1', [-1.9016628175271527e-6, -0.005757579421262278, -0.0005397597518501094],
     50., [40., 60.], ['increase', 'increase', 'increase'], ['helps', 'hurts', 'hurts'], True),
    ('P008', 'dH1', [-3.854081779297095e-7, 0.002332240733365445, -0.007818096897776286],
     60., [40., 60.], ['increase', 'decrease', 'increase'], ['helps', 'helps', 'hurts'], False),
    ('P008', 'dCav', [3.760354590212691e-8, -0.0006358411735257418, -0.001640259394912255],
     150., [100., 150.], ['decrease', 'increase', 'increase'], ['hurts', 'hurts', 'hurts'], False),
])
def test_t038_three_saved_sign_regressions(parent, name, derivatives, value, bounds,
                                          directions, effects, increase_feasible):
    saved = saved_coordinate(name, dict(zip(('G1', 'G2', 'G3'), derivatives)), value, bounds)
    before = copy.deepcopy(saved)
    facts = dc.local_direction_facts(saved, GOALS)
    assert saved == before
    row = facts['variables'][0]
    assert row['derivative_per_nm'] == dict(zip(('G1', 'G2', 'G3'), derivatives))
    decrease = row['action_effects']['decrease']
    assert decrease['feasible_in_recorded_bounds'] is True
    assert row['action_effects']['increase']['feasible_in_recorded_bounds'] is increase_feasible
    assert [v['metric_delta_direction'] for v in decrease['goals'].values()] == directions
    assert [v['effect_on_goal'] for v in decrease['goals'].values()] == effects
    assert facts['joint_direction']['status'] == 'unsupported'
    assert 'assessment was not performed' in facts['joint_direction']['detail']
    assert 'not evidence' in facts['joint_direction']['detail']
    assert facts['additional_forward_design_rows'] == 0
    assert 'others fixed' in ' '.join(facts['limits'])
    assert 'do not identify individual layer effects' in ' '.join(facts['limits'])
    assert dc.local_direction_facts(saved, GOALS) == facts
    json.dumps(facts, allow_nan=False)


def test_search_window_feasibility_does_not_change_hypothetical_effect():
    saved = saved_coordinate('d', {'G1': 1.}, 5., [5., 9.])
    saved.update(bounds_role='search_policy_bounds', original_family_bounds_nm={'d': [1., 10.]})
    facts = dc.local_direction_facts(saved, GOALS[:1])
    row = facts['variables'][0]
    assert row['action_effects']['decrease']['feasible_in_recorded_bounds'] is False
    assert row['action_effects']['decrease']['goals']['G1']['effect_on_goal'] == 'hurts'
    assert facts['direction_feasibility_scope'] == 'search_policy_bounds'
    assert facts['original_family_bounds_nm'] == {'d': [1., 10.]}


@pytest.mark.parametrize('invalid', [float('nan'), float('inf'), -float('inf')])
def test_nonfinite_saved_derivatives_are_not_fabricated(invalid):
    facts = dc.local_direction_facts(saved_coordinate('d', {'G1': invalid, 'G2': 2.}, 5., [1., 9.]), GOALS)
    assert facts['warnings']
    assert facts['variables'][0]['action_effects']['increase']['goals']['G1']['effect_on_goal'] == 'unknown'
    json.dumps(facts, allow_nan=False)


def test_context_bytes_and_no_additional_evaluation():
    previous, aliases = realistic_previous()
    before = copy.deepcopy(previous)
    calls = []
    def saved_only(envelope, design_id):
        calls.append(design_id)
        return envelope
    context = dc.build_decision_context(previous, aliases, saved_only)
    payload = context['user_payload']
    assert previous == before
    assert calls == context['selection_report']['representative_design_ids']
    assert len(calls) == 8
    assert context['selection_report']['additional_forward_design_rows'] == 0
    stripped = copy.deepcopy(payload)
    for detail in stripped['representative_decisions']:
        assert detail['local_direction_facts']['additional_forward_design_rows'] == 0
        for row in detail['local_direction_facts']['variables']:
            assert set(row.pop('action_effects')) == {'increase', 'decrease'}
    size = lambda value: len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    assert size(payload) == context['selection_report']['payload_utf8_bytes']
    assert 0 < size(payload) - size(stripped) < 10000
    assert 'token_count' not in context['selection_report']
