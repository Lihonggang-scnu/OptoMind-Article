"""Audit TMM Harness readiness without opening the sealed holdout task file."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optomind_research.runtime.artifact_store import atomic_write_json  # noqa: E402
from optomind_optics.harness.benchmarks import holdout_access_events  # noqa: E402
from optomind_optics.harness.provenance import ArtifactLineageStore  # noqa: E402
from optomind_optics.harness.qwen_policy import QWEN_POLICY_MODEL  # noqa: E402


DEFAULT_DEV = ROOT / "outputs" / "tmm_harness_dev_acceptance_v5" / "DEV_ACCEPTANCE.json"
DEFAULT_COMPILER = ROOT / "outputs" / "tmm_task_compiler_real_dev01_20260809" / "SMOKE_ACCEPTANCE.json"
DEFAULT_RUNTIME = ROOT / "outputs" / "tmm_harness_runtime_fingerprint_smoke_20260809"


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _lineage_verified(run_dir: Path) -> bool:
    try:
        ArtifactLineageStore(run_dir, resume=True).verify_all()
        return True
    except Exception:
        return False


def _holdout_access_protocol_valid(
    events: list[dict[str, Any]],
    holdout_report: dict[str, Any] | None,
) -> bool:
    """Validate the sealed-split access policy before or after one blind run."""

    if holdout_report is None:
        return len(events) == 0
    holdout_id = str(holdout_report.get("holdout_id") or "")
    if not holdout_id or len(events) < 2 or len(events) % 2:
        return False
    completed_ids: list[str] = []
    for index in range(0, len(events), 2):
        started, completed = events[index : index + 2]
        pair_id = str(started.get("requested_holdout_id") or "")
        if (
            not pair_id
            or str(started.get("event") or "") != "holdout_read_started"
            or str(completed.get("event") or "") != "holdout_read_completed"
            or str(completed.get("requested_holdout_id") or "") != pair_id
            or started.get("process_id") != completed.get("process_id")
            or not completed.get("file_sha256")
        ):
            return False
        completed_ids.append(pair_id)
    if len(completed_ids) != len(set(completed_ids)):
        return False
    return (
        completed_ids[-1] == holdout_id
        and int(holdout_report.get("holdout_audit_event_count_for_selected_id") or 0)
        == 2
        and bool((holdout_report.get("checks") or {}).get("holdout_access_audited"))
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev-acceptance", type=Path, default=DEFAULT_DEV)
    parser.add_argument("--compiler-smoke", type=Path, default=DEFAULT_COMPILER)
    parser.add_argument("--runtime-smoke", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--holdout-acceptance", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    dev = _read(args.dev_acceptance.resolve())
    compiler = _read(args.compiler_smoke.resolve())
    runtime_root = args.runtime_smoke.resolve()
    runtime_lock = _read(runtime_root / "RUNTIME_LOCK.json")
    replay = _read(runtime_root / "REPLAY_MANIFEST.json")
    audit_events = holdout_access_events()
    holdout_report: dict[str, Any] | None = None
    if args.holdout_acceptance is not None and args.holdout_acceptance.exists():
        holdout_report = _read(args.holdout_acceptance.resolve())
    checks: dict[str, bool] = {
        "five_development_tasks_passed": bool(dev.get("passed"))
        and len(dev.get("runs") or []) == 5,
        "all_development_replays_passed": all(
            bool((row.get("fresh_replay") or {}).get("success"))
            for row in dev.get("runs") or []
        ),
        "performance_targets_are_soft": dev.get("performance_targets_used_as_gates") is False,
        "diversity_is_optional": dev.get("diversity_required") is False,
        "deterministic_development_requires_no_qwen": int(dev.get("qwen_calls") or 0) == 0,
        "real_natural_language_compiler_passed": compiler.get("compilation_status") == "compiled"
        and compiler.get("harness_status") == "completed"
        and int(compiler.get("valid_candidates") or 0) > 0,
        "qwen_model_is_flash_only": QWEN_POLICY_MODEL == "qwen3.7-flash",
        "runtime_source_fingerprint_present": len(
            str((runtime_lock.get("runtime") or {}).get("source_tree_sha256") or "")
        )
        == 64,
        "runtime_dependency_versions_present": bool(
            (runtime_lock.get("runtime") or {}).get("dependency_versions")
        ),
        "runtime_smoke_replayed": bool(replay.get("success")),
        "runtime_smoke_lineage_verified": _lineage_verified(runtime_root),
        "blind_runner_exists": (ROOT / "scripts" / "run_tmm_harness_holdout_acceptance.py").is_file(),
    }
    engineering_ready = all(checks.values())
    holdout_access_protocol_valid = _holdout_access_protocol_valid(
        audit_events,
        holdout_report,
    )
    checks["holdout_access_protocol_valid"] = holdout_access_protocol_valid
    preholdout_ready = engineering_ready and holdout_access_protocol_valid
    final_holdout_passed = bool(
        holdout_report
        and holdout_report.get("passed")
        and (holdout_report.get("checks") or {}).get(
            "benchmark_semantics_delivered",
            False,
        )
    )
    goal_complete = engineering_ready and holdout_access_protocol_valid and final_holdout_passed
    report = {
        "schema_version": "tmm-harness-completion-audit.v1",
        "scope": "TMM_only",
        "allowed_qwen_model": QWEN_POLICY_MODEL,
        "checks": checks,
        "engineering_ready": engineering_ready,
        "preholdout_ready": preholdout_ready,
        "final_holdout_passed": final_holdout_passed,
        "goal_complete": goal_complete,
        "holdout_access_event_count": len(audit_events),
        "pending": []
        if final_holdout_passed
        else [
            "After validating the generic repair, the user selects one unused holdout ID; "
            "a previously executed holdout must never be reused."
        ],
        "evidence": {
            "development_acceptance": str(args.dev_acceptance.resolve()),
            "compiler_smoke": str(args.compiler_smoke.resolve()),
            "runtime_smoke": str(runtime_root),
            "holdout_acceptance": None
            if args.holdout_acceptance is None
            else str(args.holdout_acceptance.resolve()),
        },
    }
    atomic_write_json(args.output.resolve(), report)
    print(json.dumps(report, indent=2))
    return 0 if (goal_complete if holdout_report is not None else preholdout_ready) else 2


if __name__ == "__main__":
    raise SystemExit(main())
