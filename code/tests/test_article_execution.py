from __future__ import annotations

import json
from pathlib import Path

import pytest

from optomind_optics.harness.article_contracts import ArticleStage, ObservationCard
from optomind_optics.harness.article_director import ArticleDirector
from optomind_optics.harness.article_execution import (
    ActionAuthorizationError,
    ArticleExecutionError,
    ArticleExecutionCoordinator,
    ArticleExecutionResult,
    ArticleTMMExecutionAdapter,
    BudgetCeilingError,
    InvalidResolvedTask,
    LocalTaskRegistry,
    ResolvedTask,
    RunCollisionError,
    TaskIdentityMismatch,
    normalize_observation_status,
    observation_card_from_tmm_result,
    run_artifact_refs,
)
from optomind_optics.harness.article_gateway import (
    ArticleToolGateway,
    GatewayAdapterResult,
    GatewayRejection,
)
from optomind_optics.harness.article_proposals import (
    ArticleCompilationAuthority,
    CompiledExperimentRequest,
    ExperimentProposal,
    compile_proposal,
    compute_optical_design_task_digest,
)
from optomind_optics.harness.article_runtime import ArticleBudgetAdapter
from optomind_optics.harness.budget import (
    BudgetLimits,
    BudgetScheduler,
    BudgetOversubscriptionError,
)
from optomind_optics.harness.contracts import ActionType, ExperimentStatus
from optomind_optics.harness.dev_fixtures import build_dev_optical_design_task
from optomind_optics.harness.method_research import (
    MethodResearchReport,
    MethodResearchStatus,
)
from optomind_optics.harness.orchestrator import TMMHarnessRunResult
from optomind_optics.harness.problem_analyzer import (
    OpticalProblemAnalysis,
    ResearchIntent,
    TMMCompatibility,
)


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
        problem_id="problem-1", status=MethodResearchStatus.completed
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


def _authority(key: bytes = b"stage6-test-key") -> ArticleCompilationAuthority:
    return ArticleCompilationAuthority(key)


def _request(
    *,
    authority: ArticleCompilationAuthority | None = None,
    proposal_id: str = "proposal-1",
    requested_budget: dict | None = None,
    action_type: ActionType = ActionType.run_solver,
    task: object | None = None,
) -> CompiledExperimentRequest:
    task = task if task is not None else _task()
    if requested_budget is None:
        requested_budget = {
            "wall_time_seconds": float(task.budget.wall_time_seconds),
            "forward_evaluations": int(task.budget.maximum_forward_evaluations),
            "optimizer_runs": int(task.budget.maximum_optimizer_runs),
        }
    if action_type == ActionType.run_optimizer:
        parameters = {
            "experiment_id": "exp-1",
            "optimizer_id": "gradient_thickness",
            "maximum_evaluations": 100,
        }
    elif action_type == ActionType.run_reference_solver:
        parameters = {"experiment_id": "exp-1", "candidate_id": "c1"}
    elif action_type == ActionType.run_convergence_audit:
        parameters = {"experiment_id": "exp-1", "max_refinements": 3}
    elif action_type == ActionType.run_robustness_audit:
        parameters = {"experiment_id": "exp-1", "candidate_id": "c1", "samples": 8}
    elif action_type == ActionType.generate_baseline:
        parameters = {"experiment_id": "exp-1", "route_id": "r1"}
    else:
        parameters = {"experiment_id": "exp-1", "solver": "smatrix"}
    proposal = ExperimentProposal(
        proposal_id=proposal_id,
        hypothesis_ids=["hyp-01"],
        stage=ArticleStage.baseline_experiments,
        action_type=action_type,
        parameters=parameters,
        atomic_change={"variable": "thickness_layer_3", "delta_nm": 2.0},
        expected_discriminator={"metric": "R_mean", "direction": "lower"},
        rationale="Baseline solver run.",
        requested_budget=requested_budget,
    )
    return compile_proposal(
        proposal,
        plan=_plan(),
        run_id="run-1",
        branch_id="root",
        authority=authority or _authority(),
        task=task,
    )


def _task() -> "object":
    return build_dev_optical_design_task("DEV02")


def _run_result(
    status: str = "completed",
    *,
    run_id: str = "run-1",
    valid_count: int = 2,
    candidate_count: int = 3,
    usage: dict | None = None,
    failure_records: list | None = None,
) -> dict:
    payload = {
        "schema_version": "tmm-harness-result.v1",
        "run_id": run_id,
        "task_id": "task-dev02",
        "status": status,
        "state_stage": status,
        "experiment_results": [
            {
                "experiment_id": "exp-1",
                "mode": "simulate",
                "physically_valid_candidate_count": valid_count,
                "candidate_count": candidate_count,
                "baseline_status": "verified",
                "portfolio_artifact_id": "experiments/exp-1/DESIGN_PORTFOLIO.json",
                "portfolio": {
                    "selected_roles": {
                        "best_target_score": "c1",
                        "most_robust": "c2",
                    }
                },
            }
        ],
        "budget": {
            "usage": usage
            if usage is not None
            else {
                "forward_evaluations": 40,
                "optimizer_runs": 1,
                "qwen_calls": 0,
                "qwen_input_tokens": 0,
                "qwen_output_tokens": 0,
                "qwen_cost_cny": 0.0,
                "wall_time_seconds": 1.5,
            }
        },
        "stop_decision": {"stop": True, "reason": "portfolio_complete", "return_best_effort": True},
        "wall_seconds": 2.0,
    }
    if failure_records is not None:
        payload["failure_records"] = failure_records
    return payload


class FakeHarness:
    def __init__(self, result, *, raise_exc: Exception | None = None) -> None:
        self.result = result
        self.raise_exc = raise_exc
        self.calls: list = []
        self.run_dirs: list[Path] = []
        self.current_run_dir: Path | None = None

    def run(self, task):
        self.calls.append(task)
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.current_run_dir is not None:
            self.current_run_dir.mkdir(parents=True, exist_ok=True)
            (self.current_run_dir / "TASK.json").write_text(
                json.dumps(task.model_dump(mode="json")), encoding="utf-8"
            )
            (self.current_run_dir / "FINAL_RESULT.json").write_text(
                json.dumps(self.result), encoding="utf-8"
            )
        return self.result


def _stack(
    tmp_path: Path,
    *,
    harness: FakeHarness,
    limits: BudgetLimits | None = None,
    authority: ArticleCompilationAuthority | None = None,
    registry: LocalTaskRegistry | None = None,
):
    authority = authority or _authority()
    scheduler = BudgetScheduler(
        limits
        or BudgetLimits(
            wall_time_seconds=3600.0,
            forward_evaluations=50000,
            optimizer_runs=10,
            qwen_calls=10,
            qwen_input_tokens=10000,
            qwen_output_tokens=10000,
            qwen_cost_cny=100.0,
        )
    )
    budget_adapter = ArticleBudgetAdapter(scheduler)
    registry = registry or LocalTaskRegistry()

    def factory(run_dir, run_id):
        harness.current_run_dir = Path(run_dir)
        return harness

    adapter = ArticleTMMExecutionAdapter(
        resolver=registry,
        budget_adapter=budget_adapter,
        work_root=tmp_path / "work",
        branch_id="root",
        run_id="run-1",
        harness_factory=factory,
    )
    gateway = ArticleToolGateway(
        authority=authority, run_id="run-1", branch_id="root"
    )
    coordinator = ArticleExecutionCoordinator(gateway=gateway, adapter=adapter)
    return {
        "scheduler": scheduler,
        "budget_adapter": budget_adapter,
        "registry": registry,
        "adapter": adapter,
        "gateway": gateway,
        "coordinator": coordinator,
    }


def test_valid_execution_commits_measured_usage_and_returns_observation(
    tmp_path,
) -> None:
    request = _request()
    harness = FakeHarness(_run_result())
    stack = _stack(tmp_path, harness=harness)
    stack["registry"].register(request.task_hash, _task())

    result = stack["coordinator"].execute(request)
    assert isinstance(result, ArticleExecutionResult)
    assert result.observation.status == ExperimentStatus.physically_valid
    assert result.outcome == "physically_valid"
    assert result.observation.metrics["measured_budget"]["forward_evaluations"] == 40
    assert "FINAL_RESULT.json" in result.observation.artifact_ids
    assert "TASK.json" in result.observation.artifact_ids
    assert result.receipt["status"] == "adapter_completed"
    assert stack["scheduler"].snapshot()["committed"]["forward_evaluations"] == 40
    assert stack["scheduler"].snapshot()["reserved"]["forward_evaluations"] == 0
    run_dir = stack["adapter"].run_dir_for(request)
    assert run_dir.is_dir()
    marker = json.loads((run_dir / "EXECUTION_MARKER.json").read_text(encoding="utf-8"))
    assert marker["status"] == "completed"
    assert marker["task_hash"] == request.task_hash


def test_missing_resolver_task_releases_and_fails(tmp_path) -> None:
    request = _request()
    harness = FakeHarness(_run_result())
    stack = _stack(tmp_path, harness=harness)
    result = stack["coordinator"].execute(request)
    assert result.observation.status == ExperimentStatus.failed
    assert result.receipt["status"] == "adapter_rejected"
    assert "ResolverFailure" in result.receipt["reason"]
    assert harness.calls == []
    assert stack["scheduler"].snapshot()["reserved"]["forward_evaluations"] == 0
    assert not stack["adapter"].run_dir_for(request).exists()


def test_task_hash_identity_mismatch_rejected(tmp_path) -> None:
    request = _request()
    harness = FakeHarness(_run_result())
    stack = _stack(tmp_path, harness=harness)

    class WrongHashResolver:
        def resolve(self, req):
            return ResolvedTask(task_hash="other-hash", task=_task())

    stack["adapter"].resolver = WrongHashResolver()
    result = stack["coordinator"].execute(request)
    assert result.receipt["status"] == "adapter_rejected"
    assert "TaskIdentityMismatch" in result.receipt["reason"]
    assert harness.calls == []


def test_invalid_optical_design_task_rejected_before_harness(tmp_path) -> None:
    with pytest.raises(InvalidResolvedTask, match="task is invalid"):
        LocalTaskRegistry().register("hash-1", {"task_id": "x"})

    request = _request()
    harness = FakeHarness(_run_result())
    stack = _stack(tmp_path, harness=harness)

    class MappingResolver:
        def resolve(self, req):
            return {"task_hash": req.task_hash, "task": {}}

    stack["adapter"].resolver = MappingResolver()
    result = stack["coordinator"].execute(request)
    assert result.receipt["status"] == "adapter_rejected"
    assert harness.calls == []


def test_reserve_rejection_prevents_run(tmp_path) -> None:
    request = _request()
    harness = FakeHarness(_run_result())
    stack = _stack(
        tmp_path,
        harness=harness,
        limits=BudgetLimits(forward_evaluations=1),
    )
    stack["registry"].register(request.task_hash, _task())
    stack["budget_adapter"].reserve("blocker", forward_evaluations=1)
    result = stack["coordinator"].execute(request)
    assert result.receipt["status"] == "adapter_rejected"
    assert "BudgetReservationError" in result.receipt["reason"]
    assert harness.calls == []
    assert stack["scheduler"].snapshot()["reserved"]["forward_evaluations"] == 1


def test_harness_exception_releases_budget(tmp_path) -> None:
    request = _request()
    harness = FakeHarness(_run_result(), raise_exc=RuntimeError("boom"))
    stack = _stack(tmp_path, harness=harness)
    stack["registry"].register(request.task_hash, _task())
    result = stack["coordinator"].execute(request)
    assert result.receipt["status"] == "adapter_rejected"
    assert "boom" in result.receipt["reason"]
    assert result.observation.status == ExperimentStatus.failed
    assert any("boom" in str(item) for item in result.observation.failure_records)
    assert stack["scheduler"].snapshot()["reserved"]["forward_evaluations"] == 0


def test_malformed_or_missing_usage_is_hard_failure(tmp_path) -> None:
    for usage in (
        {"forward_evaluations": "many"},
        {"forward_evaluations": 1.5},
        {"forward_evaluations": -1},
    ):
        request = _request(proposal_id=f"proposal-{usage.get('forward_evaluations')}")
        harness = FakeHarness(_run_result(usage=usage))
        stack = _stack(tmp_path, harness=harness)
        stack["registry"].register(request.task_hash, _task())
        result = stack["coordinator"].execute(request)
        assert result.receipt["status"] == "adapter_rejected"
        assert "UsageMalformedError" in result.receipt["reason"]
        assert harness.calls == [stack["registry"].resolve(request).task]
        assert stack["scheduler"].snapshot()["reserved"]["forward_evaluations"] == 0

    request = _request(proposal_id="proposal-missing-usage")
    harness = FakeHarness(_run_result(usage={}))
    stack = _stack(tmp_path, harness=harness)
    stack["registry"].register(request.task_hash, _task())
    result = stack["coordinator"].execute(request)
    assert result.receipt["status"] == "adapter_rejected"
    assert "UsageMalformedError" in result.receipt["reason"]


def test_completed_without_physically_valid_candidates_is_rejected_physics(
    tmp_path,
) -> None:
    request = _request()
    harness = FakeHarness(_run_result(valid_count=0, candidate_count=3))
    stack = _stack(tmp_path, harness=harness)
    stack["registry"].register(request.task_hash, _task())
    result = stack["coordinator"].execute(request)
    assert result.observation.status == ExperimentStatus.rejected_physics
    assert result.observation.metrics["candidate_count"] == 3
    assert result.observation.metrics["physically_valid_candidate_count"] == 0
    assert stack["scheduler"].snapshot()["committed"]["forward_evaluations"] == 40


def test_needs_higher_fidelity_releases_budget(tmp_path) -> None:
    request = _request()
    harness = FakeHarness(_run_result(status="needs_higher_fidelity"))
    stack = _stack(tmp_path, harness=harness)
    stack["registry"].register(request.task_hash, _task())
    result = stack["coordinator"].execute(request)
    assert result.observation.status == ExperimentStatus.needs_higher_fidelity
    assert result.observation.metrics["run_status"] == "needs_higher_fidelity"
    assert stack["scheduler"].snapshot()["committed"]["forward_evaluations"] == 0
    assert stack["scheduler"].snapshot()["reserved"]["forward_evaluations"] == 0


def test_cancelled_and_failed_preserve_failure_info(tmp_path) -> None:
    records = [{"code": "BUDGET_EXHAUSTED", "recoverable": False}]
    request = _request(proposal_id="proposal-cancelled")
    harness = FakeHarness(
        _run_result(status="cancelled", failure_records=records)
    )
    stack = _stack(tmp_path, harness=harness)
    stack["registry"].register(request.task_hash, _task())
    result = stack["coordinator"].execute(request)
    assert result.observation.status == ExperimentStatus.cancelled
    assert result.observation.failure_records == records

    request_failed = _request(proposal_id="proposal-failed")
    harness_failed = FakeHarness(
        _run_result(status="failed", failure_records=records)
    )
    stack_failed = _stack(tmp_path, harness=harness_failed)
    stack_failed["registry"].register(request_failed.task_hash, _task())
    result_failed = stack_failed["coordinator"].execute(request_failed)
    assert result_failed.observation.status == ExperimentStatus.failed
    assert result_failed.observation.failure_records == records
    assert stack_failed["scheduler"].snapshot()["reserved"]["forward_evaluations"] == 0


def test_gateway_rejects_raw_envelope_and_resolver_rejects_mapping(tmp_path) -> None:
    request = _request()
    harness = FakeHarness(_run_result())
    stack = _stack(tmp_path, harness=harness)
    outcome = stack["gateway"].execute(
        {"proposal_id": "x", "task": {"task_id": "forged"}}, stack["adapter"]
    )
    assert isinstance(outcome, GatewayRejection)
    assert outcome.category == "direct_model_execution"
    assert harness.calls == []
    with pytest.raises(TypeError):
        stack["registry"].resolve({"proposal_id": "x"})


def test_count_resources_reject_non_integer_before_run(tmp_path) -> None:
    request = _request(
        proposal_id="proposal-float",
        requested_budget={"forward_evaluations": 100.5, "optimizer_runs": 1},
    )
    harness = FakeHarness(_run_result())
    stack = _stack(tmp_path, harness=harness)
    stack["registry"].register(request.task_hash, _task())
    result = stack["coordinator"].execute(request)
    assert result.receipt["status"] == "adapter_rejected"
    assert "must be an integer" in result.receipt["reason"]
    assert harness.calls == []
    assert stack["scheduler"].snapshot()["reserved"]["forward_evaluations"] == 0


def test_run_isolation_and_no_overwrite_for_different_hash(tmp_path) -> None:
    request_a = _request(proposal_id="proposal-a")
    request_b = _request(proposal_id="proposal-b")
    assert request_a.task_hash != request_b.task_hash
    harness = FakeHarness(_run_result())
    stack = _stack(tmp_path, harness=harness)
    stack["registry"].register(request_a.task_hash, _task())
    stack["registry"].register(request_b.task_hash, _task())
    result_a = stack["coordinator"].execute(request_a)
    result_b = stack["coordinator"].execute(request_b)
    run_dir_a = stack["adapter"].run_dir_for(request_a)
    run_dir_b = stack["adapter"].run_dir_for(request_b)
    assert run_dir_a != run_dir_b
    assert (run_dir_a / "FINAL_RESULT.json").exists()
    assert (run_dir_b / "FINAL_RESULT.json").exists()
    assert result_a.observation.artifact_ids == result_b.observation.artifact_ids

    collision = stack["adapter"].run_dir_for(request_a) / "EXECUTION_MARKER.json"
    json_payload = json.loads(collision.read_text(encoding="utf-8"))
    json_payload["task_hash"] = "different-hash"
    collision.write_text(json.dumps(json_payload), encoding="utf-8")
    harness.calls.clear()
    result = stack["coordinator"].execute(request_a)
    assert result.receipt["status"] == "adapter_rejected"
    assert "RunCollisionError" in result.receipt["reason"]
    assert harness.calls == []


def test_idempotent_replay_same_hash_does_not_rerun(tmp_path) -> None:
    request = _request()
    harness = FakeHarness(_run_result())
    stack = _stack(tmp_path, harness=harness)
    stack["registry"].register(request.task_hash, _task())
    first = stack["coordinator"].execute(request)
    second = stack["coordinator"].execute(request)
    assert len(harness.calls) == 1
    assert second.observation.status == first.observation.status
    assert second.receipt["telemetry"]["replayed"] is True
    assert stack["scheduler"].snapshot()["committed"]["forward_evaluations"] == 40


def test_artifact_refs_are_relative_and_deterministic(tmp_path) -> None:
    request = _request()
    harness = FakeHarness(_run_result())
    stack = _stack(tmp_path, harness=harness)
    stack["registry"].register(request.task_hash, _task())
    result = stack["coordinator"].execute(request)
    refs = result.observation.artifact_ids
    assert refs == sorted(refs)
    assert all(not Path(item).is_absolute() and ".." not in item for item in refs)
    run_dir = stack["adapter"].run_dir_for(request)
    assert refs == run_artifact_refs(run_dir)
    assert result.receipt["output_refs"] == refs


def test_observation_card_deterministic_and_preserves_raw_info(tmp_path) -> None:
    payload = _run_result(failure_records=[{"code": "X", "recoverable": False}])
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "FINAL_RESULT.json").write_text(json.dumps(payload), encoding="utf-8")
    (run_dir / "TASK.json").write_text("{}", encoding="utf-8")
    first = observation_card_from_tmm_result(payload, run_dir=run_dir, experiment_id="exp-1")
    second = observation_card_from_tmm_result(payload, run_dir=run_dir, experiment_id="exp-1")
    assert first.model_dump_json() == second.model_dump_json()
    metrics = first.metrics
    assert metrics["run_status"] == "completed"
    assert metrics["stop_decision"]["reason"] == "portfolio_complete"
    assert metrics["candidate_count"] == 3
    assert metrics["physically_valid_candidate_count"] == 2
    assert metrics["selected_candidate_ids"] == ["c1", "c2"]
    assert metrics["measured_budget"]["forward_evaluations"] == 40
    assert first.failure_records == [{"code": "X", "recoverable": False}]
    assert "FINAL_RESULT.json" in first.artifact_ids
    assert first.status == ExperimentStatus.physically_valid


def test_normalize_observation_status_mapping() -> None:
    assert (
        normalize_observation_status(
            {
                "status": "completed",
                "experiment_results": [
                    {"physically_valid_candidate_count": 1}
                ],
            }
        )
        == ExperimentStatus.physically_valid
    )
    assert (
        normalize_observation_status(
            {"status": "completed", "experiment_results": []}
        )
        == ExperimentStatus.rejected_physics
    )
    assert (
        normalize_observation_status({"status": "needs_higher_fidelity"})
        == ExperimentStatus.needs_higher_fidelity
    )
    assert normalize_observation_status({"status": "cancelled"}) == ExperimentStatus.cancelled
    assert normalize_observation_status({"status": "failed"}) == ExperimentStatus.failed
    assert normalize_observation_status({"status": "weird"}) == ExperimentStatus.failed


def test_run_artifact_refs_only_present_files(tmp_path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert run_artifact_refs(empty) == []
    (empty / "FINAL_RESULT.json").write_text("{}", encoding="utf-8")
    cert = empty / "experiments" / "e1" / "baseline"
    cert.mkdir(parents=True)
    (cert / "PHYSICS_ACCEPTANCE_CERTIFICATE.json").write_text("{}", encoding="utf-8")
    refs = run_artifact_refs(empty)
    assert refs == [
        "FINAL_RESULT.json",
        "experiments/e1/baseline/PHYSICS_ACCEPTANCE_CERTIFICATE.json",
    ]


def test_marker_request_or_run_mismatch_prevents_replay(tmp_path) -> None:
    request = _request()
    harness = FakeHarness(_run_result())
    stack = _stack(tmp_path, harness=harness)
    stack["registry"].register(request.task_hash, _task())
    stack["coordinator"].execute(request)
    marker = stack["adapter"].run_dir_for(request) / "EXECUTION_MARKER.json"

    for key, value in (
        ("request_id", "other-request"),
        ("run_id", "other-run"),
        ("status", "running"),
    ):
        payload = json.loads(marker.read_text(encoding="utf-8"))
        payload[key] = value
        marker.write_text(json.dumps(payload), encoding="utf-8")
        harness.calls.clear()
        result = stack["coordinator"].execute(request)
        assert result.receipt["status"] == "adapter_rejected"
        assert "RunCollisionError" in result.receipt["reason"]
        assert harness.calls == []

    marker.write_text("{not json", encoding="utf-8")
    result = stack["coordinator"].execute(request)
    assert result.receipt["status"] == "adapter_rejected"
    assert "RunCollisionError" in result.receipt["reason"]
    assert "malformed" in result.receipt["reason"]
    assert harness.calls == []


def test_result_run_id_mismatch_rejected_before_commit(tmp_path) -> None:
    request = _request()
    harness = FakeHarness(_run_result(run_id="other-run"))
    stack = _stack(tmp_path, harness=harness)
    stack["registry"].register(request.task_hash, _task())
    result = stack["coordinator"].execute(request)
    assert result.receipt["status"] == "adapter_rejected"
    assert "does not match adapter run_id" in result.receipt["reason"]
    assert harness.calls == [stack["registry"].resolve(request).task]
    snapshot = stack["scheduler"].snapshot()
    assert snapshot["committed"]["forward_evaluations"] == 0
    assert snapshot["reserved"]["forward_evaluations"] == 0


@pytest.mark.parametrize(
    "bad_component",
    ["", "   ", ".", "..", "a/b", "a\\b", "a\x00b", "C:abs", " leading"],
)
def test_branch_and_run_path_component_validation(bad_component, tmp_path) -> None:
    with pytest.raises(ArticleExecutionError):
        ArticleTMMExecutionAdapter(
            resolver=LocalTaskRegistry(),
            budget_adapter=ArticleBudgetAdapter(BudgetScheduler(BudgetLimits())),
            work_root=tmp_path / "work",
            branch_id=bad_component,
            run_id="run-1",
        )
    with pytest.raises(ArticleExecutionError):
        ArticleTMMExecutionAdapter(
            resolver=LocalTaskRegistry(),
            budget_adapter=ArticleBudgetAdapter(BudgetScheduler(BudgetLimits())),
            work_root=tmp_path / "work",
            branch_id="root",
            run_id=bad_component,
        )


def test_registry_collision_rejected_and_idempotent_reregister() -> None:
    registry = LocalTaskRegistry()
    task = _task()
    first = registry.register("hash-1", task)
    second = registry.register("hash-1", task)
    assert first is second
    assert first.task_digest == compute_optical_design_task_digest(task)
    with pytest.raises(TaskIdentityMismatch, match="different task"):
        registry.register("hash-1", build_dev_optical_design_task("DEV03"))


def test_task_swap_rejected_before_harness_and_reservation(tmp_path) -> None:
    request = _request()
    harness = FakeHarness(_run_result())
    stack = _stack(tmp_path, harness=harness)
    stack["registry"].register(request.task_hash, _task())

    class SwappedResolver:
        def resolve(self, req):
            return ResolvedTask(
                task_hash=req.task_hash,
                task=build_dev_optical_design_task("DEV03"),
            )

    stack["adapter"].resolver = SwappedResolver()
    result = stack["coordinator"].execute(request)
    assert result.receipt["status"] == "adapter_rejected"
    assert "TaskIdentityMismatch" in result.receipt["reason"]
    assert harness.calls == []
    assert not stack["adapter"].run_dir_for(request).exists()
    assert stack["scheduler"].snapshot()["reserved"]["forward_evaluations"] == 0


def test_post_attestation_task_mutation_rejected(tmp_path) -> None:
    request = _request()
    authority = _authority()
    tampered = request.model_copy(update={"task_digest": "1" * 64})
    gateway = ArticleToolGateway(
        authority=authority,
        run_id="run-1",
        branch_id="root",
    )
    outcome = gateway.execute(tampered, object())
    assert isinstance(outcome, GatewayRejection)
    assert "task hash does not match" in outcome.reason

    harness = FakeHarness(_run_result())
    stack = _stack(tmp_path, harness=harness)
    stack["registry"].register(request.task_hash, _task())

    class WrongDigestResolver:
        def resolve(self, req):
            return ResolvedTask(
                task_hash=req.task_hash,
                task_digest="a" * 64,
                task=_task(),
            )

    stack["adapter"].resolver = WrongDigestResolver()
    result = stack["coordinator"].execute(request)
    assert result.receipt["status"] == "adapter_rejected"
    assert "TaskIdentityMismatch" in result.receipt["reason"]
    assert harness.calls == []
    assert not stack["adapter"].run_dir_for(request).exists()
    assert stack["scheduler"].snapshot()["reserved"]["forward_evaluations"] == 0


def test_simulate_optimize_action_mismatch_rejected(tmp_path) -> None:
    optimize_task = build_dev_optical_design_task("DEV01")
    request_sim = _request(
        action_type=ActionType.run_solver,
        task=optimize_task,
    )
    harness = FakeHarness(_run_result())
    stack = _stack(tmp_path, harness=harness)
    stack["registry"].register(request_sim.task_hash, optimize_task)
    result = stack["coordinator"].execute(request_sim)
    assert result.receipt["status"] == "adapter_rejected"
    assert "requires action" in result.receipt["reason"]
    assert harness.calls == []
    assert not stack["adapter"].run_dir_for(request_sim).exists()

    simulate_task = build_dev_optical_design_task("DEV02")
    request_opt = _request(
        action_type=ActionType.run_optimizer,
        task=simulate_task,
    )
    harness_opt = FakeHarness(_run_result())
    stack_opt = _stack(tmp_path, harness=harness_opt)
    stack_opt["registry"].register(request_opt.task_hash, simulate_task)
    result_opt = stack_opt["coordinator"].execute(request_opt)
    assert result_opt.receipt["status"] == "adapter_rejected"
    assert "requires action" in result_opt.receipt["reason"]
    assert harness_opt.calls == []


@pytest.mark.parametrize(
    "action",
    [
        ActionType.run_reference_solver,
        ActionType.run_convergence_audit,
        ActionType.run_robustness_audit,
        ActionType.generate_baseline,
    ],
)
def test_specialized_action_misuse_rejected(tmp_path, action) -> None:
    request = _request(action_type=action, task=_task())
    harness = FakeHarness(_run_result())
    stack = _stack(tmp_path, harness=harness)
    stack["registry"].register(request.task_hash, _task())
    result = stack["coordinator"].execute(request)
    assert result.receipt["status"] == "adapter_rejected"
    assert "ActionAuthorizationError" in result.receipt["reason"]
    assert harness.calls == []
    assert not stack["adapter"].run_dir_for(request).exists()
    assert stack["scheduler"].snapshot()["reserved"]["forward_evaluations"] == 0


def test_under_reserved_budget_rejected_before_reservation(tmp_path) -> None:
    request = _request(
        requested_budget={
            "wall_time_seconds": 10.0,
            "forward_evaluations": 10,
            "optimizer_runs": 1,
        },
    )
    harness = FakeHarness(_run_result())
    stack = _stack(tmp_path, harness=harness)
    stack["registry"].register(request.task_hash, _task())
    result = stack["coordinator"].execute(request)
    assert result.receipt["status"] == "adapter_rejected"
    assert "BudgetCeilingError" in result.receipt["reason"]
    assert harness.calls == []
    assert not stack["adapter"].run_dir_for(request).exists()
    assert stack["scheduler"].snapshot()["reserved"]["forward_evaluations"] == 0


def test_unbound_compile_only_request_rejected_at_execution(tmp_path) -> None:
    proposal = ExperimentProposal(
        proposal_id="proposal-unbound",
        hypothesis_ids=["hyp-01"],
        stage=ArticleStage.baseline_experiments,
        action_type=ActionType.run_solver,
        parameters={"experiment_id": "exp-1", "solver": "smatrix"},
        requested_budget={"forward_evaluations": 100, "optimizer_runs": 1},
    )
    request = compile_proposal(
        proposal,
        plan=_plan(),
        run_id="run-1",
        branch_id="root",
        authority=_authority(),
    )
    assert request.task_digest == ""
    harness = FakeHarness(_run_result())
    stack = _stack(tmp_path, harness=harness)
    stack["registry"].register(request.task_hash, _task())
    result = stack["coordinator"].execute(request)
    assert result.receipt["status"] == "adapter_rejected"
    assert "does not bind a canonical task digest" in result.receipt["reason"]
    assert harness.calls == []
    assert not stack["adapter"].run_dir_for(request).exists()
    assert stack["scheduler"].snapshot()["reserved"]["forward_evaluations"] == 0


def test_valid_optimize_task_executes_and_commits(tmp_path) -> None:
    optimize_task = build_dev_optical_design_task("DEV01")
    request = _request(
        action_type=ActionType.run_optimizer,
        task=optimize_task,
    )
    harness = FakeHarness(_run_result())
    stack = _stack(tmp_path, harness=harness)
    binding = stack["registry"].register(request.task_hash, optimize_task)
    assert binding.task_digest == compute_optical_design_task_digest(optimize_task)
    result = stack["coordinator"].execute(request)
    assert result.observation.status == ExperimentStatus.physically_valid
    assert result.receipt["status"] == "adapter_completed"
    assert stack["scheduler"].snapshot()["committed"]["forward_evaluations"] == 40
    assert harness.calls == [binding.task]


def test_registry_digest_mismatch_rejected_before_harness(tmp_path) -> None:
    request = _request()
    harness = FakeHarness(_run_result())
    stack = _stack(tmp_path, harness=harness)

    class BadRegistryDigestResolver:
        def resolve(self, req):
            return ResolvedTask(
                task_hash=req.task_hash,
                task_digest="b" * 64,
                task=_task(),
            )

    stack["adapter"].resolver = BadRegistryDigestResolver()
    result = stack["coordinator"].execute(request)
    assert result.receipt["status"] == "adapter_rejected"
    assert "registry task digest" in result.receipt["reason"]
    assert harness.calls == []
    assert stack["scheduler"].snapshot()["reserved"]["forward_evaluations"] == 0
