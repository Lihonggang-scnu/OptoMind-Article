"""Finite three-layer antireflection decisions; independent Byrnes terminal oracle.

Synthetic, in-process evaluator only. No RCWA/FDFD, real measurements, or joint
model/program expansion claim. Frozen known-model uncertainty is shared by arms.
"""
from dataclasses import asdict
import hashlib
import itertools
import json
from pathlib import Path
import time
import traceback
import numpy as np
from scipy.special import ndtr
from tmm import coh_tmm
from .baseline_runners import ARMS, run_policy
from .protocols import EvaluationProtocol, TaskResult, digest, seal_results
from .report_core import report_frozen_results
from optomind_optics.harness.science.world_client import WorldClient, MeasurementReceipt
from optomind_optics.harness.science.model_ir import IRNode, ModelIR, ParameterSpec
from optomind_optics.harness.science.model_compiler import compile_model
from optomind_optics.harness.science.terminal_problem import TerminalDecisionProblem
from optomind_optics.harness.science.controller import MeasurementAction


def multilayer_model():
    nodes = [IRNode('low', 'constant_nk', attributes=(('n', 1.45),)),
             IRNode('high', 'constant_nk', attributes=(('n', '$n'),)),
             IRNode('sub', 'constant_nk', attributes=(('n', 1.52),))]
    for i, material in enumerate(('low', 'high', 'low')):
        nodes.append(IRNode(f'l{i}', 'layer', (material,), (('thickness', f'$d{i}'),), unit='nm'))
    nodes += [IRNode('stack', 'stack', ('l0', 'l1', 'l2', 'sub'), unit='nm'), IRNode('solver', 'tmm', ('stack',))]
    return compile_model(ModelIR(tuple(nodes), 'solver', (ParameterSpec('n', 1.5, 3., 2.1),) +
                                 tuple(ParameterSpec(f'd{i}', 1., 200., 90., 'nm') for i in range(3))))


def coupon_model():
    nodes = (IRNode('high', 'constant_nk', attributes=(('n', '$n'),)),
             IRNode('sub', 'constant_nk', attributes=(('n', 1.52),)),
             IRNode('film', 'layer', ('high',), (('thickness', '$d'),), unit='nm'),
             IRNode('stack', 'stack', ('film', 'sub'), unit='nm'), IRNode('solver', 'tmm', ('stack',)))
    return compile_model(ModelIR(nodes, 'solver', (ParameterSpec('n', 1.5, 3., 2.1), ParameterSpec('d', 1., 200., 95., 'nm'))))


def byrnes_prediction(regime, design, wavelengths, channel='R'):
    """Independent scalar coherent-TMM implementation, never policy-imported."""
    indices = [1., 1.45, regime['high_n'], 1.45, 1.52] if len(design) == 3 else [1., regime['high_n'], 1.52]
    thicknesses = [np.inf] + [d*regime['thickness_scale'] for d in design] + [np.inf]
    return np.array([coh_tmm('s', indices, thicknesses, 0., float(w))[channel] for w in wavelengths])


class CoatingService:
    """Evaluator owns true process regime. Only handle is given to policy client."""
    def __init__(self, config, task, seed, case, regime):
        self.config, self.task, self.seed, self.case = config, task, seed, case
        self.__regime, self.__spent, self.__score = regime, 0., None

    def handle(self, req):
        c = self.config
        if req['task_id'] != self.task:
            return {'error': 'unknown task'}
        if req['operation'] == 'purchase':
            channel = req['channel']
            if channel not in c['resource_costs'] or req['wavelengths_nm'] != c['measurement_grid_nm']:
                return {'error': 'outside frozen action menu'}
            cost = c['resource_costs'][channel]
            if self.__spent or cost > c['resource_budget']:
                return {'error': 'frozen one-acquisition limit'}
            self.__spent += cost
            rng = np.random.default_rng(int.from_bytes(hashlib.sha256(f"{self.seed}:{req['request_id']}".encode()).digest()[:8], 'big'))
            noise = c['cases'][self.case]['noise_std']
            values = byrnes_prediction(self.__regime, [c['coupon_thickness_nm']], c['measurement_grid_nm'], channel)
            values += rng.normal(0, noise, len(values))
            rid = digest(req)
            return asdict(MeasurementReceipt(rid, self.task, req['request_id'], channel, tuple(c['measurement_grid_nm']),
                tuple(values), tuple(tuple(r) for r in np.eye(len(values))*noise**2), self.task, 0., cost, 'planar-independent/1', rid))
        if req['operation'] == 'terminal':
            design = req['selected_design']['thicknesses_nm']
            if list(design) not in c['designs_nm']:
                return {'error': 'design outside frozen common pool'}
            reflectance = byrnes_prediction(self.__regime, design, c['scoring_grid_nm'])
            self.__score = float(reflectance.mean())
            return {'submission_id': req['submission_id'], 'status': 'sealed',
                    'sealed_digest': digest({'secret_seed': self.seed, 'score': self.__score, 'design': design})}
        return {'error': 'unsupported operation'}

    def evaluator_score(self):
        if self.__score is None:
            raise ValueError('no terminal submission')
        return self.__score


def _new(path, value):
    with path.open('x') as f:
        json.dump(value, f, sort_keys=True, allow_nan=False, indent=2)


def run_multilayer_evaluation(output_dir, *, tasks_per_case=12, seed_base=4910000):
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    config = {'version': 'c17-multilayer-v1', 'prior': [.25, .5, .25],
        'regimes': [{'high_n': 1.8, 'thickness_scale': .92}, {'high_n': 2.1, 'thickness_scale': 1.}, {'high_n': 2.4, 'thickness_scale': 1.08}],
        'designs_nm': [list(d) for d in itertools.product((50., 80., 110., 140.), (30., 60., 90., 120.), (50., 80., 110., 140.))],
        'cases': {'useful': {'noise_std': .002, 'utility_cost_per_resource': .0001},
                  'noisy': {'noise_std': .5, 'utility_cost_per_resource': .0001},
                  'expensive': {'noise_std': .002, 'utility_cost_per_resource': .03}},
        'measurement_grid_nm': [450., 750.], 'scoring_grid_nm': np.linspace(450,750,101).tolist(),
        'coupon_thickness_nm': 95., 'resource_costs': {'R': 1., 'T': 2.}, 'resource_budget': 4.,
        'max_acquisitions': 1, 'forward_call_cap': 200, 'minimum_important_difference': .001,
        'failure_loss': 1., 'tasks_per_case': tasks_per_case,
        'tasks': [{'task_id': f'coating-{case}-{i:03}', 'case': case, 'seed': seed_base+j*10000+i}
                  for j,case in enumerate(('useful','noisy','expensive')) for i in range(tasks_per_case)],
        'score': 'mean actual three-layer coating reflectance 450-750 nm + acquisition utility cost',
        'inference': 'known finite process prior; quantized mean characterization coupon observation; Gaussian kernel',
        'bin_interior_edges': np.linspace(-.5,1.5,128).tolist(),
        'stopping': 'fixed task count before outcomes; no exclusions or significance-based extension',
        'status': 'exploratory, no power claim, locally frozen before outcomes',
        'source_hashes': {n: hashlib.sha256((Path(__file__).parent/n).read_bytes()).hexdigest()
                          for n in ('multilayer_tasks.py','baseline_runners.py','protocols.py','report_core.py')}}
    protocol = EvaluationProtocol(config['version'], tuple(t['task_id'] for t in config['tasks']), ARMS,
                                 tuple(('task_evsi', a) for a in ARMS if a != 'task_evsi'), 4., ('R','T'), 1.)
    protocol.freeze(out/'protocol.json')
    _new(out/'frozen_config.json', {'config':config,'config_seal':digest(config),'protocol_seal':protocol.seal})
    (out/'source_snapshot').mkdir()
    for name in config['source_hashes']:
        (out/'source_snapshot'/name).write_bytes((Path(__file__).parent/name).read_bytes())
    _new(out/'parity_manifest.json', {'arms':ARMS, 'same_process_prior_design_pool_budget_observations':True,
         'finite_design_pool':64, 'shared_model_forward_calls':198, 'max_acquisitions':1,
         'independent_terminal_implementation':'Byrnes tmm.coh_tmm; policy uses VeriTMM S-matrix',
         'limits':['synthetic known finite family','no OS process isolation','no equal wall-clock claim','no LLM or M×E attribution']})
    model, coupon = multilayer_model(), coupon_model()
    spectra=[]
    for regime in config['regimes']:
        spectra.append([model.predict(dict(n=regime['high_n'], **{f'd{i}':d*regime['thickness_scale'] for i,d in enumerate(design)}),
                                      config['scoring_grid_nm']) for design in config['designs_nm']])
    losses=np.mean(spectra,axis=2)
    means = {channel: [float(coupon.predict({'n':reg['high_n'], 'd':config['coupon_thickness_nm']*reg['thickness_scale']},
                      config['measurement_grid_nm'], channel=channel).mean()) for reg in config['regimes']] for channel in ('R','T')}
    _new(out/'public_numerical_models.json', {'losses':losses.tolist(), 'spectra':np.array(spectra).tolist(),
                                            'coupon_means':means, 'forward_calls':198})
    rows=[]
    for spec in config['tasks']:
        task,case,seed=spec['task_id'],spec['case'],spec['seed']
        truth=config['regimes'][int(np.random.default_rng(seed).choice(3,p=config['prior']))]
        context={k:'multilayer-v1' for k in ('objective','model','sample','material','solver','fidelity','loss','split')};context['task']=task
        problem=TerminalDecisionProblem(('w0','w1','w2'),tuple(f'd{i}' for i in range(64)),losses,context,'reflectance')
        edges=np.r_[-np.inf,config['bin_interior_edges'],np.inf]
        sigma=config['cases'][case]['noise_std']/np.sqrt(2)
        actions=tuple(MeasurementAction(ch,tuple(f'bin{i}' for i in range(len(edges)-1)),
                        tuple(tuple(r) for r in np.diff(ndtr((edges[None,:]-np.array(means[ch])[:,None])/sigma),axis=1)),
                        cost*config['cases'][case]['utility_cost_per_resource'],cost) for ch,cost in config['resource_costs'].items())
        for arm in ARMS:
            service=CoatingService(config,task,seed,case,truth);client=WorldClient(service.handle)
            trace={'task_id':task,'arm':arm,'case':case,'receipts':[], 'protocol_seal':protocol.seal,
                   'config_seal':digest(config),'shared_forward_calls':198}
            start=time.perf_counter();cost=0.;loss=None;status='completed'
            def acquire(action):
                nonlocal cost
                receipt=client.purchase(task,f'common-{action.action_id}',action.action_id,config['measurement_grid_nm'])
                cost+=receipt.cost;trace['receipts'].append(asdict(receipt))
                return action.observation_ids[int(np.searchsorted(config['bin_interior_edges'],np.mean(receipt.values),side='right'))]
            try:
                decision,trajectory=run_policy(arm,problem,config['prior'],actions,acquire,budget=4.)
                trace['trajectory']=trajectory
                design=config['designs_nm'][problem.design_ids.index(decision)]
                trace['selected_design_nm']=design
                trace['terminal_receipt']=client.submit_terminal(task,'final',{'thicknesses_nm':design})
                loss=service.evaluator_score()
            except Exception:
                status='failed';trace['failure']=traceback.format_exc()
            trace.update(status=status,terminal_loss=loss,resource_cost=cost,wall_seconds=time.perf_counter()-start)
            _new(out/f'{task}-{arm}-trace.json',trace)
            rows.append(TaskResult(protocol.seal,task,arm,loss,cost*config['cases'][case]['utility_cost_per_resource'],cost,status,('R','T'),digest(trace)))
    seal_results(protocol,tuple(rows),out/'results.json')
    report=report_frozen_results(out/'results.json')
    report['case_descriptives']={}
    for case in config['cases']:
        report['case_descriptives'][case]={}
        for arm in ARMS:
            subset=[r for r in rows if r.arm==arm and r.task_id.startswith(f'coating-{case}-')]
            scores=[(r.terminal_loss if r.status=='completed' else 1.)+r.declared_loss_cost for r in subset]
            report['case_descriptives'][case][arm]={'mean_total_loss':float(np.mean(scores)),
                'mean_reflectance':float(np.mean([r.terminal_loss if r.status=='completed' else 1. for r in subset])),
                'mean_resource_cost':float(np.mean([r.resource_cost for r in subset])), 'worst_total_loss':max(scores)}
    report['decision_relevance']={'per_regime_optimal_designs_nm':[config['designs_nm'][int(np.argmin(row))] for row in losses],
                                  'prior_optimal_design_nm':config['designs_nm'][int(np.argmin(np.array(config['prior'])@losses))]}
    report['limits']=['synthetic finite known regimes and design pool','no external measured or hardware quality',
                     'no model-growth/program joint-factorial result','no power or practical superiority assertion']
    _new(out/'report.json',report)
    return report
