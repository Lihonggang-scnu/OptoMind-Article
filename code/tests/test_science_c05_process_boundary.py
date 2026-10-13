from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
import os
import pytest
from optomind_eval.process_boundary import EvaluatorProcess, export_policy_capsule, validate_request
from optomind_eval.worlds import make_world
from optomind_optics.harness.science.world_client import WorldClient


def test_actual_process_purchase_and_sealed_terminal(tmp_path):
    world = make_world("F0", task_id="t", split="V", seed=4)
    config = tmp_path / "worlds.json"
    config.write_text(json.dumps({"worlds": [asdict(world)], "budget": 2.}))
    with EvaluatorProcess(config) as process:
        assert process.handshake["pid"] != os.getpid()
        client = WorldClient(process.request)
        first = client.purchase("t", "one", "R", [500., 600.])
        assert first == client.purchase("t", "one", "R", [500., 600.])
        second = client.purchase("t", "two", "R", [500., 600.])
        assert first.values != second.values
        assert first.calibration_group == second.calibration_group
        assert "lineage:" not in first.calibration_group
        assert first.calibration_group == process.handshake["tasks"][0]["group_id"] + ":instrument"
        assert "seed" not in process.handshake["tasks"][0]
        with pytest.raises(PermissionError):
            client.purchase("t", "three", "R", [500., 600.])
        with pytest.raises(PermissionError):
            client.purchase("t", "absent", "A", [500., 600.])
        sealed = client.submit_terminal("t", "s", {"film_n": 2., "thickness_nm": 120.})
        assert set(sealed) == {"submission_id", "status", "sealed_digest"}
        assert sealed == client.submit_terminal("t", "s", {"film_n": 2., "thickness_nm": 120.})
        with pytest.raises(PermissionError):
            process.request({"operation": "export_worlds", "task_id": "t"})


def test_fail_closed_schema_and_package_export(tmp_path):
    for value in (float("nan"), float("inf"), True):
        with pytest.raises(ValueError):
            validate_request({"operation": "purchase", "task_id": "t", "request_id": "r",
                              "channel": "R", "wavelengths_nm": [500., value]})
    with pytest.raises(ValueError):
        validate_request({"operation": "terminal", "task_id": "t", "submission_id": "s",
                          "selected_design": {"film_n": 2., "thickness_nm": -1.}})
    manifest = export_policy_capsule(tmp_path / "capsule")
    assert set(manifest["files"]) == {"world_client.py"}
    assert manifest["os_isolation"] == "not established"


def test_three_process_deployment_probe_honest_host_boundary(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/audit_science_c05_deployment.py"
    spec = importlib.util.spec_from_file_location("probe_c05", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Scientific diagnostics tested separately; retain real process protocol here.
    module.write_sealed_audit = lambda *a: None
    result = module.run_probe(tmp_path / "probe")
    assert result["distinct_processes"]
    assert result["evaluator_import_denied"]
    assert result["idempotent"] and result["unavailable_channel_denied"]
    assert not result["os_security_isolation_established"]
    assert result["terminal_fields"] == ["sealed_digest", "status", "submission_id"]

def test_process_rejects_cross_split_near_duplicate_before_purchases(tmp_path):
    from dataclasses import replace
    a = make_world("F0", task_id="a", split="T", seed=1)
    b = replace(a, task=replace(a.task, task_id="b", split="V", group_id="different"),
                seed=9, provenance=(), thickness_nm=a.thickness_nm + .05)
    config = tmp_path / "worlds.json"
    config.write_text(json.dumps({"worlds": [asdict(a), asdict(b)], "budget": 2.}))
    with pytest.raises(RuntimeError, match="startup failed"):
        EvaluatorProcess(config)
