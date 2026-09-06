"""O-09: the HarnessKernel facade.

Kernel.py re-exports the plugin machinery and hosts the standard plugin
declarations (thin shells around existing modules -- no module internals
are touched; the strangler pattern wraps, never rewrites).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .plugin import (
    FiberState,
    Kernel,
    KernelContext,
    KernelError,
    Plugin,
    PluginManifest,
)

__all__ = [
    "FiberState",
    "Kernel",
    "KernelContext",
    "KernelError",
    "Plugin",
    "PluginManifest",
    "build_default_kernel",
]


# ---------------------------------------------------------------------------
# Standard plugin declarations (thin shells; behaviour-neutral)
# ---------------------------------------------------------------------------


def _plugin(
    plugin_id: str,
    provides: List[str],
    requires: List[str],
    scope: str = "run",
    effects: Optional[List[str]] = None,
) -> Callable[[Callable[[KernelContext], Callable[[], None]]], Plugin]:
    """Class decorator: turn a register(ctx)->Disposer function into a Plugin."""

    def decorator(register_fn: Callable[[KernelContext], Callable[[], None]]):
        class _DeclaredPlugin:
            def manifest(self) -> PluginManifest:
                return PluginManifest(
                    plugin_id=plugin_id,
                    provides=provides,
                    requires=requires,
                    scope=scope,
                    effects=effects or [],
                )

            def register(self, ctx: KernelContext) -> Callable[[], None]:
                return register_fn(ctx)

        _DeclaredPlugin.__name__ = plugin_id
        return _DeclaredPlugin()

    return decorator


def build_default_kernel(
    *,
    event_bus=None,
    capability_registry=None,
    run_id: str = "session",
    with_veritmm: bool = True,
) -> Kernel:
    """Assemble the standard plugin set. Registration is behaviour-neutral:
    each plugin only exposes existing module entry points as capabilities."""

    kernel = Kernel(
        event_bus=event_bus,
        capability_registry=capability_registry,
        run_id=run_id,
    )

    @_plugin(
        "problem_analysis",
        provides=["problem_analysis"],
        requires=[],
        effects=["llm_calls"],
    )
    def _problem_analysis(ctx: KernelContext):
        def get_problem_analyzer():
            from optomind_optics.harness.problem_analyzer import (
                QwenTMMProblemAnalyzer,
            )

            return QwenTMMProblemAnalyzer

        ctx.state_store["problem_analyzer_factory"] = get_problem_analyzer
        return lambda: ctx.state_store.pop("problem_analyzer_factory", None)

    @_plugin(
        "literature",
        provides=["literature_research"],
        requires=["problem_analysis"],
        effects=["network_s2", "llm_calls"],
    )
    def _literature(ctx: KernelContext):
        def get_researcher():
            from optomind_optics.harness.method_research import (
                TMMMethodResearchAdapter,
            )

            return TMMMethodResearchAdapter

        ctx.state_store["method_research_factory"] = get_researcher
        return lambda: ctx.state_store.pop("method_research_factory", None)

    @_plugin(
        "planner",
        provides=["route_planning", "strategy_planning"],
        requires=["literature_research"],
        effects=["llm_calls"],
    )
    def _planner(ctx: KernelContext):
        def get_planners():
            from optomind_optics.harness.strategy_planner import (
                QwenTMMStrategyPlanner,
            )

            return QwenTMMStrategyPlanner

        ctx.state_store["strategy_planner_factory"] = get_planners
        return lambda: ctx.state_store.pop("strategy_planner_factory", None)

    @_plugin(
        "tmm_execution",
        provides=["tmm_execution"],
        requires=[],
        scope="run",
        effects=["expensive_compute"],
    )
    def _tmm(ctx: KernelContext):
        # Register the VeriTMM tool into the capability registry (idempotent
        # with default_registry()); the disposer unregisters it.
        registry = ctx.capability_registry
        tool = None
        try:
            from optomind_optics.harness.veritmm_adapter import (
                _ensure_real_veritmm_import,
            )

            _ensure_real_veritmm_import()
            import tmm_engine

            from optomind_optics.harness.scientific_tool import VeriTMMTool
            from optomind_optics.harness.veritmm_adapter import VeriTMMAdapter
            from config.qwen_config import get_cost_tracker

            tool = VeriTMMTool(
                VeriTMMAdapter(get_cost_tracker()),
                engine_version=str(getattr(tmm_engine, "__version__", "")),
            )
            registry.register(tool)
        except Exception:
            tool = None  # registry stays honest about what exists

        def dispose():
            ctx.record("tmm_tool_unregistered", had_tool=tool is not None)

        return dispose

    @_plugin(
        "feedback",
        provides=["feedback_decisions", "action_ledger"],
        requires=["tmm_execution", "event_bus_access"],
        effects=["deterministic"],
    )
    def _feedback(ctx: KernelContext):
        from optomind_optics.harness.research_feedback import (
            DeterministicResearchFeedbackController,
        )

        controller = DeterministicResearchFeedbackController()
        ctx.state_store["feedback_controller"] = controller
        return lambda: ctx.state_store.pop("feedback_controller", None)

    if with_veritmm:
        kernel.load(_tmm)
    kernel.load(_problem_analysis)
    kernel.load(_literature)
    kernel.load(_planner)
    kernel.load(_feedback)
    return kernel
