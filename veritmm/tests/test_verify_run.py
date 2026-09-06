"""Four-state verify-run tests.

Pins the semantics agreed for 1.1: the four states are independent (a cache
replay is certified with an unavailable replay), an integrity failure stops
all downstream judgement, skip-certificate runs are uncertified even when
they replay cleanly, and the report is never collapsed into a single boolean.
"""

from __future__ import annotations

import json
from pathlib import Path

from tmm_engine import (
    IlluminationSpec,
    LayerSpec,
    MediumSpec,
    SimulationTask,
    SpectralGrid,
    StackSpec,
)
from tmm_engine.cli import main
from tmm_engine.execution import ExecutionSettings, execute_task
from tmm_engine.hashing import file_sha256
from tmm_engine.verify_run import verify_run_dir

STATES = (
    "integrity_status",
    "certification_status",
    "replay_status",
    "authenticity_status",
    "overall_status",
)


def _dbr() -> SimulationTask:
    return SimulationTask(
        stack=StackSpec(
            layers=(LayerSpec("tio2", 90.0), LayerSpec("sio2", 140.0)) * 3,
            incident=MediumSpec.air(),
            exit=MediumSpec("sio2"),
        ),
        spectrum=SpectralGrid(400.0, 800.0, 41),
        illumination=IlluminationSpec((0.0, 30.0), ("s", "p")),
    )


def _run_fresh(tmp_path: Path, *, skip_certificate: bool = False) -> Path:
    output = tmp_path / "run"
    execute_task(
        "simulate",
        _dbr(),
        output,
        settings=ExecutionSettings(skip_certificate=skip_certificate),
    )
    return output


def _rewrite_envelope(run_dir: Path, mutate) -> None:
    path = run_dir / "RUN_RESULT.json"
    envelope = json.loads(path.read_text(encoding="utf-8"))
    mutate(envelope)
    path.write_text(json.dumps(envelope), encoding="utf-8")


def test_fresh_run_verifies_end_to_end(tmp_path: Path) -> None:
    run_dir = _run_fresh(tmp_path)
    report = verify_run_dir(run_dir)
    assert {key: report[key] for key in STATES} == {
        "integrity_status": "valid",
        "certification_status": "certified",
        "replay_status": "passed",
        "authenticity_status": "unsigned",
        "overall_status": "valid",
    }
    assert report["certificate_verdict"] == "accepted"
    assert report["acceptance_checks"]["spectral_convergence"] not in (
        None,
        "not_requested",
    )
    assert report["acceptance_checks"]["independent_solver"] not in (
        None,
        "not_requested",
    )
    assert main(["verify-run", str(run_dir), "--json"]) == 0


def test_pre_ledger_evidence_replays_on_the_verdict_core(tmp_path: Path) -> None:
    """Evidence written before the independent energy ledger existed (no
    ``energy_accounting`` field) still replays under a newer engine: the
    additive certificate block is tolerated on the verdict core, while any
    scientific difference would still fail."""

    run_dir = _run_fresh(tmp_path)
    evidence_path = run_dir / "VERIFICATION_EVIDENCE.json"
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert "energy_accounting" in payload
    del payload["energy_accounting"]
    evidence_path.write_text(json.dumps(payload), encoding="utf-8")

    def mutate(envelope: dict) -> None:
        for reference in envelope["artifacts"]:
            if reference["kind"] == "verification_evidence":
                reference["sha256"] = file_sha256(evidence_path)
                reference["size_bytes"] = evidence_path.stat().st_size

    _rewrite_envelope(run_dir, mutate)
    report = verify_run_dir(run_dir)
    assert report["replay_status"] == "passed"
    assert report["replay_comparison_scope"] == "verdict_core"
    assert report["overall_status"] == "valid"


def test_cache_replay_is_certified_with_unavailable_replay(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    first = tmp_path / "first"
    execute_task("simulate", _dbr(), first, settings=ExecutionSettings())
    # Produce a cache replay through the managed layer with a fresh store.
    from tmm_engine.experiment_store import ExperimentStore
    from tmm_engine.managed_execution import execute_managed_task

    store = ExperimentStore(store_dir)
    execute_managed_task(
        "simulate", _dbr(), tmp_path / "source",
        store=store, execution_settings=ExecutionSettings(), cache=True,
    )
    replay_dir = tmp_path / "replay"
    replay = execute_managed_task(
        "simulate", _dbr(), replay_dir,
        store=store, execution_settings=ExecutionSettings(), cache=True,
    )
    assert replay["cache_hit"] is True

    report = verify_run_dir(replay_dir)
    assert report["integrity_status"] == "valid"
    assert report["certification_status"] == "certified"
    assert report["replay_status"] == "unavailable"
    assert "cache replay" in report["replay_reason"]
    assert report["overall_status"] == "incomplete"
    assert main(["verify-run", str(replay_dir), "--json"]) == 3


def test_tampered_artifact_stops_all_downstream_judgement(tmp_path: Path) -> None:
    run_dir = _run_fresh(tmp_path)
    spectra = run_dir / "SPECTRA.csv"
    text = spectra.read_text(encoding="utf-8")
    spectra.write_text(text.replace("0.9", "0.1", 1), encoding="utf-8")

    report = verify_run_dir(run_dir)
    assert report["integrity_status"] == "invalid"
    assert any(
        item["code"] == "sha256_mismatch" for item in report["integrity_failures"]
    )
    assert report["certification_status"] == "not_evaluated"
    assert report["replay_status"] == "not_evaluated"
    assert report["overall_status"] == "invalid"
    assert main(["verify-run", str(run_dir), "--json"]) == 2


def test_envelope_certificate_identity_tampering_is_detected(tmp_path: Path) -> None:
    run_dir = _run_fresh(tmp_path)
    _rewrite_envelope(
        run_dir, lambda envelope: envelope.update(certificate_id="f" * 64)
    )
    report = verify_run_dir(run_dir)
    assert report["integrity_status"] == "invalid"
    assert any(
        item["code"] == "certificate_identity_mismatch"
        for item in report["integrity_failures"]
    )


def test_evidence_spliced_from_another_run_is_rejected(tmp_path: Path) -> None:
    run_a = _run_fresh(tmp_path / "a")
    run_b = _run_fresh(tmp_path / "b")

    # Splice run B's evidence into run A and re-hash the index entry so the
    # digest check passes; the run_id binding must still catch the splice.
    evidence = json.loads(
        (run_b / "VERIFICATION_EVIDENCE.json").read_text(encoding="utf-8")
    )
    (run_a / "VERIFICATION_EVIDENCE.json").write_text(
        json.dumps(evidence), encoding="utf-8"
    )

    def splice(envelope: dict) -> None:
        for reference in envelope["artifacts"]:
            if reference["kind"] == "verification_evidence":
                reference["sha256"] = file_sha256(
                    run_a / "VERIFICATION_EVIDENCE.json"
                )
                reference["size_bytes"] = (
                    run_a / "VERIFICATION_EVIDENCE.json"
                ).stat().st_size

    _rewrite_envelope(run_a, splice)
    report = verify_run_dir(run_a)
    assert report["integrity_status"] == "invalid"
    assert any(
        item["code"] == "verification_artifact_invalid"
        for item in report["integrity_failures"]
    )


def test_replay_detects_tampered_evidence(tmp_path: Path) -> None:
    run_dir = _run_fresh(tmp_path)

    # Tamper with a recorded fact, then re-hash the index so integrity stays
    # valid: the replay must still catch the falsified observation.
    evidence_path = run_dir / "VERIFICATION_EVIDENCE.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["physics_audit"]["energy_conservation_max_abs_error"] = 0.5
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    def rehash(envelope: dict) -> None:
        for reference in envelope["artifacts"]:
            if reference["kind"] == "verification_evidence":
                reference["sha256"] = file_sha256(evidence_path)
                reference["size_bytes"] = evidence_path.stat().st_size

    _rewrite_envelope(run_dir, rehash)

    report = verify_run_dir(run_dir)
    assert report["integrity_status"] == "valid"
    assert report["replay_status"] == "failed"
    assert report["overall_status"] == "invalid"
    assert "certificate_id" in report["replay_differences"]


def test_skip_certificate_run_is_uncertified_even_when_it_replays(tmp_path: Path) -> None:
    run_dir = _run_fresh(tmp_path, skip_certificate=True)
    report = verify_run_dir(run_dir)
    assert report["integrity_status"] == "valid"
    assert report["certification_status"] == "uncertified"
    assert any("full acceptance checks" in reason for reason in report["certification_reasons"])
    assert report["acceptance_checks"] == {
        "spectral_convergence": "not_requested",
        "independent_solver": "not_requested",
    }
    assert report["replay_status"] == "passed"
    assert report["overall_status"] == "incomplete"


def test_legacy_run_without_observations_reports_unavailable_replay(
    tmp_path: Path,
) -> None:
    run_dir = _run_fresh(tmp_path)

    def make_legacy_without_observations(envelope: dict) -> None:
        envelope.pop("identity_scheme", None)
        envelope["artifacts"] = [
            reference
            for reference in envelope["artifacts"]
            if reference["kind"]
            not in (
                "verification_evidence",
                "verification_policy",
                "normalized_task",
                "simulation_result",
            )
        ]

    _rewrite_envelope(run_dir, make_legacy_without_observations)
    for name in (
        "VERIFICATION_EVIDENCE.json",
        "VERIFICATION_POLICY.json",
        "NORMALIZED_TASK.json",
        "SIMULATION_RESULT.json",
    ):
        (run_dir / name).unlink()

    report = verify_run_dir(run_dir)
    assert report["integrity_status"] == "valid"
    assert report["certification_status"] == "certified"
    assert report["replay_status"] == "unavailable"
    assert "insufficient evidence" in report["replay_reason"]
    assert report["overall_status"] == "incomplete"


def test_structural_failures_are_typed(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    report = verify_run_dir(empty)
    assert report["integrity_status"] == "invalid"
    assert report["integrity_failures"][0]["code"] == "run_envelope_missing"

    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "RUN_RESULT.json").write_text("{not json", encoding="utf-8")
    report = verify_run_dir(broken)
    assert report["integrity_failures"][0]["code"] == "run_envelope_unreadable"

    missing_file = tmp_path / "missing_ref"
    missing_file.mkdir()
    envelope = {
        "schema_version": "veritmm-run-result-v1",
        "run_id": "run_x",
        "task_sha256": "a" * 64,
        "artifacts": [
            {
                "kind": "physics_certificate",
                "path": "gone.json",
                "schema_version": "physics-acceptance-certificate-v1",
                "sha256": "b" * 64,
                "size_bytes": 1,
            }
        ],
    }
    (missing_file / "RUN_RESULT.json").write_text(json.dumps(envelope), encoding="utf-8")
    report = verify_run_dir(missing_file)
    assert report["integrity_status"] == "invalid"
    assert any(
        item["code"] == "artifact_missing" for item in report["integrity_failures"]
    )


def test_expected_identity_arguments_are_enforced(tmp_path: Path) -> None:
    run_dir = _run_fresh(tmp_path)
    report = verify_run_dir(run_dir, expected_run_id="run_other")
    assert report["integrity_status"] == "invalid"
    assert report["integrity_failures"][0]["code"] == "run_identity_mismatch"

    report = verify_run_dir(run_dir, expected_task_sha256="b" * 64)
    assert report["integrity_failures"][0]["code"] == "task_identity_mismatch"

    ok = verify_run_dir(
        run_dir,
        expected_run_id=json.loads(
            (run_dir / "RUN_RESULT.json").read_text(encoding="utf-8")
        )["run_id"],
    )
    assert ok["overall_status"] == "valid"
