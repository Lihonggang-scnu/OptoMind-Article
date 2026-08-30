#!/usr/bin/env python3
"""Build a 054 reproducibility handoff from completed source executions only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optomind_optics.harness.article_architecture import ArticleArchitectureResult
from optomind_optics.harness.article_continuation import (
    _contracted_inventory,
    _scoped_story_values,
    load_source_pipeline,
)
from optomind_optics.harness.article_manuscript import ArticleManuscriptPackage
from optomind_optics.harness.article_reproducibility import (
    build_article_reproducibility,
    write_reproducibility_package,
)
from optomind_optics.harness.article_result_synthesis import (
    ArticleResultSynthesisResult,
)
from optomind_optics.harness.article_review import ArticleReviewResult


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build an Article reproducibility handoff without replaying TMM."
    )
    parser.add_argument("--source-pipeline-dir", type=Path, required=True)
    parser.add_argument("--synthesis-path", type=Path, required=True)
    parser.add_argument("--architecture-path", type=Path, required=True)
    parser.add_argument("--review-path", type=Path, required=True)
    parser.add_argument("--manuscript-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    source_pipeline_dir = args.source_pipeline_dir.resolve()
    synthesis_path = args.synthesis_path.resolve()
    architecture_path = args.architecture_path.resolve()
    review_path = args.review_path.resolve()
    manuscript_path = args.manuscript_path.resolve()
    output_dir = args.output_dir.resolve()
    bundle = load_source_pipeline(source_pipeline_dir)
    synthesis = ArticleResultSynthesisResult.model_validate(_read(synthesis_path))
    if synthesis.derived_plan is None or synthesis.ledger is None:
        raise ValueError("synthesis has no derived plan/ledger")
    architecture = ArticleArchitectureResult.model_validate(_read(architecture_path))
    review = ArticleReviewResult.model_validate(_read(review_path))
    manuscript = ArticleManuscriptPackage.model_validate(_read(manuscript_path))
    _, values = _contracted_inventory(synthesis, bundle)
    values = _scoped_story_values(
        architecture, manuscript.story_id, values, synthesis.ledger
    )
    result = build_article_reproducibility(
        synthesis.derived_plan,
        synthesis.ledger,
        architecture,
        review,
        manuscript,
        manuscript.story_id,
        values,
        bundle.executions,
        source_pipeline_dir.parent / "execution",
        replay_provider=None,
        output_dir=output_dir,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    write_reproducibility_package(result, output_dir)
    print(
        json.dumps(
            {
                "package_id": result.package_id,
                "status": result.status,
                "critical_experiments": len(result.critical_experiments),
                "replay_records": len(result.replay_records),
                "blockers": len(result.blockers),
                "warnings": result.warnings,
                "errors": result.errors,
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
