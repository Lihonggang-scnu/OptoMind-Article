"""TMM-only optimizer adapters for fixed-topology thickness design.

The optimizer layer deliberately stops at proposing designs.  A later pipeline
stage must re-simulate the returned candidates with an independent verifier.
Neither adapter turns a target score into a physics certificate.
"""

from __future__ import annotations

import importlib.util
import math
import time
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

import numpy as np

from tmm_engine import MaterialRegistry, OptimizationTask, TMMWorkbench
from tmm_engine.optimization import DifferentiableThicknessOptimizer


_SUPPORTED_VARIABLE_TYPES = frozenset(
    {
        "continuous",
        "continuous_thickness",
        "continuous_thicknesses",
        "thickness",
        "thicknesses",
    }
)
_RECOVERY_PURPOSES = frozenset({"recovery", "portfolio", "global", "fallback", "de"})


@dataclass(frozen=True)
class OptimizerDescriptor:
    """Machine-readable capabilities and selection metadata for an adapter."""

    optimizer_id: str
    optimizer_family: str = "continuous_thickness"
    supports_gradients: bool = False
    variable_types: tuple[str, ...] = ("thickness",)
    requires_gradients: bool = False
    supports_constraints: bool = True
    supports_multiobjective: bool = False
    supports_uncertainty: bool = False
    typical_dimension_range: tuple[int, int] = (1, 128)
    typical_cost: str = "low"
    selection_rank: int = 100
    execution_modes: tuple[str, ...] = ("local_native",)
    tmm_only: bool = True

    def __post_init__(self) -> None:
        if not str(self.optimizer_id).strip():
            raise ValueError("optimizer_id must not be empty")
        if not str(self.optimizer_family).strip():
            raise ValueError("optimizer_family must not be empty")
        object.__setattr__(
            self,
            "variable_types",
            tuple(str(value) for value in self.variable_types),
        )
        object.__setattr__(
            self,
            "execution_modes",
            tuple(str(value) for value in self.execution_modes),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OptimizerCapabilityFailure:
    """A capability failure that is specific to optimizer execution."""

    code: str
    message: str
    recoverable: bool = False
    context: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": str(self.code),
            "message": str(self.message),
            "recoverable": bool(self.recoverable),
            "context": dict(self.context),
        }


@dataclass(frozen=True)
class OptimizerCapabilityAssessment:
    """Result of checking an adapter before it starts an optimization run."""

    optimizer_id: str
    supported: bool
    failures: tuple[OptimizerCapabilityFailure, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def failure_codes(self) -> tuple[str, ...]:
        return tuple(str(item.code) for item in self.failures)

    @property
    def reason(self) -> Optional[str]:
        return self.failures[0].message if self.failures else None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "optimizer_id": self.optimizer_id,
            "supported": bool(self.supported),
            "failures": [item.to_dict() for item in self.failures],
            "warnings": list(self.warnings),
        }


class OptimizerCapabilityError(ValueError):
    """Raised when an adapter is asked to run an unsupported task."""

    def __init__(self, assessment: OptimizerCapabilityAssessment) -> None:
        self.assessment = assessment
        details = "; ".join(
            "%s: %s" % (failure.code, failure.message)
            for failure in assessment.failures
        )
        super().__init__(
            "Optimizer %s cannot run this task%s"
            % (assessment.optimizer_id, ": " + details if details else "")
        )


@dataclass
class AdapterOptimizationResult:
    """Common proposal result returned by all optimizer adapters."""

    status: str
    optimizer_id: str
    best_thicknesses_nm: list[float]
    best_loss: float
    candidate_designs: list[Dict[str, Any]] = field(default_factory=list)
    evaluation_count: int = 0
    stop_reason: str = ""
    wall_seconds: float = 0.0
    audit: Dict[str, Any] = field(default_factory=dict)

    @property
    def optimized_thicknesses_nm(self) -> list[float]:
        """Compatibility alias for the underlying tmm_engine result."""

        return self.best_thicknesses_nm

    @property
    def optimized_loss(self) -> float:
        """Compatibility alias for the underlying tmm_engine result."""

        return self.best_loss

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class _BudgetExhausted(RuntimeError):
    """Internal control flow used to stop SciPy before an extra forward call."""


def _dependency_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _normalize_variable_type(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw).strip().lower().replace("-", "_").replace(" ", "_")


def _variable_type_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [_normalize_variable_type(value)]
    if isinstance(value, Mapping):
        return [_normalize_variable_type(item) for item in value.keys()]
    try:
        return [_normalize_variable_type(item) for item in value]
    except TypeError:
        return [_normalize_variable_type(value)]


def _declared_variable_types(task: OptimizationTask) -> set[str]:
    """Read current and forward-compatible variable declarations.

    ``OptimizationTask`` currently models only thickness variables.  The
    introspection is intentionally small and defensive so a future design-space
    wrapper can be rejected rather than silently optimized as thickness.
    """

    values: list[str] = []
    owners = [task, getattr(task, "optimizer", None), getattr(task, "design_space", None)]
    for owner in owners:
        if owner is None:
            continue
        for name in ("variable_types", "variable_type"):
            if hasattr(owner, name):
                declared = getattr(owner, name)
                if declared is not None:
                    values.extend(_variable_type_values(declared))
        for name in ("variable_layer_count", "variable_layers", "layer_count_variable"):
            if bool(getattr(owner, name, False)):
                values.append("layer_count")

    stack = getattr(getattr(task, "simulation", None), "stack", None)
    for layer in getattr(stack, "layers", ()):
        if hasattr(layer, "variable_type"):
            declared = getattr(layer, "variable_type")
            if declared is not None:
                values.extend(_variable_type_values(declared))

    return set(values or ["thickness"])


def _assess_common(
    task: OptimizationTask,
    descriptor: OptimizerDescriptor,
    *,
    dependency: Optional[str] = None,
) -> OptimizerCapabilityAssessment:
    failures: list[OptimizerCapabilityFailure] = []

    if not isinstance(task, OptimizationTask):
        failures.append(
            OptimizerCapabilityFailure(
                "invalid_task",
                "optimizer adapters require tmm_engine.OptimizationTask",
            )
        )
        return OptimizerCapabilityAssessment(descriptor.optimizer_id, False, tuple(failures))

    simulation = getattr(task, "simulation", None)
    stack = getattr(simulation, "stack", None)
    layers = tuple(getattr(stack, "layers", ()))

    # These checks intentionally happen before task.validate(), whose generic
    # error for an empty design space would otherwise hide the capability reason.
    if bool(getattr(stack, "has_incoherent_layers", False)):
        failures.append(
            OptimizerCapabilityFailure(
                "incoherent_stack",
                "thickness adapters support fully coherent stacks only",
                recoverable=True,
                context={"optimizer_id": descriptor.optimizer_id},
            )
        )
    if layers and not any(bool(getattr(layer, "optimizable", False)) for layer in layers):
        failures.append(
            OptimizerCapabilityFailure(
                "no_optimizable_layers",
                "optimization task has no optimizable layers",
            )
        )

    variable_types = _declared_variable_types(task)
    unsupported = sorted(variable_types - _SUPPORTED_VARIABLE_TYPES)
    if unsupported:
        failures.append(
            OptimizerCapabilityFailure(
                "unsupported_variable_type",
                "TMM thickness adapters support continuous thickness variables only; "
                "unsupported variable types: %s" % ", ".join(unsupported),
                recoverable=True,
                context={
                    "declared_variable_types": sorted(variable_types),
                    "supported_variable_types": sorted(_SUPPORTED_VARIABLE_TYPES),
                },
            )
        )

    physics = getattr(simulation, "physics", None)
    if physics is not None:
        unsupported_physics: Dict[str, Any] = {}
        if getattr(physics, "geometry_class", None) != "layered_planar":
            unsupported_physics["geometry_class"] = getattr(physics, "geometry_class", None)
        if getattr(physics, "material_class", None) != "isotropic":
            unsupported_physics["material_class"] = getattr(physics, "material_class", None)
        if getattr(physics, "excitation_class", None) != "plane_wave":
            unsupported_physics["excitation_class"] = getattr(physics, "excitation_class", None)
        if bool(getattr(physics, "time_domain_required", False)):
            unsupported_physics["time_domain_required"] = True
        if unsupported_physics:
            failures.append(
                OptimizerCapabilityFailure(
                    "unsupported_tmm_domain",
                    "optimizer registry supports isotropic, planar, frequency-domain "
                    "TMM tasks with plane-wave excitation only",
                    recoverable=True,
                    context=unsupported_physics,
                )
            )

    validation_error: Optional[Exception] = None
    try:
        task.validate()
    except Exception as exc:  # Capability assessment must not execute an optimizer.
        validation_error = exc

    known_validation_failure = (
        any(item.code == "no_optimizable_layers" for item in failures)
        or any(item.code == "incoherent_stack" for item in failures)
        or any(item.code == "unsupported_variable_type" for item in failures)
    )
    if validation_error is not None and not known_validation_failure:
        failures.append(
            OptimizerCapabilityFailure("invalid_task", str(validation_error))
        )

    if dependency is not None and not _dependency_available(dependency):
        failures.append(
            OptimizerCapabilityFailure(
                "optional_dependency_missing",
                "%s is required by optimizer %s" % (dependency, descriptor.optimizer_id),
                recoverable=False,
                context={"dependency": dependency},
            )
        )

    return OptimizerCapabilityAssessment(
        descriptor.optimizer_id,
        not failures,
        tuple(failures),
    )


def _require_supported(adapter: Any, task: OptimizationTask) -> OptimizerCapabilityAssessment:
    assessment = adapter.assess(task)
    if not assessment.supported:
        raise OptimizerCapabilityError(assessment)
    return assessment


def _simulation_with_thicknesses(
    task: OptimizationTask,
    thicknesses_nm: Sequence[float],
) -> Any:
    values = np.asarray(thicknesses_nm, dtype=np.float64).reshape(-1)
    layers = tuple(task.simulation.stack.layers)
    if values.size != len(layers):
        raise ValueError(
            "thickness vector has %d values for %d layers" % (values.size, len(layers))
        )
    updated_layers = tuple(
        replace(layer, thickness_nm=float(value))
        for layer, value in zip(layers, values)
    )
    # The optimizer objective uses only scalar R/T/A channels.  Keeping the
    # forward call on the internal coherent backend avoids an accidental route
    # to a mixed-coherence or non-TMM family for unrelated requested outputs.
    return replace(
        task.simulation,
        solver="smatrix",
        requested_outputs=("R", "T", "A"),
        stack=replace(task.simulation.stack, layers=updated_layers),
    )


def _target_key(target: Any, index: int) -> str:
    return target.name or "%s_%g_%g_%g_%s_%d" % (
        target.observable,
        target.wavelength_min_nm,
        target.wavelength_max_nm,
        target.angle_deg,
        target.polarization,
        index,
    )


def _objective_from_forward(
    task: OptimizationTask,
    forward: Any,
) -> Tuple[float, Dict[str, float]]:
    wavelengths_nm = np.asarray(forward.wavelengths_nm, dtype=np.float64)
    weighted_loss = 0.0
    total_weight = 0.0
    metrics: Dict[str, float] = {}
    for index, target in enumerate(task.targets):
        channel = forward.channel(float(target.angle_deg), str(target.polarization))
        mask = (wavelengths_nm >= float(target.wavelength_min_nm)) & (
            wavelengths_nm <= float(target.wavelength_max_nm)
        )
        values = np.asarray(channel[target.observable], dtype=np.float64)[mask]
        if values.size == 0 or not np.all(np.isfinite(values)):
            return float("inf"), metrics
        if target.constraint == "match":
            errors = (values - float(target.target)) ** 2
        elif target.constraint == "at_least":
            errors = (1.0 - values) ** 2
        else:
            errors = values**2
        term = float(np.max(errors) if target.aggregation == "worst_case" else np.mean(errors))
        weighted_loss += float(target.weight) * term
        total_weight += float(target.weight)
        metrics[_target_key(target, index)] = float(np.mean(values))
    loss = weighted_loss / max(total_weight, 1e-12)
    return (float(loss) if np.isfinite(loss) else float("inf")), metrics


def _candidate_signature(values: Sequence[float]) -> tuple[float, ...]:
    return tuple(float(value) for value in np.round(np.asarray(values, dtype=np.float64), 9))


def _candidate_row(
    values: Sequence[float],
    loss: float,
    source: str,
    *,
    evaluation_index: Optional[int] = None,
    target_metrics: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "candidate_id": "",
        "source": source,
        "thicknesses_nm": [float(value) for value in values],
        "objective_loss": float(loss),
        "loss": float(loss),
        "verification_status": "pending_independent_verification",
        "physics_certified": False,
    }
    if evaluation_index is not None:
        row["evaluation_index"] = int(evaluation_index)
    if target_metrics is not None:
        row["optimizer_metrics"] = {
            str(key): float(value) for key, value in target_metrics.items()
        }
    return row


def _normalize_candidates(
    candidates: Iterable[Mapping[str, Any]],
    *,
    fallback_values: Sequence[float],
    fallback_loss: float,
    limit: int = 8,
) -> list[Dict[str, Any]]:
    collected: list[Dict[str, Any]] = []
    seen: set[tuple[float, ...]] = set()
    for raw in candidates:
        raw_values = raw.get("thicknesses_nm")
        if raw_values is None:
            raw_values = raw.get("physical_parameter_vector_nm")
        if raw_values is None:
            continue
        try:
            values = [float(value) for value in raw_values]
            loss = float(raw.get("objective_loss", raw.get("loss", float("inf"))))
        except (TypeError, ValueError):
            continue
        if not values or not np.all(np.isfinite(values)) or not np.isfinite(loss):
            continue
        signature = _candidate_signature(values)
        if signature in seen:
            continue
        seen.add(signature)
        row = _candidate_row(
            values,
            loss,
            str(raw.get("source", "optimizer")),
            evaluation_index=raw.get("evaluation_index"),
            target_metrics=raw.get("target_metrics") or raw.get("optimizer_metrics"),
        )
        collected.append(row)

    if not collected and np.isfinite(float(fallback_loss)):
        collected.append(_candidate_row(fallback_values, float(fallback_loss), "best"))
    collected.sort(key=lambda item: (float(item["objective_loss"]), item["thicknesses_nm"]))
    for index, row in enumerate(collected[: max(1, int(limit))], start=1):
        row["candidate_id"] = "candidate_%02d" % index
    return collected[: max(1, int(limit))]


class GradientThicknessAdapter:
    """Adapter around ``DifferentiableThicknessOptimizer`` on the CPU."""

    descriptor = OptimizerDescriptor(
        optimizer_id="gradient_thickness",
        optimizer_family="continuous_thickness_gradient",
        supports_gradients=True,
        variable_types=("thickness", "continuous_thickness"),
        requires_gradients=True,
        supports_constraints=True,
        typical_cost="low",
        selection_rank=10,
        execution_modes=("local_cpu",),
    )

    def __init__(
        self,
        material_registry: Optional[MaterialRegistry] = None,
        *,
        device: str = "cpu",
    ) -> None:
        self.material_registry = material_registry or MaterialRegistry()
        self.device = str(device)

    def assess(self, task: OptimizationTask) -> OptimizerCapabilityAssessment:
        return _assess_common(task, self.descriptor, dependency="torch")

    capability = assess

    def optimize(
        self,
        task: OptimizationTask,
        maximum_forward_evaluations: Optional[int] = None,  # accepted for API symmetry with DE adapter; gradient budget is governed by task.optimizer.max_steps
    ) -> AdapterOptimizationResult:
        assessment = _require_supported(self, task)
        started = time.perf_counter()
        optimizer = DifferentiableThicknessOptimizer(
            self.material_registry,
            device=self.device,
        )
        raw_result = optimizer.optimize(task)
        raw_audit = dict(getattr(raw_result, "audit", {}) or {})
        raw_evaluation_count = getattr(raw_result, "evaluation_count", None)
        if raw_evaluation_count is None:
            raw_evaluation_count = raw_audit.get("evaluation_count", 0)
        initial = np.asarray(
            [layer.thickness_nm for layer in task.simulation.stack.layers], dtype=np.float64
        )
        best = np.asarray(raw_result.optimized_thicknesses_nm, dtype=np.float64)
        fixed = np.asarray(
            [not layer.optimizable for layer in task.simulation.stack.layers], dtype=bool
        )
        best[fixed] = initial[fixed]
        candidates = _normalize_candidates(
            raw_result.candidate_designs,
            fallback_values=best,
            fallback_loss=float(raw_result.optimized_loss),
        )
        audit = {
            "capability_assessment": assessment.to_dict(),
            "backend": "tmm_engine.DifferentiableThicknessOptimizer",
            "device": self.device,
            "seed": int(task.optimizer.seed),
            "coherent_only": True,
            "tmm_only": True,
            "independent_verification_required": True,
            "physics_self_certification": False,
            "target_attainment_used_as_gate": False,
            "raw_optimizer_audit": raw_audit,
        }
        return AdapterOptimizationResult(
            status=str(raw_result.status),
            optimizer_id=self.descriptor.optimizer_id,
            best_thicknesses_nm=best.tolist(),
            best_loss=float(raw_result.optimized_loss),
            candidate_designs=candidates,
            evaluation_count=int(raw_evaluation_count),
            stop_reason=str(raw_result.stop_reason),
            wall_seconds=float(time.perf_counter() - started),
            audit=audit,
        )

    run = optimize


class DifferentialEvolutionThicknessAdapter:
    """Deterministic CPU global search over continuous layer thicknesses."""

    descriptor = OptimizerDescriptor(
        optimizer_id="differential_evolution_thickness",
        optimizer_family="continuous_thickness_global",
        supports_gradients=False,
        variable_types=("thickness", "continuous_thickness"),
        requires_gradients=False,
        supports_constraints=True,
        typical_cost="moderate",
        selection_rank=20,
        execution_modes=("local_cpu",),
    )

    def __init__(
        self,
        material_registry: Optional[MaterialRegistry] = None,
        *,
        population_size: int = 5,
        popsize: Optional[int] = None,
        max_iterations: Optional[int] = None,
        maxiter: Optional[int] = None,
        candidate_limit: int = 8,
    ) -> None:
        self.material_registry = material_registry or MaterialRegistry()
        selected_population = population_size if popsize is None else popsize
        selected_iterations = max_iterations if maxiter is None else maxiter
        if int(selected_population) < 1:
            raise ValueError("population_size must be positive")
        if selected_iterations is not None and int(selected_iterations) < 1:
            raise ValueError("max_iterations must be positive when supplied")
        if int(candidate_limit) < 1:
            raise ValueError("candidate_limit must be positive")
        self.population_size = int(selected_population)
        self.max_iterations = None if selected_iterations is None else int(selected_iterations)
        self.candidate_limit = int(candidate_limit)

    def assess(self, task: OptimizationTask) -> OptimizerCapabilityAssessment:
        return _assess_common(task, self.descriptor, dependency="scipy")

    capability = assess

    @staticmethod
    def _resolve_budget(
        maximum_forward_evaluations: Optional[int],
        max_forward_evaluations: Optional[int],
    ) -> int:
        if maximum_forward_evaluations is None:
            maximum_forward_evaluations = max_forward_evaluations
        elif max_forward_evaluations is not None:
            raise TypeError(
                "provide only one of maximum_forward_evaluations and max_forward_evaluations"
            )
        if maximum_forward_evaluations is None:
            raise TypeError("maximum_forward_evaluations is required for DE")
        budget = int(maximum_forward_evaluations)
        if budget < 1:
            raise ValueError("maximum_forward_evaluations must be positive")
        return budget

    def optimize(
        self,
        task: OptimizationTask,
        maximum_forward_evaluations: Optional[int] = None,
        *,
        max_forward_evaluations: Optional[int] = None,
    ) -> AdapterOptimizationResult:
        budget = self._resolve_budget(
            maximum_forward_evaluations,
            max_forward_evaluations,
        )
        assessment = _require_supported(self, task)
        started = time.perf_counter()

        try:
            from scipy.optimize import differential_evolution
        except ImportError as exc:  # pragma: no cover - guarded by assess()
            raise OptimizerCapabilityError(
                OptimizerCapabilityAssessment(
                    self.descriptor.optimizer_id,
                    False,
                    (
                        OptimizerCapabilityFailure(
                            "optional_dependency_missing",
                            "scipy is required by the differential-evolution adapter",
                            context={"dependency": "scipy"},
                        ),
                    ),
                )
            ) from exc

        layers = tuple(task.simulation.stack.layers)
        initial = np.asarray([layer.thickness_nm for layer in layers], dtype=np.float64)
        optimizable_indices = [
            index for index, layer in enumerate(layers) if bool(layer.optimizable)
        ]
        fixed_indices = [index for index in range(len(layers)) if index not in optimizable_indices]
        bounds = [
            tuple(
                float(value)
                for value in layers[index].bounds_nm(task.optimizer.thickness_window_nm)
            )
            for index in optimizable_indices
        ]
        for lo, hi in bounds:
            if not np.isfinite(lo) or not np.isfinite(hi) or lo > hi:
                raise ValueError("invalid bounds for optimizable layer")
        variable_initial = initial[np.asarray(optimizable_indices, dtype=np.int64)]

        workbench = TMMWorkbench(self.material_registry)
        evaluations: list[tuple[np.ndarray, float, Dict[str, float], int]] = []
        evaluation_count = 0
        best_values: Optional[np.ndarray] = None
        best_loss = float("inf")
        budget_hit = False

        def objective(variable_values: Sequence[float]) -> float:
            nonlocal evaluation_count, best_values, best_loss
            if evaluation_count >= budget:
                raise _BudgetExhausted()
            full_values = initial.copy()
            full_values[np.asarray(optimizable_indices, dtype=np.int64)] = np.asarray(
                variable_values,
                dtype=np.float64,
            )
            # Fixed entries are copied from the task once and are never handed
            # back to SciPy as variables.
            evaluation_count += 1
            simulation = _simulation_with_thicknesses(task, full_values)
            forward = workbench.simulate(simulation)
            loss, metrics = _objective_from_forward(task, forward)
            evaluations.append((full_values.copy(), float(loss), dict(metrics), evaluation_count))
            if np.isfinite(loss) and float(loss) < best_loss:
                best_loss = float(loss)
                best_values = full_values.copy()
            return float(loss)

        # A DE population has at least five members.  The iteration cap is
        # derived from the caller's budget as a second line of defence; the
        # objective guard remains the hard limit even if SciPy changes its call
        # schedule.
        dimension = len(bounds)
        population_count = max(5, self.population_size * max(1, dimension))
        budget_iterations = max(1, int(math.ceil(float(budget) / population_count)) + 1)
        configured_iterations = self.max_iterations
        if configured_iterations is None:
            configured_iterations = max(1, int(task.optimizer.max_steps))
        iterations = max(1, min(int(configured_iterations), budget_iterations))

        de_result: Any = None
        try:
            de_result = differential_evolution(
                objective,
                bounds=bounds,
                maxiter=iterations,
                popsize=self.population_size,
                tol=0.0,
                atol=0.0,
                mutation=(0.5, 1.0),
                recombination=0.7,
                seed=int(task.optimizer.seed),
                polish=False,
                updating="immediate",
                workers=1,
                x0=variable_initial,
            )
        except _BudgetExhausted:
            budget_hit = True

        if best_values is None:
            # The budget is positive and SciPy normally evaluates at least one
            # population member.  Keep the fallback explicit if a future SciPy
            # implementation exits before invoking the objective.
            best_values = initial.copy()
            best_loss = float("inf")

        candidate_rows = [
            _candidate_row(
                values,
                loss,
                "differential_evolution",
                evaluation_index=evaluation_index,
                target_metrics=metrics,
            )
            for values, loss, metrics, evaluation_index in evaluations
            if np.isfinite(loss)
        ]
        candidates = _normalize_candidates(
            candidate_rows,
            fallback_values=best_values,
            fallback_loss=best_loss,
            limit=self.candidate_limit,
        )
        if budget_hit:
            stop_reason = "maximum_forward_evaluations"
        elif de_result is not None and bool(getattr(de_result, "success", False)):
            stop_reason = "converged"
        elif de_result is not None:
            stop_reason = "maximum_iterations"
        else:
            stop_reason = "no_finite_candidate"

        status = "completed" if np.isfinite(best_loss) and not budget_hit else (
            "best_effort" if np.isfinite(best_loss) else "failed"
        )
        audit: Dict[str, Any] = {
            "capability_assessment": assessment.to_dict(),
            "backend": "scipy.optimize.differential_evolution",
            "seed": int(task.optimizer.seed),
            "maximum_forward_evaluations": int(budget),
            "population_size": int(population_count),
            "max_iterations": int(iterations),
            "optimizable_layer_indices": list(optimizable_indices),
            "fixed_layer_indices": list(fixed_indices),
            "bounds_nm": [list(bound) for bound in bounds],
            "coherent_only": True,
            "tmm_only": True,
            "independent_verification_required": True,
            "physics_self_certification": False,
            "target_attainment_used_as_gate": False,
            "budget_exhausted": bool(budget_hit),
        }
        if de_result is not None:
            audit["scipy_success"] = bool(getattr(de_result, "success", False))
            audit["scipy_message"] = str(getattr(de_result, "message", ""))
            audit["scipy_nit"] = int(getattr(de_result, "nit", 0))
        return AdapterOptimizationResult(
            status=status,
            optimizer_id=self.descriptor.optimizer_id,
            best_thicknesses_nm=best_values.tolist(),
            best_loss=float(best_loss),
            candidate_designs=candidates,
            evaluation_count=int(evaluation_count),
            stop_reason=stop_reason,
            wall_seconds=float(time.perf_counter() - started),
            audit=audit,
        )

    run = optimize


class OptimizerRegistry:
    """Registry with gradient-first normal selection and DE recovery selection."""

    def __init__(
        self,
        adapters: Optional[Iterable[Any]] = None,
        *,
        material_registry: Optional[MaterialRegistry] = None,
    ) -> None:
        if adapters is None:
            adapters = (
                GradientThicknessAdapter(material_registry),
                DifferentialEvolutionThicknessAdapter(material_registry),
            )
        self._adapters: Dict[str, Any] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: Any) -> None:
        descriptor = getattr(adapter, "descriptor", None)
        if not isinstance(descriptor, OptimizerDescriptor):
            raise TypeError("optimizer adapter must expose an OptimizerDescriptor")
        optimizer_id = str(descriptor.optimizer_id)
        if optimizer_id in self._adapters:
            raise ValueError("Duplicate optimizer_id: %s" % optimizer_id)
        self._adapters[optimizer_id] = adapter

    def get(self, optimizer_id: str) -> Any:
        try:
            return self._adapters[str(optimizer_id)]
        except KeyError as exc:
            raise KeyError("Optimizer is not registered: %s" % optimizer_id) from exc

    def select(
        self,
        task: OptimizationTask,
        purpose: str = "normal",
        *,
        mode: Optional[str] = None,
        optimizer_id: Optional[str] = None,
    ) -> Optional[Any]:
        if optimizer_id is not None:
            adapter = self.get(optimizer_id)
            return adapter if adapter.assess(task).supported else None

        selected_purpose = str(mode if mode is not None else purpose).strip().lower()
        recovery = selected_purpose in _RECOVERY_PURPOSES
        eligible: list[tuple[int, Any]] = []
        for adapter in self._adapters.values():
            if adapter.assess(task).supported:
                rank = int(adapter.descriptor.selection_rank)
                # Lower rank wins in normal mode; DE gets the explicit recovery
                # preference without changing the descriptor's normal ordering.
                if recovery:
                    rank = 0 if "differential_evolution" in adapter.descriptor.optimizer_id else rank + 1000
                eligible.append((rank, adapter))
        if not eligible:
            return None
        return sorted(eligible, key=lambda item: (item[0], item[1].descriptor.optimizer_id))[0][1]

    def select_recovery(self, task: OptimizationTask) -> Optional[Any]:
        return self.select(task, purpose="recovery")

    def descriptors(self) -> list[Dict[str, Any]]:
        return [adapter.descriptor.to_dict() for adapter in self._adapters.values()]


# Short aliases keep the adapter names convenient for callers while retaining
# the explicit public class requested by the harness contract.
DEThicknessAdapter = DifferentialEvolutionThicknessAdapter


__all__ = [
    "AdapterOptimizationResult",
    "DEThicknessAdapter",
    "DifferentialEvolutionThicknessAdapter",
    "GradientThicknessAdapter",
    "OptimizerCapabilityAssessment",
    "OptimizerCapabilityError",
    "OptimizerCapabilityFailure",
    "OptimizerDescriptor",
    "OptimizerRegistry",
]
