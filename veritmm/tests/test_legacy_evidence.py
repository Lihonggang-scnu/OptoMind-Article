"""LegacyEvidenceAdapter tests against a real published-v1.0.0 artifact tree.

The fixture under ``tests/fixtures/legacy_v1_0_sample/`` was generated with
the actual v1.0.0 release code (``git archive`` of the release commit) and is
committed verbatim.  The tests pin the adapter contract: replay derives
evidence only from observation/audit artifacts, insufficiency degrades to
``replay_status: unavailable`` instead of approximation, and a tampered
historical verdict is caught by the replay rather than laundered into the
evidence.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tmm_engine.hashing import file_sha256
from tmm_engine.legacy_evidence import build_legacy_evidence
from tmm_engine.verify_run import verify_run_dir

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "legacy_v1_0_sample"

STATES = (
    "integrity_status",
    "certification_status",
    "replay_status",
    "authenticity_status",
    "overall_status",
)


def _copy_fixture(tmp_path: Path) -> Path:
    target = tmp_path / "legacy_run"
    shutil.copytree(FIXTURE, target)
    return target


def _rehash_reference(run_dir: Path, kind: str) -> None:
    envelope_path = run_dir / "RUN_RESULT.json"
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    for reference in envelope["artifacts"]:
        if reference["kind"] == kind:
            artifact = run_dir / reference["path"]
            reference["sha256"] = file_sha256(artifact)
            reference["size_bytes"] = artifact.stat().st_size
    envelope_path.write_text(json.dumps(envelope), encoding="utf-8")


def _strip_reference(run_dir: Path, kind: str) -> None:
    envelope_path = run_dir / "RUN_RESULT.json"
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    envelope["artifacts"] = [
        reference
        for reference in envelope["artifacts"]
        if reference["kind"] != kind
    ]
    envelope_path.write_text(json.dumps(envelope), encoding="utf-8")


def test_published_v1_0_sample_replays_to_valid(tmp_path: Path) -> None:
    report = verify_run_dir(_copy_fixture(tmp_path))
    assert {key: report[key] for key in STATES} == {
        "integrity_status": "valid",
        "certification_status": "certified",
        "replay_status": "passed",
        "authenticity_status": "unsigned",
        "overall_status": "valid",
    }
    legacy = report["legacy_replay"]
    assert legacy is not None
    assert legacy["comparison_scope"] in ("full", "verdict_core")
    assert "task_payload" in legacy["mapped_fields"]
    assert len(legacy["mapped_fields"]) == 16
    assert report["replay_differences"] == []


def test_missing_observation_artifact_degrades_to_unavailable(
    tmp_path: Path,
) -> None:
    run_dir = _copy_fixture(tmp_path)
    (run_dir / "SIMULATION_RESULT.json").unlink()
    _strip_reference(run_dir, "simulation_result")

    report = verify_run_dir(run_dir)
    assert report["integrity_status"] == "valid"
    assert report["certification_status"] == "certified"
    assert report["replay_status"] == "unavailable"
    assert "insufficient evidence" in report["replay_reason"]
    assert report["overall_status"] == "incomplete"


def test_identity_scheme_divergence_refuses_the_mapping(tmp_path: Path) -> None:
    run_dir = _copy_fixture(tmp_path)
    certificate_path = run_dir / "PHYSICS_ACCEPTANCE_CERTIFICATE.json"
    certificate = json.loads(certificate_path.read_text(encoding="utf-8"))
    certificate["task_sha256"] = "c" * 64
    certificate_path.write_text(json.dumps(certificate), encoding="utf-8")
    _rehash_reference(run_dir, "physics_certificate")

    report = verify_run_dir(run_dir)
    assert report["replay_status"] == "unavailable"
    assert "veritmm-canonical-json-v1" in report["replay_reason"]


def test_tampered_legacy_verdict_is_caught_not_laundered(tmp_path: Path) -> None:
    """The adapter must not read the verdict to build evidence.

    Flipping ``accepted`` in the historical certificate (and re-hashing the
    index so integrity passes) leaves the mapped evidence untouched: the
    replay re-derives ``accepted: true`` from the observations and the
    comparison fails against the falsified certificate.
    """

    run_dir = _copy_fixture(tmp_path)
    certificate_path = run_dir / "PHYSICS_ACCEPTANCE_CERTIFICATE.json"
    certificate = json.loads(certificate_path.read_text(encoding="utf-8"))
    assert certificate["accepted"] is True
    certificate["accepted"] = False
    certificate["status"] = "rejected_physics"
    certificate_path.write_text(json.dumps(certificate), encoding="utf-8")
    _rehash_reference(run_dir, "physics_certificate")

    report = verify_run_dir(run_dir)
    assert report["integrity_status"] == "valid"
    assert report["replay_status"] == "failed"
    assert report["overall_status"] == "invalid"
    assert "accepted" in report["replay_differences"]


def test_adapter_rejects_unsupported_legacy_capability_rejections(
    tmp_path: Path,
) -> None:
    run_dir = _copy_fixture(tmp_path)
    certificate_path = run_dir / "PHYSICS_ACCEPTANCE_CERTIFICATE.json"
    certificate = json.loads(certificate_path.read_text(encoding="utf-8"))
    certificate["capability_assessment"]["supported"] = False
    certificate_path.write_text(json.dumps(certificate), encoding="utf-8")
    _rehash_reference(run_dir, "physics_certificate")

    envelope = json.loads((run_dir / "RUN_RESULT.json").read_text(encoding="utf-8"))
    references_by_kind = {ref["kind"]: ref for ref in envelope["artifacts"]}
    mapping, reasons = build_legacy_evidence(
        run_dir, envelope, certificate, references_by_kind
    )
    assert mapping is None
    assert any("capability rejections" in reason for reason in reasons)


@pytest.mark.parametrize("kind", ("normalized_task", "simulation_result"))
def test_adapter_requires_observation_artifacts(tmp_path: Path, kind: str) -> None:
    run_dir = _copy_fixture(tmp_path)
    target = run_dir / {
        "normalized_task": "NORMALIZED_TASK.json",
        "simulation_result": "SIMULATION_RESULT.json",
    }[kind]
    target.unlink()
    _strip_reference(run_dir, kind)

    report = verify_run_dir(run_dir)
    assert report["replay_status"] == "unavailable"
    assert report["overall_status"] == "incomplete"
