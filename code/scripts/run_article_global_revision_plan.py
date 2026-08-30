#!/usr/bin/env python3
"""Create a global Article revision handoff without rewriting manuscript prose."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optomind_optics.harness.article_full_structure import FullStructureResult
from optomind_optics.harness.article_global_quality_audit import (
    GlobalQualityAuditReport,
)
from optomind_optics.harness.article_global_revision_plan import (
    QwenGlobalRevisionPlanner,
    build_global_revision_plan,
)
from optomind_optics.harness.article_review import ArticleReviewResult


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Plan bounded whole-Article revisions without editing source-bound prose."
    )
    parser.add_argument("--full-structure-path", type=Path, required=True)
    parser.add_argument("--audit-path", type=Path, required=True)
    parser.add_argument("--review-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    full = FullStructureResult.model_validate(_read(args.full_structure_path))
    audit = GlobalQualityAuditReport.model_validate(_read(args.audit_path))
    review = ArticleReviewResult.model_validate(_read(args.review_path))
    result = build_global_revision_plan(
        full,
        audit,
        review,
        provider=QwenGlobalRevisionPlanner(),
    )
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "GLOBAL_REVISION_PLAN.json").write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "plan_id": result.plan_id,
                "status": result.model_status,
                "actions": len(result.actions),
                "unhandled": len(result.unhandled_finding_ids),
                "out_of_scope": len(result.out_of_scope_finding_ids),
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
