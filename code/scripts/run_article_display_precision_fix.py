#!/usr/bin/env python3
"""Apply deterministic public-number formatting to a manuscript package."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optomind_optics.harness.article_display_precision import apply_display_precision_fix
from optomind_optics.harness.article_global_quality_audit import GlobalQualityAuditReport
from optomind_optics.harness.article_manuscript import ArticleManuscriptPackage


def main() -> int:
    parser = argparse.ArgumentParser(description="Format public numeric precision without changing source facts.")
    parser.add_argument("--manuscript-path", type=Path, required=True)
    parser.add_argument("--audit-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    manuscript = ArticleManuscriptPackage.model_validate(json.loads(args.manuscript_path.read_text(encoding="utf-8")))
    audit = GlobalQualityAuditReport.model_validate(json.loads(args.audit_path.read_text(encoding="utf-8")))
    result = apply_display_precision_fix(manuscript, audit)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "ARTICLE_MANUSCRIPT_PACKAGE.json").write_text(
        json.dumps(result.package.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "DISPLAY_PRECISION_FIX.json").write_text(
        json.dumps(result.model_dump(exclude={"package"}, mode="json"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result.model_dump(exclude={"package"}, mode="json"), ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
