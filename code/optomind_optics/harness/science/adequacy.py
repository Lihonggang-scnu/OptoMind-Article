from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

import numpy as np

from .terminal_problem import canonical_hash


@dataclass(frozen=True)
class NullCalibration:
    protocol_id: str
    scores: tuple[float, ...]
    seeds: tuple[int, ...]
    scope: str
    alpha: float

    @property
    def calibration_version(self) -> str:
        return canonical_hash(self.__dict__)

    def p_value(self, score: float) -> float:
        if not np.isfinite(score):
            raise ValueError('test statistic must be finite')
        return (1 + sum(value >= score for value in self.scores)) / (1 + len(self.scores))


def calibrate_adaptive_null(run_entire_task: Callable[[int], float], seeds: Sequence[int],
                            protocol_id: str, scope: str, alpha: float = .05) -> NullCalibration:
    if not 0 < alpha < 1 or len(seeds) < 2 or len(set(seeds)) != len(seeds) or not scope or not protocol_id:
        raise ValueError('calibration requires distinct held-out seeds, scope and protocol')
    scores = tuple(float(run_entire_task(seed)) for seed in seeds)
    if not np.isfinite(scores).all():
        raise ValueError('failed null task cannot silently drop out of calibration')
    return NullCalibration(protocol_id, scores, tuple(seeds), scope, alpha)


@dataclass(frozen=True)
class AdequacyResult:
    state: str
    audits: tuple[str, ...]
    calibration_version: str | None
    p_value: float | None
    unknown_risk: bool
    task_impact: str


def evaluate_adequacy(*, identity_valid: bool, numerical_valid: bool, noise_validated: bool,
                      residual_statistic: float | None, refit_completed: bool,
                      identifiable: bool, decision_equivalent: bool | None,
                      calibration: NullCalibration | None = None, task_impact: str = 'unknown') -> AdequacyResult:
    audits: list[str] = []
    def result(state, p=None, unknown=True):
        return AdequacyResult(state, tuple(audits), calibration.calibration_version if calibration else None,
                              p, unknown, task_impact)
    if not identity_valid:
        return result('identity_invalid')
    audits.append('identity_passed')
    if not numerical_valid:
        return result('numerical_failure')
    audits.append('numerical_passed')
    if not noise_validated:
        return result('noise_unvalidated')
    audits.append('noise_validated')
    if not refit_completed:
        return result('parameter_fit_required')
    audits.append('parameter_refit_completed')
    if calibration is None or residual_statistic is None:
        return result('uncalibrated')
    p = calibration.p_value(residual_statistic)
    audits.append('whole_adaptive_task_calibrated')
    if p <= calibration.alpha:
        return result('model_family_inadequate', p)
    if not identifiable:
        return result('nonidentifiable_decision_equivalent' if decision_equivalent else 'nonidentifiable', p)
    return result('not_rejected_within_calibration_scope', p, unknown=task_impact == 'unknown')
