"""Assemble independently written chapter manuscripts for the full commander."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from optomind_optics.harness.article_manuscript import (
    ArticleManuscriptBody,
    ArticleManuscriptPackage,
    BlockedSectionHandoff,
    ManuscriptSection,
    ParagraphManuscriptSource,
    _render_body_markdown,
    compute_manuscript_body_id,
    compute_manuscript_package_id,
)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()[:24]


def _model(value: ArticleManuscriptPackage | Mapping[str, Any]) -> ArticleManuscriptPackage:
    return value if isinstance(value, ArticleManuscriptPackage) else ArticleManuscriptPackage.model_validate(value)


def assemble_chapter_manuscripts(
    chapters: Sequence[ArticleManuscriptPackage | Mapping[str, Any]],
    *,
    expected_plan_id: str = "",
    expected_ledger_id: str = "",
    expected_architecture_id: str = "",
    expected_story_id: str = "",
    global_review_id: str = "",
    global_result_id: str = "",
) -> ArticleManuscriptPackage:
    """Create one immutable manuscript package from chapter packages.

    Chapter packages may have different review IDs because each chapter was
    reviewed independently. ``global_review_id`` must be supplied once the
    full commander has its own review checkpoint; a single shared review ID is
    inferred only for a one-package or already-shared input.
    """

    if not chapters:
        raise ValueError("at least one chapter manuscript is required")
    models = [_model(item) for item in chapters]
    expected = {
        "plan_id": expected_plan_id,
        "ledger_id": expected_ledger_id,
        "architecture_id": expected_architecture_id,
        "story_id": expected_story_id,
    }
    for index, package in enumerate(models):
        for field, wanted in expected.items():
            actual = getattr(package, field)
            if wanted and actual != wanted:
                raise ValueError(
                    f"chapter[{index}] {field} {actual!r} does not match expected {wanted!r}"
                )
    shared = {
        field: getattr(models[0], field)
        for field in ("plan_id", "ledger_id", "architecture_id", "story_id")
    }
    for index, package in enumerate(models[1:], start=1):
        for field, wanted in shared.items():
            if getattr(package, field) != wanted:
                raise ValueError(
                    f"chapter[{index}] {field} {getattr(package, field)!r} does not match shared {wanted!r}"
                )
    section_ids: set[str] = set()
    paragraph_ids: set[str] = set()
    sections: List[ManuscriptSection] = []
    source_map: List[ParagraphManuscriptSource] = []
    findings: Dict[str, Any] = {}
    blocked: Dict[str, BlockedSectionHandoff] = {}
    warnings: List[str] = []
    errors: List[str] = []
    review_ids = {package.review_id for package in models}
    for chapter_index, package in enumerate(models):
        for section in package.body.sections:
            if section.section_id in section_ids:
                raise ValueError(f"duplicate chapter section_id {section.section_id!r}")
            section_ids.add(section.section_id)
            for paragraph in section.paragraphs:
                if paragraph.paragraph_id in paragraph_ids:
                    raise ValueError(f"duplicate chapter paragraph_id {paragraph.paragraph_id!r}")
                paragraph_ids.add(paragraph.paragraph_id)
            sections.append(section)
        for paragraph in package.source_map:
            if paragraph.paragraph_id not in paragraph_ids:
                raise ValueError(
                    f"chapter[{chapter_index}] source_map paragraph {paragraph.paragraph_id!r} is not in its body"
                )
            source_map.append(paragraph)
        for finding in package.findings:
            findings.setdefault(finding.finding_id, finding)
        for handoff in package.blocked_handoff:
            blocked.setdefault(handoff.section_id, handoff)
        warnings.extend(item for item in package.warnings if item not in warnings)
        errors.extend(item for item in package.errors if item not in errors)
    if errors:
        raise ValueError("chapter manuscripts contain errors: " + "; ".join(errors[:5]))
    review_id = global_review_id.strip()
    if not review_id:
        if len(review_ids) != 1:
            raise ValueError(
                "multiple chapter review IDs require an explicit global_review_id"
            )
        review_id = next(iter(review_ids))
    result_id = global_result_id.strip() or "chapter-assembly-" + _digest(
        [package.package_id for package in models]
    )
    body_status = "blocked" if blocked else "assembled"
    body_markdown = _render_body_markdown(sections)
    finding_models = list(findings.values())
    blocked_models = list(blocked.values())
    body_id = compute_manuscript_body_id(
        shared["plan_id"],
        shared["ledger_id"],
        shared["architecture_id"],
        review_id,
        result_id,
        shared["story_id"],
        sections,
        source_map,
        finding_models,
        blocked_models,
    )
    package_id = compute_manuscript_package_id(
        body_id,
        body_markdown,
        source_map,
        finding_models,
        blocked_models,
        shared["plan_id"],
        shared["ledger_id"],
        shared["architecture_id"],
        review_id,
        result_id,
        shared["story_id"],
    )
    body = ArticleManuscriptBody(
        body_id=body_id,
        plan_id=shared["plan_id"],
        ledger_id=shared["ledger_id"],
        architecture_id=shared["architecture_id"],
        review_id=review_id,
        result_id=result_id,
        story_id=shared["story_id"],
        status=body_status,
        sections=sections,
        blocked_handoff=blocked_models,
        source_map=source_map,
        findings=finding_models,
        warnings=warnings,
        errors=[],
    )
    return ArticleManuscriptPackage(
        package_id=package_id,
        body_id=body_id,
        plan_id=shared["plan_id"],
        ledger_id=shared["ledger_id"],
        architecture_id=shared["architecture_id"],
        review_id=review_id,
        result_id=result_id,
        story_id=shared["story_id"],
        body_markdown=body_markdown,
        body=body,
        source_map=source_map,
        findings=finding_models,
        blocked_handoff=blocked_models,
        warnings=warnings,
        errors=[],
    )


__all__ = ["assemble_chapter_manuscripts"]
