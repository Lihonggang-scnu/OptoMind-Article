from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from .compute_ledger import ComputeLedger, active_compute_ledger, computational_event, compute_scope
from .likelihood_blocks import LikelihoodBlock
from .research_belief import MAX_HYPOTHESES, MAX_OBSERVATION_DIMENSION
from .terminal_problem import canonical_hash, probability_vector


def _json_value(value):
    return value.model_dump(mode='json') if hasattr(value, 'model_dump') else value


@dataclass(frozen=True)
class PhysicalHypothesis:
    hypothesis_id: str
    model: Any
    parameters: tuple[tuple[str, float], ...]

    def __post_init__(self):
        parameters = tuple((str(k), float(v)) for k, v in self.parameters)
        if not self.hypothesis_id or not getattr(self.model, 'model_id', None) or not callable(getattr(self.model, 'predict', None)):
            raise ValueError('identified physical model with predict interface required')
        if len(dict(parameters)) != len(parameters) or any(not k or not np.isfinite(v) for k, v in parameters):
            raise ValueError('unique finite immutable parameter binding required')
        object.__setattr__(self, 'parameters', parameters)


@dataclass(frozen=True)
class MeasurementPlan:
    intervention_id: str
    condition_id: str
    wavelengths_nm: tuple[float, ...]
    covariance: tuple[tuple[float, ...], ...]
    gaussian_validated: bool
    artifact_path: str
    vector_path: tuple[str, ...] = ()
    channel: str = 'R'
    angle_deg: float = 0.
    conditional_independence_group: str | None = None

    def __post_init__(self):
        object.__setattr__(self, 'wavelengths_nm', tuple(float(v) for v in self.wavelengths_nm))
        object.__setattr__(self, 'covariance', tuple(tuple(float(v) for v in row) for row in self.covariance))
        object.__setattr__(self, 'vector_path', tuple(self.vector_path))
        if not self.intervention_id or not self.condition_id or self.gaussian_validated is not True:
            raise ValueError('explicit condition, intervention and validated Gaussian noise required')
        if not 1 <= len(self.wavelengths_nm) <= MAX_OBSERVATION_DIMENSION or not np.isfinite(self.wavelengths_nm).all() or np.any(np.diff(self.wavelengths_nm) <= 0):
            raise ValueError('bounded finite increasing measurement coordinates required')
        if self.channel not in ('R', 'T', 'A') or not np.isfinite(self.angle_deg) or not 0 <= self.angle_deg < 90:
            raise ValueError('invalid physical channel or angle')
        path = Path(self.artifact_path)
        if not self.artifact_path or path.is_absolute() or '..' in path.parts:
            raise ValueError('artifact must be a relative execution artifact path')
        if any(not isinstance(k, str) or not k for k in self.vector_path):
            raise ValueError('named vector path required')
        LikelihoodBlock('declared-noise', self.wavelengths_nm, (0.,) * len(self.wavelengths_nm),
                        self.covariance, channel=self.channel, angle_deg=self.angle_deg, gaussian_validated=True)


@dataclass(frozen=True)
class PhysicalPredictionProvider:
    belief_id: str
    hypotheses: tuple[PhysicalHypothesis, ...]
    prior: tuple[float, ...]
    plan: MeasurementPlan
    compute_ledger: ComputeLedger

    def __post_init__(self):
        object.__setattr__(self, 'hypotheses', tuple(self.hypotheses))
        object.__setattr__(self, 'prior', tuple(probability_vector(self.prior, len(self.hypotheses))))
        if not self.belief_id or not 2 <= len(self.hypotheses) <= MAX_HYPOTHESES or len({h.hypothesis_id for h in self.hypotheses}) != len(self.hypotheses):
            raise ValueError('identified bounded competing model pool required')
        if not isinstance(self.compute_ledger, ComputeLedger) or 'provider_predictions' not in self.compute_ledger.limits:
            raise ValueError('explicit durable provider_predictions compute budget required')

    def predict(self, *, task, route, iteration_id):
        if not iteration_id:
            raise ValueError('iteration identity required before prediction')
        means = []
        bindings = []
        with active_compute_ledger(self.compute_ledger), compute_scope('physical_prediction_provider'):
            for hypothesis in self.hypotheses:
                parameters = dict(hypothesis.parameters)
                model_id = str(hypothesis.model.model_id)
                with computational_event('provider_predictions'):
                    values = np.asarray(hypothesis.model.predict(parameters, self.plan.wavelengths_nm,
                        channel=self.plan.channel, angle_deg=self.plan.angle_deg), dtype=float)
                    if values.shape != (len(self.plan.wavelengths_nm),) or not np.isfinite(values).all():
                        raise ValueError('physical model prediction must match finite measurement vector')
                means.append(values.tolist())
                bindings.append({'hypothesis_id': hypothesis.hypothesis_id, 'model_id': model_id,
                                 'parameters': parameters,
                                 'model_ir_hash': canonical_hash(asdict(hypothesis.model.ir)) if hasattr(hypothesis.model, 'ir') else None})
        return {'belief_id': self.belief_id, 'hypothesis_ids': [h.hypothesis_id for h in self.hypotheses],
                'prior': list(self.prior), 'intervention_id': self.plan.intervention_id,
                'condition_id': self.plan.condition_id, 'means': means,
                'covariance': [list(row) for row in self.plan.covariance], 'gaussian_validated': True,
                'conditional_independence_group': self.plan.conditional_independence_group,
                'physical_prediction_binding': {'iteration_id': iteration_id,
                    'task_hash': canonical_hash(_json_value(task)), 'route_hash': canonical_hash(_json_value(route)),
                    'measurement_plan': asdict(self.plan), 'models': bindings},
                'compute_accounting': self.compute_ledger.snapshot()}

    def observe(self, *, task, run_payload, iteration_id, prediction, execution_signature, execution_dir):
        binding = prediction.get('physical_prediction_binding') or {}
        if binding.get('iteration_id') != iteration_id or binding.get('task_hash') != canonical_hash(_json_value(task)):
            raise ValueError('prediction task or iteration binding mismatch')
        if canonical_hash(binding.get('measurement_plan')) != canonical_hash(asdict(self.plan)):
            raise ValueError('measurement plan changed after prediction')
        if not isinstance(execution_signature, str) or not execution_signature:
            raise ValueError('completed execution signature required')
        directory = Path(execution_dir).resolve()
        artifact = (directory / self.plan.artifact_path).resolve()
        if not artifact.is_relative_to(directory) or not artifact.is_file():
            raise ValueError('measurement artifact missing or outside execution directory')
        raw = artifact.read_bytes()
        row = json.loads(raw)
        for key in self.plan.vector_path:
            row = row[key]
        for key, expected in (('iteration_id', iteration_id), ('execution_signature', execution_signature),
                              ('intervention_id', self.plan.intervention_id), ('condition_id', self.plan.condition_id),
                              ('channel', self.plan.channel), ('angle_deg', self.plan.angle_deg)):
            if row.get(key) != expected:
                raise ValueError(f'observation {key} binding mismatch')
        if tuple(row.get('wavelengths_nm', ())) != self.plan.wavelengths_nm:
            raise ValueError('observation coordinates differ from frozen prediction')
        if row.get('derived_from'):
            raise ValueError('derived observation cannot be consumed as new raw evidence')
        values = np.asarray(row.get('values'), dtype=float)
        if values.shape != (len(self.plan.wavelengths_nm),) or not np.isfinite(values).all():
            raise ValueError('actual finite observation vector required')
        identity = row.get('observation_id')
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError('actual acquisition observation identity required')
        return {'observation_id': identity, 'intervention_id': self.plan.intervention_id,
                'condition_id': self.plan.condition_id, 'values': values.tolist(),
                'execution_signature': execution_signature,
                'source_artifact': {'path': self.plan.artifact_path, 'sha256': hashlib.sha256(raw).hexdigest()}}
