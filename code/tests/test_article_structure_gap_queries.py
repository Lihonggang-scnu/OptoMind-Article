from __future__ import annotations

import json
from pathlib import Path

import pytest

from optomind_optics.harness.article_full_structure import ChapterArgumentGap, FullStructureResult
from optomind_optics.harness.article_structure_gap_queries import (
    GapQueryProviderResult,
    build_gap_query_plan,
)
from optomind_optics.harness.article_structure_gap_tasks import compile_structure_gap_tasks


ROOT = Path(__file__).resolve().parents[2]
FULL = ROOT / "stage17_real_integration" / "article_full_structure_134_assembled_input_smoke" / "FULL_ARTICLE_STRUCTURE.json"


@pytest.fixture()
def full():
    if not FULL.is_file():
        pytest.skip("full structure smoke asset is not present")
    return FullStructureResult.model_validate(json.loads(FULL.read_text(encoding="utf-8")))


class FakeQueryProvider:
    def __call__(self, requests):
        task = requests[0]["gap_tasks"][0]
        return [
            GapQueryProviderResult(
                response={
                    "queries": [
                        {
                            "source_task_id": task["task_id"],
                            "protocol": "s2_snippet",
                            "query_text": "bounded robustness comparison for the target section",
                            "direction_label": "find a distinct robustness role",
                        },
                        {
                            "source_task_id": task["task_id"],
                            "protocol": "s2_snippet",
                            "query_text": "bounded robustness comparison for the target section",
                            "direction_label": "duplicate should be removed",
                        },
                    ]
                },
                provider_model="fake-query-planner",
                usage={"input_tokens": 10, "output_tokens": 5},
            )
        ]


def test_no_tasks_never_calls_provider(full):
    compilation = compile_structure_gap_tasks(full)
    result = build_gap_query_plan(full, compilation, provider=FakeQueryProvider())
    assert result.status == "no_tasks"
    assert result.queries == []


def test_query_planner_deduplicates_and_uses_task_limits(full):
    gap = ChapterArgumentGap(
        gap_id="gap-query-test",
        section_id="story-05-section-02",
        description="Missing a robustness comparison.",
        unique_contribution="Add a distinct robustness role.",
        expected_value="Clarifies trade-offs.",
        stop_reason="Stop after one bounded supplement.",
        recommended_next_action="Search a focused robustness comparison.",
    )
    full_with_gap = full.model_copy(update={"chapter_argument_gaps": [gap]})
    compilation = compile_structure_gap_tasks(full_with_gap)
    result = build_gap_query_plan(full_with_gap, compilation, provider=FakeQueryProvider())
    assert result.status == "planned"
    assert len(result.queries) == 1
    assert result.queries[0].protocol == "s2_snippet"
    assert result.queries[0].max_items == 6
    assert result.unhandled_task_ids == []
    assert any("duplicate" in warning for warning in result.warnings)
