"""Provenance graph v1: read-only projection, evidence chains, DAG invariant.

The graph is a projection, never a second source of truth: building it must
leave every run directory byte-identical, its evidence chains must point at
real files with matching hashes, and the data-provenance subgraph must be a
DAG.  Integrity follows the existing verify-run criterion.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from tmm_engine import (
    IlluminationSpec,
    LayerSpec,
    MaterialRegistry,
    MediumSpec,
    SimulationTask,
    SpectralGrid,
    StackSpec,
    TMMWorkbench,
)
from tmm_engine.execution import ExecutionSettings
from tmm_engine.managed_execution import execute_managed_task
from tmm_engine.provenance_graph import (
    ProvenanceGraph,
    build_graph,
)

SWEEP_TASK = {
    "schema_version": "sweep-task-v1",
    "mode": "sweep",
    "sweep": {
        "base_simulation": {
            "stack": {
                "name": "provenance-sweep",
                "incident": {"constant_n": 1.0},
                "exit": {"constant_n": 1.52},
                "layers": [
                    {"constant_n": 2.25, "thickness_nm": 66.7},
                    {"constant_n": 1.45, "thickness_nm": 103.4},
                    {"constant_n": 2.25, "thickness_nm": 66.7},
                ],
            },
            "spectrum": {"start_nm": 550.0, "stop_nm": 650.0, "points": 21},
            "illumination": {"angles_deg": [0.0], "polarizations": ["unpolarized"]},
            "solver": "smatrix",
            "requested_outputs": ["R", "T", "A"],
        },
        "parameters": [
            {"path": "/stack/layers/1/thickness_nm", "values": [95.0, 103.4]}
        ],
        "metrics": [
            {
                "name": "peak_R",
                "observable": "R",
                "wavelength_min_nm": 550.0,
                "wavelength_max_nm": 650.0,
                "aggregation": "max",
            }
        ],
    },
}


def _run_task(task: SimulationTask, output_dir: Path) -> dict:
    return execute_managed_task(
        "simulate", task, output_dir, execution_settings=ExecutionSettings()
    )


def _two_runs(tmp_path: Path) -> tuple:
    # one baseline and one strictly-better variant (higher mean R over the band)
    baseline = SimulationTask(
        stack=StackSpec(
            layers=(
                LayerSpec(None, 91.0, constant_n=2.15),
                LayerSpec(None, 137.0, constant_n=1.43),
            )
            * 2,
            incident=MediumSpec.air(),
            exit=MediumSpec(constant_n=1.52),
        ),
        spectrum=SpectralGrid(500.0, 700.0, 25),
        illumination=IlluminationSpec((0.0,), ("s",)),
    )
    improved = SimulationTask(
        stack=StackSpec(
            layers=(
                LayerSpec(None, 70.0, constant_n=2.15),
                LayerSpec(None, 105.0, constant_n=1.43),
            )
            * 2,
            incident=MediumSpec.air(),
            exit=MediumSpec(constant_n=1.52),
        ),
        spectrum=SpectralGrid(500.0, 700.0, 25),
        illumination=IlluminationSpec((0.0,), ("s",)),
    )
    envelope_a = _run_task(baseline, tmp_path / "run_a")
    envelope_b = _run_task(improved, tmp_path / "run_b")
    return envelope_a, envelope_b, baseline, improved


def _directory_digest(path: Path) -> str:
    digest = hashlib.sha256()
    for file in sorted(p for p in path.rglob("*") if p.is_file()):
        digest.update(str(file.relative_to(path)).encode("utf-8"))
        digest.update(file.read_bytes())
    return digest.hexdigest()


def test_graph_projection_is_complete_readonly_and_idempotent(tmp_path: Path) -> None:
    _two_runs(tmp_path)
    run_a, run_b = tmp_path / "run_a", tmp_path / "run_b"

    digests_before = {str(p): _directory_digest(p) for p in (run_a, run_b)}
    graph = build_graph([run_a, run_b])
    digests_after = {str(p): _directory_digest(p) for p in (run_a, run_b)}
    assert digests_before == digests_after  # read-only projection

    node_types = {node.node_type for node in graph.nodes.values()}
    assert node_types == {"data", "calculation", "decision"}
    edge_types = {edge.edge_type for edge in graph.edges}
    assert edge_types == {"input", "created_by", "decided_by"}
    assert graph.data_subgraph_is_dag() is True

    # idempotent rebuild: canonical bytes are identical
    graph_again = build_graph([run_a, run_b])
    assert graph_again.canonical_bytes() == graph.canonical_bytes()

    # save/load round trip preserves the canonical form
    saved = tmp_path / "graph.json"
    graph.save(saved)
    loaded = ProvenanceGraph.load(saved)
    assert loaded.canonical_bytes() == graph.canonical_bytes()


def test_workflow_nodes_from_a_sweep_container(tmp_path: Path) -> None:
    from tmm_engine.task_io import load_task

    sweep_path = tmp_path / "sweep.json"
    sweep_path.write_text(json.dumps(SWEEP_TASK), encoding="utf-8")
    _, sweep_payload = load_task(sweep_path)
    execute_managed_task(
        "sweep",
        sweep_payload,
        tmp_path / "sweep_run",
        execution_settings=ExecutionSettings(),
    )
    graph = build_graph([tmp_path / "sweep_run"])

    node_types = {node.node_type for node in graph.nodes.values()}
    assert "workflow" in node_types
    edge_types = {edge.edge_type for edge in graph.edges}
    assert {"part_of", "returned_by"} <= edge_types
    assert graph.data_subgraph_is_dag() is True


def test_explain_superiority_chain_points_at_verified_evidence(
    tmp_path: Path,
) -> None:
    envelope_a, envelope_b, baseline, improved = _two_runs(tmp_path)
    run_a, run_b = tmp_path / "run_a", tmp_path / "run_b"
    graph = build_graph([run_a, run_b])
    workbench = TMMWorkbench(MaterialRegistry())

    explanation = graph.explain_superiority(
        envelope_a["run_id"], envelope_b["run_id"]
    )
    verdict = explanation["verdict"]
    mean_baseline = float(
        np.mean(
            np.asarray(
                workbench.simulate(baseline).channel(0.0, "s")["R"], dtype=float
            )
        )
    )
    mean_improved = float(
        np.mean(
            np.asarray(
                workbench.simulate(improved).channel(0.0, "s")["R"], dtype=float
            )
        )
    )
    assert verdict["value_b"] == pytest.approx(mean_improved, abs=1e-9)
    assert verdict["value_a"] == pytest.approx(mean_baseline, abs=1e-9)
    assert mean_improved > mean_baseline
    assert verdict["superior_run"] == envelope_b["run_id"]

    for label, run_dir in (("run_a", run_a), ("run_b", run_b)):
        steps = explanation["chains"][label]
        kinds = [step["node_kind"] for step in steps]
        # the chain spans task -> run -> metrics -> certificate -> decision;
        # within that, ordering follows node hashes, not semantic position
        assert set(kinds) == {
            "task",
            "engine_run",
            "spectrum",
            "certificate",
            "metric_snapshot",
            "acceptance_decision",
        }
        assert kinds[0] == "task"
        assert kinds[1] == "engine_run"
        assert kinds[-1] == "acceptance_decision"
        for step in steps:
            evidence = step["evidence"]
            assert evidence is not None and evidence["exists"] is True
            digest = hashlib.sha256()
            digest.update(Path(evidence["path"]).read_bytes())
            assert digest.hexdigest() == evidence["sha256"]


def test_tampered_artifact_is_caught_on_rebuild(tmp_path: Path) -> None:
    _two_runs(tmp_path)
    run_a, run_b = tmp_path / "run_a", tmp_path / "run_b"
    graph = build_graph([run_a, run_b])
    assert graph.run_reports[str(run_a.resolve())]["integrity_status"] == "valid"

    spectra = run_a / "SPECTRA.csv"
    original = spectra.read_bytes()
    spectra.write_bytes(original + b"# tampered\n")

    tampered_graph = build_graph([run_a, run_b])
    assert (
        tampered_graph.run_reports[str(run_a.resolve())]["integrity_status"]
        == "invalid"
    )
    assert (
        tampered_graph.run_reports[str(run_b.resolve())]["integrity_status"]
        == "valid"
    )
