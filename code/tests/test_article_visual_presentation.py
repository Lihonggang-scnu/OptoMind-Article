from __future__ import annotations

import json
from pathlib import Path

import pytest

from optomind_optics.harness.article_manuscript import ArticleManuscriptPackage
from optomind_optics.harness.article_presentation import ArticlePresentationPackage
from optomind_optics.harness.article_visual_bridge import VisualMountManifest
from optomind_optics.harness.article_visual_cache import VisualCacheIndex
from optomind_optics.harness.article_visual_presentation import (
    augment_presentation_with_conceptual_visuals,
)


ROOT = Path(__file__).resolve().parents[2]
REAL = ROOT / "stage17_real_integration"
PRESENTATION = (
    REAL
    / "article_delivery_075_054_review_aware_frontmatter"
    / "REBUILT_PRESENTATION_PACKAGE.json"
)
MANUSCRIPT = REAL / "article_review_replay_054_advice_router" / "05-manuscript.json"
CACHE = (
    REAL
    / "article_visual_plan_057_protocol_complete"
    / "ARTICLE_VISUAL_CACHE_INDEX_APPROVED_V4.json"
)
MOUNTS = (
    REAL
    / "article_visual_plan_057_protocol_complete"
    / "ARTICLE_VISUAL_MOUNT_MANIFEST_077.json"
)


def test_approved_conceptual_visuals_augment_presentation() -> None:
    paths = [PRESENTATION, MANUSCRIPT, CACHE, MOUNTS]
    if not all(path.is_file() for path in paths):
        pytest.skip("075/077 visual presentation assets are not present")
    presentation = ArticlePresentationPackage.model_validate(
        json.loads(PRESENTATION.read_text(encoding="utf-8"))
    )
    manuscript = ArticleManuscriptPackage.model_validate(
        json.loads(MANUSCRIPT.read_text(encoding="utf-8"))
    )
    cache = VisualCacheIndex.model_validate(
        json.loads(CACHE.read_text(encoding="utf-8"))
    )
    mounts = VisualMountManifest.model_validate(
        json.loads(MOUNTS.read_text(encoding="utf-8"))
    )
    augmented = augment_presentation_with_conceptual_visuals(
        presentation, manuscript, cache, mounts
    )
    assert augmented.package_id != presentation.package_id
    assert sum(item.asset_kind == "figure" for item in augmented.visuals) == 2
    assert sum(item.asset_kind == "table" for item in augmented.visuals) == 4
    assert "not measured data" in augmented.reader_markdown
