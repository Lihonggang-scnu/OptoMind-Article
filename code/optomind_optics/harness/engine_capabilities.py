"""O-10: one-shot engine capability probing with silent degradation.

Probes the mounted VeriTMM's 2.0 capabilities once per process (bounded
retries: one probe per run), records the results as an event, and lets every
consumption point degrade to the O-09 baseline when a capability is absent.
No capability ever changes acceptance semantics -- the physics certificate
stays the only verdict.
"""

from __future__ import annotations

import importlib
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

PROBE_SCHEMA_VERSION = "optomind-engine-capabilities.v1"

_CAPABILITIES = (
    "gradient",
    "design_problem",
    "intent",
    "energy_accounting",
    "explain_superiority",
)


@dataclass(frozen=True)
class EngineCapabilities:
    gradient: bool = False
    design_problem: bool = False
    intent: bool = False
    energy_accounting: bool = False
    explain_superiority: bool = False
    details: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": PROBE_SCHEMA_VERSION,
            "capabilities": {
                name: getattr(self, name) for name in _CAPABILITIES
            },
            "details": dict(self.details),
        }

    def as_event_payload(self) -> Dict[str, Any]:
        return self.to_dict()["capabilities"]


_probe_lock = threading.Lock()
_probe_cache: Optional[EngineCapabilities] = None


def _probe_once() -> EngineCapabilities:
    details: Dict[str, str] = {}
    results: Dict[str, bool] = {}

    # gradient: module importable AND the sensitivity entry point callable.
    try:
        gradient_api = importlib.import_module("tmm_engine.gradient_api")
        results["gradient"] = callable(getattr(gradient_api, "compute_sensitivity"))
        if not results["gradient"]:
            details["gradient"] = "compute_sensitivity missing"
    except Exception as exc:
        results["gradient"] = False
        details["gradient"] = f"{type(exc).__name__}: {exc}"

    # design_problem: module importable.
    try:
        importlib.import_module("tmm_engine.design_problem")
        results["design_problem"] = True
    except Exception as exc:
        results["design_problem"] = False
        details["design_problem"] = f"{type(exc).__name__}: {exc}"

    # intent: module importable, compile_intent callable, certificate type
    # present (construct-level smoke runs where a real task exists).
    try:
        intent = importlib.import_module("tmm_engine.intent")
        results["intent"] = callable(getattr(intent, "compile_intent")) and hasattr(
            intent, "CompilationEquivalenceCertificate"
        )
        if not results["intent"]:
            details["intent"] = "compile_intent/ceertificate missing"
    except Exception as exc:
        results["intent"] = False
        details["intent"] = f"{type(exc).__name__}: {exc}"

    # energy_accounting: the acceptance layer carries the ledger block.
    try:
        acceptance = importlib.import_module("tmm_engine.acceptance")
        certified = getattr(acceptance, "CertifiedSimulation", None)
        if certified is not None and "energy_accounting" in getattr(
            certified, "__dataclass_fields__", {}
        ):
            results["energy_accounting"] = True
        else:
            results["energy_accounting"] = False
            details["energy_accounting"] = "certificate schema lacks the block"
    except Exception as exc:
        results["energy_accounting"] = False
        details["energy_accounting"] = f"{type(exc).__name__}: {exc}"

    # explain_superiority: provenance graph exposes the attribution link.
    try:
        provenance = importlib.import_module("tmm_engine.provenance_graph")
        results["explain_superiority"] = any(
            "explain" in name.lower() for name in dir(provenance)
        )
        if not results["explain_superiority"]:
            details["explain_superiority"] = "no explain_* entry point"
    except Exception as exc:
        results["explain_superiority"] = False
        details["explain_superiority"] = f"{type(exc).__name__}: {exc}"

    return EngineCapabilities(details=details, **results)


def probe(force: bool = False) -> EngineCapabilities:
    """One probe per process (bounded), thread-safe, cached afterwards."""

    global _probe_cache
    with _probe_lock:
        if _probe_cache is None or force:
            _probe_cache = _probe_once()
        return _probe_cache


def reset_cache() -> None:
    """Test seam: forget the cached probe."""

    global _probe_cache
    with _probe_lock:
        _probe_cache = None
