"""Retrieve validated S2 body snippets as first-class knowledge-base chunks."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from ftfy import fix_text

from optomind_research.s2_citation_role_mapper import map_citation_roles
from optomind_research.s2_intelligence_gateway import S2IntelligenceGateway
from optomind_research.s2_schemas import UnifiedTextChunk
from optomind_research.runtime.review_quality_contract import (
    assess_structured_snippet,
    permission_for_content,
)


_BIBLIOGRAPHY_SECTIONS = {
    "references",
    "bibliography",
    "literature cited",
    "acknowledgements",
    "acknowledgments",
}


@dataclass(slots=True)
class TextChunkRetrievalResult:
    accepted_chunks: list[UnifiedTextChunk]
    rejected_items: list[dict[str, Any]]
    query_runs: list[dict[str, Any]]
    paper_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted_chunks": [chunk.to_dict() for chunk in self.accepted_chunks],
            "rejected_items": self.rejected_items,
            "query_runs": self.query_runs,
            "paper_ids": self.paper_ids,
        }


def _normalize_text(text: str) -> str:
    repaired = fix_text(text or "")
    return re.sub(r"\s+", " ", repaired).strip()


def _snippet_chunk_id(
    *, corpus_id: int | str | None, start: int | None, end: int | None, text: str
) -> str:
    digest = hashlib.sha1(
        f"{corpus_id}|{start}|{end}|{_normalize_text(text)}".encode("utf-8")
    ).hexdigest()[:16]
    return f"s2chunk:{corpus_id or 'unknown'}:{start or 0}:{end or 0}:{digest}"


def _reject_reason(
    snippet: dict[str, Any], *, min_chars: int
) -> str:
    text = _normalize_text(str(snippet.get("text") or ""))
    kind = str(snippet.get("snippetKind") or "").casefold()
    section = str(snippet.get("section") or "").strip().casefold()
    if not text:
        return "empty_text"
    if kind != "body":
        return f"not_body:{kind or 'unknown'}"
    if len(text) < min_chars:
        return "too_short"
    if section in _BIBLIOGRAPHY_SECTIONS:
        return "bibliography_section"
    alpha = sum(char.isalpha() for char in text)
    if alpha / max(1, len(text)) < 0.45:
        return "parser_noise"
    return ""


class S2TextChunkRetriever:
    def __init__(
        self,
        gateway: S2IntelligenceGateway | None = None,
        *,
        min_chars: int = 500,
    ) -> None:
        self.gateway = gateway or S2IntelligenceGateway()
        self.min_chars = max(100, int(min_chars))

    def retrieve(
        self,
        queries: list[str],
        *,
        paper_ids: list[str] | None = None,
        limit_per_query: int = 20,
        requested_roles: list[str] | None = None,
        scope_context: dict[str, Any] | None = None,
    ) -> TextChunkRetrievalResult:
        accepted: list[UnifiedTextChunk] = []
        rejected: list[dict[str, Any]] = []
        runs: list[dict[str, Any]] = []
        seen_hashes: set[str] = set()
        resolved_paper_ids: list[str] = []

        for query in list(dict.fromkeys(queries)):
            items, response = self.gateway.search_snippets(
                query,
                limit=limit_per_query,
                paper_ids=paper_ids,
            )
            runs.append(
                {
                    "query": query,
                    "status_code": response.status_code,
                    "status_category": response.status_category,
                    "cache_hit": response.cache_hit,
                    "result_count": len(items),
                    "wait_seconds": response.wait_seconds,
                }
            )
            for item in items:
                snippet = item.get("snippet") or {}
                paper = item.get("paper") or {}
                reason = _reject_reason(snippet, min_chars=self.min_chars)
                if reason:
                    rejected.append(
                        {
                            "query": query,
                            "paper_title": str(paper.get("title") or ""),
                            "reason": reason,
                        }
                    )
                    continue
                text = _normalize_text(str(snippet.get("text") or ""))
                normalized_hash = hashlib.sha1(text.casefold().encode("utf-8")).hexdigest()
                if normalized_hash in seen_hashes:
                    rejected.append(
                        {
                            "query": query,
                            "paper_title": str(paper.get("title") or ""),
                            "reason": "duplicate_text",
                        }
                    )
                    continue
                seen_hashes.add(normalized_hash)
                corpus_raw = paper.get("corpusId")
                try:
                    corpus_id = int(corpus_raw) if corpus_raw not in (None, "") else None
                except (TypeError, ValueError):
                    corpus_id = None
                offset = snippet.get("snippetOffset") or {}
                start = offset.get("start")
                end = offset.get("end")
                paper_id = str(paper.get("paperId") or "").strip()
                if not paper_id and corpus_id is not None:
                    paper_id = f"CorpusId:{corpus_id}"
                if not paper_id:
                    paper_id = f"s2-title:{hashlib.sha1(str(paper.get('title') or '').encode('utf-8')).hexdigest()[:12]}"
                resolved_paper_ids.append(paper_id)
                score = float(item.get("score") or 0.0)
                roles = map_citation_roles(
                    query_or_claim=query,
                    text=text,
                    section=str(snippet.get("section") or ""),
                    requested_roles=requested_roles or [],
                    direct_score=score,
                )
                limitations: list[str] = []
                if re.search(r"\b(?:Fig(?:ure)?|Table|Eq(?:uation)?)\.?\s*\d", text):
                    limitations.append("referenced_visual_or_equation_not_in_snippet")
                if text.endswith(("…", "...")):
                    limitations.append("possible_truncation")
                # Even without an explicit section context, assess the
                # snippet against its own retrieval query.  A body snippet is
                # a first-class chunk, but provider origin alone must never
                # grant direct/context-complete permission.
                scope_assessment = assess_structured_snippet(
                    text,
                    query=query,
                    section_context=str(
                        (scope_context or {}).get("section_context") or ""
                    ),
                    limitations=limitations,
                )
                limitations = list(
                    dict.fromkeys(
                        limitations
                        + list(scope_assessment.get("context_limitations") or [])
                    )
                )
                scope_fit = str(scope_assessment.get("scope_fit") or "unreviewed")
                context_complete = bool(scope_assessment.get("context_complete", False))
                permission = permission_for_content(
                    "structured_snippet",
                    scope_fit=scope_fit,
                    context_complete=context_complete,
                )
                chunk = UnifiedTextChunk(
                    chunk_id=_snippet_chunk_id(
                        corpus_id=corpus_id, start=start, end=end, text=text
                    ),
                    paper_id=paper_id,
                    corpus_id=corpus_id,
                    title=fix_text(str(paper.get("title") or "")).strip(),
                    text=text,
                    section=fix_text(str(snippet.get("section") or "")).strip(),
                    source_locator={
                        "provider": "semantic_scholar",
                        "corpus_id": corpus_id,
                        "offset_start": start,
                        "offset_end": end,
                        "retrieval_version": (
                            response.payload.get("retrievalVersion")
                            if isinstance(response.payload, dict)
                            else ""
                        ),
                    },
                    citation_roles=roles,
                    query_links=[query],
                    score=score,
                    # S2 body snippets are first-class structured chunks.  The
                    # later authoring layer may downgrade a particular use
                    # when scope audit says adjacent/contextual, but the
                    # provider is not treated as an abstract-only source.
                    scope_fit=scope_fit,
                    content_depth="structured_snippet",
                    context_complete=context_complete,
                    use_permission=str(permission["use_permission"]),
                    allowed_claim_kinds=list(permission["allowed_claim_kinds"]),
                    route_provenance={
                        "discovery_route": "semantic_scholar_snippet_search",
                        "materialization_route": "s2_structured_body_snippet",
                        "query": query,
                        "requested_roles": list(dict.fromkeys(
                            str(role).strip().casefold()
                            for role in (requested_roles or [])
                            if str(role).strip()
                        )),
                        "paper_id": paper_id,
                        "scope_assessment": scope_assessment,
                    },
                    context_limitations=limitations,
                    reference_mentions=list(
                        ((snippet.get("annotations") or {}).get("refMentions") or [])
                    ),
                    sentence_spans=list(
                        ((snippet.get("annotations") or {}).get("sentences") or [])
                    ),
                    raw_metadata={"s2_item": item},
                )
                accepted.append(chunk)
        return TextChunkRetrievalResult(
            accepted_chunks=accepted,
            rejected_items=rejected,
            query_runs=runs,
            paper_ids=list(dict.fromkeys(resolved_paper_ids)),
        )
