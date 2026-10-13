from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.stats import t

from .protocols import digest


def _interval(values, alpha):
    values = np.asarray(values, dtype=float)
    mean = float(values.mean())
    half = float(t.ppf(1-alpha/2, len(values)-1) * values.std(ddof=1) / np.sqrt(len(values)))
    return {'mean': mean, 'interval': [mean-half, mean+half], 'n_independent_tasks': len(values),
            'method': 'paired_task_student_t', 'assumption': 'independent tasks; finite variance; small samples exploratory'}


def report_frozen_results(path: str | Path, *, interaction_arms: tuple[str, str, str, str] | None = None):
    payload = json.loads(Path(path).read_text())
    seal = payload.pop('seal')
    if digest(payload) != seal or digest(payload['protocol']) != payload['protocol_seal']:
        raise ValueError('frozen evaluation evidence was modified')
    protocol = payload['protocol']
    tasks = protocol['task_ids']
    rows = {(r['task_id'], r['arm']): r for r in payload['rows']}
    def score(task, arm):
        row = rows[(task, arm)]
        loss = row['terminal_loss'] if row['status'] == 'completed' else protocol['failure_loss']
        return loss + row['declared_loss_cost']
    comparisons = {}
    count = max(1, len(protocol['comparisons']))
    for a, b in protocol['comparisons']:
        comparisons[f'{a} - {b}'] = _interval([score(task,a)-score(task,b) for task in tasks], protocol['alpha']/count)
    report = {'result_seal': seal, 'protocol_seal': payload['protocol_seal'], 'comparisons': comparisons,
              'multiplicity': 'Bonferroni across preregistered comparisons',
              'failures': {arm: sum(rows[(task,arm)]['status'] != 'completed' for task in tasks) for arm in protocol['arms']},
              'resource_costs': {arm: sum(rows[(task,arm)]['resource_cost'] for task in tasks) for arm in protocol['arms']},
              'interpretation': 'Intervals are conditional on the declared task sample; no automatic scientific superiority claim.'}
    if interaction_arms is not None:
        if len(set(interaction_arms)) != 4 or any(arm not in protocol['arms'] for arm in interaction_arms):
            raise ValueError('interaction requires four distinct evaluated factorial arms')
        a00,a10,a01,a11=interaction_arms
        report['exploratory_interaction'] = _interval([score(task,a11)-score(task,a10)-score(task,a01)+score(task,a00) for task in tasks],protocol['alpha'])
        report['exploratory_interaction']['confirmatory'] = False
    return report
