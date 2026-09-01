"""Small deterministic runner used only by research-console integration tests."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--question-file", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-id", required=True)
    args, _ = parser.parse_known_args()
    output = Path(args.output_dir)
    question = Path(args.question_file).read_text(encoding="utf-8").strip()
    event_path = output / "RESEARCH_EVENTS.jsonl"

    events = [
        {"sequence": 1, "elapsed_seconds": 0.02, "event_type": "request_received"},
        {"sequence": 2, "elapsed_seconds": 0.12, "event_type": "problem_analyzed", "status": "analyzed"},
        {"sequence": 3, "elapsed_seconds": 0.22, "event_type": "scoring_standard_fixed", "metric_count": 1},
        {"sequence": 4, "elapsed_seconds": 0.32, "event_type": "strategy_planned", "normal_route_count": 1, "control_route_count": 0},
        {"sequence": 5, "elapsed_seconds": 0.42, "event_type": "route_started", "route_id": "route_01", "iteration_id": "iteration_01"},
        {"sequence": 6, "elapsed_seconds": 0.52, "event_type": "route_completed", "route_id": "route_01", "run_status": "completed", "valid_candidates": 1},
        {"sequence": 7, "elapsed_seconds": 0.62, "event_type": "research_finished", "status": "completed"},
    ]
    with event_path.open("w", encoding="utf-8", buffering=1) as stream:
        for event in events:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            time.sleep(0.12)

    iteration = output / "iterations" / "iteration_01"
    iteration.mkdir(parents=True)
    write_json(output / "REQUEST.json", {"question": question})
    write_json(output / "PROBLEM_ANALYSIS.json", {"analysis": {"compatibility": "compatible"}})
    write_json(
        output / "SCORING_STANDARD.json",
        {
            "standard": {
                "locked": True,
                "formula": "mean_transmittance_800_1500nm",
                "metrics": [
                    {
                        "variable": "mean_transmittance_800_1500nm",
                        "metric": "mean_transmittance",
                        "sense": "maximize",
                        "region": {"wavelength_nm": [800, 1500]},
                    }
                ],
            }
        },
    )
    write_json(output / "ITERATION_HISTORY.json", [])
    write_json(output / "SCORING_RANKING.json", {"leaderboard": [], "winner": None})
    write_json(output / "TOURNAMENT_SUMMARY.json", {})
    write_json(
        output / "RESEARCH_RESULT.json",
        {
            "run_id": args.run_id,
            "question": question,
            "status": "completed",
            "stage": "finished",
            "telemetry": {"qwen_calls": 1, "forward_evaluations": 10, "wall_seconds": 0.8},
        },
    )
    (output / "FINAL_ANSWER.md").write_text("# 测试运行完成\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
