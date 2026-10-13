"""Independent analytic/quadrature references for bounded-prior SMC."""
import numpy as np
import pytest
from scipy.integrate import quad
from scipy.special import logsumexp
from scipy.stats import norm, truncnorm
from optomind_optics.harness.science.tempered_smc import SMCConfig, run_tempered_smc, _systematic_resample


def moments(result):
    x = np.array([s['x'] for s in result.samples])
    w = np.array(result.weights)
    return x, float(w @ x), float(w @ (x * x))


@pytest.mark.parametrize('seed', [7, 81, 902])
def test_linear_gaussian_matches_truncated_analytic_reference(seed):
    mu, sigma, lo, hi = .35, .12, 0., 1.
    result = run_tempered_smc(lambda p: norm.logpdf(p['x'], mu, sigma), {'x': (lo, hi)}, config=SMCConfig(particles=512), seed=seed)
    assert result.validated, result.diagnostics
    x, mean, second = moments(result)
    ref = truncnorm((lo-mu)/sigma, (hi-mu)/sigma, loc=mu, scale=sigma)
    assert abs(mean-ref.mean()) < .015
    assert abs(second-(ref.var()+ref.mean()**2)) < .012
    log_z = np.log(norm.cdf((hi-mu)/sigma)-norm.cdf((lo-mu)/sigma)) - np.log(hi-lo)
    assert max(abs(np.array(result.diagnostics['log_evidence_estimates'])-log_z)) < .16
    assert np.all((x >= lo) & (x <= hi))


@pytest.mark.parametrize('seed', [3, 29, 130])
def test_multimodal_mass_matches_independent_quadrature(seed):
    # Separated unequal modes: recovering mean alone is not enough.
    def likelihood(x):
        return .3 * norm.pdf(x, -.65, .06) + .7 * norm.pdf(x, .55, .09)
    result = run_tempered_smc(lambda p: np.log(likelihood(p['x'])), {'x': (-1., 1.)}, seed=seed)
    assert result.validated, result.diagnostics
    x, mean, _ = moments(result)
    z = quad(likelihood, -1, 1, epsabs=1e-11)[0]
    mass = quad(likelihood, -1, 0, epsabs=1e-11)[0]/z
    refmean = quad(lambda v: v*likelihood(v), -1, 1, epsabs=1e-11)[0]/z
    assert abs(np.array(result.weights) @ (x < 0)-mass) < .065
    assert abs(mean-refmean) < .07
    assert max(abs(np.array(result.diagnostics['log_evidence_estimates'])-np.log(z/2))) < .2


@pytest.mark.parametrize('seed', [2, 71, 120])
def test_boundary_posterior_matches_analytic_truncation(seed):
    mu, sigma = -.08, .1
    result = run_tempered_smc(lambda p: norm.logpdf(p['x'], mu, sigma), {'x': (0., 1.)}, seed=seed)
    assert result.validated, result.diagnostics
    x, mean, _ = moments(result)
    ref = truncnorm(-mu/sigma, (1-mu)/sigma, loc=mu, scale=sigma)
    assert abs(mean-ref.mean()) < .008
    assert abs(np.array(result.weights) @ (x < .05) - ref.cdf(.05)) < .055
    assert max(abs(np.array(result.diagnostics['log_evidence_estimates'])-np.log(norm.sf(-mu/sigma)))) < .2


def test_constant_likelihood_log_evidence_and_units():
    r = run_tempered_smc(lambda p: -2.3, {'x': (100., 700.)}, seed=33)
    assert r.validated
    assert np.allclose(r.diagnostics['log_evidence_estimates'], -2.3)
    assert np.isclose(sum(r.weights), 1.)
    assert r.diagnostics['likelihood_calls'] == 6 * 256 * 9
    # Prior integrates to one; do not insert a spurious coordinate-volume factor.


def test_budget_is_strict_and_never_publishes_probability():
    count = [0]
    def loglik(p):
        count[0] += 1
        return -1000 * (p['x']-.4)**2
    r = run_tempered_smc(loglik, {'x': (0., 1.)}, config=SMCConfig(max_likelihood_calls=300))
    assert count[0] == r.diagnostics['likelihood_calls'] == 300
    assert not r.validated and r.weights is None
    assert 'likelihood_budget_exhausted' in r.diagnostics['reasons']


def test_all_zero_likelihood_is_inconclusive():
    r = run_tempered_smc(lambda p: -np.inf, {'x': (0., 1.)})
    assert not r.validated and r.weights is None
    assert not r.samples


def test_nonfinite_likelihood_and_missing_prior_are_rejected():
    with pytest.raises(ValueError, match='explicit'):
        run_tempered_smc(lambda p: 0., {})
    for bad in (np.nan, np.inf):
        with pytest.raises(ValueError, match='NaN or positive'):
            run_tempered_smc(lambda p: bad, {'x': (0., 1.)})


def test_support_hole_and_max_stages_fail_closed():
    r = run_tempered_smc(lambda p: 0. if p['x'] < .2 else -np.inf, {'x': (0., 1.)}, seed=17)
    assert r.validated, r.diagnostics
    assert all(s['x'] < .2 for s in r.samples)
    assert abs(np.mean(r.diagnostics['log_evidence_estimates'])-np.log(.2)) < .18
    short = run_tempered_smc(lambda p: -1000*(p['x']-.2)**2, {'x': (0., 1.)}, config=SMCConfig(max_stages=1))
    assert not short.validated and short.weights is None


def test_replicate_disagreement_is_not_convergence():
    r = run_tempered_smc(lambda p: -50*(p['x']-.2)**2, {'x': (0., 1.)}, config=SMCConfig(mean_tolerance=1e-12))
    assert not r.validated and r.weights is None
    assert 'replicate_mean_disagreement' in r.diagnostics['reasons']
    assert all(run['completed'] for run in r.diagnostics['replicate_diagnostics'])


def test_systematic_resampling_handles_zero_weights():
    counts = np.zeros(4)
    rng = np.random.default_rng(6)
    for _ in range(3000):
        counts += np.bincount(_systematic_resample(np.array([0., .1, .3, .6]), rng), minlength=4)
    assert np.allclose(counts/counts.sum(), [0., .1, .3, .6], atol=.006)


def test_correlated_two_parameter_gaussian_reference():
    covariance = np.array([[.09, .024], [.024, .04]])
    precision = np.linalg.inv(covariance)
    center = np.array([.2, -.1])
    constant = -np.log(2*np.pi)-.5*np.linalg.slogdet(covariance)[1]
    def ll(p):
        d = np.array([p['x'], p['y']])-center
        return constant-.5*d@precision@d
    r = run_tempered_smc(ll, {'x': (-2., 2.), 'y': (-2., 2.)},
                         config=SMCConfig(particles=512, proposal_scale=.06), seed=78)
    assert r.validated, r.diagnostics
    samples = np.array([[s['x'], s['y']] for s in r.samples])
    weights = np.array(r.weights)
    mean = weights @ samples
    estimate = (samples-mean).T @ (weights[:, None]*(samples-mean))
    # Box truncation is below 1e-8 for this centered narrow reference.
    assert np.allclose(mean, center, atol=.018)
    assert np.allclose(estimate, covariance, atol=.008)
    assert max(abs(np.array(r.diagnostics['log_evidence_estimates'])+np.log(16))) < .2


def test_loglikelihood_constant_shift_preserves_target_and_shifts_log_evidence():
    config = SMCConfig(particles=64, mean_tolerance=.3, cdf_tolerance=.5, log_evidence_tolerance=1.)
    a = run_tempered_smc(lambda p: -10*(p['x']-.4)**2, {'x': (0., 1.)}, config=config, seed=22)
    b = run_tempered_smc(lambda p: 3.-10*(p['x']-.4)**2, {'x': (0., 1.)}, config=config, seed=22)
    assert a.validated and b.validated
    assert np.allclose([s['x'] for s in a.samples], [s['x'] for s in b.samples])
    assert np.allclose(np.array(b.diagnostics['log_evidence_estimates'])-a.diagnostics['log_evidence_estimates'], 3.)


def test_cached_sample_likelihoods_are_aligned_without_extra_calls():
    r = run_tempered_smc(lambda p: -2.3, {'x': (-3., 4.)})
    assert len(r.sample_log_likelihoods) == len(r.samples)
    assert all(v == -2.3 for v in r.sample_log_likelihoods)
    assert r.diagnostics['likelihood_calls'] == 6 * 256 * 9
    assert all('ll' not in d and 'x' not in d for d in r.diagnostics['replicate_diagnostics'])
