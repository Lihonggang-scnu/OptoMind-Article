"""Persistence and replay of verification evidence and policy.

Evidence is observed facts, policy is the judgement rule, and the certificate
is the verdict recomputed from the two.  These tests pin the persistence
contract: the four-field identity binding survives disk round-trips, replaying
``evaluate_evidence`` from the persisted pair reproduces the persisted
certificate byte-for-byte, tampered bindings fail closed, and a cache replay
never presents the source run's evidence as its own.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tmm_engine import (
    AcceptanceSettings,
    IlluminationSpec,
    LayerSpec,
    MaterialRegistry,
    MediumSpec,
    SimulationTask,
    SpectralGrid,
    StackSpec,
    TMMWorkbench,
    certify_simulation,
    evaluate_evidence,
    load_verification_evidence,
    load_verification_policy,
    write_verification_artifacts,
)
from tmm_engine.acceptance import (
    VerificationArtifactError,
    verification_policy_dict,
    verification_policy_from_dict,
)
from tmm_engine.execution import ExecutionSettings, execute_task
from tmm_engine.experiment_store import ExperimentStore
from tmm_engine.hashing import canonical_json_bytes
from tmm_engine.managed_execution import execute_managed_task
from tmm_engine.run_artifacts import validate_run_artifact_integrity

_POLICY_TOP_LEVEL = {
    "schema_version",
    "identity_scheme",
    "require_spectral_convergence",
    "require_independent_solver",
    "cross_solver_tolerance",
    "energy_tolerance",
    "convergence",
}

_CONVERGENCE_FIELDS = {
    "max_refinements",
    "max_pointwise_deviation",
    "max_integral_deviation",
    "maximum_points",
    "maximum_angle_points",
    "max_intervals_per_round",
    "max_angle_intervals_per_round",
    "max_angular_deviation",
}


def _film() -> SimulationTask:
    return SimulationTask(
        stack=StackSpec(
            layers=(LayerSpec("tio2", 60.0), LayerSpec("sio2", 90.0)),
            incident=MediumSpec.air(),
            exit=MediumSpec("sio2"),
        ),
        spectrum=SpectralGrid(500.0, 600.0, 11),
        illumination=IlluminationSpec((0.0,), ("s",)),
    )


def _certified(settings: AcceptanceSettings | None = None):
    settings = settings or AcceptanceSettings()
    return certify_simulation(TMMWorkbench(MaterialRegistry()), _film(), settings)


# ---- policy serialization ----------------------------------------------------


def test_policy_round_trips_through_dict() -> None:
    for settings in (
        AcceptanceSettings(),
        AcceptanceSettings(require_spectral_convergence=False),
        AcceptanceSettings(energy_tolerance=1e-4, cross_solver_tolerance=1e-5),
    ):
        payload = verification_policy_dict(settings)
        assert payload["schema_version"] == "veritmm-verification-policy-v1"
        assert payload["identity_scheme"] == "veritmm-canonical-json-v1"
        rebuilt = verification_policy_from_dict(payload)
        assert verification_policy_dict(rebuilt) == payload


def test_policy_parsing_is_strict() -> None:
    payload = verification_policy_dict(AcceptanceSettings())

    def broken(mutate) -> dict:
        candidate = json.loads(json.dumps(payload))
        mutate(candidate)
        return candidate

    with pytest.raises(VerificationArtifactError):
        verification_policy_from_dict(broken(lambda p: p.pop("energy_tolerance")))
    with pytest.raises(VerificationArtifactError):
        verification_policy_from_dict(broken(lambda p: p.update(extra_field=1)))
    with pytest.raises(VerificationArtifactError):
        verification_policy_from_dict(
            broken(lambda p: p["convergence"].update(extra_convergence_field=1))
        )
    with pytest.raises(VerificationArtifactError):
        verification_policy_from_dict(
            broken(lambda p: p.update(schema_version="veritmm-verification-policy-v0"))
        )
    with pytest.raises(VerificationArtifactError):
        verification_policy_from_dict(
            broken(lambda p: p.update(identity_scheme="some-other-scheme"))
        )
    with pytest.raises(VerificationArtifactError):
        verification_policy_from_dict(
            broken(lambda p: p.update(require_independent_solver="yes"))
        )
    with pytest.raises(VerificationArtifactError):
        verification_policy_from_dict(
            broken(lambda p: p["convergence"].update(max_refinements="six"))
        )


# ---- write / load round trip and replay -------------------------------------


def test_write_then_load_round_trips_the_binding(tmp_path: Path) -> None:
    certified = _certified()
    assert certified.evidence is not None
    evidence_payload, policy_payload = write_verification_artifacts(
        tmp_path, certified.evidence, AcceptanceSettings(), run_id="run_abc123"
    )

    assert (tmp_path / "VERIFICATION_EVIDENCE.json").is_file()
    assert (tmp_path / "VERIFICATION_POLICY.json").is_file()
    assert evidence_payload["run_id"] == "run_abc123"
    assert evidence_payload["identity_scheme"] == "veritmm-canonical-json-v1"
    assert evidence_payload["task_sha256"] == certified.certificate["task_sha256"]
    assert evidence_payload["schema_version"] == "veritmm-verification-evidence-v1"

    loaded = load_verification_evidence(tmp_path / "VERIFICATION_EVIDENCE.json")
    # JSON turns tuples into lists, so judge the round trip on canonical bytes.
    assert canonical_json_bytes(loaded.to_dict()) == canonical_json_bytes(
        evidence_payload
    )
    assert loaded.run_id == "run_abc123"
    policy = load_verification_policy(tmp_path / "VERIFICATION_POLICY.json")
    assert verification_policy_dict(policy) == policy_payload


def test_replay_from_disk_reproduces_the_certificate(tmp_path: Path) -> None:
    certified = _certified()
    write_verification_artifacts(
        tmp_path, certified.evidence, AcceptanceSettings(), run_id="run_replay"
    )
    evidence = load_verification_evidence(tmp_path / "VERIFICATION_EVIDENCE.json")
    policy = load_verification_policy(tmp_path / "VERIFICATION_POLICY.json")

    replayed = evaluate_evidence(evidence, policy)
    assert replayed["certificate_id"] == certified.certificate["certificate_id"]
    assert replayed["accepted"] == certified.certificate["accepted"]
    assert replayed["status"] == certified.certificate["status"]
    assert replayed["failures"] == certified.certificate["failures"]
    assert canonical_json_bytes(replayed) == canonical_json_bytes(
        certified.certificate
    )


def test_evidence_artifacts_never_reference_the_run_index(tmp_path: Path) -> None:
    write_verification_artifacts(
        tmp_path, _certified().evidence, AcceptanceSettings(), run_id="run_x"
    )
    for name in ("VERIFICATION_EVIDENCE.json", "VERIFICATION_POLICY.json"):
        text = (tmp_path / name).read_text(encoding="utf-8")
        payload = json.loads(text)
        assert "artifacts" not in payload
        assert "RUN_RESULT" not in text
        assert "certificate_id" not in payload


# ---- binding attacks fail closed ---------------------------------------------


def _tampered(tmp_path: Path, mutate) -> Path:
    write_verification_artifacts(
        tmp_path, _certified().evidence, AcceptanceSettings(), run_id="run_abc123"
    )
    path = tmp_path / "VERIFICATION_EVIDENCE.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_foreign_run_id_binding_is_rejected(tmp_path: Path) -> None:
    path = _tampered(tmp_path, lambda p: p.update(run_id="run_other"))
    with pytest.raises(VerificationArtifactError):
        load_verification_evidence(path, run_id="run_abc123")
    # Without an expectation the load succeeds: the binding is simply reported.
    assert load_verification_evidence(path).run_id == "run_other"


def test_foreign_task_sha256_binding_is_rejected(tmp_path: Path) -> None:
    path = _tampered(tmp_path, lambda p: p.update(task_sha256="a" * 64))
    with pytest.raises(VerificationArtifactError):
        load_verification_evidence(path, task_sha256="b" * 64)


def test_structural_tampering_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(VerificationArtifactError):
        load_verification_evidence(
            _tampered(tmp_path, lambda p: p.update(schema_version="veritmm-verification-evidence-v0"))
        )
    with pytest.raises(VerificationArtifactError):
        load_verification_evidence(
            _tampered(tmp_path, lambda p: p.update(identity_scheme="other-scheme"))
        )
    with pytest.raises(VerificationArtifactError):
        load_verification_evidence(
            _tampered(tmp_path, lambda p: p.update(extra_field=True))
        )
    with pytest.raises(VerificationArtifactError):
        load_verification_evidence(
            _tampered(tmp_path, lambda p: p.pop("physics_audit"))
        )
    with pytest.raises(VerificationArtifactError):
        load_verification_evidence(
            _tampered(tmp_path, lambda p: p.update(run_id=""))
        )


# ---- run integration ---------------------------------------------------------


def test_execute_task_writes_and_indexes_verification_artifacts(tmp_path: Path) -> None:
    output = tmp_path / "run"
    envelope = execute_task(
        "simulate", _film(), output, settings=ExecutionSettings()
    )
    kinds = {item["kind"]: item for item in envelope["artifacts"]}
    assert "verification_evidence" in kinds
    assert "verification_policy" in kinds
    assert "physics_certificate" in kinds
    evidence_ref = kinds["verification_evidence"]
    on_disk = json.loads((output / "VERIFICATION_EVIDENCE.json").read_text(encoding="utf-8"))
    assert on_disk["run_id"] == envelope["run_id"]
    assert evidence_ref["sha256"] and evidence_ref["size_bytes"] > 0
    validate_run_artifact_integrity(output)


def test_cache_replay_never_carries_the_source_evidence(tmp_path: Path) -> None:
    store = ExperimentStore(tmp_path / "store")
    task = _film()
    first = execute_managed_task(
        "simulate",
        task,
        tmp_path / "source",
        store=store,
        execution_settings=ExecutionSettings(),
        cache=True,
    )
    assert first["cache_hit"] is False
    source_root = tmp_path / "source"
    assert (source_root / "VERIFICATION_EVIDENCE.json").is_file()

    second = execute_managed_task(
        "simulate",
        task,
        tmp_path / "replay",
        store=store,
        execution_settings=ExecutionSettings(),
        cache=True,
    )
    assert second["cache_hit"] is True
    replay_root = tmp_path / "replay"
    assert not (replay_root / "VERIFICATION_EVIDENCE.json").is_file()
    assert not (replay_root / "VERIFICATION_POLICY.json").is_file()
    replay_kinds = {item["kind"] for item in second["artifacts"]}
    assert "verification_evidence" not in replay_kinds
    assert "verification_policy" not in replay_kinds
    validate_run_artifact_integrity(replay_root)
