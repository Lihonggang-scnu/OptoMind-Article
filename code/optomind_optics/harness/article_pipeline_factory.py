"""Stage 17B/17C: production assembly for the accepted ArticlePipeline.

This module wires the existing eight-stage ``ArticlePipeline`` adapters from
the trusted Article/TMM components.  It never copies their scientific logic,
never invokes Qwen by itself, and never serializes an authority secret.  The
caller binds one immutable ``ArticlePipelineRequest`` and injects an
explicit ``ArticleCompilationAuthority``; all clients, research controls,
budget components, and the harness factory are injectable so tests run with
zero network/Qwen cost.

Fail-open/fail-closed policy follows the accepted pipeline: provider/format
availability fails open (partial/unavailable), while identity, physics,
budget, hash, and provenance integrity fail closed.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Sequence, Tuple

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from optomind_optics.harness.article_assets import compile_article_assets
from optomind_optics.harness.article_director import ArticleDirector
from optomind_optics.harness.article_execution import (
    ArticleExecutionCoordinator,
    ArticleExecutionError,
    ArticleTMMExecutionAdapter,
    LocalTaskRegistry,
)
from optomind_optics.harness.article_experiment_planning import (
    ArticleExperimentPlanningResult,
    QwenArticleExperimentPlanner,
    RouteTaskBinding,
    compute_experiment_planning_result_id,
    plan_article_experiments,
)
from optomind_optics.harness.article_gateway import ArticleToolGateway
from optomind_optics.harness.article_pipeline import (
    ArticlePipeline,
    ArticlePipelineRequest,
    ArticlePipelineResult,
    build_default_pipeline,
)
from optomind_optics.harness.article_proposals import (
    ArticleCompilationAuthority,
    CompiledExperimentRequest,
    compute_optical_design_task_digest,
)
from optomind_optics.harness.article_runtime import ArticleBudgetAdapter
from optomind_optics.harness.budget import BudgetLimits, BudgetScheduler
from optomind_optics.harness.method_research import research_tmm_methods
from optomind_optics.harness.problem_analyzer import analyze_optical_problem
from optomind_optics.harness.strategy_planner import (
    QwenTMMStrategyPlanner,
    StrategyPlan,
)
from optomind_optics.harness.task_compiler import (
    QwenTMMTaskCompiler,
    TaskCompilationResult,
)


ASSEMBLY_CONFIG_SCHEMA_VERSION = "article-pipeline-assembly-config.v1"
_DEFAULT_PLANNING_STAGES = (
    "baseline_experiments",
    "exploration",
    "controlled_improvement",
    "discriminative_experiments",
    "robustness_ablation",
)
_BUDGET_KEYS = frozenset(
    {
        "wall_time_seconds",
        "forward_evaluations",
        "optimizer_runs",
        "qwen_calls",
        "qwen_input_tokens",
        "qwen_output_tokens",
        "qwen_cost_cny",
    }
)
_COUNT_BUDGET_KEYS = frozenset(
    {
        "forward_evaluations",
        "optimizer_runs",
        "qwen_calls",
        "qwen_input_tokens",
        "qwen_output_tokens",
    }
)
_RESERVED_RESEARCH_OPTIONS = frozenset(
    {
        "problem",
        "review_kb_paths",
        "online_client",
        "online",
        "synthesis_callback",
    }
)
_SENSITIVE_USAGE_KEYS = frozenset(
    {
        "api_key",
        "api_key_source",
        "raw_key",
        "raw_messages",
        "raw_response",
        "raw_content",
        "content",
        "response",
        "messages",
    }
)


def _sanitize_usage_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Copy one provider usage row without raw content or credential fields."""

    return {
        key: value
        for key, value in row.items()
        if key not in _SENSITIVE_USAGE_KEYS
    }


class PipelineAssemblyError(ValueError):
    """Base error for production assembly configuration/wiring."""


class PipelineAssemblyIntegrityError(PipelineAssemblyError):
    """Request/authority/route/task/digest identity violation."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _default_budget() -> Dict[str, Any]:
    return {
        "wall_time_seconds": 3600.0,
        "forward_evaluations": 50000,
        "optimizer_runs": 10,
        "qwen_calls": 1,
        "qwen_input_tokens": 1000,
        "qwen_output_tokens": 1000,
        "qwen_cost_cny": 1.0,
    }


class ProductionAssemblyConfig(_StrictModel):
    """Conservative typed production configuration (no credentials)."""

    schema_version: Literal["article-pipeline-assembly-config.v1"] = (
        "article-pipeline-assembly-config.v1"
    )
    work_root: str
    review_kb_paths: Tuple[str, ...] = Field(default_factory=tuple)
    online_research: bool = False
    research_options: Dict[str, Any] = Field(default_factory=dict)
    task_compiler_maximum_attempts: int = 2
    planner_maximum_attempts: int = 2
    planner_maximum_tokens: int = 4000
    available_stages: Tuple[str, ...] = Field(default_factory=tuple)
    budget: Dict[str, Any] = Field(default_factory=_default_budget)

    @field_validator("work_root")
    @classmethod
    def _non_empty_work_root(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("work_root must be a non-empty string")
        return text

    @field_validator(
        "task_compiler_maximum_attempts",
        "planner_maximum_attempts",
        "planner_maximum_tokens",
    )
    @classmethod
    def _bounded_attempts(cls, value: int, info: Any) -> int:
        number = int(value)
        if info.field_name.endswith("attempts") and not 1 <= number <= 2:
            raise ValueError(f"{info.field_name} must be 1 or 2")
        if info.field_name == "planner_maximum_tokens" and number < 512:
            raise ValueError("planner_maximum_tokens must be at least 512")
        return number

    @field_validator("available_stages")
    @classmethod
    def _known_stages(cls, value: Tuple[str, ...]) -> Tuple[str, ...]:
        known = set(_DEFAULT_PLANNING_STAGES)
        for stage in value:
            if stage not in known:
                raise ValueError(f"unknown planning stage {stage!r}")
        return tuple(dict.fromkeys(value))

    @field_validator("budget")
    @classmethod
    def _bounded_budget(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        missing = sorted(_BUDGET_KEYS - set(value))
        if missing:
            raise ValueError(
                "budget must explicitly bound every resource; missing: "
                + ", ".join(missing)
            )
        for key, item in value.items():
            if key not in _BUDGET_KEYS:
                raise ValueError(f"unknown budget key {key!r}")
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise ValueError(f"budget {key!r} must be numeric")
            if not math.isfinite(float(item)) or float(item) <= 0:
                raise ValueError(f"budget {key!r} must be finite and positive")
            if key in _COUNT_BUDGET_KEYS and not isinstance(item, int):
                raise ValueError(f"budget {key!r} must be an integer")
        return dict(value)


def _unwrap_analysis(problem_analysis: Any) -> Any:
    if (
        hasattr(problem_analysis, "analysis")
        and problem_analysis.analysis is not None
    ):
        return problem_analysis.analysis
    return problem_analysis


class ArticlePipelineAssembly:
    """Bound production assembly; never serializes the authority key."""

    def __init__(
        self,
        *,
        pipeline: ArticlePipeline,
        request: ArticlePipelineRequest,
        authority: ArticleCompilationAuthority,
        config: ProductionAssemblyConfig,
        registry: LocalTaskRegistry,
        gateway: ArticleToolGateway,
        adapter: ArticleTMMExecutionAdapter,
        coordinator: ArticleExecutionCoordinator,
        scheduler: BudgetScheduler,
        budget_adapter: ArticleBudgetAdapter,
        task_compiler: Any,
        strategy_planner: Any,
        director: Any,
        planner: Any,
    ) -> None:
        self.pipeline = pipeline
        self.request = request
        self.authority = authority
        self.config = config
        self.registry = registry
        self.gateway = gateway
        self.adapter = adapter
        self.coordinator = coordinator
        self.scheduler = scheduler
        self.budget_adapter = budget_adapter
        self.task_compiler = task_compiler
        self.strategy_planner = strategy_planner
        self.director = director
        self.planner = planner

    def run(self) -> ArticlePipelineResult:
        return self.pipeline.run(self.request)

    def resume(self) -> ArticlePipelineResult:
        return self.pipeline.resume(self.request)


class ProductionArticlePipelineFactory:
    """Build one bound production assembly from the trusted components."""

    def __init__(
        self,
        *,
        request: ArticlePipelineRequest | Mapping[str, Any],
        authority: ArticleCompilationAuthority,
        config: ProductionAssemblyConfig | Mapping[str, Any],
    ) -> None:
        try:
            self.request = (
                request
                if isinstance(request, ArticlePipelineRequest)
                else ArticlePipelineRequest.model_validate(request)
            )
        except ValidationError as exc:
            raise PipelineAssemblyIntegrityError(
                f"pipeline request is invalid: {exc}"
            ) from exc
        if not isinstance(authority, ArticleCompilationAuthority):
            raise PipelineAssemblyIntegrityError(
                "an explicit ArticleCompilationAuthority is required; it "
                "cannot be derived from request content"
            )
        self.authority = authority
        try:
            self.config = (
                config
                if isinstance(config, ProductionAssemblyConfig)
                else ProductionAssemblyConfig.model_validate(config)
            )
        except ValidationError as exc:
            raise PipelineAssemblyIntegrityError(
                f"production assembly config is invalid: {exc}"
            ) from exc
        self.work_root = Path(self.config.work_root).resolve()

    def assemble(
        self,
        *,
        problem_analyzer_client: Optional[Any] = None,
        strategy_client: Optional[Any] = None,
        director_client: Optional[Any] = None,
        task_compiler_client: Optional[Any] = None,
        task_compiler: Optional[Any] = None,
        planner_client: Optional[Any] = None,
        planner: Optional[Any] = None,
        research_online_client: Optional[Any] = None,
        synthesis_callback: Optional[Callable[..., Any]] = None,
        research_options: Optional[Mapping[str, Any]] = None,
        scheduler: Optional[BudgetScheduler] = None,
        budget_limits: Optional[BudgetLimits | Mapping[str, Any]] = None,
        harness_factory: Optional[Callable[[Path, str], Any]] = None,
        available_budget: Optional[Mapping[str, Any]] = None,
        compile_assets: Optional[Callable[..., Any]] = None,
    ) -> ArticlePipelineAssembly:
        """Assemble the pipeline and trusted local runtime components."""

        if task_compiler is not None and task_compiler_client is not None:
            raise PipelineAssemblyIntegrityError(
                "inject either task_compiler or task_compiler_client, not both"
            )
        if planner is not None and planner_client is not None:
            raise PipelineAssemblyIntegrityError(
                "inject either planner or planner_client, not both"
            )
        if scheduler is not None and budget_limits is not None:
            raise PipelineAssemblyIntegrityError(
                "inject either scheduler or budget_limits, not both"
            )
        try:
            if scheduler is None:
                scheduler = BudgetScheduler(
                    limits=(
                        budget_limits
                        if budget_limits is not None
                        else self.config.budget
                    )
                )
            unbounded = [
                key
                for key in _BUDGET_KEYS
                if getattr(scheduler.limits, key) is None
            ]
            if unbounded:
                raise ValueError(
                    "production scheduler has unbounded resources: "
                    + ", ".join(sorted(unbounded))
                )
            budget_adapter = ArticleBudgetAdapter(scheduler)
            registry = LocalTaskRegistry()
            gateway = ArticleToolGateway(
                authority=self.authority,
                run_id=self.request.run_id,
                branch_id=self.request.branch_id,
            )
            adapter = ArticleTMMExecutionAdapter(
                resolver=registry,
                budget_adapter=budget_adapter,
                work_root=self.work_root,
                branch_id=self.request.branch_id,
                run_id=self.request.run_id,
                harness_factory=harness_factory,
            )
            coordinator = ArticleExecutionCoordinator(
                gateway=gateway, adapter=adapter
            )
        except (ArticleExecutionError, ValidationError, ValueError) as exc:
            raise PipelineAssemblyIntegrityError(
                f"assembly identity/configuration error: {exc}"
            ) from exc

        task_compiler = task_compiler or QwenTMMTaskCompiler(
            client=task_compiler_client,
            maximum_attempts=self.config.task_compiler_maximum_attempts,
        )
        strategy_planner = QwenTMMStrategyPlanner(
            client=strategy_client,
            maximum_attempts=self.config.planner_maximum_attempts,
        )
        director = ArticleDirector(client=director_client)
        planner = planner or QwenArticleExperimentPlanner(
            client=planner_client,
            maximum_tokens=self.config.planner_maximum_tokens,
        )
        research_options = dict(
            research_options
            if research_options is not None
            else self.config.research_options
        )
        reserved_research_options = sorted(
            set(research_options) & _RESERVED_RESEARCH_OPTIONS
        )
        if reserved_research_options:
            raise PipelineAssemblyIntegrityError(
                "research_options cannot override factory-owned arguments: "
                + ", ".join(reserved_research_options)
            )

        def analyze(question: str, force_mock: Optional[bool]) -> Any:
            return analyze_optical_problem(
                question,
                client=problem_analyzer_client,
                force_mock=force_mock,
            )

        def research(
            problem_analysis: Any, force_mock: Optional[bool]
        ) -> Any:
            analysis = _unwrap_analysis(problem_analysis)
            online = False if force_mock else self.config.online_research
            return research_tmm_methods(
                analysis,
                review_kb_paths=(
                    list(self.config.review_kb_paths)
                    if self.config.review_kb_paths
                    else None
                ),
                online_client=research_online_client,
                online=online,
                synthesis_callback=synthesis_callback,
                **research_options,
            )

        def plan_strategy(
            problem_analysis: Any,
            method_research: Any,
            force_mock: Optional[bool],
        ) -> Any:
            analysis = _unwrap_analysis(problem_analysis)
            return strategy_planner.plan(
                analysis, method_research, force_mock=force_mock
            )

        def direct(
            question: str,
            problem_analysis: Any,
            method_research: Any,
            prior_observations: Any,
            force_mock: Optional[bool],
        ) -> Any:
            analysis = _unwrap_analysis(problem_analysis)
            return director.plan(
                question,
                analysis,
                method_research,
                prior_observations=prior_observations,
                force_mock=force_mock,
            )

        def bind_routes(
            strategy_plan: StrategyPlan | Mapping[str, Any],
            director_plan: Any,
        ) -> List[RouteTaskBinding]:
            bindings: List[RouteTaskBinding] = []
            selected_routes = sorted(
                strategy_plan.routes,
                key=lambda item: (item.priority, item.route_id),
            )
            compiled_count = 0
            for route in selected_routes:
                if compiled_count >= self.request.maximum_routes:
                    bindings.append(
                        RouteTaskBinding(
                            route_id=route.route_id,
                            route=route,
                            compiler_status="not_run",
                            compiler_usage={
                                "status": "not_run",
                                "reason": (
                                    "planned route not selected: maximum_routes "
                                    f"limit {self.request.maximum_routes}"
                                ),
                            },
                        )
                    )
                    continue
                compiled = task_compiler.compile(
                    route.execution_request_english,
                    force_mock=self.request.force_mock,
                )
                binding = self._route_binding_from_compilation(
                    route, compiled
                )
                bindings.append(binding)
                if binding.compiler_status == "compiled":
                    compiled_count += 1
            return bindings

        def plan_experiments(
            bindings: Sequence[RouteTaskBinding],
            director_plan: Any,
            force_mock: Optional[bool],
        ) -> ArticleExperimentPlanningResult:
            result = plan_article_experiments(
                director_plan,
                bindings,
                run_id=self.request.run_id,
                branch_id=self.request.branch_id,
                authority=self.authority,
                provider=planner,
                available_budget=available_budget,
                available_stages=(
                    list(self.config.available_stages)
                    if self.config.available_stages
                    else None
                ),
                maximum_attempts=self.config.planner_maximum_attempts,
                force_mock=force_mock,
            )
            identity_error = self._registration_identity_error(
                bindings, result
            )
            if identity_error is not None:
                invalid = ArticleExperimentPlanningResult(
                    plan_id=result.plan_id,
                    status="invalid",
                    validation_errors=(identity_error,),
                    result_id="",
                )
                return invalid.model_copy(
                    update={
                        "result_id": compute_experiment_planning_result_id(
                            invalid
                        )
                    }
                )
            self._register_compiled_tasks(registry, bindings, result)
            return result

        def execute(compiled_request: CompiledExperimentRequest) -> Any:
            return coordinator.execute(compiled_request)

        def compile_assets_impl(
            compiled_request: CompiledExperimentRequest,
            execution_result: Any,
            run_root: Any,
        ) -> Any:
            if compile_assets is not None:
                return compile_assets(
                    compiled_request, execution_result, run_root
                )
            return compile_article_assets(
                compiled_request,
                execution_result,
                run_root,
                authority=self.authority,
            )

        pipeline = build_default_pipeline(
            analyze=analyze,
            research=research,
            plan_strategy=plan_strategy,
            director=direct,
            bind_routes=bind_routes,
            plan_experiments=plan_experiments,
            execute=execute,
            compile_assets=compile_assets_impl,
            authority=self.authority,
        )
        return ArticlePipelineAssembly(
            pipeline=pipeline,
            request=self.request,
            authority=self.authority,
            config=self.config,
            registry=registry,
            gateway=gateway,
            adapter=adapter,
            coordinator=coordinator,
            scheduler=scheduler,
            budget_adapter=budget_adapter,
            task_compiler=task_compiler,
            strategy_planner=strategy_planner,
            director=director,
            planner=planner,
        )

    def _route_binding_from_compilation(
        self,
        route: Any,
        compiled: TaskCompilationResult | Mapping[str, Any],
    ) -> RouteTaskBinding:
        if isinstance(compiled, TaskCompilationResult):
            status = compiled.status
            task = compiled.task
            usage = {
                "status": status,
                "attempts": int(compiled.attempts),
                "rationale": str(compiled.rationale or "")[:2000],
                "validation_errors": [
                    str(item)[:500]
                    for item in (compiled.validation_errors or ())
                ][:20],
                "raw_response_sha256": [
                    str(item) for item in (compiled.raw_response_sha256 or ())
                ][:20],
                "usage": [
                    _sanitize_usage_row(row)
                    for row in (compiled.usage or ())
                    if isinstance(row, Mapping)
                ][:20],
            }
        else:
            status = str(compiled.get("status") or "")
            task = compiled.get("task")
            usage = {
                "status": status,
                "attempts": int(compiled.get("attempts") or 0),
                "rationale": str(compiled.get("rationale") or "")[:2000],
                "validation_errors": [
                    str(item)[:500]
                    for item in (compiled.get("validation_errors") or ())
                ][:20],
                "raw_response_sha256": [
                    str(item) for item in (compiled.get("raw_response_sha256") or ())
                ][:20],
                "usage": [
                    _sanitize_usage_row(row)
                    for row in (compiled.get("usage") or ())
                    if isinstance(row, Mapping)
                ][:20],
            }
        if status == "compiled" and task is not None:
            digest = compute_optical_design_task_digest(task)
            return RouteTaskBinding(
                route_id=route.route_id,
                route=route,
                compiler_status="compiled",
                task=task,
                compiler_usage=usage,
                task_digest=digest,
            )
        compiler_status = "failed" if status == "invalid" else "unavailable"
        return RouteTaskBinding(
            route_id=route.route_id,
            route=route,
            compiler_status=compiler_status,
            compiler_usage=usage,
        )

    def _registration_identity_error(
        self,
        bindings: Sequence[RouteTaskBinding],
        planning: ArticleExperimentPlanningResult,
    ) -> Optional[str]:
        by_route = {binding.route_id: binding for binding in bindings}
        for row in planning.rows:
            if row.status != "ready" or row.request is None:
                continue
            binding = by_route.get(row.route_id)
            if binding is None:
                return (
                    f"ready route {row.route_id!r} has no route/task binding"
                )
            if (
                binding.compiler_status != "compiled"
                or binding.task is None
                or not binding.task_digest
            ):
                return (
                    f"ready route {row.route_id!r} is not bound to a "
                    "compiled task"
                )
            computed = compute_optical_design_task_digest(binding.task)
            if (
                computed != binding.task_digest
                or computed != row.request.task_digest
                or binding.task_digest != row.task_digest
            ):
                return (
                    f"route/task/digest identity mismatch for ready route "
                    f"{row.route_id!r}"
                )
        return None

    def _register_compiled_tasks(
        self,
        registry: LocalTaskRegistry,
        bindings: Sequence[RouteTaskBinding],
        planning: ArticleExperimentPlanningResult,
    ) -> None:
        by_route = {binding.route_id: binding for binding in bindings}
        for row in planning.rows:
            if row.status != "ready" or row.request is None:
                continue
            binding = by_route.get(row.route_id)
            if binding is None or binding.task is None:
                continue
            registry.register(row.request.task_hash, binding.task)


__all__ = [
    "ArticlePipelineAssembly",
    "PipelineAssemblyError",
    "PipelineAssemblyIntegrityError",
    "ProductionArticlePipelineFactory",
    "ProductionAssemblyConfig",
]
