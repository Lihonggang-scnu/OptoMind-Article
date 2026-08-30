"""Verifier-first, TMM-only optical experiment orchestration.

This module is intentionally a small deterministic state machine rather than a
free-form agent loop.  Qwen may be enabled for one bounded strategy choice,
but it never runs a solver, creates measurements, or certifies a candidate.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator

from optomind_research.runtime.artifact_store import append_jsonl, atomic_write_json
from optomind_research.runtime.cost_ledger import estimate_call_cost_cny
from tmm_engine import MaterialRegistry, OptimizationTask, SimulationTask
from tmm_engine.acceptance import AcceptanceSettings
from tmm_engine.capabilities import FailureCode, FailureRecord
from tmm_engine.convergence import SpectralConvergenceSettings
from tmm_engine.hashing import stable_sha256
from tmm_engine.schemas import dataclass_to_dict
from tmm_engine.task_io import optimization_task_from_dict, simulation_task_from_dict

from .budget import BudgetLimits, BudgetOversubscriptionError, BudgetScheduler
from .contracts import ActionProposal, ActionType, ExperimentStatus
from .design_task import EngineMode, OpticalDesignTask, PhysicsVerificationPolicy
from .evaluator import TMMResultEvaluator
from .experiment_graph import ExperimentGraph
from .failure_diagnoser import TMMFailureDiagnoser
from .material_service import MaterialResolutionService
from .material_scenarios import MaterialScenario, enumerate_material_scenarios
from .objectives import (
    TMMRobustnessEvaluator,
    evaluate_declared_objectives,
    evaluate_optimization_objectives,
    simulation_with_thicknesses,
)
from .optimizer_registry import OptimizerRegistry
from .portfolio import DesignCandidate, PortfolioSelector, score_candidate
from .provenance import ArtifactLineageStore
from .qwen_policy import QWEN_POLICY_MODEL, QwenPolicyError, QwenTMMPolicy
from .runtime_fingerprint import build_runtime_fingerprint
from .solver_registry import SolverRegistry, TMMAdapter
from .state_machine import HarnessStage, HarnessStateMachine, TERMINAL_STAGES
from .stop_controller import FrontierObservation, TMMStopController


def _stable_hash(payload: Any) -> str:
    return stable_sha256(payload)


def _compact_candidate_id(value: str, *, maximum_length: int = 48) -> str:
    """Keep artifact paths below Windows limits without losing stable identity."""

    value = str(value)
    if len(value) <= maximum_length:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    prefix_length = max(8, maximum_length - len(digest) - 2)
    return f"{value[:prefix_length]}__{digest}"


def _artifact_directory_token(value: str, *, prefix: str) -> str:
    """Return a short, stable physical directory token for a logical ID.

    Logical experiment and candidate identifiers remain unchanged in every
    scientific artifact.  Only the on-disk path is compacted, preventing
    Windows MAX_PATH failures when a harness run itself lives under a deeply
    nested audit directory.
    """

    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


class TMMHarnessConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enable_global_optimizer: bool = True
    global_optimizer_forward_evaluations: int = 96
    maximum_candidates_per_experiment: int = 8
    robustness_candidate_limit: int = 4
    minimum_normalized_candidate_separation: float = 0.002
    use_qwen_policy: bool = False
    qwen_force_mock: bool | None = None

    @field_validator(
        "global_optimizer_forward_evaluations",
        "maximum_candidates_per_experiment",
        "robustness_candidate_limit",
    )
    @classmethod
    def _positive(cls, value: int) -> int:
        if int(value) < 1:
            raise ValueError("Harness integer limits must be positive")
        return int(value)

    @field_validator("minimum_normalized_candidate_separation")
    @classmethod
    def _separation(cls, value: float) -> float:
        if not 0.0 <= float(value) <= 1.0:
            raise ValueError("candidate separation must be in [0, 1]")
        return float(value)


class TMMHarnessRunResult(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    schema_version: str = "tmm-harness-result.v1"
    run_id: str
    task_id: str
    status: str
    state_stage: str
    experiment_results: tuple[Dict[str, Any], ...] = ()
    budget: Dict[str, Any] = Field(default_factory=dict)
    qwen_usage: tuple[Dict[str, Any], ...] = ()
    stop_decision: Dict[str, Any] = Field(default_factory=dict)
    diagnoses: tuple[Dict[str, Any], ...] = ()
    wall_seconds: float = 0.0


def _summarize_diagnoses(
    records: Sequence[Mapping[str, Any]]
) -> tuple[Dict[str, Any], ...]:
    """Collapse per-candidate failure records into one entry per category.

    ``FAILURE_DIAGNOSES.json`` keeps every record for audit, but it is not the
    artifact the outer research loop reads: that loop consumes the run result
    and branches purely on ``category``.  Leaving the run result's ``diagnoses``
    empty therefore hid every physics and solver failure from the loop, which
    then had only the stop reason left to classify the iteration by -- so a run
    that failed nine passivity checks was reported as an environment fault.
    One deduplicated entry per category is enough for the loop to react and
    small enough not to bloat a terminal artifact.
    """

    summary: Dict[str, Dict[str, Any]] = {}
    for record in records:
        diagnosis = record.get("diagnosis")
        if not isinstance(diagnosis, Mapping):
            continue
        category = str(diagnosis.get("category") or "").strip()
        if not category:
            continue
        entry = summary.get(category)
        if entry is None:
            failure = record.get("failure")
            summary[category] = {
                "category": category,
                "occurrences": 1,
                "recoverable_with_tmm": bool(
                    diagnosis.get("recoverable_with_tmm")
                ),
                "explanation": str(diagnosis.get("explanation") or ""),
                "first_failure_code": str(
                    (failure or {}).get("code") or ""
                    if isinstance(failure, Mapping)
                    else ""
                ),
                "first_stage": str(record.get("stage") or ""),
            }
        else:
            entry["occurrences"] = int(entry["occurrences"]) + 1
    return tuple(summary.values())


def _budget_limits(task: OpticalDesignTask) -> BudgetLimits:
    policy = task.budget
    return BudgetLimits(
        wall_time_seconds=policy.wall_time_seconds,
        forward_evaluations=policy.maximum_forward_evaluations,
        optimizer_runs=policy.maximum_optimizer_runs,
        qwen_calls=policy.maximum_qwen_calls,
        qwen_input_tokens=policy.maximum_qwen_input_tokens,
        qwen_output_tokens=policy.maximum_qwen_output_tokens,
        qwen_cost_cny=policy.maximum_qwen_cost_cny,
    )


def _convergence_settings() -> SpectralConvergenceSettings:
    """Return the wavelength-only convergence contract this harness declares.

    The TMM adapter declares ``convergence_dimensions=("wavelength",)`` in
    ``solver_registry``, so the wavelength axis is the only axis this harness
    certifies. VeriTMM 1.0.0 added an angular refinement axis whose defaults are
    tuned for the coarse declared grids used in the engine's own tests; two of
    those defaults are wrong for this harness and are overridden here.

    ``max_intervals_per_round`` (upstream default 8) throttles refinement to at
    most 8 inserted wavelengths per round. Harness tasks declare grids of ~451
    points, so 6 rounds add at most 48 points and the pointwise deviation never
    falls below tolerance. Raising the cap to ``maximum_points`` restores the
    0.6.0 behaviour of refining every flagged interval, which reaches
    ``max_pointwise_deviation`` in 3 rounds on the DBR dev tasks.

    ``max_angular_deviation`` is set to 1.0 to record the angular residual
    without gating on it. R/T/A are bounded in [0, 1], so a midpoint
    interpolation residual cannot exceed 1.0 and the gate is inert by
    construction rather than by an arbitrary large number. This is deliberate
    and not a tolerance relaxation: TMM solves each declared angle exactly, so a
    large residual at an interpolated angle is a statement about interpolating
    between angles, not about the accuracy of any reported angle. Gating on it
    would reject correct discrete-angle spectra (a 0/30/60 deg DBR sweep shows a
    0.78 residual at 15 deg purely because the stopband shifts with angle).
    The per-round angular residual stays in the ledger under
    ``max_angular_deviation`` for inspection.
    """

    return SpectralConvergenceSettings(
        max_refinements=6,
        max_pointwise_deviation=5e-3,
        max_integral_deviation=1e-3,
        max_intervals_per_round=SpectralConvergenceSettings.maximum_points,
        max_angular_deviation=1.0,
    )


def _acceptance_settings(policy: PhysicsVerificationPolicy) -> AcceptanceSettings:
    return AcceptanceSettings(
        require_spectral_convergence=bool(policy.require_spectral_convergence),
        require_independent_solver=bool(policy.require_independent_solver),
        convergence=_convergence_settings(),
    )


def _certificate_evaluation_count(certificate: Dict[str, Any]) -> int:
    count = 1
    convergence = dict(certificate.get("spectral_convergence") or {})
    count += len(convergence.get("rounds") or [])
    independent = dict(certificate.get("independent_solver_check") or {})
    if independent.get("status") in {"passed", "failed"}:
        count += 1
    return count


def _physics_status(certificate: Dict[str, Any]) -> str:
    if certificate.get("accepted"):
        return str(certificate.get("status") or "physically_valid")
    return "rejected_physics"


def _with_declared_objective_rows(
    report: Any,
    declared: Sequence[Any],
    result: Any,
) -> Any:
    """Add the declared objectives' attainment rows to an optimize report.

    An optimize-mode experiment is scored twice and for two different readers.
    ``evaluate_optimization_objectives`` measures the targets THIS route chose,
    which is what its own optimizer and its own feedback loop need; its rows are
    keyed by target name and carry ``observable``, not ``metric``.  The run's
    frozen scoring standard is injected separately, as report-only preferences on
    ``TMMExperimentSpec.objectives``, and it needs ``metric`` plus the exact
    ``region`` to locate a row -- which only ``evaluate_declared_objectives``
    emits.

    Nothing was evaluating the second set here, so every optimize-mode candidate
    reached ``ScoringStandard.score`` with no row it could match and came back
    unscoreable, and the leaderboard the standard exists to produce was always
    empty.  Both sets are merged into one report: the declared rows are added
    under their own objective ids, and the route-local aggregate and directional
    loss are left exactly as the route's own targets computed them, because those
    drive this route's optimizer and must not start reflecting a standard the
    route was never told to pursue.

    Declared rows win on a key collision: a report-only row that the standard can
    locate is the point of the merge, and an optimizer target that happens to
    share its id is still present through the route's own aggregate.
    """

    if report is None or not declared or result is None:
        return report
    try:
        declared_report = evaluate_declared_objectives(declared, result)
    except Exception:
        # Report-only measurement must never take down a verified candidate:
        # an unscoreable candidate is a worse outcome than a slow one, but a
        # candidate lost to a reporting error is worse than both.
        return report
    merged = {
        **dict(report.target_attainment),
        **dict(declared_report.target_attainment),
    }
    return report.model_copy(update={"target_attainment": merged})


def _simplicity_score(thicknesses: Sequence[float], source: str) -> float:
    layer_count = len(thicknesses)
    base = 1.0 / (1.0 + 0.025 * max(layer_count - 1, 0))
    quantized = "quantized" in str(source).casefold() or all(
        abs(float(value) - round(float(value))) < 1e-8 for value in thicknesses
    )
    return float(min(1.0, base + (0.05 if quantized else 0.0)))


class TMMHarnessOrchestrator:
    def __init__(
        self,
        work_dir: str | Path,
        *,
        run_id: str | None = None,
        resume: bool = False,
        config: TMMHarnessConfig | None = None,
        material_registry: MaterialRegistry | None = None,
        qwen_policy: QwenTMMPolicy | None = None,
    ) -> None:
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = str(run_id or uuid.uuid4().hex[:12])
        self.resume = bool(resume)
        self.config = config or TMMHarnessConfig()
        self.registry = material_registry or MaterialRegistry()
        self.materials = MaterialResolutionService(self.registry)
        self.solver_adapter = TMMAdapter(self.registry)
        self.solvers = SolverRegistry((self.solver_adapter,))
        self.optimizers = OptimizerRegistry(material_registry=self.registry)
        self.qwen_policy = qwen_policy or QwenTMMPolicy()
        self.diagnoser = TMMFailureDiagnoser()
        self.evaluator = TMMResultEvaluator()
        self.graph = ExperimentGraph(self.work_dir / "EXPERIMENT_GRAPH.sqlite", self.run_id)
        self.state = HarnessStateMachine(self.work_dir, self.run_id, resume=self.resume)
        self.provenance = ArtifactLineageStore(self.work_dir, resume=self.resume)
        self._budget: BudgetScheduler | None = None
        self._qwen_usage: list[Dict[str, Any]] = []
        self._diagnostic_records: list[Dict[str, Any]] = []
        self._material_scenarios: Dict[str, tuple[MaterialScenario, ...]] = {}
        self._certified_results: Dict[tuple[str, str], Any] = {}
        self._events_path = self.work_dir / "EVENTS.jsonl"
        self._event_sequence = 0
        if self.resume and self._events_path.exists():
            with self._events_path.open("r", encoding="utf-8") as handle:
                self._event_sequence = sum(1 for line in handle if line.strip())
        elif not self.resume:
            self._append_event("run_created", stage=self.state.stage.value)

    def _experiment_directory(self, experiment_id: str) -> Path:
        legacy = self.work_dir / "experiments" / str(experiment_id)
        # Preserve the historical, human-readable layout whenever there is
        # enough headroom for candidate reports beneath it.  Deep run roots
        # switch deterministically to a compact directory before Windows gets
        # close to MAX_PATH.
        if len(str(legacy.resolve())) <= 180:
            return legacy
        return self.work_dir / "x" / _artifact_directory_token(
            experiment_id,
            prefix="e",
        )

    def _baseline_directory(self, experiment_id: str) -> Path:
        experiment_directory = self._experiment_directory(experiment_id)
        if experiment_directory.parent.name == "experiments":
            return experiment_directory / "baseline"
        return experiment_directory / "b"

    def _candidate_directory(self, experiment_id: str, candidate_id: str) -> Path:
        experiment_directory = self._experiment_directory(experiment_id)
        legacy = experiment_directory / "candidates" / str(candidate_id)
        longest_leaf = "MATERIAL_DATASET_UNCERTAINTY.json"
        if len(str((legacy / longest_leaf).resolve())) <= 235:
            return legacy
        return experiment_directory / "c" / _artifact_directory_token(
            candidate_id,
            prefix="c",
        )

    def _material_scenario_directory(
        self,
        experiment_id: str,
        scenario_id: str,
    ) -> Path:
        experiment_directory = self._experiment_directory(experiment_id)
        legacy = experiment_directory / "material_scenarios" / str(scenario_id)
        if len(str((legacy / "MATERIAL_MANIFEST.json").resolve())) <= 235:
            return legacy
        return experiment_directory / "m" / _artifact_directory_token(
            scenario_id,
            prefix="s",
        )

    def _candidate_material_scenario_directory(
        self,
        experiment_id: str,
        candidate_id: str,
        scenario_id: str,
    ) -> Path:
        candidate_directory = self._candidate_directory(
            experiment_id,
            candidate_id,
        )
        legacy = candidate_directory / "material_scenarios" / str(scenario_id)
        if len(
            str((legacy / "PHYSICS_ACCEPTANCE_CERTIFICATE.json").resolve())
        ) <= 235:
            return legacy
        return candidate_directory / "m" / _artifact_directory_token(
            scenario_id,
            prefix="s",
        )

    def _relative_artifact_path(self, path: Path) -> str:
        return path.resolve().relative_to(self.work_dir.resolve()).as_posix()

    def _write_artifact_path_index(self, task: OpticalDesignTask) -> None:
        """Persist the reversible logical-ID to physical-directory mapping."""

        payload = {
            "schema_version": "tmm-artifact-path-index.v1",
            "path_policy": "stable_hashed_directories_for_windows_path_safety",
            "experiments": [
                {
                    "experiment_id": spec.experiment_id,
                    "physical_directory": self._relative_artifact_path(
                        self._experiment_directory(spec.experiment_id)
                    ),
                }
                for spec in task.experiments
            ],
        }
        path = self.work_dir / "ARTIFACT_PATH_INDEX.json"
        atomic_write_json(path, payload)
        self._register_artifact(
            path,
            artifact_type="artifact_path_index",
            producing_action="map_logical_ids_to_safe_physical_paths",
            input_artifact_ids=["TASK.json"],
        )

    def run(self, task: OpticalDesignTask) -> TMMHarnessRunResult:
        started = time.perf_counter()
        parsed: list[tuple[Any, EngineMode, SimulationTask | OptimizationTask]] = []
        baselines: Dict[str, Dict[str, Any]] = {}
        verified: Dict[str, List[Dict[str, Any]]] = {}
        experiment_results: list[Dict[str, Any]] = []
        final_path = self.work_dir / "FINAL_RESULT.json"
        if self.resume and final_path.exists() and self.state.stage in TERMINAL_STAGES:
            return TMMHarnessRunResult.model_validate_json(final_path.read_text(encoding="utf-8"))

        task_payload = task.model_dump(mode="json")
        task_hash = _stable_hash(task_payload)
        task_path = self.work_dir / "TASK.json"
        if task_path.exists():
            previous = json.loads(task_path.read_text(encoding="utf-8"))
            if _stable_hash(previous) != task_hash:
                raise ValueError("Resume task does not match the immutable TASK.json")
        else:
            atomic_write_json(task_path, task_payload)
        self._register_artifact(
            task_path,
            artifact_type="task_contract",
            producing_action="validate_task_contract",
            scientific_provenance={"task_sha256": task_hash, "engine": "tmm"},
        )
        self._write_artifact_path_index(task)
        config_path = self.work_dir / "HARNESS_CONFIG.json"
        config_payload = self.config.model_dump(mode="json")
        if config_path.exists():
            previous_config = json.loads(config_path.read_text(encoding="utf-8"))
            if _stable_hash(previous_config) != _stable_hash(config_payload):
                raise ValueError("Resume configuration does not match HARNESS_CONFIG.json")
        else:
            atomic_write_json(config_path, config_payload)
        self._register_artifact(
            config_path,
            artifact_type="harness_configuration",
            producing_action="freeze_harness_configuration",
            input_artifact_ids=["TASK.json"],
            scientific_provenance={
                "qwen_policy_model": QWEN_POLICY_MODEL,
                "performance_targets_used_as_gates": False,
            },
        )
        runtime_lock_path = self.work_dir / "RUNTIME_LOCK.json"
        runtime_lock = {
            "schema_version": "tmm-harness-runtime-lock.v1",
            "runtime": build_runtime_fingerprint(),
            "solver_adapters": self.solvers.descriptors(),
            "optimizer_adapters": self.optimizers.descriptors(),
            "material_catalog": self.registry.catalog_status(),
            "qwen_policy": {
                "allowed_model": QWEN_POLICY_MODEL,
                "model_fallback_allowed": False,
                "enabled": self.config.use_qwen_policy,
            },
        }
        if runtime_lock_path.exists():
            previous_lock = json.loads(
                runtime_lock_path.read_text(encoding="utf-8")
            )
            if _stable_hash(previous_lock) != _stable_hash(runtime_lock):
                raise ValueError("Resume runtime does not match RUNTIME_LOCK.json")
        else:
            atomic_write_json(runtime_lock_path, runtime_lock)
        self._register_artifact(
            runtime_lock_path,
            artifact_type="runtime_lock",
            producing_action="freeze_runtime_dependencies",
            input_artifact_ids=["HARNESS_CONFIG.json"],
        )
        self._budget = BudgetScheduler(
            _budget_limits(task), checkpoint_path=self.work_dir / "BUDGET.json"
        )
        if self.resume and self.state.stage not in TERMINAL_STAGES:
            return self._recover_interrupted_run(task, started)

        try:
            self._advance(HarnessStage.protocol_validated, "immutable_task_contract_validated", {"task_sha256": task_hash})
            parsed = self._parse_experiments(task)
            unsupported = self._capability_assessments(parsed)
            if unsupported:
                for assessment in unsupported:
                    for failure in assessment.get("failures", []):
                        self._record_failure_dict(
                            failure,
                            experiment_id=str(assessment.get("experiment_id") or ""),
                            stage="capability_classified",
                        )
                self._advance(
                    HarnessStage.capability_classified,
                    "tmm_capability_boundary_identified",
                    {"unsupported_experiment_count": len(unsupported)},
                )
                self._advance(HarnessStage.needs_higher_fidelity, "outside_tmm_only_domain", {"assessments": unsupported})
                return self._finish(task, "needs_higher_fidelity", [], started, {"reason": "outside_tmm_only_domain"})
            self._advance(HarnessStage.capability_classified, "all_experiments_supported_by_tmm", {"experiment_count": len(parsed)})

            parsed = self._prepare_material_scenarios(task, parsed)

            material_payloads = self._resolve_materials(parsed)
            unresolved = [item for item in material_payloads if not item["resolved"]]
            if unresolved:
                for manifest in unresolved:
                    for failure in manifest.get("failures", []):
                        self._record_failure_dict(
                            failure,
                            experiment_id=str(manifest.get("experiment_id") or ""),
                            stage="materials_resolved",
                        )
                self._advance(HarnessStage.diagnosing, "material_resolution_failed", {"manifests": unresolved})
                self._advance(HarnessStage.failed, "no_legal_material_resolution", {})
                return self._finish(task, "failed", [], started, {"reason": "material_resolution_failed"})
            self._advance(HarnessStage.materials_resolved, "material_manifests_frozen", {"manifest_hashes": [item["manifest_hash"] for item in material_payloads]})

            baselines = self._run_baselines(task, parsed)
            self._advance(HarnessStage.baseline_evaluated, "baseline_physics_evaluated", {"experiment_count": len(baselines)})

            has_optimization = any(item[1] == EngineMode.optimize for item in parsed)
            proposals: Dict[str, List[Dict[str, Any]]] = {}
            if has_optimization:
                self._advance(HarnessStage.searching, "bounded_optimizer_portfolio_started", {})
                proposals = self._run_optimizers(task, parsed)
                self._advance(HarnessStage.candidate_verification, "optimizer_proposals_require_independent_verification", {})
            else:
                self._advance(HarnessStage.candidate_verification, "forward_baselines_are_candidate_observations", {})

            verified = self._verify_candidates(task, parsed, baselines, proposals)
            self._run_material_dataset_uncertainty(task, parsed, verified)
            if any(item[1] == EngineMode.optimize for item in parsed):
                self._advance(HarnessStage.robustness_verification, "verified_candidates_enter_uncertainty_audit", {})
                self._run_robustness(task, parsed, verified)
            self._advance(HarnessStage.portfolio_ranking, "physics_valid_candidates_ranked_by_soft_scores", {})
            experiment_results = self._build_portfolios(task, parsed, baselines, verified)
            any_verified = any(result.get("physically_valid_candidate_count", 0) for result in experiment_results)
            status = "completed" if any_verified else "failed"
            if status == "completed":
                self._advance(HarnessStage.completed, "verified_portfolio_written", {})
            else:
                self._advance(HarnessStage.failed, "no_candidate_passed_physics_verification", {})
            stop = self._final_stop_decision(experiment_results)
            return self._finish(task, status, experiment_results, started, stop)
        except BudgetOversubscriptionError as exc:
            self._record_failure(
                FailureRecord(
                    FailureCode.BUDGET_EXHAUSTED,
                    str(exc),
                    False,
                    context={"remaining_budget": self._budget.remaining()},
                ),
                experiment_id="",
                stage=self.state.stage.value,
            )
            if self.state.stage not in TERMINAL_STAGES:
                self._advance(
                    HarnessStage.diagnosing,
                    "operational_budget_exhausted",
                    {"message": str(exc)},
                )

            # A late budget exhaustion must not discard already certified
            # science.  Assemble only the experiments whose baseline and
            # candidate verification both finished; never invent missing
            # results for incomplete experiments.
            completed_parsed = [
                item
                for item in parsed
                if item[0].experiment_id in baselines
                and item[0].experiment_id in verified
            ]
            if completed_parsed:
                self._advance(
                    HarnessStage.portfolio_ranking,
                    "rank_verified_results_before_budget_stop",
                    {"completed_experiments": len(completed_parsed)},
                )
                experiment_results = self._build_portfolios(
                    task,
                    completed_parsed,
                    baselines,
                    verified,
                )
                any_verified = any(
                    item.get("physically_valid_candidate_count", 0)
                    for item in experiment_results
                )
                if any_verified:
                    self._advance(
                        HarnessStage.completed,
                        "best_effort_verified_portfolio_preserved",
                        {},
                    )
                    return self._finish(
                        task,
                        "completed_best_effort_budget_exhausted",
                        experiment_results,
                        started,
                        self._final_stop_decision(experiment_results),
                    )

            self._advance(
                HarnessStage.failed,
                "budget_exhausted_before_any_verified_portfolio",
                {},
            )
            return self._finish(
                task,
                "budget_exhausted",
                [],
                started,
                {
                    "should_stop": True,
                    "reason": "budget_exhausted_before_verified_portfolio",
                    "best_effort": True,
                },
            )
        except Exception as exc:
            if self.state.stage not in TERMINAL_STAGES:
                try:
                    self._advance(HarnessStage.diagnosing, "unhandled_runtime_exception", {"error_type": type(exc).__name__, "message": str(exc)})
                    self._advance(HarnessStage.failed, "runtime_exception_fail_closed", {})
                except Exception:
                    pass
            atomic_write_json(self.work_dir / "FAILURE.json", {"error_type": type(exc).__name__, "message": str(exc)})
            try:
                self._register_artifact(
                    self.work_dir / "FAILURE.json",
                    artifact_type="failure_report",
                    producing_action="fail_closed",
                    input_artifact_ids=["TASK.json"],
                )
            except Exception:
                pass
            raise

    def _recover_interrupted_run(
        self,
        task: OpticalDesignTask,
        started: float,
    ) -> TMMHarnessRunResult:
        """Recover an abrupt process interruption in an isolated child attempt.

        The partial attempt remains immutable.  Reserved work whose completion
        cannot be proven is conservatively charged, and a fresh deterministic
        attempt receives only the remaining operational budget.  This avoids
        reusing half-written numerical state while preserving a complete audit
        trail and the original total budget ceiling.
        """

        assert self._budget is not None
        interrupted_stage = self.state.stage.value
        snapshot = self._budget.snapshot()
        for action_id in list(snapshot.get("active_reservations") or {}):
            # A process may have completed none, some, or all of an interrupted
            # action.  Charging the full reservation is fail-safe and prevents
            # a restart from silently exceeding the declared budget.
            self._budget.commit(action_id)
        remaining = self._budget.remaining()
        assert isinstance(remaining, dict)
        has_optimization = any(
            spec.mode == EngineMode.optimize for spec in task.experiments
        )
        forward_remaining = int(remaining.get("forward_evaluations") or 0)
        optimizer_remaining = int(remaining.get("optimizer_runs") or 0)
        minimum_forward = 5 * len(task.experiments)
        if forward_remaining < minimum_forward or (
            has_optimization and optimizer_remaining < 1
        ):
            self._record_failure(
                FailureRecord(
                    FailureCode.BUDGET_EXHAUSTED,
                    "Insufficient remaining budget for a clean recovery attempt.",
                    False,
                    context={
                        "remaining_budget": remaining,
                        "minimum_forward_evaluations": minimum_forward,
                        "optimization_present": has_optimization,
                    },
                ),
                experiment_id="",
                stage=self.state.stage.value,
                action_id="interruption_recovery",
            )
            if self.state.stage != HarnessStage.diagnosing:
                self._advance(
                    HarnessStage.diagnosing,
                    "interrupted_run_recovery_budget_check",
                    {"remaining_budget": remaining},
                )
            self._advance(
                HarnessStage.failed,
                "insufficient_budget_for_interruption_recovery",
                {},
            )
            return self._finish(
                task,
                "budget_exhausted_during_recovery",
                [],
                started,
                {
                    "should_stop": True,
                    "reason": "insufficient_budget_for_clean_recovery",
                    "best_effort": True,
                },
            )

        attempt_root = self.work_dir / "recovery_attempts" / "attempt_001"
        if attempt_root.exists():
            raise FileExistsError(
                "Recovery attempt already exists; inspect it before another retry."
            )
        original_budget = task.budget

        def _positive_int(resource: str, fallback: int) -> int:
            value = remaining.get(resource)
            return max(1, int(fallback if value is None else value))

        def _positive_float(resource: str, fallback: float) -> float:
            value = remaining.get(resource)
            return max(1.0e-9, float(fallback if value is None else value))

        recovery_budget = original_budget.model_copy(
            update={
                "wall_time_seconds": _positive_float(
                    "wall_time_seconds", original_budget.wall_time_seconds
                ),
                "maximum_forward_evaluations": _positive_int(
                    "forward_evaluations",
                    original_budget.maximum_forward_evaluations,
                ),
                "maximum_optimizer_runs": _positive_int(
                    "optimizer_runs", original_budget.maximum_optimizer_runs
                ),
                # Qwen is disabled in the recovery child.  Positive schema
                # placeholders are harmless and cannot consume parent budget.
                "maximum_qwen_calls": 1,
                "maximum_qwen_input_tokens": 1,
                "maximum_qwen_output_tokens": 1,
                "maximum_qwen_cost_cny": 1.0e-9,
            }
        )
        recovery_task = task.model_copy(update={"budget": recovery_budget})
        recovery_config = self.config.model_copy(
            update={"use_qwen_policy": False, "qwen_force_mock": None}
        )
        reservation_id = "interruption_recovery_attempt_001"
        self._budget.reserve(
            reservation_id,
            forward_evaluations=forward_remaining,
            optimizer_runs=optimizer_remaining if has_optimization else 0,
        )
        if self.state.stage != HarnessStage.diagnosing:
            self._advance(
                HarnessStage.diagnosing,
                "abrupt_interruption_detected",
                {
                    "recovery_attempt": "recovery_attempts/attempt_001",
                    "conservative_reservation_reconciliation": True,
                },
            )
        try:
            child_result = TMMHarnessOrchestrator(
                attempt_root,
                run_id=f"{self.run_id}.recovery01",
                config=recovery_config,
                material_registry=self.registry,
            ).run(recovery_task)
        except Exception:
            # The isolated attempt owns its own checkpoint.  If it cannot
            # report measured usage, charge the conservative parent reservation.
            self._budget.commit(reservation_id)
            raise
        child_usage = dict(child_result.budget.get("usage") or {})
        self._budget.commit(
            reservation_id,
            forward_evaluations=int(child_usage.get("forward_evaluations") or 0),
            optimizer_runs=int(child_usage.get("optimizer_runs") or 0),
        )
        child_manifest_id = "recovery_attempts/attempt_001/ARTIFACT_MANIFEST.json"
        child_final_id = "recovery_attempts/attempt_001/FINAL_RESULT.json"
        self._register_artifact(
            attempt_root / "ARTIFACT_MANIFEST.json",
            artifact_type="recovery_attempt_lineage",
            producing_action="restart_after_interruption",
            input_artifact_ids=["TASK.json"],
        )
        self._register_artifact(
            attempt_root / "FINAL_RESULT.json",
            artifact_type="recovery_attempt_result",
            producing_action="restart_after_interruption",
            input_artifact_ids=[child_manifest_id],
        )
        recovery_report = {
            "schema_version": "tmm-interruption-recovery.v1",
            "source_stage": interrupted_stage,
            "strategy": "isolated_deterministic_restart_with_remaining_budget",
            "qwen_disabled_in_recovery": True,
            "child_run_id": child_result.run_id,
            "child_status": child_result.status,
            "child_result_artifact_id": child_final_id,
            "child_budget_usage": child_usage,
            "aggregate_budget": self._budget.snapshot(),
        }
        recovery_report_path = self.work_dir / "RECOVERY_REPORT.json"
        atomic_write_json(recovery_report_path, recovery_report)
        self._register_artifact(
            recovery_report_path,
            artifact_type="interruption_recovery_report",
            producing_action="reconcile_interrupted_run",
            input_artifact_ids=[child_final_id],
        )

        if child_result.status.startswith("completed"):
            self._advance(
                HarnessStage.portfolio_ranking,
                "adopt_verified_recovery_portfolio",
                {"child_run_id": child_result.run_id},
            )
            self._advance(
                HarnessStage.completed,
                "interruption_recovery_completed",
                {},
            )
            stop = dict(child_result.stop_decision)
            stop["interruption_recovery"] = recovery_report
            return self._finish(
                task,
                "completed_after_interruption_recovery",
                child_result.experiment_results,
                started,
                stop,
            )
        if child_result.status == "needs_higher_fidelity":
            self._advance(
                HarnessStage.needs_higher_fidelity,
                "recovery_confirmed_tmm_capability_boundary",
                {},
            )
            return self._finish(
                task,
                child_result.status,
                child_result.experiment_results,
                started,
                child_result.stop_decision,
            )
        self._advance(HarnessStage.failed, "recovery_attempt_failed", {})
        return self._finish(
            task,
            "failed_after_interruption_recovery",
            child_result.experiment_results,
            started,
            child_result.stop_decision,
        )

    def _register_artifact(
        self,
        path: str | Path,
        *,
        artifact_type: str,
        producing_action: str,
        input_artifact_ids: Iterable[str] = (),
        producing_node: str | None = None,
        scientific_provenance: Dict[str, Any] | None = None,
    ) -> str:
        absolute = Path(path).resolve()
        relative = absolute.relative_to(self.work_dir.resolve()).as_posix()
        self.provenance.register_file(
            absolute,
            artifact_id=relative,
            artifact_type=artifact_type,
            producing_action=producing_action,
            producing_node=producing_node,
            input_artifact_ids=tuple(input_artifact_ids),
            scientific_provenance=scientific_provenance,
        )
        return relative

    def _record_failure(
        self,
        failure: FailureRecord,
        *,
        experiment_id: str,
        stage: str,
        action_id: str | None = None,
    ) -> Dict[str, Any]:
        diagnosis = self.diagnoser.diagnose(failure)
        payload = {
            "sequence": len(self._diagnostic_records) + 1,
            "experiment_id": str(experiment_id),
            "stage": str(stage),
            "action_id": action_id,
            "failure": failure.to_dict(),
            "diagnosis": diagnosis.model_dump(mode="json"),
        }
        self._diagnostic_records.append(payload)
        self._append_event(
            "failure_diagnosed",
            stage=stage,
            experiment_id=experiment_id,
            action_id=action_id,
            failure_code=failure.code.value,
            recoverable=failure.recoverable,
        )
        return payload

    def _record_failure_dict(
        self,
        failure: Dict[str, Any],
        *,
        experiment_id: str,
        stage: str,
        action_id: str | None = None,
    ) -> Dict[str, Any]:
        try:
            code = FailureCode(str(failure.get("code") or FailureCode.INVALID_TASK.value))
        except ValueError:
            code = FailureCode.INVALID_TASK
        record = FailureRecord(
            code=code,
            message=str(failure.get("message") or "Unspecified TMM failure."),
            recoverable=bool(failure.get("recoverable", False)),
            suggested_solver_family=failure.get("suggested_solver_family"),
            context=dict(failure.get("context") or {}),
        )
        return self._record_failure(
            record,
            experiment_id=experiment_id,
            stage=stage,
            action_id=action_id,
        )

    def _advance(self, stage: HarnessStage, reason: str, details: Dict[str, Any]) -> None:
        if self.state.stage == stage:
            return
        event = self.state.transition(stage, reason, details)
        self._append_event(
            "stage_transition",
            stage=stage.value,
            reason=reason,
            state_event_hash=event["event_hash"],
            details=details,
        )

    def _append_event(self, event_type: str, **payload: Any) -> None:
        self._event_sequence += 1
        append_jsonl(
            self._events_path,
            {
                "schema_version": "tmm-harness-event.v1",
                "sequence": self._event_sequence,
                "run_id": self.run_id,
                "event_type": str(event_type),
                "created_at_unix": time.time(),
                **payload,
            },
        )

    def _parse_experiments(self, task: OpticalDesignTask) -> list[tuple[Any, EngineMode, SimulationTask | OptimizationTask]]:
        output = []
        for spec in task.experiments:
            parsed = (
                simulation_task_from_dict(spec.tmm_task)
                if spec.mode == EngineMode.simulate
                else optimization_task_from_dict(spec.tmm_task)
            )
            output.append((spec, spec.mode, parsed))
        return output

    def _capability_assessments(self, parsed: Iterable[tuple[Any, EngineMode, Any]]) -> list[Dict[str, Any]]:
        unsupported = []
        for spec, mode, task in parsed:
            simulation = task if mode == EngineMode.simulate else task.simulation
            assessment = self.solver_adapter.assess(simulation)
            if not assessment.supported:
                unsupported.append({"experiment_id": spec.experiment_id, **assessment.to_dict()})
        return unsupported

    def _prepare_material_scenarios(
        self,
        task: OpticalDesignTask,
        parsed: Iterable[tuple[Any, EngineMode, SimulationTask | OptimizationTask]],
    ) -> list[tuple[Any, EngineMode, SimulationTask | OptimizationTask]]:
        prepared: list[tuple[Any, EngineMode, SimulationTask | OptimizationTask]] = []
        audit_rows: list[Dict[str, Any]] = []
        for spec, mode, parsed_task in parsed:
            if task.uncertainty.material_dataset_policy == "evaluate_all_eligible":
                scenarios, uncapped_count = enumerate_material_scenarios(
                    parsed_task,
                    self.materials,
                    maximum_scenarios=task.uncertainty.maximum_material_scenarios,
                )
            else:
                scenarios = (
                    MaterialScenario(
                        scenario_id="materials_primary",
                        assignments=(),
                        is_primary=True,
                        task=parsed_task,
                    ),
                )
                uncapped_count = 1
            self._material_scenarios[spec.experiment_id] = scenarios
            primary = next(item for item in scenarios if item.is_primary)
            prepared.append((spec, mode, primary.task))
            audit_rows.append(
                {
                    "experiment_id": spec.experiment_id,
                    "policy": task.uncertainty.material_dataset_policy,
                    "uncapped_scenario_count": uncapped_count,
                    "retained_scenario_count": len(scenarios),
                    "truncated": uncapped_count > len(scenarios),
                    "scenarios": [item.audit_dict() for item in scenarios],
                    "admission_role": "ranking_and_uncertainty_only",
                }
            )
        path = self.work_dir / "MATERIAL_SCENARIOS.json"
        atomic_write_json(
            path,
            {
                "schema_version": "tmm-material-scenarios.v1",
                "experiments": audit_rows,
                "performance_gate": False,
            },
        )
        self._register_artifact(
            path,
            artifact_type="material_scenario_plan",
            producing_action="plan_material_dataset_uncertainty",
            input_artifact_ids=["TASK.json"],
        )
        return prepared

    def _resolve_materials(self, parsed: Iterable[tuple[Any, EngineMode, Any]]) -> list[Dict[str, Any]]:
        manifests = []
        manifest_artifact_ids: list[str] = []
        for spec, _, task in parsed:
            directory = self._experiment_directory(spec.experiment_id)
            manifest = self.materials.resolve(task, work_dir=directory)
            manifests.append({"experiment_id": spec.experiment_id, **manifest.to_dict()})
            manifest_artifact_ids.append(
                self._register_artifact(
                    directory / "MATERIAL_MANIFEST.json",
                    artifact_type="material_manifest",
                    producing_action="resolve_materials",
                    input_artifact_ids=["TASK.json"],
                    scientific_provenance={
                        "experiment_id": spec.experiment_id,
                        "manifest_hash": manifest.manifest_hash,
                    },
                )
            )
            for scenario in self._material_scenarios.get(spec.experiment_id, ())[1:]:
                scenario_directory = self._material_scenario_directory(
                    spec.experiment_id,
                    scenario.scenario_id,
                )
                scenario_manifest = self.materials.resolve(
                    scenario.task,
                    work_dir=scenario_directory,
                )
                scenario_artifact_id = self._register_artifact(
                    scenario_directory / "MATERIAL_MANIFEST.json",
                    artifact_type="material_manifest",
                    producing_action="resolve_material_uncertainty_scenario",
                    input_artifact_ids=["MATERIAL_SCENARIOS.json"],
                    scientific_provenance={
                        "experiment_id": spec.experiment_id,
                        "scenario_id": scenario.scenario_id,
                        "manifest_hash": scenario_manifest.manifest_hash,
                    },
                )
                manifest_artifact_ids.append(scenario_artifact_id)
                if not scenario_manifest.resolved:
                    for failure in scenario_manifest.failures:
                        self._record_failure(
                            failure,
                            experiment_id=spec.experiment_id,
                            stage="materials_resolved",
                            action_id=f"material_scenario:{scenario.scenario_id}",
                        )
        atomic_write_json(self.work_dir / "MATERIAL_MANIFESTS.json", {"manifests": manifests})
        self._register_artifact(
            self.work_dir / "MATERIAL_MANIFESTS.json",
            artifact_type="material_manifest_collection",
            producing_action="freeze_material_manifests",
            input_artifact_ids=manifest_artifact_ids,
        )
        return manifests

    def _reserve_and_certify(
        self,
        action_id: str,
        simulation: SimulationTask,
        directory: Path,
        verification: PhysicsVerificationPolicy,
        *,
        input_artifact_ids: Iterable[str],
        producing_node: str | None = None,
    ) -> Dict[str, Any]:
        assert self._budget is not None
        self._budget.reserve(action_id, forward_evaluations=5)
        certified = self.solver_adapter.run(simulation, _acceptance_settings(verification))
        actual = _certificate_evaluation_count(certified.certificate)
        self._budget.commit(action_id, forward_evaluations=actual)
        directory.mkdir(parents=True, exist_ok=True)
        atomic_write_json(directory / "PHYSICS_ACCEPTANCE_CERTIFICATE.json", certified.certificate)
        result_artifact_id: str | None = None
        analysis_artifact_id: str | None = None
        if certified.result is not None:
            atomic_write_json(directory / "SIMULATION_RESULT.json", certified.result.to_dict())
            result_artifact_id = self._register_artifact(
                directory / "SIMULATION_RESULT.json",
                artifact_type="simulation_result",
                producing_action="run_tmm_forward",
                producing_node=producing_node,
                input_artifact_ids=input_artifact_ids,
            )
            self.evaluator.evaluate(certified.result, work_dir=directory)
            analysis_artifact_id = self._register_artifact(
                directory / "ANALYSIS_REPORT.json",
                artifact_type="analysis_report",
                producing_action="analyze_tmm_result",
                producing_node=producing_node,
                input_artifact_ids=[result_artifact_id],
            )
        certificate_inputs = [
            item
            for item in (result_artifact_id, analysis_artifact_id)
            if item is not None
        ] or list(input_artifact_ids)
        certificate_artifact_id = self._register_artifact(
            directory / "PHYSICS_ACCEPTANCE_CERTIFICATE.json",
            artifact_type="physics_acceptance_certificate",
            producing_action="verify_tmm_physics",
            producing_node=producing_node,
            input_artifact_ids=certificate_inputs,
        )
        return {
            "certified": certified,
            "evaluation_count": actual,
            "result_artifact_id": result_artifact_id,
            "analysis_artifact_id": analysis_artifact_id,
            "certificate_artifact_id": certificate_artifact_id,
        }

    def _run_baselines(self, task: OpticalDesignTask, parsed: Iterable[tuple[Any, EngineMode, Any]]) -> Dict[str, Dict[str, Any]]:
        baselines: Dict[str, Dict[str, Any]] = {}
        for spec, mode, parsed_task in parsed:
            simulation = parsed_task if mode == EngineMode.simulate else parsed_task.simulation
            directory = self._baseline_directory(spec.experiment_id)
            node = self._graph_node(spec.experiment_id, ActionType.generate_baseline, {"mode": mode.value})
            self.graph.set_status(node, ExperimentStatus.running)
            outcome = self._reserve_and_certify(
                f"baseline:{spec.experiment_id}",
                simulation,
                directory,
                task.verification,
                input_artifact_ids=[
                    self._relative_artifact_path(
                        self._experiment_directory(spec.experiment_id)
                        / "MATERIAL_MANIFEST.json"
                    )
                ],
                producing_node=node,
            )
            certificate = outcome["certified"].certificate
            self._certified_results[
                (spec.experiment_id, f"{spec.experiment_id}__baseline")
            ] = outcome["certified"].result
            status = _physics_status(certificate)
            if status == "rejected_physics":
                for failure in certificate.get("failures", []):
                    self._record_failure_dict(
                        failure,
                        experiment_id=spec.experiment_id,
                        stage="baseline_evaluated",
                        action_id=f"baseline:{spec.experiment_id}",
                    )
            graph_status = ExperimentStatus(status)
            self.graph.set_status(node, graph_status, certificate_id=certificate.get("certificate_id"))
            baselines[spec.experiment_id] = {"node_id": node, **outcome}
        return baselines

    def _graph_node(self, experiment_id: str, action: ActionType, parameters: Dict[str, Any], parents: Iterable[str] = ()) -> str:
        proposal = ActionProposal(action_type=action, parameters={"experiment_id": experiment_id, **parameters})
        return self.graph.create_node(_stable_hash(proposal.model_dump(mode="json")), proposal, parents)

    def _run_optimizers(self, task: OpticalDesignTask, parsed: Iterable[tuple[Any, EngineMode, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        assert self._budget is not None
        output: Dict[str, List[Dict[str, Any]]] = {}
        for spec, mode, parsed_task in parsed:
            if mode != EngineMode.optimize:
                continue
            optimization: OptimizationTask = parsed_task
            rows: list[Dict[str, Any]] = []
            primary = self.optimizers.select(optimization)
            if primary is not None and self._budget.can_reserve(f"optimizer:{spec.experiment_id}:primary", optimizer_runs=1):
                estimate = min(
                    int(optimization.optimizer.max_steps) * int(optimization.optimizer.starts) + 64,
                    int(self._budget.remaining("forward_evaluations") or 0),
                )
                if estimate > 0:
                    action_id = f"optimizer:{spec.experiment_id}:primary"
                    self._budget.reserve(action_id, optimizer_runs=1, forward_evaluations=estimate)
                    node = self._graph_node(spec.experiment_id, ActionType.run_optimizer, {"optimizer_id": primary.descriptor.optimizer_id})
                    self.graph.set_status(node, ExperimentStatus.running)
                    try:
                        # Pass the forward-evaluation budget so adapters that
                        # require it (e.g. DifferentialEvolution) receive a
                        # concrete cap derived from the budget scheduler.
                        result = primary.optimize(optimization, maximum_forward_evaluations=estimate)
                        self._budget.commit(action_id, optimizer_runs=1, forward_evaluations=int(result.evaluation_count))
                        optimizer_path = self._experiment_directory(
                            spec.experiment_id
                        ) / f"OPTIMIZER_{result.optimizer_id}.json"
                        atomic_write_json(optimizer_path, result.to_dict())
                        optimizer_artifact_id = self._register_artifact(
                            optimizer_path,
                            artifact_type="optimizer_result",
                            producing_action="run_optimizer",
                            producing_node=node,
                            input_artifact_ids=[
                                self._relative_artifact_path(
                                    self._baseline_directory(spec.experiment_id)
                                    / "PHYSICS_ACCEPTANCE_CERTIFICATE.json"
                                )
                            ],
                            scientific_provenance={"optimizer_id": result.optimizer_id},
                        )
                        new_rows = self._namespace_candidates(
                            spec.experiment_id, result.optimizer_id, result.candidate_designs
                        )
                        for row in new_rows:
                            row["optimizer_artifact_id"] = optimizer_artifact_id
                        rows.extend(new_rows)
                        self.graph.set_status(node, ExperimentStatus.candidate)
                    except Exception as exc:
                        self._budget.release(action_id)
                        self.graph.set_status(node, ExperimentStatus.failed, error_type=type(exc).__name__)
                        self._record_failure(
                            FailureRecord(
                                FailureCode.OPTIMIZER_FAILURE,
                                f"{type(exc).__name__}: {exc}",
                                True,
                                context={"optimizer_id": primary.descriptor.optimizer_id},
                            ),
                            experiment_id=spec.experiment_id,
                            stage="searching",
                            action_id=action_id,
                        )

            run_global = self.config.enable_global_optimizer and self._global_search_allowed(spec.experiment_id, rows)
            recovery = self.optimizers.select(optimization, purpose="recovery") if run_global else None
            if recovery is not None and (primary is None or recovery.descriptor.optimizer_id != primary.descriptor.optimizer_id):
                remaining = self._budget.remaining("forward_evaluations")
                available = self.config.global_optimizer_forward_evaluations if remaining is None else min(self.config.global_optimizer_forward_evaluations, int(remaining))
                action_id = f"optimizer:{spec.experiment_id}:global"
                if available > 0 and self._budget.can_reserve(action_id, optimizer_runs=1, forward_evaluations=available):
                    self._budget.reserve(action_id, optimizer_runs=1, forward_evaluations=available)
                    node = self._graph_node(spec.experiment_id, ActionType.run_optimizer, {"optimizer_id": recovery.descriptor.optimizer_id, "purpose": "global_diversity"})
                    self.graph.set_status(node, ExperimentStatus.running)
                    try:
                        result = recovery.optimize(optimization, maximum_forward_evaluations=available)
                        self._budget.commit(action_id, optimizer_runs=1, forward_evaluations=int(result.evaluation_count))
                        optimizer_path = self._experiment_directory(
                            spec.experiment_id
                        ) / f"OPTIMIZER_{result.optimizer_id}.json"
                        atomic_write_json(optimizer_path, result.to_dict())
                        optimizer_artifact_id = self._register_artifact(
                            optimizer_path,
                            artifact_type="optimizer_result",
                            producing_action="run_optimizer",
                            producing_node=node,
                            input_artifact_ids=[
                                self._relative_artifact_path(
                                    self._baseline_directory(spec.experiment_id)
                                    / "PHYSICS_ACCEPTANCE_CERTIFICATE.json"
                                )
                            ],
                            scientific_provenance={"optimizer_id": result.optimizer_id},
                        )
                        new_rows = self._namespace_candidates(
                            spec.experiment_id, result.optimizer_id, result.candidate_designs
                        )
                        for row in new_rows:
                            row["optimizer_artifact_id"] = optimizer_artifact_id
                        rows.extend(new_rows)
                        self.graph.set_status(node, ExperimentStatus.candidate)
                    except Exception as exc:
                        self._budget.release(action_id)
                        self.graph.set_status(node, ExperimentStatus.failed, error_type=type(exc).__name__)
                        self._record_failure(
                            FailureRecord(
                                FailureCode.OPTIMIZER_FAILURE,
                                f"{type(exc).__name__}: {exc}",
                                False,
                                context={"optimizer_id": recovery.descriptor.optimizer_id},
                            ),
                            experiment_id=spec.experiment_id,
                            stage="searching",
                            action_id=action_id,
                        )
            spans = [
                max(
                    layer.bounds_nm(optimization.optimizer.thickness_window_nm)[1]
                    - layer.bounds_nm(optimization.optimizer.thickness_window_nm)[0],
                    1.0,
                )
                for layer in optimization.simulation.stack.layers
            ]
            baseline_values = np.asarray(
                [layer.thickness_nm for layer in optimization.simulation.stack.layers],
                dtype=np.float64,
            )
            # Optimizer traces often include the unchanged initial point.  The
            # baseline has already been independently certified, so verifying
            # this exact duplicate again adds cost but no scientific option.
            rows = [
                row
                for row in rows
                if not np.allclose(
                    np.asarray(row["thicknesses_nm"], dtype=np.float64),
                    baseline_values,
                    rtol=0.0,
                    atol=1e-8,
                )
            ]
            output[spec.experiment_id] = self._deduplicate_candidates(
                rows,
                self.config.maximum_candidates_per_experiment,
                spans_nm=spans,
                minimum_normalized_separation=self.config.minimum_normalized_candidate_separation,
            )
        return output

    def _global_search_allowed(self, experiment_id: str, rows: Sequence[Dict[str, Any]]) -> bool:
        if not self.config.use_qwen_policy:
            return True
        assert self._budget is not None
        action_id = f"qwen:{experiment_id}:global_choice"
        if not self._budget.can_reserve(action_id, qwen_calls=1, qwen_input_tokens=3000, qwen_output_tokens=500, qwen_cost_cny=0.02):
            return True
        self._budget.reserve(action_id, qwen_calls=1, qwen_input_tokens=3000, qwen_output_tokens=500, qwen_cost_cny=0.02)
        try:
            decision, usage = self.qwen_policy.propose(
                {"experiment_id": experiment_id, "primary_candidate_count": len(rows), "best_optimizer_loss": min((float(item["objective_loss"]) for item in rows), default=None), "remaining_budget": self._budget.remaining()},
                [ActionType.run_optimizer, ActionType.stop],
                force_mock=self.config.qwen_force_mock,
            )
            input_tokens = int(usage.get("input_tokens") or usage.get("estimated_input_tokens") or 0)
            output_tokens = int(usage.get("output_tokens") or usage.get("estimated_output_tokens") or 0)
            cost = estimate_call_cost_cny(QWEN_POLICY_MODEL, input_tokens, output_tokens)
            self._budget.commit(action_id, qwen_calls=1, qwen_input_tokens=input_tokens, qwen_output_tokens=output_tokens, qwen_cost_cny=cost)
            self._qwen_usage.append(usage)
            return decision.action == ActionType.run_optimizer
        except QwenPolicyError as exc:
            self._budget.release(action_id)
            self._qwen_usage.append({"model_name": QWEN_POLICY_MODEL, "success": False, "error": str(exc), "deterministic_fallback": "run_global_optimizer"})
            return True

    @staticmethod
    def _namespace_candidates(experiment_id: str, optimizer_id: str, candidates: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
        output = []
        for index, item in enumerate(candidates, 1):
            row = dict(item)
            row["candidate_id"] = _compact_candidate_id(
                f"{experiment_id}__{optimizer_id}__{index:02d}"
            )
            row["optimizer_id"] = optimizer_id
            output.append(row)
        return output

    @staticmethod
    def _deduplicate_candidates(
        rows: Iterable[Dict[str, Any]],
        limit: int,
        *,
        spans_nm: Sequence[float] | None = None,
        minimum_normalized_separation: float = 0.0,
    ) -> list[Dict[str, Any]]:
        """Remove numerical clones without imposing a diversity requirement."""

        seen: set[tuple[float, ...]] = set()
        output: list[Dict[str, Any]] = []
        scale = None
        if spans_nm is not None:
            scale = np.maximum(np.asarray(spans_nm, dtype=np.float64), 1.0)
        for row in sorted(rows, key=lambda item: (float(item.get("objective_loss", math.inf)), str(item.get("candidate_id")))):
            values = np.asarray(row["thicknesses_nm"], dtype=np.float64)
            signature = tuple(float(value) for value in np.round(values, 8))
            if signature in seen:
                continue
            if scale is not None and scale.size == values.size and output:
                closest = min(
                    float(
                        np.linalg.norm(
                            (values - np.asarray(existing["thicknesses_nm"], dtype=np.float64))
                            / scale
                        )
                        / max(math.sqrt(values.size), 1.0)
                    )
                    for existing in output
                )
                if closest < float(minimum_normalized_separation):
                    continue
            seen.add(signature)
            output.append(row)
            if len(output) >= int(limit):
                break
        return output

    def _verify_candidates(
        self,
        task: OpticalDesignTask,
        parsed: Iterable[tuple[Any, EngineMode, Any]],
        baselines: Dict[str, Dict[str, Any]],
        proposals: Dict[str, List[Dict[str, Any]]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        output: Dict[str, List[Dict[str, Any]]] = {}
        for spec, mode, parsed_task in parsed:
            rows: list[Dict[str, Any]] = []
            if mode == EngineMode.simulate:
                certified = baselines[spec.experiment_id]["certified"]
                objective_report = None
                objective_artifact_id = None
                if spec.objectives:
                    if certified.result is None:
                        raise ValueError(
                            f"Cannot materialize objectives for {spec.experiment_id}: "
                            "the verified forward result is unavailable"
                        )
                    objective_report = evaluate_declared_objectives(
                        spec.objectives,
                        certified.result,
                    )
                    objective_path = self._baseline_directory(
                        spec.experiment_id
                    ) / "OBJECTIVE_REPORT.json"
                    atomic_write_json(
                        objective_path,
                        objective_report.model_dump(mode="json"),
                    )
                    objective_artifact_id = self._register_artifact(
                        objective_path,
                        artifact_type="objective_report",
                        producing_action="materialize_declared_forward_objectives",
                        input_artifact_ids=[
                            self._relative_artifact_path(
                                self._baseline_directory(spec.experiment_id)
                                / "SIMULATION_RESULT.json"
                            )
                        ],
                    )
                rows.append({
                    "candidate_id": f"{spec.experiment_id}__baseline",
                    "source": "forward_baseline",
                    "thicknesses_nm": [float(layer.thickness_nm) for layer in parsed_task.stack.layers],
                    "certificate": certified.certificate,
                    "physics_status": _physics_status(certified.certificate),
                    "objective_report": (
                        None
                        if objective_report is None
                        else objective_report.model_dump(mode="json")
                    ),
                    "robustness_report": None,
                    "artifact_ids": [
                        self._relative_artifact_path(
                            self._baseline_directory(spec.experiment_id)
                            / "PHYSICS_ACCEPTANCE_CERTIFICATE.json"
                        ),
                        *([objective_artifact_id] if objective_artifact_id else []),
                    ],
                })
                output[spec.experiment_id] = rows
                continue

            optimization: OptimizationTask = parsed_task
            baseline_certified = baselines[spec.experiment_id]["certified"]
            baseline_objective = None
            if baseline_certified.result is not None:
                baseline_objective = evaluate_optimization_objectives(
                    optimization, baseline_certified.result
                )
                baseline_objective = _with_declared_objective_rows(
                    baseline_objective, spec.objectives, baseline_certified.result
                )
                atomic_write_json(
                    self._baseline_directory(spec.experiment_id)
                    / "OBJECTIVE_REPORT.json",
                    baseline_objective.model_dump(mode="json"),
                )
                objective_artifact_id = self._register_artifact(
                    self._baseline_directory(spec.experiment_id)
                    / "OBJECTIVE_REPORT.json",
                    artifact_type="objective_report",
                    producing_action=(
                        "score_soft_objectives_with_declared_objectives"
                        if spec.objectives
                        else "score_soft_objectives"
                    ),
                    input_artifact_ids=[
                        self._relative_artifact_path(
                            self._baseline_directory(spec.experiment_id)
                            / "SIMULATION_RESULT.json"
                        )
                    ],
                )
            else:
                objective_artifact_id = None
            rows.append(
                {
                    "candidate_id": f"{spec.experiment_id}__baseline",
                    "source": "initial_baseline",
                    "optimizer_id": None,
                    "thicknesses_nm": [
                        float(layer.thickness_nm)
                        for layer in optimization.simulation.stack.layers
                    ],
                    "objective_loss": math.inf
                    if baseline_objective is None
                    else baseline_objective.weighted_directional_loss,
                    "node_id": baselines[spec.experiment_id]["node_id"],
                    "certificate": baseline_certified.certificate,
                    "physics_status": _physics_status(
                        baseline_certified.certificate
                    ),
                    "objective_report": None
                    if baseline_objective is None
                    else baseline_objective.model_dump(mode="json"),
                    "robustness_report": None,
                    "artifact_ids": [
                        self._relative_artifact_path(
                            self._baseline_directory(spec.experiment_id)
                            / "PHYSICS_ACCEPTANCE_CERTIFICATE.json"
                        ),
                        *([objective_artifact_id] if objective_artifact_id else []),
                    ],
                }
            )
            for proposal in proposals.get(spec.experiment_id, []):
                candidate_id = str(proposal["candidate_id"])
                directory = self._candidate_directory(
                    spec.experiment_id,
                    candidate_id,
                )
                simulation = simulation_with_thicknesses(optimization.simulation, proposal["thicknesses_nm"])
                node = self._graph_node(spec.experiment_id, ActionType.run_reference_solver, {"candidate_id": candidate_id}, [baselines[spec.experiment_id]["node_id"]])
                self.graph.set_status(node, ExperimentStatus.running)
                verification_inputs = [
                    self._relative_artifact_path(
                        self._experiment_directory(spec.experiment_id)
                        / "MATERIAL_MANIFEST.json"
                    )
                ]
                if proposal.get("optimizer_artifact_id"):
                    verification_inputs.append(str(proposal["optimizer_artifact_id"]))
                identity_path = directory / "IDENTITY.json"
                atomic_write_json(
                    identity_path,
                    {
                        "schema_version": "tmm-artifact-identity.v1",
                        "experiment_id": spec.experiment_id,
                        "candidate_id": candidate_id,
                        "physical_directory": self._relative_artifact_path(
                            directory
                        ),
                    },
                )
                identity_artifact_id = self._register_artifact(
                    identity_path,
                    artifact_type="candidate_identity",
                    producing_action="map_logical_candidate_to_safe_path",
                    input_artifact_ids=verification_inputs,
                )
                verification_inputs.append(identity_artifact_id)
                outcome = self._reserve_and_certify(
                    f"verify:{candidate_id}",
                    simulation,
                    directory,
                    task.verification,
                    input_artifact_ids=verification_inputs,
                    producing_node=node,
                )
                certified = outcome["certified"]
                self._certified_results[(spec.experiment_id, candidate_id)] = (
                    certified.result
                )
                status = _physics_status(certified.certificate)
                if status == "rejected_physics":
                    for failure in certified.certificate.get("failures", []):
                        self._record_failure_dict(
                            failure,
                            experiment_id=spec.experiment_id,
                            stage="candidate_verification",
                            action_id=f"verify:{candidate_id}",
                        )
                self.graph.set_status(node, ExperimentStatus(status), certificate_id=certified.certificate.get("certificate_id"))
                objective = None
                if certified.result is not None:
                    objective = evaluate_optimization_objectives(optimization, certified.result)
                    objective = _with_declared_objective_rows(
                        objective, spec.objectives, certified.result
                    )
                    atomic_write_json(directory / "OBJECTIVE_REPORT.json", objective.model_dump(mode="json"))
                    objective_artifact_id = self._register_artifact(
                        directory / "OBJECTIVE_REPORT.json",
                        artifact_type="objective_report",
                        producing_action=(
                            "score_soft_objectives_with_declared_objectives"
                            if spec.objectives
                            else "score_soft_objectives"
                        ),
                        producing_node=node,
                        input_artifact_ids=[str(outcome["result_artifact_id"])],
                    )
                else:
                    objective_artifact_id = None
                rows.append({
                    **proposal,
                    "node_id": node,
                    "certificate": certified.certificate,
                    "physics_status": status,
                    "objective_report": None if objective is None else objective.model_dump(mode="json"),
                    "robustness_report": None,
                    "artifact_ids": [
                        str(outcome["certificate_artifact_id"]),
                        *([objective_artifact_id] if objective_artifact_id else []),
                    ],
                })
            output[spec.experiment_id] = rows
        return output

    @staticmethod
    def _maximum_spectral_delta(primary: Any, alternate: Any) -> float | None:
        if primary is None or alternate is None:
            return None
        deltas: list[float] = []
        for channel_key, primary_channel in primary.channels.items():
            alternate_channel = alternate.channels.get(channel_key)
            if alternate_channel is None:
                continue
            for observable in ("R", "T", "A"):
                if observable not in primary_channel or observable not in alternate_channel:
                    continue
                left = np.asarray(primary_channel[observable], dtype=np.float64)
                right = np.asarray(alternate_channel[observable], dtype=np.float64)
                if left.shape == right.shape and left.size:
                    deltas.append(float(np.max(np.abs(left - right))))
        return max(deltas) if deltas else None

    def _run_material_dataset_uncertainty(
        self,
        task: OpticalDesignTask,
        parsed: Iterable[tuple[Any, EngineMode, Any]],
        verified: Dict[str, List[Dict[str, Any]]],
    ) -> None:
        """Evaluate retained material datasets as soft uncertainty scenarios.

        A scenario may alter ranking but never physics admission of the primary
        candidate.  Missing budget produces an explicit partial report rather
        than silently collapsing the scenario set.
        """

        if task.uncertainty.material_dataset_policy != "evaluate_all_eligible":
            return
        assert self._budget is not None
        parsed_map = {
            spec.experiment_id: (mode, parsed_task)
            for spec, mode, parsed_task in parsed
        }
        for experiment_id, rows in verified.items():
            scenarios = self._material_scenarios.get(experiment_id, ())
            if len(scenarios) <= 1:
                continue
            mode, primary_task = parsed_map[experiment_id]
            for row in rows:
                if row["physics_status"] not in {
                    "physically_valid",
                    "physically_valid_with_limits",
                }:
                    continue
                candidate_id = str(row["candidate_id"])
                primary_result = self._certified_results.get(
                    (experiment_id, candidate_id)
                )
                primary_score = None
                if mode == EngineMode.optimize and row.get("objective_report"):
                    primary_score = float(
                        row["objective_report"]["aggregate_soft_score"]
                    )
                scenario_rows: list[Dict[str, Any]] = [
                    {
                        "scenario_id": scenarios[0].scenario_id,
                        "is_primary": True,
                        "physics_status": row["physics_status"],
                        "soft_score": primary_score,
                        "max_spectral_delta_vs_primary": 0.0,
                    }
                ]
                for scenario in scenarios[1:]:
                    action_id = (
                        f"material_uncertainty:{experiment_id}:{candidate_id}:"
                        f"{scenario.scenario_id}"
                    )
                    if not self._budget.can_reserve(
                        action_id, forward_evaluations=5
                    ):
                        scenario_rows.append(
                            {
                                "scenario_id": scenario.scenario_id,
                                "is_primary": False,
                                "physics_status": "not_run_budget_exhausted",
                                "assignments": [
                                    item.model_dump(mode="json")
                                    for item in scenario.assignments
                                ],
                            }
                        )
                        continue
                    scenario_task = scenario.task
                    scenario_simulation = (
                        scenario_task
                        if mode == EngineMode.simulate
                        else simulation_with_thicknesses(
                            scenario_task.simulation,
                            row["thicknesses_nm"],
                        )
                    )
                    directory = self._candidate_material_scenario_directory(
                        experiment_id,
                        candidate_id,
                        scenario.scenario_id,
                    )
                    outcome = self._reserve_and_certify(
                        action_id,
                        scenario_simulation,
                        directory,
                        task.verification,
                        input_artifact_ids=[
                            self._relative_artifact_path(
                                self._material_scenario_directory(
                                    experiment_id,
                                    scenario.scenario_id,
                                )
                                / "MATERIAL_MANIFEST.json"
                            )
                        ],
                    )
                    certified = outcome["certified"]
                    status = _physics_status(certified.certificate)
                    alternate_score = None
                    if (
                        mode == EngineMode.optimize
                        and certified.result is not None
                    ):
                        alternate_score = evaluate_optimization_objectives(
                            scenario_task,
                            certified.result,
                        ).aggregate_soft_score
                    scenario_rows.append(
                        {
                            "scenario_id": scenario.scenario_id,
                            "is_primary": False,
                            "assignments": [
                                item.model_dump(mode="json")
                                for item in scenario.assignments
                            ],
                            "physics_status": status,
                            "soft_score": alternate_score,
                            "max_spectral_delta_vs_primary": (
                                self._maximum_spectral_delta(
                                    primary_result, certified.result
                                )
                            ),
                            "certificate_artifact_id": outcome[
                                "certificate_artifact_id"
                            ],
                        }
                    )
                scores = [
                    float(item["soft_score"])
                    for item in scenario_rows
                    if item.get("soft_score") is not None
                ]
                deltas = [
                    float(item["max_spectral_delta_vs_primary"])
                    for item in scenario_rows
                    if item.get("max_spectral_delta_vs_primary") is not None
                ]
                completed = sum(
                    item.get("physics_status")
                    in {"physically_valid", "physically_valid_with_limits"}
                    for item in scenario_rows
                )
                report = {
                    "schema_version": "tmm-material-dataset-uncertainty.v1",
                    "candidate_id": candidate_id,
                    "policy": "evaluate_all_eligible",
                    "scenario_count": len(scenario_rows),
                    "completed_scenarios": completed,
                    "status": (
                        "complete"
                        if completed == len(scenario_rows)
                        else "partial"
                    ),
                    "scenarios": scenario_rows,
                    "mean_soft_score": (
                        float(np.mean(scores)) if scores else None
                    ),
                    "worst_soft_score": min(scores) if scores else None,
                    "maximum_spectral_delta": max(deltas) if deltas else None,
                    "admission_role": "ranking_only",
                }
                report_path = self._candidate_directory(
                    experiment_id,
                    candidate_id,
                ) / "MATERIAL_DATASET_UNCERTAINTY.json"
                atomic_write_json(report_path, report)
                artifact_id = self._register_artifact(
                    report_path,
                    artifact_type="material_dataset_uncertainty_report",
                    producing_action="evaluate_material_dataset_uncertainty",
                    input_artifact_ids=list(
                        dict.fromkeys(
                            [
                                artifact_id
                                for item in scenario_rows
                                for artifact_id in [
                                    item.get("certificate_artifact_id")
                                ]
                                if artifact_id
                            ]
                            + list(row.get("artifact_ids") or [])
                        )
                    ),
                )
                row["material_dataset_uncertainty_report"] = report
                row.setdefault("artifact_ids", []).append(artifact_id)

    def _run_robustness(self, task: OpticalDesignTask, parsed: Iterable[tuple[Any, EngineMode, Any]], verified: Dict[str, List[Dict[str, Any]]]) -> None:
        assert self._budget is not None
        parsed_map = {spec.experiment_id: parsed_task for spec, mode, parsed_task in parsed if mode == EngineMode.optimize}
        for experiment_id, rows in verified.items():
            optimization = parsed_map.get(experiment_id)
            if optimization is None:
                continue
            accepted = [row for row in rows if row["physics_status"] in {"physically_valid", "physically_valid_with_limits"} and row.get("objective_report")]
            accepted.sort(key=lambda row: -float(row["objective_report"]["aggregate_soft_score"]))
            for row in accepted[: self.config.robustness_candidate_limit]:
                candidate_id = str(row["candidate_id"])
                count = int(task.uncertainty.thickness_samples) + 1
                action_id = f"robustness:{candidate_id}"
                if not self._budget.can_reserve(action_id, forward_evaluations=count):
                    row["robustness_report"] = {"status": "not_run_budget_exhausted"}
                    continue
                self._budget.reserve(action_id, forward_evaluations=count)
                directory = self._candidate_directory(
                    experiment_id,
                    candidate_id,
                )
                report = TMMRobustnessEvaluator(self.solver_adapter.workbench).evaluate(
                    optimization,
                    row["thicknesses_nm"],
                    candidate_id=candidate_id,
                    sigma_nm=task.uncertainty.thickness_sigma_nm,
                    thickness_error_model=task.uncertainty.thickness_error_model,
                    relative_fraction=task.uncertainty.thickness_relative_fraction,
                    samples=task.uncertainty.thickness_samples,
                    random_seed=task.uncertainty.random_seed,
                    angle_perturbation_deg=task.uncertainty.angle_perturbation_deg,
                    work_dir=directory,
                )
                self._budget.commit(action_id, forward_evaluations=count)
                row["robustness_report"] = report.model_dump(mode="json")
                robustness_artifact_id = self._register_artifact(
                    directory / "ROBUSTNESS.json",
                    artifact_type="robustness_report",
                    producing_action="evaluate_manufacturing_uncertainty",
                    input_artifact_ids=list(row.get("artifact_ids") or []),
                )
                row.setdefault("artifact_ids", []).append(robustness_artifact_id)

    def _build_portfolios(
        self,
        task: OpticalDesignTask,
        parsed: Iterable[tuple[Any, EngineMode, Any]],
        baselines: Dict[str, Dict[str, Any]],
        verified: Dict[str, List[Dict[str, Any]]],
    ) -> list[Dict[str, Any]]:
        results = []
        for spec, mode, parsed_task in parsed:
            rows = verified.get(spec.experiment_id, [])
            accepted_rows = [row for row in rows if row["physics_status"] in {"physically_valid", "physically_valid_with_limits"}]
            if accepted_rows:
                best = max(
                    accepted_rows,
                    key=lambda row: float((row.get("objective_report") or {}).get("aggregate_soft_score", 0.0)),
                )
                best_values = np.asarray(best["thicknesses_nm"], dtype=np.float64)
                spans = np.asarray([
                    max(layer.bounds_nm(parsed_task.optimizer.thickness_window_nm)[1] - layer.bounds_nm(parsed_task.optimizer.thickness_window_nm)[0], 1.0)
                    if mode == EngineMode.optimize else max(float(layer.thickness_nm), 1.0)
                    for layer in (parsed_task.simulation.stack.layers if mode == EngineMode.optimize else parsed_task.stack.layers)
                ])
            else:
                best_values = np.zeros(0)
                spans = np.ones(0)
            design_candidates: list[DesignCandidate] = []
            for row in rows:
                values = np.asarray(row["thicknesses_nm"], dtype=np.float64)
                distinctive = 0.0 if not best_values.size else float(min(1.0, np.linalg.norm((values - best_values) / spans) / max(math.sqrt(values.size), 1.0)))
                objective = row.get("objective_report") or {}
                robustness = row.get("robustness_report") or {}
                material_uncertainty = (
                    row.get("material_dataset_uncertainty_report") or {}
                )
                robustness_components = [
                    float(value)
                    for value in (
                        robustness.get("robustness_score"),
                        material_uncertainty.get("worst_soft_score"),
                    )
                    if value is not None
                ]
                design_candidates.append(DesignCandidate(
                    candidate_id=str(row["candidate_id"]),
                    physics_status=str(row["physics_status"]),
                    target_attainment=dict(objective.get("target_attainment") or {}),
                    robustness_score=(
                        min(robustness_components)
                        if robustness_components
                        else None
                    ),
                    simplicity_score=_simplicity_score(values, str(row.get("source") or "")),
                    distinctiveness_score=distinctive,
                    certificate_id=row["certificate"].get("certificate_id"),
                    artifact_ids=list(row.get("artifact_ids") or []),
                    metadata={
                        "source": row.get("source"),
                        "optimizer_id": row.get("optimizer_id"),
                        "thicknesses_nm": row["thicknesses_nm"],
                        "objective_report": objective,
                        "robustness_report": robustness,
                        "material_dataset_uncertainty_report": material_uncertainty,
                    },
                ))
            portfolio = PortfolioSelector().select(
                design_candidates,
                max_pareto_candidates=task.portfolio.maximum_candidates,
                maximum_candidates=task.portfolio.maximum_candidates,
                include_best_target_score=task.portfolio.include_best_target_score,
                include_most_robust=task.portfolio.include_most_robust,
                include_simplest_fabrication=task.portfolio.include_simplest_fabrication,
                include_structurally_distinctive=task.portfolio.include_structurally_distinctive,
                include_pareto_front=task.portfolio.include_pareto_front,
            )
            directory = self._experiment_directory(spec.experiment_id)
            atomic_write_json(directory / "DESIGN_PORTFOLIO.json", portfolio.model_dump(mode="json"))
            portfolio_artifact_id = self._register_artifact(
                directory / "DESIGN_PORTFOLIO.json",
                artifact_type="design_portfolio",
                producing_action="rank_verified_candidates",
                input_artifact_ids=list(
                    dict.fromkeys(
                        artifact_id
                        for row in rows
                        for artifact_id in row.get("artifact_ids", [])
                    )
                ),
                scientific_provenance={
                    "selection_policy": "soft_objective_multi_candidate",
                    "performance_target_used_as_gate": False,
                },
            )
            results.append({
                "experiment_id": spec.experiment_id,
                "mode": mode.value,
                "physically_valid_candidate_count": len(accepted_rows),
                "candidate_count": len(rows),
                "baseline_status": _physics_status(baselines[spec.experiment_id]["certified"].certificate),
                "portfolio": portfolio.model_dump(mode="json"),
                "portfolio_artifact_id": portfolio_artifact_id,
            })
        atomic_write_json(self.work_dir / "DESIGN_PORTFOLIOS.json", {"experiments": results})
        self._register_artifact(
            self.work_dir / "DESIGN_PORTFOLIOS.json",
            artifact_type="design_portfolio_collection",
            producing_action="assemble_design_portfolios",
            input_artifact_ids=[item["portfolio_artifact_id"] for item in results],
        )
        self.graph_path_export()
        return results

    def graph_path_export(self) -> None:
        atomic_write_json(self.work_dir / "EXPERIMENT_GRAPH.json", self.graph.export())

    def _final_stop_decision(self, experiment_results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        assert self._budget is not None
        candidate_count = sum(int(item.get("physically_valid_candidate_count", 0)) for item in experiment_results)
        scores = [
            float(candidate.get("target_score") or 0.0)
            for item in experiment_results
            for candidate in item.get("portfolio", {}).get("candidates", [])
            if candidate.get("physically_admissible")
        ]
        pareto = sum(len(item.get("portfolio", {}).get("pareto_candidate_ids", [])) for item in experiment_results)
        roles = sum(len(item.get("portfolio", {}).get("selected_roles", {})) for item in experiment_results)
        controller = TMMStopController()
        controller.observe(FrontierObservation(round_index=1, physically_valid_candidates=candidate_count, best_target_score=max(scores, default=0.0), pareto_candidate_count=pareto, portfolio_role_count=roles))
        return controller.decide(budget_snapshot=self._budget.snapshot(), legal_actions=[], portfolio_written=bool(experiment_results)).model_dump(mode="json")

    def _finish(
        self,
        task: OpticalDesignTask,
        status: str,
        experiment_results: Sequence[Dict[str, Any]],
        started: float,
        stop_decision: Dict[str, Any],
    ) -> TMMHarnessRunResult:
        assert self._budget is not None
        budget_snapshot = self._budget.snapshot()
        cost_path = self.work_dir / "COST.json"
        atomic_write_json(
            cost_path,
            {
                "schema_version": "tmm-harness-cost.v1",
                "run_id": self.run_id,
                "wall_seconds": float(time.perf_counter() - started),
                "forward_evaluations": int(
                    budget_snapshot.get("usage", {}).get(
                        "forward_evaluations", 0
                    )
                    or 0
                ),
                "optimizer_runs": int(
                    budget_snapshot.get("usage", {}).get("optimizer_runs", 0)
                    or 0
                ),
                "qwen_calls": int(
                    budget_snapshot.get("usage", {}).get("qwen_calls", 0) or 0
                ),
                "qwen_input_tokens": int(
                    budget_snapshot.get("usage", {}).get(
                        "qwen_input_tokens", 0
                    )
                    or 0
                ),
                "qwen_output_tokens": int(
                    budget_snapshot.get("usage", {}).get(
                        "qwen_output_tokens", 0
                    )
                    or 0
                ),
                "qwen_cost_cny": float(
                    budget_snapshot.get("usage", {}).get("qwen_cost_cny", 0.0)
                    or 0.0
                ),
                "qwen_model_constraint": QWEN_POLICY_MODEL,
                "qwen_usage_records": self._qwen_usage,
                "budget_status": budget_snapshot.get("status"),
                "budget_overruns": budget_snapshot.get("overruns", {}),
            },
        )
        # Freeze mutable run ledgers only after the state machine has reached a
        # terminal stage.  Registering them earlier would make legitimate
        # progress look like provenance tampering.
        self.graph_path_export()
        terminal_paths = (
            (self.work_dir / "EXPERIMENT_GRAPH.json", "experiment_graph"),
            (self.work_dir / "RUN_STATE.json", "run_state"),
            (self.work_dir / "STATE_HISTORY.json", "state_history"),
            (self.work_dir / "BUDGET.json", "budget_ledger"),
            (self._events_path, "event_log"),
            (cost_path, "cost_ledger"),
        )
        if self._diagnostic_records:
            diagnostics_path = self.work_dir / "FAILURE_DIAGNOSES.json"
            atomic_write_json(
                diagnostics_path,
                {
                    "schema_version": "tmm-failure-diagnoses.v1",
                    "records": self._diagnostic_records,
                },
            )
            self._register_artifact(
                diagnostics_path,
                artifact_type="failure_diagnoses",
                producing_action="diagnose_tmm_failures",
                input_artifact_ids=["TASK.json"],
            )
        terminal_ids: list[str] = []
        for path, artifact_type in terminal_paths:
            if path.exists():
                terminal_ids.append(
                    self._register_artifact(
                        path,
                        artifact_type=artifact_type,
                        producing_action="finalize_tmm_run",
                        input_artifact_ids=["TASK.json"],
                    )
                )
        result = TMMHarnessRunResult(
            run_id=self.run_id,
            task_id=task.task_id,
            status=status,
            state_stage=self.state.stage.value,
            experiment_results=tuple(experiment_results),
            budget=budget_snapshot,
            qwen_usage=tuple(self._qwen_usage),
            stop_decision=stop_decision,
            diagnoses=_summarize_diagnoses(self._diagnostic_records),
            wall_seconds=float(time.perf_counter() - started),
        )
        atomic_write_json(self.work_dir / "FINAL_RESULT.json", result.model_dump(mode="json"))
        all_inputs = list(self.provenance.artifact_ids)
        final_artifact_id = self._register_artifact(
            self.work_dir / "FINAL_RESULT.json",
            artifact_type="final_result",
            producing_action="complete_tmm_harness_run",
            input_artifact_ids=all_inputs,
            scientific_provenance={
                "status": status,
                "performance_targets_used_as_gates": False,
                "physics_verification_required": True,
            },
        )
        if final_artifact_id not in self.provenance.artifact_ids:
            raise RuntimeError("final artifact was not registered")
        self.provenance.verify_all()
        return result


__all__ = ["TMMHarnessConfig", "TMMHarnessOrchestrator", "TMMHarnessRunResult"]
