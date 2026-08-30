"""T-13 tests: fact-token verification, claim coverage, prose hygiene."""

from __future__ import annotations

import pytest

from optomind_research.runtime.publication_integrity import (
    CoverageReport,
    IntegrityReport,
    check_forbidden_prose_and_refs,
    verify_claim_coverage,
    verify_fact_tokens,
)
from optomind_optics.harness.provenance_compiler import (
    Claim,
    ClaimLedger,
    ProvenanceEntry,
    ProvenanceLedger,
)


def _entry(token_id, value=0.42):
    return ProvenanceEntry(
        token_id=token_id,
        source_type="simulation_fact",
        quantity_name=f"q_{token_id}",
        value=value,
        scope="broadband 450-700nm",
        human_readable=f"{token_id} = {value}",
        certificate_id="cert-1",
        route_id="route_A",
        round=1,
    )


def _claim(claim_id="CLAIM_001"):
    return Claim(
        claim_id=claim_id,
        claim_type="comparison",
        statement=(
            "Route A sustains a wider certified bandwidth than route B "
            "under identical charter bounds."
        ),
        support_token_ids=["SIM_001"],
        support_ref_ids=[],
    )


def test_fact_token_mismatch():
    ledger = ProvenanceLedger()
    ledger.add(_entry("SIM_001"))
    report = verify_fact_tokens(
        "The margin is {{FACT:UNKNOWN}} here.", ledger
    )
    assert isinstance(report, IntegrityReport)
    assert not report.passed
    assert any("FACT_TOKEN_MISMATCH_ERROR" in m for m in report.mismatches)
    assert any("UNKNOWN" in m for m in report.mismatches)


def test_fact_token_valid():
    ledger = ProvenanceLedger()
    ledger.add(_entry("SIM_001", 0.87))
    report = verify_fact_tokens(
        "The certified margin is {{FACT:SIM_001}}.", ledger
    )
    assert report.passed
    assert report.mismatches == []
    assert report.warnings == []


def test_claim_coverage_unsupported():
    claims = ClaimLedger()
    claims.add(_claim())
    md = (
        "Route A shows higher transmission compared to route B because the "
        "thinner stack reduces absorption."
    )
    report = verify_claim_coverage(md, claims)
    assert isinstance(report, CoverageReport)
    assert not report.passed
    assert report.unsupported_count >= 1
    assert any("UNSUPPORTED_CLAIM_WARNING" in w for w in report.warnings)


def test_claim_coverage_supported():
    claims = ClaimLedger()
    claims.add(_claim("CLAIM_001"))
    md = (
        "Route A shows higher transmission compared to route B "
        "{{CLAIM:CLAIM_001}}."
    )
    report = verify_claim_coverage(md, claims)
    assert report.passed
    assert report.unsupported_count == 0


def test_forbidden_prose_detected():
    violations = check_forbidden_prose_and_refs(
        "This revolutionary coating beats state-of-the-art results [REF:a1]."
    )
    joined = "|".join(violations)
    assert "FORBIDDEN_PROSE: 'revolutionary'" in joined
    assert "FORBIDDEN_PROSE: 'state-of-the-art'" in joined
    assert any(v.startswith("UNRESOLVED_REF") and "[REF:a1]" in v for v in violations)


def test_cjk_trigger_words_covered():
    claims = ClaimLedger()
    claims.add(_claim())
    md = "与基准相比，该多层结构的吸收更低。"
    report = verify_claim_coverage(md, claims)
    assert not report.passed
    assert report.unsupported_count >= 1
