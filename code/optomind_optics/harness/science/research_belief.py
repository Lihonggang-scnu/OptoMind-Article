from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy

import numpy as np
from scipy.special import logsumexp
from scipy.stats import multivariate_normal

from .evsi import observation_kernel
from .terminal_problem import canonical_hash, probability_vector


MAX_HYPOTHESES = 128
MAX_OBSERVATION_DIMENSION = 64
SCOPE = 'conditional_on_declared_finite_hypotheses_prior_and_observation_model'


def _identity(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'missing_or_invalid_{name}')
    return value


def _unresolved(reason, previous):
    result = deepcopy(dict(previous)) if isinstance(previous, Mapping) else {}
    result.update(status='unresolved', reason=reason, updated=False, belief_scope=SCOPE)
    result.setdefault('posterior', None)
    result.setdefault('consumed_observation_ids', [])
    result.setdefault('predictive_support', None)
    result.setdefault('remaining_uncertainty', None)
    return result


def update_research_belief(prediction, observation, previous=None):
    try:
        return _update(prediction, observation, previous)
    except (ValueError, TypeError, KeyError, OverflowError, np.linalg.LinAlgError) as exc:
        return _unresolved(str(exc), previous)


def _update(prediction, observation, previous):
    if not isinstance(prediction, Mapping) or not isinstance(observation, Mapping):
        raise ValueError('explicit_prediction_and_observation_required')
    belief_id = _identity(prediction.get('belief_id'), 'belief_id')
    hypotheses = prediction.get('hypothesis_ids')
    if not isinstance(hypotheses, (list, tuple)) or not 2 <= len(hypotheses) <= MAX_HYPOTHESES:
        raise ValueError('requires_two_to_128_explicit_competing_hypotheses')
    hypotheses = [_identity(value, 'hypothesis_id') for value in hypotheses]
    if len(set(hypotheses)) != len(hypotheses):
        raise ValueError('duplicate_hypothesis_id')
    prior = probability_vector(prediction['prior'], len(hypotheses))
    physical_binding = prediction.get('physical_prediction_binding') or {}
    if not isinstance(physical_binding, Mapping):
        raise ValueError('invalid_physical_prediction_binding')
    definitions = physical_binding.get('models')
    binding = canonical_hash({'belief_id': belief_id, 'hypothesis_ids': hypotheses, 'prior': prior.tolist(),
                              'physical_model_definitions': definitions})
    observation_id = _identity(observation.get('observation_id'), 'observation_id')
    for key in ('intervention_id', 'condition_id'):
        if _identity(prediction.get(key), key) != _identity(observation.get(key), key):
            raise ValueError(f'{key}_mismatch')
    if observation.get('derived_from'):
        raise ValueError('derived_observation_is_not_new_evidence')
    group = prediction.get('conditional_independence_group')
    if group is not None:
        _identity(group, 'conditional_independence_group')
    observation_hash = canonical_hash(dict(observation))
    prediction_hash = canonical_hash(dict(prediction))
    consumed = {}
    probabilities = prior
    if previous is not None:
        if not isinstance(previous, Mapping) or previous.get('belief_binding') != binding:
            raise ValueError('previous_belief_binding_mismatch')
        probabilities = probability_vector(previous['posterior'], len(hypotheses))
        consumed = dict(previous['observation_bindings'])
        if observation_id in consumed:
            if consumed[observation_id] != {'observation_hash': observation_hash, 'prediction_hash': prediction_hash}:
                raise ValueError('duplicate_observation_identity_conflict')
            result = deepcopy(dict(previous))
            result.update(status='duplicate_observation', updated=False, reason='observation_already_consumed')
            return result
        if consumed and (not group or group != previous.get('conditional_independence_group')):
            raise ValueError('multiple_observations_require_explicit_conditional_independence')
    if 'kernel' in prediction:
        if 'means' in prediction or 'covariance' in prediction:
            raise ValueError('ambiguous_observation_model')
        kernel = observation_kernel(prediction['kernel'], len(hypotheses))
        outcomes = prediction.get('outcome_ids')
        if not isinstance(outcomes, (list, tuple)) or len(outcomes) != kernel.shape[1] or len(outcomes) > MAX_OBSERVATION_DIMENSION:
            raise ValueError('outcome_ids_must_match_bounded_kernel')
        outcomes = [_identity(value, 'outcome_id') for value in outcomes]
        if len(set(outcomes)) != len(outcomes):
            raise ValueError('duplicate_outcome_id')
        outcome = _identity(observation.get('outcome_id'), 'outcome_id')
        if outcome not in outcomes:
            raise ValueError('observation_outside_declared_support')
        likelihood = kernel[:, outcomes.index(outcome)]
        with np.errstate(divide='ignore'):
            log_likelihood = np.log(likelihood)
        model_kind = 'normalized_finite_observation_kernel'
    else:
        if prediction.get('gaussian_validated') is not True:
            raise ValueError('explicit_validated_noise_or_normalized_kernel_required')
        means = np.asarray(prediction['means'], dtype=float)
        values = np.asarray(observation['values'], dtype=float)
        covariance = np.asarray(prediction['covariance'], dtype=float)
        if values.ndim != 1 or not 1 <= len(values) <= MAX_OBSERVATION_DIMENSION or means.shape != (len(hypotheses), len(values)):
            raise ValueError('bounded_gaussian_prediction_dimensions_mismatch')
        if not np.isfinite(means).all() or not np.isfinite(values).all():
            raise ValueError('nonfinite_prediction_or_observation')
        if covariance.shape != (len(values), len(values)) or not np.isfinite(covariance).all() or not np.allclose(covariance, covariance.T, rtol=0, atol=1e-12):
            raise ValueError('invalid_noise_covariance')
        np.linalg.cholesky(covariance)
        log_likelihood = np.array([multivariate_normal.logpdf(values, mean=mean, cov=covariance) for mean in means])
        model_kind = 'declared_validated_gaussian'
    with np.errstate(divide='ignore', invalid='ignore'):
        log_joint = np.log(probabilities) + log_likelihood
    log_support = float(logsumexp(log_joint))
    if not np.isfinite(log_support):
        raise ValueError('observation_impossible_under_declared_model')
    posterior = probability_vector(np.exp(log_joint - log_support), len(hypotheses))
    positive = posterior[posterior > 0]
    support = {'model': model_kind, 'log_prior_predictive_support': log_support,
               'support_kind': 'probability_mass' if 'kernel' in prediction else 'probability_density',
               'per_hypothesis_log_likelihood': [float(v) if np.isfinite(v) else None for v in log_likelihood],
               'zero_likelihood_hypothesis_ids': [h for h, v in zip(hypotheses, log_likelihood) if np.isneginf(v)]}
    if 'kernel' in prediction:
        support['posterior_predictive'] = dict(zip(outcomes, map(float, posterior @ kernel)))
    else:
        mean = posterior @ means
        centered = means - mean
        support['posterior_predictive_mean'] = mean.tolist()
        support['posterior_predictive_covariance'] = (covariance + (centered * posterior[:, None]).T @ centered).tolist()
    consumed[observation_id] = {'observation_hash': observation_hash, 'prediction_hash': prediction_hash}
    return {'status': 'updated', 'updated': True, 'reason': None, 'belief_scope': SCOPE,
            'belief_id': belief_id, 'belief_binding': binding, 'hypothesis_ids': hypotheses,
            'intervention_id': prediction['intervention_id'], 'condition_id': prediction['condition_id'],
            'prior': prior.tolist(), 'posterior': posterior.tolist(),
            'conditional_independence_group': group,
            'consumed_observation_ids': list(consumed), 'observation_bindings': consumed,
            'predictive_support': support,
            'remaining_uncertainty': {'entropy_nats': float(-(positive * np.log(positive)).sum()),
                                      'effective_hypothesis_count': float(1 / (posterior @ posterior)),
                                      'outside_declared_hypotheses': 'unresolved'},
            'mechanism_claim': 'not_established_by_conditional_posterior',
            'empirical_action_utility': 'separate_not_used_as_likelihood'}


def choose_research_action(problem, belief_state, actions):
    from .evsi import exact_evsi
    from .terminal_problem import TerminalDecisionProblem

    scope = 'one_step_exact_evsi_on_declared_finite_worlds_designs_and_kernels'
    try:
        if not isinstance(problem, TerminalDecisionProblem) or not isinstance(belief_state, Mapping):
            raise ValueError('explicit_terminal_problem_and_belief_required')
        if belief_state.get('status') not in ('updated', 'duplicate_observation'):
            raise ValueError('resolved_conditional_belief_required')
        if tuple(belief_state.get('hypothesis_ids', ())) != problem.world_ids:
            raise ValueError('terminal_worlds_do_not_match_belief_hypotheses')
        probabilities = probability_vector(belief_state['posterior'], len(problem.world_ids))
        if not isinstance(actions, (list, tuple)) or len(actions) > 128:
            raise ValueError('explicit_bounded_candidate_action_list_required')
        current = problem.solve(probabilities)
        estimates = []
        seen = set()
        selected = None
        consumed = tuple(belief_state.get('consumed_observation_ids', ()))
        for action in actions:
            if not isinstance(action, Mapping):
                raise ValueError('explicit_action_contract_required')
            identity = _identity(action.get('action_id'), 'action_id')
            if identity in seen:
                raise ValueError('duplicate_action_id')
            seen.add(identity)
            if tuple(action.get('world_ids', ())) != problem.world_ids:
                raise ValueError('action_kernel_world_order_mismatch')
            if action.get('cost_units') != problem.units:
                raise ValueError('action_cost_must_be_declared_in_terminal_loss_units')
            if consumed:
                group = action.get('conditional_independence_group')
                independent = bool(group) and group == belief_state.get('conditional_independence_group')
                conditioned = tuple(action.get('conditioned_on_observation_ids', ())) == consumed
                if not independent and not conditioned:
                    raise ValueError('action_kernel_requires_history_conditioning_or_declared_independence')
            outcomes = action['observation_ids']
            if not isinstance(outcomes, (list, tuple)) or not 1 <= len(outcomes) <= MAX_OBSERVATION_DIMENSION:
                raise ValueError('bounded_declared_action_outcomes_required')
            for outcome in outcomes:
                _identity(outcome, 'action_outcome_id')
            report = exact_evsi(problem, probabilities, action['kernel'], outcomes,
                                cost=action['cost'], cost_units=action['cost_units'])
            estimate = {'action_id': identity, 'current_risk': report.prior_risk,
                        'expected_posterior_risk': report.posterior_risk,
                        'cost': report.cost, 'units': report.units,
                        'expected_risk_including_cost': report.posterior_risk + report.cost,
                        'net_value': report.net_value_interval[0],
                        'continuation_policy_hash': report.policy.policy_hash,
                        'observation_continuations': dict(zip(report.policy.observation_ids, report.policy.design_ids))}
            estimates.append(estimate)
            if estimate['net_value'] > 1e-12 and (selected is None or estimate['net_value'] > selected['net_value'] + 1e-12):
                selected = estimate
        return {'status': 'recommended' if selected else 'stop',
                'action_id': selected['action_id'] if selected else None,
                'current_terminal_design_id': current.decision_id,
                'current_risk': current.risk,
                'expected_posterior_risk': selected['expected_posterior_risk'] if selected else current.risk,
                'expected_risk_including_cost': selected['expected_risk_including_cost'] if selected else current.risk,
                'net_value': selected['net_value'] if selected else 0.,
                'units': problem.units, 'problem_hash': problem.cache_key,
                'belief_binding': belief_state.get('belief_binding'),
                'estimates': estimates, 'scope': scope,
                'execution_authorized': False,
                'resource_feasibility': 'not_evaluated_requires_separate_execution_budget_gate'}
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        return {'status': 'unresolved', 'action_id': None, 'reason': str(exc), 'scope': scope,
                'execution_authorized': False}
