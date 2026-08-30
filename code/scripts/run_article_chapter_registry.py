#!/usr/bin/env python3
"""Build a typed Article chapter registry from a JSON manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optomind_optics.harness.article_chapter_registry import build_chapter_registry


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an Article chapter asset registry.")
    parser.add_argument("--manifest-path", type=Path, required=True)
    parser.add_argument("--output-path", type=Path, required=True)
    args = parser.parse_args()
    manifest_path = args.manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    base_dir = manifest_path.parent
    normalized_chapters = []
    for raw in manifest.get("chapters") or []:
        item = dict(raw)
        for field in (
            "manuscript_path",
            "review_path",
            "reproducibility_path",
            "presentation_path",
            "delivery_path",
            "global_audit_path",
        ):
            value = str(item.get(field) or "").strip()
            if value and not Path(value).is_absolute():
                item[field] = str((base_dir / value).resolve())
        normalized_chapters.append(item)
    result = build_chapter_registry(
        normalized_chapters,
        expected_chapter_count=int(manifest.get("expected_chapter_count") or 8),
    )
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    args.output_path.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "registry_id": result.registry_id,
                "status": result.status,
                "registered": result.registered_chapter_count,
                "complete": result.complete_chapter_count,
                "missing": result.missing_chapter_count,
                "warnings": result.warnings,
                "validation_errors": result.validation_errors,
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 1 if result.status == "invalid" else 0


if __name__ == "__main__":
    raise SystemExit(main())
