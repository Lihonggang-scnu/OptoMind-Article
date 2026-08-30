from __future__ import annotations

import json
from pathlib import Path

import pytest

from optomind_optics.harness.article_full_structure import (
    ChapterArgumentGap,
    StructureGap,
)
from optomind_optics.harness.article_full_structure import FullStructureResult
from optomind_optics.harness.article_structure_gap_tasks import compile_structure_gap_tasks


ROOT = Path(__file__).resolve().parents[2]
FULL = ROOT / "stage17_real_integration" / "article_full_structure_134_assembled_input_smoke" / "FULL_ARTICLE_STRUCTURE.json"


@pytest.fixture()
def full():
    if not FULL.is_file():
        pytest.skip("full structure smoke asset is not present")
    return FullStructureResult.model_validate(json.loads(FULL.read_text(encoding="utf-8")))


def test_no_gap_structure_returns_no_tasks(full):
    result = compile_structure_gap_tasks(full)
    assert result.status == "no_gaps"
    assert result.tasks == []


def test_gap_compilation_sets_type_specific_limits(full):
    chapter = ChapterArgumentGap(
        gap_id="chapter-gap-test",
        section_id="story-05-section-02",
        description="The section lacks a distinct robustness comparison.",
        unique_contribution="Add a separate robustness role.",
        expected_value="Clarifies the section decision boundary.",
        stop_reason="Stop after one bounded supplement if no new role appears.",
        recommended_next_action="Search for route-local robustness comparisons.",
        related_claim_ids=["claim-x"],
    )
    structure = StructureGap(
        gap_id="structure-gap-test",
        description="The Article lacks a distinct limitations axis.",
        unique_contribution="Add a cross-chapter limitations axis.",
        expected_value="Improves whole-Article synthesis.",
        stop_reason="Stop when marginal structural gain is small.",
        recommended_next_action="Search the existing reference inventory for a missing limitations axis.",
        related_section_ids=["story-05-section-06"],
    )
    result = compile_structure_gap_tasks(
        full.model_copy(update={"chapter_argument_gaps": [chapter], "structure_gaps": [structure]})
    )
    assert result.status == "planned"
    chapter_task, structure_task = result.tasks
    assert (chapter_task.max_s2_items, chapter_task.max_oa_items, chapter_task.max_abstract_items) == (6, 8, 12)
    assert (structure_task.max_s2_items, structure_task.max_oa_items, structure_task.max_abstract_items) == (12, 16, 24)
    assert chapter_task.max_rounds == structure_task.max_rounds == 1
