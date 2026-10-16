"""Pure read-only T041 admission audit; writes only the T041 acceptance JSON."""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import re
import subprocess
import sys
from pathlib import Path

SOURCE = Path(r"D:\OptoMind-Article-3\r3t041a")
TASK = Path(r"D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T041")
INPUT = Path(r"D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T039\replay-input")
PYTHON = Path(r"D:\OptoMind-Article-3\local-integration\20261013\envs\tensor\Scripts\python.exe")
REPLAY_DIR = TASK / "offline-replay" / "route-replay-attempt01"
REPLAY_JSON = REPLAY_DIR / "replay.json"
CLOUD_REPLAY = TASK / "OFFLINE_ROUTE_REPLAY.json"
OUT = TASK / "preflight" / "T041_LOCAL_ACCEPTANCE.json"
EXPECTED_HEAD = "84b050d53e5d07207c032f7d1fc4c150d47dcf1f"
EXPECTED_BASE = "0437ba26003d7920c6d37b3d9437351eecf42392"
EXPECTED_CLOUD_SHA = "fcdd09670e26cb89812abc54cf38b27845839465d9851e4c2398b81a78792135"
EXPECTED_INPUT_SHA = {
    "research_routes.raw.json": "728354d9f919dba53a997bf6f5df1dd30c56eb1b00f2d9bb5f674f28aa11fd75",
    "task_sheet.json": "e1859acb6ac1b936f47519171de48fa60817f47a259ff78a5d3a7f8c17581395",
    "evidence_items.json": "6e1a08aa10774c32aaff14a02da05dcecf1bcbf4c11e2bf43d1f93b4a6997606",
    "design_constraints.json": "67a602f85043ab7ab75f7c860482d55e203edfaf9bd564ac8a4396b3129c474e",
}
TARGET_TESTS = [
    "focused_research/tests/test_t041_exploratory_admission.py",
    "focused_research/tests/test_route_partial_instances.py",
    "focused_research/tests/test_research_routes.py",
    "focused_research/tests/test_route_stage_generation.py",
]


def sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def compact(value):
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(SOURCE), *args], text=True, encoding="utf-8").strip()


def main() -> int:
    checks = {}
    blockers = []

    def check(name: str, ok: bool, detail=None):
        checks[name] = {"pass": bool(ok), "detail": compact(detail) if detail is not None else None}
        if not ok:
            blockers.append(name)

    def load(path: Path):
        return json.loads(path.read_text(encoding="utf-8"))

    source_before = {"head": git("rev-parse", "HEAD"), "status": git("status", "--short", "--untracked-files=all")}
    check("source_head", source_before["head"] == EXPECTED_HEAD, source_before)
    check("source_clean_before", source_before["status"] == "", source_before["status"])
    check("source_base_binding", git("merge-base", "HEAD", EXPECTED_BASE) == EXPECTED_BASE, EXPECTED_BASE)

    validation = load(TASK / "OFFLINE_VALIDATION.json")
    implementation_hashes = []
    for expected in validation["implementation_hashes"]:
        path = SOURCE / expected["path"]
        actual = {"path": expected["path"], "bytes": path.stat().st_size, "sha256": sha256_file(path)}
        actual["expected_bytes"] = expected["bytes"]
        actual["expected_sha256"] = expected["sha256"]
        actual["match"] = actual["bytes"] == expected["bytes"] and actual["sha256"] == expected["sha256"]
        implementation_hashes.append(actual)
    check("implementation_hashes", all(row["match"] for row in implementation_hashes), implementation_hashes)

    # The four named test files, route compiler, and saved replay helper are imported
    # from this fixed checkout.  Imports are pure and bytecode writes are disabled by
    # the caller; pytest already exercised the same imports in the targeted run.
    import_results = {}
    old_path = list(sys.path)
    sys.path.insert(0, str(SOURCE / "focused_research" / "tests"))
    sys.path.insert(0, str(SOURCE / "focused_research"))
    import_names = [
        "research_routes",
        "test_t041_exploratory_admission",
        "test_route_partial_instances",
        "test_research_routes",
        "test_route_stage_generation",
        "replay_t038_route_admission",
    ]
    for name in import_names:
        try:
            mod = importlib.import_module(name)
            import_results[name] = {"pass": True, "file": str(Path(mod.__file__).resolve())}
        except Exception as exc:  # pragma: no cover - recorded as an acceptance blocker
            import_results[name] = {"pass": False, "error": repr(exc)}
    sys.path[:] = old_path
    check("imports", all(row["pass"] for row in import_results.values()), import_results)

    input_records = {}
    input_values = {}
    for name in EXPECTED_INPUT_SHA:
        path = INPUT / name
        blob = path.read_bytes()
        input_records[name] = {"path": str(path), "bytes": len(blob), "sha256": sha256_bytes(blob), "expected_sha256": EXPECTED_INPUT_SHA[name]}
        input_records[name]["match"] = input_records[name]["sha256"] == EXPECTED_INPUT_SHA[name]
        input_values[name] = json.loads(blob)
    check("t039_replay_input_hashes", all(row["match"] for row in input_records.values()), input_records)

    local = load(REPLAY_JSON)
    cloud = load(CLOUD_REPLAY)
    core_keys = [
        "scope", "model_calls", "solver_calls", "network_calls", "input_byte_sha256",
        "raw_input_bytes_unchanged", "legal_structures_unchanged",
        "all_execution_objectives_unchanged", "raw_research_routes", "accepted_contract",
        "candidate_report", "prepared_instances",
    ]
    core_equal = all(local.get(key) == cloud.get(key) for key in core_keys)
    cloud_record = {"path": str(CLOUD_REPLAY), "bytes": CLOUD_REPLAY.stat().st_size,
                    "sha256": sha256_file(CLOUD_REPLAY), "expected_sha256": EXPECTED_CLOUD_SHA,
                    "sha256_match": sha256_file(CLOUD_REPLAY) == EXPECTED_CLOUD_SHA,
                    "core_equal_to_local_replay": core_equal, "compared_keys": core_keys}
    check("cloud_replay_binding", cloud_record["sha256_match"] and core_equal, cloud_record)

    raw = local["raw_research_routes"]
    accepted = local["accepted_contract"]
    report = local["candidate_report"]
    prepared = local["prepared_instances"]
    sheet = input_values["task_sheet.json"]
    evidence = input_values["evidence_items.json"]
    constraints = input_values["design_constraints.json"]
    expected_routes = [f"R{i}" for i in range(1, 11)]
    check("replay_input_bytes_unchanged", local.get("raw_input_bytes_unchanged") is True, local.get("raw_input_bytes_unchanged"))
    check("legal_structures_unchanged", local.get("legal_structures_unchanged") is True, local.get("legal_structures_unchanged"))
    check("execution_objectives_unchanged", local.get("all_execution_objectives_unchanged") is True, local.get("all_execution_objectives_unchanged"))
    check("route_acceptance_counts", report.get("accepted_route_ids") == expected_routes and
          report.get("rejected_route_ids") == [] and report.get("needs_revision_route_ids") == [] and
          report.get("executable_route_ids") == expected_routes,
          {key: report.get(key) for key in ("accepted_route_ids", "rejected_route_ids", "needs_revision_route_ids", "executable_route_ids")})
    rejected_instances = [item for rid in expected_routes for item in report["routes"][rid].get("rejected_instances", [])]
    retained_count = sum(len(route["instances"]) for route in accepted["routes"])
    check("prepared_and_quarantine_counts", len(prepared) == 18 and retained_count == 18 and len(rejected_instances) == 2,
          {"prepared": len(prepared), "retained": retained_count, "rejected": len(rejected_instances)})
    execution_values = sorted(set(item.get("execution_authorized") for item in prepared), key=lambda x: str(x))
    check("execution_unauthorized", execution_values == [False] and all(item.get("execution_authorized") is False for item in prepared), execution_values)

    # Reconstruct every materialized stack from the original route instance.  This
    # guards the admission claim without sampling materials or running a solver.
    originals = {route["id"]: route for route in raw["routes"]}
    expected_objectives = [goal["executable_metric"] for goal in sheet["subgoals"]]
    stack_checks = []
    for item in prepared:
        route = originals[item["route_id"]]
        instance = next(row for row in route["instances"] if row["id"] == item["instance_id"])
        params = instance["parameters_nm"]
        expected_layers = [{"material": layer["material"], "thickness_nm": params[layer["thickness_nm"]]
                           if isinstance(layer["thickness_nm"], str) else layer["thickness_nm"]}
                          for layer in route["family"]["layers"]]
        stack_checks.append({"route_id": item["route_id"], "instance_id": item["instance_id"],
                             "layers_match": item["layers"] == expected_layers,
                             "objectives_match": item["objectives"] == expected_objectives})
    check("original_stack_and_objective_inheritance", all(row["layers_match"] and row["objectives_match"] for row in stack_checks), stack_checks)

    new_routes = {"R2", "R3", "R6", "R7", "R8"}
    new_items = [item for item in prepared if item["route_id"] in new_routes]
    new_ids = [f"{item['route_id']}:{item['instance_id']}" for item in new_items]
    check("new_instance_admission_count", len(new_items) == 9 and len(new_ids) == len(set(new_ids)), new_ids)

    def recipe_payload(item):
        return {"incident_medium": item["incident_medium"], "substrate": item["substrate"],
                "layers": item["layers"], "objectives": item["objectives"]}

    recipe_groups = {}
    for item in new_items:
        payload = recipe_payload(item)
        digest = sha256_bytes(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        recipe_groups.setdefault(digest, {"recipe_sha256": digest, "instances": [], "recipe": payload})["instances"].append(f"{item['route_id']}:{item['instance_id']}")
    recipe_audit = list(recipe_groups.values())
    check("distinct_new_recipe_count", len(recipe_audit) == 6, recipe_audit)

    # Preserve every original open requirement exactly as an unresolved warning.
    warning_audit = {}
    warning_ok = True
    for route in raw["routes"]:
        rid = route["id"]
        open_requirements = route.get("open_requirements", [])
        warnings = [row for row in report["routes"][rid].get("warnings", []) if row.get("code") == "open_requirement_unresolved"]
        rows = [{"code": row.get("code"), "path": row.get("path"), "original": row.get("original"),
                 "status": row.get("status"), "execution_scope": row.get("execution_scope"), "reason": row.get("reason")}
                for row in warnings]
        warning_audit[rid] = {"original_open_requirements": open_requirements, "warnings": rows,
                              "exact_originals": [row.get("original") for row in warnings] == open_requirements,
                              "all_unresolved": all(row.get("status") == "unresolved" and row.get("execution_scope") == "existing_legal_structures_only" for row in warnings)}
        warning_ok = warning_ok and warning_audit[rid]["exact_originals"] and warning_audit[rid]["all_unresolved"]
    check("open_requirement_warning_fidelity", warning_ok, warning_audit)

    route_audit = {}
    for rid in expected_routes:
        route_audit[rid] = {"candidate_role": report["routes"][rid].get("candidate_role"),
                            "readiness": report["routes"][rid].get("readiness"),
                            "prepared_instance_ids": [item["instance_id"] for item in prepared if item["route_id"] == rid],
                            "comparison_count": len(report["routes"][rid].get("comparisons", [])),
                            "rejected_instances": [{"id": row["id"], "reason": row["reason"], "original": row["original"]} for row in report["routes"][rid].get("rejected_instances", [])],
                            "deferred_expectation_count": len(report["routes"][rid].get("deferred_expectations", []))}

    # R3 identical placeholders remain a reference; R7's 29_layer label has 15
    # actual layers; R8 is a zero-change reference; R10 has one legal seed and no
    # executable comparison, with an unverified goal-transfer clue.
    def item(route_id, instance_id):
        return next(row for row in prepared if row["route_id"] == route_id and row["instance_id"] == instance_id)
    r3_items = [item("R3", ident) for ident in ("low_layers", "high_layers")]
    r7_items = [item("R7", ident) for ident in ("15_layer", "29_layer")]
    r8_items = [item("R8", ident) for ident in ("mgf2_baseline", "sio2_variant")]
    r10_info = report["routes"]["R10"]
    special_audit = {
        "R3_identical_placeholders": {"instance_ids": [row["instance_id"] for row in r3_items], "layers_identical": r3_items[0]["layers"] == r3_items[1]["layers"], "comparison_count": len(report["routes"]["R3"]["comparisons"]), "candidate_role": report["routes"]["R3"]["candidate_role"]},
        "R7_label_vs_actual": {"instance_ids": [row["instance_id"] for row in r7_items], "family_layer_count": len(originals["R7"]["family"]["layers"]), "prepared_layer_counts": {row["instance_id"]: len(row["layers"]) for row in r7_items}},
        "R8_unchanged_material_stack": {"instance_ids": [row["instance_id"] for row in r8_items], "layers_identical": r8_items[0]["layers"] == r8_items[1]["layers"], "comparison": report["routes"]["R8"]["comparisons"], "zero_change_warning": any(row.get("code") == "zero_change_baseline_reference" for row in report["routes"]["R8"]["warnings"])},
        "R10_single_reference": {"prepared_instance_ids": [row["instance_id"] for row in prepared if row["route_id"] == "R10"], "comparison_count": len(r10_info["comparisons"]), "deferred_expectation_count": len(r10_info["deferred_expectations"]), "wide_pass_rejected": [row for row in r10_info["rejected_instances"] if row["id"] == "wide_pass"], "unverified_source_warning": [row for row in r10_info["warnings"] if row.get("code") == "clue_goal_relevance_unverified"]},
    }
    special_ok = (special_audit["R3_identical_placeholders"]["layers_identical"] and special_audit["R3_identical_placeholders"]["comparison_count"] == 0 and
                  special_audit["R7_label_vs_actual"]["family_layer_count"] == 15 and all(value == 15 for value in special_audit["R7_label_vs_actual"]["prepared_layer_counts"].values()) and
                  special_audit["R8_unchanged_material_stack"]["layers_identical"] and special_audit["R8_unchanged_material_stack"]["zero_change_warning"] and
                  special_audit["R10_single_reference"]["prepared_instance_ids"] == ["single_peak"] and special_audit["R10_single_reference"]["comparison_count"] == 0 and special_audit["R10_single_reference"]["deferred_expectation_count"] == 1 and bool(special_audit["R10_single_reference"]["unverified_source_warning"]))
    check("special_route_audits", special_ok, special_audit)

    invalid_audit = [{"route_id": rid, "instances": [{"id": row["id"], "reason": row["reason"], "original": row["original"]} for row in report["routes"][rid]["rejected_instances"]]} for rid in ("R6", "R10")]
    check("isolated_invalid_reasons", len(rejected_instances) == 2 and {row["id"] for row in rejected_instances} == {"no_ar", "wide_pass"}, invalid_audit)

    # Strict mode still rejects a candidate-ready unresolved route. A correctly
    # relabeled needs_revision route validates, but materialization remains blocked.
    strict_audit = {}
    try:
        sys.path.insert(0, str(SOURCE / "focused_research"))
        rr = importlib.import_module("research_routes")
        # R6 has an unresolved requirement but its retained D1/D2 quotes are
        # exact, so strict validation can isolate the readiness gate itself.
        strict_route_id = "R6"
        strict_route = next(row for row in accepted["routes"] if row["id"] == strict_route_id)
        clue_ids = list(strict_route["clue_ids"])
        strict_candidate = {"schema_version": accepted["schema_version"], "decision_clues": [row for row in accepted["decision_clues"] if row["id"] in clue_ids], "routes": [copy.deepcopy(strict_route)]}
        try:
            rr.validate(strict_candidate, sheet, evidence, design_constraints=constraints)
            candidate_ready_rejected = False
        except (ValueError, TypeError, KeyError) as exc:
            candidate_ready_rejected = True
            strict_candidate_error = str(exc)
        strict_needs_revision = copy.deepcopy(strict_candidate)
        strict_needs_revision["routes"][0]["readiness"] = "needs_revision"
        try:
            rr.validate(strict_needs_revision, sheet, evidence, design_constraints=constraints)
            corrected_validates = True
            corrected_error = None
        except (ValueError, TypeError, KeyError) as exc:
            corrected_validates = False
            corrected_error = str(exc)
        try:
            rr.materialize(strict_needs_revision, strict_route_id, sheet, evidence, design_constraints=constraints)
            materialization_blocked = False
            materialization_error = None
        except (ValueError, TypeError, KeyError) as exc:
            materialization_blocked = "materialization blocked" in str(exc)
            materialization_error = str(exc)
        strict_audit = {"route_id": strict_route_id, "candidate_ready_strict_rejected": candidate_ready_rejected, "candidate_ready_error": strict_candidate_error if candidate_ready_rejected else None,
                        "correctly_labeled_needs_revision_validates": corrected_validates, "corrected_validation_error": corrected_error,
                        "strict_materialization_blocked": materialization_blocked, "strict_materialization_error": materialization_error}
    except Exception as exc:  # pragma: no cover
        strict_audit = {"error": repr(exc)}
    finally:
        if str(SOURCE / "focused_research") in sys.path:
            sys.path.remove(str(SOURCE / "focused_research"))
    check("strict_unresolved_protection", strict_audit.get("candidate_ready_strict_rejected") is True and strict_audit.get("correctly_labeled_needs_revision_validates") is True and strict_audit.get("strict_materialization_blocked") is True, strict_audit)

    # Parse the exact bounded command outputs, without rerunning either command.
    test_attempt = TASK / "preflight" / "test-attempt01"
    test_log = (test_attempt / "pytest.log").read_text(encoding="utf-8", errors="replace")
    test_match = re.search(r"(?m)^\s*(?P<passed>\d+) passed(?:,\s*(?P<failed>\d+) failed)?(?:,\s*(?P<skipped>\d+) skipped)?(?:,\s*(?P<errors>\d+) errors?)?\s+in\s+", test_log)
    test_counts = {"passed": int(test_match.group("passed")) if test_match else 0, "failed": int(test_match.group("failed") or 0) if test_match else None,
                   "skipped": int(test_match.group("skipped") or 0) if test_match else None, "errors": int(test_match.group("errors") or 0) if test_match else None,
                   "summary_line": test_match.group(0).strip() if test_match else None}
    test_argv = [str(PYTHON), "-B", "-X", "utf8", "-m", "pytest", "-q", *TARGET_TESTS, "--disable-socket"]
    tests_record = {"argv": test_argv, "cwd": str(SOURCE), "env_bindings": {"T038_ROUTE_INPUT_DIR": str(INPUT), "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_ADDOPTS": "-p no:cacheprovider"}, "exit": int((test_attempt / "exit_code.txt").read_text(encoding="ascii").strip()), "counts": test_counts, "raw_log": str(test_attempt / "pytest.log"), "argv_record": str(test_attempt / "argv.txt")}
    check("targeted_tests", tests_record["exit"] == 0 and test_counts["passed"] == 114 and test_counts["failed"] in (0, None) and test_counts["errors"] in (0, None), tests_record)
    replay_attempt = TASK / "preflight" / "replay-attempt01"
    replay_record = {"argv": [str(PYTHON), "-B", "-X", "utf8", "focused_research/tests/replay_t038_route_admission.py", "--input-dir", str(INPUT), "--output", str(REPLAY_DIR)],
                     "cwd": str(SOURCE), "exit": int((replay_attempt / "exit_code.txt").read_text(encoding="ascii").strip()), "raw_log": str(replay_attempt / "replay.log"), "argv_record": str(replay_attempt / "argv.txt"), "output": str(REPLAY_DIR), "replay_json_sha256": sha256_file(REPLAY_JSON), "replay_json_bytes": REPLAY_JSON.stat().st_size}
    check("saved_route_replay", replay_record["exit"] == 0 and local["model_calls"] == 0 and local["solver_calls"] == 0 and local["network_calls"] == 0, replay_record)

    source_after = {"head": git("rev-parse", "HEAD"), "status": git("status", "--short", "--untracked-files=all")}
    check("source_unchanged_after", source_after == source_before, {"before": source_before, "after": source_after})

    t040_path = Path(r"D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T040\preflight\T040_LOCAL_ACCEPTANCE.json")
    t040 = load(t040_path)
    t040_receipt = {"path": str(t040_path), "global_pass": t040.get("global_pass"), "status": t040.get("status"), "tested_source_sha": t040.get("tested_source_sha"), "counters": t040.get("counters"), "route_replay": t040.get("route_replay"), "tests": {"reported_counts": t040.get("tests", {}).get("reported_counts")}}
    check("t040_terminal_reference", t040.get("global_pass") is True and t040.get("status") == "passed", t040_receipt)

    counters = {"model_calls": local.get("model_calls"), "solver_calls": local.get("solver_calls"), "network_calls": local.get("network_calls"), "retrieval_calls": 0, "search_rows": 0, "physics_calls": 0, "new_certifications": 0, "new_cny": 0, "ledger_writes": 0}
    check("zero_cost_counters", all(value == 0 for value in counters.values()), counters)

    acceptance = {
        "schema_version": "T041_LOCAL_ACCEPTANCE.v1",
        "task_id": "R3-T041",
        "status": "passed" if not blockers else "blocked",
        "global_pass": not blockers,
        "scope": "original-local candidate admission only; no continuation, model, network, retrieval, solver, physics, material sampling, certification, or ledger writes",
        "tested_source_sha": source_after["head"],
        "expected_source_sha": EXPECTED_HEAD,
        "base_commit": EXPECTED_BASE,
        "source_before": source_before,
        "source_after": source_after,
        "implementation_hashes": implementation_hashes,
        "imports": import_results,
        "replay_input_binding": input_records,
        "cloud_replay_comparison": cloud_record,
        "tests": tests_record,
        "route_replay": replay_record,
        "route_audit": {"candidate_ready_routes": len(report.get("executable_route_ids", [])), "accepted_route_ids": report.get("accepted_route_ids"), "legal_prepared_instances": len(prepared), "isolated_invalid_instances": len(rejected_instances), "new_route_ids": sorted(new_routes), "new_instance_ids": new_ids, "new_recipe_count": len(recipe_audit), "recipe_groups": recipe_audit, "warnings": warning_audit, "routes": route_audit, "special_routes": special_audit, "invalid_instances": invalid_audit, "execution_authorized_values": execution_values, "raw_input_bytes_unchanged": local.get("raw_input_bytes_unchanged"), "legal_structures_unchanged": local.get("legal_structures_unchanged"), "all_execution_objectives_unchanged": local.get("all_execution_objectives_unchanged")},
        "strict_protection": strict_audit,
        "counters": counters,
        "t040_terminal_reference": t040_receipt,
        "checks": checks,
        "blockers": blockers,
        "raw_logs": {"targeted_tests": str(test_attempt / "pytest.log"), "route_replay": str(replay_attempt / "replay.log")},
    }
    OUT.write_text(json.dumps(acceptance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"global_pass": acceptance["global_pass"], "blockers": blockers, "tests": test_counts, "routes": {"ready": len(report.get("executable_route_ids", [])), "legal": len(prepared), "invalid": len(rejected_instances), "new_instances": len(new_items), "new_recipes": len(recipe_audit)}, "source": source_after["head"]}, ensure_ascii=False, indent=2))
    return 0 if acceptance["global_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
