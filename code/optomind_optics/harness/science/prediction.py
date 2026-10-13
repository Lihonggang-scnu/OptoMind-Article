from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .contracts import ExperimentProgram, ModelCandidate, ObjectRef, Observation, PredictionSpec
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
    identity_state: str = 'legacy_unbound'
    failure_kind: str | None = None


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
    identity = spec.acquisition_identity
    if identity is not None:
        program = repository.load(identity.program_ref)
        if not isinstance(program, ExperimentProgram):
            raise ValueError('acquisition identity must reference an experiment program')
        nodes = [node for node in program.protocol.get('nodes', ()) if node.get('node_id') == identity.node_id]
        if len(nodes) != 1 or nodes[0].get('kind') != 'new_evidence':
            raise ValueError('acquisition identity must name one new-evidence program node')
        if nodes[0].get('channel') != spec.raw_channel:
            raise ValueError('prediction channel differs from acquisition program node')
        arguments = dict(nodes[0].get('arguments', ()))
        if 'wavelengths_nm' in arguments and not np.array_equal(arguments['wavelengths_nm'], coordinate):
            raise ValueError('prediction coordinates differ from acquisition program node')
        for key, value in identity.conditions.items():
            if key in arguments and arguments[key] != value:
                raise ValueError('prediction conditions differ from acquisition program node: ' + key)
        if spec.model_ref is not None and not isinstance(repository.load(spec.model_ref), ModelCandidate):
            raise ValueError('prediction model_ref must reference a model candidate version')
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
    expected_identity = spec.acquisition_identity
    actual_identity = observation.acquisition_identity
    identity_state = 'legacy_unbound'
    def verdict(state, reason, statistic=None, indices=(), failure_kind=None):
        return PredictionVerdict(state, reason, spec.ref(), observation.ref(), statistic, indices,
                                 'identity_failure' if failure_kind == 'identity_failure' else identity_state, failure_kind)
    if observation.quality.get('execution_status', 'unknown') not in {'complete', 'completed', 'success'}:
        return verdict('not_evaluable', 'execution did not produce a completed acquisition', failure_kind='execution_failure')
    if expected_identity is not None or actual_identity is not None:
        if expected_identity is None or actual_identity is None:
            return verdict('not_evaluable', 'versioned acquisition identity is missing on one side', failure_kind='identity_failure')
        for field in ('program_ref', 'node_id', 'sample_id', 'session_id', 'frontend_ref', 'conditions'):
            if getattr(expected_identity, field) != getattr(actual_identity, field):
                return verdict('not_evaluable', 'acquisition identity differs: ' + field, failure_kind='identity_failure')
        identity_state = 'bound'
    else:
        if spec.candidate_ref is not None and spec.candidate_ref != observation.candidate_ref:
            return verdict('not_evaluable', 'candidate identity differs', failure_kind='identity_failure')
        if spec.program_ref is not None and observation.binding.get('program_ref') != spec.program_ref.model_dump(mode='json'):
            return verdict('not_evaluable', 'experiment program differs', failure_kind='identity_failure')
    active_conditions = set(observation.quality.get('conditions', []))
    if active_conditions.intersection(spec.invalid_conditions):
        return verdict('not_evaluable', 'preregistered invalid condition occurred')
    blocks = [b for b in observation.raw_blocks if b.get('channel') == spec.raw_channel]
    if len(blocks) != 1:
        return verdict('not_evaluable', 'raw channel absent or ambiguous')
    block = blocks[0]
    conditions = dict(actual_identity.conditions) if actual_identity is not None else {}
    recorded_conditions = observation.binding.get('conditions') or {}
    if actual_identity is not None:
        for key, value in recorded_conditions.items():
            if key in conditions and conditions[key] != value:
                return verdict('not_evaluable', 'observation conditions conflict with acquisition identity: ' + key, failure_kind='identity_failure')
        if 'sample_id' in block and block['sample_id'] != actual_identity.sample_id:
            return verdict('not_evaluable', 'raw sample differs from acquisition identity', failure_kind='identity_failure')
    conditions.update(recorded_conditions)
    grid = dict(expected_identity.conditions) if expected_identity is not None else {}
    for key, value in spec.grid.items():
        if key in grid and grid[key] != value:
            return verdict('not_evaluable', 'prediction grid conflicts with acquisition identity: ' + key, failure_kind='identity_failure')
        grid[key] = value
    for key, aliases in {'angle_deg':('angle_deg','angles_deg'), 'polarization':('polarization','polarizations'),
                         'coordinate_units':('coordinate_units','wavelength_units'),
                         'bandwidth_nm':('bandwidth_nm',), 'angular_spread_deg':('angular_spread_deg',),
                         'coherence':('coherence',)}.items():
        for alias in aliases:
            if alias not in grid:
                continue
            expected = grid[alias]
            if isinstance(expected, (list, tuple)):
                if len(expected) != 1:
                    return verdict('not_evaluable', 'ambiguous multi-condition prediction for one raw block')
                expected = expected[0]
            actual = block.get(key, conditions.get(key, conditions.get(alias)))
            if key in {'angle_deg', 'bandwidth_nm', 'angular_spread_deg'}:
                try:
                    matches = float(actual) == float(expected) and np.isfinite(float(actual))
                except (TypeError, ValueError):
                    matches = False
            else:
                matches = actual is not None and actual == expected
            if not matches:
                return verdict('not_evaluable', 'declared acquisition condition differs or is missing: '+key, failure_kind='identity_failure')
    values = np.asarray(block.get('values', []), dtype=float)
    coordinates = np.asarray(block.get('coordinate', []), dtype=float)
    if block.get('units') != spec.units or values.shape != (len(spec.lower),) or not np.array_equal(coordinates, spec.coordinate):
        return verdict('not_evaluable', 'units or acquisition coordinates differ', failure_kind='identity_failure')
    if not np.isfinite(values).all():
        return verdict('not_evaluable', 'nonfinite acquisition', failure_kind='numerical_failure')
    if observation.quality.get('numerical_valid') is not True:
        return verdict('inconclusive', 'numerical or noise adequacy is not established', failure_kind='numerical_failure')
    if observation.quality.get('noise_validated') is not True:
        return verdict('inconclusive', 'numerical or noise adequacy is not established', failure_kind='noise_uncalibrated')
    lower, upper = np.asarray(spec.lower), np.asarray(spec.upper)
    distance = np.maximum(lower - values, values - upper)
    bad = tuple(int(i) for i in np.flatnonzero(distance > 0))
    return verdict('violated' if bad else 'supported', 'preregistered simultaneous envelope', float(distance.max()), bad)
