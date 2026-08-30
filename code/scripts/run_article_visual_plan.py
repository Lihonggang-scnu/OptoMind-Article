#!/usr/bin/env python3
"""Plan source-bound visual placements from full Article checkpoints."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optomind_optics.harness.article_architecture import ArticleArchitectureResult
from optomind_optics.harness.article_full_structure import FullStructureResult
from optomind_optics.harness.article_manuscript import ArticleManuscriptPackage
from optomind_optics.harness.article_review import ArticleReviewResult
from optomind_optics.harness.article_visual_planner import (
    QwenArticleVisualPlanner,
    build_visual_plan,
)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan source-bound Article visuals.")
    parser.add_argument("--full-structure-path", type=Path, required=True)
    parser.add_argument("--architecture-path", type=Path, required=True)
    parser.add_argument("--review-path", type=Path, required=True)
    parser.add_argument("--manuscript-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    full = FullStructureResult.model_validate(_read(args.full_structure_path))
    architecture = ArticleArchitectureResult.model_validate(
        _read(args.architecture_path)
    )
    review = ArticleReviewResult.model_validate(_read(args.review_path))
    manuscript = ArticleManuscriptPackage.model_validate(_read(args.manuscript_path))
    result = build_visual_plan(
        full,
        architecture,
        review,
        manuscript,
        provider=QwenArticleVisualPlanner(),
    )
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "ARTICLE_VISUAL_PLAN.json").write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "result_id": result.result_id,
                "status": result.model_status,
                "placements": len(result.placements),
                "gaps": len(result.gaps),
                "cache_records": len(result.cache_records),
                "validation_errors": result.validation_errors,
                "warnings": result.warnings,
                "usage": result.usage,
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
