"""Prespecified post-baseline pool/cost sensitivity and independent finite oracle.

All original task seeds retained; not an independent confirmatory study. No
changes to the original audit or its frozen source snapshots.
"""
from dataclasses import asdict, replace
import copy,json,hashlib,time,traceback
from pathlib import Path
import numpy as np
from . import manufacturing_tasks as mt
from .manufacturing_baselines import ARMS7,run_policy
from .protocols import EvaluationProtocol,TaskResult,digest,seal_results
from .report_core import report_frozen_results


def _load_numerical(path):
    data=json.loads(Path(path).read_text())
    return {'processes':data['processes'],'designs':tuple(tuple(d) for d in data['designs_nm']),
        'future_metrics':np.asarray(data['future_metrics']),'future_probabilities':np.asarray(data['future_probabilities']),
        'coupon_means':{k:np.asarray(v) for k,v in data['coupon_means'].items()},
        'coupon_probabilities':{k:np.asarray(v) for k,v in data['coupon_probabilities'].items()},'forward_calls':0}


def independent_all_design_metrics(config,numerical):
    deltas,weights=mt.residual_ensemble(config,3);grid=mt._grid(config['scoring_grid_nm'])
    result=np.empty((len(numerical['processes']),len(numerical['designs']),len(deltas)))
    for i,process in enumerate(numerical['processes']):
        for j,design in enumerate(numerical['designs']):
            for k,delta in enumerate(deltas):
                thickness=mt.actual_thicknesses(config,process,('low','high','low'),design,delta)
                spectrum=mt.independent_spectrum(process,('low','high','low'),thickness,grid)
                if not np.isfinite(spectrum).all():raise ValueError('nonfinite oracle forward; do not silently omit design')
                result[i,j,k]=np.mean(spectrum)
    return result


def run_manufacturing_sensitivity(output_dir,source_dir,plan_path):
    out=Path(output_dir);out.mkdir(parents=True,exist_ok=True)
    source=Path(source_dir);plan=json.loads(Path(plan_path).read_text());cfg=json.loads((source/'frozen_config.json').read_text())
    config=cfg['config'];tasks=cfg['tasks'];spec=plan['manufacturing_sensitivity']
    sourcehash={name:hashlib.sha256((source/name).read_bytes()).hexdigest() for name in ('frozen_config.json','public_numerical_models.json','results.json')}
    _new=mt._write_new
    _new(out/'frozen_sensitivity_config.json',{'plan_sha256':hashlib.sha256(Path(plan_path).read_bytes()).hexdigest(),
        'settings':spec,'tasks':tasks,'source_hashes':sourcehash,'retention':'all settings and source tasks retained',
        'status':'prespecified sensitivity after original outcomes, not independent confirmation',
        'source_code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    snapshot=out/'source_snapshot';snapshot.mkdir()
    for name in ('manufacturing_sensitivity.py','manufacturing_tasks.py','manufacturing_baselines.py','baseline_runners.py','protocols.py','report_core.py'):
        (snapshot/name).write_bytes((Path(__file__).parent/name).read_bytes())
    original=_load_numerical(source/'public_numerical_models.json')
    expanded_config=copy.deepcopy(config);expanded_config['design_axes_nm']=spec['nested_axes_nm']
    expanded_config['compute_caps']['policy_spectra']=100000
    expected=27*125*27+27*(3+3+27)+27*125*27+len(tasks)*len(ARMS7)*6
    if expected>spec['compute_cap_spectra']:raise ValueError('frozen sensitivity compute cap insufficient')
    start=time.perf_counter();expanded=mt.build_numerical_model(expanded_config)
    oracle_metrics=independent_all_design_metrics(expanded_config,expanded)
    numerical={'original64':original,'nested125':expanded}
    indices=[expanded['designs'].index(tuple(d)) for d in original['designs']]
    oracle_by_pool={'original64':oracle_metrics[:,indices,:],'nested125':oracle_metrics}
    _new(out/'independent_oracle_metrics.json',{'designs_nm':expanded['designs'],'metrics':oracle_metrics.tolist(),
        'residual_probabilities':expanded['future_probabilities'].tolist(),'spectrum_calls':int(oracle_metrics.size),
        'maximum_metric_disagreement_with_policy':float(np.max(abs(oracle_metrics-expanded['future_metrics']))),
        'scope':'all process/design/future-residual combinations; no future-realization oracle'})
    reports={}
    for poolname in spec['pools']:
        for multiplier in spec['utility_cost_multipliers']:
            label=f'{poolname}-cost-{multiplier:g}';directory=out/label;directory.mkdir()
            num=numerical[poolname];local=copy.deepcopy(config)
            if poolname=='nested125':local['design_axes_nm']=spec['nested_axes_nm']
            protocol=EvaluationProtocol(label,tuple(t['task_id'] for t in tasks),ARMS7,
                tuple(('task_evsi',a) for a in ARMS7 if a!='task_evsi'),local['resource_budget'],tuple(a['id'] for a in local['actions']),1.)
            protocol.freeze(directory/'protocol.json')
            _new(directory/'frozen_config.json',{'pool':poolname,'multiplier':multiplier,'config':local,'protocol_seal':protocol.seal,
                'plan_sha256':hashlib.sha256(Path(plan_path).read_bytes()).hexdigest()})
            independent=oracle_by_pool[poolname]
            scoring_cache={(i,j):tuple(independent[i,j]) for i in range(len(num['processes'])) for j in range(len(num['designs']))}
            rows=[];regrets={a:[] for a in ARMS7};counters={'purchased_spectra':0,'independent_terminal_spectra':0}
            for task in tasks:
                problem,q,actions,worlds=mt.make_problem(local,num,task['case'],task['threshold'],task['task_id'])
                actions=tuple(replace(a,loss_cost=a.loss_cost*multiplier) for a in actions)
                truth=int(np.random.default_rng(mt._seed(task['seed'],'truth')).choice(len(worlds),p=q))
                pi=worlds[truth]['process_index']
                oracle=float(np.min((independent[pi]>task['threshold'])@num['future_probabilities']))
                for arm in ARMS7:
                    service=mt.ManufacturingService(local,num,task['task_id'],task['seed'],task['case'],task['threshold'],worlds[truth],counters)
                    trace={'task_id':task['task_id'],'arm':arm,'pool':poolname,'cost_multiplier':multiplier,'receipts':[],
                           'oracle_terminal_loss':oracle,'protocol_seal':protocol.seal}
                    specs={a['id']:a for a in local['actions']};status='completed';terminal=None
                    def acquire(action):
                        receipt=service.purchase(action.action_id,f'common:{action.action_id}',specs[action.action_id])
                        trace['receipts'].append(asdict(receipt))
                        return action.observation_ids[int(np.searchsorted(mt._grid(local['bin_interior_edges']),receipt.value,side='right'))]
                    try:
                        selected,trajectory=run_policy(arm,problem,q,actions,acquire,budget=local['resource_budget'])
                        trace['trajectory']=trajectory;trace['selected_design_id']=selected
                        trace['terminal_receipt']=service.submit(selected,scoring_cache);terminal=service.evaluator_score()
                    except Exception:status='failed';trace['failure']=traceback.format_exc()
                    cost=service.evaluator_spent();loss_cost=cost*(2. if task['case']=='expensive' else local['utility_cost_per_resource'])*multiplier
                    regrets[arm].append((terminal if status=='completed' else 1.)-oracle)
                    trace.update(status=status,terminal_loss=terminal,resource_cost=cost,declared_loss_cost=loss_cost)
                    _new(directory/f"{task['task_id']}-{arm}-trace.json",trace)
                    rows.append(TaskResult(protocol.seal,task['task_id'],arm,terminal,loss_cost,cost,status,protocol.observation_permissions,digest(trace)))
            seal_results(protocol,tuple(rows),directory/'results.json')
            report=report_frozen_results(directory/'results.json')
            scaled=copy.deepcopy(local) # Same frozen bootstrap seed/stratification as source audit.
            report.update(mt.stratified_bootstrap(scaled,tasks,rows))
            report['same_pool_oracle_regret']={arm:{'mean':float(np.mean(v)),'worst':float(np.max(v))} for arm,v in regrets.items()}
            report['scope']='post-baseline sensitivity on reused tasks; no new confirmatory inference'
            _new(directory/'report.json',report);reports[label]=report
    summary={'settings':reports,'policy_spectrum_calls':expanded['forward_calls'],
        'independent_oracle_spectrum_calls':int(oracle_metrics.size),'precompute_and_run_seconds':time.perf_counter()-start,
        'maximum_policy_oracle_metric_disagreement':float(np.max(abs(oracle_metrics-expanded['future_metrics']))),
        'same_task_distribution':True,'all_six_settings_retained':len(reports)==6,
        'claim':'sensitivity and finite-pool oracle coverage; no practical superiority or external manufacturing claim'}
    _new(out/'summary.json',summary);return summary
