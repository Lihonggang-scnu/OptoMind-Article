from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from optomind_optics.harness import replay as replay_module
from optomind_optics.harness.dev_fixtures import build_dev_optical_design_task
from optomind_optics.harness.orchestrator import TMMHarnessConfig, TMMHarnessOrchestrator
from optomind_optics.harness.provenance import ArtifactTamperedError
from optomind_optics.harness.replay import (
    _load_candidate_identity,
    _load_experiment_path_index,
    _logical_scientific_paths,
    _run_locked_interpreter_replay,
    _scientific_digest,
    reassess_existing_replay,
    replay_completed_run,
)


def _source_run(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    result = TMMHarnessOrchestrator(
        source,
        run_id="fresh_source",
        config=TMMHarnessConfig(enable_global_optimizer=False),
    ).run(build_dev_optical_design_task("DEV02"))
    assert result.status == "completed"
    return source


def test_completed_run_replays_in_a_fresh_python_process(tmp_path: Path) -> None:
    source = _source_run(tmp_path)
    script = Path(__file__).resolve().parents[1] / "scripts" / "replay_tmm_harness_run.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--source-run",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    manifest = json.loads((source / "REPLAY_MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["success"] is True
    assert manifest["matched_artifacts"] == manifest["total_artifacts"]
    lineage = json.loads((source / "ARTIFACT_MANIFEST.json").read_text(encoding="utf-8"))
    assert any(
        item["artifact_id"] == "REPLAY_MANIFEST.json"
        for item in lineage["artifacts"]
    )


def test_replay_rejects_tampered_source_before_recomputation(tmp_path: Path) -> None:
    source = _source_run(tmp_path)
    simulation = next((source / "experiments").rglob("SIMULATION_RESULT.json"))
    simulation.write_text("{}", encoding="utf-8")

    with pytest.raises(ArtifactTamperedError):
        replay_completed_run(source)
    assert not (source / "fresh_replay").exists()


def test_replay_ignores_expected_qwen_enabled_difference_and_can_be_reassessed(
    tmp_path: Path,
) -> None:
    source = tmp_path / "qwen_enabled_source"
    result = TMMHarnessOrchestrator(
        source,
        run_id="qwen_enabled_source",
        config=TMMHarnessConfig(
            enable_global_optimizer=False,
            use_qwen_policy=True,
            qwen_force_mock=True,
        ),
    ).run(build_dev_optical_design_task("DEV02"))
    assert result.status == "completed"

    manifest = replay_completed_run(source)
    assert manifest.success is True
    runtime = next(item for item in manifest.checks if item.relative_path == "RUNTIME_LOCK.json")
    assert runtime.matched is True

    reassessed = reassess_existing_replay(source)
    assert reassessed.success is True
    assert reassessed.matched_artifacts == reassessed.total_artifacts
    lineage = json.loads((source / "ARTIFACT_MANIFEST.json").read_text(encoding="utf-8"))
    assert any(
        item["artifact_id"] == "REPLAY_REASSESSMENT.json"
        for item in lineage["artifacts"]
    )


def test_replay_reconciliation_across_reversible_layouts(tmp_path: Path) -> None:
    """Mixed source/replay reversible layouts reconcile by logical identity.

    The source uses the legacy ``experiments/<id>/baseline/...`` layout while
    the replay uses the compact ``x/e_<sha>/b/...`` layout.  Reconciliation
    keys on the logical experiment id plus normalized segment names, so
    logically identical artifacts match while tampered or missing replay
    artifacts are still rejected.
    """

    import hashlib as _hashlib

    from optomind_optics.harness.replay import (
        _load_experiment_path_index,
        _logical_scientific_paths,
        _scientific_digest,
    )

    def exp_sha(experiment_id: str) -> str:
        return _hashlib.sha256(experiment_id.encode("utf-8")).hexdigest()[:12]

    source = tmp_path / "legacy_source"
    replay = tmp_path / "compact_replay"
    experiment_id = "dev02_forward_dbr"
    source_physical = f"experiments/{experiment_id}"
    compact_physical = f"x/e_{exp_sha(experiment_id)}"

    content = {
        "SIMULATION_RESULT.json": {"status": "ok", "R_mean": 0.01},
        "PHYSICS_ACCEPTANCE_CERTIFICATE.json": {"accepted": True},
        "OBJECTIVE_REPORT.json": {"score": 0.8},
    }
    portfolio = {
        "experiment_id": experiment_id,
        "portfolio_artifact_id": "<PHYSICAL>/DESIGN_PORTFOLIO.json",
        "candidates": [
            {
                "candidate_id": "c1",
                "artifact_ids": [
                    "<PHYSICAL>/baseline/PHYSICS_ACCEPTANCE_CERTIFICATE.json",
                    "<PHYSICAL>/baseline/OBJECTIVE_REPORT.json",
                ],
            }
        ],
    }
    portfolios = {"experiments": [portfolio]}

    def write_tree(root: Path, physical: str, subdir: str) -> None:
        experiment_dir = root / physical
        baseline = experiment_dir / subdir
        baseline.mkdir(parents=True, exist_ok=True)
        for name, payload in content.items():
            (baseline / name).write_text(
                json.dumps(payload),
                encoding="utf-8",
            )
        (experiment_dir / "DESIGN_PORTFOLIO.json").write_text(
            json.dumps(portfolio).replace("<PHYSICAL>", physical),
            encoding="utf-8",
        )
        (root / "DESIGN_PORTFOLIOS.json").write_text(
            json.dumps(portfolios).replace("<PHYSICAL>", physical),
            encoding="utf-8",
        )
        (root / "RUNTIME_LOCK.json").write_text(
            json.dumps({"use_qwen_policy": True}),
            encoding="utf-8",
        )
        (root / "ARTIFACT_PATH_INDEX.json").write_text(
            json.dumps(
                {
                    "schema_version": "tmm-artifact-path-index.v1",
                    "experiments": [
                        {
                            "experiment_id": experiment_id,
                            "physical_directory": physical,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    write_tree(source, source_physical, "baseline")
    write_tree(replay, compact_physical, "b")

    source_index = _load_experiment_path_index(source)
    replay_index = _load_experiment_path_index(replay)
    source_logical = _logical_scientific_paths(source, source_index)
    replay_logical = _logical_scientific_paths(replay, replay_index)
    assert set(source_logical) == set(replay_logical)
    assert any(
        key.endswith(":baseline/SIMULATION_RESULT.json")
        for key in source_logical
    )
    for key in source_logical:
        src_digest = _scientific_digest(
            source_logical[key][1],
            root=source,
            path_index=source_index,
        )
        rep_digest = _scientific_digest(
            replay_logical[key][1],
            root=replay,
            path_index=replay_index,
        )
        assert src_digest == rep_digest, key

    tampered = replay / compact_physical / "b" / "SIMULATION_RESULT.json"
    tampered.write_text(json.dumps({"status": "tampered"}), encoding="utf-8")
    assert (
        _scientific_digest(tampered, root=replay, path_index=replay_index)
        != _scientific_digest(
            source / source_physical / "baseline" / "SIMULATION_RESULT.json",
            root=source,
            path_index=source_index,
        )
    )

    (replay / compact_physical / "b" / "PHYSICS_ACCEPTANCE_CERTIFICATE.json").unlink()
    replay_logical = _logical_scientific_paths(replay, replay_index)
    missing = [
        key
        for key in source_logical
        if key not in replay_logical
        and "PHYSICS_ACCEPTANCE_CERTIFICATE" in key
    ]
    assert missing


def test_replay_path_index_conflicts_rejected(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir()

    def write_index(rows: list[dict]) -> None:
        (root / "ARTIFACT_PATH_INDEX.json").write_text(
            json.dumps(
                {
                    "schema_version": "tmm-artifact-path-index.v1",
                    "experiments": rows,
                }
            ),
            encoding="utf-8",
        )

    cases = [
        (
            [
                {
                    "experiment_id": "dev02_forward_dbr",
                    "physical_directory": "experiments/dev02_forward_dbr",
                },
                {
                    "experiment_id": "dev02_forward_dbr",
                    "physical_directory": "x/e_63aa074298a2",
                },
            ],
            "conflicting physical",
        ),
        (
            [
                {
                    "experiment_id": "exp_a",
                    "physical_directory": "x/e_63aa074298a2",
                },
                {
                    "experiment_id": "exp_b",
                    "physical_directory": "x/e_63aa074298a2",
                },
            ],
            "conflicting experiment",
        ),
        (
            [
                {
                    "experiment_id": "exp_a",
                    "physical_directory": "../escape",
                }
            ],
            "under the run root",
        ),
        (
            [
                {
                    "experiment_id": "exp_a",
                    "physical_directory": "C:/absolute",
                }
            ],
            "relative",
        ),
        (
            [
                {
                    "experiment_id": "exp_a",
                    "physical_directory": "/absolute",
                }
            ],
            "relative",
        ),
    ]
    for rows, marker in cases:
        write_index(rows)
        with pytest.raises(ValueError, match=marker):
            _load_experiment_path_index(root)
    (root / "ARTIFACT_PATH_INDEX.json").write_text(
        "{not json",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="malformed"):
        _load_experiment_path_index(root)


def test_replay_reconciliation_rejects_duplicate_logical_artifact(
    tmp_path: Path,
) -> None:
    import hashlib as _hashlib

    root = tmp_path / "duplicate_run"
    experiment_id = "dev02_forward_dbr"
    sha = _hashlib.sha256(experiment_id.encode("utf-8")).hexdigest()[:12]
    compact = f"x/e_{sha}"
    (root / "experiments" / experiment_id / "baseline").mkdir(
        parents=True,
        exist_ok=True,
    )
    (root / compact / "b").mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"status": "ok"})
    (root / "experiments" / experiment_id / "baseline" / "SIMULATION_RESULT.json").write_text(
        payload,
        encoding="utf-8",
    )
    (root / compact / "b" / "SIMULATION_RESULT.json").write_text(
        payload,
        encoding="utf-8",
    )
    (root / "ARTIFACT_PATH_INDEX.json").write_text(
        json.dumps(
            {
                "experiments": [
                    {
                        "experiment_id": experiment_id,
                        "physical_directory": compact,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    index = _load_experiment_path_index(root)
    with pytest.raises(ValueError, match="duplicate logical scientific artifact"):
        _logical_scientific_paths(root, index)


def _locked_python(source: Path) -> str:
    lock = json.loads(
        (source / "RUNTIME_LOCK.json").read_text(encoding="utf-8")
    )
    return str(lock["runtime"]["python_executable"])


def test_replay_routes_to_locked_interpreter_fresh_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A differing caller interpreter routes replay to a locked child."""

    source = _source_run(tmp_path)
    locked = _locked_python(source)
    spawned: list[dict] = []
    real_run = subprocess.run

    def recording_run(command, **kwargs):
        spawned.append(
            {
                "command": list(command),
                "env": dict(kwargs.get("env") or os.environ),
            }
        )
        return real_run(command, **kwargs)

    monkeypatch.setattr(
        replay_module,
        "_same_interpreter",
        lambda current, locked_value: False,
    )
    monkeypatch.setattr(
        replay_module,
        "_validate_locked_interpreter",
        lambda locked_value, source_dir: Path(locked_value).resolve(
            strict=False
        ),
    )
    monkeypatch.setattr(replay_module.subprocess, "run", recording_run)

    manifest = replay_completed_run(source)

    assert manifest.success is True
    assert len(spawned) == 1
    command = spawned[0]["command"]
    assert Path(command[0]).resolve(strict=False) == Path(
        locked
    ).resolve(strict=False)
    assert "--source-run" in command
    assert spawned[0]["env"].get(replay_module._REPLAY_WORKER_ENV) == "1"
    assert (source / "REPLAY_MANIFEST.json").is_file()
    assert (source / "fresh_replay").is_dir()


def test_replay_worker_mismatched_interpreter_fails_before_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker whose interpreter differs from the lock must fail."""

    source = _source_run(tmp_path)
    monkeypatch.setattr("sys.executable", r"C:\fake\python.exe")
    monkeypatch.setenv(replay_module._REPLAY_WORKER_ENV, "1")
    monkeypatch.setattr(
        replay_module,
        "_validate_locked_interpreter",
        lambda locked_value, source_dir: Path(sys.executable),
    )

    def boom(*args, **kwargs):
        raise AssertionError("worker must not spawn or validate a child")

    monkeypatch.setattr(replay_module, "_run_locked_interpreter_replay", boom)

    with pytest.raises(RuntimeError, match="does not match the locked"):
        replay_completed_run(source)
    assert not (source / "fresh_replay").exists()


def test_replay_worker_matched_interpreter_runs_without_spawning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A matching worker runs in-process and never recursively spawns."""

    source = _source_run(tmp_path)
    monkeypatch.setenv(replay_module._REPLAY_WORKER_ENV, "1")

    def boom(*args, **kwargs):
        raise AssertionError("worker must not spawn a child")

    monkeypatch.setattr(replay_module, "_run_locked_interpreter_replay", boom)
    monkeypatch.setattr(replay_module.subprocess, "run", boom)

    manifest = replay_completed_run(source)

    assert manifest.success is True


def _manifest_payload(success: bool) -> dict:
    return {
        "schema_version": "tmm-fresh-replay-manifest.v1",
        "source_run_id": "run-1",
        "replay_run_id": "run-1.fresh_replay",
        "scientific_source_relative": ".",
        "source_task_sha256": "ab" * 32,
        "replay_task_sha256": "ab" * 32,
        "qwen_disabled_for_replay": True,
        "checks": [],
        "matched_artifacts": 1,
        "total_artifacts": 1,
        "success": success,
        "notes": [],
    }


def _fake_completed(returncode: int, stderr: str = "") -> Any:
    class _FakeCompleted:
        pass

    fake = _FakeCompleted()
    fake.returncode = returncode
    fake.stdout = ""
    fake.stderr = stderr
    return fake


def test_replay_parent_rejects_unexpected_child_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_run(tmp_path)
    (source / "REPLAY_MANIFEST.json").write_text(
        json.dumps(_manifest_payload(True)), encoding="utf-8"
    )
    monkeypatch.setattr(
        replay_module.subprocess,
        "run",
        lambda *args, **kwargs: _fake_completed(3, "child crashed"),
    )

    with pytest.raises(RuntimeError, match="unexpected code"):
        _run_locked_interpreter_replay(
            source, "fresh_replay", False, Path(sys.executable)
        )


def test_replay_parent_enforces_exit_code_manifest_consistency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_run(tmp_path)
    (source / "REPLAY_MANIFEST.json").write_text(
        json.dumps(_manifest_payload(False)), encoding="utf-8"
    )
    monkeypatch.setattr(
        replay_module.subprocess,
        "run",
        lambda *args, **kwargs: _fake_completed(0),
    )
    with pytest.raises(RuntimeError, match="exited 0 but reported a failed"):
        _run_locked_interpreter_replay(
            source, "fresh_replay", False, Path(sys.executable)
        )

    (source / "REPLAY_MANIFEST.json").write_text(
        json.dumps(_manifest_payload(True)), encoding="utf-8"
    )
    monkeypatch.setattr(
        replay_module.subprocess,
        "run",
        lambda *args, **kwargs: _fake_completed(2),
    )
    with pytest.raises(RuntimeError, match="exited 2 but reported a success"):
        _run_locked_interpreter_replay(
            source, "fresh_replay", False, Path(sys.executable)
        )


def test_replay_parent_accepts_mismatch_manifest_on_code_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_run(tmp_path)
    (source / "REPLAY_MANIFEST.json").write_text(
        json.dumps(_manifest_payload(False)), encoding="utf-8"
    )
    monkeypatch.setattr(
        replay_module.subprocess,
        "run",
        lambda *args, **kwargs: _fake_completed(2),
    )

    manifest = _run_locked_interpreter_replay(
        source, "fresh_replay", False, Path(sys.executable)
    )

    assert manifest.success is False


def test_replay_missing_locked_interpreter_fails_before_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_run(tmp_path)
    missing = (
        Path(replay_module.__file__).resolve().parents[3]
        / ".venv-missing"
        / "Scripts"
        / "python.exe"
    )
    monkeypatch.setattr(
        replay_module,
        "_same_interpreter",
        lambda current, locked_value: False,
    )
    monkeypatch.setattr(
        replay_module,
        "_locked_python_executable",
        lambda source_dir: str(missing),
    )

    with pytest.raises(RuntimeError, match="missing"):
        replay_completed_run(source)
    assert not (source / "fresh_replay").exists()


def test_replay_unsafe_locked_interpreter_outside_project_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_run(tmp_path)
    monkeypatch.setattr(
        replay_module,
        "_same_interpreter",
        lambda current, locked_value: False,
    )
    monkeypatch.setattr(
        replay_module,
        "_locked_python_executable",
        lambda source_dir: str(
            Path(os.environ.get("SystemRoot", r"C:\Windows"))
            / "System32"
            / "notepad.exe"
        )
        if os.name == "nt"
        else "/bin/sh",
    )

    with pytest.raises(RuntimeError, match="outside the project root"):
        replay_completed_run(source)
    assert not (source / "fresh_replay").exists()


def test_replay_missing_runtime_lock_declaration_fails(
    tmp_path: Path,
) -> None:
    root = tmp_path / "lockless"
    root.mkdir()
    with pytest.raises(RuntimeError, match="requires RUNTIME_LOCK.json"):
        replay_module._locked_python_executable(root)
    (root / "RUNTIME_LOCK.json").write_text(
        json.dumps({"use_qwen_policy": True}), encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="does not declare"):
        replay_module._locked_python_executable(root)
    (root / "RUNTIME_LOCK.json").write_text(
        json.dumps(
            {
                "runtime": {
                    "python_executable": r"C:\fake\python.exe",
                }
            }
        ),
        encoding="utf-8",
    )
    assert replay_module._locked_python_executable(root) == (
        r"C:\fake\python.exe"
    )


def test_replay_child_failure_is_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_run(tmp_path)

    class _FakeCompleted:
        returncode = 3
        stdout = ""
        stderr = "child exploded"

    monkeypatch.setattr(
        replay_module,
        "_same_interpreter",
        lambda current, locked_value: False,
    )
    monkeypatch.setattr(
        replay_module,
        "_validate_locked_interpreter",
        lambda locked_value, source_dir: Path(sys.executable),
    )
    monkeypatch.setattr(
        replay_module.subprocess,
        "run",
        lambda *args, **kwargs: _FakeCompleted(),
    )

    with pytest.raises(RuntimeError, match="child exploded"):
        replay_completed_run(source)
    assert not (source / "REPLAY_MANIFEST.json").exists()


def test_replay_identical_interpreter_never_spawns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_run(tmp_path)

    def boom(*args, **kwargs):
        raise AssertionError("identical interpreter must run in-process")

    monkeypatch.setattr(
        replay_module, "_run_locked_interpreter_replay", boom
    )
    monkeypatch.setattr(replay_module.subprocess, "run", boom)

    manifest = replay_completed_run(source)

    assert manifest.success is True


def test_legacy_fallback_reconciles_without_index(tmp_path: Path) -> None:
    source = tmp_path / "legacy_source"
    replay = tmp_path / "compact_replay"
    experiment_id = "dev02_forward_dbr"
    sha = __import__("hashlib").sha256(
        experiment_id.encode("utf-8")
    ).hexdigest()[:12]
    compact = f"x/e_{sha}"
    payload = json.dumps({"status": "ok", "R_mean": 0.01})

    (source / "experiments" / experiment_id / "baseline").mkdir(
        parents=True,
        exist_ok=True,
    )
    (source / "experiments" / experiment_id / "baseline" / "SIMULATION_RESULT.json").write_text(
        payload,
        encoding="utf-8",
    )
    (source / "RUNTIME_LOCK.json").write_text(
        json.dumps({"use_qwen_policy": True}),
        encoding="utf-8",
    )

    (replay / compact / "b").mkdir(parents=True, exist_ok=True)
    (replay / compact / "b" / "SIMULATION_RESULT.json").write_text(
        payload,
        encoding="utf-8",
    )
    (replay / "RUNTIME_LOCK.json").write_text(
        json.dumps({"use_qwen_policy": True}),
        encoding="utf-8",
    )
    (replay / "ARTIFACT_PATH_INDEX.json").write_text(
        json.dumps(
            {
                "experiments": [
                    {
                        "experiment_id": experiment_id,
                        "physical_directory": compact,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    source_index = _load_experiment_path_index(source)
    replay_index = _load_experiment_path_index(replay)
    assert source_index == {
        experiment_id: f"experiments/{experiment_id}"
    }
    source_logical = _logical_scientific_paths(source, source_index)
    replay_logical = _logical_scientific_paths(replay, replay_index)
    assert set(source_logical) == set(replay_logical)
    for key in source_logical:
        source_digest = _scientific_digest(
            source_logical[key][1],
            root=source,
            path_index=source_index,
        )
        replay_digest = _scientific_digest(
            replay_logical[key][1],
            root=replay,
            path_index=replay_index,
        )
        assert source_digest == replay_digest, key


def _write_path_index(root: Path, rows: list[dict]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "ARTIFACT_PATH_INDEX.json").write_text(
        json.dumps(
            {
                "schema_version": "tmm-artifact-path-index.v1",
                "experiments": rows,
            }
        ),
        encoding="utf-8",
    )


def _candidate_run(
    root: Path,
    *,
    candidate_physical: str,
    experiment_id: str,
    candidate_id: str,
) -> None:
    candidate_dir = root / candidate_physical
    candidate_dir.mkdir(parents=True, exist_ok=True)
    (candidate_dir / "IDENTITY.json").write_text(
        json.dumps(
            {
                "schema_version": "tmm-artifact-identity.v1",
                "experiment_id": experiment_id,
                "candidate_id": candidate_id,
                "physical_directory": candidate_physical,
            }
        ),
        encoding="utf-8",
    )
    (candidate_dir / "SIMULATION_RESULT.json").write_text(
        json.dumps({"status": "ok", "R_mean": 0.01}), encoding="utf-8"
    )
    (candidate_dir / "OPTIMIZER_GRADIENT.json").write_text(
        json.dumps({"iterations": 3}), encoding="utf-8"
    )
    (candidate_dir / "DESIGN_PORTFOLIO.json").write_text(
        json.dumps(
            {
                "experiment_id": experiment_id,
                "portfolio_artifact_id": (
                    f"{candidate_physical}/DESIGN_PORTFOLIO.json"
                ),
                "candidates": [
                    {
                        "candidate_id": candidate_id,
                        "artifact_ids": [
                            f"{candidate_physical}/SIMULATION_RESULT.json",
                            f"{candidate_physical}/OPTIMIZER_GRADIENT.json",
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_replay_candidate_identity_reconciles_compact_and_semantic_layouts(
    tmp_path: Path,
) -> None:
    """c/c_<hash> and candidates/<semantic-id> reconcile via IDENTITY.json."""

    experiment_id = "opt_7layer_dbr_low_r"
    candidate_id = "opt_7layer_dbr_low_r__gradient_thickness__02"
    source = tmp_path / "legacy_source"
    replay = tmp_path / "semantic_replay"
    source_physical = f"experiments/{experiment_id}/c/c_3bbd1234abcd"
    replay_physical = f"experiments/{experiment_id}/candidates/{candidate_id}"
    for root, physical in (
        (source, source_physical),
        (replay, replay_physical),
    ):
        _candidate_run(
            root,
            candidate_physical=physical,
            experiment_id=experiment_id,
            candidate_id=candidate_id,
        )
        _write_path_index(
            root,
            [
                {
                    "experiment_id": experiment_id,
                    "physical_directory": f"experiments/{experiment_id}",
                }
            ],
        )

    source_index = _load_experiment_path_index(source)
    replay_index = _load_experiment_path_index(replay)
    source_candidates = _load_candidate_identity(source, source_index)
    replay_candidates = _load_candidate_identity(replay, replay_index)
    source_logical = _logical_scientific_paths(
        source, source_index, source_candidates
    )
    replay_logical = _logical_scientific_paths(
        replay, replay_index, replay_candidates
    )

    assert set(source_logical) == set(replay_logical)
    expected = (
        f"experiment:{experiment_id}:candidates/{candidate_id}/"
        "SIMULATION_RESULT.json"
    )
    assert expected in source_logical
    assert expected in replay_logical
    for key in source_logical:
        source_digest = _scientific_digest(
            source_logical[key][1],
            root=source,
            path_index=source_index,
            candidate_identity=source_candidates,
        )
        replay_digest = _scientific_digest(
            replay_logical[key][1],
            root=replay,
            path_index=replay_index,
            candidate_identity=replay_candidates,
        )
        assert source_digest == replay_digest, key


def test_replay_candidate_identity_rejects_conflicting_duplicate(
    tmp_path: Path,
) -> None:
    root = tmp_path / "conflict"
    experiment_id = "opt_7layer_dbr_low_r"
    _write_path_index(
        root,
        [
            {
                "experiment_id": experiment_id,
                "physical_directory": f"experiments/{experiment_id}",
            }
        ],
    )
    _candidate_run(
        root,
        candidate_physical=f"experiments/{experiment_id}/c/c_aaaa",
        experiment_id=experiment_id,
        candidate_id="same_id",
    )
    _candidate_run(
        root,
        candidate_physical=f"experiments/{experiment_id}/c/c_bbbb",
        experiment_id=experiment_id,
        candidate_id="same_id",
    )
    index = _load_experiment_path_index(root)
    with pytest.raises(ValueError, match="conflicting physical directories"):
        _load_candidate_identity(root, index)


def test_replay_candidate_identity_rejects_unsafe_physical_directory(
    tmp_path: Path,
) -> None:
    root = tmp_path / "unsafe"
    experiment_id = "opt_7layer_dbr_low_r"
    _write_path_index(
        root,
        [
            {
                "experiment_id": experiment_id,
                "physical_directory": f"experiments/{experiment_id}",
            }
        ],
    )
    candidate_dir = root / "experiments" / experiment_id / "c" / "c_aaaa"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    (candidate_dir / "IDENTITY.json").write_text(
        json.dumps(
            {
                "schema_version": "tmm-artifact-identity.v1",
                "experiment_id": experiment_id,
                "candidate_id": "candidate_x",
                "physical_directory": "../escape",
            }
        ),
        encoding="utf-8",
    )
    index = _load_experiment_path_index(root)
    with pytest.raises(ValueError, match="must resolve under the run root"):
        _load_candidate_identity(root, index)


def test_replay_candidate_identity_rejects_location_and_ownership_mismatch(
    tmp_path: Path,
) -> None:
    root = tmp_path / "mismatch"
    experiment_id = "opt_7layer_dbr_low_r"
    _write_path_index(
        root,
        [
            {
                "experiment_id": experiment_id,
                "physical_directory": f"experiments/{experiment_id}",
            }
        ],
    )
    index = _load_experiment_path_index(root)
    candidate_dir = root / "experiments" / experiment_id / "c" / "c_aaaa"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    (candidate_dir / "IDENTITY.json").write_text(
        json.dumps(
            {
                "schema_version": "tmm-artifact-identity.v1",
                "experiment_id": experiment_id,
                "candidate_id": "candidate_x",
                "physical_directory": (
                    f"experiments/{experiment_id}/c/c_bbbb"
                ),
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="does not match its location"):
        _load_candidate_identity(root, index)

    (candidate_dir / "IDENTITY.json").write_text(
        json.dumps(
            {
                "schema_version": "tmm-artifact-identity.v1",
                "experiment_id": "other_experiment",
                "candidate_id": "candidate_x",
                "physical_directory": (
                    f"experiments/{experiment_id}/c/c_aaaa"
                ),
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="belongs to experiment"):
        _load_candidate_identity(root, index)


def test_replay_candidate_identity_rejects_malformed_json(
    tmp_path: Path,
) -> None:
    root = tmp_path / "malformed"
    experiment_id = "opt_7layer_dbr_low_r"
    _write_path_index(
        root,
        [
            {
                "experiment_id": experiment_id,
                "physical_directory": f"experiments/{experiment_id}",
            }
        ],
    )
    candidate_dir = root / "experiments" / experiment_id / "c" / "c_aaaa"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    (candidate_dir / "IDENTITY.json").write_text(
        "{not json", encoding="utf-8"
    )
    index = _load_experiment_path_index(root)
    with pytest.raises(ValueError, match="is malformed"):
        _load_candidate_identity(root, index)
