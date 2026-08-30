"""T-15 tests: translation routing, number verification, silent skip."""

from __future__ import annotations

import pytest

from optomind_optics.harness import translation_module as tm
from optomind_optics.harness.provenance_compiler import ProvenanceLedger
from optomind_optics.harness.translation_module import (
    TranslationResult,
    translate_sections,
)


class ScriptedPlusClient:
    model_name = "qwen3.5-plus"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def call(self, messages, *, max_tokens=4000, force_mock=None):
        self.calls.append(messages)
        if isinstance(self.replies[0], Exception):
            raise self.replies.pop(0)
        content = self.replies.pop(0)
        return {"content": content, "_llm_usage": {"total_tokens": 20}}


def _ledger():
    return ProvenanceLedger()


def test_numerical_values_consistent():
    client = ScriptedPlusClient(["厚度为 1.5 的涂层表现稳定。"])
    result = translate_sections(
        "The coating thickness is 1.5.", "", ledger=_ledger(), client=client
    )
    assert isinstance(result, TranslationResult)
    assert result.skipped is False
    assert result.abstract_zh == "厚度为 1.5 的涂层表现稳定。"
    assert result.mismatch_values == []


def test_numerical_mismatch_detected():
    client = ScriptedPlusClient(["厚度变为 1.8，其余一致。"])
    with pytest.warns(UserWarning, match="TranslationNumberMismatchWarning"):
        result = translate_sections(
            "The coating thickness is 1.5.", "", ledger=_ledger(), client=client
        )
    assert result.skipped is False
    assert result.mismatch_values
    assert any("1.5" in entry for entry in result.mismatch_values)
    assert any("1.8" in entry for entry in result.mismatch_values)


def test_trailing_zero_normalization():
    client = ScriptedPlusClient(["带宽为 32 nm。"])
    result = translate_sections(
        "The bandwidth is 32.0 nm.", "", ledger=_ledger(), client=client
    )
    assert result.mismatch_values == []


def test_material_name_lookup(monkeypatch):
    monkeypatch.setattr(
        tm, "MATERIAL_DISPLAY_NAMES", {"tio2": "TiO2"}
    )
    client = ScriptedPlusClient(["该 tio2 层厚度均匀。"])
    result = translate_sections(
        "The TiO2 layer is uniform.", "", ledger=_ledger(), client=client
    )
    assert "TiO2" in result.abstract_zh
    assert "tio2" not in result.abstract_zh


def test_translation_failure_skipped():
    client = ScriptedPlusClient([TimeoutError("api timeout")])
    with pytest.warns(UserWarning, match="TranslationSkippedWarning"):
        result = translate_sections(
            "Abstract text.", "Conclusion text.",
            ledger=_ledger(), client=client,
        )
    assert result.skipped is True
    assert result.abstract_zh is None
    assert result.conclusion_zh is None
    assert result.mismatch_values == []


def test_cost_recorded():
    client = ScriptedPlusClient(["摘要包含 2 项指标。", "结论覆盖 3 条路线。"])
    tracker = tm.get_cost_tracker()
    before = tracker.get_budget_snapshot().qwen_tokens.get("plus", 0)
    translate_sections(
        "Abstract 2.", "Conclusion 3.", ledger=_ledger(), client=client
    )
    after = tracker.get_budget_snapshot().qwen_tokens.get("plus", 0)
    assert after - before == 40
