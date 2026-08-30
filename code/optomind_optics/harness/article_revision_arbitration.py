"""Deterministic selection of a revised Article candidate versus its baseline."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Literal, Mapping, Tuple

from pydantic import BaseModel, ConfigDict, Field

from optomind_optics.harness.article_review import ArticleReviewResult


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RevisionQualityScore(_StrictModel):
    review_id: str
    hard_blocker_count: int = Field(ge=0)
    major_scientific_count: int = Field(ge=0)
    scientific_finding_count: int = Field(ge=0)
    expression_finding_count: int = Field(ge=0)
    status_rank: int = Field(ge=0)
    score: Tuple[int, int, int, int, int]


class RevisionArbitrationResult(_StrictModel):
    schema_version: Literal["article-revision-arbitration.v1"] = (
        "article-revision-arbitration.v1"
    )
    arbitration_id: str
    baseline_review_id: str
    candidate_review_id: str
    selected_review_id: str
    candidate_accepted: bool
    reason: str
    baseline_score: RevisionQualityScore
    candidate_score: RevisionQualityScore
    warnings: List[str] = Field(default_factory=list)


def _model(value: ArticleReviewResult | Mapping[str, Any]) -> ArticleReviewResult:
    return value if isinstance(value, ArticleReviewResult) else ArticleReviewResult.model_validate(value)


def _score(review: ArticleReviewResult) -> RevisionQualityScore:
    hard = len(review.hard_blockers)
    major = sum(item.severity.value == "major" for item in review.scientific_findings)
    scientific = len(review.scientific_findings)
    expression = len(review.expression_findings)
    status_rank = {"ready": 0, "ready_with_findings": 1, "partial": 2, "blocked": 3}.get(
        review.status, 4
    )
    # A revision that creates more scientific findings is not an improvement,
    # even if its remaining findings happen to be less severe.
    score = (hard, scientific, major, expression, status_rank)
    return RevisionQualityScore(
        review_id=review.review_id,
        hard_blocker_count=hard,
        major_scientific_count=major,
        scientific_finding_count=scientific,
        expression_finding_count=expression,
        status_rank=status_rank,
        score=score,
    )


def _same_lineage(base: ArticleReviewResult, candidate: ArticleReviewResult) -> None:
    pairs = [
        ("plan_id", base.plan_id, candidate.plan_id),
        ("ledger_id", base.ledger_id, candidate.ledger_id),
        ("architecture_id", base.architecture_id, candidate.architecture_id),
        ("story_id", base.story_id, candidate.story_id),
    ]
    mismatches = [f"{field}: {left!r} != {right!r}" for field, left, right in pairs if left != right]
    if mismatches:
        raise ValueError("revision arbitration lineage mismatch: " + "; ".join(mismatches))


def arbitrate_article_revision(
    baseline: ArticleReviewResult | Mapping[str, Any],
    candidate: ArticleReviewResult | Mapping[str, Any],
) -> RevisionArbitrationResult:
    """Accept a candidate only when its global review score strictly improves."""

    base = _model(baseline)
    revised = _model(candidate)
    _same_lineage(base, revised)
    base_score = _score(base)
    candidate_score = _score(revised)
    accepted = candidate_score.score < base_score.score
    if accepted:
        reason = (
            "candidate has a strictly better global quality score; retain its "
            "source-bound revisions and downstream re-review findings"
        )
        selected = revised.review_id
    else:
        reason = (
            "candidate does not strictly improve the global quality score; retain "
            "the baseline and keep candidate findings as a rejected revision record"
        )
        selected = base.review_id
    payload = {
        "baseline": base_score.model_dump(mode="json"),
        "candidate": candidate_score.model_dump(mode="json"),
        "selected": selected,
        "accepted": accepted,
    }
    return RevisionArbitrationResult(
        arbitration_id="revision-arbitration-" + hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:24],
        baseline_review_id=base.review_id,
        candidate_review_id=revised.review_id,
        selected_review_id=selected,
        candidate_accepted=accepted,
        reason=reason,
        baseline_score=base_score,
        candidate_score=candidate_score,
    )


__all__ = [
    "RevisionArbitrationResult",
    "RevisionQualityScore",
    "arbitrate_article_revision",
]
