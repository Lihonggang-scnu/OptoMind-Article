"""Persist gap-retrieval evidence into the Article-owned memory store."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .article_gap_retrieval_dispatch import GapRetrievalDispatchResult
from .article_memory import (
    ArticleMemoryStore,
    DuplicateRecordError,
    EvidenceLevel,
    MethodEvidence as ArticleMethodEvidence,
)
from .article_memory_boundary import initialize_article_memory_workspace
from .method_research import MethodContentDepth, MethodEvidence as ResearchEvidence


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GapEvidencePersistenceResult(_StrictModel):
    schema_version: Literal["article-gap-evidence-persistence.v1"] = (
        "article-gap-evidence-persistence.v1"
    )
    persistence_id: str
    source_dispatch_id: str
    memory_path: str
    added_evidence_ids: List[str] = Field(default_factory=list)
    duplicate_evidence_ids: List[str] = Field(default_factory=list)
    conflict_evidence_ids: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    validation_errors: List[str] = Field(default_factory=list)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()[:24]


def _level(depth: MethodContentDepth) -> EvidenceLevel:
    return {
        MethodContentDepth.metadata: EvidenceLevel.snippet,
        MethodContentDepth.abstract: EvidenceLevel.abstract,
        MethodContentDepth.s2_snippet: EvidenceLevel.snippet,
        MethodContentDepth.fulltext: EvidenceLevel.full_text,
    }[depth]


def _to_article_evidence(evidence: ResearchEvidence) -> ArticleMethodEvidence:
    return ArticleMethodEvidence(
        evidence_id=evidence.evidence_id,
        source=evidence.source_route,
        scope="Article gap retrieval method evidence",
        query=",".join(evidence.query_ids),
        excerpt_hash=hashlib.sha256(evidence.text.encode("utf-8")).hexdigest(),
        evidence_level=_level(evidence.content_depth),
        artifact_reference=evidence.local_path,
        metadata={
            "paper_id": evidence.paper_id,
            "title": evidence.title,
            "doi": evidence.doi,
            "year": evidence.year,
            "allowed_use": evidence.allowed_use.value,
            "content_depth": evidence.content_depth.value,
            "query_ids": list(evidence.query_ids),
        },
    )


def persist_gap_retrieval_evidence(
    dispatch: GapRetrievalDispatchResult | Mapping[str, Any],
    *,
    work_dir: str | Path,
) -> GapEvidencePersistenceResult:
    result = (
        dispatch
        if isinstance(dispatch, GapRetrievalDispatchResult)
        else GapRetrievalDispatchResult.model_validate(dispatch)
    )
    memory_path = initialize_article_memory_workspace(work_dir)
    store = ArticleMemoryStore(memory_path)
    added: List[str] = []
    duplicates: List[str] = []
    conflicts: List[str] = []
    warnings = list(result.warnings)
    for report in result.reports:
        for evidence in report.evidence:
            article_evidence = _to_article_evidence(evidence)
            try:
                store.add_evidence(article_evidence)
                added.append(article_evidence.evidence_id)
            except DuplicateRecordError:
                try:
                    existing = store.get_evidence(article_evidence.evidence_id)
                except Exception:
                    conflicts.append(article_evidence.evidence_id)
                    continue
                if existing.model_dump(mode="json") == article_evidence.model_dump(mode="json"):
                    duplicates.append(article_evidence.evidence_id)
                else:
                    conflicts.append(article_evidence.evidence_id)
    errors = [
        f"conflicting Article evidence IDs: {', '.join(sorted(set(conflicts)))}"
    ] if conflicts else []
    payload = {
        "source_dispatch_id": result.dispatch_id,
        "memory_path": str(memory_path),
        "added": sorted(set(added)),
        "duplicates": sorted(set(duplicates)),
        "conflicts": sorted(set(conflicts)),
    }
    return GapEvidencePersistenceResult(
        persistence_id="gap-evidence-persistence-" + _digest(payload),
        source_dispatch_id=result.dispatch_id,
        memory_path=str(memory_path),
        added_evidence_ids=sorted(set(added)),
        duplicate_evidence_ids=sorted(set(duplicates)),
        conflict_evidence_ids=sorted(set(conflicts)),
        warnings=warnings,
        validation_errors=errors,
    )


__all__ = ["GapEvidencePersistenceResult", "persist_gap_retrieval_evidence"]
