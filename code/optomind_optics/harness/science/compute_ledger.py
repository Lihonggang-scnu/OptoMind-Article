"""Durable computational resource meter. Counts are resource units, never cash.

Reservation is committed before work. Interrupted calls retain their reservation;
failed calls remain charged. A retry is new work and is charged again. Parent
links are explanatory: usage sums resource events once, never parent totals.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import json
import sqlite3
from time import perf_counter

_ACTIVE = ContextVar("science_compute_ledger", default=None)
_PARENT = ContextVar("science_compute_parent", default=None)
_SCOPE = ContextVar("science_compute_scope", default="research")


class ComputeBudgetExceeded(RuntimeError):
    pass


class ComputeLedger:
    def __init__(self, path, limits):
        if not limits or any(type(v) is not int or v < 1 for v in limits.values()):
            raise ValueError("positive integer compute resource limits required")
        self.limits = dict(limits)
        self.db = sqlite3.connect(str(path), timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("CREATE TABLE IF NOT EXISTS config(value TEXT); CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,parent INTEGER,scope TEXT,resource TEXT,quantity INTEGER,status TEXT,error TEXT,seconds REAL); CREATE TABLE IF NOT EXISTS totals(resource TEXT PRIMARY KEY,quantity INTEGER NOT NULL);")
        if "seconds" not in {r[1] for r in self.db.execute("PRAGMA table_info(events)")}:
            self.db.execute("ALTER TABLE events ADD COLUMN seconds REAL")
        saved = self.db.execute("SELECT value FROM config").fetchone()
        payload = json.dumps(self.limits, sort_keys=True)
        if saved and saved[0] != payload:
            raise ValueError("compute recovery limits changed")
        if not saved:
            self.db.execute("INSERT INTO config VALUES(?)", (payload,)); self.db.commit()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute("INSERT OR IGNORE INTO totals SELECT resource,SUM(quantity) FROM events GROUP BY resource")
            for resource in self.limits:self.db.execute("INSERT OR IGNORE INTO totals VALUES(?,0)",(resource,))
            expected=dict(self.db.execute("SELECT resource,SUM(quantity) FROM events GROUP BY resource"))
            actual=dict(self.db.execute("SELECT resource,quantity FROM totals"))
            if any(actual.get(r,0)!=expected.get(r,0) for r in set(actual)|set(expected)):
                raise ValueError("compute aggregate does not reconcile with event ledger")
            self.db.commit()
        except BaseException:
            self.db.rollback();raise

    def reserve(self, resource, quantity=1):
        if resource not in self.limits or type(quantity) is not int or quantity < 0:
            raise ValueError("undeclared compute resource or invalid quantity")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            used = self.db.execute("SELECT quantity FROM totals WHERE resource=?", (resource,)).fetchone()[0]
            if used + quantity > self.limits[resource]:
                raise ComputeBudgetExceeded(f"compute budget exhausted: {resource}")
            row = self.db.execute("INSERT INTO events(parent,scope,resource,quantity,status) VALUES(?,?,?,?,?)", (_PARENT.get(), _SCOPE.get(), resource, quantity, "reserved"))
            self.db.execute("UPDATE totals SET quantity=quantity+? WHERE resource=?",(quantity,resource))
            self.db.commit(); return row.lastrowid
        except BaseException:
            self.db.rollback(); raise

    def settle(self, event, error=None, seconds=0.):
        with self.db:
            self.db.execute("UPDATE events SET status=?,error=?,seconds=? WHERE id=?", ("failed_consumed" if error else "settled", error, seconds, event))

    def snapshot(self):
        usage = dict(self.db.execute("SELECT resource,SUM(quantity) FROM events GROUP BY resource"))
        states = dict(self.db.execute("SELECT status,COUNT(*) FROM events GROUP BY status"))
        return {"limits": self.limits, "usage": usage, "events_by_status": states,
                "units": "actual attempted calls and solver wavelength-points; not provider cash",
                "unknown_reserved_events": states.get("reserved", 0),
                "by_scope":[{"scope":a,"resource":b,"quantity":c} for a,b,c in self.db.execute("SELECT scope,resource,SUM(quantity) FROM events GROUP BY scope,resource")],
                "top_level_wall_seconds":float(self.db.execute("SELECT COALESCE(SUM(seconds),0) FROM events WHERE parent IS NULL").fetchone()[0]),
                "wall_time_scope":"observed completed/failed outer operations; interrupted duration unknown; not money"}

    def close(self): self.db.close()


@contextmanager
def active_compute_ledger(ledger):
    token = _ACTIVE.set(ledger)
    try: yield
    finally: _ACTIVE.reset(token)


@contextmanager
def compute_scope(name):
    token = _SCOPE.set(name)
    try: yield
    finally: _SCOPE.reset(token)


@contextmanager
def computational_event(resource, quantity=1):
    ledger = _ACTIVE.get()
    if ledger is None:
        yield
        return
    event = ledger.reserve(resource, quantity)
    token = _PARENT.set(event)
    started=perf_counter()
    try:
        yield
    except BaseException as exc:
        ledger.settle(event, type(exc).__name__, perf_counter()-started)
        raise
    else: ledger.settle(event, seconds=perf_counter()-started)
    finally: _PARENT.reset(token)


def meter_prediction(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with computational_event("model_predictions"):
            return fn(*args, **kwargs)
    return wrapped


def metered_scope(name):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            with compute_scope(name): return fn(*args, **kwargs)
        return wrapped
    return decorate
