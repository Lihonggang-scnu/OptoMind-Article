"""Safe production entry point and telemetry for the eight-stage pipeline.

The accepted :mod:`article_pipeline` remains the scientific authority.  This
module only validates caller configuration, assembles the production factory,
dispatches ``run`` or ``resume``, and writes a compact integration summary.
Credentials and compilation-authority material are never serialized.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

from optomind_research.runtime.artifact_store import atomic_write_json
from optomind_research.runtime.cost_ledger import estimate_call_cost_cny

from .article_pipeline import ArticlePipelineRequest, ArticlePipelineResult
from .article_pipeline_factory import (
    ProductionArticlePipelineFactory,
    ProductionAssemblyConfig,
)
from .article_proposals import ArticleCompilationAuthority
from .method_research import (
    DefaultMethodResearchOnlineClient,
    QwenMethodFindingSynthesizer,
)
from .article_memory_boundary import initialize_article_memory_workspace


SUMMARY_FILENAME = "ARTICLE_PIPELINE_INTEGRATION_SUMMARY.json"
SUMMARY_SCHEMA_VERSION = "article-pipeline-integration-summary.v1"
AUTHORITY_ENVIRONMENT_VARIABLE = "ARTICLE_COMPILATION_AUTHORITY_KEY"


class ArticleIntegrationError(RuntimeError):
    """Invalid integration configuration or unsafe summary persistence."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ArticleIntegrationOptions(_StrictModel):
    """Non-secret caller options for one production assembly."""

    question: str
    run_id: str
    branch_id: str = "root"
    work_dir: str
    execution_root: str
    article_memory_path: Optional[str] = None
    review_kb_paths: tuple[str, ...] = Field(default_factory=tuple)
    maximum_routes: int = 4
    resume: bool = False
    force_mock: Optional[bool] = None
    online_research: bool = False

    @field_validator("question", "run_id", "branch_id", "work_dir", "execution_root")
    @classmethod
    def _required_text(cls, value: str, info: Any) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{info.field_name} must be non-empty")
        return text

    @field_validator("maximum_routes")
    @classmethod
    def _positive_routes(cls, value: int) -> int:
        if isinstance(value, bool) or int(value) < 1:
            raise ValueError("maximum_routes must be a positive integer")
        return int(value)


class QwenUsageRow(_StrictModel):
    """Whitelisted, credential-free telemetry for one logical model call."""

    stage: str
    call_index: int
    model_name: str = "unknown"
    agent_name: str = ""
    success: Optional[bool] = None
    failure: Optional[bool] = None
    mock_llm: bool = False
    token_counts_source: str = "unavailable"
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_list_price_cost_cny: float = 0.0
    request_attempt_count: int = 0
    retry_count: int = 0
    api_key_candidate_count: int = 0
    api_key_rotation_count: int = 0
    api_key_masked: str = ""
    fallback_used: bool = False
    model_fallback_used: bool = False
    attempted_models: tuple[str, ...] = Field(default_factory=tuple)
    partial_stream: bool = False
    error_type: str = ""


class ArticleIntegrationExecution(_StrictModel):
    """In-memory return envelope for CLI and programmatic callers."""

    result: ArticlePipelineResult
    summary: dict[str, Any]
    summary_path: str


_BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+\-/=]+")
_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)((?:api[_ -]?key|authority[_ -]?key|secret)\s*[:=]\s*)[^\s,;]+"
)
_KEY_PATTERN = re.compile(r"(?i)\b(?:sk|dashscope|qwen)-[A-Za-z0-9_-]{8,}\b")


def _safe_text(value: Any, *, secrets: Sequence[str] = (), limit: int = 800) -> str:
    text = str(value or "").replace("\x00", " ").replace("\r", " ").replace("\n", " ")
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = _BEARER_PATTERN.sub(r"\1[REDACTED]", text)
    text = _ASSIGNMENT_PATTERN.sub(r"\1[REDACTED]", text)
    text = _KEY_PATTERN.sub("[REDACTED]", text)
    text = " ".join(text.split())
    return text[:limit]


def _non_negative_int(value: Any) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, number)


def _optional_bool(value: Any) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def _usage_sources(result: ArticlePipelineResult | Any) -> Iterable[tuple[str, Mapping[str, Any]]]:
    analysis = getattr(result, "problem_analysis", None)
    for row in getattr(analysis, "usage", ()) or ():
        if isinstance(row, Mapping):
            yield "problem_analysis", row

    research = getattr(result, "method_research", None)
    telemetry = getattr(research, "telemetry", None)
    for row in getattr(telemetry, "usage", ()) or ():
        if isinstance(row, Mapping):
            yield "method_research", row

    strategy = getattr(result, "strategy_plan", None)
    for row in getattr(strategy, "usage", ()) or ():
        if isinstance(row, Mapping):
            yield "strategy_planning", row

    director = getattr(result, "director_plan", None)
    director_usage = getattr(director, "usage", None)
    if isinstance(director_usage, Mapping) and director_usage:
        yield "article_director", director_usage

    for binding in getattr(result, "route_task_bindings", ()) or ():
        if getattr(binding, "compiler_status", "") == "not_run":
            # Local deterministic audit metadata (the bounded not-run reason)
            # is not a model call: it must never become a phantom zero-token
            # usage row in the cost ledger.
            continue
        usage = getattr(binding, "compiler_usage", None)
        if not isinstance(usage, Mapping) or not usage:
            continue
        nested = usage.get("usage")
        if isinstance(nested, (list, tuple)) and nested:
            # New structured compiler_usage carries one row per task-compiler
            # attempt.  Count each attempt exactly once; never also count the
            # aggregate outer mapping.
            for row in nested:
                if isinstance(row, Mapping) and row:
                    yield "route_task_binding", row
        else:
            # Legacy flat usage mapping remains backward compatible.
            yield "route_task_binding", usage

    planning = getattr(result, "experiment_planning", None)
    for row in getattr(planning, "usage", ()) or ():
        if isinstance(row, Mapping):
            yield "experiment_planning", row


def collect_qwen_usage(result: ArticlePipelineResult | Any) -> tuple[QwenUsageRow, ...]:
    """Flatten all model-bearing Stage 1-6 telemetry without secret fields."""

    rows: list[QwenUsageRow] = []
    for call_index, (stage, usage) in enumerate(_usage_sources(result), 1):
        provider_input = _non_negative_int(usage.get("input_tokens"))
        provider_output = _non_negative_int(usage.get("output_tokens"))
        provider_total = _non_negative_int(usage.get("total_tokens"))
        source_hint = str(usage.get("token_counts_source") or "").strip().lower()
        has_provider_counts = source_hint == "provider" or any(
            (provider_input, provider_output, provider_total)
        )
        mock_llm = bool(usage.get("mock_llm"))
        if has_provider_counts:
            input_tokens = provider_input
            output_tokens = provider_output
            total_tokens = provider_total or provider_input + provider_output
            token_source = "mock_provider" if mock_llm else "provider"
        else:
            input_tokens = _non_negative_int(usage.get("estimated_input_tokens"))
            output_tokens = _non_negative_int(usage.get("estimated_output_tokens"))
            total_tokens = input_tokens + output_tokens
            if total_tokens:
                token_source = "mock_estimated" if mock_llm else "estimated"
            else:
                token_source = "unavailable"
        model_name = _safe_text(usage.get("model_name") or "unknown", limit=120)
        cost = (
            estimate_call_cost_cny(model_name, input_tokens, output_tokens)
            if total_tokens and not mock_llm
            else 0.0
        )
        attempted_models = usage.get("attempted_models") or ()
        if not isinstance(attempted_models, (list, tuple)):
            attempted_models = ()
        rows.append(
            QwenUsageRow(
                stage=stage,
                call_index=call_index,
                model_name=model_name,
                agent_name=_safe_text(usage.get("agent_name"), limit=120),
                success=_optional_bool(usage.get("success")),
                failure=_optional_bool(usage.get("failure")),
                mock_llm=mock_llm,
                token_counts_source=token_source,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                estimated_list_price_cost_cny=round(float(cost), 8),
                request_attempt_count=_non_negative_int(
                    usage.get("request_attempt_count") or usage.get("call_count")
                ),
                retry_count=_non_negative_int(usage.get("retry_count")),
                api_key_candidate_count=_non_negative_int(
                    usage.get("api_key_candidate_count")
                ),
                api_key_rotation_count=_non_negative_int(
                    usage.get("api_key_rotation_count")
                ),
                api_key_masked=_safe_text(usage.get("api_key_masked"), limit=80),
                fallback_used=bool(usage.get("fallback_used")),
                model_fallback_used=bool(usage.get("model_fallback_used")),
                attempted_models=tuple(
                    _safe_text(item, limit=120) for item in attempted_models
                ),
                partial_stream=bool(usage.get("partial_stream")),
                error_type=_safe_text(usage.get("error_type"), limit=160),
            )
        )
    return tuple(rows)


def _counter(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(str(value) for value in values).items()))


def _safe_messages(values: Iterable[Any], secrets: Sequence[str]) -> list[str]:
    return [_safe_text(value, secrets=secrets) for value in values]


def _summary_id(payload: Mapping[str, Any]) -> str:
    canonical = dict(payload)
    canonical.pop("summary_id", None)
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _attempt_record(payload: Mapping[str, Any]) -> dict[str, Any]:
    qwen = payload.get("qwen_usage")
    totals = qwen.get("totals", {}) if isinstance(qwen, Mapping) else {}
    receipts = payload.get("receipts")
    receipt_count = len(receipts) if isinstance(receipts, list) else 0
    return {
        "mode": str(payload.get("mode") or ""),
        "pipeline_status": str(payload.get("pipeline_status") or ""),
        "pipeline_result_id": str(payload.get("pipeline_result_id") or ""),
        "elapsed_seconds": float(payload.get("elapsed_seconds") or 0.0),
        "receipt_count": receipt_count,
        "hard_failure": bool(payload.get("hard_failure")),
        "provider_fail_open": bool(payload.get("provider_fail_open")),
        "validation_errors": list(payload.get("validation_errors") or ()),
        "warnings": list(payload.get("warnings") or ()),
        "qwen_logical_call_count": _non_negative_int(
            totals.get("logical_call_count")
        ),
        "qwen_billable_total_tokens": _non_negative_int(
            totals.get("billable_total_tokens")
        ),
        "qwen_estimated_list_price_cost_cny": round(
            max(0.0, float(totals.get("estimated_list_price_cost_cny") or 0.0)),
            8,
        ),
    }


def _bounded_attempt_history(
    existing: Mapping[str, Any], new_payload: Mapping[str, Any]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    prior = existing.get("attempts")
    if isinstance(prior, list):
        rows.extend(dict(item) for item in prior if isinstance(item, Mapping))
    if not rows:
        rows.append(_attempt_record(existing))
    current = _attempt_record(new_payload)
    if not rows or rows[-1] != current:
        rows.append(current)
    return rows[-20:]


def build_integration_summary(
    result: ArticlePipelineResult,
    request: ArticlePipelineRequest,
    *,
    elapsed_seconds: float,
    scheduler_snapshot: Mapping[str, Any],
    mode: str,
    review_kb_count: int,
    online_research: bool,
    article_memory_path: str = "",
    secrets: Sequence[str] = (),
) -> dict[str, Any]:
    """Build a bounded operational summary from strict pipeline outputs."""

    usage_rows = collect_qwen_usage(result)
    source_counts = _counter(row.token_counts_source for row in usage_rows)
    billable_rows = tuple(row for row in usage_rows if not row.mock_llm)
    usage_totals = {
        "logical_call_count": len(usage_rows),
        "mock_call_count": sum(1 for row in usage_rows if row.mock_llm),
        "request_attempt_count": sum(row.request_attempt_count for row in usage_rows),
        "retry_count": sum(row.retry_count for row in usage_rows),
        "api_key_rotation_count": sum(row.api_key_rotation_count for row in usage_rows),
        "maximum_api_key_candidate_count": max(
            (row.api_key_candidate_count for row in usage_rows), default=0
        ),
        "input_tokens": sum(row.input_tokens for row in usage_rows),
        "output_tokens": sum(row.output_tokens for row in usage_rows),
        "total_tokens": sum(row.total_tokens for row in usage_rows),
        "billable_input_tokens": sum(row.input_tokens for row in billable_rows),
        "billable_output_tokens": sum(row.output_tokens for row in billable_rows),
        "billable_total_tokens": sum(row.total_tokens for row in billable_rows),
        "token_source_counts": source_counts,
        "estimated_list_price_cost_cny": round(
            sum(row.estimated_list_price_cost_cny for row in usage_rows), 8
        ),
        "cost_note": (
            "List-price estimate using provider token counts when available; "
            "character estimates are used only for calls without provider usage; "
            "mock calls are non-billable."
        ),
    }

    receipts = [
        {
            "sequence": receipt.sequence,
            "stage": receipt.stage,
            "status": receipt.status,
            "input_ids": list(receipt.input_ids),
            "output_ids": list(receipt.output_ids),
            "warnings": _safe_messages(receipt.warnings, secrets),
            "errors": _safe_messages(receipt.errors, secrets),
            "payload_digest": receipt.payload_digest,
        }
        for receipt in result.receipts
    ]
    bindings = tuple(result.route_task_bindings)
    planning_rows = tuple(
        result.experiment_planning.rows if result.experiment_planning else ()
    )
    assets = tuple(result.asset_compilations)
    asset_status_counts = _counter(item.status for item in assets)
    availability_stages = [
        receipt.stage
        for receipt in result.receipts
        if receipt.status in {"partial", "unavailable"}
    ]
    provider_markers = ("provider", "qwen", "http", "429", "service unavailable")
    provider_receipt_stages = [
        receipt.stage
        for receipt in result.receipts
        if receipt.status in {"partial", "unavailable"}
        and any(
            marker in str(message).casefold()
            for message in (*receipt.warnings, *receipt.errors)
            for marker in provider_markers
        )
    ]
    usage_availability_failures = [
        row.stage
        for row in usage_rows
        if not row.mock_llm
        and (row.failure is True or row.success is False)
    ]
    provider_fail_open_stages = sorted(
        set(provider_receipt_stages + usage_availability_failures)
    )

    payload: dict[str, Any] = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "summary_id": "",
        "run_id": request.run_id,
        "branch_id": request.branch_id,
        "mode": mode,
        "question_sha256": hashlib.sha256(request.question.encode("utf-8")).hexdigest(),
        "question_char_count": len(request.question),
        "pipeline_status": result.status,
        "pipeline_result_id": result.result_id,
        "elapsed_seconds": round(max(0.0, float(elapsed_seconds)), 6),
        "configuration": {
            "force_mock": request.force_mock,
            "maximum_routes": request.maximum_routes,
            "review_kb_count": max(0, int(review_kb_count)),
            "online_research": bool(online_research),
            "article_memory_path": str(article_memory_path or ""),
            "article_memory_domain": "article",
            "review_kb_mode": "read_only_external_input",
        },
        "stage_status_counts": _counter(receipt.status for receipt in result.receipts),
        "receipts": receipts,
        "route_task_binding_counts": _counter(
            binding.compiler_status for binding in bindings
        ),
        "experiment_planning_row_counts": _counter(row.status for row in planning_rows),
        "execution_count": int(result.execution_count),
        "asset_compilation_count": len(assets),
        "asset_status_counts": asset_status_counts,
        "trusted_descriptor_count": sum(
            len(item.descriptors)
            for item in assets
            if item.status in {"ready", "partial"}
        ),
        "trusted_value_count": sum(
            len(item.trusted_values)
            for item in assets
            if item.status in {"ready", "partial"}
        ),
        "verified_candidate_count": sum(
            len(item.candidates)
            for item in assets
            if item.status in {"ready", "partial"}
        ),
        "provider_fail_open": bool(provider_fail_open_stages),
        "provider_fail_open_stages": provider_fail_open_stages,
        "availability_fail_open": bool(availability_stages),
        "availability_fail_open_stages": sorted(set(availability_stages)),
        "hard_failure": result.status == "failed" or bool(result.validation_errors),
        "validation_errors": _safe_messages(result.validation_errors, secrets),
        "warnings": _safe_messages(result.warnings, secrets),
        "qwen_usage": {
            "rows": [row.model_dump(mode="json") for row in usage_rows],
            "totals": usage_totals,
        },
        "scheduler": json.loads(
            json.dumps(dict(scheduler_snapshot), ensure_ascii=False, default=str)
        ),
    }
    if not math.isfinite(float(payload["elapsed_seconds"])):
        raise ArticleIntegrationError("elapsed_seconds must be finite")
    payload["attempts"] = [_attempt_record(payload)]
    payload["summary_id"] = _summary_id(payload)
    return payload


def write_integration_summary(
    path: str | Path,
    summary: Mapping[str, Any],
    *,
    allow_same_run_update: bool,
) -> Path:
    """Atomically write one summary without replacing another run."""

    target = Path(path).resolve()
    payload = dict(summary)
    if payload.get("summary_id") != _summary_id(payload):
        raise ArticleIntegrationError("integration summary_id does not match content")
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ArticleIntegrationError(
                f"existing integration summary is unreadable: {type(exc).__name__}"
            ) from exc
        same_identity = (
            existing.get("run_id") == payload.get("run_id")
            and existing.get("branch_id") == payload.get("branch_id")
        )
        if not same_identity:
            raise ArticleIntegrationError(
                "refusing to overwrite an integration summary from another run"
            )
        if existing == payload:
            return target
        if not allow_same_run_update:
            raise ArticleIntegrationError(
                "integration summary already exists; use resume for a same-run update"
            )
        if str(existing.get("summary_id") or "") != _summary_id(existing):
            raise ArticleIntegrationError(
                "existing integration summary_id does not match content"
            )
        history = _bounded_attempt_history(existing, payload)
        existing_receipts = existing.get("receipts")
        new_receipts = payload.get("receipts")
        existing_count = len(existing_receipts) if isinstance(existing_receipts, list) else 0
        new_count = len(new_receipts) if isinstance(new_receipts, list) else 0
        # A resume preflight failure has no committed receipts. Preserve the
        # most informative prior state and record the failed attempt instead
        # of erasing stage/cost history with an empty envelope.
        selected = dict(payload if new_count >= existing_count else existing)
        selected["attempts"] = history
        selected["summary_id"] = ""
        selected["summary_id"] = _summary_id(selected)
        payload = selected
    atomic_write_json(target, payload)
    return target


def execute_article_pipeline_integration(
    options: ArticleIntegrationOptions | Mapping[str, Any],
    *,
    environment: Optional[Mapping[str, str]] = None,
    factory_type: type[ProductionArticlePipelineFactory] = ProductionArticlePipelineFactory,
    synthesizer_factory: Callable[..., Any] = QwenMethodFindingSynthesizer,
    online_client_factory: Callable[..., Any] = DefaultMethodResearchOnlineClient,
    clock: Callable[[], float] = time.monotonic,
) -> ArticleIntegrationExecution:
    """Validate, assemble, run/resume, and summarize one real integration."""

    opts = (
        options
        if isinstance(options, ArticleIntegrationOptions)
        else ArticleIntegrationOptions.model_validate(options)
    )
    env = os.environ if environment is None else environment
    authority_key = str(env.get(AUTHORITY_ENVIRONMENT_VARIABLE) or "")
    if not authority_key:
        raise ArticleIntegrationError(
            f"{AUTHORITY_ENVIRONMENT_VARIABLE} must be supplied by the caller environment"
        )

    kb_paths = tuple(Path(item).resolve() for item in opts.review_kb_paths)
    missing = [str(path) for path in kb_paths if not path.is_file()]
    if missing:
        raise ArticleIntegrationError(
            "review knowledge base path does not exist or is not a file: "
            + ", ".join(missing)
        )

    request = ArticlePipelineRequest(
        question=opts.question,
        run_id=opts.run_id,
        branch_id=opts.branch_id,
        work_dir=str(Path(opts.work_dir).resolve()),
        force_mock=opts.force_mock,
        maximum_routes=opts.maximum_routes,
    )
    article_memory_path = initialize_article_memory_workspace(
        request.work_dir,
        opts.article_memory_path,
    )
    config = ProductionAssemblyConfig(
        work_root=str(Path(opts.execution_root).resolve()),
        review_kb_paths=tuple(str(path) for path in kb_paths),
        online_research=opts.online_research,
    )
    authority = ArticleCompilationAuthority(authority_key)
    synthesis_callback = synthesizer_factory(force_mock=opts.force_mock)
    online_client = online_client_factory() if opts.online_research else None
    factory = factory_type(request=request, authority=authority, config=config)
    assembly = factory.assemble(
        research_online_client=online_client,
        synthesis_callback=synthesis_callback,
    )

    started = clock()
    result = assembly.resume() if opts.resume else assembly.run()
    elapsed = max(0.0, float(clock()) - float(started))
    summary = build_integration_summary(
        result,
        request,
        elapsed_seconds=elapsed,
        scheduler_snapshot=assembly.scheduler.snapshot(),
        mode="resume" if opts.resume else "run",
        review_kb_count=len(kb_paths),
        online_research=opts.online_research,
        article_memory_path=str(article_memory_path),
        secrets=(authority_key,),
    )
    if len(authority_key) >= 8 and authority_key in json.dumps(
        summary, ensure_ascii=False
    ):
        raise ArticleIntegrationError("authority material reached the integration summary")
    summary_path = write_integration_summary(
        Path(request.work_dir) / SUMMARY_FILENAME,
        summary,
        allow_same_run_update=opts.resume,
    )
    try:
        persisted_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArticleIntegrationError(
            f"persisted integration summary cannot be read: {type(exc).__name__}"
        ) from exc
    serialized = json.dumps(persisted_summary, ensure_ascii=False)
    if authority_key in serialized:
        raise ArticleIntegrationError("authority material reached the integration summary")
    return ArticleIntegrationExecution(
        result=result,
        summary=persisted_summary,
        summary_path=str(summary_path),
    )


def integration_exit_code(status: str) -> int:
    """Return 0 completed, 2 recoverable/partial, or 1 hard failure."""

    if status == "completed":
        return 0
    if status in {"partial", "unavailable"}:
        return 2
    return 1


__all__ = [
    "AUTHORITY_ENVIRONMENT_VARIABLE",
    "ArticleIntegrationError",
    "ArticleIntegrationExecution",
    "ArticleIntegrationOptions",
    "QwenUsageRow",
    "SUMMARY_FILENAME",
    "SUMMARY_SCHEMA_VERSION",
    "build_integration_summary",
    "collect_qwen_usage",
    "execute_article_pipeline_integration",
    "integration_exit_code",
    "write_integration_summary",
]
