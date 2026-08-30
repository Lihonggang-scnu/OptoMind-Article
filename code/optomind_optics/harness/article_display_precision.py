"""Local public-number formatting without changing scientific source facts."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Mapping, Tuple

from pydantic import BaseModel, ConfigDict, Field

from optomind_optics.harness.article_global_quality_audit import (
    GlobalQualityAuditReport,
)
from optomind_optics.harness.article_manuscript import (
    ArticleManuscriptPackage,
    ManuscriptSection,
    ParagraphManuscriptSource,
    _render_body_markdown,
    compute_manuscript_body_id,
    compute_manuscript_package_id,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DisplayPrecisionFixResult(_StrictModel):
    schema_version: Literal["article-display-precision-fix.v1"] = (
        "article-display-precision-fix.v1"
    )
    fix_id: str
    source_package_id: str
    package: ArticleManuscriptPackage
    changed_paragraph_ids: List[str] = Field(default_factory=list)
    resolved_finding_ids: List[str] = Field(default_factory=list)
    remaining_finding_ids: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


_LONG_DECIMAL = re.compile(r"(?<![A-Za-z])\d+\.\d{7,}(?:[eE][+-]?\d+)?")


def _format_number(match: re.Match[str]) -> str:
    raw = match.group(0)
    try:
        value = float(raw)
    except ValueError:
        return raw
    if "e" in raw.lower() or (value != 0.0 and abs(value) < 1e-4):
        return f"{value:.6e}"
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _fix_text(text: str) -> str:
    return _LONG_DECIMAL.sub(_format_number, str(text or ""))


def apply_display_precision_fix(
    manuscript: ArticleManuscriptPackage | Mapping[str, Any],
    audit: GlobalQualityAuditReport | Mapping[str, Any],
) -> DisplayPrecisionFixResult:
    """Return a new package with only public long decimals formatted."""

    package = (
        manuscript
        if isinstance(manuscript, ArticleManuscriptPackage)
        else ArticleManuscriptPackage.model_validate(manuscript)
    )
    audit_model = (
        audit
        if isinstance(audit, GlobalQualityAuditReport)
        else GlobalQualityAuditReport.model_validate(audit)
    )
    changed: set[str] = set()
    section_models: List[ManuscriptSection] = []
    section_source_by_id: Dict[str, ParagraphManuscriptSource] = {}
    for item in package.source_map:
        section_source_by_id[item.paragraph_id] = item
    for section in package.body.sections:
        paragraphs: List[ParagraphManuscriptSource] = []
        for paragraph in section.paragraphs:
            fixed = _fix_text(paragraph.rendered_text)
            if fixed != paragraph.rendered_text:
                changed.add(paragraph.paragraph_id)
            updated = paragraph.model_copy(update={"rendered_text": fixed})
            paragraphs.append(updated)
            section_source_by_id[paragraph.paragraph_id] = updated
        section_models.append(section.model_copy(update={"paragraphs": paragraphs}))
    source_map = [
        section_source_by_id.get(item.paragraph_id, item)
        for item in package.source_map
    ]
    body_markdown = _render_body_markdown(section_models)
    body_id = compute_manuscript_body_id(
        package.plan_id,
        package.ledger_id,
        package.architecture_id,
        package.review_id,
        package.result_id,
        package.story_id,
        section_models,
        source_map,
        package.findings,
        package.blocked_handoff,
    )
    body = package.body.model_copy(
        update={
            "body_id": body_id,
            "sections": section_models,
            "source_map": source_map,
            "warnings": [*package.body.warnings, "local display precision formatting applied"],
        }
    )
    package_id = compute_manuscript_package_id(
        body_id,
        body_markdown,
        source_map,
        package.findings,
        package.blocked_handoff,
        package.plan_id,
        package.ledger_id,
        package.architecture_id,
        package.review_id,
        package.result_id,
        package.story_id,
    )
    fixed_package = package.model_copy(
        update={
            "package_id": package_id,
            "body_id": body_id,
            "body_markdown": body_markdown,
            "body": body,
            "source_map": source_map,
            "warnings": [*package.warnings, "local display precision formatting applied"],
        }
    )
    audit_by_id = {item.finding_id: item for item in audit_model.findings}
    precision_ids = {
        item.finding_id
        for item in audit_model.findings
        if item.kind == "display_precision"
    }
    resolved = sorted(
        finding_id
        for finding_id in precision_ids
        if audit_by_id[finding_id].paragraph_id in changed
    )
    remaining = sorted(precision_ids - set(resolved))
    return DisplayPrecisionFixResult(
        fix_id="display-precision-fix-" + body_id,
        source_package_id=package.package_id,
        package=fixed_package,
        changed_paragraph_ids=sorted(changed),
        resolved_finding_ids=resolved,
        remaining_finding_ids=remaining,
    )


__all__ = ["DisplayPrecisionFixResult", "apply_display_precision_fix"]
