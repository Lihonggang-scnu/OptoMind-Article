from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from optomind_optics.harness.article_pipeline import (
    ArticlePipelineRequest,
    ArticlePipelineResult,
    StageReceipt,
    compute_pipeline_result_id,
)
from optomind_optics.harness.article_experiment_planning import (
    ArticleExperimentPlanningResult,
    PlannedRouteResult,
    RouteTaskBinding,
)
from optomind_optics.harness.article_pipeline_integration import (
    AUTHORITY_ENVIRONMENT_VARIABLE,
    ArticleIntegrationError,
    ArticleIntegrationOptions,
    build_integration_summary,
    collect_qwen_usage,
    execute_article_pipeline_integration,
    integration_exit_code,
    write_integration_summary,
)
from optomind_optics.harness.article_proposals import (
    compute_optical_design_task_digest,
)
from optomind_optics.harness.dev_fixtures import build_dev_optical_design_task
from optomind_optics.harness.strategy_planner import DesignRoute


QUESTION = "Design a broadband dielectric antireflection coating."


def _result(
    status: str = "completed",
    *,
    receipts: tuple[StageReceipt, ...] = (),
    validation_errors: tuple[str, ...] = (),
) -> ArticlePipelineResult:
    model = ArticlePipelineResult(
        status=status,
        run_id="run-integration",
        question=QUESTION,
        receipts=receipts,
        validation_errors=validation_errors,
        result_id="",
    )
    return model.model_copy(update={"result_id": compute_pipeline_result_id(model)})


def _request(work_dir: Path, *, run_id: str = "run-integration") -> ArticlePipelineRequest:
    return ArticlePipelineRequest(
        question=QUESTION,
        run_id=run_id,
        branch_id="root",
        work_dir=str(work_dir),
        maximum_routes=3,
    )


def _usage_result() -> Any:
    provider = {
        "model_name": "qwen3.7-flash",
        "agent_name": "ProblemAnalyzer",
        "input_tokens": 100,
        "output_tokens": 25,
        "total_tokens": 125,
        "estimated_input_tokens": 9999,
        "estimated_output_tokens": 9999,
        "token_counts_source": "provider",
        "success": True,
        "request_attempt_count": 2,
        "retry_count": 1,
        "api_key_candidate_count": 3,
        "api_key_rotation_count": 1,
        "api_key_masked": "sk-...abcd",
        "api_key_source": "must-not-be-copied",
        "raw_messages": "must-not-be-copied",
    }
    estimated = {
        "model_name": "qwen3.7-flash",
        "estimated_input_tokens": 40,
        "estimated_output_tokens": 10,
        "success": True,
    }
    return SimpleNamespace(
        problem_analysis=SimpleNamespace(usage=[provider]),
        method_research=SimpleNamespace(
            telemetry=SimpleNamespace(usage=[estimated])
        ),
        strategy_plan=SimpleNamespace(usage=(estimated,)),
        director_plan=SimpleNamespace(usage=estimated),
        route_task_bindings=(
            SimpleNamespace(compiler_usage=estimated),
            SimpleNamespace(compiler_usage={}),
        ),
        experiment_planning=SimpleNamespace(usage=(estimated,)),
    )


def test_collect_usage_covers_all_six_model_bearing_stages_and_prefers_provider_tokens() -> None:
    rows = collect_qwen_usage(_usage_result())

    assert [row.stage for row in rows] == [
        "problem_analysis",
        "method_research",
        "strategy_planning",
        "article_director",
        "route_task_binding",
        "experiment_planning",
    ]
    assert rows[0].token_counts_source == "provider"
    assert (rows[0].input_tokens, rows[0].output_tokens) == (100, 25)
    assert rows[0].request_attempt_count == 2
    assert rows[0].retry_count == 1
    assert rows[1].token_counts_source == "estimated"
    assert (rows[1].input_tokens, rows[1].output_tokens) == (40, 10)
    payload = json.dumps([row.model_dump(mode="json") for row in rows])
    assert "api_key_source" not in payload
    assert "raw_messages" not in payload


def test_mock_usage_is_visible_but_non_billable(tmp_path: Path) -> None:
    usage_result = _usage_result()
    usage_result.problem_analysis.usage[0]["mock_llm"] = True
    result = _result().model_copy(
        update={"problem_analysis": usage_result.problem_analysis}
    )
    rows = collect_qwen_usage(result)
    assert rows[0].token_counts_source == "mock_provider"
    assert rows[0].total_tokens == 125
    assert rows[0].estimated_list_price_cost_cny == 0.0

    summary = build_integration_summary(
        result,
        _request(tmp_path / "run"),
        elapsed_seconds=0.0,
        scheduler_snapshot={},
        mode="run",
        review_kb_count=0,
        online_research=False,
    )
    totals = summary["qwen_usage"]["totals"]
    assert totals["mock_call_count"] == 1
    assert totals["billable_total_tokens"] == 0
    assert totals["estimated_list_price_cost_cny"] == 0.0


def test_summary_redacts_authority_and_reports_fail_open_without_hard_failure(
    tmp_path: Path,
) -> None:
    secret = "authority-super-secret-value"
    receipt = StageReceipt(
        sequence=1,
        stage="problem_analysis",
        status="unavailable",
        warnings=(f"provider unavailable secret={secret}",),
        payload_digest="",
    )
    result = _result("unavailable", receipts=(receipt,))
    request = _request(tmp_path / "run")

    summary = build_integration_summary(
        result,
        request,
        elapsed_seconds=1.25,
        scheduler_snapshot={"usage": {"qwen_calls": 0}},
        mode="run",
        review_kb_count=1,
        online_research=False,
        secrets=(secret,),
    )

    serialized = json.dumps(summary)
    assert secret not in serialized
    assert "[REDACTED]" in serialized
    assert summary["provider_fail_open"] is True
    assert summary["provider_fail_open_stages"] == ["problem_analysis"]
    assert summary["availability_fail_open"] is True
    assert summary["hard_failure"] is False
    assert summary["summary_id"]


def test_summary_marks_validation_failure_as_hard(tmp_path: Path) -> None:
    result = _result("failed", validation_errors=("identity mismatch",))
    summary = build_integration_summary(
        result,
        _request(tmp_path / "run"),
        elapsed_seconds=0.0,
        scheduler_snapshot={},
        mode="run",
        review_kb_count=0,
        online_research=False,
    )
    assert summary["hard_failure"] is True
    assert summary["provider_fail_open"] is False
    assert summary["validation_errors"] == ["identity mismatch"]


def test_summary_reports_planned_not_run_binding_and_row_counts(
    tmp_path: Path,
) -> None:
    def route(route_id: str) -> DesignRoute:
        return DesignRoute(
            route_id=route_id,
            title=f"Route {route_id}",
            route_kind="analyze_known_stack",
            scientific_hypothesis="A fixed stack tests the mechanism.",
            design_principle="Alternating index layers.",
            proposed_topology="four finite layers",
            design_variables=("thickness_1", "thickness_2"),
            soft_objectives=("mean reflectance",),
            theory_basis=(
                "Bragg-like interference of alternating index layers.",
            ),
            execution_request_english=(
                "Analyze the fixed stack over 450-700 nm."
            ),
        )

    task = build_dev_optical_design_task("DEV02")
    compiled_01 = RouteTaskBinding(
        route_id="route_01",
        route=route("route_01"),
        compiler_status="compiled",
        task=task,
        task_digest=compute_optical_design_task_digest(task),
        compiler_usage={
            "agent_name": "TMMTaskCompiler",
            "model_name": "qwen3.7-flash",
            "input_tokens": 120,
            "output_tokens": 40,
            "total_tokens": 160,
            "token_counts_source": "provider",
            "success": True,
            "request_attempt_count": 1,
        },
    )
    compiled_02 = RouteTaskBinding(
        route_id="route_02",
        route=route("route_02"),
        compiler_status="compiled",
        task=task,
        task_digest=compute_optical_design_task_digest(task),
        compiler_usage={
            "agent_name": "TMMTaskCompiler",
            "model_name": "qwen3.7-flash",
            "input_tokens": 80,
            "output_tokens": 20,
            "total_tokens": 100,
            "token_counts_source": "provider",
            "success": True,
            "request_attempt_count": 1,
        },
    )
    not_run_03 = RouteTaskBinding(
        route_id="route_03",
        route=route("route_03"),
        compiler_status="not_run",
        compiler_usage={
            "status": "not_run",
            "reason": "planned route not selected: maximum_routes limit 2",
        },
    )
    not_run_04 = RouteTaskBinding(
        route_id="route_04",
        route=route("route_04"),
        compiler_status="not_run",
        compiler_usage={
            "status": "not_run",
            "reason": "planned route not selected: maximum_routes limit 2",
        },
    )
    planning = ArticleExperimentPlanningResult(
        plan_id="plan-integration",
        status="ready",
        rows=(
            PlannedRouteResult(
                route_id="route_01",
                compiler_status="compiled",
                status="ready",
            ),
            PlannedRouteResult(
                route_id="route_02",
                compiler_status="compiled",
                status="ready",
            ),
            PlannedRouteResult(
                route_id="route_03",
                compiler_status="not_run",
                status="not_run",
            ),
            PlannedRouteResult(
                route_id="route_04",
                compiler_status="not_run",
                status="not_run",
            ),
        ),
    )
    result = ArticlePipelineResult(
        status="partial",
        run_id="run-integration",
        question=QUESTION,
        receipts=(),
        route_task_bindings=(
            compiled_01,
            compiled_02,
            not_run_03,
            not_run_04,
        ),
        experiment_planning=planning,
        result_id="",
    )
    result = result.model_copy(
        update={"result_id": compute_pipeline_result_id(result)}
    )
    summary = build_integration_summary(
        result,
        _request(tmp_path / "run"),
        elapsed_seconds=0.0,
        scheduler_snapshot={},
        mode="run",
        review_kb_count=0,
        online_research=False,
    )
    assert summary["route_task_binding_counts"] == {
        "compiled": 2,
        "not_run": 2,
    }
    assert summary["experiment_planning_row_counts"] == {
        "ready": 2,
        "not_run": 2,
    }
    usage_rows = collect_qwen_usage(result)
    assert len(usage_rows) == 2
    assert all(row.stage == "route_task_binding" for row in usage_rows)
    assert {row.input_tokens for row in usage_rows} == {120, 80}
    assert not any(
        row.token_counts_source == "unavailable" for row in usage_rows
    )
    assert not any(row.model_name == "unknown" for row in usage_rows)
    totals = summary["qwen_usage"]["totals"]
    assert totals["logical_call_count"] == 2
    assert totals["request_attempt_count"] == 2
    assert totals["total_tokens"] == 260


def test_collect_qwen_usage_counts_failed_attempt_with_real_usage(
    tmp_path: Path,
) -> None:
    def route(route_id: str) -> DesignRoute:
        return DesignRoute(
            route_id=route_id,
            title=f"Route {route_id}",
            route_kind="analyze_known_stack",
            scientific_hypothesis="A fixed stack tests the mechanism.",
            design_principle="Alternating index layers.",
            proposed_topology="four finite layers",
            theory_basis=(
                "Bragg-like interference of alternating index layers.",
            ),
            execution_request_english=(
                "Analyze the fixed stack over 450-700 nm."
            ),
        )

    task = build_dev_optical_design_task("DEV02")
    compiled = RouteTaskBinding(
        route_id="route_01",
        route=route("route_01"),
        compiler_status="compiled",
        task=task,
        task_digest=compute_optical_design_task_digest(task),
        compiler_usage={
            "input_tokens": 120,
            "output_tokens": 40,
            "total_tokens": 160,
            "token_counts_source": "provider",
            "success": True,
            "request_attempt_count": 1,
        },
    )
    failed = RouteTaskBinding(
        route_id="route_02",
        route=route("route_02"),
        compiler_status="unavailable",
        compiler_usage={
            "input_tokens": 90,
            "output_tokens": 30,
            "total_tokens": 120,
            "token_counts_source": "provider",
            "success": False,
            "request_attempt_count": 3,
        },
    )
    not_run = RouteTaskBinding(
        route_id="route_03",
        route=route("route_03"),
        compiler_status="not_run",
        compiler_usage={
            "status": "not_run",
            "reason": "planned route not selected: maximum_routes limit 2",
        },
    )
    result = ArticlePipelineResult(
        status="partial",
        run_id="run-integration",
        question=QUESTION,
        receipts=(),
        route_task_bindings=(compiled, failed, not_run),
        result_id="",
    )
    result = result.model_copy(
        update={"result_id": compute_pipeline_result_id(result)}
    )
    rows = collect_qwen_usage(result)
    assert len(rows) == 2
    assert {row.request_attempt_count for row in rows} == {1, 3}
    assert {row.success for row in rows} == {True, False}
    assert not any(
        row.token_counts_source == "unavailable" for row in rows
    )


def test_collect_qwen_usage_counts_nested_task_compiler_attempts_once(
    tmp_path: Path,
) -> None:
    def route(route_id: str) -> DesignRoute:
        return DesignRoute(
            route_id=route_id,
            title=f"Route {route_id}",
            route_kind="analyze_known_stack",
            scientific_hypothesis="A fixed stack tests the mechanism.",
            design_principle="Alternating index layers.",
            proposed_topology="four finite layers",
            theory_basis=(
                "Bragg-like interference of alternating index layers.",
            ),
            execution_request_english=(
                "Analyze the fixed stack over 450-700 nm."
            ),
        )

    task = build_dev_optical_design_task("DEV02")
    nested = RouteTaskBinding(
        route_id="route_01",
        route=route("route_01"),
        compiler_status="compiled",
        task=task,
        task_digest=compute_optical_design_task_digest(task),
        compiler_usage={
            "status": "compiled",
            "attempts": 2,
            "rationale": "compiled after one bounded repair",
            "validation_errors": ["draft invalid"],
            "raw_response_sha256": ["a" * 64, "b" * 64],
            "usage": (
                {
                    "input_tokens": 120,
                    "output_tokens": 40,
                    "total_tokens": 160,
                    "token_counts_source": "provider",
                    "success": False,
                    "request_attempt_count": 1,
                },
                {
                    "input_tokens": 80,
                    "output_tokens": 20,
                    "total_tokens": 100,
                    "token_counts_source": "provider",
                    "success": True,
                    "request_attempt_count": 1,
                },
            ),
        },
    )
    legacy = RouteTaskBinding(
        route_id="route_02",
        route=route("route_02"),
        compiler_status="compiled",
        task=task,
        task_digest=compute_optical_design_task_digest(task),
        compiler_usage={
            "input_tokens": 50,
            "output_tokens": 10,
            "total_tokens": 60,
            "token_counts_source": "provider",
            "success": True,
            "request_attempt_count": 1,
        },
    )
    not_run = RouteTaskBinding(
        route_id="route_03",
        route=route("route_03"),
        compiler_status="not_run",
        compiler_usage={
            "status": "not_run",
            "reason": "planned route not selected: maximum_routes limit 2",
        },
    )
    result = ArticlePipelineResult(
        status="partial",
        run_id="run-integration",
        question=QUESTION,
        receipts=(),
        route_task_bindings=(nested, legacy, not_run),
        result_id="",
    )
    result = result.model_copy(
        update={"result_id": compute_pipeline_result_id(result)}
    )
    summary = build_integration_summary(
        result,
        _request(tmp_path / "run"),
        elapsed_seconds=0.0,
        scheduler_snapshot={},
        mode="run",
        review_kb_count=0,
        online_research=False,
    )

    usage_rows = collect_qwen_usage(result)
    assert len(usage_rows) == 3
    assert all(row.stage == "route_task_binding" for row in usage_rows)
    assert {row.input_tokens for row in usage_rows} == {120, 80, 50}
    assert {row.output_tokens for row in usage_rows} == {40, 20, 10}
    assert sum(row.total_tokens for row in usage_rows) == 320
    assert sum(row.request_attempt_count for row in usage_rows) == 3
    totals = summary["qwen_usage"]["totals"]
    assert totals["logical_call_count"] == 3
    assert totals["request_attempt_count"] == 3
    assert totals["input_tokens"] == 250
    assert totals["output_tokens"] == 70
    assert totals["total_tokens"] == 320


def test_summary_write_is_atomic_idempotent_and_rejects_conflicting_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "summary.json"
    first = build_integration_summary(
        _result(),
        _request(tmp_path / "run"),
        elapsed_seconds=1.0,
        scheduler_snapshot={},
        mode="run",
        review_kb_count=0,
        online_research=False,
    )
    assert write_integration_summary(
        path, first, allow_same_run_update=False
    ) == path.resolve()
    assert write_integration_summary(
        path, first, allow_same_run_update=False
    ) == path.resolve()

    updated = build_integration_summary(
        _result(),
        _request(tmp_path / "run"),
        elapsed_seconds=2.0,
        scheduler_snapshot={},
        mode="resume",
        review_kb_count=0,
        online_research=False,
    )
    with pytest.raises(ArticleIntegrationError, match="use resume"):
        write_integration_summary(path, updated, allow_same_run_update=False)
    write_integration_summary(path, updated, allow_same_run_update=True)
    assert json.loads(path.read_text(encoding="utf-8"))["mode"] == "resume"

    other = dict(updated)
    other["run_id"] = "another-run"
    other["summary_id"] = ""
    from optomind_optics.harness import article_pipeline_integration as module

    other["summary_id"] = module._summary_id(other)
    with pytest.raises(ArticleIntegrationError, match="another run"):
        write_integration_summary(path, other, allow_same_run_update=True)


def test_summary_write_rejects_tampered_digest(tmp_path: Path) -> None:
    summary = build_integration_summary(
        _result(),
        _request(tmp_path / "run"),
        elapsed_seconds=0.0,
        scheduler_snapshot={},
        mode="run",
        review_kb_count=0,
        online_research=False,
    )
    summary["pipeline_status"] = "failed"
    with pytest.raises(ArticleIntegrationError, match="summary_id"):
        write_integration_summary(
            tmp_path / "summary.json", summary, allow_same_run_update=False
        )


def test_regressive_resume_preserves_prior_receipts_and_attempt_history(
    tmp_path: Path,
) -> None:
    path = tmp_path / "summary.json"
    receipt = StageReceipt(
        sequence=1,
        stage="problem_analysis",
        status="completed",
        payload_digest="",
    )
    prior = build_integration_summary(
        _result(receipts=(receipt,)),
        _request(tmp_path / "run"),
        elapsed_seconds=3.0,
        scheduler_snapshot={},
        mode="run",
        review_kb_count=0,
        online_research=False,
    )
    write_integration_summary(path, prior, allow_same_run_update=False)

    regressive = build_integration_summary(
        _result("failed", validation_errors=("runtime fingerprint mismatch",)),
        _request(tmp_path / "run"),
        elapsed_seconds=0.1,
        scheduler_snapshot={},
        mode="resume",
        review_kb_count=0,
        online_research=False,
    )
    write_integration_summary(path, regressive, allow_same_run_update=True)
    persisted = json.loads(path.read_text(encoding="utf-8"))

    assert persisted["pipeline_status"] == "completed"
    assert len(persisted["receipts"]) == 1
    assert len(persisted["attempts"]) == 2
    assert persisted["attempts"][-1]["mode"] == "resume"
    assert persisted["attempts"][-1]["hard_failure"] is True


class _Scheduler:
    def snapshot(self) -> dict[str, Any]:
        return {"usage": {"qwen_calls": 0}}


class _Assembly:
    def __init__(self, result: ArticlePipelineResult, calls: list[str]) -> None:
        self.result = result
        self.calls = calls
        self.scheduler = _Scheduler()

    def run(self) -> ArticlePipelineResult:
        self.calls.append("run")
        return self.result

    def resume(self) -> ArticlePipelineResult:
        self.calls.append("resume")
        return self.result


class _Factory:
    instances: list[Any] = []
    result: ArticlePipelineResult
    calls: list[str]

    def __init__(self, *, request: Any, authority: Any, config: Any) -> None:
        self.request = request
        self.authority = authority
        self.config = config
        self.assemble_kwargs: dict[str, Any] = {}
        self.__class__.instances.append(self)

    def assemble(self, **kwargs: Any) -> _Assembly:
        self.assemble_kwargs = kwargs
        return _Assembly(self.__class__.result, self.__class__.calls)


class _Synthesizer:
    def __init__(self, *, force_mock: Any) -> None:
        self.force_mock = force_mock


class _OnlineClient:
    pass


@pytest.mark.parametrize("resume,expected", [(False, "run"), (True, "resume")])
def test_execute_dispatches_run_or_resume_and_never_serializes_authority(
    tmp_path: Path, resume: bool, expected: str
) -> None:
    _Factory.instances = []
    _Factory.calls = []
    _Factory.result = _result()
    kb = tmp_path / "kb.sqlite"
    kb.write_bytes(b"sqlite-placeholder")
    work_dir = tmp_path / ("resume" if resume else "run")
    options = ArticleIntegrationOptions(
        question=QUESTION,
        run_id="run-integration",
        work_dir=str(work_dir),
        execution_root=str(tmp_path / "execution"),
        review_kb_paths=(str(kb),),
        resume=resume,
        online_research=True,
    )
    secret = "do-not-write-this-authority-key"

    execution = execute_article_pipeline_integration(
        options,
        environment={AUTHORITY_ENVIRONMENT_VARIABLE: secret},
        factory_type=_Factory,
        synthesizer_factory=_Synthesizer,
        online_client_factory=_OnlineClient,
        clock=iter((10.0, 12.5)).__next__,
    )

    assert _Factory.calls == [expected]
    assert execution.summary["mode"] == expected
    assert execution.summary["elapsed_seconds"] == 2.5
    assert _Factory.instances[0].config.review_kb_paths == (str(kb.resolve()),)
    assert isinstance(
        _Factory.instances[0].assemble_kwargs["synthesis_callback"], _Synthesizer
    )
    assert isinstance(
        _Factory.instances[0].assemble_kwargs["research_online_client"],
        _OnlineClient,
    )
    serialized = Path(execution.summary_path).read_text(encoding="utf-8")
    assert secret not in serialized


def test_execute_requires_authority_environment_and_existing_kb(tmp_path: Path) -> None:
    options = ArticleIntegrationOptions(
        question=QUESTION,
        run_id="run-integration",
        work_dir=str(tmp_path / "run"),
        execution_root=str(tmp_path / "execution"),
    )
    with pytest.raises(ArticleIntegrationError, match=AUTHORITY_ENVIRONMENT_VARIABLE):
        execute_article_pipeline_integration(options, environment={})

    missing = options.model_copy(
        update={"review_kb_paths": (str(tmp_path / "missing.sqlite"),)}
    )
    with pytest.raises(ArticleIntegrationError, match="does not exist"):
        execute_article_pipeline_integration(
            missing,
            environment={AUTHORITY_ENVIRONMENT_VARIABLE: "test-only-key"},
        )


def test_exit_codes_distinguish_complete_partial_and_failed() -> None:
    assert integration_exit_code("completed") == 0
    assert integration_exit_code("partial") == 2
    assert integration_exit_code("unavailable") == 2
    assert integration_exit_code("failed") == 1


def test_cli_requires_exactly_one_question_source(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_article_pipeline_integration.py"
    spec = importlib.util.spec_from_file_location("article_integration_cli", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    parser = module.build_parser()
    common = [
        "--run-id",
        "r1",
        "--work-dir",
        str(tmp_path / "work"),
        "--execution-root",
        str(tmp_path / "execution"),
    ]
    with pytest.raises(SystemExit):
        parser.parse_args(common)
    with pytest.raises(SystemExit):
        parser.parse_args(common + ["--question", "q", "--question-file", "q.txt"])
