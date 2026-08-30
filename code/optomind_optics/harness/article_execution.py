"""Stage 6: connect attested requests to the deterministic TMM harness.

This module adds the trusted local task resolver, the
``ArticleTMMExecutionAdapter`` (Stage 5 ``DeterministicExecutorAdapter``
implementation), the budget binding, and truthful ObservationCard
normalization.  It never builds or modifies a solver: the existing
``TMMHarnessOrchestrator`` (or an injected fake with the same ``run(task)``
contract) is the only executor and physics-certificate authority.

Boundaries:
- Qwen never supplies or executes an ``OpticalDesignTask``.  The adapter
  receives a trusted ``ResolvedTask`` from a local resolver keyed by the
  request's task hash, validates it with the existing ``OpticalDesignTask``
  validators, and rejects missing, mismatched, or invalid tasks before calling
  the harness.
- Execution-bound requests carry the canonical ``OpticalDesignTask`` content
  digest (``CompiledExperimentRequest.task_digest``) covered by ``task_hash``,
  ``request_id``, and the compiler HMAC attestation.  The registry preserves
  the digest, and the adapter recomputes it from the resolved task and
  compares request, registry, and content before any run directory is created
  or budget reserved; compile-only requests without a task digest fail closed
  at execution.
- The whole-task required action is derived deterministically: simulate-only
  tasks require ``run_solver`` and any optimize experiment requires
  ``run_optimizer``.  Specialized follow-up actions (``run_reference_solver``,
  ``run_convergence_audit``, ``run_robustness_audit``) and
  ``generate_baseline`` are rejected because this adapter executes the
  complete task.  The request reservation must cover the task's operational
  ceilings (wall time, forward evaluations, optimizer runs); Qwen is disabled
  inside the adapter, so qwen budget use is not demanded.
- ``ArticleBudgetAdapter``/``BudgetScheduler`` is the only budget authority:
  reservation happens before any run under ``request.budget_lease_id`` (or
  ``request.request_id``); resolver failure, invalid task, harness exception,
  and rejected runs release; completed runs commit measured usage extracted
  from the trusted TMM result.  Missing or malformed usage is a hard adapter
  failure.  Count resources must be integers before reservation.
- ``ObservationCard`` status/metrics are derived only from
  ``TMMHarnessRunResult``/``FINAL_RESULT.json``.  The adapter may reference
  certificate/artifact files but never creates, edits, or claims a physics
  certificate.
- Runs live in isolated directories under a caller-supplied work root, keyed
  deterministically by (branch, task hash).  An existing completed run is
  replayed idempotently only when the recorded identity matches; a different
  hash is never overwritten.
- The Stage 5 gateway still returns only its narrow adapter receipt; the
  ``ArticleExecutionCoordinator`` combines that receipt with the locally
  stored run result into ``ArticleExecutionResult`` containing the
  ObservationCard.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from optomind_optics.harness.article_contracts import ObservationCard
from optomind_optics.harness.article_gateway import (
    ArticleToolGateway,
    DeterministicExecutorAdapter,
    GatewayAdapterResult,
    GatewayRejection,
)
from optomind_optics.harness.article_proposals import (
    CompiledExperimentRequest,
    compute_optical_design_task_digest,
)
from optomind_optics.harness.article_runtime import ArticleBudgetAdapter
from optomind_optics.harness.contracts import ActionType, ExperimentStatus
from optomind_optics.harness.design_task import EngineMode, OpticalDesignTask
from optomind_optics.harness.orchestrator import (
    TMMHarnessConfig,
    TMMHarnessOrchestrator,
    TMMHarnessRunResult,
)
from optomind_research.runtime.artifact_store import atomic_write_json


EXECUTION_SCHEMA_VERSION = "article-execution-result.v1"
RESOLVED_TASK_SCHEMA_VERSION = "resolved-task.v1"
EXECUTION_MARKER_FILENAME = "EXECUTION_MARKER.json"

_COUNT_RESOURCES = frozenset(
    {
        "forward_evaluations",
        "optimizer_runs",
        "qwen_calls",
        "qwen_input_tokens",
        "qwen_output_tokens",
    }
)
_MAX_ARTIFACT_REFS = 32


class ArticleExecutionError(ValueError):
    """Base error for trusted execution failures."""


class ResolverFailure(ArticleExecutionError):
    pass


class TaskIdentityMismatch(ArticleExecutionError):
    pass


class ActionAuthorizationError(ArticleExecutionError):
    pass


class BudgetCeilingError(ArticleExecutionError):
    pass


class InvalidResolvedTask(ArticleExecutionError):
    pass


class BudgetExecutionError(ArticleExecutionError):
    pass


class BudgetReservationError(BudgetExecutionError):
    pass


class UsageMalformedError(BudgetExecutionError):
    pass


class HarnessExecutionError(ArticleExecutionError):
    pass


class RunCollisionError(ArticleExecutionError):
    pass


def _validate_path_component(value: str, field: str) -> str:
    """Validate a value used as a path component inside the work root."""

    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ArticleExecutionError(
            f"{field} must be a non-empty string without surrounding whitespace"
        )
    if value in {".", ".."} or "\x00" in value:
        raise ArticleExecutionError(
            f"{field} must not be a dot/dot-dot or contain NUL"
        )
    if any(character in value for character in ("/", "\\", ":")):
        raise ArticleExecutionError(
            f"{field} must not contain path separators or drive markers"
        )
    return value


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ResolvedTask(_StrictModel):
    schema_version: Literal["resolved-task.v1"] = "resolved-task.v1"
    task_hash: str
    task_digest: str = ""
    task: OpticalDesignTask

    @field_validator("task_hash")
    @classmethod
    def _non_empty_hash(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("task_hash must be a non-empty string")
        return text

    @field_validator("task_digest")
    @classmethod
    def _hex_digest(cls, value: str) -> str:
        text = str(value or "").strip()
        if text and (
            len(text) != 64
            or any(char not in "0123456789abcdef" for char in text)
        ):
            raise ValueError(
                "task_digest must be empty or a 64-character lowercase hex digest"
            )
        return text


class ArticleExecutionResult(_StrictModel):
    schema_version: Literal["article-execution-result.v1"] = (
        "article-execution-result.v1"
    )
    request_id: str
    task_hash: str
    run_dir: str
    observation: ObservationCard
    receipt: Dict[str, Any]
    outcome: str


class TaskResolver(Protocol):
    def resolve(
        self, request: CompiledExperimentRequest
    ) -> Optional[ResolvedTask]: ...


class LocalTaskRegistry:
    """Trusted local registry keyed by the attested request task hash."""

    def __init__(self) -> None:
        self._bindings: Dict[str, ResolvedTask] = {}

    def register(
        self, task_hash: str, task: OpticalDesignTask | Mapping[str, Any]
    ) -> ResolvedTask:
        if not isinstance(task_hash, str) or not task_hash.strip():
            raise ResolverFailure("task_hash must be a non-empty string")
        try:
            task_model = (
                task
                if isinstance(task, OpticalDesignTask)
                else OpticalDesignTask.model_validate(task)
            )
        except ValidationError as exc:
            raise InvalidResolvedTask(f"task is invalid: {exc}") from exc
        task_digest = compute_optical_design_task_digest(task_model)
        binding = ResolvedTask(
            task_hash=task_hash.strip(),
            task_digest=task_digest,
            task=task_model,
        )
        existing = self._bindings.get(binding.task_hash)
        if existing is not None:
            if existing.task != binding.task:
                raise TaskIdentityMismatch(
                    f"task_hash {binding.task_hash!r} is already registered "
                    "with a different task"
                )
            if existing.task_digest != binding.task_digest:
                raise TaskIdentityMismatch(
                    f"task_hash {binding.task_hash!r} is already registered "
                    "with a different canonical task digest"
                )
            return existing
        self._bindings[binding.task_hash] = binding
        return binding

    def resolve(self, request: CompiledExperimentRequest) -> Optional[ResolvedTask]:
        if not isinstance(request, CompiledExperimentRequest):
            raise TypeError(
                "resolve requires a CompiledExperimentRequest; raw model "
                "envelopes can never provide a task"
            )
        return self._bindings.get(request.task_hash)


class ArticleTMMExecutionAdapter:
    """Stage 5 deterministic adapter that runs the trusted TMM harness."""

    ADAPTER_NAME = "article_tmm_execution"

    def __init__(
        self,
        *,
        resolver: TaskResolver,
        budget_adapter: ArticleBudgetAdapter,
        work_root: str | Path,
        branch_id: str,
        run_id: str,
        harness_factory: Optional[
            Callable[[Path, str], Any]
        ] = None,
    ) -> None:
        self.resolver = resolver
        self.budget_adapter = budget_adapter
        self.work_root = Path(work_root).resolve()
        self.branch_id = _validate_path_component(branch_id, "branch_id")
        self.run_id = _validate_path_component(run_id, "run_id")
        self.harness_factory = harness_factory or (
            lambda run_dir, run_identity: TMMHarnessOrchestrator(
                run_dir,
                run_id=run_identity,
                config=TMMHarnessConfig(use_qwen_policy=False),
            )
        )

    # -- deterministic run directory ----------------------------------------

    def run_dir_for(self, request: CompiledExperimentRequest) -> Path:
        run_dir = (
            self.work_root
            / self.branch_id
            / f"run-{request.task_hash[:32]}"
        ).resolve()
        if not run_dir.is_relative_to(self.work_root):
            raise ArticleExecutionError(
                "run directory resolves outside the work root"
            )
        return run_dir

    # -- Stage 5 adapter contract -------------------------------------------

    def execute(self, request: CompiledExperimentRequest) -> Mapping[str, Any]:
        try:
            return self._execute(request)
        except ArticleExecutionError as exc:
            return {
                "adapter_name": self.ADAPTER_NAME,
                "status": "adapter_rejected",
                "summary": "trusted execution failed before a TMM result",
                "reason": f"{type(exc).__name__}: {exc}",
                "output_refs": [],
                "telemetry": {
                    "run_dir": self._relative_run_dir(request),
                    "task_hash": request.task_hash,
                    "run_id": self.run_id,
                },
            }
        except Exception as exc:  # pragma: no cover - defensive fail-closed
            return {
                "adapter_name": self.ADAPTER_NAME,
                "status": "adapter_rejected",
                "summary": "unexpected trusted execution failure",
                "reason": f"{type(exc).__name__}: {exc}",
                "output_refs": [],
                "telemetry": {
                    "run_dir": self._relative_run_dir(request),
                    "task_hash": request.task_hash,
                    "run_id": self.run_id,
                },
            }

    def _execute(self, request: CompiledExperimentRequest) -> Mapping[str, Any]:
        _validate_count_resources(request.requested_budget)
        binding = self.resolver.resolve(request)
        if binding is None:
            raise ResolverFailure("no trusted task was resolved for this request")
        if binding.task_hash != request.task_hash:
            raise TaskIdentityMismatch(
                f"resolved task hash {binding.task_hash!r} does not match "
                f"request {request.task_hash!r}"
            )
        try:
            task = OpticalDesignTask.model_validate(
                binding.task.model_dump(mode="json")
            )
        except ValidationError as exc:
            raise InvalidResolvedTask(f"resolved task is invalid: {exc}") from exc
        self._validate_task_binding(request, binding, task)

        run_dir = self.run_dir_for(request)
        replay = self._existing_completed_run(request, run_dir)
        if replay is not None:
            return self._receipt(request, run_dir, replay, replayed=True)

        lease = request.budget_lease_id or request.request_id
        try:
            self.budget_adapter.reserve(lease, **dict(request.requested_budget))
        except Exception as exc:
            raise BudgetReservationError(
                f"budget reservation failed for lease {lease!r}: {exc}"
            ) from exc

        run_dir.mkdir(parents=True, exist_ok=True)
        marker = run_dir / EXECUTION_MARKER_FILENAME
        atomic_write_json(
            marker,
            {
                "task_hash": request.task_hash,
                "request_id": request.request_id,
                "run_id": self.run_id,
                "status": "running",
            },
        )
        try:
            harness = self.harness_factory(run_dir, self.run_id)
            raw_result = harness.run(task)
        except Exception as exc:
            self._release(lease)
            raise HarnessExecutionError(
                f"TMM harness failed: {exc}"
            ) from exc

        try:
            result = (
                raw_result
                if isinstance(raw_result, TMMHarnessRunResult)
                else TMMHarnessRunResult.model_validate(raw_result)
            )
        except ValidationError as exc:
            self._release(lease)
            raise HarnessExecutionError(
                f"TMM harness returned an invalid result: {exc}"
            ) from exc

        if result.run_id != self.run_id:
            self._release(lease)
            raise HarnessExecutionError(
                f"TMM result run_id {result.run_id!r} does not match "
                f"adapter run_id {self.run_id!r}"
            )

        if result.status != "completed":
            self._release(lease)
            atomic_write_json(
                marker,
                {
                    "task_hash": request.task_hash,
                    "request_id": request.request_id,
                    "run_id": self.run_id,
                    "status": result.status,
                },
            )
            return self._receipt(request, run_dir, result, replayed=False)

        try:
            usage = _extract_measured_usage(result)
            self.budget_adapter.commit(lease, **dict(usage))
        except UsageMalformedError:
            self._release(lease)
            raise
        except Exception as exc:
            self._release(lease)
            raise UsageMalformedError(
                f"measured usage could not be committed: {exc}"
            ) from exc
        atomic_write_json(
            marker,
            {
                "task_hash": request.task_hash,
                "request_id": request.request_id,
                "run_id": self.run_id,
                "status": "completed",
            },
        )
        return self._receipt(request, run_dir, result, replayed=False)

    # -- helpers ------------------------------------------------------------

    def _validate_task_binding(
        self,
        request: CompiledExperimentRequest,
        binding: ResolvedTask,
        task: OpticalDesignTask,
    ) -> None:
        """Fail closed before any run directory, reservation, or harness call.

        The request must carry a canonical task digest bound at compile time;
        the registry must preserve the same digest; and the resolved task must
        reproduce it exactly.  The whole-task required action must match the
        request, specialized follow-up actions are rejected, and the requested
        budget must cover the task's operational ceilings.
        """

        if not request.task_digest:
            raise TaskIdentityMismatch(
                "compiled request does not bind a canonical task digest; "
                "execution-bound requests are required"
            )
        recomputed = compute_optical_design_task_digest(task)
        if recomputed != request.task_digest:
            raise TaskIdentityMismatch(
                "resolved task content does not match the request task digest"
            )
        if binding.task_digest and recomputed != binding.task_digest:
            raise TaskIdentityMismatch(
                "resolved task content does not match the registry task digest"
            )
        if request.allowed_action in {
            ActionType.run_reference_solver,
            ActionType.run_convergence_audit,
            ActionType.run_robustness_audit,
        }:
            raise ActionAuthorizationError(
                f"specialized follow-up action {request.allowed_action.value!r} "
                "is not supported by the full-task adapter, which executes the "
                "complete OpticalDesignTask"
            )
        if request.allowed_action == ActionType.generate_baseline:
            raise ActionAuthorizationError(
                "generate_baseline is not a full-task action; the adapter "
                "executes the complete task including baseline generation"
            )
        required = required_action_for_task(task)
        if request.allowed_action != required:
            raise ActionAuthorizationError(
                f"task requires action {required.value!r}, but the request "
                f"allows {request.allowed_action.value!r}"
            )
        _check_operational_ceilings(task, request.requested_budget)

    def _relative_run_dir(self, request: CompiledExperimentRequest) -> str:
        return self.run_dir_for(request).relative_to(self.work_root).as_posix()

    def _existing_completed_run(
        self, request: CompiledExperimentRequest, run_dir: Path
    ) -> Optional[TMMHarnessRunResult]:
        marker = run_dir / EXECUTION_MARKER_FILENAME
        final = run_dir / "FINAL_RESULT.json"
        if not run_dir.exists():
            return None
        if marker.exists():
            try:
                payload = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise RunCollisionError(
                    f"execution marker is malformed: {exc}"
                ) from exc
            if not isinstance(payload, Mapping):
                raise RunCollisionError(
                    "execution marker is malformed: not an object"
                )
            recorded_hash = str(payload.get("task_hash") or "")
            recorded_request = str(payload.get("request_id") or "")
            recorded_run = str(payload.get("run_id") or "")
            recorded_status = str(payload.get("status") or "")
            if (
                recorded_hash == request.task_hash
                and recorded_request == request.request_id
                and recorded_run == self.run_id
                and recorded_status == "completed"
            ):
                if final.exists():
                    return TMMHarnessRunResult.model_validate_json(
                        final.read_text(encoding="utf-8")
                    )
            raise RunCollisionError(
                "run directory exists for a different task/request/run or "
                "incomplete marker; "
                "refusing to overwrite"
            )
        raise RunCollisionError(
            "run directory exists without a matching execution marker; "
            "refusing to overwrite"
        )

    def _release(self, lease: str) -> None:
        self.budget_adapter.release(lease)

    def _receipt(
        self,
        request: CompiledExperimentRequest,
        run_dir: Path,
        result: TMMHarnessRunResult,
        *,
        replayed: bool,
    ) -> Mapping[str, Any]:
        refs = run_artifact_refs(run_dir)
        return {
            "adapter_name": self.ADAPTER_NAME,
            "status": "adapter_completed",
            "summary": (
                "idempotent replay of existing completed run"
                if replayed
                else f"TMM run completed with status {result.status}"
            ),
            "reason": "",
            "output_refs": refs,
            "telemetry": {
                "run_dir": run_dir.relative_to(self.work_root).as_posix(),
                "task_hash": request.task_hash,
                "request_id": request.request_id,
                "run_id": self.run_id,
                "raw_status": result.status,
                "replayed": bool(replayed),
            },
        }


def _validate_count_resources(budget: Mapping[str, Any]) -> None:
    for key in _COUNT_RESOURCES:
        if key not in budget:
            continue
        value = budget[key]
        if isinstance(value, bool) or not isinstance(value, int):
            raise BudgetExecutionError(
                f"budget resource {key!r} must be an integer, got {value!r}"
            )


def required_action_for_task(task: OpticalDesignTask) -> ActionType:
    """Deterministic whole-task required action derived from task content.

    Simulate-only tasks require ``run_solver``; any optimize experiment
    requires ``run_optimizer``.  Mandatory convergence/independent-physics
    checks and the uncertainty audit are intrinsic safeguards of the harness,
    never model-proposed permissions, so they are not separately requirable
    actions here.
    """

    if any(experiment.mode == EngineMode.optimize for experiment in task.experiments):
        return ActionType.run_optimizer
    return ActionType.run_solver


def _check_operational_ceilings(
    task: OpticalDesignTask,
    requested_budget: Mapping[str, Any],
) -> None:
    """Require the request reservation to cover the task's operational ceilings."""

    required = {
        "wall_time_seconds": float(task.budget.wall_time_seconds),
        "forward_evaluations": float(task.budget.maximum_forward_evaluations),
        "optimizer_runs": float(task.budget.maximum_optimizer_runs),
    }
    for key, ceiling in required.items():
        reserved = requested_budget.get(key)
        if reserved is None:
            raise BudgetCeilingError(
                f"requested_budget.{key} is missing; the task requires at "
                f"least {ceiling:g}"
            )
        if float(reserved) < ceiling:
            raise BudgetCeilingError(
                f"requested_budget.{key} {reserved:g} under-reserves the task "
                f"ceiling {ceiling:g}"
            )


def _extract_measured_usage(result: TMMHarnessRunResult) -> Dict[str, Any]:
    budget = result.budget
    if not isinstance(budget, Mapping) or not budget:
        raise UsageMalformedError("trusted TMM result has no budget payload")
    usage = budget.get("usage")
    if not isinstance(usage, Mapping) or not usage:
        raise UsageMalformedError(
            "trusted TMM result budget has no measured usage payload"
        )
    normalized: Dict[str, Any] = {}
    for key, value in usage.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise UsageMalformedError(f"measured usage {key!r} is not numeric")
        if isinstance(value, float) and not math.isfinite(value):
            raise UsageMalformedError(f"measured usage {key!r} is not finite")
        if float(value) < 0:
            raise UsageMalformedError(f"measured usage {key!r} is negative")
        if key in _COUNT_RESOURCES and (
            isinstance(value, float) and not value.is_integer()
        ):
            raise UsageMalformedError(
                f"measured usage {key!r} must be an integer count"
            )
        normalized[key] = int(value) if key in _COUNT_RESOURCES else value
    return normalized


def run_artifact_refs(run_dir: str | Path) -> List[str]:
    """Deterministic relative artifact references actually present on disk."""

    root = Path(run_dir)
    refs: List[str] = []
    for name in (
        "FINAL_RESULT.json",
        "TASK.json",
        "EXPERIMENT_GRAPH.json",
        "RUN_STATE.json",
        EXECUTION_MARKER_FILENAME,
    ):
        candidate = root / name
        if candidate.is_file():
            refs.append(name)
    certificates = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("PHYSICS_ACCEPTANCE_CERTIFICATE.json")
        if path.is_file()
    )
    refs.extend(certificates)
    return sorted(refs)[: _MAX_ARTIFACT_REFS]


def normalize_observation_status(result: Mapping[str, Any]) -> ExperimentStatus:
    """Truthful status mapping from the trusted run payload only."""

    raw = str(result.get("status") or "").strip()
    if raw == "completed":
        experiments = result.get("experiment_results") or ()
        valid = sum(
            int(item.get("physically_valid_candidate_count") or 0)
            for item in experiments
            if isinstance(item, Mapping)
        )
        return (
            ExperimentStatus.physically_valid
            if valid > 0
            else ExperimentStatus.rejected_physics
        )
    if raw == "needs_higher_fidelity":
        return ExperimentStatus.needs_higher_fidelity
    if raw == "cancelled":
        return ExperimentStatus.cancelled
    return ExperimentStatus.failed


def observation_card_from_tmm_result(
    result: TMMHarnessRunResult | Mapping[str, Any] | None,
    *,
    run_dir: str | Path,
    experiment_id: str,
    observation_id: Optional[str] = None,
    receipt: Optional[Mapping[str, Any]] = None,
) -> ObservationCard:
    """Build a truthful ObservationCard from trusted run artifacts.

    Status/metrics come only from ``TMMHarnessRunResult``/``FINAL_RESULT.json``;
    raw status, stop decision, failure records, candidate counts, measured
    budget, run path, and relative artifact references are preserved.  No
    metrics or physics certificate are ever invented.
    """

    root = Path(run_dir)
    payload: Dict[str, Any]
    if result is None:
        final_path = root / "FINAL_RESULT.json"
        if final_path.is_file():
            try:
                payload = json.loads(final_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
        else:
            payload = {}
    elif isinstance(result, TMMHarnessRunResult):
        payload = result.model_dump(mode="json")
    else:
        payload = dict(result)

    fallback_error = (
        str((receipt or {}).get("reason") or "")
        if receipt is not None
        else ""
    )
    if not payload:
        return ObservationCard(
            observation_id=observation_id or _deterministic_observation_id(
                root, experiment_id
            ),
            experiment_id=experiment_id,
            status=ExperimentStatus.failed,
            metrics={"run_status": "failed"},
            artifact_ids=[],
            failure_records=[
                {"error": fallback_error or "no trusted TMM run result was produced"}
            ],
            failure_diagnosis={"run_status": "failed"},
            summary="No trusted TMM run result was produced.",
        )

    status = normalize_observation_status(payload)
    metrics: Dict[str, Any] = {
        "run_status": str(payload.get("status") or ""),
    }
    if payload.get("state_stage") is not None:
        metrics["state_stage"] = str(payload["state_stage"])
    if payload.get("stop_decision"):
        metrics["stop_decision"] = dict(payload["stop_decision"])
    experiments = [item for item in payload.get("experiment_results") or () if isinstance(item, Mapping)]
    experiment_rows: List[Dict[str, Any]] = []
    total_valid = 0
    total_candidates = 0
    selected_ids: List[str] = []
    for item in experiments:
        row: Dict[str, Any] = {}
        for key in (
            "experiment_id",
            "mode",
            "physically_valid_candidate_count",
            "candidate_count",
            "baseline_status",
            "portfolio_artifact_id",
        ):
            if item.get(key) is not None:
                row[key] = item[key]
        experiment_rows.append(row)
        total_valid += int(item.get("physically_valid_candidate_count") or 0)
        total_candidates += int(item.get("candidate_count") or 0)
        portfolio = item.get("portfolio")
        if isinstance(portfolio, Mapping):
            roles = portfolio.get("selected_roles")
            if isinstance(roles, Mapping):
                selected_ids.extend(str(value) for value in roles.values() if value)
    metrics["physically_valid_candidate_count"] = total_valid
    metrics["candidate_count"] = total_candidates
    metrics["experiment_count"] = len(experiments)
    metrics["experiments"] = experiment_rows
    if selected_ids:
        metrics["selected_candidate_ids"] = sorted(set(selected_ids))
    budget = payload.get("budget")
    if isinstance(budget, Mapping) and isinstance(budget.get("usage"), Mapping):
        metrics["measured_budget"] = dict(budget["usage"])

    failure_records: List[Dict[str, Any]] = []
    top_failures = payload.get("failure_records")
    if isinstance(top_failures, list):
        failure_records.extend(
            dict(item) for item in top_failures if isinstance(item, Mapping)
        )
    for item in experiments:
        records = item.get("failure_records")
        if isinstance(records, list):
            failure_records.extend(
                dict(record) for record in records if isinstance(record, Mapping)
            )
    failure_diagnosis: Dict[str, Any] = {}
    top_diagnosis = payload.get("failure_diagnosis")
    if isinstance(top_diagnosis, Mapping):
        failure_diagnosis = dict(top_diagnosis)
    elif status in {
        ExperimentStatus.rejected_physics,
        ExperimentStatus.failed,
    }:
        failure_diagnosis = {
            "run_status": str(payload.get("status") or ""),
            "state_stage": str(payload.get("state_stage") or ""),
        }
        if status == ExperimentStatus.rejected_physics:
            failure_diagnosis["reason"] = "no physically valid candidates"

    return ObservationCard(
        observation_id=observation_id or _deterministic_observation_id(
            root, experiment_id
        ),
        experiment_id=experiment_id,
        status=status,
        metrics=metrics,
        artifact_ids=run_artifact_refs(root),
        failure_records=failure_records,
        failure_diagnosis=failure_diagnosis,
        summary=(
            f"TMM run {payload.get('status')} ({payload.get('state_stage')}); "
            f"{total_valid} physically valid candidates."
        ),
    )


def _deterministic_observation_id(run_dir: Path, experiment_id: str) -> str:
    digest = hashlib.sha256(
        f"{run_dir.name}:{experiment_id}".encode("utf-8")
    ).hexdigest()[:16]
    return f"observation-{digest}"


class ArticleExecutionCoordinator:
    """Combine the narrow gateway receipt with the local run result."""

    def __init__(
        self,
        *,
        gateway: ArticleToolGateway,
        adapter: ArticleTMMExecutionAdapter,
    ) -> None:
        self.gateway = gateway
        self.adapter = adapter

    def execute(self, request: CompiledExperimentRequest) -> ArticleExecutionResult:
        run_dir = self.adapter.run_dir_for(request)
        receipt = self.gateway.execute(request, self.adapter)
        final_path = run_dir / "FINAL_RESULT.json"
        if isinstance(receipt, GatewayAdapterResult):
            result_payload = None
            if receipt.status == "adapter_completed" and final_path.is_file():
                try:
                    result_payload = json.loads(final_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    result_payload = None
            observation = observation_card_from_tmm_result(
                result_payload,
                run_dir=run_dir,
                experiment_id=request.experiment.experiment_id,
                receipt=receipt.model_dump(mode="json"),
            )
        else:
            observation = observation_card_from_tmm_result(
                None,
                run_dir=run_dir,
                experiment_id=request.experiment.experiment_id,
                receipt=receipt.model_dump(mode="json")
                if isinstance(receipt, GatewayRejection)
                else dict(receipt),
            )
        return ArticleExecutionResult(
            request_id=request.request_id,
            task_hash=request.task_hash,
            run_dir=str(run_dir),
            observation=observation,
            receipt=receipt.model_dump(mode="json")
            if isinstance(receipt, (GatewayAdapterResult, GatewayRejection))
            else dict(receipt),
            outcome=observation.status.value,
        )


__all__ = [
    "ArticleExecutionCoordinator",
    "ArticleExecutionError",
    "ArticleExecutionResult",
    "ArticleTMMExecutionAdapter",
    "BudgetExecutionError",
    "BudgetReservationError",
    "EXECUTION_MARKER_FILENAME",
    "EXECUTION_SCHEMA_VERSION",
    "HarnessExecutionError",
    "InvalidResolvedTask",
    "LocalTaskRegistry",
    "RESOLVED_TASK_SCHEMA_VERSION",
    "ResolvedTask",
    "ResolverFailure",
    "RunCollisionError",
    "TaskIdentityMismatch",
    "TaskResolver",
    "UsageMalformedError",
    "normalize_observation_status",
    "observation_card_from_tmm_result",
    "run_artifact_refs",
]
