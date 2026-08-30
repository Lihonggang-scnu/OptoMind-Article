"""Compile one natural-language optical question into the immutable TMM protocol."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optomind_research.runtime.artifact_store import atomic_write_json  # noqa: E402
from optomind_research.runtime.cost_ledger import estimate_call_cost_cny  # noqa: E402
from optomind_optics.harness.task_compiler import QwenTMMTaskCompiler  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--force-mock", action="store_true")
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError("compiler output directory must be new or empty")
    output_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    result = QwenTMMTaskCompiler().compile(
        args.question,
        force_mock=True if args.force_mock else None,
    )
    payload = result.model_dump(mode="json")
    atomic_write_json(output_dir / "TASK_COMPILATION.json", payload)
    if result.task is not None:
        atomic_write_json(
            output_dir / "COMPILED_TASK.json",
            result.task.model_dump(mode="json"),
        )
    input_tokens = sum(
        int(row.get("estimated_input_tokens") or row.get("input_tokens") or 0)
        for row in result.usage
    )
    output_tokens = sum(
        int(row.get("estimated_output_tokens") or row.get("output_tokens") or 0)
        for row in result.usage
    )
    cost = sum(
        estimate_call_cost_cny(
            str(row.get("model_name") or "unknown"),
            int(row.get("estimated_input_tokens") or row.get("input_tokens") or 0),
            int(row.get("estimated_output_tokens") or row.get("output_tokens") or 0),
        )
        for row in result.usage
    )
    summary = {
        "schema_version": "tmm-task-compiler-run.v1",
        "status": result.status,
        "attempts": result.attempts,
        "model_name": result.model_name,
        "fallback_used": any(bool(row.get("fallback_used")) for row in result.usage),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost_cny": cost,
        "wall_seconds": time.perf_counter() - started,
        "task_written": result.task is not None,
        "validation_errors": list(result.validation_errors),
    }
    atomic_write_json(output_dir / "COMPILER_RUN_SUMMARY.json", summary)
    print(json.dumps(summary, indent=2))
    return 0 if result.status == "compiled" else 2


if __name__ == "__main__":
    raise SystemExit(main())
