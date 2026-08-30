"""Append-only SQLite experiment graph with replayable event history.

The graph keeps the original TMM node/event APIs unchanged and adds an
Article-compatible view on top: article nodes carry a versioned
``ArticleNodePayload`` and append-only ``article.*`` events validated by
``article_contracts.validate_article_event``.  Existing databases are migrated
in place with additive columns; existing keys and consumers are preserved.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .article_contracts import (
    ARTICLE_EVENT_SCHEMA_VERSION,
    ArticleDecision,
    ArticleNodePayload,
    ArticleStage,
    CoverageStatus,
    HypothesisStatus,
    ObservationCard,
    validate_article_event,
)
from .contracts import ActionProposal, ExperimentStatus


ARTICLE_GRAPH_SCHEMA_VERSION = "optical-experiment-graph.article.v1"
ARTICLE_NODE_TASK_HASH = "article-node"


class ExperimentGraph:
    def __init__(self, path: str | Path, run_id: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = str(run_id)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS nodes (
                    node_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    task_hash TEXT NOT NULL,
                    action_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS edges (
                    run_id TEXT NOT NULL,
                    parent_id TEXT NOT NULL,
                    child_id TEXT NOT NULL,
                    PRIMARY KEY(parent_id, child_id),
                    FOREIGN KEY(parent_id) REFERENCES nodes(node_id),
                    FOREIGN KEY(child_id) REFERENCES nodes(node_id)
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(node_id) REFERENCES nodes(node_id)
                );
                CREATE INDEX IF NOT EXISTS idx_events_node ON events(node_id, event_id);
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(nodes)").fetchall()
            }
            if "node_kind" not in columns:
                connection.execute(
                    "ALTER TABLE nodes ADD COLUMN node_kind TEXT NOT NULL DEFAULT 'tmm'"
                )
            if "article_json" not in columns:
                connection.execute("ALTER TABLE nodes ADD COLUMN article_json TEXT")

    def create_node(
        self,
        task_hash: str,
        action: ActionProposal,
        parent_ids: Iterable[str] = (),
        *,
        node_id: Optional[str] = None,
    ) -> str:
        node_id = node_id or uuid.uuid4().hex[:16]
        parents = tuple(dict.fromkeys(str(item) for item in parent_ids))
        now = time.time()
        with self._lock, self._connect() as connection:
            self._validate_parents(connection, parents)
            connection.execute(
                "INSERT INTO nodes(node_id,run_id,task_hash,action_json,created_at,node_kind,article_json) "
                "VALUES(?,?,?,?,?,?,?)",
                (node_id, self.run_id, task_hash, action.model_dump_json(), now, "tmm", None),
            )
            for parent in parents:
                connection.execute(
                    "INSERT INTO edges(run_id,parent_id,child_id) VALUES(?,?,?)",
                    (self.run_id, parent, node_id),
                )
            self._insert_event(
                connection,
                node_id,
                "status",
                {"status": ExperimentStatus.proposed.value},
                now,
            )
        return node_id

    def create_article_node(
        self,
        payload: ArticleNodePayload | dict[str, Any],
        parent_ids: Iterable[str] = (),
        *,
        node_id: Optional[str] = None,
    ) -> str:
        """Create an article node with a versioned payload and lineage edges.

        The payload is validated (dicts are converted through
        ``ArticleNodePayload``); an initial ``proposed`` status event and, when
        a stage is present, an initial ``article.stage`` event are appended.
        Parent ids may reference TMM or article nodes, enabling cross-kind
        lineage (e.g. article experiments over TMM runs).
        """

        if not isinstance(payload, ArticleNodePayload):
            payload = ArticleNodePayload.model_validate(payload)
        node_id = node_id or uuid.uuid4().hex[:16]
        parents = tuple(dict.fromkeys(str(item) for item in parent_ids))
        now = time.time()
        task_hash = payload.task_hash or ARTICLE_NODE_TASK_HASH
        with self._lock, self._connect() as connection:
            self._validate_parents(connection, parents)
            connection.execute(
                "INSERT INTO nodes(node_id,run_id,task_hash,action_json,created_at,node_kind,article_json) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    node_id,
                    self.run_id,
                    task_hash,
                    "{}",
                    now,
                    "article",
                    payload.model_dump_json(),
                ),
            )
            for parent in parents:
                connection.execute(
                    "INSERT INTO edges(run_id,parent_id,child_id) VALUES(?,?,?)",
                    (self.run_id, parent, node_id),
                )
            self._insert_event(
                connection,
                node_id,
                "status",
                {"status": ExperimentStatus.proposed.value},
                now,
            )
            if payload.stage is not None:
                self._insert_event(
                    connection,
                    node_id,
                    "article.stage",
                    {
                        "schema_version": ARTICLE_EVENT_SCHEMA_VERSION,
                        "stage": payload.stage.value,
                    },
                    now,
                )
        return node_id

    def _validate_parents(
        self, connection: sqlite3.Connection, parents: tuple[str, ...]
    ) -> None:
        """Ensure every parent exists in this run before any insert happens.

        Missing or cross-run parents raise ``KeyError`` before the node or any
        edge/event is written, so rejected calls never leave partial state.
        """

        if not parents:
            return
        placeholders = ",".join("?" for _ in parents)
        rows = connection.execute(
            f"SELECT node_id FROM nodes WHERE run_id=? AND node_id IN ({placeholders})",
            (self.run_id, *parents),
        ).fetchall()
        found = {str(row["node_id"]) for row in rows}
        missing = [parent for parent in parents if parent not in found]
        if missing:
            raise KeyError(f"Unknown parent node_id(s): {sorted(missing)}")

    def record_article_event(
        self, node_id: str, event_type: str, payload: Dict[str, Any]
    ) -> None:
        """Append a validated article event to an article node.

        Unknown nodes (or nodes outside this run) raise ``KeyError``; events on
        TMM nodes, unknown event types, and malformed payloads raise
        ``ValueError``/``ArticleEventValidationError``.
        """

        normalized = validate_article_event(event_type, payload)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT node_kind FROM nodes WHERE node_id=? AND run_id=?",
                (node_id, self.run_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown node_id: {node_id}")
            if row["node_kind"] != "article":
                raise ValueError(
                    f"Node {node_id} is a TMM node; article events require an article node"
                )
            self._insert_event(connection, node_id, str(event_type), normalized)

    def set_article_stage(
        self,
        node_id: str,
        stage: ArticleStage | str,
        reason: str = "",
    ) -> None:
        self.record_article_event(
            node_id,
            "article.stage",
            {
                "schema_version": ARTICLE_EVENT_SCHEMA_VERSION,
                "stage": stage.value if isinstance(stage, ArticleStage) else stage,
                "reason": reason,
            },
        )

    def set_article_decision(
        self,
        node_id: str,
        decision: ArticleDecision | str,
        reason: str = "",
    ) -> None:
        self.record_article_event(
            node_id,
            "article.decision",
            {
                "schema_version": ARTICLE_EVENT_SCHEMA_VERSION,
                "decision": (
                    decision.value if isinstance(decision, ArticleDecision) else decision
                ),
                "reason": reason,
            },
        )

    def record_hypothesis_update(
        self,
        node_id: str,
        hypothesis_id: str,
        from_status: HypothesisStatus | str,
        to_status: HypothesisStatus | str,
        reason: str = "",
    ) -> None:
        self.record_article_event(
            node_id,
            "article.hypothesis_update",
            {
                "schema_version": ARTICLE_EVENT_SCHEMA_VERSION,
                "hypothesis_id": hypothesis_id,
                "from_status": (
                    from_status.value
                    if isinstance(from_status, HypothesisStatus)
                    else from_status
                ),
                "to_status": (
                    to_status.value if isinstance(to_status, HypothesisStatus) else to_status
                ),
                "reason": reason,
            },
        )

    def record_observation(
        self,
        node_id: str,
        observation: ObservationCard | str | Dict[str, Any],
    ) -> None:
        """Record an ``article.observation`` event from a card, id, or dict."""

        if isinstance(observation, ObservationCard):
            payload: Dict[str, Any] = observation.model_dump(mode="json")
            event_payload = {
                "schema_version": ARTICLE_EVENT_SCHEMA_VERSION,
                "observation_id": payload["observation_id"],
                "experiment_id": payload.get("experiment_id", ""),
                "artifact_ids": payload.get("artifact_ids", []),
                "summary": payload.get("summary", ""),
            }
        elif isinstance(observation, str):
            event_payload = {
                "schema_version": ARTICLE_EVENT_SCHEMA_VERSION,
                "observation_id": observation,
            }
        else:
            event_payload = dict(observation)
        self.record_article_event(node_id, "article.observation", event_payload)

    def record_coverage(
        self,
        node_id: str,
        route_id: str,
        coverage_status: CoverageStatus | str,
        reason: str = "",
    ) -> None:
        self.record_article_event(
            node_id,
            "article.coverage",
            {
                "schema_version": ARTICLE_EVENT_SCHEMA_VERSION,
                "route_id": route_id,
                "coverage_status": (
                    coverage_status.value
                    if isinstance(coverage_status, CoverageStatus)
                    else coverage_status
                ),
                "reason": reason,
            },
        )

    def record_charter(
        self,
        node_id: str,
        charter_id: str,
        stage: ArticleStage | str,
        reason: str = "",
    ) -> None:
        self.record_article_event(
            node_id,
            "article.charter",
            {
                "schema_version": ARTICLE_EVENT_SCHEMA_VERSION,
                "charter_id": charter_id,
                "stage": stage.value if isinstance(stage, ArticleStage) else stage,
                "reason": reason,
            },
        )

    def article_node(self, node_id: str) -> Dict[str, Any]:
        """Return the Article-compatible view of one article node.

        The view is derived purely from the versioned payload and the
        append-only event history: latest ``status``, latest ``stage``, latest
        decision, and all hypothesis-update events are replayed.
        """

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM nodes WHERE node_id=? AND run_id=?", (node_id, self.run_id)
            ).fetchone()
            if row is None:
                raise KeyError(node_id)
            if row["node_kind"] != "article":
                raise ValueError(f"Node {node_id} is not an article node")
            events = connection.execute(
                "SELECT event_id,event_type,payload_json,created_at FROM events "
                "WHERE node_id=? ORDER BY event_id",
                (node_id,),
            ).fetchall()
            parents = [
                item[0]
                for item in connection.execute(
                    "SELECT parent_id FROM edges WHERE child_id=? ORDER BY parent_id",
                    (node_id,),
                ).fetchall()
            ]
            children = [
                item[0]
                for item in connection.execute(
                    "SELECT child_id FROM edges WHERE parent_id=? ORDER BY child_id",
                    (node_id,),
                ).fetchall()
            ]
        history = [
            {
                "event_id": int(item["event_id"]),
                "event_type": item["event_type"],
                "payload": json.loads(item["payload_json"]),
                "created_at": float(item["created_at"]),
            }
            for item in events
        ]
        status = next(
            (
                item["payload"].get("status")
                for item in reversed(history)
                if item["event_type"] == "status"
            ),
            ExperimentStatus.proposed.value,
        )
        stage = next(
            (
                item["payload"].get("stage")
                for item in reversed(history)
                if item["event_type"] == "article.stage"
            ),
            None,
        )
        if stage is None:
            stage = json.loads(row["article_json"]).get("stage")
        decision = next(
            (
                item["payload"]
                for item in reversed(history)
                if item["event_type"] == "article.decision"
            ),
            None,
        )
        hypothesis_updates = [
            item["payload"]
            for item in history
            if item["event_type"] == "article.hypothesis_update"
        ]
        return {
            "node_id": row["node_id"],
            "run_id": row["run_id"],
            "node_kind": row["node_kind"],
            "task_hash": row["task_hash"],
            "created_at": float(row["created_at"]),
            "parent_ids": parents,
            "child_ids": children,
            "payload": json.loads(row["article_json"]),
            "status": status,
            "stage": stage,
            "decision": decision,
            "hypothesis_updates": hypothesis_updates,
            "history": history,
        }

    def article_frontier(self) -> List[Dict[str, Any]]:
        """Leaf article nodes (article nodes that are not parents of any node)."""

        with self._connect() as connection:
            node_ids = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT node_id FROM nodes
                    WHERE run_id=? AND node_kind='article' AND node_id NOT IN (
                        SELECT parent_id FROM edges WHERE run_id=?
                    ) ORDER BY created_at,node_id
                    """,
                    (self.run_id, self.run_id),
                ).fetchall()
            ]
        return [self.article_node(node_id) for node_id in node_ids]

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        node_id: str,
        event_type: str,
        payload: Dict[str, Any],
        created_at: Optional[float] = None,
    ) -> None:
        connection.execute(
            "INSERT INTO events(run_id,node_id,event_type,payload_json,created_at) VALUES(?,?,?,?,?)",
            (
                self.run_id,
                node_id,
                event_type,
                json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str),
                float(created_at or time.time()),
            ),
        )

    def record_event(self, node_id: str, event_type: str, payload: Dict[str, Any]) -> None:
        with self._lock, self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM nodes WHERE node_id=? AND run_id=?", (node_id, self.run_id)
            ).fetchone()
            if not exists:
                raise KeyError(f"Unknown node_id: {node_id}")
            self._insert_event(connection, node_id, str(event_type), dict(payload))

    def set_status(self, node_id: str, status: ExperimentStatus, **payload: Any) -> None:
        self.record_event(node_id, "status", {"status": status.value, **payload})

    def node(self, node_id: str) -> Dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM nodes WHERE node_id=? AND run_id=?", (node_id, self.run_id)
            ).fetchone()
            if row is None:
                raise KeyError(node_id)
            events = connection.execute(
                "SELECT event_id,event_type,payload_json,created_at FROM events WHERE node_id=? ORDER BY event_id",
                (node_id,),
            ).fetchall()
            parents = [
                item[0]
                for item in connection.execute(
                    "SELECT parent_id FROM edges WHERE child_id=? ORDER BY parent_id", (node_id,)
                ).fetchall()
            ]
        history = [
            {
                "event_id": int(item["event_id"]),
                "event_type": item["event_type"],
                "payload": json.loads(item["payload_json"]),
                "created_at": float(item["created_at"]),
            }
            for item in events
        ]
        status = next(
            (
                item["payload"].get("status")
                for item in reversed(history)
                if item["event_type"] == "status"
            ),
            ExperimentStatus.proposed.value,
        )
        return {
            "node_id": row["node_id"],
            "run_id": row["run_id"],
            "task_hash": row["task_hash"],
            "action": json.loads(row["action_json"]),
            "created_at": float(row["created_at"]),
            "parent_ids": parents,
            "status": status,
            "history": history,
        }

    def frontier(self) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            node_ids = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT node_id FROM nodes
                    WHERE run_id=? AND node_kind='tmm' AND node_id NOT IN (
                        SELECT parent_id FROM edges
                        WHERE run_id=? AND child_id IN (
                            SELECT node_id FROM nodes WHERE run_id=? AND node_kind='tmm'
                        )
                    ) ORDER BY created_at,node_id
                    """,
                    (self.run_id, self.run_id, self.run_id),
                ).fetchall()
            ]
        return [self.node(node_id) for node_id in node_ids]

    def export(self) -> Dict[str, Any]:
        with self._connect() as connection:
            tmm_node_ids = [
                row[0]
                for row in connection.execute(
                    "SELECT node_id FROM nodes WHERE run_id=? AND node_kind='tmm' "
                    "ORDER BY created_at,node_id",
                    (self.run_id,),
                ).fetchall()
            ]
            article_node_ids = [
                row[0]
                for row in connection.execute(
                    "SELECT node_id FROM nodes WHERE run_id=? AND node_kind='article' "
                    "ORDER BY created_at,node_id",
                    (self.run_id,),
                ).fetchall()
            ]
        return {
            "schema_version": "optical-experiment-graph.v1",
            "run_id": self.run_id,
            "nodes": [self.node(item) for item in tmm_node_ids],
            "article_schema_version": ARTICLE_GRAPH_SCHEMA_VERSION,
            "article_nodes": [self.article_node(item) for item in article_node_ids],
        }


__all__ = ["ExperimentGraph"]
