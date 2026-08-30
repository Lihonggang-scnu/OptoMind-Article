from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import pytest

from optomind_optics.harness.article_architecture import ArtifactDescriptor
from optomind_optics.harness.article_assets import (
    ArticleAssetCompilationResult,
    VerifiedCandidateRecord,
    compute_asset_compilation_result_id,
)
from optomind_optics.harness.article_contracts import (
    ExperimentStatus,
    ObservationCard,
)
from optomind_optics.harness.article_director import ArticleDirector
from optomind_optics.harness.article_execution import (
    ArticleExecutionResult,
)
from optomind_optics.harness.article_experiment_planning import (
    ArticleExperimentPlanningResult,
    PlanningProviderResult,
    RouteTaskBinding,
    compute_experiment_planning_result_id,
    plan_article_experiments,
    validate_experiment_planning_result,
)
from optomind_optics.harness.article_pipeline import (
    PIPELINE_STAGE_ORDER,
    ArticlePipeline,
    ArticlePipelineRequest,
    ArticlePipelineResult,
    PipelineConfigurationError,
    build_default_pipeline,
    compute_pipeline_result_id,
)
from optomind_optics.harness.article_writing import TrustedValueRecord
from optomind_optics.harness.article_proposals import (
    ArticleCompilationAuthority,
    CompiledExperimentRequest,
    compute_optical_design_task_digest,
)
from optomind_optics.harness.dev_fixtures import build_dev_optical_design_task
from optomind_optics.harness.method_research import (
    MethodResearchReport,
    MethodResearchStatus,
)
from optomind_optics.harness.problem_analyzer import (
    OpticalProblemAnalysis,
    ProblemAnalysisResult,
    ResearchIntent,
    TMMCompatibility,
)
from optomind_optics.harness.strategy_planner import (
    DesignRoute,
    StrategyPlan,
    StrategyPlanningResult,
)


QUESTION = (
    "Design a broadband one-dimensional antireflection coating for fused "
    "silica in air over 450-700 nm."
)


def _analysis() -> OpticalProblemAnalysis:
    return OpticalProblemAnalysis(
        problem_id="problem-1",
        original_request=QUESTION,
        normalized_request_english=QUESTION,
        primary_intent=ResearchIntent.design,
        compatibility=TMMCompatibility.compatible,
        compatibility_reason="planar multilayer stack within the TMM domain",
        needs_method_research=True,
        wavelengths_nm=[(450.0, 700.0)],
        target_observables=["mean reflectance"],
    )


def _analysis_result(
    *, incompatible: bool = False,
) -> ProblemAnalysisResult:
    analysis = _analysis()
    if incompatible:
        analysis = analysis.model_copy(
            update={
                "compatibility": TMMCompatibility.incompatible,
                "compatibility_reason": (
                    "non-planar geometry is outside the planar TMM domain"
                ),
            }
        )
    return ProblemAnalysisResult(
        analysis=analysis,
        status="analyzed",
        attempts=1,
    )


def _report() -> MethodResearchReport:
    return MethodResearchReport(
        problem_id="problem-1",
        status=MethodResearchStatus.completed,
    )


def _route(route_id: str = "route_01") -> DesignRoute:
    return DesignRoute(
        route_id=route_id,
        title="Broadband AR over 450-700 nm",
        route_kind="optimize_existing_stack",
        scientific_hypothesis=(
            "A four-layer alternating-index stack can suppress reflection "
            "across the visible band."
        ),
        design_principle="Alternating high/low index layers.",
        proposed_materials=("MgF2", "SiO2"),
        proposed_topology="exactly four finite layers from the incident side",
        design_variables=("thickness_1", "thickness_2"),
        soft_objectives=("mean reflectance",),
        manufacturing_considerations=("minimum layer thickness",),
        execution_request_english=(
            "Run TMM optimization for the four-layer AR stack."
        ),
        priority=1,
    )


def _strategy_result() -> StrategyPlanningResult:
    return StrategyPlanningResult(
        status="planned",
        plan=StrategyPlan(
            problem_id="problem-1",
            planning_summary="One broadband AR route.",
            routes=(_route("route_01"), _route("route_02")),
            stop_if_all_routes_fail=(
                "Stop and report if the single route cannot produce a "
                "physically valid candidate."
            ),
        ),
        attempts=1,
    )


def _director_result() -> Any:
    return ArticleDirector().plan(
        QUESTION, _analysis(), _report(), force_mock=True
    )


def _validation_authority() -> ArticleCompilationAuthority:
    """Authority identity the public planning validator recompiles with."""

    return ArticleCompilationAuthority(b"validator-only")


def _binding(
    route_id: str = "route_01",
    *,
    compiler_status: str = "compiled",
) -> RouteTaskBinding:
    route = _route(route_id)
    task = (
        build_dev_optical_design_task("DEV02")
        if compiler_status == "compiled"
        else None
    )
    return RouteTaskBinding(
        route_id=route.route_id,
        route=route,
        compiler_status=compiler_status,
        task=task,
        task_digest=(
            compute_optical_design_task_digest(task)
            if compiler_status == "compiled"
            else ""
        ),
    )


def _both_compiled_bindings() -> list[RouteTaskBinding]:
    task = build_dev_optical_design_task("DEV02")
    digest = compute_optical_design_task_digest(task)
    return [
        RouteTaskBinding(
            route_id=_route("route_01").route_id,
            route=_route("route_01"),
            compiler_status="compiled",
            task=task,
            task_digest=digest,
        ),
        RouteTaskBinding(
            route_id=_route("route_02").route_id,
            route=_route("route_02"),
            compiler_status="compiled",
            task=task,
            task_digest=digest,
        ),
    ]


def _hyp_aliases(plan: Any) -> tuple[str, ...]:
    ordered = sorted(plan.hypotheses, key=lambda item: item.hypothesis_id)
    return tuple(f"H{index + 1:02d}" for index in range(len(ordered)))


def _row_cells(route_alias: str, hypothesis_alias: str) -> dict[str, Any]:
    return {
        "route_alias": route_alias,
        "hypothesis_aliases": [hypothesis_alias],
        "stage": "baseline_experiments",
        "atomic_change": {"variable": "thickness_layer_3", "delta_nm": 2.0},
        "expected_discriminator": {"metric": "R_mean", "direction": "lower"},
        "rationale": "Test the mechanism.",
        "uncertainty": "Solver tolerance only.",
    }


class FakePlanner:
    def __init__(self, *responses: dict[str, Any]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request_table: Any) -> PlanningProviderResult:
        self.requests.append(dict(request_table))
        response = self.responses.pop(0) if self.responses else {"rows": []}
        return PlanningProviderResult(
            response=response,
            usage={"estimated_input_tokens": 7, "estimated_output_tokens": 9},
            provider_model="fake-planner",
        )


def _production_planning(
    director_plan: Any,
    bindings: Sequence[RouteTaskBinding],
    *,
    run_id: str = "run-pipeline-1",
    branch_id: str = "root",
    ready_route_ids: Sequence[str] = ("route_01",),
) -> ArticleExperimentPlanningResult:
    alias_for = {
        binding.route_id: f"R{index:02d}"
        for index, binding in enumerate(
            sorted(
                bindings,
                key=lambda item: (item.route.priority, item.route_id),
            ),
            1,
        )
    }
    hypothesis_alias = _hyp_aliases(director_plan)[0]
    rows = [
        _row_cells(alias_for[route_id], hypothesis_alias)
        for route_id in ready_route_ids
    ]
    provider = FakePlanner({"rows": rows})
    return plan_article_experiments(
        director_plan,
        bindings,
        run_id=run_id,
        branch_id=branch_id,
        authority=_validation_authority(),
        provider=provider,
    )


def _production_planning_two(director_plan: Any) -> ArticleExperimentPlanningResult:
    return _production_planning(
        director_plan,
        _both_compiled_bindings(),
        ready_route_ids=("route_01", "route_02"),
    )


def _execution_result(
    request: CompiledExperimentRequest, run_dir: Path
) -> ArticleExecutionResult:
    observation = ObservationCard(
        observation_id="observation-1",
        experiment_id=request.experiment.experiment_id,
        status=ExperimentStatus.physically_valid,
        metrics={"run_status": "completed"},
        artifact_ids=[],
        failure_records=[],
        failure_diagnosis={},
        summary="completed",
    )
    return ArticleExecutionResult(
        request_id=request.request_id,
        task_hash=request.task_hash,
        run_dir=str(run_dir),
        observation=observation,
        receipt={"status": "adapter_completed"},
        outcome=observation.status.value,
    )


def _asset_result(
    request: CompiledExperimentRequest,
    execution: ArticleExecutionResult,
    *,
    status: str = "ready",
) -> ArticleAssetCompilationResult:
    source_experiment_id = str(
        request.parameters.get("experiment_id")
        or request.experiment.experiment_id
    )
    descriptors: list[ArtifactDescriptor] = []
    trusted_values: list[TrustedValueRecord] = []
    candidates: list[VerifiedCandidateRecord] = []
    if status in {"ready", "partial"}:
        objective_sha = "11" * 32
        certificate_sha = "22" * 32
        simulation_sha = "33" * 32
        objective = ArtifactDescriptor(
            artifact_id="artifact-obj",
            path="experiments/exp-1/OBJECTIVE_REPORT.json",
            fields=["aggregate_soft_score"],
            artifact_type="objective_report",
            media_type="application/json",
            content_summary="verified objective report",
            sha256=objective_sha,
            source_experiment_ids=["exp-1"],
            source_observation_ids=[execution.observation.observation_id],
        )
        certificate = ArtifactDescriptor(
            artifact_id="artifact-cert",
            path=(
                "experiments/exp-1/c/c1/"
                "PHYSICS_ACCEPTANCE_CERTIFICATE.json"
            ),
            fields=["accepted"],
            artifact_type="physics_acceptance_certificate",
            media_type="application/json",
            content_summary="verified certificate",
            sha256=certificate_sha,
            source_experiment_ids=["exp-1"],
            source_observation_ids=[execution.observation.observation_id],
        )
        simulation = ArtifactDescriptor(
            artifact_id="artifact-sim",
            path="experiments/exp-1/c/c1/SIMULATION_RESULT.json",
            fields=["wavelengths_nm"],
            artifact_type="simulation_result",
            media_type="application/json",
            content_summary="verified spectrum",
            sha256=simulation_sha,
            source_experiment_ids=["exp-1"],
            source_observation_ids=[execution.observation.observation_id],
        )
        descriptors = [objective, certificate, simulation]
        trusted_values = [
            TrustedValueRecord(
                artifact_id="artifact-obj",
                field="aggregate_soft_score",
                rendered_value="0.5",
                source_hash=objective_sha,
                derivation="fake aggregate soft score",
                label="Aggregate soft score",
                prose_safe=True,
            )
        ]
        candidates = [
            VerifiedCandidateRecord(
                candidate_id="candidate-1",
                experiment_id=source_experiment_id,
                role_keys=[],
                is_pareto=False,
                is_baseline=False,
                physics_status="physically_valid",
                certificate_id="cd" * 32,
                certificate_artifact_id="artifact-cert",
                objective_artifact_id="artifact-obj",
                simulation_artifact_id="artifact-sim",
                artifact_ids=["artifact-cert", "artifact-obj", "artifact-sim"],
                target_score=0.5,
            )
        ]
    model = ArticleAssetCompilationResult(
        status=status,
        result_id="",
        request_id=request.request_id,
        task_hash=request.task_hash,
        task_digest=request.task_digest,
        run_id=request.run_id,
        experiment_id=source_experiment_id,
        observation_id=execution.observation.observation_id,
        observation=execution.observation,
        validation_errors=(
            ("asset compilation was invalid",)
            if status == "invalid"
            else ()
        ),
        warnings=("partial coverage",) if status == "partial" else (),
        descriptors=descriptors,
        trusted_values=trusted_values,
        candidates=candidates,
    )
    return model.model_copy(
        update={"result_id": compute_asset_compilation_result_id(model)}
    )


def _happy_stack(
    tmp_path: Path,
) -> Tuple[ArticlePipeline, Dict[str, List[Any]], CompiledExperimentRequest]:
    analysis = _analysis_result()
    report = _report()
    strategy = _strategy_result()
    director = _director_result()
    compiled_binding = _binding("route_01")
    not_run_binding = _binding("route_02", compiler_status="not_run")
    planning = _production_planning(
        director.plan,
        [compiled_binding, not_run_binding],
    )
    request_model = next(
        row.request
        for row in planning.rows
        if row.status == "ready"
    )
    run_dir = tmp_path / "run"
    calls: Dict[str, List[Any]] = {"execute": [], "compile": []}

    def analyze(question: str, force_mock: bool | None) -> Any:
        return analysis

    def research(
        problem_analysis: Any, force_mock: bool | None
    ) -> Any:
        return report

    def plan_strategy(
        problem_analysis: Any, method_research: Any, force_mock: bool | None
    ) -> Any:
        return strategy

    def direct(
        question: str,
        problem_analysis: Any,
        method_research: Any,
        prior_observations: Any,
        force_mock: bool | None,
    ) -> Any:
        return director

    def bind_routes(strategy_plan: Any, director_plan: Any) -> Any:
        return [compiled_binding, not_run_binding]

    def plan_experiments(
        bindings: Any, director_plan: Any, force_mock: bool | None
    ) -> Any:
        return planning

    def execute(compiled_request: CompiledExperimentRequest) -> Any:
        calls["execute"].append(compiled_request)
        return _execution_result(compiled_request, run_dir)

    def compile_assets(
        compiled_request: CompiledExperimentRequest,
        execution_result: Any,
        run_root: Any,
    ) -> Any:
        calls["compile"].append(
            (compiled_request, execution_result, run_root)
        )
        return _asset_result(compiled_request, execution_result)

    pipeline = build_default_pipeline(
        analyze=analyze,
        research=research,
        plan_strategy=plan_strategy,
        director=direct,
        bind_routes=bind_routes,
        plan_experiments=plan_experiments,
        execute=execute,
        compile_assets=compile_assets,
    )
    return pipeline, calls, request_model


def _pipeline_with(
    tmp_path: Path,
    **overrides: Any,
) -> Tuple[ArticlePipeline, Dict[str, List[Any]], CompiledExperimentRequest]:
    pipeline, calls, request_model = _happy_stack(tmp_path)
    adapters = dict(
        analyze=pipeline.analyze,
        research=pipeline.research,
        plan_strategy=pipeline.plan_strategy,
        direct=pipeline.direct,
        bind_routes=pipeline.bind_routes,
        plan_experiments=pipeline.plan_experiments,
        execute=pipeline.execute,
        compile_assets=pipeline.compile_assets,
    )
    adapters.update(overrides)
    return ArticlePipeline(**adapters), calls, request_model


def _request(
    work_dir: str, *, maximum_routes: int = 4
) -> ArticlePipelineRequest:
    return ArticlePipelineRequest(
        question=QUESTION,
        run_id="run-pipeline-1",
        branch_id="root",
        work_dir=work_dir,
        force_mock=True,
        maximum_routes=maximum_routes,
    )


def _receipts_by_stage(
    result: ArticlePipelineResult,
) -> Dict[str, Any]:
    return {receipt.stage: receipt for receipt in result.receipts}


def test_happy_path_receipts_snapshots_and_deterministic_events(
    tmp_path: Path,
) -> None:
    pipeline, calls, _ = _happy_stack(tmp_path)
    work = tmp_path / "work"
    result = pipeline.run(_request(str(work)))
    assert result.status == "completed"
    assert [receipt.stage for receipt in result.receipts] == list(
        PIPELINE_STAGE_ORDER
    )
    assert all(receipt.status == "completed" for receipt in result.receipts)
    digests = [receipt.payload_digest for receipt in result.receipts]
    assert len(set(digests)) == len(PIPELINE_STAGE_ORDER)
    for name in (
        "REQUEST.json",
        "PIPELINE_EVENTS.jsonl",
        "FINAL_PIPELINE_RESULT.json",
    ):
        assert (work / name).is_file()
    for sequence, stage in enumerate(PIPELINE_STAGE_ORDER, start=1):
        assert (work / f"{sequence:02d}-{stage}.json").is_file()
    events = [
        json.loads(line)
        for line in (work / "PIPELINE_EVENTS.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert [event["stage"] for event in events] == list(PIPELINE_STAGE_ORDER)
    assert len(calls["execute"]) == 1
    assert len(calls["compile"]) == 1
    assert result.execution_count == 1
    assert len(result.asset_compilations) == 1
    assert len(result.route_task_bindings) == 2
    assert result.route_task_bindings[0].route_id == "route_01"
    assert result.route_task_bindings[0].compiler_status == "compiled"
    assert result.route_task_bindings[1].route_id == "route_02"
    assert result.route_task_bindings[1].compiler_status == "not_run"


def test_required_failure_skips_downstream_execution(tmp_path: Path) -> None:
    pipeline, calls, _ = _happy_stack(tmp_path)

    def bad_strategy(
        problem_analysis: Any, method_research: Any, force_mock: bool | None
    ) -> Any:
        raise RuntimeError("provider unavailable")

    failing = ArticlePipeline(
        analyze=pipeline.analyze,
        research=pipeline.research,
        plan_strategy=bad_strategy,
        direct=pipeline.direct,
        bind_routes=pipeline.bind_routes,
        plan_experiments=pipeline.plan_experiments,
        execute=pipeline.execute,
        compile_assets=pipeline.compile_assets,
    )
    result = failing.run(_request(str(tmp_path / "work2")))
    assert result.status == "partial"
    by_stage = _receipts_by_stage(result)
    assert by_stage["strategy_planning"].status == "failed"
    assert by_stage["route_task_binding"].status == "skipped"
    assert by_stage["experiment_planning"].status == "skipped"
    assert by_stage["execution"].status == "skipped"
    assert by_stage["asset_compilation"].status == "skipped"
    assert calls["execute"] == []


def test_unavailable_research_preserves_analysis_and_skips_rest(
    tmp_path: Path,
) -> None:
    pipeline, _, _ = _happy_stack(tmp_path)
    unavailable = MethodResearchReport(
        problem_id="problem-1",
        status=MethodResearchStatus.unavailable,
        reasons=("no evidence available",),
    )
    degraded = ArticlePipeline(
        analyze=pipeline.analyze,
        research=lambda problem, force_mock: unavailable,
        plan_strategy=pipeline.plan_strategy,
        direct=pipeline.direct,
        bind_routes=pipeline.bind_routes,
        plan_experiments=pipeline.plan_experiments,
        execute=pipeline.execute,
        compile_assets=pipeline.compile_assets,
    )
    result = degraded.run(_request(str(tmp_path / "work3")))
    assert result.status == "partial"
    assert result.problem_analysis is not None
    assert result.problem_analysis.status == "analyzed"
    by_stage = _receipts_by_stage(result)
    assert by_stage["problem_analysis"].status == "completed"
    assert by_stage["method_research"].status == "unavailable"
    assert by_stage["strategy_planning"].status == "skipped"
    assert by_stage["execution"].status == "skipped"
    assert result.execution_count == 0


def test_only_ready_rows_execute(tmp_path: Path) -> None:
    pipeline, calls, _ = _happy_stack(tmp_path)
    planning = _production_planning(
        _director_result().plan,
        [
            _binding("route_01"),
            _binding("route_02", compiler_status="not_run"),
        ],
    )
    with_not_run = ArticlePipeline(
        analyze=pipeline.analyze,
        research=pipeline.research,
        plan_strategy=pipeline.plan_strategy,
        direct=pipeline.direct,
        bind_routes=pipeline.bind_routes,
        plan_experiments=lambda bindings, director_plan, force_mock: planning,
        execute=pipeline.execute,
        compile_assets=pipeline.compile_assets,
    )
    result = with_not_run.run(_request(str(tmp_path / "work4")))
    assert result.status == "completed"
    assert len(calls["execute"]) == 1
    assert result.execution_count == 1
    execution_receipt = _receipts_by_stage(result)["execution"]
    assert any("route_02" in warning for warning in execution_receipt.warnings)


def test_execution_identity_mismatch_not_sent_to_assets(
    tmp_path: Path,
) -> None:
    pipeline, _, _ = _happy_stack(tmp_path)
    calls: Dict[str, List[Any]] = {"compile": []}

    def bad_execute(compiled_request: CompiledExperimentRequest) -> Any:
        execution = _execution_result(compiled_request, tmp_path / "run")
        return execution.model_copy(update={"request_id": "other-request"})

    def compile_assets(
        compiled_request: CompiledExperimentRequest,
        execution_result: Any,
        run_root: Any,
    ) -> Any:
        calls["compile"].append(execution_result)
        return _asset_result(compiled_request, execution_result)

    failing = ArticlePipeline(
        analyze=pipeline.analyze,
        research=pipeline.research,
        plan_strategy=pipeline.plan_strategy,
        direct=pipeline.direct,
        bind_routes=pipeline.bind_routes,
        plan_experiments=pipeline.plan_experiments,
        execute=bad_execute,
        compile_assets=compile_assets,
    )
    result = failing.run(_request(str(tmp_path / "work5")))
    assert result.status == "failed"
    assert _receipts_by_stage(result)["execution"].status == "failed"
    assert _receipts_by_stage(result)["asset_compilation"].status == "skipped"
    assert calls["compile"] == []
    assert any("identity" in error for error in result.validation_errors)


def test_invalid_asset_retained_but_not_trusted(tmp_path: Path) -> None:
    pipeline, _, request_model = _happy_stack(tmp_path)
    execution = _execution_result(request_model, tmp_path / "run")
    invalid = _asset_result(
        request_model, execution, status="invalid"
    )
    with_invalid = ArticlePipeline(
        analyze=pipeline.analyze,
        research=pipeline.research,
        plan_strategy=pipeline.plan_strategy,
        direct=pipeline.direct,
        bind_routes=pipeline.bind_routes,
        plan_experiments=pipeline.plan_experiments,
        execute=pipeline.execute,
        compile_assets=lambda req, er, run_root: invalid,
    )
    result = with_invalid.run(_request(str(tmp_path / "work6")))
    assert result.status == "failed"
    assert len(result.asset_compilations) == 1
    assert result.asset_compilations[0].status == "invalid"
    assert _receipts_by_stage(result)["asset_compilation"].status == "failed"
    assert result.execution_count == 1


def test_result_id_stable_across_empty_dirs(tmp_path: Path) -> None:
    pipeline, _, _ = _happy_stack(tmp_path)
    first = pipeline.run(_request(str(tmp_path / "a")))
    second = pipeline.run(_request(str(tmp_path / "b")))
    assert first.result_id == second.result_id
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.result_id == compute_pipeline_result_id(first)


def test_non_empty_work_dir_not_overwritten(tmp_path: Path) -> None:
    pipeline, _, _ = _happy_stack(tmp_path)
    work = tmp_path / "occupied"
    work.mkdir()
    (work / "existing.txt").write_text("keep", encoding="utf-8")
    result = pipeline.run(_request(str(work)))
    assert result.status == "failed"
    assert any("not empty" in error for error in result.validation_errors)
    assert (work / "existing.txt").read_text(encoding="utf-8") == "keep"
    assert not (work / "REQUEST.json").exists()
    assert not (work / "FINAL_PIPELINE_RESULT.json").exists()


def test_integration_real_models_with_fake_compile_assets(
    tmp_path: Path,
) -> None:
    analysis = _analysis_result()
    report = _report()
    strategy = _strategy_result()
    assert strategy.status == "planned" and strategy.plan is not None
    director = _director_result()
    assert director.status == "planned" and director.plan is not None

    def analyze(question: str, force_mock: bool | None) -> Any:
        return analysis

    def research(
        problem_analysis: Any, force_mock: bool | None
    ) -> Any:
        return report

    def plan_strategy(
        problem_analysis: Any, method_research: Any, force_mock: bool | None
    ) -> Any:
        return strategy

    def direct(
        question: str,
        problem_analysis: Any,
        method_research: Any,
        prior_observations: Any,
        force_mock: bool | None,
    ) -> Any:
        return director

    def bind_routes(strategy_plan: Any, director_plan: Any) -> Any:
        route = strategy_plan.routes[0]
        task = build_dev_optical_design_task("DEV02")
        return [
            RouteTaskBinding(
                route_id=route.route_id,
                route=route,
                compiler_status="compiled",
                task=task,
                task_digest=compute_optical_design_task_digest(task),
            )
        ]

    def plan_experiments(
        bindings: Any, director_plan: Any, force_mock: bool | None
    ) -> Any:
        return _production_planning(
            director_plan, bindings, ready_route_ids=("route_01",)
        )

    def execute(compiled_request: CompiledExperimentRequest) -> Any:
        return _execution_result(compiled_request, tmp_path / "run")

    def compile_assets(
        compiled_request: CompiledExperimentRequest,
        execution_result: Any,
        run_root: Any,
    ) -> Any:
        assert compiled_request.request_id == execution_result.request_id
        assert Path(run_root) == Path(execution_result.run_dir)
        return _asset_result(
            compiled_request, execution_result, status="partial"
        )

    pipeline = build_default_pipeline(
        analyze=analyze,
        research=research,
        plan_strategy=plan_strategy,
        director=direct,
        bind_routes=bind_routes,
        plan_experiments=plan_experiments,
        execute=execute,
        compile_assets=compile_assets,
    )
    result = pipeline.run(_request(str(tmp_path / "work7")))
    assert result.status == "partial"
    assert result.execution_count == 1
    assert len(result.asset_compilations) == 1
    assert result.asset_compilations[0].status == "partial"
    assert result.director_plan is not None
    assert result.strategy_plan is not None
    assert result.method_research is not None
    assert result.problem_analysis is not None


def test_invalid_request_rejected_without_side_effects(tmp_path: Path) -> None:
    pipeline, _, _ = _happy_stack(tmp_path)
    result = pipeline.run(
        {
            "question": "",
            "run_id": "run-x",
            "branch_id": "root",
            "work_dir": str(tmp_path / "unused"),
        }
    )
    assert result.status == "failed"
    assert any("request is invalid" in error for error in result.validation_errors)


def test_build_default_pipeline_requires_all_adapters() -> None:
    with pytest.raises(PipelineConfigurationError):
        build_default_pipeline(
            analyze=lambda question, force_mock: None,
            research=lambda problem, force_mock: None,
            plan_strategy=lambda a, b, c: None,
            director=lambda *args: None,
            bind_routes=lambda a, b: [],
            plan_experiments=lambda a, b, c: None,
            execute=lambda request: None,
            compile_assets=None,
        )


def test_unavailable_asset_keeps_pipeline_partial(tmp_path: Path) -> None:
    pipeline, _, request_model = _happy_stack(tmp_path)
    execution = _execution_result(request_model, tmp_path / "run")
    unavailable = _asset_result(
        request_model, execution, status="unavailable"
    )
    with_unavailable = ArticlePipeline(
        analyze=pipeline.analyze,
        research=pipeline.research,
        plan_strategy=pipeline.plan_strategy,
        direct=pipeline.direct,
        bind_routes=pipeline.bind_routes,
        plan_experiments=pipeline.plan_experiments,
        execute=pipeline.execute,
        compile_assets=lambda req, er, run_root: unavailable,
    )
    result = with_unavailable.run(_request(str(tmp_path / "work8")))
    assert result.status == "partial"
    assert result.status != "completed"
    asset_receipt = _receipts_by_stage(result)["asset_compilation"]
    assert asset_receipt.status == "partial"
    assert any(
        "unavailable" in warning for warning in asset_receipt.warnings
    )
    assert len(result.asset_compilations) == 1
    assert result.asset_compilations[0].status == "unavailable"


def test_all_execution_rows_producing_no_usable_assets_not_completed(
    tmp_path: Path,
) -> None:
    pipeline, _, _ = _happy_stack(tmp_path)
    planning = _production_planning_two(_director_result().plan)
    second_request = planning.rows[1].request
    executions: Dict[str, Any] = {}

    def execute(compiled_request: CompiledExperimentRequest) -> Any:
        execution = _execution_result(
            compiled_request, tmp_path / "run"
        )
        executions[compiled_request.request_id] = execution
        return execution

    def compile_assets(
        compiled_request: CompiledExperimentRequest,
        execution_result: Any,
        run_root: Any,
    ) -> Any:
        return _asset_result(
            compiled_request,
            execution_result,
            status="unavailable",
        )

    pipeline_two = ArticlePipeline(
        analyze=pipeline.analyze,
        research=pipeline.research,
        plan_strategy=pipeline.plan_strategy,
        direct=pipeline.direct,
        bind_routes=lambda strategy_plan, director_plan: _both_compiled_bindings(),
        plan_experiments=lambda bindings, director_plan, force_mock: planning,
        execute=execute,
        compile_assets=compile_assets,
    )
    result = pipeline_two.run(_request(str(tmp_path / "work9")))
    assert result.status == "partial"
    assert result.status != "completed"
    assert result.execution_count == 2
    assert len(result.asset_compilations) == 2
    asset_receipt = _receipts_by_stage(result)["asset_compilation"]
    assert asset_receipt.status == "partial"
    assert sum(
        "unavailable" in warning for warning in asset_receipt.warnings
    ) == 2


def test_maximum_routes_enforced_at_planning_boundary(
    tmp_path: Path,
) -> None:
    pipeline, calls, _ = _happy_stack(tmp_path)
    planning = _production_planning_two(_director_result().plan)
    too_many = ArticlePipeline(
        analyze=pipeline.analyze,
        research=pipeline.research,
        plan_strategy=pipeline.plan_strategy,
        direct=pipeline.direct,
        bind_routes=lambda strategy_plan, director_plan: [
            _binding("route_01")
        ],
        plan_experiments=lambda bindings, director_plan, force_mock: planning,
        execute=pipeline.execute,
        compile_assets=pipeline.compile_assets,
    )
    result = too_many.run(
        _request(
            str(tmp_path / "work10"),
            maximum_routes=1,
        )
    )
    assert result.status == "failed"
    assert _receipts_by_stage(result)["experiment_planning"].status == "failed"
    assert _receipts_by_stage(result)["execution"].status == "skipped"
    assert _receipts_by_stage(result)["asset_compilation"].status == "skipped"
    assert calls["execute"] == []
    assert any(
        "maximum_routes" in error for error in result.validation_errors
    )


def test_not_run_bindings_do_not_count_against_maximum_routes(
    tmp_path: Path,
) -> None:
    pipeline, calls, _ = _happy_stack(tmp_path)
    bindings = [
        _binding("route_01"),
        _binding("route_02", compiler_status="not_run"),
    ]
    planning = _production_planning(_director_result().plan, bindings)
    with_not_run = ArticlePipeline(
        analyze=pipeline.analyze,
        research=pipeline.research,
        plan_strategy=pipeline.plan_strategy,
        direct=pipeline.direct,
        bind_routes=lambda strategy_plan, director_plan: bindings,
        plan_experiments=lambda b, d, force_mock: planning,
        execute=pipeline.execute,
        compile_assets=pipeline.compile_assets,
    )
    result = with_not_run.run(
        _request(str(tmp_path / "work-max-not-run"), maximum_routes=1)
    )
    assert result.status in {"completed", "partial"}
    assert result.execution_count == 1
    assert _receipts_by_stage(result)["route_task_binding"].status == "completed"
    assert _receipts_by_stage(result)["experiment_planning"].status in {
        "completed",
        "partial",
    }
    assert [binding.route_id for binding in result.route_task_bindings] == [
        "route_01",
        "route_02",
    ]
    assert len(calls["execute"]) == 1


def test_extra_non_not_run_bindings_still_hard_fail(tmp_path: Path) -> None:
    pipeline, calls, _ = _happy_stack(tmp_path)
    bindings = [
        _binding("route_01"),
        _binding("route_02"),
    ]
    too_many = ArticlePipeline(
        analyze=pipeline.analyze,
        research=pipeline.research,
        plan_strategy=pipeline.plan_strategy,
        direct=pipeline.direct,
        bind_routes=lambda strategy_plan, director_plan: bindings,
        plan_experiments=pipeline.plan_experiments,
        execute=pipeline.execute,
        compile_assets=pipeline.compile_assets,
    )
    result = too_many.run(
        _request(str(tmp_path / "work-max-extra"), maximum_routes=1)
    )
    assert result.status == "failed"
    assert _receipts_by_stage(result)["route_task_binding"].status == "failed"
    assert _receipts_by_stage(result)["execution"].status == "skipped"
    assert calls["execute"] == []
    assert any(
        "maximum_routes" in error for error in result.validation_errors
    )


def test_more_attempts_than_maximum_routes_allowed_when_compiled_count_bounded(
    tmp_path: Path,
) -> None:
    pipeline, calls, _ = _happy_stack(tmp_path)
    strategy = StrategyPlanningResult(
        status="planned",
        plan=StrategyPlan(
            problem_id="problem-1",
            planning_summary="Four broadband AR routes.",
            routes=tuple(_route(f"route_{index:02d}") for index in range(1, 5)),
            stop_if_all_routes_fail=(
                "Stop and report if no route can produce a physically "
                "valid candidate."
            ),
        ),
        attempts=1,
    )
    director = _director_result()
    bindings = [
        _binding("route_01", compiler_status="failed"),
        _binding("route_02", compiler_status="unavailable"),
        _binding("route_03"),
        _binding("route_04"),
    ]
    planning = _production_planning(
        director.plan,
        bindings,
        ready_route_ids=("route_03", "route_04"),
    )
    mixed = ArticlePipeline(
        analyze=pipeline.analyze,
        research=pipeline.research,
        plan_strategy=lambda pa, mr, force_mock: strategy,
        direct=pipeline.direct,
        bind_routes=lambda sp, dp: bindings,
        plan_experiments=lambda b, dp, force_mock: planning,
        execute=pipeline.execute,
        compile_assets=pipeline.compile_assets,
    )

    result = mixed.run(
        _request(str(tmp_path / "work-max-mixed"), maximum_routes=2)
    )

    assert result.status in {"completed", "partial"}
    assert _receipts_by_stage(result)["route_task_binding"].status == "completed"
    assert len(result.route_task_bindings) == 4
    by_id = {
        binding.route_id: binding for binding in result.route_task_bindings
    }
    assert by_id["route_01"].compiler_status == "failed"
    assert by_id["route_02"].compiler_status == "unavailable"
    assert by_id["route_03"].compiler_status == "compiled"
    assert by_id["route_04"].compiler_status == "compiled"
    assert result.execution_count == 2
    assert len(calls["execute"]) == 2
    assert len(calls["compile"]) == 2


def test_all_unavailable_route_binding_retains_audit_rows_in_result_and_resume(
    tmp_path: Path,
) -> None:
    pipeline, calls, _ = _happy_stack(tmp_path)
    bindings = [
        _binding("route_01", compiler_status="failed"),
        _binding("route_02", compiler_status="unavailable"),
    ]
    unavailable = ArticlePipeline(
        analyze=pipeline.analyze,
        research=pipeline.research,
        plan_strategy=pipeline.plan_strategy,
        direct=pipeline.direct,
        bind_routes=lambda sp, dp: bindings,
        plan_experiments=pipeline.plan_experiments,
        execute=pipeline.execute,
        compile_assets=pipeline.compile_assets,
    )
    work = str(tmp_path / "work-max-unavailable")

    result = unavailable.run(_request(work, maximum_routes=2))

    assert result.status in {"partial", "unavailable"}
    assert _receipts_by_stage(result)["route_task_binding"].status == "unavailable"
    assert [binding.compiler_status for binding in result.route_task_bindings] == [
        "failed",
        "unavailable",
    ]
    assert calls["execute"] == []
    resumed = unavailable.resume(_request(work, maximum_routes=2))
    assert resumed == result
    assert [
        binding.compiler_status for binding in resumed.route_task_bindings
    ] == ["failed", "unavailable"]


def test_final_result_retains_route_task_bindings(tmp_path: Path) -> None:
    pipeline, _, _ = _happy_stack(tmp_path)
    result = pipeline.run(_request(str(tmp_path / "work11")))
    assert len(result.route_task_bindings) == 2
    binding = result.route_task_bindings[0]
    assert binding.route_id == "route_01"
    assert binding.compiler_status == "compiled"
    assert binding.task is not None
    snapshot = json.loads(
        (tmp_path / "work11" / "05-route_task_binding.json").read_text(
            encoding="utf-8"
        )
    )
    assert snapshot[0]["route_id"] == "route_01"


def test_capability_incompatible_blocks_downstream(tmp_path: Path) -> None:
    analysis = _analysis_result(incompatible=True)
    calls: Dict[str, List[Any]] = {
        "research": [],
        "strategy": [],
        "direct": [],
        "execute": [],
    }

    def analyze(question: str, force_mock: bool | None) -> Any:
        return analysis

    def research(
        problem_analysis: Any, force_mock: bool | None
    ) -> Any:
        calls["research"].append(problem_analysis)
        return _report()

    def plan_strategy(
        problem_analysis: Any, method_research: Any, force_mock: bool | None
    ) -> Any:
        calls["strategy"].append(problem_analysis)
        return _strategy_result()

    def direct(
        question: str,
        problem_analysis: Any,
        method_research: Any,
        prior_observations: Any,
        force_mock: bool | None,
    ) -> Any:
        calls["direct"].append(problem_analysis)
        return _director_result()

    def execute(compiled_request: CompiledExperimentRequest) -> Any:
        calls["execute"].append(compiled_request)
        return None

    bounded = build_default_pipeline(
        analyze=analyze,
        research=research,
        plan_strategy=plan_strategy,
        director=direct,
        bind_routes=lambda strategy_plan, director_plan: [],
        plan_experiments=lambda bindings, director_plan, force_mock: None,
        execute=execute,
        compile_assets=lambda req, er, run_root: None,
    )
    result = bounded.run(_request(str(tmp_path / "work12")))
    assert result.status == "unavailable"
    by_stage = _receipts_by_stage(result)
    assert by_stage["problem_analysis"].status == "unavailable"
    assert any(
        "capability boundary" in error
        for error in by_stage["problem_analysis"].errors
    )
    assert by_stage["method_research"].status == "skipped"
    assert by_stage["strategy_planning"].status == "skipped"
    assert by_stage["article_director"].status == "skipped"
    assert by_stage["execution"].status == "skipped"
    assert calls["research"] == []
    assert calls["strategy"] == []
    assert calls["direct"] == []
    assert calls["execute"] == []


def test_asset_provider_exception_soft_failure_keeps_partial(
    tmp_path: Path,
) -> None:
    pipeline, _, _ = _happy_stack(tmp_path)
    planning = _production_planning_two(_director_result().plan)
    first_request = planning.rows[0].request
    second_request = planning.rows[1].request
    calls: Dict[str, List[Any]] = {"compile": []}

    def execute(compiled_request: CompiledExperimentRequest) -> Any:
        return _execution_result(compiled_request, tmp_path / "run")

    def compile_assets(
        compiled_request: CompiledExperimentRequest,
        execution_result: Any,
        run_root: Any,
    ) -> Any:
        calls["compile"].append(compiled_request.request_id)
        if compiled_request.request_id == first_request.request_id:
            raise RuntimeError("provider boom")
        return _asset_result(compiled_request, execution_result)

    partial_pipeline = ArticlePipeline(
        analyze=pipeline.analyze,
        research=pipeline.research,
        plan_strategy=pipeline.plan_strategy,
        direct=pipeline.direct,
        bind_routes=lambda a, b: _both_compiled_bindings(),
        plan_experiments=lambda b, d, m: planning,
        execute=execute,
        compile_assets=compile_assets,
    )
    result = partial_pipeline.run(_request(str(tmp_path / "work13")))
    assert result.status == "partial"
    assert result.status != "failed"
    assert len(result.asset_compilations) == 1
    assert result.asset_compilations[0].request_id == second_request.request_id
    receipt = _receipts_by_stage(result)["asset_compilation"]
    assert receipt.status == "partial"
    assert any(
        first_request.request_id in warning and "provider boom" in warning
        for warning in receipt.warnings
    )


def test_asset_identity_mismatch_fails_closed(tmp_path: Path) -> None:
    pipeline, _, request_model = _happy_stack(tmp_path)
    execution = _execution_result(request_model, tmp_path / "run")

    cross_wired = _asset_result(
        request_model, execution
    ).model_copy(update={"request_id": "other-request"})
    cross_wired = cross_wired.model_copy(
        update={
            "result_id": compute_asset_compilation_result_id(cross_wired)
        }
    )
    cross_wired_pipeline = ArticlePipeline(
        analyze=pipeline.analyze,
        research=pipeline.research,
        plan_strategy=pipeline.plan_strategy,
        direct=pipeline.direct,
        bind_routes=pipeline.bind_routes,
        plan_experiments=pipeline.plan_experiments,
        execute=pipeline.execute,
        compile_assets=lambda req, er, run_root: cross_wired,
    )
    result = cross_wired_pipeline.run(_request(str(tmp_path / "work14")))
    assert result.status == "failed"
    assert result.asset_compilations == ()
    assert any(
        "asset validation failed" in error
        for error in result.validation_errors
    )

    bad_id = _asset_result(
        request_model, execution
    ).model_copy(update={"result_id": "0" * 64})
    bad_id_pipeline = ArticlePipeline(
        analyze=pipeline.analyze,
        research=pipeline.research,
        plan_strategy=pipeline.plan_strategy,
        direct=pipeline.direct,
        bind_routes=pipeline.bind_routes,
        plan_experiments=pipeline.plan_experiments,
        execute=pipeline.execute,
        compile_assets=lambda req, er, run_root: bad_id,
    )
    result2 = bad_id_pipeline.run(_request(str(tmp_path / "work15")))
    assert result2.status == "failed"
    assert result2.asset_compilations == ()
    assert any("result_id" in error for error in result2.validation_errors)


def test_analysis_question_mismatch_blocks_downstream(tmp_path: Path) -> None:
    tampered = _analysis_result().model_copy(
        update={
            "analysis": _analysis().model_copy(
                update={"original_request": "A different question."}
            )
        }
    )
    pipeline, calls, _ = _pipeline_with(
        tmp_path, analyze=lambda question, force_mock: tampered
    )
    result = pipeline.run(_request(str(tmp_path / "work-id1")))
    assert result.status == "failed"
    assert any(
        "original_request" in error
        for error in result.validation_errors
    )
    assert calls["execute"] == []


def test_method_research_problem_id_mismatch_blocks_downstream(
    tmp_path: Path,
) -> None:
    report = _report().model_copy(update={"problem_id": "problem-other"})
    pipeline, calls, _ = _pipeline_with(
        tmp_path, research=lambda problem_analysis, force_mock: report
    )
    result = pipeline.run(_request(str(tmp_path / "work-id2")))
    assert result.status == "failed"
    assert any(
        "problem_id" in error and "method research" in error
        for error in result.validation_errors
    )
    assert calls["execute"] == []


def test_strategy_problem_id_mismatch_blocks_downstream(
    tmp_path: Path,
) -> None:
    strategy = _strategy_result().model_copy(
        update={
            "plan": _strategy_result().plan.model_copy(
                update={"problem_id": "problem-other"}
            )
        }
    )
    pipeline, calls, _ = _pipeline_with(
        tmp_path,
        plan_strategy=lambda a, b, force_mock: strategy,
    )
    result = pipeline.run(_request(str(tmp_path / "work-id3")))
    assert result.status == "failed"
    assert any(
        "problem_id" in error and "strategy" in error
        for error in result.validation_errors
    )
    assert calls["execute"] == []


@pytest.mark.parametrize(
    "tamper",
    [
        "question",
        "charter_question",
        "capability",
    ],
)
def test_director_identity_mismatch_blocks_downstream(
    tmp_path: Path, tamper: str
) -> None:
    original = _director_result()
    assert original.plan is not None
    if tamper == "question":
        plan = original.plan.model_copy(
            update={"question": "A different question."}
        )
    elif tamper == "charter_question":
        charter = original.plan.charter.model_copy(
            update={"question": "A different question."}
        )
        plan = original.plan.model_copy(update={"charter": charter})
    else:
        capability = original.plan.capability.model_copy(
            update={"status": TMMCompatibility.incompatible}
        )
        plan = original.plan.model_copy(update={"capability": capability})
    director = original.model_copy(update={"plan": plan})
    pipeline, calls, _ = _pipeline_with(
        tmp_path, direct=lambda *args: director
    )
    result = pipeline.run(_request(str(tmp_path / f"work-id4-{tamper}")))
    assert result.status == "failed"
    assert calls["execute"] == []
    assert any(
        "director" in error for error in result.validation_errors
    )


@pytest.mark.parametrize(
    "variant",
    ["unknown_route", "duplicate_route", "route_mismatch"],
)
def test_route_binding_identity_mismatch_blocks_downstream(
    tmp_path: Path, variant: str
) -> None:
    if variant == "unknown_route":
        unknown = _binding("route_99")
        bindings = [unknown]
    elif variant == "duplicate_route":
        bindings = [_binding("route_01"), _binding("route_01")]
    else:
        tampered_route = _route("route_01").model_copy(
            update={"title": "Tampered route title"}
        )
        task = build_dev_optical_design_task("DEV02")
        bindings = [
            RouteTaskBinding(
                route_id="route_01",
                route=tampered_route,
                compiler_status="compiled",
                task=task,
                task_digest=compute_optical_design_task_digest(task),
            )
        ]
    pipeline, calls, _ = _pipeline_with(
        tmp_path, bind_routes=lambda a, b: bindings
    )
    result = pipeline.run(
        _request(str(tmp_path / f"work-id5-{variant}"))
    )
    assert result.status == "failed"
    assert _receipts_by_stage(result)["route_task_binding"].status == "failed"
    assert calls["execute"] == []


@pytest.mark.parametrize(
    "variant",
    ["unknown_row_route", "plan_id_mismatch", "task_digest_mismatch"],
)
def test_planning_identity_mismatch_blocks_downstream(
    tmp_path: Path, variant: str
) -> None:
    base = _production_planning(
        _director_result().plan, [_binding("route_01")]
    )
    assert base.status == "ready" and base.result_id
    if variant == "unknown_row_route":
        row = base.rows[0].model_copy(
            update={"route_id": "route_99"}
        )
        planning = base.model_copy(update={"rows": (row,)})
        planning = planning.model_copy(
            update={
                "result_id": compute_experiment_planning_result_id(planning)
            }
        )
    elif variant == "plan_id_mismatch":
        planning = base.model_copy(
            update={"plan_id": "plan-other"}
        )
        planning = planning.model_copy(
            update={
                "result_id": compute_experiment_planning_result_id(planning)
            }
        )
    else:
        row = base.rows[0].model_copy(
            update={"task_digest": "0" * 64}
        )
        planning = base.model_copy(
            update={
                "rows": (row,)
            }
        )
        planning = planning.model_copy(
            update={
                "result_id": compute_experiment_planning_result_id(planning)
            }
        )
    pipeline, calls, _ = _pipeline_with(
        tmp_path,
        plan_experiments=lambda b, d, force_mock: planning,
    )
    result = pipeline.run(
        _request(str(tmp_path / f"work-id6-{variant}"))
    )
    assert result.status == "failed"
    assert _receipts_by_stage(result)["experiment_planning"].status == "failed"
    assert calls["execute"] == []


def test_bind_routes_malformed_scalar_hard_failure(tmp_path: Path) -> None:
    pipeline, calls, _ = _pipeline_with(
        tmp_path, bind_routes=lambda a, b: 42
    )
    result = pipeline.run(_request(str(tmp_path / "work-malformed")))
    assert result.status == "failed"
    assert _receipts_by_stage(result)["route_task_binding"].status == "failed"
    assert any(
        "non-iterable" in error for error in result.validation_errors
    )
    assert calls["execute"] == []


def test_bind_routes_generator_failure_is_soft(tmp_path: Path) -> None:
    def generate() -> Any:
        yield _binding("route_01")
        raise RuntimeError("generator boom")

    pipeline, calls, _ = _pipeline_with(
        tmp_path, bind_routes=lambda a, b: generate()
    )
    result = pipeline.run(_request(str(tmp_path / "work-generator")))
    assert result.status == "partial"
    assert _receipts_by_stage(result)["route_task_binding"].status == "failed"
    assert any(
        "iteration failed" in error for error in result.validation_errors
    )
    assert calls["execute"] == []


def test_analyzed_without_analysis_blocks_downstream(tmp_path: Path) -> None:
    tampered = ProblemAnalysisResult(
        status="analyzed", analysis=None, attempts=1
    )
    pipeline, calls, _ = _pipeline_with(
        tmp_path, analyze=lambda question, force_mock: tampered
    )
    result = pipeline.run(_request(str(tmp_path / "work-an")))
    assert result.status == "failed"
    assert any(
        "without an analysis payload" in error
        for error in result.validation_errors
    )
    assert calls["execute"] == []


def test_production_shaped_planning_passes_public_validator(
    tmp_path: Path,
) -> None:
    director_plan = _director_result().plan
    bindings = [
        _binding("route_01"),
        _binding("route_02", compiler_status="not_run"),
    ]
    planning = _production_planning(director_plan, bindings)
    assert planning.status == "ready"
    assert planning.result_id
    errors: list[str] = []
    assert validate_experiment_planning_result(
        planning,
        plan=director_plan,
        bindings=bindings,
        errors=errors,
    )
    assert not errors


def test_planning_empty_result_id_fails_closed(tmp_path: Path) -> None:
    base = _production_planning(
        _director_result().plan, [_binding("route_01")]
    )
    planning = base.model_copy(update={"result_id": ""})
    pipeline, calls, _ = _pipeline_with(
        tmp_path,
        plan_experiments=lambda b, d, force_mock: planning,
    )
    result = pipeline.run(_request(str(tmp_path / "work-empty-id")))
    assert result.status == "failed"
    assert _receipts_by_stage(result)["experiment_planning"].status == "failed"
    assert any("result_id" in error for error in result.validation_errors)
    assert calls["execute"] == []


def test_ready_row_missing_proposal_cells_fails_closed(
    tmp_path: Path,
) -> None:
    base = _production_planning(
        _director_result().plan, [_binding("route_01")]
    )
    row = base.rows[0].model_copy(update={"proposal": None, "cells": None})
    planning = base.model_copy(update={"rows": (row,)})
    planning = planning.model_copy(
        update={
            "result_id": compute_experiment_planning_result_id(planning)
        }
    )
    pipeline, calls, _ = _pipeline_with(
        tmp_path,
        plan_experiments=lambda b, d, force_mock: planning,
    )
    result = pipeline.run(_request(str(tmp_path / "work-missing-proposal")))
    assert result.status == "failed"
    assert _receipts_by_stage(result)["experiment_planning"].status == "failed"
    assert any(
        "proposal" in error for error in result.validation_errors
    )
    assert calls["execute"] == []


def test_ready_request_wrong_run_branch_fails_closed(tmp_path: Path) -> None:
    base = _production_planning(
        _director_result().plan, [_binding("route_01")]
    )
    tampered_request = base.rows[0].request.model_copy(
        update={"run_id": "other-run"}
    )
    row = base.rows[0].model_copy(update={"request": tampered_request})
    planning = base.model_copy(update={"rows": (row,)})
    planning = planning.model_copy(
        update={
            "result_id": compute_experiment_planning_result_id(planning)
        }
    )
    pipeline, calls, _ = _pipeline_with(
        tmp_path,
        plan_experiments=lambda b, d, force_mock: planning,
    )
    result = pipeline.run(_request(str(tmp_path / "work-wrong-run")))
    assert result.status == "failed"
    assert _receipts_by_stage(result)["experiment_planning"].status == "failed"
    assert any("run_id" in error for error in result.validation_errors)
    assert calls["execute"] == []


def test_non_ready_row_carrying_request_proposal_fails_closed(
    tmp_path: Path,
) -> None:
    base = _production_planning(
        _director_result().plan, [_binding("route_01")]
    )
    row = base.rows[0].model_copy(update={"status": "not_run"})
    planning = base.model_copy(update={"rows": (row,)})
    planning = planning.model_copy(
        update={
            "result_id": compute_experiment_planning_result_id(planning)
        }
    )
    pipeline, calls, _ = _pipeline_with(
        tmp_path,
        plan_experiments=lambda b, d, force_mock: planning,
    )
    result = pipeline.run(_request(str(tmp_path / "work-non-ready")))
    assert result.status == "failed"
    assert _receipts_by_stage(result)["experiment_planning"].status == "failed"
    assert any(
        "non-ready" in error for error in result.validation_errors
    )
    assert calls["execute"] == []


def test_mixed_asset_soft_and_hard_invalid_keeps_soft_warning(
    tmp_path: Path,
) -> None:
    pipeline, _, _ = _happy_stack(tmp_path)
    planning = _production_planning_two(_director_result().plan)
    first_request = planning.rows[0].request
    second_request = planning.rows[1].request

    def execute(compiled_request: CompiledExperimentRequest) -> Any:
        return _execution_result(compiled_request, tmp_path / "run")

    def compile_assets(
        compiled_request: CompiledExperimentRequest,
        execution_result: Any,
        run_root: Any,
    ) -> Any:
        if compiled_request.request_id == first_request.request_id:
            raise RuntimeError("provider boom")
        return _asset_result(
            compiled_request, execution_result, status="invalid"
        )

    mixed = ArticlePipeline(
        analyze=pipeline.analyze,
        research=pipeline.research,
        plan_strategy=pipeline.plan_strategy,
        direct=pipeline.direct,
        bind_routes=lambda a, b: _both_compiled_bindings(),
        plan_experiments=lambda b, d, force_mock: planning,
        execute=execute,
        compile_assets=compile_assets,
    )
    result = mixed.run(_request(str(tmp_path / "work-mixed-asset")))
    assert result.status == "failed"
    receipt = _receipts_by_stage(result)["asset_compilation"]
    assert receipt.status == "failed"
    assert any(
        first_request.request_id in warning and "provider boom" in warning
        for warning in receipt.warnings
    )
    assert any("invalid" in error for error in receipt.errors)
    assert len(result.asset_compilations) == 1
    assert result.asset_compilations[0].status == "invalid"
    assert result.asset_compilations[0].request_id == second_request.request_id
