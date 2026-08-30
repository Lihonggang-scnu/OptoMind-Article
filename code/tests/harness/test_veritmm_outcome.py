"""R-03 tests: VeriTMM three-outcome classification.

Before R-03, all failures mapped to certified=False + tightest_margin=-1.0,
making engine crashes indistinguishable from physics rejection. The fix adds
``outcome`` with four values and ``is_route_eliminable()`` to enforce red line 7.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from optomind_optics.harness.veritmm_adapter import (
    VeriTMMAdapter,
    VeriTMMResult,
    bounded_run,
    is_route_eliminable,
)


@pytest.fixture
def mock_tracker():
    tracker = Mock()
    tracker.record_veritmm_usage = Mock()
    return tracker


@pytest.fixture
def adapter(mock_tracker, tmp_path):
    return VeriTMMAdapter(cost_tracker=mock_tracker)


def test_certified_maps_to_certified(adapter, tmp_path, monkeypatch):
    """Accepted certificate → outcome=certified."""
    output_dir = tmp_path / "run1"
    output_dir.mkdir()
    cert = {"accepted": True, "verification": {"passed": True}}
    (output_dir / "PHYSICS_ACCEPTANCE_CERTIFICATE.json").write_text(json.dumps(cert))

    def fake_execute(mode, payload, out_dir):
        return {"status": "success"}

    monkeypatch.setattr("optomind_optics.harness.veritmm_adapter._execute_veritmm", fake_execute)

    result = adapter.run_simulation({}, output_dir)
    assert result.outcome == "certified"
    assert result.certified is True


def test_rejected_certificate_maps_to_physics_rejected(adapter, tmp_path, monkeypatch):
    """Rejected certificate → outcome=physics_rejected."""
    output_dir = tmp_path / "run2"
    output_dir.mkdir()
    cert = {"accepted": False, "verification": {"passed": False, "reason": "violates_causality"}}
    (output_dir / "PHYSICS_ACCEPTANCE_CERTIFICATE.json").write_text(json.dumps(cert))

    def fake_execute(mode, payload, out_dir):
        return {"status": "success"}

    monkeypatch.setattr("optomind_optics.harness.veritmm_adapter._execute_veritmm", fake_execute)

    result = adapter.run_simulation({}, output_dir)
    assert result.outcome == "physics_rejected"
    assert result.certified is False


def test_exception_maps_to_engine_error(adapter, tmp_path, monkeypatch):
    """Engine crash → outcome=engine_error."""
    def fake_execute(mode, payload, out_dir):
        raise RuntimeError("segfault in tmm_core")

    monkeypatch.setattr("optomind_optics.harness.veritmm_adapter._execute_veritmm", fake_execute)

    result = adapter.run_simulation({}, tmp_path / "run3")
    assert result.outcome == "engine_error"
    assert result.certified is False
    assert "segfault" in result.raw_outputs.get("error", "")


def test_missing_certificate_maps_to_engine_error(adapter, tmp_path, monkeypatch):
    """No certificate file → outcome=engine_error."""
    def fake_execute(mode, payload, out_dir):
        return {"status": "success"}

    monkeypatch.setattr("optomind_optics.harness.veritmm_adapter._execute_veritmm", fake_execute)

    result = adapter.run_simulation({}, tmp_path / "run4")
    assert result.outcome == "engine_error"
    assert result.certified is False
    assert "was not produced" in result.raw_outputs.get("error", "")


def test_budget_gate_maps_to_budget_blocked(adapter, tmp_path):
    """Budget exhausted before execution → outcome=budget_blocked, cpu_seconds=0."""
    budget = Mock()
    budget.veritmm_cpu_seconds = 100.0
    budget.tmm_cpu_seconds = None

    result = bounded_run(adapter, {}, tmp_path / "run5", budget, max_cpu_seconds=50.0)
    assert result.outcome == "budget_blocked"
    assert result.certified is False
    assert result.cpu_seconds == 0.0
    assert result.raw_outputs["budget_gate"]["blocked"] is True


def test_is_route_eliminable_only_for_physics_rejected():
    """Red line 7: only physics_rejected eliminates a route."""
    r_certified = VeriTMMResult(
        certificate_path=Path("c"), certified=True, tightest_margin=0.1, outcome="certified"
    )
    r_physics = VeriTMMResult(
        certificate_path=Path("p"), certified=False, tightest_margin=-1.0, outcome="physics_rejected"
    )
    r_engine = VeriTMMResult(
        certificate_path=Path("e"), certified=False, tightest_margin=-1.0, outcome="engine_error"
    )
    r_budget = VeriTMMResult(
        certificate_path=Path("b"), certified=False, tightest_margin=-1.0, outcome="budget_blocked"
    )

    assert not is_route_eliminable(r_certified)
    assert is_route_eliminable(r_physics)
    assert not is_route_eliminable(r_engine)
    assert not is_route_eliminable(r_budget)


def test_budget_gate_does_not_call_execute(adapter, tmp_path, monkeypatch):
    """Budget gate must not invoke _execute_veritmm."""
    called = []

    def fake_execute(mode, payload, out_dir):
        called.append(True)
        return {}

    monkeypatch.setattr("optomind_optics.harness.veritmm_adapter._execute_veritmm", fake_execute)

    budget = Mock()
    budget.veritmm_cpu_seconds = 100.0
    budget.tmm_cpu_seconds = None
    result = bounded_run(adapter, {}, tmp_path / "run6", budget, max_cpu_seconds=50.0)

    assert result.outcome == "budget_blocked"
    assert not called
