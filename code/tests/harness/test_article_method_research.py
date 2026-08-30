"""T-04 tests: method_research plus routing, cost metering, source_type."""

from __future__ import annotations

import json
from typing import Any

import pytest

import config.qwen_config as qwen_config
from optomind_optics.harness import problem_analyzer as pmod
from optomind_optics.harness.method_research import (
    MethodAllowedUse,
    MethodContentDepth,
    MethodEvidence,
    MethodFinding,
    MethodPurpose,
    MethodResearchQuery,
    MethodResearchReport,
    MethodResearchStatus,
    QwenMethodFindingSynthesizer,
)


def _evidence(**overrides: Any) -> MethodEvidence:
    base: dict[str, Any] = dict(
        evidence_id="e1",
        paper_id="p1",
        title="Coating study",
        doi="10.0000/coating",
        year=2020,
        source_route="s2",
        content_depth=MethodContentDepth.s2_snippet,
        text="Bounded passage about coating design.",
        query_ids=["q1"],
        allowed_use=MethodAllowedUse.method_guidance,
    )
    base.update(overrides)
    return MethodEvidence(**base)


def _query() -> MethodResearchQuery:
    return MethodResearchQuery(
        query_id="q1",
        query_text="antireflection coating design",
        purpose=MethodPurpose.design_family,
    )


class FakePlusClient:
    model_name = "qwen3.5-plus"

    def __init__(self, response: dict[str, Any]):
        self._response = response
        self.calls = 0

    def call(self, messages, *, max_tokens: int = 3000, force_mock=None):
        self.calls += 1
        return self._response


def test_method_research_uses_plus_model(monkeypatch):
    captured: dict = {}

    def fake_get_qwen_client(role):
        captured["role"] = role
        return object()  # never used for network traffic here

    monkeypatch.setattr(pmod, "get_qwen_client", fake_get_qwen_client)
    synthesizer = QwenMethodFindingSynthesizer()  # default construction
    assert captured["role"] == "plus"


def test_cost_recorded_after_qwen_call(monkeypatch):
    fresh_tracker = qwen_config.CostTracker()
    monkeypatch.setattr(qwen_config, "_COST_TRACKER", fresh_tracker)
    payload = {
        "method_findings": [
            {
                "method_name": "quarter-wave stack",
                "reusable_principle": "lambda/4 optical thickness layers",
                "evidence_ids": ["e1"],
                "applicability": "visible-band antireflection",
                "limitations": "narrow bandwidth only",
            }
        ]
    }
    client = FakePlusClient(
        {
            "content": json.dumps(payload),
            "_llm_usage": {"model_name": "qwen3.5-plus", "total_tokens": 55},
        }
    )
    synthesizer = QwenMethodFindingSynthesizer(client=client)
    findings = synthesizer([_evidence()], [_query()])

    assert findings and findings[0].method_name == "quarter-wave stack"
    assert client.calls == 1
    assert fresh_tracker.get_budget_snapshot().qwen_tokens.get("plus") == 55
    assert synthesizer.drain_usage()[0]["total_tokens"] == 55


def test_literature_refs_have_source_type():
    evidence = _evidence()
    assert evidence.source_type == "literature_fact"

    report = MethodResearchReport(
        problem_id="p-1",
        evidence=[evidence],
        method_findings=[
            MethodFinding(
                method_name="m",
                reusable_principle="p",
                evidence_ids=["e1"],
                applicability="a",
                limitations="l",
            )
        ],
        status=MethodResearchStatus.completed,
    )
    dumped = report.to_dict()
    assert dumped["evidence"], "report must carry its citation list"
    assert all(
        item["source_type"] == "literature_fact" for item in dumped["evidence"]
    )
