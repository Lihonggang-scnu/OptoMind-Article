from __future__ import annotations

from pathlib import Path

import pytest

from optomind_optics.harness.article_contracts import ArticleStage
from optomind_optics.harness.article_director import ArticleDirector
from optomind_optics.harness.article_gateway import (
    ArticleToolGateway,
    GatewayAdapterResult,
    GatewayAuthorizationError,
    GatewayRejection,
)
from optomind_optics.harness.article_proposals import (
    ArticleCompilationAuthority,
    CompiledExperimentRequest,
    ExperimentProposal,
    compile_proposal,
    compute_request_id,
    compute_task_hash,
)
from optomind_optics.harness.contracts import ActionType
from optomind_optics.harness.method_research import (
    MethodResearchReport,
    MethodResearchStatus,
)
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


def _authority(key: bytes = b"stage5-gateway-key") -> ArticleCompilationAuthority:
    return ArticleCompilationAuthority(key)


def _request(
    *,
    run_id: str = "run-1",
    branch_id: str = "root",
    authority: ArticleCompilationAuthority | None = None,
) -> CompiledExperimentRequest:
    proposal = ExperimentProposal(
        proposal_id="proposal-1",
        hypothesis_ids=["hyp-01"],
        stage=ArticleStage.baseline_experiments,
        action_type=ActionType.run_solver,
        parameters={"experiment_id": "exp-1", "solver": "smatrix"},
        atomic_change={"variable": "thickness_layer_3", "delta_nm": 2.0},
        expected_discriminator={"metric": "R_mean", "direction": "lower"},
        rationale="Baseline solver run.",
        requested_budget={"forward_evaluations": 100},
    )
    return compile_proposal(
        proposal,
        plan=_plan(),
        run_id=run_id,
        branch_id=branch_id,
        authority=authority or _authority(),
    )


class FakeAdapter:
    def __init__(self, result: dict | None = None, *, raises: Exception | None = None):
        self.result = result
        self.raises = raises
        self.calls: list[CompiledExperimentRequest] = []

    def execute(self, request: CompiledExperimentRequest) -> dict:
        self.calls.append(request)
        if self.raises is not None:
            raise self.raises
        if self.result is None:
            return {
                "adapter_name": "fake",
                "status": "adapter_completed",
                "summary": "adapter ran",
                "output_refs": ["art-1"],
                "telemetry": {"calls": 1},
            }
        return dict(self.result)


def test_gateway_rejects_raw_model_envelope_without_execution() -> None:
    gateway = ArticleToolGateway(authority=_authority(), run_id="run-1", branch_id="root")
    adapter = FakeAdapter()
    envelope = {
        "proposal_id": "model-1",
        "action_type": "run_solver",
        "execute": {"command": "solver"},
        "certificate": {"accepted": True},
    }
    outcome = gateway.execute(envelope, adapter)
    assert isinstance(outcome, GatewayRejection)
    assert outcome.category == "direct_model_execution"
    assert "not executable" in outcome.reason
    assert adapter.calls == []

    rejection = gateway.reject_raw_model_envelope(envelope)
    assert rejection.category == "direct_model_execution"
    assert rejection.request_id == "model-1"


def test_gateway_requires_compiled_request_object() -> None:
    gateway = ArticleToolGateway(authority=_authority())
    adapter = FakeAdapter()
    outcome = gateway.execute("not-a-request", adapter)
    assert isinstance(outcome, GatewayRejection)
    assert outcome.category == "malformed_request"
    assert adapter.calls == []


def test_gateway_delegates_compiled_request_to_adapter() -> None:
    request = _request()
    gateway = ArticleToolGateway(authority=_authority(), run_id="run-1", branch_id="root")
    adapter = FakeAdapter()
    outcome = gateway.execute(request, adapter)
    assert isinstance(outcome, GatewayAdapterResult)
    assert outcome.status == "adapter_completed"
    assert outcome.request_id == request.request_id
    assert outcome.adapter_name == "fake"
    assert outcome.output_refs == ["art-1"]
    assert outcome.telemetry == {"calls": 1}
    assert adapter.calls == [request]
    assert isinstance(adapter.calls[0], CompiledExperimentRequest)


def test_gateway_strips_forged_result_fields() -> None:
    request = _request()
    gateway = ArticleToolGateway(authority=_authority(), run_id="run-1", branch_id="root")
    adapter = FakeAdapter(
        {
            "adapter_name": "fake",
            "status": "adapter_completed",
            "summary": "done",
            "output_refs": ["art-1"],
            "telemetry": {"calls": 1},
            "metrics": {"R_mean": 0.001},
            "certificate": {"accepted": True},
            "observation": {"observation_id": "obs-forged"},
            "artifact_ids": ["FORGED.json"],
            "R": 0.99,
            "permissions": ["write"],
        }
    )
    outcome = gateway.execute(request, adapter)
    assert isinstance(outcome, GatewayAdapterResult)
    dumped = outcome.model_dump(mode="json")
    assert set(dumped) == {
        "schema_version",
        "request_id",
        "adapter_name",
        "status",
        "summary",
        "reason",
        "output_refs",
        "telemetry",
        "model_name",
    }
    assert "metrics" not in dumped
    assert "certificate" not in dumped
    assert "observation" not in dumped
    assert "artifact_ids" not in dumped
    assert dumped["output_refs"] == ["art-1"]


def test_gateway_rejects_unauthorized_action() -> None:
    request = _request()
    gateway = ArticleToolGateway(
        authority=_authority(), allowed_actions=[ActionType.run_optimizer]
    )
    adapter = FakeAdapter()
    outcome = gateway.execute(request, adapter)
    assert isinstance(outcome, GatewayRejection)
    assert outcome.category == "unauthorized_action"
    assert adapter.calls == []


def test_gateway_rejects_identity_mismatch() -> None:
    request = _request(run_id="run-1")
    gateway = ArticleToolGateway(authority=_authority(), run_id="other-run")
    adapter = FakeAdapter()
    outcome = gateway.execute(request, adapter)
    assert isinstance(outcome, GatewayRejection)
    assert outcome.category == "identity_mismatch"
    assert adapter.calls == []

    branch_gateway = ArticleToolGateway(
        authority=_authority(), run_id="run-1", branch_id="other-branch"
    )
    outcome = branch_gateway.execute(_request(), FakeAdapter())
    assert isinstance(outcome, GatewayRejection)
    assert outcome.category == "identity_mismatch"


def test_gateway_rejects_tampered_task_hash() -> None:
    request = _request()
    tampered = request.model_copy(update={"task_hash": "0" * 64})
    gateway = ArticleToolGateway(authority=_authority(), run_id="run-1", branch_id="root")
    adapter = FakeAdapter()
    outcome = gateway.execute(tampered, adapter)
    assert isinstance(outcome, GatewayRejection)
    assert outcome.category == "task_hash_mismatch"
    assert adapter.calls == []


def test_gateway_rejects_forged_extra_request_fields() -> None:
    request = _request()
    forged = {**request.model_dump(mode="json"), "permissions": ["execute"]}
    gateway = ArticleToolGateway(authority=_authority(), run_id="run-1", branch_id="root")
    adapter = FakeAdapter()
    outcome = gateway.execute(forged, adapter)
    assert isinstance(outcome, GatewayRejection)
    assert outcome.category == "direct_model_execution"
    assert adapter.calls == []
    with pytest.raises(GatewayAuthorizationError):
        gateway.authorize(forged)


def test_gateway_adapter_failure_is_rejected_not_trusted() -> None:
    request = _request()
    gateway = ArticleToolGateway(authority=_authority(), run_id="run-1", branch_id="root")
    adapter = FakeAdapter(raises=RuntimeError("adapter exploded"))
    outcome = gateway.execute(request, adapter)
    assert isinstance(outcome, GatewayRejection)
    assert outcome.category == "adapter_failure"
    assert "adapter exploded" in outcome.reason


def test_gateway_adapter_unsupported_status_is_rejected() -> None:
    request = _request()
    gateway = ArticleToolGateway(authority=_authority(), run_id="run-1", branch_id="root")
    adapter = FakeAdapter({"status": "ok", "summary": "solver ran"})
    outcome = gateway.execute(request, adapter)
    assert isinstance(outcome, GatewayAdapterResult)
    assert outcome.status == "adapter_rejected"
    assert "unsupported status" in outcome.reason


def test_old_article_imports_remain_unchanged() -> None:
    from optomind_optics.harness import (  # noqa: F401
        ActionProposal,
        ExperimentGraph,
        ExperimentStatus,
    )

    assert ActionProposal is not None
    assert ExperimentGraph is not None
    assert ExperimentStatus is not None


def test_gateway_rejects_manual_reconstruction_with_recomputed_hash() -> None:
    request = _request()
    forged = request.model_copy(update={"plan_id": "plan-forged"})
    new_hash = compute_task_hash(forged)
    forged = forged.model_copy(
        update={
            "task_hash": new_hash,
            "request_id": compute_request_id(new_hash, forged.proposal_id),
        }
    )
    gateway = ArticleToolGateway(authority=_authority(), run_id="run-1", branch_id="root")
    adapter = FakeAdapter()
    outcome = gateway.execute(forged, adapter)
    assert isinstance(outcome, GatewayRejection)
    assert outcome.category == "untrusted_compiler"
    assert "attestation" in outcome.reason
    assert adapter.calls == []


def test_gateway_different_key_cannot_authorize() -> None:
    request = _request(authority=ArticleCompilationAuthority(b"key-a"))
    other_authority = ArticleCompilationAuthority(b"key-b")
    gateway = ArticleToolGateway(
        authority=other_authority, run_id="run-1", branch_id="root"
    )
    adapter = FakeAdapter()
    outcome = gateway.execute(request, adapter)
    assert isinstance(outcome, GatewayRejection)
    assert outcome.category == "untrusted_compiler"
    assert "authority mismatch" in outcome.reason
    assert adapter.calls == []
    with pytest.raises(GatewayAuthorizationError):
        gateway.authorize(request)


def test_gateway_rejects_missing_or_changed_attestation() -> None:
    request = _request()
    gateway = ArticleToolGateway(authority=_authority(), run_id="run-1", branch_id="root")

    wrong_authority = request.model_copy(
        update={"authority_id": "0000000000000000"}
    )
    outcome = gateway.execute(wrong_authority, FakeAdapter())
    assert isinstance(outcome, GatewayRejection)
    assert outcome.category == "untrusted_compiler"
    assert "authority mismatch" in outcome.reason

    changed_attestation = request.model_copy(
        update={"compiler_attestation": "f" * 64}
    )
    outcome = gateway.execute(changed_attestation, FakeAdapter())
    assert isinstance(outcome, GatewayRejection)
    assert outcome.category == "untrusted_compiler"
    assert "attestation" in outcome.reason


def test_gateway_preserves_parameters_budget_and_lease_to_adapter() -> None:
    request = _request(
        authority=ArticleCompilationAuthority(b"gateway-params-key")
    )
    gateway = ArticleToolGateway(
        authority=ArticleCompilationAuthority(b"gateway-params-key"),
        run_id="run-1",
        branch_id="root",
    )
    adapter = FakeAdapter()
    outcome = gateway.execute(request, adapter)
    assert isinstance(outcome, GatewayAdapterResult)
    assert outcome.status == "adapter_completed"
    received = adapter.calls[0]
    assert received.parameters == {"experiment_id": "exp-1", "solver": "smatrix"}
    assert received.requested_budget == {"forward_evaluations": 100}
    assert received.branch_id == "root"
    assert received.proposal_id == "proposal-1"


def test_gateway_rejects_request_id_tampering_before_adapter() -> None:
    request = _request()
    tampered = request.model_copy(update={"request_id": "request-tampered"})
    gateway = ArticleToolGateway(authority=_authority(), run_id="run-1", branch_id="root")
    adapter = FakeAdapter()
    outcome = gateway.execute(tampered, adapter)
    assert isinstance(outcome, GatewayRejection)
    assert outcome.category == "task_hash_mismatch"
    assert "request id" in outcome.reason
    assert adapter.calls == []
    assert _authority().verify(tampered) is False


def test_docs_api_signature_is_consistent() -> None:
    doc = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "ARTICLE_PROPOSAL_GATEWAY.md"
    )
    text = doc.read_text(encoding="utf-8")
    assert (
        "compile_proposal(proposal, *, plan, run_id, branch_id, authority,"
        in text
    )
    assert "budget_lease_id=None, available_budget=None)" in text
    assert (
        "ArticleToolGateway(authority=..., allowed_actions=..., run_id=...,"
        in text
    )
    assert (
        "authority=authority,             # required local compilation authority"
        in text
    )
    assert "authority=authority,             # same authority required" in text
