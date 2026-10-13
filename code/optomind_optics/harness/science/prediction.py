from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .contracts import ObjectRef, Observation, PredictionSpec
from .repository import ScienceRepository


@dataclass(frozen=True)
class PredictionCommit:
    prediction_ref: ObjectRef
    prior_snapshot_id: str
    committed_snapshot_id: str
    committed_sequence: int


@dataclass(frozen=True)
class PredictionVerdict:
    state: str
    reason: str
    prediction_ref: ObjectRef
    observation_ref: ObjectRef
    statistic: float | None = None
    violating_indices: tuple[int, ...] = ()


def commit_prediction(repository: ScienceRepository, spec: PredictionSpec, *, task_ref: ObjectRef,
                      evidence_cutoff: str) -> PredictionCommit:
    spec = PredictionSpec.model_validate(spec.model_dump(mode='json'))
    latest = repository.latest_snapshot()
    if latest is None or spec.snapshot_id != latest.snapshot_id:
        raise ValueError('prediction must bind current scientific snapshot before execution')
    lower, upper, coordinate = map(lambda x: np.asarray(x, dtype=float), (spec.lower, spec.upper, spec.coordinate))
    if not lower.size or lower.shape != upper.shape or lower.shape != coordinate.shape:
        raise ValueError('prediction needs aligned bounds and coordinates')
    if not all(np.isfinite(x).all() for x in (lower, upper, coordinate)) or np.any(lower > upper):
        raise ValueError('invalid prediction envelope')
    if any(value in {'', 'not_recorded'} for value in (spec.raw_channel, spec.units, spec.statistic, spec.tolerance_source)):
        raise ValueError('prediction channel, units, statistic and tolerance provenance required')
    if spec.statistic != 'simultaneous_envelope':
        raise ValueError('unsupported statistic; do not silently reinterpret prediction')
    committed = repository.commit([spec], task_ref=task_ref, evidence_cutoff=evidence_cutoff,
                                   expected_sequence=latest.committed_sequence)
    return PredictionCommit(spec.ref(), latest.snapshot_id, committed.snapshot_id, committed.committed_sequence)


def evaluate_prediction(repository: ScienceRepository, commit: PredictionCommit, observation_ref: ObjectRef) -> PredictionVerdict:
    spec = repository.load(commit.prediction_ref)
    observation = repository.load(observation_ref)
    if not isinstance(spec, PredictionSpec) or not isinstance(observation, Observation):
        raise ValueError('prediction evaluation requires typed scientific objects')
    prediction_sequence = repository.object_commit_sequence(commit.prediction_ref)
    observation_sequence = repository.object_commit_sequence(observation_ref)
    committed_snapshot = repository.snapshot(commit.committed_snapshot_id)
    if prediction_sequence != commit.committed_sequence or committed_snapshot.committed_sequence != prediction_sequence or spec.snapshot_id != commit.prior_snapshot_id:
        raise ValueError('prediction commit receipt mismatch')
    acquisition_sequence = repository.acquisition_commit_sequence(observation.acquisition_id)
    if observation_sequence <= prediction_sequence or acquisition_sequence <= prediction_sequence:
        raise ValueError('observation was available before prediction commitment')
    return compare_bound_observation(spec, observation)


def compare_bound_observation(spec: PredictionSpec, observation: Observation) -> PredictionVerdict:
    spec = PredictionSpec.model_validate(spec.model_dump(mode='json'))
    observation = Observation.model_validate(observation.model_dump(mode='json'))
    def verdict(state, reason, statistic=None, indices=()):
        return PredictionVerdict(state, reason, spec.ref(), observation.ref(), statistic, indices)
    if observation.quality.get('execution_status', 'unknown') not in {'complete', 'completed', 'success'}:
        return verdict('not_evaluable', 'execution did not produce a completed acquisition')
    if spec.candidate_ref is not None and spec.candidate_ref != observation.candidate_ref:
        return verdict('not_evaluable', 'candidate identity differs')
    if spec.program_ref is not None and observation.binding.get('program_ref') != spec.program_ref.model_dump(mode='json'):
        return verdict('not_evaluable', 'experiment program differs')
    active_conditions = set(observation.quality.get('conditions', []))
    if active_conditions.intersection(spec.invalid_conditions):
        return verdict('not_evaluable', 'preregistered invalid condition occurred')
    blocks = [b for b in observation.raw_blocks if b.get('channel') == spec.raw_channel]
    if len(blocks) != 1:
        return verdict('not_evaluable', 'raw channel absent or ambiguous')
    block = blocks[0]
    conditions = observation.binding.get('conditions') or {}
    for key, aliases in {'angle_deg':('angle_deg','angles_deg'), 'polarization':('polarization','polarizations'),
                         'coordinate_units':('coordinate_units','wavelength_units')}.items():
        for alias in aliases:
            if alias not in spec.grid:
                continue
            expected = spec.grid[alias]
            if isinstance(expected, (list, tuple)):
                if len(expected) != 1:
                    return verdict('not_evaluable', 'ambiguous multi-condition prediction for one raw block')
                expected = expected[0]
            actual = block.get(key, conditions.get(key, conditions.get(alias)))
            if key == 'angle_deg':
                try:
                    matches = float(actual) == float(expected) and np.isfinite(float(actual))
                except (TypeError, ValueError):
                    matches = False
            else:
                matches = actual is not None and actual == expected
            if not matches:
                return verdict('not_evaluable', 'declared acquisition condition differs or is missing: '+key)
    values = np.asarray(block.get('values', []), dtype=float)
    coordinates = np.asarray(block.get('coordinate', []), dtype=float)
    if block.get('units') != spec.units or values.shape != (len(spec.lower),) or not np.array_equal(coordinates, spec.coordinate):
        return verdict('not_evaluable', 'units or acquisition coordinates differ')
    if not np.isfinite(values).all():
        return verdict('not_evaluable', 'nonfinite acquisition')
    if observation.quality.get('numerical_valid') is not True or observation.quality.get('noise_validated') is not True:
        return verdict('inconclusive', 'numerical or noise adequacy is not established')
    lower, upper = np.asarray(spec.lower), np.asarray(spec.upper)
    distance = np.maximum(lower - values, values - upper)
    bad = tuple(int(i) for i in np.flatnonzero(distance > 0))
    return verdict('violated' if bad else 'supported', 'preregistered simultaneous envelope', float(distance.max()), bad)
