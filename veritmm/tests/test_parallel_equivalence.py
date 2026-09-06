"""V-12 scientific fingerprint equivalence: workers=1 vs workers=4.

The same sweep executed serially and in parallel must produce identical
scientific content per child: normalized child task, simulation science
fields, certificate content identity, child logical index.  Explicitly
excluded: run_id, timestamps, output paths, completion order.
"""

from __future__ import annotations

import json
from pathlib import Path

from tmm_engine.execution import ExecutionSettings
from tmm_engine.sweep import SweepExecutionSettings, execute_sweep
from tmm_engine.task_io import load_task

SWEEP_TASK = {
    "schema_version": "sweep-task-v1",
    "mode": "sweep",
    "sweep": {
        "base_simulation": {
            "stack": {
                "name": "parallel-equivalence-sweep",
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
            {
                "path": "/stack/layers/1/thickness_nm",
                "values": [90.0, 103.4, 115.0, 128.0],
            }
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


def _run_sweep(tmp_path: Path, workers: int) -> tuple:
    sweep_path = tmp_path / "sweep.json"
    sweep_path.write_text(json.dumps(SWEEP_TASK), encoding="utf-8")
    _, sweep_payload = load_task(sweep_path)
    settings = SweepExecutionSettings(
        child_execution=ExecutionSettings(write_plot=False, workers=workers),
        workers=workers,
    )
    envelope = execute_sweep(
        sweep_payload,
        tmp_path / f"sweep_w{workers}",
        settings=settings,
    )
    report = json.loads(
        (tmp_path / f"sweep_w{workers}" / "SWEEP_RESULT.json").read_text(encoding="utf-8")
    )
    return envelope, report


def _science_fingerprint(report: dict) -> list:
    """Per-child scientific content, excluding run ids, timestamps, and paths."""

    fingerprints = []
    for child in sorted(report["children"], key=lambda item: int(item["index"])):
        fingerprints.append(
            {
                "index": child["index"],
                "child_task_sha256": child["child_task_sha256"],
                "parameters": child["parameters"],
                "status": child["status"],
                "ok": child["ok"],
                "metrics": child["metrics"],
                "failure_codes": [
                    item.get("code") for item in (child.get("failures") or [])
                ],
            }
        )
    return fingerprints


def test_parallel_sweep_matches_serial_science(tmp_path: Path) -> None:
    envelope_1, report_1 = _run_sweep(tmp_path, workers=1)
    envelope_4, report_4 = _run_sweep(tmp_path, workers=4)

    assert _science_fingerprint(report_1) == _science_fingerprint(report_4)
    assert len(report_1["children"]) == 4
    assert all(child["ok"] for child in report_4["children"])
    assert report_4["status"] == report_1["status"] == "completed"

    # certificate identity: every child carries a certificate whose id is a
    # pure function of its scientific content
    def certificate_ids(root: Path) -> dict:
        ids = {}
        for child_dir in sorted((root / "children").iterdir()):
            certificate = json.loads(
                (child_dir / "PHYSICS_ACCEPTANCE_CERTIFICATE.json").read_text(
                    encoding="utf-8"
                )
            )
            ids[child_dir.name] = certificate["certificate_id"]
        return ids

    assert certificate_ids(tmp_path / "sweep_w1" ) == certificate_ids(tmp_path / "sweep_w4")

    # completion-order independence: the parallel run may collect children in
    # any order; the sorted reports must agree once the excluded fields
    # (run ids, output paths) are dropped
    excluded = {"child_run_id", "artifact_root"}

    def comparable(report: dict) -> list:
        return [
            {key: value for key, value in child.items() if key not in excluded}
            for child in sorted(report["children"], key=lambda item: int(item["index"]))
        ]

    assert comparable(report_4) == comparable(report_1)


def test_failure_isolation_matches_across_modes(tmp_path: Path) -> None:
    broken = json.loads(json.dumps(SWEEP_TASK))
    values = broken["sweep"]["parameters"][0]["values"]
    # one deliberately impossible child: a negative layer thickness that the
    # task validators reject only when the sweep materializes it
    values[1] = -103.4
    sweep_path = tmp_path / "sweep.json"
    sweep_path.write_text(json.dumps(broken), encoding="utf-8")
    _, sweep_payload = load_task(sweep_path)

    reports = {}
    for workers in (1, 4):
        settings = SweepExecutionSettings(
            child_execution=ExecutionSettings(write_plot=False, workers=workers),
            workers=workers,
        )
        execute_sweep(
            sweep_payload,
            tmp_path / f"sweep_w{workers}",
            settings=settings,
        )
        reports[workers] = json.loads(
            (tmp_path / f"sweep_w{workers}" / "SWEEP_RESULT.json").read_text(
                encoding="utf-8"
            )
        )

    for workers in (1, 4):
        children = {
            child["index"]: child for child in reports[workers]["children"]
        }
        bad = children[1]
        assert bad["status"] == "failed"
        assert bad["ok"] is False
        assert bad["failures"]
        for index in (0, 2, 3):
            assert children[index]["ok"] is True
    assert _science_fingerprint(reports[1]) == _science_fingerprint(reports[4])


def test_child_seed_derivation_is_deterministic() -> None:
    from tmm_engine.parallel import derive_child_seed

    assert derive_child_seed(42, 0) == derive_child_seed(42, 0)
    assert derive_child_seed(42, 0) != derive_child_seed(42, 1)
    assert derive_child_seed(42, 1) != derive_child_seed(43, 1)
    assert 0 <= derive_child_seed(7, 3, domain="monte_carlo") < 2**64


def test_worker_env_pins_blas_threads() -> None:
    from tmm_engine.parallel import _THREADS_ENV, force_single_thread_blas

    saved = {name: __import__("os").environ.get(name) for name in _THREADS_ENV}
    try:
        force_single_thread_blas()
        import os

        for name in _THREADS_ENV:
            assert os.environ[name] == "1"
    finally:
        import os

        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
