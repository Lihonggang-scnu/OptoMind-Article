from __future__ import annotations

from dataclasses import dataclass
import itertools
from typing import Callable, Sequence

import numpy as np

from .evsi import ActionValueReport, exact_evsi, observation_kernel
from .terminal_problem import TerminalDecisionProblem, canonical_hash, probability_vector


@dataclass(frozen=True)
class MeasurementAction:
    action_id: str
    observation_ids: tuple[str, ...]
    kernel: tuple[tuple[float, ...], ...]
    loss_cost: float
    resource_cost: float
    conditional_independence_group: str | None = None


@dataclass(frozen=True)
class DecisionCommit:
    context_hash: str
    execution_binding_hash: str
    selected_actions: tuple[str, ...]
    estimates: tuple[tuple[tuple[str, ...], ActionValueReport], ...]
    selected_policy_hash: str | None
    reserved_resource_cost: float
    reason: str
    unknown_action_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResearchIterationReceipt:
    decision: DecisionCommit
    acquired_observations: tuple[tuple[str, str], ...]
    selected_design_id: str
    outcome: str


def _joint_action(actions: Sequence[MeasurementAction], n_worlds: int):
    kernels = [observation_kernel(action.kernel, n_worlds) for action in actions]
    combinations = tuple(itertools.product(*(range(k.shape[1]) for k in kernels)))
    kernel = np.column_stack([np.prod(np.stack([k[:, y] for k, y in zip(kernels, combination)]), axis=0)
                              for combination in combinations])
    labels = tuple(canonical_hash([action.observation_ids[y] for action, y in zip(actions, combination)]) for combination in combinations)
    return kernel, labels


def decide_next(problem: TerminalDecisionProblem, probabilities: Sequence[float], actions: Sequence[MeasurementAction], *,
                available_budget: float, final_validation_reserve: float, depth: int = 2,
                unknown_action_ids: Sequence[str] = ()) -> DecisionCommit:
    q = probability_vector(probabilities, len(problem.world_ids))
    if depth < 1 or depth > 2 or not np.isfinite(available_budget) or not 0 <= final_validation_reserve <= available_budget:
        raise ValueError('invalid horizon or explicit final-validation reserve')
    if len({a.action_id for a in actions}) != len(actions):
        raise ValueError('action identities must be unique')
    for action in actions:
        if not action.action_id or not np.isfinite([action.loss_cost, action.resource_cost]).all() or min(action.loss_cost, action.resource_cost) < 0:
            raise ValueError('actions require finite nonnegative costs and identities')
        k = observation_kernel(action.kernel, len(q))
        if len(action.observation_ids) != k.shape[1] or len(set(action.observation_ids)) != k.shape[1]:
            raise ValueError('observation support does not match action kernel')
    context = canonical_hash({'problem': problem.cache_key, 'q': q.tolist(), 'actions': [a.__dict__ for a in actions],
                              'budget': available_budget, 'reserve': final_validation_reserve, 'depth': depth,
                              'unknown': tuple(unknown_action_ids)})
    binding = canonical_hash({"problem": problem.cache_key, "q": q.tolist(), "actions": [a.__dict__ for a in actions]})
    estimates = []
    best = None
    best_net = 0.0
    for length in range(1, depth + 1):
        for candidate in itertools.combinations(actions, length):
            resource = sum(action.resource_cost for action in candidate)
            if resource > available_budget - final_validation_reserve:
                continue
            if length > 1:
                groups = {action.conditional_independence_group for action in candidate}
                if None in groups or len(groups) != 1:
                    continue
            kernel, labels = _joint_action(candidate, len(q))
            report = exact_evsi(problem, q, kernel, labels, cost=sum(a.loss_cost for a in candidate), cost_units=problem.units)
            estimates.append((tuple(a.action_id for a in candidate), report))
            if report.net_value_interval[0] > best_net:
                best, best_net = (candidate, report, resource), report.net_value_interval[0]
    if best is None:
        return DecisionCommit(context, binding, (), tuple(estimates), None, 0,
                              'unvalued_actions_require_bounded_exploration' if unknown_action_ids else 'no_positive_value_within_budget', tuple(unknown_action_ids))
    candidate, report, resource = best
    return DecisionCommit(context, binding, tuple(a.action_id for a in candidate), tuple(estimates), report.policy.policy_hash,
                          resource, 'positive_task_value', tuple(unknown_action_ids))


def execute_round(problem: TerminalDecisionProblem, probabilities: Sequence[float], actions: Sequence[MeasurementAction],
                  decision: DecisionCommit, acquire: Callable[[MeasurementAction], str], *,
                  current_context_hash: str) -> ResearchIterationReceipt:
    binding = canonical_hash({"problem": problem.cache_key, "q": probability_vector(probabilities, len(problem.world_ids)).tolist(), "actions": [a.__dict__ for a in actions]})
    if binding != decision.execution_binding_hash:
        raise ValueError("execution inputs differ from valued inputs")
    if current_context_hash != decision.context_hash:
        raise ValueError('stale decision must be revalued before execution')
    if not decision.selected_actions:
        status = 'pending_unknown_value' if decision.unknown_action_ids else 'stopped'
        return ResearchIterationReceipt(decision, (), problem.solve(probabilities).decision_id, status)
    by_id = {action.action_id: action for action in actions}
    selected = tuple(by_id[key] for key in decision.selected_actions)
    report = next(report for ids, report in decision.estimates if ids == decision.selected_actions)
    if report.policy.policy_hash != decision.selected_policy_hash or report.policy.problem_hash != problem.cache_key:
        raise ValueError('committed continuation was modified')
    acquired = []
    for action in selected:
        observation_id = acquire(action)
        if observation_id not in action.observation_ids:
            raise ValueError('acquisition outside committed observation support')
        acquired.append((action.action_id, observation_id))
    outcome = canonical_hash([value for _, value in acquired])
    design = report.policy.choose(outcome, problem_hash=problem.cache_key, policy_hash=decision.selected_policy_hash)
    return ResearchIterationReceipt(decision, tuple(acquired), design, 'committed_continuation_completed')


def route_research_state(state: str) -> str:
    """Shared C08→C13 routing, without conflating residuals with mechanism evidence."""
    routes = {
        "identity_invalid": "stop_identity",
        "numerical_failure": "refine_computation",
        "noise_unvalidated": "calibrate_noise",
        "parameter_fit_required": "refit_parameters",
        "uncalibrated": "bounded_unknown_exploration",
        "model_family_inadequate": "propose_structure",
        "nonidentifiable": "identify_task_relevant_direction",
        "nonidentifiable_decision_equivalent": "stop_equivalent",
        "not_rejected_within_calibration_scope": "value_experiment_or_stop",
    }
    if state not in routes:
        raise ValueError("unknown adequacy state")
    return routes[state]
