"""Deterministic whole-Article quality audit.

This audit checks cross-route scope and source-boundary risks after section
writing. It records findings for a commander/author loop; it never rewrites
scientific prose or silently changes Claim/Fact bindings.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from optomind_research.runtime.artifact_store import atomic_write_json


AUDIT_SCHEMA_VERSION = "article-global-quality-audit.v1"


class GlobalQualityFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    finding_id: str
    article_id: str
    paragraph_id: str = ""
    kind: str
    severity: Literal["minor", "major", "critical"]
    message: str
    suggested_action: str
    source_claim_ids: List[str] = Field(default_factory=list)
    source_artifact_ids: List[str] = Field(default_factory=list)
    auto_fixable: bool = False


class GlobalQualityAuditReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["article-global-quality-audit.v1"] = (
        AUDIT_SCHEMA_VERSION
    )
    audit_id: str
    article_id: str
    status: Literal["clean", "ready_with_findings", "blocked"]
    findings: List[GlobalQualityFinding] = Field(default_factory=list)
    checks: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class GlobalQualityAuditError(ValueError):
    """Malformed audit input or conflicting persisted audit."""


def _dump(value: Any) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:16]


def _mapping(value: Any) -> Mapping[str, Any]:
    raw = _dump(value)
    return raw if isinstance(raw, Mapping) else {}


def _claim_scope(claim: Mapping[str, Any]) -> tuple[str, str, set[str]]:
    metadata = claim.get("metadata") or {}
    contract = metadata.get("synthesis_contract") if isinstance(metadata, Mapping) else {}
    contract = contract if isinstance(contract, Mapping) else {}
    scope = str(contract.get("comparison_scope") or "").strip().casefold()
    route = str(contract.get("route_alias") or "").strip()
    aliases = {
        str(item).strip().casefold()
        for item in contract.get("subject_aliases") or []
        if str(item).strip()
    }
    statement_aliases = {
        token.upper()
        for token in re.findall(r"\bGC\d+\b", str(claim.get("statement") or ""), re.IGNORECASE)
    }
    aliases.update(statement_aliases)
    return scope, route, aliases


def _finding(
    article_id: str,
    paragraph_id: str,
    kind: str,
    severity: Literal["minor", "major", "critical"],
    message: str,
    suggested_action: str,
    *,
    claim_ids: Sequence[str] = (),
    artifact_ids: Sequence[str] = (),
    auto_fixable: bool = False,
) -> GlobalQualityFinding:
    return GlobalQualityFinding(
        finding_id="global-" + _digest(
            [article_id, paragraph_id, kind, message, list(claim_ids)]
        ),
        article_id=article_id,
        paragraph_id=paragraph_id,
        kind=kind,
        severity=severity,
        message=message,
        suggested_action=suggested_action,
        source_claim_ids=sorted(set(str(item) for item in claim_ids if item)),
        source_artifact_ids=sorted(set(str(item) for item in artifact_ids if item)),
        auto_fixable=auto_fixable,
    )


def audit_article_quality(
    *,
    article_id: str,
    manuscript: Any,
    ledger: Any,
    architecture: Any | None = None,
    presentation: Any | None = None,
    delivery: Any | None = None,
) -> GlobalQualityAuditReport:
    """Audit one complete manuscript without altering any upstream asset."""

    manuscript_map = _mapping(manuscript)
    ledger_map = _mapping(ledger)
    claims = {
        str(item.get("claim_id")): item
        for item in ledger_map.get("claims") or []
        if isinstance(item, Mapping) and item.get("claim_id")
    }
    findings: List[GlobalQualityFinding] = []
    route_scope_count = 0
    candidate_binding_count = 0
    precision_count = 0
    global_claim_count = 0
    paragraph_claims: Dict[str, List[str]] = {}
    section_aliases: Dict[str, set[str]] = {}
    global_terms = re.compile(
        r"\b(?:global(?:ly)?\s+(?:best|leader|optimal)|best\s+overall|"
        r"highest\s+(?:aggregate\s+)?(?:target|score)|among\s+all\s+evaluated)\b",
        re.IGNORECASE,
    )
    candidate_tokens = re.compile(r"\bGC\d+\b", re.IGNORECASE)
    risky_interpretation = re.compile(
        r"\b(?:lower\s+cost|manufacturability|deposition\s+cycles|"
        r"improved\s+polarization\s+separation|extinction\s+ratio|"
        r"physically\s+reliable|installation\s+reliability)\b",
        re.IGNORECASE,
    )
    long_number = re.compile(r"(?<![A-Za-z])\d+\.\d{7,}(?:[eE][+-]?\d+)?")

    # A paragraph can be a bounded synthesis of several evidence-bound
    # paragraphs in the same section. Build section-local candidate coverage
    # before flagging a candidate name as unbound.
    for paragraph in manuscript_map.get("source_map") or []:
        if not isinstance(paragraph, Mapping):
            continue
        paragraph_id = str(paragraph.get("paragraph_id") or "")
        section_id = paragraph_id.rsplit("-p", 1)[0] if "-p" in paragraph_id else paragraph_id
        claim_ids = [str(item) for item in paragraph.get("claim_ids") or []]
        aliases = set().union(
            *(_claim_scope(claims[item])[2] for item in claim_ids if item in claims)
        ) if claim_ids else set()
        section_aliases.setdefault(section_id, set()).update(aliases)

    for paragraph in manuscript_map.get("source_map") or []:
        if not isinstance(paragraph, Mapping):
            continue
        paragraph_id = str(paragraph.get("paragraph_id") or "")
        text = str(paragraph.get("rendered_text") or "")
        claim_ids = [str(item) for item in paragraph.get("claim_ids") or []]
        for claim_id in claim_ids:
            paragraph_claims.setdefault(claim_id, []).append(paragraph_id)
        bound_claims = [claims[item] for item in claim_ids if item in claims]
        scopes = [_claim_scope(item) for item in bound_claims]
        route_scoped = [item for item in scopes if item[0] == "route"]
        if route_scoped:
            route_scope_count += 1
        if global_terms.search(text):
            global_claim_count += 1
            route_names = sorted({route for _, route, _ in route_scoped if route})
            if route_scoped and not re.search(
                r"\b(?:route|subset|baseline|discriminative|R\d+)\b",
                text,
                re.IGNORECASE,
            ):
                findings.append(
                    _finding(
                        article_id,
                        paragraph_id,
                        "route_scope_ambiguity",
                        "major",
                        f"Global/best wording is bound to route-scoped claims {route_names} without naming the route.",
                        "Qualify the statement as route-local or explicitly show the cross-route comparison set.",
                        claim_ids=claim_ids,
                    )
                )
            elif not bound_claims:
                findings.append(
                    _finding(
                        article_id,
                        paragraph_id,
                        "unbound_global_claim",
                        "major",
                        "Global/best wording appears in a paragraph with no bound Claim.",
                        "Bind the sentence to a verified Claim or rewrite it as a scoped observation.",
                        claim_ids=claim_ids,
                    )
                )
        aliases = set().union(*(scope[2] for scope in scopes)) if scopes else set()
        section_id = paragraph_id.rsplit("-p", 1)[0] if "-p" in paragraph_id else paragraph_id
        contextual_aliases = aliases | section_aliases.get(section_id, set())
        for token in candidate_tokens.findall(text):
            candidate_binding_count += 1
            if token.upper() not in {item.upper() for item in contextual_aliases}:
                findings.append(
                    _finding(
                        article_id,
                        paragraph_id,
                        "candidate_binding_drift",
                        "major",
                        f"Candidate {token} is named but is absent from the paragraph's bound Claim subject aliases.",
                        "Bind the candidate's Claim/Fact to this paragraph or remove the candidate-specific assertion.",
                        claim_ids=claim_ids,
                    )
                )
        for number in long_number.findall(text):
            precision_count += 1
            findings.append(
                _finding(
                    article_id,
                    paragraph_id,
                    "display_precision",
                    "minor",
                    f"Numeric value {number} has more than six decimal places in public prose.",
                    "Round the display value while retaining the exact value in the source ledger and artifact metadata.",
                    claim_ids=claim_ids,
                    auto_fixable=True,
                )
            )
        if risky_interpretation.search(text):
            findings.append(
                _finding(
                    article_id,
                    paragraph_id,
                    "metric_overinterpretation_risk",
                    "major",
                    "The paragraph uses manufacturing, cost, isolation, or reliability language that may exceed its bound metric.",
                    "Keep the measured metric and label broader implications as hypotheses or future work unless a Claim explicitly authorizes them.",
                    claim_ids=claim_ids,
                )
            )

    best_claims = []
    best_pattern = re.compile(
        r"\b(?:global\s+best|best[- ]target[_ -]?score|highest\s+.*score|"
        r"highest\s+target\s+score)\b",
        re.IGNORECASE,
    )
    for claim_id, claim in claims.items():
        statement = str(claim.get("statement") or "")
        if not best_pattern.search(statement):
            continue
        scope, route, aliases = _claim_scope(claim)
        best_claims.append((claim_id, scope, route, aliases, statement))
    route_best = [item for item in best_claims if item[2]]
    route_names = {item[2] for item in route_best}
    if len(route_best) > 1 and len(route_names) > 1:
        subjects = sorted(
            alias.upper()
            for _, _, _, aliases, _ in route_best
            for alias in aliases
            if alias.upper().startswith("GC")
        )
        claim_ids = [item[0] for item in route_best]
        findings.append(
            _finding(
                article_id,
                "",
                "cross_route_best_conflict",
                "major",
                f"Multiple route-scoped best-score Claims coexist across routes {sorted(route_names)} for candidates {subjects}; the article must distinguish route-local winners from a cross-route winner.",
                "Qualify every best/highest statement with its route or provide an explicit verified cross-route comparison before using global wording.",
                claim_ids=claim_ids,
            )
        )

    checks = {
        "paragraph_count": len(manuscript_map.get("source_map") or []),
        "claim_count": len(claims),
        "route_scoped_paragraphs": route_scope_count,
        "global_wording_paragraphs": global_claim_count,
        "candidate_mentions": candidate_binding_count,
        "long_precision_values": precision_count,
        "route_best_claims": len(route_best),
        "route_best_scopes": sorted(route_names),
        "presentation_blockers": len(_mapping(presentation).get("blockers") or []) if presentation else None,
        "delivery_blockers": len(_mapping(delivery).get("blockers") or []) if delivery else None,
    }
    status: Literal["clean", "ready_with_findings", "blocked"] = (
        "blocked"
        if any(item.severity == "critical" for item in findings)
        else "ready_with_findings"
        if findings
        else "clean"
    )
    return GlobalQualityAuditReport(
        audit_id="global-audit-" + _digest([article_id, checks, [item.model_dump(mode="json") for item in findings]]),
        article_id=article_id,
        status=status,
        findings=findings,
        checks=checks,
    )


def write_global_quality_audit(path: str | Path, report: GlobalQualityAuditReport) -> Path:
    target = Path(path)
    payload = report.model_dump(mode="json")
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
        if existing != payload:
            raise GlobalQualityAuditError(f"refusing to overwrite conflicting audit: {target}")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(target, payload)
    return target


__all__ = [
    "GlobalQualityAuditError",
    "GlobalQualityFinding",
    "GlobalQualityAuditReport",
    "audit_article_quality",
    "write_global_quality_audit",
]
