from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Callable, Mapping, Sequence
from types import MappingProxyType

import numpy as np


def canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def probability_vector(values: Sequence[float], size: int) -> np.ndarray:
    result = np.asarray(values, dtype=float)
    if result.shape != (size,) or not np.isfinite(result).all() or np.any(result < 0):
        raise ValueError("invalid probability vector")
    if not np.isclose(result.sum(), 1.0, atol=1e-10, rtol=0):
        raise ValueError("probabilities must sum to one")
    return result / result.sum()


@dataclass(frozen=True)
class TerminalSolution:
    decision_id: str
    risk: float
    reference_kind: str = "finite_common_design_pool"
    feasible: bool = True


@dataclass(frozen=True)
class TerminalDecisionProblem:
    world_ids: tuple[str, ...]
    design_ids: tuple[str, ...]
    losses: np.ndarray
    context: Mapping[str, str]
    units: str

    def __post_init__(self) -> None:
        losses = np.array(self.losses, dtype=float, copy=True)
        if losses.shape != (len(self.world_ids), len(self.design_ids)) or not losses.size:
            raise ValueError("loss matrix dimensions must match nonempty identities")
        if not np.isfinite(losses).all():
            raise ValueError("losses must be finite; infeasibility needs an explicit finite task penalty")
        if len(set(self.world_ids)) != len(self.world_ids) or len(set(self.design_ids)) != len(self.design_ids):
            raise ValueError("world and design identities must be unique")
        required = {"task", "objective", "model", "sample", "material", "solver", "fidelity", "loss", "split"}
        if not required.issubset(self.context) or any(not self.context[key] for key in required) or not self.units:
            raise ValueError("complete scientific cache context and loss units are required")
        losses.setflags(write=False)
        object.__setattr__(self, "losses", losses)
        object.__setattr__(self, "context", MappingProxyType(dict(self.context)))

    @property
    def cache_key(self) -> str:
        return canonical_hash({"world_ids": self.world_ids, "design_ids": self.design_ids,
                               "context": dict(self.context), "units": self.units, "losses": self.losses.tolist()})

    def solve(self, probabilities: Sequence[float]) -> TerminalSolution:
        q = probability_vector(probabilities, len(self.world_ids))
        risks = q @ self.losses
        index = int(np.argmin(risks))
        return TerminalSolution(self.design_ids[index], float(risks[index]))

    def solve_robust(self, distributions: Sequence[Sequence[float]]) -> TerminalSolution:
        distributions = tuple(distributions)
        if not distributions:
            raise ValueError("ambiguity set must be nonempty")
        risks = np.stack([probability_vector(q, len(self.world_ids)) @ self.losses for q in distributions]).max(axis=0)
        index = int(np.argmin(risks))
        return TerminalSolution(self.design_ids[index], float(risks[index]))

    @classmethod
    def evaluate(cls, world_ids: Sequence[str], design_ids: Sequence[str], evaluator: Callable[[str, str], float],
                 context: Mapping[str, str], units: str) -> TerminalDecisionProblem:
        return cls(tuple(world_ids), tuple(design_ids), np.array([[evaluator(w, d) for d in design_ids] for w in world_ids]), context, units)
