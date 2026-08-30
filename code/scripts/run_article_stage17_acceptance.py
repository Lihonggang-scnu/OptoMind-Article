#!/usr/bin/env python3
"""Generate the machine-readable Stage 17 Article acceptance report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optomind_optics.harness.article_stage17_acceptance import audit_stage17_acceptance


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Article Stage 17 acceptance artifacts.")
    parser.add_argument("--manifest-path", type=Path, required=True)
    parser.add_argument("--output-path", type=Path, required=True)
    args = parser.parse_args()
    manifest_path = args.manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts = dict(manifest.get("artifacts") or manifest)
    for key, value in list(artifacts.items()):
        text = str(value or "").strip()
        if text and not Path(text).is_absolute():
            artifacts[key] = str((manifest_path.parent / text).resolve())
    result = audit_stage17_acceptance(artifacts)
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    args.output_path.write_text(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report_id": result.report_id, "status": result.status, "rows": len(result.rows), "blockers": result.blockers, "warnings": result.warnings}, ensure_ascii=True, indent=2))
    return 0 if result.status == "accepted" else 2 if result.status == "partial" else 1


if __name__ == "__main__":
    raise SystemExit(main())
