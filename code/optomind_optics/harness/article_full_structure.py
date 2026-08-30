"""Stage 13A: whole-Article structure coordination over immutable sections.

Qwen proposes organization, rhetorical deduplication, and gap value only.
The local program assembles the final ordered body from persisted manuscript
paragraphs and preserves every source binding byte-for-byte.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from optomind_optics.harness.article_architecture import ArticleArchitectureResult
from optomind_optics.harness.article_claims import ClaimLedgerResult
from optomind_optics.harness.article_director import ArticleDirectorPlan
from optomind_optics.harness.article_global_quality_audit import (
    GlobalQualityAuditReport,
)
from optomind_optics.harness.article_manuscript import ArticleManuscriptPackage
from optomind_optics.harness.article_review import ArticleReviewResult
from optomind_optics.harness.qwen_policy import QwenFlashOnlyClient


FULL_STRUCTURE_SCHEMA_VERSION = "article-full-structure-result.v1"
MODEL_NAME = "qwen3.7-flash"
DEFAULT_MAX_TOKENS = 12000
DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "optical_harness"
    / "Article Full Structure Coordinator.txt"
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _ProviderModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class FullSectionOrder(_StrictModel):
    schema_version: Literal["full-section-order.v1"] = "full-section-order.v1"
    source_section_id: str
    order: int = Field(ge=1)
    whole_article_role: str
    reason: str = ""
    transition_note: str = ""


class RhetoricalEdit(_StrictModel):
    schema_version: Literal["rhetorical-edit.v1"] = "rhetorical-edit.v1"
    source_section_ids: List[str] = Field(min_length=1)
    operation: Literal["reorder", "bridge", "deduplicate", "label_scope"]
    instruction: str
    preserve_claim_bindings: bool = True


class StructureGap(_StrictModel):
    schema_version: Literal["structure-gap.v1"] = "structure-gap.v1"
    gap_id: str
    description: str
    unique_contribution: str
    expected_value: str
    stop_reason: str
    recommended_next_action: str = ""
    related_section_ids: List[str] = Field(default_factory=list)


class GlobalQualityAction(_StrictModel):
    """The coordinator's disposition of one deterministic audit finding."""

    schema_version: Literal["global-quality-action.v1"] = "global-quality-action.v1"
    finding_id: str
    handling: Literal[
        "addressed", "planned", "deferred", "not_applicable", "unacknowledged"
    ]
    rationale: str = ""
    scope_label: str = ""


class ChapterArgumentGap(_StrictModel):
    schema_version: Literal["chapter-argument-gap.v1"] = "chapter-argument-gap.v1"
    gap_id: str
    section_id: str
    description: str
    unique_contribution: str
    expected_value: str
    stop_reason: str
    recommended_next_action: str = ""
    related_claim_ids: List[str] = Field(default_factory=list)


class FullStructureResult(_StrictModel):
    schema_version: Literal["article-full-structure-result.v1"] = (
        FULL_STRUCTURE_SCHEMA_VERSION
    )
    result_id: str
    source_plan_id: str
    source_architecture_id: str
    source_review_id: str
    source_manuscript_package_id: str
    story_id: str
    global_thesis: str
    section_order: List[FullSectionOrder]
    rhetorical_edits: List[RhetoricalEdit] = Field(default_factory=list)
    chapter_argument_gaps: List[ChapterArgumentGap] = Field(default_factory=list)
    structure_gaps: List[StructureGap] = Field(default_factory=list)
    body_markdown: str
    source_map: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    validation_errors: List[str] = Field(default_factory=list)
    model_status: Literal["available", "partial", "unavailable"]
    usage: Dict[str, Any] = Field(default_factory=dict)
    semantic_model: str = "none"
    source_global_quality_audit_id: Optional[str] = None
    global_quality_audit_status: Literal[
        "not_provided",
        "not_reviewed",
        "acknowledged",
        "partially_acknowledged",
        "unacknowledged",
    ] = "not_provided"
    global_quality_actions: List[GlobalQualityAction] = Field(default_factory=list)
    unhandled_global_quality_finding_ids: List[str] = Field(default_factory=list)
    out_of_scope_global_quality_finding_ids: List[str] = Field(default_factory=list)


class FullStructureProviderResult(_StrictModel):
    schema_version: Literal["full-structure-provider-result.v1"] = (
        "full-structure-provider-result.v1"
    )
    response: Dict[str, Any]
    usage: Dict[str, Any] = Field(default_factory=dict)
    provider_model: str = "unknown"
    mock_llm: bool = False


class _SectionDraft(_ProviderModel):
    source_section_id: str
    order: int = 0
    whole_article_role: str = ""
    reason: str = ""
    transition_note: str = ""


class _EditDraft(_ProviderModel):
    source_section_ids: List[str] = Field(default_factory=list)
    operation: str = "bridge"
    instruction: str = ""
    preserve_claim_bindings: bool = True


class _ChapterGapDraft(_ProviderModel):
    section_id: str = ""
    description: str = ""
    unique_contribution: str = ""
    expected_value: str = ""
    stop_reason: str = ""
    recommended_next_action: str = ""
    related_claim_ids: List[str] = Field(default_factory=list)


class _StructureGapDraft(_ProviderModel):
    description: str = ""
    unique_contribution: str = ""
    expected_value: str = ""
    stop_reason: str = ""
    recommended_next_action: str = ""
    related_section_ids: List[str] = Field(default_factory=list)


class _GlobalQualityActionDraft(_ProviderModel):
    finding_id: str = ""
    handling: str = "unacknowledged"
    rationale: str = ""
    scope_label: str = ""


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


def _unique_audit_findings(
    audit: GlobalQualityAuditReport | None,
) -> List[Any]:
    """Collapse repeated occurrence reports before the coordinator sees them.

    A finding ID is the stable identity. Older persisted audits could contain
    the same ID more than once when a candidate was mentioned repeatedly in a
    paragraph; sending duplicates would make a one-row disposition contract
    impossible to satisfy.
    """

    if audit is None:
        return []
    unique: List[Any] = []
    seen: set[str] = set()
    for finding in audit.findings:
        if finding.finding_id in seen:
            continue
        seen.add(finding.finding_id)
        unique.append(finding)
    return unique


def _audit_findings_for_manuscript(
    audit: GlobalQualityAuditReport | None,
    manuscript: ArticleManuscriptPackage,
) -> Tuple[List[Any], List[str]]:
    """Return only findings whose paragraph belongs to this manuscript.

    Article-level findings have an empty paragraph ID and remain in scope.
    This prevents an older chapter audit from silently steering a different
    story while preserving the excluded IDs for an explicit handoff warning.
    """

    findings = _unique_audit_findings(audit)
    known_paragraphs = {
        paragraph.paragraph_id
        for section in manuscript.body.sections
        for paragraph in section.paragraphs
    }
    in_scope: List[Any] = []
    excluded: List[str] = []
    for finding in findings:
        if not finding.paragraph_id or finding.paragraph_id in known_paragraphs:
            in_scope.append(finding)
        else:
            excluded.append(finding.finding_id)
    return in_scope, excluded


def _digest(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def build_full_structure_payload(
    plan: ArticleDirectorPlan,
    ledger: ClaimLedgerResult,
    architecture: ArticleArchitectureResult,
    review: ArticleReviewResult,
    manuscript: ArticleManuscriptPackage,
    story_id: str,
    global_quality_audit: GlobalQualityAuditReport | Mapping[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    story = next(
        (item for item in architecture.stories if item.story_id == story_id), None
    )
    if story is None:
        raise ValueError(f"unknown story_id {story_id!r}")
    claim_by_id = {claim.claim_id: claim for claim in ledger.claims}
    sections = []
    for section in manuscript.body.sections:
        contract = next(
            item
            for item in story.section_contracts
            if item.section_id == section.section_id
        )
        paragraphs = [
            {
                "paragraph_id": paragraph.paragraph_id,
                "text": paragraph.rendered_text,
                "claim_ids": list(paragraph.claim_ids),
                "figure_ids": list(paragraph.figure_ids),
                "roles": list(paragraph.roles),
                "inference_kind": paragraph.inference_kind,
            }
            for paragraph in section.paragraphs
        ]
        sections.append(
            {
                "section_id": section.section_id,
                "heading": section.heading,
                "status": section.status,
                "contract_purpose": contract.purpose,
                "contract_claim_ids": [
                    item.claim_id for item in contract.claim_bindings
                ],
                "key_messages": list(contract.key_messages),
                "paragraphs": paragraphs,
                "review_finding_ids": list(section.finding_ids),
            }
        )
    claims = []
    for assignment in story.claim_assignments:
        claim = claim_by_id.get(assignment.claim_id)
        if claim is None:
            continue
        claims.append(
            {
                "claim_id": claim.claim_id,
                "statement": claim.statement,
                "scope": claim.scope,
                "strength": claim.strength.value,
                "status": claim.status.value,
                "section_ids": list(assignment.section_ids),
                "role": assignment.role,
                "synthesis_contract": dict(
                    claim.metadata.get("synthesis_contract") or {}
                ),
            }
        )
    findings = [
        {
            "finding_id": finding.finding_id,
            "section_id": (
                str(finding.paragraph_id).rsplit("-p", 1)[0]
                if "-p" in str(finding.paragraph_id)
                else ""
            ),
            "paragraph_id": finding.paragraph_id,
            "reviewer": finding.reviewer,
            "severity": finding.severity.value,
            "reason": finding.reason,
            "suggested_action": finding.suggested_action,
        }
        for finding in review.scientific_findings + review.expression_findings
    ]
    audit_model = (
        global_quality_audit
        if isinstance(global_quality_audit, GlobalQualityAuditReport)
        else GlobalQualityAuditReport.model_validate(global_quality_audit)
        if global_quality_audit is not None
        else None
    )
    scoped_audit_findings, excluded_audit_ids = _audit_findings_for_manuscript(
        audit_model, manuscript
    )
    audit_context: Dict[str, Any] = {
        "provided": audit_model is not None,
        "audit_id": audit_model.audit_id if audit_model else "",
        "status": audit_model.status if audit_model else "",
        "findings": [
            {
                "finding_id": item.finding_id,
                "kind": item.kind,
                "severity": item.severity,
                "paragraph_id": item.paragraph_id,
                "message": item.message,
                "suggested_action": item.suggested_action,
                "source_claim_ids": list(item.source_claim_ids),
                "source_artifact_ids": list(item.source_artifact_ids),
            }
            for item in scoped_audit_findings
        ],
        "excluded_out_of_scope_finding_ids": excluded_audit_ids,
    }
    return [
        {
            "task": (
                "Coordinate a complete Article from immutable section assets. "
                "Organization-only; return a planning table, not manuscript prose."
            ),
            "question": plan.charter.question,
            "charter_scope": plan.charter.scope,
            "story_id": story.story_id,
            "story_shape": story.story_shape,
            "central_thesis": story.central_thesis,
            "sections": sections,
            "claims": claims,
            "review_findings": findings,
            "global_quality_audit": audit_context,
            "constraints": {
                "section_count": len(sections),
                "new_structure_chapter_limit": 3,
                "new_section_limit_per_existing_chapter": 3,
                "do_not_change_claims_or_sources": True,
                "rhetorical_deduplication_only": True,
                "global_quality_audit_is_constraint_only": True,
                "major_findings_must_be_acknowledged": True,
            },
        }
    ]


class QwenFullStructureCoordinator:
    """Concrete qwen3.7-flash whole-Article organization adapter."""

    def __init__(
        self,
        *,
        prompt_path: str | Path = DEFAULT_PROMPT_PATH,
        client: QwenFlashOnlyClient | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self.prompt_path = Path(prompt_path)
        self.client = client or QwenFlashOnlyClient(
            agent_name="ArticleFullStructureCoordinator"
        )
        self.max_tokens = int(max_tokens)
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be positive")

    def __call__(
        self, requests: Sequence[Mapping[str, Any]]
    ) -> List[FullStructureProviderResult]:
        results = []
        for request in requests:
            response = self.client.call(
                [
                    {
                        "role": "system",
                        "content": self.prompt_path.read_text(encoding="utf-8"),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(request, ensure_ascii=False),
                    },
                ],
                max_tokens=self.max_tokens,
                force_mock=False,
            )
            results.append(
                FullStructureProviderResult(
                    response=_safe_json(str(response.get("content") or "")),
                    usage=dict(response.get("_llm_usage") or {}),
                    provider_model=MODEL_NAME,
                    mock_llm=bool((response.get("_llm_usage") or {}).get("mock_llm")),
                )
            )
        return results


def _assemble_body(
    manuscript: ArticleManuscriptPackage,
    ordered_ids: Sequence[str],
) -> Tuple[str, List[Dict[str, Any]]]:
    by_id = {section.section_id: section for section in manuscript.body.sections}
    chunks: List[str] = []
    source_map: List[Dict[str, Any]] = []
    for section_id in ordered_ids:
        section = by_id[section_id]
        chunks.append(f"## {section.heading}\n\n")
        for paragraph in section.paragraphs:
            chunks.append(paragraph.rendered_text.strip() + "\n\n")
            source_map.append(paragraph.model_dump(mode="json"))
    return "".join(chunks).strip() + "\n", source_map


def build_full_structure(
    plan: ArticleDirectorPlan | Mapping[str, Any],
    ledger: ClaimLedgerResult | Mapping[str, Any],
    architecture: ArticleArchitectureResult | Mapping[str, Any],
    review: ArticleReviewResult | Mapping[str, Any],
    manuscript: ArticleManuscriptPackage | Mapping[str, Any],
    story_id: str,
    *,
    provider: Optional[QwenFullStructureCoordinator] = None,
    global_quality_audit: GlobalQualityAuditReport | Mapping[str, Any] | None = None,
) -> FullStructureResult:
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
    review_model = (
        review
        if isinstance(review, ArticleReviewResult)
        else ArticleReviewResult.model_validate(review)
    )
    manuscript_model = (
        manuscript
        if isinstance(manuscript, ArticleManuscriptPackage)
        else ArticleManuscriptPackage.model_validate(manuscript)
    )
    lineage_errors: List[str] = []
    expected_plan_id = plan_model.plan_id
    expected_ledger_id = ledger_model.ledger_id
    expected_architecture_id = architecture_model.architecture_id
    expected_story_id = story_id
    lineage_pairs = [
        ("architecture.source_plan_id", architecture_model.source_plan_id, expected_plan_id),
        ("architecture.source_ledger_id", architecture_model.source_ledger_id, expected_ledger_id),
        ("review.plan_id", review_model.plan_id, expected_plan_id),
        ("review.ledger_id", review_model.ledger_id, expected_ledger_id),
        ("review.architecture_id", review_model.architecture_id, expected_architecture_id),
        ("review.story_id", review_model.story_id, expected_story_id),
        ("manuscript.plan_id", manuscript_model.plan_id, expected_plan_id),
        ("manuscript.ledger_id", manuscript_model.ledger_id, expected_ledger_id),
        ("manuscript.architecture_id", manuscript_model.architecture_id, expected_architecture_id),
        ("manuscript.story_id", manuscript_model.story_id, expected_story_id),
    ]
    for field_name, actual, expected in lineage_pairs:
        if actual != expected:
            lineage_errors.append(
                f"{field_name} {actual!r} does not match expected {expected!r}"
            )
    if lineage_errors:
        raise ValueError(
            "full structure lineage mismatch; refusing to send mixed assets to Qwen: "
            + "; ".join(lineage_errors)
        )
    audit_model = (
        global_quality_audit
        if isinstance(global_quality_audit, GlobalQualityAuditReport)
        else GlobalQualityAuditReport.model_validate(global_quality_audit)
        if global_quality_audit is not None
        else None
    )
    scoped_audit_findings, excluded_audit_ids = _audit_findings_for_manuscript(
        audit_model, manuscript_model
    )
    if excluded_audit_ids:
        warnings.append(
            f"excluded {len(excluded_audit_ids)} global quality findings outside the current manuscript"
        )
    payloads = build_full_structure_payload(
        plan_model,
        ledger_model,
        architecture_model,
        review_model,
        manuscript_model,
        story_id,
        audit_model,
    )
    known_sections = {section.section_id for section in manuscript_model.body.sections}
    known_claims = {claim.claim_id for claim in ledger_model.claims}
    warnings: List[str] = []
    errors: List[str] = []
    usage: Dict[str, Any] = {}
    model_status: Literal["available", "partial", "unavailable"] = "unavailable"
    global_thesis = next(
        (
            item.central_thesis
            for item in architecture_model.stories
            if item.story_id == story_id
        ),
        "",
    )
    raw = {
        "section_order": [],
        "rhetorical_edits": [],
        "chapter_argument_gaps": [],
        "structure_gaps": [],
        "global_quality_actions": [],
    }
    if provider is not None:
        try:
            results = list(provider(payloads))
            if len(results) != 1:
                warnings.append("full structure provider returned wrong result count")
            elif isinstance(results[0], FullStructureProviderResult):
                raw = results[0].response
                usage = dict(results[0].usage or {})
                model_status = "available" if raw else "partial"
                global_thesis = str(raw.get("global_thesis") or global_thesis)
            else:
                warnings.append("full structure provider returned invalid envelope")
        except Exception as exc:
            warnings.append(f"full structure provider unavailable: {exc}")
    orders: List[FullSectionOrder] = []
    seen_sections: set[str] = set()
    for index, item in enumerate(raw.get("section_order") or (), start=1):
        try:
            draft = _SectionDraft.model_validate(item)
        except ValidationError as exc:
            warnings.append(f"malformed section-order row {index}: {exc}")
            continue
        if draft.source_section_id not in known_sections:
            errors.append(
                f"section order references unknown section {draft.source_section_id!r}"
            )
            continue
        if draft.source_section_id in seen_sections:
            errors.append(f"section order duplicates {draft.source_section_id!r}")
            continue
        seen_sections.add(draft.source_section_id)
        orders.append(
            FullSectionOrder(
                source_section_id=draft.source_section_id,
                order=len(orders) + 1,
                whole_article_role=draft.whole_article_role or "section contribution",
                reason=draft.reason,
                transition_note=draft.transition_note,
            )
        )
    for section_id in sorted(known_sections - seen_sections):
        warnings.append(
            f"section {section_id!r} was omitted by coordinator; appended unchanged"
        )
        orders.append(
            FullSectionOrder(
                source_section_id=section_id,
                order=len(orders) + 1,
                whole_article_role="unclassified retained section",
                reason="preserve immutable manuscript coverage",
            )
        )
    edits: List[RhetoricalEdit] = []
    for index, item in enumerate(raw.get("rhetorical_edits") or (), start=1):
        try:
            draft = _EditDraft.model_validate(item)
            if not draft.instruction.strip() or any(
                section_id not in known_sections
                for section_id in draft.source_section_ids
            ):
                raise ValueError("unknown section or empty instruction")
            if draft.operation not in {
                "reorder",
                "bridge",
                "deduplicate",
                "label_scope",
            }:
                raise ValueError("unsupported rhetorical operation")
            edits.append(
                RhetoricalEdit(
                    source_section_ids=list(dict.fromkeys(draft.source_section_ids)),
                    operation=draft.operation,
                    instruction=draft.instruction,
                    preserve_claim_bindings=True,
                )
            )
        except (ValidationError, ValueError) as exc:
            warnings.append(f"rhetorical edit {index} ignored: {exc}")
    chapter_gaps: List[ChapterArgumentGap] = []
    for index, item in enumerate(raw.get("chapter_argument_gaps") or (), start=1):
        try:
            draft = _ChapterGapDraft.model_validate(item)
            if draft.section_id and draft.section_id not in known_sections:
                warnings.append(
                    f"chapter gap {index} has unknown section; retained without section binding"
                )
                section_id = ""
            else:
                section_id = draft.section_id
            related_claims = [
                claim_id
                for claim_id in draft.related_claim_ids
                if claim_id in known_claims
            ]
            if len(related_claims) != len(draft.related_claim_ids):
                warnings.append(f"chapter gap {index} dropped unknown Claim IDs")
            chapter_gaps.append(
                ChapterArgumentGap(
                    gap_id=f"chapter-gap-{index:02d}",
                    section_id=section_id,
                    description=draft.description,
                    unique_contribution=draft.unique_contribution,
                    expected_value=draft.expected_value,
                    stop_reason=draft.stop_reason,
                    recommended_next_action=draft.recommended_next_action,
                    related_claim_ids=related_claims,
                )
            )
        except ValidationError as exc:
            warnings.append(f"malformed chapter gap {index}: {exc}")
    structure_gaps: List[StructureGap] = []
    for index, item in enumerate(raw.get("structure_gaps") or (), start=1):
        try:
            draft = _StructureGapDraft.model_validate(item)
            related_sections = [
                sid for sid in draft.related_section_ids if sid in known_sections
            ]
            if len(related_sections) != len(draft.related_section_ids):
                warnings.append(f"structure gap {index} dropped unknown section IDs")
            structure_gaps.append(
                StructureGap(
                    gap_id=f"structure-gap-{index:02d}",
                    description=draft.description,
                    unique_contribution=draft.unique_contribution,
                    expected_value=draft.expected_value,
                    stop_reason=draft.stop_reason,
                    recommended_next_action=draft.recommended_next_action,
                    related_section_ids=related_sections,
                )
            )
        except ValidationError as exc:
            warnings.append(f"malformed structure gap {index}: {exc}")
    global_quality_actions: List[GlobalQualityAction] = []
    known_audit_findings = {item.finding_id: item for item in scoped_audit_findings}
    seen_audit_findings: set[str] = set()
    allowed_handling = {
        "addressed",
        "planned",
        "deferred",
        "not_applicable",
        "unacknowledged",
    }
    for index, item in enumerate(raw.get("global_quality_actions") or (), start=1):
        try:
            draft = _GlobalQualityActionDraft.model_validate(item)
            finding_id = draft.finding_id.strip()
            if not finding_id or finding_id not in known_audit_findings:
                warnings.append(
                    f"global quality action {index} references unknown finding {finding_id!r}"
                )
                continue
            if finding_id in seen_audit_findings:
                warnings.append(
                    f"duplicate global quality action for finding {finding_id!r} ignored"
                )
                continue
            seen_audit_findings.add(finding_id)
            handling = draft.handling.strip().casefold()
            if handling not in allowed_handling:
                warnings.append(
                    f"global quality action {index} has unsupported handling {draft.handling!r}; marked unacknowledged"
                )
                handling = "unacknowledged"
            global_quality_actions.append(
                GlobalQualityAction(
                    finding_id=finding_id,
                    handling=handling,  # type: ignore[arg-type]
                    rationale=draft.rationale,
                    scope_label=draft.scope_label,
                )
            )
        except ValidationError as exc:
            warnings.append(f"malformed global quality action {index}: {exc}")
    unhandled_audit_ids = [
        finding_id
        for finding_id in known_audit_findings
        if finding_id not in seen_audit_findings
        or next(
            action.handling == "unacknowledged"
            for action in global_quality_actions
            if action.finding_id == finding_id
        )
    ]
    if audit_model is None:
        audit_status = "not_provided"
    elif provider is None:
        audit_status = "not_reviewed"
    elif not known_audit_findings:
        audit_status = "acknowledged"
    elif not global_quality_actions:
        audit_status = "unacknowledged"
    elif unhandled_audit_ids:
        audit_status = "partially_acknowledged"
    else:
        audit_status = "acknowledged"
    if audit_model is not None and unhandled_audit_ids:
        warnings.append(
            f"{len(unhandled_audit_ids)} global quality findings were not acknowledged by the coordinator"
        )
    body, source_map = _assemble_body(
        manuscript_model,
        [item.source_section_id for item in orders],
    )
    result_payload = {
        "source_plan_id": plan_model.plan_id,
        "source_architecture_id": architecture_model.architecture_id,
        "source_review_id": review_model.review_id,
        "source_manuscript_package_id": manuscript_model.package_id,
        "story_id": story_id,
        "global_thesis": global_thesis,
        "section_order": [item.model_dump(mode="json") for item in orders],
        "rhetorical_edits": [item.model_dump(mode="json") for item in edits],
        "chapter_argument_gaps": [
            item.model_dump(mode="json") for item in chapter_gaps
        ],
        "structure_gaps": [item.model_dump(mode="json") for item in structure_gaps],
        "body_markdown": body,
        "source_map": source_map,
        "source_global_quality_audit_id": audit_model.audit_id if audit_model else None,
        "global_quality_actions": [
            item.model_dump(mode="json") for item in global_quality_actions
        ],
        "unhandled_global_quality_finding_ids": unhandled_audit_ids,
        "out_of_scope_global_quality_finding_ids": excluded_audit_ids,
    }
    result_id = _digest(result_payload)
    return FullStructureResult(
        result_id=result_id,
        source_plan_id=plan_model.plan_id,
        source_architecture_id=architecture_model.architecture_id,
        source_review_id=review_model.review_id,
        source_manuscript_package_id=manuscript_model.package_id,
        story_id=story_id,
        global_thesis=global_thesis,
        section_order=orders,
        rhetorical_edits=edits,
        chapter_argument_gaps=chapter_gaps,
        structure_gaps=structure_gaps,
        body_markdown=body,
        source_map=source_map,
        warnings=warnings,
        validation_errors=errors,
        model_status=model_status,
        usage=usage,
        semantic_model=MODEL_NAME if provider is not None else "none",
        source_global_quality_audit_id=audit_model.audit_id if audit_model else None,
        global_quality_audit_status=audit_status,  # type: ignore[arg-type]
        global_quality_actions=global_quality_actions,
        unhandled_global_quality_finding_ids=unhandled_audit_ids,
        out_of_scope_global_quality_finding_ids=excluded_audit_ids,
    )


__all__ = [
    "ChapterArgumentGap",
    "FullSectionOrder",
    "FullStructureProviderResult",
    "FullStructureResult",
    "GlobalQualityAction",
    "QwenFullStructureCoordinator",
    "RhetoricalEdit",
    "StructureGap",
    "build_full_structure",
    "build_full_structure_payload",
]
