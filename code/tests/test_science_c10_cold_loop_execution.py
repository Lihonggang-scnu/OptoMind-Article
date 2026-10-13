"""Cold-loop protocol frozen before outcomes; no provider/model calls outside V1 fixture."""
import pytest
from optomind_optics.harness.science.open_research_cli import build_offline_loop


@pytest.mark.parametrize('estimator,outcome',[
    ('gaussian_mc','committed_continuous_gaussian_continuation_completed'),
    ('robust_gaussian_bins','committed_robust_gaussian_continuation_completed')])
def test_cold_loop_uses_declared_value_and_frozen_scalar_continuation(tmp_path,estimator,outcome):
    # Reuse the pre-existing C13 task-valued optical fixture; no search over worlds/seeds.
    config={'measurement_std':.004,'objective_target':.1,'action_loss_cost':0.,
        'design_thicknesses_nm':[135+i*.1 for i in range(121)],'max_iterations':1,'unknown_exploration_limit':0,
        'value_estimator':estimator,
        'gaussian_value_protocol':{'proposal_samples':[4096,16384],'confirmation_samples':[8192,32768],
            'proposal_seed':1001,'confirmation_seed':1000001,'support_semantics':'posterior_sample_approximation','min_world_ess':1.},
        'compute_valuation_sample_limit':100000,'compute_utility_source':'explicit-zero-loss-compute-pricing-for-execution-fixture'}
    loop,resources=build_offline_loop(tmp_path,{'family':'F0','world_seed':13,'config':config})
    try:
        result=loop.run(tmp_path)
        decision=loop._find('offline-open-research:iteration:0:decision')
        value=decision.summary['valuation']
        report=value['gaussian_report' if estimator=='gaussian_mc' else 'robust_report']
        # This preregistered fixture must report whether precision suffices; no tuning after outcome.
        if not report['confirmed_positive']:
            assert not value['decision']['selected_actions']
            assert decision.summary['action']=='stop'
            assert 'unknown' in report['status'] or report['net_value_interval'][0]<=0
        else:
            continuation=loop._find('offline-open-research:iteration:0:continuation')
            assert decision.summary['action']=='task_valued_measurement'
            assert continuation.summary['outcome']==outcome
            assert result.candidate_roles['terminal_choice']==continuation.summary['selected_design_id']
            assert continuation.summary['policy_hash']==report['policy_hash']
            assert loop.runner.gates['R'].usage()['calls']==3
        assert report['cost_vector']['valuation_samples' if estimator=='gaussian_mc' else 'milp_solves']>0
        # Full cold-loop completion and replay must not repurchase or recompute.
        usage=loop.compute_ledger.snapshot()['usage'].copy()
        assert loop.run(tmp_path).ref()==result.ref()
        assert loop.compute_ledger.snapshot()['usage']==usage
    finally:
        for resource in resources:resource.close()
