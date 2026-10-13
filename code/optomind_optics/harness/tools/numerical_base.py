"""Bounded synchronous numerical jobs with durable, content-checked receipts."""
from __future__ import annotations
import hashlib
import importlib.metadata
import inspect
import json
import time
from pathlib import Path
from typing import Any
import numpy as np
from ..scientific_tool import EffectClass, ToolDescriptor


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def complex_pairs(value):
    z = np.asarray(value)
    return np.stack((z.real, z.imag), axis=-1).tolist()


class NumericalAdapter:
    backend = ""
    backend_version = ""
    capability_id = ""
    license = ""

    def __init__(self, receipt_dir=None):
        self.receipt_dir = Path(receipt_dir) if receipt_dir is not None else None
        self._receipts = {}

    def descriptor(self):
        return ToolDescriptor(self.backend + "-adapter", self.capability_id, "scientific-tool.v1",
                              EffectClass.EXPENSIVE_COMPUTE, True,
                              digest({"backend": self.backend, "version": self.backend_version,
                                      "adapter": "independent-physics.v1",
                                      "source_hash": hashlib.sha256(
                                          (inspect.getsource(type(self)) + inspect.getsource(NumericalAdapter)).encode()
                                      ).hexdigest()}))

    def preflight(self, intent):
        try:
            payload = intent.get("task_spec", intent)
            self.validate(payload)
            actual = importlib.metadata.version(self.backend)
            if actual != self.backend_version:
                raise ValueError(f"backend version {actual}; required {self.backend_version}")
            return {"ok": True, "backend": self.backend, "version": actual, "license": self.license}
        except (ValueError, TypeError, KeyError, importlib.metadata.PackageNotFoundError) as exc:
            return {"ok": False, "reason": str(exc)}

    def run(self, request):
        payload = request.get("task_spec", request)
        return self.submit(digest(payload), payload)

    def _path(self, key):
        return self.receipt_dir / (hashlib.sha256(key.encode()).hexdigest() + ".json")

    def submit(self, key, payload):
        previous = self.status(key)
        if previous.get("state") == "completed":
            if previous["payload_hash"] != digest(payload):
                raise ValueError("idempotency identity conflict")
            return previous
        if previous.get("state") in ("running", "failed"):
            raise ValueError("existing execution must not be blindly retried; inspect receipt")
        check = self.preflight(payload)
        if not check["ok"]:
            raise ValueError(check["reason"])
        start = time.process_time()
        initial = {"state": "running", "handle_id": key, "payload_hash": digest(payload)}
        self._store(key, initial)
        try:
            result = self.solve(payload)
        except Exception as exc:
            self._store(key, dict(initial, state="failed", error=str(exc),
                                  cost={"cpu_seconds": time.process_time() - start}))
            raise
        receipt = {"state": "completed", "status": "completed", "handle_id": key,
                   "payload_hash": digest(payload), "result": result,
                   "result_hash": digest(result), "cost": {"cpu_seconds": time.process_time() - start},
                   "backend": self.backend, "backend_version": self.backend_version,
                   "external_cost_usd": 0.0, "certified": False}
        self._store(key, receipt)
        return receipt

    def _store(self, key, receipt):
        self._receipts[key] = json.loads(json.dumps(receipt))
        if self.receipt_dir is not None:
            self.receipt_dir.mkdir(parents=True, exist_ok=True)
            path = self._path(key)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(receipt, sort_keys=True, allow_nan=False))
            temporary.replace(path)

    def status(self, handle):
        key = handle if isinstance(handle, str) else handle.get("handle_id", "")
        if key in self._receipts:
            return json.loads(json.dumps(self._receipts[key]))
        if self.receipt_dir is not None and self._path(key).is_file():
            receipt = json.loads(self._path(key).read_text())
            if receipt.get("state") == "completed" and digest(receipt["result"]) != receipt["result_hash"]:
                raise ValueError("result integrity mismatch")
            return receipt
        return {"state": "unknown_outcome", "supported": True, "handle_id": key}

    poll = status
    collect = status

    def cancel(self, handle):
        return {"supported": False, "reason": "synchronous backend; no mid-flight cancellation"}

    def replay(self, run_id):
        receipt = self.status(run_id)
        return {"supported": True, "ok": receipt.get("state") == "completed" and
                digest(receipt["result"]) == receipt["result_hash"],
                "scope": "artifact integrity only; not physics recertification"}

    verify = replay

    def capability(self):
        from ..science.jobs import ToolCapability
        return ToolCapability(effect_class="compute", domain=self.capability_id,
                              channels=("R", "T", "complex_amplitudes"), units=("nm",),
                              supports_status=True, supports_cancel=False, supports_resume=False,
                              validation_refs=("tests/test_science_c18_rcwa.py" if self.backend == "grcwa"
                                               else "tests/test_science_c19_fullwave.py",))
