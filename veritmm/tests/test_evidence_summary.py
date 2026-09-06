"""Golden consistency for the evidence summary helper.

For the same managed run, ``build_evidence_summary`` on the persisted
certificate must reproduce the ``evidence_coverage`` ledger embedded in
``RESULT_SUMMARY.json`` field by field — both input forms (file path and
parsed dict) — because both derive from the single authoritative
``EvidenceCoverage.from_certificate`` definition.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tmm_engine import (
    IlluminationSpec,
    LayerSpec,
    MediumSpec,
    SimulationTask,
    SpectralGrid,
    StackSpec,
)
from tmm_engine.execution import ExecutionSettings
from tmm_engine.managed_execution import execute_managed_task
from tmm_engine.protocol.evidence import (
    EVIDENCE_SUMMARY_SCHEMA_VERSION,
    build_evidence_summary,
)


@pytest.fixture(scope="module")
def managed_run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    task = SimulationTask(
        stack=StackSpec(
            layers=(
                LayerSpec(None, 91.0, constant_n=2.15),
                LayerSpec(None, 137.0, constant_n=1.43),
            )
            * 2,
            incident=MediumSpec.air(),
            exit=MediumSpec(constant_n=1.52),
        ),
        spectrum=SpectralGrid(550.0, 650.0, 21),
        illumination=IlluminationSpec((0.0,), ("s",)),
    )
    root = tmp_path_factory.mktemp("managed_run")
    execute_managed_task(
        "simulate",
        task,
        root / "run",
        execution_settings=ExecutionSettings(),
    )
    return root / "run"


def test_summary_matches_result_summary_field_by_field(
    managed_run: Path,
) -> None:
    certificate = json.loads(
        (managed_run / "PHYSICS_ACCEPTANCE_CERTIFICATE.json").read_text(
            encoding="utf-8"
        )
    )
    result_summary = json.loads(
        (managed_run / "RESULT_SUMMARY.json").read_text(encoding="utf-8")
    )

    summary = build_evidence_summary(managed_run / "PHYSICS_ACCEPTANCE_CERTIFICATE.json")

    assert summary["schema_version"] == EVIDENCE_SUMMARY_SCHEMA_VERSION
    assert summary["certificate_id"] == certificate["certificate_id"]
    assert summary["accepted"] == certificate["accepted"]
    assert summary["status"] == certificate["status"]
    assert (
        summary["evidence_coverage"] == result_summary["evidence_coverage"]
    ), "the helper and RESULT_SUMMARY must agree field by field"


def test_path_and_dict_input_forms_are_identical(managed_run: Path) -> None:
    certificate_path = managed_run / "PHYSICS_ACCEPTANCE_CERTIFICATE.json"
    certificate = json.loads(certificate_path.read_text(encoding="utf-8"))

    from_path = build_evidence_summary(certificate_path)
    from_dict = build_evidence_summary(certificate)

    assert from_path == from_dict


def test_rejected_certificate_reports_failed_capability(
    managed_run: Path,
) -> None:
    certificate = json.loads(
        (managed_run / "PHYSICS_ACCEPTANCE_CERTIFICATE.json").read_text(
            encoding="utf-8"
        )
    )
    certificate["accepted"] = False
    certificate["status"] = "rejected_physics"
    certificate["capability_assessment"]["supported"] = False

    summary = build_evidence_summary(certificate)

    assert summary["accepted"] is False
    assert summary["status"] == "rejected_physics"
    assert summary["evidence_coverage"]["capability_domain"] == "failed"


def test_invalid_source_type_is_typed() -> None:
    with pytest.raises(ValueError):
        build_evidence_summary(12345)  # type: ignore[arg-type]
