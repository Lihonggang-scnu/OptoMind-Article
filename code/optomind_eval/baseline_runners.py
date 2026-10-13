"""Frozen finite-space baselines. No hidden-world imports or terminal scores.

The model library, action menu, prior and optimization pool are shared. These
are exact finite-pool baselines, not a claim about unrestricted optimization.
"""
from dataclasses import asdict
import numpy as np
from scipy.special import xlogy
from optomind_optics.harness.science.controller import decide_next, execute_round

ARMS = ('fixed_bayes', 'fixed_robust', 'fixed_measure_fit', 'entropy_design', 'entropy_cost_gated', 'task_evsi')


def entropy_gain(q, kernel):
    q = np.asarray(q, float)
    joint = q[:, None] * np.asarray(kernel)
    mass = joint.sum(axis=0)
    posterior = np.divide(joint, mass, out=np.zeros_like(joint), where=mass > 0)
    return float(-xlogy(q, q).sum() + (mass * xlogy(posterior, posterior).sum(axis=0)).sum())


def run_policy(arm, problem, prior, actions, acquire, *, budget):
    """Return design and full decision trajectory; acquire only accepts public actions."""
    if arm not in ARMS:
        raise ValueError('unknown evaluation arm')
    q = np.asarray(prior, float)
    trajectory = [{'event': 'start', 'arm': arm, 'prior': q.tolist(), 'problem_hash': problem.cache_key}]
    if arm == 'fixed_bayes':
        design = problem.solve(q).decision_id
    elif arm == 'fixed_robust':
        design = problem.solve_robust(np.eye(len(q))).decision_id
    elif arm == 'task_evsi':
        decision = decide_next(problem, q, actions, available_budget=budget,
                               final_validation_reserve=0, depth=1)
        trajectory.append({'event': 'valuation', 'decision': asdict(decision)})
        receipt = execute_round(problem, q, actions, decision, acquire,
                                current_context_hash=decision.context_hash)
        trajectory.append({'event': 'execution', 'receipt': asdict(receipt)})
        design = receipt.selected_design_id
    else:
        affordable = [a for a in actions if a.resource_cost <= budget]
        if arm == 'entropy_cost_gated':
            # Universal upper bound on measurement utility, not a fitted EVSI estimate.
            perfect_information_gain = problem.solve(q).risk - float(q @ problem.losses.min(axis=1))
            affordable = [a for a in affordable if a.loss_cost < perfect_information_gain
                          and entropy_gain(q, a.kernel) > 1e-12]
            trajectory.append({'event': 'classical_cost_gate', 'perfect_information_gain_upper_bound': perfect_information_gain,
                               'eligible_actions': [a.action_id for a in affordable]})
        if not affordable:
            design = problem.solve(q).decision_id
        else:
            gains = {a.action_id: entropy_gain(q, a.kernel) for a in affordable}
            action = (min(affordable, key=lambda a: (a.resource_cost, a.action_id)) if arm == 'fixed_measure_fit'
                      else max(affordable, key=lambda a: (gains[a.action_id] / a.resource_cost if arm == 'entropy_cost_gated'
                                                        else gains[a.action_id], -a.resource_cost)))
            label = acquire(action)
            column = action.observation_ids.index(label)
            q = q * np.asarray(action.kernel)[:, column]
            if q.sum() <= 0:
                raise ValueError('zero probability observation under frozen model')
            q /= q.sum()
            design = problem.solve(q).decision_id
            trajectory.append({'event': 'posterior', 'action': action.action_id, 'observation': label,
                               'prior_information_gains': gains, 'posterior': q.tolist()})
    trajectory.append({'event': 'terminal_choice', 'design_id': design})
    return design, trajectory
