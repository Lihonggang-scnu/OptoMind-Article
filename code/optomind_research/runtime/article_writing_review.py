"""Stage 11-12 article writing + independent review (T-12).

Routing contract (P1-05 + dual-tier):
  - writing / synthesis completions route through the PLUS tier;
  - review / evaluation completions route through the TURBO tier
    (structured judgement, cheaper tier), each recorded separately in the
    shared CostTracker.

Reviewer independence: review_article receives ONLY the article markdown,
both ledgers and the charter -- never the writer's chain-of-thought,
message history or planner internals.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from config.qwen_config import get_cost_tracker

ReviewSeverity = Literal["critical", "major", "minor", "suggestion"]
ReviewVerdict = Literal["accept", "major_revision", "reject"]
_SEVERITIES = frozenset({"critical", "major", "minor", "suggestion"})
_VERDICTS = frozenset({"accept", "major_revision", "reject"})


def _qwen_total_tokens(usage: Any) -> int:
    usage = dict(usage or {})
    for key in ("total_tokens", "totalTokens"):
        if usage.get(key) is not None:
            return int(usage[key])
    input_tokens = (
        usage.get("input_tokens") or usage.get("inputTokens")
        or usage.get("prompt_tokens") or 0
    )
    output_tokens = (
        usage.get("output_tokens") or usage.get("outputTokens")
        or usage.get("completion_tokens") or 0
    )
    return int(input_tokens) + int(output_tokens)


@dataclass
class ReviewFinding:
    finding_id: str
    severity: str
    section: str
    description: str
    suggestion: str
    related_claim_id: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ArticleReviewReport:
    findings: list[ReviewFinding] = field(default_factory=list)
    overall_verdict: str = "major_revision"
    reviewer_model: str = "qwen3.7-flash"

    def to_dict(self) -> dict:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "overall_verdict": self.overall_verdict,
            "reviewer_model": self.reviewer_model,
        }


def _default_turbo_client():
    # Lazy cross-package import keeps runtime modules decoupled at import time.
    from optomind_optics.harness.task_compiler import ArticleTurboQwenClient

    return ArticleTurboQwenClient()


#: Seam for tests / alternative deployments. review_article itself keeps a
#: four-parameter signature (reviewer independence is auditable by inspect).
REVIEW_CLIENT_FACTORY = _default_turbo_client


def _default_plus_client():
    from optomind_optics.harness.problem_analyzer import ArticlePlusQwenClient

    return ArticlePlusQwenClient()


PLUS_CLIENT_FACTORY = _default_plus_client


def run_writing_completion(messages, *, client=None, max_tokens=4000):
    """PLUS-tier completion for writing/synthesis tasks (cost role "plus")."""
    active = client if client is not None else PLUS_CLIENT_FACTORY()
    response = active.call(messages, max_tokens=max_tokens)
    get_cost_tracker().record_qwen_usage(
        "plus", _qwen_total_tokens(response.get("_llm_usage"))
    )
    return response


_REVIEW_SYSTEM_PROMPT = (
    "You are an independent reviewer for a reproducible optical thin-film "
    "article. Judge only the submitted markdown against the supplied "
    "provenance facts and claims. Respond with strict JSON."
)

_REVIEW_USER_TEMPLATE = (
    "Review the following article draft. Return JSON exactly like:\n"
    '{{\"findings\": [{{\"severity\": \"critical|major|minor|suggestion\", '
    '\"section\": \"<section name>\", \"description\": \"<problem>\", '
    '\"suggestion\": \"<fix>\", \"related_claim_id\": null}}], '
    '\"overall_verdict\": \"accept|major_revision|reject\"}}\n'
    "--- ARTICLE MARKDOWN ---\n{article}\n"
    "--- PROVENANCE SUMMARY ---\n{facts}\n"
    "--- CLAIMS ---\n{claims}\n"
    "--- CHARTER ---\n{charter}"
)


def _ledger_summary(provenance_ledger, claim_ledger, charter) -> dict:
    facts = [
        {
            "token_id": e.token_id,
            "source_type": e.source_type,
            "quantity_name": e.quantity_name,
            "human_readable": e.human_readable,
        }
        for e in getattr(provenance_ledger, "entries", [])
    ]
    claims = [
        {
            "claim_id": c.claim_id,
            "claim_type": c.claim_type,
            "statement": c.statement,
            "evidence_level": c.evidence_level,
        }
        for c in getattr(claim_ledger, "claims", [])
    ]
    if isinstance(charter, dict):
        charter_payload = charter
    else:
        charter_payload = {
            name: str(getattr(charter, name, ""))
            for name in (
                "wavelength_range_nm",
                "angle_range_deg",
                "polarization",
                "material_whitelist",
                "layer_count_bounds",
            )
        }
    return {"facts": facts, "claims": claims, "charter": charter_payload}


def _parse_review_payload(raw: str) -> dict:
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            return payload
    except ValueError:
        pass
    start, end = raw.find("{"), raw.rfind("}")
    if 0 <= start < end:
        try:
            payload = json.loads(raw[start : end + 1])
            if isinstance(payload, dict):
                return payload
        except ValueError:
            pass
    return {}


def _coerce_findings(payload: dict) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []
    raw_items = payload.get("findings")
    if not isinstance(raw_items, list):
        return findings
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        description = str(item.get("description") or "").strip()
        if not description:
            continue
        severity = str(item.get("severity") or "minor").lower()
        if severity not in _SEVERITIES:
            severity = "minor"
        section = str(item.get("section") or "General").strip() or "General"
        suggestion = str(item.get("suggestion") or "").strip()
        related = item.get("related_claim_id")
        related_claim_id = str(related).strip() if related else None
        digest = hashlib.sha256(
            f"{severity}|{section}|{description}".encode("utf-8")
        ).hexdigest()[:12]
        findings.append(
            ReviewFinding(
                finding_id=f"rvw_{digest}",
                severity=severity,
                section=section,
                description=description,
                suggestion=suggestion,
                related_claim_id=related_claim_id,
            )
        )
    return findings


def review_article(
    article_markdown: str,
    provenance_ledger,
    claim_ledger,
    charter,
) -> ArticleReviewReport:
    """Independent structured review -- P1-05 isolation by construction.

    The parameter list is deliberately closed: markdown, both ledgers,
    charter. Writer chain-of-thought or planner internals cannot be passed
    here, so the reviewer physically cannot see them.
    """
    client = REVIEW_CLIENT_FACTORY()
    summary = _ledger_summary(provenance_ledger, claim_ledger, charter)
    user_content = _REVIEW_USER_TEMPLATE.format(
        article=str(article_markdown or ""),
        facts=json.dumps(summary["facts"], ensure_ascii=False),
        claims=json.dumps(summary["claims"], ensure_ascii=False),
        charter=json.dumps(summary["charter"], ensure_ascii=False),
    )
    messages = [
        {"role": "system", "content": _REVIEW_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    response = client.call(messages, max_tokens=4000)
    declared_model = str(
        getattr(client, "model_name", "") or response.get("_llm_usage", {}).get("model_name") or "qwen3.7-flash"
    )
    get_cost_tracker().record_qwen_usage(
        "turbo", _qwen_total_tokens(response.get("_llm_usage"))
    )
    payload = _parse_review_payload(str(response.get("content") or ""))
    findings = _coerce_findings(payload)
    verdict = str(payload.get("overall_verdict") or "major_revision").lower()
    if verdict not in _VERDICTS:
        verdict = "major_revision"
    return ArticleReviewReport(
        findings=findings,
        overall_verdict=verdict,
        reviewer_model=declared_model,
    )


__all__ = [
    "ArticleReviewReport",
    "PLUS_CLIENT_FACTORY",
    "REVIEW_CLIENT_FACTORY",
    "ReviewFinding",
    "ReviewSeverity",
    "ReviewVerdict",
    "review_article",
    "run_writing_completion",
]
