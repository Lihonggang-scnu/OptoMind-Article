"""Read-only audit of frozen whole-task evidence, without world execution."""
import hashlib
import json
from pathlib import Path
from .protocols import digest
from .report_core import report_frozen_results


def verify_evaluation_artifacts(directory):
    root = Path(directory)
    protocol = json.loads((root/'protocol.json').read_text())
    frozen = json.loads((root/'frozen_config.json').read_text())
    results = json.loads((root/'results.json').read_text())
    if digest(protocol['protocol']) != protocol['seal'] or protocol['seal'] != frozen['protocol_seal']:
        raise ValueError('protocol seal mismatch')
    if digest(frozen['config']) != frozen['config_seal']:
        raise ValueError('configuration modified')
    if results['protocol_seal'] != protocol['seal']:
        raise ValueError('results belong to another protocol')
    # Includes results seal, completeness and paired analysis validation.
    report_frozen_results(root/'results.json')
    config = frozen['config']
    for name, expected in config['source_hashes'].items():
        if hashlib.sha256((root/'source_snapshot'/name).read_bytes()).hexdigest() != expected:
            raise ValueError(f'source snapshot modified: {name}')
    expected_rows = {(t, a) for t in protocol['protocol']['task_ids'] for a in protocol['protocol']['arms']}
    actual_rows = [(r['task_id'], r['arm']) for r in results['rows']]
    if set(actual_rows) != expected_rows or len(actual_rows) != len(expected_rows):
        raise ValueError('task arm completeness mismatch')
    count, acquisitions = 0, 0
    for row in results['rows']:
        trace = json.loads((root/f"{row['task_id']}-{row['arm']}-trace.json").read_text())
        if digest(trace) != row['trace_hash']:
            raise ValueError('trajectory modified')
        if trace['protocol_seal'] != protocol['seal'] or trace['config_seal'] != frozen['config_seal']:
            raise ValueError('trajectory protocol/config binding mismatch')
        receipts = trace['receipts']
        if len(receipts) > config['max_measurements_per_arm']:
            raise ValueError('acquisition count exceeds frozen limit')
        cost = sum(r['cost'] for r in receipts)
        if cost != row['resource_cost'] or cost > config['resource_budget']:
            raise ValueError('actual acquisition budget mismatch')
        expected_loss_cost = cost * config['cases'][trace['case']]['loss_cost_per_resource']
        if expected_loss_cost != row['declared_loss_cost']:
            raise ValueError('utility cost mismatch')
        for receipt in receipts:
            if receipt['task_id'] != row['task_id'] or receipt['channel'] not in protocol['protocol']['observation_permissions']:
                raise ValueError('observation permission mismatch')
            if receipt['wavelengths_nm'] != config['measurement_grid_nm']:
                raise ValueError('measurement grid differs from frozen menu')
            if receipt['cost'] != config['resource_costs'][receipt['channel']]:
                raise ValueError('receipt cost differs from frozen cost')
        if row['status'] != trace['status'] or row['terminal_loss'] != trace['terminal_loss']:
            raise ValueError('trace terminal status/loss mismatch')
        if trace['forward_calls'] > config['max_forward_calls_per_arm']:
            raise ValueError('model computation bound exceeded')
        count += 1
        acquisitions += len(receipts)
    return {'verified': True, 'task_arms': count, 'acquisitions': acquisitions,
            'source_snapshot_files': len(config['source_hashes']),
            'scope': 'hash integrity, complete task arms, receipt budgets/permissions, trace binding; not external timestamp or OS security attestation'}


def verify_multilayer_artifacts(directory, *, recompute_oracle=True):
    """Audit frozen coating results; optionally independently rerun every oracle score."""
    import numpy as np
    from .multilayer_tasks import byrnes_prediction
    root=Path(directory)
    p=json.loads((root/'protocol.json').read_text())
    f=json.loads((root/'frozen_config.json').read_text())
    r=json.loads((root/'results.json').read_text())
    if digest(p['protocol'])!=p['seal'] or p['seal']!=f['protocol_seal'] or digest(f['config'])!=f['config_seal']:
        raise ValueError('coating protocol/config modified')
    report_frozen_results(root/'results.json')
    config=f['config']
    for name,expected in config['source_hashes'].items():
        if hashlib.sha256((root/'source_snapshot'/name).read_bytes()).hexdigest()!=expected:
            raise ValueError('coating source snapshot modified')
    specs={t['task_id']:t for t in config['tasks']}
    expected={(t,a) for t in specs for a in p['protocol']['arms']}
    actual=[(v['task_id'],v['arm']) for v in r['rows']]
    if len(actual)!=len(expected) or set(actual)!=expected:
        raise ValueError('coating task arms missing or duplicated')
    max_score_error=0.;acquisitions=0
    for row in r['rows']:
        trace=json.loads((root/f"{row['task_id']}-{row['arm']}-trace.json").read_text())
        if digest(trace)!=row['trace_hash'] or trace['config_seal']!=f['config_seal'] or trace['protocol_seal']!=p['seal']:
            raise ValueError('coating trajectory modified')
        rec=trace['receipts'];cost=sum(v['cost'] for v in rec)
        if len(rec)>config['max_acquisitions'] or cost!=row['resource_cost'] or cost>config['resource_budget']:
            raise ValueError('coating budget mismatch')
        if row['declared_loss_cost'] != cost*config['cases'][trace['case']]['utility_cost_per_resource']:
            raise ValueError('coating utility cost mismatch')
        for receipt in rec:
            if receipt['channel'] not in p['protocol']['observation_permissions'] or receipt['wavelengths_nm']!=config['measurement_grid_nm']:
                raise ValueError('coating observation permission mismatch')
        if trace['shared_forward_calls']>config['forward_call_cap']:
            raise ValueError('coating computational bound exceeded')
        if row['status']=='completed' and recompute_oracle:
            spec=specs[row['task_id']]
            regime=config['regimes'][int(np.random.default_rng(spec['seed']).choice(3,p=config['prior']))]
            loss=float(byrnes_prediction(regime,trace['selected_design_nm'],config['scoring_grid_nm']).mean())
            max_score_error=max(max_score_error,abs(loss-row['terminal_loss']))
            if abs(loss-row['terminal_loss'])>1e-12:
                raise ValueError('coating independent terminal score mismatch')
        acquisitions+=len(rec)
    max_pool_error=None
    if recompute_oracle:
        public=json.loads((root/'public_numerical_models.json').read_text())
        reference=np.array([[byrnes_prediction(regime,design,config['scoring_grid_nm']).mean()
                             for design in config['designs_nm']] for regime in config['regimes']])
        max_pool_error=float(np.max(abs(reference-np.array(public['losses']))))
        if max_pool_error>1e-12:
            raise ValueError('production finite pool and independent oracle disagree')
    return {'verified':True,'task_arms':len(actual),'acquisitions':acquisitions,
            'independently_recomputed_terminal_scores':recompute_oracle,'max_terminal_score_error':max_score_error,
            'max_all_192_design_regime_loss_error':max_pool_error,
            'scope':'synthetic three-layer finite pool, hash integrity, costs/permissions, independent Byrnes physics; not real measured quality'}
