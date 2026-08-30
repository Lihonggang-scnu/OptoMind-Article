#!/usr/bin/env python3
"""Ingest trusted local Presentation panels into the visual cache."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optomind_optics.harness.article_visual_cache import (
    extend_visual_cache_index,
    ingest_presentation_visuals,
    write_visual_cache_index,
)
from optomind_optics.harness.article_visual_cache import VisualCacheIndex


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingest trusted Presentation panels into visual cache."
    )
    parser.add_argument("--cache-path", type=Path, required=True)
    parser.add_argument("--presentation-package-path", type=Path, required=True)
    parser.add_argument("--presentation-dir", type=Path, required=True)
    parser.add_argument("--article-id", required=True)
    parser.add_argument("--output-path", type=Path, required=True)
    args = parser.parse_args()
    cache = VisualCacheIndex.model_validate(
        json.loads(args.cache_path.read_text(encoding="utf-8"))
    )
    package = json.loads(args.presentation_package_path.read_text(encoding="utf-8"))
    records = ingest_presentation_visuals(
        package,
        args.presentation_dir.resolve(),
        article_id=args.article_id,
    )
    index = extend_visual_cache_index(cache, records)
    write_visual_cache_index(index, args.output_path)
    print(
        json.dumps(
            {
                "index_id": index.index_id,
                "ingested": len(records),
                "available": sum(
                    entry.status == "available" for entry in index.entries
                ),
                "planned": sum(entry.status == "planned" for entry in index.entries),
                "warnings": index.warnings,
                "validation_errors": index.validation_errors,
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
