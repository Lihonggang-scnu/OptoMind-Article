from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from optomind_optics.harness.article_pipeline import (
    ArticlePipeline,
    ArticlePipelineRequest,
    ArticlePipelineResult,
    build_default_pipeline,
)
from optomind_optics.harness.article_pipeline_recovery import (
    LEDGER_FILENAME,
    LOCK_FILENAME,
    ROUTE_PROGRESS_FILENAME,
    PipelineCheckpointRecord,
    RecoveryIntegrityError,
    compute_checkpoint_id,
    load_route_progress,
    validate_recovery_state,
    write_asset_route,
    write_execution_route,
)
import optomind_optics.harness.article_pipeline as pipeline_module
import optomind_optics.harness.article_pipeline_recovery as recovery_module
from optomind_optics.harness.article_runtime import (
    RuntimeLock,
    article_runtime_fingerprint,
)


sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_article_pipeline import (  # noqa: E402
    QUESTION,
    _analysis_result,
    _asset_result,
    _binding,
    _both_compiled_bindings,
    _director_result,
    _execution_result,
    _production_planning,
    _production_planning_two,
    _report,
    _request,
    _strategy_result,
)


def _happy_adapters(
    tmp_path: Path,
    *,
    interrupt_strategy: bool = False,
    interrupt_analyze: bool = False,
    interrupt_execution_route: int | None = None,
    interrupt_asset_route: int | None = None,
    two_routes: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Deterministic adapters with call counters and injectable interrupts."""

    analysis = _analysis_result()
    report = _report()
    strategy = _strategy_result()
    director = _director_result()
    if two_routes:
        planning = _production_planning_two(director.plan)
    else:
        planning = _production_planning(
            director.plan,
            [
                _binding("route_01"),
                _binding("route_02", compiler_status="not_run"),
            ],
        )
    calls: Dict[str, Any] = {
        "analyze": 0,
        "research": 0,
        "strategy": 0,
        "direct": 0,
        "bind": 0,
        "plan": 0,
        "execute": [],
        "compile": [],
    }
    interrupt_fired = {"value": False}

    def analyze(question: str, force_mock: bool | None) -> Any:
        calls["analyze"] += 1
        if interrupt_analyze and calls["analyze"] == 1:
            raise KeyboardInterrupt("injected interruption before stage one")
        return analysis

    def research(
        problem_analysis: Any, force_mock: bool | None
    ) -> Any:
        calls["research"] += 1
        return report

    def plan_strategy(
        problem_analysis: Any, method_research: Any, force_mock: bool | None
    ) -> Any:
        calls["strategy"] += 1
        if interrupt_strategy and calls["strategy"] == 1:
            raise KeyboardInterrupt("injected interruption after stage 2")
        return strategy

    def direct(
        question: str,
        problem_analysis: Any,
        method_research: Any,
        prior_observations: Any,
        force_mock: bool | None,
    ) -> Any:
        calls["direct"] += 1
        return director

    def bind_routes(strategy_plan: Any, director_plan: Any) -> Any:
        calls["bind"] += 1
        if two_routes:
            return _both_compiled_bindings()
        return [
            _binding("route_01"),
            _binding("route_02", compiler_status="not_run"),
        ]

    def plan_experiments(
        bindings: Any, director_plan: Any, force_mock: bool | None
    ) -> Any:
        calls["plan"] += 1
        return planning

    def execute(compiled_request: Any) -> Any:
        if (
            interrupt_execution_route is not None
            and not interrupt_fired["value"]
            and len(calls["execute"]) + 1 == interrupt_execution_route
        ):
            interrupt_fired["value"] = True
            raise KeyboardInterrupt("injected interruption during execution")
        calls["execute"].append(compiled_request.request_id)
        return _execution_result(compiled_request, tmp_path / "run")

    def compile_assets(
        compiled_request: Any,
        execution_result: Any,
        run_root: Any,
    ) -> Any:
        if (
            interrupt_asset_route is not None
            and not interrupt_fired["value"]
            and len(calls["compile"]) + 1 == interrupt_asset_route
        ):
            interrupt_fired["value"] = True
            raise KeyboardInterrupt("injected interruption during assets")
        calls["compile"].append(compiled_request.request_id)
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
    return {"pipeline": pipeline, "calls": calls, "planning": planning}, calls


def _run_until_interrupt(pipeline: ArticlePipeline, request: Any) -> None:
    try:
        pipeline.run(request)
    except KeyboardInterrupt:
        return
    raise AssertionError("expected a KeyboardInterrupt interruption")


def _fault_after(
    monkeypatch: Any,
    module: Any,
    func_name: str,
    predicate: Any,
    *,
    nth: int = 1,
) -> None:
    """Inject KeyboardInterrupt after the nth matching atomic write."""

    original = getattr(module, func_name)
    state = {"count": 0, "fired": False}

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        if not state["fired"] and predicate(*args, **kwargs):
            state["count"] += 1
            if state["count"] == nth:
                state["fired"] = True
                raise KeyboardInterrupt("injected persistence fault")
        return result

    monkeypatch.setattr(module, func_name, wrapper)


def _baseline_result(tmp_path: Path) -> ArticlePipelineResult:
    ctx, _ = _happy_adapters(tmp_path)
    return ctx["pipeline"].run(_request(str(tmp_path / "baseline")))


def test_interrupt_after_stage_two_resume_completes_without_rerun(
    tmp_path: Path,
) -> None:
    ctx, calls = _happy_adapters(
        tmp_path, interrupt_strategy=True
    )
    pipeline = ctx["pipeline"]
    work = tmp_path / "work"
    request = _request(str(work))
    _run_until_interrupt(pipeline, request)
    ledger = json.loads(
        (work / LEDGER_FILENAME).read_text(encoding="utf-8")
    )
    assert len(ledger["committed_checkpoints"]) == 2
    assert not (work / LOCK_FILENAME).exists()
    assert calls["analyze"] == 1
    assert calls["research"] == 1
    assert calls["strategy"] == 1
    assert calls["direct"] == 0
    assert calls["execute"] == []

    result = pipeline.resume(request)
    assert result.status == "completed"
    assert calls["analyze"] == 1
    assert calls["research"] == 1
    assert calls["strategy"] == 2
    assert calls["direct"] == 1
    assert calls["bind"] == 1
    assert calls["plan"] == 1
    assert len(calls["execute"]) == 1
    assert len(calls["compile"]) == 1
    assert (work / "FINAL_PIPELINE_RESULT.json").is_file()

    uninterrupted, _ = _happy_adapters(tmp_path)
    other = tmp_path / "other"
    baseline = uninterrupted["pipeline"].run(_request(str(other)))
    assert result.result_id == baseline.result_id
    assert result.model_dump(mode="json") == baseline.model_dump(mode="json")


def test_interrupt_between_execution_routes_resume_skips_route_one(
    tmp_path: Path,
) -> None:
    ctx, calls = _happy_adapters(
        tmp_path, interrupt_execution_route=2, two_routes=True
    )
    pipeline = ctx["pipeline"]
    work = tmp_path / "work"
    request = _request(str(work))
    _run_until_interrupt(pipeline, request)
    progress = json.loads(
        (work / ROUTE_PROGRESS_FILENAME).read_text(encoding="utf-8")
    )
    assert len(progress["execution"]) == 1
    assert len(calls["execute"]) == 1

    result = pipeline.resume(request)
    assert result.status == "completed"
    assert len(calls["execute"]) == 2
    assert len(calls["compile"]) == 2
    assert result.execution_count == 2
    assert len(result.asset_compilations) == 2


def test_interrupt_between_asset_routes_resume_skips_asset_one(
    tmp_path: Path,
) -> None:
    ctx, calls = _happy_adapters(
        tmp_path, interrupt_asset_route=2, two_routes=True
    )
    pipeline = ctx["pipeline"]
    work = tmp_path / "work"
    request = _request(str(work))
    _run_until_interrupt(pipeline, request)
    progress = json.loads(
        (work / ROUTE_PROGRESS_FILENAME).read_text(encoding="utf-8")
    )
    assert len(progress["asset"]) == 1
    assert len(calls["compile"]) == 1

    result = pipeline.resume(request)
    assert result.status == "completed"
    assert len(calls["compile"]) == 2
    assert len(result.asset_compilations) == 2


def test_crash_after_stage_eight_checkpoint_rebuilds_final_without_adapters(
    tmp_path: Path,
) -> None:
    ctx, calls = _happy_adapters(tmp_path)
    pipeline = ctx["pipeline"]
    work = tmp_path / "work"
    request = _request(str(work))
    completed = pipeline.run(request)
    final_path = work / "FINAL_PIPELINE_RESULT.json"
    assert final_path.is_file()
    final_path.unlink()
    baseline_calls = {
        key: (len(value) if isinstance(value, list) else value)
        for key, value in calls.items()
    }

    rebuilt = pipeline.resume(request)
    assert rebuilt.result_id == completed.result_id
    assert rebuilt.model_dump(mode="json") == completed.model_dump(mode="json")
    assert final_path.is_file()
    for key, value in baseline_calls.items():
        current = (
            len(calls[key]) if isinstance(calls[key], list) else calls[key]
        )
        assert current == value, key


def test_repeated_resume_of_terminal_runs_is_zero_call(
    tmp_path: Path,
) -> None:
    ctx, calls = _happy_adapters(tmp_path)
    pipeline = ctx["pipeline"]
    work = tmp_path / "work"
    request = _request(str(work))
    first = pipeline.run(request)
    baseline = {
        key: (len(value) if isinstance(value, list) else value)
        for key, value in calls.items()
    }
    for _ in range(2):
        resumed = pipeline.resume(request)
        assert resumed.result_id == first.result_id
        assert resumed.model_dump(mode="json") == first.model_dump(mode="json")
        for key, value in baseline.items():
            current = (
                len(calls[key])
                if isinstance(calls[key], list)
                else calls[key]
            )
            assert current == value, key


def test_partial_terminal_run_resume_is_zero_call(tmp_path: Path) -> None:
    from optomind_optics.harness.method_research import (
        MethodResearchReport,
        MethodResearchStatus,
    )

    ctx, calls = _happy_adapters(tmp_path)
    base = ctx["pipeline"]
    unavailable = MethodResearchReport(
        problem_id="problem-1",
        status=MethodResearchStatus.unavailable,
        reasons=("no evidence",),
    )
    pipeline = ArticlePipeline(
        analyze=base.analyze,
        research=lambda problem, force_mock: unavailable,
        plan_strategy=base.plan_strategy,
        direct=base.direct,
        bind_routes=base.bind_routes,
        plan_experiments=base.plan_experiments,
        execute=base.execute,
        compile_assets=base.compile_assets,
    )
    work = tmp_path / "work"
    request = _request(str(work))
    partial = pipeline.run(request)
    assert partial.status == "partial"
    baseline = {
        key: (len(value) if isinstance(value, list) else value)
        for key, value in calls.items()
    }
    resumed = pipeline.resume(request)
    assert resumed.status == "partial"
    assert resumed.result_id == partial.result_id
    assert resumed.model_dump(mode="json") == partial.model_dump(mode="json")
    for key, value in baseline.items():
        current = (
            len(calls[key]) if isinstance(calls[key], list) else calls[key]
        )
        assert current == value, key


def test_failed_terminal_run_resume_is_zero_call(tmp_path: Path) -> None:
    from optomind_optics.harness.article_pipeline import (
        compute_pipeline_result_id,
    )

    ctx, calls = _happy_adapters(tmp_path)
    base = ctx["pipeline"]
    planning = ctx["planning"].model_copy(update={"result_id": ""})

    pipeline = ArticlePipeline(
        analyze=base.analyze,
        research=base.research,
        plan_strategy=base.plan_strategy,
        direct=base.direct,
        bind_routes=base.bind_routes,
        plan_experiments=lambda b, d, force_mock: planning,
        execute=base.execute,
        compile_assets=base.compile_assets,
    )
    work = tmp_path / "work"
    request = _request(str(work))
    failed = pipeline.run(request)
    assert failed.status == "failed"
    baseline = {
        key: (len(value) if isinstance(value, list) else value)
        for key, value in calls.items()
    }
    resumed = pipeline.resume(request)
    assert resumed.status == "failed"
    assert resumed.result_id == failed.result_id
    assert resumed.model_dump(mode="json") == failed.model_dump(mode="json")
    for key, value in baseline.items():
        current = (
            len(calls[key]) if isinstance(calls[key], list) else calls[key]
        )
        assert current == value, key


def test_tampered_request_fails_closed(tmp_path: Path) -> None:
    ctx, _ = _happy_adapters(tmp_path)
    pipeline = ctx["pipeline"]
    work = tmp_path / "work"
    request = _request(str(work))
    pipeline.run(request)
    request_path = work / "REQUEST.json"
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    payload["run_id"] = "other-run"
    request_path.write_text(json.dumps(payload), encoding="utf-8")
    result = pipeline.resume(request)
    assert result.status == "failed"
    assert any(
        "REQUEST.json" in error for error in result.validation_errors
    )


def test_runtime_fingerprint_mismatch_fails_closed(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import optomind_optics.harness.article_pipeline_recovery as recovery

    ctx, _ = _happy_adapters(tmp_path)
    pipeline = ctx["pipeline"]
    work = tmp_path / "work"
    request = _request(str(work))
    pipeline.run(request)
    monkeypatch.setattr(
        recovery,
        "article_runtime_fingerprint",
        lambda: "0" * 64,
    )
    result = pipeline.resume(request)
    assert result.status == "failed"
    assert any(
        "fingerprint" in error for error in result.validation_errors
    )


def test_tampered_checkpoint_chain_fails_closed(tmp_path: Path) -> None:
    ctx, _ = _happy_adapters(tmp_path, interrupt_strategy=True)
    pipeline = ctx["pipeline"]
    work = tmp_path / "work"
    request = _request(str(work))
    _run_until_interrupt(pipeline, request)
    checkpoint = work / "checkpoint-02-method_research.json"
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    payload["checkpoint_id"] = "0" * 64
    checkpoint.write_text(json.dumps(payload), encoding="utf-8")
    result = pipeline.resume(request)
    assert result.status == "failed"
    assert any(
        "checkpoint" in error for error in result.validation_errors
    )


def test_tampered_event_line_fails_closed(tmp_path: Path) -> None:
    ctx, _ = _happy_adapters(tmp_path, interrupt_strategy=True)
    pipeline = ctx["pipeline"]
    work = tmp_path / "work"
    request = _request(str(work))
    _run_until_interrupt(pipeline, request)
    events = work / "PIPELINE_EVENTS.jsonl"
    lines = events.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace('"status": "completed"', '"status": "partial"')
    events.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = pipeline.resume(request)
    assert result.status == "failed"
    assert any("event" in error for error in result.validation_errors)


def test_tampered_snapshot_payload_fails_closed(tmp_path: Path) -> None:
    ctx, _ = _happy_adapters(tmp_path, interrupt_strategy=True)
    pipeline = ctx["pipeline"]
    work = tmp_path / "work"
    request = _request(str(work))
    _run_until_interrupt(pipeline, request)
    snapshot = work / "02-method_research.json"
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    payload["problem_id"] = "problem-tampered"
    snapshot.write_text(json.dumps(payload), encoding="utf-8")
    result = pipeline.resume(request)
    assert result.status == "failed"
    assert any("snapshot" in error for error in result.validation_errors)


def test_tampered_receipt_digest_fails_closed(tmp_path: Path) -> None:
    ctx, _ = _happy_adapters(tmp_path, interrupt_strategy=True)
    pipeline = ctx["pipeline"]
    work = tmp_path / "work"
    request = _request(str(work))
    _run_until_interrupt(pipeline, request)
    checkpoint = work / "checkpoint-01-problem_analysis.json"
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    payload["receipt"]["payload_digest"] = "0" * 64
    checkpoint.write_text(json.dumps(payload), encoding="utf-8")
    result = pipeline.resume(request)
    assert result.status == "failed"
    assert any("checkpoint" in error for error in result.validation_errors)


def test_tampered_execution_route_progress_fails_closed(
    tmp_path: Path,
) -> None:
    ctx, _ = _happy_adapters(
        tmp_path, interrupt_execution_route=2, two_routes=True
    )
    pipeline = ctx["pipeline"]
    work = tmp_path / "work"
    request = _request(str(work))
    _run_until_interrupt(pipeline, request)
    progress_path = work / ROUTE_PROGRESS_FILENAME
    payload = json.loads(progress_path.read_text(encoding="utf-8"))
    payload["execution"][0]["request_id"] = "other-request"
    progress_path.write_text(json.dumps(payload), encoding="utf-8")
    result = pipeline.resume(request)
    assert result.status == "failed"
    assert any("route" in error for error in result.validation_errors)


def test_tampered_asset_route_snapshot_fails_closed(tmp_path: Path) -> None:
    ctx, _ = _happy_adapters(
        tmp_path, interrupt_asset_route=2, two_routes=True
    )
    pipeline = ctx["pipeline"]
    work = tmp_path / "work"
    request = _request(str(work))
    _run_until_interrupt(pipeline, request)
    snapshot = next((work).glob("route-asset-*.json"))
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    payload["request_id"] = "other-request"
    snapshot.write_text(json.dumps(payload), encoding="utf-8")
    result = pipeline.resume(request)
    assert result.status == "failed"
    assert any("route" in error for error in result.validation_errors)


def test_stale_runtime_lock_fails_closed(tmp_path: Path) -> None:
    ctx, _ = _happy_adapters(tmp_path, interrupt_strategy=True)
    pipeline = ctx["pipeline"]
    work = tmp_path / "work"
    request = _request(str(work))
    _run_until_interrupt(pipeline, request)
    lock = RuntimeLock(work / LOCK_FILENAME)
    token = lock.acquire("run-pipeline-1", "root")
    assert lock.is_held(token)
    result = pipeline.resume(request)
    assert result.status == "failed"
    assert any("lock" in error for error in result.validation_errors)
    lock.release(token)


def test_non_empty_directory_without_ledger_is_rejected(
    tmp_path: Path,
) -> None:
    ctx, _ = _happy_adapters(tmp_path)
    pipeline = ctx["pipeline"]
    work = tmp_path / "junk"
    work.mkdir()
    (work / "stray.txt").write_text("x", encoding="utf-8")
    result = pipeline.resume(_request(str(work)))
    assert result.status == "failed"
    assert any(
        "REQUEST.json" in error or "ledger" in error
        for error in result.validation_errors
    )
    fresh = pipeline.run(_request(str(work)))
    assert fresh.status == "failed"
    assert any("not empty" in error for error in fresh.validation_errors)


def test_checkpoint_ids_are_path_and_time_independent(tmp_path: Path) -> None:
    first_ctx, _ = _happy_adapters(tmp_path)
    first = first_ctx["pipeline"].run(_request(str(tmp_path / "a")))
    second_ctx, _ = _happy_adapters(tmp_path)
    second = second_ctx["pipeline"].run(_request(str(tmp_path / "b")))
    assert first.result_id == second.result_id
    ledger_a = json.loads(
        (tmp_path / "a" / LEDGER_FILENAME).read_text(encoding="utf-8")
    )
    ledger_b = json.loads(
        (tmp_path / "b" / LEDGER_FILENAME).read_text(encoding="utf-8")
    )
    assert ledger_a["request_digest"] == ledger_b["request_digest"]
    checkpoint_a = json.loads(
        (tmp_path / "a" / "checkpoint-01-problem_analysis.json").read_text(
            encoding="utf-8"
        )
    )
    checkpoint_b = json.loads(
        (tmp_path / "b" / "checkpoint-01-problem_analysis.json").read_text(
            encoding="utf-8"
        )
    )
    assert checkpoint_a["checkpoint_id"] == checkpoint_b["checkpoint_id"]
    assert checkpoint_a["snapshot_filename"] == checkpoint_b["snapshot_filename"]


def test_recovery_validator_rejects_rehashed_forged_checkpoint(
    tmp_path: Path,
) -> None:
    ctx, _ = _happy_adapters(tmp_path, interrupt_strategy=True)
    pipeline = ctx["pipeline"]
    work = tmp_path / "work"
    request = _request(str(work))
    _run_until_interrupt(pipeline, request)
    checkpoint_path = work / "checkpoint-01-problem_analysis.json"
    payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    payload["receipt"]["warnings"] = ["forged"]
    record = type(
        "Record",
        (),
        {"model_dump": lambda self, mode=None: payload},
    )()
    payload["checkpoint_id"] = compute_checkpoint_id(record)  # type: ignore[arg-type]
    checkpoint_path.write_text(json.dumps(payload), encoding="utf-8")
    errors: List[str] = []
    assert (
        validate_recovery_state(
            work, request, errors, []
        )
        is None
    )
    assert errors


def test_missing_committed_checkpoint_file_fails_closed(
    tmp_path: Path,
) -> None:
    ctx, _ = _happy_adapters(tmp_path, interrupt_strategy=True)
    pipeline = ctx["pipeline"]
    work = tmp_path / "work"
    request = _request(str(work))
    _run_until_interrupt(pipeline, request)
    (work / "checkpoint-02-method_research.json").unlink()
    result = pipeline.resume(request)
    assert result.status == "failed"
    assert any(
        "checkpoint" in error for error in result.validation_errors
    )


def test_stage_snapshot_fault_resume_reruns_stage(
    tmp_path: Path, monkeypatch: Any
) -> None:
    ctx, calls = _happy_adapters(tmp_path)
    pipeline = ctx["pipeline"]
    work = tmp_path / "work"
    request = _request(str(work))
    _fault_after(
        monkeypatch,
        pipeline_module,
        "atomic_write_text",
        lambda path, *a: Path(path).name == "01-problem_analysis.json",
    )
    _run_until_interrupt(pipeline, request)
    assert calls["analyze"] == 1
    result = pipeline.resume(request)
    baseline = _baseline_result(tmp_path)
    assert result.status == "completed"
    assert result.result_id == baseline.result_id
    assert calls["analyze"] == 2


def test_stage_event_fault_resume_reruns_without_duplicate_event(
    tmp_path: Path, monkeypatch: Any
) -> None:
    ctx, calls = _happy_adapters(tmp_path)
    pipeline = ctx["pipeline"]
    work = tmp_path / "work"
    request = _request(str(work))
    _fault_after(
        monkeypatch,
        pipeline_module,
        "atomic_write_text",
        lambda path, *a: Path(path).name == "PIPELINE_EVENTS.jsonl",
    )
    _run_until_interrupt(pipeline, request)
    result = pipeline.resume(request)
    baseline = _baseline_result(tmp_path)
    assert result.status == "completed"
    assert result.result_id == baseline.result_id
    lines = (
        work / "PIPELINE_EVENTS.jsonl"
    ).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 8
    stages = [
        json.loads(line)["stage"] for line in lines if line.strip()
    ]
    assert stages == [
        "problem_analysis",
        "method_research",
        "strategy_planning",
        "article_director",
        "route_task_binding",
        "experiment_planning",
        "execution",
        "asset_compilation",
    ]
    assert calls["analyze"] == 2


def test_stage_checkpoint_fault_resume_promotes_orphan(
    tmp_path: Path, monkeypatch: Any
) -> None:
    ctx, calls = _happy_adapters(tmp_path)
    pipeline = ctx["pipeline"]
    work = tmp_path / "work"
    request = _request(str(work))
    _fault_after(
        monkeypatch,
        recovery_module,
        "atomic_write_json",
        lambda path, *a: Path(path).name.startswith("checkpoint-01-"),
    )
    _run_until_interrupt(pipeline, request)
    assert calls["analyze"] == 1
    result = pipeline.resume(request)
    baseline = _baseline_result(tmp_path)
    assert result.status == "completed"
    assert result.result_id == baseline.result_id
    assert calls["analyze"] == 1


def test_stage_ledger_fault_resume_continues(
    tmp_path: Path, monkeypatch: Any
) -> None:
    ctx, calls = _happy_adapters(tmp_path)
    pipeline = ctx["pipeline"]
    work = tmp_path / "work"
    request = _request(str(work))
    original = recovery_module.write_checkpoint
    state = {"fired": False}

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        if not state["fired"]:
            state["fired"] = True
            raise KeyboardInterrupt("injected persistence fault")
        return result

    monkeypatch.setattr(recovery_module, "write_checkpoint", wrapper)
    _run_until_interrupt(pipeline, request)
    assert calls["analyze"] == 1
    result = pipeline.resume(request)
    baseline = _baseline_result(tmp_path)
    assert result.status == "completed"
    assert result.result_id == baseline.result_id
    assert calls["analyze"] == 1


def test_execution_snapshot_fault_resume_reruns_route(
    tmp_path: Path, monkeypatch: Any
) -> None:
    ctx, calls = _happy_adapters(tmp_path)
    pipeline = ctx["pipeline"]
    work = tmp_path / "work"
    request = _request(str(work))
    _fault_after(
        monkeypatch,
        recovery_module,
        "atomic_write_text",
        lambda path, *a: Path(path).name.startswith("route-execution-"),
    )
    _run_until_interrupt(pipeline, request)
    assert len(calls["execute"]) == 1
    result = pipeline.resume(request)
    baseline = _baseline_result(tmp_path)
    assert result.status == "completed"
    assert result.result_id == baseline.result_id
    assert len(calls["execute"]) == 2


def test_execution_progress_fault_resume_skips_route(
    tmp_path: Path, monkeypatch: Any
) -> None:
    ctx, calls = _happy_adapters(tmp_path)
    pipeline = ctx["pipeline"]
    work = tmp_path / "work"
    request = _request(str(work))
    _fault_after(
        monkeypatch,
        recovery_module,
        "atomic_write_json",
        lambda path, *a: Path(path).name == "ROUTE_PROGRESS.json",
        nth=1,
    )
    _run_until_interrupt(pipeline, request)
    assert len(calls["execute"]) == 1
    result = pipeline.resume(request)
    baseline = _baseline_result(tmp_path)
    assert result.status == "completed"
    assert result.result_id == baseline.result_id
    assert len(calls["execute"]) == 1


def test_asset_snapshot_fault_resume_reruns_asset(
    tmp_path: Path, monkeypatch: Any
) -> None:
    ctx, calls = _happy_adapters(tmp_path)
    pipeline = ctx["pipeline"]
    work = tmp_path / "work"
    request = _request(str(work))
    _fault_after(
        monkeypatch,
        recovery_module,
        "atomic_write_text",
        lambda path, *a: Path(path).name.startswith("route-asset-"),
    )
    _run_until_interrupt(pipeline, request)
    assert len(calls["compile"]) == 1
    result = pipeline.resume(request)
    baseline = _baseline_result(tmp_path)
    assert result.status == "completed"
    assert result.result_id == baseline.result_id
    assert len(calls["compile"]) == 2


def test_asset_progress_fault_resume_skips_asset(
    tmp_path: Path, monkeypatch: Any
) -> None:
    ctx, calls = _happy_adapters(tmp_path)
    pipeline = ctx["pipeline"]
    work = tmp_path / "work"
    request = _request(str(work))
    _fault_after(
        monkeypatch,
        recovery_module,
        "atomic_write_json",
        lambda path, *a: Path(path).name == "ROUTE_PROGRESS.json",
        nth=2,
    )
    _run_until_interrupt(pipeline, request)
    assert len(calls["compile"]) == 1
    result = pipeline.resume(request)
    baseline = _baseline_result(tmp_path)
    assert result.status == "completed"
    assert result.result_id == baseline.result_id
    assert len(calls["compile"]) == 1


def test_route_writers_idempotent_no_op(tmp_path: Path) -> None:
    ctx, _ = _happy_adapters(tmp_path)
    planning = ctx["planning"]
    request = planning.rows[0].request
    execution = _execution_result(request, tmp_path / "run")
    asset = _asset_result(request, execution)
    work = tmp_path / "work"
    work.mkdir()
    write_execution_route(work, request, execution, route_id="route_01")
    write_execution_route(work, request, execution, route_id="route_01")
    progress = load_route_progress(work)
    assert len(progress.execution) == 1
    write_asset_route(work, request, execution, asset)
    write_asset_route(work, request, execution, asset)
    progress = load_route_progress(work)
    assert len(progress.execution) == 1
    assert len(progress.asset) == 1


def test_route_writers_conflict_rejected(tmp_path: Path) -> None:
    ctx, _ = _happy_adapters(tmp_path)
    planning = ctx["planning"]
    request = planning.rows[0].request
    execution = _execution_result(request, tmp_path / "run")
    work = tmp_path / "work"
    work.mkdir()
    write_execution_route(work, request, execution, route_id="route_01")
    snapshot = next(work.glob("route-execution-*.json"))
    before = snapshot.read_bytes()
    with pytest.raises(RecoveryIntegrityError):
        write_execution_route(
            work,
            request,
            execution,
            route_id="route_01",
            warnings=("changed",),
        )
    assert snapshot.read_bytes() == before
    assert len(load_route_progress(work).execution) == 1


def test_duplicate_route_progress_keys_fail_closed(tmp_path: Path) -> None:
    ctx, calls = _happy_adapters(
        tmp_path, interrupt_execution_route=2, two_routes=True
    )
    pipeline = ctx["pipeline"]
    work = tmp_path / "work"
    request = _request(str(work))
    _run_until_interrupt(pipeline, request)
    progress_path = work / ROUTE_PROGRESS_FILENAME
    payload = json.loads(progress_path.read_text(encoding="utf-8"))
    payload["execution"].append(dict(payload["execution"][0]))
    progress_path.write_text(json.dumps(payload), encoding="utf-8")
    result = pipeline.resume(request)
    assert result.status == "failed"
    assert any(
        "duplicate" in error for error in result.validation_errors
    )
    assert len(calls["execute"]) == 1


def test_crash_before_first_checkpoint_initializes_empty_ledger(
    tmp_path: Path,
) -> None:
    ctx, calls = _happy_adapters(tmp_path, interrupt_analyze=True)
    pipeline = ctx["pipeline"]
    work = tmp_path / "work"
    request = _request(str(work))
    _run_until_interrupt(pipeline, request)
    assert not (work / LEDGER_FILENAME).exists()
    assert calls["analyze"] == 1
    result = pipeline.resume(request)
    baseline = _baseline_result(tmp_path)
    assert result.status == "completed"
    assert result.result_id == baseline.result_id
    assert (work / LEDGER_FILENAME).is_file()


def test_junk_directory_with_valid_request_fails_closed(
    tmp_path: Path,
) -> None:
    ctx, _ = _happy_adapters(tmp_path, interrupt_analyze=True)
    pipeline = ctx["pipeline"]
    work = tmp_path / "work"
    request = _request(str(work))
    _run_until_interrupt(pipeline, request)
    (work / "junk.txt").write_text("x", encoding="utf-8")
    result = pipeline.resume(request)
    assert result.status == "failed"
    assert any(
        "unrecognized" in error for error in result.validation_errors
    )


def test_forged_orphan_checkpoint_not_promoted(tmp_path: Path) -> None:
    from optomind_optics.harness.article_pipeline import StageReceipt

    ctx, _ = _happy_adapters(tmp_path, interrupt_strategy=True)
    pipeline = ctx["pipeline"]
    work = tmp_path / "work"
    request = _request(str(work))
    _run_until_interrupt(pipeline, request)
    second = json.loads(
        (work / "checkpoint-02-method_research.json").read_text(
            encoding="utf-8"
        )
    )
    forged = {
        "schema_version": "pipeline-checkpoint-record.v1",
        "request_digest": second["request_digest"],
        "runtime_fingerprint": second["runtime_fingerprint"],
        "stage_sequence": 3,
        "stage": "strategy_planning",
        "stage_status": "completed",
        "receipt": StageReceipt(
            sequence=3,
            stage="strategy_planning",
            status="completed",
            input_ids=(),
            output_ids=(),
            warnings=(),
            errors=(),
            payload_digest="0" * 64,
        ).model_dump(mode="json"),
        "snapshot_filename": "03-strategy_planning.json",
        "snapshot_sha256": "0" * 64,
        "payload_digest": "0" * 64,
        "event_prefix_digest": "0" * 64,
        "previous_checkpoint_id": second["checkpoint_id"],
        "checkpoint_id": "",
        "hard_failure": False,
    }
    record = PipelineCheckpointRecord.model_validate(forged)
    forged["checkpoint_id"] = compute_checkpoint_id(record)
    (work / "checkpoint-03-strategy_planning.json").write_text(
        json.dumps(forged), encoding="utf-8"
    )
    result = pipeline.resume(request)
    assert result.status == "failed"
    assert any(
        "checkpoint" in error for error in result.validation_errors
    )


def test_self_consistent_forged_orphan_not_promoted(
    tmp_path: Path,
) -> None:
    from optomind_optics.harness.article_pipeline import StageReceipt

    ctx, calls = _happy_adapters(tmp_path, interrupt_strategy=True)
    pipeline = ctx["pipeline"]
    work = tmp_path / "work"
    request = _request(str(work))
    _run_until_interrupt(pipeline, request)
    second = json.loads(
        (work / "checkpoint-02-method_research.json").read_text(
            encoding="utf-8"
        )
    )

    forged_strategy = _strategy_result().model_copy(
        update={
            "plan": _strategy_result().plan.model_copy(
                update={"problem_id": "problem-other"}
            )
        }
    )
    snapshot_dump = forged_strategy.model_dump(mode="json")
    snapshot_filename = "03-strategy_planning.json"
    snapshot_text = pipeline_module._canonical_json(snapshot_dump) + "\n"
    pipeline_module.atomic_write_text(
        work / snapshot_filename, snapshot_text
    )
    snapshot_sha256 = hashlib.sha256(
        (work / snapshot_filename).read_bytes()
    ).hexdigest()
    payload_digest = pipeline_module._digest(snapshot_dump)

    event = {
        "schema_version": "pipeline-event.v1",
        "sequence": 3,
        "stage": "strategy_planning",
        "status": "completed",
        "payload_digest": payload_digest,
        "event_id": pipeline_module._digest(
            3, "strategy_planning", "completed", payload_digest
        ),
    }
    events_path = work / "PIPELINE_EVENTS.jsonl"
    events_path.write_text(
        events_path.read_text(encoding="utf-8").rstrip("\n")
        + "\n"
        + json.dumps(event, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    event_prefix_digest = recovery_module.compute_event_prefix_digest(events)

    forged = {
        "schema_version": "pipeline-checkpoint-record.v1",
        "request_digest": second["request_digest"],
        "runtime_fingerprint": second["runtime_fingerprint"],
        "stage_sequence": 3,
        "stage": "strategy_planning",
        "stage_status": "completed",
        "receipt": StageReceipt(
            sequence=3,
            stage="strategy_planning",
            status="completed",
            input_ids=(),
            output_ids=(),
            warnings=(),
            errors=(),
            payload_digest=payload_digest,
        ).model_dump(mode="json"),
        "snapshot_filename": snapshot_filename,
        "snapshot_sha256": snapshot_sha256,
        "payload_digest": payload_digest,
        "event_prefix_digest": event_prefix_digest,
        "route_progress_digest": "",
        "previous_checkpoint_id": second["checkpoint_id"],
        "checkpoint_id": "",
        "hard_failure": False,
    }
    record = PipelineCheckpointRecord.model_validate(forged)
    forged["checkpoint_id"] = compute_checkpoint_id(record)
    (work / "checkpoint-03-strategy_planning.json").write_text(
        json.dumps(forged), encoding="utf-8"
    )

    ledger_path = work / LEDGER_FILENAME
    ledger_before = ledger_path.read_bytes()
    baseline_calls = {
        key: (len(value) if isinstance(value, list) else value)
        for key, value in calls.items()
    }

    first = pipeline.resume(request)
    assert first.status == "failed"
    assert any(
        "problem_id" in error for error in first.validation_errors
    )
    assert ledger_path.read_bytes() == ledger_before
    ledger_payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert len(ledger_payload["committed_checkpoints"]) == 2
    for key, value in baseline_calls.items():
        current = (
            len(calls[key]) if isinstance(calls[key], list) else calls[key]
        )
        assert current == value, key

    second_resume = pipeline.resume(request)
    assert second_resume.status == "failed"
    assert ledger_path.read_bytes() == ledger_before
    ledger_payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert len(ledger_payload["committed_checkpoints"]) == 2


def test_route_writer_byte_tamper_not_noop(tmp_path: Path) -> None:
    ctx, _ = _happy_adapters(tmp_path)
    planning = ctx["planning"]
    request = planning.rows[0].request
    execution = _execution_result(request, tmp_path / "run")
    asset = _asset_result(request, execution)
    work = tmp_path / "work"
    work.mkdir()
    write_execution_route(work, request, execution, route_id="route_01")
    execution_snapshot = next(work.glob("route-execution-*.json"))
    original_bytes = execution_snapshot.read_bytes()
    execution_snapshot.write_bytes(original_bytes + b" ")
    with pytest.raises(RecoveryIntegrityError):
        write_execution_route(work, request, execution, route_id="route_01")
    assert len(load_route_progress(work).execution) == 1
    execution_snapshot.write_bytes(original_bytes)

    write_asset_route(work, request, execution, asset)
    asset_snapshot = next(work.glob("route-asset-*.json"))
    original_asset_bytes = asset_snapshot.read_bytes()
    asset_snapshot.write_bytes(original_asset_bytes + b" ")
    with pytest.raises(RecoveryIntegrityError):
        write_asset_route(work, request, execution, asset)
    assert len(load_route_progress(work).asset) == 1


def test_same_request_id_different_task_hash_rejected(
    tmp_path: Path,
) -> None:
    ctx, _ = _happy_adapters(tmp_path)
    planning = ctx["planning"]
    request = planning.rows[0].request
    execution = _execution_result(request, tmp_path / "run")
    asset = _asset_result(request, execution)
    work = tmp_path / "work"
    work.mkdir()
    write_execution_route(work, request, execution, route_id="route_01")
    execution_snapshot = next(work.glob("route-execution-*.json"))
    execution_bytes = execution_snapshot.read_bytes()
    other_hash = request.model_copy(update={"task_hash": "0" * 64})
    with pytest.raises(RecoveryIntegrityError):
        write_execution_route(
            work, other_hash, execution, route_id="route_01"
        )
    assert execution_snapshot.read_bytes() == execution_bytes
    assert len(load_route_progress(work).execution) == 1

    write_asset_route(work, request, execution, asset)
    asset_snapshot = next(work.glob("route-asset-*.json"))
    asset_bytes = asset_snapshot.read_bytes()
    with pytest.raises(RecoveryIntegrityError):
        write_asset_route(work, other_hash, execution, asset)
    assert asset_snapshot.read_bytes() == asset_bytes
    assert len(load_route_progress(work).asset) == 1


def test_asset_writer_rejects_orphan_execution_snapshot(
    tmp_path: Path,
) -> None:
    ctx, _ = _happy_adapters(tmp_path)
    planning = ctx["planning"]
    request = planning.rows[0].request
    execution = _execution_result(request, tmp_path / "run")
    asset = _asset_result(request, execution)
    work = tmp_path / "work"
    work.mkdir()
    filename = (
        "route-execution-"
        + recovery_module._short_digest(request.request_id)
        + ".json"
    )
    (work / filename).write_text(
        recovery_module._canonical_json(
            execution.model_dump(mode="json")
        ),
        encoding="utf-8",
    )
    with pytest.raises(RecoveryIntegrityError):
        write_asset_route(work, request, execution, asset)
    assert not (work / ROUTE_PROGRESS_FILENAME).exists()


def test_duplicate_request_id_progress_fails_closed(tmp_path: Path) -> None:
    ctx, calls = _happy_adapters(
        tmp_path, interrupt_execution_route=2, two_routes=True
    )
    pipeline = ctx["pipeline"]
    work = tmp_path / "work"
    request = _request(str(work))
    _run_until_interrupt(pipeline, request)
    progress_path = work / ROUTE_PROGRESS_FILENAME
    payload = json.loads(progress_path.read_text(encoding="utf-8"))
    forged = dict(payload["execution"][0])
    forged["task_hash"] = "0" * 64
    payload["execution"].append(forged)
    progress_path.write_text(json.dumps(payload), encoding="utf-8")
    result = pipeline.resume(request)
    assert result.status == "failed"
    assert any(
        "duplicate" in error for error in result.validation_errors
    )
    assert len(calls["execute"]) == 1
