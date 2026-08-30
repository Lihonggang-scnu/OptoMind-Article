from __future__ import annotations

import json
from pathlib import Path

import pytest

from optomind_optics.harness.article_display_precision import apply_display_precision_fix
from optomind_optics.harness.article_global_quality_audit import (
    GlobalQualityAuditReport,
    audit_article_quality,
)
from optomind_optics.harness.article_manuscript import ArticleManuscriptPackage


ROOT = Path(__file__).resolve().parents[2]
REAL = ROOT / "stage17_real_integration"
MANUSCRIPT = REAL / "article_review_replay_054_advice_router" / "05-manuscript.json"
SYNTHESIS = REAL / "article_continuation_040_structured_attainment_tolerant_full" / "01-result_synthesis.json"


@pytest.fixture()
def real_assets():
    if not MANUSCRIPT.is_file() or not SYNTHESIS.is_file():
        pytest.skip("real display precision assets are not present")
    manuscript = ArticleManuscriptPackage.model_validate(json.loads(MANUSCRIPT.read_text(encoding="utf-8")))
    synthesis = json.loads(SYNTHESIS.read_text(encoding="utf-8"))
    audit = audit_article_quality(article_id=manuscript.package_id, manuscript=manuscript, ledger=synthesis["ledger"])
    return manuscript, audit


def test_display_precision_fix_resolves_only_public_long_decimals(real_assets):
    manuscript, audit = real_assets
    result = apply_display_precision_fix(manuscript, audit)
    assert result.changed_paragraph_ids
    assert result.resolved_finding_ids
    assert result.remaining_finding_ids == []
    assert result.package.package_id != manuscript.package_id
    assert result.package.body.source_map == result.package.source_map
    assert all(
        "[VALUE:" not in paragraph.rendered_text
        or paragraph.rendered_text == manuscript.source_map[0].rendered_text
        for section in result.package.body.sections
        for paragraph in section.paragraphs
    ) is True


def test_display_precision_fix_does_not_change_binding_fields(real_assets):
    manuscript, audit = real_assets
    result = apply_display_precision_fix(manuscript, audit)
    before = {item.paragraph_id: item for item in manuscript.source_map}
    after = {item.paragraph_id: item for item in result.package.source_map}
    assert before.keys() == after.keys()
    for paragraph_id in before:
        assert before[paragraph_id].claim_ids == after[paragraph_id].claim_ids
        assert before[paragraph_id].fact_ids == after[paragraph_id].fact_ids
        assert before[paragraph_id].artifact_ids == after[paragraph_id].artifact_ids
        assert before[paragraph_id].value_token_ids == after[paragraph_id].value_token_ids
