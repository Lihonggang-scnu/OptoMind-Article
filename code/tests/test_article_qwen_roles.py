"""T-00 tests: article role routing + RunBudget/CostTracker (mock only, no network)."""

from __future__ import annotations

import datetime as _dt

import openai
import pytest

from config import qwen_config
from config.qwen_config import (
    CostTracker,
    RunBudgetSnapshot,
    get_cost_tracker,
    get_qwen_client,
)


def _capture_config(monkeypatch):
    """Patch get_qwen_client_config and record the requested tier."""
    captured: dict = {}

    def fake_config(model_tier_or_agent=None):
        captured["tier"] = model_tier_or_agent
        return {
            "api_key": "test-key",
            "base_url": qwen_config.DASHSCOPE_COMPATIBLE_BASE_URL,
        }

    monkeypatch.setattr(qwen_config, "get_qwen_client_config", fake_config)
    return captured


def test_get_qwen_client_plus(monkeypatch):
    captured = _capture_config(monkeypatch)
    client = get_qwen_client("plus")
    assert isinstance(client, openai.OpenAI)
    assert "dashscope" in str(client.base_url)
    # plus -> c_model tier (qwen3.5-plus per model_policy.yaml aliases)
    assert captured["tier"] == "c_model"


def test_get_qwen_client_turbo(monkeypatch):
    captured = _capture_config(monkeypatch)
    client = get_qwen_client("turbo")
    assert isinstance(client, openai.OpenAI)
    assert "dashscope" in str(client.base_url)
    # turbo -> advanced_model tier (qwen3.7-flash per model_policy.yaml aliases)
    assert captured["tier"] == "advanced_model"


def test_get_qwen_client_invalid_role():
    with pytest.raises(ValueError):
        get_qwen_client("gpt4")


def test_cost_tracker_record_and_snapshot():
    tracker = CostTracker()
    tracker.record_qwen_usage("plus", 100)
    tracker.record_qwen_usage("plus", 50)
    snapshot = tracker.get_budget_snapshot()
    assert isinstance(snapshot, RunBudgetSnapshot)
    assert snapshot.qwen_tokens["plus"] == 150

    tracker.record_tmm_usage(3.5)
    snapshot_after_tmm = tracker.get_budget_snapshot()
    assert snapshot_after_tmm.tmm_cpu_seconds == 3.5

    # timestamp must be ISO 8601 parseable and timezone-aware (UTC)
    parsed = _dt.datetime.fromisoformat(snapshot.timestamp)
    assert parsed.tzinfo is not None


def test_get_cost_tracker_singleton():
    first = get_cost_tracker()
    second = get_cost_tracker()
    assert first is second
    assert id(first) == id(second)
