"""T-03 tests: problem_analyzer plus routing, Charter gate, cost metering."""

from __future__ import annotations

from typing import Any

import pytest

import config.qwen_config as qwen_config
from optomind_optics.harness import problem_analyzer as pmod
from optomind_optics.harness.problem_analyzer import (
    ARTICLE_PROBLEM_ANALYZER_MODEL,
    QwenTMMProblemAnalyzer,
    validate_research_charter,
)

FULL_CHARTER: dict[str, Any] = {
    "wavelength_range_nm": [450.0, 800.0],
    "angle_range_deg": [0.0, 30.0],
    "polarization": "unpolarized",
    "objectives": [{"type": "max_reflectivity", "target": None}],
    "material_whitelist": ["SiO2", "TiO2"],
    "layer_count_bounds": [2, 8],
}


class FakePlusClient:
    """Injectable fake declaring the article plus model."""

    model_name = ARTICLE_PROBLEM_ANALYZER_MODEL

    def __init__(self, responses: list[dict[str, Any]]):
        self._responses = list(responses)

    def call(self, messages, *, max_tokens: int = 4000, force_mock=None):
        return self._responses.pop(0)


def test_analyze_problem_uses_plus_model(monkeypatch):
    captured: dict = {}

    def fake_get_qwen_client(role):
        captured["role"] = role
        return object()  # never used for network traffic in this test

    monkeypatch.setattr(pmod, "get_qwen_client", fake_get_qwen_client)
    analyzer = QwenTMMProblemAnalyzer()  # default construction must route plus
    assert captured["role"] == "plus"
    assert analyzer._model_label == ARTICLE_PROBLEM_ANALYZER_MODEL


def test_charter_validation_passes():
    # Direct validator contract.
    validate_research_charter(FULL_CHARTER)  # must not raise
    # Entry-point integration: a complete charter lets analysis proceed past
    # the gate into the normal pipeline (fake LLM output is deliberately
    # unparseable, so the pipeline reports invalid WITHOUT any charter error).
    client = FakePlusClient(
        [
            {
                "content": "not json on purpose",
                "_llm_usage": {
                    "model_name": ARTICLE_PROBLEM_ANALYZER_MODEL,
                    "total_tokens": 7,
                },
            }
        ]
    )
    result = QwenTMMProblemAnalyzer(client=client, maximum_attempts=1).analyze(
        "Design an antireflection coating.", charter=FULL_CHARTER
    )
    assert result.status in {"analyzed", "invalid"}
    assert not any("CHARTER_FIELD_MISSING" in w for w in result.validation_warnings)


def test_charter_validation_missing_field():
    broken = {k: v for k, v in FULL_CHARTER.items() if k != "material_whitelist"}
    with pytest.raises(ValueError, match="CHARTER_FIELD_MISSING: material_whitelist"):
        validate_research_charter(broken)

    class ExplodingClient:
        model_name = ARTICLE_PROBLEM_ANALYZER_MODEL

        def call(self, *args, **kwargs):
            raise AssertionError("client must not be called when charter invalid")

    with pytest.raises(ValueError, match="CHARTER_FIELD_MISSING: material_whitelist"):
        QwenTMMProblemAnalyzer(client=ExplodingClient()).analyze(
            "any request", charter=broken
        )


def test_cost_recorded(monkeypatch):
    fresh_tracker = qwen_config.CostTracker()
    monkeypatch.setattr(qwen_config, "_COST_TRACKER", fresh_tracker)
    client = FakePlusClient(
        [
            {
                "content": "still not json",
                "_llm_usage": {
                    "model_name": ARTICLE_PROBLEM_ANALYZER_MODEL,
                    "total_tokens": 123,
                },
            }
        ]
    )
    QwenTMMProblemAnalyzer(client=client, maximum_attempts=1).analyze(
        "hello coating", charter=FULL_CHARTER
    )
    snapshot = fresh_tracker.get_budget_snapshot()
    assert snapshot.qwen_tokens.get("plus") == 123
