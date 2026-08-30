#!/usr/bin/env python3
"""Approve safe conceptual visuals and render the reader-facing overlay."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optomind_optics.harness.article_full_structure import FullStructureResult
from optomind_optics.harness.article_manuscript import ArticleManuscriptPackage
from optomind_optics.harness.article_visual_bridge import (
    approve_conceptual_asset,
    build_visual_mount_manifest,
)
from optomind_optics.harness.article_visual_cache import VisualCacheIndex
from optomind_optics.harness.article_visual_planner import VisualPlanResult
from optomind_optics.harness.article_visual_render import render_visual_reader_overlay


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render approved Article visual mounts."
    )
    parser.add_argument("--full-structure-path", type=Path, required=True)
    parser.add_argument("--visual-plan-path", type=Path, required=True)
    parser.add_argument("--cache-path", type=Path, required=True)
    parser.add_argument("--manuscript-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    full = FullStructureResult.model_validate(_read(args.full_structure_path))
    plan = VisualPlanResult.model_validate(_read(args.visual_plan_path))
    cache = VisualCacheIndex.model_validate(_read(args.cache_path))
    manuscript = ArticleManuscriptPackage.model_validate(_read(args.manuscript_path))
    for gap in plan.gaps:
        if gap.visual_role in {"mechanism", "workflow", "overview"}:
            cache = approve_conceptual_asset(cache, f"conceptual-{gap.gap_id}")
    manifest = build_visual_mount_manifest(full, plan, cache, manuscript)
    overlay = render_visual_reader_overlay(
        full, manuscript, cache, manifest, args.output_dir
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "ARTICLE_VISUAL_MOUNT_MANIFEST.json").write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "ARTICLE_VISUAL_READER_OVERLAY.json").write_text(
        json.dumps(overlay.model_dump(mode="json"), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "ARTICLE_VISUAL_READER_BODY.md").write_text(
        overlay.body_markdown,
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "overlay_id": overlay.overlay_id,
                "mounts": len(manifest.mounts),
                "copied_assets": len(overlay.copied_assets),
                "withheld": len(overlay.withheld_asset_ids),
                "warnings": overlay.warnings,
                "validation_errors": overlay.validation_errors,
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
