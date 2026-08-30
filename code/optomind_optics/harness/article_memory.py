"""Separated typed memory domains for the Article Scientific Harness.

Three domains, one store, strictly separated:

- ``MethodEvidence``: literature/evidence records with source, scope, query,
  excerpt hash, evidence level, time, and an optional artifact reference.
- ``RunMemoryRecord``: operational notes plus event/graph/artifact references;
  never scientific facts or physics certificates.
- ``FactRecord``: immutable scientific fact statements that MUST carry at
  least one source artifact ID.  Corrections append a superseding fact;
  existing fact payloads are never mutated in place.

The store is append-only SQLite.  Duplicate identities are rejected and all
queries are deterministic (ordered by recorded time then id).  This module
reuses no solver/compiler/orchestrator behavior and cannot authorize tasks or
physics certificates by itself.

Cross-process consistency:
- Fact corrections use a ``BEGIN IMMEDIATE`` write transaction plus a unique
  invariant ``fact_status_events(fact_id)``, so exactly one supersede per fact
  ever wins; concurrent losers raise ``FactMutationError`` and leave no
  corrected row or event behind.
- ``snapshot()`` and ``fact_records()`` read all rows through one SQLite
  connection inside a single read transaction, so callers see a consistent
  view of evidence, run memory, and facts.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError


# ---------------------------------------------------------------------------
# Typed records
# ---------------------------------------------------------------------------


class EvidenceLevel(str, Enum):
    snippet = "snippet"
    abstract = "abstract"
    full_text = "full_text"
    theory_prior = "theory_prior"
    experiment = "experiment"
    review = "review"


class FactStatus(str, Enum):
    active = "active"
    superseded = "superseded"
    retired = "retired"


class _MemoryModel(BaseModel):
    """Common base: strict required fields, tolerant forward fields."""

    model_config = ConfigDict(extra="ignore")


class MethodEvidence(_MemoryModel):
    schema_version: Literal["method-evidence.v1"] = "method-evidence.v1"
    evidence_id: str
    source: str
    scope: str
    query: str
    excerpt_hash: str
    evidence_level: EvidenceLevel
    recorded_at: Optional[str] = None
    artifact_reference: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RunMemoryRecord(_MemoryModel):
    schema_version: Literal["run-memory.v1"] = "run-memory.v1"
    memory_id: str
    run_id: str
    event_type: str
    graph_node_id: Optional[str] = None
    artifact_ids: List[str] = Field(default_factory=list)
    operational_note: str
    recorded_at: Optional[str] = None


class FactRecord(_MemoryModel):
    schema_version: Literal["fact-record.v1"] = "fact-record.v1"
    fact_id: str
    statement: str
    source_artifact_ids: List[str] = Field(min_length=1)
    supersedes_id: Optional[str] = None
    superseded_by_id: Optional[str] = None
    status: FactStatus = FactStatus.active
    recorded_at: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DuplicateRecordError(ValueError):
    """Raised when an append-only store is asked to reuse an identity."""


class FactMutationError(ValueError):
    """Raised when a fact correction violates immutability rules."""


class UnknownRecordError(KeyError):
    """Raised when a record identity is unknown."""


# ---------------------------------------------------------------------------
# Append-only store
# ---------------------------------------------------------------------------


class ArticleMemoryStore:
    """Append-only persistence for evidence, run memory, and fact records."""

    SCHEMA_VERSION = "article-memory-store.v1"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS method_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    recorded_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS run_memory (
                    memory_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    recorded_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS facts (
                    fact_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    recorded_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fact_status_events (
                    event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    fact_id TEXT NOT NULL,
                    superseded_by_id TEXT,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_fact_status ON fact_status_events(fact_id, event_seq);
                """
            )
            try:
                connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_fact_status_one_per_fact "
                    "ON fact_status_events(fact_id)"
                )
            except sqlite3.IntegrityError as exc:
                raise FactMutationError(
                    "fact_status_events contains multiple supersede records for "
                    "one fact; database is inconsistent and cannot be opened"
                ) from exc

    @staticmethod
    def _canonical_json(payload: Mapping[str, Any]) -> str:
        return json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    def _insert_record(
        self,
        connection: sqlite3.Connection,
        table: str,
        identity_column: str,
        identity: str,
        payload: Mapping[str, Any],
        now: float,
    ) -> None:
        try:
            connection.execute(
                f"INSERT INTO {table}({identity_column},payload_json,recorded_at) "
                f"VALUES(?,?,?)",
                (identity, self._canonical_json(payload), now),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateRecordError(
                f"Duplicate {identity_column} in {table}: {identity!r}"
            ) from exc

    # -- evidence -----------------------------------------------------------

    def add_evidence(
        self, evidence: MethodEvidence | Mapping[str, Any]
    ) -> MethodEvidence:
        if isinstance(evidence, RunMemoryRecord) or isinstance(evidence, FactRecord):
            raise TypeError("Only MethodEvidence records may be registered as evidence")
        model = (
            evidence
            if isinstance(evidence, MethodEvidence)
            else MethodEvidence.model_validate(evidence)
        )
        now = time.time()
        with self._lock, self._connect() as connection:
            self._insert_record(
                connection,
                "method_evidence",
                "evidence_id",
                model.evidence_id,
                model.model_dump(mode="json"),
                now,
            )
        return model

    def get_evidence(self, evidence_id: str) -> MethodEvidence:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM method_evidence WHERE evidence_id=?",
                (evidence_id,),
            ).fetchone()
        if row is None:
            raise UnknownRecordError(f"Unknown evidence_id: {evidence_id}")
        return MethodEvidence.model_validate_json(row["payload_json"])

    def evidence_records(self) -> List[MethodEvidence]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM method_evidence ORDER BY recorded_at,evidence_id"
            ).fetchall()
        return [MethodEvidence.model_validate_json(row["payload_json"]) for row in rows]

    # -- run memory ---------------------------------------------------------

    def add_run_memory(
        self, record: RunMemoryRecord | Mapping[str, Any]
    ) -> RunMemoryRecord:
        if not isinstance(record, RunMemoryRecord) and not isinstance(record, Mapping):
            raise TypeError("Run memory requires a RunMemoryRecord or mapping")
        if isinstance(record, MethodEvidence) or isinstance(record, FactRecord):
            raise TypeError("Only RunMemoryRecord records may be registered as run memory")
        model = (
            record
            if isinstance(record, RunMemoryRecord)
            else RunMemoryRecord.model_validate(record)
        )
        now = time.time()
        with self._lock, self._connect() as connection:
            self._insert_record(
                connection,
                "run_memory",
                "memory_id",
                model.memory_id,
                model.model_dump(mode="json"),
                now,
            )
        return model

    def get_run_memory(self, memory_id: str) -> RunMemoryRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM run_memory WHERE memory_id=?", (memory_id,)
            ).fetchone()
        if row is None:
            raise UnknownRecordError(f"Unknown memory_id: {memory_id}")
        return RunMemoryRecord.model_validate_json(row["payload_json"])

    def run_memory_records(self) -> List[RunMemoryRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM run_memory ORDER BY recorded_at,memory_id"
            ).fetchall()
        return [RunMemoryRecord.model_validate_json(row["payload_json"]) for row in rows]

    # -- facts --------------------------------------------------------------

    def add_fact(self, fact: FactRecord | Mapping[str, Any]) -> FactRecord:
        """Register an immutable fact; requires at least one source artifact."""

        if isinstance(fact, RunMemoryRecord) or isinstance(fact, MethodEvidence):
            raise TypeError("Only FactRecord records may be registered as facts")
        if isinstance(fact, FactRecord):
            model = fact
        else:
            model = FactRecord.model_validate(fact)
        now = time.time()
        with self._lock, self._connect() as connection:
            self._insert_record(
                connection,
                "facts",
                "fact_id",
                model.fact_id,
                model.model_dump(mode="json"),
                now,
            )
        return self.get_fact(model.fact_id)

    def supersede_fact(
        self, existing_fact_id: str, fact: FactRecord | Mapping[str, Any]
    ) -> FactRecord:
        """Append a correction; the superseded payload is never mutated.

        Runs inside a ``BEGIN IMMEDIATE`` transaction so concurrent processes
        cannot both supersede the same fact: the loser fails with
        ``FactMutationError`` and leaves no corrected row or event behind.
        """

        if isinstance(fact, RunMemoryRecord) or isinstance(fact, MethodEvidence):
            raise TypeError("A fact correction must be a FactRecord")
        model = fact if isinstance(fact, FactRecord) else FactRecord.model_validate(fact)
        if model.fact_id == existing_fact_id:
            raise FactMutationError("A fact cannot supersede itself")
        if model.supersedes_id not in (None, existing_fact_id):
            raise FactMutationError(
                f"Fact {model.fact_id!r} declares supersedes_id "
                f"{model.supersedes_id!r} instead of {existing_fact_id!r}"
            )
        now = time.time()
        with self._lock:
            connection = self._connect()
            committed = False
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT fact_id FROM facts WHERE fact_id=?", (existing_fact_id,)
                ).fetchone()
                if existing is None:
                    raise UnknownRecordError(f"Unknown fact_id: {existing_fact_id}")
                current = self._fact_view_locked(connection, existing_fact_id)
                if current.status == FactStatus.superseded:
                    raise FactMutationError(
                        f"Fact {existing_fact_id!r} is already superseded and immutable"
                    )
                corrected = model.model_copy(
                    update={
                        "supersedes_id": existing_fact_id,
                        "status": FactStatus.active,
                        "superseded_by_id": None,
                    }
                )
                self._insert_record(
                    connection,
                    "facts",
                    "fact_id",
                    corrected.fact_id,
                    corrected.model_dump(mode="json"),
                    now,
                )
                connection.execute(
                    "INSERT INTO fact_status_events(fact_id,superseded_by_id,created_at) "
                    "VALUES(?,?,?)",
                    (existing_fact_id, corrected.fact_id, now),
                )
                connection.execute("COMMIT")
                committed = True
            except DuplicateRecordError:
                raise
            except sqlite3.IntegrityError as exc:
                raise FactMutationError(
                    f"Fact {existing_fact_id!r} is already superseded and immutable"
                ) from exc
            except BaseException:
                raise
            finally:
                if not committed:
                    try:
                        connection.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                connection.close()
        return self.get_fact(corrected.fact_id)

    def get_fact(self, fact_id: str) -> FactRecord:
        with self._connect() as connection:
            return self._fact_view_locked(connection, fact_id)

    def _fact_view_locked(
        self, connection: sqlite3.Connection, fact_id: str
    ) -> FactRecord:
        row = connection.execute(
            "SELECT payload_json FROM facts WHERE fact_id=?", (fact_id,)
        ).fetchone()
        if row is None:
            raise UnknownRecordError(f"Unknown fact_id: {fact_id}")
        payload = json.loads(row["payload_json"])
        event = connection.execute(
            "SELECT superseded_by_id FROM fact_status_events "
            "WHERE fact_id=? ORDER BY event_seq DESC LIMIT 1",
            (fact_id,),
        ).fetchone()
        superseded_by = event["superseded_by_id"] if event is not None else None
        status = (
            FactStatus.superseded
            if superseded_by is not None
            else FactStatus(payload.get("status", FactStatus.active.value))
        )
        return FactRecord.model_validate(
            {**payload, "status": status.value, "superseded_by_id": superseded_by}
        )

    def fact_records(self) -> List[FactRecord]:
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            rows = connection.execute(
                "SELECT fact_id FROM facts ORDER BY recorded_at,fact_id"
            ).fetchall()
            result = [
                self._fact_view_locked(connection, row["fact_id"]) for row in rows
            ]
            connection.execute("COMMIT")
            return result
        except BaseException:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()

    def fact_lineage(self, fact_id: str) -> List[FactRecord]:
        """Deterministic lineage from the origin fact to the requested fact."""

        chain: List[str] = []
        seen: set[str] = set()
        current = fact_id
        while current not in seen:
            seen.add(current)
            chain.append(current)
            fact = self.get_fact(current)
            if fact.supersedes_id is None:
                break
            current = fact.supersedes_id
        chain.reverse()
        return [self.get_fact(item) for item in chain]

    # -- snapshot -----------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        """Deterministic, consistent view of all persisted records.

        All three domains are read through one connection inside a single
        SQLite read transaction, so the snapshot is internally consistent even
        while another process is writing.  Ordering stays deterministic
        (recorded time, then identity).
        """

        connection = self._connect()
        try:
            connection.execute("BEGIN")
            evidence_rows = connection.execute(
                "SELECT payload_json FROM method_evidence ORDER BY recorded_at,evidence_id"
            ).fetchall()
            memory_rows = connection.execute(
                "SELECT payload_json FROM run_memory ORDER BY recorded_at,memory_id"
            ).fetchall()
            fact_rows = connection.execute(
                "SELECT fact_id FROM facts ORDER BY recorded_at,fact_id"
            ).fetchall()
            evidence = [
                MethodEvidence.model_validate_json(row["payload_json"]).model_dump(
                    mode="json"
                )
                for row in evidence_rows
            ]
            memory = [
                RunMemoryRecord.model_validate_json(row["payload_json"]).model_dump(
                    mode="json"
                )
                for row in memory_rows
            ]
            facts = [
                self._fact_view_locked(connection, row["fact_id"]).model_dump(
                    mode="json"
                )
                for row in fact_rows
            ]
            connection.execute("COMMIT")
        except BaseException:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()
        return {
            "schema_version": self.SCHEMA_VERSION,
            "evidence": evidence,
            "run_memory": memory,
            "facts": facts,
        }


__all__ = [
    "ArticleMemoryStore",
    "DuplicateRecordError",
    "EvidenceLevel",
    "FactMutationError",
    "FactRecord",
    "FactStatus",
    "MethodEvidence",
    "RunMemoryRecord",
    "UnknownRecordError",
]
