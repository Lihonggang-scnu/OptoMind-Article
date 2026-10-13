"""Local normalized-Jacobian diagnostics, not a proof of global identifiability."""
from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class IdentifiabilityReport:
    rank: int
    parameter_count: int
    singular_values: tuple[float, ...]
    confounded_directions: tuple[tuple[float, ...], ...]
    equivalence_classes: tuple[tuple[int, ...], ...]
    task_impact: str
    limits: tuple[str, ...] = ("Local SVD does not prove global identifiability.", "Task impact requires a supplied terminal-action consumer.")


def diagnose_identifiability(jacobian, basin_predictions=(), *, relative_threshold=1e-6, equivalence_tolerance=1e-3, terminal_actions=None):
    jacobian = np.asarray(jacobian, float)
    _, s, vh = np.linalg.svd(jacobian, full_matrices=True)
    threshold = relative_threshold * max(float(s[0]) if len(s) else 0., 1.)
    rank = int(np.sum(s > threshold))
    groups = []
    for i, pred in enumerate(basin_predictions):
        for group in groups:
            if np.linalg.norm(np.asarray(pred) - np.asarray(basin_predictions[group[0]])) <= equivalence_tolerance:
                group.append(i)
                break
        else:
            groups.append([i])
    impact = "unknown"
    if terminal_actions is not None:
        if len(terminal_actions) != len(basin_predictions):
            raise ValueError("terminal actions must align with basin predictions")
        impact = "action_ambiguous" if any(len({terminal_actions[i] for i in g}) > 1 for g in groups) else "stable_on_examined_basins"
    return IdentifiabilityReport(rank, jacobian.shape[1], tuple(map(float, s)), tuple(tuple(map(float, row)) for row in vh[rank:]), tuple(tuple(g) for g in groups), impact)
