"""T-02 tests: ExperimentStore run-namespace directory conventions."""

from __future__ import annotations

from optomind_optics.harness import experiment_store
from optomind_optics.harness.experiment_store import ExperimentStore


def test_round_dir_format():
    store = ExperimentStore("p1", "r1")
    assert store.round_dir(2, "route_A").as_posix() == "runs/p1/r1/round_2/route_A"


def test_artifact_path():
    store = ExperimentStore("p1", "r1")
    path = store.artifact_path(1, "route_A", "RUN_RESULT.json")
    assert path.name == "RUN_RESULT.json"
    assert path.as_posix() == "runs/p1/r1/round_1/route_A/RUN_RESULT.json"


def test_ensure_round_dir_creates(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment_store, "BASE_DIR", tmp_path)
    store = ExperimentStore("p1", "r1")
    created = store.ensure_round_dir(2, "route_A")
    assert created.is_dir()
    assert created == tmp_path / "p1" / "r1" / "round_2" / "route_A"
    # Repeat calls are idempotent and return the same location.
    assert store.ensure_round_dir(2, "route_A") == created


def test_global_artifact():
    store = ExperimentStore("p1", "r1")
    assert (
        store.global_artifact("run_manifest.json").as_posix()
        == "runs/p1/r1/run_manifest.json"
    )
