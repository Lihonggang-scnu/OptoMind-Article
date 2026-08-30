#!/usr/bin/env python3
"""Run whole-Article structure coordination from immutable checkpoints."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optomind_optics.harness.article_architecture import ArticleArchitectureResult
from optomind_optics.harness.article_claims import ClaimLedgerResult
from optomind_optics.harness.article_director import ArticleDirectorPlan
from optomind_optics.harness.article_full_structure import (
    QwenFullStructureCoordinator,
    build_full_structure,
)
from optomind_optics.harness.article_global_quality_audit import (
    GlobalQualityAuditReport,
)
from optomind_optics.harness.article_manuscript import ArticleManuscriptPackage
from optomind_optics.harness.article_result_synthesis import (
    ArticleResultSynthesisResult,
)
from optomind_optics.harness.article_review import ArticleReviewResult


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Coordinate a whole Article without rewriting source-bound prose."
    )
    parser.add_argument("--synthesis-path", type=Path, required=True)
    parser.add_argument("--architecture-path", type=Path, required=True)
    parser.add_argument("--review-path", type=Path, required=True)
    parser.add_argument("--manuscript-path", type=Path, required=True)
    parser.add_argument(
        "--global-quality-audit-path",
        type=Path,
        default=None,
        help="Optional deterministic whole-Article audit to expose to the coordinator.",
    )
    parser.add_argument("--story-id", default="")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    synthesis = ArticleResultSynthesisResult.model_validate(_read(args.synthesis_path))
    if synthesis.derived_plan is None or synthesis.ledger is None:
        raise ValueError("synthesis has no derived plan or claim ledger")
    architecture = ArticleArchitectureResult.model_validate(
        _read(args.architecture_path)
    )
    review = ArticleReviewResult.model_validate(_read(args.review_path))
    manuscript = ArticleManuscriptPackage.model_validate(_read(args.manuscript_path))
    audit = (
        GlobalQualityAuditReport.model_validate(_read(args.global_quality_audit_path))
        if args.global_quality_audit_path
        else None
    )
    story_id = args.story_id or manuscript.story_id
    result = build_full_structure(
        synthesis.derived_plan,
        synthesis.ledger,
        architecture,
        review,
        manuscript,
        story_id,
        provider=QwenFullStructureCoordinator(),
        global_quality_audit=audit,
    )
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "FULL_ARTICLE_STRUCTURE.json").write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "FULL_ARTICLE_BODY.md").write_text(
        result.body_markdown,
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "result_id": result.result_id,
                "status": result.model_status,
                "sections": len(result.section_order),
                "chapter_argument_gaps": len(result.chapter_argument_gaps),
                "structure_gaps": len(result.structure_gaps),
                "rhetorical_edits": len(result.rhetorical_edits),
                "validation_errors": result.validation_errors,
                "warnings": result.warnings,
                "global_quality_audit_id": result.source_global_quality_audit_id,
                "global_quality_audit_status": result.global_quality_audit_status,
                "unhandled_global_quality_findings": len(
                    result.unhandled_global_quality_finding_ids
                ),
                "out_of_scope_global_quality_findings": len(
                    result.out_of_scope_global_quality_finding_ids
                ),
                "usage": result.usage,
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
