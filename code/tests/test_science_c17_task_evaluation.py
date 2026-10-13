import json
import numpy as np
import pytest
from optomind_eval.baseline_runners import entropy_gain
from optomind_eval.task_runner import default_config, build_public_problem, run_evaluation
from optomind_eval.protocols import digest


def test_public_physical_problem_has_gaussian_normalized_kernels():
    config = default_config(2)
    problem, actions, designs, numerical = build_public_problem(config, 'unseen-test', 'useful')
    assert len(designs) == 5
    assert numerical['forward_calls'] <= config['max_forward_calls_per_arm']
    assert np.allclose(np.diag(problem.losses[:, :3]), 0.)
    assert np.max(problem.losses) > .001
    for action in actions:
        assert np.allclose(np.sum(action.kernel, axis=1), 1.)
        assert entropy_gain(config['prior'], action.kernel) > 0


def test_forward_tasks_complete_and_preserve_all_receipts_and_seals(tmp_path):
    report = run_evaluation(tmp_path, tasks_per_case=2, seed_base=910000)
    payload = json.loads((tmp_path/'results.json').read_text())
    assert len(payload['rows']) == 48
    assert sum(report['failures'].values()) == 0
    for row in payload['rows']:
        trace = json.loads((tmp_path/f"{row['task_id']}-{row['arm']}-trace.json").read_text())
        assert digest(trace) == row['trace_hash']
        assert row['resource_cost'] == sum(r['cost'] for r in trace['receipts'])
        assert len(trace['receipts']) <= 1
        assert trace['terminal_receipt']['status'] == 'sealed'
        assert set(trace['terminal_receipt']) == {'submission_id', 'status', 'sealed_digest'}
    assert report['case_descriptives']['expensive']['task_evsi']['mean_resource_cost'] == 0
    with pytest.raises(FileExistsError):
        run_evaluation(tmp_path, tasks_per_case=2, seed_base=910000)


def test_artifact_audit_rejects_modified_trajectory(tmp_path):
    from optomind_eval.audit_artifacts import verify_evaluation_artifacts
    run_evaluation(tmp_path, tasks_per_case=2, seed_base=920000)
    assert verify_evaluation_artifacts(tmp_path)['task_arms'] == 48
    path = next(tmp_path.glob('*-trace.json'))
    trace = json.loads(path.read_text())
    trace['wall_seconds'] += 1
    path.write_text(json.dumps(trace))
    with pytest.raises(ValueError, match='trajectory modified'):
        verify_evaluation_artifacts(tmp_path)


def test_all_failed_tasks_and_paid_costs_are_retained(tmp_path, monkeypatch):
    import optomind_eval.task_runner as runner
    def failing_policy(arm, problem, prior, actions, acquire, *, budget):
        acquire(actions[0])
        raise RuntimeError('deliberate post-acquisition failure')
    monkeypatch.setattr(runner, 'run_policy', failing_policy)
    report = runner.run_evaluation(tmp_path, tasks_per_case=2, seed_base=930000)
    results = json.loads((tmp_path/'results.json').read_text())
    assert len(results['rows']) == 48
    assert sum(report['failures'].values()) == 48
    assert all(r['terminal_loss'] is None and r['resource_cost'] == 1 for r in results['rows'])
    for values in report['case_descriptives'].values():
        assert all(x['mean_total_loss'] > 1 for x in values.values())
