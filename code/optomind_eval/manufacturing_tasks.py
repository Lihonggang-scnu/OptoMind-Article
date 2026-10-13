"""Frozen finite manufacturing decisions, with fresh specimen variation marginalized.

Synthetic one-acquisition adapter only. Production research loops are unchanged.
Policy: VeriTMM. Independently scored future product: Byrnes coherent TMM.
"""
from dataclasses import asdict, dataclass
from functools import lru_cache
import copy
import hashlib
import itertools
import json
from pathlib import Path
import time
import traceback

import numpy as np
from scipy.special import ndtr
from tmm import coh_tmm

from .protocols import EvaluationProtocol, TaskResult, digest, seal_results
from .report_core import report_frozen_results
from .manufacturing_baselines import ARMS7 as ARMS, run_policy, information_labels, grouped_information_gain
from optomind_optics.harness.science.controller import MeasurementAction
from optomind_optics.harness.science.terminal_problem import TerminalDecisionProblem
from optomind_optics.harness.science.model_ir import IRNode, ModelIR, ParameterSpec
from optomind_optics.harness.science.model_compiler import compile_model

# Frozen before implementation and outcomes; see tracking plan in delivery bundle.
FROZEN_PLAN_SHA256 = "c28e826bcbca2609609a9442497c0278a485f546b235d9c11cedea7040a10969"
FROZEN_PLAN = {'version': 'c17-manufacturing-finite-v1',
 'status': 'frozen before implementation and policy outcomes; exploratory, not powered',
 'touchset': ['code/optomind_eval/manufacturing_tasks.py',
              'code/optomind_eval/manufacturing_baselines.py',
              'code/scripts/run_science_c17_manufacturing.py',
              'code/tests/test_science_c17_manufacturing.py'],
 'scope': 'separate one-acquisition finite-distribution synthetic evaluation; unchanged old audits and '
          'C13/C14',
 'high_n': [1.8, 2.1, 2.4],
 'low_bias_nm': [-4.0, 0.0, 4.0],
 'high_bias_nm': [-5.0, 0.0, 5.0],
 'coordinate_prior': [0.25, 0.5, 0.25],
 'instrument_offset': [-0.004, 0.0, 0.004],
 'nuisance_instrument_offset': [-0.03, 0.0, 0.03],
 'instrument_prior': [0.25, 0.5, 0.25],
 'residual_support_nm': [-3.4641016151377544, 0.0, 3.4641016151377544],
 'residual_probabilities': [0.16666666666666666, 0.6666666666666666, 0.16666666666666666],
 'residual_semantics': 'independent three-point discrete law per layer and per specimen conditional on '
                       'process; coupon draw never reused for future product; not a Gaussian claim',
 'physical_minimum_nm': 0.1,
 'design_axes_nm': [[50.0, 80.0, 110.0, 140.0], [30.0, 60.0, 90.0, 120.0], [50.0, 80.0, 110.0, 140.0]],
 'scoring_grid_nm': {'start': 450.0, 'stop': 750.0, 'count': 31},
 'spec': 'mean normal-incidence R <= threshold, evaluated independently over future residual support',
 'thresholds': [0.005, 0.015, 0.04],
 'actions': [{'id': 'low_short_R',
              'materials': ['low'],
              'thicknesses_nm': [80.0],
              'grid_nm': [450.0, 480.0, 510.0],
              'angle_deg': 0.0,
              'polarization': 's',
              'resource_cost': 1.0},
             {'id': 'high_long_R',
              'materials': ['high'],
              'thicknesses_nm': [95.0],
              'grid_nm': [690.0, 720.0, 750.0],
              'angle_deg': 0.0,
              'polarization': 's',
              'resource_cost': 1.0},
             {'id': 'stack_oblique_p_R',
              'materials': ['low', 'high', 'low'],
              'thicknesses_nm': [95.0, 75.0, 95.0],
              'grid_nm': [540.0, 600.0, 660.0],
              'angle_deg': 55.0,
              'polarization': 'p',
              'resource_cost': 2.0}],
 'observation': 'mean R plus persistent instrument offset plus independent Gaussian scalar acquisition '
                'noise with std 0.003; finite mixture over fresh coupon residuals',
 'bin_interior_edges': {'start': -0.1, 'stop': 1.1, 'count': 80},
 'tail_bins': 'negative and positive infinity',
 'cases': ['mixed', 'aligned', 'nuisance', 'known_process', 'uninformative', 'expensive'],
 'case_semantics': {'mixed': 'independent full product process and instrument priors',
                    'aligned': 'only high index varies; both biases and instrument offset fixed at zero',
                    'nuisance': 'full process prior and larger instrument support',
                    'known_process': 'central process known; instrument uncertain and future residual '
                                     'law unchanged',
                    'uninformative': 'observation mean exactly 0.2 for every world; same scalar noise, '
                                     'zero state information',
                    'expensive': 'mixed prior; utility cost 2.0 per resource, exceeding maximum '
                                 'possible improvement in failure probability'},
 'utility_cost_per_resource': 0.01,
 'resource_budget': 2.0,
 'max_acquisitions': 1,
 'qoi_labels': 'whole per-design conditional yield vector rounded to bins of width 0.05 by '
               'floor(yield/0.05+0.5)',
 'optimal_design_labels': 'first minimum-loss design in frozen pool order; ties reported',
 'information_gate': 'positive information above 1e-12 and acquisition loss-cost strictly below prior '
                     'EVPI; rank information/resource, then frozen action order; no outcome-tuned '
                     'thresholds',
 'arms': ['fixed_bayes',
          'fixed_robust',
          'fixed_measure_fit',
          'entropy_cost_gated',
          'qoi_information',
          'optimal_design_information',
          'task_evsi'],
 'primary_comparison': 'task_evsi minus qoi_information in expected future spec-failure plus '
                       'acquisition cost; all other comparisons also retained with Bonferroni intervals',
 'practical_margin': 0.01,
 'tasks_per_case': 24,
 'seed_base': 6130000,
 'task_generation': 'independent truth and action-noise/coupon draws by stable task/action hash; '
                    'thresholds cycle across all three values within each case; identical potential '
                    'outcomes across policies',
 'failure_loss': 1.0,
 'retention': 'retain all tasks, failures, nulls and equivalences; no outcome-triggered exclusions, '
              'sample extension, or threshold changes',
 'independent_scoring': 'Byrnes coherent TMM expected spec-failure over independent future residual '
                        'distribution; oracle knows persistent process but not realized future '
                        'residuals',
 'future_sensitivity': 'pool enlargement and cost-ratio studies deferred to a separately frozen '
                       'protocol, never retrofit this audit',
 'limits': ['synthetic known finite family',
            'no device or manufacture claim',
            'no generic mismatch detection',
            'no OS isolation',
            'exact finite EVSI is utility-optimal by construction',
            'no algorithmic superiority assertion'],
 'pre_outcome_technical_amendment': '2026-10-12: independent review requested explicit compute cap and '
                                    'analysis procedure before any test or evaluation outcomes; '
                                    'physical/distribution/policy settings unchanged.',
 'compute_caps': {'policy_spectra': 50000,
                  'independent_terminal_spectra': 50000,
                  'purchased_spectra': 1008},
 'analysis': {'method': 'paired stratified task bootstrap within case x fixed spec threshold',
              'replicates': 5000,
              'seed': 812630,
              'confidence': 0.95,
              'multiplicity': 'Bonferroni across all six EVSI-versus-baseline comparisons',
              'weights': 'equal retained task weights; all failures included',
              'scope': 'exploratory percentile intervals; no power, asymptotic guarantee or automatic '
                       'superiority claim'}}

def _write_new(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, sort_keys=True, allow_nan=False, indent=2)


def _seed(*parts):
    return int.from_bytes(hashlib.sha256(':'.join(map(str, parts)).encode()).digest()[:8], 'big')


def _grid(spec):
    return np.linspace(spec['start'], spec['stop'], spec['count'])


def residual_ensemble(config, layers):
    support, weights = config['residual_support_nm'], config['residual_probabilities']
    choices = tuple(itertools.product(range(len(support)), repeat=layers))
    return (np.array([[support[i] for i in row] for row in choices]),
            np.array([np.prod([weights[i] for i in row]) for row in choices]))


def process_states(config):
    return tuple(dict(high_n=n, low_bias_nm=low, high_bias_nm=high)
                 for n, low, high in itertools.product(config['high_n'], config['low_bias_nm'], config['high_bias_nm']))


def actual_thicknesses(config, process, materials, nominal, residual):
    bias = [process[f'{material}_bias_nm'] for material in materials]
    return np.maximum(np.array(nominal) + bias + residual, config['physical_minimum_nm'])


@lru_cache(maxsize=16)
def _model(materials, polarization):
    nodes = [IRNode('low', 'constant_nk', attributes=(('n', 1.45),)),
             IRNode('high', 'constant_nk', attributes=(('n', '$n'),)),
             IRNode('sub', 'constant_nk', attributes=(('n', 1.52),))]
    for index, material in enumerate(materials):
        nodes.append(IRNode(f'l{index}', 'layer', (material,), (('thickness', f'$d{index}'),), unit='nm'))
    nodes.extend((IRNode('stack', 'stack', tuple(f'l{i}' for i in range(len(materials))) + ('sub',), unit='nm'),
                  IRNode('solver', 'tmm', ('stack',), (('s_fraction', 1. if polarization == 's' else 0.),))))
    return compile_model(ModelIR(tuple(nodes), 'solver',
        (ParameterSpec('n', 1.5, 3., 2.1),) + tuple(ParameterSpec(f'd{i}', .1, 300., 95., 'nm') for i in range(len(materials)))))


def policy_spectrum(process, materials, thicknesses, grid, *, angle=0., polarization='s'):
    return _model(tuple(materials), polarization).predict(
        {'n': process['high_n'], **{f'd{i}': float(d) for i, d in enumerate(thicknesses)}}, grid,
        angle_deg=angle, channel='R')


def independent_spectrum(process, materials, thicknesses, grid, *, angle=0., polarization='s'):
    indices = [1.] + [1.45 if material == 'low' else process['high_n'] for material in materials] + [1.52]
    thickness = [np.inf] + list(thicknesses) + [np.inf]
    result = np.array([coh_tmm(polarization, indices, thickness, np.deg2rad(angle), float(wavelength))['R'] for wavelength in grid])
    if not np.isfinite(result).all():
        raise ValueError('nonfinite independent spectrum; cannot count failed sample as pass')
    return result


def build_numerical_model(config):
    """Public conditional loss table; instrument and specimen realizations are not designs."""
    processes = process_states(config)
    designs = tuple(itertools.product(*config['design_axes_nm']))
    residuals, probabilities = residual_ensemble(config, 3)
    grid = _grid(config['scoring_grid_nm'])
    metrics = np.empty((len(processes), len(designs), len(residuals)))
    expected_calls = len(processes)*len(designs)*len(residuals) + sum(len(processes)*len(residual_ensemble(config,len(a['materials']))[0]) for a in config['actions'])
    if expected_calls > config['compute_caps']['policy_spectra']:
        raise ValueError('frozen policy-spectrum cap exceeded before execution')
    calls = 0
    for i, process in enumerate(processes):
        for j, design in enumerate(designs):
            for k, delta in enumerate(residuals):
                thickness = actual_thicknesses(config, process, ('low', 'high', 'low'), design, delta)
                spectrum = policy_spectrum(process, ('low', 'high', 'low'), thickness, grid)
                if not np.isfinite(spectrum).all():
                    raise ValueError('nonfinite public forward; do not drop failed samples')
                metrics[i, j, k] = spectrum.mean()
                calls += 1
    means, weights = {}, {}
    for action in config['actions']:
        deltas, mass = residual_ensemble(config, len(action['materials']))
        values = np.empty((len(processes), len(deltas)))
        for i, process in enumerate(processes):
            for k, delta in enumerate(deltas):
                thickness = actual_thicknesses(config, process, action['materials'], action['thicknesses_nm'], delta)
                values[i, k] = policy_spectrum(process, action['materials'], thickness, action['grid_nm'],
                    angle=action['angle_deg'], polarization=action['polarization']).mean()
                calls += 1
                if not np.isfinite(values[i, k]):
                    raise ValueError('nonfinite public coupon prediction')
        means[action['id']], weights[action['id']] = values, mass
    return {'processes': processes, 'designs': designs, 'future_metrics': metrics,
            'future_probabilities': probabilities, 'coupon_means': means, 'coupon_probabilities': weights,
            'forward_calls': calls}


def make_problem(config, numerical, case, threshold, task_id):
    process_prior = np.array([np.prod(row) for row in itertools.product(config['coordinate_prior'], repeat=3)])
    instrument_values = config['nuisance_instrument_offset'] if case == 'nuisance' else config['instrument_offset']
    worlds, prior = [], []
    for index, process in enumerate(numerical['processes']):
        if case in ('aligned', 'known_process') and (process['low_bias_nm'] != 0 or process['high_bias_nm'] != 0):
            continue
        if case == 'known_process' and process['high_n'] != config['high_n'][1]:
            continue
        for bindex, offset in enumerate(instrument_values):
            if case == 'aligned' and offset != 0:
                continue
            worlds.append({'process_index': index, 'instrument_offset': offset})
            prior.append(process_prior[index] * config['instrument_prior'][bindex])
    prior = np.array(prior); prior /= prior.sum()
    process_losses = (numerical['future_metrics'] > threshold) @ numerical['future_probabilities']
    losses = np.array([process_losses[world['process_index']] for world in worlds])
    context = {key: 'manufacturing-finite-v1' for key in ('objective', 'model', 'sample', 'material', 'solver', 'fidelity', 'loss', 'split')}
    context.update(task=task_id, objective=digest({'threshold': threshold, 'grid': config['scoring_grid_nm']}),
                   model=digest({'plan':config, 'worlds': worlds}), sample='future-independent-device', loss='spec_failure_probability')
    problem = TerminalDecisionProblem(tuple(f'w{i}' for i in range(len(worlds))),
        tuple(f'd{i}' for i in range(len(numerical['designs']))), losses, context, 'spec_failure_probability')
    edges = np.r_[-np.inf, _grid(config['bin_interior_edges']), np.inf]
    actions = []
    for spec in config['actions']:
        means = np.array([numerical['coupon_means'][spec['id']][world['process_index']] + world['instrument_offset'] for world in worlds])
        if case == 'uninformative':
            means = np.full_like(means, .2)
        conditional = np.diff(ndtr((edges[None, None, :] - means[:, :, None]) / .003), axis=2)
        kernel = np.einsum('wro,r->wo', conditional, numerical['coupon_probabilities'][spec['id']])
        unit_cost = 2. if case == 'expensive' else config['utility_cost_per_resource']
        actions.append(MeasurementAction(spec['id'], tuple(f'bin{i}' for i in range(len(edges)-1)),
                       tuple(tuple(row) for row in kernel), unit_cost*spec['resource_cost'], spec['resource_cost']))
    return problem, prior, tuple(actions), tuple(worlds)


@dataclass(frozen=True)
class ManufacturingReceipt:
    task_id: str
    request_id: str
    action_id: str
    action_manifest_hash: str
    wavelengths_nm: tuple[float, ...]
    angle_deg: float
    polarization: str
    channel: str
    sample_id: str
    calibration_group: str
    statistic: str
    value: float
    acquisition_noise_std: float
    cost: float
    evidence_tier: str = 'V1_synthetic'


class ManufacturingService:
    """Evaluator-local transport; policies receive purchase/submit closures only.

    Deliberately not a security boundary; private attributes are not OS isolation.
    """
    def __init__(self, config, numerical, task_id, seed, case, threshold, world, counters=None):
        self.config, self.numerical = config, numerical
        self.task_id, self.seed, self.case, self.threshold = task_id, seed, case, threshold
        self.__world, self.__spent, self.__score = dict(world), 0., None
        self.__submitted = False
        self.counters = counters if counters is not None else {'purchased_spectra':0,'independent_terminal_spectra':0}

    def purchase(self, action_id, request_id, action_manifest):
        specs = {row['id']: row for row in self.config['actions']}
        if action_id not in specs or action_manifest != specs[action_id]:
            raise PermissionError('purchase differs from frozen optical conditions')
        spec = specs[action_id]
        if self.__spent or spec['resource_cost'] > self.config['resource_budget']:
            raise PermissionError('outside one-acquisition budget')
        if self.counters['purchased_spectra'] >= self.config['compute_caps']['purchased_spectra']:
            raise PermissionError('frozen purchased-spectrum compute cap exceeded')
        self.__spent = spec['resource_cost']
        self.counters['purchased_spectra'] += 1
        process = self.numerical['processes'][self.__world['process_index']]
        residuals, weights = residual_ensemble(self.config, len(spec['materials']))
        # Same potential acquisition across policies; fresh specimen, no terminal draw reuse.
        rng = np.random.default_rng(_seed(self.seed, action_id, 'coupon'))
        residual = residuals[int(rng.choice(len(residuals), p=weights))]
        thickness = actual_thicknesses(self.config, process, spec['materials'], spec['thicknesses_nm'], residual)
        mean = independent_spectrum(process, spec['materials'], thickness, spec['grid_nm'],
                  angle=spec['angle_deg'], polarization=spec['polarization']).mean() + self.__world['instrument_offset']
        if self.case == 'uninformative':
            mean = .2
        value = float(mean + np.random.default_rng(_seed(self.seed, action_id, 'noise')).normal(0., .003))
        if not np.isfinite(value):
            raise ValueError('nonfinite purchased coupon observation')
        return ManufacturingReceipt(self.task_id, request_id, action_id, digest(spec), tuple(spec['grid_nm']),
            spec['angle_deg'], spec['polarization'], 'R', f'{self.task_id}:{action_id}:fresh-coupon',
            f'{self.task_id}:instrument', 'mean_R', value, .003, spec['resource_cost'])

    def submit(self, design_id, scoring_cache):
        if self.__submitted:
            raise PermissionError('terminal already sealed')
        ids = tuple(f'd{i}' for i in range(len(self.numerical['designs'])))
        if design_id not in ids:
            raise PermissionError('terminal design outside frozen common pool')
        index = ids.index(design_id)
        key = (self.__world['process_index'], index)
        if key not in scoring_cache:
            process = self.numerical['processes'][key[0]]
            residuals, weights = residual_ensemble(self.config, 3)
            if self.counters['independent_terminal_spectra'] + len(residuals) > self.config['compute_caps']['independent_terminal_spectra']:
                raise PermissionError('frozen independent-terminal spectrum cap exceeded')
            values = []
            for delta in residuals:
                self.counters['independent_terminal_spectra'] += 1
                thickness = actual_thicknesses(self.config, process, ('low','high','low'), self.numerical['designs'][index], delta)
                values.append(float(independent_spectrum(process, ('low','high','low'), thickness,
                                                        _grid(self.config['scoring_grid_nm'])).mean()))
            scoring_cache[key] = tuple(values)
        if not np.isfinite(scoring_cache[key]).all():
            raise ValueError('nonfinite independent metrics; never count as pass')
        self.__score = float((np.asarray(scoring_cache[key]) > self.threshold) @ self.numerical['future_probabilities'])
        self.__submitted = True
        return {'status':'sealed', 'submission_id':f'{self.task_id}:final',
                'sealed_digest':digest({'task':self.task_id,'seed':self.seed,'design':design_id,'score':self.__score})}

    def evaluator_spent(self):
        return self.__spent

    def evaluator_score(self):
        if not self.__submitted:
            raise ValueError('no terminal submission')
        return self.__score


def run_manufacturing_evaluation(output_dir, *, tasks_per_case=24, seed_base=6130000, config_override=None):
    """Run all frozen arms; overrides are visibly labeled engineering fixtures."""
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    config = copy.deepcopy(FROZEN_PLAN if config_override is None else config_override)
    if tasks_per_case < 2:
        raise ValueError('at least two independent tasks per case')
    config['tasks_per_case'], config['seed_base'] = tasks_per_case, seed_base
    fixture = config_override is not None or tasks_per_case != 24 or seed_base != 6130000
    config['execution_scope'] = 'engineering_fixture_not_frozen_audit' if fixture else 'frozen_exploratory_audit'
    tasks = [{'task_id':f'manufacturing-{case}-{i:03}', 'case':case,
              'threshold':config['thresholds'][i % len(config['thresholds'])],
              'seed':seed_base+j*10000+i} for j,case in enumerate(config['cases']) for i in range(tasks_per_case)]
    protocol = EvaluationProtocol(config['version'], tuple(t['task_id'] for t in tasks), ARMS,
        tuple(('task_evsi', arm) for arm in ARMS if arm != 'task_evsi'), config['resource_budget'],
        tuple(a['id'] for a in config['actions']), config['failure_loss'])
    protocol.freeze(out/'protocol.json')
    sources = ('manufacturing_tasks.py', 'manufacturing_baselines.py', 'baseline_runners.py', 'protocols.py', 'report_core.py')
    hashes = {name:hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest() for name in sources}
    import inspect
    from optomind_optics.harness.science import controller, evsi, terminal_problem, model_ir, model_compiler
    from tmm_engine import tmm_solver
    import tmm
    runtime_sources = {f'{module.__name__}.py':Path(inspect.getsourcefile(module)) for module in (controller,evsi,terminal_problem,model_ir,model_compiler,tmm_solver,tmm)}
    runtime_sources['tmm.tmm_core.py'] = Path(inspect.getsourcefile(coh_tmm))
    runtime_hashes = {name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in runtime_sources.items()}
    _write_new(out/'frozen_config.json', {'config':config,'config_seal':digest(config),'tasks':tasks,
        'source_hashes':hashes,'runtime_source_hashes':runtime_hashes,'plan_file_sha256':FROZEN_PLAN_SHA256,'protocol_seal':protocol.seal})
    snapshot = out/'source_snapshot'; snapshot.mkdir()
    for name in sources:
        (snapshot/name).write_bytes((Path(__file__).parent/name).read_bytes())
    for name,path in runtime_sources.items():
        (snapshot/name).write_bytes(path.read_bytes())
    start = time.perf_counter()
    numerical = build_numerical_model(config)
    precompute_seconds = time.perf_counter()-start
    _write_new(out/'public_numerical_models.json', {
        'processes':numerical['processes'],'designs_nm':numerical['designs'],
        'future_metrics':numerical['future_metrics'].tolist(),
        'future_probabilities':numerical['future_probabilities'].tolist(),
        'coupon_means':{key:value.tolist() for key,value in numerical['coupon_means'].items()},
        'coupon_probabilities':{key:value.tolist() for key,value in numerical['coupon_probabilities'].items()},
        'forward_calls':numerical['forward_calls']})
    rows, diagnostics, scoring_cache = [], {}, {}
    counters = {'purchased_spectra':0,'independent_terminal_spectra':0}
    for task in tasks:
        task_id, case = task['task_id'], task['case']
        problem, prior, actions, worlds = make_problem(config, numerical, case, task['threshold'], task_id)
        truth_index = int(np.random.default_rng(_seed(task['seed'], 'truth')).choice(len(worlds), p=prior))
        key = f"{case}:threshold={task['threshold']}"
        if key not in diagnostics:
            diagnostics[key] = {'world_count':len(worlds), 'active_prior_worlds':int(np.count_nonzero(prior)),
                'optimal_design_label_count':len(np.unique(information_labels(problem, 'optimal_design_information'), axis=0)),
                'qoi_label_count':len(np.unique(information_labels(problem, 'qoi_information'), axis=0)),
                'distinct_process_loss_rows':len(np.unique(problem.losses,axis=0)),
                'kernels':{a.action_id:np.asarray(a.kernel).tolist() for a in actions},
                'worlds':worlds, 'prior':prior.tolist(), 'losses':problem.losses.tolist(),
                'information_by_direction':{a.action_id:{label:grouped_information_gain(prior,a.kernel,values) for label,values in {
                    'process_state':[w['process_index'] for w in worlds],
                    'instrument_offset':[w['instrument_offset'] for w in worlds],
                    **{name:[numerical['processes'][w['process_index']][name] for w in worlds] for name in ('high_n','low_bias_nm','high_bias_nm')}
                }.items()} for a in actions},
                'pairwise_kernel_max_abs':{f'{a.action_id}:{b.action_id}':float(np.max(abs(np.asarray(a.kernel)-np.asarray(b.kernel))))
                    for i,a in enumerate(actions) for b in actions[i+1:]}}
        for arm in ARMS:
            service = ManufacturingService(config, numerical, task_id, task['seed'], case, task['threshold'], worlds[truth_index], counters)
            trace = {'task_id':task_id,'arm':arm,'case':case,'threshold':task['threshold'],
                     'config_seal':digest(config),'protocol_seal':protocol.seal,'receipts':[]}
            cost, terminal_loss, status = 0., None, 'completed'
            specs = {a['id']:a for a in config['actions']}
            def acquire(action):
                nonlocal cost
                receipt = service.purchase(action.action_id, f'common:{action.action_id}', specs[action.action_id])
                cost += receipt.cost
                if (receipt.action_manifest_hash != digest(specs[action.action_id]) or receipt.action_id != action.action_id
                    or receipt.task_id != task_id or receipt.cost != action.resource_cost
                    or receipt.request_id != f'common:{action.action_id}'
                    or receipt.wavelengths_nm != tuple(specs[action.action_id]['grid_nm'])
                    or receipt.angle_deg != specs[action.action_id]['angle_deg']
                    or receipt.polarization != specs[action.action_id]['polarization']
                    or receipt.channel != 'R' or receipt.statistic != 'mean_R'
                    or receipt.sample_id != f'{task_id}:{action.action_id}:fresh-coupon'
                    or receipt.calibration_group != f'{task_id}:instrument'
                    or receipt.acquisition_noise_std != .003 or not np.isfinite(receipt.value)):
                    raise ValueError('receipt differs from committed acquisition')
                trace['receipts'].append(asdict(receipt))
                index = int(np.searchsorted(_grid(config['bin_interior_edges']), receipt.value, side='right'))
                return action.observation_ids[index]
            started = time.perf_counter()
            try:
                decision, trajectory = run_policy(arm, problem, prior, actions, acquire, budget=config['resource_budget'])
                trace['policy_seconds'] = time.perf_counter()-started
                trace.update(trajectory=trajectory, selected_design_id=decision,
                             selected_design_nm=numerical['designs'][problem.design_ids.index(decision)])
                trace['terminal_receipt'] = service.submit(decision, scoring_cache)
                terminal_loss = service.evaluator_score()
            except Exception:
                status = 'failed'; trace['failure'] = traceback.format_exc()
            cost = service.evaluator_spent()  # Includes purchases whose post-purchase validation/scoring failed.
            unit_cost = 2. if case == 'expensive' else config['utility_cost_per_resource']
            trace.update(status=status, terminal_loss=terminal_loss, resource_cost=cost, wall_seconds=time.perf_counter()-started,
                         timing_scope='wall_seconds includes cached evaluator scoring; policy_seconds includes acquisition service; no equal-compute or speed claim')
            _write_new(out/f'{task_id}-{arm}-trace.json', trace)
            rows.append(TaskResult(protocol.seal,task_id,arm,terminal_loss,cost*unit_cost,cost,status,protocol.observation_permissions,digest(trace)))
    _write_new(out/'diagnostics.json', diagnostics)
    seal_results(protocol, tuple(rows), out/'results.json')
    report = report_frozen_results(out/'results.json')
    report.update(stratified_bootstrap(config, tasks, rows))
    report.update(scope=config['execution_scope'], practical_margin=config['practical_margin'],
        primary_comparison='task_evsi - qoi_information', limits=config['limits'],
        shared_forward_calls=numerical['forward_calls'], shared_precompute_seconds=precompute_seconds,
        independent_terminal_pairs=len(scoring_cache), independent_forward_counters=counters,
        memory_measurement='not measured; no memory/compute-parity claim',
        timing_scope='trace wall_seconds includes cached evaluator scoring; policy_seconds includes acquisition; no speed comparison',
        oracle_regret='Not reported: independent all-design process oracle not evaluated in this bounded stage',
        sensitivities='Pool enlargement, cost-ratio sensitivity and mismatch stress deferred; not completed',
        future_variation_semantics=config['residual_semantics'],
        conditional_yield_claim='exact under declared finite law; no real-factory yield claim')
    report['case_descriptives'] = {}
    for case in config['cases']:
        report['case_descriptives'][case] = {}
        for arm in ARMS:
            subset = [r for r in rows if r.arm == arm and r.task_id.startswith(f'manufacturing-{case}-')]
            losses = [(r.terminal_loss if r.status == 'completed' else config['failure_loss']) for r in subset]
            report['case_descriptives'][case][arm] = {'mean_total_loss':float(np.mean([l+r.declared_loss_cost for l,r in zip(losses,subset)])),
                'mean_spec_failure_probability':float(np.mean(losses)), 'mean_resource_cost':float(np.mean([r.resource_cost for r in subset])),
                'measurement_rate':float(np.mean([r.resource_cost>0 for r in subset])), 'failed_tasks':sum(r.status!='completed' for r in subset)}
    _write_new(out/'report.json',report)
    return report


def stratified_bootstrap(config, tasks, rows):
    """Frozen paired resampling of independent tasks, retaining spec/case mixture."""
    mapping = {(row.task_id, row.arm): row for row in rows}
    def score(task_id, arm):
        row = mapping[task_id, arm]
        return (row.terminal_loss if row.status == 'completed' else config['failure_loss']) + row.declared_loss_cost
    groups = {}
    for index, task in enumerate(tasks):
        groups.setdefault((task['case'], task['threshold']), []).append(index)
    rng = np.random.default_rng(config['analysis']['seed'])
    replicates = config['analysis']['replicates']
    picks = np.concatenate([rng.choice(indices, size=(replicates,len(indices)), replace=True) for indices in groups.values()], axis=1)
    comparisons = {}
    alpha = (1-config['analysis']['confidence']) / (len(ARMS)-1)
    for arm in ARMS:
        if arm == 'task_evsi':
            continue
        differences = np.array([score(task['task_id'],'task_evsi')-score(task['task_id'],arm) for task in tasks])
        estimates = differences[picks].mean(axis=1)
        comparisons[f'task_evsi - {arm}'] = {'mean':float(differences.mean()),
            'interval':np.quantile(estimates,[alpha/2,1-alpha/2]).tolist(),
            'n_independent_tasks':len(tasks), 'method':'paired_stratified_task_percentile_bootstrap',
            'replicates':replicates,'seed':config['analysis']['seed'],
            'assumption':'independent tasks conditional on frozen strata; exploratory bootstrap, no power claim'}
    return {'comparisons':comparisons,'multiplicity':config['analysis']['multiplicity'],
            'analysis':config['analysis'],'bootstrap_strata_sizes':{f'{case}:{threshold}':len(ids) for (case,threshold),ids in groups.items()}}
