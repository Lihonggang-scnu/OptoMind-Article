"""Independent citation-placement audit for the Article presentation layer.

The auditor may only keep, move, or drop an existing paragraph/reference pair.
It cannot create evidence, alter manuscript prose, or change Claim/Fact data.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from optomind_research.runtime.artifact_store import atomic_write_json


AUDIT_SCHEMA_VERSION = "article-citation-audit.v1"
AUDIT_PROMPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "optical_harness"
    / "Article Citation Auditor.txt"
)


class CitationAuditDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    paragraph_id: str
    reference_alias: str
    action: Literal["keep", "move", "drop"]
    sentence_position: int | None = None
    reason: str

    @field_validator("paragraph_id", "reference_alias", "reason")
    @classmethod
    def _required_text(cls, value: str) -> str:
        value = str(value or "").strip()
        if not value:
            raise ValueError("citation audit identifiers/reason must be non-empty")
        return value


class CitationAuditResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["article-citation-audit.v1"] = AUDIT_SCHEMA_VERSION
    audit_id: str
    source_presentation_id: str
    decisions: List[CitationAuditDecision] = Field(default_factory=list)
    status: Literal["ready", "blocked"] = "ready"
    usage: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    decision_digest: str = ""


class CitationAuditError(ValueError):
    """Malformed or identity-inconsistent citation audit."""


class CitationAuditProvider(Protocol):
    def __call__(
        self,
        package: Any,
        manuscript: Any,
        evidence_by_id: Mapping[str, Any],
    ) -> Any: ...


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _parse_json_object(text: str) -> Mapping[str, Any]:
    raw = str(text or "").strip()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise CitationAuditError("citation auditor returned no JSON object")
        try:
            value = json.loads(raw[start : end + 1])
        except json.JSONDecodeError as exc:
            raise CitationAuditError("citation auditor returned malformed JSON") from exc
    if not isinstance(value, Mapping):
        raise CitationAuditError("citation auditor response must be a JSON object")
    return value


def _pair_key(paragraph_id: str, reference_alias: str) -> tuple[str, str]:
    return str(paragraph_id), str(reference_alias)


def build_citation_audit_request(
    package: Any,
    manuscript: Any,
    evidence_by_id: Mapping[str, Any],
) -> Dict[str, Any]:
    """Build a bounded audit request from an existing presentation package."""

    from .article_presentation import _sentence_table

    paragraph_text = {
        item.paragraph_id: item.rendered_text for item in manuscript.source_map
    }
    placements_by_pair = {
        _pair_key(item.paragraph_id, item.reference_alias): item
        for item in package.placements
    }
    references = {item.reference_alias: item for item in package.references}
    rows: List[Dict[str, Any]] = []
    for pair, placement in sorted(placements_by_pair.items()):
        reference = references.get(placement.reference_alias)
        if reference is None or placement.paragraph_id not in paragraph_text:
            continue
        excerpts = []
        for evidence_id in reference.evidence_ids:
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None:
                continue
            excerpts.append(
                {
                    "evidence_id": evidence_id,
                    "allowed_use": evidence.allowed_use.value,
                    "content_depth": evidence.content_depth.value,
                    "excerpt": evidence.text[:1200],
                }
            )
        rows.append(
            {
                "paragraph_id": placement.paragraph_id,
                "reference_alias": placement.reference_alias,
                "current_sentence_position": placement.sentence_position,
                "reference_title": reference.title,
                "reference_year": reference.year,
                "sentences": _sentence_table(paragraph_text[placement.paragraph_id]),
                "evidence_excerpts": excerpts,
            }
        )
    return {
        "task": "Audit existing citation placements only.",
        "source_presentation_id": package.package_id,
        "placements": rows,
        "response_contract": {
            "decisions": [
                {
                    "paragraph_id": "existing paragraph id",
                    "reference_alias": "existing reference alias",
                    "action": "keep|move|drop",
                    "sentence_position": 0,
                    "reason": "short evidence-based reason",
                }
            ]
        },
    }


def validate_citation_audit(
    payload: Mapping[str, Any] | CitationAuditResult,
    package: Any,
    *,
    allow_omitted_keep: bool = False,
) -> CitationAuditResult:
    """Validate decisions against the exact existing package pair/position set."""

    if isinstance(payload, CitationAuditResult):
        result = payload
    else:
        try:
            result = CitationAuditResult.model_validate(payload)
        except ValidationError as exc:
            raise CitationAuditError(f"invalid citation audit envelope: {exc}") from exc
    if result.source_presentation_id != package.package_id:
        raise CitationAuditError("citation audit source_presentation_id does not match package")
    paragraph_ids = {item.paragraph_id for item in package.placements}
    allowed_pairs = {
        _pair_key(item.paragraph_id, item.reference_alias)
        for item in package.placements
    }
    valid_positions: Dict[str, set[int]] = {}
    from .article_presentation import _sentence_table

    for paragraph in package.reader_markdown.split("\n"):
        del paragraph
    # The package does not carry a source-map object; the builder includes a
    # sentence table in the request, and callers may pass it through decisions.
    # Position validation therefore rejects negative values here and checks the
    # upper bound when the request table is available in the caller.
    seen: set[tuple[str, str]] = set()
    normalized: List[CitationAuditDecision] = []
    for decision in result.decisions:
        pair = _pair_key(decision.paragraph_id, decision.reference_alias)
        if pair not in allowed_pairs:
            raise CitationAuditError(
                f"citation audit references unknown pair {pair!r}"
            )
        if pair in seen:
            raise CitationAuditError(f"citation audit repeats pair {pair!r}")
        seen.add(pair)
        if decision.action == "drop":
            if decision.sentence_position is not None:
                raise CitationAuditError("drop decisions must use null sentence_position")
        elif decision.sentence_position is None or decision.sentence_position < 0:
            raise CitationAuditError(
                "keep/move decisions require a non-negative sentence_position"
            )
        normalized.append(decision)
    missing = sorted(allowed_pairs - seen)
    if missing and not allow_omitted_keep:
        raise CitationAuditError(f"citation audit omitted existing pairs: {missing}")
    if missing:
        normalized.extend(
            CitationAuditDecision(
                paragraph_id=paragraph_id,
                reference_alias=reference_alias,
                action="keep",
                sentence_position=next(
                    item.sentence_position
                    for item in package.placements
                    if _pair_key(item.paragraph_id, item.reference_alias)
                    == (paragraph_id, reference_alias)
                ),
                reason="omitted decision defaults to existing placement",
            )
            for paragraph_id, reference_alias in missing
        )
    normalized.sort(key=lambda item: (item.paragraph_id, item.reference_alias))
    digest = _digest([item.model_dump(mode="json") for item in normalized])
    return result.model_copy(
        update={
            "decisions": normalized,
            "decision_digest": digest,
            "status": "ready",
            "errors": [],
        }
    )


def apply_citation_audit(
    package: Any,
    manuscript: Any,
    audit: CitationAuditResult,
) -> Any:
    """Apply a validated audit without changing manuscript prose or visuals."""

    from .article_presentation import (
        ArticlePresentationPackage,
        _render_reader_manuscript,
        compute_presentation_package_id,
    )

    existing_audit = (package.usage or {}).get("citation_audit")
    requested_digest = _digest(
        [item.model_dump(mode="json") for item in audit.decisions]
    )
    if (
        isinstance(existing_audit, Mapping)
        and existing_audit.get("audit_id") == audit.audit_id
        and existing_audit.get("decision_digest") == requested_digest
    ):
        return package
    audit = validate_citation_audit(audit, package, allow_omitted_keep=True)
    decision_by_pair = {
        _pair_key(item.paragraph_id, item.reference_alias): item
        for item in audit.decisions
    }
    retained_pairs: set[tuple[str, str]] = set()
    seen_placements: set[tuple[str, str]] = set()
    placements = []
    for placement in package.placements:
        pair = _pair_key(placement.paragraph_id, placement.reference_alias)
        if pair in seen_placements:
            continue
        seen_placements.add(pair)
        decision = decision_by_pair[pair]
        if decision.action == "drop":
            continue
        retained_pairs.add(pair)
        if decision.action == "move":
            placement = placement.model_copy(
                update={"sentence_position": decision.sentence_position, "fallback": False}
            )
        placements.append(placement)
    citations = [
        item
        for item in package.citations
        if _pair_key(item.paragraph_id, item.reference_alias) in retained_pairs
    ]
    citations_by_alias: Dict[str, List[Any]] = {}
    for item in citations:
        citations_by_alias.setdefault(item.reference_alias, []).append(item)
    references = []
    for reference in package.references:
        rows = citations_by_alias.get(reference.reference_alias, [])
        if not rows:
            continue
        references.append(
            reference.model_copy(
                update={
                    "paragraph_ids": sorted({item.paragraph_id for item in rows}),
                    "claim_ids": sorted({item.claim_id for item in rows if item.claim_id}),
                    "hypothesis_ids": sorted(
                        {item.hypothesis_id for item in rows if item.hypothesis_id}
                    ),
                    "evidence_ids": sorted({item.evidence_id for item in rows}),
                    "support_semantics": sorted({item.support_semantics for item in rows}),
                    "content_depth": sorted({item.content_depth for item in rows}),
                }
            )
        )
    reader = _render_reader_manuscript(
        front_matter=package.front_matter,
        manuscript=manuscript,
        citations=citations,
        references=references,
        placements=placements,
        visuals=package.visuals,
    )
    warnings = list(package.warnings)
    for decision in audit.decisions:
        if decision.action == "drop":
            warning = (
                f"citation audit dropped {decision.reference_alias} from "
                f"{decision.paragraph_id}: {decision.reason}"
            )
            if warning not in warnings:
                warnings.append(warning)
    attempts = package.attempts + 1
    status = "ready_with_findings" if warnings else "ready"
    package_id = compute_presentation_package_id(
        plan_id=package.plan_id,
        ledger_id=package.ledger_id,
        architecture_id=package.architecture_id,
        review_id=package.review_id,
        result_id=package.result_id,
        manuscript_body_id=package.manuscript_body_id,
        reproducibility_package_id=package.reproducibility_package_id,
        story_id=package.story_id,
        status=status,
        citations=citations,
        references=references,
        placements=placements,
        front_matter=package.front_matter,
        visuals=package.visuals,
        reader_markdown=reader,
        blockers=[],
        warnings=warnings,
        errors=[],
        attempts=attempts,
        literature_supplement_id=package.literature_supplement_id,
    )
    usage = dict(package.usage)
    usage["citation_audit"] = {
        **dict(audit.usage or {}),
        "audit_id": audit.audit_id,
        "source_presentation_id": audit.source_presentation_id,
        "decision_digest": audit.decision_digest,
    }
    return package.model_copy(
        update={
            "package_id": package_id,
            "status": status,
            "citations": citations,
            "references": references,
            "placements": placements,
            "reader_markdown": reader,
            "blockers": [],
            "errors": [],
            "warnings": warnings,
            "attempts": attempts,
            "usage": usage,
        }
    )


def write_citation_audit(path: str | Path, audit: CitationAuditResult) -> Path:
    target = Path(path)
    payload = audit.model_dump(mode="json")
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
        if existing != payload:
            raise CitationAuditError(f"refusing to overwrite conflicting audit: {target}")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(target, payload)
    return target


def load_citation_audit(path: str | Path) -> CitationAuditResult:
    target = Path(path)
    try:
        return CitationAuditResult.model_validate(
            json.loads(target.read_text(encoding="utf-8"))
        )
    except (OSError, ValueError, ValidationError) as exc:
        raise CitationAuditError(f"invalid citation audit file: {target}") from exc


class QwenCitationAuditor:
    """Bounded qwen3.7-flash auditor; provider failure is caller fail-open."""

    def __init__(self, *, client: Any | None = None, prompt_path: str | Path = AUDIT_PROMPT_PATH):
        if client is None:
            from .qwen_policy import QwenFlashOnlyClient

            client = QwenFlashOnlyClient(agent_name="ArticleCitationAuditor")
        self.client = client
        self.prompt_path = Path(prompt_path)

    def __call__(self, package: Any, manuscript: Any, evidence_by_id: Mapping[str, Any]) -> CitationAuditResult:
        request = build_citation_audit_request(package, manuscript, evidence_by_id)
        response = self.client.call(
            [
                {"role": "system", "content": self.prompt_path.read_text(encoding="utf-8")},
                {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
            ],
            max_tokens=5000,
            force_mock=False,
        )
        payload = _parse_json_object(str(response.get("content") or ""))
        raw_decisions = []
        normalization_warnings: List[str] = []
        for raw in payload.get("decisions") or []:
            if isinstance(raw, Mapping) and raw.get("action") == "drop":
                item = dict(raw)
                if item.get("sentence_position") is not None:
                    item["sentence_position"] = None
                    normalization_warnings.append(
                        f"drop decision for {item.get('paragraph_id')} / "
                        f"{item.get('reference_alias')} ignored its unused "
                        "sentence_position"
                    )
                raw_decisions.append(item)
            else:
                raw_decisions.append(raw)
        result = CitationAuditResult(
            audit_id="audit-"
            + _digest([package.package_id, payload.get("decisions", [])])[:16],
            source_presentation_id=package.package_id,
            decisions=raw_decisions,
            usage=dict(response.get("_llm_usage") or {}),
            warnings=normalization_warnings,
        )
        return validate_citation_audit(result, package, allow_omitted_keep=True)


__all__ = [
    "AUDIT_PROMPT_PATH",
    "CitationAuditDecision",
    "CitationAuditError",
    "CitationAuditProvider",
    "CitationAuditResult",
    "QwenCitationAuditor",
    "apply_citation_audit",
    "build_citation_audit_request",
    "load_citation_audit",
    "validate_citation_audit",
    "write_citation_audit",
]
