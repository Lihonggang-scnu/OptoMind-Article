"""Typed, deterministic unified Article production pipeline (orchestration).

This module is an orchestration shell: it composes the already available
problem analysis, method research, strategy planning, article director,
route/task binding, experiment planning, execution, and trusted asset
compilation modules without copying their scientific logic.  It provides a
stable handoff from a natural-language question through the existing outputs
into ``compile_article_assets``, with explicit stage receipts and honest
partial/fail-open semantics.

Boundaries:
- The pipeline never calls a model, never executes TMM, and never fabricates
  a stage output.  Every stage runs through a caller-supplied adapter.
- Stage payloads are normalized into the existing strict Pydantic models;
  identity/contract violations (invalid envelopes, request/task mismatches,
  invalid asset results) fail closed and mark downstream stages skipped.
- Ordinary adapter/provider failures (exceptions or explicit unavailable
  envelopes) preserve valid earlier outputs and return a partial result.
- Work directories are written exactly once: a non-empty ``work_dir`` is
  never overwritten, and a fresh run writes deterministic JSON snapshots,
  an append-only ``PIPELINE_EVENTS.jsonl``, and
  ``FINAL_PIPELINE_RESULT.json``.
- No wall-clock timestamps appear in receipts, snapshots, events, or the
  deterministic ``result_id``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Literal,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from optomind_optics.harness.article_assets import (
    ArticleAssetCompilationResult,
    validate_asset_compilation_result,
)
from optomind_optics.harness.article_director import (
    ArticleDirectorPlan,
    ArticleDirectorResult,
)
from optomind_optics.harness.article_execution import ArticleExecutionResult
from optomind_optics.harness.article_experiment_planning import (
    ArticleExperimentPlanningResult,
    RouteTaskBinding,
    compute_experiment_planning_result_id,
    validate_experiment_planning_result,
)
from optomind_optics.harness.article_proposals import CompiledExperimentRequest
from optomind_optics.harness.article_runtime import (
    RuntimeLock,
    RuntimeLockError,
    article_runtime_fingerprint,
)
from optomind_optics.harness.contracts import ExperimentStatus
from optomind_optics.harness.method_research import (
    MethodResearchReport,
    MethodResearchStatus,
)
from optomind_optics.harness.problem_analyzer import (
    ProblemAnalysisResult,
    TMMCompatibility,
)
from optomind_optics.harness.strategy_planner import (
    StrategyPlan,
    StrategyPlanningResult,
)
from optomind_research.runtime.artifact_store import (
    atomic_write_json,
    atomic_write_text,
)


PIPELINE_SCHEMA_VERSION = "article-pipeline-result.v1"
PIPELINE_REQUEST_SCHEMA_VERSION = "article-pipeline-request.v1"
RECEIPT_SCHEMA_VERSION = "stage-receipt.v1"
PIPELINE_EVENT_SCHEMA_VERSION = "pipeline-event.v1"

REQUEST_FILENAME = "REQUEST.json"
EVENTS_FILENAME = "PIPELINE_EVENTS.jsonl"
FINAL_RESULT_FILENAME = "FINAL_PIPELINE_RESULT.json"
_LOCK_FILENAME = "PIPELINE_RUNTIME.lock"
_RECOVERY_LEDGER_FILENAME = "RECOVERY_LEDGER.json"

PIPELINE_STAGE_ORDER = (
    "problem_analysis",
    "method_research",
    "strategy_planning",
    "article_director",
    "route_task_binding",
    "experiment_planning",
    "execution",
    "asset_compilation",
)

PipelineStage = Literal[
    "problem_analysis",
    "method_research",
    "strategy_planning",
    "article_director",
    "route_task_binding",
    "experiment_planning",
    "execution",
    "asset_compilation",
]
StageStatus = Literal[
    "completed", "partial", "unavailable", "failed", "skipped"
]
PipelineStatus = Literal["completed", "partial", "unavailable", "failed"]


class PipelineError(ValueError):
    """Base error for pipeline configuration/contract violations."""


class PipelineConfigurationError(PipelineError):
    """An adapter is missing or not callable."""


class PipelineContractError(PipelineError):
    """A stage produced an identity/contract violation."""


class _ProviderFailure(PipelineContractError):
    """An adapter callable raised; treated as a soft provider failure."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(*parts: Any) -> str:
    payload = [
        part if isinstance(part, (dict, list, tuple)) else str(part)
        for part in parts
    ]
    return hashlib.sha256(
        _canonical_json(payload).encode("utf-8")
    ).hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _short_digest(*parts: Any) -> str:
    return _digest(*parts)[:16]


class StageReceipt(_StrictModel):
    """One deterministic receipt for one pipeline stage."""

    schema_version: Literal["stage-receipt.v1"] = "stage-receipt.v1"
    sequence: int
    stage: PipelineStage
    status: StageStatus
    input_ids: Tuple[str, ...] = Field(default_factory=tuple)
    output_ids: Tuple[str, ...] = Field(default_factory=tuple)
    warnings: Tuple[str, ...] = Field(default_factory=tuple)
    errors: Tuple[str, ...] = Field(default_factory=tuple)
    payload_digest: str = ""

    @field_validator("sequence")
    @classmethod
    def _positive_sequence(cls, value: int) -> int:
        if int(value) < 1:
            raise ValueError("stage sequence must be positive")
        return int(value)

    @field_validator("payload_digest")
    @classmethod
    def _hex_digest(cls, value: str) -> str:
        text = str(value or "").strip()
        if text and (
            len(text) != 64
            or any(char not in "0123456789abcdef" for char in text)
        ):
            raise ValueError(
                "payload_digest must be empty or a 64-character lowercase "
                "hex digest"
            )
        return text


class ArticlePipelineRequest(_StrictModel):
    """One immutable pipeline run request."""

    schema_version: Literal["article-pipeline-request.v1"] = (
        "article-pipeline-request.v1"
    )
    question: str
    run_id: str
    branch_id: str
    work_dir: str
    force_mock: Optional[bool] = None
    maximum_routes: int = 4

    @field_validator("question", "run_id", "branch_id", "work_dir")
    @classmethod
    def _non_empty_text(cls, value: str, info: Any) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return text

    @field_validator("maximum_routes")
    @classmethod
    def _positive_routes(cls, value: int) -> int:
        if not isinstance(value, bool) and int(value) >= 1:
            return int(value)
        raise ValueError("maximum_routes must be a positive integer")


class ArticlePipelineResult(_StrictModel):
    """Deterministic result of one pipeline run."""

    schema_version: Literal["article-pipeline-result.v1"] = (
        "article-pipeline-result.v1"
    )
    status: PipelineStatus
    run_id: str
    question: str
    receipts: Tuple[StageReceipt, ...] = Field(default_factory=tuple)
    problem_analysis: Optional[ProblemAnalysisResult] = None
    method_research: Optional[MethodResearchReport] = None
    strategy_plan: Optional[StrategyPlanningResult] = None
    director_plan: Optional[ArticleDirectorResult] = None
    experiment_planning: Optional[ArticleExperimentPlanningResult] = None
    route_task_bindings: Tuple[RouteTaskBinding, ...] = Field(
        default_factory=tuple
    )
    execution_count: int = 0
    asset_compilations: Tuple[ArticleAssetCompilationResult, ...] = Field(
        default_factory=tuple
    )
    validation_errors: Tuple[str, ...] = Field(default_factory=tuple)
    warnings: Tuple[str, ...] = Field(default_factory=tuple)
    result_id: str = ""


def compute_pipeline_result_id(
    result: ArticlePipelineResult | Mapping[str, Any],
) -> str:
    """Deterministic content ID over a pipeline result (excluding result_id)."""

    model = (
        result
        if isinstance(result, ArticlePipelineResult)
        else ArticlePipelineResult.model_validate(result)
    )
    payload = model.model_dump(mode="json")
    payload.pop("result_id", None)
    return _digest(payload)


class AnalyzeAdapter(Protocol):
    def __call__(
        self, question: str, force_mock: Optional[bool]
    ) -> ProblemAnalysisResult | Mapping[str, Any]: ...


class ResearchAdapter(Protocol):
    def __call__(
        self,
        problem_analysis: ProblemAnalysisResult | Mapping[str, Any],
        force_mock: Optional[bool],
    ) -> MethodResearchReport | Mapping[str, Any]: ...


class StrategyAdapter(Protocol):
    def __call__(
        self,
        problem_analysis: ProblemAnalysisResult | Mapping[str, Any],
        method_research: MethodResearchReport | Mapping[str, Any],
        force_mock: Optional[bool],
    ) -> StrategyPlanningResult | Mapping[str, Any]: ...


class DirectorAdapter(Protocol):
    def __call__(
        self,
        question: str,
        problem_analysis: ProblemAnalysisResult | Mapping[str, Any],
        method_research: MethodResearchReport | Mapping[str, Any],
        prior_observations: Iterable[Any],
        force_mock: Optional[bool],
    ) -> ArticleDirectorResult | Mapping[str, Any]: ...


class BindRoutesAdapter(Protocol):
    def __call__(
        self,
        strategy_plan: StrategyPlan | Mapping[str, Any],
        director_plan: ArticleDirectorPlan | Mapping[str, Any],
    ) -> Sequence[RouteTaskBinding | Mapping[str, Any]]: ...


class PlanExperimentsAdapter(Protocol):
    def __call__(
        self,
        bindings: Sequence[RouteTaskBinding | Mapping[str, Any]],
        director_plan: ArticleDirectorPlan | Mapping[str, Any],
        force_mock: Optional[bool],
    ) -> ArticleExperimentPlanningResult | Mapping[str, Any]: ...


class ExecuteAdapter(Protocol):
    def __call__(
        self, compiled_request: CompiledExperimentRequest
    ) -> ArticleExecutionResult | Mapping[str, Any]: ...


class CompileAssetsAdapter(Protocol):
    def __call__(
        self,
        compiled_request: CompiledExperimentRequest,
        execution_result: ArticleExecutionResult | Mapping[str, Any],
        run_root: str | Path,
    ) -> ArticleAssetCompilationResult | Mapping[str, Any]: ...


def _normalize(
    value: Any,
    model_type: Any,
    label: str,
) -> Any:
    if isinstance(value, model_type):
        return value
    if value is None:
        raise _ProviderFailure(f"{label} adapter returned no payload")
    try:
        return model_type.model_validate(value)
    except ValidationError as exc:
        raise PipelineContractError(f"{label} payload is invalid: {exc}") from exc


def _invoke(name: str, fn: Callable[..., Any], *args: Any) -> Any:
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 - provider failures fail open
        raise _ProviderFailure(
            f"{name} adapter failed: {exc}"
        ) from exc


class ArticlePipeline:
    """Compose existing stage adapters into one deterministic run."""

    def __init__(
        self,
        *,
        analyze: AnalyzeAdapter,
        research: ResearchAdapter,
        plan_strategy: StrategyAdapter,
        direct: DirectorAdapter,
        bind_routes: BindRoutesAdapter,
        plan_experiments: PlanExperimentsAdapter,
        execute: ExecuteAdapter,
        compile_assets: CompileAssetsAdapter,
        authority: Optional[ArticleCompilationAuthority] = None,
    ) -> None:
        adapters = {
            "analyze": analyze,
            "research": research,
            "plan_strategy": plan_strategy,
            "direct": direct,
            "bind_routes": bind_routes,
            "plan_experiments": plan_experiments,
            "execute": execute,
            "compile_assets": compile_assets,
        }
        for name, adapter in adapters.items():
            if not callable(adapter):
                raise PipelineConfigurationError(
                    f"{name} adapter must be callable"
                )
        self.analyze = analyze
        self.research = research
        self.plan_strategy = plan_strategy
        self.direct = direct
        self.bind_routes = bind_routes
        self.plan_experiments = plan_experiments
        self.execute = execute
        self.compile_assets = compile_assets
        self.authority = authority

    def run(
        self,
        request: ArticlePipelineRequest | Mapping[str, Any],
    ) -> ArticlePipelineResult:
        """Run the pipeline once (write-once); never overwrite a work_dir.

        Continuation of an existing run must go through :meth:`resume`.
        """

        try:
            request_model = (
                request
                if isinstance(request, ArticlePipelineRequest)
                else ArticlePipelineRequest.model_validate(request)
            )
        except ValidationError as exc:
            return _failed_result(
                errors=[f"pipeline request is invalid: {exc}"],
                question=str(
                    (request or {}).get("question", "")
                    if isinstance(request, Mapping)
                    else ""
                ),
                run_id=str(
                    (request or {}).get("run_id", "")
                    if isinstance(request, Mapping)
                    else ""
                ),
            )

        work_dir = Path(request_model.work_dir)
        if work_dir.exists():
            try:
                existing = list(work_dir.iterdir())
            except OSError as exc:
                return _failed_result(
                    errors=[f"cannot inspect work_dir: {exc}"],
                    question=request_model.question,
                    run_id=request_model.run_id,
                )
            if existing:
                return _failed_result(
                    errors=[
                        "work_dir is not empty; refusing to overwrite an "
                        f"existing run: {work_dir}"
                    ],
                    question=request_model.question,
                    run_id=request_model.run_id,
                )
        else:
            try:
                work_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return _failed_result(
                    errors=[f"cannot create work_dir: {exc}"],
                    question=request_model.question,
                    run_id=request_model.run_id,
                )

        from . import article_pipeline_recovery as recovery

        lock = RuntimeLock(work_dir / recovery.LOCK_FILENAME)
        try:
            token = lock.acquire(
                request_model.run_id, request_model.branch_id
            )
        except RuntimeLockError as exc:
            return _failed_result(
                errors=[f"cannot acquire runtime lock: {exc}"],
                question=request_model.question,
                run_id=request_model.run_id,
            )
        try:
            atomic_write_json(
                work_dir / REQUEST_FILENAME,
                request_model.model_dump(mode="json"),
            )
            runner = _PipelineRunner(self, request_model, work_dir)
            return runner.run()
        except BaseException:
            raise
        finally:
            try:
                lock.release(token)
            except RuntimeLockError:
                pass

    def resume(
        self,
        request: ArticlePipelineRequest | Mapping[str, Any],
    ) -> ArticlePipelineResult:
        """Resume an interrupted pipeline from its committed recovery ledger.

        Accepts only the same immutable request.  Fails closed before any
        adapter runs on request/fingerprint/chain/hash/identity mismatches;
        committed stages are never re-executed.  A fully committed run with a
        valid final result is returned idempotently with zero adapter calls.
        """

        try:
            request_model = (
                request
                if isinstance(request, ArticlePipelineRequest)
                else ArticlePipelineRequest.model_validate(request)
            )
        except ValidationError as exc:
            return _failed_result(
                errors=[f"pipeline request is invalid: {exc}"],
                question=str(
                    (request or {}).get("question", "")
                    if isinstance(request, Mapping)
                    else ""
                ),
                run_id=str(
                    (request or {}).get("run_id", "")
                    if isinstance(request, Mapping)
                    else ""
                ),
            )

        work_dir = Path(request_model.work_dir)
        if not work_dir.is_dir():
            return _failed_result(
                errors=[
                    "work_dir does not exist; there is nothing to resume"
                ],
                question=request_model.question,
                run_id=request_model.run_id,
            )
        from . import article_pipeline_recovery as recovery

        lock = RuntimeLock(work_dir / recovery.LOCK_FILENAME)
        try:
            token = lock.acquire(
                request_model.run_id, request_model.branch_id
            )
        except RuntimeLockError as exc:
            return _failed_result(
                errors=[f"cannot acquire runtime lock: {exc}"],
                question=request_model.question,
                run_id=request_model.run_id,
            )
        try:
            errors: List[str] = []
            warnings: List[str] = []
            state = recovery.validate_recovery_state(
                work_dir, request_model, errors, warnings
            )
            if state is None:
                return _failed_result(
                    errors=errors
                    or ["recovery ledger validation failed"],
                    question=request_model.question,
                    run_id=request_model.run_id,
                )
            runner_state = self._build_resume_runner_state(
                work_dir, request_model, state, errors, warnings
            )
            if runner_state is None:
                return _failed_result(
                    errors=errors or ["resume state validation failed"],
                    question=request_model.question,
                    run_id=request_model.run_id,
                )
            if state.get("pending") is not None:
                try:
                    recovery.promote_pending_checkpoint(
                        work_dir, state
                    )
                except recovery.RecoveryIntegrityError as exc:
                    return _failed_result(
                        errors=[
                            "cannot promote pending checkpoint: "
                            f"{exc}"
                        ],
                        question=request_model.question,
                        run_id=request_model.run_id,
                    )
            if len(state["records"]) == len(PIPELINE_STAGE_ORDER):
                final_path = work_dir / FINAL_RESULT_FILENAME
                if final_path.is_file():
                    final = self._validate_final_result(
                        final_path, request_model, state, errors
                    )
                    if final is not None:
                        return final
                    return _failed_result(
                        errors=errors or ["final result is invalid"],
                        question=request_model.question,
                        run_id=request_model.run_id,
                    )
                runner = _PipelineRunner(
                    self,
                    request_model,
                    work_dir,
                    resume_state=runner_state,
                )
                result = runner.assemble_terminal_result()
                atomic_write_json(
                    final_path, result.model_dump(mode="json")
                )
                return result
            runner = _PipelineRunner(
                self,
                request_model,
                work_dir,
                resume_state=runner_state,
            )
            return runner.run()
        except BaseException:
            raise
        finally:
            try:
                lock.release(token)
            except RuntimeLockError:
                pass

    # -- resume-state helpers ------------------------------------------------

    def _normalize_committed_payload(self, stage: str, raw: Any) -> Any:
        if stage == "problem_analysis":
            return ProblemAnalysisResult.model_validate(raw)
        if stage == "method_research":
            return MethodResearchReport.model_validate(raw)
        if stage == "strategy_planning":
            return StrategyPlanningResult.model_validate(raw)
        if stage == "article_director":
            return ArticleDirectorResult.model_validate(raw)
        if stage == "route_task_binding":
            return [
                item
                if isinstance(item, RouteTaskBinding)
                else RouteTaskBinding.model_validate(item)
                for item in raw
            ]
        if stage == "experiment_planning":
            return ArticleExperimentPlanningResult.model_validate(raw)
        raise PipelineContractError(
            f"cannot normalize committed payload for stage {stage!r}"
        )

    def _build_resume_runner_state(
        self,
        work_dir: str | Path,
        request_model: ArticlePipelineRequest,
        state: Dict[str, Any],
        errors: List[str],
        warnings: List[str],
    ) -> Optional[Dict[str, Any]]:
        records = list(state["records"])
        pending = state.get("pending")
        if pending is not None:
            records.append(pending)
        receipts = [record.receipt for record in records]
        payloads: Dict[str, Any] = {}
        for record in records:
            if record.stage in {"execution", "asset_compilation"}:
                continue
            if record.stage_status == "skipped":
                continue
            try:
                raw = (
                    state["pending_payload"]
                    if pending is not None
                    and record.stage == pending.stage
                    else state["payload_snapshots"][record.stage]
                )
                payloads[record.stage] = self._normalize_committed_payload(
                    record.stage, raw
                )
            except (ValidationError, ValueError) as exc:
                errors.append(
                    f"committed {record.stage} payload is invalid: {exc}"
                )
                return None
        restored = self._restore_route_progress(
            work_dir, state["route_progress"], payloads, errors
        )
        if restored is None:
            return None
        (
            executions,
            assets,
            asset_records,
            execution_keys,
            asset_keys,
            execution_warnings,
            asset_warnings,
        ) = restored
        identity_errors = self._validate_committed_identities(
            request_model,
            payloads,
            {
                receipt.stage
                for receipt in receipts
                if receipt.status in {"completed", "partial"}
            },
        )
        if identity_errors:
            errors.extend(identity_errors)
            return None
        output_ids = {
            receipt.stage: tuple(receipt.output_ids)
            for receipt in receipts
        }
        error_list: List[str] = []
        warning_list: List[str] = []
        for receipt in receipts:
            if receipt.status == "failed":
                error_list.extend(receipt.errors)
            elif receipt.status == "skipped" and receipt.errors:
                warning_list.extend(receipt.errors)
        return {
            "receipts": receipts,
            "payloads": payloads,
            "output_ids": output_ids,
            "executions": executions,
            "assets": assets,
            "asset_records": asset_records,
            "errors": error_list,
            "warnings": warning_list,
            "hard_failed": any(
                record.hard_failure for record in records
            ),
            "last_checkpoint_id": (
                records[-1].checkpoint_id if records else ""
            ),
            "execution_keys": execution_keys,
            "asset_keys": asset_keys,
            "execution_warnings": execution_warnings,
            "asset_warnings": asset_warnings,
        }

    def _restore_route_progress(
        self,
        work_dir: str | Path,
        progress: Any,
        payloads: Dict[str, Any],
        errors: List[str],
    ) -> Optional[
        Tuple[
            List[Tuple[CompiledExperimentRequest, ArticleExecutionResult]],
            List[ArticleAssetCompilationResult],
            List[
                Tuple[
                    CompiledExperimentRequest,
                    ArticleExecutionResult,
                    ArticleAssetCompilationResult,
                ]
            ],
            set,
            set,
            Dict[str, List[str]],
            Dict[str, List[str]],
        ]
    ]:
        root = Path(work_dir)
        executions: List[
            Tuple[CompiledExperimentRequest, ArticleExecutionResult]
        ] = []
        assets: List[ArticleAssetCompilationResult] = []
        asset_records: List[
            Tuple[
                CompiledExperimentRequest,
                ArticleExecutionResult,
                ArticleAssetCompilationResult,
            ]
        ] = []
        execution_keys: set = set()
        asset_keys: set = set()
        execution_warnings: Dict[str, List[str]] = {}
        asset_warnings: Dict[str, List[str]] = {}
        planning = payloads.get("experiment_planning")
        rows_by_request: Dict[str, Any] = {}
        if planning is not None:
            for row in planning.rows:
                if row.status == "ready" and row.request is not None:
                    rows_by_request[row.request.request_id] = row
        for entry in progress.execution:
            row = rows_by_request.get(entry.request_id)
            if row is None or row.request.task_hash != entry.task_hash:
                errors.append(
                    f"execution route progress {entry.request_id!r} does "
                    "not match a ready planning row"
                )
                continue
            snapshot_path = root / entry.snapshot_filename
            try:
                execution_model = ArticleExecutionResult.model_validate(
                    json.loads(
                        snapshot_path.read_text(encoding="utf-8")
                    )
                )
            except (OSError, ValueError, ValidationError) as exc:
                errors.append(
                    f"execution route snapshot is invalid: {exc}"
                )
                continue
            if (
                execution_model.request_id != entry.request_id
                or execution_model.task_hash != entry.task_hash
            ):
                errors.append(
                    f"execution route progress {entry.request_id!r} "
                    "identity does not match its snapshot"
                )
                continue
            executions.append((row.request, execution_model))
            execution_keys.add(f"{entry.request_id}|{entry.task_hash}")
            execution_warnings[entry.request_id] = list(entry.warnings)
        for entry in progress.asset:
            row = rows_by_request.get(entry.request_id)
            execution_model = next(
                (
                    execution
                    for request, execution in executions
                    if request.request_id == entry.request_id
                ),
                None,
            )
            if (
                row is None
                or row.request.task_hash != entry.task_hash
                or execution_model is None
            ):
                errors.append(
                    f"asset route progress {entry.request_id!r} has no "
                    "matching execution"
                )
                continue
            snapshot_path = root / entry.snapshot_filename
            try:
                asset_model = ArticleAssetCompilationResult.model_validate(
                    json.loads(
                        snapshot_path.read_text(encoding="utf-8")
                    )
                )
            except (OSError, ValueError, ValidationError) as exc:
                errors.append(f"asset route snapshot is invalid: {exc}")
                continue
            validation_errors: List[str] = []
            validation_warnings: List[str] = []
            if (
                validate_asset_compilation_result(
                    asset_model,
                    validation_errors,
                    validation_warnings,
                    request=row.request,
                    execution_result=execution_model,
                )
                is None
            ):
                errors.append(
                    f"asset route progress {entry.request_id!r} fails "
                    "asset validation: "
                    + "; ".join(validation_errors)
                )
                continue
            assets.append(asset_model)
            asset_records.append(
                (row.request, execution_model, asset_model)
            )
            asset_keys.add(f"{entry.request_id}|{entry.task_hash}")
            asset_warnings[entry.request_id] = list(entry.warnings)
        if errors:
            return None
        return (
            executions,
            assets,
            asset_records,
            execution_keys,
            asset_keys,
            execution_warnings,
            asset_warnings,
        )

    def _validate_committed_identities(
        self,
        request_model: ArticlePipelineRequest,
        payloads: Dict[str, Any],
        usable_stages: Any,
    ) -> List[str]:
        identity_errors: List[str] = []
        analysis = payloads.get("problem_analysis")
        if analysis is not None and "problem_analysis" in usable_stages:
            if analysis.analysis is None:
                identity_errors.append(
                    "committed problem analysis lacks an analysis payload"
                )
            elif (
                analysis.analysis.original_request.strip()
                != request_model.question.strip()
            ):
                identity_errors.append(
                    "committed problem analysis original_request does not "
                    "match the pipeline question"
                )
        research = payloads.get("method_research")
        if (
            "method_research" in usable_stages
            and
            research is not None
            and analysis is not None
            and analysis.analysis is not None
            and research.problem_id != analysis.analysis.problem_id
        ):
            identity_errors.append(
                "committed method research problem_id does not match the "
                "problem analysis"
            )
        strategy = payloads.get("strategy_planning")
        if (
            "strategy_planning" in usable_stages
            and
            strategy is not None
            and strategy.plan is not None
            and analysis is not None
            and analysis.analysis is not None
            and strategy.plan.problem_id != analysis.analysis.problem_id
        ):
            identity_errors.append(
                "committed strategy plan problem_id does not match the "
                "problem analysis"
            )
        director = payloads.get("article_director")
        if (
            "article_director" in usable_stages
            and director is not None
            and director.plan is not None
        ):
            if director.plan.question != request_model.question:
                identity_errors.append(
                    "committed director plan question does not match the "
                    "pipeline question"
                )
            if director.plan.charter.question != request_model.question:
                identity_errors.append(
                    "committed director charter question does not match "
                    "the pipeline question"
                )
            if (
                analysis is not None
                and analysis.analysis is not None
                and director.plan.capability.status
                != analysis.analysis.compatibility
            ):
                identity_errors.append(
                    "committed director capability status does not match "
                    "the problem analysis compatibility"
                )
        bindings = payloads.get("route_task_binding")
        strategy_plan = strategy.plan if strategy is not None else None
        if (
            "route_task_binding" in usable_stages
            and bindings is not None
            and strategy_plan is not None
        ):
            plan_routes = {
                route.route_id: route for route in strategy_plan.routes
            }
            route_ids = [binding.route_id for binding in bindings]
            if len(set(route_ids)) != len(route_ids):
                identity_errors.append(
                    "committed route/task bindings contain duplicate "
                    "route_ids"
                )
            for binding in bindings:
                plan_route = plan_routes.get(binding.route_id)
                if plan_route is None:
                    identity_errors.append(
                        f"committed binding route {binding.route_id!r} is "
                        "not part of the strategy plan"
                    )
                elif binding.route != plan_route:
                    identity_errors.append(
                        f"committed binding route {binding.route_id!r} "
                        "does not match the strategy plan route"
                    )
        planning = payloads.get("experiment_planning")
        if (
            "experiment_planning" in usable_stages
            and planning is not None
        ):
            error = self._planning_contract_error(
                request_model,
                planning,
                director.plan if director is not None else None,
                bindings or [],
            )
            if error is not None:
                identity_errors.append(error)
        return identity_errors

    def _validate_final_result(
        self,
        final_path: str | Path,
        request_model: ArticlePipelineRequest,
        state: Dict[str, Any],
        errors: List[str],
    ) -> Optional[ArticlePipelineResult]:
        try:
            final = ArticlePipelineResult.model_validate(
                json.loads(
                    Path(final_path).read_text(encoding="utf-8")
                )
            )
        except (OSError, ValueError, ValidationError) as exc:
            errors.append(f"final pipeline result is invalid: {exc}")
            return None
        if final.result_id != compute_pipeline_result_id(final):
            errors.append(
                "final pipeline result_id does not match its recomputed "
                "content"
            )
            return None
        if (
            final.run_id != request_model.run_id
            or final.question != request_model.question
        ):
            errors.append(
                "final pipeline result identity does not match the request"
            )
            return None
        committed_receipts = tuple(
            record.receipt for record in state["records"]
        )
        if final.receipts != committed_receipts:
            errors.append(
                "final pipeline result receipts do not match the committed "
                "recovery ledger"
            )
            return None
        return final

    def _planning_contract_error(
        self,
        request_model: ArticlePipelineRequest,
        planning: ArticleExperimentPlanningResult,
        director_plan: Optional[ArticleDirectorPlan],
        bindings: Sequence[RouteTaskBinding | Mapping[str, Any]],
    ) -> Optional[str]:
        """Deterministic planning contract error, or None when valid."""

        recomputed_id = compute_experiment_planning_result_id(planning)
        if not planning.result_id or recomputed_id != planning.result_id:
            return (
                "experiment planning result_id is missing or does not "
                "match the recomputed identity"
            )
        ready_count = sum(
            1 for row in planning.rows if row.status == "ready"
        )
        attempted_rows = sum(
            1 for row in planning.rows if row.status != "not_run"
        )
        if (
            attempted_rows > request_model.maximum_routes
            or ready_count > request_model.maximum_routes
        ):
            return (
                "experiment planning produced "
                f"{attempted_rows} attemptable row(s) / {ready_count} ready "
                f"row(s); maximum_routes is "
                f"{request_model.maximum_routes}"
            )
        if not planning.rows:
            return (
                "experiment planning claims ready/partial with no "
                "coverage rows"
            )
        row_ids = [row.route_id for row in planning.rows]
        if len(set(row_ids)) != len(row_ids):
            return "experiment planning rows contain duplicate route_ids"
        if director_plan is not None and planning.plan_id != director_plan.plan_id:
            return (
                "experiment planning plan_id does not match the director "
                "plan_id"
            )
        for row in planning.rows:
            if row.status != "ready":
                continue
            if row.request is None:
                continue
            if row.request.run_id != request_model.run_id:
                return (
                    "experiment planning ready request run_id does not "
                    "match the pipeline request run_id"
                )
            if row.request.branch_id != request_model.branch_id:
                return (
                    "experiment planning ready request branch_id does not "
                    "match the pipeline request branch_id"
                )
            if row.proposal is None or row.cells is None:
                return (
                    f"ready route {row.route_id!r} lacks the proposal/cells "
                    "required for deterministic revalidation"
                )
        if director_plan is None:
            return "experiment planning requires the director plan"
        binding_models = [
            item
            if isinstance(item, RouteTaskBinding)
            else RouteTaskBinding.model_validate(item)
            for item in bindings
        ]
        validation_errors: List[str] = []
        try:
            planning_valid = validate_experiment_planning_result(
                planning,
                plan=director_plan,
                bindings=binding_models,
                authority=self.authority,
                errors=validation_errors,
            )
        except Exception as exc:  # noqa: BLE001 - validator robustness
            validation_errors.append(
                f"experiment planning validator raised: {exc}"
            )
            planning_valid = False
        if not planning_valid:
            return (
                "experiment planning validation failed: "
                + "; ".join(validation_errors)
            )
        return None


class _PipelineRunner:
    """Internal sequential stage execution with receipts and snapshots."""

    def __init__(
        self,
        pipeline: ArticlePipeline,
        request: ArticlePipelineRequest,
        work_dir: Path,
        resume_state: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.pipeline = pipeline
        self.request = request
        self.work_dir = work_dir
        self.receipts: List[StageReceipt] = []
        self.warnings: List[str] = []
        self.errors: List[str] = []
        self.payloads: Dict[str, Any] = {}
        self.output_ids: Dict[str, Tuple[str, ...]] = {}
        self.executions: List[Tuple[CompiledExperimentRequest, ArticleExecutionResult]] = []
        self.assets: List[ArticleAssetCompilationResult] = []
        self.asset_records: List[
            Tuple[
                CompiledExperimentRequest,
                ArticleExecutionResult,
                ArticleAssetCompilationResult,
            ]
        ] = []
        self.hard_failed = False
        self.question_id = f"question-{_short_digest(self.request.question)}"
        self.last_checkpoint_id = ""
        self.execution_committed_keys: set = set()
        self.asset_committed_keys: set = set()
        self.execution_route_warnings: Dict[str, List[str]] = {}
        self.asset_route_warnings: Dict[str, List[str]] = {}
        if resume_state is not None:
            self.receipts = list(resume_state["receipts"])
            self.payloads = dict(resume_state["payloads"])
            self.output_ids = dict(resume_state["output_ids"])
            self.executions = list(resume_state["executions"])
            self.assets = list(resume_state["assets"])
            self.asset_records = list(resume_state["asset_records"])
            self.errors = list(resume_state["errors"])
            self.warnings = list(resume_state["warnings"])
            self.hard_failed = bool(resume_state["hard_failed"])
            self.last_checkpoint_id = str(
                resume_state.get("last_checkpoint_id") or ""
            )
            self.execution_committed_keys = set(
                resume_state.get("execution_keys") or ()
            )
            self.asset_committed_keys = set(
                resume_state.get("asset_keys") or ()
            )
            self.execution_route_warnings = dict(
                resume_state.get("execution_warnings") or {}
            )
            self.asset_route_warnings = dict(
                resume_state.get("asset_warnings") or {}
            )

    def run(self) -> ArticlePipelineResult:
        analysis = self._stage_problem_analysis()
        if self._usable(analysis):
            research = self._stage_method_research()
            if self._usable(research):
                strategy = self._stage_strategy_planning()
                director = self._stage_article_director()
                if self._usable(strategy) and self._usable(director):
                    bindings = self._stage_route_task_binding()
                    if self._usable(bindings):
                        planning = self._stage_experiment_planning()
                        if self._usable(planning):
                            self._stage_execution()
                            self._stage_asset_compilation()
                        else:
                            self._skip_remaining(7, "experiment planning unavailable")
                    else:
                        self._skip_remaining(6, "route/task binding unavailable")
                else:
                    self._skip_remaining(
                        5,
                        "strategy planning or article director unavailable",
                    )
            else:
                self._skip_remaining(3, "method research unavailable")
        else:
            self._skip_remaining(2, "problem analysis unavailable")

        final = self._assemble_result()
        atomic_write_json(
            self.work_dir / FINAL_RESULT_FILENAME,
            final.model_dump(mode="json"),
        )
        return final

    def assemble_terminal_result(self) -> ArticlePipelineResult:
        """Assemble the final result from committed state (no adapters)."""

        return self._assemble_result()

    def _assemble_result(self) -> ArticlePipelineResult:
        status = self._derive_status()
        result = ArticlePipelineResult(
            status=status,
            run_id=self.request.run_id,
            question=self.request.question,
            receipts=tuple(self.receipts),
            problem_analysis=self.payloads.get("problem_analysis"),
            method_research=self.payloads.get("method_research"),
            strategy_plan=self.payloads.get("strategy_planning"),
            director_plan=self.payloads.get("article_director"),
            experiment_planning=self.payloads.get("experiment_planning"),
            route_task_bindings=tuple(
                self.payloads.get("route_task_binding") or ()
            ),
            execution_count=len(self.executions),
            asset_compilations=tuple(self.assets),
            validation_errors=tuple(dict.fromkeys(self.errors)),
            warnings=tuple(dict.fromkeys(self.warnings)),
            result_id="",
        )
        final = result.model_copy(
            update={"result_id": compute_pipeline_result_id(result)}
        )
        return final

    # -- helpers ------------------------------------------------------------

    def _usable(self, stage: str) -> bool:
        receipt = next(
            (
                item
                for item in self.receipts
                if item.stage == stage
            ),
            None,
        )
        return receipt is not None and receipt.status in {
            "completed",
            "partial",
        }

    def _skip_remaining(self, first_sequence: int, cause: str) -> None:
        for sequence, stage in enumerate(
            PIPELINE_STAGE_ORDER, start=1
        ):
            if sequence < first_sequence:
                continue
            if any(item.sequence == sequence for item in self.receipts):
                continue
            self._record_stage(
                sequence,
                stage,
                "skipped",
                input_ids=(),
                output_ids=(),
                warnings=(),
                errors=(cause,),
                payload={"status": "skipped", "cause": cause},
            )
        if cause not in self.warnings:
            self.warnings.append(cause)

    def _record_stage(
        self,
        sequence: int,
        stage: str,
        status: StageStatus,
        *,
        input_ids: Sequence[str],
        output_ids: Sequence[str],
        warnings: Sequence[str],
        errors: Sequence[str],
        payload: Any,
        hard_failure: bool = False,
    ) -> StageReceipt:
        payload_digest = _digest(_snapshot_payload(payload))
        receipt = StageReceipt(
            sequence=sequence,
            stage=stage,
            status=status,
            input_ids=tuple(input_ids),
            output_ids=tuple(output_ids),
            warnings=tuple(dict.fromkeys(warnings)),
            errors=tuple(dict.fromkeys(errors)),
            payload_digest=payload_digest,
        )
        self.receipts.append(receipt)
        self.output_ids[stage] = tuple(output_ids)
        snapshot_name = f"{sequence:02d}-{stage}.json"
        snapshot_text = (
            _canonical_json(_snapshot_payload(payload)) + "\n"
        )
        atomic_write_text(self.work_dir / snapshot_name, snapshot_text)
        snapshot_sha256 = _sha256_bytes(
            (self.work_dir / snapshot_name).read_bytes()
        )
        event = {
            "schema_version": PIPELINE_EVENT_SCHEMA_VERSION,
            "sequence": sequence,
            "stage": stage,
            "status": status,
            "payload_digest": payload_digest,
            "event_id": _digest(
                sequence, stage, status, payload_digest
            ),
        }
        events_path = self.work_dir / EVENTS_FILENAME
        lines: List[str] = []
        if events_path.exists():
            lines.extend(
                line
                for line in events_path.read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip()
            )
        lines.append(json.dumps(event, sort_keys=True))
        atomic_write_text(events_path, "\n".join(lines) + "\n")
        from . import article_pipeline_recovery as recovery

        events_parsed = [
            json.loads(line)
            for line in lines
            if line.strip()
        ]
        route_progress_digest = ""
        if stage in {"execution", "asset_compilation"}:
            try:
                current_progress = recovery.load_route_progress(
                    self.work_dir
                )
                if stage == "execution":
                    route_progress_digest = (
                        recovery.compute_execution_progress_digest(
                            current_progress
                        )
                    )
                else:
                    route_progress_digest = (
                        recovery.compute_asset_progress_digest(
                            current_progress
                        )
                    )
            except recovery.RecoveryIntegrityError:
                route_progress_digest = ""
        record = recovery.PipelineCheckpointRecord(
            request_digest=recovery.request_digest(self.request),
            runtime_fingerprint=article_runtime_fingerprint(),
            stage_sequence=sequence,
            stage=stage,
            stage_status=status,
            receipt=receipt,
            snapshot_filename=snapshot_name,
            snapshot_sha256=snapshot_sha256,
            payload_digest=payload_digest,
            event_prefix_digest=recovery.compute_event_prefix_digest(
                events_parsed
            ),
            route_progress_digest=route_progress_digest,
            previous_checkpoint_id=self.last_checkpoint_id,
            checkpoint_id="",
            hard_failure=hard_failure,
        )
        record = record.model_copy(
            update={
                "checkpoint_id": recovery.compute_checkpoint_id(record)
            }
        )
        recovery.write_checkpoint(self.work_dir, record)
        self.last_checkpoint_id = record.checkpoint_id
        return receipt

    def _mark_failed_stage(
        self,
        sequence: int,
        stage: str,
        *,
        input_ids: Sequence[str],
        errors: Sequence[str],
        warnings: Sequence[str],
        payload: Any,
        hard: bool,
    ) -> None:
        self._record_stage(
            sequence,
            stage,
            "failed",
            input_ids=input_ids,
            output_ids=(),
            warnings=warnings,
            errors=errors,
            payload=payload,
            hard_failure=hard,
        )
        self.errors.extend(errors)
        if hard:
            self.hard_failed = True

    def _derive_status(self) -> PipelineStatus:
        if self.hard_failed:
            return "failed"
        statuses = [receipt.status for receipt in self.receipts]
        completed = statuses.count("completed")
        if completed == 0 and "unavailable" in statuses:
            return "unavailable"
        if any(
            item in {"partial", "failed", "unavailable", "skipped"}
            for item in statuses
        ):
            return "partial"
        return "completed"

    # -- stages -------------------------------------------------------------

    def _stage_problem_analysis(self) -> str:
        if any(
            item.stage == "problem_analysis" for item in self.receipts
        ):
            return "problem_analysis"
        try:
            raw = _invoke(
                "problem_analysis",
                self.pipeline.analyze,
                self.request.question,
                self.request.force_mock,
            )
            model = _normalize(
                raw, ProblemAnalysisResult, "problem_analysis"
            )
        except PipelineContractError as exc:
            self._mark_failed_stage(
                1,
                "problem_analysis",
                input_ids=(self.question_id,),
                errors=(str(exc),),
                warnings=(),
                payload=None,
                hard=not _is_soft_provider_failure(exc),
            )
            return "problem_analysis"
        self.payloads["problem_analysis"] = model
        if model.status == "unavailable":
            self._record_stage(
                1,
                "problem_analysis",
                "unavailable",
                input_ids=(self.question_id,),
                output_ids=(),
                warnings=tuple(model.validation_warnings),
                errors=(),
                payload=model,
            )
        elif model.status == "invalid":
            self._mark_failed_stage(
                1,
                "problem_analysis",
                input_ids=(self.question_id,),
                errors=(
                    "problem analysis is invalid: "
                    + "; ".join(model.validation_warnings),
                ),
                warnings=(),
                payload=model,
                hard=True,
            )
        else:
            if model.analysis is None:
                self._mark_failed_stage(
                    1,
                    "problem_analysis",
                    input_ids=(self.question_id,),
                    errors=(
                        "problem analysis reported analyzed without an "
                        "analysis payload",
                    ),
                    warnings=tuple(model.validation_warnings),
                    payload=model,
                    hard=True,
                )
                return "problem_analysis"
            if (
                model.analysis.original_request.strip()
                != self.request.question.strip()
            ):
                self._mark_failed_stage(
                    1,
                    "problem_analysis",
                    input_ids=(self.question_id,),
                    errors=(
                        "problem analysis original_request does not match "
                        "the pipeline question",
                    ),
                    warnings=tuple(model.validation_warnings),
                    payload=model,
                    hard=True,
                )
                return "problem_analysis"
            if (
                model.analysis is not None
                and model.analysis.compatibility
                == TMMCompatibility.incompatible
            ):
                self._record_stage(
                    1,
                    "problem_analysis",
                    "unavailable",
                    input_ids=(self.question_id,),
                    output_ids=(),
                    warnings=(),
                    errors=(
                        "capability boundary: "
                        + str(model.analysis.compatibility_reason),
                    ),
                    payload=model,
                )
                return "problem_analysis"
            output_id = (
                f"analysis:{model.analysis.problem_id}"
                if model.analysis is not None
                else f"analysis:{_short_digest(model)}"
            )
            self._record_stage(
                1,
                "problem_analysis",
                "completed",
                input_ids=(self.question_id,),
                output_ids=(output_id,),
                warnings=tuple(model.validation_warnings),
                errors=(),
                payload=model,
            )
        return "problem_analysis"

    def _stage_method_research(self) -> str:
        if any(item.stage == "method_research" for item in self.receipts):
            return "method_research"
        analysis_id = self.output_ids["problem_analysis"][0]
        try:
            raw = _invoke(
                "method_research",
                self.pipeline.research,
                self.payloads["problem_analysis"],
                self.request.force_mock,
            )
            model = _normalize(
                raw, MethodResearchReport, "method_research"
            )
        except PipelineContractError as exc:
            self._mark_failed_stage(
                2,
                "method_research",
                input_ids=(analysis_id,),
                errors=(str(exc),),
                warnings=(),
                payload=None,
                hard=not _is_soft_provider_failure(exc),
            )
            return "method_research"
        self.payloads["method_research"] = model
        analysis_model = self.payloads["problem_analysis"]
        analysis_problem_id = (
            analysis_model.analysis.problem_id
            if analysis_model.analysis is not None
            else ""
        )
        if analysis_problem_id and model.problem_id != analysis_problem_id:
            self._mark_failed_stage(
                2,
                "method_research",
                input_ids=(analysis_id,),
                errors=(
                    "method research problem_id does not match the problem "
                    f"analysis problem_id ({model.problem_id!r} != "
                    f"{analysis_problem_id!r})",
                ),
                warnings=(),
                payload=model,
                hard=True,
            )
            return "method_research"
        output_id = f"research:{model.problem_id}"
        if model.status == MethodResearchStatus.unavailable:
            self._record_stage(
                2,
                "method_research",
                "unavailable",
                input_ids=(analysis_id,),
                output_ids=(),
                warnings=tuple(model.reasons),
                errors=(),
                payload=model,
            )
        else:
            stage_status = (
                "partial"
                if model.status == MethodResearchStatus.partial
                else "completed"
            )
            self._record_stage(
                2,
                "method_research",
                stage_status,
                input_ids=(analysis_id,),
                output_ids=(output_id,),
                warnings=tuple(model.reasons),
                errors=(),
                payload=model,
            )
        return "method_research"

    def _stage_strategy_planning(self) -> str:
        if any(
            item.stage == "strategy_planning" for item in self.receipts
        ):
            return "strategy_planning"
        input_ids = (
            self.output_ids["problem_analysis"][0],
            self.output_ids["method_research"][0],
        )
        try:
            raw = _invoke(
                "strategy_planning",
                self.pipeline.plan_strategy,
                self.payloads["problem_analysis"],
                self.payloads["method_research"],
                self.request.force_mock,
            )
            model = _normalize(
                raw, StrategyPlanningResult, "strategy_planning"
            )
        except PipelineContractError as exc:
            self._mark_failed_stage(
                3,
                "strategy_planning",
                input_ids=input_ids,
                errors=(str(exc),),
                warnings=(),
                payload=None,
                hard=not _is_soft_provider_failure(exc),
            )
            return "strategy_planning"
        self.payloads["strategy_planning"] = model
        if model.status == "unavailable":
            self._record_stage(
                3,
                "strategy_planning",
                "unavailable",
                input_ids=input_ids,
                output_ids=(),
                warnings=tuple(model.normalization_warnings),
                errors=tuple(model.validation_errors),
                payload=model,
            )
        elif model.status == "invalid":
            self._mark_failed_stage(
                3,
                "strategy_planning",
                input_ids=input_ids,
                errors=(
                    "strategy planning is invalid: "
                    + "; ".join(model.validation_errors),
                ),
                warnings=tuple(model.normalization_warnings),
                payload=model,
                hard=True,
            )
        else:
            if model.plan is None:
                self._mark_failed_stage(
                    3,
                    "strategy_planning",
                    input_ids=input_ids,
                    errors=(
                        "strategy planning reported planned without a plan",
                    ),
                    warnings=tuple(model.normalization_warnings),
                    payload=model,
                    hard=True,
                )
                return "strategy_planning"
            analysis_problem_id = (
                self.payloads["problem_analysis"].analysis.problem_id
                if self.payloads["problem_analysis"].analysis is not None
                else ""
            )
            if (
                analysis_problem_id
                and model.plan.problem_id != analysis_problem_id
            ):
                self._mark_failed_stage(
                    3,
                    "strategy_planning",
                    input_ids=input_ids,
                    errors=(
                        "strategy plan problem_id does not match the problem "
                        f"analysis problem_id ({model.plan.problem_id!r} != "
                        f"{analysis_problem_id!r})",
                    ),
                    warnings=tuple(model.normalization_warnings),
                    payload=model,
                    hard=True,
                )
                return "strategy_planning"
            self._record_stage(
                3,
                "strategy_planning",
                "completed",
                input_ids=input_ids,
                output_ids=(f"strategy:{model.plan.problem_id}",),
                warnings=tuple(model.normalization_warnings),
                errors=(),
                payload=model,
            )
        return "strategy_planning"

    def _stage_article_director(self) -> str:
        if any(
            item.stage == "article_director" for item in self.receipts
        ):
            return "article_director"
        input_ids = (
            self.output_ids["problem_analysis"][0],
            self.output_ids["method_research"][0],
        )
        try:
            raw = _invoke(
                "article_director",
                self.pipeline.direct,
                self.request.question,
                self.payloads["problem_analysis"],
                self.payloads["method_research"],
                (),
                self.request.force_mock,
            )
            model = _normalize(
                raw, ArticleDirectorResult, "article_director"
            )
        except PipelineContractError as exc:
            self._mark_failed_stage(
                4,
                "article_director",
                input_ids=input_ids,
                errors=(str(exc),),
                warnings=(),
                payload=None,
                hard=not _is_soft_provider_failure(exc),
            )
            return "article_director"
        self.payloads["article_director"] = model
        if model.status == "unavailable":
            self._record_stage(
                4,
                "article_director",
                "unavailable",
                input_ids=input_ids,
                output_ids=(),
                warnings=tuple(model.normalization_warnings),
                errors=tuple(model.validation_errors),
                payload=model,
            )
        elif model.status == "invalid":
            self._mark_failed_stage(
                4,
                "article_director",
                input_ids=input_ids,
                errors=(
                    "article director is invalid: "
                    + "; ".join(model.validation_errors),
                ),
                warnings=tuple(model.normalization_warnings),
                payload=model,
                hard=True,
            )
        else:
            if model.plan is None:
                self._mark_failed_stage(
                    4,
                    "article_director",
                    input_ids=input_ids,
                    errors=(
                        "article director reported planned without a plan",
                    ),
                    warnings=tuple(model.normalization_warnings),
                    payload=model,
                    hard=True,
                )
                return "article_director"
            if model.plan.question != self.request.question:
                self._mark_failed_stage(
                    4,
                    "article_director",
                    input_ids=input_ids,
                    errors=(
                        "director plan question does not match the pipeline "
                        "question",
                    ),
                    warnings=tuple(model.normalization_warnings),
                    payload=model,
                    hard=True,
                )
                return "article_director"
            if model.plan.charter.question != self.request.question:
                self._mark_failed_stage(
                    4,
                    "article_director",
                    input_ids=input_ids,
                    errors=(
                        "director plan charter question does not match the "
                        "pipeline question",
                    ),
                    warnings=tuple(model.normalization_warnings),
                    payload=model,
                    hard=True,
                )
                return "article_director"
            analysis_model = self.payloads["problem_analysis"]
            if (
                analysis_model.analysis is not None
                and model.plan.capability.status
                != analysis_model.analysis.compatibility
            ):
                self._mark_failed_stage(
                    4,
                    "article_director",
                    input_ids=input_ids,
                    errors=(
                        "director capability status does not match the "
                        "problem analysis compatibility",
                    ),
                    warnings=tuple(model.normalization_warnings),
                    payload=model,
                    hard=True,
                )
                return "article_director"
            self._record_stage(
                4,
                "article_director",
                "completed",
                input_ids=input_ids,
                output_ids=(model.plan.plan_id,),
                warnings=tuple(model.normalization_warnings),
                errors=(),
                payload=model,
            )
        return "article_director"

    def _stage_route_task_binding(self) -> str:
        if any(
            item.stage == "route_task_binding" for item in self.receipts
        ):
            return "route_task_binding"
        strategy = self.payloads["strategy_planning"]
        director = self.payloads["article_director"]
        input_ids = (
            self.output_ids["strategy_planning"][0],
            self.output_ids["article_director"][0],
        )
        assert strategy.plan is not None
        assert director.plan is not None
        try:
            raw = _invoke(
                "route_task_binding",
                self.pipeline.bind_routes,
                strategy.plan,
                director.plan,
            )
        except _ProviderFailure as exc:
            self._mark_failed_stage(
                5,
                "route_task_binding",
                input_ids=input_ids,
                errors=(str(exc),),
                warnings=(),
                payload=None,
                hard=False,
            )
            return "route_task_binding"
        except PipelineContractError as exc:
            self._mark_failed_stage(
                5,
                "route_task_binding",
                input_ids=input_ids,
                errors=(str(exc),),
                warnings=(),
                payload=None,
                hard=True,
            )
            return "route_task_binding"
        if isinstance(raw, RouteTaskBinding):
            items: Sequence[Any] = [raw]
        elif isinstance(raw, Mapping):
            items = [raw]
        elif isinstance(raw, (list, tuple)):
            items = list(raw)
        elif isinstance(raw, (str, bytes)):
            self._mark_failed_stage(
                5,
                "route_task_binding",
                input_ids=input_ids,
                errors=(
                    "route/task binding adapter returned a scalar payload",
                ),
                warnings=(),
                payload=None,
                hard=True,
            )
            return "route_task_binding"
        else:
            try:
                items = list(raw)
            except TypeError as exc:
                self._mark_failed_stage(
                    5,
                    "route_task_binding",
                    input_ids=input_ids,
                    errors=(
                        "route/task binding adapter returned a "
                        f"non-iterable payload: {exc}",
                    ),
                    warnings=(),
                    payload=None,
                    hard=True,
                )
                return "route_task_binding"
            except Exception as exc:  # noqa: BLE001 - generator failure
                self._mark_failed_stage(
                    5,
                    "route_task_binding",
                    input_ids=input_ids,
                    errors=(
                        "route/task binding adapter iteration failed: "
                        f"{exc}",
                    ),
                    warnings=(),
                    payload=None,
                    hard=False,
                )
                return "route_task_binding"
        try:
            bindings = [
                item
                if isinstance(item, RouteTaskBinding)
                else RouteTaskBinding.model_validate(item)
                for item in items
            ]
        except ValidationError as exc:
            self._mark_failed_stage(
                5,
                "route_task_binding",
                input_ids=input_ids,
                errors=(f"route/task binding is invalid: {exc}",),
                warnings=(),
                payload=None,
                hard=True,
            )
            return "route_task_binding"
        if not bindings:
            self.payloads["route_task_binding"] = []
            self._record_stage(
                5,
                "route_task_binding",
                "unavailable",
                input_ids=input_ids,
                output_ids=(),
                warnings=("no bindable routes were produced",),
                errors=(),
                payload=[],
            )
            return "route_task_binding"
        plan_routes = {route.route_id: route for route in strategy.plan.routes}
        route_ids = [binding.route_id for binding in bindings]
        if len(set(route_ids)) != len(route_ids):
            self._mark_failed_stage(
                5,
                "route_task_binding",
                input_ids=input_ids,
                errors=(
                    "route/task bindings contain duplicate route_ids",
                ),
                warnings=(),
                payload=bindings,
                hard=True,
            )
            return "route_task_binding"
        for binding in bindings:
            plan_route = plan_routes.get(binding.route_id)
            if plan_route is None:
                self._mark_failed_stage(
                    5,
                    "route_task_binding",
                    input_ids=input_ids,
                    errors=(
                        f"route/task binding route {binding.route_id!r} is "
                        "not part of the strategy plan",
                    ),
                    warnings=(),
                    payload=bindings,
                    hard=True,
                )
                return "route_task_binding"
            if binding.route != plan_route:
                self._mark_failed_stage(
                    5,
                    "route_task_binding",
                    input_ids=input_ids,
                    errors=(
                        f"route/task binding route {binding.route_id!r} "
                        "does not exactly match the strategy plan route",
                    ),
                    warnings=(),
                    payload=bindings,
                    hard=True,
                )
                return "route_task_binding"
        compiled_bindings = sum(
            1
            for binding in bindings
            if binding.compiler_status == "compiled"
        )
        if compiled_bindings > self.request.maximum_routes:
            self._mark_failed_stage(
                5,
                "route_task_binding",
                input_ids=input_ids,
                errors=(
                    f"route_task_binding produced {compiled_bindings} "
                    f"compiled route(s); maximum_routes is "
                    f"{self.request.maximum_routes}",
                ),
                warnings=(),
                payload=bindings,
                hard=True,
            )
            return "route_task_binding"
        compiled = [item for item in bindings if item.compiler_status == "compiled"]
        if not compiled:
            self.payloads["route_task_binding"] = bindings
            self._record_stage(
                5,
                "route_task_binding",
                "unavailable",
                input_ids=input_ids,
                output_ids=(),
                warnings=("no compiled route/task bindings were produced",),
                errors=(),
                payload=bindings,
            )
            return "route_task_binding"
        self.payloads["route_task_binding"] = bindings
        self._record_stage(
            5,
            "route_task_binding",
            "completed",
            input_ids=input_ids,
            output_ids=(f"bindings:{_short_digest(bindings)}",),
            warnings=(),
            errors=(),
            payload=bindings,
        )
        return "route_task_binding"

    def _stage_experiment_planning(self) -> str:
        if any(
            item.stage == "experiment_planning" for item in self.receipts
        ):
            return "experiment_planning"
        bindings = self.payloads["route_task_binding"]
        director = self.payloads["article_director"]
        input_ids = (
            self.output_ids["route_task_binding"][0],
            self.output_ids["article_director"][0],
        )
        assert director.plan is not None
        try:
            raw = _invoke(
                "experiment_planning",
                self.pipeline.plan_experiments,
                bindings,
                director.plan,
                self.request.force_mock,
            )
            model = _normalize(
                raw,
                ArticleExperimentPlanningResult,
                "experiment_planning",
            )
        except PipelineContractError as exc:
            self._mark_failed_stage(
                6,
                "experiment_planning",
                input_ids=input_ids,
                errors=(str(exc),),
                warnings=(),
                payload=None,
                hard=not _is_soft_provider_failure(exc),
            )
            return "experiment_planning"
        self.payloads["experiment_planning"] = model
        if model.status == "unavailable":
            self._record_stage(
                6,
                "experiment_planning",
                "unavailable",
                input_ids=input_ids,
                output_ids=(),
                warnings=tuple(model.omissions),
                errors=tuple(model.validation_errors),
                payload=model,
            )
        elif model.status == "invalid":
            self._mark_failed_stage(
                6,
                "experiment_planning",
                input_ids=input_ids,
                errors=(
                    "experiment planning is invalid: "
                    + "; ".join(model.validation_errors),
                ),
                warnings=tuple(model.omissions),
                payload=model,
                hard=True,
            )
        else:
            contract_error = self.pipeline._planning_contract_error(
                self.request, model, director.plan, bindings
            )
            if contract_error is not None:
                self._mark_failed_stage(
                    6,
                    "experiment_planning",
                    input_ids=input_ids,
                    errors=(contract_error,),
                    warnings=tuple(model.omissions),
                    payload=model,
                    hard=True,
                )
                return "experiment_planning"
            output_id = (
                model.result_id
                or model.plan_id
                or f"planning:{_short_digest(model)}"
            )
            self._record_stage(
                6,
                "experiment_planning",
                (
                    "partial"
                    if model.status == "partial"
                    else "completed"
                ),
                input_ids=input_ids,
                output_ids=(output_id,),
                warnings=tuple(model.omissions),
                errors=(),
                payload=model,
            )
        return "experiment_planning"

    def _stage_execution(self) -> None:
        if any(item.stage == "execution" for item in self.receipts):
            return
        planning = self.payloads["experiment_planning"]
        input_ids = (self.output_ids["experiment_planning"][0],)
        ready_rows = [
            row
            for row in planning.rows
            if row.status == "ready" and row.request is not None
        ]
        not_ready = [
            row
            for row in planning.rows
            if row.status != "ready" or row.request is None
        ]
        if not ready_rows:
            self._record_stage(
                7,
                "execution",
                "skipped",
                input_ids=input_ids,
                output_ids=(),
                warnings=(
                    "no ready planned rows with compiled requests; "
                    f"{len(not_ready)} row(s) not executed",
                ),
                errors=(),
                payload=[],
            )
            return
        warnings: List[str] = [
            item
            for values in self.execution_route_warnings.values()
            for item in values
        ]
        for row in not_ready:
            warnings.append(
                f"route {row.route_id} not executed (status "
                f"{row.status}); retained as not_run/omitted"
            )
        executed: List[
            Tuple[CompiledExperimentRequest, ArticleExecutionResult]
        ] = list(self.executions)
        soft_failures: List[str] = []
        for row in ready_rows:
            request_model = row.request
            route_key = (
                f"{request_model.request_id}|{request_model.task_hash}"
            )
            if route_key in self.execution_committed_keys:
                continue
            route_warnings: List[str] = []
            try:
                raw = _invoke(
                    "execution",
                    self.pipeline.execute,
                    request_model,
                )
                execution_model = _normalize(
                    raw, ArticleExecutionResult, "execution"
                )
            except PipelineContractError as exc:
                if _is_soft_provider_failure(exc):
                    soft_failures.append(
                        f"route {row.route_id}: {exc}"
                    )
                    continue
                self._mark_failed_stage(
                    7,
                    "execution",
                    input_ids=input_ids,
                    errors=(
                        f"route {row.route_id}: {exc}",
                    ),
                    warnings=warnings,
                    payload=executed,
                    hard=True,
                )
                return
            if (
                execution_model.request_id != request_model.request_id
                or execution_model.task_hash != request_model.task_hash
            ):
                self._mark_failed_stage(
                    7,
                    "execution",
                    input_ids=input_ids,
                    errors=(
                        f"route {row.route_id}: execution identity does not "
                        "match the compiled request; request_id/task_hash "
                        "binding failed",
                    ),
                    warnings=warnings,
                    payload=executed,
                    hard=True,
                )
                return
            if execution_model.observation.status in {
                ExperimentStatus.failed,
                ExperimentStatus.rejected_physics,
                ExperimentStatus.cancelled,
            }:
                route_warnings.append(
                    f"route {row.route_id}: execution observation status is "
                    f"{execution_model.observation.status.value}; assets "
                    "will not be trusted"
                )
                warnings.append(route_warnings[-1])
            executed.append((request_model, execution_model))
            from . import article_pipeline_recovery as recovery

            recovery.write_execution_route(
                self.work_dir,
                request_model,
                execution_model,
                route_id=row.route_id,
                warnings=route_warnings,
            )
            self.execution_committed_keys.add(route_key)
        self.executions = executed
        status = "completed" if not soft_failures else "partial"
        warnings.extend(soft_failures)
        self._record_stage(
            7,
            "execution",
            status,
            input_ids=input_ids,
            output_ids=tuple(
                f"exec:{request_model.request_id}"
                for request_model, _ in executed
            ),
            warnings=warnings,
            errors=(),
            payload=executed,
        )

    def _stage_asset_compilation(self) -> None:
        if any(
            item.stage == "asset_compilation" for item in self.receipts
        ):
            return
        input_ids = tuple(
            f"exec:{request_model.request_id}"
            for request_model, _ in self.executions
        )
        if not self.executions:
            self._record_stage(
                8,
                "asset_compilation",
                "skipped",
                input_ids=input_ids,
                output_ids=(),
                warnings=("no executions available for asset compilation",),
                errors=(),
                payload=[],
            )
            return
        assets: List[ArticleAssetCompilationResult] = list(self.assets)
        asset_records: List[
            Tuple[
                CompiledExperimentRequest,
                ArticleExecutionResult,
                ArticleAssetCompilationResult,
            ]
        ] = list(self.asset_records)
        invalid: List[str] = []
        soft_failures: List[str] = []
        new_route_warnings: List[str] = []
        for request_model, execution_model in self.executions:
            route_key = (
                f"{request_model.request_id}|{request_model.task_hash}"
            )
            if route_key in self.asset_committed_keys:
                continue
            try:
                raw = _invoke(
                    "asset_compilation",
                    self.pipeline.compile_assets,
                    request_model,
                    execution_model,
                    execution_model.run_dir,
                )
            except _ProviderFailure as exc:
                soft_failures.append(
                    f"route {request_model.request_id}: {exc}"
                )
                continue
            except PipelineContractError as exc:
                invalid.append(f"route {request_model.request_id}: {exc}")
                continue
            try:
                asset_model = _normalize(
                    raw,
                    ArticleAssetCompilationResult,
                    "asset_compilation",
                )
            except _ProviderFailure as exc:
                soft_failures.append(
                    f"route {request_model.request_id}: {exc}"
                )
                continue
            except PipelineContractError as exc:
                invalid.append(f"route {request_model.request_id}: {exc}")
                continue
            validation_errors: List[str] = []
            validation_warnings: List[str] = []
            if (
                validate_asset_compilation_result(
                    asset_model,
                    validation_errors,
                    validation_warnings,
                    request=request_model,
                    execution_result=execution_model,
                )
                is None
            ):
                invalid.append(
                    f"route {request_model.request_id}: asset validation "
                    "failed: " + "; ".join(validation_errors)
                )
                continue
            assets.append(asset_model)
            asset_records.append(
                (request_model, execution_model, asset_model)
            )
            route_warnings: List[str] = []
            if asset_model.status == "invalid":
                invalid.append(
                    f"route {request_model.request_id}: asset compilation "
                    "is invalid; retained but not trusted"
                )
            elif asset_model.status == "unavailable":
                route_warnings.append(
                    f"route {request_model.request_id}: asset compilation "
                    "is unavailable; no trusted assets for this route"
                )
            elif asset_model.status == "partial":
                route_warnings.append(
                    f"route {request_model.request_id}: asset compilation "
                    "is partial"
                )
            new_route_warnings.extend(route_warnings)
            from . import article_pipeline_recovery as recovery

            recovery.write_asset_route(
                self.work_dir,
                request_model,
                execution_model,
                asset_model,
                warnings=route_warnings,
            )
            self.asset_committed_keys.add(route_key)
        self.assets = assets
        self.asset_records = asset_records
        if invalid:
            self._mark_failed_stage(
                8,
                "asset_compilation",
                input_ids=input_ids,
                errors=tuple(invalid),
                warnings=tuple(soft_failures + new_route_warnings),
                payload=assets,
                hard=True,
            )
            return
        if not asset_records:
            self._record_stage(
                8,
                "asset_compilation",
                "unavailable",
                input_ids=input_ids,
                output_ids=(),
                warnings=(
                    "no usable asset results were produced",
                ) + tuple(soft_failures),
                errors=(),
                payload=[],
            )
            return
        route_warnings: List[str] = [
            item
            for values in self.asset_route_warnings.values()
            for item in values
        ]
        route_warnings.extend(new_route_warnings)
        route_warnings.extend(soft_failures)
        if soft_failures or any(
            asset.status in {"unavailable", "partial"}
            for _, _, asset in asset_records
        ):
            stage_status: StageStatus = "partial"
        else:
            stage_status = "completed"
        self._record_stage(
            8,
            "asset_compilation",
            stage_status,
            input_ids=input_ids,
            output_ids=tuple(
                f"assets:{asset.result_id or _short_digest(asset)}"
                for _, _, asset in asset_records
            ),
            warnings=route_warnings,
            errors=(),
            payload=asset_records,
        )


def _snapshot_payload(payload: Any) -> Any:
    """Deterministic snapshot representation of a stage payload."""

    if payload is None:
        return {"status": "none"}
    return _to_jsonable(payload)


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    return value


def _is_soft_provider_failure(exc: PipelineContractError) -> bool:
    """Provider exceptions/empty payloads are soft; validation is contract."""

    return isinstance(exc, _ProviderFailure)


def _failed_result(
    *,
    errors: Sequence[str],
    question: str,
    run_id: str,
) -> ArticlePipelineResult:
    model = ArticlePipelineResult(
        status="failed",
        run_id=run_id,
        question=question,
        validation_errors=tuple(dict.fromkeys(errors)),
        warnings=(),
        result_id="",
    )
    return model.model_copy(
        update={"result_id": compute_pipeline_result_id(model)}
    )


def build_default_pipeline(
    *,
    analyze: AnalyzeAdapter,
    research: ResearchAdapter,
    plan_strategy: StrategyAdapter,
    director: DirectorAdapter,
    bind_routes: BindRoutesAdapter,
    plan_experiments: PlanExperimentsAdapter,
    execute: ExecuteAdapter,
    compile_assets: CompileAssetsAdapter,
    authority: Optional[ArticleCompilationAuthority] = None,
) -> ArticlePipeline:
    """Wire caller-supplied adapters into an ArticlePipeline.

    This helper never instantiates a Qwen client and never runs network or
    solver work itself; every stage adapter must be provided explicitly.
    """

    return ArticlePipeline(
        analyze=analyze,
        research=research,
        plan_strategy=plan_strategy,
        direct=director,
        bind_routes=bind_routes,
        plan_experiments=plan_experiments,
        execute=execute,
        compile_assets=compile_assets,
        authority=authority,
    )


def run_article_pipeline(
    request: ArticlePipelineRequest | Mapping[str, Any],
    pipeline: ArticlePipeline,
) -> ArticlePipelineResult:
    """Run one request through an ArticlePipeline."""

    return pipeline.run(request)


__all__ = [
    "AnalyzeAdapter",
    "ArticlePipeline",
    "ArticlePipelineRequest",
    "ArticlePipelineResult",
    "BindRoutesAdapter",
    "CompileAssetsAdapter",
    "DirectorAdapter",
    "ExecuteAdapter",
    "PIPELINE_STAGE_ORDER",
    "PipelineConfigurationError",
    "PipelineContractError",
    "PipelineError",
    "PlanExperimentsAdapter",
    "ResearchAdapter",
    "StageReceipt",
    "StrategyAdapter",
    "build_default_pipeline",
    "compute_pipeline_result_id",
    "run_article_pipeline",
]
