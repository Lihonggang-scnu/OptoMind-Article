"""SimulationJob runtime: state machine, batch isolation, fingerprint cache.

The job runtime layers a ToolUniverse-style tool fingerprint over the existing
content-addressed cache: a fingerprint combines the capability declaration,
implementation source hashes, and the parameter schema, so any code change
invalidates cached entries.  Cancel is honest about synchronous execution.
"""

from __future__ import annotations

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
from tmm_engine.experiment_store import ExperimentStore
from tmm_engine.job_runtime import (
    JOB_STATES,
    JOB_TRANSITIONS,
    JobRequest,
    JobRuntime,
    JobRuntimeError,
    SimulationJob,
    _module_source_hashes,
    engine_fingerprint,
)


def _dbr(shift: float = 0.0) -> SimulationTask:
    return SimulationTask(
        stack=StackSpec(
            layers=(
                LayerSpec(None, 91.0 + shift, constant_n=2.15),
                LayerSpec(None, 137.0, constant_n=1.43),
            )
            * 2,
            incident=MediumSpec.air(),
            exit=MediumSpec(constant_n=1.52),
        ),
        spectrum=SpectralGrid(500.0, 700.0, 25),
        illumination=IlluminationSpec((0.0,), ("s",)),
    )


def _absorbing_film() -> SimulationTask:
    return SimulationTask(
        stack=StackSpec(
            layers=(LayerSpec(None, 100.0, constant_n=2.1, constant_k=0.4),),
            incident=MediumSpec.air(),
            exit=MediumSpec(constant_n=1.5),
        ),
        spectrum=SpectralGrid(500.0, 700.0, 21),
        illumination=IlluminationSpec((0.0,), ("s",)),
    )


def test_state_machine_rejects_illegal_transitions() -> None:
    job = SimulationJob(
        job_id="job_test",
        config_hash="0" * 64,
        capability_id="0" * 64,
    )
    assert job.status == "queued"
    assert set(JOB_STATES) == {
        "queued",
        "running",
        "verifying",
        "completed",
        "failed",
        "cancelled",
    }

    job.transition("running")
    job.transition("verifying")
    job.transition("completed")
    for illegal in ("running", "queued", "verifying", "failed", "cancelled"):
        with pytest.raises(JobRuntimeError) as excinfo:
            job.transition(illegal)
        assert excinfo.value.code == "invalid_job_transition"

    with pytest.raises(JobRuntimeError) as excinfo:
        SimulationJob(job_id="x", config_hash="", capability_id="").transition(
            "teleport"
        )
    assert excinfo.value.code == "invalid_job_state"

    # queued -> verifying skips the running state and must be rejected
    fresh = SimulationJob(job_id="y", config_hash="", capability_id="")
    with pytest.raises(JobRuntimeError):
        fresh.transition("verifying")


def test_cancel_is_honest_about_synchronous_execution() -> None:
    runtime = JobRuntime()
    job = SimulationJob(job_id="job_done", config_hash="", capability_id="")
    job.transition("running")
    job.transition("completed")
    runtime._jobs["job_done"] = job

    outcome = runtime.cancel("job_done")
    assert outcome["cancelled"] is False
    assert outcome["supported"] is False
    assert "synchronous" in outcome["reason"]

    queued = SimulationJob(job_id="job_queued", config_hash="", capability_id="")
    runtime._jobs["job_queued"] = queued
    assert runtime.cancel("job_queued")["cancelled"] is True
    assert queued.status == "cancelled"
    with pytest.raises(JobRuntimeError):
        queued.transition("running")

    with pytest.raises(JobRuntimeError) as excinfo:
        runtime.status("missing")
    assert excinfo.value.code == "unknown_job"


def test_run_batch_is_isolated_and_matches_individual_execute(tmp_path: Path) -> None:
    store = ExperimentStore(tmp_path / "store")
    runtime = JobRuntime(store=store)
    good_tasks = [_dbr(), _absorbing_film()]
    bad_task = SimulationTask(
        stack=StackSpec(
            layers=(LayerSpec("not_a_real_material", 60.0),),
            incident=MediumSpec.air(),
            exit=MediumSpec(constant_n=1.5),
        ),
        spectrum=SpectralGrid(500.0, 700.0, 11),
        illumination=IlluminationSpec((0.0,), ("s",)),
    )
    report = runtime.run_batch(
        [
            JobRequest("simulate", good_tasks[0], tmp_path / "run0"),
            JobRequest("simulate", bad_task, tmp_path / "run1"),
            JobRequest("simulate", good_tasks[1], tmp_path / "run2"),
        ]
    )
    assert report["batch_size"] == 3
    assert report["completed_count"] == 2
    assert report["failed_count"] == 1
    assert report["entries"][1]["status"] == "failed"
    assert report["entries"][1]["failure_code"]
    for index in (0, 2):
        assert report["entries"][index]["status"] == "completed"
        assert report["entries"][index]["physics_accepted"] is True

    # scientific consistency with a plain workbench simulation
    workbench = TMMWorkbench(MaterialRegistry())
    for entry, task in ((report["entries"][0], good_tasks[0]), (report["entries"][2], good_tasks[1])):
        run_id = runtime._results[entry["job_id"]]["envelope"]["run_id"]
        record = store.get_run(run_id)
        assert record is not None
        simulation_path = Path(record.artifact_root) / "SIMULATION_RESULT.json"
        import json

        simulation = json.loads(simulation_path.read_text(encoding="utf-8"))
        channel = workbench.simulate(task).channel(0.0, "s")
        np.testing.assert_allclose(simulation["channels"]["angle=0|pol=s"]["R"], channel["R"])
        np.testing.assert_allclose(simulation["channels"]["angle=0|pol=s"]["T"], channel["T"])


def test_jobs_are_recorded_in_the_store_lineage(tmp_path: Path) -> None:
    store = ExperimentStore(tmp_path / "store")
    runtime = JobRuntime(store=store)
    job = runtime.submit_job(JobRequest("simulate", _dbr(), tmp_path / "run"))
    run_id = job["outputs"]["run_id"]
    record = store.get_run(run_id)
    assert record is not None
    lineage = store.get_lineage(run_id)
    assert lineage is not None


def test_tampered_fingerprint_forces_a_cache_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ExperimentStore(tmp_path / "store")
    runtime = JobRuntime(store=store)

    first = runtime.submit_job(JobRequest("simulate", _dbr(), tmp_path / "a"))
    assert first["status"] == "completed"

    # second identical submission: fingerprint matches -> content cache hits
    second = runtime.submit_job(JobRequest("simulate", _dbr(), tmp_path / "b"))
    assert second["outputs"]["cache_hit"] is True

    # tamper with the implementation source hash input: the recorded
    # fingerprint no longer matches, so the cache must be treated as a miss
    original = _module_source_hashes
    monkeypatch.setattr(
        "tmm_engine.job_runtime._module_source_hashes",
        lambda: {**original(), "tmm_engine.tmm_solver": "0" * 64},
    )
    assert engine_fingerprint() != first["provenance"]["engine_fingerprint"]
    third = runtime.submit_job(JobRequest("simulate", _dbr(), tmp_path / "c"))
    assert third["outputs"]["cache_hit"] is not True
    assert third["status"] == "completed"


def test_workers_above_one_is_rejected_until_process_parallelism_lands(
    tmp_path: Path,
) -> None:
    runtime = JobRuntime()
    with pytest.raises(JobRuntimeError) as excinfo:
        runtime.run_batch(
            [JobRequest("simulate", _dbr(), tmp_path / "run")], workers=4
        )
    assert excinfo.value.code == "workers_unsupported"


def test_fingerprint_is_stable_and_input_sensitive() -> None:
    assert engine_fingerprint() == engine_fingerprint()
    hashes = _module_source_hashes()
    assert "tmm_engine.tmm_solver" in hashes
    altered = {**hashes, "tmm_engine.tmm_solver": "f" * 64}
    assert altered != hashes
    assert JOB_TRANSITIONS["queued"] == {"running", "cancelled"}
