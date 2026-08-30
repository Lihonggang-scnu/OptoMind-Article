"""Article route-to-experiment proposal bridge (production proposal planning).

Stage 6.5: fills high-information proposal cells with qwen3.7-flash from a
compact local table, while local code owns the schema, IDs, the actual
OpticalDesignTask, required action, parameters, budgets, hashes, and HMAC
compilation.  This removes test-only hand construction before the later
integration orchestrator.

Boundaries:
- Qwen sees only semantic aliases (R01..., H01...) and route/hypothesis
  summaries; it never sees HMAC secrets, task hashes, proposal IDs, model
  names, action permissions, parameters, or budgets, and it never supplies an
  OpticalDesignTask.
- Every route/task binding must carry the task compiler status/usage and the
  canonical task digest; mismatched, duplicate, missing, or extra identities
  fail closed.  Task compiler failures become explicit ``not_run`` rows.
- One row per executable route.  Unknown/duplicate aliases, malformed rows,
  and missing routes preserve valid independent rows and record
  route-specific omissions/errors; routes are never remapped and tasks are
  never invented.  One compact repair attempt with validation feedback is
  allowed.
- Local code derives ``required_action_for_task``, allowed parameters from
  the exact task/route, and ``requested_budget`` from the task's non-Qwen
  ceilings; the resulting task-bound requests pass the execution ceiling
  checks, existing global caps, and gateway authorization.
- No TMM execution happens here.  Ordinary provider/format issues fail open
  per route; identity, alias, action, budget, and HMAC violations fail closed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Sequence, Tuple

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from optomind_optics.harness.article_contracts import ArticleStage
from optomind_optics.harness.article_director import (
    ArticleDirectorPlan,
    HypothesisCandidate,
)
from optomind_optics.harness.article_execution import required_action_for_task
from optomind_optics.harness.article_proposals import (
    BUDGET_CAPS,
    ArticleCompilationAuthority,
    CompiledExperimentRequest,
    ExperimentProposal,
    ProposalCompileError,
    compile_proposal,
    compute_optical_design_task_digest,
    compute_task_hash,
)
from optomind_optics.harness.contracts import ActionType
from optomind_optics.harness.design_task import OpticalDesignTask
from optomind_optics.harness.strategy_planner import DesignRoute
from optomind_optics.harness.qwen_policy import QWEN_POLICY_MODEL, QwenFlashOnlyClient


PLANNING_SCHEMA_VERSION = "article-experiment-planning-result.v1"
PLANNING_PROVIDER_SCHEMA_VERSION = "article-experiment-planning-provider.v1"
DEFAULT_PLANNING_PROMPT = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "optical_harness"
    / "Article Experiment Planning.txt"
)
MODEL_NAME = QWEN_POLICY_MODEL

DEFAULT_AVAILABLE_STAGES = frozenset(
    {
        ArticleStage.baseline_experiments,
        ArticleStage.exploration,
        ArticleStage.controlled_improvement,
        ArticleStage.discriminative_experiments,
        ArticleStage.robustness_ablation,
    }
)


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


class RouteTaskBinding(_StrictModel):
    """One visible strategy route plus its locally compiled task binding."""

    schema_version: Literal["route-task-binding.v1"] = "route-task-binding.v1"
    route_id: str
    route: DesignRoute
    compiler_status: Literal["compiled", "failed", "unavailable", "not_run"]
    task: Optional[OpticalDesignTask] = None
    compiler_usage: Dict[str, Any] = Field(default_factory=dict)
    task_digest: str = ""

    @model_validator(mode="after")
    def _identity_and_digest(self) -> "RouteTaskBinding":
        if self.route_id != self.route.route_id:
            raise ValueError(
                "binding route_id does not match its DesignRoute route_id"
            )
        if self.compiler_status == "compiled":
            if self.task is None:
                raise ValueError(
                    "compiled route binding requires an OpticalDesignTask"
                )
            expected = compute_optical_design_task_digest(self.task)
            if self.task_digest != expected:
                raise ValueError(
                    "route task_digest does not match the compiled task content"
                )
        else:
            if self.task is not None or self.task_digest:
                raise ValueError(
                    "non-compiled route binding must not carry a task or digest"
                )
        return self


class PlanningProviderResult(_StrictModel):
    schema_version: Literal["article-experiment-planning-provider.v1"] = (
        "article-experiment-planning-provider.v1"
    )
    response: Dict[str, Any]
    usage: Dict[str, Any] = Field(default_factory=dict)
    provider_model: str = "unknown"
    mock_llm: bool = False


PlanningProvider = Callable[[Mapping[str, Any]], PlanningProviderResult]


class PlanningRowCells(_StrictModel):
    """The only semantic cells a model may fill for one executable route."""

    schema_version: Literal["planning-row-cells.v1"] = "planning-row-cells.v1"
    route_alias: str
    hypothesis_aliases: Tuple[str, ...] = Field(default_factory=tuple)
    stage: str = ""
    atomic_change: Dict[str, Any] = Field(default_factory=dict)
    expected_discriminator: Dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    uncertainty: str = ""


class PlannedRouteResult(_StrictModel):
    schema_version: Literal["planned-route-result.v1"] = "planned-route-result.v1"
    route_id: str
    route_alias: str = ""
    compiler_status: str = ""
    status: Literal["ready", "error", "omitted", "not_run", "unavailable"]
    task_digest: str = ""
    allowed_action: Optional[str] = None
    proposal_id: Optional[str] = None
    proposal: Optional[ExperimentProposal] = None
    request: Optional[CompiledExperimentRequest] = None
    cells: Optional[PlanningRowCells] = None
    errors: Tuple[str, ...] = Field(default_factory=tuple)
    warnings: Tuple[str, ...] = Field(default_factory=tuple)


class ArticleExperimentPlanningResult(_StrictModel):
    schema_version: Literal["article-experiment-planning-result.v1"] = (
        "article-experiment-planning-result.v1"
    )
    plan_id: str
    status: Literal["ready", "partial", "unavailable", "invalid"]
    rows: Tuple[PlannedRouteResult, ...] = Field(default_factory=tuple)
    omissions: Tuple[str, ...] = Field(default_factory=tuple)
    validation_errors: Tuple[str, ...] = Field(default_factory=tuple)
    attempts: int = 0
    usage: Tuple[Dict[str, Any], ...] = Field(default_factory=tuple)
    model_name: str = "none"
    result_id: str = ""


def _as_model(value: Any, model_type: Any, label: str) -> Any:
    if isinstance(value, model_type):
        return value
    try:
        return model_type.model_validate(value)
    except ValidationError as exc:
        raise ValueError(f"{label} is invalid: {exc}") from exc


def _route_aliases(bindings: Sequence[RouteTaskBinding]) -> Dict[str, str]:
    ordered = sorted(
        bindings,
        key=lambda item: (item.route.priority, item.route_id),
    )
    return {
        f"R{index:02d}": item.route_id
        for index, item in enumerate(ordered, 1)
    }


def _hypothesis_aliases(
    hypotheses: Sequence[HypothesisCandidate],
) -> Dict[str, str]:
    ordered = sorted(hypotheses, key=lambda item: item.hypothesis_id)
    return {
        f"H{index:02d}": item.hypothesis_id
        for index, item in enumerate(ordered, 1)
    }


def _derive_parameters(task: OpticalDesignTask, action: ActionType) -> Dict[str, Any]:
    experiment_id = task.experiments[0].experiment_id
    if action == ActionType.run_optimizer:
        return {
            "experiment_id": experiment_id,
            "optimizer_id": "gradient_thickness",
            "maximum_evaluations": int(
                min(task.budget.maximum_forward_evaluations, 100_000)
            ),
        }
    return {
        "experiment_id": experiment_id,
        "solver": "smatrix",
    }


def _derive_requested_budget(task: OpticalDesignTask) -> Dict[str, Any]:
    return {
        "wall_time_seconds": float(task.budget.wall_time_seconds),
        "forward_evaluations": int(task.budget.maximum_forward_evaluations),
        "optimizer_runs": int(task.budget.maximum_optimizer_runs),
    }


def _proposal_id_for(
    *,
    plan_id: str,
    route_id: str,
    task_digest: str,
    cells: PlanningRowCells,
    stage: ArticleStage,
    hypothesis_ids: Tuple[str, ...],
) -> str:
    payload = _canonical_json(
        {
            "plan_id": plan_id,
            "route_id": route_id,
            "task_digest": task_digest,
            "stage": stage.value,
            "hypothesis_ids": sorted(hypothesis_ids),
            "atomic_change": cells.atomic_change,
            "expected_discriminator": cells.expected_discriminator,
            "rationale": cells.rationale,
            "uncertainty": cells.uncertainty,
        }
    )
    return "proposal-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _compile_ready_row(
    *,
    plan: ArticleDirectorPlan,
    binding: RouteTaskBinding,
    cells: PlanningRowCells,
    stage: ArticleStage,
    hypothesis_ids: Tuple[str, ...],
    run_id: str,
    branch_id: str,
    authority: ArticleCompilationAuthority,
    available_budget: Optional[Mapping[str, Any]],
) -> Tuple[PlannedRouteResult, List[str]]:
    errors: List[str] = []
    task = binding.task
    if task is None:
        errors.append("route task is unavailable")
        return (
            PlannedRouteResult(
                route_id=binding.route_id,
                status="error",
                errors=tuple(errors),
            ),
            errors,
        )
    action = required_action_for_task(task)
    requested_budget = _derive_requested_budget(task)
    for key, value in requested_budget.items():
        if float(value) > float(BUDGET_CAPS[key]):
            errors.append(
                f"task ceiling {key} {value} exceeds the documented budget cap "
                f"{BUDGET_CAPS[key]}"
            )
    if errors:
        return (
            PlannedRouteResult(
                route_id=binding.route_id,
                status="error",
                errors=tuple(errors),
            ),
            errors,
        )
    proposal_id = _proposal_id_for(
        plan_id=plan.plan_id,
        route_id=binding.route_id,
        task_digest=binding.task_digest,
        cells=cells,
        stage=stage,
        hypothesis_ids=hypothesis_ids,
    )
    try:
        proposal = ExperimentProposal(
            proposal_id=proposal_id,
            hypothesis_ids=list(hypothesis_ids),
            stage=stage,
            action_type=action,
            parameters=_derive_parameters(task, action),
            atomic_change=dict(cells.atomic_change),
            expected_discriminator=dict(cells.expected_discriminator),
            rationale=cells.rationale,
            uncertainty=cells.uncertainty,
            requested_budget=requested_budget,
        )
        request = compile_proposal(
            proposal,
            plan=plan,
            run_id=run_id,
            branch_id=branch_id,
            authority=authority,
            budget_lease_id=None,
            available_budget=available_budget,
            task=task,
        )
    except (ProposalCompileError, ValueError) as exc:
        errors.append(str(exc))
        return (
            PlannedRouteResult(
                route_id=binding.route_id,
                status="error",
                errors=tuple(errors),
            ),
            errors,
        )
    if compute_task_hash(request) != request.task_hash or not authority.verify(
        request
    ):
        errors.append("compiled request failed local attestation verification")
    if request.task_digest != binding.task_digest:
        errors.append("compiled request task digest does not match the binding")
    if errors:
        return (
            PlannedRouteResult(
                route_id=binding.route_id,
                status="error",
                errors=tuple(errors),
            ),
            errors,
        )
    return (
        PlannedRouteResult(
            route_id=binding.route_id,
            route_alias=cells.route_alias,
            compiler_status=binding.compiler_status,
            status="ready",
            task_digest=binding.task_digest,
            allowed_action=action.value,
            proposal_id=proposal_id,
            proposal=proposal,
            request=request,
            cells=cells,
        ),
        errors,
    )


def _build_provider_request(
    *,
    plan: ArticleDirectorPlan,
    executable: Sequence[RouteTaskBinding],
    route_alias: Dict[str, str],
    hypothesis_alias: Dict[str, str],
    available_stages: Sequence[ArticleStage],
    prior_feedback: Mapping[str, str],
) -> Dict[str, Any]:
    hypotheses = {
        item.hypothesis_id: item for item in plan.hypotheses
    }
    routes = []
    alias_to_route = {value: key for key, value in route_alias.items()}
    for binding in executable:
        task = binding.task
        route_rows = []
        for experiment in task.experiments:
            route_rows.append(
                {
                    "experiment_id": experiment.experiment_id,
                    "mode": experiment.mode.value,
                }
            )
        candidate_hypotheses = [
            item
            for item in plan.hypotheses
            if item.route_kind == binding.route.route_kind
        ] or list(plan.hypotheses)
        route_hypotheses = [
            {
                "hypothesis_alias": next(
                    alias
                    for alias, hypothesis_id in hypothesis_alias.items()
                    if hypothesis_id == hypothesis.hypothesis_id
                ),
                "prediction": hypothesis.falsifiable_prediction,
            }
            for hypothesis in sorted(
                candidate_hypotheses,
                key=lambda item: item.hypothesis_id,
            )
        ]
        routes.append(
            {
                "route_alias": alias_to_route[binding.route_id],
                "route_title": binding.route.title,
                "route_kind": binding.route.route_kind,
                "scientific_hypothesis": binding.route.scientific_hypothesis,
                "design_principle": binding.route.design_principle,
                "execution_request_summary": binding.route.execution_request_english[
                    :1200
                ],
                "task_mode": (
                    "optimize"
                    if any(
                        experiment.mode.value == "optimize"
                        for experiment in task.experiments
                    )
                    else "simulate"
                ),
                "task_experiments": route_rows,
                "hypotheses": route_hypotheses,
                "coverage_responsibilities": [
                    str(item)
                    for item in (
                        plan.coverage_matrix.rows
                        if hasattr(plan.coverage_matrix, "rows")
                        else []
                    )
                ][:8],
                "prior_feedback": prior_feedback.get(binding.route_id, ""),
            }
        )
    return {
        "protocol": "article-experiment-proposal-fill.v1",
        "instruction": (
            "Fill proposal semantic cells only. Never output schema boilerplate, "
            "IDs, hashes, model names, actions, parameters, or budgets."
        ),
        "available_stages": [item.value for item in available_stages],
        "routes": routes,
    }


def _parse_provider_rows(
    response: Mapping[str, Any],
) -> Tuple[List[PlanningRowCells], List[str]]:
    errors: List[str] = []
    raw_rows = response.get("rows")
    if not isinstance(raw_rows, list):
        errors.append("provider response has no rows list")
        return [], errors
    cells: List[PlanningRowCells] = []
    for index, raw in enumerate(raw_rows):
        if not isinstance(raw, dict):
            errors.append(f"provider row {index} is not an object")
            continue
        try:
            cells.append(PlanningRowCells.model_validate(raw))
        except ValidationError as exc:
            errors.append(f"provider row {index} is malformed: {exc}")
    return cells, errors


def _resolve_stage(value: str, available: Sequence[ArticleStage]) -> Optional[ArticleStage]:
    try:
        stage = ArticleStage(value)
    except ValueError:
        return None
    return stage if stage in available else None


def _plan_single_attempt(
    *,
    plan: ArticleDirectorPlan,
    executable: Sequence[RouteTaskBinding],
    alias_to_route: Dict[str, str],
    hypothesis_alias: Dict[str, str],
    available_stages: Sequence[ArticleStage],
    run_id: str,
    branch_id: str,
    authority: ArticleCompilationAuthority,
    available_budget: Optional[Mapping[str, Any]],
    provider: PlanningProvider,
    request_table: Mapping[str, Any],
    force_mock: bool | None,
) -> Tuple[Dict[str, PlannedRouteResult], Dict[str, str], Dict[str, Any], List[str]]:
    """One provider attempt; returns per-route results keyed by route_id."""

    envelope = provider(request_table)
    if not isinstance(envelope, PlanningProviderResult):
        raise TypeError(
            "planning provider must return a PlanningProviderResult"
        )
    rows, parse_errors = _parse_provider_rows(envelope.response)
    route_results: Dict[str, PlannedRouteResult] = {}
    route_feedback: Dict[str, str] = {}
    used_aliases: set[str] = set()
    for cells in rows:
        route_id = alias_to_route.get(cells.route_alias)
        if route_id is None:
            route_feedback[cells.route_alias] = "unknown route alias"
            continue
        if cells.route_alias in used_aliases:
            route_feedback[cells.route_alias] = "duplicate route row"
            route_results.setdefault(
                route_id,
                PlannedRouteResult(
                    route_id=route_id,
                    route_alias=cells.route_alias,
                    compiler_status="compiled",
                    status="error",
                    errors=("duplicate route row",),
                ),
            )
            continue
        used_aliases.add(cells.route_alias)
        binding = next(
            (item for item in executable if item.route_id == route_id),
            None,
        )
        if binding is None:
            route_feedback[cells.route_alias] = "route is not executable"
            route_results.setdefault(
                route_id,
                PlannedRouteResult(
                    route_id=route_id,
                    route_alias=cells.route_alias,
                    compiler_status="failed",
                    status="not_run",
                    errors=("route is not executable",),
                ),
            )
            continue
        hypothesis_ids = []
        hypothesis_errors = []
        for alias in cells.hypothesis_aliases:
            hypothesis_id = hypothesis_alias.get(alias)
            if hypothesis_id is None:
                hypothesis_errors.append(f"unknown hypothesis alias {alias!r}")
            else:
                hypothesis_ids.append(hypothesis_id)
        if not cells.hypothesis_aliases:
            hypothesis_errors.append("no hypothesis aliases supplied")
        stage = _resolve_stage(cells.stage, available_stages)
        if stage is None:
            hypothesis_errors.append(
                f"invalid or unavailable stage {cells.stage!r}"
            )
        if hypothesis_errors:
            route_feedback[cells.route_alias] = "; ".join(hypothesis_errors)
            route_results[route_id] = PlannedRouteResult(
                route_id=route_id,
                route_alias=cells.route_alias,
                compiler_status=binding.compiler_status,
                status="error",
                task_digest=binding.task_digest,
                cells=cells,
                errors=tuple(hypothesis_errors),
            )
            continue
        result, _errors = _compile_ready_row(
            plan=plan,
            binding=binding,
            cells=cells,
            stage=stage,
            hypothesis_ids=tuple(hypothesis_ids),
            run_id=run_id,
            branch_id=branch_id,
            authority=authority,
            available_budget=available_budget,
        )
        route_results[route_id] = result
        if result.status != "ready":
            route_feedback[cells.route_alias] = "; ".join(result.errors)
    return (
        route_results,
        route_feedback,
        dict(envelope.usage or {}),
        parse_errors,
        envelope.provider_model,
    )


def plan_article_experiments(
    plan: ArticleDirectorPlan | Mapping[str, Any],
    bindings: Sequence[RouteTaskBinding | Mapping[str, Any]],
    *,
    run_id: str,
    branch_id: str,
    authority: ArticleCompilationAuthority,
    provider: Optional[PlanningProvider] = None,
    available_budget: Optional[Mapping[str, Any]] = None,
    prior_feedback: Optional[Mapping[str, str]] = None,
    available_stages: Optional[Sequence[ArticleStage]] = None,
    maximum_attempts: int = 2,
    force_mock: bool | None = None,
) -> ArticleExperimentPlanningResult:
    """Plan task-bound Article experiment proposals for visible routes."""

    validation_errors: List[str] = []
    try:
        plan_model = _as_model(plan, ArticleDirectorPlan, "plan")
        binding_models = [
            _as_model(item, RouteTaskBinding, "route/task binding")
            if not isinstance(item, RouteTaskBinding)
            else item
            for item in bindings
        ]
    except ValueError as exc:
        return ArticleExperimentPlanningResult(
            plan_id=str(plan.get("plan_id") or "") if isinstance(plan, Mapping) else "",
            status="invalid",
            validation_errors=(str(exc),),
            result_id="",
        )
    if not binding_models:
        validation_errors.append("at least one route/task binding is required")
    route_ids = [item.route_id for item in binding_models]
    if len(route_ids) != len(set(route_ids)):
        validation_errors.append("route/task binding route_ids must be unique")
    if not run_id or not str(run_id).strip():
        validation_errors.append("run_id must be a non-empty string")
    if not branch_id or not str(branch_id).strip():
        validation_errors.append("branch_id must be a non-empty string")
    if not isinstance(authority, ArticleCompilationAuthority):
        validation_errors.append(
            "an ArticleCompilationAuthority is required"
        )
    if validation_errors:
        return ArticleExperimentPlanningResult(
            plan_id=plan_model.plan_id,
            status="invalid",
            rows=tuple(
                PlannedRouteResult(
                    route_id=item.route_id,
                    compiler_status=item.compiler_status,
                    status="not_run",
                    errors=tuple(validation_errors),
                )
                for item in binding_models
            ),
            validation_errors=tuple(validation_errors),
            result_id="",
        )
    stage_list = list(
        available_stages
        if available_stages is not None
        else sorted(DEFAULT_AVAILABLE_STAGES, key=lambda item: item.value)
    )
    executable = [
        item for item in binding_models if item.compiler_status == "compiled"
    ]
    not_run = [
        item for item in binding_models if item.compiler_status != "compiled"
    ]
    alias_to_route = _route_aliases(binding_models)
    route_to_alias = {value: key for key, value in alias_to_route.items()}
    hypothesis_alias = _hypothesis_aliases(plan_model.hypotheses)
    rows: Dict[str, PlannedRouteResult] = {}
    for item in not_run:
        rows[item.route_id] = PlannedRouteResult(
            route_id=item.route_id,
            route_alias=route_to_alias.get(item.route_id, ""),
                compiler_status=item.compiler_status,
                status="not_run",
                task_digest="",
                warnings=(
                    "task compiler did not produce an executable OpticalDesignTask"
                    if item.compiler_status != "not_run"
                    else "route not run"
                ,),
            )
    usages: List[Dict[str, Any]] = []
    attempts = 0
    model_names: set[str] = set()
    if executable and provider is not None:
        base_request = _build_provider_request(
            plan=plan_model,
            executable=executable,
            route_alias=alias_to_route,
            hypothesis_alias=hypothesis_alias,
            available_stages=stage_list,
            prior_feedback=dict(prior_feedback or {}),
        )
        pending = {item.route_id for item in executable}
        repair_feedback: Dict[str, str] = {}
        parse_errors: List[str] = []
        for attempt in range(1, max(1, int(maximum_attempts)) + 1):
            if not pending:
                break
            if attempt == 1:
                request_table = dict(base_request)
            else:
                request_table = {
                    **base_request,
                    "repair_request": {
                        "validation_errors": list(repair_feedback.values())
                        + list(parse_errors),
                        "instruction": (
                            "Repair only the listed route rows. Return rows for "
                            "exactly those route aliases with corrected semantic "
                            "cells. Never remap a route alias."
                        ),
                    },
                }
            try:
                (
                    attempt_results,
                    feedback,
                    usage,
                    attempt_parse_errors,
                    attempt_model,
                ) = (
                    _plan_single_attempt(
                        plan=plan_model,
                        executable=executable,
                        alias_to_route=alias_to_route,
                        hypothesis_alias=hypothesis_alias,
                        available_stages=stage_list,
                        run_id=run_id,
                        branch_id=branch_id,
                        authority=authority,
                        available_budget=available_budget,
                        provider=provider,
                        request_table=request_table,
                        force_mock=force_mock,
                    )
                )
                model_names.add(attempt_model)
            except Exception as exc:
                attempts += 1
                for route_id in pending:
                    rows[route_id] = PlannedRouteResult(
                        route_id=route_id,
                        route_alias=route_to_alias.get(route_id, ""),
                        compiler_status="compiled",
                        status="unavailable",
                        task_digest=next(
                            item.task_digest
                            for item in executable
                            if item.route_id == route_id
                        ),
                        errors=(f"{type(exc).__name__}: {exc}",),
                    )
                break
            attempts += 1
            usages.append(dict(usage))
            parse_errors = attempt_parse_errors
            repair_feedback = dict(feedback)
            for route_id, result in attempt_results.items():
                if route_id in pending:
                    rows[route_id] = result
                    if result.status == "ready":
                        pending.discard(route_id)
            for route_id in list(pending):
                if route_id in rows and rows[route_id].status in {
                    "ready",
                    "unavailable",
                }:
                    pending.discard(route_id)
        for route_id in pending:
            if route_id not in rows:
                rows[route_id] = PlannedRouteResult(
                    route_id=route_id,
                    route_alias=route_to_alias.get(route_id, ""),
                    compiler_status="compiled",
                    status="omitted",
                    task_digest=next(
                        item.task_digest
                        for item in executable
                        if item.route_id == route_id
                    ),
                    errors=("no valid provider row after all attempts",),
                )
    elif executable and provider is None:
        for route_id in {item.route_id for item in executable}:
            rows[route_id] = PlannedRouteResult(
                route_id=route_id,
                route_alias=route_to_alias.get(route_id, ""),
                compiler_status="compiled",
                status="unavailable",
                task_digest=next(
                    item.task_digest
                    for item in executable
                    if item.route_id == route_id
                ),
                errors=("no planning provider supplied",),
            )
    ready_count = sum(1 for item in rows.values() if item.status == "ready")
    if not executable:
        status = "ready"
    elif ready_count == len(executable):
        status = "ready"
    elif ready_count == 0:
        status = "unavailable"
    else:
        status = "partial"
    result = ArticleExperimentPlanningResult(
        plan_id=plan_model.plan_id,
        status=status,
        rows=tuple(
            rows[item.route_id]
            for item in sorted(
                binding_models,
                key=lambda item: (item.route.priority, item.route_id),
            )
        ),
        omissions=tuple(
            item.route_id
            for item in rows.values()
            if item.status == "omitted"
        ),
        validation_errors=tuple(validation_errors),
        attempts=attempts,
        usage=tuple(usages),
        model_name=(
            "none"
            if not model_names
            else "mixed"
            if len(model_names) > 1
            else next(iter(model_names))
        ),
        result_id="",
    )
    return result.model_copy(
        update={"result_id": compute_experiment_planning_result_id(result)}
    )


def compute_experiment_planning_result_id(
    result: ArticleExperimentPlanningResult,
) -> str:
    return hashlib.sha256(
        _canonical_json(
            result.model_dump(exclude={"result_id"}, mode="json")
        ).encode("utf-8")
    ).hexdigest()


def validate_experiment_planning_result(
    result: ArticleExperimentPlanningResult | Mapping[str, Any],
    *,
    plan: Optional[ArticleDirectorPlan | Mapping[str, Any]] = None,
    bindings: Optional[Sequence[RouteTaskBinding | Mapping[str, Any]]] = None,
    authority: Optional[ArticleCompilationAuthority] = None,
    errors: Optional[List[str]] = None,
) -> bool:
    """Public deterministic validator (no network/model calls)."""

    if errors is None:
        errors = []
    model = (
        result
        if isinstance(result, ArticleExperimentPlanningResult)
        else ArticleExperimentPlanningResult.model_validate(result)
    )
    recomputed = compute_experiment_planning_result_id(model)
    if recomputed != model.result_id:
        errors.append(
            "experiment planning result_id does not match recomputed identity"
        )
    for row in model.rows:
        if row.status == "ready":
            if row.request is None or row.proposal is None:
                errors.append(
                    f"ready route {row.route_id!r} lacks a proposal/request"
                )
                continue
            if row.request.task_digest != row.task_digest:
                errors.append(
                    f"ready route {row.route_id!r} request task digest "
                    "does not match its row"
                )
            if row.request.allowed_action.value != row.allowed_action:
                errors.append(
                    f"ready route {row.route_id!r} allowed action mismatch"
                )
            if compute_task_hash(row.request) != row.request.task_hash:
                errors.append(
                    f"ready route {row.route_id!r} request task hash mismatch"
                )
            if (
                authority is not None
                and not authority.verify(row.request)
            ):
                errors.append(
                    f"ready route {row.route_id!r} request attestation invalid"
                )
            if row.proposal_id != row.proposal.proposal_id:
                errors.append(
                    f"ready route {row.route_id!r} proposal id mismatch"
                )
        else:
            if row.request is not None or row.proposal is not None:
                errors.append(
                    f"non-ready route {row.route_id!r} must not carry a "
                    "proposal/request"
                )
    if plan is not None and bindings is not None:
        try:
            plan_model = _as_model(plan, ArticleDirectorPlan, "plan")
            binding_models = [
                _as_model(item, RouteTaskBinding, "route/task binding")
                for item in bindings
            ]
        except ValueError as exc:
            errors.append(str(exc))
            return not errors
        by_route = {item.route_id: item for item in binding_models}
        for row in model.rows:
            binding = by_route.get(row.route_id)
            if binding is None:
                errors.append(
                    f"route {row.route_id!r} has no supplied binding"
                )
                continue
            if row.status == "ready":
                if binding.compiler_status != "compiled" or binding.task is None:
                    errors.append(
                        f"ready route {row.route_id!r} binding is not compiled"
                    )
                    continue
                expected = _compile_ready_row(
                    plan=plan_model,
                    binding=binding,
                    cells=row.cells,
                    stage=ArticleStage(row.proposal.stage.value),
                    hypothesis_ids=tuple(row.proposal.hypothesis_ids),
                    run_id=row.request.run_id,
                    branch_id=row.request.branch_id,
                    authority=authority or _validate_authority(),
                    available_budget=None,
                )
                expected_row, _ = expected
                if (
                    expected_row.status != "ready"
                    or expected_row.request.model_dump(
                        exclude={"compiler_attestation"}, mode="json"
                    )
                    != row.request.model_dump(
                        exclude={"compiler_attestation"}, mode="json"
                    )
                ):
                    errors.append(
                        f"ready route {row.route_id!r} does not match "
                        "deterministic recompilation"
                    )
            elif row.status == "not_run":
                if binding.compiler_status == "compiled":
                    errors.append(
                        f"route {row.route_id!r} is not_run despite a compiled "
                        "binding"
                    )
    return not errors


def _validate_authority() -> ArticleCompilationAuthority:
    return ArticleCompilationAuthority(b"validator-only")


class QwenArticleExperimentPlanner:
    """Concrete qwen3.7-flash planning provider; no fallback model."""

    def __init__(
        self,
        *,
        client: Optional[QwenFlashOnlyClient] = None,
        prompt_path: str | Path = DEFAULT_PLANNING_PROMPT,
        maximum_tokens: int = 4000,
    ) -> None:
        self.client = client or QwenFlashOnlyClient(
            agent_name="ArticleExperimentPlanner"
        )
        self.prompt_path = Path(prompt_path)
        self.maximum_tokens = max(512, int(maximum_tokens))

    def __call__(
        self,
        request_table: Mapping[str, Any],
        *,
        force_mock: bool | None = None,
    ) -> PlanningProviderResult:
        system_prompt = self.prompt_path.read_text(encoding="utf-8")
        raw = self.client.call(
            [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        dict(request_table),
                        ensure_ascii=False,
                    ),
                },
            ],
            max_tokens=self.maximum_tokens,
            force_mock=force_mock,
        )
        content = str(raw.get("content") or "")
        response = _safe_json(content)
        usage = dict(raw.get("_llm_usage") or {})
        return PlanningProviderResult(
            response=response,
            usage=usage,
            provider_model=MODEL_NAME,
            mock_llm=bool(usage.get("mock_llm")),
        )


def _safe_json(text: str) -> Dict[str, Any]:
    candidate = str(text or "").strip()
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start >= 0 and end > start:
        candidate = candidate[start : end + 1]
    try:
        value = json.loads(candidate)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


__all__ = [
    "ArticleExperimentPlanningResult",
    "DEFAULT_AVAILABLE_STAGES",
    "PlannedRouteResult",
    "PlanningProvider",
    "PlanningProviderResult",
    "PlanningRowCells",
    "QwenArticleExperimentPlanner",
    "RouteTaskBinding",
    "compute_experiment_planning_result_id",
    "plan_article_experiments",
    "validate_experiment_planning_result",
]
