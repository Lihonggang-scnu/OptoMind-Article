#!/usr/bin/env python3
"""Run or resume the Article continuation (synthesis->manuscript)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optomind_optics.harness.article_architecture import (  # noqa: E402
    QwenArticleArchitecturePlanner,
)
from optomind_optics.harness.article_continuation import (  # noqa: E402
    ArticleContinuation,
    ContinuationRequest,
)
from optomind_optics.harness.article_result_synthesis import (  # noqa: E402
    QwenArticleResultClaimSynthesizer,
)
from optomind_optics.harness.article_review import (  # noqa: E402
    QwenAuthorReviser,
    QwenExpressionReviewer,
    QwenGlobalAdviceRouter,
    QwenGlobalConsistencyReviewer,
    QwenScientificReviewer,
)
from optomind_optics.harness.article_writing import (  # noqa: E402
    QwenFormatRepair,
    QwenSectionWriter,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run or resume the Article continuation from an accepted "
            "eight-stage pipeline run through result synthesis, "
            "architecture, writing, review, and manuscript assembly."
        )
    )
    parser.add_argument("--source-pipeline-dir", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--branch-id", default="root")
    parser.add_argument("--selected-story-id", default="")
    parser.add_argument("--literature-supplement-path", type=Path)
    parser.add_argument("--resume", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        request = ContinuationRequest(
            run_id=args.run_id,
            branch_id=args.branch_id,
            source_pipeline_dir=str(Path(args.source_pipeline_dir).resolve()),
            work_dir=str(Path(args.work_dir).resolve()),
            selected_story_id=args.selected_story_id,
            literature_supplement_path=(
                str(Path(args.literature_supplement_path).resolve())
                if args.literature_supplement_path
                else ""
            ),
        )
        continuation = ArticleContinuation(
            result_synthesis_provider=QwenArticleResultClaimSynthesizer(),
            architecture_provider=QwenArticleArchitecturePlanner(),
            section_writer=QwenSectionWriter(),
            format_repair=QwenFormatRepair(),
            scientific_reviewer=QwenScientificReviewer(),
            expression_reviewer=QwenExpressionReviewer(),
            global_consistency_reviewer=QwenGlobalConsistencyReviewer(),
            global_advice_router=QwenGlobalAdviceRouter(),
            author_reviser=QwenAuthorReviser(),
        )
        result = (
            continuation.resume(request) if args.resume else continuation.run(request)
        )
        print(
            json.dumps(
                {
                    "status": result.status,
                    "result_id": result.result_id,
                    "run_id": result.run_id,
                    "source_pipeline_dir": result.source_pipeline_dir,
                    "selected_story_id": result.selected_story_id,
                    "counts": result.counts,
                    "usage_totals": result.usage.get("totals", {}),
                    "warnings": result.warnings,
                    "errors": result.errors,
                    "receipts": [
                        {
                            "sequence": item.sequence,
                            "stage": item.stage,
                            "status": item.status,
                        }
                        for item in result.receipts
                    ],
                },
                ensure_ascii=True,
                indent=2,
            )
        )
        if result.status == "completed":
            return 0
        if result.status == "partial":
            return 0
        if result.status == "unavailable":
            return 2
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(
            json.dumps(
                {
                    "status": "configuration_error",
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:800],
                },
                ensure_ascii=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
