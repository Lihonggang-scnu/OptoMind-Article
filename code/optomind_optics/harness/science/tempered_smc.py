"""Bounded uniform-prior tempered SMC with budgeted, replicated diagnostics.

The prior must be supplied deliberately. Final beta/particle ESS are not mixing
certificates. Diagnostics compare independent populations at two resolutions;
passing them is an approximate numerical check, never proof of absent modes or
of the scientific validity of the likelihood. No gradients/framework required.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from time import perf_counter
import numpy as np
from scipy.special import logsumexp


@dataclass(frozen=True)
class SMCConfig:
    particles: int = 256
    replicates: int = 4
    ess_target: float = .8
    rejuvenation_steps: int = 8
    proposal_scale: float = .12
    global_proposal_probability: float = .2
    max_stages: int = 128
    max_likelihood_calls: int = 200000
    mean_tolerance: float = .08  # absolute normalized prior-coordinate units
    cdf_tolerance: float = .15
    log_evidence_tolerance: float = .3
    min_acceptance: float = .01

    def __post_init__(self):
        for name in ('particles', 'replicates', 'rejuvenation_steps', 'max_stages', 'max_likelihood_calls'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f'{name} must be a positive integer')
        if self.particles < 32 or self.replicates < 4:
            raise ValueError('diagnostic SMC requires >=32 particles and >=4 independent replicates')
        if not 0 < self.ess_target < 1 or not 0 < self.global_proposal_probability < 1:
            raise ValueError('ESS and global proposal probabilities must be interior')
        for name in ('proposal_scale', 'mean_tolerance', 'cdf_tolerance', 'log_evidence_tolerance', 'min_acceptance'):
            if not np.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f'{name} must be finite and positive')


@dataclass(frozen=True)
class SMCResult:
    samples: tuple[dict[str, float], ...]
    weights: tuple[float, ...] | None
    validated: bool
    diagnostics: dict
    sample_log_likelihoods: tuple[float, ...] = ()


class _BudgetExhausted(Exception):
    pass


def _systematic_resample(weights, rng):
    """One uniform offset, N evenly spaced positions; no likelihood reordering."""
    cumulative = np.cumsum(weights)
    cumulative[-1] = 1.
    return np.searchsorted(cumulative, (rng.random() + np.arange(len(weights))) / len(weights))


def _increment(log_likelihoods, delta):
    # delta=0 must leave zero-likelihood particles unchanged, not form 0 * -inf.
    return np.zeros_like(log_likelihoods) if delta == 0 else delta * log_likelihoods


def _effective_size(log_weights):
    if not np.isfinite(logsumexp(log_weights)):
        return 0.
    lw = log_weights - logsumexp(log_weights)
    return float(np.exp(-logsumexp(2 * lw)))


def _single_run(evaluate, dimension, n, config, rng):
    x = rng.uniform(size=(n, dimension))
    ll = np.array([evaluate(row) for row in x])
    if not np.isfinite(ll).any():
        return {'completed': False, 'reason': 'no_finite_prior_likelihood', 'particles': n}
    beta = 0.
    log_z = 0.
    stages = []
    for _ in range(config.max_stages):
        remaining = 1. - beta
        target = config.ess_target * n
        if _effective_size(_increment(ll, remaining)) >= target:
            delta = remaining
        else:
            lower, upper = 0., remaining
            for __ in range(60):
                middle = .5 * (lower + upper)
                if _effective_size(_increment(ll, middle)) < target:
                    upper = middle
                else:
                    lower = middle
            delta = lower
            # Discontinuous loss of prior support can make every positive step
            # violate ESS target. Take a tiny valid step, record the lower ESS.
            if delta < 1e-12:
                delta = min(remaining, 1e-8)
        increment = _increment(ll, delta)
        normalizer = float(logsumexp(increment))
        log_z += normalizer - np.log(n)
        weights = np.exp(increment - normalizer)
        ess = float(1. / np.sum(weights ** 2))
        beta = min(1., beta + delta)
        indices = _systematic_resample(weights, rng)
        x, ll = x[indices].copy(), ll[indices].copy()
        accepted = 0
        for __ in range(config.rejuvenation_steps):
            # Both kernels are symmetric on [0,1]^d: uniform independence, or
            # Gaussian random walk reflected (not clipped) at each boundary.
            proposal = x + rng.normal(0, config.proposal_scale, x.shape)
            proposal = 1. - np.abs(1. - np.mod(proposal, 2.))
            global_mask = rng.random(n) < config.global_proposal_probability
            proposal[global_mask] = rng.uniform(size=(global_mask.sum(), dimension))
            proposed_ll = np.array([evaluate(row) for row in proposal])
            with np.errstate(invalid='ignore'):
                ratio = beta * (proposed_ll - ll)
            accept = np.log(rng.random(n)) < ratio
            x[accept], ll[accept] = proposal[accept], proposed_ll[accept]
            accepted += int(accept.sum())
        stages.append({'beta': beta, 'pre_resample_ess': ess,
                       'unique_parents': len(np.unique(indices)),
                       'acceptance': accepted / (n * config.rejuvenation_steps)})
        if beta == 1.:
            return {'completed': True, 'x': x, 'll': ll, 'log_evidence': float(log_z),
                    'particles': n, 'stages': stages}
    return {'completed': False, 'reason': 'max_stages', 'particles': n, 'stages': stages}


def run_tempered_smc(log_likelihood, prior_bounds, *, config=None, seed=0):
    """Approximate a posterior under explicit independent bounded uniform priors.

    `log_likelihood` must include its normalization for meaningful log evidence.
    NaN/+inf or exceptions are hard model errors, not silently zero likelihood.
    The caller must separately establish scientific noise validity. Inconclusive
    runs return candidate scenarios WITHOUT probability weights.
    """
    config = config or SMCConfig()
    if isinstance(seed, bool) or not isinstance(seed, (int, np.integer)) or seed < 0:
        raise ValueError('seed must be a nonnegative integer')
    if not prior_bounds:
        raise ValueError('an explicit nonempty bounded uniform prior is required')
    names = tuple(prior_bounds)
    if any(not isinstance(name, str) or not name for name in names):
        raise ValueError('prior parameter names must be nonempty strings')
    bounds = np.asarray([prior_bounds[name] for name in names], dtype=float)
    if bounds.shape != (len(names), 2) or not np.isfinite(bounds).all() or np.any(bounds[:, 0] >= bounds[:, 1]):
        raise ValueError('prior bounds must be finite, ordered pairs')
    lo, span = bounds[:, 0], bounds[:, 1] - bounds[:, 0]
    def unpack(x):
        return dict(zip(names, map(float, lo + x * span)))
    calls = 0
    start = perf_counter()
    def evaluate(x):
        nonlocal calls
        if calls >= config.max_likelihood_calls:
            raise _BudgetExhausted()
        calls += 1
        value = float(log_likelihood(unpack(x)))
        if np.isnan(value) or np.isposinf(value):
            raise ValueError('log likelihood returned NaN or positive infinity')
        return value
    runs, reasons = [], []
    seeds = np.random.SeedSequence(seed).spawn(config.replicates)
    for i, child_seed in enumerate(seeds):
        # First half base population, second half doubled: expose N-sensitivity.
        n = config.particles * (1 if i < config.replicates // 2 else 2)
        try:
            runs.append(_single_run(evaluate, len(names), n, config, np.random.default_rng(child_seed)))
        except _BudgetExhausted:
            reasons.append('likelihood_budget_exhausted')
            break
    completed = [r for r in runs if r['completed']]
    if len(completed) != config.replicates:
        reasons.append('incomplete_independent_replicates')
    comparison = {}
    if len(completed) >= 2:
        means = np.array([r['x'].mean(axis=0) for r in completed])
        variances = np.array([r['x'].var(axis=0) for r in completed])
        mean_range = float(np.ptp(means, axis=0).max())
        variance_range = float(np.ptp(variances, axis=0).max())
        # Coordinate and fixed random-projection empirical CDFs detect mode mass
        # disagreement that matching means alone would miss. No fitted thresholds.
        projections = np.vstack((np.eye(len(names)), np.random.default_rng(1968).normal(size=(8, len(names)))))
        cdf_gap = 0.
        for p in projections:
            samples = [np.sort(r['x'] @ p) for r in completed]
            grid = np.unique(np.concatenate(samples))
            cdfs = np.array([np.searchsorted(s, grid, side='right') / len(s) for s in samples])
            cdf_gap = max(cdf_gap, float(np.ptp(cdfs, axis=0).max()))
        log_z_range = float(np.ptp([r['log_evidence'] for r in completed]))
        comparison = {'normalized_mean_range': mean_range, 'normalized_variance_range': variance_range,
                      'projection_cdf_gap': cdf_gap, 'log_evidence_range': log_z_range}
        if mean_range > config.mean_tolerance: reasons.append('replicate_mean_disagreement')
        if variance_range > config.mean_tolerance: reasons.append('replicate_variance_disagreement')
        if cdf_gap > config.cdf_tolerance: reasons.append('replicate_mode_mass_disagreement')
        if log_z_range > config.log_evidence_tolerance: reasons.append('replicate_evidence_disagreement')
        if any(r['stages'][-1]['acceptance'] < config.min_acceptance for r in completed):
            reasons.append('insufficient_final_rejuvenation')
    validated = not reasons
    all_samples = tuple(unpack(x) for r in completed for x in r['x'])
    # Each independently normalized population receives equal mass, regardless N.
    weights = tuple(1. / (len(completed) * len(r['x'])) for r in completed for _ in r['x']) if validated else None
    diagnostics = {'backend': 'bounded_uniform_tempered_smc', 'status': 'replicated_diagnostics_passed' if validated else 'inconclusive',
                   'reasons': reasons, 'config': asdict(config), 'seed': int(seed), 'likelihood_calls': calls, 'max_likelihood_calls': config.max_likelihood_calls,
                   'wall_seconds': perf_counter() - start, 'prior': {'family': 'independent_bounded_uniform', 'bounds': dict(prior_bounds)},
                   'replicate_diagnostics': [{k: v for k, v in r.items() if k not in ('x', 'll')} for r in runs],
                   'comparison': comparison, 'log_evidence_estimates': [r['log_evidence'] for r in completed],
                   'limitations': 'Finite-population diagnostics cannot prove all modes were found; scientific validity remains conditional on explicit prior and validated likelihood. Cost counts every likelihood evaluation; each evaluation may contain multiple forward solves.'}
    return SMCResult(all_samples, weights, validated, diagnostics, tuple(float(ll) for r in completed for ll in r['ll']))
