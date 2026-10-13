"""Analysis consumes sealed results only; no evaluator, policy, or raw-world access."""
import math
import numpy as np
from .factorial_protocol import FACTORIAL_ARMS, read_factorial_results


def _summary(values,tail_fraction):
    a=np.asarray(values,float)
    if not a.size:return {'defined_count':0,'mean':None,'worst':None,'q90':None,'cvar_upper_tail':None}
    return {'defined_count':len(a),'mean':float(a.mean()),'worst':float(a.max()),'q90':float(np.quantile(a,.9)),
            'cvar_upper_tail':float(np.sort(a)[-max(1,math.ceil(len(a)*tail_fraction)):].mean()),'tail_fraction':tail_fraction}


def report_factorial(path):
    payload,seal=read_factorial_results(path);p=payload['protocol'];rows=payload['rows']
    tasks=p['task_ids'];mapping={(r['task_id'],r['arm']):r for r in rows}
    def total(task,arm):
        row=mapping[task,arm]
        return (row['terminal_loss'] if row['status']=='completed' else p['failure_loss'])+row['declared_loss_cost']
    values={arm:np.array([total(t,arm) for t in tasks]) for arm in FACTORIAL_ARMS}
    strata={}
    for index,label in enumerate(p['task_strata']):strata.setdefault(label,[]).append(index)
    rng=np.random.default_rng(p['bootstrap_seed'])
    picks=np.concatenate([rng.choice(ids,size=(p['bootstrap_replicates'],len(ids)),replace=True) for ids in strata.values()],axis=1)
    tests=len(p['comparisons'])+1;alpha=p['alpha']/tests
    def interval(d):
        reps=d[picks].mean(axis=1)
        return {'mean':float(d.mean()),'interval':np.quantile(reps,[alpha/2,1-alpha/2]).tolist(),
                'n_independent_tasks':len(tasks),'method':'paired_stratified_task_percentile_bootstrap',
                'bootstrap_replicates':p['bootstrap_replicates'],'minimum_important_difference':p['minimum_important_difference']}
    comparisons={f'{a} - {b}':interval(values[a]-values[b]) for a,b in p['comparisons']}
    interaction=values['M1E1']-values['M1E0']-values['M0E1']+values['M0E0']
    summaries={}
    for arm in FACTORIAL_ARMS:
        armrows=[mapping[t,arm] for t in tasks]
        metrics={}
        for key in ('terminal_loss','prediction_loss','oracle_terminal_loss','resource_cost','declared_loss_cost','wall_seconds','memory_bytes'):
            defined=[r[key] for r in armrows if r[key] is not None]
            metrics[key]={**_summary(defined,p['tail_fraction']),'unavailable_count':len(armrows)-len(defined)}
        regrets=[total(t,arm)-mapping[t,arm]['declared_loss_cost']-mapping[t,arm]['oracle_terminal_loss'] for t in tasks if mapping[t,arm]['oracle_terminal_loss'] is not None]
        metrics['same_pool_oracle_regret']=_summary(regrets,p['tail_fraction'])
        indicators={}
        for key in ('false_acceptance','constraint_violation'):
            defined=[r[key] for r in armrows if r[key] is not None]
            indicators[key]={'positive_count':sum(defined),'defined_count':len(defined),'unavailable_count':len(armrows)-len(defined),
                             'rate':float(np.mean(defined)) if defined else None}
        summaries[arm]={'total_loss':_summary(values[arm],p['tail_fraction']),'metrics':metrics,'indicators':indicators,
            'status_counts':{status:sum(r['status']==status for r in armrows) for status in ('completed','failed','abstained','budget_exceeded')},
            'input_tokens':sum(r['input_tokens'] for r in armrows),'output_tokens':sum(r['output_tokens'] for r in armrows),
            'paid_calls':sum(r['paid_calls'] for r in armrows)}
    pilot={}
    for a,b in p['comparisons']:
        d=values[a]-values[b];sd=float(np.std(d,ddof=1));margin=p['minimum_important_difference']
        needed=math.ceil(((1.959963984540054+.8416212335729143)*sd/margin)**2) if margin else None
        pilot[f'{a} - {b}']={'paired_sd':sd,'normal_approximation_N_80pct_power':needed,
            'recommended_N_with_256_cap':min(256,max(2,needed)) if needed is not None else None,
            'caveat':'heuristic diagnostic, ignores stratification and multiplicity; never resizes frozen audit; zero pilot variance is not proof of zero population variance'}
    return {'result_seal':seal,'protocol_seal':payload['protocol_seal'],'phase':p['phase'],'comparisons':comparisons,
        'interaction':{**interval(interaction),'formula':'Y11-Y10-Y01+Y00','direction':'negative favors combination for lower-is-better loss'},
        'multiplicity':f'Bonferroni across {tests} prespecified comparisons including interaction','arms':summaries,
        'baseline_parity_manifest':p['parity'],'pilot_precision_diagnostics':pilot,
        'claims':{'engineering':'completed execution and validation reported separately from scientific results',
                  'superiority':'not automatically inferred','synergy':'full arm being best is insufficient; use paired interaction interval',
                  'equivalence':'nonsignificance is not equivalence; margin and interval required',
                  'llm_attribution':'offline classical grammar runner cannot establish paid-model or free-agent contribution'},
        'statistical_unit':'independent task; never wavelength or round',
        'limits':['synthetic world distribution','small fixed sample exploratory','percentile bootstrap is not a power guarantee',
                  'all failures and abstentions retained with terminal penalty plus actual cost']}
