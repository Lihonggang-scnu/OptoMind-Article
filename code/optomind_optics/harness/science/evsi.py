from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from .terminal_problem import TerminalDecisionProblem, canonical_hash, probability_vector


@dataclass(frozen=True)
class ContinuationPolicy:
    problem_hash: str
    observation_ids: tuple[str, ...]
    design_ids: tuple[str, ...]
    commitment_mode: str
    update_policy_hash: str

    @property
    def policy_hash(self) -> str:
        return canonical_hash(self.__dict__)

    def choose(self, observation_id: str, *, problem_hash: str, policy_hash: str) -> str:
        if problem_hash != self.problem_hash or policy_hash != self.policy_hash:
            raise ValueError("continuation context or policy changed after valuation")
        try:
            return self.design_ids[self.observation_ids.index(observation_id)]
        except ValueError as exc:
            raise ValueError("observation outside committed support") from exc


@dataclass(frozen=True)
class ActionValueReport:
    estimator: str
    prior_risk: float
    posterior_risk: float
    value_interval: tuple[float, float]
    cost: float
    units: str
    policy: ContinuationPolicy
    status: str
    approximation_scope: str

    @property
    def net_value_interval(self) -> tuple[float, float]:
        return (self.value_interval[0] - self.cost, self.value_interval[1] - self.cost)


def observation_kernel(values: Sequence[Sequence[float]], n_worlds: int) -> np.ndarray:
    kernel = np.asarray(values, dtype=float)
    if kernel.ndim != 2 or kernel.shape[0] != n_worlds or kernel.shape[1] == 0:
        raise ValueError("kernel must be world by observation")
    for row in kernel:
        probability_vector(row, kernel.shape[1])
    return kernel


def exact_evsi(problem: TerminalDecisionProblem, probabilities: Sequence[float], kernel: Sequence[Sequence[float]],
               observation_ids: Sequence[str], *, cost: float = 0.0, cost_units: str | None = None) -> ActionValueReport:
    q = probability_vector(probabilities, len(problem.world_ids))
    k = observation_kernel(kernel, len(q))
    ids = tuple(observation_ids)
    if len(ids) != k.shape[1] or len(set(ids)) != len(ids):
        raise ValueError("observation identities must be unique and match kernel")
    if not np.isfinite(cost) or cost < 0 or (cost_units is not None and cost_units != problem.units):
        raise ValueError("cost must be nonnegative and converted to terminal loss units")
    joint = q[:, None] * k
    mass = joint.sum(axis=0)
    choices: list[str] = []
    posterior_risk = 0.0
    incumbent = problem.solve(q)
    for index, probability in enumerate(mass):
        if probability == 0:
            choices.append(incumbent.decision_id)
            continue
        solution = problem.solve(joint[:, index] / probability)
        posterior_risk += probability * solution.risk
        choices.append(solution.decision_id)
    # The identical deterministic continuation is algebraically the incumbent,
    # regardless of floating summation order. Do not turn cancellation noise into
    # a positive-value acquisition (or clip genuinely negative MC estimates).
    if all(choice == incumbent.decision_id for choice in choices):
        posterior_risk = incumbent.risk
    value = float(incumbent.risk - posterior_risk)
    policy = ContinuationPolicy(problem.cache_key, ids, tuple(choices), "bayesian_precommit",
                                canonical_hash({"q": q.tolist(), "kernel": k.tolist(), "rule": "bayes_finite_v1"}))
    return ActionValueReport("exact_finite_evsi", incumbent.risk, float(posterior_risk), (value, value), float(cost),
                             problem.units, policy, "supported_on_declared_finite_problem", "finite common design pool and fixed known kernel")
