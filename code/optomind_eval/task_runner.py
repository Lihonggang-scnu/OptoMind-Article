"""Offline forward whole-task evaluation, V1 synthetic evidence only.

Evaluator side owns worlds and scores. Policies receive only public finite models
and a receipt-producing capability. Local Python isolation is NOT security isolation.
The protocol/config are written exclusively before any world is drawn or evaluated.
"""
from dataclasses import asdict, replace
import json
from pathlib import Path
import time
import traceback
import numpy as np
from scipy.special import ndtr
from .baseline_runners import ARMS, run_policy
from .protocols import EvaluationProtocol, TaskResult, digest, seal_results
from .report_core import report_frozen_results
from .service import MeasurementService
from .worlds import WorldManifest, airy_intensity
from optomind_optics.harness.science.world_client import TaskManifest, WorldClient
from optomind_optics.harness.science.model_ir import IRNode, ModelIR, ParameterSpec
from optomind_optics.harness.science.model_compiler import compile_model
from optomind_optics.harness.science.controller import MeasurementAction
from optomind_optics.harness.science.terminal_problem import TerminalDecisionProblem


def default_config(tasks_per_case=16, *, seed_base=2910000):
    if tasks_per_case < 2:
        raise ValueError('at least two independent tasks per case')
    return {
        'version': 'c17-finite-optical-v2', 'tasks_per_case': tasks_per_case,
        'source_hashes': {name: __import__('hashlib').sha256((Path(__file__).parent/name).read_bytes()).hexdigest()
                          for name in ('task_runner.py', 'baseline_runners.py', 'protocols.py', 'report_core.py', 'worlds.py', 'service.py')},
        'cases': {'useful': {'noise_std': .002, 'loss_cost_per_resource': .0003},
                  'useless': {'noise_std': .002, 'loss_cost_per_resource': .0003, 'matched_medium': True},
                  'near_useless': {'noise_std': .5, 'loss_cost_per_resource': .0003},
                  'expensive': {'noise_std': .002, 'loss_cost_per_resource': .02}},
        'seeds': {case: [seed_base + j*10000+i for i in range(tasks_per_case)]
                  for j, case in enumerate(('useful', 'useless', 'near_useless', 'expensive'))},
        'hypotheses': [{'film_n': 1.7, 'thickness_nm': 75.}, {'film_n': 2., 'thickness_nm': 120.},
                       {'film_n': 2.3, 'thickness_nm': 165.}],
        'additional_designs': [{'film_n': 1.85, 'thickness_nm': 97.5},
                               {'film_n': 2.15, 'thickness_nm': 142.5}],
        'prior': [.25, .5, .25], 'resource_budget': 4., 'resource_costs': {'R': 1., 'T': 2.},
        'measurement_grid_nm': [450., 750.], 'scoring_grid_nm': [400., 900., 501],
        'bin_interior_edges': np.linspace(-.5, 1.5, 128).tolist(), 'shared_calibration_std': .001,
        'max_measurements_per_arm': 1, 'max_forward_calls_per_arm': 16,
        'failure_loss': 1., 'minimum_important_difference': .001,
        'status': 'exploratory_fixed_size_no_pilot_power_claim',
        'exclusions': 'none; all failed task-arms score failure_loss plus actual costs',
        'stopping': 'run exactly frozen task IDs once; no significance-based extension',
        'baseline_revision': 'v1 retained as development: pure entropy ignored acquisition cost; v2 adds information-per-cost with zero-information stop and perfect-information utility upper-bound gate; EVSI untouched; disjoint new seeds',
        'score': 'noiseless heldout reflectance prediction MSE + declared acquisition utility cost',
        'scope': 'known finite correct model library; independent Airy versus VeriTMM implementation; shared physics; no real data',
    }


def public_model():
    nodes = (IRNode('film', 'constant_nk', attributes=(('n', '$n'),)),
             IRNode('sub', 'constant_nk', attributes=(('n', 1.5),)),
             IRNode('layer', 'layer', ('film',), (('thickness', '$d'),), unit='nm'),
             IRNode('stack', 'stack', ('layer', 'sub'), unit='nm'),
             IRNode('solver', 'tmm', ('stack',)))
    return compile_model(ModelIR(nodes, 'solver', (ParameterSpec('n', 1., 3., 2.),
                                                  ParameterSpec('d', 1., 250., 120., 'nm'))))


def build_public_problem(config, task_id, case):
    model = public_model()
    hypotheses = [dict(p) for p in config['hypotheses']]
    designs = hypotheses + [dict(p) for p in config['additional_designs']]
    if config['cases'][case].get('matched_medium'):
        for p in designs:
            p['film_n'] = 1.
    grid = np.linspace(*config['scoring_grid_nm'])
    spectra = [model.predict({'n': p['film_n'], 'd': p['thickness_nm']}, grid) for p in designs]
    losses = np.array([[np.mean((a-b)**2) for b in spectra] for a in spectra[:len(hypotheses)]])
    context = {key: 'frozen-v1' for key in ('objective', 'sample', 'material', 'fidelity', 'loss')}
    context.update(task=task_id, model=model.model_id, solver='VeriTMM', split='A')
    problem = TerminalDecisionProblem(tuple(f'w{i}' for i in range(len(hypotheses))),
                                      tuple(f'd{i}' for i in range(len(designs))), losses, context, 'prediction_mse')
    edges = np.r_[-np.inf, config['bin_interior_edges'], np.inf]
    noise = config['cases'][case]['noise_std']
    sigma = np.sqrt(noise**2 / len(config['measurement_grid_nm']) + config['shared_calibration_std']**2)
    actions = []
    for channel, resource in config['resource_costs'].items():
        means = [float(model.predict({'n': p['film_n'], 'd': p['thickness_nm']},
                                    config['measurement_grid_nm'], channel=channel).mean()) for p in hypotheses]
        kernel = np.diff(ndtr((edges[None, :] - np.array(means)[:, None]) / sigma), axis=1)
        actions.append(MeasurementAction(channel, tuple(f'bin{i}' for i in range(len(edges)-1)),
                                         tuple(tuple(row) for row in kernel),
                                         resource * config['cases'][case]['loss_cost_per_resource'], resource))
    return problem, tuple(actions), designs, {'forward_calls': len(designs)+len(actions)*len(hypotheses),
                                             'losses': losses.tolist(), 'spectra': [a.tolist() for a in spectra],
                                             'scoring_grid_nm': grid.tolist(), 'actions': [asdict(a) for a in actions]}


def _write_new(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, sort_keys=True, allow_nan=False, indent=2)


def run_evaluation(output_dir, *, tasks_per_case=16, seed_base=2910000):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    config = default_config(tasks_per_case, seed_base=seed_base)
    task_specs = [(f'c17v2-{case}-{i:03}', case, seed) for case, seeds in config['seeds'].items() for i, seed in enumerate(seeds)]
    protocol = EvaluationProtocol('c17-finite-optical-v2', tuple(t[0] for t in task_specs), ARMS,
                                  tuple(('task_evsi', arm) for arm in ARMS if arm != 'task_evsi'),
                                  config['resource_budget'], ('R', 'T'), config['failure_loss'])
    # Exclusive creates ensure evidence cannot silently replace a prior audit run.
    protocol.freeze(output/'protocol.json')
    snapshot = output/'source_snapshot'
    snapshot.mkdir()
    for name in config['source_hashes']:
        (snapshot/name).write_bytes((Path(__file__).parent/name).read_bytes())
    _write_new(output/'frozen_config.json', {'config': config, 'config_seal': digest(config), 'protocol_seal': protocol.seal})
    _write_new(output/'parity_manifest.json', {
        'arms': ARMS, 'same_prior': True, 'same_finite_hypotheses_and_designs': True,
        'same_action_menu': True, 'same_acquisition_budget': True, 'same_quantized_receipt_statistics': True,
        'same_numerical_precomputation': True, 'max_forward_calls_per_arm': config['max_forward_calls_per_arm'],
        'same_wall_clock': False, 'same_optimization_algorithm': False,
        'hidden_world_access': 'policy only WorldClient callback; in-process research harness, not OS isolation',
        'known_model_prior': 'all fair arms share true finite generative family and probabilities; no oracle arm',
        'common_random_numbers': 'same task/action request ID across independent arm service instances',
        'permission_limit': 'all arms limited to one R or T acquisition at frozen two-wavelength grid',
        'no_claims': ['real external quality', 'LLM contribution', 'open-model growth', 'M x E synergy', 'confirmatory power'],
    })
    rows, traces = [], []
    for task_id, case, seed in task_specs:
        rng = np.random.default_rng(seed)
        truth = dict(config['hypotheses'][int(rng.choice(len(config['hypotheses']), p=config['prior']))])
        if config['cases'][case].get('matched_medium'):
            truth['film_n'] = 1.
        visible = {'geometry': 'single_planar_film', 'wavelength_domain_nm': [400., 900.], 'evidence_tier': 'V1_synthetic'}
        manifest = TaskManifest(task_id, task_id, 'A', visible, ('R', 'T'), config['resource_costs'])
        world = WorldManifest(manifest, truth['film_n'], truth['thickness_nm'], 1.5, seed,
                              independent_noise_std=config['cases'][case]['noise_std'],
                              shared_calibration_std=config['shared_calibration_std'])
        problem, actions, designs, numerical = build_public_problem(config, task_id, case)
        assert numerical['forward_calls'] <= config['max_forward_calls_per_arm']
        _write_new(output/f'{task_id}-public-model.json', numerical)
        for arm in ARMS:
            service = MeasurementService([world], budget=config['resource_budget'])
            client = WorldClient(service.handle)
            trace = {'task_id': task_id, 'arm': arm, 'case': case, 'protocol_seal': protocol.seal,
                     'config_seal': digest(config), 'receipts': [], 'forward_calls': numerical['forward_calls']}
            started = time.perf_counter()
            cost = 0.
            def acquire(action):
                nonlocal cost
                if trace['receipts']:
                    raise PermissionError('frozen one-measurement limit')
                receipt = client.purchase(task_id, f'common-{action.action_id}', action.action_id, config['measurement_grid_nm'])
                cost += receipt.cost
                trace['receipts'].append(asdict(receipt))
                value = float(np.mean(receipt.values))
                index = int(np.searchsorted(config['bin_interior_edges'], value, side='right'))
                return action.observation_ids[index]
            status, loss = 'completed', None
            try:
                design_id, trajectory = run_policy(arm, problem, config['prior'], actions, acquire, budget=config['resource_budget'])
                trace['trajectory'] = trajectory
                selected = designs[problem.design_ids.index(design_id)]
                trace['selected_design'] = selected
                trace['terminal_receipt'] = client.submit_terminal(task_id, 'final', selected)
                grid = np.linspace(*config['scoring_grid_nm'])
                # Evaluation-only reference; no score is passed back into any policy.
                estimate = replace(world, **selected)
                loss = float(np.mean((airy_intensity(estimate, grid, 'R')-airy_intensity(world, grid, 'R'))**2))
            except Exception:
                status = 'failed'
                trace['failure'] = traceback.format_exc()
            trace.update(status=status, terminal_loss=loss, acquisition_resource_cost=cost,
                         wall_seconds=time.perf_counter()-started)
            _write_new(output/f'{task_id}-{arm}-trace.json', trace)
            rows.append(TaskResult(protocol.seal, task_id, arm, loss,
                                   cost*config['cases'][case]['loss_cost_per_resource'], cost, status, ('R', 'T'), digest(trace)))
            traces.append(trace)
    seal_results(protocol, tuple(rows), output/'results.json')
    report = report_frozen_results(output/'results.json')
    report['case_descriptives'] = {}
    for case in config['cases']:
        report['case_descriptives'][case] = {}
        for arm in ARMS:
            subset = [r for r in rows if r.arm == arm and f'-{case}-' in r.task_id]
            scores = [(r.terminal_loss if r.status == 'completed' else config['failure_loss'])+r.declared_loss_cost for r in subset]
            report['case_descriptives'][case][arm] = {'n': len(subset), 'mean_total_loss': float(np.mean(scores)),
                'p90_total_loss': float(np.quantile(scores, .9)), 'worst_total_loss': max(scores),
                'mean_resource_cost': float(np.mean([r.resource_cost for r in subset])),
                'failures': sum(r.status != 'completed' for r in subset)}
    report['unsupported_claims'] = ['real external quality', 'open-model growth benefit', 'program expansion benefit',
                                    'M x E synergy', 'LLM contribution', 'adequate power or equivalence']
    report['useless_definition'] = 'incident-index matched film: thickness changes no reflectance prediction or terminal decision utility'
    report['near_useless_definition'] = 'weakly informative high-noise intensity acquisition; not mathematically zero information'
    report['cost_scope'] = 'acquisition resource and utility costs; fixed bounded shared solver precomputation; measured wall time retained but not converted into utility'
    _write_new(output/'report.json', report)
    return report
