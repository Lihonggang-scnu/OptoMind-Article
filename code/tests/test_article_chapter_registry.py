from __future__ import annotations

from pathlib import Path

import pytest

from optomind_optics.harness.article_chapter_registry import (
    build_chapter_registry,
    require_complete_chapter_registry,
)


ROOT = Path(__file__).resolve().parents[2]
REAL = ROOT / "stage17_real_integration"


def _spec(chapter_id: str = "chapter-01") -> dict:
    return {
        "chapter_id": chapter_id,
        "manuscript_path": str(REAL / "article_display_precision_fix_109_smoke_best_candidate_synced" / "ARTICLE_MANUSCRIPT_PACKAGE.json"),
        "review_path": str(REAL / "article_global_revision_execution_096_pbs_correct_lineage_smoke" / "04-review.json"),
        "reproducibility_path": str(REAL / "article_reproducibility_112_smoke_best_candidate_overlay" / "ARTICLE_REPRODUCIBILITY_PACKAGE.json"),
        "presentation_path": str(REAL / "article_presentation_131_same_lineage_visuals" / "ARTICLE_PRESENTATION_PACKAGE.json"),
        "delivery_path": str(REAL / "article_delivery_132_same_lineage_visuals" / "ARTICLE_DELIVERY_PACKAGE.json"),
        "global_audit_path": str(REAL / "article_global_quality_audit_107_smoke_best_candidate" / "GLOBAL_QUALITY_AUDIT.json"),
    }


def test_registry_reports_partial_when_only_one_of_eight_chapters_exists():
    if not Path(_spec()["manuscript_path"]).is_file():
        pytest.skip("chapter registry assets are not present")
    result = build_chapter_registry([_spec()], expected_chapter_count=8)
    assert result.status == "partial"
    assert result.registered_chapter_count == 1
    assert result.complete_chapter_count == 1
    assert result.missing_chapter_count == 7
    assert result.validation_errors == []
    with pytest.raises(ValueError, match="registry is not complete"):
        require_complete_chapter_registry(result)


def test_registry_rejects_duplicate_chapter_ids():
    with pytest.raises(ValueError, match="chapter_id values must be unique"):
        build_chapter_registry([_spec(), _spec()])


def test_registry_marks_missing_asset_as_invalid():
    spec = _spec("chapter-bad")
    spec["manuscript_path"] = str(REAL / "missing.json")
    result = build_chapter_registry([spec], expected_chapter_count=1)
    assert result.status == "invalid"
    assert result.validation_errors
