"""Deterministic, replayable run-stage state machine for the TMM Harness."""

from __future__ import annotations

import hashlib
import json
import time
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Dict, List, Optional

from optomind_research.runtime.artifact_store import atomic_write_json


class HarnessStage(str, Enum):
    received = "received"
    protocol_validated = "protocol_validated"
    capability_classified = "capability_classified"
    materials_resolved = "materials_resolved"
    baseline_evaluated = "baseline_evaluated"
    searching = "searching"
    candidate_verification = "candidate_verification"
    robustness_verification = "robustness_verification"
    portfolio_ranking = "portfolio_ranking"
    diagnosing = "diagnosing"
    needs_higher_fidelity = "needs_higher_fidelity"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


TERMINAL_STAGES = {
    HarnessStage.needs_higher_fidelity,
    HarnessStage.completed,
    HarnessStage.failed,
    HarnessStage.cancelled,
}


_ALLOWED: Dict[HarnessStage, set[HarnessStage]] = {
    HarnessStage.received: {HarnessStage.protocol_validated, HarnessStage.diagnosing},
    HarnessStage.protocol_validated: {HarnessStage.capability_classified, HarnessStage.diagnosing},
    HarnessStage.capability_classified: {
        HarnessStage.materials_resolved,
        HarnessStage.needs_higher_fidelity,
        HarnessStage.diagnosing,
    },
    HarnessStage.materials_resolved: {HarnessStage.baseline_evaluated, HarnessStage.diagnosing},
    HarnessStage.baseline_evaluated: {HarnessStage.searching, HarnessStage.candidate_verification, HarnessStage.diagnosing},
    HarnessStage.searching: {HarnessStage.candidate_verification, HarnessStage.diagnosing},
    HarnessStage.candidate_verification: {
        HarnessStage.robustness_verification,
        HarnessStage.portfolio_ranking,
        HarnessStage.diagnosing,
    },
    HarnessStage.robustness_verification: {
        HarnessStage.portfolio_ranking,
        HarnessStage.diagnosing,
    },
    HarnessStage.diagnosing: {
        HarnessStage.protocol_validated,
        HarnessStage.capability_classified,
        HarnessStage.materials_resolved,
        HarnessStage.baseline_evaluated,
        HarnessStage.searching,
        HarnessStage.candidate_verification,
        HarnessStage.robustness_verification,
        HarnessStage.portfolio_ranking,
        HarnessStage.needs_higher_fidelity,
    },
    HarnessStage.portfolio_ranking: {HarnessStage.completed},
}


class InvalidStageTransition(RuntimeError):
    pass


def _hash_event(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class HarnessStateMachine:
    def __init__(
        self,
        work_dir: str | Path,
        run_id: str,
        *,
        resume: bool = False,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = str(run_id)
        self.clock = clock
        self._lock = RLock()
        self._history_path = self.work_dir / "STATE_HISTORY.json"
        self._state_path = self.work_dir / "RUN_STATE.json"
        self._history: List[Dict[str, Any]] = []
        if resume:
            self._load()
        else:
            if self._history_path.exists() or self._state_path.exists():
                raise FileExistsError("Run state already exists; use resume=True")
            self._append(HarnessStage.received, "run_created", {})

    @property
    def stage(self) -> HarnessStage:
        return HarnessStage(self._history[-1]["stage"])

    @property
    def history(self) -> List[Dict[str, Any]]:
        return [dict(item) for item in self._history]

    def transition(
        self,
        next_stage: HarnessStage | str,
        reason: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        next_stage = HarnessStage(next_stage)
        with self._lock:
            current = self.stage
            if current in TERMINAL_STAGES:
                raise InvalidStageTransition(f"Terminal stage {current.value} cannot transition")
            globally_allowed = {HarnessStage.failed, HarnessStage.cancelled}
            allowed = _ALLOWED.get(current, set()) | globally_allowed
            if next_stage not in allowed:
                raise InvalidStageTransition(
                    f"Invalid Harness transition: {current.value} -> {next_stage.value}"
                )
            return self._append(next_stage, reason, details or {})

    def snapshot(self) -> Dict[str, Any]:
        latest = self._history[-1]
        return {
            "schema_version": "tmm-harness-run-state.v1",
            "run_id": self.run_id,
            "stage": latest["stage"],
            "terminal": self.stage in TERMINAL_STAGES,
            "event_count": len(self._history),
            "latest_event_hash": latest["event_hash"],
            "updated_at_unix": latest["created_at_unix"],
        }

    def _append(self, stage: HarnessStage, reason: str, details: Dict[str, Any]) -> Dict[str, Any]:
        previous_hash = self._history[-1]["event_hash"] if self._history else None
        body = {
            "sequence": len(self._history) + 1,
            "run_id": self.run_id,
            "stage": stage.value,
            "reason": str(reason),
            "details": dict(details),
            "created_at_unix": float(self.clock()),
            "previous_event_hash": previous_hash,
        }
        event = {**body, "event_hash": _hash_event(body)}
        self._history.append(event)
        atomic_write_json(
            self._history_path,
            {"schema_version": "tmm-harness-state-history.v1", "events": self._history},
        )
        atomic_write_json(self._state_path, self.snapshot())
        return dict(event)

    def _load(self) -> None:
        payload = json.loads(self._history_path.read_text(encoding="utf-8"))
        events = payload.get("events") if isinstance(payload, dict) else None
        if not isinstance(events, list) or not events:
            raise ValueError("State history is empty or malformed")
        previous_hash = None
        for expected_sequence, event in enumerate(events, 1):
            if event.get("run_id") != self.run_id:
                raise ValueError("State history run_id mismatch")
            if int(event.get("sequence", 0)) != expected_sequence:
                raise ValueError("State history sequence mismatch")
            if event.get("previous_event_hash") != previous_hash:
                raise ValueError("State history hash chain is broken")
            body = {key: value for key, value in event.items() if key != "event_hash"}
            if event.get("event_hash") != _hash_event(body):
                raise ValueError("State history event hash mismatch")
            HarnessStage(event.get("stage"))
            previous_hash = event["event_hash"]
        self._history = [dict(item) for item in events]


__all__ = [
    "HarnessStage",
    "HarnessStateMachine",
    "InvalidStageTransition",
    "TERMINAL_STAGES",
]
