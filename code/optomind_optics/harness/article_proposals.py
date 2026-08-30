"""Strict whitelist ExperimentProposal contract and Article proposal compiler.

Stage 5 of the Article Scientific Harness.  A model (locked to
``qwen3.7-flash``) may draft only a proposal envelope; program code creates
IDs, schema markers, action allowlists, task hashes, budget lease references,
and statuses.  The compiler turns a validated proposal plus an
``ArticleDirectorPlan`` into an immutable ``CompiledExperimentRequest`` that
is the only input a tool gateway may execute.

Fail-closed boundaries:
- Forged/extra fields, unknown actions, invalid stages, empty hypothesis
  references, and non-bounded parameters are rejected by the strict models.
- The compiler rejects incompatible (or ambiguous) capability, unknown
  hypothesis IDs, non-whitelisted actions, and budget overflows.
- No proposal or compiled request can carry solver results, certificates,
  metrics, permissions, or executable code.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
from typing import Any, Dict, List, Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from optomind_optics.harness.article_contracts import (
    ArticleStage,
    ExperimentCard,
)
from optomind_optics.harness.article_director import ArticleDirectorPlan
from optomind_optics.harness.contracts import ActionType, ExperimentStatus
from optomind_optics.harness.design_task import OpticalDesignTask
from optomind_optics.harness.problem_analyzer import TMMCompatibility
from tmm_engine.hashing import canonical_json_dumps


PROPOSAL_SCHEMA_VERSION = "experiment-proposal.v1"
COMPILED_REQUEST_SCHEMA_VERSION = "compiled-experiment-request.v1"
PROPOSAL_MODEL_NAME = "qwen3.7-flash"

# Only true experimental stages may be proposed by a model.  Fresh replay is
# deterministic infrastructure and must not be model-proposed; later pipeline
# stages are not experiments.
EXPERIMENTAL_STAGES: frozenset[ArticleStage] = frozenset(
    {
        ArticleStage.baseline_experiments,
        ArticleStage.exploration,
        ArticleStage.controlled_improvement,
        ArticleStage.discriminative_experiments,
        ArticleStage.robustness_ablation,
    }
)

# Documented, non-tiny bounds for model-draftable fields.
MAX_PARAMETER_KEYS = 16
MAX_PARAMETER_STRING_CHARS = 500
MAX_PARAMETER_LIST_ITEMS = 64
MAX_NESTED_KEYS = 16
MAX_NESTED_STRING_CHARS = 300
MAX_HYPOTHESIS_REFERENCES = 8
MAX_RATIONALE_CHARS = 2000

# Hard budget caps applied by the compiler before any execution.
BUDGET_CAPS: Dict[str, float] = {
    "wall_time_seconds": 86400.0,
    "forward_evaluations": 100000.0,
    "optimizer_runs": 200.0,
    "qwen_calls": 100.0,
    "qwen_input_tokens": 2000000.0,
    "qwen_output_tokens": 500000.0,
    "qwen_cost_cny": 100.0,
}
BUDGET_KEYS = frozenset(BUDGET_CAPS)

# Actions a model may propose for bounded TMM work (never policy or stop).
TMM_WORK_ACTIONS: frozenset[ActionType] = frozenset(
    {
        ActionType.generate_baseline,
        ActionType.run_solver,
        ActionType.run_optimizer,
        ActionType.run_convergence_audit,
        ActionType.run_reference_solver,
        ActionType.run_robustness_audit,
    }
)

# Parameter keys a model may set per TMM work action.  Anything else is
# non-bounded and rejected.
ACTION_PARAMETER_KEYS: Dict[ActionType, frozenset[str]] = {
    ActionType.generate_baseline: frozenset(
        {"experiment_id", "route_id", "notes"}
    ),
    ActionType.run_solver: frozenset(
        {"experiment_id", "solver", "requested_outputs", "notes"}
    ),
    ActionType.run_optimizer: frozenset(
        {"experiment_id", "optimizer_id", "maximum_evaluations", "notes"}
    ),
    ActionType.run_convergence_audit: frozenset(
        {"experiment_id", "max_refinements", "notes"}
    ),
    ActionType.run_reference_solver: frozenset(
        {"experiment_id", "candidate_id", "notes"}
    ),
    ActionType.run_robustness_audit: frozenset(
        {"experiment_id", "candidate_id", "samples", "notes"}
    ),
}


class ProposalCompileError(ValueError):
    """Raised when a validated proposal cannot be compiled into a request."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _bounded_scalar(value: Any, *, string_limit: int, context: str) -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        if len(value) > string_limit:
            raise ValueError(
                f"{context} string exceeds {string_limit} characters"
            )
        return
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{context} must be finite")
        if isinstance(value, (int, float)) and not (-1_000_000 <= value <= 1_000_000):
            raise ValueError(f"{context} must be within the documented numeric bound")
        return
    if isinstance(value, list):
        if len(value) > MAX_PARAMETER_LIST_ITEMS:
            raise ValueError(f"{context} list exceeds {MAX_PARAMETER_LIST_ITEMS} items")
        for item in value:
            if not isinstance(item, (str, int, float, bool, type(None))):
                raise ValueError(f"{context} list contains a non-scalar item")
            _bounded_scalar(item, string_limit=string_limit, context=context)
        return
    raise ValueError(f"{context} must be a scalar or list of scalars")


def _bounded_mapping(values: Mapping[str, Any], *, context: str) -> None:
    if len(values) > MAX_NESTED_KEYS:
        raise ValueError(f"{context} exceeds {MAX_NESTED_KEYS} keys")
    for key, value in values.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"{context} keys must be non-empty strings")
        _bounded_scalar(
            value, string_limit=MAX_NESTED_STRING_CHARS, context=f"{context}.{key}"
        )


def _validate_parameters(action_type: ActionType, parameters: Mapping[str, Any]) -> Dict[str, Any]:
    if len(parameters) > MAX_PARAMETER_KEYS:
        raise ValueError(f"parameters exceed {MAX_PARAMETER_KEYS} keys")
    allowed = ACTION_PARAMETER_KEYS.get(action_type)
    if allowed is None:
        raise ValueError(f"action {action_type.value!r} is not proposable")
    unknown = sorted(set(parameters) - set(allowed))
    if unknown:
        raise ValueError(
            f"non-bounded parameter keys for {action_type.value!r}: {unknown}"
        )
    result: Dict[str, Any] = {}
    for key, value in parameters.items():
        _bounded_scalar(
            value, string_limit=MAX_PARAMETER_STRING_CHARS, context=f"parameters.{key}"
        )
        result[key] = value
    return result


def _validate_requested_budget(budget: Mapping[str, Any]) -> Dict[str, Any]:
    if len(budget) > len(BUDGET_KEYS):
        raise ValueError("requested_budget exceeds the known resource keys")
    unknown = sorted(set(budget) - BUDGET_KEYS)
    if unknown:
        raise ValueError(f"unknown budget resources: {unknown}")
    result: Dict[str, Any] = {}
    for key, value in budget.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"requested_budget.{key} must be numeric")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"requested_budget.{key} must be finite")
        if value < 0:
            raise ValueError(f"requested_budget.{key} must be non-negative")
        result[key] = value
    return result


class ExperimentProposal(_StrictModel):
    """The only shape a model may draft; strictly bounded and auditable."""

    schema_version: Literal["experiment-proposal.v1"] = "experiment-proposal.v1"
    proposal_id: str
    hypothesis_ids: List[str] = Field(min_length=1, max_length=MAX_HYPOTHESIS_REFERENCES)
    stage: ArticleStage
    action_type: ActionType
    parameters: Dict[str, Any] = Field(default_factory=dict)
    atomic_change: Dict[str, Any] = Field(default_factory=dict)
    expected_discriminator: Dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    uncertainty: str = ""
    requested_budget: Dict[str, Any] = Field(default_factory=dict)
    model_name: Literal["qwen3.7-flash"] = PROPOSAL_MODEL_NAME
    created_at: Optional[str] = None

    @field_validator("proposal_id")
    @classmethod
    def _non_empty_proposal_id(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("proposal_id must be a non-empty string")
        return text

    @field_validator("stage")
    @classmethod
    def _experimental_stage_only(cls, value: ArticleStage) -> ArticleStage:
        if value not in EXPERIMENTAL_STAGES:
            raise ValueError(
                f"stage {value.value!r} is not an experimental stage; allowed: "
                + ", ".join(sorted(item.value for item in EXPERIMENTAL_STAGES))
            )
        return value

    @field_validator("hypothesis_ids")
    @classmethod
    def _unique_hypotheses(cls, values: List[str]) -> List[str]:
        cleaned = [str(item).strip() for item in values if str(item).strip()]
        if not cleaned:
            raise ValueError("hypothesis_ids must not be empty")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("hypothesis_ids must be unique")
        return cleaned

    @field_validator("rationale", "uncertainty")
    @classmethod
    def _bounded_text(cls, value: str) -> str:
        text = str(value or "").strip()
        if len(text) > MAX_RATIONALE_CHARS:
            raise ValueError(f"text exceeds {MAX_RATIONALE_CHARS} characters")
        return text

    @model_validator(mode="after")
    def _bounded_model_fields(self) -> "ExperimentProposal":
        _validate_parameters(self.action_type, self.parameters)
        _bounded_mapping(self.atomic_change, context="atomic_change")
        _bounded_mapping(self.expected_discriminator, context="expected_discriminator")
        _validate_requested_budget(self.requested_budget)
        return self


class CompiledExperimentRequest(_StrictModel):
    """Immutable, locally compiled request; the only executable input."""

    schema_version: Literal["compiled-experiment-request.v1"] = (
        "compiled-experiment-request.v1"
    )
    request_id: str
    task_hash: str
    plan_id: str
    capability_id: str
    run_id: str
    branch_id: str
    proposal_id: str
    authority_id: str
    compiler_attestation: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    requested_budget: Dict[str, Any] = Field(default_factory=dict)
    budget_lease_id: Optional[str] = None
    task_digest: str = ""
    experiment: ExperimentCard
    allowed_action: ActionType
    source: Literal["article_compiler"] = "article_compiler"
    status: Literal["compiled"] = "compiled"

    @field_validator("request_id", "task_hash", "plan_id", "capability_id", "run_id", "branch_id", "proposal_id", "authority_id", "compiler_attestation")
    @classmethod
    def _non_empty_identity_fields(cls, value: str, info: Any) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return text

    @field_validator("task_digest")
    @classmethod
    def _task_digest_hex(cls, value: str) -> str:
        text = str(value or "").strip()
        if text and (
            len(text) != 64
            or any(char not in "0123456789abcdef" for char in text)
        ):
            raise ValueError(
                "task_digest must be empty or a 64-character lowercase hex digest"
            )
        return text


def _canonical_json(value: Any) -> str:
    return canonical_json_dumps(value)


def _request_content(request: CompiledExperimentRequest) -> Dict[str, Any]:
    """Canonical content covered by the task hash and compiler attestation."""

    data = request.model_dump(mode="json")
    data.pop("request_id", None)
    data.pop("task_hash", None)
    data.pop("compiler_attestation", None)
    experiment = data.get("experiment")
    if isinstance(experiment, dict):
        experiment.pop("task_hash", None)
    return data


def compute_task_hash(
    request: CompiledExperimentRequest | Mapping[str, Any],
) -> str:
    """Deterministic public integrity hash over the compiled request content."""

    compiled = (
        request
        if isinstance(request, CompiledExperimentRequest)
        else CompiledExperimentRequest.model_validate(request)
    )
    payload = _canonical_json(_request_content(compiled)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def compute_optical_design_task_digest(
    task: OpticalDesignTask | Mapping[str, Any],
) -> str:
    """Deterministic canonical SHA256 of the exact OpticalDesignTask content."""

    task_model = (
        task
        if isinstance(task, OpticalDesignTask)
        else OpticalDesignTask.model_validate(task)
    )
    payload = _canonical_json(task_model.model_dump(mode="json")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def compute_request_id(task_hash: str, proposal_id: str) -> str:
    """Deterministic audit identity derived from the task hash and proposal."""

    return "request-" + hashlib.sha256(
        _canonical_json(
            {
                "task_hash": task_hash,
                "proposal_id": proposal_id,
            }
        ).encode("utf-8")
    ).hexdigest()[:16]


class ArticleCompilationAuthority:
    """Local HMAC-SHA256 compilation authority (provenance, not just hash).

    The key is caller-supplied and local; it never appears in Qwen input or in
    any serialized request.  Only the compiler (holding the key) can produce a
    valid ``compiler_attestation``; manual reconstruction or ``model_copy`` of
    a request cannot forge one.
    """

    PREFIX = b"article-compilation-authority.v1:"

    def __init__(
        self,
        key: bytes | str,
        *,
        authority_id: Optional[str] = None,
    ) -> None:
        self.key = key if isinstance(key, bytes) else str(key).encode("utf-8")
        if not self.key:
            raise ValueError("compilation authority key must be non-empty")
        self.authority_id = authority_id or hashlib.sha256(
            self.PREFIX + self.key
        ).hexdigest()[:16]

    def attest(self, request: CompiledExperimentRequest) -> str:
        payload = _canonical_json(
            {
                **_request_content(request),
                "task_hash": request.task_hash,
                "request_id": request.request_id,
            }
        ).encode("utf-8")
        return hmac.new(self.key, payload, hashlib.sha256).hexdigest()

    def verify(self, request: CompiledExperimentRequest) -> bool:
        expected = self.attest(request)
        return hmac.compare_digest(expected, str(request.compiler_attestation))


def _validate_plan_capability(plan: ArticleDirectorPlan) -> None:
    if plan.capability.status != TMMCompatibility.compatible:
        raise ProposalCompileError(
            "capability is not compatible: "
            f"{plan.capability.status.value} ({plan.capability.recommended_next_action})"
        )


def _check_budget_within_caps(budget: Mapping[str, Any]) -> None:
    for key, value in budget.items():
        cap = BUDGET_CAPS[key]
        if float(value) > cap:
            raise ProposalCompileError(
                f"requested_budget.{key} {value} exceeds documented cap {cap}"
            )


def _check_available_budget(
    requested: Mapping[str, Any], available: Optional[Mapping[str, Any]]
) -> None:
    if available is None:
        return
    unknown = sorted(set(available) - BUDGET_KEYS)
    if unknown:
        raise ProposalCompileError(
            f"available_budget has unknown resources: {unknown}"
        )
    for key, value in available.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ProposalCompileError(f"available_budget.{key} must be numeric")
        if value < 0:
            raise ProposalCompileError(
                f"available_budget.{key} must be non-negative"
            )
    for key, value in requested.items():
        available_value = available.get(key)
        if available_value is not None and float(value) > float(available_value):
            raise ProposalCompileError(
                f"requested_budget.{key} {value} exceeds available budget "
                f"{available_value}"
            )


def compile_proposal(
    proposal: ExperimentProposal | Mapping[str, Any],
    *,
    plan: ArticleDirectorPlan | Mapping[str, Any],
    run_id: str,
    branch_id: str,
    authority: ArticleCompilationAuthority,
    budget_lease_id: Optional[str] = None,
    available_budget: Optional[Mapping[str, Any]] = None,
    task: Optional[OpticalDesignTask | Mapping[str, Any]] = None,
) -> CompiledExperimentRequest:
    """Deterministically compile one validated proposal into a request.

    Rejects incompatible/ambiguous capability, unknown hypothesis IDs,
    non-whitelisted actions, non-experimental stages, budget overflows
    (global caps and caller-supplied ``available_budget``), and missing
    identity.  The request preserves normalized action parameters and the
    requested budget for Stage 6 reservation, and carries a local HMAC
    compiler attestation.  Program code creates the request id, task hash,
    experiment card, budget lease reference, and status.

    When ``task`` is supplied, its canonical content digest is bound into the
    request (``task_digest``) and is therefore covered by ``task_hash``,
    ``request_id``, and the compiler attestation; Stage 6 execution requires
    this binding and fails closed without it.  Compile-only requests may omit
    ``task`` for non-execution inspection.
    """

    try:
        proposal_model = (
            proposal
            if isinstance(proposal, ExperimentProposal)
            else ExperimentProposal.model_validate(proposal)
        )
    except ValidationError as exc:
        raise ProposalCompileError(f"proposal is invalid: {exc}") from exc

    try:
        plan_model = (
            plan
            if isinstance(plan, ArticleDirectorPlan)
            else ArticleDirectorPlan.model_validate(plan)
        )
    except ValidationError as exc:
        raise ProposalCompileError(f"plan is invalid: {exc}") from exc

    _validate_plan_capability(plan_model)
    if proposal_model.action_type not in TMM_WORK_ACTIONS:
        raise ProposalCompileError(
            f"action {proposal_model.action_type.value!r} is not in the TMM work allowlist"
        )
    if proposal_model.stage not in EXPERIMENTAL_STAGES:
        raise ProposalCompileError(
            f"stage {proposal_model.stage.value!r} is not an experimental stage"
        )
    try:
        parameters = _validate_parameters(
            proposal_model.action_type, proposal_model.parameters
        )
        requested_budget = dict(
            _validate_requested_budget(proposal_model.requested_budget)
        )
    except ValueError as exc:
        raise ProposalCompileError(f"proposal is invalid: {exc}") from exc
    _check_budget_within_caps(proposal_model.requested_budget)
    _check_available_budget(requested_budget, available_budget)

    known_hypotheses = {item.hypothesis_id for item in plan_model.hypotheses}
    unknown = sorted(set(proposal_model.hypothesis_ids) - known_hypotheses)
    if unknown:
        raise ProposalCompileError(
            f"unknown hypothesis IDs in plan: {unknown}"
        )

    if not run_id or not str(run_id).strip():
        raise ProposalCompileError("run_id must be a non-empty string")
    if not branch_id or not str(branch_id).strip():
        raise ProposalCompileError("branch_id must be a non-empty string")
    if not isinstance(authority, ArticleCompilationAuthority):
        raise ProposalCompileError(
            "an ArticleCompilationAuthority is required to compile a proposal"
        )
    if budget_lease_id is not None and not str(budget_lease_id).strip():
        raise ProposalCompileError("budget_lease_id must be non-empty when supplied")
    task_digest = ""
    if task is not None:
        try:
            task_model = (
                task
                if isinstance(task, OpticalDesignTask)
                else OpticalDesignTask.model_validate(task)
            )
        except ValidationError as exc:
            raise ProposalCompileError(f"task is invalid: {exc}") from exc
        task_digest = compute_optical_design_task_digest(task_model)

    experiment_id = "experiment-" + hashlib.sha256(
        _canonical_json(
            {
                "proposal_id": proposal_model.proposal_id,
                "plan_id": plan_model.plan_id,
                "run_id": run_id,
            }
        ).encode("utf-8")
    ).hexdigest()[:16]
    card = ExperimentCard(
        experiment_id=experiment_id,
        hypothesis_ids=list(proposal_model.hypothesis_ids),
        action_type=proposal_model.action_type,
        task_hash="",  # filled below after the card is fully known
        stage=proposal_model.stage,
        status=ExperimentStatus.proposed,
        atomic_change=dict(proposal_model.atomic_change),
        expected_discriminator=dict(proposal_model.expected_discriminator),
        budget_lease_id=budget_lease_id,
        artifact_ids=[],
    )
    draft = CompiledExperimentRequest(
        request_id="pending",
        task_hash="pending",
        plan_id=plan_model.plan_id,
        capability_id=plan_model.capability.capability_id,
        run_id=run_id,
        branch_id=branch_id,
        proposal_id=proposal_model.proposal_id,
        authority_id=authority.authority_id,
        compiler_attestation="pending",
        parameters=parameters,
        requested_budget=requested_budget,
        budget_lease_id=budget_lease_id,
        task_digest=task_digest,
        experiment=card,
        allowed_action=proposal_model.action_type,
    )
    task_hash = compute_task_hash(draft)
    request_id = compute_request_id(task_hash, proposal_model.proposal_id)
    signed = draft.model_copy(
        update={
            "request_id": request_id,
            "task_hash": task_hash,
            "experiment": card.model_copy(update={"task_hash": task_hash}),
        }
    )
    attestation = authority.attest(signed)
    return signed.model_copy(update={"compiler_attestation": attestation})


def _require_compiled_request(
    request: CompiledExperimentRequest | Mapping[str, Any],
) -> CompiledExperimentRequest:
    if isinstance(request, CompiledExperimentRequest):
        return request
    try:
        return CompiledExperimentRequest.model_validate(request)
    except ValidationError as exc:
        raise ProposalCompileError(f"not a compiled experiment request: {exc}") from exc


__all__ = [
    "ACTION_PARAMETER_KEYS",
    "ArticleCompilationAuthority",
    "BUDGET_CAPS",
    "BUDGET_KEYS",
    "COMPILED_REQUEST_SCHEMA_VERSION",
    "CompiledExperimentRequest",
    "EXPERIMENTAL_STAGES",
    "ExperimentProposal",
    "PROPOSAL_MODEL_NAME",
    "PROPOSAL_SCHEMA_VERSION",
    "ProposalCompileError",
    "TMM_WORK_ACTIONS",
    "compile_proposal",
    "compute_optical_design_task_digest",
    "compute_task_hash",
    "compute_request_id",
]
