"""O-09: plugin protocol -- manifests, lifecycle fibers, disposers.

"Every registration has a disposer": unloading executes disposers in
reverse registration order. A plugin whose requires are not satisfied stays
PENDING and activates automatically when its dependencies appear -- load
order EMERGES from dependencies, never from manual sorting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, runtime_checkable


class FiberState(str, Enum):
    PENDING = "PENDING"
    LOADING = "LOADING"
    ACTIVE = "ACTIVE"
    UNLOADING = "UNLOADING"
    DISPOSED = "DISPOSED"


SCOPES = ("run", "session", "global")


@dataclass(frozen=True)
class PluginManifest:
    plugin_id: str
    provides: List[str]
    requires: List[str]
    scope: str = "run"
    effects: List[str] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.scope not in SCOPES:
            raise ValueError(f"scope must be one of {SCOPES}, got {self.scope!r}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "provides": list(self.provides),
            "requires": list(self.requires),
            "scope": self.scope,
            "effects": list(self.effects),
            "permissions": list(self.permissions),
        }


Disposer = Callable[[], None]


@runtime_checkable
class Plugin(Protocol):
    def manifest(self) -> PluginManifest: ...

    def register(self, ctx: "KernelContext") -> Disposer: ...


@dataclass
class _Fiber:
    plugin: Plugin
    state: FiberState = FiberState.PENDING
    disposers: List[Disposer] = field(default_factory=list)


class KernelError(RuntimeError):
    pass


class KernelContext:
    """Declarative dependency injection: services exposed as attributes,
    scope-filtered capability resolution, and an event sink."""

    def __init__(self, kernel: "Kernel", scope: str) -> None:
        self._kernel = kernel
        self.scope = scope

    @property
    def event_bus(self):
        return self._kernel.event_bus

    @property
    def capability_registry(self):
        return self._kernel.capability_registry

    @property
    def artifact_store(self):
        return self._kernel.artifact_store

    @property
    def state_store(self):
        return self._kernel.state_store

    @property
    def scheduler(self):
        return self._kernel.scheduler

    @property
    def budget(self):
        return self._kernel.budget

    @property
    def policy(self):
        return self._kernel.policy

    def resolve(self, capability_id: str):
        return self._kernel.capability_registry.resolve(
            capability_id, scope=self.scope
        )

    def record(self, event_type: str, **payload: Any) -> None:
        self._kernel.record(event_type, **payload)


class Kernel:
    """Minimal harness kernel. The event_bus reuses the O-03 EventStore (a
    projection over the same hash-chained journal); the capability registry
    reuses O-06. artifact_store/state_store/scheduler/budget/policy are
    thin state carriers the plugins may adopt incrementally."""

    def __init__(self, *, event_bus=None, capability_registry=None, run_id: str = "session") -> None:
        if event_bus is None:
            from optomind_optics.harness.event_store import EventStore

            event_bus = _InMemoryEventBus()
        if capability_registry is None:
            from optomind_optics.harness.capability_registry import CapabilityRegistry

            capability_registry = CapabilityRegistry()
        self.event_bus = event_bus
        self.capability_registry = capability_registry
        self.artifact_store: Dict[str, Any] = {}
        self.state_store: Dict[str, Any] = {}
        self.scheduler: Dict[str, Any] = {}
        self.budget: Dict[str, Any] = {}
        self.policy: Dict[str, Any] = {}
        self.run_id = str(run_id)
        self._fibers: Dict[str, _Fiber] = {}
        self._provided_by: Dict[str, str] = {
            # The kernel itself provides the event bus to its plugins.
            "event_bus_access": "kernel",
        }
        self._scope: str = "run"

    # -- lifecycle ---------------------------------------------------------

    def load(self, plugin: Plugin, *, scope: Optional[str] = None) -> str:
        manifest = plugin.manifest()
        plugin_id = manifest.plugin_id
        if plugin_id in self._fibers:
            raise KernelError(f"plugin {plugin_id!r} already loaded")
        for capability in manifest.provides:
            if capability in self._provided_by:
                raise KernelError(
                    f"capability {capability!r} already provided by "
                    f"{self._provided_by[capability]!r}"
                )
        fiber = _Fiber(plugin=plugin)
        self._fibers[plugin_id] = fiber
        self._scope = scope or manifest.scope

        if self._requires_satisfied(manifest):
            self._activate(plugin_id)
        return plugin_id

    def _requires_satisfied(self, manifest: PluginManifest) -> bool:
        return all(
            requirement in self._provided_by for requirement in manifest.requires
        )

    def _activate(self, plugin_id: str) -> None:
        fiber = self._fibers[plugin_id]
        manifest = fiber.plugin.manifest()
        fiber.state = FiberState.LOADING
        ctx = KernelContext(self, self._scope)
        try:
            disposer = fiber.plugin.register(ctx)
        except Exception as exc:
            fiber.state = FiberState.PENDING
            raise KernelError(f"plugin {plugin_id!r} failed to register: {exc}") from exc
        fiber.disposers.append(disposer if callable(disposer) else (lambda: None))
        for capability in manifest.provides:
            self._provided_by[capability] = plugin_id
        fiber.state = FiberState.ACTIVE
        self.record(
            "plugin_activated",
            plugin_id=plugin_id,
            provides=list(manifest.provides),
        )
        # Dependency emergence: a PENDING plugin whose requirements just
        # appeared activates automatically.
        for other_id, other in list(self._fibers.items()):
            if other.state == FiberState.PENDING and self._requires_satisfied(
                other.plugin.manifest()
            ):
                self._activate(other_id)

    def unload(self, plugin_id: str) -> None:
        fiber = self._fibers.get(plugin_id)
        if fiber is None:
            raise KernelError(f"plugin {plugin_id!r} is not loaded")
        if fiber.state != FiberState.ACTIVE:
            raise KernelError(
                f"plugin {plugin_id!r} is {fiber.state.value}, cannot unload"
            )
        fiber.state = FiberState.UNLOADING
        # Dependents go back to PENDING first (they lose their requirement).
        lost = set(self._fibers[plugin_id].plugin.manifest().provides)
        for other_id, other in list(self._fibers.items()):
            if (
                other.state == FiberState.ACTIVE
                and lost & set(other.plugin.manifest().requires)
            ):
                self._deactivate(other_id, back_to_pending=True)
        for disposer in reversed(fiber.disposers):
            disposer()
        for capability, provider in list(self._provided_by.items()):
            if provider == plugin_id:
                del self._provided_by[capability]
        fiber.state = FiberState.DISPOSED
        self.record(
            "plugin_unloaded",
            plugin_id=plugin_id,
            disposers_executed=len(fiber.disposers),
        )

    def _deactivate(self, plugin_id: str, *, back_to_pending: bool) -> None:
        fiber = self._fibers[plugin_id]
        manifest = fiber.plugin.manifest()
        fiber.state = FiberState.UNLOADING
        lost = set(manifest.provides)
        for other_id, other in list(self._fibers.items()):
            if (
                other.state == FiberState.ACTIVE
                and lost & set(other.plugin.manifest().requires)
            ):
                self._deactivate(other_id, back_to_pending=True)
        for disposer in reversed(fiber.disposers):
            disposer()
        fiber.disposers = []
        manifest = fiber.plugin.manifest()
        for capability in manifest.provides:
            if self._provided_by.get(capability) == plugin_id:
                del self._provided_by[capability]
        fiber.state = FiberState.PENDING if back_to_pending else FiberState.DISPOSED
        self.record(
            "plugin_deactivated",
            plugin_id=plugin_id,
            state=fiber.state.value,
        )

    # -- introspection -------------------------------------------------------

    def fibers(self) -> Dict[str, Dict[str, Any]]:
        return {
            plugin_id: {
                "state": fiber.state.value,
                **fiber.plugin.manifest().to_dict(),
            }
            for plugin_id, fiber in self._fibers.items()
        }

    def catalog(self) -> Dict[str, Any]:
        """The runtime snapshot: plugins/capabilities/tools + composed-at."""

        from datetime import datetime, timezone

        return {
            "schema_version": "optomind-runtime-catalog.v1",
            "run_id": self.run_id,
            "composed_at": datetime.now(timezone.utc).isoformat(),
            "plugins": self.fibers(),
            "capabilities": self.capability_registry.list_capabilities(
                scope="planner"
            ),
        }

    def record(self, event_type: str, **payload: Any) -> None:
        record = getattr(self.event_bus, "record", None)
        if callable(record):
            record(event_type, **payload)
            return
        append = getattr(self.event_bus, "append", None)
        if callable(append):
            append(event_type, payload)
            return
        self.event_bus.append(event_type, payload)


class _InMemoryEventBus:
    """Session-scoped bus for kernels that run without a run directory."""

    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    def record(self, event_type: str, **payload: Any) -> None:
        self.events.append(
            {
                "event_type": str(event_type),
                "time": datetime_now_iso(),
                "payload": dict(payload),
            }
        )


def datetime_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
