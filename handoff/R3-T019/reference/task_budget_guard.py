"""Task-scoped, fail-closed adapter for the *existing* native budget ledger.

The caller loads the repository's GlobalBudgetLedger and supplies its instance.
This module never imports/copies that runtime, creates a ledger database, resets
spend, changes its ceiling, reconciles holds, or performs network operations.

For each logical request the transport receives guard.for_request(key, sha256).
Call lookup_completed before dispatch. Only verified cached bytes may replay;
reserve never repeats an existing request. A reservation is conservatively an
attempt, even if the process dies before opening the HTTP connection.

Only namespaced, append-only r3_task_* tables/triggers are added. Native reserve
cannot be called here: its insert and our gates/linkage must share one SQLite
BEGIN IMMEDIATE transaction. Settlement remains the native ledger's job, so its
billing, usage, provider identity and transport telemetry remain authoritative.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Iterator, Mapping
import uuid


MAX_AUTHORIZED_GLOBAL_CNY = Decimal("140")
_SCHEMA_VERSION = 1
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_REQUIRED_NATIVE_COLUMNS = {
    "reservation_id", "call_id", "amount_cny", "actual_cny", "status", "created_at",
    "usage_json", "returned_model", "finish_reason", "request_id",
    "raw_response_sha256", "status_code", "provider_error_code", "key_index",
    "attempt", "request_metadata_json",
}


class TaskBudgetError(RuntimeError):
    """A terminal local refusal: the caller must not retry it as a network error."""

    def __init__(self, code: str, **record: Any):
        self.code = code
        self.record = record
        self.transient = False
        super().__init__(code)


def _decimal(value: Any, field: str, *, positive: bool = False) -> Decimal:
    try:
        if isinstance(value, bool) or value is None:
            raise ValueError
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or (positive and result == 0):
            raise ValueError
        return result
    except (ValueError, InvalidOperation, TypeError):
        raise TaskBudgetError("invalid_budget_value", field=field) from None


def _text(value: Any, field: str, *, max_length: int = 1024) -> str:
    if (not isinstance(value, str) or not value.strip() or value != value.strip()
            or "\x00" in value or len(value) > max_length):
        raise TaskBudgetError("invalid_budget_identity", field=field)
    return value


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise TaskBudgetError("invalid_sha256", field=field)
    return value


@dataclass(frozen=True)
class _TaskConfig:
    task_id: str
    lane: str
    task_limit_cny: Decimal
    max_calls: int
    checkpoint_cny: Decimal | None
    global_limit_cny: Decimal

    def database_values(self) -> tuple[Any, ...]:
        return (self.task_id, self.lane, str(self.task_limit_cny), self.max_calls,
                str(self.checkpoint_cny) if self.checkpoint_cny is not None else None,
                str(self.global_limit_cny), _SCHEMA_VERSION)


class TaskBudgetGuard:
    """Immutable task limits enforced against one durable native ledger.

    checkpoint_approval_reference must already have been verified by the
    wrapper against the explicit approval receipt. This adapter records that
    reference; a filename or reference alone is not an authorization verifier.
    A new approval may be appended on resume, but never replaced or revoked by
    changing this object's constructor arguments.
    """

    def __init__(self, native_ledger: Any, *, task_id: str, lane: str,
                 task_limit_cny: float, max_calls: int,
                 checkpoint_cny: float | None = None,
                 checkpoint_approval_reference: str | None = None,
                 global_limit_cny: float = 140.0):
        global_limit = _decimal(global_limit_cny, "global_limit_cny", positive=True)
        task_limit = _decimal(task_limit_cny, "task_limit_cny", positive=True)
        checkpoint = (_decimal(checkpoint_cny, "checkpoint_cny", positive=True)
                      if checkpoint_cny is not None else None)
        if global_limit > MAX_AUTHORIZED_GLOBAL_CNY:
            raise TaskBudgetError("authorized_global_limit_exceeded")
        if task_limit > global_limit:
            raise TaskBudgetError("task_limit_exceeds_global_limit")
        if checkpoint is not None and checkpoint > global_limit:
            raise TaskBudgetError("checkpoint_exceeds_global_limit")
        if isinstance(max_calls, bool) or not isinstance(max_calls, int) or max_calls <= 0:
            raise TaskBudgetError("invalid_max_calls")
        if checkpoint_approval_reference is not None:
            _text(checkpoint_approval_reference, "checkpoint_approval_reference", max_length=4096)
            if checkpoint is None:
                raise TaskBudgetError("checkpoint_required_for_approval")
        if (not getattr(native_ledger, "path", None)
                or not callable(getattr(native_ledger, "settle", None))
                or not callable(getattr(native_ledger, "as_dict", None))):
            raise TaskBudgetError("durable_native_ledger_required")
        self._native = native_ledger
        try:
            self._path = Path(native_ledger.path).resolve(strict=True)
            if not self._path.is_file():
                raise OSError
        except (OSError, RuntimeError):
            raise TaskBudgetError("existing_ledger_file_required") from None
        self._config = _TaskConfig(_text(task_id, "task_id", max_length=256),
                                   _text(lane, "lane", max_length=64), task_limit,
                                   max_calls, checkpoint, global_limit)
        self._assert_native_binding()
        with self._transaction() as db:
            self._validate_native_schema(db)
            self._validate_global_limit(db)
            self._create_schema(db)
            db.execute(
                "INSERT OR IGNORE INTO r3_task_budgets "
                "(task_id,lane,task_limit_cny,max_calls,checkpoint_cny,global_limit_cny,"
                "schema_version,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (*self._config.database_values(), time.time()),
            )
            self._validate_task(db)
            self._validate_links(db)
            if checkpoint_approval_reference is not None:
                row = db.execute("SELECT approval_reference FROM r3_task_checkpoint_approvals "
                                 "WHERE task_id=?", (self.task_id,)).fetchone()
                if row is not None and row[0] != checkpoint_approval_reference:
                    raise TaskBudgetError("checkpoint_approval_conflict", task_id=self.task_id)
                db.execute("INSERT OR IGNORE INTO r3_task_checkpoint_approvals "
                           "(task_id,checkpoint_cny,approval_reference,created_at) VALUES (?,?,?,?)",
                           (self.task_id, str(checkpoint), checkpoint_approval_reference, time.time()))

    @property
    def task_id(self) -> str:
        return self._config.task_id

    @property
    def lane(self) -> str:
        return self._config.lane

    @property
    def path(self) -> Path:
        return self._path

    @property
    def task_limit_cny(self) -> float:
        return float(self._config.task_limit_cny)

    @property
    def max_calls(self) -> int:
        return self._config.max_calls

    @property
    def limit_cny(self) -> float:
        return float(self._config.global_limit_cny)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        # mode=rw is deliberate: removal or a typo must not create an empty DB.
        with closing(sqlite3.connect(self._path.as_uri() + "?mode=rw", uri=True,
                                     timeout=30.0)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=30000")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def _assert_native_binding(self) -> None:
        try:
            bound_path = Path(self._native.path).resolve(strict=True)
        except (OSError, TypeError, RuntimeError):
            raise TaskBudgetError("native_ledger_binding_changed") from None
        if bound_path != self._path:
            raise TaskBudgetError("native_ledger_binding_changed")

    @staticmethod
    def _validate_native_schema(db: sqlite3.Connection) -> None:
        columns = {row[1] for row in db.execute("PRAGMA table_info(reservations)")}
        meta = {row[1] for row in db.execute("PRAGMA table_info(budget_meta)")}
        if not _REQUIRED_NATIVE_COLUMNS.issubset(columns) or not {"key", "value"}.issubset(meta):
            raise TaskBudgetError("unsupported_native_ledger_schema")

    @staticmethod
    def _create_schema(db: sqlite3.Connection) -> None:
        # Do not use executescript: it may commit before the caller's gates.
        statements = [
            "CREATE TABLE IF NOT EXISTS r3_task_budgets ("
            "task_id TEXT PRIMARY KEY,lane TEXT NOT NULL,task_limit_cny TEXT NOT NULL,"
            "max_calls INTEGER NOT NULL,checkpoint_cny TEXT,global_limit_cny TEXT NOT NULL,"
            "schema_version INTEGER NOT NULL,created_at REAL NOT NULL)",
            "CREATE TABLE IF NOT EXISTS r3_task_calls ("
            "task_id TEXT NOT NULL,request_key TEXT NOT NULL,request_hash TEXT NOT NULL,"
            "reservation_id TEXT NOT NULL UNIQUE,call_id TEXT NOT NULL UNIQUE,"
            "created_at REAL NOT NULL,PRIMARY KEY(task_id,request_key),"
            "FOREIGN KEY(task_id) REFERENCES r3_task_budgets(task_id),"
            "FOREIGN KEY(reservation_id) REFERENCES reservations(reservation_id))",
            "CREATE TABLE IF NOT EXISTS r3_task_responses ("
            "task_id TEXT NOT NULL,request_key TEXT NOT NULL,response_path TEXT NOT NULL,"
            "response_sha256 TEXT NOT NULL,created_at REAL NOT NULL,"
            "PRIMARY KEY(task_id,request_key),FOREIGN KEY(task_id,request_key) "
            "REFERENCES r3_task_calls(task_id,request_key))",
            "CREATE TABLE IF NOT EXISTS r3_task_checkpoint_approvals ("
            "task_id TEXT PRIMARY KEY,checkpoint_cny TEXT NOT NULL,"
            "approval_reference TEXT NOT NULL,created_at REAL NOT NULL,"
            "FOREIGN KEY(task_id) REFERENCES r3_task_budgets(task_id))",
        ]
        for statement in statements:
            db.execute(statement)
        for table in ("r3_task_budgets", "r3_task_calls", "r3_task_responses",
                      "r3_task_checkpoint_approvals"):
            for event in ("UPDATE", "DELETE"):
                db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{event.lower()} "
                           f"BEFORE {event} ON {table} BEGIN "
                           "SELECT RAISE(ABORT,'r3_budget_audit_is_append_only'); END")

    def _validate_global_limit(self, db: sqlite3.Connection) -> None:
        row = db.execute("SELECT value FROM budget_meta WHERE key='limit_cny'").fetchone()
        if row is None:
            raise TaskBudgetError("explicit_global_limit_required")
        stored = _decimal(row[0], "stored_global_limit_cny", positive=True)
        native = _decimal(getattr(self._native, "limit_cny", None),
                          "native_global_limit_cny", positive=True)
        if stored != self._config.global_limit_cny or native != self._config.global_limit_cny:
            raise TaskBudgetError("global_budget_limit_conflict",
                                  configured_limit_cny=self.limit_cny,
                                  stored_limit_cny=float(stored), native_limit_cny=float(native))
        if stored > MAX_AUTHORIZED_GLOBAL_CNY:
            raise TaskBudgetError("authorized_global_limit_exceeded")

    def _validate_task(self, db: sqlite3.Connection) -> None:
        row = db.execute("SELECT task_id,lane,task_limit_cny,max_calls,checkpoint_cny,"
                         "global_limit_cny,schema_version FROM r3_task_budgets WHERE task_id=?",
                         (self.task_id,)).fetchone()
        if row is None:
            raise TaskBudgetError("task_registration_missing", task_id=self.task_id)
        try:
            actual = (row[0], row[1], _decimal(row[2], "stored_task_limit", positive=True),
                      row[3], _decimal(row[4], "stored_checkpoint", positive=True)
                      if row[4] is not None else None,
                      _decimal(row[5], "stored_global_limit", positive=True), row[6])
            expected = (self.task_id, self.lane, self._config.task_limit_cny, self.max_calls,
                        self._config.checkpoint_cny, self._config.global_limit_cny, _SCHEMA_VERSION)
            if actual != expected:
                raise TaskBudgetError("task_configuration_conflict", task_id=self.task_id)
        except (IndexError, TypeError):
            raise TaskBudgetError("task_configuration_conflict", task_id=self.task_id) from None

    @staticmethod
    def _validate_links(db: sqlite3.Connection) -> None:
        orphan = db.execute(
            "SELECT c.reservation_id FROM r3_task_calls c "
            "LEFT JOIN reservations r ON r.reservation_id=c.reservation_id "
            "LEFT JOIN r3_task_budgets t ON t.task_id=c.task_id "
            "WHERE r.reservation_id IS NULL OR t.task_id IS NULL OR r.call_id != c.call_id LIMIT 1"
        ).fetchone()
        if orphan is not None:
            raise TaskBudgetError("task_reservation_link_corrupt", reservation_id=orphan[0])

    @staticmethod
    def _cost_rows(rows: list[sqlite3.Row]) -> tuple[Decimal, Decimal, Decimal]:
        actual = Decimal("0")
        held = Decimal("0")
        exposure = Decimal("0")
        for row in rows:
            amount = _decimal(row["amount_cny"], "stored_reservation_amount")
            spent = (_decimal(row["actual_cny"], "stored_actual_amount")
                     if row["actual_cny"] is not None else None)
            if spent is not None:
                actual += spent
            if row["status"] == "settled":
                if spent is None:
                    raise TaskBudgetError("settled_cost_evidence_missing",
                                          reservation_id=row["reservation_id"])
                exposure += spent
            else:
                held += amount
                exposure += amount
        return actual, held, exposure

    def _request_row(self, db: sqlite3.Connection, request_key: str) -> sqlite3.Row | None:
        return db.execute(
            "SELECT c.*,r.status,r.amount_cny,r.actual_cny,s.response_path,s.response_sha256 "
            "FROM r3_task_calls c JOIN reservations r ON r.reservation_id=c.reservation_id "
            "LEFT JOIN r3_task_responses s ON s.task_id=c.task_id AND s.request_key=c.request_key "
            "WHERE c.task_id=? AND c.request_key=?", (self.task_id, request_key)
        ).fetchone()

    @staticmethod
    def _check_request_hash(row: sqlite3.Row, request_hash: str) -> None:
        if row["request_hash"] != request_hash:
            raise TaskBudgetError("request_identity_conflict", request_key=row["request_key"])

    def for_request(self, request_key: str, request_hash: str) -> "RequestBudgetLedger":
        return RequestBudgetLedger(self, _text(request_key, "request_key"),
                                   _sha256(request_hash, "request_hash"))

    def _reserve(self, request_key: str, request_hash: str,
                 amount_cny: float, call_id: str) -> dict[str, Any]:
        amount = _decimal(amount_cny, "reservation_amount_cny")
        call_id = _text(call_id, "call_id")
        # SQLite REAL must represent the requested finite amount; avoid inf/0
        # underflow when external callers supply non-float decimal values.
        if _decimal(float(amount), "reservation_amount_cny") != amount:
            raise TaskBudgetError("reservation_amount_not_representable")
        self._assert_native_binding()
        with self._transaction() as db:
            self._validate_task(db)
            self._validate_global_limit(db)
            self._validate_links(db)
            prior = self._request_row(db, request_key)
            if prior is not None:
                self._check_request_hash(prior, request_hash)
                raise TaskBudgetError("request_already_attempted", request_key=request_key,
                                      reservation_id=prior["reservation_id"], status=prior["status"])
            rows = db.execute("SELECT reservation_id,amount_cny,actual_cny,status FROM reservations").fetchall()
            # Status, not its monetary sum, is the gate. Zero-cost holds and
            # statuses unknown to this adapter remain unresolved spend.
            pending = next((row for row in rows if row["status"] != "settled"), None)
            if pending is not None:
                raise TaskBudgetError("unresolved_global_reservation",
                                      reservation_id=pending["reservation_id"], status=pending["status"])
            _, _, global_used = self._cost_rows(rows)
            task_rows = db.execute(
                "SELECT r.reservation_id,r.amount_cny,r.actual_cny,r.status "
                "FROM r3_task_calls c JOIN reservations r ON r.reservation_id=c.reservation_id "
                "WHERE c.task_id=?", (self.task_id,)
            ).fetchall()
            _, _, task_used = self._cost_rows(task_rows)
            if len(task_rows) >= self.max_calls:
                raise TaskBudgetError("task_max_calls_exceeded", task_id=self.task_id,
                                      attempted_calls=len(task_rows), max_calls=self.max_calls)
            if global_used >= self._config.global_limit_cny or global_used + amount > self._config.global_limit_cny:
                raise TaskBudgetError("global_budget_exceeded", used_cny=float(global_used),
                                      amount_cny=float(amount), limit_cny=self.limit_cny)
            if task_used >= self._config.task_limit_cny or task_used + amount > self._config.task_limit_cny:
                raise TaskBudgetError("task_budget_exceeded", task_id=self.task_id,
                                      used_cny=float(task_used), amount_cny=float(amount),
                                      limit_cny=self.task_limit_cny)
            checkpoint = self._config.checkpoint_cny
            if checkpoint is not None and (global_used >= checkpoint or global_used + amount > checkpoint):
                approval = db.execute("SELECT checkpoint_cny,approval_reference "
                                      "FROM r3_task_checkpoint_approvals WHERE task_id=?",
                                      (self.task_id,)).fetchone()
                if approval is None or _decimal(approval[0], "approved_checkpoint") != checkpoint or not approval[1]:
                    raise TaskBudgetError("checkpoint_approval_required", task_id=self.task_id,
                                          used_cny=float(global_used), amount_cny=float(amount),
                                          checkpoint_cny=float(checkpoint))
            if db.execute("SELECT 1 FROM reservations WHERE call_id=? LIMIT 1", (call_id,)).fetchone():
                raise TaskBudgetError("call_identity_conflict", call_id=call_id)
            reservation_id = "res-" + uuid.uuid4().hex
            created_at = time.time()
            db.execute("INSERT INTO reservations "
                       "(reservation_id,call_id,amount_cny,actual_cny,status,created_at) "
                       "VALUES (?,?,?,NULL,'reserved',?)",
                       (reservation_id, call_id, float(amount), created_at))
            db.execute("INSERT INTO r3_task_calls "
                       "(task_id,request_key,request_hash,reservation_id,call_id,created_at) "
                       "VALUES (?,?,?,?,?,?)",
                       (self.task_id, request_key, request_hash, reservation_id, call_id, created_at))
        result = {"reservation_id": reservation_id, "call_id": call_id,
                  "amount_cny": float(amount), "status": "reserved"}
        # Keep the native object's optional in-process audit view useful.
        # Durable accounting above never depends on this mutable list/cache.
        if isinstance(getattr(self._native, "reservations", None), list):
            self._native.reservations.append(dict(result))
        refresh = getattr(self._native, "_refresh_from_db", None)
        if callable(refresh):
            refresh()
        return result

    def _settle(self, request_key: str, request_hash: str, reservation_id: str,
                actual_cny: float | None, *, uncertain: bool = False,
                telemetry: Mapping[str, Any] | None = None) -> None:
        self._assert_native_binding()
        with self._transaction() as db:
            self._validate_task(db)
            self._validate_links(db)
            row = self._request_row(db, request_key)
            if row is None or row["reservation_id"] != reservation_id:
                raise TaskBudgetError("reservation_task_mismatch", task_id=self.task_id,
                                      request_key=request_key, reservation_id=reservation_id)
            self._check_request_hash(row, request_hash)
        # Do not cap/clamp actual cost or copy settlement SQL. Actual spend can
        # exceed the reservation; preserve it and block later dispatches.
        # The append-only link exists before this call and survives any crash.
        self._native.settle(reservation_id, actual_cny, uncertain=uncertain, telemetry=telemetry)

    @staticmethod
    def _verified_response(path: Path, expected_sha256: str) -> bytes:
        try:
            if not path.is_file():
                raise OSError
            content = path.read_bytes()
        except (OSError, RuntimeError):
            raise TaskBudgetError("response_cache_missing") from None
        if not content:
            raise TaskBudgetError("response_cache_empty")
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            raise TaskBudgetError("response_cache_hash_mismatch")
        return content

    def lookup_completed(self, request_key: str, request_hash: str) -> dict[str, Any] | None:
        request_key = _text(request_key, "request_key")
        request_hash = _sha256(request_hash, "request_hash")
        self._assert_native_binding()
        with self._transaction() as db:
            self._validate_task(db)
            self._validate_links(db)
            row = self._request_row(db, request_key)
            if row is None:
                return None
            self._check_request_hash(row, request_hash)
            if row["status"] != "settled":
                raise TaskBudgetError("request_not_settled", request_key=request_key, status=row["status"])
            self._cost_rows([row])
            if not row["response_path"] or not row["response_sha256"]:
                raise TaskBudgetError("response_cache_missing", request_key=request_key)
            digest = _sha256(row["response_sha256"], "response_sha256")
            content = self._verified_response(Path(row["response_path"]), digest)
            return {"task_id": self.task_id, "request_key": request_key,
                    "request_hash": request_hash, "reservation_id": row["reservation_id"],
                    "call_id": row["call_id"], "actual_cny": row["actual_cny"],
                    "response_path": row["response_path"], "response_sha256": digest,
                    "response_bytes": content, "status": "settled"}

    def record_response(self, request_key: str, path: str | Path, sha256: str) -> dict[str, Any]:
        request_key = _text(request_key, "request_key")
        digest = _sha256(sha256, "response_sha256")
        try:
            response_path = Path(path).resolve(strict=True)
        except (OSError, RuntimeError):
            raise TaskBudgetError("response_cache_missing") from None
        self._assert_native_binding()
        with self._transaction() as db:
            self._validate_task(db)
            self._validate_links(db)
            row = self._request_row(db, request_key)
            if row is None:
                raise TaskBudgetError("request_not_attempted", request_key=request_key)
            if row["status"] != "settled":
                raise TaskBudgetError("request_not_settled", request_key=request_key, status=row["status"])
            self._cost_rows([row])
            self._verified_response(response_path, digest)
            if row["response_path"] is not None:
                if row["response_path"] != str(response_path) or row["response_sha256"] != digest:
                    raise TaskBudgetError("response_cache_registration_conflict", request_key=request_key)
            else:
                db.execute("INSERT INTO r3_task_responses "
                           "(task_id,request_key,response_path,response_sha256,created_at) VALUES (?,?,?,?,?)",
                           (self.task_id, request_key, str(response_path), digest, time.time()))
        return {"task_id": self.task_id, "request_key": request_key,
                "response_path": str(response_path), "response_sha256": digest}

    def as_dict(self) -> dict[str, Any]:
        self._assert_native_binding()
        native = self._native.as_dict()
        with self._transaction() as db:
            self._validate_task(db)
            self._validate_links(db)
            rows = db.execute("SELECT r.reservation_id,r.amount_cny,r.actual_cny,r.status "
                              "FROM r3_task_calls c JOIN reservations r ON r.reservation_id=c.reservation_id "
                              "WHERE c.task_id=?", (self.task_id,)).fetchall()
            actual, held, exposure = self._cost_rows(rows)
            approval = db.execute("SELECT approval_reference FROM r3_task_checkpoint_approvals "
                                  "WHERE task_id=?", (self.task_id,)).fetchone()
            task = {"task_id": self.task_id, "lane": self.lane,
                    "limit_cny": self.task_limit_cny, "max_calls": self.max_calls,
                    "attempted_calls": len(rows), "actual_cny": float(actual),
                    "reserved_cny": float(held), "exposure_cny": float(exposure),
                    "remaining_cny": max(0.0, float(self._config.task_limit_cny - exposure)),
                    "checkpoint_cny": float(self._config.checkpoint_cny)
                    if self._config.checkpoint_cny is not None else None,
                    "checkpoint_approval_reference": approval[0] if approval else None,
                    "unresolved_count": sum(row["status"] != "settled" for row in rows)}
        return {**native, "task_budget": task}


@dataclass(frozen=True)
class RequestBudgetLedger:
    """A native QwenDirectClient-compatible adapter for exactly one request."""

    guard: TaskBudgetGuard
    request_key: str
    request_hash: str

    @property
    def path(self) -> Path:
        return self.guard.path

    @property
    def limit_cny(self) -> float:
        return self.guard.limit_cny

    def reserve(self, amount_cny: float, call_id: str) -> dict[str, Any]:
        return self.guard._reserve(self.request_key, self.request_hash, amount_cny, call_id)

    def settle(self, reservation_id: str, actual_cny: float | None, *,
               uncertain: bool = False, telemetry: Mapping[str, Any] | None = None) -> None:
        self.guard._settle(self.request_key, self.request_hash, reservation_id,
                           actual_cny, uncertain=uncertain, telemetry=telemetry)

    def as_dict(self) -> dict[str, Any]:
        return self.guard.as_dict()
