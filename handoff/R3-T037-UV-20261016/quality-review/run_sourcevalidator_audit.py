from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

SOURCE = Path(r"D:\OptoMind-Article-3\r3t037a")
TASK = Path(r"D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T037")
CASE = TASK / "cases" / "e2e-04-solarblind-uv-triple"
OUT = TASK / "quality-review"
EXPECTED_SOURCE = "49861e169084976069d9ef1008e4fafc84b86bb8"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def response_metadata(raw_text: str):
    content_sha = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
    for response_path in sorted((CASE / "private" / "audit").glob("*/calls/*/RESPONSE.json")):
        response = read_json(response_path)
        if hashlib.sha256(response.get("content", "").encode("utf-8")).hexdigest() != content_sha:
            continue
        call_dir = response_path.parent
        request_path = call_dir / "REQUEST.json"
        raw_path = next(call_dir.joinpath("raw").iterdir())
        return {
            "call_dir": str(call_dir),
            "request_file_sha256": sha256(request_path),
            "response_file_sha256": sha256(response_path),
            "raw_response_path": str(raw_path),
            "raw_response_file_sha256": sha256(raw_path),
            "raw_response_sha256_field": response.get("raw_response_sha256"),
            "status_code": response.get("status_code"),
            "requested_model": response.get("requested_model"),
            "returned_model": response.get("returned_model"),
            "finish_reason": response.get("finish_reason"),
            "usage": response.get("usage"),
            "elapsed_seconds": response.get("elapsed_seconds"),
        }
    return None


def exact_span_rows(sheet, question):
    rows = []
    for index, row in enumerate(sheet.get("subgoals", [])):
        span = row.get("original_span")
        rows.append({
            "path": f"subgoals[{index}].original_span",
            "id": row.get("id"),
            "raw_span": span,
            "exact_in_original_question": isinstance(span, str) and span in question,
            "question_offset": question.find(span) if isinstance(span, str) else None,
        })
    return rows


def validate_round(record, question):
    raw_text = record["raw_response"]
    raw = json.loads(raw_text)
    from focused_research import task_sheet

    prepared, normalization_audit = task_sheet.prepare_analysis(question, raw)
    error = None
    try:
        task_sheet.validate(prepared, question=question)
    except (ValueError, TypeError) as exc:
        error = str(exc)
    offending = next(
        (row for row in exact_span_rows(raw, question) if not row["exact_in_original_question"]),
        None,
    )
    metadata = response_metadata(raw_text)
    return {
        "round": record.get("round"),
        "stored_error": record.get("error"),
        "validator_error": error,
        "validator_ok": error is None,
        "raw_response_sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        "raw_response_length": len(raw_text),
        "raw_response_metadata": metadata,
        "offending_field": offending["path"] if offending else None,
        "offending_raw_span": offending["raw_span"] if offending else None,
        "offending_exact_in_original_question": offending["exact_in_original_question"] if offending else None,
        "offending_question_offset": offending["question_offset"] if offending else None,
        "correct_exact_span_in_question": "700–1100 nm 近红外杂散光" if offending else None,
        "correct_span_question_offset": question.find("700–1100 nm 近红外杂散光"),
        "all_subgoal_spans": exact_span_rows(raw, question),
        "normalization_audit": normalization_audit,
        "raw_proposal_summary": {
            "user_objective": raw.get("user_objective"),
            "readiness": raw.get("readiness"),
            "goal_count": len(raw.get("subgoals", [])),
            "constraint_count": len(raw.get("explicit_constraints", [])),
            "query_count": len(raw.get("search_queries", [])),
        },
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(SOURCE))
    from focused_research import task_sheet

    question_path = SOURCE / "focused_research/campaigns/t032/inputs/e2e-04-solarblind-uv-triple/FRESH_QUESTION.txt"
    request_path = SOURCE / "focused_research/campaigns/t032/inputs/e2e-04-solarblind-uv-triple/FRESH_REQUEST.json"
    question = read_json(request_path)["question"]
    planning_path = CASE / "plan" / "planning.json"
    planning = read_json(planning_path)
    rounds = [validate_round(record, question) for record in planning["rounds"]]
    final_raw = json.loads(planning["rounds"][-1]["raw_response"])
    source_task_sheet_hash = sha256(SOURCE / "focused_research/task_sheet.py")
    source_commit = subprocess.run(
        ["git", "-C", str(SOURCE), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip()

    goals = []
    for row in final_raw.get("subgoals", []):
        metric = row.get("executable_metric", {})
        region = metric.get("region", {})
        goals.append({
            "id": row.get("id"),
            "objective": row.get("objective"),
            "original_span": row.get("original_span"),
            "original_span_exact": row.get("original_span") in question,
            "metric": metric.get("metric"),
            "sense": metric.get("sense"),
            "mean_reduction": metric.get("mean_reduction"),
            "band_nm": region.get("wavelength_nm"),
            "angle_deg": region.get("angle_deg"),
            "polarization": region.get("polarization"),
            "status": "raw proposal only; no accepted task sheet",
        })
    constraints = []
    for row in final_raw.get("explicit_constraints", []):
        span = row.get("original_span")
        constraints.append({
            "id": row.get("id"),
            "constraint": row.get("constraint"),
            "original_span": span,
            "exact_in_original_question": span in question,
        })
    assumptions = [
        {
            "kind": row.get("kind"),
            "statement": row.get("statement"),
            "assumed_value": row.get("assumed_value"),
            "status": "explicitly labeled model execution assumption/unknown; not user fact",
        }
        for row in final_raw.get("unknowns_assumptions", [])
    ]
    q5_audit = next(
        (row for row in rounds[-1]["normalization_audit"]
         if row.get("source_path") == "search_queries[4].research_question_ids"),
        None,
    )
    guard = read_json(CASE / "GUARD_CURRENT.json")
    result = read_json(CASE / "CASE_RESULT.json")
    budget = read_json(CASE / "BUDGET_STATUS.json")
    report = {
        "schema_version": "t037.case04.quality_review.v1",
        "task_id": "R3-T037",
        "case_id": CASE.name,
        "source": {
            "source_commit": source_commit,
            "expected_source_commit": EXPECTED_SOURCE,
            "commit_match": source_commit == EXPECTED_SOURCE,
            "task_sheet_validator": str(SOURCE / "focused_research/task_sheet.py"),
            "task_sheet_validator_sha256": source_task_sheet_hash,
            "validation_method": "focused_research.task_sheet.prepare_analysis(question, saved_raw_response); validate(sheet, question=question)",
        },
        "input_bindings": {
            "fresh_question_path": str(question_path),
            "fresh_question_sha256": sha256(question_path),
            "fresh_request_path": str(request_path),
            "fresh_request_sha256": sha256(request_path),
            "planning_path": str(planning_path),
            "planning_sha256": sha256(planning_path),
            "question": question,
        },
        "planning_outcome": {
            "status": planning.get("status"),
            "revision_rounds": planning.get("revision_rounds"),
            "accepted_task_sheet": bool(planning.get("task_sheet")),
            "accepted_plan": bool(planning.get("plan")),
            "rounds": rounds,
            "final_error": planning.get("errors", [None])[-1],
        },
        "fidelity_audit": {
            "goals_and_metrics": goals,
            "explicit_constraints": constraints,
            "material_and_layer_reading": {
                "raw_user_objective": final_raw.get("user_objective"),
                "material_constraint": next((x for x in constraints if x["id"] == "C7"), None),
                "substrate_constraint": next((x for x in constraints if x["id"] == "C5"), None),
                "air_constraint": next((x for x in constraints if x["id"] == "C4"), None),
                "metal_constraint": next((x for x in constraints if x["id"] == "C6"), None),
                "layer_constraint": next((x for x in constraints if x["id"] == "C9"), None),
                "assessment": "Faithful broad ordinary-inorganic-material, fused-silica, air-incidence, metal-free, planar, <=30-layer reading; no named two-material whitelist was invented.",
            },
            "additional_observable_issues": [
                {
                    "field": "user_objective",
                    "observation": "The English summary says conventional inorganic materials and omits the words depositable and dielectric; authoritative C7 still preserves the exact native constraint.",
                    "impact": "Summary wording loss only; no accepted contract or downstream execution exists.",
                }
            ],
            "assumptions_and_unknowns": assumptions,
            "optional_link_audit": q5_audit,
            "scientific_claim_boundary": {
                "candidate_mechanisms": final_raw.get("candidate_mechanisms", []),
                "status": "candidate model hypotheses only; no retrieval, route generation, design search, certification or physics execution occurred.",
            },
        },
        "guard_and_budget": {
            "dispatch_attempts": guard.get("dispatch_attempts"),
            "stage_counts": guard.get("stage_counts"),
            "shared_contract_repair_used": guard.get("shared_contract_repair_used"),
            "failed_closed": guard.get("failed_closed"),
            "task_budget": budget.get("task_budget"),
            "case_result": {
                "engineering_status": result.get("engineering_status"),
                "scientific_status": result.get("scientific_status"),
                "failure_type": result.get("failure_type"),
                "reason": result.get("reason"),
                "budget_after": result.get("budget_after"),
            },
        },
        "minimal_reproduction": {
            "read_only": True,
            "network_calls": 0,
            "raw_response_paths_only": True,
            "round_results": [
                {
                    "round": row["round"],
                    "offending_field": row["offending_field"],
                    "offending_raw_span": row["offending_raw_span"],
                    "exact_in_original_question": row["offending_exact_in_original_question"],
                    "correct_exact_span": row["correct_exact_span_in_question"],
                    "validator_error": row["validator_error"],
                    "raw_response_sha256": row["raw_response_sha256"],
                    "raw_response_path": (row["raw_response_metadata"] or {}).get("raw_response_path"),
                }
                for row in rounds
            ],
            "conclusion": "Both saved rounds fail closed at subgoals[2].original_span; no task sheet or plan was accepted.",
        },
        "limitations": [
            "This is a contract/provenance audit of saved planning responses, not a scientific quality or physical-design result.",
            "Private request/response files were referenced by hashes and metadata only; headers and credentials were not copied.",
            "No route, retrieval, search, refinement or numerical artifact exists to audit after the invalid planning stage.",
        ],
    }
    json_path = OUT / "T037_CASE04_QUALITY_AUDIT.json"
    md_path = OUT / "REPORT.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    round_lines = []
    for row in rounds:
        meta = row["raw_response_metadata"] or {}
        round_lines.append(
            f"| {row['round']} | {row['offending_field']} | {row['offending_raw_span']} | "
            f"no ({row['offending_question_offset']}) | {row['validator_error']} | "
            f"{meta.get('call_dir', '')} |"
        )
    md = f"""# T037 case04 quality audit

Result: the saved original case04 run is correctly BLOCKED at planning. The task-sheet validator rejected both model rounds; no accepted task sheet or plan exists. The guard records two task-sheet dispatches, zero route-generation/search-revision stages, and shared contract repair used once.

The exact repeated failure is subgoals[2].original_span. Both raw rounds supplied "尽可能抑制 700–1100 nm 近红外杂散光", but the original question contains "700–1100 nm 近红外杂散光" after the conjunction "和"; the repeated "尽可能抑制" is not an exact native span. The source validator therefore fails closed. All other inspected goal spans and C1–C9 constraint spans are exact.

| Round | Field | Raw span | Exact? | Validator result | Saved call |
|---|---|---|---|---|---|
{chr(10).join(round_lines)}

The proposed goals otherwise map faithfully to the question: G1 maximizes mean transmission over 255–280 nm; G2 and G3 minimize mean transmission over 300–700 nm and 700–1100 nm. Air incidence, fused silica, metal-free planar dielectric construction, and the <=30-layer bound are preserved. The broad ordinary-inorganic-material permission remains broad; no named-material whitelist is introduced. Zero-degree, equal-s/p unpolarized, wavelength-integral means, and suppression-as-transmission-minimization are separately labeled execution assumptions/interpretations. Thickness bounds, material inventory and thresholds remain unresolved unknowns.

One nonfatal wording issue is visible in the English user_objective summary: it shortens “conventional depositable inorganic dielectric materials” to “conventional inorganic materials.” The authoritative C7 constraint retains the exact native wording; because the contract was rejected before acceptance, this summary loss did not reach downstream execution.

The raw candidate mechanisms and coupling language remain untested hypotheses. The normalization audit also records that Q5 has no linked research-question IDs for its three direct subgoals; this is an advisory linkage issue, not the fatal validator error. No retrieval, route generation, physics, search, certification or scientific acceptance occurred. Actual case exposure was 0.0371824 CNY across two HTTP attempts.

Minimal reproduction used only the saved raw responses, FRESH_REQUEST.json, and the current source validator; request/response headers and credentials were not copied. JSON details and hashes are in T037_CASE04_QUALITY_AUDIT.json.
"""
    md_path.write_text(md, encoding="utf-8")
    print(json.dumps({
        "json": str(json_path),
        "markdown": str(md_path),
        "rounds": len(rounds),
        "all_validator_failures_exact": all(
            row["validator_error"] == "task sheet subgoal.original_span: must be an exact span of original_question"
            for row in rounds
        ),
        "accepted_task_sheet": report["planning_outcome"]["accepted_task_sheet"],
        "dispatch_attempts": guard.get("dispatch_attempts"),
        "route_generation": guard.get("stage_counts", {}).get("route_generation"),
        "search_revision": guard.get("stage_counts", {}).get("search_revision"),
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

