"""Offline regressions for shared-cap diagnostic reservation, not goal reachability."""
from dataclasses import replace
import json
from pathlib import Path
import socket

import pytest

from optomind_optics.harness.budget import BudgetLimits, BudgetScheduler
from optomind_optics.harness.design_task import OpticalDesignTask
from optomind_optics.harness.orchestrator import TMMHarnessConfig, TMMHarnessOrchestrator
from optomind_optics.harness.science.gradient_binding import planned_gradient_forward_cost, adopted_gradient_summary
from optomind_optics.harness.scoring_standard import FixedScoreMetric, ScoringStandard
from tmm_engine.task_io import optimization_task_from_dict


def inputs():
    payload = json.loads((Path(__file__).parent / 'fixtures/article_r3_returned_task.json').read_text())
    standard = ScoringStandard(metrics=(FixedScoreMetric(variable='r', canonical_id='mean_reflectance@450-700nm@0deg@s',
        metric='mean_reflectance', sense='minimize', target=.03, mean_reduction='sample_mean',
        region={'wavelength_nm': [450., 700.], 'angle_deg': 0., 'polarization': 's'}),), formula='-r')
    objective = {'kind': 'optomind_frozen_score', 'scoring_standard': standard.model_dump(mode='json')}
    payload.setdefault('metadata', {})['gradient_diagnostic_objective'] = objective
    return payload, standard, objective


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('offline regression attempted network access')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)


@pytest.mark.parametrize('parameter_count', [1, 2, 3])
def test_cost_uses_actual_optimizable_parameter_count(parameter_count):
    payload, _, objective = inputs()
    native = optimization_task_from_dict(payload['experiments'][0]['tmm_task'])
    layers = list(native.simulation.stack.layers)
    if parameter_count == 3:
        layers.append(layers[-1])
    elif parameter_count == 1:
        layers[0] = replace(layers[0], optimizable=False)
    native = replace(native, simulation=replace(native.simulation, stack=replace(native.simulation.stack, layers=tuple(layers))))
    assert planned_gradient_forward_cost(native, objective) == 1 + 2 * parameter_count
    assert planned_gradient_forward_cost(native, {'kind': 'optomind_soft_score'}) == 2 + 2 * parameter_count


def test_frozen_multichannel_cost_and_unsupported_objective():
    payload, _, objective = inputs()
    native = optimization_task_from_dict(payload['experiments'][0]['tmm_task'])
    native = replace(native, simulation=replace(native.simulation, illumination=replace(native.simulation.illumination, angles_deg=(0., 30.))))
    objective['scoring_standard']['metrics'][0]['region']['angle_deg'] = [0., 30.]
    assert planned_gradient_forward_cost(native, objective) == 10
    with pytest.raises(ValueError, match='unknown objective'):
        planned_gradient_forward_cost(native, {'kind': 'not_supported'})


def test_reserve_once_max_not_sum_and_never_actual_usage(tmp_path):
    payload, _, _ = inputs()
    task = OpticalDesignTask.model_validate(payload)
    harness = TMMHarnessOrchestrator(tmp_path)
    harness._budget = BudgetScheduler(BudgetLimits(forward_evaluations=64))
    parsed = harness._parse_experiments(task)
    spec, mode, native = parsed[0]
    larger = replace(native, simulation=replace(native.simulation, stack=replace(
        native.simulation.stack, layers=(*native.simulation.stack.layers, native.simulation.stack.layers[-1]))))
    second_spec = spec.model_copy(update={'experiment_id': 'larger'})
    harness._reserve_gradient_diagnostic(task, [*parsed, (second_spec, mode, larger)])
    assert sorted(harness._gradient_allocation['experiment_costs'].values()) == [5, 7]
    assert harness._gradient_allocation['reserved'] == 7
    assert harness._budget.remaining('forward_evaluations') == 57
    assert harness._budget.snapshot()['usage']['forward_evaluations'] == 0
    harness._budget.release(harness._gradient_reservation)
    assert harness._budget.remaining('forward_evaluations') == 64


def test_legitimately_too_small_budget_stays_unavailable(tmp_path):
    payload, _, _ = inputs()
    task = OpticalDesignTask.model_validate(payload)
    harness = TMMHarnessOrchestrator(tmp_path)
    harness._budget = BudgetScheduler(BudgetLimits(forward_evaluations=4))
    harness._reserve_gradient_diagnostic(task, harness._parse_experiments(task))
    assert harness._gradient_allocation['required'] == 5
    assert harness._gradient_allocation['reserved'] == 0
    assert harness._gradient_allocation['reason'] == 'insufficient_round_budget'
    assert harness._budget.snapshot()['usage']['forward_evaluations'] == 0


@pytest.mark.parametrize('reverse_layers', [False, True])
def test_real_solver_search_leaves_gradient_room_within_64(tmp_path, monkeypatch, reverse_layers):
    pytest.importorskip('torch')
    from tmm_engine import TMMWorkbench
    from tmm_engine.differentiable import DifferentiableTMM
    payload, standard, _ = inputs()
    if reverse_layers:
        payload['experiments'][0]['tmm_task']['simulation']['stack']['layers'].reverse()
    actual = [0]
    simulate, forward = TMMWorkbench.simulate, DifferentiableTMM.forward
    def counted_simulate(self, *args, **kwargs):
        actual[0] += 1
        return simulate(self, *args, **kwargs)
    def counted_forward(self, thicknesses, *args, **kwargs):
        actual[0] += int(thicknesses.shape[0])
        return forward(self, thicknesses, *args, **kwargs)
    monkeypatch.setattr(TMMWorkbench, 'simulate', counted_simulate)
    monkeypatch.setattr(DifferentiableTMM, 'forward', counted_forward)
    task = OpticalDesignTask.model_validate(payload)
    harness = TMMHarnessOrchestrator(tmp_path, config=TMMHarnessConfig(use_qwen_policy=False))
    result = harness.run(task)
    assert result.status == 'completed'
    used = result.budget['usage']['forward_evaluations']
    assert used == actual[0] <= 59
    allocation = json.loads(next(tmp_path.rglob('SEARCH_ALLOCATION.json')).read_text())
    assert allocation['gradient_allowance']['reserved'] == 5
    assert allocation['verification_allowance'] == 10
    assert allocation['robustness_allowance'] == 17
    assert allocation['primary_allocated'] == 29
    assert not result.budget['active_reservations']
    gradient = adopted_gradient_summary(task.model_dump(mode='json'), result.model_dump(mode='json'), tmp_path, scoring_standard=standard)
    assert gradient['status'] == 'available', gradient
    assert gradient['verification']['passed']
    assert gradient['compute_cost']['differentiable_forward_calls'] == 5
    assert gradient['combined_round_forward_evaluations'] == actual[0] <= 64


def test_allocator_passes_29_to_search_without_spending_gradient_hold(tmp_path, monkeypatch):
    from types import SimpleNamespace
    payload, _, _ = inputs()
    task = OpticalDesignTask.model_validate(payload)
    harness = TMMHarnessOrchestrator(tmp_path, config=TMMHarnessConfig(enable_global_optimizer=False))
    harness._budget = BudgetScheduler(BudgetLimits(forward_evaluations=64, optimizer_runs=2))
    harness._budget.reserve('baseline:test', forward_evaluations=3)
    harness._budget.commit('baseline:test', forward_evaluations=3)
    seen = []
    def optimize(optimization, maximum_forward_evaluations):
        seen.append(maximum_forward_evaluations)
        result = SimpleNamespace(evaluation_count=maximum_forward_evaluations, optimizer_id='fake',
                                 stop_reason='bounded', candidate_designs=[])
        result.to_dict = lambda: {'evaluation_count': result.evaluation_count}
        return result
    monkeypatch.setattr(harness.optimizers, 'select', lambda _: SimpleNamespace(
        descriptor=SimpleNamespace(optimizer_id='fake'), optimize=optimize))
    monkeypatch.setattr(harness, '_register_artifact', lambda *a, **kw: 'fake')
    harness._run_optimizers(task, harness._parse_experiments(task))
    assert seen == [29]
    allocation = json.loads(next(tmp_path.rglob('SEARCH_ALLOCATION.json')).read_text())
    assert allocation['verification_allowance'] == 10
    assert allocation['robustness_allowance'] == 17
    assert allocation['gradient_allowance']['reserved'] == 5
    assert harness._budget.snapshot()['usage']['forward_evaluations'] == 32
    assert harness._budget.snapshot()['reserved']['forward_evaluations'] == 22


def test_round_gate_charges_actual_diagnostic_once_after_released_hold(tmp_path, monkeypatch):
    from optomind_optics.harness.research_orchestrator import _compute_budgeted_gradient
    from optomind_optics.harness.science import gradient_binding
    payload, standard, _ = inputs()
    run = {'budget': {'usage': {'forward_evaluations': 55}}}
    seen = []
    def diagnostic(*args, maximum_forward_evaluations, **kwargs):
        seen.append(maximum_forward_evaluations)
        calls = 5 if maximum_forward_evaluations >= 5 else 0
        return {'status': 'available' if calls else 'unavailable',
                'compute_cost': {'differentiable_forward_calls': calls, 'solver_points': 0, 'paid_calls': 0}}
    monkeypatch.setattr(gradient_binding, 'adopted_gradient_summary', diagnostic)
    ledger = tmp_path / 'temporary_compute.sqlite'
    first = _compute_budgeted_gradient(payload, run, tmp_path, ledger, {"model_predictions": 100}, scoring_standard=standard)
    assert first['status'] == 'available'
    assert first['round_budget']['usage']['forward_evaluations'] == 60
    assert not first['round_budget']['active_reservations']
    second = _compute_budgeted_gradient(payload, run, tmp_path, ledger, {"model_predictions": 100}, scoring_standard=standard)
    assert second['status'] == 'unavailable'
    assert second['round_budget']['usage']['forward_evaluations'] == 60
    assert seen == [9, 4]


def test_canonical_signature_records_diagnostic_policy_not_display_names():
    from optomind_optics.harness.research_orchestrator import canonical_execution_signature
    payload, standard, _ = inputs()
    reserved = canonical_execution_signature(payload, scoring_standard=standard)
    payload['metadata']['gradient_diagnostic_objective']['scoring_standard']['metrics'][0]['canonical_id'] = 'display-only'
    assert canonical_execution_signature(payload, scoring_standard=standard) == reserved
    del payload['metadata']['gradient_diagnostic_objective']
    assert canonical_execution_signature(payload, scoring_standard=standard) != reserved


def test_interruption_releases_never_executed_gradient_hold(tmp_path, monkeypatch):
    payload, _, _ = inputs()
    task = OpticalDesignTask.model_validate(payload)
    harness = TMMHarnessOrchestrator(tmp_path)
    harness._budget = BudgetScheduler(BudgetLimits(forward_evaluations=64, optimizer_runs=1))
    harness._budget.reserve('optimizer:interrupted', optimizer_runs=1, forward_evaluations=29)
    harness._budget.reserve('gradient_diagnostic_allowance', forward_evaluations=5)
    monkeypatch.setattr(harness, '_advance', lambda *a, **kw: None)
    monkeypatch.setattr(harness, '_record_failure', lambda *a, **kw: None)
    monkeypatch.setattr(harness, '_finish', lambda *a, **kw: harness._budget.snapshot())
    recovered = harness._recover_interrupted_run(task, 0.)
    assert recovered['usage']['forward_evaluations'] == 29
    assert recovered['remaining']['forward_evaluations'] == 35
    assert not recovered['active_reservations']


def test_pool_worker_keeps_bound_diagnostic_intent(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from optomind_optics.harness import research_orchestrator as research
    payload, _, objective = inputs()
    seen = []
    class Harness(TMMHarnessOrchestrator):
        def run(self, task):
            seen.append(task.metadata['gradient_diagnostic_objective'])
            self._budget = BudgetScheduler(BudgetLimits(forward_evaluations=64))
            self._reserve_gradient_diagnostic(task, self._parse_experiments(task))
            return SimpleNamespace(model_dump_json=lambda: json.dumps({
                'reserved': self._gradient_allocation['reserved'],
                'remaining': self._budget.remaining('forward_evaluations')}))
    monkeypatch.setattr(research, 'TMMHarnessOrchestrator', Harness)
    outcome = research._tmm_harness_worker(json.dumps({'work_dir': str(tmp_path), 'task': payload}))
    assert outcome['ok']
    assert seen == [objective]
    assert outcome['final_result'] == {'reserved': 5, 'remaining': 59}


def test_standalone_without_postrun_diagnostic_keeps_legacy_budget(tmp_path):
    payload, _, _ = inputs()
    del payload['metadata']['gradient_diagnostic_objective']
    task = OpticalDesignTask.model_validate(payload)
    harness = TMMHarnessOrchestrator(tmp_path)
    harness._budget = BudgetScheduler(BudgetLimits(forward_evaluations=64))
    harness._reserve_gradient_diagnostic(task, harness._parse_experiments(task))
    assert harness._budget.remaining('forward_evaluations') == 64
    assert not harness._gradient_allocation
