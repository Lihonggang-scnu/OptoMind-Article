from __future__ import annotations

import json
from pathlib import Path

import pytest

from optomind_optics.harness.article_architecture import ArticleArchitectureResult
from optomind_optics.harness.article_full_structure import FullStructureResult
from optomind_optics.harness.article_manuscript import ArticleManuscriptPackage
from optomind_optics.harness.article_visual_bridge import (
    approve_conceptual_asset,
    build_visual_mount_manifest,
)
from optomind_optics.harness.article_visual_cache import VisualCacheIndex
from optomind_optics.harness.article_visual_planner import VisualPlanResult
from optomind_optics.harness.article_visual_render import render_visual_reader_overlay


ROOT = Path(__file__).resolve().parents[2]
REAL = ROOT / "stage17_real_integration"
FULL = REAL / "article_full_structure_055_qwen" / "FULL_ARTICLE_STRUCTURE.json"
ARCHITECTURE = (
    REAL / "article_upper_replay_046_section_subject_binding" / "02-architecture.json"
)
MANUSCRIPT = REAL / "article_review_replay_054_advice_router" / "05-manuscript.json"
PLAN = REAL / "article_visual_plan_057_protocol_complete" / "ARTICLE_VISUAL_PLAN.json"
CACHE = (
    REAL
    / "article_visual_plan_057_protocol_complete"
    / "ARTICLE_VISUAL_CACHE_INDEX_CONCEPTUAL_V2.json"
)


@pytest.fixture()
def assets():
    paths = [FULL, ARCHITECTURE, MANUSCRIPT, PLAN, CACHE]
    if not all(path.is_file() for path in paths):
        pytest.skip("057 visual assets are not present")
    full = FullStructureResult.model_validate(
        json.loads(FULL.read_text(encoding="utf-8"))
    )
    architecture = ArticleArchitectureResult.model_validate(
        json.loads(ARCHITECTURE.read_text(encoding="utf-8"))
    )
    manuscript = ArticleManuscriptPackage.model_validate(
        json.loads(MANUSCRIPT.read_text(encoding="utf-8"))
    )
    plan = VisualPlanResult.model_validate(json.loads(PLAN.read_text(encoding="utf-8")))
    cache = VisualCacheIndex.model_validate(
        json.loads(CACHE.read_text(encoding="utf-8"))
    )
    return full, architecture, manuscript, plan, cache


def test_reader_overlay_mounts_only_approved_conceptual_assets(tmp_path: Path, assets):
    full, _, manuscript, plan, cache = assets
    cache = approve_conceptual_asset(cache, "conceptual-visual-gap-auto-mechanism")
    cache = approve_conceptual_asset(cache, "conceptual-visual-gap-auto-workflow")
    manifest = build_visual_mount_manifest(full, plan, cache, manuscript)
    overlay = render_visual_reader_overlay(full, manuscript, cache, manifest, tmp_path)
    assert overlay.validation_errors == []
    assert len(overlay.copied_assets) == 2
    assert "Conceptual" in overlay.body_markdown
    assert "not measured data" in overlay.body_markdown
    assert all((tmp_path / path).is_file() for path in overlay.copied_assets)
