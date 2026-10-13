"""Constrained common-pool consumer of the existing thickness DE optimizer.

Scope: fixed-topology bounded thickness designs, declared box manufacturing
errors, and finite-world expected utility (or worst-world loss). This is not
continuous-observation EVSI, yield certification, or a global-optimality proof.
"""
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Mapping
import numpy as np
from .terminal_problem import TerminalDecisionProblem, canonical_hash, probability_vector


@dataclass(frozen=True)
class TerminalConstraints:
    version: str
    source_refs: tuple[str, ...]
    lower_nm: tuple[float, ...]
    upper_nm: tuple[float, ...]
    tolerance_nm: tuple[float, ...] = ()
    max_total_nm: float | None = None

    def __post_init__(self):
        object.__setattr__(self, "source_refs", tuple(self.source_refs))
        for key in ("lower_nm", "upper_nm", "tolerance_nm"):
            object.__setattr__(self, key, tuple(float(x) for x in getattr(self,key)))
        n = len(self.lower_nm)
        if not self.version or not self.source_refs or any(not x for x in self.source_refs):
            raise ValueError("constraint version and source references required")
        if not n or len(self.upper_nm) != n or (self.tolerance_nm and len(self.tolerance_nm) != n):
            raise ValueError("constraint thickness dimensions differ")
        lo, hi, tol = np.array(self.lower_nm), np.array(self.upper_nm), np.array(self.tolerance_nm or (0.,)*n)
        if not np.isfinite([lo,hi,tol]).all() or np.any(lo < 0) or np.any(hi < lo) or np.any(tol < 0):
            raise ValueError("invalid thickness/manufacturing bounds")
        if self.max_total_nm is not None and (not np.isfinite(self.max_total_nm) or self.max_total_nm <= 0):
            raise ValueError("invalid total thickness constraint")

    @property
    def identity(self): return canonical_hash(asdict(self))

    def check(self, values):
        x = np.asarray(values, dtype=float)
        if x.shape != (len(self.lower_nm),) or not np.isfinite(x).all():
            return {"feasible":False,"violations":["invalid_thickness_vector"],"constraint_ref":self.identity}
        tol = np.asarray(self.tolerance_nm or (0.,)*len(x))
        violations = []
        if np.any(x-tol < self.lower_nm): violations.append("manufacturing_lower_bound")
        if np.any(x+tol > self.upper_nm): violations.append("manufacturing_upper_bound")
        if self.max_total_nm is not None and np.sum(x+tol) > self.max_total_nm:
            violations.append("manufacturing_total_thickness")
        return {"feasible":not violations,"violations":violations,"constraint_ref":self.identity,
                "manufacturing_scope":"all points in declared thickness-error box; no probabilistic yield claim"}


@dataclass(frozen=True)
class TerminalObjective:
    """Per-world utility loss; risk_aversion has inverse declared loss units.

    For exponential utility, eta * raw_loss is dimensionless and expm1/eta
    returns the same units as raw_loss. When loss units scale by s, eta must
    scale by 1/s. These are objective semantics, not a probability model.
    """
    version: str
    source_ref: str
    transform: str = "identity"
    risk_aversion: float = 1.0

    def __post_init__(self):
        if not self.version or not self.source_ref or self.transform not in ("identity", "exponential"):
            raise ValueError("versioned supported objective and source required")
        if not np.isfinite(self.risk_aversion) or self.risk_aversion <= 0:
            raise ValueError("risk aversion must be positive")

    @property
    def identity(self): return canonical_hash(asdict(self))

    def loss(self, raw):
        raw = float(raw)
        with np.errstate(over="ignore", invalid="ignore"):
            value = raw if self.transform == "identity" else np.expm1(self.risk_aversion*raw)/self.risk_aversion
        if not np.isfinite(value): raise ValueError("nonfinite transformed terminal loss")
        return float(value)


@dataclass(frozen=True)
class ConstrainedPoolResult:
    problem: TerminalDecisionProblem | None
    solution: object | None
    designs: Mapping[str, tuple[float, ...]]
    receipt: dict


def _design_id(values):
    return f"thickness:{values[0]:.17g}" if len(values)==1 else "design:"+canonical_hash(values)


def build_constrained_pool(world_ids, designs, world_loss, context, units, constraints, objective, probabilities=None):
    """Evaluate only explicitly feasible designs; all rejection receipts survive."""
    worlds = tuple(world_ids)
    if not worlds or len(set(worlds)) != len(worlds): raise ValueError("unique nonempty worlds required")
    q = None if probabilities is None else probability_vector(probabilities,len(worlds))
    if not designs: raise ValueError("nonempty design proposal pool required")
    accepted, feasibility, columns = {}, {}, []
    evaluations = 0
    for name, raw in designs.items():
        values = tuple(float(x) for x in raw)
        receipt = constraints.check(values); feasibility[name] = receipt
        if not receipt["feasible"]: continue
        accepted[name] = values
        column = []
        for world in worlds:
            evaluations += 1
            column.append(objective.loss(world_loss(world,values)))
        columns.append(column)
    identity = dict(context, constraints=constraints.identity, terminal_objective=objective.identity,
                    compute_cost_contract="attempted-world-loss-and-optimizer-evaluations:v1")
    receipt = {"constraint_ref":constraints.identity,"objective_ref":objective.identity,
               "objective_units":{"raw_loss":units,"transformed_loss":units,
                                  "risk_aversion":"1/("+units+")","exponent":"dimensionless"},
               "posterior_ref":canonical_hash({"worlds":worlds,"probabilities":None if q is None else q.tolist()}),
               "source_refs":list(constraints.source_refs)+( [objective.source_ref]),
               "feasibility":feasibility,"rejected_design_ids":[d for d,r in feasibility.items() if not r["feasible"]],
               "cost_vector":{"world_loss_evaluations":evaluations,"optimizer_objective_evaluations":0},
               "cost_units":"attempted evaluations; model/solver resource costs in active compute ledger",
               "cost_refs":{"count_contract":identity["compute_cost_contract"],"model":context.get("model"),
                            "solver":context.get("solver"),"fidelity":context.get("fidelity")},
               "risk_semantics":"posterior expected transformed loss" if q is not None else "worst declared scenario transformed loss",
               "approximation_scope":"finite common pool, fixed topology, finite worlds, declared bounded manufacturing errors",
               "continuous_design_optimality":"unknown", "continuous_design_error_bound":None}
    if not accepted:
        receipt["status"] = "unknown_no_feasible_design"
        return ConstrainedPoolResult(None,None,MappingProxyType({}),receipt)
    problem = TerminalDecisionProblem(worlds,tuple(accepted),np.asarray(columns).T,identity,units)
    solution = problem.solve(q) if q is not None else problem.solve_robust(np.eye(len(worlds)))
    receipt.update(status="supported_on_declared_finite_pool", pool_ref=problem.cache_key,
                   solution={**asdict(solution),"feasibility":feasibility[solution.decision_id],
                             "error":0.,"error_scope":"no optimization approximation on finite pool; floating-point roundoff, model, sample and continuous design error not bounded"})
    return ConstrainedPoolResult(problem,solution,MappingProxyType(accepted),receipt)


def optimize_common_pool(*, task, world_ids, probabilities, world_loss, context, units, constraints,
                         objective, incumbent_designs, budget, adapter=None):
    """Use existing DE with sample-average nonlinear loss, then rescore union.

    budget bounds objective attempts, not physical forwards: one feasible attempt
    evaluates every world. Rescoring is additional explicitly reported work.
    An exhausted DE run is a useful candidate generator, never convergence proof.
    """
    from ..optimizer_registry import DifferentialEvolutionThicknessAdapter
    if type(budget) is not int or budget < 1: raise ValueError("positive integer optimizer budget required")
    worlds = tuple(world_ids)
    q = None if probabilities is None else probability_vector(probabilities,len(worlds))
    if len(task.simulation.stack.layers) != len(constraints.lower_nm):
        raise ValueError("optimizer and constraint dimensions differ")
    proposals, rejected = dict(incumbent_designs), {}
    attempted = 0
    def loss(values):
        nonlocal attempted
        name = _design_id(values)
        check = constraints.check(values)
        if not check["feasible"]:
            rejected[name] = check
            return float("inf")
        scores=[]
        for world in worlds:
            attempted += 1
            scores.append(objective.loss(world_loss(world, values)))
        proposals[name] = values
        return float(np.max(scores) if q is None else q @ np.asarray(scores))
    adapter = adapter or DifferentialEvolutionThicknessAdapter(candidate_limit=max(1,budget))
    run = adapter.optimize(task, maximum_forward_evaluations=budget, objective_callback=loss,
                           objective_ref=canonical_hash({"context":dict(context),"objective":objective.identity,
                               "constraints":constraints.identity,"worlds":worlds,"q":None if q is None else q.tolist()}))
    result = build_constrained_pool(worlds,proposals,world_loss,context,units,constraints,objective,q)
    result.receipt["feasibility"].update(rejected)
    result.receipt["rejected_design_ids"] = sorted(set(result.receipt["rejected_design_ids"])|set(rejected))
    result.receipt["cost_vector"]["world_loss_evaluations"] += attempted
    result.receipt["cost_vector"]["optimizer_objective_evaluations"] = run.evaluation_count
    result.receipt["optimizer"] = {"id":run.optimizer_id,"status":run.status,"stop_reason":run.stop_reason,
                                   "audit":run.audit,"evaluation_count":run.evaluation_count}
    result.receipt["refinement_status"] = "unknown_continuous_design_error; refine budget and compare union risk"
    return result


def rescore_pool_union(*, pools, world_ids, world_loss, context, units, constraints, objective, probabilities,
                       kernel, observation_ids, cost=0.):
    """Recompute both before/after risks on identical expanded C, never mix pools."""
    from .evsi import exact_evsi
    designs={}
    for pool in pools:
        for name,values in pool.items():
            values=tuple(values)
            if name in designs and designs[name]!=values: raise ValueError("design identity reused for different coordinates")
            designs[name]=values
    result=build_constrained_pool(world_ids,designs,world_loss,context,units,constraints,objective,probabilities)
    if result.problem is None: return result,None
    report=exact_evsi(result.problem,probabilities,kernel,observation_ids,cost=cost,cost_units=units)
    result.receipt["union_rescoring"]={"prior_risk":report.prior_risk,"posterior_risk":report.posterior_risk,
        "pool_ref":result.problem.cache_key,"value_interval":report.value_interval,
        "claim":"exact on this union only; neither lower nor upper bound on larger design spaces"}
    return result,report
