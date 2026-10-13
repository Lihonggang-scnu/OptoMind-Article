from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from itertools import combinations, product
from typing import Callable
import numpy as np
from scipy.linalg import block_diag, solve_triangular
from scipy.optimize import least_squares
from scipy.stats import chi2

from .contracts import canonical_hash
from .model_ir import IRNode, ModelIR, model_ir_from_dict
from .model_compiler import compile_model


@dataclass(frozen=True)
class ReferenceLayer:
    n: float
    thickness_nm: float
    tolerance_nm: float = 0.
    k: float = 0.
    coherence: str = "coherent"

    def __post_init__(self):
        if not np.isfinite([self.n, self.k, self.thickness_nm, self.tolerance_nm]).all() or self.n <= 0 or min(self.k, self.thickness_nm, self.tolerance_nm) < 0 or self.tolerance_nm > self.thickness_nm:
            raise ValueError("invalid reference-layer physical domain")
        if self.coherence not in {"coherent", "incoherent"}:
            raise ValueError("invalid reference-layer coherence")


@dataclass(frozen=True)
class DiagnosticFrontEnd:
    frontend_id: str
    layers: tuple[ReferenceLayer, ...] = ()
    mounting_cost: float = 0.

    def __post_init__(self):
        object.__setattr__(self, "layers", tuple(self.layers))
        if not self.frontend_id or len(self.layers) > 4 or not np.isfinite(self.mounting_cost) or self.mounting_cost < 0:
            raise ValueError("bounded reference front end required")

    @property
    def content_hash(self):
        return canonical_hash(asdict(self))


@dataclass(frozen=True)
class DiagnosticAction:
    action_id: str
    frontend: DiagnosticFrontEnd
    wavelengths_nm: tuple[float, ...]
    angle_deg: float = 0.
    polarization: str = "s"
    bandwidth_nm: float = 0.
    angular_spread_deg: float = 0.
    sample_id: str = "specimen"
    session_id: str = "diagnostic"
    acquisition_cost: float = 1.
    channel: str = "R"

    def __post_init__(self):
        object.__setattr__(self, "wavelengths_nm", tuple(self.wavelengths_nm))
        w = np.asarray(self.wavelengths_nm, float)
        if w.ndim != 1 or not 2 <= len(w) <= 256 or not np.isfinite(w).all() or min(w) <= 0 or np.any(np.diff(w) <= 0):
            raise ValueError("ordered positive bounded wavelength grid required")
        if not np.isfinite([self.angle_deg, self.bandwidth_nm, self.angular_spread_deg, self.acquisition_cost]).all() or not 0 <= self.angle_deg < 85 or min(self.bandwidth_nm, self.angular_spread_deg) < 0 or self.acquisition_cost <= 0:
            raise ValueError("invalid diagnostic acquisition domain")
        if self.angle_deg + self.angular_spread_deg >= 90 or self.polarization not in {"s", "p", "unpolarized"} or self.channel not in {"R", "T", "A"}:
            raise ValueError("unsupported diagnostic measurement")
        if not all((self.action_id, self.sample_id, self.session_id)):
            raise ValueError("action, sample and session identities required")

    @property
    def cost(self):
        return self.acquisition_cost + self.frontend.mounting_cost

    @property
    def content_hash(self):
        return canonical_hash(asdict(self))


@dataclass(frozen=True)
class MechanismFamily:
    family_id: str
    model: ModelIR

    def __post_init__(self):
        if not self.family_id or any(p.namespace != "sample" for p in self.model.parameters):
            raise ValueError("mechanism unknowns must belong only to the original sample")
        if sum(n.operator == "stack" for n in self.model.nodes) != 1 or sum(n.operator == "tmm" for n in self.model.nodes) != 1:
            raise ValueError("diagnostic adapter requires one planar sample stack and one TMM output")
        if next(n for n in self.model.nodes if n.node_id == self.model.output).operator != "tmm":
            raise ValueError("sample mechanism output must be raw TMM intensity")
        compile_model(self.model)


@dataclass(frozen=True)
class DiagnosticNoise:
    measurement_std: float = .003
    offset_std: float = .001
    gain_std: float = .002
    nuisance_bound_sigma: float = 3.

    def __post_init__(self):
        if not np.isfinite(list(asdict(self).values())).all() or self.measurement_std <= 0 or min(self.offset_std, self.gain_std) < 0 or self.nuisance_bound_sigma <= 0:
            raise ValueError("finite calibrated diagnostic noise required")

    def covariance(self, mean):
        mean = np.asarray(mean, float)
        return np.eye(len(mean)) * self.measurement_std ** 2 + np.ones((len(mean), len(mean))) * self.offset_std ** 2 + np.outer(mean, mean) * self.gain_std ** 2


@dataclass(frozen=True)
class DiagnosticDatum:
    acquisition_id: str
    action: DiagnosticAction
    values: tuple[float, ...]
    covariance: tuple[tuple[float, ...], ...]

    def __post_init__(self):
        object.__setattr__(self,"values",tuple(float(v) for v in self.values))
        object.__setattr__(self,"covariance",tuple(tuple(float(v) for v in row) for row in self.covariance))
        y = np.asarray(self.values, float)
        c = np.asarray(self.covariance, float)
        if not self.acquisition_id or y.shape != (len(self.action.wavelengths_nm),) or c.shape != (len(y), len(y)) or not np.isfinite(y).all() or not np.isfinite(c).all() or not np.allclose(c, c.T):
            raise ValueError("invalid diagnostic observation")
        np.linalg.cholesky(c)


@dataclass(frozen=True)
class DiagnosticConfig:
    seed: int = 1741
    fit_starts: int = 4
    max_nfev: int = 120
    alpha: float = .01
    basin_loss_delta: float = 12.
    candidate_limit: int = 90
    adversarial_shortlist: int = 4
    adversarial_starts: int = 2
    adversarial_max_nfev: int = 45
    max_prediction_calls: int = 180000
    max_solver_points: int = 16000000
    measurement_budget: float = 5.
    max_rounds: int = 2
    minimum_separation: float = 9.
    readout_terms: int = 4
    noise: DiagnosticNoise = field(default_factory=DiagnosticNoise)

    def __post_init__(self):
        if not isinstance(self.noise,DiagnosticNoise) or type(self.seed) is not int or self.seed<0:
            raise ValueError("typed diagnostic noise and nonnegative integer seed required")
        for name in ("fit_starts", "max_nfev", "candidate_limit", "adversarial_shortlist", "adversarial_starts", "adversarial_max_nfev", "max_prediction_calls", "max_solver_points", "max_rounds", "readout_terms"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError("positive finite diagnostic compute budgets required")
        if self.fit_starts > 32 or self.candidate_limit > 1024 or self.max_nfev > 2000 or self.max_rounds > 8 or self.readout_terms > 32:
            raise ValueError("diagnostic search bounds exceeded")
        if not np.isfinite([self.alpha,self.measurement_budget,self.minimum_separation,self.basin_loss_delta]).all() or not 0 < self.alpha < 1 or self.measurement_budget <= 0 or self.minimum_separation < 0 or self.basin_loss_delta < 0:
            raise ValueError("invalid frozen diagnostic policy")


class DiagnosticBudgetExceeded(RuntimeError):
    pass


class DiagnosticForward:
    def __init__(self, config):
        self.config = config
        self.prediction_calls = 0
        self.solver_points = 0
        self._compiled = {}

    def predict(self, family, parameters, action, tolerance=()):
        offsets = (0.,) if not action.bandwidth_nm else (-.5 * action.bandwidth_nm, 0., .5 * action.bandwidth_nm)
        points = len(action.wavelengths_nm) * len(offsets) * (3 if action.angular_spread_deg else 1) * (2 if action.polarization == "unpolarized" else 1)
        if self.prediction_calls + 1 > self.config.max_prediction_calls or self.solver_points + points > self.config.max_solver_points:
            raise DiagnosticBudgetExceeded("diagnostic compute budget exhausted")
        self.prediction_calls += 1
        self.solver_points += points
        key = (canonical_hash(asdict(family.model)), action.frontend.content_hash, action.polarization, action.angular_spread_deg, tuple(tolerance))
        if key not in self._compiled:
            nodes = []
            prefix = "diagnostic_frontend_"
            if any(n.node_id.startswith(prefix) for n in family.model.nodes):
                raise ValueError("reserved diagnostic front-end namespace")
            front_ids = []
            for i, layer in enumerate(action.frontend.layers):
                d = layer.thickness_nm + (tolerance[i] if tolerance else 0.)
                if d < 0:
                    raise ValueError("front-end tolerance gives negative thickness")
                nk, geom = prefix + str(i) + "_nk", prefix + str(i) + "_layer"
                nodes.extend((IRNode(nk, "constant_nk", attributes=(("n", layer.n), ("k", layer.k))), IRNode(geom, "layer", (nk,), (("thickness", d), ("coherence", layer.coherence)), unit="nm")))
                front_ids.append(geom)
            for node in family.model.nodes:
                if node.operator == "stack":
                    node = replace(node, inputs=tuple(front_ids) + node.inputs)
                if node.operator == "tmm":
                    attrs = {**node.attrs, "s_fraction": {"s": 1., "p": 0., "unpolarized": .5}[action.polarization], "aperture_deg": action.angular_spread_deg, "quadrature_points": 3}
                    node = replace(node, attributes=tuple(sorted(attrs.items())))
                nodes.append(node)
            self._compiled[key] = compile_model(replace(family.model, nodes=tuple(nodes)))
        model = self._compiled[key]
        weights = (1.,) if len(offsets) == 1 else (1 / 6, 2 / 3, 1 / 6)
        return sum(weight * model.predict(parameters, np.asarray(action.wavelengths_nm) + offset, angle_deg=action.angle_deg, channel=action.channel) for weight, offset in zip(weights, offsets))

    def ledger(self):
        return {"prediction_calls": self.prediction_calls, "solver_points": self.solver_points, "prediction_limit": self.config.max_prediction_calls, "solver_point_limit": self.config.max_solver_points, "paid_model_calls": 0}


def tolerance_vertices(frontend):
    axes = [(-l.tolerance_nm, l.tolerance_nm) if l.tolerance_nm else (0.,) for l in frontend.layers]
    vertices = tuple(product(*axes)) if axes else ((),)
    return tuple(dict.fromkeys((tuple(0. for _ in axes),) + vertices))


@dataclass(frozen=True)
class FamilyFit:
    family_id: str
    state: str
    parameters: dict
    nuisance: dict
    discrepancy: float | None
    threshold: float
    basins: tuple[dict, ...]
    starts: tuple[dict, ...]
    acquisition_ids: tuple[str, ...]
    parameter_bounds: tuple[dict, ...]
    search_scope: str = "finite bounded multistart; no global exclusion certificate"


@dataclass(frozen=True)
class CompetitionResult:
    fits: tuple[FamilyFit, ...]
    retained_families: tuple[str, ...]
    selected_family: str | None
    state: str
    acquisition_ids: tuple[str, ...]
    evidence_semantics: str = "raw acquisitions counted once per model likelihood; competing models are alternative explanations"


def _fit_family(family, data, forward, config, *, warm=(), starts=None, max_nfev=None):
    data = tuple(data)
    ids = tuple(d.acquisition_id for d in data)
    if len(ids) != len(set(ids)) or not data:
        raise ValueError("unique raw acquisitions required for diagnostic fitting")
    if len({d.action.sample_id for d in data}) != 1:
        raise ValueError("one shared original sample required per competition")
    specs = family.model.parameters
    sessions = tuple(sorted({d.action.session_id for d in data}))
    descriptors = [(p.name, p.lower, p.upper, p.initial) for p in specs]
    for session in sessions:
        if config.noise.offset_std:
            bound = config.noise.nuisance_bound_sigma * config.noise.offset_std
            descriptors.append((session + ":offset", -bound, bound, 0.))
        if config.noise.gain_std:
            bound = config.noise.nuisance_bound_sigma * config.noise.gain_std
            descriptors.append((session + ":gain", -bound, bound, 0.))
    lo = np.array([p[1] for p in descriptors]); span = np.array([p[2] - p[1] for p in descriptors])
    names = [p[0] for p in descriptors]
    if len(names)!=len(set(names)):
        raise ValueError("sample and session nuisance parameter names must be disjoint")
    chols = [np.linalg.cholesky(d.covariance) for d in data]
    n = sum(len(d.values) for d in data)
    threshold = float(chi2.ppf(1 - config.alpha, n))
    def unpack(x):
        values = dict(zip(names, map(float, lo + x * span)))
        return {p.name: values[p.name] for p in specs}, {k: values[k] for k in names[len(specs):]}
    def residual(x):
        parameters, nuisance = unpack(x)
        residuals = []
        for datum, chol in zip(data, chols):
            prediction = forward.predict(family, parameters, datum.action)
            session = datum.action.session_id
            prediction = prediction * (1 + nuisance.get(session + ":gain", 0.)) + nuisance.get(session + ":offset", 0.)
            residuals.extend(solve_triangular(chol, prediction - datum.values, lower=True))
        return np.asarray(residuals)
    starts = starts or config.fit_starts
    rng = np.random.default_rng(config.seed)
    initial = (np.array([p[3] for p in descriptors]) - lo) / span
    guesses = [initial]
    for parameters in warm:
        guesses.insert(0, np.array([(parameters.get(p[0], p[3]) - p[1]) / (p[2] - p[1]) for p in descriptors]))
    guesses = guesses[:starts] + list(rng.uniform(.05, .95, (max(0, starts - len(guesses)), len(names))))
    solutions = []; receipts = []
    for index, guess in enumerate(guesses):
        try:
            if len(names):
                opt = least_squares(residual, np.clip(guess, 1e-10, 1 - 1e-10), bounds=(np.zeros(len(names)), np.ones(len(names))), max_nfev=max_nfev or config.max_nfev, ftol=1e-8, xtol=1e-8, gtol=1e-7)
                pars, nuisance = unpack(opt.x)
                score = float(opt.fun @ opt.fun)
                receipt = {"start": index, "success": bool(opt.success), "discrepancy": score, "nfev": int(opt.nfev), "message": str(opt.message)}
            else:
                r = residual(np.array([])); pars, nuisance = {}, {}; score = float(r @ r)
                receipt = {"start": index, "success": True, "discrepancy": score, "nfev": 1, "message": "fixed mechanism"}
            solutions.append({"parameters": pars, "nuisance": nuisance, "discrepancy": score, "converged": receipt["success"]})
            receipts.append(receipt)
        except DiagnosticBudgetExceeded:
            raise
        except (ValueError, FloatingPointError) as exc:
            receipts.append({"start": index, "success": False, "error": str(exc)})
    converged = [s for s in solutions if s["converged"]]
    best = min(converged or solutions, key=lambda x: x["discrepancy"]) if solutions else None
    state = "numerical_failure" if not converged else "retained" if best["discrepancy"] <= threshold else "inadequate_within_search"
    kept = []
    for solution in sorted(solutions, key=lambda item: item["discrepancy"]):
        if not solution["converged"] or solution["discrepancy"] > max(threshold, best["discrepancy"] + config.basin_loss_delta):
            continue
        normalized = np.array([(solution["parameters"][p.name] - p.lower) / (p.upper - p.lower) for p in specs])
        if all(np.linalg.norm(normalized - np.array([(other["parameters"][p.name] - p.lower) / (p.upper - p.lower) for p in specs])) > 1e-4 for other in kept):
            kept.append(solution)
    basins = tuple(kept)
    return FamilyFit(family.family_id, state, best["parameters"] if best else {}, best["nuisance"] if best else {}, best["discrepancy"] if best else None, threshold, basins, tuple(receipts), ids, tuple(asdict(p) for p in specs))


def refit_competition(families, data, forward, config, previous=None):
    previous = {fit.family_id: fit for fit in previous.fits} if previous else {}
    fits = tuple(_fit_family(family, data, forward, config, warm=tuple(b["parameters"] for b in previous[family.family_id].basins) if family.family_id in previous else ()) for family in families)
    retained = tuple(f.family_id for f in fits if f.state == "retained")
    failed = any(f.state == "numerical_failure" for f in fits)
    state = "numerical_failure" if failed else "model_family_inadequate" if not retained else "mechanisms_confounded" if len(retained) > 1 else "one_family_retained_within_search"
    selected = min((f for f in fits if f.state == "retained"), key=lambda f: f.discrepancy).family_id if retained else None
    return CompetitionResult(fits, retained, selected, state, tuple(d.acquisition_id for d in data))


@dataclass(frozen=True)
class DiagnosticSearchSpace:
    reference_indices: tuple[float, ...] = (1.55, 2.3)
    thicknesses_nm: tuple[float, ...] = (35., 90., 160.)
    gaps_nm: tuple[float, ...] = (0., 130.)
    angles_deg: tuple[float, ...] = (0., 35., 60.)
    polarizations: tuple[str, ...] = ("s", "p")
    wavelength_grids_nm: tuple[tuple[float, ...], ...] = (tuple(np.linspace(450., 750., 13)),)
    layer_tolerance_nm: float = 1.
    gap_tolerance_nm: float = 2.
    bandwidth_nm: float = 1.
    angular_spread_deg: float = .25
    mounting_cost: float = .3
    acquisition_cost: float = 1.
    fixed_frontend: DiagnosticFrontEnd = field(default_factory=lambda: DiagnosticFrontEnd("fixed-reference-baseline", (ReferenceLayer(2.,35.,1.),ReferenceLayer(1.,250.,2.)), .3))

    def __post_init__(self):
        for name in ("reference_indices","thicknesses_nm","gaps_nm","angles_deg","polarizations"):
            object.__setattr__(self,name,tuple(getattr(self,name)))
        object.__setattr__(self,"wavelength_grids_nm",tuple(tuple(float(v) for v in grid) for grid in self.wavelength_grids_nm))
        if isinstance(self.fixed_frontend, dict):
            data=dict(self.fixed_frontend); data["layers"]=tuple(ReferenceLayer(**v) for v in data.get("layers",()))
            object.__setattr__(self,"fixed_frontend",DiagnosticFrontEnd(**data))
        if not all((self.reference_indices, self.thicknesses_nm, self.gaps_nm, self.angles_deg, self.polarizations, self.wavelength_grids_nm)):
            raise ValueError("nonempty finite search axes required")
        count = (2 + len(self.reference_indices) * len(self.thicknesses_nm) * len(self.gaps_nm)) * len(self.angles_deg) * len(self.polarizations) * len(self.wavelength_grids_nm)
        if count > 10000:
            raise ValueError("diagnostic candidate generation exceeds 10000 combinations")

    def actions(self, *, sample_id, session_id, limit):
        fronts = [DiagnosticFrontEnd("bare"), self.fixed_frontend]
        for n, thickness, gap in product(self.reference_indices, self.thicknesses_nm, self.gaps_nm):
            layers = (ReferenceLayer(float(n), float(thickness), min(self.layer_tolerance_nm, thickness)),)
            if gap:
                layers += (ReferenceLayer(1., float(gap), min(self.gap_tolerance_nm, gap)),)
            fronts.append(DiagnosticFrontEnd("reference:" + canonical_hash([n, thickness, gap])[:12], layers, self.mounting_cost))
        actions = []
        for front, angle, pol, wavelengths in product(fronts, self.angles_deg, self.polarizations, self.wavelength_grids_nm):
            actions.append(DiagnosticAction("action:" + canonical_hash([front.content_hash, angle, pol, wavelengths])[:16], front, tuple(wavelengths), float(angle), pol, self.bandwidth_nm, self.angular_spread_deg, sample_id, session_id, self.acquisition_cost))
        if len(actions) > limit:
            bare = [a for a in actions if not a.frontend.layers]
            nonbare = [a for a in actions if a.frontend.layers]
            if len(bare) >= limit:
                actions = [bare[int(i)] for i in np.linspace(0, len(bare) - 1, limit, dtype=int)]
            else:
                actions = bare + [nonbare[int(i)] for i in np.linspace(0, len(nonbare) - 1, limit - len(bare), dtype=int)]
        return tuple(actions)


@dataclass(frozen=True)
class BoundedReadout:
    weights: tuple[float, ...]
    nonzero_indices: tuple[int, ...]
    variance: float
    separation: float
    raw_mahalanobis_separation: float
    expression: str
    scope: str = "frozen bounded linear intensity readout; no information gain beyond raw vector"


def optimize_readout(delta, covariance, *, max_terms):
    delta = np.asarray(delta, float)
    covariance = np.asarray(covariance, float)
    if covariance.shape != (len(delta), len(delta)) or max_terms < 1 or not np.isfinite(delta).all():
        raise ValueError("invalid bounded readout inputs")
    np.linalg.cholesky(covariance)
    selected = []
    for _ in range(min(max_terms, len(delta))):
        scores = []
        for i in set(range(len(delta))) - set(selected):
            ix = selected + [i]
            scores.append((float(delta[ix] @ np.linalg.solve(covariance[np.ix_(ix, ix)], delta[ix])), i))
        selected.append(max(scores, key=lambda p: (p[0], -p[1]))[1])
    local = np.linalg.solve(covariance[np.ix_(selected, selected)], delta[selected])
    weights = np.zeros(len(delta))
    if np.abs(local).sum():
        weights[selected] = local / np.abs(local).sum()
    else:
        weights[selected[0]] = 1.
    variance = float(weights @ covariance @ weights)
    score = float((weights @ delta) ** 2 / variance)
    raw = float(delta @ np.linalg.solve(covariance, delta))
    if score > raw + 1e-7 * max(1., raw):
        raise ValueError("derived readout cannot exceed raw information")
    expression = " + ".join(f"({weights[i]:.12g})*I[{i}]" for i in selected if weights[i])
    return BoundedReadout(tuple(map(float, weights)), tuple(sorted(selected)), variance, score, raw, expression)


@dataclass(frozen=True)
class DiagnosticSelection:
    selected: DiagnosticAction | None
    state: str
    candidates: tuple[dict, ...]
    readout: BoundedReadout | None
    minimum_refitted_separation: float | None
    compute_ledger: dict
    search_scope: str = "finite material/thickness/gap/angle/polarization/grid candidates, corner tolerances and multistart adversarial refits; no global optimum or exclusion guarantee"


def design_diagnostic_action(families, data, competition, forward, config, space, *, session_id, remaining_budget):
    fitted = {fit.family_id: fit for fit in competition.fits if fit.state == "retained"}
    active = tuple(f for f in families if f.family_id in fitted)
    if len(active) < 2:
        return DiagnosticSelection(None, competition.state, (), None, None, forward.ledger())
    actions = space.actions(sample_id=data[0].action.sample_id, session_id=session_id, limit=config.candidate_limit)
    candidates = []; screened = []
    for action in actions:
        row = {"action": asdict(action), "cost": action.cost, "status": "screened", "screening_separation": None, "refitted_separation": None}
        if action.cost > remaining_budget:
            row["status"] = "budget_rejected"; candidates.append(row); continue
        scenarios = []
        for fa, fb in combinations(active, 2):
            basins_a = fitted[fa.family_id].basins or ({"parameters": fitted[fa.family_id].parameters},)
            basins_b = fitted[fb.family_id].basins or ({"parameters": fitted[fb.family_id].parameters},)
            for vertex, ba, bb in product(tolerance_vertices(action.frontend), basins_a, basins_b):
                pa = forward.predict(fa, ba["parameters"], action, vertex)
                pb = forward.predict(fb, bb["parameters"], action, vertex)
                covariance = config.noise.covariance((pa + pb) / 2)
                readout = optimize_readout(pa - pb, covariance, max_terms=config.readout_terms)
                scenarios.append((pa-pb,covariance,readout,(fa.family_id,fb.family_id),vertex))
        proposals={tuple(np.round(scenario[2].weights,10)):scenario[2] for scenario in scenarios}
        robust=[]
        for proposed in proposals.values():
            weights=np.asarray(proposed.weights)
            scores=[float((weights@delta)**2/(weights@covariance@weights)) for delta,covariance,_,_,_ in scenarios]
            index=int(np.argmin(scores));delta,covariance,_,pair,vertex=scenarios[index]
            robust.append((scores[index],replace(proposed,variance=float(weights@covariance@weights),separation=scores[index],raw_mahalanobis_separation=float(delta@np.linalg.solve(covariance,delta))),pair,vertex))
        worst=max(robust,key=lambda item:item[0])
        row.update(screening_separation=worst[0], utility_per_cost=worst[0] / action.cost, worst_pair=worst[2], worst_tolerance_nm=worst[3])
        candidates.append(row); screened.append((row, action, worst[1]))
    if not screened:
        return DiagnosticSelection(None, "measurement_budget_exhausted", tuple(candidates), None, None, forward.ledger())
    screened.sort(key=lambda item: (-item[0]["utility_per_cost"], item[1].action_id))
    shortlist = screened[:config.adversarial_shortlist]
    for predicate in (lambda item: not item[1].frontend.layers, lambda item: item[1].frontend.frontend_id==space.fixed_frontend.frontend_id):
        baseline=next((item for item in screened if predicate(item)),None)
        if baseline is not None and all(baseline is not item for item in shortlist):
            shortlist.append(baseline)
    for row, action, readout in shortlist:
        adversaries = []
        for source, target in product(active, active):
            if source.family_id == target.family_id:
                continue
            source_fit = fitted[source.family_id]; target_fit = fitted[target.family_id]
            for vertex in tolerance_vertices(action.frontend):
                mean = forward.predict(source, source_fit.parameters, action, vertex)
                pseudo = DiagnosticDatum("prospective:" + action.content_hash, action, tuple(map(float, mean)), tuple(tuple(map(float, v)) for v in config.noise.covariance(mean)))
                fit = _fit_family(target, (*data, pseudo), forward, config, warm=tuple(b["parameters"] for b in target_fit.basins), starts=config.adversarial_starts, max_nfev=config.adversarial_max_nfev)
                score = max(0., fit.discrepancy - target_fit.threshold) if fit.discrepancy is not None and fit.state != "numerical_failure" else 0.
                adversaries.append({"source": source.family_id, "target": target.family_id, "tolerance_nm": vertex, "score": score, "fit": asdict(fit)})
        row["adversarial_refits"] = adversaries
        row["refitted_separation"] = min(a["score"] for a in adversaries)
        row["utility_per_cost"] = row["refitted_separation"] / action.cost
        row["status"] = "adversarial_refitted"
    best = max(shortlist, key=lambda item: (item[0]["utility_per_cost"], item[0]["screening_separation"]))
    best[0]["status"] = "selected" if best[0]["refitted_separation"] >= config.minimum_separation else "separation_insufficient"
    for row, action, _ in screened:
        if row["status"] == "screened":
            row["status"] = "outside_adversarial_shortlist"
        elif row["status"] == "adversarial_refitted":
            row["status"] = "lower_refitted_utility"
    state = "selected" if best[0]["refitted_separation"] >= config.minimum_separation else "mechanisms_confounded_within_budgeted_search"
    return DiagnosticSelection(best[1] if state == "selected" else None, state, tuple(candidates), best[2], best[0]["refitted_separation"], forward.ledger())


def action_from_dict(payload):
    payload = dict(payload)
    front = dict(payload.pop("frontend"))
    front["layers"] = tuple(ReferenceLayer(**row) for row in front.get("layers", ()))
    return DiagnosticAction(frontend=DiagnosticFrontEnd(**front), **payload)


def config_from_dict(payload):
    payload = dict(payload)
    if "noise" in payload:
        payload["noise"] = DiagnosticNoise(**payload["noise"])
    return DiagnosticConfig(**payload)


def family_from_dict(payload):
    if set(payload) != {"family_id", "model"}:
        raise ValueError("mechanism family requires only family_id and sample ModelIR")
    return MechanismFamily(payload["family_id"], model_ir_from_dict(payload["model"]))
