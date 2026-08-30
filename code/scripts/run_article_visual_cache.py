#!/usr/bin/env python3
"""Materialize the visual cache index from a visual planning checkpoint."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optomind_optics.harness.article_visual_cache import (
    build_visual_cache_index,
    materialize_conceptual_gaps,
    write_visual_cache_index,
)
from optomind_optics.harness.article_visual_planner import VisualPlanResult


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a durable Article visual cache index."
    )
    parser.add_argument("--visual-plan-path", type=Path, required=True)
    parser.add_argument("--output-path", type=Path, required=True)
    parser.add_argument("--conceptual-output-dir", type=Path)
    args = parser.parse_args()
    plan = VisualPlanResult.model_validate(
        json.loads(args.visual_plan_path.read_text(encoding="utf-8"))
    )
    conceptual_records = []
    if args.conceptual_output_dir is not None:
        conceptual_records = materialize_conceptual_gaps(
            plan, args.conceptual_output_dir
        )
        plan = plan.model_copy(
            update={"cache_records": [*plan.cache_records, *conceptual_records]}
        )
    index = build_visual_cache_index(
        plan,
        available_paths={
            record.asset_id: record.asset_path for record in conceptual_records
        },
        permission_states={
            record.asset_id: record.permission_state for record in conceptual_records
        },
    )
    write_visual_cache_index(index, args.output_path)
    print(
        json.dumps(
            {
                "index_id": index.index_id,
                "entries": len(index.entries),
                "available": sum(item.status == "available" for item in index.entries),
                "planned": sum(item.status == "planned" for item in index.entries),
                "conceptual_generated": len(conceptual_records),
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
