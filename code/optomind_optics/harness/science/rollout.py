"""Exact bounded adaptive policies over an explicitly finite observation model.

This is not a hidden-world sampler. Action kernels and losses are the legal
pre-action model. Distinct acquisitions may be combined only when their shared
conditional-independence declaration is explicit. Existing fixed-bundle policy
semantics in controller.py are deliberately unchanged.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable, Sequence
import numpy as np

from .controller import MeasurementAction
from .evsi import observation_kernel
from .terminal_problem import canonical_hash, probability_vector


@dataclass(frozen=True)
class AdaptiveNode:
    action_id: str | None
    terminal_design_id: str
    probabilities: tuple[float, ...]
    expected_loss: float
    expected_resource_cost: float
    maximum_resource_cost: float
    # outcome, probability, committed continuation
    branches: tuple[tuple[str, float, 'AdaptiveNode'], ...] = ()


@dataclass(frozen=True)
class AdaptivePlan:
    problem_hash: str
    input_hash: str
    root: AdaptiveNode
    prior_risk: float
    available_budget: float
    final_validation_reserve: float
    depth: int
    commitment_mode: str = 'finite_bayesian_adaptive_precommit'

    @property
    def policy_hash(self):
        return canonical_hash(asdict(self))

    def choose(self, observation_history=()):
        """Return the committed next node (action_id=None means terminal stop)."""
        node = self.root
        for pair in observation_history:
            if len(pair) != 2 or node.action_id != pair[0] or node.action_id is None:
                raise ValueError("history is not a committed adaptive prefix")
            matches = [(mass, child) for label, mass, child in node.branches if label == pair[1]]
            if len(matches) != 1 or matches[0][0] <= 0:
                raise ValueError("observation impossible or outside committed support")
            node = matches[0][1]
        return node

    @property
    def net_value(self):
        return self.prior_risk - self.root.expected_loss


@dataclass(frozen=True)
class AdaptiveReceipt:
    policy_hash: str
    observations: tuple[tuple[str, str], ...]
    selected_design_id: str
    resource_cost: float
    loss_cost: float
    status: str = 'committed_adaptive_continuation_completed'


def _inputs(problem, probabilities, actions, available_budget, final_validation_reserve, depth):
    q = probability_vector(probabilities, len(problem.world_ids))
    actions = tuple(actions)
    if depth not in (1, 2) or isinstance(depth, bool):
        raise ValueError('adaptive horizon must be one or two')
    if not np.isfinite([available_budget, final_validation_reserve]).all() or not 0 <= final_validation_reserve <= available_budget:
        raise ValueError('finite budget and explicit final-validation reserve required')
    if len({a.action_id for a in actions}) != len(actions):
        raise ValueError('action identities must be unique')
    for action in actions:
        if not action.action_id or not np.isfinite([action.resource_cost, action.loss_cost]).all() or min(action.resource_cost, action.loss_cost) < 0:
            raise ValueError('invalid action identity or costs')
        if action.conditional_independence_group is not None and (not isinstance(action.conditional_independence_group, str) or not action.conditional_independence_group):
            raise ValueError("conditional independence declaration must be a nonempty identifier")
        k = observation_kernel(action.kernel, len(q))
        if len(action.observation_ids) != k.shape[1] or len(set(action.observation_ids)) != k.shape[1]:
            raise ValueError('observation support differs from kernel')
    binding = canonical_hash({'problem': problem.cache_key, 'probabilities': q.tolist(),
                              'actions': [asdict(a) for a in actions], 'available_budget': float(available_budget),
                              'final_validation_reserve': float(final_validation_reserve), 'depth': depth})
    return q, actions, binding


def plan_adaptive(problem, probabilities: Sequence[float], actions: Sequence[MeasurementAction], *,
                  available_budget: float, final_validation_reserve: float, depth: int = 2) -> AdaptivePlan:
    q, actions, binding = _inputs(problem, probabilities, actions, available_budget, final_validation_reserve, depth)
    kernels = {a.action_id: observation_kernel(a.kernel, len(q)) for a in actions}

    def solve(p, remaining, budget, horizon, independence_group=None):
        stop = problem.solve(p)
        best = AdaptiveNode(None, stop.decision_id, tuple(float(x) for x in p), stop.risk, 0., 0.)
        if not horizon:
            return best
        for action in remaining:
            if action.resource_cost > budget:
                continue
            if independence_group is not None and action.conditional_independence_group != independence_group:
                continue
            joint = p[:, None] * kernels[action.action_id]
            masses = joint.sum(axis=0)
            children = []
            expected = action.loss_cost
            expected_resource = action.resource_cost
            maximum_resource = action.resource_cost
            rest = tuple(a for a in remaining if a.action_id != action.action_id)
            # No declaration means this action is valid singly, not that
            # multiple observations are independent by default.
            next_horizon = horizon - 1 if action.conditional_independence_group is not None else 0
            for index, mass in enumerate(masses):
                posterior = joint[:, index] / mass if mass > 0 else p
                child = solve(posterior, rest, budget-action.resource_cost, next_horizon,
                              action.conditional_independence_group) if mass > 0 else AdaptiveNode(
                                  None, stop.decision_id, tuple(float(x) for x in p), stop.risk, 0., 0.)
                children.append((action.observation_ids[index], float(mass), child))
                expected += float(mass) * child.expected_loss
                expected_resource += float(mass) * child.expected_resource_cost
                if mass > 0:
                    maximum_resource = max(maximum_resource, action.resource_cost + child.maximum_resource_cost)
            # Exact ties retain stop or the earlier declared action. The
            # tolerance suppresses floating-point fake positive value.
            if expected < best.expected_loss - 1e-12:
                best = AdaptiveNode(action.action_id, stop.decision_id, tuple(float(x) for x in p),
                                    float(expected), float(expected_resource), float(maximum_resource), tuple(children))
        return best

    root = solve(q, actions, available_budget-final_validation_reserve, depth)
    return AdaptivePlan(problem.cache_key, binding, root, problem.solve(q).risk,
                        float(available_budget), float(final_validation_reserve), depth)


def adaptive_plan_from_dict(payload):
    """Parse a saved tree; execution still recomputes and verifies its binding."""
    def node(row):
        data = dict(row)
        data['probabilities'] = tuple(data['probabilities'])
        data['branches'] = tuple((label, float(mass), node(child)) for label, mass, child in data['branches'])
        return AdaptiveNode(**data)
    data = dict(payload)
    data['root'] = node(data['root'])
    return AdaptivePlan(**data)


def execute_adaptive(problem, probabilities, actions, plan: AdaptivePlan, acquire: Callable[[MeasurementAction], str], *,
                     completed_observations=(), checkpoint=None, unresolved_action_id=None):
    """Execute only the committed branch, with explicit completed-prefix replay.

    ``acquire`` must use the application's durable C03 gate. A checkpoint is
    written BEFORE dispatch and AFTER a verified return. If dispatch has unknown
    outcome, callers must reconcile it through that gate and supply the actual
    completed prefix; passing unresolved_action_id never triggers a retry.
    """
    actions = tuple(actions)
    fresh = plan_adaptive(problem, probabilities, actions, available_budget=plan.available_budget,
                          final_validation_reserve=plan.final_validation_reserve, depth=plan.depth)
    if fresh.policy_hash != plan.policy_hash:
        raise ValueError('stale or altered adaptive policy; revalue before execution')
    if unresolved_action_id is not None:
        raise RuntimeError('unresolved acquisition outcome requires durable-gate reconciliation; no automatic retry')
    by_id = {a.action_id: a for a in actions}
    prefix = tuple(tuple(row) for row in completed_observations)
    node = plan.root
    observations = []
    resource = loss_cost = 0.
    index = 0
    while node.action_id is not None:
        action = by_id[node.action_id]
        if index < len(prefix):
            if len(prefix[index]) != 2 or prefix[index][0] != action.action_id:
                raise ValueError('completed observations are not a prefix of the committed policy')
            outcome = prefix[index][1]
        else:
            if checkpoint is not None:
                checkpoint({'policy_hash': plan.policy_hash, 'state': 'dispatching',
                            'completed_observations': tuple(observations), 'action_id': action.action_id})
            outcome = acquire(action)
        matching = [(mass, child) for label, mass, child in node.branches if label == outcome]
        if len(matching) != 1 or matching[0][0] <= 0:
            raise ValueError('observation impossible or outside committed support')
        observations.append((action.action_id, outcome))
        resource += action.resource_cost
        loss_cost += action.loss_cost
        node = matching[0][1]
        index += 1
        if checkpoint is not None:
            checkpoint({'policy_hash': plan.policy_hash, 'state': 'completed_observation',
                        'completed_observations': tuple(observations), 'next_action_id': node.action_id})
    if index != len(prefix) and len(prefix) > index:
        raise ValueError('completed prefix continues past committed stop')
    return AdaptiveReceipt(plan.policy_hash, tuple(observations), node.terminal_design_id, resource, loss_cost)
