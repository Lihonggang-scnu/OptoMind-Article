from dataclasses import FrozenInstanceError, replace
import hashlib
import json

import numpy as np
import pytest

from optomind_optics.harness.science.compute_ledger import ComputeLedger, ComputeBudgetExceeded
from optomind_optics.harness.science.model_ir import IRNode, ModelIR, ParameterSpec
from optomind_optics.harness.science.model_compiler import compile_model
from optomind_optics.harness.science.physical_prediction_provider import PhysicalHypothesis, MeasurementPlan, PhysicalPredictionProvider
from optomind_optics.harness.science.research_belief import update_research_belief


class LinearModel:
    model_id = 'explicit-linear-model'

    def predict(self, parameters, wavelengths_nm, **kwargs):
        return parameters['slope'] * np.asarray(wavelengths_nm)


def provider(tmp_path, model=None, parameters=None, budget=2):
    model = model or LinearModel()
    parameters = parameters or ((('slope', 0.),), (('slope', .01),))
    plan = MeasurementPlan('angle-test', 'same-specimen', (500., 600.), ((.01, 0.), (0., .01)), True, 'measurement.json')
    ledger = ComputeLedger(tmp_path / 'compute.sqlite', {'provider_predictions': budget, 'model_predictions': 2, 'solver_points': 8})
    return PhysicalPredictionProvider('finite-models', tuple(PhysicalHypothesis(h, model, p) for h, p in zip(('A', 'B'), parameters)), (.5, .5), plan, ledger)


def write_observation(tmp_path, values, **changes):
    row = {'observation_id': 'acquisition-1', 'iteration_id': 'iteration-1', 'execution_signature': 'execution-1',
           'intervention_id': 'angle-test', 'condition_id': 'same-specimen', 'channel': 'R', 'angle_deg': 0.,
           'wavelengths_nm': [500., 600.], 'values': values}
    row.update(changes)
    path = tmp_path / 'measurement.json'
    path.write_text(json.dumps(row))
    return path


def observe(p, tmp_path, prediction):
    return p.observe(task={'task': 'test'}, run_payload={'status': 'completed'}, iteration_id='iteration-1',
                     prediction=prediction, execution_signature='execution-1', execution_dir=tmp_path)


def test_linear_model_freezes_predictions_then_reads_actual_bound_artifact(tmp_path):
    p = provider(tmp_path)
    prediction = p.predict(task={'task': 'test'}, route={'route_id': 'route'}, iteration_id='iteration-1')
    assert prediction['means'] == [[0., 0.], [5., 6.]]
    path = write_observation(tmp_path, [0., 0.])
    measured = observe(p, tmp_path, prediction)
    assert measured['source_artifact']['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    result = update_research_belief(prediction, measured)
    assert result['status'] == 'updated'
    assert result['posterior'][0] > .999
    assert p.compute_ledger.snapshot()['usage']['provider_predictions'] == 2
    assert result['condition_id'] == 'same-specimen'


def test_real_compiled_optical_model_is_budgeted_and_matches_airy_reference(tmp_path):
    nodes = (IRNode('film', 'constant_nk', attributes=(('n', 2.),)),
             IRNode('sub', 'constant_nk', attributes=(('n', 1.5),)),
             IRNode('layer', 'layer', ('film',), (('thickness', '$d'),), unit='nm'),
             IRNode('stack', 'stack', ('layer', 'sub'), unit='nm'), IRNode('solver', 'tmm', ('stack',)))
    model = compile_model(ModelIR(nodes, 'solver', (ParameterSpec('d', 50., 200., 100., 'nm'),)))
    p = provider(tmp_path, model, ((('d', 100.),), (('d', 150.),)))
    prediction = p.predict(task={'task': 'test'}, route={}, iteration_id='iteration-1')
    phase = np.exp(4j*np.pi*2*100/np.asarray(p.plan.wavelengths_nm))
    reference = abs((-1/3 + phase/7)/(1-phase/21))**2
    assert np.allclose(prediction['means'][0], reference, atol=2e-14)
    assert p.compute_ledger.snapshot()['usage'] == {'provider_predictions': 2, 'model_predictions': 2, 'solver_points': 8}
    write_observation(tmp_path, reference.tolist())
    result = update_research_belief(prediction, observe(p, tmp_path, prediction))
    assert result['posterior'][0] > .5


def test_no_unbudgeted_forward_calls(tmp_path):
    p = provider(tmp_path, budget=1)
    with pytest.raises(ComputeBudgetExceeded):
        p.predict(task={}, route={}, iteration_id='iteration-1')
    assert p.compute_ledger.snapshot()['usage']['provider_predictions'] == 1
    with pytest.raises(ValueError, match='budget'):
        replace(p, compute_ledger=None)


@pytest.mark.parametrize('changes', [{'condition_id': 'other'}, {'iteration_id': 'other'},
    {'execution_signature': 'other'}, {'channel': 'T'}, {'angle_deg': 2.},
    {'wavelengths_nm': [501., 600.]}, {'derived_from': ['raw']}, {'values': [0.]}, {'observation_id': ''}])
def test_unbound_or_invalid_observation_is_rejected(tmp_path, changes):
    p = provider(tmp_path)
    prediction = p.predict(task={'task': 'test'}, route={}, iteration_id='iteration-1')
    write_observation(tmp_path, **({'values': [0., 0.]} | changes))
    with pytest.raises(ValueError):
        observe(p, tmp_path, prediction)


def test_plan_parameters_are_immutable_and_noise_explicit(tmp_path):
    p = provider(tmp_path)
    with pytest.raises(FrozenInstanceError):
        p.plan.angle_deg = 30.
    with pytest.raises(FrozenInstanceError):
        p.hypotheses[0].parameters = (('slope', 99.),)
    with pytest.raises(ValueError):
        replace(p.plan, gaussian_validated=False)
    with pytest.raises(ValueError):
        replace(p.plan, artifact_path='../elsewhere.json')


def test_missing_artifact_never_uses_objective_as_observation(tmp_path):
    p = provider(tmp_path)
    prediction = p.predict(task={'task': 'test'}, route={}, iteration_id='iteration-1')
    with pytest.raises(ValueError, match='artifact missing'):
        p.observe(task={'task': 'test'}, run_payload={'objective': 10000}, iteration_id='iteration-1',
                  prediction=prediction, execution_signature='execution-1', execution_dir=tmp_path)


def test_changed_plan_or_task_rejected_after_freeze(tmp_path):
    p = provider(tmp_path)
    prediction = p.predict(task={'task': 'test'}, route={}, iteration_id='iteration-1')
    write_observation(tmp_path, [0., 0.])
    changed = replace(p, plan=replace(p.plan, angle_deg=10.))
    with pytest.raises(ValueError, match='plan changed'):
        observe(changed, tmp_path, prediction)
    prediction['physical_prediction_binding']['task_hash'] = 'wrong'
    with pytest.raises(ValueError, match='task or iteration'):
        observe(p, tmp_path, prediction)
