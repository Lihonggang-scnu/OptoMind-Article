"""Bounded normalized multistart fitting and explicitly conditional Laplace inference.

No posterior probabilities are invented for unknown noise or unresolved multimodality.
Existing VeriTMM optimizers are untouched; this is a consumer adapter using scipy.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict, replace
import numpy as np
from scipy.optimize import least_squares
from .likelihood_blocks import JointLikelihood
from .identifiability import diagnose_identifiability, IdentifiabilityReport


@dataclass(frozen=True)
class FitResult:
    model_id: str
    parameters: dict[str, float]
    loss: float
    success: bool
    representation: str
    weights_semantics: str
    samples: tuple[dict[str, float], ...]
    weights: tuple[float, ...] | None
    covariance: tuple[tuple[float, ...], ...] | None
    diagnostics: dict
    identifiability: IdentifiabilityReport
    evidence_uses: tuple[str, ...]
    calibration_scope: str = "conditional on supplied covariance and forward model"
    evidence_hashes: tuple[str, ...] = ()
    parameter_binding_hash: str = ""

    def finite_worlds(self):
        """Return scenario worlds and optional probability weights for decision consumers."""
        return tuple({"world_id": f"{self.model_id}:{i}", "model_id": self.model_id, "parameters": p} for i, p in enumerate(self.samples)), self.weights


def fit_model(model, blocks, *, starts=12, seed=0, max_nfev=1000, laplace_samples=256, scenario_loss_delta=4., terminal_action=None, bounded_uniform_prior=None, smc_config=None, profile_config=None):
    if starts < 1 or max_nfev < 1 or laplace_samples < 1 or scenario_loss_delta < 0:
        raise ValueError("invalid fit budget")
    likelihood = JointLikelihood(blocks)
    from .contracts import canonical_hash
    parameter_binding_hash = canonical_hash(asdict(model.ir)) if hasattr(model, "ir") else ""
    specs = model.parameter_specs
    names = [p.name for p in specs]
    lo = np.array([p.lower for p in specs]); hi = np.array([p.upper for p in specs]); span = hi - lo
    prior = None
    prior_matches_fit_bounds = False
    if bounded_uniform_prior is not None:
        if set(bounded_uniform_prior) != set(names):
            raise ValueError("explicit prior parameter names must match the model")
        prior_array = np.asarray([bounded_uniform_prior[name] for name in names], dtype=float)
        if specs and (prior_array.shape != (len(names), 2) or not np.isfinite(prior_array).all()
                      or np.any(prior_array[:, 0] >= prior_array[:, 1])
                      or np.any(prior_array[:, 0] < lo) or np.any(prior_array[:, 1] > hi)):
            raise ValueError("prior must be finite ordered bounds within model validity bounds")
        prior = {name: tuple(map(float, bounded_uniform_prior[name])) for name in names}
        prior_matches_fit_bounds = bool(not specs or np.array_equal(prior_array, np.column_stack((lo, hi))))
    if profile_config is not None:
        from .profile_identifiability import ProfileIdentifiabilityConfig
        if not isinstance(profile_config, ProfileIdentifiabilityConfig):
            raise TypeError("profile_config must be a frozen ProfileIdentifiabilityConfig")
    action_cache = {}
    action_call_count = 0
    def tracked_action(parameters):
        nonlocal action_call_count
        key = tuple(sorted(parameters.items()))
        if key not in action_cache:
            action_call_count += 1
            action_cache[key] = terminal_action(parameters)
        return action_cache[key]
    rng = np.random.default_rng(seed)
    def unpack(x):
        return dict(zip(names, map(float, lo + span * x)))
    evaluation_counts = {"residual": 0, "predictive": 0, "log_prob": 0}
    def parameter_residual(parameters):
        evaluation_counts["residual"] += 1
        return likelihood.residual(model, parameters)
    def residual(x):
        return parameter_residual(unpack(x))
    def predictive(parameters):
        evaluation_counts["predictive"] += 1
        return likelihood.predict(model, parameters)
    def log_probability(parameters):
        evaluation_counts["log_prob"] += 1
        return likelihood.log_prob(model, parameters)
    if not specs:
        r = residual(np.array([]))
        report = diagnose_identifiability(np.zeros((len(r), 0)))
        return FitResult(model.model_id, {}, float(r @ r / 2), True, "scenario_set", "unweighted singleton", ({},), None, None, {"backend": "fixed_model", "nfev": 1, "evaluation_counts": dict(evaluation_counts), "model_predict_calls": len(likelihood.blocks)}, report, tuple(b.observation_id for b in likelihood.blocks), evidence_hashes=likelihood.evidence_hashes, parameter_binding_hash=parameter_binding_hash)
    initial = (np.array([p.initial for p in specs]) - lo) / span
    solutions = []
    failed = []
    for x0 in [initial] + list(rng.uniform(0, 1, (starts - 1, len(specs)))):
        try:
            opt = least_squares(residual, x0, bounds=(np.zeros(len(specs)), np.ones(len(specs))), max_nfev=max_nfev, xtol=1e-11, ftol=1e-11, gtol=1e-10)
            solutions.append(opt)
        except (ValueError, FloatingPointError) as exc:
            failed.append(str(exc))
    if not solutions:
        raise RuntimeError("all inference starts failed: " + "; ".join(failed))
    solutions.sort(key=lambda s: s.cost)
    best = solutions[0]
    basins = []
    for opt in solutions:
        if opt.success and opt.cost <= best.cost + scenario_loss_delta and all(np.linalg.norm(opt.x - other.x) > 1e-3 for other in basins):
            basins.append(opt)
    if not basins:
        basins = [best]
    predictions = [predictive(unpack(b.x)) for b in basins]
    actions = [tracked_action(unpack(b.x)) for b in basins] if terminal_action else None
    report = diagnose_identifiability(best.jac, predictions, terminal_actions=actions)
    profile_report = None
    if profile_config is not None:
        from .profile_identifiability import profile_identifiability
        profile_report = profile_identifiability(residual=residual,unpack=unpack,best_x=best.x,best_residual=best.fun,
            parameter_names=names,config=profile_config,terminal_action=tracked_action if terminal_action else None,best_converged=bool(best.success),
            source_ref=canonical_hash({"model":model.model_id,"parameters":parameter_binding_hash,"evidence":likelihood.evidence_hashes}))
        if profile_report["task_impact"] == "action_ambiguous":
            report = replace(report,task_impact="action_ambiguous",limits=report.limits+tuple(profile_report["limits"]))
        elif report.task_impact != "action_ambiguous":
            report = replace(report,task_impact=profile_report["task_impact"],limits=report.limits+tuple(profile_report["limits"]))
    boundary = bool(np.any(best.x < 1e-4) or np.any(best.x > 1 - 1e-4))
    reasons = []
    if prior is None: reasons.append("missing_explicit_prior")
    elif not prior_matches_fit_bounds: reasons.append("prior_bounds_require_sampling")
    if not likelihood.gaussian_validated: reasons.append("unvalidated_noise")
    if not best.success: reasons.append("optimizer_not_converged")
    if len(basins) > 1: reasons.append("multiple_basins")
    if report.rank < len(specs): reasons.append("rank_deficient")
    if boundary: reasons.append("boundary_solution")
    if report.task_impact == "action_ambiguous": reasons.append("terminal_action_flip")
    if profile_report is not None and profile_report["status"] != "completed_declared_profile_search":
        reasons.append("profile_search_incomplete")
    covariance = None
    samples = tuple(unpack(b.x) for b in basins)
    weights = None
    representation = "scenario_set"
    semantics = "unweighted feasible fitted basins; not exhaustive and not posterior probability"
    if not reasons:
        cov_normal = np.linalg.inv(best.jac.T @ best.jac)
        covariance_array = span[:, None] * cov_normal * span[None, :]
        # A nonlinearity check tests the likelihood curvature along local principal axes.
        vals, vectors = np.linalg.eigh(cov_normal)
        discrepancies = []
        for i in range(len(specs)):
            step = vectors[:, i] * np.sqrt(max(vals[i], 0.))
            for sign in (-1, 1):
                x = best.x + sign * step
                if np.any(x <= 0) or np.any(x >= 1):
                    reasons.append("posterior_reaches_boundary")
                    continue
                actual = residual(x) - best.fun
                linear = best.jac @ (sign * step)
                discrepancies.append(float(np.linalg.norm(actual - linear)))
        if discrepancies and max(discrepancies) > .25:
            reasons.append("nonlinear_likelihood")
        if not reasons:
            draws = rng.multivariate_normal(best.x, cov_normal, size=laplace_samples)
            if np.any((draws < 0) | (draws > 1)):
                reasons.append("laplace_draws_outside_bounds")
            else:
                samples = tuple(unpack(x) for x in draws)
                weights = tuple([1 / len(samples)] * len(samples))
                representation = "conditional_bayes"
                semantics = "equal Monte Carlo weights from local Gaussian likelihood with explicitly declared bounded uniform prior; Laplace approximation"
                covariance = tuple(tuple(map(float, r)) for r in covariance_array)
    examined_actions = []
    if terminal_action is not None and profile_config is not None and weights is not None:
        indices = np.linspace(0,len(samples)-1,min(len(samples),profile_config.max_action_samples),dtype=int)
        examined_actions = [tracked_action(samples[int(index)]) for index in indices]
        if len(set(examined_actions)) > 1:
            reasons.append("terminal_action_flip")
            report = replace(report,task_impact="action_ambiguous")
            samples = tuple(unpack(b.x) for b in basins)
            weights = None; covariance = None; representation = "scenario_set"
            semantics = "unweighted fitted scenarios after action-sensitive Laplace rejection; sampling escalation required"
    diagnostics = {"backend": "scipy_least_squares_multistart+checked_laplace", "starts": starts, "failed_starts": failed, "nfev": sum(s.nfev for s in solutions), "basin_count": len(basins), "boundary": boundary, "escalation_required": bool(reasons), "escalation_reasons": reasons, "escalation_backend": "configured validated tempered SMC required; no probability claim while fast-path prerequisites fail", "evidence_status": "offline_numerical_only", "covariance_units": [p.unit for p in specs]}
    diagnostics["profile_identifiability"] = profile_report
    diagnostics["action_sensitivity"] = {"examined_laplace_actions":examined_actions,"terminal_action_calls":action_call_count,
        "scope":"finite fitted/profile/selected Laplace points; not global action equivalence"}
    diagnostics["prior"] = {"family": "independent_bounded_uniform", "bounds": prior} if prior is not None else {"status": "not_declared"}
    point_parameters, point_loss, success = unpack(best.x), float(best.cost), bool(best.success)
    if reasons and likelihood.gaussian_validated and prior is not None and smc_config is not None:
        from .tempered_smc import run_tempered_smc
        smc = run_tempered_smc(log_probability, prior,
                               config=smc_config, seed=seed)
        diagnostics["fast_path_rejection_reasons"] = list(reasons)
        diagnostics["smc"] = smc.diagnostics
        diagnostics["backend"] = "scipy_least_squares_multistart+bounded_uniform_tempered_smc"
        known_basin_missing = False
        coverage = []
        if smc.validated:
            from scipy.stats import chi2
            normalized_samples = (np.array([[sample[name] for name in names] for sample in smc.samples]) - lo) / span
            radius = float(np.sqrt(chi2.ppf(.99, len(names))))
            for index, basin in enumerate(basins):
                parameters = unpack(basin.x)
                in_prior = all(prior[name][0] <= parameters[name] <= prior[name][1] for name in names)
                singular = np.linalg.svd(basin.jac, compute_uv=False)
                full_rank = np.count_nonzero(singular > 1e-6 * max(float(singular[0]) if len(singular) else 0., 1.)) == len(names)
                entry = {"basin": index, "in_prior": in_prior, "full_local_rank": bool(full_rank)}
                if in_prior and full_rank:
                    distances = np.linalg.norm((normalized_samples - basin.x) @ basin.jac.T, axis=1)
                    entry["covered"] = bool(np.any(distances <= radius))
                    known_basin_missing = known_basin_missing or not entry["covered"]
                coverage.append(entry)
            diagnostics["known_basin_coverage"] = {"checks": coverage, "local_whitened_radius": radius,
                "scope": "Conservative local quadratic neighborhoods of competitive optimizer-discovered full-rank basins inside the declared prior; not proof all modes exist or were found."}
        if smc.validated and not known_basin_missing:
            samples, weights = smc.samples, smc.weights
            matrix = np.array([[sample[name] for name in names] for sample in samples])
            w = np.asarray(weights)
            mean = w @ matrix
            centered = matrix - mean
            covariance = tuple(tuple(map(float, row)) for row in (centered * w[:, None]).T @ centered)
            diagnostics["posterior_mean"] = dict(zip(names, map(float, mean)))
            if best.success and all(prior[name][0] <= point_parameters[name] <= prior[name][1] for name in names):
                diagnostics["point_estimate_source"] = "maximum_likelihood_fitted_point_within_prior"
            else:
                index = int(np.argmax(smc.sample_log_likelihoods))
                point_parameters = dict(samples[index])
                r = parameter_residual(point_parameters)
                point_loss = float(r @ r / 2)
                diagnostics["point_estimate_source"] = "maximum_likelihood_sample_within_prior"
            success = True
            representation = "conditional_bayes"
            semantics = "weighted replicated tempered-SMC particles under explicit bounded uniform prior and validated Gaussian likelihood; approximate, not proof of all modes"
            diagnostics["escalation_required"] = False
            diagnostics["escalation_reasons"] = []
            diagnostics["escalation_backend"] = "replicated tempered SMC numerical diagnostics passed"
        else:
            diagnostics["escalation_reasons"] = list(reasons) + ["known_basin_unrepresented" if known_basin_missing else "smc_inconclusive"]
            diagnostics["escalation_backend"] = "tempered SMC attempted; no probability weights published"
    diagnostics["evaluation_counts"] = dict(evaluation_counts)
    diagnostics["model_predict_calls"] = sum(evaluation_counts.values()) * len(likelihood.blocks)
    diagnostics["cost_scope"] = "Counts inference likelihood model.predict calls including finite differences and profile diagnostics; terminal-action callback forwards are separately charged by the shared compute ledger; provider billing needs receipts."
    return FitResult(model.model_id, point_parameters, point_loss, success, representation, semantics, samples, weights, covariance, diagnostics, report, tuple(b.observation_id for b in likelihood.blocks), evidence_hashes=likelihood.evidence_hashes, parameter_binding_hash=parameter_binding_hash)


def gaussian_observation_kernel(model, worlds, wavelengths_nm, covariance, *, channel="R", angle_deg=0.):
    """Explicit Gaussian kernel factory, preserving full within-observation covariance."""
    covariance = np.asarray(covariance, float)
    means = np.array([model.predict(w["parameters"], wavelengths_nm, channel=channel, angle_deg=angle_deg) for w in worlds])
    if covariance.shape != (means.shape[1], means.shape[1]):
        raise ValueError("kernel covariance dimension mismatch")
    if not np.isfinite(covariance).all() or not np.allclose(covariance, covariance.T, atol=1e-12, rtol=1e-10):
        raise ValueError("kernel covariance must be finite and symmetric")
    chol = np.linalg.cholesky(covariance)
    def sample(world_index, rng):
        return means[world_index] + chol @ rng.normal(size=means.shape[1])
    def log_prob(observation):
        residual = np.linalg.solve(chol, (np.asarray(observation) - means).T).T
        return -.5 * (np.sum(residual ** 2, axis=1) + 2 * np.log(np.diag(chol)).sum() + means.shape[1] * np.log(2 * np.pi))
    return sample, log_prob


def fit_result_to_state(fit, candidate, observations, *, object_id, source_run_id):
    """Publish a numerical result into the single C01 canonical inference contract."""
    from .contracts import InferenceState
    from .model_compiler import compile_candidate
    model = compile_candidate(candidate)
    if model.model_id != fit.model_id:
        raise ValueError("fit does not belong to this model structure; new structures require refitting")
    expected_names = {p.name for p in model.parameter_specs}
    if set(fit.parameters) != expected_names or any(set(sample) != expected_names for sample in fit.samples):
        raise ValueError("fit parameter identities differ; alpha-renamed models require explicit remapping or refitting")
    from .contracts import canonical_hash
    if fit.parameter_binding_hash != canonical_hash(asdict(model.ir)):
        raise ValueError("fit parameter-to-operator binding differs; explicit remapping or refitting required")
    observations = tuple(observations)
    from .likelihood_blocks import blocks_from_observations
    supplied_likelihood = JointLikelihood(blocks_from_observations(observations))
    if not fit.evidence_hashes or fit.evidence_hashes != supplied_likelihood.evidence_hashes:
        raise ValueError("fit evidence content differs from supplied canonical observations")
    refs = tuple(o.ref() for o in observations)
    observed_ids = {f"{o.acquisition_id}:{b['block_id']}" for o in observations for b in o.raw_blocks}
    if not set(fit.evidence_uses) <= observed_ids:
        raise ValueError("fit evidence is not bound to supplied canonical observations")
    return InferenceState(object_id=object_id, source_run_id=source_run_id, input_refs=(candidate.ref(),) + refs, candidate_refs=(candidate.ref(),), observation_refs=refs, representation=fit.representation, weights_semantics=fit.weights_semantics, calibration_scope={"description": fit.calibration_scope}, noise_model={"type": "joint_raw_covariance", "shared_calibration": "preserved", "derived_views": "excluded"}, posterior={"parameter_order": list(fit.parameters), "parameters": fit.parameters, "samples": fit.samples, "weights": fit.weights, "covariance": fit.covariance}, diagnostics={**fit.diagnostics, "identifiability": asdict(fit.identifiability)}, belief_scope="model_pool_conditional" if fit.representation == "conditional_bayes" else "scenario")
