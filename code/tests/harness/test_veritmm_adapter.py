"""T-01 tests: VeriTMMAdapter (all VeriTMM interactions mocked, no real sims)."""

from __future__ import annotations

import json

import pytest

from optomind_optics.harness import veritmm_adapter as vmod
from optomind_optics.harness.veritmm_adapter import (
    CERTIFICATE_FILENAME,
    VeriTMMAdapter,
    VeriTMMResult,
    bounded_run,
)


class FakeTracker:
    def __init__(self):
        self.cpu_calls: list[float] = []

    def record_tmm_usage(self, cpu_seconds: float) -> None:
        self.cpu_calls.append(cpu_seconds)


def _write_certificate(output_dir, *, accepted: bool, normalized_margin: float):
    payload = {
        "accepted": accepted,
        "status": "physically_valid" if accepted else "rejected_physics",
        "tightest_margin": {"check": "energy_conservation", "normalized_margin": normalized_margin},
    }
    path = output_dir / CERTIFICATE_FILENAME
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _patch_engine(monkeypatch, behavior):
    """Patch the real engine seam; behavior(mode, payload, output_dir)->dict."""

    def fake_execute(mode, payload, output_dir):
        return behavior(mode, payload, output_dir)

    monkeypatch.setattr(vmod, "_execute_veritmm", fake_execute)


def test_run_simulation_certified(tmp_path, monkeypatch):
    def behavior(mode, payload, output_dir):
        assert mode == "simulate"
        _write_certificate(output_dir, accepted=True, normalized_margin=0.42)
        return {"run_id": "run_abc"}

    _patch_engine(monkeypatch, behavior)
    adapter = VeriTMMAdapter(FakeTracker())
    result = adapter.run_simulation({"mode": "simulate", "task": {"stack": {}}}, tmp_path)

    assert isinstance(result, VeriTMMResult)
    assert result.certified is True
    assert result.tightest_margin == 0.42
    assert result.certificate_path.name == CERTIFICATE_FILENAME
    assert result.certificate_path.is_file()
    assert result.raw_outputs["envelope"]["run_id"] == "run_abc"
    assert result.raw_outputs[CERTIFICATE_FILENAME]["accepted"] is True


def test_run_simulation_not_certified(tmp_path, monkeypatch):
    def behavior(mode, payload, output_dir):
        _write_certificate(output_dir, accepted=False, normalized_margin=-0.05)
        return {"run_id": "run_bad"}

    _patch_engine(monkeypatch, behavior)
    result = VeriTMMAdapter(FakeTracker()).run_simulation({"mode": "simulate"}, tmp_path)

    assert result.certified is False
    assert result.tightest_margin == -0.05


def test_run_simulation_records_cost(tmp_path, monkeypatch):
    def behavior(mode, payload, output_dir):
        _write_certificate(output_dir, accepted=True, normalized_margin=0.9)
        return {}

    _patch_engine(monkeypatch, behavior)
    tracker = FakeTracker()
    result = VeriTMMAdapter(tracker).run_simulation({"mode": "simulate"}, tmp_path)

    assert len(tracker.cpu_calls) == 1
    assert tracker.cpu_calls[0] == result.cpu_seconds
    assert tracker.cpu_calls[0] >= 0.0


def test_bounded_run_over_budget(tmp_path, monkeypatch):
    called = []

    def behavior(mode, payload, output_dir):
        called.append(True)
        return {}

    _patch_engine(monkeypatch, behavior)

    class Snapshot:
        tmm_cpu_seconds = 12.5

    tracker = FakeTracker()
    adapter = VeriTMMAdapter(tracker)
    result = bounded_run(adapter, {"mode": "simulate"}, tmp_path, Snapshot(), max_cpu_seconds=10.0)

    assert called == []  # engine never invoked
    assert result.certified is False
    assert result.tightest_margin == -1.0
    assert result.cpu_seconds == 0.0
    assert result.raw_outputs["budget_gate"]["reason"] == "veritmm_budget_exhausted"
    assert tracker.cpu_calls == []


def test_seam_resolves_installed_not_deprecated():
    """The real seam must bind tmm_engine to the veritmm/ install, never the
    deprecated code/tmm_engine snapshot (which lacks managed_execution)."""
    if vmod._installed_veritmm_root() is None:
        pytest.skip("installed veritmm/ layout not present")
    vmod._ensure_real_veritmm_import()
    import tmm_engine  # noqa: F401  (re-bound by the guard)

    assert "veritmm" in tmm_engine.__file__.replace("\\", "/")
    assert hasattr(tmm_engine, "__version__")


def test_run_simulation_exception_handled(tmp_path, monkeypatch):
    def behavior(mode, payload, output_dir):
        raise RuntimeError("engine exploded")

    _patch_engine(monkeypatch, behavior)
    tracker = FakeTracker()
    result = VeriTMMAdapter(tracker).run_simulation({"mode": "simulate"}, tmp_path)

    assert result.certified is False
    assert result.tightest_margin == -1.0
    assert "engine exploded" in result.raw_outputs["error"]
    assert result.raw_outputs["error_type"] == "RuntimeError"
    assert result.certificate_path.name == CERTIFICATE_FILENAME
    assert len(tracker.cpu_calls) == 1
