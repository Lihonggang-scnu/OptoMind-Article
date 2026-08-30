from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from optomind_optics.harness import (
    ActionProposal,
    ActionType,
    DesignCandidate,
    ExperimentGraph,
    ExperimentStatus,
    OpticalExperimentRuntime,
)
from tmm_engine import (
    LayerSpec,
    MediumSpec,
    PhysicsRequirements,
    SimulationTask,
    SpectralGrid,
    StackSpec,
)


def _task() -> SimulationTask:
    return SimulationTask(
        stack=StackSpec(
            layers=(LayerSpec(None, 105.0, constant_n=2.05),),
            incident=MediumSpec.air(),
            exit=MediumSpec(constant_n=1.5),
        ),
        spectrum=SpectralGrid(start_nm=450.0, stop_nm=750.0, points=31),
    )


def test_experiment_graph_preserves_append_only_status_history(tmp_path) -> None:
    graph = ExperimentGraph(tmp_path / "graph.sqlite", "run_a")
    node = graph.create_node(
        "task_hash",
        ActionProposal(action_type=ActionType.run_solver),
    )
    graph.set_status(node, ExperimentStatus.running)
    graph.set_status(node, ExperimentStatus.physically_valid, certificate_id="cert")
    payload = graph.node(node)
    assert payload["status"] == "physically_valid"
    statuses = [
        item["payload"]["status"]
        for item in payload["history"]
        if item["event_type"] == "status"
    ]
    assert statuses == ["proposed", "running", "physically_valid"]


def test_runtime_completes_physics_valid_tmm_task_without_qwen(tmp_path) -> None:
    runtime = OpticalExperimentRuntime(tmp_path / "run", run_id="deterministic_a")
    result = runtime.run_simulation(_task())
    assert result["status"] == "physically_valid"
    assert (tmp_path / "run" / "FINAL_RESULT.json").exists()
    cert = json.loads(
        next((tmp_path / "run" / "nodes").glob("*/PHYSICS_ACCEPTANCE_CERTIFICATE.json")).read_text(
            encoding="utf-8"
        )
    )
    assert cert["accepted"]
    assert cert["independent_solver_check"]["status"] == "passed"
    task_payload = json.loads((tmp_path / "run" / "TASK.json").read_text(encoding="utf-8"))
    assert task_payload["mode"] == "simulate"
    assert (tmp_path / "run" / "RUN_STATE.json").exists()
    assert next((tmp_path / "run" / "nodes").glob("*/ACTION.json")).exists()


def test_runtime_routes_out_of_domain_task_instead_of_running_tmm(tmp_path) -> None:
    task = replace(_task(), physics=PhysicsRequirements(geometry_class="lateral_periodic"))
    result = OpticalExperimentRuntime(tmp_path / "route", run_id="route_a").run_simulation(task)
    assert result["status"] == "needs_higher_fidelity"
    failures = result["capability_assessment"]["failures"]
    assert failures[0]["suggested_solver_family"] == "rcwa"
    assert not list((tmp_path / "route" / "nodes").glob("*/SIMULATION_RESULT.json"))


def test_normalized_task_replays_in_fresh_python_process(tmp_path) -> None:
    first = tmp_path / "first"
    OpticalExperimentRuntime(first, run_id="first_process").run_simulation(_task())
    replay = tmp_path / "replay"
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_optical_experiment.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--task",
            str(first / "TASK.json"),
            "--output-dir",
            str(replay),
            "--run-id",
            "fresh_process",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    first_result = json.loads(next((first / "nodes").glob("*/SIMULATION_RESULT.json")).read_text(encoding="utf-8"))
    replay_result = json.loads(next((replay / "nodes").glob("*/SIMULATION_RESULT.json")).read_text(encoding="utf-8"))
    assert first_result["channels"] == replay_result["channels"]


def test_runtime_persists_multi_candidate_soft_objective_portfolio(tmp_path) -> None:
    runtime = OpticalExperimentRuntime(tmp_path / "portfolio", run_id="portfolio_a")
    candidates = [
        DesignCandidate(
            candidate_id="best_score",
            physics_status="physically_valid",
            target_attainment={"R": {"constraint": "at_most", "target": 0.01, "observed": 0.012}},
            robustness_score=0.5,
            simplicity_score=0.4,
        ),
        DesignCandidate(
            candidate_id="robust",
            physics_status="physically_valid",
            target_attainment={"R": {"constraint": "at_most", "target": 0.01, "observed": 0.03}},
            robustness_score=0.95,
            simplicity_score=0.7,
        ),
    ]
    payload = runtime.build_portfolio(candidates)
    assert payload["selected_roles"]["best_target_score"] == "best_score"
    assert payload["selected_roles"]["most_robust"] == "robust"
    assert (tmp_path / "portfolio" / "DESIGN_PORTFOLIO.json").exists()
