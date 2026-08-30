from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from optomind_optics.harness.article_assets import ArticleAssetCompilationResult
from optomind_optics.harness.article_director import ArticleDirector
from optomind_optics.harness.article_experiment_planning import (
    QwenArticleExperimentPlanner,
    RouteTaskBinding,
    compute_experiment_planning_result_id,
    plan_article_experiments,
)
from optomind_optics.harness.article_feedback import ArticleFeedbackController
from optomind_optics.harness.article_pipeline import ArticlePipelineRequest
from optomind_optics.harness.article_pipeline_factory import (
    PipelineAssemblyIntegrityError,
    ProductionArticlePipelineFactory,
    ProductionAssemblyConfig,
)
from optomind_optics.harness.article_proposals import (
    ArticleCompilationAuthority,
    compute_optical_design_task_digest,
)
from optomind_optics.harness.article_execution import (
    observation_card_from_tmm_result,
)
from optomind_optics.harness.dev_fixtures import build_dev_optical_design_task
from optomind_optics.harness.method_research import (
    MethodResearchReport,
    MethodResearchStatus,
)
from optomind_optics.harness.problem_analyzer import (
    OpticalProblemAnalysis,
    ResearchIntent,
    TMMCompatibility,
    analyze_optical_problem,
)
from optomind_optics.harness.provenance import ArtifactLineageStore
from optomind_optics.harness.strategy_planner import (
    DesignRoute,
    StrategyPlan,
)
from optomind_optics.harness.task_compiler import TaskCompilationResult


QUESTION = (
    "Design a broadband one-dimensional antireflection coating for fused "
    "silica in air over 450-700 nm."
)
KEY = b"factory-test-key"


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


def _report() -> MethodResearchReport:
    return MethodResearchReport(
        problem_id="problem-1",
        status=MethodResearchStatus.completed,
    )


def _route(route_id: str) -> DesignRoute:
    return DesignRoute(
        route_id=route_id,
        title=f"Broadband AR {route_id}",
        route_kind="analyze_known_stack",
        scientific_hypothesis=(
            "A four-layer alternating-index stack suppresses reflection."
        ),
        design_principle="Alternating high/low index layers.",
        proposed_materials=("MgF2", "SiO2"),
        proposed_topology="four finite layers from the incident side",
        design_variables=("thickness_1", "thickness_2"),
        soft_objectives=("mean reflectance",),
        manufacturing_considerations=("minimum layer thickness",),
        theory_basis=(
            "Bragg-like interference of alternating high/low index layers.",
        ),
        execution_request_english=(
            "Analyze the fixed four-layer stack over 450-700 nm."
        ),
        priority=1,
    )


class _FakeClient:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    def call(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 4000,
        force_mock: Optional[bool] = None,
    ) -> Dict[str, Any]:
        return {
            "content": json.dumps(self.payload),
            "_llm_usage": {
                "estimated_input_tokens": 7,
                "estimated_output_tokens": 9,
            },
        }


class _RaisingClient:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def call(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        raise self.error


class _FakeTaskCompiler:
    def __init__(self, tasks: Optional[List[Any]] = None) -> None:
        self.tasks = list(tasks or [build_dev_optical_design_task("DEV02")])
        self.calls: List[str] = []

    def compile(
        self,
        question: str,
        *,
        benchmark: Any = None,
        force_mock: Optional[bool] = None,
    ) -> TaskCompilationResult:
        self.calls.append(question)
        task = self.tasks[0] if len(self.tasks) == 1 else self.tasks.pop(0)
        return TaskCompilationResult(
            status="compiled",
            attempts=1,
            task=task,
            usage=(
                {
                    "estimated_input_tokens": 1,
                    "estimated_output_tokens": 1,
                },
            ),
        )


class _StagedTaskCompiler:
    """Scripted compiler: per-route compiled/failed/unavailable outcomes."""

    def __init__(self, results: Sequence[Any]) -> None:
        self.results = list(results)
        self.calls: List[str] = []

    def compile(
        self,
        question: str,
        *,
        benchmark: Any = None,
        force_mock: Optional[bool] = None,
    ) -> TaskCompilationResult:
        self.calls.append(question)
        spec = self.results.pop(0)
        if spec == "compiled":
            return TaskCompilationResult(
                status="compiled",
                attempts=1,
                task=build_dev_optical_design_task("DEV02"),
                usage=(
                    {
                        "input_tokens": 60,
                        "output_tokens": 20,
                        "total_tokens": 80,
                        "token_counts_source": "provider",
                        "success": True,
                        "request_attempt_count": 1,
                    },
                ),
                raw_response_sha256=("a" * 64,),
            )
        if spec == "failed":
            return TaskCompilationResult(
                status="invalid",
                attempts=2,
                rationale="draft was invalid",
                validation_errors=("experiments.0: invalid",),
                usage=(
                    {
                        "input_tokens": 40,
                        "output_tokens": 10,
                        "total_tokens": 50,
                        "token_counts_source": "provider",
                        "success": False,
                        "request_attempt_count": 1,
                        "api_key_source": "must-not-be-copied",
                    },
                    {
                        "input_tokens": 30,
                        "output_tokens": 12,
                        "total_tokens": 42,
                        "token_counts_source": "provider",
                        "success": False,
                        "request_attempt_count": 1,
                    },
                ),
                raw_response_sha256=("b" * 64, "c" * 64),
            )
        return TaskCompilationResult(
            status="needs_higher_fidelity",
            attempts=1,
            rationale="provider unavailable",
            usage=(
                {
                    "input_tokens": 20,
                    "output_tokens": 5,
                    "total_tokens": 25,
                    "token_counts_source": "provider",
                    "success": False,
                    "request_attempt_count": 1,
                },
            ),
            raw_response_sha256=("d" * 64,),
        )


def _canned_strategy(
    problem_id: str,
    route_ids: tuple[str, ...] = ("route_01",),
) -> StrategyPlan:
    return StrategyPlan(
        problem_id=problem_id,
        planning_summary="One broadband AR route.",
        routes=tuple(_route(route_id) for route_id in route_ids),
        stop_if_all_routes_fail=(
            "Stop and report if no route can produce a physically valid "
            "candidate."
        ),
    )


def _canned_director_plan(analysis: OpticalProblemAnalysis) -> Dict[str, Any]:
    result = ArticleDirector().plan(
        QUESTION, analysis, _report(), force_mock=True
    )
    assert result.status == "planned" and result.plan is not None
    return result.plan.model_dump(mode="json")


def _canned_planner_rows(route_aliases: tuple[str, ...] = ("R01",)) -> Dict[str, Any]:
    rows = []
    for alias in route_aliases:
        rows.append(
            {
                "route_alias": alias,
                "hypothesis_aliases": ["H01"],
                "stage": "baseline_experiments",
                "atomic_change": {
                    "variable": "thickness_layer_3",
                    "delta_nm": 2.0,
                },
                "expected_discriminator": {
                    "metric": "R_mean",
                    "direction": "lower",
                },
                "rationale": "Test the mechanism.",
                "uncertainty": "Solver tolerance only.",
            }
        )
    return {"rows": rows}


def _request(work_dir: str) -> ArticlePipelineRequest:
    return ArticlePipelineRequest(
        question=QUESTION,
        run_id="run-factory-1",
        branch_id="root",
        work_dir=work_dir,
        force_mock=True,
        maximum_routes=4,
    )


def _config(tmp_path: Path) -> ProductionAssemblyConfig:
    return ProductionAssemblyConfig(
        work_root=str(tmp_path / "work"),
    )


def _harness_write_run(
    run_dir: Path,
    run_identity: str,
    task: Any,
    *,
    valid_candidates: int = 1,
) -> Dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    task_payload = task.model_dump(mode="json")
    digest = compute_optical_design_task_digest(task)
    experiment = task.experiments[0]
    experiment_id = experiment.experiment_id
    (run_dir / "TASK.json").write_text(
        json.dumps(task_payload, sort_keys=True), encoding="utf-8"
    )
    (run_dir / "ARTIFACT_PATH_INDEX.json").write_text(
        json.dumps(
            {
                "schema_version": "tmm-artifact-path-index.v1",
                "path_policy": "stable_hashed_directories_for_windows_path_safety",
                "experiments": [
                    {
                        "experiment_id": experiment_id,
                        "physical_directory": f"experiments/{experiment_id}",
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    exp_dir = run_dir / "experiments" / experiment_id
    baseline_dir = exp_dir / "baseline"
    baseline_dir.mkdir(parents=True)
    cert_id = "53da6334fbedc0863ed27ecd5cff7ef7fb1dbd0a23de45377e4b8f88902dd1ef"
    cert_rel = (
        f"experiments/{experiment_id}/baseline/"
        "PHYSICS_ACCEPTANCE_CERTIFICATE.json"
    )
    obj_rel = f"experiments/{experiment_id}/baseline/OBJECTIVE_REPORT.json"
    sim_rel = f"experiments/{experiment_id}/baseline/SIMULATION_RESULT.json"
    portfolio = {
        "schema_version": "tmm-design-portfolio.v1",
        "selection_policy": "multi_objective",
        "candidates": [
            {
                "candidate_id": f"{experiment_id}__baseline",
                "physics_status": "physically_valid",
                "physically_admissible": True,
                "target_score": 0.4,
                "objective_scores": {"canonical_r_500_650_at_least_mean_45_s_1_1": 0.5},
                "robustness_score": None,
                "simplicity_score": 0.7,
                "distinctiveness_score": 0.4,
                "certificate_id": cert_id,
                "artifact_ids": [cert_rel, obj_rel],
                "metadata": {"source": "initial_baseline", "optimizer_id": None},
            }
        ],
        "assessed_candidate_count": 1,
        "maximum_candidates": 8,
        "selected_roles": {
            "simplest_fabrication": f"{experiment_id}__baseline",
        },
        "pareto_candidate_ids": [f"{experiment_id}__baseline"],
        "rejected_candidate_ids": [],
        "omitted_admissible_candidate_ids": [],
        "notes": "factory harness fixture",
    }
    (exp_dir / "DESIGN_PORTFOLIO.json").write_text(
        json.dumps(portfolio, sort_keys=True), encoding="utf-8"
    )
    files = {
        sim_rel: {
            "artifact_type": "simulation_result",
            "data": {
                "wavelengths_nm": [500.0, 575.0, 650.0],
                "channels": {
                    "angle=45|pol=s": {
                        "R": [0.9, 0.92, 0.88],
                        "T": [0.1, 0.08, 0.12],
                    }
                },
            },
        },
        cert_rel: {
            "artifact_type": "physics_acceptance_certificate",
            "data": {
                "schema_version": "physics-acceptance-certificate-v1",
                "certificate_id": cert_id,
                "accepted": True,
                "status": "physically_valid",
                "physics_audit": {
                    "energy_conservation_max_abs_error": 1.11e-16,
                    "minimum_observable": 6.05e-06,
                    "maximum_observable": 0.97,
                    "nonfinite_value_count": 0,
                },
                "spectral_convergence": {
                    "status": "passed",
                    "final_points": 601,
                },
            },
            "inputs": [sim_rel],
        },
        obj_rel: {
            "artifact_type": "objective_report",
            "data": {
                "schema_version": "tmm-objective-report.v1",
                "aggregate_soft_score": 0.4,
                "weighted_directional_loss": 0.21,
                "target_attainment": {
                    "canonical_r_500_650_at_least_mean_45_s_1_1": {
                        "observed": 0.91,
                        "target": 0.9,
                        "constraint": "at_least",
                        "aggregation": "mean",
                        "weight": 1.0,
                        "tolerance": None,
                        "soft_score": 0.5,
                        "role": "soft_scoring_objective",
                    }
                },
                "admission_role": "ranking_only",
            },
            "inputs": [sim_rel],
        },
    }
    for relative, info in files.items():
        target = run_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(info["data"], sort_keys=True), encoding="utf-8"
        )
    final = {
        "schema_version": "tmm-harness-result.v1",
        "run_id": run_identity,
        "task_id": task.task_id,
        "status": "completed",
        "state_stage": "completed",
        "experiment_results": [
            {
                "experiment_id": experiment_id,
                "mode": experiment.mode.value,
                "physically_valid_candidate_count": valid_candidates,
                "candidate_count": 1,
                "baseline_status": "verified",
                "portfolio_artifact_id": (
                    f"experiments/{experiment_id}/DESIGN_PORTFOLIO.json"
                ),
            }
        ],
        "budget": {
            "usage": {
                "forward_evaluations": 10,
                "optimizer_runs": 1,
                "qwen_calls": 0,
                "qwen_input_tokens": 0,
                "qwen_output_tokens": 0,
                "qwen_cost_cny": 0.0,
                "wall_time_seconds": 1.0,
            }
        },
        "stop_decision": {
            "stop": True,
            "reason": "portfolio_complete",
            "return_best_effort": True,
        },
        "wall_seconds": 1.0,
    }
    (run_dir / "FINAL_RESULT.json").write_text(
        json.dumps(final, sort_keys=True), encoding="utf-8"
    )
    store = ArtifactLineageStore(run_dir)
    store.register_file(
        "TASK.json",
        artifact_id="TASK.json",
        artifact_type="task_contract",
        producing_action="validate_task_contract",
        scientific_provenance={"engine": "tmm", "task_sha256": digest},
    )
    store.register_file(
        "ARTIFACT_PATH_INDEX.json",
        artifact_id="ARTIFACT_PATH_INDEX.json",
        artifact_type="artifact_path_index",
        producing_action="map_logical_ids_to_safe_physical_paths",
        input_artifact_ids=["TASK.json"],
    )
    store.register_file(
        "FINAL_RESULT.json",
        artifact_id="FINAL_RESULT.json",
        artifact_type="final_result",
        producing_action="write_harness_result",
        input_artifact_ids=["ARTIFACT_PATH_INDEX.json"],
    )
    store.register_file(
        f"experiments/{experiment_id}/DESIGN_PORTFOLIO.json",
        artifact_id=f"experiments/{experiment_id}/DESIGN_PORTFOLIO.json",
        artifact_type="design_portfolio",
        producing_action="select_portfolio",
        input_artifact_ids=["FINAL_RESULT.json"],
    )
    for relative, info in files.items():
        store.register_file(
            relative,
            artifact_id=relative,
            artifact_type=info["artifact_type"],
            producing_action="materialize_verified_artifact",
            input_artifact_ids=info.get("inputs") or [],
        )
    return final


class _FakeHarness:
    def __init__(self, run_dir: Path, run_identity: str, run: Any) -> None:
        self.run_dir = run_dir
        self.run_identity = run_identity
        self._run = run

    def run(self, task: Any) -> Dict[str, Any]:
        return self._run(task)


def _harness_factory(
    calls: List[Path],
    *,
    valid_candidates: int = 1,
    interrupt_on_call: Optional[int] = None,
):
    def factory(run_dir: Path, run_identity: str) -> _FakeHarness:
        calls.append(Path(run_dir))

        def run(task: Any) -> Dict[str, Any]:
            if (
                interrupt_on_call is not None
                and len(calls) == interrupt_on_call
            ):
                raise KeyboardInterrupt("injected harness interruption")
            return _harness_write_run(
                Path(run_dir),
                run_identity,
                task,
                valid_candidates=valid_candidates,
            )

        return _FakeHarness(Path(run_dir), run_identity, run)

    return factory


def _assembly(
    tmp_path: Path,
    *,
    route_ids: tuple[str, ...] = ("route_01",),
    strategy_client: Any = None,
    task_compiler: Any = None,
    harness_factory: Any = None,
    scheduler: Any = None,
    compile_assets: Any = None,
    planner_client: Any = None,
    maximum_routes: int = 4,
):
    authority = ArticleCompilationAuthority(KEY)
    analysis_client = _FakeClient(_analysis().model_dump(mode="json"))
    analyzed = analyze_optical_problem(
        QUESTION, client=analysis_client, force_mock=None
    )
    assert analyzed.status == "analyzed" and analyzed.analysis is not None
    analysis = analyzed.analysis
    factory = ProductionArticlePipelineFactory(
        request=_request(str(tmp_path / "run")).model_copy(
            update={"maximum_routes": maximum_routes}
        ),
        authority=authority,
        config=_config(tmp_path),
    )
    return factory.assemble(
        problem_analyzer_client=analysis_client,
        strategy_client=strategy_client
        or _FakeClient(
            _canned_strategy(
                analysis.problem_id, route_ids
            ).model_dump(mode="json")
        ),
        director_client=_FakeClient(_canned_director_plan(analysis)),
        task_compiler=task_compiler
        or _FakeTaskCompiler(
            [build_dev_optical_design_task("DEV02") for _ in route_ids]
        ),
        planner_client=planner_client
        or _FakeClient(
            _canned_planner_rows(
                tuple(f"R{index + 1:02d}" for index in range(len(route_ids)))
            )
        ),
        harness_factory=harness_factory
        or _harness_factory([]),
        scheduler=scheduler,
        compile_assets=compile_assets,
    )


def test_maximum_routes_selects_before_compilation_instead_of_failing_late(
    tmp_path: Path,
) -> None:
    route_ids = ("route_04", "route_03", "route_02", "route_01")
    compiler = _FakeTaskCompiler(
        [build_dev_optical_design_task("DEV02") for _ in route_ids]
    )
    harness_calls: List[Path] = []
    assembly = _assembly(
        tmp_path,
        route_ids=route_ids,
        task_compiler=compiler,
        harness_factory=_harness_factory(harness_calls),
        planner_client=_FakeClient(_canned_planner_rows(("R01", "R02"))),
        maximum_routes=2,
    )

    result = assembly.run()

    assert result.status in {"completed", "partial"}
    assert result.strategy_plan is not None
    assert result.strategy_plan.plan is not None
    assert len(result.strategy_plan.plan.routes) == 4
    assert [binding.route_id for binding in result.route_task_bindings] == [
        "route_01",
        "route_02",
        "route_03",
        "route_04",
    ]
    by_id = {
        binding.route_id: binding for binding in result.route_task_bindings
    }
    assert by_id["route_01"].compiler_status == "compiled"
    assert by_id["route_02"].compiler_status == "compiled"
    assert by_id["route_03"].compiler_status == "not_run"
    assert by_id["route_04"].compiler_status == "not_run"
    assert by_id["route_03"].task is None
    assert by_id["route_03"].task_digest == ""
    assert by_id["route_03"].route_id == "route_03"
    assert "maximum_routes" in str(
        by_id["route_03"].compiler_usage.get("reason", "")
    )
    assert len(compiler.calls) == 2
    assert result.execution_count == 2
    planning = result.experiment_planning
    assert planning is not None
    assert sum(1 for row in planning.rows if row.status == "ready") == 2
    assert sum(1 for row in planning.rows if row.status == "not_run") == 2


def test_failed_compilations_do_not_consume_successful_route_quota(
    tmp_path: Path,
) -> None:
    route_ids = ("route_01", "route_02", "route_03", "route_04")
    compiler = _StagedTaskCompiler(
        ["failed", "unavailable", "compiled", "compiled"]
    )
    harness_calls: List[Path] = []
    assembly = _assembly(
        tmp_path,
        route_ids=route_ids,
        task_compiler=compiler,
        harness_factory=_harness_factory(harness_calls),
        planner_client=_FakeClient(_canned_planner_rows(("R03", "R04"))),
        maximum_routes=2,
    )

    result = assembly.run()

    assert result.status in {"completed", "partial"}
    assert [binding.route_id for binding in result.route_task_bindings] == list(
        route_ids
    )
    by_id = {
        binding.route_id: binding for binding in result.route_task_bindings
    }
    assert by_id["route_01"].compiler_status == "failed"
    assert by_id["route_02"].compiler_status == "unavailable"
    assert by_id["route_03"].compiler_status == "compiled"
    assert by_id["route_04"].compiler_status == "compiled"
    assert len(compiler.calls) == 4
    assert result.execution_count == 2
    assert len(harness_calls) == 2
    planning = result.experiment_planning
    assert planning is not None
    assert sum(1 for row in planning.rows if row.status == "ready") == 2
    assert sum(1 for row in planning.rows if row.status == "not_run") == 2
    failed_usage = by_id["route_01"].compiler_usage
    assert failed_usage["status"] == "invalid"
    assert failed_usage["attempts"] == 2
    assert failed_usage["rationale"] == "draft was invalid"
    assert failed_usage["validation_errors"] == ["experiments.0: invalid"]
    assert failed_usage["raw_response_sha256"] == ["b" * 64, "c" * 64]
    assert len(failed_usage["usage"]) == 2
    assert all(
        "api_key_source" not in row for row in failed_usage["usage"]
    )
    _assert_no_key_leakage(tmp_path / "run")


def _assert_no_key_leakage(work_dir: Path) -> None:
    for path in work_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        assert KEY not in data, f"authority key leaked in {path}"


def test_force_mock_assembly_reaches_honest_terminal_status(
    tmp_path: Path,
) -> None:
    harness_calls: List[Path] = []
    assembly = _assembly(
        tmp_path,
        harness_factory=_harness_factory(harness_calls),
    )
    result = assembly.run()
    assert result.status in {"completed", "partial"}
    assert len(result.receipts) == 8
    assert result.execution_count == 1
    assert len(result.asset_compilations) == 1
    assert result.asset_compilations[0].status in {
        "ready",
        "partial",
    }
    compiled_asset = result.asset_compilations[0]
    resolved_task = next(iter(assembly.registry._bindings.values())).task
    assert (
        compiled_asset.observation.experiment_id
        != compiled_asset.experiment_id
    )
    assert compiled_asset.experiment_id == resolved_task.experiments[0].experiment_id
    assert len(harness_calls) == 1
    assert len(assembly.registry._bindings) == 1
    _assert_no_key_leakage(tmp_path / "run")
    final = json.loads(
        (tmp_path / "run" / "FINAL_PIPELINE_RESULT.json").read_text(
            encoding="utf-8"
        )
    )
    assert final["result_id"] == result.result_id


def test_strategy_service_unavailable_preserves_prior_stages(
    tmp_path: Path,
) -> None:
    harness_calls: List[Path] = []
    assembly = _assembly(
        tmp_path,
        strategy_client=_RaisingClient(RuntimeError("429 service unavailable")),
        harness_factory=_harness_factory(harness_calls),
    )
    result = assembly.run()
    assert result.status == "partial"
    by_stage = {receipt.stage: receipt for receipt in result.receipts}
    assert by_stage["problem_analysis"].status == "completed"
    assert by_stage["strategy_planning"].status == "unavailable"
    assert by_stage["execution"].status == "skipped"
    assert harness_calls == []


def test_malformed_planner_output_preserves_prior_stages(
    tmp_path: Path,
) -> None:
    harness_calls: List[Path] = []
    assembly = _assembly(
        tmp_path,
        planner_client=_FakeClient({"rows": [{"route_alias": "R99"}]}),
        harness_factory=_harness_factory(harness_calls),
    )
    result = assembly.run()
    assert result.status == "partial"
    by_stage = {receipt.stage: receipt for receipt in result.receipts}
    assert by_stage["strategy_planning"].status == "completed"
    assert by_stage["experiment_planning"].status in {
        "unavailable",
        "partial",
    }
    assert by_stage["execution"].status == "skipped"
    assert harness_calls == []


def test_missing_local_material_does_not_fabricate_evidence(
    tmp_path: Path,
) -> None:
    assembly = _assembly(tmp_path)
    result = assembly.run()
    assert result.method_research is not None
    assert result.method_research.evidence == []
    assert any(
        "no_accepted_evidence" in reason
        for reason in result.method_research.reasons
    )
    assert result.status == "partial"


def test_budget_exhaustion_fails_closed(tmp_path: Path) -> None:
    from optomind_optics.harness.budget import BudgetLimits, BudgetScheduler

    scheduler = BudgetScheduler(
        BudgetLimits(
            wall_time_seconds=60.0,
            forward_evaluations=1,
            optimizer_runs=1,
            qwen_calls=1,
            qwen_input_tokens=1000,
            qwen_output_tokens=1000,
            qwen_cost_cny=1.0,
        )
    )
    harness_calls: List[Path] = []
    assembly = _assembly(
        tmp_path,
        scheduler=scheduler,
        harness_factory=_harness_factory(harness_calls),
    )
    result = assembly.run()
    assert result.status == "failed"
    assert harness_calls == []
    assert not any(
        asset.status in {"ready", "partial"}
        for asset in result.asset_compilations
    )
    execution_receipt = next(
        item for item in result.receipts if item.stage == "execution"
    )
    assert any("failed" in warning for warning in execution_receipt.warnings)


def test_rejected_physics_cannot_yield_trusted_assets(tmp_path: Path) -> None:
    harness_calls: List[Path] = []
    assembly = _assembly(
        tmp_path,
        harness_factory=_harness_factory(
            harness_calls, valid_candidates=0
        ),
    )
    result = assembly.run()
    assert result.status == "failed"
    assert len(harness_calls) == 1
    assert not any(
        asset.status in {"ready", "partial"}
        for asset in result.asset_compilations
    )


def test_asset_compilation_exception_route_specific_partial(
    tmp_path: Path,
) -> None:
    from optomind_optics.harness.article_assets import compile_article_assets

    harness_calls: List[Path] = []
    state = {"calls": 0}

    def compile_assets(
        compiled_request: Any,
        execution_result: Any,
        run_root: Any,
    ) -> Any:
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("asset compiler boom")
        return compile_article_assets(
            compiled_request,
            execution_result,
            run_root,
            authority=ArticleCompilationAuthority(KEY),
        )

    assembly = _assembly(
        tmp_path,
        route_ids=("route_01", "route_02"),
        task_compiler=_FakeTaskCompiler(
            [
                build_dev_optical_design_task("DEV02"),
                build_dev_optical_design_task("DEV02"),
            ]
        ),
        harness_factory=_harness_factory(harness_calls),
        compile_assets=compile_assets,
    )
    result = assembly.run()
    assert result.status == "partial"
    assert result.execution_count == 2
    assert len(result.asset_compilations) == 1
    receipt = next(
        item for item in result.receipts if item.stage == "asset_compilation"
    )
    assert receipt.status == "partial"
    assert any("asset compiler boom" in warning for warning in receipt.warnings)


def test_interruption_and_resume_reuse_committed_routes(
    tmp_path: Path,
) -> None:
    from optomind_optics.harness.article_assets import compile_article_assets

    harness_calls: List[Path] = []
    state = {"fired": False}

    def compile_assets(
        compiled_request: Any,
        execution_result: Any,
        run_root: Any,
    ) -> Any:
        if not state["fired"]:
            state["fired"] = True
            raise KeyboardInterrupt("injected asset interruption")
        return compile_article_assets(
            compiled_request,
            execution_result,
            run_root,
            authority=ArticleCompilationAuthority(KEY),
        )

    assembly = _assembly(
        tmp_path,
        route_ids=("route_01", "route_02"),
        task_compiler=_FakeTaskCompiler(
            [
                build_dev_optical_design_task("DEV02"),
                build_dev_optical_design_task("DEV02"),
            ]
        ),
        harness_factory=_harness_factory(harness_calls),
        compile_assets=compile_assets,
    )
    try:
        assembly.run()
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("expected an asset interruption")
    assert len(harness_calls) == 2
    result = assembly.resume()
    assert result.status in {"completed", "partial"}
    assert len(harness_calls) == 2
    assert result.execution_count == 2
    assert len(result.asset_compilations) == 2


def test_authority_and_request_identity_mistakes_fail_early(
    tmp_path: Path,
) -> None:
    with pytest.raises(PipelineAssemblyIntegrityError):
        ProductionArticlePipelineFactory(
            request=_request(str(tmp_path / "run")),
            authority="not-an-authority",  # type: ignore[arg-type]
            config=_config(tmp_path),
        )
    with pytest.raises(PipelineAssemblyIntegrityError):
        ProductionArticlePipelineFactory(
            request={
                "question": "",
                "run_id": "",
                "branch_id": "",
                "work_dir": str(tmp_path / "run"),
            },
            authority=ArticleCompilationAuthority(KEY),
            config=_config(tmp_path),
        )


def test_production_configuration_rejects_unbounded_or_shadowed_inputs(
    tmp_path: Path,
) -> None:
    from optomind_optics.harness.budget import BudgetLimits, BudgetScheduler

    authority = ArticleCompilationAuthority(KEY)
    request = _request(str(tmp_path / "run"))
    with pytest.raises(PipelineAssemblyIntegrityError, match="missing"):
        ProductionArticlePipelineFactory(
            request=request,
            authority=authority,
            config={
                "work_root": str(tmp_path / "work"),
                "budget": {"wall_time_seconds": 60.0},
            },
        )

    factory = ProductionArticlePipelineFactory(
        request=request,
        authority=authority,
        config=_config(tmp_path),
    )
    unbounded_scheduler = BudgetScheduler(
        BudgetLimits(
            wall_time_seconds=60.0,
            forward_evaluations=10,
            optimizer_runs=1,
        )
    )
    with pytest.raises(PipelineAssemblyIntegrityError, match="unbounded"):
        factory.assemble(scheduler=unbounded_scheduler)

    shadowed_factory = ProductionArticlePipelineFactory(
        request=request,
        authority=authority,
        config=_config(tmp_path).model_copy(
            update={"research_options": {"online": True}}
        ),
    )
    with pytest.raises(PipelineAssemblyIntegrityError, match="factory-owned"):
        shadowed_factory.assemble()


def test_assembly_never_serializes_authority_key(tmp_path: Path) -> None:
    harness_calls: List[Path] = []
    assembly = _assembly(
        tmp_path,
        harness_factory=_harness_factory(harness_calls),
    )
    result = assembly.run()
    assert result.status in {"completed", "partial"}
    _assert_no_key_leakage(tmp_path / "run")
    assert isinstance(assembly.authority, ArticleCompilationAuthority)


def test_two_assemblies_from_one_factory_are_isolated(
    tmp_path: Path,
) -> None:
    authority = ArticleCompilationAuthority(KEY)
    factory = ProductionArticlePipelineFactory(
        request=_request(str(tmp_path / "run-a")),
        authority=authority,
        config=_config(tmp_path),
    )
    analysis_client = _FakeClient(_analysis().model_dump(mode="json"))
    analyzed = analyze_optical_problem(
        QUESTION, client=analysis_client, force_mock=None
    )
    assert analyzed.status == "analyzed" and analyzed.analysis is not None
    analysis = analyzed.analysis
    injectables = {
        "problem_analyzer_client": analysis_client,
        "strategy_client": _FakeClient(
            _canned_strategy(
                analysis.problem_id, ("route_01",)
            ).model_dump(mode="json")
        ),
        "director_client": _FakeClient(_canned_director_plan(analysis)),
        "task_compiler": _FakeTaskCompiler(
            [build_dev_optical_design_task("DEV02")]
        ),
        "planner_client": _FakeClient(_canned_planner_rows(("R01",))),
    }
    first = factory.assemble(**injectables, harness_factory=_harness_factory([]))
    second = factory.assemble(
        **injectables,
        harness_factory=_harness_factory([]),
    )
    result = first.run()
    assert result.status in {"completed", "partial"}
    assert len(first.registry._bindings) == 1
    assert len(second.registry._bindings) == 0


def test_registration_identity_error_fails_closed(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import optomind_optics.harness.article_pipeline_factory as factory_module

    original = factory_module.plan_article_experiments

    def tampered(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        row = result.rows[0].model_copy(
            update={"task_digest": "0" * 64}
        )
        result = result.model_copy(update={"rows": (row,)})
        return result.model_copy(
            update={
                "result_id": compute_experiment_planning_result_id(result)
            }
        )

    monkeypatch.setattr(factory_module, "plan_article_experiments", tampered)
    harness_calls: List[Path] = []
    assembly = _assembly(
        tmp_path,
        harness_factory=_harness_factory(harness_calls),
    )
    result = assembly.run()
    assert result.status == "failed"
    assert len(assembly.registry._bindings) == 0
    assert harness_calls == []
    assert any(
        "identity mismatch" in error or "route/task/digest" in error
        for error in result.validation_errors
    )


def test_stage7_feedback_accepts_distinct_experiment_identities(
    tmp_path: Path,
) -> None:
    authority = ArticleCompilationAuthority(KEY)
    analysis_client = _FakeClient(_analysis().model_dump(mode="json"))
    analyzed = analyze_optical_problem(
        QUESTION, client=analysis_client, force_mock=None
    )
    assert analyzed.status == "analyzed" and analyzed.analysis is not None
    analysis = analyzed.analysis
    strategy_plan = _canned_strategy(analysis.problem_id, ("route_01",))
    route = strategy_plan.routes[0]
    task = build_dev_optical_design_task("DEV02")
    binding = RouteTaskBinding(
        route_id=route.route_id,
        route=route,
        compiler_status="compiled",
        task=task,
        task_digest=compute_optical_design_task_digest(task),
    )
    director_result = ArticleDirector(
        client=_FakeClient(_canned_director_plan(analysis))
    ).plan(QUESTION, analysis, _report(), force_mock=None)
    assert director_result.status == "planned" and director_result.plan is not None
    planning = plan_article_experiments(
        director_result.plan,
        [binding],
        run_id="run-factory-1",
        branch_id="root",
        authority=authority,
        provider=QwenArticleExperimentPlanner(
            client=_FakeClient(_canned_planner_rows(("R01",)))
        ),
        force_mock=True,
    )
    ready = next(row for row in planning.rows if row.status == "ready")
    request = ready.request
    assert request.parameters.get("experiment_id") != (
        request.experiment.experiment_id
    )
    run_dir = tmp_path / "run"
    final = _harness_write_run(run_dir, "run-factory-1", task)
    observation = observation_card_from_tmm_result(
        final,
        run_dir=run_dir,
        experiment_id=request.experiment.experiment_id,
    )
    feedback = ArticleFeedbackController().update(
        director_result.plan,
        observations=[observation],
        experiment_context=request.experiment,
        run_id="run-factory-1",
    )
    assert not any(
        "experiment_id" in error and "does not match" in error
        for error in feedback.validation_errors
    )
