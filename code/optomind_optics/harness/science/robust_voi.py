from __future__ import annotations

from typing import Sequence

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from .evsi import ActionValueReport, ContinuationPolicy
from .terminal_problem import TerminalDecisionProblem, canonical_hash


def joint_ambiguity(values: Sequence[Sequence[Sequence[float]]], n_worlds: int) -> np.ndarray:
    joint = np.asarray(values, dtype=float)
    if joint.ndim != 3 or joint.shape[0] == 0 or joint.shape[1] != n_worlds or joint.shape[2] == 0:
        raise ValueError("joint ambiguity must be scenario by world by observation")
    if not np.isfinite(joint).all() or np.any(joint < 0) or not np.allclose(joint.sum(axis=(1, 2)), 1.0, atol=1e-10, rtol=0):
        raise ValueError("each joint scenario must be a probability distribution")
    return joint


def robust_voi(problem: TerminalDecisionProblem, joint_scenarios: Sequence[Sequence[Sequence[float]]],
               observation_ids: Sequence[str], *, mode: str = "precommit", cost: float = 0.0,
               cost_units: str | None = None, time_limit: float = 30.0,
               solver_receipt: dict | None = None) -> ActionValueReport:
    joint = joint_ambiguity(joint_scenarios, len(problem.world_ids))
    ids = tuple(observation_ids)
    ns, nw, ny = joint.shape
    nd = len(problem.design_ids)
    if len(ids) != ny or len(set(ids)) != ny or mode not in {"precommit", "replanning"}:
        raise ValueError("invalid observation identities or commitment mode")
    if not np.isfinite(cost) or cost < 0 or (cost_units is not None and cost_units != problem.units):
        raise ValueError("cost must be finite, nonnegative and in terminal loss units")
    priors = joint.sum(axis=2)
    baseline = problem.solve_robust(priors)
    coefficients = np.einsum("swy,wd->syd", joint, problem.losses)
    lower = upper = 0.0
    status = "supported_on_declared_finite_problem"
    if mode == "replanning":
        if solver_receipt is not None:
            solver_receipt.update(status=0,method="finite_conditional_minimax",milp_called=False)
        choices = []
        for y in range(ny):
            masses = joint[:, :, y].sum(axis=1)
            possible = masses > 0
            if not possible.any():
                choices.append(problem.design_ids.index(baseline.decision_id))
                continue
            conditional = joint[possible, :, y] / masses[possible, None]
            risks = (conditional @ problem.losses).max(axis=0)
            choices.append(int(np.argmin(risks)))
        risk = float(max(sum(coefficients[s, y, choices[y]] for y in range(ny)) for s in range(ns)))
        lower = upper = risk
    else:
        objective = np.zeros(ny * nd + 1)
        objective[-1] = 1
        one_hot = np.zeros((ny, len(objective)))
        for y in range(ny):
            one_hot[y, y * nd:(y + 1) * nd] = 1
        worst_case = np.column_stack((coefficients.reshape(ns, -1), -np.ones(ns)))
        bounds = Bounds(np.r_[np.zeros(ny * nd), float(problem.losses.min())],
                        np.r_[np.ones(ny * nd), float(problem.losses.max())])
        result = milp(objective, integrality=np.r_[np.ones(ny * nd), 0], bounds=bounds,
                      constraints=[LinearConstraint(one_hot, 1, 1), LinearConstraint(worst_case, -np.inf, 0)],
                      options={"time_limit": time_limit})
        if solver_receipt is not None:
            solver_receipt.update(status=int(result.status),message=str(result.message),method="scipy.optimize.milp",milp_called=True,
                raw_mip_dual_bound=float(result.mip_dual_bound) if getattr(result,"mip_dual_bound",None) is not None and np.isfinite(result.mip_dual_bound) else None,
                raw_primal_objective=float(result.fun) if getattr(result,"fun",None) is not None and np.isfinite(result.fun) else None,
                mip_gap=float(result.mip_gap) if getattr(result,"mip_gap",None) is not None and np.isfinite(result.mip_gap) else None,
                mip_node_count=int(result.mip_node_count) if getattr(result,"mip_node_count",None) is not None else None)
        if result.x is None:
            raise RuntimeError(f"no feasible deterministic continuation: {result.message}")
        x = result.x[:-1].reshape(ny, nd)
        if not np.allclose(x, np.round(x), atol=1e-6) or not np.allclose(x.sum(axis=1), 1, atol=1e-6):
            raise RuntimeError("solver returned a fractional continuation; cannot deploy")
        choices = list(np.argmax(x, axis=1).astype(int))
        upper = float(max(sum(coefficients[s, y, choices[y]] for y in range(ny)) for s in range(ns)))
        bound = getattr(result, "mip_dual_bound", None)
        lower = float(bound) if bound is not None and np.isfinite(bound) else float(problem.losses.min())
        lower = min(lower, upper)
        if result.status != 0:
            status = "bounded_incomplete_solve"
    if solver_receipt is not None:
        solver_receipt.update(primal_risk=upper,dual_risk_bound=lower,absolute_gap=upper-lower,
                              baseline_risk=baseline.risk,value_interval=(baseline.risk-upper,baseline.risk-lower),
                              deterministic_policy=True,commitment_mode=mode)
    policy = ContinuationPolicy(problem.cache_key, ids, tuple(problem.design_ids[c] for c in choices), mode,
                                canonical_hash({"joint": joint.tolist(), "update": "conditional_minimax_v1" if mode == "replanning" else "deterministic_milp_v1"}))
    return ActionValueReport("finite_joint_ambiguity_minimax", baseline.risk, upper,
                             (baseline.risk - upper, baseline.risk - lower), float(cost), problem.units,
                             policy, status, "convex hull of declared joint scenarios; deterministic finite-pool policy")
