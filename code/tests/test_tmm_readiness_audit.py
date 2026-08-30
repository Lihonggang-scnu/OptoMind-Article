from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_tmm_harness_readiness.py"


def _audit_module():
    spec = importlib.util.spec_from_file_location("tmm_readiness_audit", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _events(holdout_id: str = "HOLDOUT09") -> list[dict[str, object]]:
    return [
        {
            "event": "holdout_read_started",
            "requested_holdout_id": holdout_id,
            "process_id": 42,
        },
        {
            "event": "holdout_read_completed",
            "requested_holdout_id": holdout_id,
            "process_id": 42,
            "file_sha256": "a" * 64,
        },
    ]


def _report(holdout_id: str = "HOLDOUT09") -> dict[str, object]:
    return {
        "holdout_id": holdout_id,
        "holdout_audit_event_count_for_selected_id": 2,
        "checks": {"holdout_access_audited": True},
    }


def test_preholdout_requires_zero_access_events() -> None:
    module = _audit_module()
    assert module._holdout_access_protocol_valid([], None) is True
    assert module._holdout_access_protocol_valid(_events(), None) is False


def test_postholdout_accepts_exactly_one_audited_started_completed_pair() -> None:
    module = _audit_module()
    assert module._holdout_access_protocol_valid(_events(), _report()) is True


def test_postholdout_rejects_wrong_id_or_extra_access() -> None:
    module = _audit_module()
    assert module._holdout_access_protocol_valid(_events("HOLDOUT08"), _report()) is False
    assert module._holdout_access_protocol_valid(_events() + _events(), _report()) is False


def test_postholdout_accepts_distinct_one_shot_attempts_and_latest_report() -> None:
    module = _audit_module()
    events = _events("HOLDOUT09") + _events("HOLDOUT10")
    assert module._holdout_access_protocol_valid(events, _report("HOLDOUT10")) is True
    assert module._holdout_access_protocol_valid(events, _report("HOLDOUT09")) is False
