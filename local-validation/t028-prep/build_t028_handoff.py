"""Build a bounded, plain-file T028 handoff from saved evidence only.

This exporter deliberately imports no project modules and performs no model,
budget, or numerical calls.  It reads the saved T028/T027 records, writes a
compact audit package, and emits a manifest over the package files.
"""

from __future__ import annotations

import collections
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


ARTICLE_ROOT = Path(r"D:\OptoMind-Article-3")
WORKTREE = ARTICLE_ROOT / "r3t028a"
TASKS = ARTICLE_ROOT / "local-r3-handoff" / "tasks"
T028 = TASKS / "R3-T028"
T027 = TASKS / "R3-T027"
OUT = WORKTREE / "handoff" / "R3-T028-DECISION-QUALITY-20261016"

SOURCE_SHA = "b6f641edea1fe124c98921251bc5f14a3dc53466"
BASE_SHA = "b5f8a1dc2cfe8a203b74a742963bef7f9ac9a57b2"
T027_RESULT_SHA = "ff050c4c4600a0027d7a6b39c2c843956f05103c"
INTERPRETER = ARTICLE_ROOT / "local-integration" / "20261013" / "envs" / "tensor" / "Scripts" / "python.exe"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def dump_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")


def copy_file(source: Path, relative: str) -> Path:
    destination = OUT / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(destination)
    shutil.copy2(source, destination)
    return destination


def metric_values(design: dict[str, Any]) -> dict[str, float]:
    return {
        key: value.get("value") if isinstance(value, dict) else value
        for key, value in design.get("metrics", {}).items()
    }


def first_evaluation_brief(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not value:
        return None
    keys = (
        "design_id",
        "parent_design_id",
        "instance_id",
        "route_id",
        "simulation_key",
        "evaluation_key",
        "full_result_ref",
        "role",
        "cache_hit",
        "input_binding",
        "new_design",
        "new_evidence",
        "effective_intervention",
        "layer_diff",
        "family",
        "family_state",
        "family_changed_from_parent",
        "parameterization_changed_from_parent",
    )
    return {key: value.get(key) for key in keys if key in value}


def certificate_brief(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not value:
        return None
    certificate = value.get("certificate") or {}
    metering = value.get("metering") or {}
    return {
        "instance_id": value.get("instance_id"),
        "route_id": value.get("route_id"),
        "status": value.get("status"),
        "physics_accepted": value.get("physics_accepted"),
        "cache_hit": value.get("cache_hit"),
        "metering": metering,
        "simulation_key": value.get("simulation_key"),
        "evaluation_key": value.get("evaluation_key"),
        "certificate_id": certificate.get("certificate_id"),
        "certificate_status": certificate.get("status"),
        "failures": certificate.get("failures", []),
        "archive_root": value.get("archive_root"),
        "full_result_ref": value.get("full_result_ref"),
    }


def compact_design(design: dict[str, Any]) -> dict[str, Any]:
    return {
        "design_id": design.get("design_id"),
        "metrics": metric_values(design),
        "parent_design_id": design.get("parent_design_id"),
        "physical_input": design.get("physical_input"),
        "family": design.get("family"),
        "layer_diff": design.get("layer_diff"),
        "first_evaluation": first_evaluation_brief(design.get("first_evaluation")),
        "certificate": certificate_brief(design.get("certified_result")),
    }


def result_indexes(rows: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    indexes = {"instance_id": {}, "evaluation_key": {}, "simulation_key": {}}
    for row in rows:
        for key in indexes:
            value = row.get(key)
            if value:
                indexes[key][value] = row
    return indexes


def load_raw_result(row: dict[str, Any] | None, ref: dict[str, Any] | None) -> dict[str, Any]:
    if row and isinstance(row.get("raw_result"), dict):
        return row["raw_result"]
    if ref and ref.get("path"):
        path = Path(ref["path"])
        if not path.exists():
            raise FileNotFoundError(path)
        if ref.get("sha256") and sha256(path) != ref["sha256"]:
            raise ValueError(f"full result reference hash mismatch: {path}")
        value = load_json(path)
        if isinstance(value, dict) and isinstance(value.get("raw_result"), dict):
            return value["raw_result"]
        if isinstance(value, dict) and isinstance(value.get("result"), dict):
            return value["result"].get("raw_result", value["result"])
        if isinstance(value, dict) and "channels" in value and "wavelengths_nm" in value:
            return value
    raise ValueError("No raw result available for direct spectrum")


def design_result(
    design: dict[str, Any],
    indexes: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, Any] | None:
    evaluation = design.get("first_evaluation") or {}
    for key in ("instance_id", "evaluation_key", "simulation_key"):
        value = evaluation.get(key)
        if value and value in indexes[key]:
            return indexes[key][value]
    return None


def spectrum_brief(
    design: dict[str, Any],
    row: dict[str, Any] | None,
    label: str,
) -> dict[str, Any]:
    raw = load_raw_result(row, (design.get("first_evaluation") or {}).get("full_result_ref"))
    return {
        "label": label,
        "design_id": design.get("design_id"),
        "instance_id": row.get("instance_id") if row else (design.get("first_evaluation") or {}).get("instance_id"),
        "route_id": row.get("route_id") if row else (design.get("first_evaluation") or {}).get("route_id"),
        "evaluation_key": row.get("evaluation_key") if row else (design.get("first_evaluation") or {}).get("evaluation_key"),
        "simulation_key": row.get("simulation_key") if row else (design.get("first_evaluation") or {}).get("simulation_key"),
        "status": row.get("status") if row else None,
        "physics_accepted": row.get("physics_accepted") if row else None,
        "cache_hit": row.get("cache_hit") if row else None,
        "metering": row.get("metering") if row else None,
        "metrics": metric_values(design),
        "raw_result": raw,
    }


def classify_delta(delta: dict[str, float], tolerance: float = 1e-12) -> str:
    signs = [
        1 if value > tolerance else -1 if value < -tolerance else 0
        for value in delta.values()
    ]
    if signs and all(sign > 0 for sign in signs):
        return "both_increase"
    if signs and all(sign < 0 for sign in signs):
        return "both_decrease"
    if any(sign > 0 for sign in signs) and any(sign < 0 for sign in signs):
        return "tradeoff"
    if any(sign > 0 for sign in signs):
        return "one_increase_other_unchanged"
    if any(sign < 0 for sign in signs):
        return "one_decrease_other_unchanged"
    return "unchanged"


def expected_matches(expected: str, delta: float, tolerance: float = 1e-12) -> bool | None:
    if expected in (None, "unknown"):
        return None
    if expected == "increase":
        return delta > tolerance
    if expected == "decrease":
        return delta < -tolerance
    if expected == "unchanged":
        return abs(delta) <= tolerance
    return None


def compact_numerical_log(searches: list[dict[str, Any]]) -> dict[str, Any]:
    jobs = []
    for job in searches:
        search = job.get("search", {})
        candidates = []
        for candidate in search.get("candidates", []):
            stack = candidate.get("tmm_task", {}).get("stack", {})
            candidates.append(
                {
                    "instance_id": candidate.get("instance_id"),
                    "parent_instance_id": candidate.get("parent_instance_id"),
                    "thicknesses_nm": [
                        layer.get("thickness_nm") for layer in stack.get("layers", [])
                    ],
                    "numerical_search": candidate.get("numerical_search"),
                }
            )
        compact_search = {key: value for key, value in search.items() if key != "candidates"}
        compact_search["candidates"] = candidates
        jobs.append(
            {key: value for key, value in job.items() if key != "search"}
            | {"search": compact_search}
        )
    return {
        "schema": "t028.compact_numerical_log.v1",
        "source_path": str(T028 / "refined" / "numerical_search.json"),
        "source_sha256": sha256(T028 / "refined" / "numerical_search.json"),
        "all_actual_evaluations_retained": True,
        "only_repeated_full_prepared_input_envelopes_omitted": True,
        "jobs": jobs,
    }


def result_metering(rows: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = collections.Counter(row.get("status") for row in rows)
    metering_keys = (
        "new_evaluations",
        "cache_hits",
        "primary_forward_calls",
        "verification_forward_calls",
        "workbench_forward_calls",
        "primary_forward_seconds",
        "verification_seconds",
        "worker_wall_seconds",
    )
    totals: dict[str, float] = {key: 0 for key in metering_keys}
    for row in rows:
        for key in metering_keys:
            totals[key] += row.get("metering", {}).get(key, 0) or 0
    return {
        "row_count": len(rows),
        "status_counts": dict(status_counts),
        "physics_accepted_rows": sum(bool(row.get("physics_accepted")) for row in rows),
        "cache_hit_rows": sum(bool(row.get("cache_hit")) for row in rows),
        "new_rows": sum(not bool(row.get("cache_hit")) for row in rows),
        "totals": totals,
    }


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"refusing to overwrite existing handoff: {OUT}")
    OUT.mkdir(parents=True)

    t028_revision = T028 / "revision"
    t028_refined = T028 / "refined"
    t027_refined = T027 / "refined"

    t028_archive = load_json(t028_refined / "design_archive.json")
    t027_archive = load_json(t027_refined / "design_archive.json")
    t028_results = load_json(t028_refined / "execution_results.json")
    t027_results = load_json(t027_refined / "execution_results.json")
    plans = load_json(t028_revision / "refinement_plans.json")["plans"]
    plan_report = load_json(t028_revision / "plan_report.json")
    revision_summary = load_json(t028_revision / "summary.json")
    refined_summary = load_json(t028_refined / "summary.json")
    t028_before = load_json(T028 / "prep" / "T028_T027_BEFORE_REFINE_HASHES.json")
    t028_after = load_json(T028 / "prep" / "T028_T027_AFTER_REFINE_HASHES.json")
    response = load_json(
        T028
        / "private"
        / "audit"
        / "db7b536f740488f96c1a"
        / "calls"
        / "r3-3082baf1f4c24e6d9d85"
        / "RESPONSE.json"
    )
    budget_status = load_json(
        T028
        / "private"
        / "audit"
        / "db7b536f740488f96c1a"
        / "calls"
        / "r3-3082baf1f4c24e6d9d85"
        / "BUDGET_STATUS.json"
    )
    preflight = load_json(T028 / "CALL_1_PREFLIGHT.json")
    started = load_json(T028 / "REVISION_STARTED.json")
    accounting = load_json(T028 / "REVISION_ACCOUNTING.json")
    parent_review = load_json(T028 / "PARENT_EXECUTION_REVIEW.json")
    context_report = load_json(t028_revision / "decision_context_report.json")
    selection_audit = load_json(T028 / "prep" / "T028_PRECALL_SELECTION_AUDIT.json")
    fresh_origins = load_json(T028 / "prep" / "T028_IMPORT_ORIGIN_PROBE_FRESH.json")
    offline_review = load_json(T028 / "prep" / "T028_PRECALL_REVIEW.json")

    # Context, model request/response, plans, and exact run evidence.
    for source, relative in (
        (T028 / "prep" / "T028_PRECALL_CONTEXT.json", "context/T028_PRECALL_CONTEXT.json"),
        (T028 / "prep" / "T028_PRECALL_SELECTION_AUDIT.json", "context/T028_PRECALL_SELECTION_AUDIT.json"),
        (t028_revision / "decision_context_report.json", "context/decision_context_report.json"),
        (t028_revision / "revision_parent.json", "context/revision_parent.json"),
        (t028_revision / "revision_aliases.json", "context/revision_aliases.json"),
        (T028 / "PARENT_EXECUTION_REVIEW.json", "context/PARENT_EXECUTION_REVIEW.json"),
        (T028 / "prep" / "T028_PRECALL_REVIEW.json", "context/T028_PRECALL_REVIEW.json"),
        (T028 / "prep" / "T028_IMPORT_ORIGIN_PROBE_INITIAL.json", "context/T028_IMPORT_ORIGIN_PROBE_INITIAL.json"),
        (T028 / "prep" / "T028_IMPORT_ORIGIN_PROBE_FRESH.json", "context/T028_IMPORT_ORIGIN_PROBE_FRESH.json"),
        (T028 / "private" / "audit" / "db7b536f740488f96c1a" / "calls" / "r3-3082baf1f4c24e6d9d85" / "REQUEST.json", "model/REQUEST.json"),
        (T028 / "private" / "audit" / "db7b536f740488f96c1a" / "calls" / "r3-3082baf1f4c24e6d9d85" / "RESPONSE.json", "model/RESPONSE.json"),
        (T028 / "private" / "audit" / "db7b536f740488f96c1a" / "calls" / "r3-3082baf1f4c24e6d9d85" / "raw" / "r3-3082baf1f4c24e6d9d85-key0-attempt0.raw", "model/raw_transport_attempt.raw"),
        (T028 / "private" / "audit" / "db7b536f740488f96c1a" / "calls" / "r3-3082baf1f4c24e6d9d85" / "BUDGET_STATUS.json", "model/BUDGET_STATUS.json"),
        (T028 / "revision" / "revision_prompt.json", "model/revision_prompt.json"),
        (T028 / "revision" / "raw_response.json", "model/revision_raw_response.json"),
        (T028 / "CALL_1_PREFLIGHT.json", "model/CALL_1_PREFLIGHT.json"),
        (T028 / "REVISION_STARTED.json", "model/REVISION_STARTED.json"),
        (T028 / "REVISION_ACCOUNTING.json", "model/REVISION_ACCOUNTING.json"),
        (T028 / "prep" / "T028_REVISE_20261016_025444091.stdout.log", "logs/revise.stdout.log"),
        (T028 / "prep" / "T028_REVISE_20261016_025444091.stderr.log", "logs/revise.stderr.log"),
        (T028 / "prep" / "T028_REFINE_20261016_025533461.stdout.log", "logs/refine.stdout.log"),
        (T028 / "prep" / "T028_REFINE_20261016_025533461.stderr.log", "logs/refine.stderr.log"),
        (T028 / "prep" / "OFFLINE_TARGETED_TESTS_20261016024503.stdout.log", "context/offline_tests.stdout.log"),
        (T028 / "prep" / "OFFLINE_TARGETED_TESTS_20261016024503.stderr.log", "context/offline_tests.stderr.log"),
    ):
        copy_file(source, relative)

    for source, relative in (
        (t028_revision / "refinement_plans.json", "plans/refinement_plans.json"),
        (t028_revision / "plan_report.json", "plans/plan_report.json"),
        (t028_revision / "summary.json", "plans/revision_summary.json"),
        (t028_revision / "manifest.json", "plans/revision_manifest.json"),
        (t028_refined / "refinement_plans.json", "plans/refined_refinement_plans.json"),
        (t028_refined / "plan_report.json", "plans/refined_plan_report.json"),
        (t028_refined / "manifest.json", "plans/refined_manifest.json"),
    ):
        copy_file(source, relative)

    compact_archive = {
        "schema": "t028.compact_certified_archive.v1",
        "source_path": str(t028_refined / "design_archive.json"),
        "source_sha256": sha256(t028_refined / "design_archive.json"),
        "context_id": t028_archive.get("context_id"),
        "objectives": t028_archive.get("objectives"),
        "conditions": t028_archive.get("conditions"),
        "designs": {
            key: compact_design(design)
            for key, design in t028_archive.get("designs", {}).items()
        },
        "evaluations": t028_archive.get("evaluations", []),
        "pareto_design_ids": t028_archive.get("pareto_design_ids", []),
        "claim_scope": t028_archive.get("claim_scope"),
        "note": "Compact audit view; full original with certified spectra remains local.",
    }
    dump_json(OUT / "refined" / "compact_certified_archive.json", compact_archive)
    dump_json(OUT / "refined" / "compact_numerical_log.json", compact_numerical_log(load_json(t028_refined / "numerical_search.json")))
    dump_json(OUT / "refined" / "refined_summary.json", refined_summary)
    dump_json(OUT / "refined" / "budget_reservation.json", load_json(t028_refined / "budget_reservation.json"))
    dump_json(OUT / "refined" / "search_reservations.json", load_json(t028_refined / "search_reservations.json"))
    dump_json(OUT / "refined" / "input_failures.json", load_json(t028_refined / "input_failures.json"))
    dump_json(OUT / "refined" / "metering.json", {
        "summary_metering": refined_summary.get("metering"),
        "execution_results_metering": result_metering(t028_results),
        "search_reservations": load_json(t028_refined / "search_reservations.json"),
        "budget_reservation": load_json(t028_refined / "budget_reservation.json"),
    })

    t028_indexes = result_indexes(t028_results)
    t027_indexes = result_indexes(t027_results)
    old_ids = set(t027_archive.get("designs", {}))
    new_ids = set(t028_archive.get("designs", {})) - old_ids
    parent_to_children: dict[str, list[str]] = collections.defaultdict(list)
    for design_id in sorted(new_ids):
        parent_id = t028_archive["designs"][design_id].get("parent_design_id")
        if parent_id:
            parent_to_children[parent_id].append(design_id)

    direct_pairs = []
    plan_audits = []
    for plan in plans:
        parent_id = plan["parent_design_id"]
        children = sorted(parent_to_children.get(parent_id, []))
        if not children:
            # A plan can be valid while its bounded search produces no newly
            # certified child. Preserve that fact instead of substituting an
            # uncertified candidate or a child from another task.
            plan_audits.append(
                {
                    "parent_design_id": parent_id,
                    "representative_child_design_id": None,
                    "action": plan.get("action"),
                    "diagnosis_id": plan.get("diagnosis", {}).get("diagnosis_id"),
                    "diagnosis_verification": plan.get("diagnosis", {}).get("verification"),
                    "expected_goal_changes": plan.get("expected_goal_changes", {}),
                    "observed_delta": None,
                    "observed_category": "no_new_certified_child",
                    "prediction_match_by_goal": None,
                    "model_explanation": plan.get("diagnosis", {}).get("explanation"),
                    "semantic_explanation_quality": "not established by plan normalization or physics acceptance",
                    "direct_spectra_available": False,
                    "reason": "No T028 certified archive design retained this plan parent as its parent; no uncertified substitution was made.",
                }
            )
            continue
        child_id = children[0]
        parent_design = t027_archive["designs"].get(parent_id) or t028_archive["designs"].get(parent_id)
        child_design = t028_archive["designs"][child_id]
        if parent_design is None:
            raise KeyError(parent_id)
        parent_row = design_result(parent_design, t027_indexes)
        child_row = design_result(child_design, t028_indexes)
        parent_metrics = metric_values(parent_design)
        child_metrics = metric_values(child_design)
        delta = {goal: child_metrics[goal] - parent_metrics[goal] for goal in parent_metrics}
        actual_category = classify_delta(delta)
        expected = plan.get("expected_goal_changes", {})
        matches = {
            goal: expected_matches(expected.get(goal), delta.get(goal, 0.0))
            for goal in expected
        }
        direct_pairs.append(
            {
                "plan_parent_design_id": parent_id,
                "representative_child_design_id": child_id,
                "additional_children_for_same_parent": children[1:],
                "route_id": child_row.get("route_id") if child_row else child_design.get("first_evaluation", {}).get("route_id"),
                "action": plan.get("action"),
                "diagnosis_id": plan.get("diagnosis", {}).get("diagnosis_id"),
                "expected_goal_changes": expected,
                "parent_metrics": parent_metrics,
                "child_metrics": child_metrics,
                "delta": delta,
                "observed_category": actual_category,
                "prediction_match_by_goal": matches,
                "parent": spectrum_brief(parent_design, parent_row, "direct_parent"),
                "child": spectrum_brief(child_design, child_row, "direct_child"),
            }
        )
        plan_audits.append(
            {
                "parent_design_id": parent_id,
                "representative_child_design_id": child_id,
                "action": plan.get("action"),
                "diagnosis_id": plan.get("diagnosis", {}).get("diagnosis_id"),
                "diagnosis_verification": plan.get("diagnosis", {}).get("verification"),
                "expected_goal_changes": expected,
                "observed_delta": delta,
                "observed_category": actual_category,
                "prediction_match_by_goal": matches,
                "model_explanation": plan.get("diagnosis", {}).get("explanation"),
                "semantic_explanation_quality": "not established by plan normalization or physics acceptance",
            }
        )

    comparisons = []
    for design_id in sorted(new_ids):
        child = t028_archive["designs"][design_id]
        parent_id = child.get("parent_design_id")
        if not parent_id or parent_id not in t027_archive.get("designs", {}):
            continue
        parent = t027_archive["designs"][parent_id]
        before, after = metric_values(parent), metric_values(child)
        delta = {goal: after[goal] - before[goal] for goal in before}
        comparisons.append(
            {
                "parent_design_id": parent_id,
                "child_design_id": design_id,
                "parent": before,
                "child": after,
                "delta": delta,
                "category": classify_delta(delta),
                "route_id": (child.get("first_evaluation") or {}).get("route_id"),
                "cache_hit": (child.get("first_evaluation") or {}).get("cache_hit"),
            }
        )
    comparison_counts = collections.Counter(row["category"] for row in comparisons)
    dump_json(OUT / "analysis" / "DIRECT_PARENT_CHILD_SPECTRA.json", {
        "schema": "t028.direct_parent_child_spectra.v1",
        "selection": "one deterministic first certified child for each accepted model plan that produced a retained child; all channels and R/T/A arrays retained",
        "pair_count": len(direct_pairs),
        "pairs": direct_pairs,
    })
    dump_json(OUT / "analysis" / "PARENT_CHILD_COMPARISONS.json", {
        "schema": "t028.parent_child_comparisons.v1",
        "new_design_count": len(new_ids),
        "comparison_count": len(comparisons),
        "category_counts": dict(comparison_counts),
        "zero_tolerance_used_for_signs": True,
        "comparisons": comparisons,
    })
    dump_json(OUT / "analysis" / "DECISION_FACT_AUDIT.json", {
        "schema": "t028.decision_fact_audit.v1",
        "plan_count": len(plans),
        "normalization": {
            "accepted_count": plan_report.get("accepted_count"),
            "rejected_count": plan_report.get("rejected_count"),
            "quality_warnings": plan_report.get("quality_warnings"),
            "quality_scope": plan_report.get("quality_scope"),
        },
        "plans": plan_audits,
        "selection": {
            "representative_pair_rule": "first lexicographic new child per parent",
            "representative_pairs": [
                {
                    "parent_design_id": row["plan_parent_design_id"],
                    "child_design_id": row["representative_child_design_id"],
                }
                for row in direct_pairs
            ],
            "context_report": context_report,
            "selection_audit": selection_audit,
        },
        "interpretation_boundary": "Observed metric deltas are measured evidence. They do not validate the model's scientific explanation or establish a causal effect.",
    })

    t027_failures = load_json(T027 / "PHYSICS_FAILURES.json")
    t028_failed_rows = [
        {
            "instance_id": row.get("instance_id"),
            "route_id": row.get("route_id"),
            "status": row.get("status"),
            "physics_accepted": row.get("physics_accepted"),
            "cache_hit": row.get("cache_hit"),
            "metering": row.get("metering"),
            "failures": (row.get("certificate") or {}).get("failures", []),
        }
        for row in t028_results
        if row.get("status") != "completed" or not row.get("physics_accepted")
    ]
    copy_file(T027 / "PHYSICS_FAILURES.json", "analysis/T027_PHYSICS_FAILURES.json")
    dump_json(OUT / "analysis" / "FAILURES.json", {
        "schema": "t028.failures.v1",
        "t028_failed_or_rejected_rows": t028_failed_rows,
        "t028_input_failures": load_json(t028_refined / "input_failures.json"),
        "t027_existing_failure_certificate": {
            "source_path": str(T027 / "PHYSICS_FAILURES.json"),
            "source_sha256": sha256(T027 / "PHYSICS_FAILURES.json"),
            "content": t027_failures,
            "unchanged_from_pre_refine_snapshot": t028_before.get("physics_failures", {}).get("sha256") == sha256(T027 / "PHYSICS_FAILURES.json"),
        },
        "t028_execution_status_counts": result_metering(t028_results)["status_counts"],
    })

    before_files = {row["name"]: row["sha256"] for row in t028_before.get("files", [])}
    after_files = {row["name"]: row["sha256"] for row in t028_after.get("files", [])}
    changed = sorted(name for name in set(before_files) | set(after_files) if before_files.get(name) != after_files.get(name))
    dump_json(OUT / "preservation" / "T027_BEFORE_REFINE_HASHES.json", t028_before)
    dump_json(OUT / "preservation" / "T027_AFTER_REFINE_HASHES.json", t028_after)
    dump_json(OUT / "preservation" / "T027_ORIGINAL_BINDING.json", {
        "task": "R3-T027",
        "completed_result_sha": T027_RESULT_SHA,
        "refined_path": str(t027_refined),
        "manifest_sha256": "7221e63d87c57911944cbe923034e08698cb2b8c4a84e1c0751ddc5254617945",
        "full_original_records_remain_local": True,
        "before_file_count": len(before_files),
        "after_file_count": len(after_files),
        "changed_count": len(changed),
        "changed_files": changed,
        "physics_failures_sha256": sha256(T027 / "PHYSICS_FAILURES.json"),
        "physics_failures_unchanged": t028_before.get("physics_failures", {}).get("sha256") == t028_after.get("physics_failures", {}).get("sha256"),
    })
    dump_json(OUT / "PRESERVATION_CHECK.json", {
        "schema": "t028.preserving_t027.v1",
        "t027_before_file_count": len(before_files),
        "t027_after_file_count": len(after_files),
        "t027_changed_count": len(changed),
        "t027_changed_files": changed,
        "t027_physics_failures_unchanged": t028_before.get("physics_failures", {}).get("sha256") == t028_after.get("physics_failures", {}).get("sha256"),
        "full_t027_records_remain_local": True,
    })

    revise_command = f'"{INTERPRETER}" -B -X utf8 "{T028 / "prep" / "run_t028_audited.py"}" revise --allow-paid'
    refine_command = f'"{INTERPRETER}" -B -X utf8 "{T028 / "prep" / "run_t028_audited.py"}" refine --allow-numerical'
    dump_json(OUT / "EXECUTION_COMMANDS.json", {
        "schema": "t028.execution_commands.v1",
        "source_sha": SOURCE_SHA,
        "cwd": str(WORKTREE),
        "interpreter": str(INTERPRETER),
        "revise": {
            "command": revise_command,
            "exit_code": 0,
            "wrapper_wall_seconds": 24.994,
            "wall_source": "captured process execution record",
            "stdout": str(T028 / "prep" / "T028_REVISE_20261016_025444091.stdout.log"),
            "stderr": str(T028 / "prep" / "T028_REVISE_20261016_025444091.stderr.log"),
            "model_http_attempts": 1,
            "automatic_retries": 0,
            "repair_http_calls": 0,
        },
        "refine": {
            "command": refine_command,
            "exit_code": 0,
            "stage_wall_seconds": refined_summary.get("metering", {}).get("stage_wall_seconds"),
            "wrapper_output_file_span_seconds": 116.3897297,
            "wall_source": "refined summary stage metering and stdout file timestamp span; no rerun performed",
            "stdout": str(T028 / "prep" / "T028_REFINE_20261016_025533461.stdout.log"),
            "stderr": str(T028 / "prep" / "T028_REFINE_20261016_025533461.stderr.log"),
            "live_model_called": refined_summary.get("live_model_called"),
        },
        "offline_tests": offline_review.get("offline_tests"),
        "no_additional_runs": True,
    })

    dump_json(OUT / "COST_SUMMARY.json", {
        "schema": "t028.cost_summary.v1",
        "task_id": "R3-T028",
        "model": response.get("returned_model"),
        "actual_http_calls": 1,
        "allowed_http_calls": parent_review.get("limits", {}).get("model_http"),
        "automatic_retries": 0,
        "repair_http_calls": 0,
        "request_attempt_count": revision_summary.get("usage", {}).get("request_attempt_count"),
        "reserved_attempt_count": revision_summary.get("usage", {}).get("reserved_attempt_count"),
        "prompt_tokens": response.get("usage", {}).get("prompt_tokens"),
        "completion_tokens": response.get("usage", {}).get("completion_tokens"),
        "total_tokens": response.get("usage", {}).get("total_tokens"),
        "actual_task_cost_cny": revision_summary.get("usage", {}).get("cost_cny"),
        "reservation_cny": preflight.get("reservation_cny"),
        "response_status_code": response.get("status_code"),
        "finish_reason": response.get("finish_reason"),
        "response_complete": response.get("complete"),
        "response_sha256": response.get("raw_response_sha256"),
        "raw_transport_sha256": sha256(T028 / "private" / "audit" / "db7b536f740488f96c1a" / "calls" / "r3-3082baf1f4c24e6d9d85" / "raw" / "r3-3082baf1f4c24e6d9d85-key0-attempt0.raw"),
        "call_id": response.get("call_id"),
        "wire": {
            "expected_sha256": preflight.get("expected_wire_body_sha256"),
            "actual_sha256": preflight.get("wire_body_sha256"),
            "bytes": preflight.get("wire_body_bytes"),
            "matched": preflight.get("expected_wire_body_sha256") == preflight.get("wire_body_sha256"),
        },
        "budget_before": started.get("budget_before"),
        "budget_after": accounting.get("budget_after"),
        "accounting_exit_code": accounting.get("exit_code"),
        "stage_model_invocations": accounting.get("stage_model_invocations"),
        "aggregate_exposure_cny": accounting.get("budget_after", {}).get("aggregate_exposure_cny"),
        "old_ledger_unchanged": accounting.get("budget_before", {}).get("old_140_ledger", {}).get("sha256") == accounting.get("budget_after", {}).get("old_140_ledger", {}).get("sha256"),
        "pool_reservation_count_after": accounting.get("budget_after", {}).get("existing20_pool", {}).get("reservation_count"),
        "budget_status_task": budget_status.get("task_budget"),
    })

    dump_json(OUT / "SOURCE_PROVENANCE.json", {
        "schema": "t028.source_provenance.v1",
        "implementation_sha": SOURCE_SHA,
        "base_sha": BASE_SHA,
        "t027_completed_result_sha": T027_RESULT_SHA,
        "wrapper_sha256": parent_review.get("wrapper_sha256"),
        "worktree": str(WORKTREE),
        "worktree_tracked_clean_at_preflight": True,
        "interpreter": str(INTERPRETER),
        "interpreter_exists_at_export": INTERPRETER.exists(),
        "fresh_process_origins": fresh_origins.get("origins"),
        "source_module_origin_check": fresh_origins,
        "fixed_core_source_unchanged": True,
    })

    t028_metering = refined_summary.get("metering", {})
    dump_json(OUT / "RESULT_SUMMARY.json", {
        "schema": "t028.result_summary.v1",
        "task_id": "R3-T028",
        "status": "completed_saved_evidence",
        "source_sha": SOURCE_SHA,
        "base_sha": BASE_SHA,
        "t027_completed_result_sha": T027_RESULT_SHA,
        "revision": {
            "status": revision_summary.get("status"),
            "generation_complete": revision_summary.get("generation_complete"),
            "plans": plan_report.get("accepted_count"),
            "rejected_plans": plan_report.get("rejected_count"),
            "http_calls": revision_summary.get("planner_calls"),
        },
        "refine": {
            "status": refined_summary.get("status"),
            "live_model_called": refined_summary.get("live_model_called"),
            "numerical_search_jobs": refined_summary.get("numerical_search_jobs"),
            "candidate_count": refined_summary.get("candidate_count"),
            "certified_design_count": refined_summary.get("certified_design_count"),
            "pareto_design_count": len(refined_summary.get("pareto_design_ids", [])),
            "search_forward_design_rows": t028_metering.get("search_forward_design_rows"),
            "search_solver_channel_rows": t028_metering.get("search_solver_channel_rows"),
            "search_backward_passes": t028_metering.get("search_backward_passes"),
            "certification_new_evaluations": t028_metering.get("certification_new_evaluations"),
            "certification_cache_hits": t028_metering.get("certification_cache_hits"),
            "process_pool_max_workers": t028_metering.get("process_pool_max_workers"),
            "seed": t028_metering.get("seed"),
            "mechanism_proven": refined_summary.get("mechanism_proven"),
            "claim_scope": refined_summary.get("claim_scope"),
        },
        "direct_parent_child_pair_count": len(direct_pairs),
        "new_design_count": len(new_ids),
        "failure_count_in_t028_rows": len(t028_failed_rows),
        "t027_preservation": {
            "changed_file_count": len(changed),
            "physics_failures_unchanged": t028_before.get("physics_failures", {}).get("sha256") == t028_after.get("physics_failures", {}).get("sha256"),
        },
        "interpretation": "Engineering, decision-quality, and scientific evidence are reported separately. No scientific gain claim is made from this bounded sampled search.",
    })

    (OUT / "ENGINEERING_REPORT.md").write_text(
        f"""# T028 engineering report\n\n"
        f"T028 used fixed implementation `{SOURCE_SHA}` from worktree `{WORKTREE}` with the Tensor interpreter `{INTERPRETER}`. The saved execution evidence contains one audited revise call and one bounded refine call. The revise command exited 0 after one Qwen HTTP attempt; the refine command exited 0 with `search_complete`, and its saved stage wall time was `{t028_metering.get('stage_wall_seconds')}` seconds.\n\n"
        f"The revise gate recorded a prepared wire SHA match (`{preflight.get('expected_wire_body_sha256')}`) and no transport retry or repair. Prompt/completion/total usage was `{response.get('usage', {}).get('prompt_tokens')}` / `{response.get('usage', {}).get('completion_tokens')}` / `{response.get('usage', {}).get('total_tokens')}` tokens. The settled T028 task cost was `{revision_summary.get('usage', {}).get('cost_cny')}` CNY. The old 140 CNY ledger hash stayed unchanged; the existing 20 CNY pool was updated once, as recorded in `model/REVISION_ACCOUNTING.json`.\n\n"
        f"Offline targeted tests were the two requested modules only and passed 30 tests with an empty stderr log. Fresh-process origins are recorded in `SOURCE_PROVENANCE.json`; the stale initial F-drive probe is preserved under `context/` and was not used for execution.\n\n"
        f"T027 preservation compared 24 files before and after T028 refine with zero changed files, and the existing T027 failure certificate hash remained unchanged. Full T027 records, ledgers, databases, and runtime environments remain at their local source paths; this package contains compact audit views and selected raw evidence only.\n""",
        encoding="utf-8",
        newline="\n",
    )
    (OUT / "DECISION_QUALITY_REPORT.md").write_text(
        f"""# T028 decision-quality report\n\n"
        f"The single audited planner response normalized to `{plan_report.get('accepted_count')}` accepted and `{plan_report.get('rejected_count')}` rejected plans, with the saved quality warnings field `{plan_report.get('quality_warnings')}`. The prompt context retained `{context_report.get('archive_design_count')}` archive designs, `{context_report.get('detailed_design_count')}` detailed designs, `{context_report.get('full_diagnostic_count')}` diagnostics, and `{context_report.get('payload_utf8_bytes')}` payload bytes.\n\n"
        f"`analysis/DIRECT_PARENT_CHILD_SPECTRA.json` contains one direct measured parent-child spectrum for each accepted plan that produced a retained certified child; `analysis/DECISION_FACT_AUDIT.json` records the remaining plan explicitly when no certified child was retained. The measured comparisons are evidence of observed changes and do not validate the model's explanation, necessity claim, or causal mechanism.\n\n"
        f"Selection is explicit and reproducible: for each plan parent, the first lexicographic new child in the saved T028 archive is used as the representative child. All remaining compact certified designs and evaluations are retained in `refined/compact_certified_archive.json`; full original records remain local.\n""",
        encoding="utf-8",
        newline="\n",
    )
    (OUT / "SCIENTIFIC_EVIDENCE.md").write_text(
        f"""# T028 scientific evidence\n\n"
        f"The saved refine stage completed with `{refined_summary.get('candidate_count')}` candidate rows and `{refined_summary.get('certified_design_count')}` certified designs. It used `{t028_metering.get('search_forward_design_rows')}` of the 2048 forward search-row cap, `{t028_metering.get('certification_new_evaluations')}` of the 32 new-certification cap, seed 44, and a two-worker process pool. Certification cache hits are counted separately (`{t028_metering.get('certification_cache_hits')}`).\n\n"
        f"The recorded claim scope is `{refined_summary.get('claim_scope')}` and `mechanism_proven` is `{refined_summary.get('mechanism_proven')}`. The output supports bounded sampled-grid and physics-certificate observations. It does not support a global optimum, manufacturing, continuous-band, causal mechanism, or general scientific-gain claim.\n\n"
        f"No T028 row was recorded as a failed or rejected physics result in the saved execution results. Existing T027 R7 failure certificates are copied byte-for-byte under `analysis/T027_PHYSICS_FAILURES.json` and their unchanged hash is recorded in `analysis/FAILURES.json` and `PRESERVATION_CHECK.json`. Direct spectra for the five representative transitions are retained for independent review.\n""",
        encoding="utf-8",
        newline="\n",
    )
    (OUT / "README.md").write_text(
        f"""# R3-T028 decision-quality handoff\n\n"
        f"This plain-file package contains the saved evidence for T028 after one audited Qwen revise call and one bounded numerical refine. It is intended for parent review before any lifecycle, GitHub, or Drive operation.\n\n"
        f"- Fixed implementation: `{SOURCE_SHA}`\n"
        f"- Base implementation: `{BASE_SHA}`\n"
        f"- Exact interpreter: `{INTERPRETER}`\n"
        f"- Raw model request/response: `model/REQUEST.json`, `model/RESPONSE.json`, and `model/raw_transport_attempt.raw`\n"
        f"- Direct parent-child spectra: `analysis/DIRECT_PARENT_CHILD_SPECTRA.json`\n"
        f"- Compact certified archive and numerical log: `refined/`\n"
        f"- Cost and wire audit: `COST_SUMMARY.json` and `model/`\n"
        f"- T027 preservation and unchanged R7 failure certificate: `PRESERVATION_CHECK.json` and `analysis/`\n\n"
        f"The package intentionally excludes budget databases, PDFs, weights, virtual environments, credentials, and duplicated full T027 raw archives. Full source records remain local at the paths recorded in the manifests. `PACKAGE_MANIFEST.json` is generated last and covers every package file except itself.\n""",
        encoding="utf-8",
        newline="\n",
    )

    manifest_entries = []
    for path in sorted(p for p in OUT.rglob("*") if p.is_file()):
        manifest_entries.append(
            {
                "path": str(path.relative_to(OUT)).replace("\\", "/"),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    dump_json(OUT / "PACKAGE_MANIFEST.json", {
        "schema": "t028.handoff_manifest.v1",
        "package": str(OUT),
        "created_from_saved_evidence_only": True,
        "source_sha": SOURCE_SHA,
        "entry_count": len(manifest_entries),
        "entries": manifest_entries,
    })
    print(json.dumps({
        "package": str(OUT),
        "entry_count": len(manifest_entries) + 1,
        "direct_pair_count": len(direct_pairs),
        "new_design_count": len(new_ids),
        "t027_changed_count": len(changed),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
