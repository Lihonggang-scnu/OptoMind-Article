from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

SOURCE = Path(r"D:\OptoMind-Article-3\r3t032b")
TASK = Path(r"D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T032")
FOCUSED = SOURCE / "focused_research"
MATERIALS = SOURCE / "veritmm" / "tmm_engine" / "materials"
RII_DB = SOURCE / "veritmm" / "tmm_engine" / "rii_cache.db"
EXPECTED_SOURCE_SHA = "8a38b2d0d7b18c4308c4f5978691a234f6ab3732"
PROV_PATH = TASK / "INPUT_PROVENANCE.json"
CASE_INPUT_ROOT = FOCUSED / "campaigns" / "t032" / "inputs"
CASE_REPLAY_ROOT = SOURCE / "replay_data" / "runs"

CASES = {
    "e2e-01-methane-swir-window": {
        "lo_nm": 300,
        "hi_nm": 1700,
        "substrate": "fused_silica",
        "materials": [
            {"alias": "HfO2", "material": "HfO2", "provider": "local_csv", "dataset_id": "hfo2"},
            {"alias": "SiO2", "material": "SiO2", "provider": "rii_sqlite", "dataset_id": "411"},
            {"alias": "fused_silica", "material": "fused_silica", "provider": "rii_sqlite", "dataset_id": "835"},
        ],
    },
    "e2e-04-solarblind-uv-triple": {
        "lo_nm": 255,
        "hi_nm": 1100,
        "substrate": "fused_silica",
        "materials": [
            {"alias": "HfO2", "material": "HfO2", "provider": "local_csv", "dataset_id": "hfo2"},
            {"alias": "SiO2", "material": "SiO2", "provider": "rii_sqlite", "dataset_id": "411"},
            {"alias": "MgF2", "material": "MgF2", "provider": "rii_sqlite", "dataset_id": "204"},
            {"alias": "fused_silica", "material": "fused_silica", "provider": "rii_sqlite", "dataset_id": "835"},
        ],
    },
    "e2e-05-visual-absorber-cr": {
        "lo_nm": 400,
        "hi_nm": 2000,
        "substrate": "aluminum",
        "materials": [
            {"alias": "HfO2", "material": "HfO2", "provider": "local_csv", "dataset_id": "hfo2"},
            {"alias": "SiO2", "material": "SiO2", "provider": "rii_sqlite", "dataset_id": "411"},
            {"alias": "Cr", "material": "Cr", "provider": "rii_sqlite", "dataset_id": "164"},
            {"alias": "Al", "material": "Al", "provider": "rii_sqlite", "dataset_id": "22"},
        ],
    },
}

SOURCE_BINDINGS = [
    "focused_research/research_routes.py",
    "focused_research/local_campaign_guard.py",
    "focused_research/route_consumer.py",
    "focused_research/tests/test_research_routes.py",
    "focused_research/tests/test_t032_protocols.py",
    "focused_research/tests/test_local_campaign_guard.py",
    "focused_research/tests/test_task_sheet_tolerance.py",
    "veritmm/tmm_engine/material_registry.py",
]
BOUNDED_ROUTE_SCAN = [
    "focused_research/research_routes.py",
    "focused_research/route_consumer.py",
    "focused_research/tests/test_research_routes.py",
    "focused_research/local_campaign_guard.py",
]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def jsonable(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.ndarray):
        return [jsonable(v) for v in value.tolist()]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, tuple):
        return [jsonable(v) for v in value]
    if isinstance(value, list):
        return [jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if hasattr(value, "__dict__"):
        return {str(k): jsonable(v) for k, v in vars(value).items()}
    return value


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(jsonable(value), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def git(*args: str) -> str:
    result = subprocess.run(["git", "-C", str(SOURCE), *args], check=True, capture_output=True, text=True, encoding="utf-8")
    return result.stdout.strip()


def module_binding(module):
    path = Path(module.__file__).resolve()
    return {"module": module.__name__, "path": str(path), "sha256": sha256_file(path), "source_relative": str(path.relative_to(SOURCE)) if path.is_relative_to(SOURCE) else None}


def question_audit(provenance):
    rows = []
    for entry in provenance["cases"]:
        case_id = entry["case_id"]
        fresh_dir = CASE_INPUT_ROOT / case_id
        fresh_request = fresh_dir / "FRESH_REQUEST.json"
        fresh_question = fresh_dir / "FRESH_QUESTION.txt"
        replay = CASE_REPLAY_ROOT / f"{case_id}.json"
        request_obj = read_json(fresh_request)
        replay_obj = read_json(replay)
        q = request_obj.get("question")
        replay_q = replay_obj.get("summary", {}).get("question")
        q_bytes = str(q).encode("utf-8") if isinstance(q, str) else b""
        exact_text_bytes = fresh_question.read_bytes()
        source_path = entry["source_path"].split("#", 1)[0]
        actual_blob = git("rev-parse", f"{entry['source_commit']}:{source_path}")
        rows.append({
            "case_id": case_id,
            "fresh_request_sha256": sha256_file(fresh_request),
            "fresh_request_sha256_expected": entry["files"][f"inputs/{case_id}/FRESH_REQUEST.json"],
            "fresh_question_sha256": sha256_file(fresh_question),
            "fresh_question_sha256_expected": entry["files"][f"inputs/{case_id}/FRESH_QUESTION.txt"],
            "question_utf8_sha256": sha256_bytes(q_bytes),
            "question_utf8_sha256_expected": entry["question_utf8_sha256"],
            "question_exact_fresh_request": q == fresh_question.read_text(encoding="utf-8").rstrip("\r\n"),
            "question_exact_replay_summary": q == replay_q,
            "fresh_question_terminal_lf_only": exact_text_bytes == (q + "\n").encode("utf-8"),
            "replay_sha256": sha256_file(replay),
            "source_blob_expected": entry["source_blob"],
            "source_blob_actual": actual_blob,
            "source_blob_match": actual_blob == entry["source_blob"],
            "old_answers_included": entry["old_answers_included"],
        })
    # The original duplicate is an explicit provenance check, outside the three fresh rows.
    q01 = read_json(CASE_REPLAY_ROOT / "e2e-01-methane-swir-window.json")["summary"]["question"]
    q03 = read_json(CASE_REPLAY_ROOT / "e2e-03-methane-swir-chirped.json")["summary"]["question"]
    rows.append({
        "case_id": "e2e-03-methane-swir-chirped",
        "duplicate_of": "e2e-01-methane-swir-window",
        "question_exact_match_original01": q03 == q01,
        "question_utf8_sha256": sha256_bytes(q03.encode("utf-8")),
        "replay_sha256": sha256_file(CASE_REPLAY_ROOT / "e2e-03-methane-swir-chirped.json"),
    })
    return rows


def protocol_audit(local_campaign_guard):
    checks = {}
    checks["e2e-01-methane-swir-window"] = {
        "required_fragments": ["1000–1700", "300–450", "HfO2/SiO2", "30 layers"],
        "assumption_fragments": ["0 degree", "equal-s/p", "wavelength-integral"],
    }
    checks["e2e-04-solarblind-uv-triple"] = {
        "required_fragments": ["255–280", "300–700", "700–1100", "ordinary depositable inorganic dielectric", "not a named two-material set"],
        "assumption_fragments": ["0 degree", "equal-s/p", "wavelength-integral", "transmission minimization"],
    }
    checks["e2e-05-visual-absorber-cr"] = {
        "required_fragments": ["400–700", "700–2000", "HfO2/SiO2/Cr", "aluminum substrate", "A_finite"],
        "assumption_fragments": ["0 degree", "equal-s/p", "wavelength-integral", "semi-infinite absorbing exit medium"],
    }
    result = {}
    for case_id, expectation in checks.items():
        actual = local_campaign_guard.CASE_PROTOCOLS[case_id]
        requirements = " ".join(actual["requirements"])
        assumptions = " ".join(actual["assumptions"])
        result[case_id] = {
            "case_id": case_id,
            "requirements": actual["requirements"],
            "assumptions": actual["assumptions"],
            "required_fragments_present": {fragment: fragment in requirements for fragment in expectation["required_fragments"]},
            "assumption_fragments_present": {fragment: fragment in assumptions for fragment in expectation["assumption_fragments"]},
            "global_thickness_bounds": local_campaign_guard.CASE_THICKNESS_BOUNDS[case_id],
            "no_global_thickness_bounds": local_campaign_guard.CASE_THICKNESS_BOUNDS[case_id] is None,
        }
        result[case_id]["protocol_pass"] = (
            all(result[case_id]["required_fragments_present"].values())
            and all(result[case_id]["assumption_fragments_present"].values())
            and result[case_id]["no_global_thickness_bounds"]
        )
    return result


def minimal_route_repro(rr):
    example_dir = FOCUSED / "examples"
    names = ["research_routes_example.json", "rgb_nir_task_sheet.json", "research_routes_evidence_fixture.json", "research_routes_constraints.json"]
    values = [read_json(example_dir / name) for name in names]
    routes, sheet, evidence, constraints = values
    finite_reasons = rr.revision_reasons(routes["routes"][0], sheet, constraints)
    finite_validate = rr.validate(routes, sheet, evidence, design_constraints=constraints) is routes
    mutated = copy.deepcopy(values)
    mutated[3]["allowed_materials"] = None
    none_reasons = rr.revision_reasons(mutated[0]["routes"][0], mutated[1], mutated[3])
    mutated[0]["routes"][0]["readiness"] = "needs_revision"
    none_validate = rr.validate(mutated[0], mutated[1], mutated[2], design_constraints=mutated[3]) is mutated[0]
    materialize_error = None
    try:
        rr.materialize(mutated[0], "R1", mutated[1], mutated[2], design_constraints=mutated[3])
    except Exception as exc:
        materialize_error = {"type": type(exc).__name__, "message": str(exc)}
    bounded_hits = {}
    for relative in BOUNDED_ROUTE_SCAN:
        path = SOURCE / relative
        hits = []
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            low = line.casefold()
            if any(token in low for token in ("inventory", "category", "allowed_materials")):
                hits.append({"line": line_no, "text": line.strip()})
        bounded_hits[relative] = hits
    constraint_keys = sorted(constraints.keys())
    alternate_fields = sorted(key for key in constraint_keys if "invent" in key.casefold() or "categor" in key.casefold())
    return {
        "fixture_files": names,
        "finite_constraints": {
            "route_readiness": routes["routes"][0].get("readiness"),
            "revision_reasons": finite_reasons,
            "validate_pass": finite_validate,
        },
        "allowed_materials_none": {
            "revision_reasons": none_reasons,
            "missing_configuration_reason_present": "missing configuration: allowed_materials" in none_reasons,
            "validate_after_explicit_needs_revision": none_validate,
            "materialize_error": materialize_error,
        },
        "fixture_constraint_keys": constraint_keys,
        "alternate_inventory_or_category_fields": alternate_fields,
        "bounded_source_scan": bounded_hits,
        "interpretation": "Current route contract has no supported inventory/category alternative for broad case04 material permission; allowed_materials=None is treated as missing configuration and blocks materialization.",
        "repro_pass": (
            routes["routes"][0].get("readiness") == "ready"
            and finite_reasons == []
            and finite_validate
            and "missing configuration: allowed_materials" in none_reasons
            and none_validate
            and materialize_error is not None
            and materialize_error["message"] == "research routes route.readiness: needs_revision; materialization blocked"
            and not alternate_fields
        ),
    }


def material_audit(MaterialRegistry):
    registry = MaterialRegistry(materials_dir=MATERIALS, rii_db_path=RII_DB)
    records = {}
    source_hashes = {"rii_cache.db": sha256_file(RII_DB)}
    for csv_path in sorted(MATERIALS.glob("*.csv")):
        source_hashes[str(csv_path.relative_to(SOURCE))] = sha256_file(csv_path)
    registry_rows = {}
    for case_id, config in CASES.items():
        lo_nm, hi_nm = config["lo_nm"], config["hi_nm"]
        wavelengths_nm = np.arange(lo_nm, hi_nm + 1, dtype=np.int64)
        wavelengths_um = wavelengths_nm.astype(np.float64) / 1000.0
        rows = []
        for spec in config["materials"]:
            sample = registry.sample(spec["material"], wavelengths_um, provider=spec["provider"], dataset_id=spec["dataset_id"], allow_extrapolation=False)
            provenance = dict(sample.provenance)
            resolved_path = Path(sample.ref.filepath) if sample.ref and sample.ref.filepath else None
            if resolved_path and not resolved_path.is_absolute():
                resolved_path = SOURCE / "veritmm" / "tmm_engine" / resolved_path
            record = {
                "alias": spec["alias"],
                "requested_material": spec["material"],
                "requested_provider": spec["provider"],
                "requested_dataset_id": spec["dataset_id"],
                "resolved_ref": jsonable(sample.ref),
                "resolved_provenance": jsonable(provenance),
                "resolved_source_path": str(resolved_path) if resolved_path else None,
                "resolved_source_sha256": sha256_file(resolved_path) if resolved_path and resolved_path.exists() else None,
                "query_grid_nm": [int(lo_nm), int(hi_nm)],
                "query_count": int(wavelengths_nm.size),
                "query_step_nm": 1,
                "allow_extrapolation": False,
                "extrapolated_count": int(np.count_nonzero(sample.extrapolated_mask)),
                "extrapolated": bool(sample.extrapolated),
                "finite_n": bool(np.all(np.isfinite(sample.n))),
                "finite_k": bool(np.all(np.isfinite(sample.k))),
                "k_nonzero_count": int(np.count_nonzero(sample.k != 0.0)),
                "k_nonzero": bool(np.any(sample.k != 0.0)),
                "n_min": float(np.min(sample.n)),
                "n_max": float(np.max(sample.n)),
                "k_min": float(np.min(sample.k)),
                "k_max": float(np.max(sample.k)),
                "warnings": list(sample.warnings),
            }
            rows.append(record)
            registry_rows[f"{case_id}:{spec['alias']}"] = record
        records[case_id] = {
            "case_id": case_id,
            "grid": {"min_nm": lo_nm, "max_nm": hi_nm, "step_nm": 1, "count": int(wavelengths_nm.size)},
            "substrate_alias": config["substrate"],
            "records": rows,
            "all_records_full_grid_in_range": all(r["extrapolated_count"] == 0 for r in rows),
            "all_n_k_finite": all(r["finite_n"] and r["finite_k"] for r in rows),
            "provider_losses_preserved": {r["alias"]: r["k_nonzero"] for r in rows},
        }
    return records, source_hashes, registry_rows


def optical_media_for(case_id):
    config = CASES[case_id]
    media = {"air": {"constant_n": 1.0, "constant_k": 0.0}}
    for spec in config["materials"]:
        row = {"material": spec["material"], "provider": spec["provider"], "dataset_id": spec["dataset_id"]}
        media[spec["alias"]] = row
        if case_id == "e2e-05-visual-absorber-cr" and spec["alias"] == "Al":
            # The parent launcher names the semi-infinite exit medium aluminum.
            media["aluminum"] = dict(row)
    return media


def main():
    out_root = TASK / "material-preflight"
    preflight_root = TASK / "preflight"
    out_root.mkdir(parents=True, exist_ok=True)
    preflight_root.mkdir(parents=True, exist_ok=True)
    provenance = read_json(PROV_PATH)
    before = {
        "source_commit": git("rev-parse", "HEAD"),
        "source_status_porcelain": git("status", "--porcelain"),
        "source_bindings": {rel: sha256_file(SOURCE / rel) for rel in SOURCE_BINDINGS},
        "task_inputs": {
            "HANDOFF.md": sha256_file(TASK / "HANDOFF.md"),
            "BUDGET.json": sha256_file(TASK / "BUDGET.json"),
            "INPUT_PROVENANCE.json": sha256_file(PROV_PATH),
            "C2L-R3-0049-SUPERSESSION.json": sha256_file(TASK / "C2L-R3-0049-SUPERSESSION.json"),
        },
    }
    if before["source_commit"] != EXPECTED_SOURCE_SHA:
        raise RuntimeError(f"source SHA mismatch: {before['source_commit']} != {EXPECTED_SOURCE_SHA}")
    sys.path.insert(0, str(FOCUSED))
    sys.path.insert(0, str(SOURCE))
    import local_campaign_guard
    import research_routes
    from veritmm.tmm_engine.material_registry import MaterialRegistry

    import_bindings = {
        "local_campaign_guard": module_binding(local_campaign_guard),
        "research_routes": module_binding(research_routes),
        "material_registry": module_binding(sys.modules["veritmm.tmm_engine.material_registry"]),
    }
    questions = question_audit(provenance)
    protocol = protocol_audit(local_campaign_guard)
    route_repro = minimal_route_repro(research_routes)
    material_records, material_source_hashes, registry_rows = material_audit(MaterialRegistry)

    for case_id, config in CASES.items():
        case_dir = out_root / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        grid = list(range(config["lo_nm"], config["hi_nm"] + 1))
        write_json(case_dir / "wavelengths.json", grid)
        write_json(case_dir / "optical_media.json", optical_media_for(case_id))
        write_json(case_dir / "material_audit.json", {
            "schema_version": "t032.material_preflight.v1",
            "case_id": case_id,
            "source_commit": EXPECTED_SOURCE_SHA,
            "material_inventory_is_not_user_allowlist": case_id == "e2e-04-solarblind-uv-triple",
            "inventory_note": "These are explicit execution records with provider and dataset identity; they do not narrow the user's broad case04 ordinary inorganic dielectric permission." if case_id == "e2e-04-solarblind-uv-triple" else "These are explicit execution records for the original named materials; they do not add global thickness bounds.",
            "protocol": protocol[case_id],
            "grid": material_records[case_id]["grid"],
            "records": material_records[case_id]["records"],
            "all_records_full_grid_in_range": material_records[case_id]["all_records_full_grid_in_range"],
            "all_n_k_finite": material_records[case_id]["all_n_k_finite"],
            "provider_losses_preserved": material_records[case_id]["provider_losses_preserved"],
            "optical_media_sha256": sha256_file(case_dir / "optical_media.json"),
            "wavelengths_sha256": sha256_file(case_dir / "wavelengths.json"),
        })

    after = {
        "source_commit": git("rev-parse", "HEAD"),
        "source_status_porcelain": git("status", "--porcelain"),
        "source_bindings": {rel: sha256_file(SOURCE / rel) for rel in SOURCE_BINDINGS},
        "task_inputs": {
            "HANDOFF.md": sha256_file(TASK / "HANDOFF.md"),
            "BUDGET.json": sha256_file(TASK / "BUDGET.json"),
            "INPUT_PROVENANCE.json": sha256_file(PROV_PATH),
            "C2L-R3-0049-SUPERSESSION.json": sha256_file(TASK / "C2L-R3-0049-SUPERSESSION.json"),
        },
    }
    source_unchanged = before == after
    question_pass = all(
        row.get("fresh_request_sha256") == row.get("fresh_request_sha256_expected")
        and row.get("fresh_question_sha256") == row.get("fresh_question_sha256_expected")
        and row.get("question_utf8_sha256") == row.get("question_utf8_sha256_expected")
        and row.get("question_exact_fresh_request")
        and row.get("question_exact_replay_summary")
        and row.get("fresh_question_terminal_lf_only")
        and row.get("source_blob_match")
        and not row.get("old_answers_included", False)
        for row in questions if row.get("case_id") != "e2e-03-methane-swir-chirped"
    ) and next(row for row in questions if row.get("case_id") == "e2e-03-methane-swir-chirped")["question_exact_match_original01"]
    case_results = {}
    for case_id in CASES:
        material_pass = material_records[case_id]["all_records_full_grid_in_range"] and material_records[case_id]["all_n_k_finite"]
        if case_id == "e2e-04-solarblind-uv-triple":
            status = "blocked_shared_route_contract"
            reason = "Original case04 permits broad ordinary depositable inorganic dielectrics, but current route readiness treats allowed_materials=None as missing configuration; no supported inventory/category alternative exists in bounded source/fixture path."
        else:
            status = "ready_for_parent_launcher" if material_pass and protocol[case_id]["protocol_pass"] else "blocked_material_or_protocol"
            reason = None if status == "ready_for_parent_launcher" else "material or protocol preflight failed"
        case_results[case_id] = {
            "status": status,
            "reason": reason,
            "protocol_pass": protocol[case_id]["protocol_pass"],
            "material_pass": material_pass,
            "grid": material_records[case_id]["grid"],
            "all_records_full_grid_in_range": material_records[case_id]["all_records_full_grid_in_range"],
            "all_n_k_finite": material_records[case_id]["all_n_k_finite"],
        }
    report = {
        "schema_version": "t032.offline_admission.v1",
        "global_pass": bool(source_unchanged and question_pass and route_repro["repro_pass"] and all(v["protocol_pass"] and v["material_pass"] and v["status"] == "ready_for_parent_launcher" for v in case_results.values())),
        "global_pass_explanation": "false because case04 is blocked by the shared route readiness contract; cases01 and05 are independently material/protocol ready.",
        "tested_source_sha": EXPECTED_SOURCE_SHA,
        "actual_tests": {"status": "pending_exact_targeted_command", "command": None, "returncode": None, "passed": None, "failed": None, "skipped": None, "errors": None},
        "counters": {"http_calls": 0, "model_calls": 0, "retrieval_calls": 0, "physics_calls": 0, "fees_cny": 0.0},
        "source_before": before,
        "source_after": after,
        "source_unchanged": source_unchanged,
        "import_bindings": import_bindings,
        "input_provenance_path": str(PROV_PATH),
        "input_provenance_sha256": sha256_file(PROV_PATH),
        "question_checks": questions,
        "protocol_checks": protocol,
        "route_contract_repro": route_repro,
        "material_source_hashes": material_source_hashes,
        "material_records": material_records,
        "cases": case_results,
        "case_blockers": {
            "e2e-04-solarblind-uv-triple": "critical_shared_route_contract_allowed_materials_none_is_missing_configuration",
            "e2e-01-methane-swir-window": None,
            "e2e-05-visual-absorber-cr": None,
        },
        "independent_cases_may_proceed": {
            "e2e-01-methane-swir-window": case_results["e2e-01-methane-swir-window"]["status"] == "ready_for_parent_launcher",
            "e2e-04-solarblind-uv-triple": False,
            "e2e-05-visual-absorber-cr": case_results["e2e-05-visual-absorber-cr"]["status"] == "ready_for_parent_launcher",
        },
        "owned_output_files": [str(p) for p in sorted(out_root.rglob("*")) if p.is_file()],
        "limitations": ["No model, retrieval, HTTP, fee, TMM, solver, optimization, or numerical physics call was made.", "Case04 requires parent/manual readiness decision before any paid admission because broad ordinary dielectric permission is not representable as allowed_materials=None under the current route contract."],
    }
    # Recompute output list after writing report-independent case files.
    write_json(preflight_root / "T032_OFFLINE_ADMISSION.json", report)
    write_json(preflight_root / "T032_OFFLINE_MATERIAL_REGISTRY_ROWS.json", {"source_commit": EXPECTED_SOURCE_SHA, "rows": registry_rows})
    write_json(preflight_root / "T032_OFFLINE_RUN_METADATA.json", {
        "argv": sys.argv,
        "python": sys.executable,
        "cwd": str(Path.cwd()),
        "source": str(SOURCE),
        "task": str(TASK),
        "source_sha": EXPECTED_SOURCE_SHA,
        "no_network_or_provider_calls": True,
    })
    print(json.dumps({"global_pass": report["global_pass"], "source_unchanged": source_unchanged, "question_pass": question_pass, "route_repro_pass": route_repro["repro_pass"], "cases": case_results}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
