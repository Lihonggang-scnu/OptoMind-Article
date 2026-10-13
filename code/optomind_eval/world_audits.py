"""Evaluator-only physical split and sealed numerical diagnostics for F0--F8.

Finite scans/refits provide witnesses and diagnostics, not global impossibility,
empirical validity, or rigorous continuum error bounds.
"""
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import numpy as np
from scipy.optimize import least_squares
from tmm import coh_tmm
from .worlds import airy_intensity, audit_splits

GRID = np.linspace(400., 900., 101)
PHYSICAL_FIELDS = ("film_n", "thickness_nm", "substrate_n", "dispersion_b_um2", "gain", "offset", "resolution_nm")
PHYSICAL_TOLERANCES = np.array([.001, .1, .001, .0001, .001, .0001, .1])


def _vector(world):
    values = np.array([getattr(world, k) for k in PHYSICAL_FIELDS], float)
    if not np.all(np.isfinite(values)):
        raise ValueError("nonfinite world physics")
    return values


def audit_physical_splits(worlds, *, spectral_tolerance=1e-5):
    """Reject cross-split near-identical physics OR noiseless observational aliases."""
    worlds = tuple(worlds)
    if not np.isfinite(spectral_tolerance) or spectral_tolerance <= 0:
        raise ValueError("positive finite spectral tolerance required")
    declared = audit_splits(worlds)
    vectors = [_vector(w) for w in worlds]
    spectra = [np.concatenate([airy_intensity(w, GRID, c) for c in ("R", "T")]) for w in worlds]
    if any(not np.isfinite(s).all() for s in spectra):
        raise ValueError("nonfinite audit spectrum")
    comparisons = 0
    for i, a in enumerate(worlds):
        for j in range(i):
            b = worlds[j]
            if a.task.split == b.task.split:
                continue
            comparisons += 1
            physical = bool(np.all(np.abs(vectors[i] - vectors[j]) <= PHYSICAL_TOLERANCES))
            spectral = float(np.max(np.abs(spectra[i] - spectra[j])))
            if physical or spectral <= spectral_tolerance:
                raise ValueError(f"cross-split physical near-duplicate: {a.task.task_id}/{b.task.task_id}; physical={physical}; spectral_max={spectral:.9g}")
    return {"passed": True, "declared": declared, "cross_split_pairs": comparisons,
            "physical_fields": PHYSICAL_FIELDS, "physical_tolerances": PHYSICAL_TOLERANCES.tolist(),
            "grid_nm": GRID.tolist(), "spectral_max_tolerance": spectral_tolerance,
            "fingerprints": {w.task.task_id: hashlib.sha256(v.tobytes()).hexdigest() for w, v in zip(worlds, vectors)},
            "limitations": ["finite normal-incidence R/T grid can miss aliases elsewhere",
                           "shared mechanism categories alone are not duplicate samples",
                           "no empirical independence claim"]}


def independent_spectrum(world, grid, channel="R"):
    grid = np.asarray(grid, float)
    raw = np.array([coh_tmm("s", [1., world.film_n + world.dispersion_b_um2 / (wl / 1000.)**2, world.substrate_n],
                           [np.inf, world.thickness_nm, np.inf], 0., wl)[channel] for wl in grid])
    if world.resolution_nm:
        # Separate scalar accumulation; same declared finite-grid Gaussian operator.
        out = []
        widths = np.gradient(grid)
        for wl in grid:
            weights = np.exp(-.5 * ((grid - wl) / world.resolution_nm)**2) * widths
            out.append(float(np.dot(weights, raw) / weights.sum()))
        raw = np.asarray(out)
    result = world.gain * raw + world.offset
    if not np.isfinite(result).all():
        raise ValueError("nonfinite independent spectrum")
    return result


def audit_world(world):
    vector = _vector(world)
    target = airy_intensity(world, GRID, "R")
    independent = independent_spectrum(world, GRID)
    error = float(np.max(np.abs(independent - target)))
    # Explicit bounded initial grammar: ideal constant-index film n,d, known substrate.
    def initial(parameters, grid=GRID):
        return airy_intensity(replace(world, film_n=float(parameters[0]), thickness_nm=float(parameters[1]),
                                      dispersion_b_um2=0., gain=1., offset=0., resolution_nm=0.), grid, "R")
    bounds = ([.8, 0.], [3.5, 500.])
    starts = [(n, d) for n in (1., 1.7, 2.4, 3.2) for d in (30., 110., 220., 400.)]
    fits = [least_squares(lambda p: initial(p) - target, start, bounds=bounds,
                          max_nfev=250, xtol=1e-10, ftol=1e-10, gtol=1e-10) for start in starts]
    best = min(fits, key=lambda fit: float(np.mean(fit.fun**2)))
    exact_witness = (world.dispersion_b_um2 == 0 and world.gain == 1 and world.offset == 0 and world.resolution_nm == 0
                     and .8 <= world.film_n <= 3.5 and 0 <= world.thickness_nm <= 500.)
    audit_grid = np.linspace(402.5, 897.5, 100)
    witness_error = float(np.max(abs(initial(best.x, audit_grid) - airy_intensity(world, audit_grid, "R"))))
    fit_error = float(np.max(abs(best.fun)))
    # Local sensitivity of true full world: derivative n and thickness, not fitted grammar.
    derivatives = []
    for field, step in (("film_n", 1e-5), ("thickness_nm", 1e-3)):
        hi = airy_intensity(replace(world, **{field: getattr(world, field) + step}), GRID, "R")
        lo = airy_intensity(replace(world, **{field: getattr(world, field) - step}), GRID, "R")
        derivatives.append((hi - lo) / (2 * step))
    singular = np.linalg.svd(np.stack(derivatives, axis=1), compute_uv=False)
    thick_variation = float(np.max(abs(airy_intensity(replace(world, thickness_nm=world.thickness_nm + 100.), GRID, "R") - target)))
    refinement = None
    if world.resolution_nm:
        grids = [np.linspace(400., 900., size) for size in (501, 1001, 2001)]
        values = [independent_spectrum(world, grid) for grid in grids]
        errors = [float(np.max(abs(values[k] - values[k + 1][::2]))) for k in (0, 1)]
        refinement = {"grid_sizes": [501, 1001, 2001], "successive_max_differences": errors,
                      "error_bound": None, "grade": "high_budget_reference",
                      "meaning": "finite-grid discretization convergence diagnostic; not rigorous continuum bound",
                      "boundary_rule": "Gaussian truncated and renormalized on queried 400--900 nm grid"}
    underlying = replace(world, gain=1., offset=0., resolution_nm=0.)
    raw_r = independent_spectrum(underlying, GRID, "R")
    raw_t = independent_spectrum(underlying, GRID, "T")
    minimum_n = float(np.min(world.film_n + world.dispersion_b_um2 / (GRID / 1000.)**2))
    conservation = float(np.max(abs(raw_r + raw_t - 1.)))
    if minimum_n <= 0 or min(raw_r.min(), raw_t.min()) < -1e-12 or conservation > 1e-10:
        raise ValueError("world violates declared lossless normal-incidence physical checks")
    record = {"task_id": world.task.task_id,
              "physical_domain": {"minimum_film_n": minimum_n, "underlying_energy_conservation_max_error": conservation,
                  "scope": "real positive-index lossless coherent planar layer at normal incidence",
                  "instrument_outputs_need_not_obey_energy_conservation": True}, "world_sha256": hashlib.sha256(json.dumps(asdict(world), sort_keys=True).encode()).hexdigest(),
              "physical_parameters_finite": bool(np.isfinite(vector).all()),
              "independent_reference": {"implementation": "scalar Byrnes coh_tmm versus evaluator Airy",
                  "maximum_abs_error": error, "grade": "exact" if not world.resolution_nm else "high_budget_reference",
                  "reference_error_bound": world.reference_error, "refinement": refinement,
                  "independence": "numerical implementation; shared Maxwell assumptions; no empirical independence"},
              "initial_grammar": {"bounds_n_d": bounds, "starts": len(starts), "max_nfev_per_start": 250,
                  "best_parameters": best.x.tolist(), "max_residual": fit_error, "heldout_max_residual": witness_error,
                  "status": "expressible_by_constructive_parameter_witness" if exact_witness else "bounded_search_diagnostic_only",
                  "mechanism_impossibility_proven": False,
                  "note": "finite multistart residual is not a certified global approximation lower bound"},
              "identifiability": {"local_jacobian_singular_values": singular.tolist(), "thickness_100nm_change_max": thick_variation,
                  "thickness_invariance_witness": bool(world.film_n == 1 and world.dispersion_b_um2 == 0),
                  "scope": "local sensitivity and one finite displacement; not global parameter uniqueness"},
              "sealed": True}
    record["seal"] = hashlib.sha256(json.dumps(record, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return record


def write_sealed_audit(worlds, output):
    path = Path(output)
    records = [audit_world(w) for w in worlds]
    payload = {"schema": "c05-physical-audit/1", "records": records, "policy_export": False,
               "expert_review": "pending", "F9_F11": "deferred"}
    with path.open("x") as handle:
        json.dump(payload, handle, indent=2, allow_nan=False)
        handle.write("\n")
    return payload
