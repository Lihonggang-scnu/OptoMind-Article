"""Stage 11: deterministic fact audit, scientific/expression review, and
bounded author revision for accepted Stage 10 evidence-token section drafts.

The deterministic fact audit is the only hard-review authority: it revalidates
plan/ledger/architecture/bundle identity, recomputes the aggregate source
ledger and alias maps instead of trusting duplicated bundle fields, and
per-paragraph verifies identity, ledger consistency, claim->fact->artifact
provenance, section-local authorization, exact value-token rendering, and
numeric safety.

Scientific and expression reviewers are advisory ``qwen3.7-flash`` roles.
Their findings are minor/major and never become hard blockers merely because
the model says so.  Author revision is a third ``qwen3.7-flash`` role that
edits only paragraphs explicitly named by actionable findings; every revised
section must pass the same Stage 10 assembler/validation plus the
deterministic fact audit, and unaffected paragraphs must remain byte-for-byte
identical.

Fail-open: an audit-clean section with unresolved ordinary scientific/
expression findings is ``ready_with_findings``; reviewer/reviser
unavailability or malformed soft output keeps the last safe draft and records
warnings.  Deterministic hard integrity errors block only the affected
section; valid siblings continue.  The bundle can be partial.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from optomind_optics.harness.article_architecture import (
    ArticleArchitectureResult,
    SectionContract,
    StoryCandidate,
)
from optomind_optics.harness.article_claims import ClaimLedgerResult
from optomind_optics.harness.article_contracts import (
    ARTICLE_EVENT_SCHEMA_VERSION,
    ArticleNodePayload,
    ArticleStage,
    ClaimCard,
    validate_article_event,
)
from optomind_optics.harness.article_director import ArticleDirectorPlan
from optomind_optics.harness.article_memory import (
    ArticleMemoryStore,
    DuplicateRecordError,
    RunMemoryRecord,
)
from optomind_optics.harness.article_writing import (
    ArticleDraftBundle,
    ArticleSectionDraft,
    ParagraphSourceLedger,
    TrustedValueRecord,
    _aggregate_usage,
    _claim_literal_allowlist,
    _claim_authorizes_value_field,
    _source_literal_allowlist,
    _usage_with_cost,
    _VALUE_TOKEN_RE,
    _verify_no_invented_numbers,
    build_writing_alias_maps,
    compute_bundle_id,
    revalidate_section_draft,
    validate_writing_inputs,
)
from optomind_optics.harness.experiment_graph import ExperimentGraph
from optomind_optics.harness.qwen_policy import QwenFlashOnlyClient
from optomind_research.runtime.artifact_store import atomic_write_json


REVIEW_SCHEMA_VERSION = "article-review-result.v1"
REVIEWER_MODEL_NAME = "qwen3.7-flash"
DEFAULT_SCIENTIFIC_MAX_TOKENS = 6000
DEFAULT_EXPRESSION_MAX_TOKENS = 5000
DEFAULT_REVISION_MAX_TOKENS = 6000
DEFAULT_GLOBAL_CONSISTENCY_MAX_TOKENS = 7000
DEFAULT_GLOBAL_ADVICE_ROUTER_MAX_TOKENS = 4000
MAX_REVISION_ROUNDS = 3
SCIENTIFIC_PROMPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "optical_harness"
    / "Article Scientific Reviewer.txt"
)
EXPRESSION_PROMPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "optical_harness"
    / "Article Expression Reviewer.txt"
)
REVISION_PROMPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "optical_harness"
    / "Article Author Reviser.txt"
)
GLOBAL_CONSISTENCY_PROMPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "optical_harness"
    / "Article Global Consistency Reviewer.txt"
)
GLOBAL_ADVICE_ROUTER_PROMPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "optical_harness"
    / "Article Global Advice Router.txt"
)
SEVERITY_WEIGHT = {"minor": 1, "major": 2}


class ArticleReviewError(ValueError):
    """Base error for Stage 11 review failures."""


class ArticleReviewIntegrityError(ArticleReviewError):
    """Unknown/cross-wired provenance or conflicting persistence content."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReviewSeverity(str, Enum):
    minor = "minor"
    major = "major"


class SectionReviewStatus(str, Enum):
    ready = "ready"
    ready_with_findings = "ready_with_findings"
    blocked = "blocked"


class ReviewRoleOutcome(str, Enum):
    valid = "valid"
    unavailable = "unavailable"
    malformed = "malformed"


class DeterministicAuditFinding(_StrictModel):
    schema_version: Literal["deterministic-audit-finding.v1"] = (
        "deterministic-audit-finding.v1"
    )
    finding_id: str
    section_id: str
    paragraph_id: str = ""
    kind: str
    message: str
    severity: Literal["hard"] = "hard"


class ReviewerFinding(_StrictModel):
    schema_version: Literal["reviewer-finding.v1"] = "reviewer-finding.v1"
    finding_id: str
    reviewer: Literal["scientific", "expression"]
    severity: ReviewSeverity
    kind: str
    paragraph_id: str
    span: str = ""
    reason: str
    suggested_action: str = ""
    claim_aliases: List[str] = Field(default_factory=list)
    claim_ids: List[str] = Field(default_factory=list)


class RevisionRound(_StrictModel):
    schema_version: Literal["revision-round.v1"] = "revision-round.v1"
    round_number: int
    before_content_id: str
    after_content_id: str
    before_finding_ids: List[str] = Field(default_factory=list)
    after_finding_ids: List[str] = Field(default_factory=list)
    resolved_finding_ids: List[str] = Field(default_factory=list)
    retained_finding_ids: List[str] = Field(default_factory=list)
    revised_paragraph_ids: List[str] = Field(default_factory=list)
    progress: bool
    warnings: List[str] = Field(default_factory=list)


class ReviewedSection(_StrictModel):
    schema_version: Literal["reviewed-section.v1"] = "reviewed-section.v1"
    section_id: str
    story_id: str
    status: SectionReviewStatus
    section_draft: ArticleSectionDraft
    original_section_draft: ArticleSectionDraft
    audit_findings: List[DeterministicAuditFinding] = Field(default_factory=list)
    hard_blockers: List[str] = Field(default_factory=list)
    findings: List[ReviewerFinding] = Field(default_factory=list)
    revisions: List[RevisionRound] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    reviewer_status: Dict[str, str] = Field(default_factory=dict)
    usage: Dict[str, Any] = Field(default_factory=dict)
    semantic_model: str = "none"
    attempts: int = 0


class ArticleReviewResult(_StrictModel):
    schema_version: Literal["article-review-result.v1"] = "article-review-result.v1"
    review_id: str
    result_id: str
    plan_id: str
    ledger_id: str
    architecture_id: str
    bundle_id: str
    story_id: str
    status: Literal["ready", "ready_with_findings", "blocked", "partial"]
    sections: List[ReviewedSection] = Field(default_factory=list)
    audit_findings: List[DeterministicAuditFinding] = Field(default_factory=list)
    hard_blockers: List[str] = Field(default_factory=list)
    scientific_findings: List[ReviewerFinding] = Field(default_factory=list)
    expression_findings: List[ReviewerFinding] = Field(default_factory=list)
    original_source_ledger: List[ParagraphSourceLedger] = Field(default_factory=list)
    final_source_ledger: List[ParagraphSourceLedger] = Field(default_factory=list)
    retained_advice: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    model_status: Literal["available", "partial", "unavailable"]
    semantic_model: str = "none"
    usage: Dict[str, Any] = Field(default_factory=dict)
    attempts: int = 0


class ReviewerProviderResult(_StrictModel):
    """Envelope returned by any Stage 11 provider (reviewer or reviser)."""

    schema_version: Literal["reviewer-provider-result.v1"] = (
        "reviewer-provider-result.v1"
    )
    response: Dict[str, Any]
    usage: Dict[str, Any] = Field(default_factory=dict)
    provider_model: str = "unknown"
    mock_llm: bool = False


ReviewerProvider = Callable[[Mapping[str, Any]], ReviewerProviderResult]
AuthorReviserProvider = Callable[[Mapping[str, Any]], ReviewerProviderResult]


class _ModelReviewerFinding(_StrictModel):
    paragraph_id: str
    span: str = ""
    severity: ReviewSeverity
    kind: str
    reason: str
    suggested_action: str = ""
    claim_aliases: List[str] = Field(default_factory=list)

    @field_validator("reason", "kind")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not str(value or "").strip():
            raise ValueError("reason/kind must be non-empty")
        return value


class _ModelReviewerResponse(_StrictModel):
    findings: List[_ModelReviewerFinding] = Field(default_factory=list)
    advice: List[str] = Field(default_factory=list)


class _ModelRevisedParagraph(_StrictModel):
    paragraph_id: str
    text_with_value_tokens: str

    @field_validator("text_with_value_tokens")
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        if not str(value or "").strip():
            raise ValueError("revised paragraph text must be non-empty")
        return value


class _ModelRevisionResponse(_StrictModel):
    revised_paragraphs: List[_ModelRevisedParagraph] = Field(min_length=1)
    author_notes: List[str] = Field(default_factory=list)


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
    """Project review payloads onto the stable pre-literature identity."""

    if isinstance(value, Mapping):
        return {
            str(key): _identity_dump(item)
            for key, item in value.items()
            if not (str(key) == "literature_evidence_ids" and item == [])
        }
    if isinstance(value, list):
        return [_identity_dump(item) for item in value]
    if isinstance(value, tuple):
        return [_identity_dump(item) for item in value]
    return value


def _identity_model_json(model: BaseModel) -> str:
    return _canonical_json(_identity_dump(model.model_dump(mode="json")))


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


def _severity_weight(findings: Sequence[ReviewerFinding]) -> int:
    return sum(SEVERITY_WEIGHT.get(item.severity.value, 1) for item in findings)


def _addressed_major_spans(
    before: ArticleSectionDraft,
    after: ArticleSectionDraft,
    findings: Sequence[ReviewerFinding],
) -> List[str]:
    """Return major finding IDs whose exact criticized span was removed.

    Reviewer finding IDs are content-addressed, so a successful rewrite often
    replaces every old ID with new advisory findings.  Counting IDs or total
    findings alone therefore mistakes a repaired major defect for no progress.
    Exact spans give us a model-independent signal that the author acted on a
    concrete major criticism while the deterministic evidence audit remains
    the hard safety boundary.
    """

    before_by_id = {item.paragraph_id: item for item in before.paragraphs}
    after_by_id = {item.paragraph_id: item for item in after.paragraphs}
    addressed: List[str] = []
    for finding in findings:
        if finding.severity != ReviewSeverity.major or not finding.span:
            continue
        original = before_by_id.get(finding.paragraph_id)
        revised = after_by_id.get(finding.paragraph_id)
        if original is None or revised is None:
            continue
        if (
            finding.span in original.rendered_text
            and finding.span not in revised.rendered_text
        ):
            addressed.append(finding.finding_id)
    return sorted(set(addressed))


def _compute_review_status(
    sections: Sequence[ReviewedSection],
) -> Literal["ready", "ready_with_findings", "blocked", "partial"]:
    if not sections:
        return "blocked"
    blocked = [item for item in sections if item.status == SectionReviewStatus.blocked]
    if len(blocked) == len(sections):
        return "blocked"
    if blocked:
        return "partial"
    if any(item.status == SectionReviewStatus.ready_with_findings for item in sections):
        return "ready_with_findings"
    return "ready"


def _section_content_id(draft: ArticleSectionDraft) -> str:
    """Content ID over paragraph prose/ledger only (ignores usage metadata)."""

    return _digest(
        draft.section_id,
        [_identity_model_json(paragraph) for paragraph in draft.paragraphs],
        [_identity_model_json(entry) for entry in draft.source_ledger],
    )


def compute_review_id(
    plan_id: str,
    ledger_id: str,
    architecture_id: str,
    bundle_id: str,
    story_id: str,
) -> str:
    """Content-addressed Stage 11 review-task identity (public)."""

    return _digest(
        str(plan_id),
        str(ledger_id),
        str(architecture_id),
        str(bundle_id),
        str(story_id),
    )


def compute_review_result_id(
    review_id: str,
    sections: Sequence[ReviewedSection | Mapping[str, Any]],
    audit_findings: Sequence[DeterministicAuditFinding | Mapping[str, Any]],
    scientific_findings: Sequence[ReviewerFinding | Mapping[str, Any]],
    expression_findings: Sequence[ReviewerFinding | Mapping[str, Any]],
) -> str:
    """Content-addressed versioned Stage 11 result identity (public)."""

    section_models = [
        (
            item
            if isinstance(item, ReviewedSection)
            else ReviewedSection.model_validate(item)
        )
        for item in sections
    ]
    audit_models = [
        (
            item
            if isinstance(item, DeterministicAuditFinding)
            else DeterministicAuditFinding.model_validate(item)
        )
        for item in audit_findings
    ]
    sci_models = [
        (
            item
            if isinstance(item, ReviewerFinding)
            else ReviewerFinding.model_validate(item)
        )
        for item in scientific_findings
    ]
    expr_models = [
        (
            item
            if isinstance(item, ReviewerFinding)
            else ReviewerFinding.model_validate(item)
        )
        for item in expression_findings
    ]
    return _digest(
        str(review_id),
        [_identity_model_json(item) for item in section_models],
        [_identity_model_json(item) for item in audit_models],
        [_identity_model_json(item) for item in sci_models],
        [_identity_model_json(item) for item in expr_models],
    )


def validate_review_result(
    plan: ArticleDirectorPlan | Mapping[str, Any],
    ledger: ClaimLedgerResult | Mapping[str, Any],
    architecture: ArticleArchitectureResult | Mapping[str, Any],
    review: ArticleReviewResult | Mapping[str, Any],
    selected_story_id: str,
    value_records: Sequence[TrustedValueRecord | Mapping[str, Any]],
    errors: List[str],
    warnings: List[str],
) -> Tuple[Optional[StoryCandidate], Dict[str, Any], Dict[str, Dict[str, Any]]]:
    """Public revalidation of a Stage 11 result without invoking reviewers."""

    try:
        plan_model = (
            plan
            if isinstance(plan, ArticleDirectorPlan)
            else ArticleDirectorPlan.model_validate(plan)
        )
    except ValidationError as exc:
        errors.append(f"plan is invalid: {exc}")
        return None, {}, {}
    try:
        ledger_model = (
            ledger
            if isinstance(ledger, ClaimLedgerResult)
            else ClaimLedgerResult.model_validate(ledger)
        )
    except ValidationError as exc:
        errors.append(f"ledger is invalid: {exc}")
        return None, {}, {}
    try:
        architecture_model = (
            architecture
            if isinstance(architecture, ArticleArchitectureResult)
            else ArticleArchitectureResult.model_validate(architecture)
        )
    except ValidationError as exc:
        errors.append(f"architecture is invalid: {exc}")
        return None, {}, {}
    try:
        review_model = (
            review
            if isinstance(review, ArticleReviewResult)
            else ArticleReviewResult.model_validate(review)
        )
    except ValidationError as exc:
        errors.append(f"review is invalid: {exc}")
        return None, {}, {}
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
        return None, {}, {}

    if review_model.plan_id != plan_model.plan_id:
        errors.append(
            f"review plan_id {review_model.plan_id!r} does not match plan "
            f"{plan_model.plan_id!r}"
        )
    if review_model.ledger_id != ledger_model.ledger_id:
        errors.append(
            f"review ledger_id {review_model.ledger_id!r} does not match "
            f"ledger {ledger_model.ledger_id!r}"
        )
    if review_model.architecture_id != architecture_model.architecture_id:
        errors.append(
            f"review architecture_id {review_model.architecture_id!r} does "
            f"not match architecture {architecture_model.architecture_id!r}"
        )
    if review_model.story_id != selected_story_id:
        errors.append(
            f"review story_id {review_model.story_id!r} does not match "
            f"selected story {selected_story_id!r}"
        )
    story, fact_by_claim = validate_writing_inputs(
        plan_model,
        ledger_model,
        architecture_model,
        selected_story_id,
        records,
        errors,
        warnings,
    )
    if story is None:
        return None, {}, {}
    aliases = build_writing_alias_maps(story, ledger_model, records, fact_by_claim)
    story_section_ids = [item.section_id for item in story.section_contracts]
    review_section_ids = [item.section_id for item in review_model.sections]
    if review_section_ids != story_section_ids:
        errors.append(
            "review section order/IDs do not match the selected story " "section order"
        )
    recomputed_review_id = compute_review_id(
        plan_model.plan_id,
        ledger_model.ledger_id,
        architecture_model.architecture_id,
        review_model.bundle_id,
        review_model.story_id,
    )
    if recomputed_review_id != review_model.review_id:
        errors.append(
            f"review_id {review_model.review_id!r} does not match recomputed "
            f"identity {recomputed_review_id!r}"
        )
    recomputed_result_id = compute_review_result_id(
        review_model.review_id,
        review_model.sections,
        review_model.audit_findings,
        review_model.scientific_findings,
        review_model.expression_findings,
    )
    if recomputed_result_id != review_model.result_id:
        errors.append(
            f"result_id {review_model.result_id!r} does not match recomputed "
            f"identity {recomputed_result_id!r}"
        )
    expected_original_ledger = [
        entry
        for section in review_model.sections
        for entry in section.original_section_draft.source_ledger
    ]
    if expected_original_ledger != review_model.original_source_ledger:
        errors.append(
            "review original_source_ledger is not derivable from its sections"
        )
    expected_final_ledger = [
        entry
        for section in review_model.sections
        for entry in section.section_draft.source_ledger
    ]
    if expected_final_ledger != review_model.final_source_ledger:
        errors.append("review final_source_ledger is not derivable from its sections")
    expected_audit = [
        finding
        for section in review_model.sections
        for finding in section.audit_findings
    ]
    if expected_audit != review_model.audit_findings:
        errors.append("review audit_findings are not derivable from its sections")
    expected_scientific = [
        finding
        for section in review_model.sections
        for finding in section.findings
        if finding.reviewer == "scientific"
    ]
    if expected_scientific != review_model.scientific_findings:
        errors.append("review scientific_findings are not derivable from its sections")
    expected_expression = [
        finding
        for section in review_model.sections
        for finding in section.findings
        if finding.reviewer == "expression"
    ]
    if expected_expression != review_model.expression_findings:
        errors.append("review expression_findings are not derivable from its sections")
    expected_hard_blockers = [
        message
        for section in review_model.sections
        for message in section.hard_blockers
    ]
    if expected_hard_blockers != review_model.hard_blockers:
        errors.append("review hard_blockers are not derivable from its sections")
    expected_status = _compute_review_status(review_model.sections)
    if expected_status != review_model.status:
        errors.append(
            f"review status {review_model.status!r} does not match derived "
            f"status {expected_status!r}"
        )
    paragraph_ids_seen: set[str] = set()
    value_records_by_key = {
        (record.artifact_id, record.field): record for record in records
    }
    for reviewed_section in review_model.sections:
        section = _section_from_story(story, reviewed_section.section_id)
        if section is None:
            errors.append(
                f"review section {reviewed_section.section_id!r} has no "
                "matching story section"
            )
            continue
        for draft in (
            reviewed_section.section_draft,
            reviewed_section.original_section_draft,
        ):
            if draft.section_id != reviewed_section.section_id:
                errors.append(
                    f"review section {reviewed_section.section_id!r} draft "
                    f"section_id {draft.section_id!r} mismatch"
                )
            if draft.story_id != review_model.story_id:
                errors.append(
                    f"review section {reviewed_section.section_id!r} draft "
                    f"story_id {draft.story_id!r} mismatch"
                )
            if draft.architecture_id != review_model.architecture_id:
                errors.append(
                    f"review section {reviewed_section.section_id!r} draft "
                    f"architecture_id {draft.architecture_id!r} mismatch"
                )
        if reviewed_section.story_id != review_model.story_id:
            errors.append(
                f"review section {reviewed_section.section_id!r} wrapper "
                f"story_id {reviewed_section.story_id!r} mismatch"
            )
        if reviewed_section.status == SectionReviewStatus.blocked:
            blocked_findings, blocked_hard = _audit_section(
                plan=plan_model,
                ledger=ledger_model,
                story=story,
                section=section,
                aliases=aliases,
                value_records_by_key=value_records_by_key,
                fact_by_claim=fact_by_claim,
                section_draft=reviewed_section.section_draft,
                paragraph_ids_seen=paragraph_ids_seen,
            )
            expected_blocked_findings = list(blocked_findings)
            expected_blocked_hard = list(blocked_hard)
            if reviewed_section.section_draft.status == "publishable":
                rebuilt, recon_errors = _reconstruct_section_draft(
                    plan=plan_model,
                    ledger=ledger_model,
                    architecture=architecture_model,
                    story=story,
                    section=section,
                    aliases=aliases,
                    value_records_by_key=value_records_by_key,
                    fact_by_claim=fact_by_claim,
                    section_draft=reviewed_section.section_draft,
                )
                if recon_errors:
                    for message in recon_errors:
                        finding_id = "audit-" + _digest(
                            reviewed_section.section_id,
                            "",
                            "reconstruction",
                            message,
                        )
                        expected_blocked_findings.append(
                            DeterministicAuditFinding(
                                finding_id=finding_id,
                                section_id=reviewed_section.section_id,
                                kind="reconstruction",
                                message=message,
                            )
                        )
                        expected_blocked_hard.append(message)
                else:
                    assert rebuilt is not None
                    for message in _compare_reconstructed(
                        reviewed_section.section_draft, rebuilt
                    ):
                        finding_id = "audit-" + _digest(
                            reviewed_section.section_id,
                            "",
                            "reconstruction",
                            message,
                        )
                        expected_blocked_findings.append(
                            DeterministicAuditFinding(
                                finding_id=finding_id,
                                section_id=reviewed_section.section_id,
                                kind="reconstruction",
                                message=message,
                            )
                        )
                        expected_blocked_hard.append(message)
            if (
                reviewed_section.audit_findings != expected_blocked_findings
                or reviewed_section.hard_blockers != expected_blocked_hard
            ):
                errors.append(
                    f"blocked section {reviewed_section.section_id!r} audit "
                    "findings/hard blockers do not match deterministic "
                    "derivation"
                )
            if not expected_blocked_hard:
                errors.append(
                    f"blocked section {reviewed_section.section_id!r} has no "
                    "deterministic hard blockers"
                )
            if reviewed_section.section_draft.model_dump(
                mode="json"
            ) != reviewed_section.original_section_draft.model_dump(mode="json"):
                errors.append(
                    f"blocked section {reviewed_section.section_id!r} original "
                    "and final drafts differ"
                )
            if reviewed_section.findings:
                errors.append(
                    f"blocked section {reviewed_section.section_id!r} carries "
                    "soft findings"
                )
            if reviewed_section.revisions:
                errors.append(
                    f"blocked section {reviewed_section.section_id!r} carries "
                    "revision rounds"
                )
            continue
        if reviewed_section.status not in {
            SectionReviewStatus.ready,
            SectionReviewStatus.ready_with_findings,
        }:
            errors.append(
                f"section {reviewed_section.section_id!r} has unknown status "
                f"{reviewed_section.status!r}"
            )
            continue
        if reviewed_section.hard_blockers:
            errors.append(
                f"non-blocked section {reviewed_section.section_id!r} carries "
                "hard blockers"
            )
        if (
            reviewed_section.status == SectionReviewStatus.ready
            and reviewed_section.findings
        ):
            errors.append(
                f"ready section {reviewed_section.section_id!r} carries findings"
            )
        if (
            reviewed_section.status == SectionReviewStatus.ready_with_findings
            and not reviewed_section.findings
        ):
            errors.append(
                f"ready_with_findings section {reviewed_section.section_id!r} "
                "carries no findings"
            )
        audit_findings, audit_hard = _audit_section(
            plan=plan_model,
            ledger=ledger_model,
            story=story,
            section=section,
            aliases=aliases,
            value_records_by_key=value_records_by_key,
            fact_by_claim=fact_by_claim,
            section_draft=reviewed_section.section_draft,
            paragraph_ids_seen=paragraph_ids_seen,
        )
        if audit_findings != reviewed_section.audit_findings:
            errors.append(
                f"section {reviewed_section.section_id!r} audit findings do "
                "not match deterministic derivation"
            )
        if audit_hard:
            errors.extend(audit_hard)
            continue
        rebuilt, recon_errors = _reconstruct_section_draft(
            plan=plan_model,
            ledger=ledger_model,
            architecture=architecture_model,
            story=story,
            section=section,
            aliases=aliases,
            value_records_by_key=value_records_by_key,
            fact_by_claim=fact_by_claim,
            section_draft=reviewed_section.section_draft,
        )
        if recon_errors:
            errors.extend(recon_errors)
            continue
        assert rebuilt is not None
        errors.extend(_compare_reconstructed(reviewed_section.section_draft, rebuilt))
    return story, fact_by_claim, aliases


def _validate_bundle_inputs(
    plan: ArticleDirectorPlan,
    ledger: ClaimLedgerResult,
    architecture: ArticleArchitectureResult,
    bundle: ArticleDraftBundle,
    selected_story_id: str,
    value_records: Sequence[TrustedValueRecord],
    errors: List[str],
    warnings: List[str],
) -> Tuple[Optional[StoryCandidate], Dict[str, Any], Dict[str, Dict[str, Any]]]:
    if bundle.plan_id != plan.plan_id:
        errors.append(
            f"bundle plan_id {bundle.plan_id!r} does not match plan "
            f"{plan.plan_id!r}"
        )
    if bundle.ledger_id != ledger.ledger_id:
        errors.append(
            f"bundle ledger_id {bundle.ledger_id!r} does not match ledger "
            f"{ledger.ledger_id!r}"
        )
    if bundle.architecture_id != architecture.architecture_id:
        errors.append(
            f"bundle architecture_id {bundle.architecture_id!r} does not "
            f"match architecture {architecture.architecture_id!r}"
        )
    if bundle.story_id != selected_story_id:
        errors.append(
            f"bundle story_id {bundle.story_id!r} does not match selected "
            f"story {selected_story_id!r}"
        )
    recomputed_bundle_id = compute_bundle_id(
        plan.plan_id,
        ledger.ledger_id,
        architecture.architecture_id,
        bundle.story_id,
        bundle.sections,
    )
    if recomputed_bundle_id != bundle.bundle_id:
        errors.append(
            f"bundle_id {bundle.bundle_id!r} does not match recomputed "
            f"identity {recomputed_bundle_id!r} (content changed)"
        )
    recomputed_ledger = [
        entry for section in bundle.sections for entry in section.source_ledger
    ]
    if recomputed_ledger != bundle.source_ledger:
        errors.append(
            "bundle source_ledger is not derivable from its sections "
            "(duplicated or tampered)"
        )
    story, fact_by_claim = validate_writing_inputs(
        plan,
        ledger,
        architecture,
        selected_story_id,
        value_records,
        errors,
        warnings,
    )
    if story is None:
        return None, {}, {}
    aliases = build_writing_alias_maps(story, ledger, value_records, fact_by_claim)
    if aliases["claim_alias_to_id"] != bundle.claim_alias_map:
        errors.append("bundle claim_alias_map is not derivable from inputs")
    if aliases["fact_alias_map"] != bundle.fact_alias_map:
        errors.append("bundle fact_alias_map is not derivable from inputs")
    if aliases["figure_alias_to_id"] != bundle.figure_alias_map:
        errors.append("bundle figure_alias_map is not derivable from inputs")
    if aliases["value_alias_map"] != bundle.value_alias_map:
        errors.append("bundle value_alias_map is not derivable from inputs")
    bundle_section_ids = [section.section_id for section in bundle.sections]
    story_section_ids = [item.section_id for item in story.section_contracts]
    if bundle_section_ids != story_section_ids:
        errors.append(
            "bundle section order/IDs do not match the selected story " "section order"
        )
    return story, fact_by_claim, aliases


def _section_value_authorization(
    story: StoryCandidate,
    section: SectionContract,
    claims_by_id: Mapping[str, ClaimCard],
    fact_by_claim: Mapping[str, Any],
) -> Tuple[set[Tuple[str, str]], Dict[Tuple[str, str], set[str]]]:
    section_figures = [
        figure
        for figure in story.figure_contracts
        if figure.figure_id in set(section.figure_ids)
    ]
    allowed_value_pairs: set[Tuple[str, str]] = {
        (binding.artifact_id, field)
        for figure in section_figures
        for binding in figure.artifact_bindings
        for field in binding.selected_fields
    }
    authorizers: Dict[Tuple[str, str], set[str]] = {}
    for figure in section_figures:
        for binding in figure.artifact_bindings:
            for field in binding.selected_fields:
                claims = {
                    item.claim_id
                    for item in figure.claim_bindings
                    if item.claim_id in claims_by_id
                    and _claim_authorizes_value_field(
                        claims_by_id[item.claim_id],
                        fact_by_claim,
                        binding.artifact_id,
                        field,
                    )
                }
                authorizers.setdefault((binding.artifact_id, field), set()).update(
                    claims
                )
    for placement in section.claim_bindings:
        claim = claims_by_id.get(placement.claim_id)
        if claim is None:
            continue
        for ref in claim.metadata.get("value_lineage") or []:
            artifact_id = str(ref.get("artifact_id") or "")
            field = str(ref.get("field") or "")
            if not (artifact_id and field):
                continue
            key = (artifact_id, field)
            allowed_value_pairs.add(key)
            authorizers.setdefault(key, set()).add(placement.claim_id)
    return allowed_value_pairs, authorizers


def _render_text(
    text: str,
    value_records_by_key: Mapping[Tuple[str, str], TrustedValueRecord],
    value_alias_map: Mapping[str, Mapping[str, Any]],
) -> str:
    rendered = str(text)
    for alias in _VALUE_TOKEN_RE.findall(rendered):
        info = value_alias_map.get(alias)
        if info is None or not info["prose_safe"]:
            continue
        record = value_records_by_key.get((info["artifact_id"], info["field"]))
        if record is None:
            continue
        replacement = record.rendered_value
        if record.unit:
            replacement = f"{replacement} {record.unit}"
        rendered = rendered.replace(f"[VALUE:{alias}]", replacement)
    return rendered


def _audit_section(
    *,
    plan: ArticleDirectorPlan,
    ledger: ClaimLedgerResult,
    story: StoryCandidate,
    section: SectionContract,
    aliases: Mapping[str, Any],
    value_records_by_key: Mapping[Tuple[str, str], TrustedValueRecord],
    fact_by_claim: Mapping[str, Any],
    section_draft: ArticleSectionDraft,
    paragraph_ids_seen: set[str],
) -> Tuple[List[DeterministicAuditFinding], List[str]]:
    findings: List[DeterministicAuditFinding] = []
    hard: List[str] = []
    claims_by_id = {claim.claim_id: claim for claim in ledger.claims}
    source_literals = _source_literal_allowlist(plan)

    def add(kind: str, message: str, paragraph_id: str = "") -> None:
        finding_id = (
            f"audit-{_digest(section_draft.section_id, paragraph_id, kind, message)}"
        )
        findings.append(
            DeterministicAuditFinding(
                finding_id=finding_id,
                section_id=section_draft.section_id,
                paragraph_id=paragraph_id,
                kind=kind,
                message=message,
            )
        )
        hard.append(message)

    if section_draft.status != "publishable":
        add(
            "stage10_status",
            f"section {section_draft.section_id} is not publishable "
            f"(status {section_draft.status})",
        )
    ledger_by_id: Dict[str, ParagraphSourceLedger] = {}
    for entry in section_draft.source_ledger:
        if entry.paragraph_id in ledger_by_id:
            add("identity", f"duplicate source-ledger entry {entry.paragraph_id}")
        ledger_by_id[entry.paragraph_id] = entry
    allowed_value_pairs, authorizers = _section_value_authorization(
        story, section, claims_by_id, fact_by_claim
    )
    value_alias_map = aliases["value_alias_map"]
    section_claim_ids = {item.claim_id for item in section.claim_bindings}
    section_figure_ids = set(section.figure_ids)
    section_roles = {item.claim_id: item.role for item in section.claim_bindings}

    for paragraph in section_draft.paragraphs:
        pid = paragraph.paragraph_id
        if pid in paragraph_ids_seen:
            add("identity", f"duplicate paragraph_id {pid}", pid)
        paragraph_ids_seen.add(pid)
        entry = ledger_by_id.get(pid)
        if entry is None:
            add("identity", f"paragraph {pid} has no source-ledger entry", pid)
            continue
        if paragraph.claim_ids != entry.claim_ids:
            add("ledger_consistency", f"{pid} claim_ids mismatch ledger", pid)
        if paragraph.figure_ids != entry.figure_ids:
            add("ledger_consistency", f"{pid} figure_ids mismatch ledger", pid)
        if paragraph.value_token_ids != entry.value_token_ids:
            add("ledger_consistency", f"{pid} value_token_ids mismatch ledger", pid)
        if paragraph.literature_evidence_ids != entry.literature_evidence_ids:
            add(
                "ledger_consistency",
                f"{pid} literature_evidence_ids mismatch ledger",
                pid,
            )
        if (
            paragraph.inference_kind != entry.inference_kind
            or paragraph.inference_note != entry.inference_note
        ):
            add("ledger_consistency", f"{pid} inference metadata mismatch ledger", pid)
        for claim_id in paragraph.claim_ids:
            claim = claims_by_id.get(claim_id)
            if claim is None:
                add("provenance", f"{pid} references unknown claim {claim_id}", pid)
                continue
            fact = fact_by_claim.get(claim_id)
            source_artifacts = (
                set(fact.source_artifact_ids)
                if fact is not None
                else set(claim.source_artifact_ids)
            )
            missing = sorted(source_artifacts - set(entry.artifact_ids))
            if missing:
                add(
                    "provenance",
                    f"{pid} source ledger omits claim/fact artifacts {missing}",
                    pid,
                )
            if claim_id not in section_claim_ids:
                add(
                    "authorization",
                    f"{pid} claim {claim_id} not assigned to section",
                    pid,
                )
        for figure_id in paragraph.figure_ids:
            if figure_id not in section_figure_ids:
                add(
                    "authorization",
                    f"{pid} figure {figure_id} not assigned to section",
                    pid,
                )
        tokens_in_text = set(_VALUE_TOKEN_RE.findall(paragraph.text_with_value_tokens))
        if tokens_in_text != set(paragraph.value_token_ids):
            add("value", f"{pid} value tokens in text do not match ledger", pid)
        cited = set(paragraph.claim_ids)
        for alias in paragraph.value_token_ids:
            info = value_alias_map.get(alias)
            if info is None:
                add("value", f"{pid} unknown value alias {alias}", pid)
                continue
            record = value_records_by_key.get((info["artifact_id"], info["field"]))
            if record is None or not record.prose_safe:
                add("value", f"{pid} missing/figure-only value record {alias}", pid)
                continue
            key = (info["artifact_id"], info["field"])
            if key not in allowed_value_pairs:
                add(
                    "authorization",
                    f"{pid} value {alias} not bound by any section figure "
                    "or claim-value lineage",
                    pid,
                )
            if not (cited & authorizers.get(key, set())):
                add(
                    "authorization",
                    f"{pid} value {alias} not authorized by a cited claim",
                    pid,
                )
        expected = _render_text(
            paragraph.text_with_value_tokens, value_records_by_key, value_alias_map
        )
        if expected != paragraph.rendered_text:
            add(
                "value_rendering",
                f"{pid} rendered text is not an exact substitution",
                pid,
            )
        numeric_errors: List[str] = []
        numeric_warnings: List[str] = []
        _verify_no_invented_numbers(
            paragraph.text_with_value_tokens,
            numeric_errors,
            numeric_warnings,
            pid,
            source_literals
            | _claim_literal_allowlist(claims_by_id, paragraph.claim_ids),
        )
        for message in numeric_errors:
            add("numeric_safety", f"{pid} {message}", pid)
        if paragraph.role.value == "result" and not paragraph.claim_ids:
            add(
                "inference",
                f"{pid} is a {paragraph.role.value} paragraph with no claims",
                pid,
            )
        elif (
            paragraph.role.value in {"method", "limitation"}
            and not paragraph.claim_ids
            and not paragraph.literature_evidence_ids
        ):
            add(
                "inference",
                f"{pid} is a {paragraph.role.value} paragraph with neither "
                "claims nor literature evidence",
                pid,
            )
        if paragraph.inference_kind.value == "bounded_inference" and (
            not paragraph.claim_ids or not paragraph.inference_note.strip()
        ):
            add(
                "inference",
                f"{pid} bounded_inference requires claims and a note",
                pid,
            )
    return findings, hard


def _section_from_story(
    story: StoryCandidate, section_id: str
) -> Optional[SectionContract]:
    return next(
        (item for item in story.section_contracts if item.section_id == section_id),
        None,
    )


def _reconstruct_section_draft(
    *,
    plan: ArticleDirectorPlan,
    ledger: ClaimLedgerResult,
    architecture: ArticleArchitectureResult,
    story: StoryCandidate,
    section: SectionContract,
    aliases: Mapping[str, Any],
    value_records_by_key: Mapping[Tuple[str, str], TrustedValueRecord],
    fact_by_claim: Mapping[str, Any],
    section_draft: ArticleSectionDraft,
) -> Tuple[Optional[ArticleSectionDraft], List[str]]:
    """Rebuild a section from its stored paragraph response and authoritative
    inputs using the same Stage 10 assembler."""

    errors: List[str] = []
    claim_id_to_alias = aliases["claim_id_to_alias"]
    figure_id_to_alias = aliases["figure_id_to_alias"]
    paragraphs = []
    for paragraph in section_draft.paragraphs:
        claim_aliases: List[str] = []
        for claim_id in paragraph.claim_ids:
            alias = claim_id_to_alias.get(claim_id)
            if alias is None:
                errors.append(
                    f"paragraph {paragraph.paragraph_id} claim {claim_id!r} "
                    "cannot be reconstructed from authoritative inputs"
                )
            else:
                claim_aliases.append(alias)
        figure_aliases: List[str] = []
        for figure_id in paragraph.figure_ids:
            alias = figure_id_to_alias.get(figure_id)
            if alias is None:
                errors.append(
                    f"paragraph {paragraph.paragraph_id} figure {figure_id!r} "
                    "cannot be reconstructed from authoritative inputs"
                )
            else:
                figure_aliases.append(alias)
        for alias in paragraph.value_token_ids:
            if alias not in aliases["value_alias_map"]:
                errors.append(
                    f"paragraph {paragraph.paragraph_id} value alias "
                    f"{alias!r} cannot be reconstructed from authoritative "
                    "inputs"
                )
        paragraphs.append(
            {
                "text_with_value_tokens": paragraph.text_with_value_tokens,
                "claim_aliases": claim_aliases,
                "figure_aliases": figure_aliases,
                "paragraph_role": paragraph.role.value,
                "inference_kind": paragraph.inference_kind.value,
                "inference_note": paragraph.inference_note,
            }
        )
    deferred_aliases: List[str] = []
    for claim_id in section_draft.deferred_claim_ids:
        alias = claim_id_to_alias.get(claim_id)
        if alias is None:
            errors.append(
                f"deferred claim {claim_id!r} cannot be reconstructed from "
                "authoritative inputs"
            )
        else:
            deferred_aliases.append(alias)
    if errors:
        return None, errors
    raw_response = {
        "paragraphs": paragraphs,
        "deferred_claim_aliases": deferred_aliases,
        "author_notes": list(section_draft.author_notes),
    }
    try:
        rebuilt = revalidate_section_draft(
            plan=plan,
            ledger=ledger,
            architecture_id=architecture.architecture_id,
            story=story,
            section=section,
            aliases=aliases,
            value_records_by_key=value_records_by_key,
            fact_by_claim=fact_by_claim,
            raw_response=raw_response,
            semantic_model=section_draft.semantic_model,
            usage=section_draft.usage,
            model_status=section_draft.model_status,
            repair_rounds=section_draft.repair_rounds,
            attempts=section_draft.attempts,
            preserved_literature_evidence_ids={
                paragraph.paragraph_id: paragraph.literature_evidence_ids
                for paragraph in section_draft.paragraphs
            },
        )
    except ValidationError as exc:
        return None, [
            f"section {section_draft.section_id} cannot be " f"reconstructed: {exc}"
        ]
    return rebuilt, []


def _compare_reconstructed(
    stored: ArticleSectionDraft, rebuilt: ArticleSectionDraft
) -> List[str]:
    """Compare every science/provenance-bearing derived field."""

    mismatches: List[str] = []

    def check(label: str, left: Any, right: Any) -> None:
        if left != right:
            mismatches.append(f"{label} mismatch")

    check("section_id", stored.section_id, rebuilt.section_id)
    check("title", stored.title, rebuilt.title)
    check("story_id", stored.story_id, rebuilt.story_id)
    check("architecture_id", stored.architecture_id, rebuilt.architecture_id)
    check("figure_ids", stored.figure_ids, rebuilt.figure_ids)
    check("status", stored.status, rebuilt.status)
    check("tokenized_prose", stored.tokenized_prose, rebuilt.tokenized_prose)
    check("rendered_prose", stored.rendered_prose, rebuilt.rendered_prose)
    check("word_count", stored.word_count, rebuilt.word_count)
    check("target_word_range", stored.target_word_range, rebuilt.target_word_range)
    check(
        "deferred_claim_aliases",
        stored.deferred_claim_aliases,
        rebuilt.deferred_claim_aliases,
    )
    check("deferred_claim_ids", stored.deferred_claim_ids, rebuilt.deferred_claim_ids)
    check("author_notes", stored.author_notes, rebuilt.author_notes)
    check("warnings", stored.warnings, rebuilt.warnings)
    check("errors", stored.errors, rebuilt.errors)
    check(
        "paragraphs",
        [item.model_dump(mode="json") for item in stored.paragraphs],
        [item.model_dump(mode="json") for item in rebuilt.paragraphs],
    )
    check(
        "source_ledger",
        [item.model_dump(mode="json") for item in stored.source_ledger],
        [item.model_dump(mode="json") for item in rebuilt.source_ledger],
    )
    return mismatches


def _build_review_request(
    *,
    plan: ArticleDirectorPlan,
    ledger: ClaimLedgerResult,
    architecture: ArticleArchitectureResult,
    story: StoryCandidate,
    section: SectionContract,
    section_draft: ArticleSectionDraft,
    aliases: Mapping[str, Any],
    value_records: Sequence[TrustedValueRecord],
    fact_by_claim: Mapping[str, Any],
    role: Literal["scientific", "expression"],
) -> Dict[str, Any]:
    claims_by_id = {claim.claim_id: claim for claim in ledger.claims}
    section_claims = sorted({item.claim_id for item in section.claim_bindings})
    claims = []
    for claim_id in section_claims:
        claim = claims_by_id[claim_id]
        fact = fact_by_claim.get(claim_id)
        claims.append(
            {
                "claim_alias": aliases["claim_id_to_alias"][claim_id],
                "statement": claim.statement,
                "scope": claim.scope,
                "strength": claim.strength.value,
                "status": claim.status.value,
                "roles": [
                    item.role
                    for item in section.claim_bindings
                    if item.claim_id == claim_id
                ],
                "limits": list(claim.metadata.get("limits") or []),
                "synthesis_contract": dict(
                    claim.metadata.get("synthesis_contract") or {}
                ),
                "fact_statement": fact.statement if fact is not None else None,
                "source_artifact_ids": list(claim.source_artifact_ids),
            }
        )
    section_claim_ids = {item.claim_id for item in section.claim_bindings}
    section_artifact_ids = set()
    for entry in section_draft.source_ledger:
        section_artifact_ids.update(entry.artifact_ids)
    for claim_id in section_claim_ids:
        claim = claims_by_id.get(claim_id)
        if claim is not None:
            section_artifact_ids.update(claim.source_artifact_ids)
            fact = fact_by_claim.get(claim_id)
            if fact is not None:
                section_artifact_ids.update(fact.source_artifact_ids)
    for figure in story.figure_contracts:
        if figure.figure_id in set(section.figure_ids):
            for binding in figure.artifact_bindings:
                section_artifact_ids.add(binding.artifact_id)
    allowed_value_pairs, _ = _section_value_authorization(
        story, section, claims_by_id, fact_by_claim
    )
    artifacts = [
        descriptor.model_dump(mode="json")
        for descriptor in architecture.artifact_inventory
        if descriptor.artifact_id in set(section_artifact_ids)
    ]
    values = [
        {
            "alias": alias,
            "label": info["label"],
            "unit": info["unit"],
        }
        for alias, info in aliases["value_alias_map"].items()
        if (info["artifact_id"], info["field"]) in allowed_value_pairs
    ]
    paragraphs = []
    for paragraph in section_draft.paragraphs:
        paragraphs.append(
            {
                "paragraph_id": paragraph.paragraph_id,
                "text": paragraph.rendered_text,
                "text_with_value_tokens": paragraph.text_with_value_tokens,
                "claim_aliases": [
                    aliases["claim_id_to_alias"][claim_id]
                    for claim_id in paragraph.claim_ids
                    if claim_id in aliases["claim_id_to_alias"]
                ],
                "figure_aliases": [
                    aliases["figure_id_to_alias"][figure_id]
                    for figure_id in paragraph.figure_ids
                    if figure_id in aliases["figure_id_to_alias"]
                ],
                "value_labels": [
                    aliases["value_alias_map"][alias]["label"]
                    for alias in paragraph.value_token_ids
                    if alias in aliases["value_alias_map"]
                ],
                "paragraph_role": paragraph.role.value,
                "inference_kind": paragraph.inference_kind.value,
                "inference_note": paragraph.inference_note,
            }
        )
    source_ledger = []
    for entry in section_draft.source_ledger:
        source_ledger.append(
            {
                "paragraph_id": entry.paragraph_id,
                "claim_aliases": [
                    aliases["claim_id_to_alias"][claim_id]
                    for claim_id in entry.claim_ids
                    if claim_id in aliases["claim_id_to_alias"]
                ],
                "figure_aliases": [
                    aliases["figure_id_to_alias"][figure_id]
                    for figure_id in entry.figure_ids
                    if figure_id in aliases["figure_id_to_alias"]
                ],
                "value_labels": [
                    aliases["value_alias_map"][alias]["label"]
                    for alias in entry.value_token_ids
                    if alias in aliases["value_alias_map"]
                ],
                "artifact_ids": list(entry.artifact_ids),
                "scopes": list(entry.scopes),
                "limits": list(entry.limits),
                "roles": list(entry.roles),
            }
        )
    return {
        "task": (
            "Review one article section. Advisory only; do not change any "
            "facts, values, sources, IDs, or claim bindings."
        ),
        "review_role": role,
        "question": plan.charter.question,
        "charter_scope": plan.charter.scope,
        "story": {
            "story_id": story.story_id,
            "story_shape": story.story_shape,
            "central_thesis": story.central_thesis,
        },
        "section": {
            "section_id": section.section_id,
            "heading": section.heading,
            "purpose": section.purpose,
        },
        "claims": claims,
        "artifacts": artifacts,
        "values": values,
        "paragraphs": paragraphs,
        "source_ledger": source_ledger,
        "response_contract": {
            "findings": [
                {
                    "paragraph_id": "story-01-section-01-p01",
                    "span": "optional exact span",
                    "severity": "minor|major",
                    "kind": "short machine kind",
                    "reason": "concise reason",
                    "suggested_action": "action",
                    "claim_aliases": ["C01_..."],
                }
            ],
            "advice": ["optional general advice"],
        },
    }


def _build_global_consistency_request(
    *,
    plan: ArticleDirectorPlan,
    ledger: ClaimLedgerResult,
    story: StoryCandidate,
    bundle: ArticleDraftBundle,
    aliases: Mapping[str, Any],
    fact_by_claim: Mapping[str, Any],
    focus: str = "all",
) -> Dict[str, Any]:
    claims_by_id = {claim.claim_id: claim for claim in ledger.claims}
    assignment_by_claim = {item.claim_id: item for item in story.claim_assignments}
    claims = []
    for claim_id in sorted(assignment_by_claim):
        claim = claims_by_id.get(claim_id)
        if claim is None:
            continue
        assignment = assignment_by_claim[claim_id]
        fact = fact_by_claim.get(claim_id)
        claims.append(
            {
                "claim_alias": aliases["claim_id_to_alias"][claim_id],
                "statement": claim.statement,
                "scope": claim.scope,
                "strength": claim.strength.value,
                "status": claim.status.value,
                "story_role": assignment.role,
                "assigned_section_ids": list(assignment.section_ids),
                "limits": list(claim.metadata.get("limits") or []),
                "synthesis_contract": dict(
                    claim.metadata.get("synthesis_contract") or {}
                ),
                "fact_statement": fact.statement if fact is not None else None,
            }
        )
    section_by_id = {item.section_id: item for item in story.section_contracts}
    paragraphs = []
    for section_draft in bundle.sections:
        section = section_by_id.get(section_draft.section_id)
        for paragraph in section_draft.paragraphs:
            section_heading = section.heading if section is not None else ""
            if focus == "recommendation":
                if (
                    paragraph.role.value not in {"discussion", "conclusion"}
                    and paragraph.inference_kind.value != "unsupported"
                ):
                    continue
            elif focus == "cross_metric":
                if paragraph.role.value not in {"result", "discussion"}:
                    continue
            elif focus == "boundary":
                if paragraph.role.value not in {
                    "background",
                    "method",
                    "limitation",
                    "transition",
                }:
                    continue
            paragraphs.append(
                {
                    "paragraph_id": paragraph.paragraph_id,
                    "section_id": section_draft.section_id,
                    "section_heading": section_heading,
                    "section_purpose": section.purpose if section is not None else "",
                    "text": paragraph.rendered_text,
                    "text_with_value_tokens": paragraph.text_with_value_tokens,
                    "claim_aliases": [
                        aliases["claim_id_to_alias"][claim_id]
                        for claim_id in paragraph.claim_ids
                        if claim_id in aliases["claim_id_to_alias"]
                    ],
                    "paragraph_role": paragraph.role.value,
                    "inference_kind": paragraph.inference_kind.value,
                    "inference_note": paragraph.inference_note,
                }
            )
    return {
        "task": (
            "Audit the complete Article draft once for cross-section and "
            "cross-metric scientific consistency. Advisory only; do not "
            "change facts, values, sources, IDs, or claim bindings."
        ),
        "review_role": "global_consistency",
        "focus": focus,
        "question": plan.charter.question,
        "charter_scope": plan.charter.scope,
        "story": {
            "story_id": story.story_id,
            "story_shape": story.story_shape,
            "central_thesis": story.central_thesis,
        },
        "claims": claims,
        "paragraphs": paragraphs,
        "response_contract": {
            "findings": [
                {
                    "paragraph_id": "story-01-section-01-p01",
                    "span": "optional exact span",
                    "severity": "minor|major",
                    "kind": "short machine kind",
                    "reason": "concise reason",
                    "suggested_action": "action",
                    "claim_aliases": [],
                }
            ],
            "advice": ["optional whole-Article advice"],
        },
    }


def _parse_reviewer_findings(
    *,
    reviewer: Literal["scientific", "expression"],
    raw_response: Mapping[str, Any],
    known_paragraph_ids: set[str],
    section_alias_to_claim: Mapping[str, str],
    paragraph_texts: Mapping[str, Tuple[str, str]],
    warnings: List[str],
) -> Tuple[List[ReviewerFinding], ReviewRoleOutcome]:
    findings: List[ReviewerFinding] = []
    seen_finding_ids: set[str] = set()
    duplicate_count = 0
    try:
        model_response = _ModelReviewerResponse.model_validate(dict(raw_response))
    except ValidationError as exc:
        warnings.append(f"{reviewer} reviewer response is malformed: {exc}")
        return findings, ReviewRoleOutcome.malformed
    for index, raw in enumerate(model_response.findings):
        if raw.paragraph_id not in known_paragraph_ids:
            warnings.append(
                f"{reviewer} finding[{index}] targets unknown paragraph "
                f"{raw.paragraph_id!r}; skipped"
            )
            continue
        span = raw.span
        rendered_text, tokenized_text = paragraph_texts.get(raw.paragraph_id, ("", ""))
        if span and span not in rendered_text and span not in tokenized_text:
            warnings.append(
                f"{reviewer} finding[{index}] span {span!r} is absent from "
                f"paragraph {raw.paragraph_id!r}; span cleared"
            )
            span = ""
        claim_aliases = [
            alias for alias in raw.claim_aliases if alias in section_alias_to_claim
        ]
        dropped_aliases = sorted(set(raw.claim_aliases) - set(claim_aliases))
        if dropped_aliases:
            warnings.append(
                f"{reviewer} finding[{index}] dropped non-section claim "
                f"aliases {dropped_aliases}"
            )
        claim_ids = sorted({section_alias_to_claim[alias] for alias in claim_aliases})
        finding_id = (
            f"review-{_digest(reviewer, raw.paragraph_id, raw.kind, span, raw.reason)}"
        )
        if finding_id in seen_finding_ids:
            duplicate_count += 1
            continue
        seen_finding_ids.add(finding_id)
        findings.append(
            ReviewerFinding(
                finding_id=finding_id,
                reviewer=reviewer,
                severity=raw.severity,
                kind=raw.kind,
                paragraph_id=raw.paragraph_id,
                span=span,
                reason=raw.reason,
                suggested_action=raw.suggested_action,
                claim_aliases=claim_aliases,
                claim_ids=claim_ids,
            )
        )
    if duplicate_count:
        warnings.append(
            f"{reviewer} reviewer response contained {duplicate_count} exact "
            "duplicate finding(s); deduplicated"
        )
    return findings, ReviewRoleOutcome.valid


def _build_revision_request(
    *,
    plan: ArticleDirectorPlan,
    ledger: ClaimLedgerResult,
    architecture: ArticleArchitectureResult,
    story: StoryCandidate,
    section: SectionContract,
    section_draft: ArticleSectionDraft,
    aliases: Mapping[str, Any],
    value_records: Sequence[TrustedValueRecord],
    fact_by_claim: Mapping[str, Any],
    findings: Sequence[ReviewerFinding],
) -> Dict[str, Any]:
    base = _build_review_request(
        plan=plan,
        ledger=ledger,
        architecture=architecture,
        story=story,
        section=section,
        section_draft=section_draft,
        aliases=aliases,
        value_records=value_records,
        fact_by_claim=fact_by_claim,
        role="revision",
    )
    actionable = [
        {
            "finding_id": item.finding_id,
            "paragraph_id": item.paragraph_id,
            "severity": item.severity.value,
            "kind": item.kind,
            "reason": item.reason,
            "suggested_action": item.suggested_action,
            "claim_aliases": list(item.claim_aliases),
        }
        for item in findings
        if item.paragraph_id and item.suggested_action
    ]
    base["findings"] = actionable
    base["response_contract"] = {
        "revised_paragraphs": [
            {
                "paragraph_id": "story-01-section-01-p01",
                "text_with_value_tokens": (
                    "revised prose; exact values only via provided "
                    "[VALUE:...] tokens"
                ),
            }
        ],
        "author_notes": ["concise note"],
    }
    return base


def _merge_revision(
    original: ArticleSectionDraft,
    raw_response: Mapping[str, Any],
    aliases: Mapping[str, Any],
) -> Dict[str, Any]:
    try:
        model = _ModelRevisionResponse.model_validate(dict(raw_response))
    except ValidationError as exc:
        raise ValueError(f"malformed revision response: {exc}") from exc
    original_ids = {paragraph.paragraph_id for paragraph in original.paragraphs}
    revised_by_id = {item.paragraph_id: item for item in model.revised_paragraphs}
    seen_targets = set()
    for item in model.revised_paragraphs:
        if item.paragraph_id in seen_targets:
            raise ValueError(
                f"revision lists duplicate paragraph target " f"{item.paragraph_id!r}"
            )
        seen_targets.add(item.paragraph_id)
    unknown_targets = sorted(set(revised_by_id) - original_ids)
    if unknown_targets:
        raise ValueError(f"revision targets unknown paragraphs: {unknown_targets}")
    paragraphs = []
    for paragraph in original.paragraphs:
        if paragraph.paragraph_id in revised_by_id:
            text = revised_by_id[paragraph.paragraph_id].text_with_value_tokens
        else:
            text = paragraph.text_with_value_tokens
        paragraphs.append(
            {
                "text_with_value_tokens": text,
                "claim_aliases": [
                    aliases["claim_id_to_alias"][claim_id]
                    for claim_id in paragraph.claim_ids
                ],
                "figure_aliases": [
                    aliases["figure_id_to_alias"][figure_id]
                    for figure_id in paragraph.figure_ids
                ],
                "paragraph_role": paragraph.role.value,
                "inference_kind": paragraph.inference_kind.value,
                "inference_note": paragraph.inference_note,
            }
        )
    return {
        "paragraphs": paragraphs,
        "deferred_claim_aliases": list(original.deferred_claim_aliases),
        "author_notes": list(original.author_notes) + list(model.author_notes),
    }


def _unaffected_paragraphs_unchanged(
    original: ArticleSectionDraft,
    revised: ArticleSectionDraft,
    revised_ids: Sequence[str],
) -> bool:
    original_by_id = {item.paragraph_id: item for item in original.paragraphs}
    for paragraph in revised.paragraphs:
        if paragraph.paragraph_id in revised_ids:
            continue
        original_paragraph = original_by_id.get(paragraph.paragraph_id)
        if original_paragraph is None:
            return False
        if original_paragraph.model_dump(mode="json") != paragraph.model_dump(
            mode="json"
        ):
            return False
    return True


def _revision_preserves_bindings(
    original: ArticleSectionDraft,
    revised: ArticleSectionDraft,
    revised_ids: Sequence[str],
) -> Optional[str]:
    """Revisions may change prose only; source bindings must be immutable."""

    original_by_id = {item.paragraph_id: item for item in original.paragraphs}
    revised_by_id = {item.paragraph_id: item for item in revised.paragraphs}
    original_ledger = {entry.paragraph_id: entry for entry in original.source_ledger}
    revised_ledger = {entry.paragraph_id: entry for entry in revised.source_ledger}
    for pid in revised_ids:
        orig = original_by_id.get(pid)
        rev = revised_by_id.get(pid)
        if orig is None or rev is None:
            return f"paragraph {pid} missing after revision"
        for field in (
            "claim_ids",
            "figure_ids",
            "value_token_ids",
            "literature_evidence_ids",
        ):
            if getattr(orig, field) != getattr(rev, field):
                return (
                    f"paragraph {pid} changed {field} "
                    f"({getattr(orig, field)} -> {getattr(rev, field)})"
                )
        for field in ("role", "inference_kind", "inference_note"):
            if getattr(orig, field) != getattr(rev, field):
                return f"paragraph {pid} changed {field}"
        orig_entry = original_ledger.get(pid)
        rev_entry = revised_ledger.get(pid)
        if orig_entry is None or rev_entry is None:
            return f"paragraph {pid} source ledger missing after revision"
        if orig_entry.model_dump(mode="json") != rev_entry.model_dump(mode="json"):
            return f"paragraph {pid} source ledger changed"
    return None


def _run_global_consistency_review(
    *,
    plan: ArticleDirectorPlan,
    ledger: ClaimLedgerResult,
    story: StoryCandidate,
    bundle: ArticleDraftBundle,
    aliases: Mapping[str, Any],
    fact_by_claim: Mapping[str, Any],
    provider: Optional[ReviewerProvider],
    advice_router: Optional[ReviewerProvider],
    warnings: List[str],
) -> Tuple[List[ReviewerFinding], Dict[str, Any], str, int, str]:
    if provider is None:
        return [], {}, "none", 0, ReviewRoleOutcome.unavailable.value
    paragraph_texts = {
        paragraph.paragraph_id: (
            paragraph.rendered_text,
            paragraph.text_with_value_tokens,
        )
        for section in bundle.sections
        for paragraph in section.paragraphs
    }
    findings: Dict[str, ReviewerFinding] = {}
    usages: List[Dict[str, Any]] = []
    models: List[str] = []
    outcomes: List[str] = []
    attempts = 0
    for focus in ("recommendation", "cross_metric", "boundary"):
        request = _build_global_consistency_request(
            plan=plan,
            ledger=ledger,
            story=story,
            bundle=bundle,
            aliases=aliases,
            fact_by_claim=fact_by_claim,
            focus=focus,
        )
        if not request["paragraphs"]:
            continue
        try:
            attempts += 1
            envelope = provider(request)
            if not isinstance(envelope, ReviewerProviderResult):
                raise TypeError(
                    "global consistency provider must return " "ReviewerProviderResult"
                )
            usages.append(dict(envelope.usage or {}))
            models.append(envelope.provider_model)
            parsed, outcome = _parse_reviewer_findings(
                reviewer="scientific",
                raw_response=envelope.response,
                known_paragraph_ids=set(paragraph_texts),
                section_alias_to_claim=dict(aliases["claim_alias_to_id"]),
                paragraph_texts=paragraph_texts,
                warnings=warnings,
            )
            outcomes.append(outcome.value)
            findings.update({item.finding_id: item for item in parsed})
            advice_rows = [
                item
                for item in (envelope.response.get("advice") or [])
                if isinstance(item, str) and item.strip()
            ]
            if advice_rows:
                warnings.extend(
                    f"global consistency advice ({focus}): {item}"
                    for item in advice_rows
                )
            if advice_rows and advice_router is not None:
                router_request = {
                    **request,
                    "task": (
                        "Convert concrete scientific defects in "
                        "advice_to_route into paragraph-targeted findings. "
                        "Discard optional organization advice."
                    ),
                    "advice_to_route": advice_rows,
                    "auditor_findings": list(envelope.response.get("findings") or []),
                }
                attempts += 1
                router_envelope = advice_router(router_request)
                if not isinstance(router_envelope, ReviewerProviderResult):
                    raise TypeError(
                        "global advice router must return ReviewerProviderResult"
                    )
                usages.append(dict(router_envelope.usage or {}))
                models.append(router_envelope.provider_model)
                routed, routed_outcome = _parse_reviewer_findings(
                    reviewer="scientific",
                    raw_response=router_envelope.response,
                    known_paragraph_ids=set(paragraph_texts),
                    section_alias_to_claim=dict(aliases["claim_alias_to_id"]),
                    paragraph_texts=paragraph_texts,
                    warnings=warnings,
                )
                outcomes.append(routed_outcome.value)
                findings.update({item.finding_id: item for item in routed})
                if router_envelope.response.get("advice"):
                    warnings.extend(
                        f"global advice router retained ({focus}): {item}"
                        for item in router_envelope.response["advice"]
                        if isinstance(item, str) and item.strip()
                    )
        except Exception as exc:
            outcomes.append(ReviewRoleOutcome.unavailable.value)
            warnings.append(f"global consistency reviewer ({focus}) unavailable: {exc}")
    if not outcomes:
        outcome = ReviewRoleOutcome.unavailable.value
    elif all(item == ReviewRoleOutcome.valid.value for item in outcomes):
        outcome = ReviewRoleOutcome.valid.value
    elif any(item == ReviewRoleOutcome.valid.value for item in outcomes):
        outcome = ReviewRoleOutcome.malformed.value
    else:
        outcome = ReviewRoleOutcome.unavailable.value
    return (
        list(findings.values()),
        _aggregate_usage(usages),
        models[0] if len(set(models)) == 1 else ("mixed" if models else "none"),
        attempts,
        outcome,
    )


def _review_section(
    *,
    plan: ArticleDirectorPlan,
    ledger: ClaimLedgerResult,
    architecture: ArticleArchitectureResult,
    story: StoryCandidate,
    section: SectionContract,
    section_draft: ArticleSectionDraft,
    aliases: Mapping[str, Any],
    value_records: Sequence[TrustedValueRecord],
    fact_by_claim: Mapping[str, Any],
    scientific_reviewer: Optional[ReviewerProvider],
    expression_reviewer: Optional[ReviewerProvider],
    warnings: List[str],
) -> Tuple[
    Dict[str, str],
    List[ReviewerFinding],
    List[ReviewerFinding],
    Dict[str, Any],
    str,
    int,
]:
    known_paragraph_ids = {
        paragraph.paragraph_id for paragraph in section_draft.paragraphs
    }
    section_claim_ids = {item.claim_id for item in section.claim_bindings}
    section_alias_to_claim = {
        alias: claim_id
        for alias, claim_id in aliases["claim_alias_to_id"].items()
        if claim_id in section_claim_ids
    }
    paragraph_texts = {
        paragraph.paragraph_id: (
            paragraph.rendered_text,
            paragraph.text_with_value_tokens,
        )
        for paragraph in section_draft.paragraphs
    }
    scientific: List[ReviewerFinding] = []
    expression: List[ReviewerFinding] = []
    outcomes: Dict[str, str] = {}
    usages: List[Dict[str, Any]] = []
    models: List[str] = []
    attempts = 0
    for reviewer, provider in (
        ("scientific", scientific_reviewer),
        ("expression", expression_reviewer),
    ):
        if provider is None:
            outcomes[reviewer] = ReviewRoleOutcome.unavailable.value
            warnings.append(f"{reviewer} reviewer provider not supplied")
            continue
        request = _build_review_request(
            plan=plan,
            ledger=ledger,
            architecture=architecture,
            story=story,
            section=section,
            section_draft=section_draft,
            aliases=aliases,
            value_records=value_records,
            fact_by_claim=fact_by_claim,
            role=reviewer,
        )
        attempts += 1
        try:
            envelope = provider(request)
            if not isinstance(envelope, ReviewerProviderResult):
                raise TypeError(
                    f"{reviewer} provider must return ReviewerProviderResult"
                )
            usages.append(dict(envelope.usage or {}))
            models.append(envelope.provider_model)
            parsed, outcome = _parse_reviewer_findings(
                reviewer=reviewer,
                raw_response=envelope.response,
                known_paragraph_ids=known_paragraph_ids,
                section_alias_to_claim=section_alias_to_claim,
                paragraph_texts=paragraph_texts,
                warnings=warnings,
            )
            outcomes[reviewer] = outcome.value
            if reviewer == "scientific":
                scientific.extend(parsed)
            else:
                expression.extend(parsed)
            if envelope.response.get("advice"):
                warnings.extend(
                    f"{reviewer} advice: {item}"
                    for item in envelope.response["advice"]
                    if isinstance(item, str) and item.strip()
                )
        except Exception as exc:
            outcomes[reviewer] = ReviewRoleOutcome.unavailable.value
            warnings.append(f"{reviewer} reviewer unavailable: {exc}")
    semantic_model = (
        models[0] if len(set(models)) == 1 else ("mixed" if models else "none")
    )
    return (
        outcomes,
        scientific,
        expression,
        _aggregate_usage(usages),
        semantic_model,
        attempts,
    )


def _run_revisions(
    *,
    plan: ArticleDirectorPlan,
    ledger: ClaimLedgerResult,
    architecture: ArticleArchitectureResult,
    story: StoryCandidate,
    section: SectionContract,
    aliases: Mapping[str, Any],
    value_records: Sequence[TrustedValueRecord],
    fact_by_claim: Mapping[str, Any],
    value_records_by_key: Mapping[Tuple[str, str], TrustedValueRecord],
    current_draft: ArticleSectionDraft,
    scientific_findings: List[ReviewerFinding],
    expression_findings: List[ReviewerFinding],
    initial_outcomes: Mapping[str, str],
    scientific_reviewer: Optional[ReviewerProvider],
    expression_reviewer: Optional[ReviewerProvider],
    author_reviser: Optional[AuthorReviserProvider],
    warnings: List[str],
) -> Tuple[
    ArticleSectionDraft,
    List[ReviewerFinding],
    List[RevisionRound],
    Dict[str, Any],
    str,
    int,
    Dict[str, str],
]:
    if author_reviser is None:
        return (
            current_draft,
            scientific_findings + expression_findings,
            [],
            {},
            "none",
            0,
            dict(initial_outcomes),
        )
    all_findings = scientific_findings + expression_findings
    rounds: List[RevisionRound] = []
    usages: List[Dict[str, Any]] = []
    models: List[str] = []
    attempts = 0
    current_outcomes = dict(initial_outcomes)
    for round_number in range(1, MAX_REVISION_ROUNDS + 1):
        actionable = [
            item for item in all_findings if item.paragraph_id and item.suggested_action
        ]
        if not actionable:
            break
        before_content_id = _section_content_id(current_draft)
        before_ids = sorted({item.finding_id for item in all_findings})
        request = _build_revision_request(
            plan=plan,
            ledger=ledger,
            architecture=architecture,
            story=story,
            section=section,
            section_draft=current_draft,
            aliases=aliases,
            value_records=value_records,
            fact_by_claim=fact_by_claim,
            findings=actionable,
        )
        attempts += 1
        try:
            envelope = author_reviser(request)
            if not isinstance(envelope, ReviewerProviderResult):
                raise TypeError(
                    "author reviser provider must return ReviewerProviderResult"
                )
            usages.append(dict(envelope.usage or {}))
            models.append(envelope.provider_model)
            merged = _merge_revision(current_draft, envelope.response, aliases)
            revised = revalidate_section_draft(
                plan=plan,
                ledger=ledger,
                architecture_id=architecture.architecture_id,
                story=story,
                section=section,
                aliases=aliases,
                value_records_by_key=value_records_by_key,
                fact_by_claim=fact_by_claim,
                raw_response=merged,
                semantic_model=envelope.provider_model,
                usage=envelope.usage,
                model_status="available",
                repair_rounds=0,
                attempts=1,
                preserved_literature_evidence_ids={
                    paragraph.paragraph_id: paragraph.literature_evidence_ids
                    for paragraph in current_draft.paragraphs
                },
            )
            revised_ids = [item.paragraph_id for item in actionable]
            if not _unaffected_paragraphs_unchanged(
                current_draft, revised, revised_ids
            ):
                warnings.append(
                    f"revision round {round_number}: unaffected paragraphs "
                    "changed; revision rejected"
                )
                break
            binding_error = _revision_preserves_bindings(
                current_draft, revised, revised_ids
            )
            if binding_error is not None:
                warnings.append(
                    f"revision round {round_number}: {binding_error}; "
                    "revision rejected, last safe draft retained"
                )
                break
            paragraph_ids_seen: set[str] = set()
            audit_findings, audit_hard = _audit_section(
                plan=plan,
                ledger=ledger,
                story=story,
                section=section,
                aliases=aliases,
                value_records_by_key=value_records_by_key,
                fact_by_claim=fact_by_claim,
                section_draft=revised,
                paragraph_ids_seen=paragraph_ids_seen,
            )
            if audit_hard or revised.status != "publishable":
                warnings.append(
                    f"revision round {round_number}: revised section failed "
                    "deterministic audit; last safe draft retained"
                )
                break
            after_content_id = _section_content_id(revised)
            (
                review_outcomes,
                new_sci,
                new_expr,
                review_usage,
                review_model,
                review_attempts,
            ) = _review_section(
                plan=plan,
                ledger=ledger,
                architecture=architecture,
                story=story,
                section=section,
                section_draft=revised,
                aliases=aliases,
                value_records=value_records,
                fact_by_claim=fact_by_claim,
                scientific_reviewer=scientific_reviewer,
                expression_reviewer=expression_reviewer,
                warnings=warnings,
            )
            attempts += review_attempts
            usages.append(review_usage)
            if review_model != "none":
                models.append(review_model)
            current_outcomes = dict(review_outcomes)
            required_roles = sorted({item.reviewer for item in actionable})
            failed_required = [
                role
                for role in required_roles
                if review_outcomes.get(role) != ReviewRoleOutcome.valid.value
            ]
            if failed_required:
                warnings.append(
                    f"revision round {round_number}: re-review did not succeed "
                    f"for required role(s) {failed_required} "
                    f"({review_outcomes}); previous findings and last "
                    "reviewed safe draft retained"
                )
                rounds.append(
                    RevisionRound(
                        round_number=round_number,
                        before_content_id=before_content_id,
                        after_content_id=before_content_id,
                        before_finding_ids=before_ids,
                        after_finding_ids=before_ids,
                        resolved_finding_ids=[],
                        retained_finding_ids=before_ids,
                        revised_paragraph_ids=sorted(set(revised_ids)),
                        progress=False,
                        warnings=[
                            "re-review did not succeed for required role(s) "
                            f"{failed_required} ({review_outcomes})"
                        ],
                    )
                )
                break
            prior_by_role = {
                "scientific": [
                    item for item in all_findings if item.reviewer == "scientific"
                ],
                "expression": [
                    item for item in all_findings if item.reviewer == "expression"
                ],
            }
            new_findings: List[ReviewerFinding] = []
            for role in ("scientific", "expression"):
                fresh = new_sci if role == "scientific" else new_expr
                if review_outcomes.get(role) == ReviewRoleOutcome.valid.value:
                    new_findings.extend(fresh)
                else:
                    new_findings.extend(prior_by_role[role])
            after_ids = sorted({item.finding_id for item in new_findings})
            resolved = sorted(set(before_ids) - set(after_ids))
            retained = sorted(set(before_ids) & set(after_ids))
            content_changed = after_content_id != before_content_id
            addressed_major_spans = _addressed_major_spans(
                current_draft,
                revised,
                actionable,
            )
            before_major_count = sum(
                item.severity == ReviewSeverity.major for item in all_findings
            )
            after_major_count = sum(
                item.severity == ReviewSeverity.major for item in new_findings
            )
            if before_major_count:
                progress = content_changed and (
                    after_major_count < before_major_count
                    or bool(addressed_major_spans)
                )
            else:
                # One bounded polish pass is useful.  Once only advisory/minor
                # issues remain, rotating reviewer wording is not evidence that
                # another full author-review round will improve the science.
                progress = content_changed and (
                    len(new_findings) < len(all_findings)
                    or _severity_weight(new_findings) < _severity_weight(all_findings)
                )
            rounds.append(
                RevisionRound(
                    round_number=round_number,
                    before_content_id=before_content_id,
                    after_content_id=after_content_id,
                    before_finding_ids=before_ids,
                    after_finding_ids=after_ids,
                    resolved_finding_ids=resolved,
                    retained_finding_ids=retained,
                    revised_paragraph_ids=sorted(set(revised_ids)),
                    progress=progress,
                    warnings=[],
                )
            )
            if not content_changed:
                warnings.append(
                    f"revision round {round_number}: repeated content identity; "
                    "stopping"
                )
                break
            if not progress:
                warnings.append(
                    f"revision round {round_number}: no material progress "
                    "(findings only swapped); stopping"
                )
                break
            if addressed_major_spans:
                warnings.append(
                    f"revision round {round_number}: accepted after removing "
                    "the exact span of major finding(s) "
                    f"{addressed_major_spans}; new advisory findings retained"
                )
            current_draft = revised
            all_findings = new_findings
            if after_major_count == 0:
                warnings.append(
                    f"revision round {round_number}: no major scientific or "
                    "expression findings remain; ordinary advice retained and "
                    "further revision stopped"
                )
                break
            if after_major_count >= before_major_count:
                warnings.append(
                    f"revision round {round_number}: revised safe draft "
                    "retained, but major-finding count did not decrease; "
                    "ordinary findings retained and further revision stopped"
                )
                break
        except Exception as exc:
            warnings.append(
                f"revision round {round_number}: reviser failed: {exc}; "
                "last safe draft retained"
            )
            break
    semantic_model = (
        models[0] if len(set(models)) == 1 else ("mixed" if models else "none")
    )
    return (
        current_draft,
        all_findings,
        rounds,
        _aggregate_usage(usages),
        semantic_model,
        attempts,
        current_outcomes,
    )


def build_article_review(
    plan: ArticleDirectorPlan | Mapping[str, Any],
    ledger: ClaimLedgerResult | Mapping[str, Any],
    architecture: ArticleArchitectureResult | Mapping[str, Any],
    bundle: ArticleDraftBundle | Mapping[str, Any],
    selected_story_id: str,
    value_records: Sequence[TrustedValueRecord | Mapping[str, Any]],
    *,
    scientific_reviewer: Optional[ReviewerProvider] = None,
    expression_reviewer: Optional[ExpressionReviewerProvider] = None,
    global_consistency_reviewer: Optional[ReviewerProvider] = None,
    global_advice_router: Optional[ReviewerProvider] = None,
    global_revision_reviewer: Optional[ReviewerProvider] = None,
    author_reviser: Optional[AuthorReviserProvider] = None,
    memory_store: ArticleMemoryStore | None = None,
    graph: ExperimentGraph | None = None,
    run_id: Optional[str] = None,
    journal_path: str | Path | None = None,
) -> ArticleReviewResult:
    errors: List[str] = []
    warnings: List[str] = []
    if (memory_store is not None or graph is not None) and not run_id:
        errors.append("run_id is required when memory_store or graph is provided")
    try:
        plan_model = (
            plan
            if isinstance(plan, ArticleDirectorPlan)
            else ArticleDirectorPlan.model_validate(plan)
        )
    except ValidationError as exc:
        errors.append(f"plan is invalid: {exc}")
        return _hard_blocker(errors, warnings)
    try:
        ledger_model = (
            ledger
            if isinstance(ledger, ClaimLedgerResult)
            else ClaimLedgerResult.model_validate(ledger)
        )
    except ValidationError as exc:
        errors.append(f"ledger is invalid: {exc}")
        return _hard_blocker(errors, warnings)
    try:
        architecture_model = (
            architecture
            if isinstance(architecture, ArticleArchitectureResult)
            else ArticleArchitectureResult.model_validate(architecture)
        )
    except ValidationError as exc:
        errors.append(f"architecture is invalid: {exc}")
        return _hard_blocker(errors, warnings)
    try:
        bundle_model = (
            bundle
            if isinstance(bundle, ArticleDraftBundle)
            else ArticleDraftBundle.model_validate(bundle)
        )
    except ValidationError as exc:
        errors.append(f"bundle is invalid: {exc}")
        return _hard_blocker(errors, warnings)
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
        return _hard_blocker(errors, warnings)

    story, fact_by_claim, aliases = _validate_bundle_inputs(
        plan_model,
        ledger_model,
        architecture_model,
        bundle_model,
        selected_story_id,
        records,
        errors,
        warnings,
    )
    if errors:
        return _hard_blocker(errors, warnings)
    assert story is not None
    value_records_by_key = {
        (record.artifact_id, record.field): record for record in records
    }

    reviewed_sections: List[ReviewedSection] = []
    paragraph_ids_seen: set[str] = set()
    all_audit_findings: List[DeterministicAuditFinding] = []
    all_scientific: List[ReviewerFinding] = []
    all_expression: List[ReviewerFinding] = []
    all_usage: List[Dict[str, Any]] = []
    all_models: List[str] = []
    total_attempts = 0

    for section_draft in bundle_model.sections:
        section = _section_from_story(story, section_draft.section_id)
        if section is None:
            errors.append(
                f"bundle section {section_draft.section_id!r} has no matching "
                "story section"
            )
            continue
        section_warnings: List[str] = []
        audit_findings, audit_hard = _audit_section(
            plan=plan_model,
            ledger=ledger_model,
            story=story,
            section=section,
            aliases=aliases,
            value_records_by_key=value_records_by_key,
            fact_by_claim=fact_by_claim,
            section_draft=section_draft,
            paragraph_ids_seen=paragraph_ids_seen,
        )
        if section_draft.status == "publishable":
            rebuilt, recon_errors = _reconstruct_section_draft(
                plan=plan_model,
                ledger=ledger_model,
                architecture=architecture_model,
                story=story,
                section=section,
                aliases=aliases,
                value_records_by_key=value_records_by_key,
                fact_by_claim=fact_by_claim,
                section_draft=section_draft,
            )
            if recon_errors:
                for message in recon_errors:
                    finding_id = "audit-" + _digest(
                        section_draft.section_id,
                        "",
                        "reconstruction",
                        message,
                    )
                    audit_findings.append(
                        DeterministicAuditFinding(
                            finding_id=finding_id,
                            section_id=section_draft.section_id,
                            kind="reconstruction",
                            message=message,
                        )
                    )
                    audit_hard.append(message)
            else:
                assert rebuilt is not None
                for message in _compare_reconstructed(section_draft, rebuilt):
                    finding_id = "audit-" + _digest(
                        section_draft.section_id,
                        "",
                        "reconstruction",
                        message,
                    )
                    audit_findings.append(
                        DeterministicAuditFinding(
                            finding_id=finding_id,
                            section_id=section_draft.section_id,
                            kind="reconstruction",
                            message=message,
                        )
                    )
                    audit_hard.append(message)
        all_audit_findings.extend(audit_findings)
        if audit_hard:
            reviewed_sections.append(
                ReviewedSection(
                    section_id=section_draft.section_id,
                    story_id=story.story_id,
                    status=SectionReviewStatus.blocked,
                    section_draft=section_draft,
                    original_section_draft=section_draft,
                    audit_findings=audit_findings,
                    hard_blockers=list(audit_hard),
                    findings=[],
                    revisions=[],
                    warnings=list(section_warnings),
                    usage={},
                    semantic_model="none",
                    attempts=0,
                )
            )
            continue
        (
            outcomes,
            scientific,
            expression,
            review_usage,
            review_model,
            review_attempts,
        ) = _review_section(
            plan=plan_model,
            ledger=ledger_model,
            architecture=architecture_model,
            story=story,
            section=section,
            section_draft=section_draft,
            aliases=aliases,
            value_records=records,
            fact_by_claim=fact_by_claim,
            scientific_reviewer=scientific_reviewer,
            expression_reviewer=expression_reviewer,
            warnings=section_warnings,
        )
        total_attempts += review_attempts
        all_usage.append(review_usage)
        if review_model != "none":
            all_models.append(review_model)
        (
            final_draft,
            final_findings,
            revisions,
            revision_usage,
            revision_model,
            revision_attempts,
            reviewer_status,
        ) = _run_revisions(
            plan=plan_model,
            ledger=ledger_model,
            architecture=architecture_model,
            story=story,
            section=section,
            aliases=aliases,
            value_records=records,
            fact_by_claim=fact_by_claim,
            value_records_by_key=value_records_by_key,
            current_draft=section_draft,
            scientific_findings=scientific,
            expression_findings=expression,
            initial_outcomes=outcomes,
            scientific_reviewer=scientific_reviewer,
            expression_reviewer=expression_reviewer,
            author_reviser=author_reviser,
            warnings=section_warnings,
        )
        total_attempts += revision_attempts
        all_usage.append(revision_usage)
        if revision_model != "none":
            all_models.append(revision_model)
        status = (
            SectionReviewStatus.ready_with_findings
            if final_findings
            else SectionReviewStatus.ready
        )
        reviewed_sections.append(
            ReviewedSection(
                section_id=section_draft.section_id,
                story_id=story.story_id,
                status=status,
                section_draft=final_draft,
                original_section_draft=section_draft,
                audit_findings=audit_findings,
                hard_blockers=[],
                findings=final_findings,
                revisions=revisions,
                warnings=list(section_warnings),
                reviewer_status=reviewer_status,
                usage=_aggregate_usage([review_usage, revision_usage]),
                semantic_model=(
                    revision_model if revision_model != "none" else review_model
                ),
                attempts=review_attempts + revision_attempts,
            )
        )
        all_scientific.extend(
            item for item in final_findings if item.reviewer == "scientific"
        )
        all_expression.extend(
            item for item in final_findings if item.reviewer == "expression"
        )
        warnings.extend(section_warnings)

    if (
        not errors
        and reviewed_sections
        and all(not section.hard_blockers for section in reviewed_sections)
        and global_consistency_reviewer is not None
    ):
        post_section_bundle = bundle_model.model_copy(
            update={
                "sections": [section.section_draft for section in reviewed_sections]
            }
        )
        (
            global_findings,
            global_usage,
            global_model,
            global_attempts,
            global_outcome,
        ) = _run_global_consistency_review(
            plan=plan_model,
            ledger=ledger_model,
            story=story,
            bundle=post_section_bundle,
            aliases=aliases,
            fact_by_claim=fact_by_claim,
            provider=global_consistency_reviewer,
            advice_router=global_advice_router,
            warnings=warnings,
        )
        if global_usage:
            all_usage.append(global_usage)
        if global_model != "none":
            all_models.append(global_model)
        total_attempts += global_attempts
        global_by_paragraph = {
            finding.paragraph_id: finding for finding in global_findings
        }
        for section_index, reviewed_section in enumerate(reviewed_sections):
            section = _section_from_story(story, reviewed_section.section_id)
            if section is None:
                continue
            section_global = [
                global_by_paragraph[paragraph.paragraph_id]
                for paragraph in reviewed_section.section_draft.paragraphs
                if paragraph.paragraph_id in global_by_paragraph
            ]
            reviewer_status = {
                **reviewed_section.reviewer_status,
                "global_consistency": global_outcome,
            }
            if not section_global:
                reviewed_sections[section_index] = reviewed_section.model_copy(
                    update={"reviewer_status": reviewer_status}
                )
                continue
            global_warnings: List[str] = []
            (
                final_draft,
                unresolved_global,
                global_revisions,
                global_revision_usage,
                global_revision_model,
                global_revision_attempts,
                _,
            ) = _run_revisions(
                plan=plan_model,
                ledger=ledger_model,
                architecture=architecture_model,
                story=story,
                section=section,
                aliases=aliases,
                value_records=records,
                fact_by_claim=fact_by_claim,
                value_records_by_key=value_records_by_key,
                current_draft=reviewed_section.section_draft,
                scientific_findings=section_global,
                expression_findings=[],
                initial_outcomes={"global_consistency": global_outcome},
                scientific_reviewer=(global_revision_reviewer or scientific_reviewer),
                expression_reviewer=None,
                author_reviser=author_reviser,
                warnings=global_warnings,
            )
            total_attempts += global_revision_attempts
            if global_revision_usage:
                all_usage.append(global_revision_usage)
            if global_revision_model != "none":
                all_models.append(global_revision_model)
            combined_findings = list(
                {
                    finding.finding_id: finding
                    for finding in [
                        *reviewed_section.findings,
                        *unresolved_global,
                    ]
                }.values()
            )
            combined_usage = _aggregate_usage(
                [reviewed_section.usage, global_revision_usage]
            )
            semantic_models = {
                model
                for model in (
                    reviewed_section.semantic_model,
                    global_revision_model,
                )
                if model and model != "none"
            }
            combined_model = (
                next(iter(semantic_models))
                if len(semantic_models) == 1
                else ("mixed" if semantic_models else "none")
            )
            reviewed_sections[section_index] = reviewed_section.model_copy(
                update={
                    "status": (
                        SectionReviewStatus.ready_with_findings
                        if combined_findings
                        else SectionReviewStatus.ready
                    ),
                    "section_draft": final_draft,
                    "findings": combined_findings,
                    "revisions": [
                        *reviewed_section.revisions,
                        *global_revisions,
                    ],
                    "warnings": [
                        *reviewed_section.warnings,
                        *global_warnings,
                    ],
                    "reviewer_status": reviewer_status,
                    "usage": combined_usage,
                    "semantic_model": combined_model,
                    "attempts": (reviewed_section.attempts + global_revision_attempts),
                }
            )
            warnings.extend(global_warnings)

    if errors:
        return _hard_blocker(errors, warnings)
    all_scientific = [
        finding
        for section in reviewed_sections
        for finding in section.findings
        if finding.reviewer == "scientific"
    ]
    all_expression = [
        finding
        for section in reviewed_sections
        for finding in section.findings
        if finding.reviewer == "expression"
    ]
    original_source_ledger = list(bundle_model.source_ledger)
    final_source_ledger = [
        entry
        for section in reviewed_sections
        for entry in section.section_draft.source_ledger
    ]
    status = _compute_review_status(reviewed_sections)
    final_outcomes = [
        outcome
        for section in reviewed_sections
        for outcome in section.reviewer_status.values()
    ]
    valid_outcomes = sum(
        1 for outcome in final_outcomes if outcome == ReviewRoleOutcome.valid.value
    )
    if not final_outcomes:
        model_status: Literal["available", "partial", "unavailable"] = "unavailable"
    elif valid_outcomes == len(final_outcomes):
        model_status = "available"
    elif valid_outcomes:
        model_status = "partial"
    else:
        model_status = "unavailable"
    usage = _aggregate_usage(all_usage)
    semantic_model = (
        all_models[0]
        if len(set(all_models)) == 1
        else ("mixed" if all_models else "none")
    )
    retained_advice = sorted(
        {
            message[len(prefix) :]
            for message in warnings
            for prefix in ("scientific advice: ", "expression advice: ")
            if message.startswith(prefix)
        }
    )
    review_id = compute_review_id(
        plan_model.plan_id,
        ledger_model.ledger_id,
        architecture_model.architecture_id,
        bundle_model.bundle_id,
        story.story_id,
    )
    result_id = compute_review_result_id(
        review_id,
        reviewed_sections,
        all_audit_findings,
        all_scientific,
        all_expression,
    )
    hard_blockers = [
        message for section in reviewed_sections for message in section.hard_blockers
    ]
    result = ArticleReviewResult(
        review_id=review_id,
        result_id=result_id,
        plan_id=plan_model.plan_id,
        ledger_id=ledger_model.ledger_id,
        architecture_id=architecture_model.architecture_id,
        bundle_id=bundle_model.bundle_id,
        story_id=story.story_id,
        status=status,
        sections=reviewed_sections,
        audit_findings=all_audit_findings,
        hard_blockers=hard_blockers,
        scientific_findings=all_scientific,
        expression_findings=all_expression,
        original_source_ledger=original_source_ledger,
        final_source_ledger=final_source_ledger,
        retained_advice=sorted(set(retained_advice)),
        warnings=warnings,
        model_status=model_status,
        semantic_model=semantic_model,
        usage=usage,
        attempts=total_attempts,
    )
    if memory_store is not None or graph is not None or journal_path is not None:
        _persist(
            result_id=result_id,
            result=result,
            memory_store=memory_store,
            graph=graph,
            run_id=str(run_id or ""),
            journal_path=journal_path,
        )
    return result


ExpressionReviewerProvider = ReviewerProvider


def _hard_blocker(
    errors: Sequence[str], warnings: Sequence[str]
) -> ArticleReviewResult:
    return ArticleReviewResult(
        review_id=f"review-{_digest('invalid')}",
        result_id=f"result-{_digest('invalid')}",
        plan_id="",
        ledger_id="",
        architecture_id="",
        bundle_id="",
        story_id="",
        status="blocked",
        sections=[],
        audit_findings=[],
        hard_blockers=[str(item) for item in errors],
        scientific_findings=[],
        expression_findings=[],
        original_source_ledger=[],
        final_source_ledger=[],
        retained_advice=[],
        warnings=[str(item) for item in warnings],
        model_status="unavailable",
        semantic_model="none",
        usage={},
        attempts=0,
    )


class _QwenReviewBase:
    """Shared concrete qwen3.7-flash reviewer/reviser adapter behavior."""

    def __init__(
        self,
        *,
        prompt_path: str | Path,
        client: QwenFlashOnlyClient | None = None,
        max_tokens: int,
        agent_name: str,
    ) -> None:
        self.prompt_path = Path(prompt_path)
        self.max_tokens = int(max_tokens)
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be at least 1")
        self.client = client or QwenFlashOnlyClient(agent_name=agent_name)

    def _call(self, request: Mapping[str, Any]) -> ReviewerProviderResult:
        messages = [
            {
                "role": "system",
                "content": self.prompt_path.read_text(encoding="utf-8"),
            },
            {
                "role": "user",
                "content": json.dumps(dict(request), ensure_ascii=False),
            },
        ]
        response = self.client.call(
            messages, max_tokens=self.max_tokens, force_mock=False
        )
        parsed = _safe_json(str(response.get("content") or ""))
        usage = _usage_with_cost(response.get("_llm_usage") or {})
        return ReviewerProviderResult(
            response=parsed,
            usage=usage,
            provider_model=REVIEWER_MODEL_NAME,
            mock_llm=bool(usage.get("mock_llm")),
        )


class QwenScientificReviewer(_QwenReviewBase):
    def __init__(
        self,
        *,
        prompt_path: str | Path = SCIENTIFIC_PROMPT_PATH,
        client: QwenFlashOnlyClient | None = None,
        max_tokens: int = DEFAULT_SCIENTIFIC_MAX_TOKENS,
    ) -> None:
        super().__init__(
            prompt_path=prompt_path,
            client=client,
            max_tokens=max_tokens,
            agent_name="ArticleScientificReviewer",
        )

    def __call__(self, request: Mapping[str, Any]) -> ReviewerProviderResult:
        return self._call(request)


class QwenGlobalConsistencyReviewer(_QwenReviewBase):
    def __init__(
        self,
        *,
        prompt_path: str | Path = GLOBAL_CONSISTENCY_PROMPT_PATH,
        client: QwenFlashOnlyClient | None = None,
        max_tokens: int = DEFAULT_GLOBAL_CONSISTENCY_MAX_TOKENS,
    ) -> None:
        super().__init__(
            prompt_path=prompt_path,
            client=client,
            max_tokens=max_tokens,
            agent_name="ArticleGlobalConsistencyReviewer",
        )

    def __call__(self, request: Mapping[str, Any]) -> ReviewerProviderResult:
        return self._call(request)


class QwenGlobalAdviceRouter(_QwenReviewBase):
    def __init__(
        self,
        *,
        prompt_path: str | Path = GLOBAL_ADVICE_ROUTER_PROMPT_PATH,
        client: QwenFlashOnlyClient | None = None,
        max_tokens: int = DEFAULT_GLOBAL_ADVICE_ROUTER_MAX_TOKENS,
    ) -> None:
        super().__init__(
            prompt_path=prompt_path,
            client=client,
            max_tokens=max_tokens,
            agent_name="ArticleGlobalAdviceRouter",
        )

    def __call__(self, request: Mapping[str, Any]) -> ReviewerProviderResult:
        return self._call(request)


class QwenExpressionReviewer(_QwenReviewBase):
    def __init__(
        self,
        *,
        prompt_path: str | Path = EXPRESSION_PROMPT_PATH,
        client: QwenFlashOnlyClient | None = None,
        max_tokens: int = DEFAULT_EXPRESSION_MAX_TOKENS,
    ) -> None:
        super().__init__(
            prompt_path=prompt_path,
            client=client,
            max_tokens=max_tokens,
            agent_name="ArticleExpressionReviewer",
        )

    def __call__(self, request: Mapping[str, Any]) -> ReviewerProviderResult:
        return self._call(request)


class QwenAuthorReviser(_QwenReviewBase):
    def __init__(
        self,
        *,
        prompt_path: str | Path = REVISION_PROMPT_PATH,
        client: QwenFlashOnlyClient | None = None,
        max_tokens: int = DEFAULT_REVISION_MAX_TOKENS,
    ) -> None:
        super().__init__(
            prompt_path=prompt_path,
            client=client,
            max_tokens=max_tokens,
            agent_name="ArticleAuthorReviser",
        )

    def __call__(self, request: Mapping[str, Any]) -> ReviewerProviderResult:
        return self._call(request)


def _read_journal(path: str | Path) -> Dict[str, Any]:
    journal_path = Path(path)
    if not journal_path.exists():
        return {}
    try:
        payload = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArticleReviewError(f"review journal is unreadable: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ArticleReviewError("review journal must be a JSON object")
    return {
        str(key): dict(value)
        for key, value in payload.items()
        if isinstance(value, Mapping)
    }


def _write_journal(
    path: str | Path,
    journal: Mapping[str, Any],
    review_id: str,
    state: Mapping[str, Any],
) -> None:
    payload = dict(journal)
    payload[str(review_id)] = dict(state)
    atomic_write_json(Path(path), payload)


def _expected_review_events(
    result: ArticleReviewResult,
) -> List[Tuple[str, Mapping[str, Any]]]:
    events: List[Tuple[str, Mapping[str, Any]]] = []
    for finding in result.scientific_findings + result.expression_findings:
        events.append(
            (
                "article.review",
                validate_article_event(
                    "article.review",
                    {
                        "schema_version": ARTICLE_EVENT_SCHEMA_VERSION,
                        "review_id": finding.finding_id,
                        "severity": finding.severity.value,
                        "decision": None,
                    },
                ),
            )
        )
    for finding in result.audit_findings:
        events.append(
            (
                "article.review",
                validate_article_event(
                    "article.review",
                    {
                        "schema_version": ARTICLE_EVENT_SCHEMA_VERSION,
                        "review_id": finding.finding_id,
                        "severity": "blocking",
                        "decision": None,
                    },
                ),
            )
        )
    return events


def _persist(
    *,
    result_id: str,
    result: ArticleReviewResult,
    memory_store: Optional[ArticleMemoryStore],
    graph: Optional[ExperimentGraph],
    run_id: str,
    journal_path: Optional[str | Path],
) -> None:
    if journal_path is None:
        if graph is not None:
            _persist_graph(graph, result_id, result)
        if memory_store is not None:
            _persist_memory(memory_store, result_id, result, run_id)
        return
    journal = _read_journal(journal_path)
    state = journal.get(result_id)
    if state is not None and state.get("status") == "completed":
        return
    if state is None:
        state = {
            "status": "in_progress",
            "graph_written": graph is None,
            "memory_written": memory_store is None,
        }
    try:
        if graph is not None and not state.get("graph_written"):
            _persist_graph(graph, result_id, result)
            state["graph_written"] = True
            _write_journal(journal_path, journal, result_id, state)
        if memory_store is not None and not state.get("memory_written"):
            _persist_memory(memory_store, result_id, result, run_id)
            state["memory_written"] = True
            _write_journal(journal_path, journal, result_id, state)
        state["status"] = "completed"
        _write_journal(journal_path, journal, result_id, state)
    except Exception as exc:
        _write_journal(journal_path, journal, result_id, state)
        raise ArticleReviewError(f"review persistence failed: {exc}") from exc


def _persist_graph(
    graph: ExperimentGraph,
    result_id: str,
    result: ArticleReviewResult,
) -> None:
    node_id = f"review-{result_id}"
    summary = f"review-{result_id}"
    payload = ArticleNodePayload(
        stage=ArticleStage.scientific_review,
        hypothesis_ids=[],
        card_refs={
            "story_ids": [result.story_id],
            "section_ids": [section.section_id for section in result.sections],
        },
        summary=summary,
    )
    expected_events = _expected_review_events(result)
    created = False
    try:
        graph.create_article_node(payload, node_id=node_id)
        created = True
    except sqlite3.IntegrityError:
        existing = graph.article_node(node_id)
        if existing.get("payload", {}).get("summary") != summary:
            raise ArticleReviewIntegrityError(
                f"review node {node_id!r} already exists with different content"
            )
    if created:
        for event_type, event_payload in expected_events:
            graph.record_article_event(node_id, event_type, event_payload)
        return
    existing = graph.article_node(node_id)
    seen = {
        (item["event_type"], _canonical_json(item["payload"]))
        for item in existing["history"]
    }
    by_identity: Dict[str, Tuple[str, str]] = {}
    for item in existing["history"]:
        if item["event_type"] == "article.review":
            identity = f"review:{item['payload'].get('review_id')}:{item['payload'].get('severity')}"
            by_identity[identity] = (
                item["event_type"],
                _canonical_json(item["payload"]),
            )
    for event_type, event_payload in expected_events:
        canonical = _canonical_json(event_payload)
        identity = (
            f"review:{event_payload.get('review_id')}:"
            f"{event_payload.get('severity')}"
        )
        if identity in by_identity and by_identity[identity] != (event_type, canonical):
            raise ArticleReviewIntegrityError(
                f"review node {node_id!r} has conflicting review event "
                f"for {identity}"
            )
        if (event_type, canonical) in seen:
            continue
        graph.record_article_event(node_id, event_type, event_payload)
        seen.add((event_type, canonical))
        by_identity[identity] = (event_type, canonical)


def _persist_memory(
    memory_store: ArticleMemoryStore,
    result_id: str,
    result: ArticleReviewResult,
    run_id: str,
) -> None:
    bundle_artifacts = sorted(
        {
            artifact
            for section in result.sections
            for entry in section.section_draft.source_ledger
            for artifact in entry.artifact_ids
        }
    )
    records: List[RunMemoryRecord] = [
        RunMemoryRecord(
            memory_id=f"review-{result_id}",
            run_id=run_id,
            event_type="article_review",
            graph_node_id=f"review-{result_id}",
            artifact_ids=bundle_artifacts,
            operational_note=_canonical_json(result.model_dump(mode="json")),
        ),
        RunMemoryRecord(
            memory_id=f"review-ledger-{result_id}",
            run_id=run_id,
            event_type="article_review_ledger",
            graph_node_id=f"review-{result_id}",
            artifact_ids=bundle_artifacts,
            operational_note=_canonical_json(
                [item.model_dump(mode="json") for item in result.final_source_ledger]
            ),
        ),
    ]
    for section in result.sections:
        section_artifacts = sorted(
            {
                artifact
                for entry in section.section_draft.source_ledger
                for artifact in entry.artifact_ids
            }
        )
        records.append(
            RunMemoryRecord(
                memory_id=f"review-section-{result_id}-{section.section_id}",
                run_id=run_id,
                event_type="article_review_section",
                graph_node_id=f"review-{result_id}",
                artifact_ids=section_artifacts,
                operational_note=_canonical_json(section.model_dump(mode="json")),
            )
        )
    for record in records:
        try:
            memory_store.add_run_memory(record)
        except DuplicateRecordError:
            existing = memory_store.get_run_memory(record.memory_id)
            if existing.model_dump(mode="json") != record.model_dump(mode="json"):
                raise ArticleReviewIntegrityError(
                    f"memory record {record.memory_id!r} already exists with "
                    "different content"
                ) from None


__all__ = [
    "ArticleReviewError",
    "ArticleReviewIntegrityError",
    "ArticleReviewResult",
    "AuthorReviserProvider",
    "DEFAULT_EXPRESSION_MAX_TOKENS",
    "DEFAULT_GLOBAL_ADVICE_ROUTER_MAX_TOKENS",
    "DEFAULT_GLOBAL_CONSISTENCY_MAX_TOKENS",
    "DEFAULT_REVISION_MAX_TOKENS",
    "DEFAULT_SCIENTIFIC_MAX_TOKENS",
    "DeterministicAuditFinding",
    "ExpressionReviewerProvider",
    "MAX_REVISION_ROUNDS",
    "QwenAuthorReviser",
    "QwenExpressionReviewer",
    "QwenGlobalAdviceRouter",
    "QwenGlobalConsistencyReviewer",
    "QwenScientificReviewer",
    "REVIEWER_MODEL_NAME",
    "ReviewSeverity",
    "ReviewerFinding",
    "ReviewerProvider",
    "ReviewerProviderResult",
    "ReviewedSection",
    "RevisionRound",
    "SectionReviewStatus",
    "build_article_review",
    "compute_review_id",
    "compute_review_result_id",
    "validate_review_result",
]
