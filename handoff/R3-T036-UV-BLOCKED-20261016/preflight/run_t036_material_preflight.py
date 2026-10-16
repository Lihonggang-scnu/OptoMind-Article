from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np

SOURCE = Path(r"D:\OptoMind-Article-3\r3t036a")
TASK = Path(r"D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T036")
PRE = TASK / "preflight"
MAT = TASK / "material-preflight"
CASE = MAT / "e2e-04-solarblind-uv-triple"
EXPECTED_SOURCE_SHA = "408fef514d4ed156ffde03c868a26cea3aba9dfa"
BASE_SOURCE_SHA = "5c50430693151d2066cf352fcc9bb80e5ee6b667"
GRID_NM = list(range(255, 1101))
GRID_UM = np.asarray(GRID_NM, dtype=np.float64) / 1000.0
CATEGORY = "ordinary_depositable_inorganic_dielectrics_no_metals"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def jsonable(value):
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.ndarray):
        return [jsonable(item) for item in value.tolist()]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, tuple):
        return [jsonable(item) for item in value]
    if isinstance(value, list):
        return [jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if hasattr(value, "__dict__"):
        return {str(key): jsonable(item) for key, item in vars(value).items()}
    return str(value)


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(jsonable(value), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
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


def source_snapshot(offline_validation):
    expected_files = dict(offline_validation["implementation_file_sha256"])
    bindings = {}
    for rel, expected in expected_files.items():
        path = SOURCE / rel
        actual = sha256_file(path) if path.exists() else None
        bindings[rel] = {"path": str(path), "expected_sha256": expected, "actual_sha256": actual, "match": actual == expected}
    task_files = [
        "HANDOFF.md", "BUDGET.json", "INPUT_PROVENANCE.json", "OFFLINE_VALIDATION.json",
        "C2L-R3-0051.json", "ARTIFACT_HASH_CHECK.json", "SHA256SUMS", "RELEASE.json", "SOURCE_BLOCKER.json", "T036.bundle",
    ]
    task_hashes = {name: {"sha256": sha256_file(TASK / name), "bytes": (TASK / name).stat().st_size} for name in task_files}
    return {
        "source_commit": git("rev-parse", "HEAD"),
        "source_status_porcelain": git("status", "--porcelain"),
        "source_commit_match": git("rev-parse", "HEAD") == EXPECTED_SOURCE_SHA,
        "base_implementation_sha": BASE_SOURCE_SHA,
        "implementation_bindings": bindings,
        "task_input_hashes": task_hashes,
    }


def question_check():
    request_path = SOURCE / "focused_research/campaigns/t032/inputs/e2e-04-solarblind-uv-triple/FRESH_REQUEST.json"
    question_path = SOURCE / "focused_research/campaigns/t032/inputs/e2e-04-solarblind-uv-triple/FRESH_QUESTION.txt"
    t032_prov = read_json(SOURCE / "focused_research/campaigns/t032/INPUT_PROVENANCE.json")
    expected = next(row for row in t032_prov["cases"] if row["case_id"] == "e2e-04-solarblind-uv-triple")
    request = read_json(request_path)
    question = request["question"]
    txt = question_path.read_bytes()
    return {
        "case_id": "e2e-04-solarblind-uv-triple",
        "fresh_request_path": str(request_path),
        "fresh_question_path": str(question_path),
        "fresh_request_sha256": sha256_file(request_path),
        "fresh_request_sha256_expected": expected["files"]["inputs/e2e-04-solarblind-uv-triple/FRESH_REQUEST.json"],
        "fresh_question_sha256": sha256_file(question_path),
        "fresh_question_sha256_expected": expected["files"]["inputs/e2e-04-solarblind-uv-triple/FRESH_QUESTION.txt"],
        "question_utf8_sha256": sha256_bytes(question.encode("utf-8")),
        "question_utf8_sha256_expected": expected["question_utf8_sha256"],
        "question_exact_request_text": question_path.read_text(encoding="utf-8").rstrip("\r\n") == question,
        "question_terminal_lf_only": txt == question.encode("utf-8") + b"\n",
        "old_answers_included": expected.get("old_answers_included", False),
        "question_pass": (
            sha256_file(request_path) == expected["files"]["inputs/e2e-04-solarblind-uv-triple/FRESH_REQUEST.json"]
            and sha256_file(question_path) == expected["files"]["inputs/e2e-04-solarblind-uv-triple/FRESH_QUESTION.txt"]
            and sha256_bytes(question.encode("utf-8")) == expected["question_utf8_sha256"]
            and question_path.read_text(encoding="utf-8").rstrip("\r\n") == question
            and txt == question.encode("utf-8") + b"\n"
            and not expected.get("old_answers_included", False)
        ),
    }


def module_binding(module):
    path = Path(module.__file__).resolve()
    return {"module": module.__name__, "path": str(path), "sha256": sha256_file(path), "source_relative": str(path.relative_to(SOURCE)) if path.is_relative_to(SOURCE) else None}


def ref_dict(ref):
    keys = ("name", "normalized_name", "provider", "dataset_id", "shelf", "book", "page", "filepath", "range", "range_min", "range_max", "points")
    return {key: jsonable(getattr(ref, key, None)) for key in keys}


def source_for_record(registry, provider, ref):
    if provider == "rii_sqlite":
        return Path(registry.rii_provider.db_path).resolve()
    return Path(ref.filepath).resolve()


def sample_record(registry, spec, *, role="film"):
    row = dict(spec)
    row["role"] = role
    ref = None
    source_path = None
    try:
        ref = registry.resolve(spec["material"], provider=spec["provider"], dataset_id=spec["dataset_id"])
        source_path = source_for_record(registry, spec["provider"], ref)
        sample = registry.sample(ref, GRID_UM, allow_extrapolation=False)
        provenance = dict(sample.provenance)
        n = np.asarray(sample.n)
        k = np.asarray(sample.k)
        mask = np.asarray(getattr(sample, "extrapolated_mask", np.zeros_like(n, dtype=bool)))
        raw_has_n = bool(provenance.get("has_n"))
        raw_has_k = bool(provenance.get("has_k"))
        finite_n = bool(n.shape == GRID_UM.shape and np.isfinite(n).all())
        finite_k = bool(k.shape == GRID_UM.shape and np.isfinite(k).all())
        no_extrapolation = bool(mask.shape == GRID_UM.shape and not mask.any())
        full_grid = bool(raw_has_n and raw_has_k and finite_n and finite_k and no_extrapolation)
        substrate_grid = bool(raw_has_n and finite_n and no_extrapolation)
        row.update({
            "status": "available" if (full_grid if role == "film" else substrate_grid) else "rejected",
            "resolved_ref": ref_dict(ref),
            "resolved_provenance": jsonable(provenance),
            "source_path": str(source_path),
            "source_sha256": sha256_file(source_path),
            "record_filepath": jsonable(getattr(ref, "filepath", None)),
            "raw_has_n": raw_has_n,
            "raw_has_k": raw_has_k,
            "sampled_finite_n": finite_n,
            "sampled_finite_k": finite_k,
            "sample_count": int(n.size),
            "query_count": int(GRID_UM.size),
            "query_grid_nm": [GRID_NM[0], GRID_NM[-1]],
            "query_step_nm": 1,
            "extrapolated_count": int(mask.sum()) if mask.size else None,
            "allow_extrapolation": False,
            "no_extrapolation": no_extrapolation,
            "full_grid_n_k": full_grid,
            "substrate_n_grid": substrate_grid,
            "n_min": float(np.min(n)) if n.size else None,
            "n_max": float(np.max(n)) if n.size else None,
            "k_min": float(np.min(k)) if k.size else None,
            "k_max": float(np.max(k)) if k.size else None,
            "k_nonzero_count": int(np.count_nonzero(k != 0.0)),
            "k_nonzero": bool(np.any(k != 0.0)),
            "warnings": list(getattr(sample, "warnings", []) or []),
        })
        if role == "substrate" and not raw_has_k:
            row["substrate_k_note"] = "Provider record has_n=true and has_k=false; sampled k is provider lossless-zero representation for substrate mapping, not a film n/k record."
        if role == "film" and not raw_has_k:
            row["rejection_reason"] = "record lacks provider k data; sampled zero k is not accepted as film n/k coverage"
        elif role == "film" and not no_extrapolation:
            row["rejection_reason"] = "requested grid requires extrapolation"
        return row
    except Exception as exc:
        row.update({
            "status": "rejected",
            "resolved_ref": ref_dict(ref) if ref is not None else None,
            "source_path": str(source_path) if source_path is not None else None,
            "source_sha256": sha256_file(source_path) if source_path is not None and source_path.exists() else None,
            "record_filepath": jsonable(getattr(ref, "filepath", None)) if ref is not None else None,
            "error_type": type(exc).__name__, 
            "error": " ".join(str(exc).split()),
            "query_count": int(GRID_UM.size),
            "query_grid_nm": [GRID_NM[0], GRID_NM[-1]],
            "query_step_nm": 1,
            "allow_extrapolation": False,
            "full_grid_n_k": False,
        })
        return row


def catalog_observation(registry, materials_dir, db_path, alias):
    local_path = materials_dir / (alias.lower() + ".csv")
    rows = []
    with sqlite3.connect(db_path) as connection:
        for item in connection.execute(
            "select pageid,shelf,book,page,filepath,hasrefractive,hasextinction,rangeMin,rangeMax,points from pages where lower(book)=lower(?) order by pageid",
            (alias,),
        ).fetchall():
            rows.append({
                "pageid": int(item[0]), "shelf": item[1], "book": item[2], "page": item[3], "filepath": item[4],
                "has_n": bool(item[5]), "has_k": bool(item[6]), "range_um": [item[7], item[8]], "points": item[9],
            })
    return {
        "alias": alias,
        "local_csv": {
            "path": str(local_path),
            "exists": local_path.exists(),
            "sha256": sha256_file(local_path) if local_path.exists() else None,
            "bytes": local_path.stat().st_size if local_path.exists() else None,
        },
        "rii_exact_book_pages": rows,
    }


def test_summary():
    log_path = PRE / "T036_TARGETED_TESTS_attempt01.log"
    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    match = re.search(r"(?:(\d+) failed, )?(?:(\d+) passed)?(?:, (\d+) skipped)?(?:, (\d+) errors?)? in [0-9.]+s", text)
    failed_names = re.findall(r"^_ (test_[^\n]+) _$", text, flags=re.MULTILINE)
    if not failed_names:
        failed_names = re.findall(r"^FAILED (focused_research/tests/[^\n]+)$", text, flags=re.MULTILINE)
    return {
        "command": [
            str(Path(r"D:\OptoMind-Article-3\local-integration\20261013\envs\tensor\Scripts\python.exe")), "-B", "-X", "utf8", "-m", "pytest", "-q",
            "focused_research/tests/test_material_capability_contract.py",
            "focused_research/tests/test_category_material_consumer.py",
            "--disable-socket",
        ],
        "cwd": str(SOURCE),
        "log_path": str(log_path),
        "returncode": 1,
        "passed": 51,
        "failed": 2,
        "skipped": 0,
        "errors": 0,
        "observed_summary_line": next((line.strip() for line in text.splitlines() if "passed" in line and "%" not in line), None),
        "failed_tests": failed_names,
        "failure_detail": [
            {
                "test": "test_source_hash_cache_reuses_reads_and_invalidates_changed_source[local_csv-sio2]",
                "observed": "assert len(reads) == 2; observed 1",
                "reason": "source content changed while size/mtime were restored, but the test observed no second source read",
            },
            {
                "test": "test_source_hash_cache_reuses_reads_and_invalidates_changed_source[rii_sqlite-1]",
                "observed": "assert len(reads) == 2; observed 1",
                "reason": "source content changed while size/mtime were restored, but the test observed no second source read",
            },
        ],
        "raw_log_preserved": log_path.exists(),
    }


def main():
    MAT.mkdir(parents=True, exist_ok=True)
    PRE.mkdir(parents=True, exist_ok=True)
    offline_validation = read_json(TASK / "OFFLINE_VALIDATION.json")
    before = source_snapshot(offline_validation)
    qcheck = question_check()
    sys.path.insert(0, str(SOURCE))
    from focused_research import route_consumer
    from focused_research.material_capabilities import CATEGORY, validate_material_capabilities
    from focused_research.research_execution import _engine
    _engine()
    from tmm_engine.material_registry import MaterialRegistry
    from tmm_engine.preflight import preflight_task
    from tmm_engine.task_io import simulation_task_from_dict

    registry = MaterialRegistry()
    materials_dir = Path(registry.local_provider.materials_dir).resolve()
    rii_db = Path(registry.rii_provider.db_path).resolve()
    import_bindings = {
        "route_consumer": module_binding(route_consumer),
        "material_capabilities": module_binding(sys.modules["focused_research.material_capabilities"]),
        "material_registry": module_binding(sys.modules["tmm_engine.material_registry"]),
        "preflight": module_binding(sys.modules["tmm_engine.preflight"]),
        "task_io": module_binding(sys.modules["tmm_engine.task_io"]),
    }

    specs = {
        "HfO2": {"material": "hfo2", "provider": "local_csv", "dataset_id": "hfo2"},
        "SiO2": {"material": "SiO2", "provider": "rii_sqlite", "dataset_id": 411},
        "MgF2": {"material": "MgF2", "provider": "rii_sqlite", "dataset_id": 204},
        "TiO2": {"material": "tio2", "provider": "local_csv", "dataset_id": "tio2"},
        "Al2O3": {"material": "al2o3", "provider": "local_csv", "dataset_id": "al2o3"},
        "Ta2O5": {"material": "Ta2O5", "provider": "rii_sqlite", "dataset_id": 414},
        "Nb2O5": {"material": "Nb2O5", "provider": "rii_sqlite", "dataset_id": 397},
    }
    recognized = ["HfO2", "SiO2", "MgF2", "TiO2", "Al2O3", "Ta2O5", "Nb2O5", "ZrO2"]
    catalog = {alias: catalog_observation(registry, materials_dir, rii_db, alias) for alias in recognized}
    film_records = {alias: sample_record(registry, dict(spec, alias=alias), role="film") for alias, spec in specs.items()}
    # Evaluate both exact ZrO2 RII records; neither may be silently substituted.
    zr_candidates = [
        {"alias": "ZrO2", "material": "ZrO2", "provider": "rii_sqlite", "dataset_id": 430},
        {"alias": "ZrO2", "material": "ZrO2", "provider": "rii_sqlite", "dataset_id": 431},
    ]
    zr_records = [sample_record(registry, spec, role="film") for spec in zr_candidates]
    substrate_spec = {"alias": "fused_silica", "material": "fused_silica", "provider": "rii_sqlite", "dataset_id": 835}
    substrate_record = sample_record(registry, substrate_spec, role="substrate")
    air_record = {"alias": "air", "constant_n": 1.0, "constant_k": 0.0, "explicit_constant": True, "coverage": "all requested wavelengths by model definition"}

    execution_materials = {
        alias: {"material": row["material"], "provider": row["provider"], "dataset_id": row["dataset_id"]}
        for alias, row in film_records.items() if row.get("status") == "available"
    }
    inventory = {
        "schema_version": "t036.material_inventory.v1",
        "source_commit": EXPECTED_SOURCE_SHA,
        "case_id": "e2e-04-solarblind-uv-triple",
        "grid": {"min_nm": GRID_NM[0], "max_nm": GRID_NM[-1], "step_nm": 1, "count": len(GRID_NM)},
        "category_permission": {
            "allowed_materials": None,
            "allowed_material_category": CATEGORY,
            "inventory_is_user_permission": False,
            "execution_inventory_is_finite_capability_subset": True,
        },
        "recognized_chemical_identities": recognized,
        "catalog_observation": catalog,
        "film_records": film_records,
        "ZrO2_candidate_records": zr_records,
        "selected_execution_materials": execution_materials,
        "substrate": substrate_record,
        "air": air_record,
        "rii_cache_path": str(rii_db),
        "rii_cache_sha256": sha256_file(rii_db),
        "limitations": [
            "No record was accepted from metadata alone; film acceptance required provider has_n/has_k, finite sampled arrays and zero extrapolation over every requested grid point.",
            "ZrO2 records were observed but not selected because the exact local records lack full requested n/k coverage; no substitution was made.",
            "Fused silica 835 is a substrate mapping with provider has_n=true and has_k=false; sampled zero k is retained as a substrate lossless-zero representation and is not counted as a film n/k record.",
        ],
    }
    write_json(MAT / "T036_MATERIAL_INVENTORY.json", inventory)
    write_json(MAT / "execution_materials.json", {
        "schema_version": "t036.execution_materials.v1",
        "source_commit": EXPECTED_SOURCE_SHA,
        "case_id": "e2e-04-solarblind-uv-triple",
        "allowed_materials": None,
        "allowed_material_category": CATEGORY,
        "execution_materials": execution_materials,
        "inventory_is_user_permission": False,
        "source_inventory_report": str(MAT / "T036_MATERIAL_INVENTORY.json"),
    })

    media = {"air": {"constant_n": 1.0, "constant_k": 0.0}, "fused_silica": {"material": "fused_silica", "provider": "rii_sqlite", "dataset_id": 835}}
    media.update(execution_materials)
    write_json(CASE / "wavelengths.json", GRID_NM)
    write_json(CASE / "optical_media.json", media)
    write_json(CASE / "material_audit.json", {
        "schema_version": "t036.material_case_audit.v1",
        "case_id": "e2e-04-solarblind-uv-triple",
        "source_commit": EXPECTED_SOURCE_SHA,
        "grid": {"min_nm": GRID_NM[0], "max_nm": GRID_NM[-1], "step_nm": 1, "count": len(GRID_NM)},
        "material_inventory_is_not_user_allowlist": True,
        "inventory_note": "These are explicit local execution records and capability results; they do not narrow the user's broad ordinary inorganic dielectric permission.",
        "records": list(film_records.values()) + zr_records + [substrate_record],
        "execution_materials": execution_materials,
        "optical_media_sha256": sha256_file(CASE / "optical_media.json"),
        "wavelengths_sha256": sha256_file(CASE / "wavelengths.json"),
        "all_selected_films_full_grid_n_k": all(row.get("status") == "available" for row in film_records.values()),
        "substrate_n_grid": bool(substrate_record.get("substrate_n_grid")),
    })

    selected_aliases = list(execution_materials)
    instance = {
        "status": "prepared", "route_id": "T036-OFFLINE-TOY", "instance_id": "T036-OFFLINE-TOY-ALL-AVAILABLE",
        "incident_medium": "air", "substrate": "fused_silica",
        "layers": [{"material": alias, "thickness_nm": 100.0} for alias in selected_aliases],
        "design_constraints": {"allowed_materials": None, "allowed_material_category": CATEGORY, "execution_materials": execution_materials},
        "objectives": [
            {"objective_id": "G1", "metric": "mean_transmittance", "sense": "maximize", "mean_reduction": "wavelength_integral_mean", "region": {"wavelength_nm": [255, 280], "angle_deg": 0, "polarization": "unpolarized"}},
            {"objective_id": "G2", "metric": "mean_transmittance", "sense": "minimize", "mean_reduction": "wavelength_integral_mean", "region": {"wavelength_nm": [300, 700], "angle_deg": 0, "polarization": "unpolarized"}},
            {"objective_id": "G3", "metric": "mean_transmittance", "sense": "minimize", "mean_reduction": "wavelength_integral_mean", "region": {"wavelength_nm": [700, 1100], "angle_deg": 0, "polarization": "unpolarized"}},
        ],
    }
    consumer_check = {"fixture_only": True, "simulation_performed": False}
    try:
        prepared = route_consumer.prepare_tmm_inputs(instance, optical_media=media, wavelengths_nm=GRID_NM)
        binding = prepared["material_capability_binding"]
        identity_match = True
        identity_rows = {}
        for alias, expected_record in execution_materials.items():
            actual = binding["bindings"].get(alias, {})
            identity_rows[alias] = {"expected": expected_record, "actual": {key: actual.get(key) for key in ("material", "provider", "dataset_id")}, "exact": all(actual.get(key) == value for key, value in expected_record.items())}
            identity_match = identity_match and identity_rows[alias]["exact"]
        consumer_check.update({
            "prepare_pass": True,
            "simulation_performed": prepared.get("simulation_performed"),
            "coverage_verified": binding.get("coverage_verified"),
            "numerical_preflight_required": binding.get("numerical_preflight_required"),
            "identity_match": identity_match,
            "identity_rows": identity_rows,
            "binding_aliases": sorted(binding.get("bindings", {})),
        })
        preflight_report = preflight_task("simulate", simulation_task_from_dict(prepared["tmm_task"]), registry=registry)
        consumer_check["fixture_preflight"] = {
            "ok": bool(preflight_report.get("ok")),
            "status": preflight_report.get("status"),
            "material_count": len(preflight_report.get("materials", [])),
            "failure_count": len(preflight_report.get("failures", [])),
            "failures": jsonable(preflight_report.get("failures", [])),
            "solver_invoked": False,
        }
        consumer_check["consumer_pass"] = bool(
            consumer_check["prepare_pass"] and consumer_check["identity_match"]
            and consumer_check["simulation_performed"] is False
            and consumer_check["coverage_verified"] is False
            and consumer_check["numerical_preflight_required"] is True
            and consumer_check["fixture_preflight"]["ok"]
        )
    except Exception as exc:
        consumer_check.update({"prepare_pass": False, "consumer_pass": False, "error_type": type(exc).__name__, "error": " ".join(str(exc).split())})
    write_json(MAT / "T036_CONSUMER_BINDING.json", consumer_check)

    after = source_snapshot(offline_validation)
    test_info = test_summary()
    source_unchanged = before == after
    source_bindings_pass = all(row["match"] for row in before["implementation_bindings"].values()) and all(row["match"] for row in after["implementation_bindings"].values())
    inventory_pass = bool(
        all(row.get("status") == "available" and row.get("full_grid_n_k") for row in film_records.values())
        and substrate_record.get("status") == "available"
        and substrate_record.get("substrate_n_grid")
        and len(execution_materials) == 7
    )
    consumer_pass = bool(consumer_check.get("consumer_pass"))
    test_pass = test_info["returncode"] == 0 and test_info["failed"] == 0 and test_info["errors"] == 0
    report = {
        "schema_version": "t036.offline_admission.v1",
        "global_pass": bool(source_unchanged and source_bindings_pass and qcheck["question_pass"] and inventory_pass and consumer_pass and test_pass),
        "tested_source_sha": EXPECTED_SOURCE_SHA,
        "base_source_sha": BASE_SOURCE_SHA,
        "actual_tests": test_info,
        "checks": {
            "source_commit_match": before["source_commit_match"],
            "source_bindings_pass": source_bindings_pass,
            "question_pass": qcheck["question_pass"],
            "inventory_pass": inventory_pass,
            "consumer_binding_pass": consumer_pass,
            "targeted_test_pass": test_pass,
        },
        "source_before": before,
        "source_after": after,
        "source_unchanged": source_unchanged,
        "input_provenance": {
            "path": str(TASK / "INPUT_PROVENANCE.json"),
            "sha256": sha256_file(TASK / "INPUT_PROVENANCE.json"),
            "base_implementation_sha": read_json(TASK / "INPUT_PROVENANCE.json")["base_implementation_sha"],
        },
        "question_checks": qcheck,
        "import_bindings": import_bindings,
        "material_inventory": {
            "path": str(MAT / "T036_MATERIAL_INVENTORY.json"),
            "sha256": sha256_file(MAT / "T036_MATERIAL_INVENTORY.json"),
            "recognized_count": len(recognized),
            "selected_execution_count": len(execution_materials),
            "selected_execution_materials": execution_materials,
            "unavailable_or_rejected": ["ZrO2"],
            "grid": {"min_nm": GRID_NM[0], "max_nm": GRID_NM[-1], "step_nm": 1, "count": len(GRID_NM)},
        },
        "case_results": {
            "e2e-04-solarblind-uv-triple": {
                "status": "offline_inventory_ready_targeted_tests_failed" if not test_pass else "offline_inventory_ready",
                "inventory_pass": inventory_pass,
                "consumer_pass": consumer_pass,
                "selected_execution_material_count": len(execution_materials),
                "selected_execution_materials": execution_materials,
                "unsupported_records": {"ZrO2": "no exact local record with full 255-1100 nm provider n/k coverage without extrapolation"},
            }
        },
        "counters": {"http_calls": 0, "model_calls": 0, "retrieval_calls": 0, "physics_calls": 0, "solver_calls": 0, "fees_cny": 0.0},
        "blockers": test_info["failure_detail"] if not test_pass else [],
        "limitations": [
            "No model, retrieval, network, paid call, solver, route generation, search, certification, or ledger operation was performed.",
            "Fixture preflight_task was used only for local material sampling after identity binding; no Workbench.simulate call occurred.",
        ],
        "owned_outputs": [
            str(PRE / "T036_LOCAL_PRECHECK.json"), str(PRE / "T036_LOCAL_ACCEPTANCE.json"),
            str(MAT / "T036_MATERIAL_INVENTORY.json"), str(MAT / "execution_materials.json"),
            str(CASE / "optical_media.json"), str(CASE / "wavelengths.json"), str(CASE / "material_audit.json"),
            str(MAT / "T036_CONSUMER_BINDING.json"),
        ],
    }
    write_json(PRE / "T036_LOCAL_PRECHECK.json", {
        "schema_version": "t036.local_precheck.v1",
        "argv": sys.argv,
        "python": sys.executable,
        "cwd": str(Path.cwd()),
        "source": str(SOURCE),
        "task": str(TASK),
        "source_before": before,
        "source_after": after,
        "source_unchanged": source_unchanged,
        "tested_source_sha": EXPECTED_SOURCE_SHA,
        "question_check": qcheck,
        "import_bindings": import_bindings,
        "inventory_path": str(MAT / "T036_MATERIAL_INVENTORY.json"),
        "execution_materials_path": str(MAT / "execution_materials.json"),
        "optical_media_path": str(CASE / "optical_media.json"),
        "wavelengths_path": str(CASE / "wavelengths.json"),
        "counters": report["counters"],
    })
    write_json(PRE / "T036_LOCAL_ACCEPTANCE.json", report)
    print(json.dumps({"global_pass": report["global_pass"], "source_unchanged": source_unchanged, "question_pass": qcheck["question_pass"], "inventory_pass": inventory_pass, "consumer_pass": consumer_pass, "targeted_test_pass": test_pass, "selected_execution_count": len(execution_materials), "rejected": ["ZrO2"]}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()



