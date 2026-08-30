#!/usr/bin/env python3
"""Compare a revised Article review with its immutable baseline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optomind_optics.harness.article_revision_arbitration import arbitrate_article_revision
from optomind_optics.harness.article_review import ArticleReviewResult


def main() -> int:
    parser = argparse.ArgumentParser(description="Arbitrate a revised Article review against a baseline.")
    parser.add_argument("--baseline-review-path", type=Path, required=True)
    parser.add_argument("--candidate-review-path", type=Path, required=True)
    parser.add_argument("--output-path", type=Path, required=True)
    args = parser.parse_args()
    baseline = ArticleReviewResult.model_validate(json.loads(args.baseline_review_path.read_text(encoding="utf-8")))
    candidate = ArticleReviewResult.model_validate(json.loads(args.candidate_review_path.read_text(encoding="utf-8")))
    result = arbitrate_article_revision(baseline, candidate)
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    args.output_path.write_text(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
