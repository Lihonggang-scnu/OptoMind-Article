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
    return (
        FullStructureResult.model_validate(
            json.loads(FULL.read_text(encoding="utf-8"))
        ),
        ArticleArchitectureResult.model_validate(
            json.loads(ARCHITECTURE.read_text(encoding="utf-8"))
        ),
        ArticleManuscriptPackage.model_validate(
            json.loads(MANUSCRIPT.read_text(encoding="utf-8"))
        ),
        VisualPlanResult.model_validate(json.loads(PLAN.read_text(encoding="utf-8"))),
        VisualCacheIndex.model_validate(json.loads(CACHE.read_text(encoding="utf-8"))),
    )


def test_unapproved_assets_are_withheld(assets):
    full, _, manuscript, plan, cache = assets
    manifest = build_visual_mount_manifest(full, plan, cache, manuscript)
    assert manifest.mounts == []
    assert len(manifest.withheld_asset_ids) == 6


def test_approved_conceptual_assets_can_mount_but_data_assets_stay_withheld(assets):
    full, _, manuscript, plan, cache = assets
    cache = approve_conceptual_asset(cache, "conceptual-visual-gap-auto-mechanism")
    cache = approve_conceptual_asset(cache, "conceptual-visual-gap-auto-workflow")
    manifest = build_visual_mount_manifest(full, plan, cache, manuscript)
    assert len(manifest.mounts) == 2
    assert {mount.source_kind for mount in manifest.mounts} == {"generated_conceptual"}
    assert len(manifest.withheld_asset_ids) == 4


def test_visual_mount_rejects_mixed_lineage(assets):
    full, _, manuscript, plan, cache = assets
    mixed = plan.model_copy(update={"source_full_structure_id": "mixed-full"})
    with pytest.raises(ValueError, match="visual mount lineage mismatch"):
        build_visual_mount_manifest(full, mixed, cache, manuscript)
