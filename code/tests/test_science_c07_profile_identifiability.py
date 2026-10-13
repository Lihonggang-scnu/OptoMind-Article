from dataclasses import replace
import numpy as np
import pytest
from optomind_optics.harness.science.profile_identifiability import ProfileIdentifiabilityConfig
from optomind_optics.harness.science.inference import fit_model
from optomind_optics.harness.science.model_ir import ParameterSpec
from optomind_optics.harness.science.likelihood_blocks import LikelihoodBlock
from optomind_optics.harness.science.compute_ledger import ComputeLedger,active_compute_ledger,meter_prediction
from optomind_optics.harness.science.tempered_smc import SMCConfig


class Scalar:
    model_id='scalar'
    parameter_specs=(ParameterSpec('a',-2.,2.,.1),)
    @meter_prediction
    def predict(self,p,x,**kwargs):return np.full(len(x),p['a'])


class Sum:
    model_id='sum'
    parameter_specs=(ParameterSpec('a',-2.,2.,.2),ParameterSpec('b',-2.,2.,-.2))
    @meter_prediction
    def predict(self,p,x,**kwargs):return np.full(len(x),p['a']+p['b'])


def block(value=0.,sigma=.1):
    return LikelihoodBlock('raw',(1.,2.,3.,4.),(value,)*4,tuple(map(tuple,np.eye(4)*sigma**2)),gaussian_validated=True)


def test_profile_exact_analytic_loss_and_counted_source(tmp_path):
    ledger=ComputeLedger(tmp_path/'ledger.sqlite',{'model_predictions':10000})
    cfg=ProfileIdentifiabilityConfig(grid_points=9,starts=1)
    with active_compute_ledger(ledger):
        fit=fit_model(Scalar(),[block(.5)],starts=1,bounded_uniform_prior={'a':(-2.,2.)},profile_config=cfg)
    r=fit.diagnostics['profile_identifiability']
    assert r['status']=='completed_declared_profile_search' and r['source_ref']
    for point in r['profile_points']:
        assert point['loss']==pytest.approx(4*(point['fixed_value']-.5)**2/(2*.1**2),abs=1e-9)
    assert r['support_intervals']['a'][0]<=.5<=r['support_intervals']['a'][1]
    assert fit.diagnostics['model_predict_calls']==ledger.snapshot()['usage']['model_predictions']
    ledger.close()


def test_nuisance_refitted_profile_finds_action_different_equivalent_pair():
    cfg=ProfileIdentifiabilityConfig(grid_points=5,starts=2,max_nfev=60,max_residual_evaluations=1000,equivalence_radius=.1)
    fit=fit_model(Sum(),[block()],starts=1,profile_config=cfg,terminal_action=lambda p:'left' if p['a']<0 else 'right')
    report=fit.diagnostics['profile_identifiability'];pair=report['worst_pair']
    assert pair['actions_differ'] and pair['whitened_prediction_distance']<.1
    assert pair['normalized_parameter_separation']>1.
    assert fit.identifiability.task_impact=='action_ambiguous'
    assert 'terminal_action_flip' in fit.diagnostics['escalation_reasons']
    assert fit.weights is None and fit.representation=='scenario_set'
    assert all(abs(sum(row['parameters'].values()))<.01 for row in report['profile_points'] if row['within_support_threshold'])


def test_profile_budget_exhaustion_never_becomes_identifiability_or_probability_claim():
    cfg=ProfileIdentifiabilityConfig(max_residual_evaluations=2)
    fit=fit_model(Scalar(),[block(.5)],starts=1,bounded_uniform_prior={'a':(-2.,2.)},profile_config=cfg)
    report=fit.diagnostics['profile_identifiability']
    assert report['residual_evaluations']==2 and report['status']=='unknown_search_incomplete'
    assert fit.weights is None and 'profile_search_incomplete' in fit.diagnostics['escalation_reasons']
    assert fit.identifiability.task_impact=='unknown'


def test_laplace_action_flip_triggers_real_configured_smc_or_scenario_fallback():
    cfg=ProfileIdentifiabilityConfig(grid_points=5,starts=1,loss_delta=0.,equivalence_radius=0.)
    kwargs=dict(starts=1,bounded_uniform_prior={'a':(-2.,2.)},profile_config=cfg,
                terminal_action=lambda p:'left' if p['a']<0 else 'right',seed=28)
    short=fit_model(Scalar(),[block()],smc_config=SMCConfig(particles=32,max_likelihood_calls=5),**kwargs)
    assert 'terminal_action_flip' in short.diagnostics['fast_path_rejection_reasons']
    assert short.diagnostics['smc']['likelihood_calls']==5
    assert short.weights is None and short.identifiability.task_impact=='action_ambiguous'


def test_profile_parameter_rescaling_preserves_physical_likelihood():
    class Milliscalar:
        model_id='milliscalar';parameter_specs=(ParameterSpec('a',-2000.,2000.,100.,'milliunit'),)
        def predict(self,p,x,**kwargs):return np.full(len(x),p['a']/1000.)
    cfg=ProfileIdentifiabilityConfig(grid_points=9,starts=1)
    first=fit_model(Scalar(),[block(.5)],starts=1,profile_config=cfg)
    second=fit_model(Milliscalar(),[block(.5)],starts=1,profile_config=cfg)
    a=first.diagnostics['profile_identifiability'];b=second.diagnostics['profile_identifiability']
    for row in a['profile_points']:
        match=min(b['profile_points'],key=lambda x:abs(x['normalized_fixed_value']-row['normalized_fixed_value']))
        assert match['loss']==pytest.approx(row['loss'],abs=1e-8)
        assert match['fixed_value']==pytest.approx(1000.*row['fixed_value'],abs=1e-8)


def test_profile_invalid_forward_region_stays_unknown_and_failed_cost_is_retained(tmp_path):
    class Partial(Scalar):
        @meter_prediction
        def predict(self,p,x,**kwargs):
            if p['a']>1.:raise ValueError('unsupported physical domain')
            return np.full(len(x),p['a'])
    ledger=ComputeLedger(tmp_path/'failure.sqlite',{'model_predictions':10000})
    with active_compute_ledger(ledger):
        fit=fit_model(Partial(),[block(.5)],starts=1,profile_config=ProfileIdentifiabilityConfig(grid_points=5,starts=1),
                      bounded_uniform_prior={'a':(-2.,2.)})
    report=fit.diagnostics['profile_identifiability']
    assert report['status']=='unknown_search_incomplete'
    assert any(row.get('errors') and row['loss'] is None for row in report['profile_points'])
    assert ledger.snapshot()['events_by_status']['failed_consumed']>0
    assert fit.weights is None
    ledger.close()


def test_nonconverged_reference_is_not_profile_identifiability_certificate():
    fit=fit_model(Scalar(),[block(1.)],starts=1,max_nfev=1,profile_config=ProfileIdentifiabilityConfig(grid_points=3,starts=1))
    report=fit.diagnostics['profile_identifiability']
    assert not report['reference_point_converged']
    assert report['status']=='unknown_search_incomplete' and report['task_impact']=='unknown'
