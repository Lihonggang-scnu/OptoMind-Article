from dataclasses import asdict,replace
import numpy as np
import pytest
from scipy.stats import norm
from optomind_optics.harness.science.terminal_problem import TerminalDecisionProblem
from optomind_optics.harness.science.gaussian_terminal_value import (
    GaussianAction,GaussianValueProtocol,evaluate_gaussian_actions,gaussian_value_report_from_dict)
from optomind_optics.harness.science.compute_ledger import ComputeLedger,active_compute_ledger


def problem():
    return TerminalDecisionProblem(('w0','w1'),('d0','d1'),np.array([[0.,1.],[1.,0.]]),
        {k:'v1' for k in ('task','objective','model','sample','material','solver','fidelity','loss','split')},'loss')


def protocol(**kwargs):
    return GaussianValueProtocol(proposal_samples=(512,2048),confirmation_samples=(2048,4096),**kwargs)


def test_continuous_gaussian_matches_analytic_and_independent_executable_policy(tmp_path):
    p=problem();ledger=ComputeLedger(tmp_path/'cost.sqlite',{'valuation_samples':20000})
    action=GaussianAction('measure',(-1.,1.),1.,source_ref='noise:known')
    with active_compute_ledger(ledger): r=evaluate_gaussian_actions(p,(.5,.5),(action,),protocol())
    expected=.5-norm.cdf(-1.)
    assert r.value_interval[0] <= expected <= r.value_interval[1]
    assert r.confirmed_positive and r.selected_action_id=='measure'
    assert r.policy.choose(-.01,problem_hash=p.cache_key)=='d0'
    assert r.policy.choose(.01,problem_hash=p.cache_key)=='d1'
    restored=gaussian_value_report_from_dict(asdict(r))
    assert restored.policy.policy_hash==r.policy.policy_hash
    assert restored.policy.choose(.7,problem_hash=p.cache_key)=='d1'
    assert ledger.snapshot()['usage']['valuation_samples']==r.cost_vector['valuation_samples']
    assert r.diagnostics['stages'][0]['confirmation']['confirmation_split_id'].startswith('confirmation:')
    with pytest.raises(ValueError):r.policy.choose(-1,problem_hash='stale')
    with pytest.raises(ValueError):r.policy.choose({'world':0},problem_hash=p.cache_key)
    ledger.close()


def test_frozen_ranking_refinement_unknown_and_too_expensive():
    p=problem();a=GaussianAction('measure',(-1.,1.),1.)
    r=evaluate_gaussian_actions(p,(.5,.5),(a,replace(a,action_id='tied')),protocol())
    assert not r.confirmed_positive and r.status=='unknown_insufficient_precision'
    assert len(r.diagnostics['stages'])==2
    expensive=evaluate_gaussian_actions(p,(.5,.5),(replace(a,loss_cost=.9),),protocol())
    assert expensive.status=='no_positive_value_confirmed' and expensive.net_value_interval[1]<0
    useless=evaluate_gaussian_actions(p,(.5,.5),(replace(a,means=(0.,0.)),),protocol())
    assert not useless.confirmed_positive
    assert useless.value_interval[0]<0  # Negative lower estimates are not clipped away.


def test_preregistered_split_seed_ess_and_compute_budget_unknown(tmp_path):
    with pytest.raises(ValueError,match='streams overlap'):protocol(proposal_seed=1,confirmation_seed=2)
    with pytest.raises(ValueError,match='distinct split'):protocol(proposal_split_id='same',confirmation_split_id='same')
    p=problem();a=GaussianAction('measure',(-1.,1.),1.)
    with pytest.raises(ValueError,match='fresh splits'):
        evaluate_gaussian_actions(p,(.5,.5),(a,),protocol(),used_split_ids=('confirmation',))
    low=evaluate_gaussian_actions(p,(.5,.5),(a,),protocol(support_semantics='posterior_sample_approximation',min_world_ess=1.9))
    assert low.status=='unknown_refine_posterior_world_support'
    ledger=ComputeLedger(tmp_path/'small.sqlite',{'valuation_samples':7})
    with active_compute_ledger(ledger):r=evaluate_gaussian_actions(p,(.5,.5),(a,),protocol())
    assert r.status=='unknown_compute_budget_exhausted' and not r.confirmed_positive
    assert ledger.snapshot()['usage']['valuation_samples']==r.cost_vector['valuation_samples']==7
    ledger.close()


def test_internal_computation_can_erase_positive_value():
    p=problem();a=GaussianAction('measure',(-1.,1.),1.)
    unpriced=evaluate_gaussian_actions(p,(.5,.5),(a,),protocol())
    priced=evaluate_gaussian_actions(p,(.5,.5),(a,),protocol(evaluation_loss_cost_per_sample=.001,cost_ref='compute:conversion:v1'))
    assert unpriced.confirmed_positive
    assert priced.value_interval[0]>0 and priced.net_value_interval[1]<0 and not priced.confirmed_positive
    assert priced.cost_vector['internal_loss_cost']==pytest.approx(priced.cost_vector['valuation_samples']*.001)
    assert priced.protocol_hash!=unpriced.protocol_hash


def test_serialized_policy_and_protocol_tampering_rejected():
    p=problem();r=evaluate_gaussian_actions(p,(.5,.5),(GaussianAction('measure',(-1.,1.),1.),),protocol())
    raw=asdict(r);raw['policy']['means']=(0.,0.)
    with pytest.raises(ValueError,match='binding changed'):gaussian_value_report_from_dict(raw)
    raw=asdict(r);raw['diagnostics']['protocol']['confirmation_seed']+=99
    with pytest.raises(ValueError,match='protocol changed'):gaussian_value_report_from_dict(raw)


def test_precomputation_cost_conversion_is_charged_and_identity_bound():
    p=problem();a=GaussianAction('measure',(-1.,1.),1.)
    r=evaluate_gaussian_actions(p,(.5,.5),(a,),protocol(precomputation_loss_cost=1.,cost_ref='ledger:rates:v1'))
    assert r.cost_vector['internal_loss_cost']==1.
    assert r.cost_vector['precomputation_loss_cost']==1.
    assert not r.confirmed_positive and r.net_value_interval[1]<0
