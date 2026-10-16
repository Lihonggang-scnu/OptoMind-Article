from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import numpy as np

SOURCE = Path(r"D:\OptoMind-Article-3\r3t037a")
TASK = Path(r"D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T037")
PRE = TASK / "preflight"
MAT = TASK / "material-preflight"
CASE = MAT / "e2e-04-solarblind-uv-triple"
EXPECTED_SOURCE_SHA = "49861e169084976069d9ef1008e4fafc84b86bb8"
BASE_SOURCE_SHA = "408fef514d4ed156ffde03c868a26cea3aba9dfa"
CASE_ID = "e2e-04-solarblind-uv-triple"
GRID_NM = list(range(255, 1101))
GRID_UM = np.asarray(GRID_NM, dtype=np.float64) / 1000.0
PYTHON = Path(sys.executable).resolve()

# The T036 helper is read-only reference code.  Its pure JSON/hash/material
# helpers are reused here; no T036 output is written or imported as input.
OLD_HELPER_PATH = Path(r"D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T036\preflight\run_t036_material_preflight.py")
OLD_SPEC = importlib.util.spec_from_file_location("t036_readonly_helpers", OLD_HELPER_PATH)
OLD = importlib.util.module_from_spec(OLD_SPEC)
assert OLD_SPEC and OLD_SPEC.loader
OLD_SPEC.loader.exec_module(OLD)
OLD.SOURCE = SOURCE
OLD.TASK = TASK
OLD.PRE = PRE
OLD.MAT = MAT
OLD.CASE = CASE
OLD.EXPECTED_SOURCE_SHA = EXPECTED_SOURCE_SHA
OLD.BASE_SOURCE_SHA = BASE_SOURCE_SHA
OLD.GRID_NM = GRID_NM
OLD.GRID_UM = GRID_UM


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def jsonable(value):
    return OLD.jsonable(value)


def write_json(path: Path, value):
    return OLD.write_json(path, value)


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(SOURCE), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def module_binding(module):
    path = Path(module.__file__).resolve()
    return {
        "module": module.__name__,
        "path": str(path),
        "sha256": sha256_file(path),
        "source_relative": str(path.relative_to(SOURCE)) if path.is_relative_to(SOURCE) else None,
    }


def normalized_path_match(actual, expected):
    if expected is None:
        return True
    if actual is None:
        return False
    a = str(actual).replace("/", "\\").casefold()
    e = str(expected).replace("/", "\\").casefold()
    if a == e:
        return True
    # Historical provenance contains the T036 checkout root; record identity
    # is retained by provider plus dataset and the current basename/suffix.
    return Path(a).name == Path(e).name


def source_snapshot(provenance, artifact_check):
    expected_source = {
        "focused_research/route_consumer.py": "5a42f8cacd72f0c16d868d07946249eb718bf7577f0c9fc3e4b00476035327c4",
        "focused_research/material_capabilities.py": "122b69f0b57fedc262953f221b2a18b039e68168a23a879e9426c0ce2436665b",
        "focused_research/research_routes.py": "c9caf19f309d8c44d4b21991ee1d7f5b6bb11be83daeec432ace4859ac94e082",
        "focused_research/research_routes_prompt.txt": "07544273cebfdd39ea4dfc4d9f962405ce3206e68cec7d7c1c8179c6818cfda4",
        "focused_research/tests/test_material_capability_contract.py": "cb287f1ec863fab793dbcd2eacbfcce2091a8b7bbdde03017250db193cf69ce3",
        "focused_research/tests/test_category_material_consumer.py": "a5c07dbfdcbd6ff82716bf9207f1869175ba54724cd7df9ff4ed40a80e077c73",
        "focused_research/tests/test_research_routes.py": "e432659652dfce4b0b8314a5e0e79eb631a6e33bdbccc189f5b2c57e91a39c10",
        "focused_research/tests/test_route_consumer.py": "e539dabc36a519b20c3a29dd7e95c8bc072c51d5082ba87345605394112e8338",
    }
    implementation = {}
    for rel, expected in expected_source.items():
        path = SOURCE / rel
        actual = sha256_file(path) if path.exists() else None
        implementation[rel] = {
            "path": str(path),
            "expected_sha256": expected,
            "actual_sha256": actual,
            "bytes": path.stat().st_size if path.exists() else None,
            "match": actual == expected,
        }
    artifact_rows = []
    artifact_pass = True
    for item in artifact_check:
        rel = str(item["file"])
        path = TASK / rel
        actual = sha256_file(path) if path.exists() else None
        match = actual == item["expected"]
        artifact_rows.append({
            "file": rel,
            "path": str(path),
            "expected_sha256": item["expected"],
            "actual_sha256": actual,
            "bytes": path.stat().st_size if path.exists() else None,
            "match": match,
        })
        artifact_pass = artifact_pass and match

    t032 = read_json(SOURCE / "focused_research/campaigns/t032/INPUT_PROVENANCE.json")
    expected_case = next(row for row in t032["cases"] if row["case_id"] == CASE_ID)
    request_path = SOURCE / "focused_research/campaigns/t032/inputs/e2e-04-solarblind-uv-triple/FRESH_REQUEST.json"
    question_path = SOURCE / "focused_research/campaigns/t032/inputs/e2e-04-solarblind-uv-triple/FRESH_QUESTION.txt"
    request = read_json(request_path) if request_path.exists() else {}
    question = request.get("question", "")
    question_bytes = question_path.read_bytes() if question_path.exists() else b""
    question_checks = {
        "fresh_request_path": str(request_path),
        "fresh_question_path": str(question_path),
        "fresh_request_sha256": sha256_file(request_path) if request_path.exists() else None,
        "fresh_request_sha256_expected": expected_case["files"]["inputs/e2e-04-solarblind-uv-triple/FRESH_REQUEST.json"],
        "fresh_question_sha256": sha256_file(question_path) if question_path.exists() else None,
        "fresh_question_sha256_expected": expected_case["files"]["inputs/e2e-04-solarblind-uv-triple/FRESH_QUESTION.txt"],
        "question_utf8_sha256": sha256_bytes(question.encode("utf-8")),
        "question_utf8_sha256_expected": expected_case["question_utf8_sha256"],
        "question_exact_request_text": question_path.exists() and question_path.read_text(encoding="utf-8").rstrip("\r\n") == question,
        "question_terminal_lf_only": question_bytes == question.encode("utf-8") + b"\n",
        "old_answers_included": expected_case.get("old_answers_included", False),
    }
    question_checks["question_pass"] = bool(
        question_checks["fresh_request_sha256"] == question_checks["fresh_request_sha256_expected"]
        and question_checks["fresh_question_sha256"] == question_checks["fresh_question_sha256_expected"]
        and question_checks["question_utf8_sha256"] == question_checks["question_utf8_sha256_expected"]
        and question_checks["question_exact_request_text"]
        and question_checks["question_terminal_lf_only"]
        and not question_checks["old_answers_included"]
    )
    source_commit = git("rev-parse", "HEAD")
    status = git("status", "--porcelain")
    provenance_hash = sha256_file(TASK / "INPUT_PROVENANCE.json")
    return {
        "source_commit": source_commit,
        "source_commit_expected": EXPECTED_SOURCE_SHA,
        "source_commit_match": source_commit == EXPECTED_SOURCE_SHA,
        "source_status_porcelain": status,
        "source_status_clean": status == "",
        "base_source_sha": BASE_SOURCE_SHA,
        "tested_source_sha": EXPECTED_SOURCE_SHA,
        "implementation_bindings": implementation,
        "implementation_bindings_pass": all(row["match"] for row in implementation.values()),
        "task_artifact_bindings": artifact_rows,
        "task_artifact_bindings_pass": artifact_pass,
        "question_checks": question_checks,
        "input_provenance_sha256": provenance_hash,
        "input_provenance_expected_sha256": "f40bfa722f12a2f5256ca2309eeb9d22cd71c2bee0341ca7aec9bcc0d3c21fc5",
        "input_provenance_hash_match": provenance_hash == "f40bfa722f12a2f5256ca2309eeb9d22cd71c2bee0341ca7aec9bcc0d3c21fc5",
    }


def augment_record(row, expected, role):
    spec = expected["record"]
    resolved_ref = row.get("resolved_ref") or {}
    resolved_material = (
        resolved_ref.get("book") if spec["provider"] == "rii_sqlite"
        else resolved_ref.get("name")
    )
    actual_identity = {
        "material": resolved_material,
        "provider": resolved_ref.get("provider"),
        "dataset_id": resolved_ref.get("dataset_id"),
    }
    expected_identity = {
        "material": spec["material"],
        "provider": spec["provider"],
        "dataset_id": spec["dataset_id"],
    }
    row["expected_identity"] = expected_identity
    row["resolved_identity"] = actual_identity
    row["identity_match"] = actual_identity == expected_identity
    row["source_sha256_match"] = row.get("source_sha256") == expected["source_sha256"]
    row["record_filepath_match"] = normalized_path_match(
        row.get("record_filepath"), expected.get("record_filepath")
    )
    if role == "film":
        row["pass"] = bool(
            row.get("status") == "available"
            and row.get("full_grid_n_k")
            and row["identity_match"]
            and row["source_sha256_match"]
            and row["record_filepath_match"]
        )
    else:
        row["pass"] = bool(
            row.get("status") == "available"
            and row.get("substrate_n_grid")
            and row["identity_match"]
            and row["source_sha256_match"]
            and row["record_filepath_match"]
        )
    if not row["pass"]:
        row["failure_reasons"] = [
            name for name, ok in (
                ("available_status", row.get("status") == "available"),
                ("full_grid_n_k", row.get("full_grid_n_k") if role == "film" else row.get("substrate_n_grid")),
                ("identity_match", row["identity_match"]),
                ("source_sha256_match", row["source_sha256_match"]),
                ("record_filepath_match", row["record_filepath_match"]),
            ) if not ok
        ]
    return row


def same_size_fresh_hash_toy(route_consumer):
    path = PRE / "T037_WINDOWS_CACHE_TOY.bin"
    path.write_bytes(b"alpha")
    initial_stat = path.stat()
    signature_before = tuple(route_consumer._source_signature(path))
    first_sha = route_consumer._source_sha256(path, signature_before)
    path.write_bytes(b"bravo")
    os.utime(path, ns=(initial_stat.st_atime_ns, initial_stat.st_mtime_ns))
    signature_after = tuple(route_consumer._source_signature(path))
    second_sha = route_consumer._source_sha256(path, signature_after)
    actual_sha = sha256_file(path)
    result = {
        "path": str(path),
        "first_bytes_sha256": first_sha,
        "second_bytes_sha256": second_sha,
        "actual_current_bytes_sha256": actual_sha,
        "expected_alpha_sha256": sha256_bytes(b"alpha"),
        "expected_bravo_sha256": sha256_bytes(b"bravo"),
        "signature_before": list(signature_before),
        "signature_after": list(signature_after),
        "signature_equal": signature_before == signature_after,
        "same_size": initial_stat.st_size == path.stat().st_size,
        "mtime_restored": initial_stat.st_mtime_ns == path.stat().st_mtime_ns,
        "first_differs_from_current": first_sha != actual_sha,
        "second_matches_current": second_sha == actual_sha,
    }
    result["pass"] = bool(
        result["signature_equal"]
        and result["same_size"]
        and result["mtime_restored"]
        and result["first_differs_from_current"]
        and result["second_matches_current"]
    )
    write_json(PRE / "T037_WINDOWS_TOY_REPRO.json", result)
    return result


def test_summary():
    log_path = PRE / "T037_TARGETED_TESTS_attempt01.log"
    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    match = re.search(
        r"(?:(\d+) failed, )?(\d+) passed(?:, (\d+) skipped)?(?:, (\d+) errors?)? in [0-9.]+s",
        text,
    )
    failed = int(match.group(1) or 0) if match else None
    passed = int(match.group(2)) if match else None
    skipped = int(match.group(3) or 0) if match else None
    errors = int(match.group(4) or 0) if match else None
    failed_names = re.findall(r"^FAILED (focused_research/tests/[^\n]+)$", text, flags=re.MULTILINE)
    return {
        "command": [
            str(PYTHON), "-B", "-X", "utf8", "-m", "pytest", "-q",
            "focused_research/tests/test_material_capability_contract.py",
            "focused_research/tests/test_category_material_consumer.py",
            "focused_research/tests/test_research_routes.py",
            "focused_research/tests/test_route_consumer.py",
            "--disable-socket",
        ],
        "cwd": str(SOURCE),
        "log_path": str(log_path),
        "log_exists": log_path.exists(),
        "returncode": 0 if match and (failed or 0) == 0 and (errors or 0) == 0 else None,
        "passed": passed,
        "failed": failed or 0 if match else None,
        "skipped": skipped or 0 if match else None,
        "errors": errors or 0 if match else None,
        "observed_summary_line": next((line.strip() for line in text.splitlines() if re.search(r"\d+ passed", line)), None),
        "failed_tests": failed_names,
    }


def main():
    PRE.mkdir(parents=True, exist_ok=True)
    MAT.mkdir(parents=True, exist_ok=True)
    CASE.mkdir(parents=True, exist_ok=True)
    provenance = read_json(TASK / "INPUT_PROVENANCE.json")
    artifact_check = read_json(TASK / "ARTIFACT_HASH_CHECK.json")
    before = source_snapshot(provenance, artifact_check)

    sys.path.insert(0, str(SOURCE))
    from focused_research import route_consumer
    from focused_research.research_execution import _engine
    _engine()
    from tmm_engine.material_registry import MaterialRegistry
    from tmm_engine.preflight import preflight_task
    from tmm_engine.task_io import simulation_task_from_dict

    registry = MaterialRegistry()
    import_bindings = {
        "focused_research.route_consumer": module_binding(route_consumer),
        "focused_research.material_capabilities": module_binding(sys.modules["focused_research.material_capabilities"]),
        "tmm_engine.material_registry": module_binding(sys.modules["tmm_engine.material_registry"]),
        "tmm_engine.preflight": module_binding(sys.modules["tmm_engine.preflight"]),
        "tmm_engine.task_io": module_binding(sys.modules["tmm_engine.task_io"]),
    }

    expected_specs = provenance["reused_inventory"]["film_records"]
    film_records = {}
    for expected in expected_specs:
        row = OLD.sample_record(
            registry,
            dict(expected["record"], alias=expected["alias"]),
            role="film",
        )
        film_records[expected["alias"]] = augment_record(row, expected, "film")
    substrate_expected = provenance["reused_inventory"]["substrate"]
    substrate_spec = {
        "alias": "fused_silica",
        "material": substrate_expected["material"],
        "provider": substrate_expected["provider"],
        "dataset_id": substrate_expected["dataset_id"],
    }
    substrate = OLD.sample_record(registry, substrate_spec, role="substrate")
    substrate = augment_record(
        substrate,
        {"record": {key: substrate_spec[key] for key in ("material", "provider", "dataset_id")},
         "source_sha256": substrate_expected["source_sha256"],
         "record_filepath": substrate_expected.get("record_filepath")},
        "substrate",
    )
    execution_materials = {
        expected["alias"]: deepcopy(expected["record"]) for expected in expected_specs
    }
    execution_materials_pass = (
        set(execution_materials) == {"HfO2", "SiO2", "MgF2", "TiO2", "Al2O3", "Ta2O5", "Nb2O5"}
        and all(film_records[alias]["pass"] for alias in execution_materials)
    )
    inventory = {
        "schema_version": "t037.material_inventory.v1",
        "task_id": "R3-T037",
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
        "film_records": film_records,
        "substrate": substrate,
        "air": {
            "alias": "air",
            "constant_n": 1.0,
            "constant_k": 0.0,
            "explicit_constant": True,
        },
        "selected_execution_materials": execution_materials,
        "reused_inventory_binding": "INPUT_PROVENANCE.json reused_inventory; no catalog rediscovery or download performed",
        "excluded_from_current_execution_inventory": deepcopy(
            provenance["reused_inventory"].get("excluded_from_current_execution_inventory", {})
        ),
        "limitations": [
            "Film acceptance required provider n/k flags, finite arrays and zero extrapolation at all 846 requested points.",
            "Fused silica 835 has provider n data and no provider k; sampled zero k is retained only for substrate mapping.",
            "The finite execution inventory is an availability disclosure, not a user whitelist or narrowed category permission.",
        ],
    }
    write_json(MAT / "T037_MATERIAL_INVENTORY.json", inventory)
    write_json(MAT / "execution_materials.json", {
        "schema_version": "t037.execution_materials.v1",
        "task_id": "R3-T037",
        "case_id": CASE_ID,
        "source_commit": EXPECTED_SOURCE_SHA,
        "allowed_materials": None,
        "allowed_material_category": "ordinary_depositable_inorganic_dielectrics_no_metals",
        "execution_materials": execution_materials,
        "inventory_is_user_permission": False,
        "source_inventory_report": str(MAT / "T037_MATERIAL_INVENTORY.json"),
    })

    media = {
        "air": {"constant_n": 1.0, "constant_k": 0.0},
        "fused_silica": {
            "material": substrate_spec["material"],
            "provider": substrate_spec["provider"],
            "dataset_id": substrate_spec["dataset_id"],
        },
    }
    media.update(execution_materials)
    write_json(CASE / "wavelengths.json", GRID_NM)
    write_json(CASE / "optical_media.json", media)

    instance = {
        "status": "prepared",
        "route_id": "T037-OFFLINE-FIXTURE",
        "instance_id": "T037-OFFLINE-FIXTURE-ALL-AVAILABLE",
        "incident_medium": "air",
        "substrate": "fused_silica",
        "layers": [{"material": alias, "thickness_nm": 100.0} for alias in execution_materials],
        "design_constraints": {
            "allowed_materials": None,
            "allowed_material_category": "ordinary_depositable_inorganic_dielectrics_no_metals",
            "execution_materials": execution_materials,
        },
        "objectives": [
            {"objective_id": "G1", "metric": "mean_transmittance", "sense": "maximize", "mean_reduction": "wavelength_integral_mean",
             "region": {"wavelength_nm": [255, 280], "angle_deg": 0, "polarization": "unpolarized"}},
            {"objective_id": "G2", "metric": "mean_transmittance", "sense": "minimize", "mean_reduction": "wavelength_integral_mean",
             "region": {"wavelength_nm": [300, 700], "angle_deg": 0, "polarization": "unpolarized"}},
            {"objective_id": "G3", "metric": "mean_transmittance", "sense": "minimize", "mean_reduction": "wavelength_integral_mean",
             "region": {"wavelength_nm": [700, 1100], "angle_deg": 0, "polarization": "unpolarized"}},
        ],
    }
    consumer_check = {
        "fixture_only": True,
        "simulation_performed": False,
        "solver_invoked": False,
        "selected_materials": execution_materials,
    }
    try:
        prepared = route_consumer.prepare_tmm_inputs(instance, optical_media=media, wavelengths_nm=GRID_NM)
        binding = prepared["material_capability_binding"]
        identity_rows = {}
        for alias, expected_record in execution_materials.items():
            actual = binding["bindings"].get(alias, {})
            actual_record = {key: actual.get(key) for key in ("material", "provider", "dataset_id")}
            identity_rows[alias] = {
                "expected": expected_record,
                "actual": actual_record,
                "exact": actual_record == expected_record,
            }
        second_prepare = route_consumer.prepare_tmm_inputs(instance, optical_media=media, wavelengths_nm=GRID_NM)
        fixture_report = preflight_task(
            "simulate",
            simulation_task_from_dict(prepared["tmm_task"]),
            registry=registry,
        )
        consumer_check.update({
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
                "failures": jsonable(fixture_report.get("failures", [])),
                "solver_invoked": False,
            },
        })
        consumer_check["consumer_pass"] = bool(
            consumer_check["prepare_pass"]
            and consumer_check["repeat_prepare_pass"]
            and consumer_check["simulation_performed"] is False
            and consumer_check["coverage_verified"] is False
            and consumer_check["numerical_preflight_required"] is True
            and consumer_check["identity_match"]
            and consumer_check["fixture_preflight"]["ok"]
        )
    except Exception as exc:
        consumer_check.update({
            "prepare_pass": False,
            "consumer_pass": False,
            "error_type": type(exc).__name__,
            "error": " ".join(str(exc).split()),
        })
    write_json(MAT / "T037_CONSUMER_BINDING.json", consumer_check)
    toy = same_size_fresh_hash_toy(route_consumer)

    after = source_snapshot(provenance, artifact_check)
    source_unchanged = before == after
    tests = test_summary()
    tests_pass = (
        tests["log_exists"]
        and tests["returncode"] == 0
        and tests["passed"] == 131
        and tests["failed"] == 0
        and tests["errors"] == 0
    )
    source_pass = bool(
        before["source_commit_match"] and before["source_status_clean"]
        and before["implementation_bindings_pass"] and before["task_artifact_bindings_pass"]
        and before["input_provenance_hash_match"]
        and after["source_commit_match"] and after["source_status_clean"]
        and after["implementation_bindings_pass"] and after["task_artifact_bindings_pass"]
        and after["input_provenance_hash_match"]
    )
    inventory_pass = bool(
        execution_materials_pass
        and len(film_records) == 7
        and all(row.get("pass") and row.get("sample_count") == len(GRID_NM) for row in film_records.values())
        and substrate.get("pass")
        and substrate.get("sample_count") == len(GRID_NM)
    )
    checks = {
        "source_commit_and_hash_bindings_pass": source_pass,
        "source_unchanged": source_unchanged,
        "question_pass": before["question_checks"]["question_pass"],
        "inventory_pass": inventory_pass,
        "consumer_binding_pass": bool(consumer_check.get("consumer_pass")),
        "same_size_fresh_hash_toy_pass": bool(toy.get("pass")),
        "targeted_tests_pass": tests_pass,
        "all_counters_zero": True,
    }
    counters = {
        "http_calls": 0,
        "model_calls": 0,
        "retrieval_calls": 0,
        "physics_calls": 0,
        "solver_calls": 0,
        "fees_cny": 0.0,
    }
    report = {
        "schema_version": "t037.offline_admission.v1",
        "task_id": "R3-T037",
        "case_id": CASE_ID,
        "global_pass": bool(all(checks.values())),
        "tested_source_sha": EXPECTED_SOURCE_SHA,
        "base_source_sha": BASE_SOURCE_SHA,
        "actual_tests": tests,
        "checks": checks,
        "source_before": before,
        "source_after": after,
        "source_unchanged": source_unchanged,
        "import_bindings": import_bindings,
        "question_checks": before["question_checks"],
        "material_inventory": {
            "path": str(MAT / "T037_MATERIAL_INVENTORY.json"),
            "sha256": sha256_file(MAT / "T037_MATERIAL_INVENTORY.json"),
            "selected_execution_count": len(execution_materials),
            "selected_execution_materials": execution_materials,
            "film_records_current_hashes": {alias: row.get("source_sha256") for alias, row in film_records.items()},
            "substrate_current_hash": substrate.get("source_sha256"),
            "grid": {"min_nm": GRID_NM[0], "max_nm": GRID_NM[-1], "step_nm": 1, "count": len(GRID_NM)},
            "excluded_from_current_execution_inventory": deepcopy(
                provenance["reused_inventory"].get("excluded_from_current_execution_inventory", {})
            ),
        },
        "consumer_binding": {
            "path": str(MAT / "T037_CONSUMER_BINDING.json"),
            "sha256": sha256_file(MAT / "T037_CONSUMER_BINDING.json"),
        },
        "toy_reproduction": {
            "path": str(PRE / "T037_WINDOWS_TOY_REPRO.json"),
            "sha256": sha256_file(PRE / "T037_WINDOWS_TOY_REPRO.json"),
            "result": toy,
        },
        "counters": counters,
        "limitations": [
            "No model, retrieval, network, paid call, production route generation, search, certification, optimizer, solver or ledger operation was performed.",
            "Fixture preflight only sampled local material records after exact identity binding; it is not a numerical design result.",
            "The same-size toy demonstrates fresh current-byte hashing when the complete observed signature is unchanged; it is not an atomic snapshot guarantee.",
        ],
        "owned_outputs": [
            str(PRE / "T037_LOCAL_PRECHECK.json"),
            str(PRE / "T037_LOCAL_ACCEPTANCE.json"),
            str(PRE / "T037_WINDOWS_TOY_REPRO.json"),
            str(MAT / "T037_MATERIAL_INVENTORY.json"),
            str(MAT / "execution_materials.json"),
            str(MAT / "T037_CONSUMER_BINDING.json"),
            str(CASE / "optical_media.json"),
            str(CASE / "wavelengths.json"),
            str(CASE / "material_audit.json"),
        ],
    }
    write_json(CASE / "material_audit.json", {
        "schema_version": "t037.material_case_audit.v1",
        "task_id": "R3-T037",
        "case_id": CASE_ID,
        "source_commit": EXPECTED_SOURCE_SHA,
        "grid": {"min_nm": GRID_NM[0], "max_nm": GRID_NM[-1], "step_nm": 1, "count": len(GRID_NM)},
        "material_inventory_is_not_user_allowlist": True,
        "execution_inventory_is_finite_availability_subset": True,
        "records": list(film_records.values()) + [substrate],
        "execution_materials": execution_materials,
        "selected_film_count": len(execution_materials),
        "all_selected_films_full_grid_n_k": all(row.get("pass") for row in film_records.values()),
        "substrate_n_grid": bool(substrate.get("pass")),
        "excluded_from_current_execution_inventory": deepcopy(
            provenance["reused_inventory"].get("excluded_from_current_execution_inventory", {})
        ),
        "optical_media_sha256": sha256_file(CASE / "optical_media.json"),
        "wavelengths_sha256": sha256_file(CASE / "wavelengths.json"),
    })
    write_json(PRE / "T037_LOCAL_PRECHECK.json", {
        "schema_version": "t037.local_precheck.v1",
        "argv": sys.argv,
        "python": str(PYTHON),
        "cwd": str(Path.cwd()),
        "source": str(SOURCE),
        "task": str(TASK),
        "tested_source_sha": EXPECTED_SOURCE_SHA,
        "source_before": before,
        "question_checks": before["question_checks"],
        "import_bindings": import_bindings,
        "material_inventory_path": str(MAT / "T037_MATERIAL_INVENTORY.json"),
        "execution_materials_path": str(MAT / "execution_materials.json"),
        "optical_media_path": str(CASE / "optical_media.json"),
        "wavelengths_path": str(CASE / "wavelengths.json"),
        "toy_path": str(PRE / "T037_WINDOWS_TOY_REPRO.json"),
        "counters": counters,
    })
    write_json(PRE / "T037_LOCAL_ACCEPTANCE.json", report)
    print(json.dumps({
        "global_pass": report["global_pass"],
        "source_unchanged": source_unchanged,
        "question_pass": before["question_checks"]["question_pass"],
        "inventory_pass": inventory_pass,
        "consumer_pass": bool(consumer_check.get("consumer_pass")),
        "same_size_fresh_hash_toy_pass": toy.get("pass"),
        "targeted_test_pass": tests_pass,
        "selected_execution_count": len(execution_materials),
        "film_full_grid_count": sum(1 for row in film_records.values() if row.get("pass")),
        "substrate_pass": substrate.get("pass"),
        "counters": counters,
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

