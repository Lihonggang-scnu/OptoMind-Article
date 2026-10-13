import numpy as np
import pytest
from optomind_optics.harness.science.inference import fit_model
from optomind_optics.harness.science.model_ir import ParameterSpec
from optomind_optics.harness.science.likelihood_blocks import LikelihoodBlock
from optomind_optics.harness.science.tempered_smc import SMCConfig


class Square:
    model_id='square'
    parameter_specs=(ParameterSpec('a',-3.,3.,.3),)
    def predict(self,p,x,**kwargs): return np.full(len(x),p['a']**2)


def block(validated=True):
    return LikelihoodBlock('obs',(500.,600.),(1.,1.),((.04,0.),(0.,.04)),gaussian_validated=validated)


def test_multimodal_fit_escalates_to_real_smc_only_with_explicit_prior():
    config=SMCConfig(particles=256,rejuvenation_steps=8,max_likelihood_calls=100000)
    fit=fit_model(Square(),[block()],starts=12,bounded_uniform_prior={'a':(-3.,3.)},smc_config=config,seed=12)
    assert fit.representation == 'conditional_bayes', fit.diagnostics
    assert fit.weights is not None and sum(fit.weights)==pytest.approx(1)
    assert 'multiple_basins' in fit.diagnostics['fast_path_rejection_reasons']
    assert fit.diagnostics['smc']['likelihood_calls'] <= config.max_likelihood_calls
    negative=sum(w for s,w in zip(fit.samples,fit.weights) if s['a']<0)
    assert .35 < negative < .65
    assert fit.diagnostics['smc']['status']=='replicated_diagnostics_passed'


def test_missing_prior_preserves_fit_without_inventing_probabilities():
    fit=fit_model(Square(),[block()],starts=8,smc_config=SMCConfig(particles=32))
    assert fit.success and fit.loss < 1e-12
    assert fit.representation=='scenario_set' and fit.weights is None
    assert 'missing_explicit_prior' in fit.diagnostics['escalation_reasons']
    assert 'smc' not in fit.diagnostics


def test_invalid_noise_prevents_smc_and_budget_failure_stays_scenarios():
    prior={'a':(-3.,3.)};config=SMCConfig(particles=32,max_likelihood_calls=10)
    unknown=fit_model(Square(),[block(False)],starts=8,bounded_uniform_prior=prior,smc_config=config)
    assert unknown.weights is None and 'smc' not in unknown.diagnostics
    bounded=fit_model(Square(),[block()],starts=8,bounded_uniform_prior=prior,smc_config=config)
    assert bounded.representation=='scenario_set' and bounded.weights is None
    assert bounded.diagnostics['smc']['likelihood_calls']==10
    assert 'smc_inconclusive' in bounded.diagnostics['escalation_reasons']


@pytest.mark.parametrize('prior',[{'wrong':(-3.,3.)},{'a':(-4.,3.)},{'a':(1.,0.)},{'a':(float('nan'),3.)}])
def test_bad_or_outside_domain_priors_are_rejected(prior):
    with pytest.raises(ValueError): fit_model(Square(),[block()],bounded_uniform_prior=prior)


def test_subset_prior_requires_sampling_not_full_bounds_laplace():
    fit=fit_model(Square(),[block()],starts=8,bounded_uniform_prior={'a':(.5,1.5)})
    assert fit.representation=='scenario_set'
    assert 'prior_bounds_require_sampling' in fit.diagnostics['escalation_reasons']


def test_actual_forward_call_count_includes_numerical_jacobians():
    class Counting(Square):
        calls=0
        def predict(self,p,x,**kwargs):
            self.calls+=1
            return super().predict(p,x,**kwargs)
    model=Counting()
    fit=fit_model(model,[block()],starts=2)
    assert fit.diagnostics['model_predict_calls']==model.calls
    assert model.calls > fit.diagnostics['nfev']


def test_subset_prior_posterior_uses_feasible_sample_point_not_outside_fit():
    class Linear:
        model_id='linear'
        parameter_specs=(ParameterSpec('a',-3.,3.,.3),)
        def predict(self,p,x,**kwargs): return np.full(len(x),p['a'])
    raw=LikelihoodBlock('linear',(500.,),(1.,),((.25,),),gaussian_validated=True)
    fit=fit_model(Linear(),[raw],starts=2,bounded_uniform_prior={'a':(-1.,0.)},smc_config=SMCConfig(particles=256),seed=3)
    assert fit.representation=='conditional_bayes',fit.diagnostics
    assert -1 <= fit.parameters['a'] <= 0
    assert all(-1 <= s['a'] <= 0 for s in fit.samples)
    assert fit.diagnostics['point_estimate_source']=='maximum_likelihood_sample_within_prior'


def test_smc_missing_known_admissible_mode_is_not_published(monkeypatch):
    from optomind_optics.harness.science import tempered_smc
    fake=tempered_smc.SMCResult(({'a':.99},{'a':1.01}),(.5,.5),True,{'likelihood_calls':0},(0.,0.))
    monkeypatch.setattr(tempered_smc,'run_tempered_smc',lambda *a,**k:fake)
    fit=fit_model(Square(),[block()],starts=16,bounded_uniform_prior={'a':(-3.,3.)},smc_config=SMCConfig(particles=32),seed=0)
    assert fit.representation=='scenario_set' and fit.weights is None
    assert 'known_basin_unrepresented' in fit.diagnostics['escalation_reasons']


def test_smc_coverage_does_not_require_modes_excluded_by_declared_prior(monkeypatch):
    from optomind_optics.harness.science import tempered_smc
    fake=tempered_smc.SMCResult(({'a':.99},{'a':1.01}),(.5,.5),True,{'likelihood_calls':0},(0.,0.))
    monkeypatch.setattr(tempered_smc,'run_tempered_smc',lambda *a,**k:fake)
    fit=fit_model(Square(),[block()],starts=16,bounded_uniform_prior={'a':(.5,1.5)},smc_config=SMCConfig(particles=32),seed=0)
    assert fit.representation=='conditional_bayes'
    checks=fit.diagnostics['known_basin_coverage']['checks']
    assert any(not check['in_prior'] for check in checks)
