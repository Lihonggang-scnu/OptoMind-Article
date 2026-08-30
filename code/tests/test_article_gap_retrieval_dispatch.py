from __future__ import annotations

from optomind_optics.harness.article_gap_retrieval_dispatch import dispatch_gap_queries
from optomind_optics.harness.article_structure_gap_queries import (
    GapQueryPlanningResult,
    GapSearchQuery,
)
from optomind_optics.harness.method_research import MethodResearchReport, MethodResearchStatus


def _plan(queries):
    return GapQueryPlanningResult(
        result_id="query-plan",
        source_full_structure_id="full",
        source_compilation_id="compilation",
        status="planned",
        queries=queries,
        model_status="available",
    )


class FakeAdapter:
    def __init__(self):
        self.calls = []

    def research(self, problem, explicit_queries, *, problem_id=None, online=None):
        self.calls.append((problem, list(explicit_queries), problem_id, online))
        return MethodResearchReport(
            problem_id=problem_id or "problem",
            queries=list(explicit_queries),
            status=MethodResearchStatus.completed,
        )


def test_empty_query_plan_does_not_call_adapter():
    adapter = FakeAdapter()
    result = dispatch_gap_queries(
        _plan([]), problem={"scope": "TMM"}, adapter=adapter
    )
    assert result.status == "no_tasks"
    assert adapter.calls == []


def test_dispatch_batches_queries_and_preserves_ids():
    queries = [
        GapSearchQuery(
            query_id=f"q{i}",
            source_task_id="task-1",
            protocol="s2_snippet",
            query_text=f"focused query {i}",
            direction_label="gap",
            max_items=6,
            dedupe_key=f"d{i}",
        )
        for i in range(7)
    ]
    adapter = FakeAdapter()
    result = dispatch_gap_queries(
        _plan(queries),
        problem={"scope": "TMM"},
        adapter=adapter,
        problem_id="gap-problem",
        online=False,
        max_queries_per_call=6,
    )
    assert result.status == "completed"
    assert len(adapter.calls) == 2
    assert result.dispatched_query_ids == [f"q{i}" for i in range(7)]
    assert result.unhandled_query_ids == []
    assert [len(call[1]) for call in adapter.calls] == [6, 1]
