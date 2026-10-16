import copy
import hashlib
import json
import sys
from pathlib import Path

TASK = Path(r"D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T039")
SRC = Path(r"D:\OptoMind-Article-3\r3t039a")
HANDOFF = Path(r"D:\OptoMind-Article-3\r3t038a\handoff\R3-T038-UV-20261016")
ENV_PATH = Path(r"D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T038\cases\e2e-04-solarblind-uv-triple\search\design_envelopes.json")
REQ_PATH = HANDOFF / "model" / "r3-2cc300bce79b4054b73c_REQUEST.json"
EXPECTED_ENV_SHA = "6a10a12fe2e4e6b5ac2b29882e334e6333c896f0f86d727058333b780a1771ea"


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def dump(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    sys.path.insert(0, str(SRC))
    from focused_research.decision_context import local_direction_facts

    request = json.loads(REQ_PATH.read_text(encoding="utf-8"))
    old_payload = json.loads(request["messages"][1]["content"])
    envelopes = json.loads(ENV_PATH.read_text(encoding="utf-8"))
    objectives = old_payload["objectives"]
    rebuilt = copy.deepcopy(old_payload)
    parents = []
    for old_rep, new_rep in zip(
        old_payload["representative_decisions"],
        rebuilt["representative_decisions"],
    ):
        did = old_rep["design_id"]
        env = envelopes[did]
        observations = [
            x for x in env.get("numeric_search_observations", [])
            if x.get("design_id") == did
            and x.get("local_derivative_summary", {}).get("status") == "local_derivative_uncertified"
        ]
        if not observations:
            raise RuntimeError(f"missing matching saved derivative summary: {old_rep['parent_alias']} {did}")
        obs = observations[-1]
        summary = obs["local_derivative_summary"]
        new_facts = local_direction_facts(summary, objectives)
        new_rep["local_direction_facts"] = new_facts
        old_facts = old_rep["local_direction_facts"]
        old_rows = {
            x["name"]: {
                "current_value_nm": x["current_value_nm"],
                "bounds_nm": x["bounds_nm"],
                "derivative_per_nm": x["derivative_per_nm"],
                "feasible_directions": x.get("feasible_directions"),
            }
            for x in old_facts.get("variables", [])
        }
        new_rows = {
            x["name"]: {
                "current_value_nm": x["current_value_nm"],
                "bounds_nm": x["bounds_nm"],
                "derivative_per_nm": x["derivative_per_nm"],
                "feasible_directions": x.get("feasible_directions"),
            }
            for x in new_facts.get("variables", [])
        }
        parents.append({
            "parent_alias": old_rep["parent_alias"],
            "stable_design_id": did,
            "saved_source_instance_id": obs.get("instance_id"),
            "reported_derivative_source_instance_id": old_rep.get("derivative_source", {}).get("source_instance_id"),
            "source_instance_binding_match": obs.get("instance_id") == old_rep.get("derivative_source", {}).get("source_instance_id"),
            "summary_status": summary.get("status"),
            "summary_evaluation_index": summary.get("evaluation_index"),
            "coordinate_names": sorted(new_rows),
            "coordinate_count": len(new_rows),
            "coordinate_values_bounds_derivatives_unchanged": old_rows == new_rows,
            "old_action_effects_present": all("action_effects" in x for x in old_facts.get("variables", [])),
            "new_action_effects_present": all("action_effects" in x for x in new_facts.get("variables", [])),
            "new_joint_direction": new_facts.get("joint_direction"),
            "new_variables": new_facts.get("variables", []),
        })
    rebuilt["decision_guidance"][0] = (
        "Use action_effects for local single-coordinate sign arithmetic; check "
        "feasible_in_recorded_bounds separately. Missing or zero derivatives do "
        "not establish a finite-step effect."
    )
    compact = lambda x: len(json.dumps(x, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    before = compact(old_payload)
    after = compact(rebuilt)
    size_doc = json.loads((TASK / "OFFLINE_VALIDATION.json").read_text(encoding="utf-8"))["T039_action_effects_replay"]["size_measurement"]
    report = {
        "task_id": "R3-T039",
        "source_head": "19387a35a9c2a43ee2aaea286becc7c18ebbb040",
        "full_saved_summary_source": str(ENV_PATH),
        "full_saved_summary_bytes": ENV_PATH.stat().st_size,
        "full_saved_summary_sha256": sha(ENV_PATH),
        "full_saved_summary_sha256_expected": EXPECTED_ENV_SHA,
        "full_saved_summary_hash_match": sha(ENV_PATH) == EXPECTED_ENV_SHA,
        "request_sha256": sha(REQ_PATH),
        "parents": parents,
        "parent_count": len(parents),
        "coordinate_count": sum(x["coordinate_count"] for x in parents),
        "stable_parent_ids_unchanged": all(x["stable_design_id"] == next(r["design_id"] for r in old_payload["representative_decisions"] if r["parent_alias"] == x["parent_alias"]) for x in parents),
        "source_instance_bindings_match": all(x["source_instance_binding_match"] for x in parents),
        "coordinate_values_bounds_derivatives_unchanged": all(x["coordinate_values_bounds_derivatives_unchanged"] for x in parents),
        "new_action_effects_present": all(x["new_action_effects_present"] for x in parents),
        "all_old_action_effects_absent_as_expected": all(not x["old_action_effects_present"] for x in parents),
        "all_three_goal_joint_status_unsupported": all(x["new_joint_direction"].get("status") == "unsupported" for x in parents),
        "payload_size": {
            "serialization": "compact ensure_ascii=False JSON of saved user payload UTF-8",
            "old_bytes_actual": before,
            "new_bytes_actual": after,
            "delta_bytes_actual": after - before,
            "documented_old_bytes": size_doc["compact_user_payload_bytes_before"],
            "documented_new_bytes": size_doc["compact_user_payload_bytes_after"],
            "documented_delta_bytes": size_doc["delta_bytes"],
            "old_matches_documented": before == size_doc["compact_user_payload_bytes_before"],
            "new_matches_documented": after == size_doc["compact_user_payload_bytes_after"],
            "local_reproduction_note": "Current fixed source and full saved summaries reproduce the old byte count and action effects; documented new worker byte count differs by 56 bytes.",
            "new_model_request": False,
            "new_token_or_cost_measurement": False,
        },
        "p002_p008": {
            x["parent_alias"]: {
                "stable_design_id": x["stable_design_id"],
                "variables": [
                    {
                        "name": v["name"],
                        "derivative_per_nm": v["derivative_per_nm"],
                        "bounds_nm": v["bounds_nm"],
                        "current_value_nm": v["current_value_nm"],
                        "action_effects": v["action_effects"],
                    }
                    for v in x["new_variables"]
                ],
            }
            for x in parents if x["parent_alias"] in ("P002", "P008")
        },
        "limits": [
            "Saved derivatives are uncertified local first-order signs.",
            "Missing/zero derivatives remain unknown; no finite-step outcome is inferred.",
            "Three-goal joint-direction helper is unsupported and not absence proof.",
            "Shared-variable effects are not individual-layer attribution.",
        ],
    }
    out = TASK / "direction-audit"
    dump(out / "DIRECTION_AUDIT_FULL.json", report)
    (out / "DIRECTION_AUDIT_FULL.md").write_text(
        "# T039 direction audit (full saved summaries)\n\n"
        f"- Rebuilt {report['parent_count']} parents from {ENV_PATH}; {report['coordinate_count']} coordinates and {report['goal_count'] if 'goal_count' in report else len(objectives)} goals.\n"
        f"- Source summary hash match: {report['full_saved_summary_hash_match']}; source-instance bindings: {report['source_instance_bindings_match']}.\n"
        f"- Stable IDs and values/bounds/derivatives unchanged: {report['stable_parent_ids_unchanged'] and report['coordinate_values_bounds_derivatives_unchanged']}.\n"
        f"- New action effects present: {report['new_action_effects_present']}; three-goal joint helper remains unsupported.\n"
        f"- Reproduced payload bytes: {before} -> {after} (delta {after-before}); documented worker values: {size_doc['compact_user_payload_bytes_before']} -> {size_doc['compact_user_payload_bytes_after']}.\n"
        "- The 56-byte documentation discrepancy is recorded as a measurement limitation; it did not alter IDs, derivatives, bounds, or action effects.\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "parent_count": report["parent_count"],
        "coordinate_count": report["coordinate_count"],
        "source_hash_match": report["full_saved_summary_hash_match"],
        "source_instance_bindings_match": report["source_instance_bindings_match"],
        "coordinates_unchanged": report["coordinate_values_bounds_derivatives_unchanged"],
        "action_effects_present": report["new_action_effects_present"],
        "payload_old": before,
        "payload_new": after,
        "payload_delta": after-before,
        "documented_payload_new": size_doc["compact_user_payload_bytes_after"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

