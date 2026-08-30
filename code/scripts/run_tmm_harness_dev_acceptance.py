"""Run the five frozen development tasks and write a compact acceptance report.

This script deliberately cannot load HOLDOUT06--HOLDOUT10.  Its scientific
checks are broad implementation sanity checks, not target-performance gates in
the Harness itself.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optomind_research.runtime.artifact_store import atomic_write_json  # noqa: E402
from optomind_optics.harness.dev_fixtures import (  # noqa: E402
    build_dev_optical_design_task,
)
from optomind_optics.harness.benchmarks import (  # noqa: E402
    holdout_access_events,
    load_benchmark_task,
)
from optomind_optics.harness.benchmark_delivery import (  # noqa: E402
    audit_benchmark_delivery,
)
from optomind_optics.harness.orchestrator import (  # noqa: E402
    TMMHarnessConfig,
    TMMHarnessOrchestrator,
)
from optomind_optics.harness.replay import replay_completed_run  # noqa: E402


DEV_IDS = tuple(f"DEV0{index}" for index in range(1, 6))


def _channels(run_dir: Path, experiment_id: str) -> dict[str, Any]:
    path = run_dir / "experiments" / experiment_id / "baseline" / "SIMULATION_RESULT.json"
    return json.loads(path.read_text(encoding="utf-8"))["channels"]


def _portfolio_scores(run_dir: Path, result: Any) -> tuple[float, float]:
    experiment_id = result.experiment_results[0]["experiment_id"]
    candidates = result.experiment_results[0]["portfolio"]["candidates"]
    baseline_report = json.loads(
        (
            run_dir
            / "experiments"
            / experiment_id
            / "baseline"
            / "OBJECTIVE_REPORT.json"
        ).read_text(encoding="utf-8")
    )
    best = max(float(item.get("target_score") or 0.0) for item in candidates)
    return float(baseline_report.get("aggregate_soft_score") or 0.0), best


def _scientific_sanity(benchmark_id: str, run_dir: Path, result: Any) -> list[dict[str, Any]]:
    experiment_id = result.experiment_results[0]["experiment_id"]
    checks: list[dict[str, Any]] = []
    if benchmark_id in {"DEV01", "DEV04"}:
        baseline, best = _portfolio_scores(run_dir, result)
        checks.append(
            {
                "name": "optimizer_does_not_degrade_best_soft_score",
                "passed": best + 1e-12 >= baseline,
                "observed": {"baseline": baseline, "best": best},
            }
        )
    if benchmark_id == "DEV02":
        maximum_r = max(
            float(np.max(np.asarray(channel["R"], dtype=np.float64)))
            for channel in _channels(run_dir, experiment_id).values()
        )
        checks.append(
            {"name": "dbr_stopband_is_visible", "passed": maximum_r > 0.90, "observed": maximum_r}
        )
    if benchmark_id == "DEV03":
        maximum_t = max(
            float(np.max(np.asarray(channel["T"], dtype=np.float64)))
            for channel in _channels(run_dir, experiment_id).values()
        )
        checks.append(
            {"name": "defect_transmission_mode_is_visible", "passed": maximum_t > 0.50, "observed": maximum_t}
        )
    if benchmark_id == "DEV05":
        certificate = json.loads(
            (
                run_dir
                / "experiments"
                / experiment_id
                / "baseline"
                / "PHYSICS_ACCEPTANCE_CERTIFICATE.json"
            ).read_text(encoding="utf-8")
        )
        checks.append(
            {
                "name": "mixed_coherence_limit_is_explicit",
                "passed": certificate.get("status") == "physically_valid_with_limits",
                "observed": certificate.get("status"),
            }
        )
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument(
        "--fresh-replay",
        action="store_true",
        help="Recompute every development task in an isolated fresh run.",
    )
    args = parser.parse_args()
    root = args.output_dir.resolve()
    if root.exists() and args.replace:
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    rows = []
    for benchmark_id in DEV_IDS:
        run_dir = root / benchmark_id
        task = build_dev_optical_design_task(benchmark_id)
        result = TMMHarnessOrchestrator(
            run_dir,
            run_id=f"accept_{benchmark_id.lower()}",
            config=TMMHarnessConfig(
                enable_global_optimizer=True,
                use_qwen_policy=False,
            ),
        ).run(task)
        delivery_audit = audit_benchmark_delivery(
            load_benchmark_task(benchmark_id),
            task,
            run_dir,
        )
        atomic_write_json(
            run_dir / "BENCHMARK_DELIVERY_AUDIT.json",
            delivery_audit.model_dump(mode="json"),
        )
        replay_manifest = (
            replay_completed_run(run_dir)
            if args.fresh_replay
            else None
        )
        checks = [
            {
                "name": "verified_portfolio_exists",
                "passed": bool(result.experiment_results)
                and result.experiment_results[0]["physically_valid_candidate_count"] > 0,
            },
            {
                "name": "no_performance_target_was_an_admission_gate",
                "passed": all(
                    "Target attainment affects ranking only" in note
                    for item in result.experiment_results
                    for note in item["portfolio"]["notes"][:1]
                ),
            },
            {
                "name": "qwen_was_not_needed_for_deterministic_physics",
                "passed": len(result.qwen_usage) == 0,
            },
            {
                "name": "benchmark_semantics_delivered",
                "passed": delivery_audit.passed,
                "observed": {
                    "passed_checks": sum(item.passed for item in delivery_audit.checks),
                    "total_checks": len(delivery_audit.checks),
                },
            },
            *(
                [
                    {
                        "name": "fresh_process_scientific_replay",
                        "passed": bool(replay_manifest.success),
                        "observed": {
                            "matched_artifacts": replay_manifest.matched_artifacts,
                            "total_artifacts": replay_manifest.total_artifacts,
                        },
                    }
                ]
                if replay_manifest is not None
                else []
            ),
            *_scientific_sanity(benchmark_id, run_dir, result),
        ]
        rows.append(
            {
                "benchmark_id": benchmark_id,
                "status": result.status,
                "wall_seconds": result.wall_seconds,
                "budget_usage": result.budget.get("usage", {}),
                "valid_candidates": sum(
                    int(item["physically_valid_candidate_count"])
                    for item in result.experiment_results
                ),
                "selected_roles": {
                    item["experiment_id"]: item["portfolio"]["selected_roles"]
                    for item in result.experiment_results
                },
                "fresh_replay": (
                    None
                    if replay_manifest is None
                    else replay_manifest.model_dump(mode="json")
                ),
                "checks": checks,
                "passed": result.status.startswith("completed")
                and all(check["passed"] for check in checks),
            }
        )
    holdout_events = holdout_access_events()
    report = {
        "schema_version": "tmm-harness-dev-acceptance.v1",
        "scope": list(DEV_IDS),
        "holdout_accessed": bool(holdout_events),
        "holdout_access_event_count": len(holdout_events),
        "holdout_access_claim_scope": "since_audit_enforcement",
        "holdout_executed": False,
        "holdout_content_used_for_tuning": False,
        "legacy_access_note": (
            "Before access auditing was installed, schema tests mechanically parsed "
            "the holdout JSON without executing tasks or exposing outcomes to tuning."
        ),
        "qwen_policy_model_allowed": "qwen3.7-flash",
        "qwen_calls": 0,
        "fresh_replay_enabled": bool(args.fresh_replay),
        "performance_targets_used_as_gates": False,
        "diversity_required": False,
        "wall_seconds": time.perf_counter() - started,
        "runs": rows,
        "passed": all(item["passed"] for item in rows),
    }
    atomic_write_json(root / "DEV_ACCEPTANCE.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
