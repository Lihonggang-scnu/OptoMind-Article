"""Stage 12A: deterministic manuscript-body assembly boundary.

Consumes an accepted Stage 11 ``ArticleReviewResult`` and assembles a
deterministic manuscript body: only sections with status ``ready`` or
``ready_with_findings`` are assembled in selected-story order, with exact
final paragraph prose and a full paragraph-level source map.  Blocked
sections are recorded as an explicit handoff, so a partial review produces a
useful partial body.

This is NOT the final publication renderer: no title, abstract, citations,
figures, or references are invented here, no Qwen/external services are
called, and internal hash aliases never appear in reader-facing prose.

Identity: ``body_id``/``package_id`` are content-addressed over all
scientific body content, order, source map, findings, blocked handoff, and
upstream identities.  Upstream identity/provenance/reconstruction failure
blocks assembly before any content is emitted.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from optomind_optics.harness.article_architecture import (
    ArticleArchitectureResult,
    StoryCandidate,
)
from optomind_optics.harness.article_claims import ClaimLedgerResult
from optomind_optics.harness.article_director import ArticleDirectorPlan
from optomind_optics.harness.article_review import (
    ArticleReviewResult,
    ReviewedSection,
    ReviewerFinding,
    SectionReviewStatus,
    validate_review_result,
)
from optomind_optics.harness.article_writing import (
    TrustedValueRecord,
    validate_writing_inputs,
)
from optomind_research.runtime.artifact_store import (
    atomic_write_json,
    atomic_write_text,
)


MANUSCRIPT_SCHEMA_VERSION = "article-manuscript-body.v1"
MANUSCRIPT_PACKAGE_SCHEMA_VERSION = "article-manuscript-package.v1"
PARAGRAPH_MANUSCRIPT_SOURCE_SCHEMA_VERSION = "paragraph-manuscript-source.v1"
BLOCKED_SECTION_HANDOFF_SCHEMA_VERSION = "blocked-section-handoff.v1"


class ArticleManuscriptError(ValueError):
    """Base error for manuscript-body assembly failures."""


class ArticleManuscriptIntegrityError(ArticleManuscriptError):
    """Conflicting persisted manuscript content or provenance mismatch."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ParagraphManuscriptSource(_StrictModel):
    schema_version: Literal["paragraph-manuscript-source.v1"] = (
        "paragraph-manuscript-source.v1"
    )
    paragraph_id: str
    section_id: str
    rendered_text: str
    claim_ids: List[str] = Field(default_factory=list)
    fact_ids: List[str] = Field(default_factory=list)
    artifact_ids: List[str] = Field(default_factory=list)
    figure_ids: List[str] = Field(default_factory=list)
    value_token_ids: List[str] = Field(default_factory=list)
    literature_evidence_ids: List[str] = Field(default_factory=list)
    scope: str = ""
    scopes: List[str] = Field(default_factory=list)
    limits: List[str] = Field(default_factory=list)
    roles: List[str] = Field(default_factory=list)
    inference_kind: str = ""
    inference_note: str = ""
    finding_ids: List[str] = Field(default_factory=list)


class ManuscriptSection(_StrictModel):
    schema_version: Literal["manuscript-section.v1"] = "manuscript-section.v1"
    section_id: str
    heading: str
    story_id: str
    status: Literal["ready", "ready_with_findings"]
    paragraphs: List[ParagraphManuscriptSource] = Field(default_factory=list)
    finding_ids: List[str] = Field(default_factory=list)


class BlockedSectionHandoff(_StrictModel):
    schema_version: Literal["blocked-section-handoff.v1"] = (
        "blocked-section-handoff.v1"
    )
    section_id: str
    hard_blockers: List[str] = Field(default_factory=list)


class ArticleManuscriptBody(_StrictModel):
    schema_version: Literal["article-manuscript-body.v1"] = (
        "article-manuscript-body.v1"
    )
    body_id: str
    plan_id: str
    ledger_id: str
    architecture_id: str
    review_id: str
    result_id: str
    story_id: str
    status: Literal["assembled", "partial", "blocked"]
    sections: List[ManuscriptSection] = Field(default_factory=list)
    blocked_handoff: List[BlockedSectionHandoff] = Field(default_factory=list)
    source_map: List[ParagraphManuscriptSource] = Field(default_factory=list)
    findings: List[ReviewerFinding] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class ArticleManuscriptPackage(_StrictModel):
    schema_version: Literal["article-manuscript-package.v1"] = (
        "article-manuscript-package.v1"
    )
    package_id: str
    body_id: str
    plan_id: str
    ledger_id: str
    architecture_id: str
    review_id: str
    result_id: str
    story_id: str
    body_markdown: str
    body: ArticleManuscriptBody
    source_map: List[ParagraphManuscriptSource] = Field(default_factory=list)
    findings: List[ReviewerFinding] = Field(default_factory=list)
    blocked_handoff: List[BlockedSectionHandoff] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(*parts: Any) -> str:
    return hashlib.sha256(
        _canonical_json([str(part) for part in parts]).encode("utf-8")
    ).hexdigest()[:16]


def _identity_dump(value: Any) -> Any:
    """Return the identity view while preserving pre-literature-bridge IDs.

    ``literature_evidence_ids`` was added as an optional provenance field to
    paragraph source records.  Older persisted manuscript packages therefore
    hashed records that did not contain this key at all.  Pydantic fills the
    missing key with ``[]`` when those packages are loaded, so the identity
    view removes only that specific empty field.  Non-empty literature
    bindings remain part of the content identity, and no persisted payload is
    modified by this compatibility projection.
    """

    if isinstance(value, Mapping):
        return {
            str(key): _identity_dump(item)
            for key, item in value.items()
            if not (
                str(key) == "literature_evidence_ids"
                and item == []
            )
        }
    if isinstance(value, list):
        return [_identity_dump(item) for item in value]
    if isinstance(value, tuple):
        return [_identity_dump(item) for item in value]
    return value


def _identity_model_dump(model: BaseModel) -> str:
    """Canonical JSON for a model in content-addressed identity hashes."""

    return _canonical_json(_identity_dump(model.model_dump(mode="json")))


def _json_equal(left_text: str, right_text: str) -> bool:
    try:
        return json.loads(left_text) == json.loads(right_text)
    except json.JSONDecodeError:
        return False


def _sanitize_heading(heading: str, index: int) -> str:
    """Preserve human-readable heading text; neutralize Markdown/control
    injection (newlines, control characters, leading heading markers)."""

    text = str(heading or "")
    text = text.replace("\r", " ").replace("\n", " ")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return f"Section {index}"
    if text.startswith("#"):
        text = text.lstrip("#").strip()
    if not text:
        return f"Section {index}"
    return text


def _assemble_section(
    reviewed_section: ReviewedSection,
    story_id: str,
) -> Tuple[ManuscriptSection, List[ParagraphManuscriptSource]]:
    ledger_by_id = {
        entry.paragraph_id: entry
        for entry in reviewed_section.section_draft.source_ledger
    }
    findings_by_paragraph: Dict[str, List[str]] = {}
    for finding in reviewed_section.findings:
        findings_by_paragraph.setdefault(finding.paragraph_id, []).append(
            finding.finding_id
        )
    paragraphs: List[ParagraphManuscriptSource] = []
    for paragraph in reviewed_section.section_draft.paragraphs:
        entry = ledger_by_id.get(paragraph.paragraph_id)
        paragraphs.append(
            ParagraphManuscriptSource(
                paragraph_id=paragraph.paragraph_id,
                section_id=reviewed_section.section_id,
                rendered_text=paragraph.rendered_text,
                claim_ids=list(paragraph.claim_ids),
                fact_ids=list(entry.fact_ids) if entry is not None else [],
                artifact_ids=list(entry.artifact_ids) if entry is not None else [],
                figure_ids=list(paragraph.figure_ids),
                value_token_ids=list(paragraph.value_token_ids),
                literature_evidence_ids=list(paragraph.literature_evidence_ids),
                scope=(entry.scope if entry is not None else ""),
                scopes=list(entry.scopes) if entry is not None else [],
                limits=list(entry.limits) if entry is not None else [],
                roles=list(entry.roles) if entry is not None else [],
                inference_kind=paragraph.inference_kind.value,
                inference_note=paragraph.inference_note,
                finding_ids=sorted(
                    findings_by_paragraph.get(paragraph.paragraph_id, [])
                ),
            )
        )
    status = (
        "ready"
        if reviewed_section.status == SectionReviewStatus.ready
        else "ready_with_findings"
    )
    section = ManuscriptSection(
        section_id=reviewed_section.section_id,
        heading=reviewed_section.section_draft.title,
        story_id=story_id,
        status=status,
        paragraphs=paragraphs,
        finding_ids=sorted(
            {finding.finding_id for finding in reviewed_section.findings}
        ),
    )
    return section, paragraphs


def _render_body_markdown(sections: Sequence[ManuscriptSection]) -> str:
    blocks: List[str] = []
    for index, section in enumerate(sections, start=1):
        heading = _sanitize_heading(section.heading, index)
        body = "\n\n".join(
            paragraph.rendered_text for paragraph in section.paragraphs
        )
        blocks.append(f"## {heading}\n\n{body}".rstrip())
    return "\n\n".join(blocks)


def validate_manuscript_package(
    package: ArticleManuscriptPackage | Mapping[str, Any],
    plan: ArticleDirectorPlan | Mapping[str, Any],
    ledger: ClaimLedgerResult | Mapping[str, Any],
    architecture: ArticleArchitectureResult | Mapping[str, Any],
    selected_story_id: str,
    value_records: Sequence[TrustedValueRecord | Mapping[str, Any]],
    errors: List[str],
    warnings: List[str],
) -> Optional[StoryCandidate]:
    """Public deterministic revalidation of a manuscript package."""

    try:
        package_model = (
            package
            if isinstance(package, ArticleManuscriptPackage)
            else ArticleManuscriptPackage.model_validate(package)
        )
    except ValidationError as exc:
        errors.append(f"manuscript package is invalid: {exc}")
        return None
    try:
        plan_model = (
            plan
            if isinstance(plan, ArticleDirectorPlan)
            else ArticleDirectorPlan.model_validate(plan)
        )
    except ValidationError as exc:
        errors.append(f"plan is invalid: {exc}")
        return None
    try:
        ledger_model = (
            ledger
            if isinstance(ledger, ClaimLedgerResult)
            else ClaimLedgerResult.model_validate(ledger)
        )
    except ValidationError as exc:
        errors.append(f"ledger is invalid: {exc}")
        return None
    try:
        architecture_model = (
            architecture
            if isinstance(architecture, ArticleArchitectureResult)
            else ArticleArchitectureResult.model_validate(architecture)
        )
    except ValidationError as exc:
        errors.append(f"architecture is invalid: {exc}")
        return None
    records: List[TrustedValueRecord] = []
    for index, raw in enumerate(value_records):
        try:
            records.append(
                raw
                if isinstance(raw, TrustedValueRecord)
                else TrustedValueRecord.model_validate(raw)
            )
        except ValidationError as exc:
            errors.append(f"value_records[{index}] is invalid: {exc}")
    if errors:
        return None
    story, _fact_by_claim = validate_writing_inputs(
        plan_model,
        ledger_model,
        architecture_model,
        selected_story_id,
        records,
        errors,
        warnings,
    )
    if story is None:
        return None

    if package_model.plan_id != plan_model.plan_id:
        errors.append(
            f"package plan_id {package_model.plan_id!r} does not match plan "
            f"{plan_model.plan_id!r}"
        )
    if package_model.ledger_id != ledger_model.ledger_id:
        errors.append(
            f"package ledger_id {package_model.ledger_id!r} does not match "
            f"ledger {ledger_model.ledger_id!r}"
        )
    if package_model.architecture_id != architecture_model.architecture_id:
        errors.append(
            f"package architecture_id {package_model.architecture_id!r} does "
            f"not match architecture {architecture_model.architecture_id!r}"
        )
    if package_model.story_id != selected_story_id:
        errors.append(
            f"package story_id {package_model.story_id!r} does not match "
            f"selected story {selected_story_id!r}"
        )
    if package_model.body_id != package_model.body.body_id:
        errors.append("package body_id does not match its embedded body")
    if package_model.plan_id != package_model.body.plan_id:
        errors.append("package plan_id does not match its embedded body")
    if package_model.ledger_id != package_model.body.ledger_id:
        errors.append("package ledger_id does not match its embedded body")
    if package_model.architecture_id != package_model.body.architecture_id:
        errors.append(
            "package architecture_id does not match its embedded body"
        )
    if package_model.story_id != package_model.body.story_id:
        errors.append("package story_id does not match its embedded body")
    if package_model.review_id != package_model.body.review_id:
        errors.append("package review_id does not match its embedded body")
    if package_model.result_id != package_model.body.result_id:
        errors.append("package result_id does not match its embedded body")
    if package_model.body.errors or package_model.errors:
        errors.append("manuscript package carries errors")
    if package_model.warnings != package_model.body.warnings:
        errors.append("package warnings do not match its embedded body")
    if package_model.errors != package_model.body.errors:
        errors.append("package errors do not match its embedded body")
    if package_model.body.blocked_handoff != package_model.blocked_handoff:
        errors.append(
            "package blocked_handoff does not match its embedded body"
        )
    if package_model.body.findings != package_model.findings:
        errors.append("package findings do not match its embedded body")
    if package_model.body.source_map != package_model.source_map:
        errors.append("package source_map does not match its embedded body")

    flattened = [
        paragraph
        for section in package_model.body.sections
        for paragraph in section.paragraphs
    ]
    if flattened != package_model.body.source_map:
        errors.append(
            "package source_map is not derivable from its body sections"
        )
    if flattened != package_model.source_map:
        errors.append(
            "package source_map is not derivable from its sections"
        )
    for paragraph in flattened:
        if paragraph.rendered_text != paragraph.rendered_text.strip():
            errors.append(
                f"paragraph {paragraph.paragraph_id} rendered text is not "
                "trimmed identically"
            )
    recomputed_markdown = _render_body_markdown(package_model.body.sections)
    if recomputed_markdown != package_model.body_markdown:
        errors.append(
            "package body_markdown is not derivable from its body sections"
        )
    if not package_model.body.sections and not package_model.body.blocked_handoff:
        derived_status = "blocked"
    elif package_model.body.sections and not package_model.body.blocked_handoff:
        derived_status = "assembled"
    elif package_model.body.sections and package_model.body.blocked_handoff:
        derived_status = "partial"
    else:
        derived_status = "blocked"
    if derived_status != package_model.body.status:
        errors.append(
            f"package body status {package_model.body.status!r} does not "
            f"match derived status {derived_status!r}"
        )
    recomputed_body_id = compute_manuscript_body_id(
        package_model.plan_id,
        package_model.ledger_id,
        package_model.architecture_id,
        package_model.review_id,
        package_model.result_id,
        package_model.story_id,
        package_model.body.sections,
        package_model.body.source_map,
        package_model.body.findings,
        package_model.body.blocked_handoff,
    )
    if recomputed_body_id != package_model.body_id:
        errors.append(
            f"package body_id {package_model.body_id!r} does not match "
            f"recomputed identity {recomputed_body_id!r}"
        )
    recomputed_package_id = compute_manuscript_package_id(
        package_model.body_id,
        package_model.body_markdown,
        package_model.source_map,
        package_model.findings,
        package_model.blocked_handoff,
        package_model.plan_id,
        package_model.ledger_id,
        package_model.architecture_id,
        package_model.review_id,
        package_model.result_id,
        package_model.story_id,
    )
    if recomputed_package_id != package_model.package_id:
        errors.append(
            f"package_id {package_model.package_id!r} does not match "
            f"recomputed identity {recomputed_package_id!r}"
        )
    story_section_ids = [item.section_id for item in story.section_contracts]
    body_section_ids = [item.section_id for item in package_model.body.sections]
    if body_section_ids != [
        item for item in story_section_ids if item in set(body_section_ids)
    ]:
        errors.append(
            "package body section order is not consistent with the story"
        )
    blocked_ids = {
        item.section_id for item in package_model.body.blocked_handoff
    }
    if body_section_ids and set(body_section_ids) & blocked_ids:
        errors.append(
            "package body and blocked handoff share section IDs"
        )
    unknown_blocked = sorted(blocked_ids - set(story_section_ids))
    if unknown_blocked:
        errors.append(f"package blocked handoff has unknown sections {unknown_blocked}")
    combined = body_section_ids + [
        item.section_id for item in package_model.body.blocked_handoff
    ]
    if combined != story_section_ids:
        errors.append(
            "package assembled sections and blocked handoff do not form an "
            "exact non-overlapping partition of the story sections in story "
            "order"
        )
    if len(blocked_ids) != len(package_model.body.blocked_handoff):
        errors.append("package blocked handoff contains duplicate section IDs")
    finding_ids_in_sections = {
        finding_id
        for section in package_model.body.sections
        for finding_id in section.finding_ids
    }
    if {
        finding.finding_id for finding in package_model.findings
    } != finding_ids_in_sections:
        errors.append(
            "package findings do not match section finding_ids"
        )
    return story


def compute_manuscript_body_id(
    plan_id: str,
    ledger_id: str,
    architecture_id: str,
    review_id: str,
    result_id: str,
    story_id: str,
    sections: Sequence[ManuscriptSection | Mapping[str, Any]],
    source_map: Sequence[ParagraphManuscriptSource | Mapping[str, Any]],
    findings: Sequence[ReviewerFinding | Mapping[str, Any]],
    blocked_handoff: Sequence[BlockedSectionHandoff | Mapping[str, Any]],
) -> str:
    """Content-addressed manuscript body identity (public)."""

    section_models = [
        item if isinstance(item, ManuscriptSection)
        else ManuscriptSection.model_validate(item)
        for item in sections
    ]
    source_models = [
        item if isinstance(item, ParagraphManuscriptSource)
        else ParagraphManuscriptSource.model_validate(item)
        for item in source_map
    ]
    finding_models = [
        item if isinstance(item, ReviewerFinding)
        else ReviewerFinding.model_validate(item)
        for item in findings
    ]
    blocked_models = [
        item if isinstance(item, BlockedSectionHandoff)
        else BlockedSectionHandoff.model_validate(item)
        for item in blocked_handoff
    ]
    return _digest(
        str(plan_id),
        str(ledger_id),
        str(architecture_id),
        str(review_id),
        str(result_id),
        str(story_id),
        [_identity_model_dump(item) for item in section_models],
        [_identity_model_dump(item) for item in source_models],
        [_identity_model_dump(item) for item in finding_models],
        [_identity_model_dump(item) for item in blocked_models],
    )


def compute_manuscript_package_id(
    body_id: str,
    body_markdown: str,
    source_map: Sequence[ParagraphManuscriptSource | Mapping[str, Any]],
    findings: Sequence[ReviewerFinding | Mapping[str, Any]],
    blocked_handoff: Sequence[BlockedSectionHandoff | Mapping[str, Any]],
    plan_id: str,
    ledger_id: str,
    architecture_id: str,
    review_id: str,
    result_id: str,
    story_id: str,
) -> str:
    """Content-addressed manuscript package identity (public)."""

    source_models = [
        item if isinstance(item, ParagraphManuscriptSource)
        else ParagraphManuscriptSource.model_validate(item)
        for item in source_map
    ]
    finding_models = [
        item if isinstance(item, ReviewerFinding)
        else ReviewerFinding.model_validate(item)
        for item in findings
    ]
    blocked_models = [
        item if isinstance(item, BlockedSectionHandoff)
        else BlockedSectionHandoff.model_validate(item)
        for item in blocked_handoff
    ]
    return _digest(
        str(body_id),
        str(body_markdown),
        [_identity_model_dump(item) for item in source_models],
        [_identity_model_dump(item) for item in finding_models],
        [_identity_model_dump(item) for item in blocked_models],
        str(plan_id),
        str(ledger_id),
        str(architecture_id),
        str(review_id),
        str(result_id),
        str(story_id),
    )


def build_article_manuscript(
    plan: ArticleDirectorPlan | Mapping[str, Any],
    ledger: ClaimLedgerResult | Mapping[str, Any],
    architecture: ArticleArchitectureResult | Mapping[str, Any],
    review: ArticleReviewResult | Mapping[str, Any],
    selected_story_id: str,
    value_records: Sequence[TrustedValueRecord | Mapping[str, Any]],
    *,
    output_dir: str | Path | None = None,
) -> ArticleManuscriptPackage:
    """Deterministic manuscript-body assembly from an accepted review."""

    errors: List[str] = []
    warnings: List[str] = []
    story, fact_by_claim, aliases = validate_review_result(
        plan,
        ledger,
        architecture,
        review,
        selected_story_id,
        value_records,
        errors,
        warnings,
    )
    if errors or story is None:
        return _hard_blocker(errors, warnings)
    try:
        review_model = (
            review
            if isinstance(review, ArticleReviewResult)
            else ArticleReviewResult.model_validate(review)
        )
        plan_model = (
            plan
            if isinstance(plan, ArticleDirectorPlan)
            else ArticleDirectorPlan.model_validate(plan)
        )
        ledger_model = (
            ledger
            if isinstance(ledger, ClaimLedgerResult)
            else ClaimLedgerResult.model_validate(ledger)
        )
        architecture_model = (
            architecture
            if isinstance(architecture, ArticleArchitectureResult)
            else ArticleArchitectureResult.model_validate(architecture)
        )
    except ValidationError as exc:
        return _hard_blocker([f"review input is invalid: {exc}"], warnings)

    reviewed_by_id = {
        section.section_id: section for section in review_model.sections
    }
    sections: List[ManuscriptSection] = []
    source_map: List[ParagraphManuscriptSource] = []
    blocked_handoff: List[BlockedSectionHandoff] = []
    findings: List[ReviewerFinding] = []
    for section_contract in story.section_contracts:
        reviewed_section = reviewed_by_id.get(section_contract.section_id)
        if reviewed_section is None:
            errors.append(
                f"review has no section {section_contract.section_id!r}"
            )
            continue
        if reviewed_section.status == SectionReviewStatus.blocked:
            blocked_handoff.append(
                BlockedSectionHandoff(
                    section_id=section_contract.section_id,
                    hard_blockers=list(reviewed_section.hard_blockers),
                )
            )
            continue
        section, paragraph_sources = _assemble_section(
            reviewed_section, review_model.story_id
        )
        sections.append(section)
        source_map.extend(paragraph_sources)
        findings.extend(reviewed_section.findings)
    if errors:
        return _hard_blocker(errors, warnings)
    body_markdown = _render_body_markdown(sections)
    status = (
        "assembled"
        if not blocked_handoff and sections
        else ("partial" if sections and blocked_handoff else "blocked")
    )
    body_id = compute_manuscript_body_id(
        plan_model.plan_id,
        ledger_model.ledger_id,
        architecture_model.architecture_id,
        review_model.review_id,
        review_model.result_id,
        review_model.story_id,
        sections,
        source_map,
        findings,
        blocked_handoff,
    )
    body = ArticleManuscriptBody(
        body_id=body_id,
        plan_id=plan_model.plan_id,
        ledger_id=ledger_model.ledger_id,
        architecture_id=architecture_model.architecture_id,
        review_id=review_model.review_id,
        result_id=review_model.result_id,
        story_id=review_model.story_id,
        status=status,
        sections=sections,
        blocked_handoff=blocked_handoff,
        source_map=source_map,
        findings=findings,
        warnings=list(warnings),
        errors=list(errors),
    )
    package_id = compute_manuscript_package_id(
        body_id,
        body_markdown,
        source_map,
        findings,
        blocked_handoff,
        plan_model.plan_id,
        ledger_model.ledger_id,
        architecture_model.architecture_id,
        review_model.review_id,
        review_model.result_id,
        review_model.story_id,
    )
    package = ArticleManuscriptPackage(
        package_id=package_id,
        body_id=body_id,
        plan_id=body.plan_id,
        ledger_id=body.ledger_id,
        architecture_id=body.architecture_id,
        review_id=body.review_id,
        result_id=body.result_id,
        story_id=body.story_id,
        body_markdown=body_markdown,
        body=body,
        source_map=source_map,
        findings=findings,
        blocked_handoff=blocked_handoff,
        warnings=list(warnings),
        errors=list(errors),
    )
    if output_dir is not None:
        write_manuscript_package(package, output_dir)
    return package


def _hard_blocker(
    errors: Sequence[str], warnings: Sequence[str]
) -> ArticleManuscriptPackage:
    body = ArticleManuscriptBody(
        body_id=f"body-{_digest('invalid')}",
        plan_id="",
        ledger_id="",
        architecture_id="",
        review_id="",
        result_id="",
        story_id="",
        status="blocked",
        sections=[],
        blocked_handoff=[],
        source_map=[],
        findings=[],
        warnings=[str(item) for item in warnings],
        errors=[str(item) for item in errors],
    )
    return ArticleManuscriptPackage(
        package_id=f"package-{_digest('invalid')}",
        body_id=body.body_id,
        plan_id="",
        ledger_id="",
        architecture_id="",
        review_id="",
        result_id="",
        story_id="",
        body_markdown="",
        body=body,
        source_map=[],
        findings=[],
        blocked_handoff=[],
        warnings=list(body.warnings),
        errors=list(body.errors),
    )


def write_manuscript_package(
    package: ArticleManuscriptPackage,
    output_dir: str | Path,
) -> Dict[str, Path]:
    """Atomic fixed-name writer; refuses to overwrite conflicting content."""

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    body_path = output_dir / "ARTICLE_MANUSCRIPT_BODY.md"
    package_path = output_dir / "ARTICLE_MANUSCRIPT_PACKAGE.json"
    source_map_path = output_dir / "ARTICLE_SOURCE_MAP.json"
    expected_body = package.body_markdown
    expected_package = _canonical_json(package.model_dump(mode="json"))
    expected_source_map = _canonical_json(
        [item.model_dump(mode="json") for item in package.source_map]
    )
    for path, expected in (
        (body_path, expected_body),
        (package_path, expected_package),
        (source_map_path, expected_source_map),
    ):
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if path.suffix == ".json":
                identical = _json_equal(existing, expected)
            else:
                identical = existing == expected
            if not identical:
                raise ArticleManuscriptIntegrityError(
                    f"refusing to overwrite conflicting {path.name} under "
                    f"package {package.package_id!r}"
                )
    atomic_write_text(body_path, expected_body)
    atomic_write_json(package_path, package.model_dump(mode="json"))
    atomic_write_json(
        source_map_path,
        [item.model_dump(mode="json") for item in package.source_map],
    )
    return {
        "body": body_path,
        "package": package_path,
        "source_map": source_map_path,
    }


__all__ = [
    "ArticleManuscriptBody",
    "ArticleManuscriptError",
    "ArticleManuscriptIntegrityError",
    "ArticleManuscriptPackage",
    "BlockedSectionHandoff",
    "ManuscriptSection",
    "ParagraphManuscriptSource",
    "build_article_manuscript",
    "compute_manuscript_body_id",
    "compute_manuscript_package_id",
    "validate_manuscript_package",
    "write_manuscript_package",
]
