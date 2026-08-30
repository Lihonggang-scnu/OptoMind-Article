from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from optomind_optics.harness.article_contracts import ArticleStage
from optomind_optics.harness.article_director import ArticleDirector
from optomind_optics.harness.article_execution import required_action_for_task
from optomind_optics.harness.article_experiment_planning import (
    ArticleExperimentPlanningResult,
    PlanningProviderResult,
    QwenArticleExperimentPlanner,
    RouteTaskBinding,
    compute_experiment_planning_result_id,
    plan_article_experiments,
    validate_experiment_planning_result,
)
from optomind_optics.harness.article_gateway import ArticleToolGateway
from optomind_optics.harness.article_proposals import (
    ArticleCompilationAuthority,
    compute_optical_design_task_digest,
)
from optomind_optics.harness.contracts import ActionType
from optomind_optics.harness.dev_fixtures import build_dev_optical_design_task
from optomind_optics.harness.method_research import (
    MethodResearchReport,
    MethodResearchStatus,
)
from optomind_optics.harness.problem_analyzer import (
    OpticalProblemAnalysis,
    ResearchIntent,
    TMMCompatibility,
)
from optomind_optics.harness.strategy_planner import DesignRoute


def _analysis() -> OpticalProblemAnalysis:
    return OpticalProblemAnalysis(
        problem_id="problem-1",
        original_request="Design a broadband AR coating over 450-700 nm.",
        normalized_request_english=(
            "Design a broadband one-dimensional antireflection coating for "
            "fused silica in air over 450-700 nm."
        ),
        primary_intent=ResearchIntent.design,
        compatibility=TMMCompatibility.compatible,
        compatibility_reason="planar multilayer stack within the TMM domain",
        needs_method_research=True,
        wavelengths_nm=[(450.0, 700.0)],
        target_observables=["mean reflectance"],
    )


def _report() -> MethodResearchReport:
    return MethodResearchReport(
        problem_id="problem-1",
        status=MethodResearchStatus.completed,
    )


def _plan():
    result = ArticleDirector().plan(
        "Design a broadband AR coating over 450-700 nm.",
        _analysis(),
        _report(),
        force_mock=True,
    )
    assert result.status == "planned" and result.plan is not None
    return result.plan


def _authority(key: bytes = b"planning-test-key") -> ArticleCompilationAuthority:
    return ArticleCompilationAuthority(key)


def _design_route(route_id: str, kind: str, priority: int = 1) -> DesignRoute:
    return DesignRoute(
        route_id=route_id,
        title=f"Route {route_id}",
        route_kind=kind,
        scientific_hypothesis="The fixed stack tests the proposed mechanism.",
        design_principle="Alternating index layers for broad bandwidth.",
        proposed_materials=("MgF2", "SiO2"),
        proposed_topology="exactly 4 finite layers from the incident side",
        design_variables=("thickness 1", "thickness 2"),
        soft_objectives=("mean reflectance",),
        manufacturing_considerations=("minimum layer thickness",),
        execution_request_english="Analyze the fixed stack over 450-700 nm.",
        priority=priority,
    )


def _binding(
    route_id: str,
    task: Any,
    *,
    compiler_status: str = "compiled",
) -> RouteTaskBinding:
    digest = (
        compute_optical_design_task_digest(task)
        if compiler_status == "compiled"
        else ""
    )
    kind = (
        "optimize_existing_stack"
        if any(item.mode.value == "optimize" for item in task.experiments)
        else "analyze_known_stack"
    )
    return RouteTaskBinding(
        route_id=route_id,
        route=_design_route(route_id, kind),
        compiler_status=compiler_status,
        task=task if compiler_status == "compiled" else None,
        compiler_usage={} if compiler_status == "compiled" else {"status": "failed"},
        task_digest=digest,
    )


def _row(
    route_alias: str,
    *hypothesis_aliases: str,
    stage: str = "baseline_experiments",
    atomic_change: dict | None = None,
    discriminator: dict | None = None,
    rationale: str = "Test the mechanism.",
    uncertainty: str = "Solver tolerance only.",
) -> dict:
    return {
        "route_alias": route_alias,
        "hypothesis_aliases": list(hypothesis_aliases),
        "stage": stage,
        "atomic_change": atomic_change
        if atomic_change is not None
        else {"variable": "thickness_layer_3", "delta_nm": 2.0},
        "expected_discriminator": discriminator
        if discriminator is not None
        else {"metric": "R_mean", "direction": "lower"},
        "rationale": rationale,
        "uncertainty": uncertainty,
    }


class FakePlanner:
    def __init__(self, *responses: dict) -> None:
        self.responses = list(responses)
        self.requests: list[dict] = []

    def __call__(self, request_table) -> PlanningProviderResult:
        self.requests.append(dict(request_table))
        response = self.responses.pop(0) if self.responses else {"rows": []}
        return PlanningProviderResult(
            response=response,
            usage={"estimated_input_tokens": 7, "estimated_output_tokens": 9},
            provider_model="fake-planner",
        )


def _hyp_aliases(plan) -> tuple[str, str]:
    ordered = sorted(plan.hypotheses, key=lambda item: item.hypothesis_id)
    return tuple(
        f"H{index + 1:02d}"
        for index in range(len(ordered))
    )


def _stack(tmp_path: Path):
    plan = _plan()
    simulate = build_dev_optical_design_task("DEV02")
    optimize = build_dev_optical_design_task("DEV01")
    simulate_binding = _binding("route_sim", simulate)
    optimize_binding = _binding("route_opt", optimize)
    authority = _authority()
    return {
        "plan": plan,
        "simulate": simulate,
        "optimize": optimize,
        "simulate_binding": simulate_binding,
        "optimize_binding": optimize_binding,
        "authority": authority,
    }


def test_valid_multi_route_fill_binds_hmac_requests(tmp_path) -> None:
    ctx = _stack(tmp_path)
    h = _hyp_aliases(ctx["plan"])[0]
    provider = FakePlanner(
        {
            "rows": [
                _row("R01", h),
                _row("R02", h, stage="controlled_improvement"),
            ]
        }
    )
    result = plan_article_experiments(
        ctx["plan"],
        [ctx["simulate_binding"], ctx["optimize_binding"]],
        run_id="run-1",
        branch_id="root",
        authority=ctx["authority"],
        provider=provider,
    )
    assert result.status == "ready"
    ready = [row for row in result.rows if row.status == "ready"]
    assert len(ready) == 2
    by_route = {row.route_id: row for row in ready}
    assert by_route["route_sim"].allowed_action == ActionType.run_solver.value
    assert by_route["route_opt"].allowed_action == ActionType.run_optimizer.value
    gateway = ArticleToolGateway(
        authority=ctx["authority"],
        run_id="run-1",
        branch_id="root",
    )
    for row in ready:
        assert row.request.task_digest == row.task_digest
        assert ctx["authority"].verify(row.request)
        gateway.authorize(row.request)  # must not raise
    assert result.attempts == 1
    assert result.model_name == "fake-planner"
    assert result.usage
    assert compute_experiment_planning_result_id(result) == result.result_id
    errors: list[str] = []
    assert validate_experiment_planning_result(
        result,
        plan=ctx["plan"],
        bindings=[ctx["simulate_binding"], ctx["optimize_binding"]],
        authority=ctx["authority"],
        errors=errors,
    )
    assert errors == []


def test_request_table_has_semantic_aliases_only(tmp_path) -> None:
    ctx = _stack(tmp_path)
    provider = FakePlanner({"rows": []})
    plan_article_experiments(
        ctx["plan"],
        [ctx["simulate_binding"], ctx["optimize_binding"]],
        run_id="run-1",
        branch_id="root",
        authority=ctx["authority"],
        provider=provider,
    )
    request = provider.requests[0]
    assert {route["route_alias"] for route in request["routes"]} == {
        "R01",
        "R02",
    }
    assert "hypothesis_alias" in request["routes"][0]["hypotheses"][0]
    for forbidden in (
        "authority",
        "key",
        "task_hash",
        "requested_budget",
        "parameters",
        "allowed_action",
        "hypothesis_ids",
    ):
        assert forbidden not in request
        assert all(
            forbidden not in route
            for route in request["routes"]
        )


def test_local_action_parameters_and_budget_derivation(tmp_path) -> None:
    ctx = _stack(tmp_path)
    h = _hyp_aliases(ctx["plan"])[0]
    provider = FakePlanner(
        {
            "rows": [
                _row("R01", h),
                _row("R02", h),
            ]
        }
    )
    result = plan_article_experiments(
        ctx["plan"],
        [ctx["simulate_binding"], ctx["optimize_binding"]],
        run_id="run-1",
        branch_id="root",
        authority=ctx["authority"],
        provider=provider,
    )
    sim = next(row for row in result.rows if row.route_id == "route_sim")
    opt = next(row for row in result.rows if row.route_id == "route_opt")
    assert sim.proposal.parameters == {
        "experiment_id": ctx["simulate"].experiments[0].experiment_id,
        "solver": "smatrix",
    }
    assert opt.proposal.parameters["optimizer_id"] == "gradient_thickness"
    assert opt.proposal.parameters["experiment_id"] == (
        ctx["optimize"].experiments[0].experiment_id
    )
    for row, task in ((sim, ctx["simulate"]), (opt, ctx["optimize"])):
        assert row.proposal.requested_budget["wall_time_seconds"] == (
            task.budget.wall_time_seconds
        )
        assert row.proposal.requested_budget["forward_evaluations"] == (
            task.budget.maximum_forward_evaluations
        )
        assert row.proposal.requested_budget["optimizer_runs"] == (
            task.budget.maximum_optimizer_runs
        )
        assert required_action_for_task(task).value == row.allowed_action


def test_duplicate_and_unknown_aliases_preserve_valid_rows(tmp_path) -> None:
    ctx = _stack(tmp_path)
    h = _hyp_aliases(ctx["plan"])[0]
    provider = FakePlanner(
        {
            "rows": [
                _row("R01", h),
                _row("R01", h),
                _row("UNKNOWN", h),
            ]
        }
    )
    result = plan_article_experiments(
        ctx["plan"],
        [ctx["simulate_binding"], ctx["optimize_binding"]],
        run_id="run-1",
        branch_id="root",
        authority=ctx["authority"],
        provider=provider,
    )
    ready = [row for row in result.rows if row.status == "ready"]
    omitted = [row for row in result.rows if row.status == "omitted"]
    assert len(ready) == 1
    assert len(omitted) == 1
    assert ready[0].route_alias == "R01"
    assert result.status == "partial"


def test_repair_preserves_valid_rows_and_merges(tmp_path) -> None:
    ctx = _stack(tmp_path)
    h = _hyp_aliases(ctx["plan"])[0]
    provider = FakePlanner(
        {"rows": [_row("R01", h)]},
        {"rows": [_row("R02", h)]},
    )
    result = plan_article_experiments(
        ctx["plan"],
        [ctx["simulate_binding"], ctx["optimize_binding"]],
        run_id="run-1",
        branch_id="root",
        authority=ctx["authority"],
        provider=provider,
    )
    assert result.attempts == 2
    assert len(provider.requests) == 2
    assert "repair_request" in provider.requests[1]
    by_route = {row.route_id: row for row in result.rows}
    assert by_route["route_sim"].status == "ready"
    assert by_route["route_opt"].status == "ready"
    assert result.status == "ready"


def test_no_surviving_row_returns_unavailable(tmp_path) -> None:
    ctx = _stack(tmp_path)
    provider = FakePlanner({"rows": []}, {"rows": []})
    result = plan_article_experiments(
        ctx["plan"],
        [ctx["simulate_binding"], ctx["optimize_binding"]],
        run_id="run-1",
        branch_id="root",
        authority=ctx["authority"],
        provider=provider,
    )
    assert result.status == "unavailable"
    assert all(
        row.status in {"omitted", "error"} for row in result.rows
    )


def test_provider_exception_is_honest_unavailable(tmp_path) -> None:
    ctx = _stack(tmp_path)

    class RaisingProvider:
        def __call__(self, request_table):
            raise RuntimeError("provider down")

    result = plan_article_experiments(
        ctx["plan"],
        [ctx["simulate_binding"]],
        run_id="run-1",
        branch_id="root",
        authority=ctx["authority"],
        provider=RaisingProvider(),
    )
    assert result.status == "unavailable"
    assert result.rows[0].status == "unavailable"
    assert any("provider down" in item for item in result.rows[0].errors)
    assert result.attempts == 1


def test_task_compiler_failure_is_not_run_row(tmp_path) -> None:
    ctx = _stack(tmp_path)
    failed = _binding(
        "route_failed",
        build_dev_optical_design_task("DEV03"),
        compiler_status="failed",
    )

    class EchoExecutableAlias:
        def __init__(self) -> None:
            self.requests: list[dict] = []

        def __call__(self, request_table) -> PlanningProviderResult:
            self.requests.append(dict(request_table))
            alias = request_table["routes"][0]["route_alias"]
            hypothesis_alias = request_table["routes"][0]["hypotheses"][0][
                "hypothesis_alias"
            ]
            return PlanningProviderResult(
                response={"rows": [_row(alias, hypothesis_alias)]},
                usage={"estimated_input_tokens": 7, "estimated_output_tokens": 9},
                provider_model="fake-planner",
            )

    provider = EchoExecutableAlias()
    result = plan_article_experiments(
        ctx["plan"],
        [ctx["simulate_binding"], failed],
        run_id="run-1",
        branch_id="root",
        authority=ctx["authority"],
        provider=provider,
    )
    by_route = {row.route_id: row for row in result.rows}
    assert by_route["route_failed"].status == "not_run"
    assert by_route["route_failed"].request is None
    assert by_route["route_sim"].status == "ready"
    assert result.status == "ready"


def test_validator_detects_tampered_nested_result(tmp_path) -> None:
    ctx = _stack(tmp_path)
    h = _hyp_aliases(ctx["plan"])[0]
    provider = FakePlanner({"rows": [_row("R01", h)]})
    result = plan_article_experiments(
        ctx["plan"],
        [ctx["simulate_binding"]],
        run_id="run-1",
        branch_id="root",
        authority=ctx["authority"],
        provider=provider,
    )
    row = result.rows[0]
    tampered_request = row.request.model_copy(
        update={
            "parameters": {
                **row.request.parameters,
                "solver": "forged-solver",
            }
        }
    )
    tampered_row = row.model_copy(update={"request": tampered_request})
    tampered = result.model_copy(
        update={
            "rows": tuple(
                tampered_row if item.route_id == row.route_id else item
                for item in result.rows
            )
        }
    )
    tampered = tampered.model_copy(
        update={
            "result_id": compute_experiment_planning_result_id(tampered)
        }
    )
    errors: list[str] = []
    assert not validate_experiment_planning_result(
        tampered,
        plan=ctx["plan"],
        bindings=[ctx["simulate_binding"]],
        authority=ctx["authority"],
        errors=errors,
    )
    assert any("does not match deterministic recompilation" in item for item in errors)
    # Rehashed rationale is also rejected by identity recomputation.
    forged = result.model_copy(
        update={
            "rows": (
                result.rows[0].model_copy(
                    update={"cells": result.rows[0].cells.model_copy(
                        update={"rationale": "forged"}
                    )}
                ),
            )
        }
    )
    forged = forged.model_copy(
        update={"result_id": compute_experiment_planning_result_id(forged)}
    )
    errors = []
    assert not validate_experiment_planning_result(
        forged,
        plan=ctx["plan"],
        bindings=[ctx["simulate_binding"]],
        authority=ctx["authority"],
        errors=errors,
    )
    assert any("does not match deterministic recompilation" in item for item in errors)


def test_result_id_deterministic_and_content_sensitive(tmp_path) -> None:
    ctx = _stack(tmp_path)
    h = _hyp_aliases(ctx["plan"])[0]
    provider_a = FakePlanner({"rows": [_row("R01", h, rationale="A")]})
    provider_b = FakePlanner({"rows": [_row("R01", h, rationale="B")]})
    result_a = plan_article_experiments(
        ctx["plan"],
        [ctx["simulate_binding"]],
        run_id="run-1",
        branch_id="root",
        authority=ctx["authority"],
        provider=provider_a,
    )
    result_b = plan_article_experiments(
        ctx["plan"],
        [ctx["simulate_binding"]],
        run_id="run-1",
        branch_id="root",
        authority=ctx["authority"],
        provider=provider_b,
    )
    assert result_a.result_id != result_b.result_id
    repeated = plan_article_experiments(
        ctx["plan"],
        [ctx["simulate_binding"]],
        run_id="run-1",
        branch_id="root",
        authority=ctx["authority"],
        provider=FakePlanner({"rows": [_row("R01", h, rationale="A")]}),
    )
    assert repeated.result_id == result_a.result_id


def test_under_capped_budget_fails_as_route_error(tmp_path) -> None:
    ctx = _stack(tmp_path)
    h = _hyp_aliases(ctx["plan"])[0]
    task = build_dev_optical_design_task("DEV02")
    oversized = task.model_copy(
        update={"budget": task.budget.model_copy(update={"wall_time_seconds": 100_000.0})}
    )
    binding = _binding("route_big", oversized)
    provider = FakePlanner({"rows": [_row("R01", h)]})
    result = plan_article_experiments(
        ctx["plan"],
        [binding],
        run_id="run-1",
        branch_id="root",
        authority=ctx["authority"],
        provider=provider,
    )
    assert result.rows[0].status == "error"
    assert any("budget cap" in item for item in result.rows[0].errors)


def test_real_adapter_constructor_and_model_lock() -> None:
    planner = QwenArticleExperimentPlanner()
    assert planner.maximum_tokens >= 512
    assert planner.client.model_name == "qwen3.7-flash"
