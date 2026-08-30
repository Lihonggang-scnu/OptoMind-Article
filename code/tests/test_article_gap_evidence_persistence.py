from __future__ import annotations

from optomind_optics.harness.article_gap_evidence_persistence import (
    persist_gap_retrieval_evidence,
)
from optomind_optics.harness.article_gap_retrieval_dispatch import (
    GapRetrievalDispatchResult,
)
from optomind_optics.harness.method_research import (
    MethodAllowedUse,
    MethodContentDepth,
    MethodEvidence,
    MethodResearchQuery,
    MethodResearchReport,
    MethodResearchStatus,
    MethodPurpose,
)


def _dispatch(evidence_id: str = "e1") -> GapRetrievalDispatchResult:
    query = MethodResearchQuery(
        query_id="q1",
        query_text="bounded gap query",
        purpose=MethodPurpose.failure_mode,
    )
    evidence = MethodEvidence(
        evidence_id=evidence_id,
        paper_id="paper-1",
        title="A method paper",
        source_route="s2_snippet_search",
        content_depth=MethodContentDepth.s2_snippet,
        text="A bounded method passage.",
        query_ids=[query.query_id],
        allowed_use=MethodAllowedUse.method_guidance,
    )
    report = MethodResearchReport(
        problem_id="gap-problem",
        queries=[query],
        evidence=[evidence],
        status=MethodResearchStatus.completed,
    )
    return GapRetrievalDispatchResult(
        dispatch_id="dispatch-1",
        source_query_plan_id="plan-1",
        status="completed",
        reports=[report],
        dispatched_query_ids=[query.query_id],
    )


def test_gap_evidence_persistence_is_article_owned_and_idempotent(tmp_path):
    first = persist_gap_retrieval_evidence(_dispatch(), work_dir=tmp_path / "article")
    second = persist_gap_retrieval_evidence(_dispatch(), work_dir=tmp_path / "article")
    assert first.added_evidence_ids == ["e1"]
    assert second.duplicate_evidence_ids == ["e1"]
    assert first.memory_path.endswith("ARTICLE_MEMORY.sqlite")
    assert first.validation_errors == []


def test_gap_evidence_conflict_is_reported(tmp_path):
    first = persist_gap_retrieval_evidence(_dispatch(), work_dir=tmp_path / "article")
    altered = _dispatch()
    altered_report = altered.reports[0].model_copy(
        update={"evidence": [altered.reports[0].evidence[0].model_copy(update={"text": "different"})]}
    )
    altered_dispatch = altered.model_copy(update={"reports": [altered_report]})
    second = persist_gap_retrieval_evidence(altered_dispatch, work_dir=tmp_path / "article")
    assert second.conflict_evidence_ids == ["e1"]
    assert second.validation_errors
