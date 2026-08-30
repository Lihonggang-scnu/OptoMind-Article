"""Global Article revision planning over immutable audit and manuscript assets.

This stage turns whole-Article quality findings into an execution handoff. It
does not rewrite prose, Claims, Facts, values, citations, or source bindings.
The existing section-level review/reviser remains the executor for author
changes; this module only assigns an owner and a bounded instruction.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from optomind_optics.harness.article_full_structure import FullStructureResult
from optomind_optics.harness.article_global_quality_audit import (
    GlobalQualityAuditReport,
)
from optomind_optics.harness.article_review import ArticleReviewResult
from optomind_optics.harness.qwen_policy import QwenFlashOnlyClient


GLOBAL_REVISION_PLAN_SCHEMA_VERSION = "article-global-revision-plan.v1"
MODEL_NAME = "qwen3.7-flash"
DEFAULT_MAX_TOKENS = 8000
DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "optical_harness"
    / "Article Global Revision Planner.txt"
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _ProviderModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class GlobalRevisionPlanAction(_StrictModel):
    schema_version: Literal["global-revision-plan-action.v1"] = (
        "global-revision-plan-action.v1"
    )
    action_id: str
    finding_id: str
    revision_mode: Literal[
        "local_display_fix",
        "author_scope_revision",
        "editorial_scope_label",
        "record_only",
    ]
    owner: Literal[
        "local_renderer",
        "author_reviser",
        "full_commander",
        "human_review",
    ]
    target_paragraph_ids: List[str] = Field(default_factory=list)
    instruction: str
    rationale: str
    source_claim_ids: List[str] = Field(default_factory=list)
    preserve_claim_bindings: bool = True
    disposition: Literal["planned", "deferred", "not_actionable", "unacknowledged"]


class GlobalRevisionPlanResult(_StrictModel):
    schema_version: Literal["article-global-revision-plan.v1"] = (
        GLOBAL_REVISION_PLAN_SCHEMA_VERSION
    )
    plan_id: str
    source_full_structure_id: str
    source_audit_id: str
    source_review_id: str
    story_id: str
    actions: List[GlobalRevisionPlanAction] = Field(default_factory=list)
    unhandled_finding_ids: List[str] = Field(default_factory=list)
    out_of_scope_finding_ids: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    validation_errors: List[str] = Field(default_factory=list)
    model_status: Literal["available", "partial", "unavailable"]
    usage: Dict[str, Any] = Field(default_factory=dict)
    semantic_model: str = "none"


class GlobalRevisionPlanProviderResult(_StrictModel):
    schema_version: Literal["global-revision-plan-provider-result.v1"] = (
        "global-revision-plan-provider-result.v1"
    )
    response: Dict[str, Any]
    usage: Dict[str, Any] = Field(default_factory=dict)
    provider_model: str = "unknown"
    mock_llm: bool = False


class _ActionDraft(_ProviderModel):
    finding_id: str = ""
    revision_mode: str = "record_only"
    owner: str = "full_commander"
    target_paragraph_ids: List[str] = Field(default_factory=list)
    instruction: str = ""
    rationale: str = ""
    disposition: str = "planned"


def _safe_json(text: str) -> Dict[str, Any]:
    text = str(text or "").strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                value = json.loads(text[start : end + 1])
                return value if isinstance(value, dict) else {}
            except json.JSONDecodeError:
                return {}
    return {}


def _dump(value: Any) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:24]


def _models(
    full_structure: FullStructureResult | Mapping[str, Any],
    audit: GlobalQualityAuditReport | Mapping[str, Any],
    review: ArticleReviewResult | Mapping[str, Any],
) -> tuple[FullStructureResult, GlobalQualityAuditReport, ArticleReviewResult]:
    full = (
        full_structure
        if isinstance(full_structure, FullStructureResult)
        else FullStructureResult.model_validate(full_structure)
    )
    audit_model = (
        audit
        if isinstance(audit, GlobalQualityAuditReport)
        else GlobalQualityAuditReport.model_validate(audit)
    )
    review_model = (
        review
        if isinstance(review, ArticleReviewResult)
        else ArticleReviewResult.model_validate(review)
    )
    return full, audit_model, review_model


def _unique_findings(audit: GlobalQualityAuditReport) -> List[Any]:
    seen: set[str] = set()
    result: List[Any] = []
    for finding in audit.findings:
        if finding.finding_id in seen:
            continue
        seen.add(finding.finding_id)
        result.append(finding)
    return result


def build_global_revision_plan_payload(
    full_structure: FullStructureResult | Mapping[str, Any],
    audit: GlobalQualityAuditReport | Mapping[str, Any],
    review: ArticleReviewResult | Mapping[str, Any],
    *,
    batch_size: int = 12,
) -> List[Dict[str, Any]]:
    """Build bounded semantic requests for the revision planner.

    A whole Article can have dozens of audit findings. Batching keeps each
    response parseable and lets the local layer merge the small action tables.
    """

    if batch_size < 1:
        raise ValueError("batch_size must be positive")

    full, audit_model, review_model = _models(full_structure, audit, review)
    paragraph_ids = {str(item.get("paragraph_id") or "") for item in full.source_map}
    out_of_scope = set(full.out_of_scope_global_quality_finding_ids)
    findings = [
        item
        for item in _unique_findings(audit_model)
        if item.finding_id not in out_of_scope
        and (not item.paragraph_id or item.paragraph_id in paragraph_ids)
    ]
    review_findings = [
        {
            "finding_id": item.finding_id,
            "reviewer": item.reviewer,
            "severity": item.severity.value,
            "kind": item.kind,
            "paragraph_id": item.paragraph_id,
            "reason": item.reason,
            "suggested_action": item.suggested_action,
        }
        for item in review_model.scientific_findings + review_model.expression_findings
    ]
    action_by_finding = {
        item.finding_id: item.model_dump(mode="json")
        for item in full.global_quality_actions
    }
    requests: List[Dict[str, Any]] = []
    batches = [findings[index : index + batch_size] for index in range(0, len(findings), batch_size)]
    if not batches:
        batches = [[]]
    for batch_index, batch in enumerate(batches, start=1):
        batch_paragraph_ids = {
            item.paragraph_id for item in batch if item.paragraph_id
        }
        # Article-level findings need the cross-section context; paragraph
        # findings only need their local text and nearby review observations.
        if any(not item.paragraph_id for item in batch):
            selected_paragraphs = list(full.source_map)
        else:
            selected_paragraphs = [
                item for item in full.source_map
                if str(item.get("paragraph_id") or "") in batch_paragraph_ids
            ]
        selected_review_findings = [
            item for item in review_findings
            if not batch_paragraph_ids or item["paragraph_id"] in batch_paragraph_ids
        ]
        requests.append(
            {
            "task": "Create an executable whole-Article revision handoff.",
            "batch_index": batch_index,
            "batch_count": len(batches),
            "story_id": full.story_id,
            "full_structure_id": full.result_id,
            "audit_id": audit_model.audit_id,
            "review_id": review_model.review_id,
            "section_order": [item.model_dump(mode="json") for item in full.section_order],
            "paragraphs": [
                {
                    "paragraph_id": str(item.get("paragraph_id") or ""),
                    "text": str(item.get("rendered_text") or item.get("text") or ""),
                    "claim_ids": list(item.get("claim_ids") or []),
                    "figure_ids": list(item.get("figure_ids") or []),
                }
                for item in selected_paragraphs
            ],
            "audit_findings": [
                {
                    "finding_id": item.finding_id,
                    "paragraph_id": item.paragraph_id,
                    "kind": item.kind,
                    "severity": item.severity,
                    "message": item.message,
                    "suggested_action": item.suggested_action,
                    "source_claim_ids": list(item.source_claim_ids),
                    "auto_fixable": item.auto_fixable,
                    "commander_disposition": action_by_finding.get(item.finding_id),
                }
                for item in batch
            ],
            "existing_review_findings": selected_review_findings,
            "constraints": {
                "one_action_per_audit_finding": True,
                "do_not_rewrite_prose": True,
                "do_not_change_claim_fact_value_source_or_citation_bindings": True,
                "display_precision_is_local_fix": True,
                "major_scope_or_metric_findings_need_author_or_human_owner": True,
                "ordinary_advice_is_record_only": True,
            },
        }
        )
    return requests


class QwenGlobalRevisionPlanner:
    """Concrete qwen3.7-flash adapter for revision-task planning only."""

    def __init__(
        self,
        *,
        prompt_path: str | Path = DEFAULT_PROMPT_PATH,
        client: QwenFlashOnlyClient | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self.prompt_path = Path(prompt_path)
        self.client = client or QwenFlashOnlyClient(
            agent_name="ArticleGlobalRevisionPlanner"
        )
        self.max_tokens = int(max_tokens)
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be positive")

    def __call__(
        self, requests: Sequence[Mapping[str, Any]]
    ) -> List[GlobalRevisionPlanProviderResult]:
        results: List[GlobalRevisionPlanProviderResult] = []
        for request in requests:
            response = self.client.call(
                [
                    {"role": "system", "content": self.prompt_path.read_text(encoding="utf-8")},
                    {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
                ],
                max_tokens=self.max_tokens,
                force_mock=False,
            )
            usage = dict(response.get("_llm_usage") or {})
            results.append(
                GlobalRevisionPlanProviderResult(
                    response=_safe_json(str(response.get("content") or "")),
                    usage=usage,
                    provider_model=MODEL_NAME,
                    mock_llm=bool(usage.get("mock_llm")),
                )
            )
        return results


def _fallback_mode(finding: Any) -> tuple[str, str]:
    if finding.auto_fixable or finding.kind == "display_precision":
        return "local_display_fix", "local_renderer"
    if finding.severity in {"major", "critical"}:
        return "author_scope_revision", "author_reviser"
    return "record_only", "full_commander"


def _normalize_instruction(finding: Any, instruction: str) -> str:
    """Keep planner prose inside the immutable source-binding contract."""

    if finding.kind == "candidate_binding_drift":
        return (
            "Remove or narrow the candidate-specific assertion to the Claims "
            "already bound to this paragraph; do not add a new Claim alias, "
            "Fact, citation, or source binding."
        )
    if finding.kind == "metric_overinterpretation_risk":
        return (
            "Keep only the measured metric and qualify broader cost, reliability, "
            "manufacturing, or deployment language as a hypothesis or future test."
        )
    return instruction


def build_global_revision_plan(
    full_structure: FullStructureResult | Mapping[str, Any],
    audit: GlobalQualityAuditReport | Mapping[str, Any],
    review: ArticleReviewResult | Mapping[str, Any],
    *,
    provider: Optional[QwenGlobalRevisionPlanner] = None,
    batch_size: int = 12,
) -> GlobalRevisionPlanResult:
    """Validate a planner response and preserve an executable handoff."""

    full, audit_model, review_model = _models(full_structure, audit, review)
    paragraph_ids = {str(item.get("paragraph_id") or "") for item in full.source_map}
    out_of_scope = set(full.out_of_scope_global_quality_finding_ids)
    findings = {
        item.finding_id: item
        for item in _unique_findings(audit_model)
        if item.finding_id not in out_of_scope
        and (not item.paragraph_id or item.paragraph_id in paragraph_ids)
    }
    warnings: List[str] = []
    usage: Dict[str, Any] = {}
    model_status: Literal["available", "partial", "unavailable"] = "unavailable"
    raw_actions: List[Any] = []
    if provider is not None:
        try:
            payloads = build_global_revision_plan_payload(
                full, audit_model, review_model, batch_size=batch_size
            )
            results = list(provider(payloads))
            if len(results) != len(payloads):
                warnings.append(
                    f"global revision planner returned {len(results)} results for {len(payloads)} batches"
                )
            valid_results = [
                item for item in results if isinstance(item, GlobalRevisionPlanProviderResult)
            ]
            count_keys = {
                "request_attempt_count",
                "retry_count",
                "api_key_rotation_count",
            }
            for result in valid_results:
                raw_actions.extend(result.response.get("actions") or [])
                for key, value in (result.usage or {}).items():
                    if key in {
                        "input_tokens",
                        "output_tokens",
                        "estimated_input_tokens",
                        "estimated_output_tokens",
                        "total_tokens",
                        "estimated_cost_cny",
                        "estimated_list_price_cost_cny",
                    } | count_keys and isinstance(value, (int, float)) and not isinstance(value, bool):
                        usage[key] = usage.get(key, 0) + value
                    elif key not in usage:
                        usage[key] = value
            if valid_results:
                model_status = "available" if raw_actions else "partial"
                usage["batch_count"] = len(payloads)
        except Exception as exc:
            warnings.append(f"global revision planner unavailable: {exc}")

    actions: List[GlobalRevisionPlanAction] = []
    seen: set[str] = set()
    allowed_modes = {
        "local_display_fix",
        "author_scope_revision",
        "editorial_scope_label",
        "record_only",
    }
    allowed_owners = {"local_renderer", "author_reviser", "full_commander", "human_review"}
    allowed_dispositions = {"planned", "deferred", "not_actionable", "unacknowledged"}
    for index, item in enumerate(raw_actions, start=1):
        try:
            draft = _ActionDraft.model_validate(item)
        except ValidationError as exc:
            warnings.append(f"malformed global revision action {index}: {exc}")
            continue
        finding = findings.get(draft.finding_id.strip())
        if finding is None:
            warnings.append(f"global revision action {index} references unknown finding {draft.finding_id!r}")
            continue
        finding_id = finding.finding_id
        if finding_id in seen:
            warnings.append(f"duplicate global revision action for {finding_id!r} ignored")
            continue
        seen.add(finding_id)
        mode = draft.revision_mode.strip().casefold()
        owner = draft.owner.strip().casefold()
        disposition = draft.disposition.strip().casefold()
        if mode not in allowed_modes:
            warnings.append(f"action {index} has unsupported revision mode; using record_only")
            mode = "record_only"
        if owner not in allowed_owners:
            warnings.append(f"action {index} has unsupported owner; using full_commander")
            owner = "full_commander"
        if disposition not in allowed_dispositions:
            warnings.append(f"action {index} has unsupported disposition; using unacknowledged")
            disposition = "unacknowledged"
        targets = [pid for pid in dict.fromkeys(draft.target_paragraph_ids) if pid in paragraph_ids]
        if finding.paragraph_id and finding.paragraph_id in paragraph_ids and not targets:
            targets = [finding.paragraph_id]
            warnings.append(f"action {index} omitted target paragraph; restored audit target")
        instruction = _normalize_instruction(
            finding, draft.instruction or finding.suggested_action
        )
        if not instruction.strip():
            warnings.append(f"action {index} has empty instruction; retained as record_only")
            mode = "record_only"
            owner = "full_commander"
            disposition = "unacknowledged"
        actions.append(
            GlobalRevisionPlanAction(
                action_id=f"global-revision-action-{len(actions)+1:03d}",
                finding_id=finding_id,
                revision_mode=mode,  # type: ignore[arg-type]
                owner=owner,  # type: ignore[arg-type]
                target_paragraph_ids=targets,
                instruction=instruction,
                rationale=draft.rationale or finding.message,
                source_claim_ids=list(finding.source_claim_ids),
                preserve_claim_bindings=True,
                disposition=disposition,  # type: ignore[arg-type]
            )
        )
    unhandled = sorted(set(findings) - seen)
    if unhandled:
        warnings.append(f"{len(unhandled)} audit findings have no planner action")
        # Preserve a handoff even when a batch response is truncated or omits
        # one row. The finding remains explicitly unacknowledged, so this
        # local fallback cannot be mistaken for successful semantic planning.
        for finding_id in unhandled:
            finding = findings[finding_id]
            mode, owner = _fallback_mode(finding)
            target_ids = (
                [finding.paragraph_id]
                if finding.paragraph_id and finding.paragraph_id in paragraph_ids
                else []
            )
            actions.append(
                GlobalRevisionPlanAction(
                    action_id=f"global-revision-action-{len(actions)+1:03d}",
                    finding_id=finding_id,
                    revision_mode=mode,  # type: ignore[arg-type]
                    owner=owner,  # type: ignore[arg-type]
                    target_paragraph_ids=target_ids,
                    instruction=_normalize_instruction(
                        finding, finding.suggested_action or finding.message
                    ),
                    rationale=(
                        "Local fail-open fallback: the planner did not return a row; "
                        + finding.message
                    ),
                    source_claim_ids=list(finding.source_claim_ids),
                    preserve_claim_bindings=True,
                    disposition="unacknowledged",
                )
            )
    result_payload = {
        "source_full_structure_id": full.result_id,
        "source_audit_id": audit_model.audit_id,
        "source_review_id": review_model.review_id,
        "story_id": full.story_id,
        "actions": [item.model_dump(mode="json") for item in actions],
        "unhandled_finding_ids": unhandled,
        "out_of_scope_finding_ids": sorted(out_of_scope),
    }
    return GlobalRevisionPlanResult(
        plan_id="global-revision-plan-" + _digest(result_payload),
        source_full_structure_id=full.result_id,
        source_audit_id=audit_model.audit_id,
        source_review_id=review_model.review_id,
        story_id=full.story_id,
        actions=actions,
        unhandled_finding_ids=unhandled,
        out_of_scope_finding_ids=sorted(out_of_scope),
        warnings=warnings,
        validation_errors=[],
        model_status=model_status,
        usage=usage,
        semantic_model=MODEL_NAME if provider is not None else "none",
    )


__all__ = [
    "GlobalRevisionPlanAction",
    "GlobalRevisionPlanProviderResult",
    "GlobalRevisionPlanResult",
    "QwenGlobalRevisionPlanner",
    "build_global_revision_plan",
    "build_global_revision_plan_payload",
]
