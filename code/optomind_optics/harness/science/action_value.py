from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

import numpy as np

from .terminal_problem import TerminalDecisionProblem, canonical_hash, probability_vector


@dataclass(frozen=True)
class FrozenPolicyEvaluation:
    context_hash: str
    policy_hash: str
    confirmation_split_id: str
    sample_count: int
    estimate: float
    value_interval: tuple[float, float]
    family_alpha: float
    declared_comparison_count: int
    loss_cost: float
    internal_evaluation_cost: float
    status: str
    gain_bounds: tuple[float, float] | None = None

    @property
    def net_interval(self):
        return (self.value_interval[0] - self.loss_cost, self.value_interval[1] - self.loss_cost)


def confirm_policy_value(problem: TerminalDecisionProblem, probabilities: Sequence[float],
                         sample_observation: Callable[[int, np.random.Generator], Any],
                         frozen_policy: Callable[[Any], str], *, policy_hash: str,
                         confirmation_split_id: str, used_split_ids: Sequence[str],
                         n_samples: int, seed: int, comparison_count: int = 1,
                         family_alpha: float = .05, loss_cost: float = 0,
                         evaluation_cost_per_sample: float = 0,
                         meter_resource: str | None = None) -> FrozenPolicyEvaluation:
    q = probability_vector(probabilities, len(problem.world_ids))
    if not confirmation_split_id or confirmation_split_id in used_split_ids:
        raise ValueError('confirmation must use a fresh independent split')
    if len(policy_hash) != 64 or any(c not in '0123456789abcdef' for c in policy_hash):
        raise ValueError('frozen executable policy identity required')
    if n_samples < 2 or comparison_count < 1 or not 0 < family_alpha < 1:
        raise ValueError('fixed sample count and multiplicity must be preregistered')
    if not np.isfinite([loss_cost,evaluation_cost_per_sample]).all() or min(loss_cost,evaluation_cost_per_sample) < 0:
        raise ValueError('costs must be explicitly nonnegative')
    rng = np.random.default_rng(seed)
    baseline = problem.design_ids.index(problem.solve(q).decision_id)
    gains = []
    from contextlib import nullcontext
    from .compute_ledger import computational_event
    for _ in range(n_samples):
        with computational_event(meter_resource) if meter_resource else nullcontext():
            world_index = int(rng.choice(len(q), p=q))
            observation = sample_observation(int(world_index), rng)
            decision_id = frozen_policy(observation)
            if decision_id not in problem.design_ids:
                raise ValueError('continuation left the frozen common design pool')
            selected = problem.design_ids.index(decision_id)
            gains.append(problem.losses[world_index,baseline] - problem.losses[world_index,selected])
    estimate = float(np.mean(gains))
    possible_gains = problem.losses[:,baseline,None] - problem.losses
    gain_lower, gain_upper = float(possible_gains.min()), float(possible_gains.max())
    half_width = (gain_upper-gain_lower) * float(np.sqrt(np.log(2*comparison_count/family_alpha)/(2*n_samples)))
    interval = (max(gain_lower,estimate-half_width), min(gain_upper,estimate+half_width))
    context = canonical_hash({'problem':problem.cache_key,'q':q.tolist(),'policy_hash':policy_hash,
                              'split':confirmation_split_id,'n':n_samples,'seed':seed,
                              'comparisons':comparison_count,'alpha':family_alpha,
                              'bound':'finite_paired_gain_range_hoeffding/1','gain_bounds':(gain_lower,gain_upper)})
    status = 'positive_net_value_confirmed' if interval[0] > loss_cost else (
        'nonpositive_net_value_confirmed' if interval[1] <= loss_cost else 'inconclusive')
    return FrozenPolicyEvaluation(context,policy_hash,confirmation_split_id,n_samples,estimate,interval,
                                  family_alpha,comparison_count,loss_cost,evaluation_cost_per_sample*n_samples,status,(gain_lower,gain_upper))
