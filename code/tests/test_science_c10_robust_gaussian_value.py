from dataclasses import asdict
from types import SimpleNamespace
import importlib,itertools
import numpy as np
import pytest
from scipy.stats import norm
from optomind_optics.harness.science.terminal_problem import TerminalDecisionProblem
from optomind_optics.harness.science.robust_gaussian_value import evaluate_robust_gaussian,robust_gaussian_report_from_dict
from optomind_optics.harness.science.compute_ledger import ComputeLedger,active_compute_ledger,computational_event


def problem():
    return TerminalDecisionProblem(('w0','w1'),('d0','d1','safe'),np.array([[0.,1.,.4],[1.,0.,.4]]),
        {k:'v1' for k in ('task','objective','model','sample','material','solver','fidelity','loss','split')},'loss')


def evaluate(**kwargs):
    return evaluate_robust_gaussian(problem(),np.eye(2),(-1.,1.),(1/norm.ppf(.9),),(0.,),source_ref='declared:noise-prior:v1',**kwargs)


def test_actual_robust_milp_gaussian_joint_and_rolling_semantics(tmp_path):
    ledger=ComputeLedger(tmp_path/'cost.sqlite',{'milp_solves':5})
    with active_compute_ledger(ledger):
        committed=evaluate();rolling=evaluate(commitment_mode='replanning')
    assert committed.prior_risk==pytest.approx(.4)
    assert committed.posterior_risk==pytest.approx(.1)
    assert committed.value_interval==pytest.approx((.3,.3)) and committed.confirmed_positive
    assert rolling.posterior_risk==pytest.approx(.4) and not rolling.confirmed_positive
    assert committed.solver_receipt['primal_risk']==pytest.approx(.1)
    assert committed.solver_receipt['dual_risk_bound']==pytest.approx(.1)
    assert committed.solver_receipt['status']==0 and committed.solver_receipt['deterministic_policy']
    assert ledger.snapshot()['usage']['milp_solves']==1
    assert committed.policy.choose(-.1,problem_hash=problem().cache_key,policy_hash=committed.policy_hash)=='d0'
    assert rolling.policy.choose(-.1,problem_hash=problem().cache_key,policy_hash=rolling.policy_hash)=='safe'
    assert robust_gaussian_report_from_dict(asdict(committed)).policy_hash==committed.policy_hash
    assert committed.policy_hash!=rolling.policy_hash
    ledger.close()


def test_joint_noise_vertices_match_deterministic_enumeration_without_uniform_prior():
    p=problem()
    report=evaluate_robust_gaussian(p,np.eye(2),(-1.,1.),(.7,1.4),(-.2,.2),source_ref='finite-noise-vertices')
    joint=np.array(report.diagnostics['joint_scenarios'])
    exact=min(max(sum(joint[s,w,y]*p.losses[w,policy[y]] for w in range(2) for y in range(3)) for s in range(4))
              for policy in itertools.product(range(3),repeat=3))
    assert report.posterior_risk==pytest.approx(exact)
    assert report.diagnostics['prior_vertices']==[[1.,0.],[0.,1.]]
    assert all(np.count_nonzero(vertex.sum(axis=1))==1 for vertex in joint)
    assert 'no continuum' in report.approximation_scope


def test_incomplete_or_failed_solver_is_unknown_and_charged(monkeypatch,tmp_path):
    module=importlib.import_module('optomind_optics.harness.science.robust_voi')
    ledger=ComputeLedger(tmp_path/'cost.sqlite',{'milp_solves':2})
    fake=SimpleNamespace(x=np.array([1.,0.,0.,0.,1.,0.,.1]),status=1,message='time limit',mip_gap=.1,mip_node_count=1,mip_dual_bound=0.)
    monkeypatch.setattr(module,'milp',lambda *a,**kw:fake)
    with active_compute_ledger(ledger):r=evaluate()
    assert r.status=='unknown_incomplete_milp' and not r.confirmed_positive
    assert r.solver_receipt['absolute_gap']>0
    monkeypatch.setattr(module,'milp',lambda *a,**kw:SimpleNamespace(x=None,status=2,message='failed'))
    with active_compute_ledger(ledger):failed=evaluate()
    assert failed.status=='unknown_no_certified_deterministic_policy'
    assert ledger.snapshot()['events_by_status']['failed_consumed']==1
    with active_compute_ledger(ledger):limited=evaluate()
    assert limited.status=='unknown_compute_budget_exhausted' and limited.cost_vector['milp_solves']==0
    assert ledger.snapshot()['usage']['milp_solves']==2
    assert limited.policy.choose(10.,problem_hash=problem().cache_key)=='safe'
    ledger.close()


def test_cost_and_joint_policy_receipt_binding():
    r=evaluate(cost=.4,internal_loss_cost=.1)
    assert not r.confirmed_positive and r.net_value_interval==pytest.approx((-.2,-.2))
    raw=asdict(r);raw['policy']['thresholds']=(.5,)
    with pytest.raises(ValueError,match='binding changed'):robust_gaussian_report_from_dict(raw)
    raw=asdict(r);raw['diagnostics']['joint_scenarios'][0][0][0]=.8
    with pytest.raises(ValueError,match='joint ambiguity'):robust_gaussian_report_from_dict(raw)
    with pytest.raises(ValueError,match='cost units'):evaluate(cost_units='currency')
