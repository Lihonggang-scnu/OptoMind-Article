"""Audit whether one completed TMM run delivered its declared benchmark semantics."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optomind_research.runtime.artifact_store import atomic_write_json  # noqa: E402
from optomind_optics.harness.benchmark_delivery import audit_benchmark_delivery  # noqa: E402
from optomind_optics.harness.benchmarks import BenchmarkTask  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    benchmark = BenchmarkTask.model_validate_json(
        args.benchmark.read_text(encoding="utf-8")
    )
    task = json.loads(args.task.read_text(encoding="utf-8"))
    if not isinstance(task, dict):
        raise ValueError("task JSON must contain an object")
    audit = audit_benchmark_delivery(benchmark, task, args.run_dir)
    atomic_write_json(args.output.resolve(), audit.model_dump(mode="json"))
    print(json.dumps(audit.model_dump(mode="json"), indent=2))
    return 0 if audit.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
