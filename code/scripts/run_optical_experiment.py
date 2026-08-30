"""Run or replay one normalized deterministic optical simulation task."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optomind_optics.harness import OpticalExperimentRuntime
from tmm_engine.task_io import load_task


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()
    mode, task = load_task(args.task)
    if mode != "simulate":
        raise SystemExit("The deterministic kernel currently accepts simulate tasks only.")
    result = OpticalExperimentRuntime(args.output_dir, run_id=args.run_id).run_simulation(task)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] in {"physically_valid", "physically_valid_with_limits", "needs_higher_fidelity"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
