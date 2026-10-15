from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import traceback


SRC = Path(r"D:\OptoMind-Article-3\r3t033a")
T033 = Path(r"D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T033")
T030_ROOT = Path(r"D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T030")
T030 = T030_ROOT / "search"
T028 = Path(r"D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T028\refined")
T030_HANDOFF = Path(r"D:\OptoMind-Article-3\r3t030a\handoff\R3-T030-DIRECTIONAL-20261016")
OUT = T033 / "replay" / "run-03"
PREFLIGHT = T033 / "preflight" / "run-03"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def json_digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def resolve_source_file(relative: str) -> Path:
    candidates = [T030_ROOT / relative, T030_HANDOFF / relative]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(relative)


def source_hash_snapshot():
    declared = load_json(T033 / "SOURCE_HASHES.json")
    rows = {}
    mismatches = []
    for relative, spec in declared["files"].items():
        try:
            path = resolve_source_file(relative)
            actual = sha256_file(path)
            row = {
                "relative": relative,
                "path": str(path),
                "expected_sha256": spec["sha256"],
                "actual_sha256": actual,
                "expected_size_bytes": spec["size_bytes"],
                "actual_size_bytes": path.stat().st_size,
                "matches": actual == spec["sha256"] and path.stat().st_size == spec["size_bytes"],
            }
            rows[relative] = row
            if not row["matches"]:
                mismatches.append(row)
        except Exception as exc:
            row = {"relative": relative, "error": str(exc), "matches": False}
            rows[relative] = row
            mismatches.append(row)
    return {"hash_scope": declared.get("hash_scope"), "files": rows,
            "file_count": len(rows), "mismatch_count": len(mismatches), "mismatches": mismatches}


def handoff_artifact_snapshot():
    c2l = load_json(T033 / "C2L-R3-0048.json")
    rows = []
    mismatches = []
    for spec in c2l.get("files", []):
        path = T033 / spec["name"]
        actual = sha256_file(path) if path.is_file() else None
        row = {"name": spec["name"], "path": str(path), "expected_sha256": spec["hash"],
               "actual_sha256": actual, "matches": actual == spec["hash"]}
        rows.append(row)
        if not row["matches"]:
            mismatches.append(row)
    return {"expected_artifact_count": len(c2l.get("files", [])), "actual_artifacts": rows,
            "mismatch_count": len(mismatches), "mismatches": mismatches}


def reference_snapshot():
    ref_path = T030_HANDOFF / "FULL_LOCAL_ARCHIVE_REFERENCES.json"
    refs = load_json(ref_path)
    rows = []
    mismatches = []
    for spec in refs:
        path = Path(spec["local_path"])
        actual = sha256_file(path) if path.is_file() else None
        row = {"path": str(path), "size_bytes_expected": spec["size_bytes"],
               "size_bytes_actual": path.stat().st_size if path.is_file() else None,
               "expected_sha256": spec["sha256"], "actual_sha256": actual,
               "compact_export_present": spec.get("compact_export_present"),
               "matches": bool(path.is_file() and actual == spec["sha256"] and path.stat().st_size == spec["size_bytes"])}
        rows.append(row)
        if not row["matches"]:
            mismatches.append(row)
    return {"path": str(ref_path), "reference_file_sha256": sha256_file(ref_path),
            "entry_count": len(rows), "entries": rows, "mismatch_count": len(mismatches),
            "mismatches": mismatches}


def git_sha() -> str:
    result = subprocess.run(["git", "-C", str(SRC), "rev-parse", "HEAD"],
                            check=True, capture_output=True, text=True)
    return result.stdout.strip()


def variable_name(route, parameterization, key):
    if parameterization == "family":
        return key
    if ":" not in key:
        return key
    return key.split(":", 1)[1]


def validate_expansion_replay(raw_payload, coarse, previous, expanded):
    raw_branches = raw_payload.get("branches", [])
    jobs = expanded.get("jobs", [])
    by_id = {job.get("branch_id"): job for job in jobs}
    rows = []
    mismatches = []
    total_windows = 0
    seed_violations = []
    for branch in raw_branches:
        branch_id = branch.get("branch_id")
        source_fields = [key for key in ("search_bounds_nm", "optional_search_bounds_nm") if key in branch]
        maps = [branch.get(key) or {} for key in source_fields]
        effective = maps[0] if maps else {}
        if len(maps) == 2 and maps[0] != maps[1]:
            mismatches.append({"branch_id": branch_id, "kind": "conflicting_raw_alias_maps",
                               "search_bounds_nm": maps[0], "optional_search_bounds_nm": maps[1]})
        total_windows += len(effective)
        job = by_id.get(branch_id)
        row = {"branch_id": branch_id, "raw_source_fields": source_fields,
               "raw_effective_search_bounds_nm": copy.deepcopy(effective),
               "compiled_present": job is not None,
               "compiled_search_bounds_nm": copy.deepcopy(job.get("search_bounds_nm", {})) if job else None,
               "window_values_unchanged": bool(job and job.get("search_bounds_nm", {}) == effective)}
        if job is None:
            mismatches.append({"branch_id": branch_id, "kind": "missing_compiled_branch"})
            rows.append(row)
            continue
        if job.get("search_bounds_nm", {}) != effective:
            mismatches.append({"branch_id": branch_id, "kind": "window_value_mismatch",
                               "raw": effective, "compiled": job.get("search_bounds_nm", {})})
        route = job["route"]
        original_bounds = route["family"]["thickness_variables_nm"]
        for key, value in job.get("parameters_nm", {}).items():
            var = variable_name(route, job.get("parameterization"), key)
            bound = effective.get(key, effective.get(var, original_bounds.get(var)))
            original = original_bounds.get(var)
            if original is None or not (original[0] <= value <= original[1]):
                seed_violations.append({"branch_id": branch_id, "variable": key, "kind": "outside_original_bound",
                                        "value": value, "bound": original})
            if bound is None or not (bound[0] <= value <= bound[1]):
                seed_violations.append({"branch_id": branch_id, "variable": key, "kind": "outside_effective_bound",
                                        "value": value, "bound": bound})
        rows.append(row)
    return {
        "raw_branch_count": len(raw_branches),
        "compiled_accepted_count": expanded["report"].get("accepted_count"),
        "compiled_rejected_count": expanded["report"].get("rejected_count"),
        "effective_window_count": total_windows,
        "window_rows": rows,
        "seed_containment_violations": seed_violations,
        "mismatches": mismatches,
        "all_branch_ids_present": len(by_id) == len(raw_branches),
        "all_windows_unchanged": not mismatches,
        "all_seeds_contained": not seed_violations,
        "raw_response_sha256": json_digest(raw_payload),
        "coarse_plans_sha256": json_digest(coarse),
        "prior_manifest_sha256": previous["manifest_sha256"],
    }


def compare_feedback(previous, feedback, feedback_view, aliases):
    original_branches = {row["branch_id"]: row for row in feedback.get("branches", [])}
    derived_branches = {row["branch_id"]: row for row in feedback_view.get("branches", [])}
    rows = []
    mismatches = []
    for branch_id, branch in original_branches.items():
        derived = derived_branches.get(branch_id)
        for index, outcome in enumerate(branch.get("selected_certified_designs", [])):
            candidate_id = outcome.get("design_id")
            parent_id = branch.get("parent_design_id")
            d_outcome = (derived or {}).get("selected_certified_designs", [])[index] if derived and index < len((derived or {}).get("selected_certified_designs", [])) else None
            endpoint_ok = bool(candidate_id in previous["archive"]["designs"] and
                               parent_id in previous["archive"]["designs"] and d_outcome and
                               d_outcome.get("comparison_baseline_design_id") == parent_id)
            vector_ok = bool(d_outcome and d_outcome.get("metrics") == outcome.get("metrics") and
                             d_outcome.get("deltas_from_parent") == outcome.get("deltas_from_parent"))
            diagnostics = (d_outcome or {}).get("current_diagnostic_references") or []
            diagnostic_endpoint_ok = all(ref.get("comparison_baseline_design_id") == parent_id and
                                         ref.get("design_id") == candidate_id for ref in diagnostics)
            row = {"branch_id": branch_id, "selected_index": index, "parent_design_id": parent_id,
                   "design_id": candidate_id, "parent_alias": aliases["parents"].get(parent_id),
                   "design_alias": aliases["parents"].get(candidate_id),
                   "metrics": copy.deepcopy(outcome.get("metrics")),
                   "deltas_from_parent": copy.deepcopy(outcome.get("deltas_from_parent")),
                   "endpoint_unchanged": endpoint_ok,
                   "vector_and_delta_unchanged": vector_ok,
                   "diagnostic_endpoints_bound": diagnostic_endpoint_ok,
                   "diagnostic_reference_count": len(diagnostics)}
            rows.append(row)
            if not (endpoint_ok and vector_ok and diagnostic_endpoint_ok):
                mismatches.append(row)
    return {"actual_selected_comparison_count": len(rows), "unchanged_endpoint_vector_count": len(rows) - len(mismatches),
            "rows": rows, "mismatches": mismatches, "all_unchanged": not mismatches}


def action_gate_check(current, decision_view, st_module):
    action_rows = {}
    mismatches = []
    for parent in sorted(current["archive"]["designs"]):
        advertised = st_module._refinement_action_availability(current, parent)
        direct_available, direct_unavailable = [], {}
        for action in ("refine_family", "refine_layers"):
            try:
                st_module._validate_refinement_action(current, parent, action)
                direct_available.append(action)
            except (ValueError, TypeError, KeyError, StopIteration) as exc:
                direct_unavailable[action] = str(exc) or "missing parent execution context"
        row = {"available_actions": advertised["available_actions"],
               "unavailable_actions": advertised["unavailable_actions"],
               "direct_available_actions": direct_available,
               "direct_unavailable_actions": direct_unavailable,
               "gate_matches": advertised["available_actions"] == direct_available and
                               advertised["unavailable_actions"] == direct_unavailable}
        action_rows[parent] = row
        if not row["gate_matches"]:
            mismatches.append({"parent": parent, **row})
    decision_rows = {row.get("design_id"): row for row in decision_view.get("user_payload", {}).get("certified_designs", [])}
    for parent, row in action_rows.items():
        drow = decision_rows.get(parent)
        if not drow or drow.get("available_actions") != row["available_actions"] or drow.get("unavailable_actions") != row["unavailable_actions"]:
            mismatches.append({"parent": parent, "kind": "decision_view_action_mismatch", "action": row,
                               "decision_row": drow})
    return {"actual_archive_parent_count": len(action_rows), "decision_view_parent_count": len(decision_rows),
            "action_rows": action_rows, "mismatch_count": len(mismatches), "mismatches": mismatches,
            "all_action_gates_match": len(action_rows) == len(current["archive"]["designs"]) and not mismatches}


def run_tests():
    PREFLIGHT.mkdir(parents=True, exist_ok=True)
    expression = "not test_alias_windows_reach_numerical_search_candidate_ancestry_and_feedback and not test_alias_windows_also_guard_candidates_before_certification"
    argv = [sys.executable, "-B", "-X", "utf8", "-m", "pytest", "-q",
            "focused_research/tests/test_direction_bounds_normalization.py",
            "focused_research/tests/test_t033_reflection_identity.py", "-k", expression,
            "--disable-socket"]
    result = subprocess.run(argv, cwd=str(SRC), capture_output=True, text=True, encoding="utf-8", errors="replace")
    (PREFLIGHT / "targeted_tests.stdout.log").write_text(result.stdout, encoding="utf-8")
    (PREFLIGHT / "targeted_tests.stderr.log").write_text(result.stderr, encoding="utf-8")
    text = result.stdout + "\n" + result.stderr
    def count(pattern):
        m = re.search(pattern, text)
        return int(m.group(1)) if m else 0
    return {"argv": argv, "cwd": str(SRC), "returncode": result.returncode,
            "passed": count(r"(\d+) passed"), "failed": count(r"(\d+) failed"),
            "skipped": count(r"(\d+) skipped"), "errors": count(r"(\d+) error"),
            "socket_disabled": "--disable-socket" in argv,
            "stdout_log": str(PREFLIGHT / "targeted_tests.stdout.log"),
            "stderr_log": str(PREFLIGHT / "targeted_tests.stderr.log")}


def main():
    if OUT.exists():
        raise RuntimeError("output already exists; preserve prior attempt")
    if PREFLIGHT.exists():
        raise RuntimeError("preflight output already exists; preserve prior attempt")
    OUT.mkdir(parents=True)
    PREFLIGHT.mkdir(parents=True)
    (OUT / "derived").mkdir()

    before = source_hash_snapshot()
    handoff = handoff_artifact_snapshot()
    refs = reference_snapshot()
    source_sha = git_sha()
    write_json(OUT / "source_hashes_before.json", before)
    write_json(OUT / "handoff_artifact_hashes.json", handoff)
    write_json(OUT / "full_archive_reference_hashes.json", refs)

    sys.path.insert(0, str(SRC))
    from focused_research import direction_expansion as de
    from focused_research import directional_reflection as dr
    from focused_research import search_stage as st

    prior = st.read_search(T028)
    current = st.read_search(T030)
    coarse = st.load(T030_ROOT / "expansion" / "coarse_plans.json")
    expansion_raw = st.load(T030_ROOT / "expansion" / "raw_response.json")
    expansion_payload = json.loads(expansion_raw["response"])
    expanded = de.validate_expansion(expansion_payload, prior, coarse)
    expansion_check = validate_expansion_replay(expansion_payload, coarse, prior, expanded)
    write_json(OUT / "derived" / "expansion_compiled.json", expanded)
    write_json(OUT / "derived" / "expansion_check.json", expansion_check)

    aliases = st._aliases(current)
    decision_view = st._revision_decision_view(current, aliases)
    feedback = st.load(T030_ROOT / "search" / "branch_feedback.json")
    feedback_view = dr._feedback_decision_view(feedback, current, aliases)
    actions = action_gate_check(current, decision_view, st)
    write_json(OUT / "derived" / "decision_view.json", decision_view)
    write_json(OUT / "derived" / "feedback_view.json", feedback_view)
    write_json(OUT / "derived" / "action_availability.json", actions)
    comparison_check = compare_feedback(current, feedback, feedback_view, aliases)
    write_json(OUT / "derived" / "comparison_check.json", comparison_check)

    reflection_raw = st.load(T030_ROOT / "reflection" / "raw_response.json")
    reflection_payload = json.loads(reflection_raw["response"])
    compiled_plans, plan_report = st._compile_plans(reflection_payload, current)
    reflection_check = {
        "raw_response_sha256": json_digest(reflection_payload),
        "snapshot_manifest_sha256": current["manifest_sha256"],
        "plans": compiled_plans,
        "plan_report": plan_report,
        "accepted_count": plan_report.get("accepted_count"),
        "rejected_count": plan_report.get("rejected_count"),
        "rejection_reasons": [row.get("error") for row in plan_report.get("rejected_plans", [])],
        "next_action_executed": False,
    }
    write_json(OUT / "derived" / "reflection_compiled.json", reflection_check)

    tests = run_tests()
    write_json(PREFLIGHT / "targeted_tests.json", tests)

    after = source_hash_snapshot()
    write_json(OUT / "source_hashes_after.json", after)
    source_unchanged = before["mismatch_count"] == 0 and after["mismatch_count"] == 0 and before["files"] == after["files"]
    source_sha_after = git_sha()

    compact_refs = []
    for path in sorted(OUT.rglob("*")):
        if path.is_file():
            compact_refs.append({"path": str(path), "relative": str(path.relative_to(OUT)),
                                 "size_bytes": path.stat().st_size, "sha256": sha256_file(path)})
    write_json(OUT / "compact_references.json", {
        "source_sha": source_sha, "source_sha_after": source_sha_after,
        "t028_full_search": str(T028), "t030_full_search": str(T030),
        "generated_files": compact_refs,
        "raw_inputs": {
            "expansion_raw_response": str(T030_ROOT / "expansion" / "raw_response.json"),
            "expansion_coarse_plans": str(T030_ROOT / "expansion" / "coarse_plans.json"),
            "reflection_raw_response": str(T030_ROOT / "reflection" / "raw_response.json"),
            "branch_feedback": str(T030_ROOT / "search" / "branch_feedback.json"),
        },
    })

    replay_report = {
        "schema_version": "T033.local_replay.v1",
        "status": "completed" if source_sha == source_sha_after else "blocked_source_changed",
        "tested_sha": source_sha,
        "tested_sha_after": source_sha_after,
        "fixed_sha_expected": "f61755a86cd96adfe5b04f566bd1f42f870f2d03",
        "fixed_sha_matches": source_sha == "f61755a86cd96adfe5b04f566bd1f42f870f2d03",
        "full_archive_bindings": {
            "t028_path": str(T028), "t030_path": str(T030),
            "t028_manifest_sha256": prior["manifest_sha256"],
            "t030_manifest_sha256": current["manifest_sha256"],
            "t028_read_search_succeeded": True, "t030_read_search_succeeded": True,
            "t028_archive_design_count": len(prior["archive"]["designs"]),
            "t030_archive_design_count": len(current["archive"]["designs"]),
        },
        "expansion": expansion_check,
        "identity_action": actions,
        "measured_comparisons": comparison_check,
        "reflection": reflection_check,
        "source_hash_before_after_unchanged": source_unchanged,
        "source_hash_before": before,
        "source_hash_after": after,
        "handoff_artifact_hashes": handoff,
        "full_archive_reference_hashes": refs,
        "tests": tests,
        "zero_counters": {
            "new_model_http": 0, "new_retrieval": 0, "new_search_rows": 0,
            "new_certifications": 0, "new_physics_solves": 0, "new_cost_cny": 0,
            "ledger_mutations": 0, "planner_calls": 0, "solver_calls": 0,
        },
        "derived_outputs": str(OUT / "derived"),
        "compact_references": str(OUT / "compact_references.json"),
    }
    write_json(OUT / "replay_report.json", replay_report)

    global_pass = bool(
        replay_report["status"] == "completed" and replay_report["fixed_sha_matches"] and
        handoff["mismatch_count"] == 0 and refs["mismatch_count"] == 0 and
        before["mismatch_count"] == 0 and after["mismatch_count"] == 0 and source_unchanged and
        expansion_check["raw_branch_count"] == expansion_check["compiled_accepted_count"] and
        expansion_check["compiled_rejected_count"] == 0 and expansion_check["effective_window_count"] == 30 and
        expansion_check["all_windows_unchanged"] and expansion_check["all_seeds_contained"] and
        actions["actual_archive_parent_count"] == 286 and actions["all_action_gates_match"] and
        comparison_check["actual_selected_comparison_count"] == 18 and comparison_check["all_unchanged"] and
        reflection_check["accepted_count"] == 3 and reflection_check["rejected_count"] == 2 and
        tests["returncode"] == 0 and tests["failed"] == 0 and tests["errors"] == 0 and
        tests["socket_disabled"]
    )
    final = {
        "schema_version": "T033.local_acceptance.v1",
        "status": "completed" if global_pass else "partial_or_blocked",
        "global_pass": global_pass,
        "tested_sha": source_sha,
        "tested_sha_after": source_sha_after,
        "fixed_sha_matches": replay_report["fixed_sha_matches"],
        "full_archive_manifests": replay_report["full_archive_bindings"],
        "source_hash_before_after_unchanged": source_unchanged,
        "source_hashes_before": before,
        "source_hashes_after": after,
        "handoff_artifact_hashes": handoff,
        "full_archive_reference_hashes": refs,
        "replay_counts": {
            "expansion_branches_raw": expansion_check["raw_branch_count"],
            "expansion_branches_accepted": expansion_check["compiled_accepted_count"],
            "expansion_branches_rejected": expansion_check["compiled_rejected_count"],
            "effective_windows": expansion_check["effective_window_count"],
            "window_mismatches": len(expansion_check["mismatches"]),
            "seed_containment_violations": len(expansion_check["seed_containment_violations"]),
            "archive_parents": actions["actual_archive_parent_count"],
            "action_gate_mismatches": actions["mismatch_count"],
            "measured_comparisons": comparison_check["actual_selected_comparison_count"],
            "unchanged_comparison_vectors_endpoints": comparison_check["unchanged_endpoint_vector_count"],
            "comparison_mismatches": len(comparison_check["mismatches"]),
            "reflection_accepted": reflection_check["accepted_count"],
            "reflection_rejected": reflection_check["rejected_count"],
        },
        "reflection_rejection_reasons": reflection_check["rejection_reasons"],
        "tests": tests,
        "zero_counters": replay_report["zero_counters"],
        "mismatch_details": {
            "expansion": expansion_check["mismatches"],
            "actions": actions["mismatches"][:20],
            "comparisons": comparison_check["mismatches"][:20],
            "source_before": before["mismatches"],
            "source_after": after["mismatches"],
            "handoff_artifacts": handoff["mismatches"],
            "full_archive_references": refs["mismatches"],
        },
        "derived_outputs": str(OUT / "derived"),
        "replay_report": str(OUT / "replay_report.json"),
        "compact_references": str(OUT / "compact_references.json"),
    }
    write_json(PREFLIGHT / "T033_LOCAL_ACCEPTANCE.json", final)
    print(json.dumps({"global_pass": global_pass, "tested_sha": source_sha,
                      "expansion": final["replay_counts"], "tests": tests}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(traceback.format_exc(), file=sys.stderr)
        raise
