"""T-12 tests: routing split, reviewer independence, structured findings."""

from __future__ import annotations

import inspect
import json

import pytest

from optomind_research.runtime import article_writing_review as awr
from optomind_research.runtime.article_writing_review import (
    ArticleReviewReport,
    ReviewFinding,
    review_article,
)
from optomind_optics.harness.provenance_compiler import (
    ClaimLedger,
    ProvenanceEntry,
    ProvenanceLedger,
)

CHARTER = {
    "wavelength_range_nm": [450.0, 700.0],
    "angle_range_deg": [0.0, 30.0],
    "polarization": "unpolarized",
    "objectives": [{"name": "reflectivity", "weight": 1.0}],
    "material_whitelist": ["SiO2", "TiO2"],
    "layer_count_bounds": {"min": 1, "max": 8},
}

ARTICLE_MD = "# Sample Article\\n\\nCertified margins are anchored via FACT placeholders."

RESPONSE_PAYLOAD = {
    "findings": [
        {
            "severity": "major",
            "section": "Results",
            "description": "Bandwidth comparison lacks an explicit uncertainty band.",
            "suggestion": "Add the propagated uncertainty next to {{FACT:bnd}}.",
            "related_claim_id": None,
        },
        {
            "severity": "suggestion",
            "section": "Abstract",
            "description": "Lead sentence could state the certified margin.",
            "suggestion": "Quote tightest_margin directly.",
            "related_claim_id": None,
        },
    ],
    "overall_verdict": "major_revision",
}


class ScriptedTurboClient:
    model_name = "qwen3.7-flash"

    def __init__(self):
        self.calls = []

    def call(self, messages, *, max_tokens=4000, force_mock=None):
        self.calls.append(messages)
        return {
            "content": json.dumps(RESPONSE_PAYLOAD),
            "_llm_usage": {"model_name": "qwen3.7-flash", "total_tokens": 55},
        }


@pytest.fixture()
def scripted_turbo(monkeypatch):
    client = ScriptedTurboClient()
    monkeypatch.setattr(awr, "REVIEW_CLIENT_FACTORY", lambda: client)
    return client


def _sample_ledgers():
    ledger = ProvenanceLedger()
    ledger.add(
        ProvenanceEntry(
            token_id="tok1",
            source_type="simulation_fact",
            quantity_name="R_avg_450_700nm",
            value=0.41,
            scope="broadband",
            human_readable="R_avg = 0.41",
            certificate_id="cert-1",
            route_id="route_A",
            round=1,
        )
    )
    return ledger, ClaimLedger()


def test_review_uses_turbo_model(scripted_turbo):
    ledger, claims = _sample_ledgers()
    report = review_article(ARTICLE_MD, ledger, claims, CHARTER)
    assert len(scripted_turbo.calls) == 1
    assert report.reviewer_model == "qwen3.7-flash"


def test_review_output_is_structured(scripted_turbo):
    ledger, claims = _sample_ledgers()
    report = review_article(ARTICLE_MD, ledger, claims, CHARTER)
    assert isinstance(report, ArticleReviewReport)
    assert not isinstance(report, str)
    assert all(isinstance(f, ReviewFinding) for f in report.findings)
    assert report.overall_verdict in ("accept", "major_revision", "reject")


def test_reviewer_independence():
    params = set(inspect.signature(review_article).parameters)
    assert params == {
        "article_markdown",
        "provenance_ledger",
        "claim_ledger",
        "charter",
    }
    forbidden = {"messages", "history", "writer_log", "cot", "reasoning"}
    assert not (params & forbidden)


def test_review_finding_fields(scripted_turbo):
    ledger, claims = _sample_ledgers()
    report = review_article(ARTICLE_MD, ledger, claims, CHARTER)
    assert report.findings
    required = {
        "finding_id",
        "severity",
        "section",
        "description",
        "suggestion",
        "related_claim_id",
    }
    for finding in report.findings:
        data = finding.to_dict()
        assert required <= set(data)
        assert finding.severity in ("critical", "major", "minor", "suggestion")


def test_cost_recorded_turbo(scripted_turbo):
    tracker = awr.get_cost_tracker()
    before = tracker.get_budget_snapshot().qwen_tokens.get("turbo", 0)
    ledger, claims = _sample_ledgers()
    review_article(ARTICLE_MD, ledger, claims, CHARTER)
    after = tracker.get_budget_snapshot().qwen_tokens.get("turbo", 0)
    assert after - before == 55


def test_writing_completion_records_plus(monkeypatch):
    class ScriptedPlusClient:
        model_name = "qwen3.5-plus"

        def call(self, messages, *, max_tokens=4000, force_mock=None):
            return {"content": "prose", "_llm_usage": {"total_tokens": 31}}

    monkeypatch.setattr(awr, "PLUS_CLIENT_FACTORY", lambda: ScriptedPlusClient())
    tracker = awr.get_cost_tracker()
    before = tracker.get_budget_snapshot().qwen_tokens.get("plus", 0)
    response = awr.run_writing_completion(
        [{"role": "user", "content": "draft the abstract"}]
    )
    after = tracker.get_budget_snapshot().qwen_tokens.get("plus", 0)
    assert response["content"] == "prose"
    assert after - before == 31
