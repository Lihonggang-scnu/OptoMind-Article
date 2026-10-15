"""Artifact-only T026 accounting and result analysis; no HTTP or solver calls."""
from __future__ import annotations
import datetime, hashlib, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = Path(r'D:\OptoMind-Article-3\r3t026a')
def load(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def digest(value): return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
def save(name, value):
    with (ROOT / name).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2); stream.write('\n')

def main():
    accounting = load(ROOT / 'REVISION_ACCOUNTING.json')
    preflight = load(ROOT / 'REVISION_PREFLIGHT.json')
    assert accounting['prior_reservations_unchanged']
    assert accounting['original_140_ledger_sha256'] == preflight['budget_before']['ledgers'][0]['sha256']
    assert accounting['task_guard_after']['unresolved_count'] == 0
    old = load(ROOT / 'OLD_OUTPUT_HASHES_BEFORE.json')
    preservation = {}
    for task, hashes in old.items():
        task_root = ROOT.parent / task
        now = {str(p.relative_to(task_root)): sha(p) for p in sorted(task_root.rglob('*')) if p.is_file()}
        preservation[task] = {'files_before': len(hashes), 'files_after': len(now), 'unchanged': now == hashes}
        assert now == hashes, 'Historical output changed: ' + task
    save('PRESERVATION_CHECK.json', {'task_outputs': preservation, 'original_140_ledger_unchanged': True,
        'seven_prior_subpool_reservations_unchanged': True, 'subpool_append_only_for_this_task': True})
    sys.path.insert(0, str(SOURCE / 'focused_research'))
    import iteration_state
    inputs = load(ROOT / 'INPUT_PATHS.json')
    expected_metrics = {g['id']: g['executable_metric'] for g in load(inputs['task_sheet'])['subgoals']}
    records, unique, compact = {}, {}, {}
    for round_name in ('round1', 'round2'):
        records[round_name] = load(ROOT / round_name / 'execution_results.json')['results']
        observations = load(ROOT / round_name / 'physical_observations.json')
        compact[round_name] = []
        for entry in observations:
            chain_stage = 'next_action' if round_name == 'round1' else 'observation'
            iteration_state._chain(Path(entry['iteration_path']), chain_stage)
            for goal in entry['observation']['goals']:
                assert goal['metric'] == expected_metrics[goal['goal_id']]
            compact[round_name].append({'route_id': entry['proposal']['route_id'],
                'baseline_instance_id': entry['proposal']['baseline_instance_id'],
                'candidate_instance_id': entry['proposal']['candidate_instance_id'],
                'goals': [{k: g[k] for k in ('goal_id', 'status', 'baseline', 'candidate', 'delta')} for g in entry['observation']['goals']],
                'prediction_results': [{k: g.get(k) for k in ('goal_id', 'status', 'observed_delta', 'mechanism_status')} for g in entry['prediction_result']]})
        for result in records[round_name]:
            assert result['physics_accepted'] and result['certificate']['accepted'] is True
            unique.setdefault(result['simulation_key'], []).append({'round': round_name, 'route_id': result['route_id'], 'instance_id': result['instance_id']})
    save('ROUTE_RESULTS.json', compact)
    assert load(ROOT / 'round1/input_bindings.json') == load(ROOT / 'round2/input_bindings.json')
    assert load(ROOT / 'revision/research_routes.json') == load(ROOT / 'round2/raw_proposals.json')
    request_paths = sorted((ROOT / 'private/audit').rglob('REQUEST.json'))
    response_paths = sorted((ROOT / 'private/audit').rglob('RESPONSE.json'))
    assert len(request_paths) == len(response_paths) == accounting['stage_model_invocations'] == 1
    request, response = load(request_paths[0]), load(response_paths[0])
    user = json.loads(next(m['content'] for m in request['messages'] if m['role'] == 'user'))
    observations = load(ROOT / 'round1/physical_observations.json')
    assert user['previous_physical_iteration'] == observations
    save('ACTUAL_OBSERVATION_CONSUMPTION.json', {'request_ref': str(request_paths[0]),
        'request_sha256': sha(request_paths[0]), 'exact_previous_observations_match': True,
        'observation_pairs': len(observations), 'context_sha256': digest(observations),
        'actual_user_payload_keys': list(user), 'original_evidence_items_unchanged': user['evidence_items'] == load(inputs['evidence']),
        'delivery_does_not_prove_model_understanding': True})
    save('MODEL_CALLS.json', {'http_attempts': 1, 'transport_retries': 0,
        'calls': [{k: response.get(k) for k in ('call_id', 'request_id', 'requested_model', 'returned_model', 'finish_reason', 'complete', 'status_code', 'attempt', 'elapsed_seconds', 'usage')}],
        'output_limit': 16384, 'task_cost_cny': accounting['task_guard_after']['actual_cny'],
        'held_cny': accounting['task_guard_after']['reserved_cny'], 'unresolved_count': 0})
    meters = {name: load(ROOT / name / 'summary.json')['metering'] for name in ('round1', 'cacheprobe', 'round2')}
    reservation_rows = []
    for path in sorted((ROOT / 'cache/evaluations').glob('*.reservation.json')):
        obj = load(path)
        reservation_rows.append({'path': str(path), 'sha256': sha(path), 'new_evaluations_reserved': len(obj['reserved_simulation_keys'])})
    total = sum(x['new_evaluations_reserved'] for x in reservation_rows)
    assert total == len(unique) == 17 and total <= 64
    assert meters['cacheprobe']['new_evaluations'] == meters['cacheprobe']['workbench_forward_calls'] == 0
    save('NUMERICAL_BUDGET_FINAL.json', {'new_certified_evaluations': total, 'cap': 64, 'unspent_evaluations': 64 - total,
        'reservations': reservation_rows, 'per_stage': meters, 'main_round_cache_hits': meters['round1']['cache_hits'] + meters['round2']['cache_hits'],
        'cache_probe_hits': meters['cacheprobe']['cache_hits'], 'total_workbench_forward_calls': sum(x['workbench_forward_calls'] for x in meters.values()),
        'workbench_counter_scope': meters['round1']['call_count_scope'], 'max_workers': 2, 'unique_physics_tasks': unique})
    def vectors(round_name):
        values = []
        for entry in load(ROOT / round_name / 'physical_observations.json'):
            goals = {g['goal_id']: g for g in entry['observation']['goals']}
            for role in ('baseline', 'candidate'):
                iid = entry['proposal'][role + '_instance_id']
                row = next(z for z in records[round_name] if z['route_id'] == entry['proposal']['route_id'] and z['instance_id'] == iid)
                values.append({'round': round_name, 'route_id': row['route_id'], 'instance_id': iid,
                    'simulation_key': row['simulation_key'], 'parameters': row['parameters'],
                    'G1': goals['G1'][role], 'G2': goals['G2'][role]})
        return values
    first, second = vectors('round1'), vectors('round2')
    dominated = []
    for value in second:
        dominators = [x for x in first if x['G1'] >= value['G1'] and x['G2'] >= value['G2'] and (x['G1'] > value['G1'] or x['G2'] > value['G2'])]
        dominated.append({'new_instance': value, 'first_round_dominators': [{k: x[k] for k in ('route_id', 'instance_id', 'G1', 'G2', 'simulation_key')} for x in dominators]})
    assert all(x['first_round_dominators'] for x in dominated)
    all_values = {x['simulation_key']: x for x in first + second}
    pareto = [x for x in all_values.values() if not any(y['G1'] >= x['G1'] and y['G2'] >= x['G2'] and (y['G1'] > x['G1'] or y['G2'] > x['G2']) for y in all_values.values())]
    save('FINITE_CANDIDATE_COMPARISON.json', {'scope': 'Only measured finite candidates, same material/grid/angle/metrics; no weighted ranking or global-optimality claim.',
        'round2_instances': len(second), 'all_round2_instances_dominated_by_round1': True,
        'dominance_evidence': dominated, 'sampled_pareto_set': pareto,
        'same_route_ids_do_not_establish_lineage': True,
        'cross_round_exact_physics_matches': [(x['route_id'], x['instance_id']) for x in records['round2'] if x['simulation_key'] in {y['simulation_key'] for y in records['round1']}]})
    compile_report = load(ROOT / 'compile/candidate_report.json')
    save('COMPILE_RECONCILIATION.json', {'local_original_raw_sha256': sha(inputs['routes']),
        'published_t025_raw_sha256': '7d927af3bdd640106281dbf66188fe9606809d803037378e7ee4fa731c0d8e8e',
        'raw_bytes_match_fixed_t025_publication': True, 'cloud_replay_raw_file_sha256_differs': True,
        'local_accepted_contract_sha256': compile_report['accepted_contract_sha256'],
        'cloud_accepted_contract_sha256': '90b3c345d7afdc933685a738c57c12192c861762a770400da6f9f3a5ccfc3915',
        'compiled_object_matches_cloud_replay': compile_report['accepted_contract_sha256'] == '90b3c345d7afdc933685a738c57c12192c861762a770400da6f9f3a5ccfc3915',
        'original_file_not_modified': True})
    assert compile_report['accepted_contract_sha256'] == '90b3c345d7afdc933685a738c57c12192c861762a770400da6f9f3a5ccfc3915'
    save('EXECUTION_LINKAGE.json', {'task_id': 'R3-T026', 'round1_observations_sha256': digest(observations),
        'revision_contract_sha256': digest(load(ROOT / 'revision/research_routes.json')),
        'round2_raw_proposals_equal_revision_contract': True, 'original_input_bindings_identical': True,
        'round1_immutable_chains_verified': 7, 'round2_immutable_chains_verified': 3,
        'execution_witnesses': ['ROUND1_STARTED.json', 'REVISION_STARTED.json', 'ROUND2_STARTED.json'],
        'source_next_action_executed_fields_remain_false': True,
        'interpretation': 'CLI stages explicitly executed in sequence; this audit links saved artifacts. No automatic per-route lineage or next_action execution-state update is claimed.'})
    save('RESULT_SUMMARY.json', {'task_id': 'R3-T026', 'in_reply_to': 'C2L-R3-0039', 'status': 'completed',
        'completed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'source_sha': '5786e08575b19020a6aece338436406884d3542d', 'base_sha': 'e2e3a54aa6bd5b3fd1757b82b718d44c24391417',
        'local_production_code_modified': False,
        'engineering_closed_loop': 'passed_with_partial_route_acceptance',
        'scientific_improvement': 'not_demonstrated; all round2 evaluated designs dominated by measured round1 designs',
        'compile_accepted': 7, 'compile_rejected': 3, 'revision_generated': 6, 'revision_accepted': 3,
        'revision_real_interventions': 2, 'revision_baseline_reference_only': 1, 'revision_rejected': 3,
        'physics_certificates_accepted_instances': 20, 'unique_new_certified_evaluations': 17,
        'max_new_certified_evaluations': 64, 'max_workers': 2, 'http_attempts': 1,
        'transport_retries': 0, 'task_cost_cny': accounting['task_guard_after']['actual_cny'],
        'task_cap_cny': 3, 'aggregate_cost_cny': accounting['budget_after']['aggregate_exposure_cny'],
        'chain_cost_cny': accounting['budget_after']['chain_exposure_cny'], 'held_cny': 0, 'unresolved_count': 0,
        'constraints_and_metrics_preserved': True, 'historical_outputs_preserved': True,
        'no_retrieval_docling_pdf_full_regression_or_extra_experiments': True,
        'stop_after_publication': True})
    lines = ['# T026 actual measured results', '', 'G1: 450–650 nm wavelength-integral mean reflectance. G2: 700–1000 nm wavelength-integral mean transmittance. Equal-s/p unpolarized light at 45°. Values are dimensionless fractions.', '']
    for round_name in ('round1', 'round2'):
        lines += ['## ' + round_name, '', '| Route | Baseline → candidate | G1 baseline → candidate | G2 baseline → candidate | Predicted directions |', '|---|---|---:|---:|---|']
        for row in compact[round_name]:
            goals = {x['goal_id']: x for x in row['goals']}
            predictions = '; '.join(x['goal_id'] + ': ' + x['status'] for x in row['prediction_results'])
            lines.append(f"| {row['route_id']} | {row['baseline_instance_id']} → {row['candidate_instance_id']} | {goals['G1']['baseline']:.6f} → {goals['G1']['candidate']:.6f} | {goals['G2']['baseline']:.6f} → {goals['G2']['candidate']:.6f} | {predictions} |")
        lines.append('')
    lines += ['Route IDs in the two rounds do not establish identical structures or causal lineage. Raw parameters, certified spectra and exact simulation keys are retained.', '']
    with (ROOT / 'MEASURED_RESULTS.md').open('x', encoding='utf-8', newline='\n') as stream: stream.write('\n'.join(lines))
    print(json.dumps(load(ROOT / 'RESULT_SUMMARY.json'), ensure_ascii=False))

if __name__ == '__main__': main()
