"""Independent artifact audit: canonical seals, physical oracle and task statistics.

Terminal references use scalar Byrnes coherent TMM rather than the evaluator's
Airy implementation. No mutation of production runs or their sealed results.
"""
import hashlib,json,sqlite3
from pathlib import Path
import numpy as np
from tmm import coh_tmm
from .protocols import digest


def _byrnes_loss(family,seed,thickness):
    rng=np.random.default_rng(seed);n=float(rng.uniform(1.6,2.3));rng.uniform(60,180)
    if family=='F6':n=1.
    if family in ('F3','F4'):n+=.035/.55**2
    value=float(coh_tmm('s',[1.,n,1.5],[np.inf,float(thickness),np.inf],0.,550.)['R'])
    if family in ('F4','F5'):value=.94*value+.015
    return (value-.1)**2


def verify_factorial_artifacts(directory):
    root=Path(directory);results=json.loads((root/'results.json').read_text());seal=results.pop('seal')
    if digest(results)!=seal or digest(results['protocol'])!=results['protocol_seal']:raise ValueError('result/protocol seal mismatch')
    p=results['protocol'];config=json.loads((root/'frozen_config.json').read_text());report=json.loads((root/'report.json').read_text())
    if report['result_seal']!=seal:raise ValueError('report bound to different result')
    if config['protocol_seal']!=results['protocol_seal']:raise ValueError('configuration protocol mismatch')
    for name,expected in p['source_hashes']:
        if hashlib.sha256((root/'source_snapshot'/name).read_bytes()).hexdigest()!=expected:raise ValueError('source snapshot hash mismatch')
    taskmeta={row['task_id']:row for row in config['tasks']};rows={(r['task_id'],r['arm']):r for r in results['rows']}
    arms=('M0E0','M1E0','M0E1','M1E1')
    if set(rows)!={(t,a) for t in p['task_ids'] for a in arms}:raise ValueError('missing task-arm outcome')
    terminal_error=oracle_error=0.;rescored=0;receipts_checked=0;common_matches=0;potential={}
    for (task,arm),row in rows.items():
        trace=json.loads((root/f'{task}-{arm}-evaluation-trace.json').read_text())
        if digest(trace)!=row['trace_hash']:raise ValueError('trace modified')
        run=root/task/arm
        if 'selected_result' in trace:
            if hashlib.sha256((run/'SCIENCE_OPEN_TRAJECTORY.json').read_bytes()).hexdigest()!=trace['production_trajectory_sha256']:raise ValueError('production trajectory modified')
            if hashlib.sha256((run/'SELECTED_RESEARCH_RESULT.json').read_bytes()).hexdigest()!=trace['production_result_sha256']:raise ValueError('production selected result modified')
            actual=_byrnes_loss(taskmeta[task]['family'],taskmeta[task]['seed'],trace['selected_design_nm'])
            oracle=min(_byrnes_loss(taskmeta[task]['family'],taskmeta[task]['seed'],d) for d in p['parity']['terminal_pool'])
            terminal_error=max(terminal_error,abs(actual-row['terminal_loss']));oracle_error=max(oracle_error,abs(oracle-row['oracle_terminal_loss']));rescored+=1
            expected_false=(trace['selected_result']['candidate_roles']['adoption_status']=='proposed_design' and trace['independent_prediction_max_abs_error']>3*np.hypot(.002,.001))
            if row['false_acceptance']!=expected_false:raise ValueError('false-acceptance indicator mismatch')
            trajectory=json.loads((run/'SCIENCE_OPEN_TRAJECTORY.json').read_text())
            if (trajectory['protocol']['model_expansion_enabled'],trajectory['protocol']['program_composition_enabled'])!=(row['model_expansion_enabled'],row['program_composition_enabled']):raise ValueError('native intervention flags mismatch')
        database=run/'provider.sqlite'
        if database.exists():
            with sqlite3.connect(f'file:{database}?mode=ro',uri=True) as connection:
                receipts=[json.loads(r[0]) for r in connection.execute('SELECT result FROM receipts')]
            cost=sum(r['cost']['cost'] for r in receipts)
            if abs(cost-row['resource_cost'])>1e-10:raise ValueError('actual receipt costs differ from retained costs')
            receipts_checked+=len(receipts)
            for receipt in receipts:
                raw=receipt.get('measurement_receipt',{})
                key=(task,raw.get('task_id'),raw.get('request_id'),raw.get('channel'),tuple(raw.get('wavelengths_nm',())))
                if key in potential:
                    if digest(raw)!=potential[key]:raise ValueError('paired common acquisition returned different raw evidence')
                    common_matches+=1
                else:potential[key]=digest(raw)
    if max(terminal_error,oracle_error)>1e-11:raise ValueError('independent Byrnes terminal/oracle disagreement')
    totals={arm:np.array([(rows[t,arm]['terminal_loss'] if rows[t,arm]['status']=='completed' else p['failure_loss'])+rows[t,arm]['declared_loss_cost'] for t in p['task_ids']]) for arm in arms}
    labels=list(dict.fromkeys(p['task_strata']));rng=np.random.default_rng(p['bootstrap_seed'])
    index_blocks=[]
    for label in labels:
        indices=np.where(np.asarray(p['task_strata'])==label)[0]
        index_blocks.append(rng.choice(indices,size=(p['bootstrap_replicates'],len(indices)),replace=True))
    indexes=np.concatenate(index_blocks,axis=1);alpha=p['alpha']/(len(p['comparisons'])+1)
    deltas={f'{a} - {b}':totals[a]-totals[b] for a,b in p['comparisons']}
    deltas['interaction']=totals['M1E1']-totals['M1E0']-totals['M0E1']+totals['M0E0']
    stat_error=0.
    for name,delta in deltas.items():
        record=report['interaction'] if name=='interaction' else report['comparisons'][name]
        interval=np.quantile(delta[indexes].mean(axis=1),[alpha/2,1-alpha/2])
        stat_error=max(stat_error,abs(float(delta.mean())-record['mean']),float(np.max(abs(interval-record['interval']))))
    if stat_error>1e-12:raise ValueError('paired/interaction bootstrap mismatch')
    return {'verified':True,'task_arm_rows':len(rows),'actual_receipts_checked':receipts_checked,'matched_common_potential_receipts':common_matches,'independently_rescored_rows':rescored,
        'maximum_terminal_loss_error':terminal_error,'maximum_oracle_error':oracle_error,'maximum_statistical_error':stat_error,
        'physical_reference':'independent scalar Byrnes versus evaluator Airy','analysis':'independent paired stratified bootstrap recomputation',
        'failure_retention':{status:sum(r['status']==status for r in rows.values()) for status in ('completed','abstained','failed','budget_exceeded')},
        'limits':['prediction-MSE traces bound by source/seals; this audit rescored terminal/oracle physics','same synthetic task distribution','no live-model comparison']}
