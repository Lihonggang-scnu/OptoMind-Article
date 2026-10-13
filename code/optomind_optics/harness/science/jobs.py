"""Durable idempotent execution gate. Unknown outcomes retain reservations.

Provider adapters expose submit(key, payload), status(key) and optional cancel(key).
The key is durable before submission; recovery always queries before retrying.
"""
from __future__ import annotations
import json
import math
import sqlite3
from typing import Any, Literal
from pydantic import Field
from .contracts import StrictModel, canonical_hash


class ToolCapability(StrictModel):
    effect_class: Literal["compute", "external_measurement", "read_only"]
    domain: str
    channels: tuple[str, ...] = ()
    units: tuple[str, ...] = ()
    supports_status: bool = False
    supports_cancel: bool = False
    supports_resume: bool = False
    validation_refs: tuple[str, ...] = ()


class CostEvent(StrictModel):
    event_id: str
    parent_action: str
    resource: str
    quantity: float = Field(ge=0, allow_inf_nan=False)
    status: Literal["estimated", "reserved", "measured", "settled", "unknown"]
    basis: str
    cache_state: str = "miss"
    failure: str | None = None


class JobReceipt(StrictModel):
    job_id: str
    idempotency_key: str
    state: Literal["reserved", "submitting", "running", "completed", "failed", "cancelled", "unknown_outcome"]
    provider_receipt: dict[str, Any] = Field(default_factory=dict)
    attempts: tuple[str, ...] = ()
    cost_refs: tuple[str, ...] = ()


class ExecutionGate:
    VERSION = "science-job.v1"
    def __init__(self, path, *, limits: dict[str, float], capability: ToolCapability, fault_hook=None):
        if any(not math.isfinite(v) or v < 0 for v in limits.values()): raise ValueError("invalid limits")
        self.db = sqlite3.connect(str(path), timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript('''CREATE TABLE IF NOT EXISTS jobs(key TEXT PRIMARY KEY,payload_hash TEXT,version TEXT,receipt TEXT,prediction TEXT,reservation TEXT,actual TEXT);
CREATE TABLE IF NOT EXISTS costs(id TEXT PRIMARY KEY,payload TEXT);
CREATE TABLE IF NOT EXISTS gate_config(key TEXT PRIMARY KEY,value TEXT);''')
        saved = self.db.execute("SELECT value FROM gate_config WHERE key='limits'").fetchone()
        if saved and json.loads(saved[0]) != limits: raise ValueError("recovery budget configuration conflict")
        self.db.execute("INSERT OR IGNORE INTO gate_config VALUES('limits',?)", (json.dumps(limits,sort_keys=True),))
        self.db.commit()
        self.limits, self.capability = limits, capability
        self.fault_hook = fault_hook or (lambda stage: None)

    def close(self): self.db.close()

    def receipt(self, key):
        row = self.db.execute("SELECT receipt,version FROM jobs WHERE key=?",(key,)).fetchone()
        if not row: return None
        if row[1] != self.VERSION: raise ValueError("recovery version conflict")
        return JobReceipt.model_validate_json(row[0])

    def usage(self):
        total = {resource:0.0 for resource in self.limits}
        for receipt, reservation, actual in self.db.execute("SELECT receipt,reservation,actual FROM jobs"):
            state = json.loads(receipt)["state"]
            values = json.loads(actual) if actual is not None else json.loads(reservation)
            for resource, quantity in values.items(): total[resource] = total.get(resource,0)+quantity
        return total

    def _costs(self, key, values, status, failure=None):
        ids=[]
        for resource, quantity in values.items():
            eid=f"{key}:{status}:{resource}"
            event=CostEvent(event_id=eid,parent_action=key,resource=resource,quantity=quantity,status=status,basis="provider_receipt" if status=="settled" else "reservation",failure=failure)
            self.db.execute("INSERT OR IGNORE INTO costs VALUES(?,?)",(eid,event.model_dump_json()))
            ids.append(eid)
        return tuple(ids)

    def reserve(self, key, payload, *, estimate, prediction_commit):
        if not key or not prediction_commit: raise ValueError("idempotency key and prediction commit required")
        if any(k not in self.limits or not math.isfinite(v) or v<0 for k,v in estimate.items()): raise ValueError("invalid reservation")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old=self.receipt(key)
            if old:
                row=self.db.execute("SELECT payload_hash,prediction FROM jobs WHERE key=?",(key,)).fetchone()
                if row!=(canonical_hash(payload),json.dumps(prediction_commit,sort_keys=True)): raise ValueError("idempotency identity conflict")
                self.db.rollback(); return old
            usage=self.usage()
            if any(usage.get(r,0)+q>self.limits[r] for r,q in estimate.items()): raise ValueError("hard budget exhausted")
            costs=self._costs(key,estimate,"reserved")
            receipt=JobReceipt(job_id=key,idempotency_key=key,state="reserved",cost_refs=costs)
            self.db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,NULL)",(key,canonical_hash(payload),self.VERSION,receipt.model_dump_json(),json.dumps(prediction_commit,sort_keys=True),json.dumps(estimate)))
            self.db.commit()
            return receipt
        except BaseException:
            self.db.rollback(); raise

    def _update(self, receipt, actual=None):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            current = self.receipt(receipt.idempotency_key)
            if current is not None and current.state in ("completed", "failed", "cancelled") and current != receipt:
                self.db.rollback()
                return current
            if actual is not None:
                actual = {k:v for k,v in actual.items() if k in self.limits or v != 0}
                if any(k not in self.limits or not math.isfinite(v) or v<0 for k,v in actual.items()): raise ValueError("invalid measured costs")
                reserved = json.loads(self.db.execute("SELECT reservation FROM jobs WHERE key=?", (receipt.idempotency_key,)).fetchone()[0])
                # An omitted resource is unknown, not a measured zero.
                actual = {**reserved, **actual}
                refs=self._costs(receipt.idempotency_key,actual,"settled",receipt.state if receipt.state in ("failed","cancelled") else None)
                receipt=receipt.model_copy(update={"cost_refs":receipt.cost_refs+refs})
                self.db.execute("UPDATE jobs SET actual=? WHERE key=?",(json.dumps(actual),receipt.idempotency_key))
            self.db.execute("UPDATE jobs SET receipt=? WHERE key=?",(receipt.model_dump_json(),receipt.idempotency_key))
            self.db.commit()
        except BaseException:
            self.db.rollback(); raise
        return receipt

    def _collect(self, key, result):
        old=self.receipt(key)
        state=result.get("state","unknown_outcome")
        if state not in ("running","completed","failed","cancelled","unknown_outcome"): state="unknown_outcome"
        terminal=state in ("completed","failed","cancelled")
        # A terminal provider state without measured cost still holds its full reserve.
        actual=result.get("cost") if terminal else None
        receipt=JobReceipt(job_id=old.job_id,idempotency_key=key,state=state,provider_receipt=result,attempts=old.attempts,cost_refs=old.cost_refs)
        self.fault_hook("before_settlement")
        return self._update(receipt,actual)

    def execute(self, provider, key, payload, *, estimate, prediction_commit):
        receipt=self.reserve(key,payload,estimate=estimate,prediction_commit=prediction_commit)
        if receipt.state in ("completed","failed","cancelled"): return receipt
        if receipt.state != "reserved": return self.recover(provider,key)
        self.fault_hook("before_submit")
        self.db.execute("BEGIN IMMEDIATE")
        current = self.receipt(key)
        if current.state != "reserved":
            self.db.rollback()
            return self.recover(provider, key)
        receipt=current.model_copy(update={"state":"submitting","attempts":(f"{key}:attempt:1",)})
        self.db.execute("UPDATE jobs SET receipt=? WHERE key=?", (receipt.model_dump_json(), key))
        self.db.commit()
        try:
            result=provider.submit(key,payload)
            self.fault_hook("after_provider_return")
            return self._collect(key,result)
        except BaseException:
            self._update(receipt.model_copy(update={"state":"unknown_outcome"}))
            raise

    def recover(self, provider, key):
        old=self.receipt(key)
        if old is None: raise ValueError("unknown job")
        if old.state in ("completed","failed","cancelled"): return old
        if not self.capability.supports_status:
            return self._update(old.model_copy(update={"state":"unknown_outcome"}))
        # Never resubmit after ambiguous completion, including a provider 'not found'.
        return self._collect(key,provider.status(key))

    def cancel(self, provider, key):
        old=self.receipt(key)
        if old is None: raise ValueError("unknown job")
        if old.state in ("completed","failed","cancelled"): return old
        if not self.capability.supports_cancel: raise ValueError("cancel unsupported")
        return self._collect(key,provider.cancel(key))
