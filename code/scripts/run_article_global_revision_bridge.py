#!/usr/bin/env python3
"""Bridge a global revision plan into a forced Article review checkpoint."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optomind_optics.harness.article_global_revision_bridge import (
    build_global_revision_bridge,
)
from optomind_optics.harness.article_global_revision_plan import (
    GlobalRevisionPlanResult,
)
from optomind_optics.harness.article_review import ArticleReviewResult


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bridge global revision actions without editing the manuscript."
    )
    parser.add_argument("--plan-path", type=Path, required=True)
    parser.add_argument("--review-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-actions", type=int, default=None)
    args = parser.parse_args()
    plan = GlobalRevisionPlanResult.model_validate(_read(args.plan_path))
    review = ArticleReviewResult.model_validate(_read(args.review_path))
    result = build_global_revision_bridge(
        plan,
        review,
        max_actions=args.max_actions,
    )
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "FORCED_GLOBAL_REVIEW.json").write_text(
        json.dumps(result.forced_review.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "GLOBAL_REVISION_BRIDGE.json").write_text(
        json.dumps(result.model_dump(exclude={"forced_review"}, mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "bridge_id": result.bridge_id,
                "executable_actions": len(result.executable_action_ids),
                "forced_findings": len(result.forced_review.scientific_findings),
                "skipped_actions": len(result.skipped_action_ids),
                "warnings": result.warnings,
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
