"""T038 bounded local acceptance: saved recovery replay and material capability only.

This helper is task-local.  It never creates a model client, performs retrieval,
calls a solver, or writes outside the T038 preflight/material-preflight folders.
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.util
import json
import re
import subprocess
import sys
import traceback
from pathlib import Path


SOURCE = Path(r"D:\OptoMind-Article-3\r3t038a")
TASK = Path(r"D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T038")
PRE = TASK / "preflight"
MAT = TASK / "material-preflight"
CASE = MAT / "e2e-04-solarblind-uv-triple"
CASE_RECOVERY = TASK / "cases" / "e2e-04-solarblind-uv-triple"
PYTHON = Path(sys.executable).resolve()

EXPECTED_SOURCE_SHA = "366f3faa70217b6209afb1162bac6759765cd628"
BASE_SOURCE_SHA = "49861e169084976069d9ef1008e4fafc84b86bb8"
CASE_ID = "e2e-04-solarblind-uv-triple"
PLANNING_SHA = "04d04afb21e081d0493b95af3e5f25449f45a0bbad2aa558680baac60a6d26e2"
SOURCE_T037_COMMIT = "028b4955f75fb6e421e0e727c7451d38431b7970"
QUESTION_UTF8_SHA = "a072e2f7732cc73738382bb04f916bcd0a1f4814c526cb9c022290cd6bd73d12"
QUESTION_FILE_SHA = "a3f9e66596b0fce92d0f89753c77b6add18b48705c09b921041da851b35ca5fd"
GRID_NM = list(range(255, 1101))

EXPECTED_ROUND2_CANONICAL = {
    "normalization_audit.json": "7bd9821c32e535c63c2a5cdaa84fdc8524720fc45ee5fca49c8e98c0404414dc",
    "query_plan.json": "e8e0d0e716e49f048d3313d9317fc3020921379df6eecfce09719522b4d2ef7e",
    "raw_response.json": "5f7e6d2eeb25dffbcca87059970f9d0b5c562b55b269df0eba16db05e5fdbe9a",
    "raw_task_sheet.json": "fc8b7f266addee4d5038c62154246444ff0186edd078d502ab8fe087f24b1307",
    "recovery.json": "2e147c73926f87f946af018bb92d14536af358e43ec8383165b8473241f1a5f4",
    "task_sheet.json": "70754cccfea49e884680ec01c7c3012db6d68862a8428ec646aa34a113a90893",
}

REQUEST_REL = Path("focused_research/campaigns/t032/inputs/e2e-04-solarblind-uv-triple/FRESH_REQUEST.json")
QUESTION_REL = Path("focused_research/campaigns/t032/inputs/e2e-04-solarblind-uv-triple/FRESH_QUESTION.txt")
T032_PROVENANCE_REL = Path("focused_research/campaigns/t032/INPUT_PROVENANCE.json")
PLANNING_PATH = Path(r"D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T037\cases\e2e-04-solarblind-uv-triple\plan\planning.json")
T037_MATERIAL_PATH = Path(r"D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T037\material-preflight\T037_MATERIAL_INVENTORY.json")
T036_HELPER_PATH = Path(r"D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T036\preflight\run_t036_material_preflight.py")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def canonical_digest(value) -> str:
    return sha256_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    )


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(SOURCE), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def normalized_record_path_match(actual, expected) -> bool:
    if actual is None or expected is None:
        return actual == expected
    a = str(actual).replace("/", "\\").casefold()
    e = str(expected).replace("/", "\\").casefold()
    if a == e:
        return True
    # The old record keeps the T037 checkout root.  Compare the stable suffix
    # below veritmm; RII record paths are already relative to the DB.
    for marker in ("\\veritmm\\", "/veritmm/"):
        if marker in a and marker in e:
            return a.split(marker, 1)[1] == e.split(marker, 1)[1]
    return Path(a).name == Path(e).name


def module_binding(module) -> dict:
    path = Path(module.__file__).resolve()
    return {
        "module": module.__name__,
        "path": str(path),
        "source_relative": str(path.relative_to(SOURCE)) if path.is_relative_to(SOURCE) else None,
        "sha256": sha256_file(path),
    }


def implementation_checks(offline_validation: dict) -> dict:
    expected = offline_validation.get("implementation_file_sha256", {})
    rows = {}
    for rel, expected_sha in expected.items():
        path = SOURCE / rel
        actual = sha256_file(path) if path.exists() else None
        rows[rel] = {
            "path": str(path),
            "expected_sha256": expected_sha,
            "actual_sha256": actual,
            "bytes": path.stat().st_size if path.exists() else None,
            "match": actual == expected_sha,
        }
    return rows


def task_artifact_checks(artifact_check: list[dict]) -> list[dict]:
    rows = []
    for item in artifact_check:
        rel = str(item["file"])
        path = TASK / rel
        expected = item.get("sha256", item.get("expected_sha256"))
        actual = sha256_file(path) if path.exists() else None
        rows.append({
            "file": rel,
            "path": str(path),
            "expected_sha256": expected,
            "actual_sha256": actual,
            "bytes": path.stat().st_size if path.exists() else None,
            "match": actual == expected,
        })
    return rows


def question_checks() -> dict:
    request_path = SOURCE / REQUEST_REL
    question_path = SOURCE / QUESTION_REL
    t032_path = SOURCE / T032_PROVENANCE_REL
    request = load_json(request_path)
    question = request["question"]
    t032 = load_json(t032_path)
    expected_case = next(row for row in t032["cases"] if row["case_id"] == CASE_ID)
    q_file = question_path.read_bytes()
    return {
        "request_path": str(request_path),
        "question_path": str(question_path),
        "request_sha256": sha256_file(request_path),
        "request_sha256_expected": expected_case["files"]["inputs/e2e-04-solarblind-uv-triple/FRESH_REQUEST.json"],
        "question_file_sha256": sha256_file(question_path),
        "question_file_sha256_expected": expected_case["files"]["inputs/e2e-04-solarblind-uv-triple/FRESH_QUESTION.txt"],
        "question_utf8_sha256": sha256_bytes(question.encode("utf-8")),
        "question_utf8_sha256_expected": expected_case["question_utf8_sha256"],
        "question_native_utf8_sha256_expected": QUESTION_UTF8_SHA,
        "question_exact_request_text": question_path.read_text(encoding="utf-8").rstrip("\r\n") == question,
        "question_terminal_lf_only": q_file == question.encode("utf-8") + b"\n",
        "old_answers_included": bool(expected_case.get("old_answers_included", False)),
    }


def parse_test_log() -> dict:
    log_path = PRE / "T038_TARGETED_TESTS_attempt01.log"
    argv_path = PRE / "T038_TARGETED_TESTS_attempt01.argv.txt"
    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    match = re.search(r"(?:(\d+) failed, )?(\d+) passed(?:, (\d+) skipped)?(?:, (\d+) errors?)? in [0-9.]+s", text)
    failed = int(match.group(1) or 0) if match else None
    passed = int(match.group(2)) if match else None
    skipped = int(match.group(3) or 0) if match else None
    errors = int(match.group(4) or 0) if match else None
    argv = next((line.strip() for line in text.splitlines() if line.startswith("ARGV:")), None)
    if argv is None and argv_path.exists():
        argv = argv_path.read_text(encoding="utf-8").strip()
    return {
        "path": str(log_path),
        "argv_path": str(argv_path),
        "exists": log_path.exists(),
        "argv": argv,
        "sockets_disabled": bool(argv and "--disable-socket" in argv),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "errors": errors,
        "observed_summary_line": next((line.strip() for line in text.splitlines() if "passed" in line), None),
        "returncode": 0 if match and (failed or 0) == 0 and (errors or 0) == 0 else None,
    }


def parse_recovery_log(round_number: int) -> dict:
    path = PRE / f"T038_RECOVERY_round{round_number}_attempt01.log"
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    exit_match = re.search(r"EXIT_CODE=(\d+)", text)
    argv = next((line.strip() for line in text.splitlines() if line.startswith("ARGV:")), None)
    binding = None
    for line in text.splitlines():
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("case_id") == CASE_ID:
            binding = value
            break
    return {
        "path": str(path),
        "exists": path.exists(),
        "argv": argv,
        "exit_code": int(exit_match.group(1)) if exit_match else None,
        "sockets_disabled": "--disable-socket" in text,
        "binding": binding,
        "case_id": binding.get("case_id") if binding else None,
        "source_commit": binding.get("source_commit") if binding else None,
        "source_planning_sha256": binding.get("source_planning_sha256") if binding else None,
        "source_round": binding.get("source_round") if binding else None,
        "new_model_calls": binding.get("new_model_calls") if binding else None,
        "physics_executed": binding.get("physics_executed") if binding else None,
    }


def allowed_derived_sheet(raw: dict, question: str) -> dict:
    expected = copy.deepcopy(raw)
    expected["original_question"] = question
    for goal in expected.get("subgoals", []):
        goal["quote_status"] = (
            "exact_user_quote" if goal.get("original_span") in question else "unverified_model_paraphrase"
        )
    return expected


def verify_round(round_number: int, output: Path, planning: dict, question: str, task_sheet_module) -> dict:
    row = next(r for r in planning["rounds"] if r["round"] == round_number)
    raw_response_source = row["raw_response"]
    raw_analysis_source = json.loads(raw_response_source) if isinstance(raw_response_source, str) else copy.deepcopy(raw_response_source)
    files = {
        name: output / name
        for name in [
            "manifest.json", "normalization_audit.json", "query_plan.json", "raw_response.json",
            "raw_task_sheet.json", "recovery.json", "task_sheet.json",
        ]
    }
    exists = {name: path.exists() for name, path in files.items()}
    loaded = {name: load_json(path) for name, path in files.items() if path.exists()}
    raw_response_match = loaded.get("raw_response.json") == raw_response_source
    raw_task_sheet_match = loaded.get("raw_task_sheet.json") == raw_analysis_source
    expected_sheet = allowed_derived_sheet(raw_analysis_source, question)
    derived_sheet_match = loaded.get("task_sheet.json") == expected_sheet
    query_plan_match = False
    adapter_validation = None
    if "task_sheet.json" in loaded and "query_plan.json" in loaded:
        query_plan_match = loaded["query_plan.json"] == task_sheet_module.adapt(loaded["task_sheet.json"])
        try:
            adapter_validation = task_sheet_module.validate_adapter(loaded["query_plan.json"]) == loaded["query_plan.json"]
        except Exception:
            adapter_validation = False

    quote_statuses = [g.get("quote_status") for g in loaded.get("task_sheet.json", {}).get("subgoals", [])]
    audit = loaded.get("normalization_audit.json", {})
    analysis_audit = audit.get("analysis_normalization", []) if isinstance(audit, dict) else []
    quote_audit = [r for r in analysis_audit if r.get("operation") == "classify_subgoal_quote"]
    query_audit = [r for r in analysis_audit if r.get("operation") == "retain_optional_query_links"]
    recovery = loaded.get("recovery.json", {})
    manifest = loaded.get("manifest.json", {})
    canonical = {}
    manifest_matches = {}
    for name in EXPECTED_ROUND2_CANONICAL:
        if name in loaded:
            canonical[name] = canonical_digest(loaded[name])
            manifest_matches[name] = canonical[name] == manifest.get(name)
    expected_recovery = {
        "case_id": CASE_ID,
        "source_commit": SOURCE_T037_COMMIT,
        "source_planning_sha256": PLANNING_SHA,
        "source_round": round_number,
        "source_response_sha256": canonical_digest(raw_response_source),
        "task_sheet_sha256": canonical_digest(expected_sheet),
        "original_question_sha256": QUESTION_UTF8_SHA,
        "new_model_calls": 0,
        "physics_executed": False,
        "status": "recovered_for_continuation_not_scientific_acceptance",
    }
    recovery_fields_match = recovery == expected_recovery
    semantic_flags = {
        "physical_metrics_edited": audit.get("physical_metrics_edited") is False,
        "constraints_edited": audit.get("constraints_edited") is False,
        "queries_edited": audit.get("queries_edited") is False,
        "quote_audit_one": len(quote_audit) == 1,
        "query_audit_retained": len(query_audit) == 1,
        "quote_statuses": quote_statuses,
        "quote_statuses_expected": quote_statuses == ["exact_user_quote", "exact_user_quote", "unverified_model_paraphrase"],
        "g3_raw_span_preserved": bool(quote_audit and quote_audit[0].get("raw_original_span") == raw_analysis_source["subgoals"][2]["original_span"]),
    }
    result = {
        "round": round_number,
        "output": str(output),
        "files_exist": exists,
        "raw_response_exact": raw_response_match,
        "raw_task_sheet_exact": raw_task_sheet_match,
        "derived_task_sheet_program_only": derived_sheet_match,
        "query_plan_adapter_match": query_plan_match,
        "query_plan_adapter_validation": adapter_validation,
        "semantic_flags": semantic_flags,
        "recovery_binding_exact": recovery_fields_match,
        "canonical_digests": canonical,
        "manifest_matches_recomputed": manifest_matches,
        "manifest_matches_all": bool(manifest_matches) and all(manifest_matches.values()),
        "recovery": recovery,
        "pass": bool(
            all(exists.values()) and raw_response_match and raw_task_sheet_match and derived_sheet_match
            and query_plan_match and adapter_validation and recovery_fields_match
            and all(semantic_flags.values() if False else [
                semantic_flags["physical_metrics_edited"], semantic_flags["constraints_edited"],
                semantic_flags["queries_edited"], semantic_flags["quote_audit_one"],
                semantic_flags["query_audit_retained"], semantic_flags["quote_statuses_expected"],
                semantic_flags["g3_raw_span_preserved"],
            ]) and bool(manifest_matches) and all(manifest_matches.values())
        ),
    }
    if round_number == 2:
        result["cloud_canonical_expected"] = EXPECTED_ROUND2_CANONICAL
        result["cloud_canonical_actual"] = canonical
        result["cloud_canonical_match"] = canonical == EXPECTED_ROUND2_CANONICAL
        result["pass"] = bool(result["pass"] and result["cloud_canonical_match"])
    return result


def material_preflight(old_inventory: dict) -> dict:
    # T036's sampler is read-only code reused for local coverage sampling; all
    # output below belongs to T038.  No T036/T037 files are written.
    old_spec = importlib.util.spec_from_file_location("t036_readonly_helpers_t038", T036_HELPER_PATH)
    old = importlib.util.module_from_spec(old_spec)
    assert old_spec and old_spec.loader
    old_spec.loader.exec_module(old)
    import numpy as np

    old.GRID_NM = GRID_NM
    old.GRID_UM = np.asarray(GRID_NM, dtype=np.float64) / 1000.0
    sys.path.insert(0, str(SOURCE))
    from focused_research import route_consumer
    from focused_research.research_execution import _engine
    _engine()
    from tmm_engine.material_registry import MaterialRegistry
    from tmm_engine.preflight import preflight_task
    from tmm_engine.task_io import simulation_task_from_dict

    registry = MaterialRegistry()
    selected = old_inventory["selected_execution_materials"]
    records = {}
    for alias, spec in selected.items():
        expected_old = old_inventory["film_records"][alias]
        row = old.sample_record(registry, dict(spec, alias=alias), role="film")
        resolved = row.get("resolved_ref") or {}
        actual_material = resolved.get("book") if spec["provider"] == "rii_sqlite" else resolved.get("name")
        expected_identity = {
            "material": spec["material"], "provider": spec["provider"], "dataset_id": spec["dataset_id"]
        }
        actual_identity = {
            "material": actual_material, "provider": resolved.get("provider"), "dataset_id": resolved.get("dataset_id")
        }
        row.update({
            "expected_identity": expected_identity,
            "resolved_identity": actual_identity,
            "identity_match": actual_identity == expected_identity,
            "source_sha256_expected": expected_old.get("source_sha256"),
            "source_sha256_match": row.get("source_sha256") == expected_old.get("source_sha256"),
            "record_filepath_expected": expected_old.get("record_filepath"),
            "record_filepath_match": normalized_record_path_match(row.get("record_filepath"), expected_old.get("record_filepath")),
        })
        row["pass"] = bool(
            row.get("status") == "available" and row.get("full_grid_n_k")
            and row.get("identity_match") and row.get("source_sha256_match")
            and row.get("record_filepath_match") and row.get("extrapolated_count") == 0
        )
        records[alias] = row

    substrate_spec = {
        "alias": "fused_silica",
        "material": old_inventory["substrate"]["material"],
        "provider": old_inventory["substrate"]["provider"],
        "dataset_id": old_inventory["substrate"]["dataset_id"],
    }
    substrate = old.sample_record(registry, substrate_spec, role="substrate")
    resolved = substrate.get("resolved_ref") or {}
    substrate_expected = old_inventory["substrate"]
    substrate.update({
        "expected_identity": {
            "material": substrate_spec["material"], "provider": substrate_spec["provider"], "dataset_id": substrate_spec["dataset_id"]
        },
        "resolved_identity": {
            "material": resolved.get("book"), "provider": resolved.get("provider"), "dataset_id": resolved.get("dataset_id")
        },
        "identity_match": {
            "material": resolved.get("book"), "provider": resolved.get("provider"), "dataset_id": resolved.get("dataset_id")
        } == {
            "material": substrate_spec["material"], "provider": substrate_spec["provider"], "dataset_id": substrate_spec["dataset_id"]
        },
        "source_sha256_expected": substrate_expected.get("source_sha256"),
        "source_sha256_match": substrate.get("source_sha256") == substrate_expected.get("source_sha256"),
        "record_filepath_expected": substrate_expected.get("record_filepath"),
        "record_filepath_match": normalized_record_path_match(substrate.get("record_filepath"), substrate_expected.get("record_filepath")),
        "substrate_k_note": "Provider record has_n=true and has_k=false; sampled k is explicit lossless-zero substrate mapping, not film n/k coverage.",
    })
    substrate["pass"] = bool(
        substrate.get("status") == "available" and substrate.get("substrate_n_grid")
        and substrate.get("identity_match") and substrate.get("source_sha256_match")
        and substrate.get("record_filepath_match") and substrate.get("extrapolated_count") == 0
        and substrate.get("raw_has_k") is False
    )

    media = {
        "air": {"constant_n": 1.0, "constant_k": 0.0},
        "fused_silica": {
            "material": substrate_spec["material"], "provider": substrate_spec["provider"], "dataset_id": substrate_spec["dataset_id"]
        },
    }
    media.update(copy.deepcopy(selected))
    write_json(CASE / "wavelengths.json", GRID_NM)
    write_json(CASE / "optical_media.json", media)
    write_json(MAT / "execution_materials.json", {
        "schema_version": "t038.execution_materials.v1",
        "task_id": "R3-T038",
        "case_id": CASE_ID,
        "source_commit": EXPECTED_SOURCE_SHA,
        "allowed_materials": None,
        "allowed_material_category": "ordinary_depositable_inorganic_dielectrics_no_metals",
        "execution_materials": copy.deepcopy(selected),
        "inventory_is_user_permission": False,
        "source_inventory_report": str(MAT / "T038_MATERIAL_INVENTORY.json"),
    })

    instance = {
        "status": "prepared",
        "route_id": "T038-OFFLINE-FIXTURE",
        "instance_id": "T038-OFFLINE-FIXTURE-ALL-AVAILABLE",
        "incident_medium": "air",
        "substrate": "fused_silica",
        "layers": [{"material": alias, "thickness_nm": 100.0} for alias in selected],
        "design_constraints": {
            "allowed_materials": None,
            "allowed_material_category": "ordinary_depositable_inorganic_dielectrics_no_metals",
            "execution_materials": copy.deepcopy(selected),
        },
        "objectives": [
            {"objective_id": "G1", "metric": "mean_transmittance", "sense": "maximize", "mean_reduction": "wavelength_integral_mean", "region": {"wavelength_nm": [255, 280], "angle_deg": 0, "polarization": "unpolarized"}},
            {"objective_id": "G2", "metric": "mean_transmittance", "sense": "minimize", "mean_reduction": "wavelength_integral_mean", "region": {"wavelength_nm": [300, 700], "angle_deg": 0, "polarization": "unpolarized"}},
            {"objective_id": "G3", "metric": "mean_transmittance", "sense": "minimize", "mean_reduction": "wavelength_integral_mean", "region": {"wavelength_nm": [700, 1100], "angle_deg": 0, "polarization": "unpolarized"}},
        ],
    }
    consumer = {
        "fixture_only": True,
        "simulation_performed": False,
        "solver_invoked": False,
        "selected_materials": copy.deepcopy(selected),
    }
    try:
        prepared = route_consumer.prepare_tmm_inputs(instance, optical_media=media, wavelengths_nm=GRID_NM)
        binding = prepared["material_capability_binding"]
        identity_rows = {}
        for alias, expected in selected.items():
            actual = binding["bindings"].get(alias, {})
            actual_record = {key: actual.get(key) for key in ("material", "provider", "dataset_id")}
            identity_rows[alias] = {"expected": expected, "actual": actual_record, "exact": actual_record == expected}
        second_prepare = route_consumer.prepare_tmm_inputs(instance, optical_media=media, wavelengths_nm=GRID_NM)
        fixture_report = preflight_task("simulate", simulation_task_from_dict(prepared["tmm_task"]), registry=registry)
        consumer.update({
            "prepare_pass": True,
            "repeat_prepare_pass": second_prepare == prepared,
            "simulation_performed": prepared.get("simulation_performed"),
            "coverage_verified": binding.get("coverage_verified"),
            "numerical_preflight_required": binding.get("numerical_preflight_required"),
            "identity_rows": identity_rows,
            "identity_match": all(row["exact"] for row in identity_rows.values()),
            "binding_aliases": sorted(binding.get("bindings", {})),
            "fixture_preflight": {
                "ok": bool(fixture_report.get("ok")),
                "status": fixture_report.get("status"),
                "material_count": len(fixture_report.get("materials", [])),
                "failure_count": len(fixture_report.get("failures", [])),
                "failures": fixture_report.get("failures", []),
                "solver_invoked": False,
            },
        })
        consumer["consumer_pass"] = bool(
            consumer["prepare_pass"] and consumer["repeat_prepare_pass"]
            and consumer["simulation_performed"] is False and consumer["coverage_verified"] is False
            and consumer["numerical_preflight_required"] is True and consumer["identity_match"]
            and consumer["fixture_preflight"]["ok"]
        )
    except Exception as exc:
        consumer.update({"prepare_pass": False, "consumer_pass": False, "error_type": type(exc).__name__, "error": " ".join(str(exc).split())})
    write_json(MAT / "T038_CONSUMER_BINDING.json", consumer)

    inventory = {
        "schema_version": "t038.material_inventory.v1",
        "task_id": "R3-T038",
        "case_id": CASE_ID,
        "source_commit": EXPECTED_SOURCE_SHA,
        "base_source_sha": BASE_SOURCE_SHA,
        "grid": {"min_nm": GRID_NM[0], "max_nm": GRID_NM[-1], "step_nm": 1, "count": len(GRID_NM)},
        "category_permission": {
            "allowed_materials": None,
            "allowed_material_category": "ordinary_depositable_inorganic_dielectrics_no_metals",
            "inventory_is_user_permission": False,
            "execution_inventory_is_finite_capability_subset": True,
        },
        "film_records": records,
        "substrate": substrate,
        "air": {"alias": "air", "constant_n": 1.0, "constant_k": 0.0, "explicit_constant": True},
        "selected_execution_materials": copy.deepcopy(selected),
        "reused_inventory_binding": str(T037_MATERIAL_PATH) + "; records rechecked against current T038 source; no catalog rediscovery/download",
        "excluded_from_current_execution_inventory": copy.deepcopy(old_inventory.get("excluded_from_current_execution_inventory", {})),
        "limitations": [
            "Film acceptance required provider n/k flags, finite arrays and zero extrapolation at all 846 requested points.",
            "Fused silica 835 has provider n data and no provider k; sampled zero k is retained only for substrate mapping.",
            "The finite execution inventory is an availability disclosure, not a user whitelist or narrowed category permission.",
        ],
    }
    write_json(MAT / "T038_MATERIAL_INVENTORY.json", inventory)
    material_pass = bool(all(row["pass"] for row in records.values()) and substrate["pass"] and consumer.get("consumer_pass"))
    return {
        "inventory_path": str(MAT / "T038_MATERIAL_INVENTORY.json"),
        "execution_materials_path": str(MAT / "execution_materials.json"),
        "media_path": str(CASE / "optical_media.json"),
        "wavelengths_path": str(CASE / "wavelengths.json"),
        "consumer_path": str(MAT / "T038_CONSUMER_BINDING.json"),
        "film_records": records,
        "substrate": substrate,
        "consumer": consumer,
        "material_pass": material_pass,
        "no_simulation": consumer.get("simulation_performed") is False and consumer.get("solver_invoked") is False,
    }


def main() -> int:
    PRE.mkdir(parents=True, exist_ok=True)
    MAT.mkdir(parents=True, exist_ok=True)
    CASE.mkdir(parents=True, exist_ok=True)
    artifact_check = load_json(TASK / "ARTIFACT_HASH_CHECK.json")
    input_provenance = load_json(TASK / "INPUT_PROVENANCE.json")
    offline_validation = load_json(TASK / "OFFLINE_VALIDATION.json")
    planning_before = sha256_file(PLANNING_PATH)
    question = load_json(SOURCE / REQUEST_REL)["question"]

    sys.path.insert(0, str(SOURCE))
    task_sheet_module = importlib.import_module("focused_research.task_sheet")
    recovery_module = importlib.import_module("focused_research.task_sheet_recovery")
    guard_module = importlib.import_module("focused_research.local_campaign_guard")
    source_imports = {
        "focused_research.task_sheet": module_binding(task_sheet_module),
        "focused_research.task_sheet_recovery": module_binding(recovery_module),
        "focused_research.local_campaign_guard": module_binding(guard_module),
    }

    implementation = implementation_checks(offline_validation)
    artifacts = task_artifact_checks(artifact_check)
    qchecks = question_checks()
    planning = load_json(PLANNING_PATH)
    round_checks = {
        "round1": verify_round(1, CASE_RECOVERY / "recovered_round1", planning, question, task_sheet_module),
        "round2": verify_round(2, CASE_RECOVERY / "recovered", planning, question, task_sheet_module),
    }
    old_inventory = load_json(T037_MATERIAL_PATH)
    material = material_preflight(old_inventory)
    planning_after = sha256_file(PLANNING_PATH)

    tests = parse_test_log()
    recovery_logs = {str(n): parse_recovery_log(n) for n in (1, 2)}
    input_provenance_actual = sha256_file(TASK / "INPUT_PROVENANCE.json")
    input_provenance_expected = next(item["sha256"] for item in artifact_check if item["file"] == "INPUT_PROVENANCE.json")
    checks = {
        "source_head": git("rev-parse", "HEAD") == EXPECTED_SOURCE_SHA,
        "source_clean": git("status", "--porcelain") == "",
        "implementation_hashes": all(row["match"] for row in implementation.values()),
        "source_import_bindings": all(row["source_relative"] is not None for row in source_imports.values()),
        "task_artifact_hashes": all(row["match"] for row in artifacts),
        "input_provenance_hash": input_provenance_actual == input_provenance_expected,
        "input_provenance_schema": input_provenance["recovery_binding"]["case_id"] == CASE_ID,
        "planning_hash": planning_before == PLANNING_SHA,
        "question_binding": bool(
            qchecks["request_sha256"] == qchecks["request_sha256_expected"]
            and qchecks["question_file_sha256"] == qchecks["question_file_sha256_expected"]
            and qchecks["question_utf8_sha256"] == QUESTION_UTF8_SHA == qchecks["question_utf8_sha256_expected"]
            and qchecks["question_exact_request_text"] and qchecks["question_terminal_lf_only"]
            and not qchecks["old_answers_included"]
        ),
        "round1_recovery": round_checks["round1"]["pass"] and recovery_logs["1"]["exit_code"] == 0,
        "round2_recovery": round_checks["round2"]["pass"] and recovery_logs["2"]["exit_code"] == 0,
        "round2_cloud_canonical": round_checks["round2"].get("cloud_canonical_match", False),
        "targeted_tests": bool(tests["exists"] and tests["sockets_disabled"] and tests["returncode"] == 0 and tests["passed"] == 126 and (tests["failed"] or 0) == 0 and (tests["errors"] or 0) == 0),
        "planning_unchanged": planning_before == planning_after,
        "material_preflight": material["material_pass"],
        "no_model_retrieval_physics": bool(
            all(recovery_logs[str(n)]["new_model_calls"] == 0 and recovery_logs[str(n)]["physics_executed"] is False for n in (1, 2))
            and material["no_simulation"]
        ),
    }
    result = {
        "schema_version": "t038.local_acceptance.v1",
        "task_id": "R3-T038",
        "case_id": CASE_ID,
        "status": "accepted_offline" if all(checks.values()) else "blocked_offline",
        "global_pass": bool(all(checks.values())),
        "tested_source_sha": EXPECTED_SOURCE_SHA,
        "base_source_sha": BASE_SOURCE_SHA,
        "source": {
            "head": git("rev-parse", "HEAD"),
            "status_porcelain": git("status", "--porcelain"),
            "implementation": implementation,
            "imports": source_imports,
        },
        "inputs": {
            "artifact_hashes": artifacts,
            "input_provenance_actual_sha256": input_provenance_actual,
            "input_provenance_expected_sha256": input_provenance_expected,
            "planning_path": str(PLANNING_PATH),
            "planning_sha256": planning_before,
            "planning_expected_sha256": PLANNING_SHA,
            "question": qchecks,
        },
        "recovery": {
            "rounds": round_checks,
            "logs": recovery_logs,
            "round2_canonical_expected": EXPECTED_ROUND2_CANONICAL,
            "round2_canonical_actual": round_checks["round2"].get("canonical_digests", {}),
        },
        "material": material,
        "tests": tests,
        "counters": {
            "new_model_http": 0,
            "retrieval_calls": 0,
            "production_physics_calls": 0,
            "solver_calls": 0,
            "new_certifications": 0,
            "new_cny": 0,
        },
        "checks": checks,
        "limitations": [
            "This is offline contract/recovery/material acceptance only; no route generation, retrieval, model answer, solver, or scientific certification was performed.",
            "Round2 remains recovered_for_continuation_not_scientific_acceptance until the parent performs its separately guarded continuation.",
        ],
    }
    write_json(PRE / "T038_LOCAL_ACCEPTANCE.json", result)
    return 0 if result["global_pass"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException as exc:
        failure = {
            "schema_version": "t038.local_acceptance_attempt_failure.v1",
            "task_id": "R3-T038",
            "error_type": type(exc).__name__,
            "error": " ".join(str(exc).split()),
            "traceback": traceback.format_exc(),
        }
        write_json(PRE / "T038_LOCAL_ACCEPTANCE_attempt01_failure.json", failure)
        raise
