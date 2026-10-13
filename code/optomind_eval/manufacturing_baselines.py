"""Frozen one-acquisition baselines for finite manufacturing-yield tasks.

The information objectives concern persistent-world labels, not a realized
future specimen. Only the public failure-probability matrix and observation
kernels are used. Existing evaluation arms retain their original implementation.
"""
from __future__ import annotations

from typing import Callable, Sequence

import numpy as np

from .baseline_runners import run_policy as _existing_run_policy
from optomind_optics.harness.science.controller import MeasurementAction
from optomind_optics.harness.science.evsi import observation_kernel
from optomind_optics.harness.science.terminal_problem import (
    TerminalDecisionProblem, canonical_hash, probability_vector,
)


ARMS7 = (
    "fixed_bayes", "fixed_robust", "fixed_measure_fit", "entropy_cost_gated",
    "qoi_information", "optimal_design_information", "task_evsi",
)
_INFORMATION_ARMS = ("qoi_information", "optimal_design_information")
_INFORMATION_THRESHOLD = 1e-12
_QOI_BIN_WIDTH = 0.05


def _label_groups(labels, n_worlds):
    """Validate scalar/vector labels and assign each whole row one group."""
    try:
        values = np.asarray(labels)
    except (TypeError, ValueError) as exc:
        raise ValueError("labels must be finite numeric scalars or vectors") from exc
    if (values.dtype.kind not in "biuf" or values.ndim not in (1, 2)
            or values.shape[0] != n_worlds
            or (values.ndim == 2 and values.shape[1] == 0)
            or not np.isfinite(values).all()):
        raise ValueError("labels must have one finite scalar or nonempty vector per world")
    unique, groups = np.unique(values, axis=0, return_inverse=True)
    return unique, groups


def grouped_information_gain(prior, kernel, labels):
    """Return I(label(world); observation) in nats under ``prior``.

    Labels may be one scalar per world or a matrix whose *whole rows* identify
    labels. Aggregate the world-observation joint distribution before computing
    mutual information; an unweighted average of kernels within a group is not
    the correct conditional law. Zero-prior labels and impossible observations
    contribute zero and never require division by their zero probabilities.
    """
    q = np.asarray(prior, dtype=float)
    if q.ndim != 1 or not q.size:
        raise ValueError("prior must be a nonempty probability vector")
    q = probability_vector(q, len(q))
    k = observation_kernel(kernel, len(q))
    unique, groups = _label_groups(labels, len(q))
    joint = np.zeros((len(unique), k.shape[1]), dtype=float)
    np.add.at(joint, groups, q[:, None] * k)
    label_mass = joint.sum(axis=1)
    observation_mass = joint.sum(axis=0)
    rows, columns = np.nonzero(joint > 0)
    positive = joint[rows, columns]
    information = np.sum(positive * (
        np.log(positive) - np.log(label_mass[rows]) - np.log(observation_mass[columns])
    ))
    # Independent variables can leave a negative roundoff residual near zero.
    return max(0.0, float(information))


def information_labels(problem: TerminalDecisionProblem, kind: str) -> np.ndarray:
    """Construct frozen labels from conditional future failure probabilities.

    QoI labels preserve the entire design-ordered conditional yield vector.
    Optimal-design labels use the first exact minimum in the frozen pool order;
    no approximate-tie tolerance or observed outcome enters either definition.
    """
    losses = np.asarray(problem.losses, dtype=float)
    if (losses.shape != (len(problem.world_ids), len(problem.design_ids))
            or not losses.size or not np.isfinite(losses).all()):
        raise ValueError("losses must be a finite nonempty world-by-design matrix")
    if kind in ("qoi", "qoi_information"):
        if np.any(losses < 0) or np.any(losses > 1):
            raise ValueError("QoI labels require conditional failure probabilities in [0, 1]")
        return np.floor((1.0 - losses) / _QOI_BIN_WIDTH + 0.5).astype(np.int64)
    if kind in ("optimal_design", "optimal_design_information"):
        return np.argmin(losses, axis=1)
    raise ValueError("unknown information-label kind")


def run_policy(
    arm: str, problem: TerminalDecisionProblem, prior,
    actions: Sequence[MeasurementAction], acquire: Callable[[MeasurementAction], str],
    *, budget: float,
):
    """Return a Bayes design and auditable trajectory after at most one acquisition.

    New information arms share the frozen EVPI cost gate. Among affordable
    eligible actions, maximize label information per resource, with ties resolved
    by the supplied frozen action order. Acquisition updates the full world
    posterior, never merely the information-label posterior.
    """
    if arm not in ARMS7:
        raise ValueError("unknown manufacturing evaluation arm")
    if arm not in _INFORMATION_ARMS:
        return _existing_run_policy(arm, problem, prior, actions, acquire, budget=budget)

    q = probability_vector(prior, len(problem.world_ids))
    if not np.isfinite(budget) or budget < 0:
        raise ValueError("budget must be finite and nonnegative")
    actions = tuple(actions)
    if len({action.action_id for action in actions}) != len(actions):
        raise ValueError("action identities must be unique")
    kernels = {}
    for action in actions:
        if (not action.action_id
                or not np.isfinite([action.loss_cost, action.resource_cost]).all()
                or action.loss_cost < 0 or action.resource_cost <= 0):
            raise ValueError("actions need identities, nonnegative loss costs and positive resource costs")
        kernel = observation_kernel(action.kernel, len(q))
        if (len(action.observation_ids) != kernel.shape[1]
                or len(set(action.observation_ids)) != kernel.shape[1]):
            raise ValueError("observation identities must be unique and match kernel")
        kernels[action.action_id] = kernel

    labels = information_labels(problem, arm)
    unique, groups = _label_groups(labels, len(q))
    label_mass = np.bincount(groups, weights=q, minlength=len(unique))
    label_record = {
        "event": "information_labels", "kind": arm,
        "world_ids": list(problem.world_ids), "design_ids": list(problem.design_ids),
        "labels": labels.tolist(), "unique_labels": unique.tolist(),
        "group_indices_by_world": groups.tolist(), "label_prior": label_mass.tolist(),
        "label_hash": canonical_hash({"kind": arm, "world_ids": problem.world_ids,
                                      "design_ids": problem.design_ids, "labels": labels.tolist()}),
    }
    if arm == "qoi_information":
        label_record["definition"] = "floor((1 - conditional_failure_probability) / 0.05 + 0.5), whole vector"
    else:
        label_record["definition"] = "first exact minimum-loss design in frozen pool order"
        label_record["optimal_design_ids"] = [problem.design_ids[index] for index in labels]
        label_record["minimum_loss_ties_by_world"] = [
            [problem.design_ids[index] for index in np.flatnonzero(row == row.min())]
            for row in problem.losses
        ]

    trajectory = [{
        "event": "start", "arm": arm, "prior": q.tolist(),
        "problem_hash": problem.cache_key, "budget": float(budget),
        "action_order": [action.action_id for action in actions],
        "action_identities": [{
            "action_id": action.action_id, "observation_ids": list(action.observation_ids),
            "kernel_hash": canonical_hash(kernels[action.action_id].tolist()),
            "loss_cost": float(action.loss_cost), "resource_cost": float(action.resource_cost),
        } for action in actions],
    }, label_record]
    incumbent = problem.solve(q)
    perfect_information_gain = incumbent.risk - float(q @ problem.losses.min(axis=1))
    gains = {action.action_id: grouped_information_gain(q, kernels[action.action_id], labels)
             for action in actions}
    eligible = [action for action in actions if action.resource_cost <= budget
                and action.loss_cost < perfect_information_gain
                and gains[action.action_id] > _INFORMATION_THRESHOLD]
    rates = {action.action_id: gains[action.action_id] / action.resource_cost for action in actions}
    trajectory.append({
        "event": "information_cost_gate",
        "perfect_information_gain_upper_bound": float(perfect_information_gain),
        "information_threshold": _INFORMATION_THRESHOLD, "information_units": "nats",
        "prior_information_gains": gains, "information_per_resource": rates,
        "eligible_actions": [action.action_id for action in eligible],
        "tie_break": "first in frozen action order",
    })
    if not eligible:
        design = incumbent.decision_id
        trajectory.append({"event": "stop", "reason": "no_eligible_information_action"})
    else:
        # Python's max retains the first input on equal keys, the frozen order.
        action = max(eligible, key=lambda candidate: rates[candidate.action_id])
        trajectory.append({"event": "selection", "action": action.action_id,
                           "information_gain": gains[action.action_id],
                           "information_per_resource": rates[action.action_id]})
        observation = acquire(action)
        if observation not in action.observation_ids:
            raise ValueError("acquisition outside frozen observation support")
        column = action.observation_ids.index(observation)
        posterior_mass = q * kernels[action.action_id][:, column]
        evidence = float(posterior_mass.sum())
        if evidence <= 0:
            raise ValueError("zero probability observation under frozen model")
        q = posterior_mass / evidence
        design = problem.solve(q).decision_id
        trajectory.append({
            "event": "posterior", "action": action.action_id, "observation": observation,
            "observation_probability": evidence, "prior_information_gains": gains,
            "posterior": q.tolist(),
            "label_posterior": np.bincount(groups, weights=q, minlength=len(unique)).tolist(),
        })
    trajectory.append({"event": "terminal_choice", "design_id": design})
    return design, trajectory
