import copy
import json

import numpy as np
import pytest

from optomind_optics.harness.science.research_belief import update_research_belief


def prediction():
    return {'belief_id': 'mechanisms', 'hypothesis_ids': ['A', 'B'], 'prior': [.5, .5],
            'intervention_id': 'change-angle', 'condition_id': 'sample-1-calibration-1',
            'outcome_ids': ['up', 'down'], 'kernel': [[.9, .1], [.1, .9]],
            'conditional_independence_group': 'independent-replicates-given-mechanism'}


def observation(identity='measurement-1', outcome='up'):
    return {'observation_id': identity, 'outcome_id': outcome,
            'intervention_id': 'change-angle', 'condition_id': 'sample-1-calibration-1'}


def test_numeric_bayes_update_and_predictive_uncertainty():
    result = update_research_belief(prediction(), observation())
    assert result['status'] == 'updated'
    assert result['posterior'] == pytest.approx([.9, .1])
    assert result['predictive_support']['posterior_predictive'] == pytest.approx({'up': .82, 'down': .18})
    assert result['remaining_uncertainty']['entropy_nats'] == pytest.approx(-.9*np.log(.9)-.1*np.log(.1))
    assert result['remaining_uncertainty']['outside_declared_hypotheses'] == 'unresolved'
    json.dumps(result, allow_nan=False)


def test_contradictory_evidence_restores_uncertainty():
    first = update_research_belief(prediction(), observation())
    second = update_research_belief(prediction(), observation('measurement-2', 'down'), first)
    assert second['posterior'] == pytest.approx([.5, .5])
    assert second['remaining_uncertainty']['entropy_nats'] > first['remaining_uncertainty']['entropy_nats']


def test_duplicate_is_idempotent_and_conflicting_reuse_unresolved():
    first = update_research_belief(prediction(), observation())
    saved = copy.deepcopy(first)
    replay = update_research_belief(prediction(), observation(), first)
    assert replay['status'] == 'duplicate_observation'
    assert replay['posterior'] == first['posterior']
    conflict = update_research_belief(prediction(), observation(outcome='down'), first)
    assert conflict['status'] == 'unresolved'
    assert conflict['reason'] == 'duplicate_observation_identity_conflict'
    assert conflict['posterior'] == first['posterior']
    assert first == saved


@pytest.mark.parametrize('prior', [[.4, .4], [-.1, 1.1], [float('nan'), .5], [1], None])
def test_invalid_prior_never_falls_back(prior):
    p = prediction(); p['prior'] = prior
    assert update_research_belief(p, observation())['status'] == 'unresolved'


@pytest.mark.parametrize('key', ['condition_id', 'intervention_id'])
def test_mismatched_conditions_do_not_consume_evidence(key):
    obs = observation(); obs[key] = 'different'
    result = update_research_belief(prediction(), obs)
    assert result['status'] == 'unresolved'
    assert result['consumed_observation_ids'] == []


def test_missing_probabilistic_contract_is_unresolved():
    assert update_research_belief({'hypothesis': 'improved objective proves A'}, observation())['posterior'] is None
    assert update_research_belief(None, None)['status'] == 'unresolved'
    p = prediction(); del p['kernel']
    assert update_research_belief(p, observation())['status'] == 'unresolved'


def gaussian_prediction():
    p = prediction()
    del p['kernel']; del p['outcome_ids']
    p.update(means=[[0.], [2.]], covariance=[[1.]], gaussian_validated=True)
    return p


def test_declared_gaussian_noise_gives_real_numeric_posterior():
    obs = observation(); obs['values'] = [0.]
    result = update_research_belief(gaussian_prediction(), obs)
    assert result['posterior'] == pytest.approx([1/(1+np.exp(-2)), 1/(1+np.exp(2))])
    assert result['predictive_support']['support_kind'] == 'probability_density'


@pytest.mark.parametrize('covariance', [[[0]], [[-1]], [[float('nan')]], [[1, 0], [0, 1]]])
def test_invalid_noise_is_unresolved(covariance):
    p = gaussian_prediction(); p['covariance'] = covariance
    obs = observation(); obs['values'] = [0.]
    assert update_research_belief(p, obs)['status'] == 'unresolved'


def test_unvalidated_noise_is_unresolved():
    p = gaussian_prediction(); p['gaussian_validated'] = False
    obs = observation(); obs['values'] = [0.]
    assert update_research_belief(p, obs)['status'] == 'unresolved'


def test_impossible_observation_does_not_fabricate_posterior():
    p = prediction(); p['kernel'] = [[1., 0.], [1., 0.]]
    result = update_research_belief(p, observation(outcome='down'))
    assert result['reason'] == 'observation_impossible_under_declared_model'
    assert result['posterior'] is None


def test_invalid_kernel_does_not_normalize_arbitrary_scores():
    p = prediction(); p['kernel'] = [[9., 1.], [1., 9.]]
    assert update_research_belief(p, observation())['status'] == 'unresolved'


def test_sequential_update_needs_explicit_independence():
    p = prediction(); del p['conditional_independence_group']
    first = update_research_belief(p, observation())
    second = update_research_belief(p, observation('measurement-2'), first)
    assert second['status'] == 'unresolved'
    assert second['posterior'] == first['posterior']


def test_changed_prior_or_hypotheses_cannot_reuse_posterior():
    first = update_research_belief(prediction(), observation())
    p = prediction(); p['prior'] = [.8, .2]
    result = update_research_belief(p, observation('measurement-2'), first)
    assert result['reason'] == 'previous_belief_binding_mismatch'


def test_action_utility_is_not_mechanistic_evidence():
    p = prediction(); obs = observation()
    base = update_research_belief(p, obs)
    obs['objective_improvement'] = 1000000
    obs['llm_confidence'] = .999999
    result = update_research_belief(p, obs)
    assert result['posterior'] == base['posterior']


def test_zero_probability_world_stays_zero_and_serializes():
    p = prediction(); p['prior'] = [1., 0.]; p['kernel'] = [[1., 0.], [0., 1.]]
    result = update_research_belief(p, observation())
    assert result['posterior'] == [1., 0.]
    json.dumps(result, allow_nan=False)


def test_ledger_default_keeps_action_utility_separate():
    from optomind_optics.harness.action_ledger import build_ledger_entry, update_belief
    entry = build_ledger_entry(action_id='action', hypothesis_id='route', rounds_used=1,
                               best_score_before=0., best_score_after=1., expected_delta=1.)
    assert entry.observed_effect.delta == 1.
    assert entry.belief_update.status == 'unresolved'
    assert entry.belief_update.after is None
    assert update_belief(.5, 1., 1.).after is None


def test_ledger_binds_complete_posterior_without_route_scalar():
    from optomind_optics.harness.action_ledger import build_ledger_entry, BeliefUpdate
    result = update_research_belief(prediction(), observation())
    entry = build_ledger_entry(action_id='action', hypothesis_id='route', rounds_used=1,
                               best_score_before=0., best_score_after=1., mechanism_update=result)
    assert entry.belief_update.rule == 'finite_model_bayes'
    assert entry.belief_update.mechanism_update['posterior'] == pytest.approx([.9, .1])
    assert entry.belief_update.after is None
    legacy = BeliefUpdate.model_validate({'before': .5, 'after': .6, 'rule': 'confirmed'})
    assert legacy.after == .6


def test_changed_physical_model_parameters_cannot_reuse_posterior():
    p = prediction()
    p['physical_prediction_binding'] = {'models': [
        {'hypothesis_id': 'A', 'model_id': 'linear', 'parameters': {'slope': 1.}},
        {'hypothesis_id': 'B', 'model_id': 'linear', 'parameters': {'slope': 2.}}]}
    first = update_research_belief(p, observation())
    changed = copy.deepcopy(p)
    changed['physical_prediction_binding']['models'][0]['parameters']['slope'] = 3.
    result = update_research_belief(changed, observation('measurement-2'), first)
    assert result['status'] == 'unresolved'
    assert result['reason'] == 'previous_belief_binding_mismatch'
    assert result['posterior'] == first['posterior']


def test_new_intervention_with_same_physical_models_can_update():
    p = prediction()
    p['physical_prediction_binding'] = {'models': [
        {'hypothesis_id': 'A', 'model_id': 'linear', 'parameters': {'slope': 1.}},
        {'hypothesis_id': 'B', 'model_id': 'linear', 'parameters': {'slope': 2.}}],
        'iteration_id': 'iteration-1', 'task_hash': 'task-1'}
    first = update_research_belief(p, observation())
    changed = copy.deepcopy(p)
    changed['intervention_id'] = 'new-intervention'
    changed['condition_id'] = 'new-condition'
    changed['physical_prediction_binding']['iteration_id'] = 'iteration-2'
    changed['physical_prediction_binding']['task_hash'] = 'task-2'
    obs = observation('measurement-2', 'down')
    obs.update(intervention_id='new-intervention', condition_id='new-condition')
    result = update_research_belief(changed, obs, first)
    assert result['status'] == 'updated'
    assert result['posterior'] == pytest.approx([.5, .5])


def test_changed_model_ir_binding_cannot_reuse_posterior():
    p = prediction()
    p['physical_prediction_binding'] = {'models': [{'model_id': 'same', 'model_ir_hash': 'original'}]}
    first = update_research_belief(p, observation())
    changed = copy.deepcopy(p)
    changed['physical_prediction_binding']['models'][0]['model_ir_hash'] = 'changed'
    result = update_research_belief(changed, observation('measurement-2'), first)
    assert result['reason'] == 'previous_belief_binding_mismatch'


def terminal_problem():
    from optomind_optics.harness.science.terminal_problem import TerminalDecisionProblem
    return TerminalDecisionProblem(('A', 'B'), ('design-A', 'design-B'), np.array([[0., 1.], [1., 0.]]),
        {key: key for key in ('task', 'objective', 'model', 'sample', 'material', 'solver', 'fidelity', 'loss', 'split')}, 'loss')


def research_action(cost=.01):
    return {'action_id': 'discriminate', 'world_ids': ['A', 'B'], 'observation_ids': ['a', 'b'],
            'kernel': [[1., 0.], [0., 1.]], 'cost': cost, 'cost_units': 'loss',
            'conditional_independence_group': 'independent-replicates-given-mechanism'}


def test_exact_evsi_selects_positive_value_using_actual_posterior():
    from optomind_optics.harness.science.research_belief import choose_research_action
    belief = update_research_belief(prediction(), observation())
    result = choose_research_action(terminal_problem(), belief, [research_action()])
    assert result['status'] == 'recommended'
    assert result['action_id'] == 'discriminate'
    assert result['current_risk'] == pytest.approx(.1)
    assert result['expected_posterior_risk'] == 0.
    assert result['expected_risk_including_cost'] == .01
    assert result['net_value'] == pytest.approx(.09)
    assert result['estimates'][0]['observation_continuations'] == {'a': 'design-A', 'b': 'design-B'}
    assert result['execution_authorized'] is False


@pytest.mark.parametrize('actions', [[], [research_action(.2)], [research_action(.1)]])
def test_exact_evsi_stops_without_positive_net_value(actions):
    from optomind_optics.harness.science.research_belief import choose_research_action
    belief = update_research_belief(prediction(), observation())
    result = choose_research_action(terminal_problem(), belief, actions)
    assert result['status'] == 'stop'
    assert result['action_id'] is None
    assert result['net_value'] == 0.


@pytest.mark.parametrize('change', [{'cost_units': 'dollars'}, {'world_ids': ['B', 'A']},
    {'kernel': [[2., 0.], [0., 2.]]}, {'cost': -1}, {'conditional_independence_group': None}])
def test_exact_evsi_rejects_ungrounded_action_inputs(change):
    from optomind_optics.harness.science.research_belief import choose_research_action
    belief = update_research_belief(prediction(), observation())
    result = choose_research_action(terminal_problem(), belief, [research_action() | change])
    assert result['status'] == 'unresolved'
    assert result['action_id'] is None


def test_exact_evsi_accepts_explicit_history_conditioned_kernel():
    from optomind_optics.harness.science.research_belief import choose_research_action
    belief = update_research_belief(prediction(), observation())
    action = research_action()
    action.pop('conditional_independence_group')
    action['conditioned_on_observation_ids'] = ['measurement-1']
    assert choose_research_action(terminal_problem(), belief, [action])['status'] == 'recommended'
