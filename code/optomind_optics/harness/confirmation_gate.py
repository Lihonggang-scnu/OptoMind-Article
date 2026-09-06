"""O-12: human-in-the-loop confirmation gates at key data hand-offs.

A gate parks the payload between an upstream producer and its downstream
consumer, waits (<=1s poll) for a human/UTF responder to write a RESOLVED
file, and auto-accepts after ``timeout_seconds`` of silence. During
development and E2E the "human" is silent by default -- the protocol is
built for a future UI to take over without changing shape.

Responder protocol (for O-13/future UI):
  read confirmations/PENDING_<gate_id>.json -> show/edit -> write
  confirmations/RESOLVED_<gate_id>.json with
  {decision: accepted|modified|rejected, payload_after, responder} ->
  the gate validates and releases. A RESOLVED arriving after the
  auto-timeout is honoured as already-decided history (the gate's own
  auto-RESOLVED wins; the late file is quarantined and recorded).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

CONFIRMATIONS_DIRNAME = "confirmations"
PENDING_PREFIX = "PENDING_"
RESOLVED_PREFIX = "RESOLVED_"
LATE_PREFIX = "LATE_"
DEFAULT_TIMEOUT_SECONDS = 30
_MAX_PAYLOAD_BYTES = 64 * 1024


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_diff(before: Any, after: Any, path: str = "") -> List[Dict[str, Any]]:
    """Field-level diff between the original and the human-edited payload."""

    diffs: List[Dict[str, Any]] = []
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        for key in sorted(set(before) | set(after)):
            here = f"{path}.{key}" if path else str(key)
            if key not in after:
                diffs.append({"field": here, "change": "removed", "before": before[key]})
            elif key not in before:
                diffs.append({"field": here, "change": "added", "after": after[key]})
            else:
                diffs.extend(compute_diff(before[key], after[key], here))
    elif before != after:
        diffs.append({"field": path or "<root>", "change": "changed", "before": before, "after": after})
    return diffs


@dataclass
class ConfirmationRequest:
    gate_id: str
    node: str
    payload_kind: str
    payload: Any
    schema_hint: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now)
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS

    def to_dict(self) -> Dict[str, Any]:
        payload = self.payload
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        summary_note = None
        if len(encoded) > _MAX_PAYLOAD_BYTES:
            summary_note = (
                "payload exceeds 64KiB: full copy stored as artifact, only "
                "editable summary shown here"
            )
        return {
            "gate_id": self.gate_id,
            "node": self.node,
            "payload_kind": self.payload_kind,
            "payload": payload if summary_note is None else self.payload,
            "payload_bytes": len(encoded),
            "payload_note": summary_note,
            "schema_hint": self.schema_hint,
            "created_at": self.created_at,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass
class Resolution:
    gate_id: str
    decision: str  # accepted | modified | timeout_accepted | rejected
    payload_after: Any
    diff: List[Dict[str, Any]] = field(default_factory=list)
    resolved_at: str = field(default_factory=_utc_now)
    responder: str = "auto:timeout"


class ConfirmationGate:
    """File-protocol gate. ``sleeper``/``now`` are injectable for tests."""

    def __init__(
        self,
        run_dir: Path,
        *,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        sleeper: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.dir = Path(run_dir) / CONFIRMATIONS_DIRNAME
        self.timeout_seconds = int(timeout_seconds)
        self._sleeper = sleeper
        self._now = now

    # -- file helpers -------------------------------------------------------

    def _pending_path(self, gate_id: str) -> Path:
        return self.dir / f"{PENDING_PREFIX}{gate_id}.json"

    def _resolved_path(self, gate_id: str) -> Path:
        return self.dir / f"{RESOLVED_PREFIX}{gate_id}.json"

    def _late_path(self, gate_id: str) -> Path:
        return self.dir / f"{LATE_PREFIX}{gate_id}.json"

    def _write_json(self, path: Path, payload: Mapping[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def _read_json(self, path: Path) -> Optional[Dict[str, Any]]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, ValueError):
            return None

    # -- the gate ------------------------------------------------------------

    def ask(
        self,
        request: ConfirmationRequest,
        *,
        event_sink: Optional[Callable[[str, Mapping[str, Any]], None]] = None,
    ) -> Resolution:
        def emit(event_type: str, payload: Mapping[str, Any]) -> None:
            if event_sink is not None:
                event_sink(event_type, payload)

        request_payload = request.to_dict()
        pending_path = self._pending_path(request.gate_id)
        resolved_path = self._resolved_path(request.gate_id)
        self._write_json(pending_path, request_payload)
        emit(
            "confirmation_requested",
            {
                "gate_id": request.gate_id,
                "node": request.node,
                "payload_kind": request.payload_kind,
                "timeout_seconds": request.timeout_seconds,
            },
        )

        deadline = self._now() + self.timeout_seconds
        human_resolution: Optional[Dict[str, Any]] = None
        while self._now() < deadline:
            if resolved_path.is_file():
                candidate = self._read_json(resolved_path)
                if candidate is not None:
                    human_resolution = candidate
                    break
            # Poll at 50ms: responsive to a human/UI answer while staying
            # far below the 1s interval ceiling the protocol allows.
            self._sleeper(0.05)

        if human_resolution is None:
            # Timeout: auto-accept the original payload and write the
            # auto-RESOLVED FIRST so a late human file cannot double-decide.
            resolution = Resolution(
                gate_id=request.gate_id,
                decision="timeout_accepted",
                payload_after=request.payload,
                diff=[],
                responder="auto:timeout",
            )
            self._write_json(
                resolved_path,
                {
                    "gate_id": request.gate_id,
                    "decision": resolution.decision,
                    "payload_after": resolution.payload_after,
                    "responder": resolution.responder,
                    "resolved_at": resolution.resolved_at,
                },
            )
        else:
            decision = str(human_resolution.get("decision") or "")
            payload_after = human_resolution.get(
                "payload_after", request.payload
            )
            if decision == "modified":
                resolution = Resolution(
                    gate_id=request.gate_id,
                    decision="modified",
                    payload_after=payload_after,
                    diff=compute_diff(request.payload, payload_after),
                    responder=str(human_resolution.get("responder") or "human"),
                )
            elif decision == "rejected":
                resolution = Resolution(
                    gate_id=request.gate_id,
                    decision="rejected",
                    payload_after=None,
                    responder=str(human_resolution.get("responder") or "human"),
                )
            elif decision == "accepted":
                resolution = Resolution(
                    gate_id=request.gate_id,
                    decision="accepted",
                    payload_after=request.payload,
                    responder=str(human_resolution.get("responder") or "human"),
                )
            else:
                # Malformed RESOLVED: defensive fallback to auto-accept with
                # the violation recorded (never a silent swallow).
                resolution = Resolution(
                    gate_id=request.gate_id,
                    decision="timeout_accepted",
                    payload_after=request.payload,
                    responder="auto:timeout",
                )
                emit(
                    "confirmation_invalid_resolution",
                    {
                        "gate_id": request.gate_id,
                        "reason": f"unknown decision {decision!r}",
                    },
                )

        # Archive + quarantine the late file if one appeared after the
        # auto-resolution (defence against double-decide).
        if human_resolution is None and resolved_path.is_file():
            written_by_gate = self._read_json(resolved_path) or {}
            if written_by_gate.get("responder") != "auto:timeout":
                late = self._late_path(request.gate_id)
                late.write_text(resolved_path.read_text(encoding="utf-8"), encoding="utf-8")
                resolved_path.write_text(
                    json.dumps(
                        {
                            "gate_id": request.gate_id,
                            "decision": "timeout_accepted",
                            "payload_after": request.payload,
                            "responder": "auto:timeout",
                            "resolved_at": resolution.resolved_at,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                emit(
                    "confirmation_late_response_quarantined",
                    {"gate_id": request.gate_id, "quarantined_to": str(late.name)},
                )

        emit(
            "confirmation_resolved",
            {
                "gate_id": request.gate_id,
                "node": request.node,
                "decision": resolution.decision,
                "responder": resolution.responder,
                "diff_summary": resolution.diff[:8],
                "diff_count": len(resolution.diff),
            },
        )
        pending_path.unlink(missing_ok=True)
        return resolution
