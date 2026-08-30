"""Bridge global revision actions into the existing section author reviser."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from optomind_optics.harness.article_global_revision_plan import (
    GlobalRevisionPlanResult,
)
from optomind_optics.harness.article_review import (
    ArticleReviewResult,
    ReviewerFinding,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GlobalRevisionBridgeResult(_StrictModel):
    schema_version: Literal["article-global-revision-bridge.v1"] = (
        "article-global-revision-bridge.v1"
    )
    bridge_id: str
    source_plan_id: str
    source_review_id: str
    forced_review: ArticleReviewResult
    executable_action_ids: List[str] = Field(default_factory=list)
    skipped_action_ids: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()[:24]


def build_global_revision_bridge(
    plan: GlobalRevisionPlanResult | Mapping[str, Any],
    review: ArticleReviewResult | Mapping[str, Any],
    *,
    max_actions: int | None = None,
) -> GlobalRevisionBridgeResult:
    """Build a forced-review envelope without changing the persisted review.

    Only planned author/editor actions with an explicit paragraph target are
    executable by the section reviser. Article-level findings and local
    renderer tasks remain visible as skipped handoffs.
    """

    plan_model = (
        plan if isinstance(plan, GlobalRevisionPlanResult)
        else GlobalRevisionPlanResult.model_validate(plan)
    )
    review_model = (
        review if isinstance(review, ArticleReviewResult)
        else ArticleReviewResult.model_validate(review)
    )
    warnings: List[str] = []
    if max_actions is not None and max_actions < 1:
        raise ValueError("max_actions must be positive when supplied")
    candidates = [
        action
        for action in plan_model.actions
        if action.owner == "author_reviser"
        and action.revision_mode == "author_scope_revision"
        and action.disposition == "planned"
        and action.target_paragraph_ids
    ]
    if max_actions is not None:
        candidates = candidates[:max_actions]
    executable_ids = [item.action_id for item in candidates]
    selected = set(executable_ids)
    skipped = [
        action.action_id
        for action in plan_model.actions
        if action.action_id not in selected
    ]
    findings: List[ReviewerFinding] = []
    for action in candidates:
        severity = "major" if action.revision_mode == "author_scope_revision" else "minor"
        for paragraph_id in action.target_paragraph_ids:
            findings.append(
                ReviewerFinding(
                    finding_id=f"global-plan-{action.finding_id}-{paragraph_id}",
                    reviewer="scientific",
                    severity=severity,  # type: ignore[arg-type]
                    kind=f"global_{action.revision_mode}",
                    paragraph_id=paragraph_id,
                    span="",
                    reason=action.rationale,
                    suggested_action=action.instruction,
                    claim_aliases=[],
                    claim_ids=list(action.source_claim_ids),
                )
            )
    if not findings:
        warnings.append("no planned author action has an explicit paragraph target")
    forced_review = review_model.model_copy(
        update={
            "scientific_findings": findings,
            "expression_findings": [],
            "warnings": [
                *review_model.warnings,
                "global revision plan bridged into forced author findings",
            ],
        }
    )
    bridge_payload = {
        "source_plan_id": plan_model.plan_id,
        "source_review_id": review_model.review_id,
        "executable_action_ids": executable_ids,
        "skipped_action_ids": skipped,
        "finding_ids": [item.finding_id for item in findings],
    }
    return GlobalRevisionBridgeResult(
        bridge_id="global-revision-bridge-" + _digest(bridge_payload),
        source_plan_id=plan_model.plan_id,
        source_review_id=review_model.review_id,
        forced_review=forced_review,
        executable_action_ids=executable_ids,
        skipped_action_ids=skipped,
        warnings=warnings,
    )


__all__ = ["GlobalRevisionBridgeResult", "build_global_revision_bridge"]
