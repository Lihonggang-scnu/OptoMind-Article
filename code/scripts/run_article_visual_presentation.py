#!/usr/bin/env python3
"""Augment a Presentation package with approved conceptual visuals."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optomind_optics.harness.article_manuscript import ArticleManuscriptPackage
from optomind_optics.harness.article_presentation import (
    ArticlePresentationPackage,
    write_presentation_package,
)
from optomind_optics.harness.article_visual_bridge import VisualMountManifest
from optomind_optics.harness.article_visual_cache import VisualCacheIndex
from optomind_optics.harness.article_visual_presentation import (
    augment_presentation_with_conceptual_visuals,
)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add approved conceptual visuals to Presentation."
    )
    parser.add_argument("--presentation-path", type=Path, required=True)
    parser.add_argument("--manuscript-path", type=Path, required=True)
    parser.add_argument("--cache-path", type=Path, required=True)
    parser.add_argument("--mount-manifest-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    presentation = ArticlePresentationPackage.model_validate(
        _read(args.presentation_path)
    )
    manuscript = ArticleManuscriptPackage.model_validate(_read(args.manuscript_path))
    cache = VisualCacheIndex.model_validate(_read(args.cache_path))
    mounts = VisualMountManifest.model_validate(_read(args.mount_manifest_path))
    augmented = augment_presentation_with_conceptual_visuals(
        presentation, manuscript, cache, mounts
    )
    args.output_dir.mkdir(parents=True, exist_ok=False)
    write_presentation_package(augmented, args.output_dir, manuscript=manuscript)
    print(
        json.dumps(
            {
                "package_id": augmented.package_id,
                "status": augmented.status,
                "visuals": len(augmented.visuals),
                "figures": sum(
                    item.asset_kind == "figure" for item in augmented.visuals
                ),
                "tables": sum(item.asset_kind == "table" for item in augmented.visuals),
                "blockers": len(augmented.blockers),
                "errors": augmented.errors,
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
