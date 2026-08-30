"""Run one normalized TMM Harness task or a frozen development fixture."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optomind_optics.harness import (  # noqa: E402
    OpticalDesignTask,
    TMMHarnessConfig,
    TMMHarnessOrchestrator,
)
from optomind_optics.harness.dev_fixtures import build_dev_optical_design_task  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--task", type=Path, help="OpticalDesignTask JSON")
    source.add_argument("--dev-benchmark", choices=[f"DEV0{i}" for i in range(1, 6)])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--disable-global-optimizer", action="store_true")
    parser.add_argument("--use-qwen-policy", action="store_true")
    parser.add_argument("--qwen-force-mock", action="store_true")
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print a compact run summary instead of the full result payload.",
    )
    args = parser.parse_args()

    if args.task is not None:
        # ``utf-8-sig`` accepts ordinary UTF-8 as well as Windows-authored JSON
        # carrying a BOM.  The task contract remains byte-for-byte validated
        # after decoding; this only prevents a platform encoding artifact from
        # masquerading as invalid scientific input.
        task = OpticalDesignTask.model_validate_json(
            args.task.read_text(encoding="utf-8-sig")
        )
    else:
        task = build_dev_optical_design_task(str(args.dev_benchmark))
    config = TMMHarnessConfig(
        enable_global_optimizer=not args.disable_global_optimizer,
        use_qwen_policy=bool(args.use_qwen_policy),
        qwen_force_mock=True if args.qwen_force_mock else None,
    )
    result = TMMHarnessOrchestrator(
        args.output_dir,
        run_id=args.run_id,
        resume=bool(args.resume),
        config=config,
    ).run(task)
    payload = result.model_dump(mode="json")
    if args.summary_only:
        payload = {
            "run_id": result.run_id,
            "task_id": result.task_id,
            "status": result.status,
            "state_stage": result.state_stage,
            "wall_seconds": result.wall_seconds,
            "experiment_count": len(result.experiment_results),
            "physically_valid_candidate_count": sum(
                int(item.get("physically_valid_candidate_count", 0))
                for item in result.experiment_results
            ),
            "budget_usage": result.budget.get("usage", {}),
            "qwen_call_count": len(result.qwen_usage),
            "stop_decision": result.stop_decision,
        }
    print(json.dumps(payload, indent=2))
    return 0 if result.status.startswith("completed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
