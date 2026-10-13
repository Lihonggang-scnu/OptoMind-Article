"""Independent read-only physics/contract audit of formal entry artifacts."""
from __future__ import annotations
import csv
import json
from pathlib import Path
import sys

import numpy as np
from tmm import coh_tmm
from optomind_optics.harness.research_charter import build_research_charter
from optomind_optics.harness.task_compiler import _check_task_spec_charter_drift
from optomind_optics.harness.scoring_standard import ScoringStandard, _orientation_errors


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def main():
    run = Path(sys.argv[1]).resolve()
    request = load(run / 'REQUEST.json')['question']
    result = load(run / 'RESEARCH_RESULT.json')
    answer = load(run / 'FINAL_ANSWER.json')
    history = load(run / 'ITERATION_HISTORY.json')
    charter = build_research_charter(request, {})
    checked, rounds, errors = [], [], []
    for row in history:
        iteration = row['iteration_id']
        directory = run / 'iterations' / iteration
        task_file = directory / 'COMPILED_TASK.json'
        if not task_file.is_file():
            rounds.append({'iteration_id': iteration, 'compiled': False, 'run_status': row['run_status']})
            continue
        task = load(task_file)
        _check_task_spec_charter_drift({'task': task}, charter)
        count = row.get('budget_usage', {}).get('forward_evaluations', 0)
        assert count <= task['budget']['maximum_forward_evaluations'] <= 64
        assert task['uncertainty']['random_seed'] == 17
        snapshot = row.get('route_snapshot') or {}
        saved_route = load(directory / 'ROUTE.json')
        assert snapshot == saved_route, 'Historical route snapshot mismatch'
        rounds.append({'iteration_id': iteration, 'compiled': True, 'run_status': row['run_status'],
                       'forward_evaluations': count, 'cap': task['budget']['maximum_forward_evaluations'],
                       'seed': task['uncertainty']['random_seed'], 'route_snapshot_matches': True})
        experiments = {item['experiment_id']: item for item in task['experiments']}
        for candidate in row.get('candidate_summaries', []):
            candidate_id, experiment_id = candidate['candidate_id'], candidate['experiment_id']
            raw = experiments[experiment_id]['tmm_task']
            simulation = raw.get('simulation', raw)
            stack = simulation['stack']
            thicknesses = candidate['thicknesses_nm']
            assert len(thicknesses) == 2 and all(40 <= d <= 160 for d in thicknesses)
            indices = [layer['constant_n'] for layer in stack['layers']]
            assert sorted(indices) == [1.46, 2.4]
            artifacts = directory / 'tmm_run' / 'experiments' / experiment_id
            path = artifacts / 'candidates' / candidate_id / 'SIMULATION_RESULT.json'
            if not path.is_file() and candidate_id.endswith('__baseline'):
                path = artifacts / 'baseline' / 'SIMULATION_RESULT.json'
            if not path.is_file():
                # Windows uses stable hashed directories for long logical IDs.
                # Resolve by the stored identity, never by directory order.
                matches = [identity.parent / 'SIMULATION_RESULT.json'
                    for identity in artifacts.rglob('IDENTITY.json')
                    if load(identity).get('candidate_id') == candidate_id
                    and load(identity).get('experiment_id') == experiment_id]
                assert len(matches) == 1, (candidate_id, matches)
                path = matches[0]
            actual = load(path)
            waves = np.asarray(actual['wavelengths_nm'], dtype=float)
            assert np.array_equal(waves, np.arange(450., 701., 5.))
            reference = np.asarray([coh_tmm('s', [1., *indices, 1.52],
                [np.inf, *thicknesses, np.inf], 0., wavelength)['R'] for wavelength in waves])
            observed = np.asarray(actual['channels']['angle=0|pol=s']['R'])
            max_error = float(np.max(np.abs(reference - observed)))
            assert max_error <= 1e-8, max_error
            means = {'sample_mean': float(reference.mean()),
                     'wavelength_integral_mean': float(np.trapezoid(reference, waves) / (waves[-1] - waves[0]))}
            metrics = []
            for metric_id, metric in candidate['objective_report']['target_attainment'].items():
                if metric.get('metric') != 'mean_reflectance':
                    continue
                reduction = metric['mean_reduction']
                error = abs(metric['observed'] - means[reduction])
                assert error <= 1e-12, (metric_id, reduction, error)
                metrics.append({'metric_id': metric_id, 'reduction': reduction,
                                'reported': metric['observed'], 'independent': means[reduction]})
            checked.append({'iteration_id': iteration, 'candidate_id': candidate_id,
                'layer_indices': indices, 'thicknesses_nm': thicknesses, 'max_absolute_error': max_error,
                **means, 'target_below_0_03_met_sample_mean': means['sample_mean'] < 0.03,
                'metrics': metrics, 'simulation_result': str(path.relative_to(run))})
            csv_path = directory / ('INDEPENDENT_' + candidate_id + '.csv')
            with csv_path.open('w', newline='', encoding='utf-8') as handle:
                writer = csv.writer(handle)
                writer.writerow(['wavelength_nm', 'VeriTMM_R', 'independent_tmm_R'])
                writer.writerows(zip(waves, observed, reference))
    recommended = answer.get('recommended_candidates') or []
    checked_ids = {row['candidate_id'] for row in checked}
    assert all(row['candidate_id'] in checked_ids for row in recommended)
    standard_payload = load(run / 'SCORING_STANDARD.json').get('standard')
    direction_errors = []
    if standard_payload:
        standard = ScoringStandard.model_validate(standard_payload)
        direction_errors = list(_orientation_errors(standard.compiled(), standard.metrics))
    output = {'run': str(run), 'status': result['status'], 'acceptance_status': result['acceptance_status'],
        'independent_scientific_acceptance': 'not_accepted_scoring_direction' if direction_errors else ('accepted_for_this_case' if checked else 'unverified'),
        'scoring_direction_errors': direction_errors,
        'failed_iteration_ids': result.get('failed_iteration_ids'), 'rounds': rounds,
        'independently_checked_candidates': checked, 'recommended_candidates': recommended,
        'numeric_tolerance': 1e-8, 'metric_tolerance': 1e-12,
        'audit_solver_work_excluded_from_production_budget': True,
        'scope': 'same frozen two-layer constant-index case; no manufactured or experimental validation'}
    (run / 'INDEPENDENT_ACCEPTANCE.json').write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': output['status'], 'acceptance_status': output['acceptance_status'],
        'independent_scientific_acceptance': output['independent_scientific_acceptance'],
        'scoring_direction_errors': direction_errors,
        'rounds': rounds, 'checked_candidates': len(checked),
        'recommended_ids': [row['candidate_id'] for row in recommended],
        'max_absolute_error': max((row['max_absolute_error'] for row in checked), default=None)}, indent=2))


if __name__ == '__main__':
    main()
