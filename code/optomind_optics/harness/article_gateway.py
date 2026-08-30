"""Local tool gateway boundary for the Article Scientific Harness (Stage 5).

The gateway enforces that only a locally compiled ``CompiledExperimentRequest``
(produced by ``article_proposals.compile_proposal``) may be delegated to an
explicit deterministic executor adapter.  Raw model envelopes, direct
solver/certificate/permission operations, and arbitrary code are never
accepted as executable input; they produce a structured ``GatewayRejection``.

The gateway itself never certifies physics: its result type carries only
adapter identity, status, summary, artifact references, and telemetry.  It can
never mint an ``ObservationCard``, metrics, artifacts, or a physics acceptance
certificate.  The existing deterministic TMM compiler/orchestrator remains the
execution and certification authority.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Literal, Mapping, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field

from optomind_optics.harness.article_proposals import (
    COMPILED_REQUEST_SCHEMA_VERSION,
    TMM_WORK_ACTIONS,
    ArticleCompilationAuthority,
    CompiledExperimentRequest,
    ProposalCompileError,
    _require_compiled_request,
    compute_task_hash,
    compute_request_id,
)
from optomind_optics.harness.contracts import ActionType
from optomind_optics.harness.qwen_policy import QWEN_POLICY_MODEL


GATEWAY_REJECTION_SCHEMA_VERSION = "gateway-rejection.v1"
ADAPTER_RESULT_SCHEMA_VERSION = "adapter-result.v1"
MAX_OUTPUT_REFS = 64
MAX_OUTPUT_REF_CHARS = 500
MAX_TELEMETRY_KEYS = 64


class GatewayAuthorizationError(ValueError):
    """Raised by the strict authorize path for unauthorized requests."""


class _GatewayModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GatewayRejection(_GatewayModel):
    schema_version: Literal["gateway-rejection.v1"] = "gateway-rejection.v1"
    request_id: Optional[str] = None
    category: Literal[
        "direct_model_execution",
        "malformed_request",
        "unauthorized_action",
        "identity_mismatch",
        "task_hash_mismatch",
        "capability_boundary",
        "budget_overflow",
        "adapter_failure",
        "untrusted_compiler",
    ]
    reason: str
    model_name: Literal["qwen3.7-flash"] = QWEN_POLICY_MODEL


class GatewayAdapterResult(_GatewayModel):
    """The only result the gateway returns after adapter delegation.

    Deliberately carries no metrics, no physics certificate, no ObservationCard,
    and no fabricated artifacts; the existing TMM authority must normalize any
    deeper result.
    """

    schema_version: Literal["adapter-result.v1"] = "adapter-result.v1"
    request_id: str
    adapter_name: str
    status: Literal["adapter_completed", "adapter_rejected"]
    summary: str = ""
    reason: str = ""
    output_refs: List[str] = Field(default_factory=list)
    telemetry: Dict[str, Any] = Field(default_factory=dict)
    model_name: Literal["qwen3.7-flash"] = QWEN_POLICY_MODEL


class DeterministicExecutorAdapter(Protocol):
    """Explicit adapter required for any gateway delegation."""

    def execute(self, request: CompiledExperimentRequest) -> Mapping[str, Any]: ...


class ArticleToolGateway:
    """Requires locally compiled requests and an explicit executor adapter."""

    def __init__(
        self,
        *,
        authority: ArticleCompilationAuthority,
        allowed_actions: Optional[Iterable[ActionType | str]] = None,
        run_id: Optional[str] = None,
        branch_id: Optional[str] = None,
    ) -> None:
        if not isinstance(authority, ArticleCompilationAuthority):
            raise TypeError("authority must be an ArticleCompilationAuthority")
        self.authority = authority
        if allowed_actions is None:
            self.allowed_actions = set(TMM_WORK_ACTIONS)
        else:
            self.allowed_actions = {
                item if isinstance(item, ActionType) else ActionType(str(item))
                for item in allowed_actions
            }
        self.run_id = str(run_id) if run_id is not None else None
        self.branch_id = str(branch_id) if branch_id is not None else None

    # -- strict authorization -------------------------------------------------

    def authorize(
        self, request: CompiledExperimentRequest | Mapping[str, Any]
    ) -> CompiledExperimentRequest:
        """Validate identity, action, and task-hash integrity; raise otherwise."""

        try:
            compiled = _require_compiled_request(request)
        except ProposalCompileError as exc:
            raise GatewayAuthorizationError(str(exc)) from exc
        if compiled.status != "compiled" or compiled.source != "article_compiler":
            raise GatewayAuthorizationError(
                "request is not a locally compiled Article request"
            )
        if compiled.authority_id != self.authority.authority_id:
            raise GatewayAuthorizationError(
                "compiler authority mismatch: request was not issued by this authority"
            )
        recomputed = compute_task_hash(
            compiled
        )
        if recomputed != compiled.task_hash:
            raise GatewayAuthorizationError("task hash does not match the request")
        expected_request_id = compute_request_id(
            compiled.task_hash, compiled.proposal_id
        )
        if expected_request_id != compiled.request_id:
            raise GatewayAuthorizationError(
                "request id does not match the compiled request content"
            )
        if not self.authority.verify(compiled):
            raise GatewayAuthorizationError("invalid compiler attestation")
        if compiled.allowed_action not in self.allowed_actions:
            raise GatewayAuthorizationError(
                f"action {compiled.allowed_action.value!r} is not allowed by this gateway"
            )
        if compiled.allowed_action != compiled.experiment.action_type:
            raise GatewayAuthorizationError(
                "allowed_action does not match the experiment action type"
            )
        if self.run_id is not None and compiled.run_id != self.run_id:
            raise GatewayAuthorizationError(
                f"run identity mismatch: {compiled.run_id!r} != {self.run_id!r}"
            )
        if self.branch_id is not None and compiled.branch_id != self.branch_id:
            raise GatewayAuthorizationError(
                f"branch identity mismatch: {compiled.branch_id!r} != {self.branch_id!r}"
            )
        return compiled

    # -- execution -----------------------------------------------------------

    def execute(
        self,
        request: Any,
        adapter: DeterministicExecutorAdapter,
    ) -> GatewayAdapterResult | GatewayRejection:
        """Delegate only authorized compiled requests; reject everything else.

        A raw model envelope (or any non-compiled input) is never executed and
        returns a structured ``GatewayRejection``.  Adapter output is stripped
        to the adapter-result contract before it is returned.
        """

        if not isinstance(request, CompiledExperimentRequest):
            if isinstance(request, Mapping):
                envelope_id = str(request.get("proposal_id") or "")
                return GatewayRejection(
                    request_id=envelope_id or None,
                    category="direct_model_execution",
                    reason=(
                        "raw model envelopes are not executable; compile a "
                        "CompiledExperimentRequest through article_proposals "
                        "and delegate only to an explicit deterministic adapter"
                    ),
                )
            return GatewayRejection(
                category="malformed_request",
                reason="execution requires a CompiledExperimentRequest object",
            )
        try:
            compiled = self.authorize(request)
        except GatewayAuthorizationError as exc:
            category = self._authorization_category(exc)
            return GatewayRejection(
                request_id=request.request_id,
                category=category,
                reason=str(exc),
            )
        try:
            raw_result = adapter.execute(compiled)
        except Exception as exc:
            return GatewayRejection(
                request_id=compiled.request_id,
                category="adapter_failure",
                reason=f"deterministic adapter failed: {exc}",
            )
        return self._normalize_adapter_result(compiled, raw_result)

    def reject_raw_model_envelope(
        self,
        envelope: Mapping[str, Any],
        *,
        reason: str = "direct model execution is forbidden",
    ) -> GatewayRejection:
        """Structured rejection for direct model execution attempts."""

        return GatewayRejection(
            request_id=str(envelope.get("proposal_id") or "").strip() or None,
            category="direct_model_execution",
            reason=reason,
        )

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _authorization_category(exc: GatewayAuthorizationError) -> Literal[
        "malformed_request",
        "unauthorized_action",
        "identity_mismatch",
        "task_hash_mismatch",
        "capability_boundary",
        "budget_overflow",
        "untrusted_compiler",
    ]:
        message = str(exc)
        if "not allowed by this gateway" in message or "does not match the experiment" in message:
            return "unauthorized_action"
        if "identity mismatch" in message:
            return "identity_mismatch"
        if "task hash" in message:
            return "task_hash_mismatch"
        if "request id" in message:
            return "task_hash_mismatch"
        if "authority" in message or "attestation" in message:
            return "untrusted_compiler"
        if "capability" in message:
            return "capability_boundary"
        if "budget" in message:
            return "budget_overflow"
        return "malformed_request"

    @staticmethod
    def _normalize_adapter_result(
        request: CompiledExperimentRequest,
        raw: Any,
    ) -> GatewayAdapterResult:
        if not isinstance(raw, Mapping):
            return GatewayAdapterResult(
                request_id=request.request_id,
                adapter_name="deterministic_adapter",
                status="adapter_rejected",
                reason="adapter returned a non-mapping result",
            )
        data = dict(raw)
        status = str(data.get("status") or "").strip()
        if status not in {"adapter_completed", "adapter_rejected"}:
            return GatewayAdapterResult(
                request_id=request.request_id,
                adapter_name="deterministic_adapter",
                status="adapter_rejected",
                reason=f"adapter returned unsupported status {status!r}",
            )
        output_refs = [
            str(item)[:MAX_OUTPUT_REF_CHARS]
            for item in (data.get("output_refs") or [])
            if isinstance(item, (str,))
        ][:MAX_OUTPUT_REFS]
        telemetry_raw = data.get("telemetry")
        telemetry = dict(telemetry_raw) if isinstance(telemetry_raw, Mapping) else {}
        telemetry = {
            str(key): value
            for key, value in list(telemetry.items())[:MAX_TELEMETRY_KEYS]
        }
        return GatewayAdapterResult(
            request_id=request.request_id,
            adapter_name=str(data.get("adapter_name") or "deterministic_adapter")[
                :200
            ],
            status=status,
            summary=str(data.get("summary") or "")[:2000],
            reason=str(data.get("reason") or "")[:2000],
            output_refs=output_refs,
            telemetry=telemetry,
        )


__all__ = [
    "ADAPTER_RESULT_SCHEMA_VERSION",
    "ArticleToolGateway",
    "DeterministicExecutorAdapter",
    "GATEWAY_REJECTION_SCHEMA_VERSION",
    "GatewayAdapterResult",
    "GatewayAuthorizationError",
    "GatewayRejection",
]
