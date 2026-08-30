"""T-10 tests: provenance compiler v0.8 Scalability Contract."""

from __future__ import annotations

import json

import pytest

from optomind_optics.harness import experiment_store as experiment_store_module
from optomind_optics.harness.experiment_store import ExperimentStore
from optomind_optics.harness.provenance_compiler import (
    Claim,
    MissingCertificateIdError,
    MissingRefIdError,
    ProvenanceEntry,
    ProvenanceLedger,
    ScalabilityViolationError,
    UnacceptedCertificateError,
    build_literature_entries,
    build_simulation_entries,
    compile,
    compute_token_id,
    find_or_create_token,
)

CHARTER = {
    "wavelength_range_nm": [450.0, 700.0],
    "angle_range_deg": [0.0, 30.0],
    "polarization": "unpolarized",
    "objectives": [{"name": "reflectivity", "weight": 1.0}],
    "material_whitelist": ["SiO2", "TiO2"],
    "layer_count_bounds": {"min": 1, "max": 8},
}

SCALARS = {
    "R_avg_450_700nm": 0.41,
    "bandwidth_nm": 32.0,
    "peak_wavelength_nm": 550.0,
    "worst_angle_deg": 28.0,
    "objective_score": 0.72,
}


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment_store_module, "BASE_DIR", tmp_path)
    return ExperimentStore("prob-1", "run-1")


def _write_run_result(store, route_id, extra=None):
    directory = store.ensure_round_dir(1, route_id)
    summary = {"accepted": True, "certificate_id": f"cert-{route_id}", **SCALARS}
    if extra:
        summary.update(extra)
    (directory / "RUN_RESULT.json").write_text(
        json.dumps(summary), encoding="utf-8"
    )


def _entry(quantity="R_avg_450_700nm", token_id=""):
    return ProvenanceEntry(
        token_id=token_id,
        source_type="simulation_fact",
        quantity_name=quantity,
        value=0.42,
        scope="broadband 450-700nm unpolarized 0-30deg",
        human_readable="R_avg = 0.42",
        certificate_id="cert-1",
        route_id="route_A",
        round=1,
    )


def test_simulation_fact_requires_certificate_id():
    with pytest.raises(UnacceptedCertificateError):
        build_simulation_entries(
            {"accepted": False, "certificate_id": "cert-x"},
            route_id="route_A",
            round_k=1,
            scope="s",
            source_artifact_hash="h",
        )


def test_simulation_fact_missing_cert_id():
    with pytest.raises(MissingCertificateIdError):
        build_simulation_entries(
            {"accepted": True},
            route_id="route_A",
            round_k=1,
            scope="s",
            source_artifact_hash="h",
        )


def test_literature_fact_missing_ref_id():
    with pytest.raises(MissingRefIdError):
        build_literature_entries(
            [
                {
                    "source_locator": "Table 2, row 3",
                    "source_text": "quoted material response",
                    "extraction_method": "manual",
                }
            ]
        )


def test_spectral_array_rejected():
    ledger = ProvenanceLedger()
    entry = _entry()
    entry.value = list(range(11))
    with pytest.raises(ScalabilityViolationError):
        ledger.add(entry)
    with pytest.raises(ScalabilityViolationError):
        find_or_create_token(ledger, "tok-spectral", entry)


def test_literature_fact_missing_locator_degrades():
    with pytest.warns(UserWarning, match="UNVERIFIED_LIT_FACT_WARNING"):
        entries = build_literature_entries(
            [
                {
                    "ref_id": "doi:10.1234/example",
                    "source_text": "long enough quoted passage for hashing",
                    "extraction_method": "manual",
                }
            ]
        )
    assert entries[0].source_type == "literature_fact_unverified"


def test_find_or_create_token_dedup():
    ledger = ProvenanceLedger()
    first = find_or_create_token(ledger, "abc123def456", _entry())
    second = find_or_create_token(ledger, "abc123def456", _entry())
    assert first is second
    assert len(ledger.entries) == 1


def test_token_id_deterministic():
    args = ("hash-1", "R_avg_450_700nm", "broadband", "route_A", 1)
    assert compute_token_id(*args) == compute_token_id(*args)
    changed = ("hash-1", "bandwidth_nm", "broadband", "route_A", 1)
    assert compute_token_id(*args) != compute_token_id(*changed)


def test_token_count_warning():
    ledger = ProvenanceLedger()
    with pytest.warns(UserWarning, match="SCALABILITY_RED_LINE_WARNING"):
        for index in range(201):
            find_or_create_token(
                ledger,
                f"tok{index:04d}",
                _entry(quantity=f"q_{index}"),
            )
    assert len(ledger.entries) == 201


def test_compile_populates_ledger(store):
    for route_id in ("route_A", "route_B", "route_C"):
        _write_run_result(store, route_id)
    candidates = [
        {"route_id": f"route_{letter}", "round_k": 1}
        for letter in "ABC"
    ]
    ledger, claims = compile(candidates, store, CHARTER, evidence_bundle=None)
    simulation_tokens = [
        e for e in ledger.entries if e.source_type == "simulation_fact"
    ]
    assert len(simulation_tokens) == 15
    assert len(ledger.entries) >= 15


def test_append_only(store):
    _write_run_result(store, "route_A")
    ledger1, _ = compile([{"route_id": "route_A", "round_k": 1}], store, CHARTER)
    snapshot = {
        e.token_id: e.to_dict() for e in ledger1.entries
    }
    prov_file = store.global_artifact("facts") / "provenance_ledger.json"
    saved_first = json.loads(prov_file.read_text(encoding="utf-8"))

    ledger2, _ = compile([{"route_id": "route_A", "round_k": 1}], store, CHARTER)
    saved_second = json.loads(prov_file.read_text(encoding="utf-8"))
    second_ids = {e["token_id"] for e in saved_second["entries"]}
    assert set(snapshot) <= second_ids
    for token_id, payload in snapshot.items():
        restored = ledger2.get(token_id)
        assert restored is not None
        assert restored.to_dict()["value"] == payload["value"]
    assert len(saved_second["entries"]) == len(saved_first["entries"])


def test_claim_ledger_generated(store):
    for letter in "AB":
        _write_run_result(store, f"route_{letter}")
    _, claims = compile(
        [{"route_id": f"route_{letter}", "round_k": 1} for letter in "AB"],
        store,
        CHARTER,
    )
    assert len(claims.claims) >= 1


def test_claim_granularity(store):
    for letter in "ABC":
        _write_run_result(store, f"route_{letter}")
    _, claims = compile(
        [{"route_id": f"route_{letter}", "round_k": 1} for letter in "ABC"],
        store,
        CHARTER,
    )
    assert claims.claims
    for claim in claims.claims:
        assert len(claim.statement.strip()) > 20


def test_claim_rejects_single_number_statement():
    with pytest.raises(ValueError, match="SC-5"):
        Claim(
            claim_id="clm_bad",
            claim_type="descriptive",
            statement="R = 0.42.",
        )
