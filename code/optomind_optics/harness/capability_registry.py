"""O-06: capability registry -- tools resolved by capability, not import.

Tools register side by side by capability_id. ``resolve`` supports scopes so
a multi-agent deployment can hide solver-class capabilities from roles that
must not touch them (the literature-only role groundwork).
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from .scientific_tool import EffectClass, ScientificTool, ToolDescriptor

#: Effect-class ordering for the resolve ceiling: a scope with
#: effect_class_max=COMPUTATIONAL will not be handed an EXPENSIVE_COMPUTE
#: tool. The two reserved classes rank above everything.
_EFFECT_RANK = {
    EffectClass.READ_ONLY: 0,
    EffectClass.COMPUTATIONAL: 1,
    EffectClass.EXPENSIVE_COMPUTE: 2,
    EffectClass.EXTERNAL_EXPERIMENT: 3,
    EffectClass.PHYSICAL_ACTUATION: 4,
}

SCOPES = ("planner", "literature-only")

#: Capabilities a literature-only role may resolve: everything except
#: solver-class compute. Named explicitly -- the deny list is the contract.
_SCOPE_DENY_EFFECT = {
    "literature-only": {EffectClass.EXPENSIVE_COMPUTE},
}


class CapabilityRegistry:
    """Thread-safe in-process registry; same-id registration is idempotent
    for the identical descriptor and rejected for a conflicting one."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tools: Dict[str, ScientificTool] = {}

    def register(self, tool: ScientificTool) -> ToolDescriptor:
        descriptor = tool.descriptor()
        key = f"{descriptor.capability_id}::{descriptor.tool_id}"
        with self._lock:
            existing = self._tools.get(key)
            if existing is not None:
                existing_desc = existing.descriptor()
                if existing_desc == descriptor:
                    return existing_desc
                raise ValueError(
                    f"conflicting registration for {key}: "
                    f"{existing_desc.implementation_fingerprint} vs "
                    f"{descriptor.implementation_fingerprint}"
                )
            self._tools[key] = tool
        return descriptor

    def resolve(
        self,
        capability_id: str,
        *,
        effect_class_max: Optional[EffectClass] = None,
        scope: str = "planner",
    ) -> Optional[ScientificTool]:
        if scope not in SCOPES:
            raise ValueError(f"unknown scope {scope!r}; known: {sorted(SCOPES)}")
        with self._lock:
            candidates = [
                tool
                for key, tool in self._tools.items()
                if key.split("::", 1)[0] == capability_id
            ]
        for tool in candidates:
            descriptor = tool.descriptor()
            if descriptor.effect_class in _SCOPE_DENY_EFFECT.get(scope, set()):
                continue
            if (
                effect_class_max is not None
                and _EFFECT_RANK[descriptor.effect_class]
                > _EFFECT_RANK[effect_class_max]
            ):
                continue
            return tool
        return None

    def list_capabilities(self, *, scope: str = "planner") -> List[Dict[str, Any]]:
        if scope not in SCOPES:
            raise ValueError(f"unknown scope {scope!r}; known: {sorted(SCOPES)}")
        rows = []
        with self._lock:
            tools = dict(self._tools)
        for tool in tools.values():
            descriptor = tool.descriptor()
            if descriptor.effect_class in _SCOPE_DENY_EFFECT.get(scope, set()):
                continue
            rows.append(descriptor.to_dict())
        rows.sort(key=lambda row: (row["capability_id"], row["tool_id"]))
        return rows


_DEFAULT_REGISTRY: Optional[CapabilityRegistry] = None
_DEFAULT_LOCK = threading.Lock()


def default_registry() -> CapabilityRegistry:
    """Process-wide registry with VeriTMM registered when importable."""

    global _DEFAULT_REGISTRY
    with _DEFAULT_LOCK:
        if _DEFAULT_REGISTRY is None:
            registry = CapabilityRegistry()
            try:
                from optomind_optics.harness.veritmm_adapter import (
                    _ensure_real_veritmm_import,
                )

                _ensure_real_veritmm_import()
                import tmm_engine

                from .scientific_tool import VeriTMMTool
                from .veritmm_adapter import VeriTMMAdapter
                from config.qwen_config import get_cost_tracker

                registry.register(
                    VeriTMMTool(
                        VeriTMMAdapter(get_cost_tracker()),
                        engine_version=str(getattr(tmm_engine, "__version__", "")),
                    )
                )
            except Exception:
                # Registry stays empty rather than half-initialised; callers
                # fall back to direct construction (the test escape hatch).
                pass
            _DEFAULT_REGISTRY = registry
        return _DEFAULT_REGISTRY
