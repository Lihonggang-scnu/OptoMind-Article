from __future__ import annotations

import json
from pathlib import Path

import pytest

from optomind_optics.harness.article_chapter_assembly import assemble_chapter_manuscripts
from optomind_optics.harness.article_manuscript import ArticleManuscriptPackage


ROOT = Path(__file__).resolve().parents[2]
REAL = ROOT / "stage17_real_integration"
MANUSCRIPT = REAL / "article_display_precision_fix_109_smoke_best_candidate_synced" / "ARTICLE_MANUSCRIPT_PACKAGE.json"


@pytest.fixture()
def manuscript():
    if not MANUSCRIPT.is_file():
        pytest.skip("best manuscript asset is not present")
    return ArticleManuscriptPackage.model_validate(json.loads(MANUSCRIPT.read_text(encoding="utf-8")))


def test_single_chapter_assembly_preserves_sections_and_bindings(manuscript):
    result = assemble_chapter_manuscripts(
        [manuscript],
        expected_plan_id=manuscript.plan_id,
        expected_ledger_id=manuscript.ledger_id,
        expected_architecture_id=manuscript.architecture_id,
        expected_story_id=manuscript.story_id,
    )
    assert len(result.body.sections) == len(manuscript.body.sections)
    assert [item.paragraph_id for item in result.source_map] == [
        item.paragraph_id for item in manuscript.source_map
    ]
    assert result.body.source_map == result.source_map
    assert result.plan_id == manuscript.plan_id


def test_assembly_rejects_duplicate_section_ids(manuscript):
    with pytest.raises(ValueError, match="duplicate chapter section_id"):
        assemble_chapter_manuscripts(
            [manuscript, manuscript],
            expected_plan_id=manuscript.plan_id,
            expected_ledger_id=manuscript.ledger_id,
            expected_architecture_id=manuscript.architecture_id,
            expected_story_id=manuscript.story_id,
        )


def test_assembly_requires_global_review_for_mixed_chapter_reviews(manuscript):
    empty_body = manuscript.body.model_copy(update={"sections": [], "source_map": []})
    second = manuscript.model_copy(
        update={"review_id": "chapter-review-2", "body": empty_body, "source_map": []}
    )
    with pytest.raises(ValueError, match="global_review_id"):
        assemble_chapter_manuscripts([manuscript, second])
