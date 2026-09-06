"""O-04: Scientific Legibility -- read-only meta-tools over the event store.

The research state, failures, metrics and artifacts become machine-queryable
so a model never has to diff dozens of JSON files to locate itself. Every
tool emits an audit event (peeking is recorded, never hidden) and every
answer is compact JSON capped at 4 KiB -- oversized answers degrade to a
summary plus pointers, never to truncation errors.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .event_store import EventStore

MAX_OUTPUT_BYTES = 4096
_AUDIT_TOOL = "tool_called"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cap(
    payload: Mapping[str, Any], limit: int = MAX_OUTPUT_BYTES
) -> Dict[str, Any]:
    """Shrink an answer below ``limit`` bytes, keeping pointers, not lies.

    Oversized list/dict values collapse to a count plus a pointer the caller
    can follow with the targeted inspectors; scalar identity fields survive.
    """

    body = json.dumps(payload, ensure_ascii=False)
    if len(body.encode("utf-8")) <= limit:
        return dict(payload)
    slim: Dict[str, Any] = {}
    dropped: List[str] = []
    for key, value in payload.items():
        rendered = json.dumps(value, ensure_ascii=False)
        if len(rendered.encode("utf-8")) > limit // 4:
            if isinstance(value, (list, dict)):
                slim[key] = {
                    "omitted": True,
                    "count": len(value),
                    "pointer": key,
                }
            else:
                slim[key] = str(value)[: limit // 8] + "…"
            dropped.append(key)
        else:
            slim[key] = value
    if dropped:
        slim["_omitted_fields"] = dropped
        slim["_note"] = (
            "output exceeded the 4KiB legibility budget; query the pointed "
            "fields with the targeted inspectors for full detail"
        )
    return slim


def observation_brief(observation: Mapping[str, Any]) -> Dict[str, Any]:
    """Progressive disclosure: the decision-relevant slice of one round.

    Feedback/planner prompts need scores, statuses, failure categories and
    design pointers -- not the full objective/robustness report bodies, which
    stay on disk in ITERATION_OBSERVATION.json and are referenced by path.
    """

    candidates: List[Dict[str, Any]] = []
    for row in observation.get("candidate_summaries") or []:
        if not isinstance(row, Mapping):
            continue
        candidates.append(
            {
                "candidate_id": row.get("candidate_id"),
                "target_score": row.get("target_score"),
                "robustness_score": row.get("robustness_score"),
                "thicknesses_nm": row.get("thicknesses_nm"),
                "certificate_id": row.get("certificate_id"),
            }
        )
    experiments = [
        {
            "experiment_id": row.get("experiment_id"),
            "mode": row.get("mode"),
            "physically_valid_candidate_count": row.get(
                "physically_valid_candidate_count"
            ),
        }
        for row in observation.get("experiment_summaries") or []
        if isinstance(row, Mapping)
    ]
    brief = {
        "iteration_id": observation.get("iteration_id"),
        "route_id": observation.get("route_id"),
        "run_status": observation.get("run_status"),
        "compilation_status": observation.get("compilation_status"),
        "physically_valid_candidate_count": observation.get(
            "physically_valid_candidate_count"
        ),
        "best_target_score": observation.get("best_target_score"),
        "best_robustness_score": observation.get("best_robustness_score"),
        "failure_categories": list(observation.get("failure_categories") or []),
        "selected_candidate_ids": list(
            observation.get("selected_candidate_ids") or []
        ),
        "candidates": candidates,
        "experiments": experiments,
        "full_record": observation.get("result_path")
        or observation.get("work_dir"),
    }
    return brief


class ResearchLegibility:
    """The seven read-only meta-tools, bound to one run directory."""

    def __init__(self, run_dir: str | Path, store: EventStore) -> None:
        self.run_dir = Path(run_dir)
        self.store = store

    # -- shared plumbing --------------------------------------------------

    def _audit(self, tool: str, query: Mapping[str, Any]) -> None:
        self.store.append(
            _AUDIT_TOOL,
            {"tool": tool, "audit": True, "query": dict(query)},
        )

    def _read(self, name: str) -> Optional[Dict[str, Any]]:
        path = self.run_dir / name
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    # -- 1. the state of the study ----------------------------------------

    def research_status(self) -> Dict[str, Any]:
        self._audit("research_status", {})
        current = self._read("CURRENT_STATE.json") or {}
        scoring = self._read("SCORING_STANDARD.json") or {}
        state = self._read("TOURNAMENT_STATE.json") or {}
        events = self.store.read_all()
        failure_categories: Dict[str, int] = {}
        verified_candidates = 0
        for row in events:
            if row.get("event_type") == "round_observed":
                payload = row.get("payload") or {}
                for category in payload.get("failure_categories") or ():
                    failure_categories[str(category)] = (
                        failure_categories.get(str(category), 0) + 1
                    )
                verified_candidates += int(
                    payload.get("valid_candidates") or 0
                )
        plan = self._read("RESEARCH_PLAN.json") or {}
        open_questions = [
            route.get("route_id")
            for route in plan.get("routes") or []
            if isinstance(route, Mapping) and not route.get("passes")
        ]
        ceiling = current.get("rounds_started")
        routes = current.get("routes") or []
        active = [r for r in routes if r.get("status") == "racing"]
        answer = {
            "schema_version": "legibility.research-status.v1",
            "stage": (
                "completed"
                if current.get("run_completed") or state.get("finished")
                else "running"
            ),
            "active_hypotheses": len(active),
            "finished_hypotheses": len(routes) - len(active),
            "verified_candidates": verified_candidates,
            "budget_utilization": {
                "rounds_started": current.get("rounds_started") or 0,
                "routes": len(routes),
            },
            "bottleneck": min(
                (
                    r.get("best_score")
                    for r in routes
                    if r.get("best_score") is not None
                ),
                default=None,
            ),
            "recent_failure_categories": failure_categories,
            "open_questions": open_questions,
            "scoring_standard": scoring.get("formula")
            or scoring.get("ranking_mechanism"),
        }
        return _cap(answer)

    # -- 2/3. hypothesis and experiment cards ------------------------------

    def inspect_hypothesis(self, hypothesis_id: str) -> Dict[str, Any]:
        self._audit("inspect_hypothesis", {"id": hypothesis_id})
        routes = (self._read("TOURNAMENT_STATE.json") or {}).get("tracks")
        card: Dict[str, Any] = {"id": hypothesis_id, "found": False}
        if isinstance(routes, list):
            for row in routes:
                if isinstance(row, Mapping) and row.get("route_id") == hypothesis_id:
                    card = {"found": True, "card": dict(row)}
                    break
        elif isinstance(routes, dict):
            if hypothesis_id in routes:
                card = {"found": True, "card": dict(routes[hypothesis_id])}
        pointers = [
            {"seq": row.get("seq"), "event_type": row.get("event_type")}
            for row in self.store.read_all()
            if (row.get("payload") or {}).get("route_id") == hypothesis_id
        ][-8:]
        answer = {
            "schema_version": "legibility.hypothesis.v1",
            **card,
            "related_events": pointers,
        }
        return _cap(answer)

    def inspect_experiment(self, experiment_id: str) -> Dict[str, Any]:
        self._audit("inspect_experiment", {"id": experiment_id})
        path = (
            self.run_dir
            / "iterations"
            / experiment_id
            / "ITERATION_OBSERVATION.json"
        )
        payload: Dict[str, Any] = {"id": experiment_id, "found": path.is_file()}
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                payload.update(
                    {
                        "run_status": data.get("run_status"),
                        "compilation_status": data.get("compilation_status"),
                        "physically_valid_candidate_count": data.get(
                            "physically_valid_candidate_count"
                        ),
                        "best_target_score": data.get("best_target_score"),
                        "failure_categories": data.get("failure_categories"),
                        "verification": data.get("verification"),
                        "evidence_coverage": data.get("evidence_coverage"),
                        "full_record_pointer": str(
                            path.relative_to(self.run_dir)
                        ),
                    }
                )
            except (OSError, ValueError) as exc:
                payload["error"] = str(exc)
        return _cap(payload)

    # -- 4. event queries ---------------------------------------------------

    def query_events(
        self,
        *,
        event_type: Optional[str] = None,
        route_id: Optional[str] = None,
        since_seq: int = 0,
        limit: int = 20,
    ) -> Dict[str, Any]:
        self._audit(
            "query_events",
            {"event_type": event_type, "route_id": route_id, "since_seq": since_seq},
        )
        matched = [
            {
                "seq": row.get("seq"),
                "event_type": row.get("event_type"),
                "payload": row.get("payload"),
            }
            for row in self.store.read_all()
            if (event_type is None or row.get("event_type") == event_type)
            and (route_id is None or (row.get("payload") or {}).get("route_id") == route_id)
            and int(row.get("seq") or 0) > since_seq
        ]
        return _cap(
            {
                "schema_version": "legibility.query-events.v1",
                "matched": len(matched),
                "returned": matched[: max(1, limit)],
                "truncated": len(matched) > limit,
            }
        )

    # -- 5. artifacts ---------------------------------------------------------

    def inspect_artifact(self, artifact_path: str) -> Dict[str, Any]:
        self._audit("inspect_artifact", {"path": artifact_path})
        path = self.run_dir / artifact_path
        answer: Dict[str, Any] = {"path": artifact_path}
        answer["exists"] = path.is_file()
        if path.is_file():
            data = path.read_bytes()
            answer["sha256"] = _digest(data)
            answer["bytes"] = len(data)
            answer["kind"] = path.suffix.lstrip(".") or "none"
        downstream = [
            {"seq": row.get("seq"), "event_type": row.get("event_type")}
            for row in self.store.read_all()
            if artifact_path in json.dumps(row.get("payload") or {})
        ][-8:]
        answer["referenced_by_events"] = downstream
        return _cap(answer)

    # -- 6. failures -----------------------------------------------------------

    def inspect_failure(self, failure_id: str) -> Dict[str, Any]:
        self._audit("inspect_failure", {"id": failure_id})
        answer: Dict[str, Any] = {"id": failure_id, "found": False}
        recoverable = {
            "runtime_environment": True,
            "budget_exhausted": False,
            "outside_tmm_domain": False,
            "physics_violation": False,
            "material_data": True,
        }
        for row in self.store.read_all():
            payload = row.get("payload") or {}
            if failure_id in (json.dumps(payload.get("failure_categories") or "")):
                categories = list(payload.get("failure_categories") or [])
                answer.update(
                    {
                        "found": True,
                        "iteration_id": payload.get("iteration_id"),
                        "route_id": payload.get("route_id"),
                        "failure_categories": categories,
                        "recoverability": {
                            category: recoverable.get(category, True)
                            for category in categories
                        },
                    }
                )
                break
        return _cap(answer)

    # -- 7. budget ----------------------------------------------------------------

    def inspect_budget(self) -> Dict[str, Any]:
        self._audit("inspect_budget", {})
        budget = self._read("BUDGET_STATE.json") or {}
        quota = [
            row.get("payload")
            for row in self.store.read_all()
            if row.get("event_type") == "budget_changed"
        ]
        answer = {
            "schema_version": "legibility.budget.v1",
            "projection": budget,
            "quota_events": quota[-4:],
        }
        return _cap(answer)
