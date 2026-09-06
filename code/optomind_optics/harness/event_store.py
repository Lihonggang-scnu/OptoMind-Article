"""O-03: append-only event store as the single source of truth.

EVENTS.jsonl is an append-only hash-chained log. Everything else -- current
state, budget, progress -- is a projection rebuilt from events and never a
fact source. The hash chain reuses ``tmm_engine.hashing.stable_sha256`` so
canonicalisation is identical across engine and harness.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from tmm_engine.hashing import stable_sha256

EVENTS_SCHEMA_VERSION = "optomind-event.v1"
EVENTS_FILENAME = "EVENTS.jsonl"
RESEARCH_PLAN_FILENAME = "RESEARCH_PLAN.json"

#: Events that freeze a decision. A second event of the same type with a
#: different payload digest can never be legal -- replay rejects it.
FROZEN_EVENT_TYPES = frozenset({"scoring_standard_frozen"})

_PROJECTION_FILES = ("CURRENT_STATE.json", "BUDGET_STATE.json", "PROGRESS.json")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventStore:
    """Append-only EVENTS.jsonl writer/reader with a tamper-evident chain."""

    def __init__(self, run_dir: str | Path, run_id: str) -> None:
        self._dir = Path(run_dir)
        self._run_id = str(run_id)
        self._path = self._dir / EVENTS_FILENAME
        self._seq = 0
        self._prev_hash: Optional[str] = None
        self._loaded = False

    # -- writing ---------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    def append(
        self,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        parent_event: Optional[str] = None,
    ) -> Dict[str, Any]:
        """The only write door: chain one envelope and append one line."""
        self._dir.mkdir(parents=True, exist_ok=True)
        self._load_once()
        envelope: Dict[str, Any] = {
            "schema_version": EVENTS_SCHEMA_VERSION,
            "run_id": self._run_id,
            "seq": self._seq + 1,
            "event_type": str(event_type),
            "time": _utc_now(),
            "payload": dict(payload or {}),
            "parent_event": parent_event,
            "prev_hash": self._prev_hash,
        }
        envelope["entry_hash"] = stable_sha256(envelope)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(envelope, ensure_ascii=False) + "\n")
            handle.flush()
        self._seq = int(envelope["seq"])
        self._prev_hash = str(envelope["entry_hash"])
        return envelope

    def _load_once(self) -> None:
        """Adopt an existing chain (seq/prev_hash) when reopening a run."""
        if self._loaded:
            return
        self._loaded = True
        if not self._path.is_file():
            return
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            envelope = json.loads(line)
            self._seq = int(envelope["seq"])
            self._prev_hash = str(envelope["entry_hash"])

    # -- reading / verification ------------------------------------------

    def read_all(self) -> List[Dict[str, Any]]:
        self._load_once()
        if not self._path.is_file():
            return []
        rows: List[Dict[str, Any]] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def verify_chain(self) -> List[str]:
        """Replay every envelope and return the list of chain violations."""
        failures: List[str] = []
        expected_seq = 0
        prev_hash: Optional[str] = None
        for index, envelope in enumerate(self.read_all()):
            where = f"line {index + 1}"
            recorded = envelope.get("entry_hash")
            recomputed = stable_sha256(
                {k: v for k, v in envelope.items() if k != "entry_hash"}
            )
            if recorded != recomputed:
                failures.append(f"{where}: entry_hash mismatch")
                continue
            expected_seq += 1
            if int(envelope.get("seq") or 0) != expected_seq:
                failures.append(
                    f"{where}: seq {envelope.get('seq')} breaks monotonicity "
                    f"(expected {expected_seq})"
                )
            if envelope.get("prev_hash") != prev_hash:
                failures.append(f"{where}: prev_hash does not link to parent")
            prev_hash = recorded
        return failures


# ---------------------------------------------------------------------------
# Frozen-decision contract and projections
# ---------------------------------------------------------------------------


def assert_frozen_contract(events: Iterable[Mapping[str, Any]]) -> List[str]:
    """Reject any attempt to rewrite a frozen decision.

    A frozen event type may appear once. Any later event of the same type
    with a different payload digest is a tampering attempt, not a decision.
    """

    violations: List[str] = []
    frozen: Dict[str, str] = {}
    for index, envelope in enumerate(events):
        event_type = str(envelope.get("event_type") or "")
        if event_type not in FROZEN_EVENT_TYPES:
            continue
        digest = stable_sha256(envelope.get("payload") or {})
        if event_type in frozen and frozen[event_type] != digest:
            violations.append(
                f"event {index + 1}: frozen decision {event_type!r} rewritten "
                "with a different payload"
            )
        frozen.setdefault(event_type, digest)
    return violations


def rebuild_projections(
    events: Iterable[Mapping[str, Any]], out_dir: str | Path
) -> Dict[str, Any]:
    """Rebuild every projection from events; projections are never facts."""

    rows = list(events)
    routes: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    budget: Dict[str, Any] = {"changes": []}
    frozen_scoring: Dict[str, Any] | None = None
    rounds_started = 0
    run_completed: Dict[str, Any] | None = None

    for envelope in rows:
        event_type = str(envelope.get("event_type") or "")
        payload = dict(envelope.get("payload") or {})
        route_id = str(payload.get("route_id") or "") or None
        if route_id and route_id not in routes:
            order.append(route_id)
            routes[route_id] = {
                "route_id": route_id,
                "status": "racing",
                "rounds_used": 0,
                "best_score": None,
            }
        if event_type == "scoring_standard_frozen":
            frozen_scoring = payload
        elif event_type == "round_started":
            rounds_started += 1
            if route_id:
                routes[route_id]["status"] = "racing"
        elif event_type == "route_completed":
            if route_id:
                routes[route_id]["rounds_used"] += 1
                score = payload.get("best_target_score")
                if score is not None:
                    best = routes[route_id]["best_score"]
                    routes[route_id]["best_score"] = (
                        score if best is None else max(best, score)
                    )
        elif event_type == "track_status":
            if route_id:
                routes[route_id]["status"] = str(
                    payload.get("status") or routes[route_id]["status"]
                )
        elif event_type == "budget_changed":
            budget["changes"].append(
                {
                    "seq": envelope.get("seq"),
                    "reason": payload.get("reason"),
                    **{
                        k: v
                        for k, v in payload.items()
                        if k not in {"reason"}
                    },
                }
            )
        elif event_type == "run_completed":
            run_completed = payload

    current_state = {
        "schema_version": "optomind-current-state.v1",
        "routes": [routes[key] for key in order],
        "rounds_started": rounds_started,
        "active_routes": [
            r["route_id"] for r in (routes[k] for k in order) if r["status"] == "racing"
        ],
        "scoring_standard_frozen": frozen_scoring,
    }
    budget_state = {
        "schema_version": "optomind-budget-state.v1",
        **budget,
    }
    finished = [r for r in (routes[k] for k in order) if r["status"] != "racing"]
    progress = {
        "schema_version": "optomind-progress.v1",
        "events": len(rows),
        "routes": len(order),
        "rounds_started": rounds_started,
        "routes_finished": len(finished),
        "active_routes": current_state["active_routes"],
        "run_completed": bool(run_completed),
        "summary": (
            f"{len(order)} routes, {rounds_started} rounds started, "
            f"{len(finished)} finished"
            + ("; run completed" if run_completed else "; run in progress")
        ),
    }

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, payload in (
        ("CURRENT_STATE.json", current_state),
        ("BUDGET_STATE.json", budget_state),
        ("PROGRESS.json", progress),
    ):
        (out / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return {
        "CURRENT_STATE.json": current_state,
        "BUDGET_STATE.json": budget_state,
        "PROGRESS.json": progress,
    }


def write_research_plan(run_dir: str | Path, routes: Iterable[Mapping[str, Any]]) -> Path:
    """Anthropic-style plan file: JSON steps with pass flags, written at start."""

    plan = {
        "schema_version": "optomind-research-plan.v1",
        "written_at": _utc_now(),
        "routes": [
            {
                "route_id": str(route.get("route_id") or ""),
                "kind": str(route.get("kind") or route.get("route_kind") or ""),
                "description": str(route.get("title") or route.get("description") or ""),
                "steps": [
                    str(step) for step in (route.get("steps") or [])
                ],
                "passes": False,
            }
            for route in routes
        ],
    }
    path = Path(run_dir) / RESEARCH_PLAN_FILENAME
    path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def recover_state(run_dir: str | Path) -> Dict[str, Any]:
    """Crash-recovery checklist: chain -> contract -> projections -> sanity."""

    directory = Path(run_dir)
    report: Dict[str, Any] = {"run_dir": str(directory)}
    store = EventStore(directory, run_id=directory.name)
    events = store.read_all()
    report["events"] = len(events)
    report["chain_failures"] = store.verify_chain()
    report["chain_valid"] = not report["chain_failures"]
    report["contract_violations"] = assert_frozen_contract(events)
    charter_path = directory / "REQUEST.json"
    report["charter_loaded"] = charter_path.is_file()
    if report["chain_valid"] and not report["contract_violations"]:
        projections = rebuild_projections(events, directory)
        report["projections"] = sorted(projections)
        missing = [
            str(path.relative_to(directory))
            for envelope in events
            for path in [
                directory / str(
                    (envelope.get("payload") or {}).get("artifact_path") or ""
                ),
            ]
            if str((envelope.get("payload") or {}).get("artifact_path") or "")
            and not path.is_file()
        ]
        report["missing_artifacts"] = missing
        report["resumable"] = not missing
    else:
        report["resumable"] = False
        report["missing_artifacts"] = []
    return report
