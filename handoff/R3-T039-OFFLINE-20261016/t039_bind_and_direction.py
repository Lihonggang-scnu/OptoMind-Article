import copy
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

TASK = Path(r"D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T039")
SRC = Path(r"D:\OptoMind-Article-3\r3t039a")
HANDOFF = Path(r"D:\OptoMind-Article-3\r3t038a\handoff\R3-T038-UV-20261016")
LOCAL_T038 = Path(r"D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T038")
REPLAY_INPUT = TASK / "replay-input"
DIRECTION = TASK / "direction-audit"
PROV_PATH = TASK / "INPUT_PROVENANCE.json"
OFFLINE_PATH = TASK / "OFFLINE_VALIDATION.json"
REQ_PATH = HANDOFF / "model" / "r3-2cc300bce79b4054b73c_REQUEST.json"
PYTHON = Path(r"D:\OptoMind-Article-3\local-integration\20261013\envs\tensor\Scripts\python.exe")


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def check_file(path, expected_sha, expected_bytes):
    return {
        "path": str(path),
        "exists": path.is_file(),
        "bytes": path.stat().st_size if path.is_file() else None,
        "expected_bytes": expected_bytes,
        "sha256": sha256(path) if path.is_file() else None,
        "expected_sha256": expected_sha,
        "match": path.is_file() and path.stat().st_size == expected_bytes and sha256(path) == expected_sha,
    }


def main():
    prov = load(PROV_PATH)
    offline = load(OFFLINE_PATH)
    preflight = TASK / "preflight"

    head = subprocess.run(
        ["git", "-C", str(SRC), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False
    )
    status = subprocess.run(
        ["git", "-C", str(SRC), "status", "--short"],
        capture_output=True, text=True, check=False
    )
    source_head = head.stdout.strip()
    clean_status = status.stdout
    source_binding = {
        "source_directory": str(SRC),
        "head": source_head,
        "expected_head": "19387a35a9c2a43ee2aaea286becc7c18ebbb040",
        "head_match": source_head == "19387a35a9c2a43ee2aaea286becc7c18ebbb040",
        "git_status_exit": status.returncode,
        "git_status_short": clean_status,
        "clean": status.returncode == 0 and clean_status == "",
    }

    pinned = []
    for entry in prov["source_files"]:
        path = HANDOFF / Path(entry["path"])
        pinned.append(check_file(path, entry["sha256"], entry["bytes"]))

    implementation = []
    for rel, expected in offline["implementation_file_sha256"].items():
        implementation.append(check_file(SRC / Path(rel), expected, (SRC / Path(rel)).stat().st_size if (SRC / Path(rel)).is_file() else None))
        implementation[-1]["relative_path"] = rel

    route_bindings = []
    staged = []
    for entry in prov["route_replay_inputs"]:
        name = entry["staged_filename"]
        handoff_path = HANDOFF / "case" / "routes" / name
        local_path = Path(entry["original_local_source"])
        local_check = check_file(local_path, entry["sha256"], entry["bytes"])
        handoff_check = check_file(handoff_path, entry["sha256"], entry["bytes"])
        target = REPLAY_INPUT / name
        if target.exists():
            raise RuntimeError(f"refusing to overwrite existing replay input: {target}")
        shutil.copyfile(handoff_path, target)
        target_check = check_file(target, entry["sha256"], entry["bytes"])
        route_bindings.append({
            "staged_filename": name,
            "expected_sha256": entry["sha256"],
            "expected_bytes": entry["bytes"],
            "original_local": local_check,
            "handoff_packet": handoff_check,
            "staged": target_check,
            "local_handoff_equal": local_check["match"] and handoff_check["match"],
        })
        staged.append(target_check["match"])

    binding = {
        "task_id": "R3-T039",
        "source": source_binding,
        "pinned_source_files_count": len(pinned),
        "pinned_source_files": pinned,
        "implementation_files": implementation,
        "route_replay_inputs_count": len(route_bindings),
        "route_replay_inputs": route_bindings,
        "manifest_reference_scope": "13 pinned provenance files and 4 replay inputs; no full 102-file archive rehash",
        "all_pinned_match": all(x["match"] for x in pinned),
        "all_implementation_match": all(x["match"] for x in implementation),
        "all_route_match": all(x["local_handoff_equal"] and x["staged"]["match"] for x in route_bindings),
    }
    dump(preflight / "BINDING_CHECK.json", binding)

    sys.path.insert(0, str(SRC))
    from focused_research.decision_context import local_direction_facts

    request = load(REQ_PATH)
    old_payload = json.loads(request["messages"][1]["content"])
    objectives = old_payload["objectives"]
    details = []
    rebuilt_payload = copy.deepcopy(old_payload)

    for old_rep, new_rep in zip(
        old_payload["representative_decisions"],
        rebuilt_payload["representative_decisions"],
    ):
        old_facts = old_rep["local_direction_facts"]
        variables = old_facts.get("variables", [])
        sensitivities = []
        for goal in objectives:
            gid = goal["objective_id"]
            top = []
            for row in variables:
                if gid in row.get("derivative_per_nm", {}):
                    top.append({
                        "name": row["name"],
                        "derivative_per_nm": row["derivative_per_nm"][gid],
                        "current_value_nm": row["current_value_nm"],
                        "bounds_nm": row["bounds_nm"],
                    })
            sensitivities.append({"objective_id": gid, "top_variables": top})
        summary = {
            "status": old_facts.get("status"),
            "goal_gradient_conflicts": {
                "variable_count": old_facts.get("derivative_coverage", {}).get(
                    "declared_variable_count", len(variables)
                )
            },
            "goal_sensitivities": sensitivities,
        }
        for key in ("bounds_role", "original_family_bounds_nm"):
            if key in old_facts:
                summary[key] = copy.deepcopy(old_facts[key])
        new_facts = local_direction_facts(summary, objectives)
        rebuilt_payload_rep = new_rep
        rebuilt_payload_rep["local_direction_facts"] = new_facts
        old_coordinates = [
            {
                "name": x["name"],
                "current_value_nm": x["current_value_nm"],
                "bounds_nm": x["bounds_nm"],
                "derivative_per_nm": x["derivative_per_nm"],
                "feasible_directions": x.get("feasible_directions"),
            }
            for x in variables
        ]
        new_coordinates = [
            {
                "name": x["name"],
                "current_value_nm": x["current_value_nm"],
                "bounds_nm": x["bounds_nm"],
                "derivative_per_nm": x["derivative_per_nm"],
                "feasible_directions": x.get("feasible_directions"),
            }
            for x in new_facts.get("variables", [])
        ]
        details.append({
            "parent_alias": old_rep["parent_alias"],
            "stable_design_id_old": old_rep["design_id"],
            "stable_design_id_new": new_rep["design_id"],
            "stable_identity_unchanged": old_rep["design_id"] == new_rep["design_id"],
            "old_status": old_facts.get("status"),
            "new_status": new_facts.get("status"),
            "old_action_effects_present": all("action_effects" in x for x in variables),
            "new_action_effects_present": all("action_effects" in x for x in new_facts.get("variables", [])),
            "coordinate_values_bounds_derivatives_unchanged": old_coordinates == new_coordinates,
            "old_coordinates": old_coordinates,
            "new_coordinates": new_facts.get("variables", []),
            "new_joint_direction": new_facts.get("joint_direction"),
        })

    rebuilt_payload["decision_guidance"][0] = (
        "Use action_effects for local single-coordinate sign arithmetic; check "
        "feasible_in_recorded_bounds separately. Missing or zero derivatives do "
        "not establish a finite-step effect."
    )
    compact = lambda x: len(json.dumps(x, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    before_bytes = compact(old_payload)
    after_bytes = compact(rebuilt_payload)
    expected_size = offline["T039_action_effects_replay"]["size_measurement"]
    direction = {
        "task_id": "R3-T039",
        "source_head": source_head,
        "request_path": str(REQ_PATH),
        "parent_count": len(details),
        "parent_aliases": [x["parent_alias"] for x in details],
        "coordinate_count": sum(len(x["new_coordinates"]) for x in details),
        "goal_count": len(objectives),
        "all_stable_id_bindings_unchanged": all(x["stable_identity_unchanged"] for x in details),
        "all_coordinate_values_bounds_derivatives_unchanged": all(
            x["coordinate_values_bounds_derivatives_unchanged"] for x in details
        ),
        "all_new_action_effects_present": all(x["new_action_effects_present"] for x in details),
        "three_goal_joint_statuses": {x["parent_alias"]: x["new_joint_direction"]["status"] for x in details},
        "payload_size": {
            "scope": "compact JSON of saved user payload only, ensure_ascii=False separators comma/colon UTF-8",
            "old_bytes_actual": before_bytes,
            "new_bytes_actual": after_bytes,
            "delta_bytes_actual": after_bytes - before_bytes,
            "documented_old_bytes": expected_size["compact_user_payload_bytes_before"],
            "documented_new_bytes": expected_size["compact_user_payload_bytes_after"],
            "documented_delta_bytes": expected_size["delta_bytes"],
            "old_matches_documented": before_bytes == expected_size["compact_user_payload_bytes_before"],
            "new_matches_documented": after_bytes == expected_size["compact_user_payload_bytes_after"],
            "no_model_request": True,
            "no_token_or_cost_measurement": True,
        },
        "parents": details,
        "p002_p008_effects": {
            x["parent_alias"]: [
                {
                    "name": v["name"],
                    "decrease": v["action_effects"]["decrease"],
                    "increase": v["action_effects"]["increase"],
                }
                for v in x["new_coordinates"]
                if x["parent_alias"] in ("P002", "P008")
            ]
            for x in details
            if x["parent_alias"] in ("P002", "P008")
        },
        "limits": [
            "Saved derivatives are uncertified local first-order signs.",
            "Missing/zero derivatives remain unknown; no finite-step outcome is inferred.",
            "Three-goal joint-direction helper is unsupported and not absence proof.",
            "Shared-variable effects are not individual-layer attribution.",
        ],
    }
    dump(DIRECTION / "DIRECTION_AUDIT.json", direction)
    (DIRECTION / "DIRECTION_AUDIT.md").write_text(
        "# T039 direction audit\n\n"
        f"- Saved parents rebuilt: {len(details)}; coordinates: {direction['coordinate_count']}; goals: {len(objectives)}.\n"
        f"- Stable IDs and coordinate values/bounds/derivatives unchanged: {direction['all_stable_id_bindings_unchanged'] and direction['all_coordinate_values_bounds_derivatives_unchanged']}.\n"
        f"- Action effects present after rebuild: {direction['all_new_action_effects_present']}.\n"
        f"- Payload bytes: {before_bytes} -> {after_bytes} (delta {after_bytes - before_bytes}); documented {expected_size['compact_user_payload_bytes_before']} -> {expected_size['compact_user_payload_bytes_after']}.\n"
        "- P002/P008 facts are saved first-order sign arithmetic; three-goal joint direction remains unsupported.\n",
        encoding="utf-8",
    )
    result = {
        "binding_check": binding,
        "direction_check": {
            "parent_count": len(details),
            "coordinate_count": direction["coordinate_count"],
            "stable_ids_unchanged": direction["all_stable_id_bindings_unchanged"],
            "coordinates_unchanged": direction["all_coordinate_values_bounds_derivatives_unchanged"],
            "action_effects_present": direction["all_new_action_effects_present"],
            "payload_old_matches_documented": direction["payload_size"]["old_matches_documented"],
            "payload_new_matches_documented": direction["payload_size"]["new_matches_documented"],
        },
    }
    dump(preflight / "BINDING_AND_DIRECTION_RESULT.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

