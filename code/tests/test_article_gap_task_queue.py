from __future__ import annotations

from optomind_optics.harness.article_gap_task_queue import (
    build_gap_task_queue,
    claim_next_task,
    complete_task,
    fail_task,
)
from optomind_optics.harness.article_structure_gap_queries import GapQueryPlanningResult, GapSearchQuery
from optomind_optics.harness.article_structure_gap_tasks import StructureGapRetrievalTask, StructureGapTaskCompilationResult


def _compilation():
    task = StructureGapRetrievalTask(
        task_id="task-1", source_full_structure_id="full", source_gap_id="gap-1",
        task_type="chapter_argument_gap", query_scope="section_local", description="gap",
        unique_contribution="new role", expected_value="useful", stop_reason="one round",
        recommended_next_action="search", related_section_ids=["s1"], context_inputs=["section"],
        max_s2_items=6, max_oa_items=8, max_abstract_items=12, dedupe_key="d", status="planned",
    )
    return StructureGapTaskCompilationResult(
        result_id="compilation", source_full_structure_id="full", status="planned", tasks=[task]
    )


def test_queue_claim_complete_is_idempotent():
    query_plan = GapQueryPlanningResult(
        result_id="query-plan", source_full_structure_id="full", source_compilation_id="compilation",
        status="planned", model_status="available", queries=[GapSearchQuery(
            query_id="q1", source_task_id="task-1", protocol="s2_snippet", query_text="gap query",
            direction_label="gap", max_items=6, dedupe_key="q",
        )]
    )
    state = build_gap_task_queue(_compilation(), query_plan)
    state, item = claim_next_task(state, "worker-a")
    assert item is not None and item.status == "claimed"
    state = complete_task(state, item.queue_item_id, dispatch_id="dispatch", persistence_id="persist")
    state = complete_task(state, item.queue_item_id, dispatch_id="dispatch", persistence_id="persist")
    assert state.items[0].status == "completed"
    state, none = claim_next_task(state, "worker-b")
    assert none is None


def test_queue_failure_is_terminal_after_one_attempt():
    state = build_gap_task_queue(_compilation())
    state, item = claim_next_task(state, "worker-a")
    state = fail_task(state, item.queue_item_id, error="adapter unavailable")
    assert state.items[0].status == "failed"
