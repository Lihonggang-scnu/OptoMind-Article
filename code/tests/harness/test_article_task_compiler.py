"""T-06 tests: task_compiler turbo routing, Charter diff, sha256, store paths."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

import config.qwen_config as qwen_config
from optomind_optics.harness import problem_analyzer as pmod
from optomind_optics.harness import task_compiler as tcmod
from optomind_optics.harness.dev_fixtures import build_dev_optical_design_task
from optomind_optics.harness.design_task import HarnessBudgetPolicy, UncertaintyPolicy
from optomind_optics.harness.experiment_store import ExperimentStore
from optomind_optics.harness.task_compiler import (
    CompileFailure,
    QwenTMMTaskCompiler,
    _check_task_spec_charter_drift,
    build_veritmm_task_spec,
)
from optomind_optics.harness.research_charter import build_research_charter

FULL_CHARTER: dict[str, Any] = {
    "wavelength_range_nm": [450.0, 800.0],
    "angle_range_deg": [0.0, 30.0],
    "polarization": "unpolarized",
    "objectives": [{"type": "max_reflectivity"}],
    "material_whitelist": ["SiO2", "TiO2"],
    # DEV01 compiles a single-layer coating, so the lower bound admits 1.
    "layer_count_bounds": {"min": 1, "max": 8},
}


def _spec(layers: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    route: dict[str, Any] = {
        "route_id": "route_A",
        "task": {
            "experiments": [
                {
                    "tmm_task": {
                        "simulation": {"stack": {"layers": layers}},
                        "targets": [
                            {
                                "observable": "R",
                                "wavelength_min_nm": 500.0,
                                "wavelength_max_nm": 600.0,
                            }
                        ],
                    }
                }
            ]
        },
    }
    route.update(extra)
    return route


class _ScriptedTurboClient:
    model_name = "qwen3.7-flash"

    def __init__(self, responses: list[dict[str, Any]]):
        self.responses = list(responses)
        self.calls = 0

    def call(self, messages, *, max_tokens: int = 4000, force_mock=None):
        self.calls += 1
        return self.responses.pop(0)


def _formal01_fixture() -> dict[str, Any]:
    fixture = Path(__file__).parents[1] / "fixtures" / "tmm_harness" / "formal01_controls.json"
    return json.loads(fixture.read_text(encoding="utf-8"))


def _formal01_request_and_draft() -> tuple[str, dict[str, Any], str]:
    fixture = _formal01_fixture()
    prompt_question = (
        str(fixture["route_execution_request_english"])
        + "\n\nAllow at most 20000 solver evaluations per round. Use seed 99."
        + "\n\nImmutable original user request (overrides conflicting route proposals):\n"
        + str(fixture["original_request"])
        + "\nPreserve its physical constraints. Performance targets remain soft objectives."
    )
    return prompt_question, dict(fixture["compiler_draft"]), str(fixture["original_request"])


def test_formal01_original_cap_and_seed_bind_before_charter_check():
    question, draft, original_request = _formal01_request_and_draft()
    result = QwenTMMTaskCompiler(
        client=_ScriptedTurboClient([{"content": json.dumps(draft)}])
    ).compile(question)

    assert result.status == "compiled", result.validation_errors
    assert result.task is not None
    assert result.task.budget.maximum_forward_evaluations == 64
    assert result.task.uncertainty.random_seed == 17
    expected_optimizer = draft["experiments"][0]["tmm_task"]["optimizer"]
    actual_optimizer = result.task.experiments[0].tmm_task["optimizer"]
    assert {
        key: value for key, value in actual_optimizer.items() if key != "seed"
    } == {key: value for key, value in expected_optimizer.items() if key != "seed"}
    assert all(
        experiment.tmm_task["optimizer"]["seed"] == 17
        for experiment in result.task.experiments
        if experiment.mode.value == "optimize"
    )
    assert result.task.metadata["original_request_control_bindings"] == [
        "maximum_forward_evaluations bound to explicit original request cap 64",
        "optimizer seed bound to explicit original request seed 17",
        "uncertainty random_seed bound to explicit original request seed 17",
    ]
    charter = build_research_charter(
        original_request,
        {},
    )
    _check_task_spec_charter_drift(
        {"task": result.task.model_dump(mode="json")}, charter
    )


def _compiled_response(total_tokens: int | None = None) -> dict[str, Any]:
    source = build_dev_optical_design_task("DEV01")
    payload = {
        "status": "compiled",
        "rationale": "A planar single-layer coating is supported by TMM.",
        "normalized_request_english": source.normalized_request_english,
        "experiments": [item.model_dump(mode="json") for item in source.experiments],
        "uncertainty": source.uncertainty.model_dump(mode="json"),
    }
    usage: dict[str, Any] = {"model_name": "qwen3.7-flash"}
    if total_tokens is not None:
        usage["total_tokens"] = total_tokens
    return {"content": json.dumps(payload), "_llm_usage": usage}


def test_task_compiler_uses_turbo_model(monkeypatch):
    captured: dict = {}

    def fake_get_qwen_client(role):
        captured["role"] = role
        return object()

    monkeypatch.setattr(pmod, "get_qwen_client", fake_get_qwen_client)
    compiler = QwenTMMTaskCompiler()  # default construction must route turbo
    assert captured["role"] == "turbo"
    assert compiler._model_label == "qwen3.7-flash"


def test_charter_drift_raises_material():
    drifting = _spec([{"material": "SiO2"}, {"material": "Au"}])
    with pytest.raises(CompileFailure, match="CHARTER_DRIFT_ERROR"):
        _check_task_spec_charter_drift(drifting, FULL_CHARTER)


def test_charter_drift_raises_layer_count():
    drifting = _spec([{"material": "SiO2"} for _ in range(12)])
    with pytest.raises(CompileFailure, match="CHARTER_DRIFT_ERROR"):
        _check_task_spec_charter_drift(drifting, FULL_CHARTER)
    legacy_charter = dict(FULL_CHARTER)
    legacy_charter.pop("layer_count_bounds")
    legacy_charter["layer_count_hard_bounds"] = {"min": 1, "max": 8}
    with pytest.raises(CompileFailure, match="CHARTER_DRIFT_ERROR"):
        _check_task_spec_charter_drift(_spec([{"material": "SiO2"} for _ in range(9)]), legacy_charter)


def test_valid_task_spec_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        __import__("optomind_optics.harness.experiment_store", fromlist=["BASE_DIR"]),
        "BASE_DIR",
        tmp_path,
    )
    result = QwenTMMTaskCompiler(client=_ScriptedTurboClient([_compiled_response()])).compile(
        "Design a single-layer antireflection coating on glass over 500-600 nm."
    )
    assert result.status == "compiled"
    assert result.task is not None
    assert result.task.budget.maximum_forward_evaluations == HarnessBudgetPolicy().maximum_forward_evaluations
    assert result.task.uncertainty.random_seed == UncertaintyPolicy().random_seed
    spec = build_veritmm_task_spec(
        result,
        route_id="route_A",
        round_k=2,
        experiment_store=ExperimentStore("p1", "r1"),
        charter=FULL_CHARTER,
    )
    assert re.fullmatch(r"[0-9a-f]{64}", spec["task_sha256"])
    assert spec["output_dir"]
    # unit-level compliant spec also clears the drift gate directly
    ok_spec = _spec([{"material": "SiO2"}, {"material": "TiO2"}])
    _check_task_spec_charter_drift(ok_spec, FULL_CHARTER)  # must not raise


def test_task_sha256_deterministic(tmp_path, monkeypatch):
    monkeypatch.setattr(
        __import__("optomind_optics.harness.experiment_store", fromlist=["BASE_DIR"]),
        "BASE_DIR",
        tmp_path,
    )
    result = QwenTMMTaskCompiler(client=_ScriptedTurboClient([_compiled_response()])).compile(
        "Design a single-layer antireflection coating on glass over 500-600 nm."
    )
    kwargs = dict(route_id="route_A", round_k=2, experiment_store=ExperimentStore("p1", "r1"))
    first = build_veritmm_task_spec(result, **kwargs)
    second = build_veritmm_task_spec(result, **kwargs)
    assert first["task_sha256"] == second["task_sha256"]


def test_output_dir_via_experiment_store(tmp_path, monkeypatch):
    monkeypatch.setattr(
        __import__("optomind_optics.harness.experiment_store", fromlist=["BASE_DIR"]),
        "BASE_DIR",
        tmp_path,
    )
    result = QwenTMMTaskCompiler(client=_ScriptedTurboClient([_compiled_response()])).compile(
        "Design a single-layer antireflection coating on glass over 500-600 nm."
    )
    spec = build_veritmm_task_spec(
        result,
        route_id="route_A",
        round_k=2,
        experiment_store=ExperimentStore("p1", "r1"),
    )
    expected = tmp_path / "p1" / "r1" / "round_2" / "route_A"
    assert Path(spec["output_dir"]) == expected
    assert expected.is_dir()


def test_no_hardcoded_runs_path(tmp_path, monkeypatch):
    monkeypatch.setattr(
        __import__("optomind_optics.harness.experiment_store", fromlist=["BASE_DIR"]),
        "BASE_DIR",
        tmp_path,
    )
    result = QwenTMMTaskCompiler(client=_ScriptedTurboClient([_compiled_response()])).compile(
        "Design a single-layer antireflection coating on glass over 500-600 nm."
    )
    spec = build_veritmm_task_spec(
        result,
        route_id="route_A",
        round_k=2,
        experiment_store=ExperimentStore("p1", "r1"),
    )
    parts = Path(spec["output_dir"]).parts
    assert "runs" not in parts
    assert not str(spec["output_dir"]).replace("\\", "/").startswith("runs/")


def test_cost_recorded(monkeypatch):
    fresh_tracker = qwen_config.CostTracker()
    monkeypatch.setattr(qwen_config, "_COST_TRACKER", fresh_tracker)
    result = QwenTMMTaskCompiler(
        client=_ScriptedTurboClient([_compiled_response(total_tokens=77)])
    ).compile("Design a single-layer antireflection coating on glass over 500-600 nm.")
    assert result.status == "compiled"
    assert fresh_tracker.get_budget_snapshot().qwen_tokens.get("turbo") == 77
