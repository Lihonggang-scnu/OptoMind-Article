import hashlib, json, math, subprocess, sys
from pathlib import Path

ROOT = Path(r"D:\OptoMind-Article-3")
TASK = ROOT / "local-r3-handoff" / "tasks" / "R3-T035"
T034_LOCAL = ROOT / "local-r3-handoff" / "tasks" / "R3-T034"
T034_HANDOFF = ROOT / "r3t034a" / "handoff" / "R3-T034-CACHED-CONTINUATION-20261016"
T034_CASE = T034_LOCAL / "cases" / "e2e-02-space-qkd-cband"
SEARCH = T034_CASE / "search"
SRC = ROOT / "r3t035a"
PYTHON = ROOT / "local-integration" / "20261013" / "envs" / "tensor" / "Scripts" / "python.exe"
P002 = "design:0dfa0da6805d956226e86249295b9c269130a4ed0247971be4bbc9df5382a2ea"
REQ_HANDOFF = T034_HANDOFF / "cases" / "e2e-02-space-qkd-cband" / "model" / "r3-a6875425ad8d42ba803c_REQUEST.json"
REQ_AUDIT = T034_CASE / "private" / "audit" / "40e40a05b74830c1222c" / "calls" / "r3-a6875425ad8d42ba803c" / "REQUEST.json"
REFS = T034_HANDOFF / "FULL_LOCAL_ARCHIVE_REFERENCES.json"
SM_PATH = TASK / "SOURCE_HASHES.json"
RM_PATH = TASK / "OFFLINE_DERIVATIVE_REPLAY.json"
REL_PATH = TASK / "RELEASE.json"
PRE = TASK / "preflight"
REPLAY = TASK / "replay"
CORR = TASK / "quality-correction"

def h(p):
    x = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            x.update(b)
    return x.hexdigest()

def j(p):
    return json.loads(p.read_text(encoding="utf-8"))

def w(p, x):
    p.write_text(json.dumps(x, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def eq(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=1e-15)
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(eq(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(eq(x, y) for x, y in zip(a, b))
    return a == b

def git(*args):
    return subprocess.check_output(["git", "-C", str(SRC), *args], text=True, stderr=subprocess.STDOUT).strip()

def p002_rows(x):
    rows = []
    def walk(v, path=()):
        if isinstance(v, dict):
            if isinstance(v.get("representative_decisions"), list):
                for i, row in enumerate(v["representative_decisions"]):
                    if isinstance(row, dict) and row.get("parent_alias") == "P002":
                        rows.append(("/".join(path + ("representative_decisions", str(i))), row))
            for k, z in v.items():
                walk(z, path + (str(k),))
        elif isinstance(v, list):
            for i, z in enumerate(v):
                walk(z, path + (str(i),))
    walk(x)
    return rows

def wire(path):
    root = json.loads(path.read_bytes())
    payloads = [
        ("messages[1].content", json.loads(root["messages"][1]["content"])),
        ("wire_body.messages[1].content", json.loads(root["wire_body"]["messages"][1]["content"])),
    ]
    rows = []
    for label, payload in payloads:
        hits = p002_rows(payload)
        if len(hits) != 1:
            raise AssertionError(f"{label} P002 rows={len(hits)}")
        path_text, row = hits[0]
        by = {v["name"]: v for v in row["local_direction_facts"]["variables"]}
        rows.append({
            "source": label, "path": path_text, "parent_alias": row["parent_alias"], "design_id": row["design_id"],
            "metrics": row.get("metrics"),
            "variables": {n: {k: by[n].get(k) for k in ("current_value_nm", "bounds_nm", "derivative_per_nm", "improvement_rate_normalized", "missing_goal_derivatives", "feasible_directions")} for n in ("dH", "dL", "dC1", "dC2")},
        })
    for z in rows:
        if list(z["variables"]["dL"]["derivative_per_nm"]) != ["G2"] or z["variables"]["dL"]["missing_goal_derivatives"] != ["G1"]:
            raise AssertionError("wire dL omission mismatch")
        if list(z["variables"]["dC2"]["derivative_per_nm"]) != ["G1"] or z["variables"]["dC2"]["missing_goal_derivatives"] != ["G2"]:
            raise AssertionError("wire dC2 omission mismatch")
    if not eq(rows[0]["variables"], rows[1]["variables"]):
        raise AssertionError("user/wire P002 rows differ")
    return {"path": str(path), "sha256": h(path), "size_bytes": path.stat().st_size, "root_keys": list(root), "production_call_id": root.get("production_call_id"), "messages_count": len(root["messages"]), "wire_body_messages_count": len(root["wire_body"]["messages"]), "matches": rows, "dL_wire_has_G1": False, "dC2_wire_has_G2": False}

def full():
    envp, archp, nump = SEARCH / "design_envelopes.json", SEARCH / "design_archive.json", SEARCH / "numerical_search.json"
    envs, arch, branches = j(envp), j(archp), j(nump)
    env = envs[P002]
    hits = []
    for bi, branch in enumerate(branches):
        search = branch.get("search", branch) if isinstance(branch, dict) else {}
        for pi, row in enumerate(search.get("evaluations", [])):
            if row.get("evaluation_index") == 50 and list(row.get("parameters_nm", {})) == ["dH", "dL", "dC1", "dC2"]:
                hits.append((bi, pi, row))
    if len(hits) != 1:
        raise AssertionError(f"full Jacobian row count={len(hits)}")
    bi, pi, row = hits[0]
    matrix = [[0.0013870135047967156, 0.0005033902132708982, 0.005985350898379451, 0.0062055534841278116], [-0.10327362026277613, -0.041632459628660946, -0.01083076901431185, -0.003215842098587008]]
    vals = {"dH": 85.0, "dL": 148.30703694325155, "dC1": 550.0, "dC2": 550.0}
    bounds = {"dH": [85.0, 95.0], "dL": [135.0, 155.0], "dC1": [500.0, 550.0], "dC2": [500.0, 550.0]}
    if row["metric_jacobian_wrt_normalized_parameters"] != matrix or row["parameters_nm"] != vals:
        raise AssertionError("full Jacobian/current values mismatch")
    if row.get("parent_evaluation_index") != 24 or env["numerical_search"]["evaluation_index"] != 50:
        raise AssertionError("evaluation identity mismatch")
    if env["numeric_search_observations"][0]["design_id"] != P002 or env["numeric_search_observations"][0]["evaluation_index"] != 50:
        raise AssertionError("design identity mismatch")
    if env["numerical_search"]["search_policy"]["original_family_bounds_nm"] != bounds:
        raise AssertionError("bounds mismatch")
    ae = [x for x in arch["evaluations"] if x.get("design_id") == P002]
    if len(ae) != 1:
        raise AssertionError("archive evaluation mismatch")
    ae = ae[0]
    fr = ae["full_result_ref"]
    frp = Path(fr["path"])
    if not frp.exists() or h(frp) != fr["sha256"]:
        raise AssertionError("full result reference mismatch")
    layers = [x["thickness_nm"] for x in arch["designs"][P002]["physical_input"]["stack"]["layers"]]
    expected_layers = [85.0, 148.30703694325155, 85.0, 550.0, 85.0, 148.30703694325155, 85.0, 550.0, 85.0, 148.30703694325155, 85.0, 148.30703694325155, 85.0, 148.30703694325155]
    if layers != expected_layers:
        raise AssertionError("physical layer values mismatch")
    ref_map = {x["local_path"]: x for x in j(REFS)}
    ref_checks = []
    for name in ("design_envelopes.json", "design_archive.json", "numerical_search.json", "execution_inputs.json", "execution_results.json", "input_bindings.json", "manifest.json", "summary.json"):
        p = SEARCH / name
        e = ref_map.get(str(p))
        if e is None or not p.exists():
            raise AssertionError(f"missing archive ref {p}")
        a = {"path": str(p), "sha256": h(p), "size_bytes": p.stat().st_size, "expected_sha256": e["sha256"], "expected_size_bytes": e["size_bytes"]}
        a["hash_match"], a["size_match"] = a["sha256"] == e["sha256"], a["size_bytes"] == e["size_bytes"]
        if not a["hash_match"] or not a["size_match"]:
            raise AssertionError(f"archive ref mismatch {p}")
        ref_checks.append(a)
    return {"authoritative_file": str(nump), "authoritative_file_sha256": h(nump), "design_envelopes_sha256": h(envp), "design_archive_sha256": h(archp), "branch_index": bi, "evaluation_position": pi, "evaluation_index": 50, "parent_evaluation_index": 24, "design_id": P002, "source_instance_id": env["instance_id"], "route_id": env["route_id"], "parameterization": env["numerical_search"]["parameterization"], "variable_names": list(row["parameters_nm"]), "current_values_nm": vals, "bounds_nm": bounds, "original_family_bounds_nm": bounds, "metrics": row.get("metrics"), "uncertified_metrics": env["numerical_search"]["uncertified_metrics"], "jacobian_wrt_normalized_parameters": matrix, "archive_evaluation": {"instance_id": ae.get("instance_id"), "route_id": ae.get("route_id"), "evaluation_key": ae.get("evaluation_key"), "full_result_ref": fr, "full_result_sha256": h(frp), "input_binding": ae.get("input_binding")}, "physical_input_layer_thicknesses_nm": layers, "archive_reference_checks": ref_checks, "full_source_authority": True, "compact_test_transcription_not_authority": True}

def legacy_match(w, before):
    wm = w["matches"][0]["variables"]
    bm = {x["name"]: x for x in before["variables"]}
    fields = ("current_value_nm", "bounds_nm", "derivative_per_nm", "improvement_rate_normalized", "missing_goal_derivatives", "feasible_directions")
    checks = {n: {f: eq(wm[n][f], bm[n][f]) for f in fields} for n in ("dH", "dL", "dC1", "dC2")}
    return {"all_fields_match": all(all(v.values()) for v in checks.values()), "checks_by_variable": checks, "unknown_legacy_omissions_preserved": True}

def main():
    PRE.mkdir(parents=True, exist_ok=True)
    REPLAY.mkdir(parents=True, exist_ok=True)
    CORR.mkdir(parents=True, exist_ok=True)
    rel, sm, rm = j(REL_PATH), j(SM_PATH), j(RM_PATH)
    source_files = {"family_optimization.py": SRC / "focused_research" / "family_optimization.py", "decision_context.py": SRC / "focused_research" / "decision_context.py", "test_t035_complete_derivative_vectors.py": SRC / "focused_research" / "tests" / "test_t035_complete_derivative_vectors.py"}
    source_before = {k: {"path": str(p), "sha256": h(p), "size_bytes": p.stat().st_size} for k, p in source_files.items()}
    head, status = git("rev-parse", "HEAD"), git("status", "--porcelain")
    assert head == rel["implementation_sha"], (head, rel["implementation_sha"])
    assert rel["base_sha"] == "8a38b2d0d7b18c4308c4f5978691a234f6ab3732"
    assert (rel["max_new_http"], rel["max_new_physical_evaluations"], rel["max_new_cost_cny"]) == (0, 0, 0)
    supplied = {"REPORT.md": T034_LOCAL / "REPORT.md", "RUN_SUMMARY.json": T034_LOCAL / "RUN_SUMMARY.json", "FULL_LOCAL_ARCHIVE_REFERENCES.json": REFS, "quality-review/case02/CASE02_QUALITY_REVIEW.json": T034_LOCAL / "quality-review" / "case02" / "CASE02_QUALITY_REVIEW.json", "cases/e2e-02-space-qkd-cband/model/r3-a6875425ad8d42ba803c_REQUEST.json": REQ_HANDOFF, "cases/e2e-02-space-qkd-cband/revision/raw_response.json": T034_CASE / "revision" / "raw_response.json", "cases/e2e-02-space-qkd-cband/revision/revision_prompt.json": T034_CASE / "revision" / "revision_prompt.json", "cases/e2e-02-space-qkd-cband/revision/revision_aliases.json": T034_CASE / "revision" / "revision_aliases.json", "cases/e2e-02-space-qkd-cband/revision/revision_parent.json": T034_CASE / "revision" / "revision_parent.json"}
    artifacts = {}
    for relpath, exp in sm["files"].items():
        p = supplied[relpath]
        a = {"path": str(p), "exists": p.exists(), "size_bytes": p.stat().st_size if p.exists() else None, "expected_size_bytes": exp["size_bytes"], "expected_sha256": exp["sha256"]}
        if p.exists():
            a["sha256"], a["hash_match"] = h(p), h(p) == exp["sha256"]
        if not a["exists"] or a["size_bytes"] != a["expected_size_bytes"] or not a.get("hash_match"):
            raise AssertionError(f"artifact mismatch {relpath}: {a}")
        artifacts[relpath] = a
    wr, wa = wire(REQ_HANDOFF), wire(REQ_AUDIT)
    assert wr["sha256"] == sm["files"]["cases/e2e-02-space-qkd-cband/model/r3-a6875425ad8d42ba803c_REQUEST.json"]["sha256"]
    assert wr["sha256"] == wa["sha256"] and wr["matches"] == wa["matches"]
    fs = full()
    goals = j(SEARCH / "design_envelopes.json")[P002]["objectives"]
    row = {"evaluation_index": 50, "parameters_nm": fs["current_values_nm"], "metric_jacobian_wrt_normalized_parameters": fs["jacobian_wrt_normalized_parameters"]}
    sys.path.insert(0, str(SRC))
    from focused_research.family_optimization import summarize_local_derivatives
    from focused_research.decision_context import local_direction_facts
    names = fs["variable_names"]
    summary = summarize_local_derivatives(row, names, [fs["bounds_nm"][n] for n in names], goals, top_k=3, original_family_bounds_nm=fs["original_family_bounds_nm"], bounds_role="original_family_bounds")
    facts = local_direction_facts(summary, goals)
    expected_top = rm["per_goal_top_k_rankings"]
    observed_top = {x["objective_id"]: [v["name"] for v in x["top_variables"]] for x in summary["goal_sensitivities"]}
    assert observed_top == expected_top, (observed_top, expected_top)
    observed_union = [x["name"] for x in summary["selected_variable_derivatives"]]
    assert set(observed_union) == set(rm["selected_parameter_names_after"]) == set(rm["selected_parameter_names_before"])
    expected_after = {x["name"]: x for x in rm["after"]["variables"]}
    observed = {x["name"]: x for x in facts["variables"]}
    checks = {}
    for n in observed_union:
        checks[n] = {k: eq(observed[n][k], expected_after[n][k]) for k in ("current_value_nm", "bounds_nm", "derivative_per_nm", "improvement_rate_normalized", "missing_goal_derivatives", "feasible_directions")}
    assert all(all(v.values()) for v in checks.values()), checks
    assert facts["derivative_coverage"] == rm["after"]["derivative_coverage"]
    assert facts["same_direction_opportunities"] == rm["after"]["same_direction_opportunities"]
    lm = legacy_match(wr, rm["before"])
    assert lm["all_fields_match"], lm
    dL, dC2 = observed["dL"], observed["dC2"]
    correction_values = {"dL_G1": {"normalized": dL["improvement_rate_normalized"]["G1"], "span_nm": 20.0, "per_nm": dL["derivative_per_nm"]["G1"]}, "dC2_G2": {"normalized": dC2["improvement_rate_normalized"]["G2"], "span_nm": 50.0, "per_nm": dC2["derivative_per_nm"]["G2"]}}
    pre = {"task_id": "T035", "status": "preflight_pass", "global_pass": True, "exact_argv": [str(PYTHON), "-B", "-X", "utf8", str(PRE / "T035_replay_driver.py")], "source_binding": {"source_repo": str(SRC), "base_sha": rel["base_sha"], "implementation_sha": rel["implementation_sha"], "git_head": head, "git_status_porcelain": status, "source_before": source_before}, "manifest_bindings": {"release": {"path": str(REL_PATH), "sha256": h(REL_PATH), "implementation_sha": rel["implementation_sha"], "base_sha": rel["base_sha"], "zero_caps": {"http": rel["max_new_http"], "physical": rel["max_new_physical_evaluations"], "cost_cny": rel["max_new_cost_cny"]}}, "source_hashes": {"path": str(SM_PATH), "sha256": h(SM_PATH), "source_result": sm["source_result"], "manifest_base_sha": sm["source_implementation_sha"]}, "offline_derivative_replay": {"path": str(RM_PATH), "sha256": h(RM_PATH), "legacy_reconstruction_matches_actual_wire": rm["legacy_reconstruction_matches_actual_wire"]}}, "supplied_input_hashes": artifacts, "request_binding": {"handoff": wr, "private_audit": {"path": str(REQ_AUDIT), "sha256": wa["sha256"], "size_bytes": wa["size_bytes"]}, "byte_identity": wr["sha256"] == wa["sha256"], "wire_derivative_omissions_verified": {"dL_missing_G1": True, "dC2_missing_G2": True}}, "authoritative_full_source": fs, "production_reprojection": {"summary_status": summary["status"], "facts_status": facts["status"], "top_k": observed_top, "selected_union_before": rm["selected_parameter_names_before"], "selected_union_after": observed_union, "legacy_wire_vs_saved_before": lm, "derived_vector_checks": checks, "derivative_coverage": facts["derivative_coverage"], "same_direction_opportunities": facts["same_direction_opportunities"], "joint_direction": facts["joint_direction"], "correction_values": correction_values, "new_observations": 0, "new_forward_rows": summary["additional_forward_design_rows"], "new_model_calls": 0, "new_physical_evaluations": 0, "new_retrieval_calls": 0, "new_cost_cny": 0}, "first_check": {"full_jacobian_authority": str(SEARCH / "numerical_search.json"), "compact_test_transcription_used_as_authority": False, "user_json_and_wire_body_checked": True, "source_imports_local_only": True}}
    w(PRE / "T035_LOCAL_PRECHECK.json", pre)
    replay = {"task_id": "T035", "status": "replay_pass", "source_sha": head, "full_source": fs, "wire_legacy": wr, "legacy_vs_before": lm, "production_summary": summary, "production_direction_facts": facts, "expected_after_match": checks, "top_k_unchanged": observed_top == expected_top, "selected_union_unchanged": set(rm["selected_parameter_names_before"]) == set(observed_union), "selected_union_source_order": observed_union, "selected_union_manifest_order": rm["selected_parameter_names_after"], "all_goals_complete": facts["derivative_coverage"]["available_complete_coordinates"] == 4, "unknown_legacy_omissions_preserved": True, "unit_correction": rm["dL_G1_unit_correction"], "correction_values": correction_values, "zero_counters": {"http": 0, "retrieval": 0, "model": 0, "physics": 0, "forward_rows": 0, "certification": 0, "ledger_writes": 0, "cost_cny": 0}}
    w(REPLAY / "T035_P002_DERIVATIVE_REPLAY.json", replay)
    correction = {"task_id": "T035", "status": "additive_correction_only", "preserve_original_report": str(T034_LOCAL / "quality-review" / "case02" / "CASE02_QUALITY_REVIEW.json"), "source_report_sha256": sm["files"]["quality-review/case02/CASE02_QUALITY_REVIEW.json"]["sha256"], "case": "e2e-02-space-qkd-cband", "parent_alias": "P002", "design_id": P002, "correction": {"actual_wire_statement": "Immutable actual model-facing REQUEST compact P002 row has dL only for G2 with missing_goal_derivatives=['G1'], and dC2 only for G1 with missing_goal_derivatives=['G2']; user JSON and wire_body copies are byte-identical.", "full_saved_jacobian_statement": "Authoritative saved numerical-search evaluation 50 has complete G1/G2 derivatives for dH,dL,dC1,dC2.", "attribution": "The prior T034 criticism specifically concerning the P002 dL derivative was a misattribution: it compared compact model-facing dL (G2 only) with the fuller saved Jacobian containing dL G1. The dC2 missing-G2 field is separately a verified projection gap; this correction does not claim that the old report classified that omission as a model error. Both compact omissions remain unknown legacy values. P012 derivative sign/band findings and the P003 stable-ID versus narrative-identity finding are unaffected.", "unit_statement": "dL G1 is 0.0005033902132708982 normalized over 20 nm, hence 0.00002516951066354491 per nm. dC2 G2 is -0.003215842098587008 normalized over 50 nm, hence -0.00006431684197174016 per nm.", "scope": "Additive saved-evidence attribution and complete-vector replay only; no new observations, solver, physics, certification, retrieval, model call, or cost."}, "source_hashes": {"request_sha256": wr["sha256"], "full_numerical_search_sha256": fs["authoritative_file_sha256"], "quality_report_sha256": sm["files"]["quality-review/case02/CASE02_QUALITY_REVIEW.json"]["sha256"]}}
    w(CORR / "T034_CASE02_P002_ATTRIBUTION_CORRECTION.json", correction)
    md = f"""# T034 case02 P002 attribution correction

This is an additive correction; the original T034 quality report remains byte-preserved.

The immutable model-facing REQUEST compact P002 row reports dL for G2 only with missing G1 and dC2 for G1 only with missing G2. The user JSON and wire_body copies are byte-identical ({wr["sha256"]}).

The authoritative saved numerical-search row is evaluation 50, parent evaluation 24, design {P002}, with a complete normalized Jacobian for dH,dL,dC1,dC2. The prior T034 criticism specifically concerning the P002 dL derivative was a source-attribution mistake: it compared compact model-facing dL (G2 only) with the fuller saved Jacobian containing dL G1. The dC2 missing-G2 field is separately a verified projection gap; this correction does not claim that the old report classified that omission as a model error. Both compact omissions remain unknown legacy values.

The saved dL G1 value is 0.0005033902132708982 per normalized coordinate over a 20 nm span, or 0.00002516951066354491 per nm. The saved dC2 G2 value is -0.003215842098587008 per normalized coordinate over a 50 nm span, or -0.00006431684197174016 per nm. Normalized values are not per-nm values.

P012 derivative sign/band findings and the P003 stable-ID versus narrative-identity finding are unaffected.

This replay adds no observations, forward rows, solver or physics result, certification, retrieval, model call, or cost.
"""
    (CORR / "T034_CASE02_P002_ATTRIBUTION_CORRECTION.md").write_text(md, encoding="utf-8")
    print(json.dumps({"status": "PASS", "preflight": str(PRE / "T035_LOCAL_PRECHECK.json"), "replay": str(REPLAY / "T035_P002_DERIVATIVE_REPLAY.json"), "correction": str(CORR / "T034_CASE02_P002_ATTRIBUTION_CORRECTION.json")}, ensure_ascii=False))

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FAIL:{type(e).__name__}:{e}", file=sys.stderr)
        raise

