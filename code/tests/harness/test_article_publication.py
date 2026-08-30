"""T-11 tests: select_evidence subsets, injectors, integrity scanning."""

from __future__ import annotations

import json

import pytest

from optomind_optics.harness.article_publication import (
    ARTICLE_EVIDENCE_TOKEN_LIMIT,
    EvidenceOverflowError,
    FactTokenNotFoundWarning,
    IntegrityViolation,
    UnsupportedClaimError,
    UnverifiedInResultsError,
    inject_claim_tokens,
    inject_fact_tokens,
    scan_integrity,
    select_evidence,
    write_section_draft,
)
from optomind_optics.harness.provenance_compiler import (
    Claim,
    ClaimLedger,
    ProvenanceEntry,
    ProvenanceLedger,
)


def _entry(
    token_id,
    source_type="simulation_fact",
    *,
    value=0.42,
    unit=None,
    route_id="route_A",
    quantity=None,
):
    return ProvenanceEntry(
        token_id=token_id,
        source_type=source_type,
        quantity_name=quantity or f"q_{token_id}",
        value=value,
        scope="broadband 450-700nm unpolarized 0deg",
        human_readable=f"{quantity or token_id} = {value}",
        unit=unit,
        route_id=route_id,
        round=1,
        certificate_id="cert-1" if source_type == "simulation_fact" else None,
        ref_id=f"ref-{token_id}" if source_type.startswith("literature") else None,
    )


def _claim(claim_id, claim_type="comparison", with_support=True):
    return Claim(
        claim_id=claim_id,
        claim_type=claim_type,
        statement=(
            f"Route A sustains a clearly wider certified bandwidth than route B "
            f"across the shared window ({claim_id})."
        ),
        support_token_ids=["t1"] if with_support else [],
        support_ref_ids=["ref-1"] if with_support else [],
    )


def test_select_evidence_introduction():
    ledger = ProvenanceLedger()
    ledger.add(_entry("lit1", "literature_fact", route_id=None))
    ledger.add(_entry("uc1", "user_constraint", route_id=None))
    ledger.add(_entry("sim1", "simulation_fact"))
    ledger.add(_entry("bad1", "literature_fact_unverified", route_id=None))
    subset = select_evidence("introduction", ledger, ClaimLedger())
    kinds = {e.source_type for e in subset.tokens}
    assert kinds == {"literature_fact", "user_constraint"}
    assert all(e.source_type != "simulation_fact" for e in subset.tokens)
    assert all(e.source_type != "literature_fact_unverified" for e in subset.tokens)
    assert subset.claims == []


def test_select_evidence_results():
    ledger = ProvenanceLedger()
    for letter, route in (("a", "route_A"), ("b", "route_B")):
        ledger.add(_entry(f"sims{letter}", "simulation_fact", route_id=route))
    ledger.add(_entry("ucX", "user_constraint", route_id=None))
    claims = ClaimLedger()
    claims.add(_claim("clm_cmp", "comparison"))
    claims.add(_claim("clm_trd", "trend"))
    claims.add(_claim("clm_rob", "robustness"))

    subset = select_evidence("results", ledger, claims, route_ids=["route_A"])
    assert {e.token_id for e in subset.tokens} == {"simsa"}
    assert {c.claim_id for c in subset.claims} == {"clm_cmp", "clm_rob"}
    assert all(e.source_type == "simulation_fact" for e in subset.tokens)


def test_select_evidence_overflow():
    ledger = ProvenanceLedger()
    for index in range(ARTICLE_EVIDENCE_TOKEN_LIMIT + 1):
        ledger.add(
            _entry(f"uc{index:03d}", "user_constraint", route_id=None)
        )
    with pytest.raises(EvidenceOverflowError, match="EVIDENCE_OVERFLOW"):
        select_evidence("introduction", ledger, ClaimLedger())


def test_unverified_in_results_blocked():
    ledger = ProvenanceLedger()
    ledger.add(_entry("simA", "simulation_fact"))
    ledger.add(_entry("uvm", "literature_fact_unverified", route_id=None))
    with pytest.raises(UnverifiedInResultsError, match="token_id=uvm"):
        select_evidence("results", ledger, ClaimLedger())


def test_inject_fact_tokens_replaces():
    ledger = ProvenanceLedger()
    ledger.add(_entry("t1", value=0.42))
    ledger.add(_entry("t2", value=32.0, unit="nm"))
    out = inject_fact_tokens("R={{FACT:t1}}, B={{FACT:t2}}.", ledger)
    assert out == "R=0.42, B=32.0 nm."


def test_inject_fact_tokens_missing():
    ledger = ProvenanceLedger()
    with pytest.warns(FactTokenNotFoundWarning):
        out = inject_fact_tokens("value {{FACT:nope}} kept", ledger)
    assert "{{FACT:nope}}" in out


def test_inject_claim_tokens():
    claims = ClaimLedger()
    claim = _claim("clm_ok")
    claims.add(claim)
    out = inject_claim_tokens("As shown, {{CLAIM:clm_ok}}.", claims)
    assert claim.statement in out


def test_inject_claim_no_evidence():
    claims = ClaimLedger()
    claims.add(_claim("clm_empty", with_support=False))
    with pytest.raises(UnsupportedClaimError):
        inject_claim_tokens("{{CLAIM:clm_empty}}", claims)
    with pytest.raises(UnsupportedClaimError):
        inject_claim_tokens("{{CLAIM:ghost}}", claims)


def test_scan_integrity_bare_number():
    md = (
        "# Results\n"
        "| col | 3.14 |\n"
        "The certified margin reaches 0.87 in this window.\n"
        "Anchored value {{FACT:t1}} stays compliant.\n"
    )
    violations = scan_integrity(md, ProvenanceLedger(), ClaimLedger())
    kinds = [v.kind for v in violations]
    assert kinds == ["bare_number"]
    assert isinstance(violations[0], IntegrityViolation)
    assert violations[0].line_no == 3


def test_scan_integrity_bare_comparison():
    md = (
        "Route A outperforms route B on every metric.\n"
        "See {{CLAIM:clm_cmp}} for the supported comparison.\n"
    )
    violations = scan_integrity(md, ProvenanceLedger(), ClaimLedger())
    assert [v.kind for v in violations] == ["unsupported_comparison"]


def test_full_ledger_inject_prohibited():
    ledger = ProvenanceLedger()
    for index in range(25):
        ledger.add(_entry(f"s{index:03d}", "simulation_fact"))
    ledger.add(
        _entry(
            "secret",
            "simulation_fact",
            route_id="route_Z",
            quantity="SECRET_CANARY",
        )
    )

    class ScriptedClient:
        model_name = "qwen3.5-plus"

        def __init__(self):
            self.calls = []

        def call(self, messages, *, max_tokens=4000, force_mock=None):
            self.calls.append(messages)
            return {"content": "draft text", "_llm_usage": {"total_tokens": 77}}

    client = ScriptedClient()
    result = write_section_draft(
        "results",
        ledger,
        ClaimLedger(),
        route_ids=["route_A"],
        evidence_summary="summary",
        draft_template="write about it",
        client=client,
    )
    assert len(client.calls) == 1
    user_payload = json.loads(client.calls[0][1]["content"])
    assert len(user_payload["evidence_tokens"]) <= ARTICLE_EVIDENCE_TOKEN_LIMIT
    assert len(user_payload["evidence_tokens"]) == 25
    serialized = json.dumps(user_payload, ensure_ascii=False)
    assert "SECRET_CANARY" not in serialized
    assert result["subset"].tokens
