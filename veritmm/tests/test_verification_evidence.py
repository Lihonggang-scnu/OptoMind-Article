"""Unit tests for the VerificationEvidence / evaluate_evidence split.

The equivalence fixtures (``test_verification_equivalence.py``) prove that the
split certificate equals the pre-refactor certificate.  These tests pin the
properties that make the split worth having: evidence carries raw decision
inputs and no verdicts, and ``evaluate_evidence`` is a pure, solver-free
deterministic function of evidence plus settings.
"""

from __future__ import annotations

import dataclasses

from test_verification_equivalence import _Case, build_cases

from tmm_engine import (
    AcceptanceSettings,
    MaterialRegistry,
    TMMWorkbench,
    certify_simulation,
)
from tmm_engine.acceptance import (
    EVIDENCE_SCHEMA_VERSION,
    VerificationEvidence,
    collect_verification_evidence,
    evaluate_evidence,
)

_BY_NAME = {case.name: case for case in build_cases()}
_ACCEPTED = _BY_NAME["dbr_accepted_default"]
_UNSUPPORTED = _BY_NAME["unsupported_anisotropic_rejection"]


def _collected(case: _Case):
    workbench = TMMWorkbench(MaterialRegistry())
    return collect_verification_evidence(workbench, case.task, case.settings)


def test_evidence_schema_version_is_pinned() -> None:
    assert EVIDENCE_SCHEMA_VERSION == "veritmm-verification-evidence-v1"


def test_evidence_carries_raw_inputs_and_no_verdicts() -> None:
    evidence, result = _collected(_ACCEPTED)
    payload = evidence.to_dict()

    assert evidence.schema_version == EVIDENCE_SCHEMA_VERSION
    for forbidden in ("accepted", "status", "certificate_id", "evidence_coverage"):
        assert forbidden not in payload

    # Raw quantities with the tolerances left to the acceptance settings.
    assert isinstance(evidence.physics_audit["energy_conservation_max_abs_error"], float)
    assert "energy_conservation_max_abs_error" in payload["physics_audit"]
    assert evidence.spectral_convergence is not None
    assert evidence.independent_solver_check["status"] in {"passed", "failed"}
    assert result is not None


def test_evaluate_evidence_is_pure_and_deterministic() -> None:
    evidence, _ = _collected(_ACCEPTED)

    # No workbench is involved: judgement consumes only evidence + settings.
    first = evaluate_evidence(evidence, _ACCEPTED.settings)
    second = evaluate_evidence(evidence, _ACCEPTED.settings)
    assert first == second
    assert first["certificate_id"] == second["certificate_id"]
    assert first["accepted"] is True


def test_evaluate_matches_certify_for_every_fixture_case() -> None:
    for case in build_cases():
        try:
            evidence, _ = _collected(case)
        except Exception:
            # Collection aborts (the unknown-material case): certify turns the
            # exception into a rejection certificate, so there is no evidence
            # to evaluate.  That path is covered by the golden fixtures.
            continue
        composed = evaluate_evidence(evidence, case.settings)
        direct = certify_simulation(
            TMMWorkbench(MaterialRegistry()), case.task, case.settings
        ).certificate
        assert composed == direct, case.name


def test_evidence_task_identity_binds_the_certificate() -> None:
    evidence, _ = _collected(_ACCEPTED)
    certificate = evaluate_evidence(evidence, _ACCEPTED.settings)
    assert evidence.task_sha256 == certificate["task_sha256"]


def test_unsupported_evidence_replays_to_the_same_rejection() -> None:
    evidence, result = _collected(_UNSUPPORTED)
    assert result is None
    assert evidence.capability_assessment["supported"] is False
    # Raw (non-action-enriched) failure dicts travel beside the assessment.
    assert evidence.capability_failure_dicts
    assert all(isinstance(item, dict) for item in evidence.capability_failure_dicts)

    certificate = evaluate_evidence(evidence, _UNSUPPORTED.settings)
    assert certificate["accepted"] is False
    assert certificate["status"] == "rejected_physics"
    assert certificate["failures"] == evidence.capability_failure_dicts


def test_recorded_execution_error_short_circuits_to_rejection() -> None:
    evidence, _ = _collected(_ACCEPTED)
    error_evidence = dataclasses.replace(
        evidence,
        execution_error={
            "code": "unexpected_runtime_failure",
            "message": "synthetic",
            "retriable": False,
            "context": {},
            "actions": [],
        },
    )
    certificate = evaluate_evidence(error_evidence, _ACCEPTED.settings)
    assert certificate["accepted"] is False
    assert certificate["status"] == "rejected_physics"
    assert certificate["failures"][0]["message"] == "synthetic"


def test_default_settings_are_applied_when_none_are_given() -> None:
    evidence, _ = _collected(_ACCEPTED)
    assert evaluate_evidence(evidence, None) == evaluate_evidence(
        evidence, AcceptanceSettings()
    )


def test_evidence_to_dict_round_trips_json() -> None:
    import json

    from tmm_engine.hashing import canonical_json_bytes

    evidence, _ = _collected(_UNSUPPORTED)
    encoded = json.dumps(evidence.to_dict(), ensure_ascii=False, allow_nan=False)
    decoded = json.loads(encoded)
    # Tuples become lists across JSON, so the round-trip property is judged on
    # the canonical encoding, which is exactly how identities are derived.
    assert canonical_json_bytes(VerificationEvidence(**decoded).to_dict()) == (
        canonical_json_bytes(evidence.to_dict())
    )
