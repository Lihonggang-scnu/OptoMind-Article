#!/usr/bin/env python3
"""Approve safe conceptual assets and build a visual mount manifest."""

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
from optomind_optics.harness.article_visual_cache import write_visual_cache_index
from optomind_optics.harness.article_visual_planner import VisualPlanResult


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build an approved visual mount manifest."
    )
    parser.add_argument("--full-structure-path", type=Path, required=True)
    parser.add_argument("--visual-plan-path", type=Path, required=True)
    parser.add_argument("--cache-path", type=Path, required=True)
    parser.add_argument("--manuscript-path", type=Path, required=True)
    parser.add_argument("--output-path", type=Path, required=True)
    parser.add_argument("--approved-cache-output-path", type=Path)
    args = parser.parse_args()
    full = FullStructureResult.model_validate(_read(args.full_structure_path))
    plan = VisualPlanResult.model_validate(_read(args.visual_plan_path))
    cache = VisualCacheIndex.model_validate(_read(args.cache_path))
    manuscript = ArticleManuscriptPackage.model_validate(_read(args.manuscript_path))
    for gap in plan.gaps:
        if gap.visual_role in {"mechanism", "workflow", "overview"}:
            cache = approve_conceptual_asset(cache, f"conceptual-{gap.gap_id}")
    manifest = build_visual_mount_manifest(full, plan, cache, manuscript)
    if args.approved_cache_output_path is not None:
        write_visual_cache_index(cache, args.approved_cache_output_path)
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    args.output_path.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "manifest_id": manifest.manifest_id,
                "mounts": len(manifest.mounts),
                "withheld": len(manifest.withheld_asset_ids),
                "warnings": manifest.warnings,
                "validation_errors": manifest.validation_errors,
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
