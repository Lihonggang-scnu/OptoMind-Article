"""End-to-end research, design, execution and feedback loop for TMM.

The orchestration layer is intentionally deterministic.  Models may propose
problem interpretations and design routes, but immutable contracts, budgets,
solver execution, physics acceptance, duplicate suppression and stopping are
owned by code.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Protocol,
    Sequence,
    Tuple,
)

from pydantic import BaseModel, ConfigDict, Field, field_validator

from optomind_research.runtime.artifact_store import atomic_write_json
from optomind_research.runtime.cost_ledger import estimate_call_cost_cny

from .orchestrator import TMMHarnessConfig, TMMHarnessOrchestrator
from .portfolio_seeding import (
    PORTFOLIO_SEEDING_MODEL,
    QwenTMPPortfolioSeeder,
)
from .research_feedback import (
    DeterministicResearchFeedbackController,
    ResearchFeedbackDecision,
    ResearchIterationObservation,
    observation_from_run_result,
)
from .research_report import DeterministicTMMResearchReporter, TMMResearchAnswer
from .lineage_writer import LINEAGE_FILENAME, LineageRecord, write_lineage
from .route_planning import (
    CONTROL_NO_LITERATURE_DISCLOSURE,
    CONTROL_ROUTE_ID,
    CONTROL_ROUTE_PLANNING_ARTIFACT,
    CONTROL_ROUTE_SOURCE,
    DEFAULT_LITERATURE_LIMIT,
    DEFAULT_MAXIMUM_ROUTES,
    QwenLiteratureRoutePlanner,
    QwenMemoryControlRoutePlanner,
    RoutePlanResult,
)
from .scoring_standard import (
    SCORING_STANDARD_SCHEMA_VERSION,
    QwenScoringStandardBuilder,
    ScoringStandard,
)
from .stop_controller import DEFAULT_MINIMUM_SCORE_IMPROVEMENT, evaluate_stagnation
from .strategy_planner import DesignRoute
from .tournament_summary import summarize_tournament
from .task_compiler import ArticleTurboQwenClient, QwenTMMTaskCompiler
from .veritmm_adapter import MAX_POOL_WORKERS
from .route_reflection import (
    RouteReflection,
    reflect_on_route,
    write_reflection_sidecar,
    REFLECTION_MODEL,
)
from config.qwen_config import get_qwen_client

# ---------------------------------------------------------------------------
# R-06 tournament scheduling constants (handoff item 2: named, single home).
# The stagnation EPSILON deliberately has NO constant here — its single source
# of truth remains stop_controller.DEFAULT_MINIMUM_SCORE_IMPROVEMENT.
# ---------------------------------------------------------------------------
# Upper bound on LLM-side worker threads inside one wave (reflections /
# replanning). VeriTMM execution never enters this pool.
MAX_CONCURRENT_LLM_WORKERS: int = 4
# Length of the recent-scores window fed to evaluate_stagnation.
STAGNATION_WINDOW_ROUNDS: int = 3
# Default hard cap of executed rounds per route lineage.
DEFAULT_MAX_ROUNDS_PER_ROUTE: int = 6

# R-10 control arm: the source marker and stable id of the one route planned
# from the model's own prior knowledge, with no literature and no method
# research in front of it.  Named here because three modules match them
# literally -- the orchestrator that appends the route, the tournament summary
# that splits the comparison on them, and the tests that prove the split.
CONTROL_ROUTE_SOURCE = "llm_memory_control"
LITERATURE_ROUTE_SOURCE = "literature_planned"

# Route lifecycle states (names are matched literally by R-07; do not rename).
TRACK_RACING = "racing"
TRACK_STOPPED_STAGNANT = "stopped_stagnant"
TRACK_STOPPED_LLM_ADVICE = "stopped_llm_advice"
TRACK_ELIMINATED_PHYSICS = "eliminated_physics"
TRACK_STOPPED_ROUND_LIMIT = "stopped_round_limit"
TRACK_STOPPED_BUDGET = "stopped_budget"
TRACK_ERROR_UNRECOVERABLE = "error_unrecoverable"
# A track that was still racing when the RUN stopped for a reason of its
# own (today: another route left the declared TMM boundary). Without this
# the track keeps "racing" in the final ledger, which reads as "still in
# the race" and carries no end reason at all.
TRACK_STOPPED_RUN_HALTED = "stopped_run_halted"


@dataclass
class RouteTrack:
    """Per-route racing state for the tournament scheduler (R-06).

    Red-line 5 note: a RouteTrack is bookkeeping, never part of the
    DesignRoute contract. It must not be serialized into route payloads,
    hashed into request-hash sets, or added to DesignRoute.model_fields.
    It exists precisely so per-route ledgers have ONE home.
    """

    route_id: str
    source: str                       # evidence_derived | experience_derived | planned
    rounds_used: int = 0              # executed rounds consumed by THIS chain
    score_history: list[float] = field(default_factory=list)
    best_candidate_ids: list[str] = field(default_factory=list)
    status: str = TRACK_RACING
    termination_reason: str = ""     # empty while racing
    current_route: Dict[str, Any] = field(default_factory=dict)
    # Chain-local lineage bookkeeping (single ownership -- the former
    # orchestrator-wide _route_lineage dict / _lineage_key resolver are gone).
    # A renamed-or-reused revision keeps the SAME track: only current_route is
    # replaced, so declarations, score history and round accounting continue by
    # construction instead of by resolution patch.
    lineage_round: int = 0
    lineage_parent_sha: str | None = None
    # _route_hash of every accepted version of this chain, including the
    # original portfolio member; guards against non-substantive revisions.
    version_hashes: set[str] = field(default_factory=set)


class ComponentProtocol(Protocol):
    pass


# ---------------------------------------------------------------------------
# R-08: spawn-safe worker + default batch executor for Phase 1b
# ---------------------------------------------------------------------------

def _tmm_harness_worker(job: str) -> dict[str, Any]:
    """Child-process body for one route's inner-harness run (spawn safe).

    job is a JSON string: {"work_dir":..., "run_id":..., "config":{...},
    "task": <OpticalDesignTask dump>}. The child rebuilds a fresh
    TMMHarnessOrchestrator inside its OWN sys.modules -- dodging the
    tmm_engine re-import race entirely -- and reports pure data back:
    time.process_time() covers the run only; module import cost belongs to
    the pool, not to any single task's budget.
    """
    import time as _time

    payload = json.loads(job)
    from .design_task import OpticalDesignTask

    work_dir = Path(payload["work_dir"])
    work_dir.mkdir(parents=True, exist_ok=True)
    config_kwargs = dict(payload.get("config") or {})
    started_cpu = time.process_time()
    started_wall = time.perf_counter()
    try:
        orchestrator = TMMHarnessOrchestrator(
            work_dir,
            run_id=payload.get("run_id") or work_dir.name,
            config=TMMHarnessConfig(
                use_qwen_policy=bool(config_kwargs.get("use_qwen_policy", False)),
                qwen_force_mock=bool(config_kwargs.get("qwen_force_mock", True)),
            ),
        )
        task = OpticalDesignTask.model_validate(payload["task"])
        result = orchestrator.run(task)
        final_result = json.loads(result.model_dump_json())
        return {
            "ok": True,
            "final_result": final_result,
            "cpu_seconds": time.process_time() - started_cpu,
            "wall_seconds": time.perf_counter() - started_wall,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error_type": type(exc).__name__,
            "message": str(exc)[:500],
            "cpu_seconds": time.process_time() - started_cpu,
            "wall_seconds": time.perf_counter() - started_wall,
        }


def _tmm_batch_default(
    jobs: list[tuple[str, Path]],
    *,
    budget_snapshot: Any = None,
    max_cpu_seconds: float | None = None,
) -> list[dict[str, Any]]:
    """R-08 Phase-1b executor: N inner-harness runs in a process pool.

    Same discipline as veritmm_adapter.batch_run (which stays the engine-
    level primitive): bounded workers, BLAS pinning initializer, results in
    input order, CPU summed once into CostTracker, BrokenPool degrades to
    serial WITHOUT rerunning children that already wrote FINAL_RESULT.json.
    The bounded_run budget-snapshot protocol is honored identically so a
    future metering source can gate this path without signature changes.
    """
    import os
    from concurrent.futures import ProcessPoolExecutor

    from .veritmm_adapter import _batch_pool_initializer

    used: float | None = None
    if budget_snapshot is not None:
        for attr in ("tmm_cpu_seconds", "veritmm_cpu_seconds"):
            value = getattr(budget_snapshot, attr, None)
            if value is not None:
                used = float(value)
                break
    if max_cpu_seconds is not None and used is not None and used >= float(max_cpu_seconds):
        return [
            {"ok": False, "budget_blocked": True, "cpu_seconds": 0.0}
            for _ in jobs
        ]

    payloads: list[str] = []
    out_dirs: list[Path] = []
    for job_json, execution_dir in jobs:
        payloads.append(job_json)
        out_dirs.append(Path(execution_dir))

    results: list[dict[str, Any] | None] = [None] * len(payloads)
    pending: list[int] = list(range(len(payloads)))
    cpu_total = 0.0
    if len(payloads) >= 2:
        max_workers = min(
            len(payloads),
            max(1, (os.cpu_count() or 2) - 1),
            MAX_POOL_WORKERS,
        )
        degraded = False
        try:
            with ProcessPoolExecutor(
                max_workers=max_workers,
                initializer=_batch_pool_initializer,
            ) as pool:
                futures = [
                    (i, pool.submit(_tmm_harness_worker, p))
                    for i, p in enumerate(payloads)
                ]
                for i, future in futures:
                    payload = future.result()
                    results[i] = payload
                    cpu_total += float(payload.get("cpu_seconds") or 0.0)
        except Exception:
            degraded = True
        if not degraded:
            pending = []
        else:
            pending = [i for i, res in enumerate(results) if res is None]

    for i in pending:
        out_dir = out_dirs[i]
        final_file = out_dir / "FINAL_RESULT.json"
        if final_file.exists():
            # Completed by an earlier attempt: adopt it, do NOT rerun.
            try:
                results[i] = {
                    "ok": True,
                    "final_result": json.loads(
                        final_file.read_text(encoding="utf-8")
                    ),
                    "cpu_seconds": 0.0,
                    "wall_seconds": 0.0,
                }
                continue
            except Exception:
                pass
        try:
            payload = _tmm_harness_worker(payloads[i])
        except Exception as exc:
            payload = {
                "ok": False,
                "error_type": type(exc).__name__,
                "message": str(exc)[:500],
                "cpu_seconds": 0.0,
            }
        results[i] = payload
        cpu_total += float(payload.get("cpu_seconds") or 0.0)

    if cpu_total > 0.0:
        try:
            from config.qwen_config import get_cost_tracker

            tracker = get_cost_tracker()
            recorder = getattr(tracker, "record_tmm_usage", None) or getattr(
                tracker, "record_veritmm_usage", None
            )
            if recorder is not None:
                recorder(cpu_total)
        except Exception:
            pass
    return [res for res in results if res is not None]

class TMMResearchHarnessConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    maximum_iterations: int = 6
    maximum_initial_routes: int = 3
    maximum_refinement_rounds: int = 1
    maximum_method_research_rounds: int = 2
    wall_time_seconds: float = 3600.0
    online_method_research: bool = False
    use_qwen_policy_inside_tmm: bool = False
    qwen_force_mock: bool | None = None
    # R-05: when True the INITIAL route portfolio comes from dual-source
    # seeding (evidence-derived + experience-derived, merged and deduplicated
    # via _route_hash) instead of the single-shot strategy plan. Default OFF:
    # flipping it on changes the run's provenance chain, so the caller (or a
    # later work order) opts in explicitly.
    portfolio_seeding_enabled: bool = False
    # R-06 tournament shape: how many routes race in parallel, and how many
    # executed rounds each route lineage may consume.
    max_routes: int = 5
    max_rounds_per_route: int = DEFAULT_MAX_ROUNDS_PER_ROUTE
    # A route-level LLM stop is deliberately conservative.  The model may
    # recommend stopping early, but the scheduler ignores that vote until the
    # route has produced at least this many executed rounds and the model has
    # supplied an explicit, typed no-benefit basis.
    minimum_rounds_before_llm_stop: int = 2
    # Derive the run's ranking criteria from the user's question before any
    # route runs, and rank every route by that one expression. Default ON: with
    # it off the compiler falls back to copying whichever route compiled first
    # onto the others, so the portfolio ranks by accident of ordering. The two
    # extra LLM calls and the added link in the provenance chain are the point
    # of the stage, not a side effect of it.
    scoring_standard_enabled: bool = True
    # Plan the initial portfolio from retrieved literature, and let the model
    # decide HOW MANY routes the problem needs (bounded at both ends) instead
    # of taking that count from maximum_initial_routes. Default ON: taking the
    # width from a configuration number pads a two-axis problem with a route
    # nobody needed and silently discards axes from a five-axis one. The stage
    # reaches an external search provider; a provider that cannot be reached is
    # a recorded condition and the routes proceed identified as theory-based.
    route_planning_enabled: bool = True
    # How many papers the planning model may see. The user-facing starting
    # point; every retained paper is spent from that model's context window, so
    # raising it trades breadth of evidence against room for the request
    # itself, and route_planning.py additionally caps the total characters.
    route_planning_literature_limit: int = DEFAULT_LITERATURE_LIMIT
    # The bound on the model's own answer. One route is a legitimate plan for a
    # single-axis problem; each additional route costs its own iteration budget
    # for the whole run, which is what the ceiling protects.
    route_planning_maximum_routes: int = DEFAULT_MAXIMUM_ROUTES
    # Give every route its OWN round quota (max_rounds_per_route) instead of
    # rationing all routes out of the single maximum_iterations pool. With the
    # pool shared, a five-route portfolio against maximum_iterations=6 lets the
    # first routes spend the budget and the last ones never run a round, so the
    # portfolio's width stops meaning what it says. With this on, the run's
    # iteration ceiling is raised to routes x max_rounds_per_route, which is
    # exactly enough for each route to spend its own quota and no more: a route
    # that stops early does not hand its unused rounds to anyone else, because
    # max_rounds_per_route still caps every route individually. Default ON: a
    # longer run is what a portfolio whose width is real actually costs.
    per_route_round_quota_enabled: bool = True
    # R-08: run same-wave VeriTMM rounds in a process pool. Auto-disabled
    # whenever an injected tmm_harness_factory is present (tests).
    parallel_tmm: bool = True
    # R-10: add one knowledge-blind control route beside the normal portfolio.
    # The low-level harness keeps this opt-in for backwards-compatible injected
    # test seams; the production CLI enables it by default. Its quota is added
    # after the normal portfolio is selected, so it never consumes a normal
    # route slot.
    control_route_enabled: bool = False

    @field_validator(
        "maximum_iterations",
        "maximum_initial_routes",
        "maximum_method_research_rounds",
        "max_routes",
        "max_rounds_per_route",
        "route_planning_literature_limit",
        "route_planning_maximum_routes",
        "minimum_rounds_before_llm_stop",
    )
    @classmethod
    def _positive_integer(cls, value: int) -> int:
        if int(value) <= 0:
            raise ValueError("research harness limits must be positive")
        return int(value)

    @field_validator("maximum_refinement_rounds")
    @classmethod
    def _non_negative_integer(cls, value: int) -> int:
        if int(value) < 0:
            raise ValueError("maximum_refinement_rounds must be non-negative")
        return int(value)

    @field_validator("wall_time_seconds")
    @classmethod
    def _positive_time(cls, value: float) -> float:
        if float(value) <= 0:
            raise ValueError("wall_time_seconds must be positive")
        return float(value)


class TMMResearchHarnessResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "tmm-research-harness-result.v1"
    run_id: str
    status: str
    stage: str
    question: str
    problem_analysis: Dict[str, Any] = Field(default_factory=dict)
    method_research: Dict[str, Any] = Field(default_factory=dict)
    strategy_plan: Dict[str, Any] = Field(default_factory=dict)
    iterations: Tuple[ResearchIterationObservation, ...] = ()
    feedback_history: Tuple[ResearchFeedbackDecision, ...] = ()
    final_answer: TMMResearchAnswer | None = None
    telemetry: Dict[str, Any] = Field(default_factory=dict)
    artifacts: Tuple[str, ...] = ()


def _mapping(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return dict(value)
    if callable(getattr(value, "model_dump", None)):
        return dict(value.model_dump(mode="json"))
    raise TypeError(f"expected mapping-like component result, got {type(value).__name__}")


def _unwrap(value: Any, field: str) -> tuple[str, Dict[str, Any], Dict[str, Any]]:
    envelope = _mapping(value)
    status = str(envelope.get("status") or "completed")
    nested = envelope.get(field)
    payload = _mapping(nested) if nested is not None else envelope
    return status, payload, envelope


def _usage_rows(value: Any) -> list[dict[str, Any]]:
    payload = _mapping(value)
    raw = payload.get("usage") or payload.get("telemetry", {}).get("usage") or []
    if isinstance(raw, Mapping):
        return [dict(raw)]
    return [dict(item) for item in raw if isinstance(item, Mapping)]


def _route_hash(route: Mapping[str, Any]) -> str:
    request = " ".join(str(route.get("execution_request_english") or "").lower().split())
    return hashlib.sha256(request.encode("utf-8")).hexdigest()


def _scientific_followup_queries(
    problem: Mapping[str, Any],
    observation: ResearchIterationObservation,
    route: Mapping[str, Any],
) -> list[str]:
    """Translate an execution failure into bounded scientific searches.

    Feedback instructions such as "try another route" belong to the planner,
    not a scholarly search engine.  This function keeps those control words
    out of S2/OpenAlex and emits problem-anchored optics queries instead.
    """

    anchor = " ".join(
        str(
            problem.get("normalized_request_english")
            or problem.get("original_request")
            or "planar multilayer optical design"
        ).split()
    )[:220]
    categories = set(observation.failure_categories)
    queries: list[str] = []
    if "material_data" in categories:
        queries.append(
            f"{anchor}; dispersive optical constants and alternative thin-film material systems"
        )
    if categories & {"numerical_convergence", "solver_disagreement", "physics_violation"}:
        queries.append(
            f"{anchor}; stable transfer-matrix formulations and convergence validation methods"
        )
    if categories & {"search_progress", "objective_shortfall", "budget_exhausted"}:
        queries.append(
            f"{anchor}; alternative nonconvex thickness optimization and robust multilayer parameterizations"
        )
    if categories & {"invalid_input", "runtime_environment"} or observation.compilation_status != "compiled":
        queries.append(
            f"{anchor}; executable isotropic transfer-matrix stack parameterizations"
        )
    if not queries:
        route_kind = str(route.get("route_kind") or "multilayer")
        queries.append(
            f"{anchor}; alternative {route_kind.replace('_', ' ')} structures and known failure modes"
        )
    return list(dict.fromkeys(queries))[:3]


def _merge_method_reports(reports: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    reports = [dict(item) for item in reports]
    if not reports:
        return {"status": "unavailable", "evidence": [], "method_findings": []}
    evidence: dict[str, dict[str, Any]] = {}
    findings: dict[str, dict[str, Any]] = {}
    unresolved: list[str] = []
    queries: dict[str, dict[str, Any]] = {}
    telemetry: list[dict[str, Any]] = []
    for report in reports:
        for item in report.get("evidence", []) or []:
            if isinstance(item, Mapping) and item.get("evidence_id"):
                evidence[str(item["evidence_id"])] = dict(item)
        for index, item in enumerate(report.get("method_findings", []) or []):
            if not isinstance(item, Mapping):
                continue
            key = str(
                item.get("finding_id")
                or item.get("method_name")
                or item.get("name")
                or f"finding_{len(findings) + index}"
            )
            findings[key] = dict(item)
        for item in report.get("queries", []) or []:
            if isinstance(item, Mapping):
                key = str(item.get("query_id") or item.get("query_text") or len(queries))
                queries[key] = dict(item)
        unresolved.extend(str(item) for item in report.get("unresolved_questions", []) or [])
        telemetry.append(dict(report.get("telemetry") or {}))
    status = "completed" if evidence or findings else str(reports[-1].get("status") or "unavailable")
    return {
        "status": status,
        "problem_id": reports[-1].get("problem_id"),
        "queries": list(queries.values()),
        "evidence": list(evidence.values()),
        "method_findings": list(findings.values()),
        "unresolved_questions": list(dict.fromkeys(unresolved)),
        "telemetry_rounds": telemetry,
    }


class TMMResearchHarness:
    """Coordinate analysis, method research, routes, TMM and feedback."""

    def __init__(
        self,
        work_dir: str | Path,
        *,
        problem_analyzer: Any,
        method_researcher: Any,
        strategy_planner: Any,
        task_compiler: Any | None = None,
        tmm_harness_factory: Callable[[Path, str], Any] | None = None,
        feedback_controller: DeterministicResearchFeedbackController | None = None,
        reporter: DeterministicTMMResearchReporter | None = None,
        portfolio_seeder: Any | None = None,
        scoring_standard_builder: Any | None = None,
        route_planner: Any | None = None,
        control_route_planner: Any | None = None,
        route_literature_client: Any | None = None,
        config: TMMResearchHarnessConfig | None = None,
        run_id: str | None = None,
    ) -> None:
        self.work_dir = Path(work_dir).resolve()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id or self.work_dir.name
        self.problem_analyzer = problem_analyzer
        self.method_researcher = method_researcher
        self.strategy_planner = strategy_planner
        self.task_compiler = task_compiler or QwenTMMTaskCompiler()
        self.config = config or TMMResearchHarnessConfig()
        self.feedback = feedback_controller or DeterministicResearchFeedbackController(
            maximum_refinement_rounds=self.config.maximum_refinement_rounds,
            maximum_research_rounds=self.config.maximum_method_research_rounds,
        )
        self.reporter = reporter or DeterministicTMMResearchReporter()
        # R-05: optional dual-source portfolio seeder; only consulted when
        # config.portfolio_seeding_enabled is set.
        self.portfolio_seeder = portfolio_seeder
        # The run's ranking criteria. Built once at the top of run(), because
        # the criteria come from the user's question and this object is
        # constructed before the question is known. None means the run falls
        # back to copying the first route's objectives onto the others.
        self.scoring_standard_builder = scoring_standard_builder
        self.scoring_standard: ScoringStandard | None = None
        self.route_planner = route_planner
        self.control_route_planner = control_route_planner
        self.route_literature_client = route_literature_client
        self.route_plan_result: RoutePlanResult | None = None
        self.control_route_plan_result: RoutePlanResult | None = None
        self.control_route_plan_envelope: Dict[str, Any] | None = None
        self.tmm_harness_factory = tmm_harness_factory or self._default_tmm_factory
        # R-08: Phase-1b executor; tests may inject a fake batch callable.
        self._tmm_batch_fn = _tmm_batch_default
        # R-04: reflection client (flash/turbo tier for cost efficiency).
        # R-09 fix: default to the turbo-role ADAPTER implementing the
        # harness client protocol (.call); the raw SDK object returned by
        # get_qwen_client has no .call and would crash Phase-2 reflection.
        self._reflection_client = ArticleTurboQwenClient()
        # The run's effective iteration ceiling. Equal to the configured
        # maximum_iterations until the portfolio exists; raised once its width
        # is known when per_route_round_quota_enabled is set. Kept as run state
        # rather than recomputed per call so every gate, the telemetry and the
        # tournament state all read the same number.  Read off self.config, not
        # the parameter: the caller may pass None and take the defaults.
        self._iteration_ceiling = int(self.config.maximum_iterations)
        self.events_path = self.work_dir / "RESEARCH_EVENTS.jsonl"
        # R-06 concurrency primitives. _state_lock guards every shared mutable
        # ledger (usage rows, artifacts and observations);
        # _event_lock serializes sequence-number assignment + file append.
        self._state_lock = threading.RLock()
        self._event_lock = threading.Lock()
        # Sequence numbers come from an in-memory counter seeded from any
        # pre-existing events file, never from recounting lines per call.
        if self.events_path.exists():
            with self.events_path.open("r", encoding="utf-8") as handle:
                self._event_sequence = sum(1 for line in handle if line.strip())
        else:
            self._event_sequence = 0
        self._artifacts: list[str] = []
        self._usage: list[dict[str, Any]] = []
        self._service_telemetry: list[dict[str, Any]] = []
        self._observations: list[ResearchIterationObservation] = []
        # Resume metadata is kept separate from the live usage/observation
        # ledgers.  A resumed run copies the parent artifacts into a new
        # directory, loads the parent observations, and only adds new calls to
        # the live ledgers.  The parent telemetry is folded into the final
        # snapshot so a continuation does not look artificially cheap or
        # short.
        self._resume_parent_telemetry: Dict[str, Any] = {}
        self._resume_parent_run_id: str | None = None
        # R-06: route_id -> RouteTrack for the most recent (or running)
        # tournament. Exposed read-only-ish for introspection, tests and R-07
        # summarization; the scheduler itself owns the single reference.
        self.tournament_tracks: Dict[str, RouteTrack] = {}
        self._started = 0.0

    def _default_tmm_factory(self, directory: Path, run_id: str) -> TMMHarnessOrchestrator:
        return TMMHarnessOrchestrator(
            directory,
            run_id=run_id,
            config=TMMHarnessConfig(
                use_qwen_policy=self.config.use_qwen_policy_inside_tmm,
                qwen_force_mock=self.config.qwen_force_mock,
            ),
        )

    def _write(self, name: str, value: Any) -> Path:
        path = self.work_dir / name
        payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
        atomic_write_json(path, payload)
        relative = path.relative_to(self.work_dir).as_posix()
        with self._state_lock:
            if relative not in self._artifacts:
                self._artifacts.append(relative)
        return path

    def _rank_by_scoring_standard(
        self, candidates_by_route: Mapping[str, Sequence[Mapping[str, Any]]]
    ) -> dict[str, Any]:
        """Score every candidate by the frozen expression and rank the routes.

        Written as its own artifact rather than left inside the report, so the
        leaderboard can be recomputed from the recorded measurements without
        rerunning anything or trusting the prose.

        Routes that produced nothing scoreable are listed with the reason
        instead of being dropped: with several routes racing, some are expected
        to come back empty, and an empty route is a result about that strategy,
        not a hole in the ranking.
        """

        standard = self.scoring_standard
        assert standard is not None
        routes: list[dict[str, Any]] = []
        for route_id in sorted(candidates_by_route):
            rows = list(candidates_by_route[route_id] or ())
            ordered = standard.rank(rows)
            scoreable = [(index, outcome) for index, outcome in ordered if outcome.ok]
            entry: dict[str, Any] = {
                "route_id": route_id,
                "candidates_scored": len(rows),
                "candidates_scoreable": len(scoreable),
            }
            if scoreable:
                index, outcome = scoreable[0]
                best = rows[index]
                entry["representative"] = {
                    "candidate_id": str(best.get("candidate_id") or ""),
                    "iteration_id": str(best.get("iteration_id") or ""),
                    "experiment_id": str(best.get("experiment_id") or ""),
                    "score": outcome.value,
                    "inputs": dict(outcome.values),
                }
            else:
                unscoreable = [outcome for _, outcome in ordered if not outcome.ok]
                entry["representative"] = None
                entry["unscoreable_reason"] = (
                    "the route produced no candidates"
                    if not rows
                    else "; ".join(
                        dict.fromkeys(
                            reason
                            for outcome in unscoreable
                            for reason in (
                                *(f"missing {name}" for name in outcome.missing),
                                *outcome.errors,
                            )
                        )
                    )
                    or "no candidate carried the measurements the standard needs"
                )
            routes.append(entry)
        ranked = sorted(
            (entry for entry in routes if entry.get("representative")),
            key=lambda entry: (
                -float(entry["representative"]["score"] or 0.0),
                entry["route_id"],
            ),
        )
        for position, entry in enumerate(ranked, 1):
            entry["rank"] = position
        winner = ranked[0] if ranked else None
        self._event(
            "scoring_standard_ranking_written",
            routes=len(routes),
            ranked=len(ranked),
            winner=(winner or {}).get("route_id"),
        )
        return {
            "schema_version": SCORING_STANDARD_SCHEMA_VERSION,
            "run_id": self.run_id,
            "formula": standard.formula,
            "metrics": [metric.canonical_id for metric in standard.metrics],
            "question_digest": standard.question_digest,
            "routes": routes,
            "leaderboard": [
                {
                    "rank": entry["rank"],
                    "route_id": entry["route_id"],
                    "candidate_id": entry["representative"]["candidate_id"],
                    "score": entry["representative"]["score"],
                }
                for entry in ranked
            ],
            "winner": winner["route_id"] if winner else None,
            "routes_without_a_scoreable_result": [
                entry["route_id"] for entry in routes if not entry.get("representative")
            ],
        }

    def _establish_scoring_standard(
        self, question: str, problem: Mapping[str, Any]
    ) -> ScoringStandard | None:
        """Fix the run's ranking criteria before any route runs.

        Two LLM stages choose which measurable quantities matter and write the
        single expression that ranks designs by them; a local check rejects any
        quantity the simulator cannot compute and any expression whose direction
        is reversed, and the rejection is sent back for another attempt.

        Failure degrades rather than aborts.  Without a standard the compiler
        falls back to copying the first route that compiled onto the others,
        which still yields comparable numbers -- it just takes the criteria from
        whichever route happened to be first.  A run that ends up there says so
        in its artifact and its event stream, because the two mechanisms answer
        "what was this ranked by?" differently and a reader has to be able to
        tell which one applied.
        """

        if not self.config.scoring_standard_enabled:
            return None
        builder = self.scoring_standard_builder
        if builder is None:
            # The plus-tier ADAPTER, not the raw SDK object: this builder calls
            # .call(messages, max_tokens=, force_mock=) like every other stage.
            from .problem_analyzer import ArticlePlusQwenClient

            builder = QwenScoringStandardBuilder(ArticlePlusQwenClient(role="plus"))
        try:
            result = builder.build(
                question,
                problem_analysis=dict(problem),
                force_mock=self.config.qwen_force_mock,
            )
        except Exception as exc:
            self._write(
                "SCORING_STANDARD.json",
                {
                    "status": "unavailable",
                    "validation_errors": [f"{type(exc).__name__}: {exc}"],
                    "ranking_mechanism": "first_route_objective_freeze",
                },
            )
            self._event("scoring_standard_unavailable", reason=f"{type(exc).__name__}")
            return None
        with self._state_lock:
            # Read off the object, not off its serialised form: the combined
            # usage of the two stages is a derived property, so model_dump
            # leaves it out and the calls would go unmetered.
            rows = getattr(result, "usage", None)
            if rows:
                self._usage.extend(dict(row) for row in rows)
            else:
                self._usage.extend(_usage_rows(result))
        envelope = result.model_dump(mode="json")
        standard = result.standard
        envelope["ranking_mechanism"] = (
            "frozen_scoring_standard"
            if standard is not None
            else "first_route_objective_freeze"
        )
        path = self._write("SCORING_STANDARD.json", envelope)
        if standard is None:
            self._event(
                "scoring_standard_unavailable",
                status=result.status,
                errors=len(result.validation_errors),
            )
            return None
        # Attested for the same reason a route plan is: the whole claim is that
        # the criteria were fixed BEFORE the results existed, and a hash
        # recorded at this point in the run is what makes that checkable.
        self._attest(
            path,
            "pre_execution_scoring_standard",
            formula=standard.formula,
            metrics=[metric.canonical_id for metric in standard.metrics],
            question_digest=standard.question_digest,
        )
        adopt = getattr(self.task_compiler, "adopt_scoring_standard", None)
        if callable(adopt):
            adopt(standard)
        self._event(
            "scoring_standard_fixed",
            formula=standard.formula,
            metric_count=len(standard.metrics),
        )
        return standard

    def _plan_routes_from_literature(
        self, question: str, problem: Mapping[str, Any]
    ) -> RoutePlanResult | None:
        """Derive the initial portfolio, and its width, from the literature.

        The count of routes is the point.  Everywhere else in this harness the
        portfolio width is a configured number, so a problem with two real
        strategy axes gets a padded third and a problem with five loses two --
        and an axis nobody proposed cannot be recovered by iterating inside the
        axes that were.  Here a model reads the request together with the papers
        retrieved for it and answers with as many routes as the problem has,
        inside a bound, each stating what it tunes.

        Failure degrades rather than aborts, for the same reason the scoring
        standard does: the legacy planner is a working path, and a run that
        produced routes some other way is far more useful than a run that
        produced none.  Which mechanism planned the portfolio is recorded in the
        artifact and the event stream, because a reader comparing two runs has
        to be able to tell.
        """

        if not self.config.route_planning_enabled:
            return None
        planner = self.route_planner
        if planner is None:
            # The plus-tier ADAPTER for the same reason the seeder needs it: the
            # raw SDK object has no .call(messages, max_tokens=, force_mock=).
            from .problem_analyzer import ArticlePlusQwenClient
            from .route_planning import DefaultRouteLiteratureClient

            literature_client = self.route_literature_client
            if literature_client is None:
                try:
                    literature_client = DefaultRouteLiteratureClient()
                except Exception as exc:
                    # No key pool, no network, no gateway: planning continues
                    # from theory alone rather than losing the stage.
                    self._event(
                        "route_literature_unavailable", reason=f"{type(exc).__name__}: {exc}"
                    )
                    literature_client = None
            planner = QwenLiteratureRoutePlanner(
                ArticlePlusQwenClient(role="plus"),
                literature_client=literature_client,
                literature_limit=self.config.route_planning_literature_limit,
                maximum_routes=self.config.route_planning_maximum_routes,
            )
        try:
            result = planner.plan(
                question,
                problem_analysis=dict(problem),
                force_mock=self.config.qwen_force_mock,
            )
        except Exception as exc:
            self._write(
                "ROUTE_PLANNING.json",
                {
                    "status": "unavailable",
                    "validation_errors": [f"{type(exc).__name__}: {exc}"],
                    "planning_mechanism": "strategy_planner_fallback",
                },
            )
            self._event("route_planning_unavailable", reason=f"{type(exc).__name__}")
            return None
        with self._state_lock:
            # Off the object, not off its dump: the two stages' combined usage
            # is a derived property, so a caller metering the serialised form
            # would record nothing.
            rows = getattr(result, "usage", None)
            if rows:
                self._usage.extend(dict(row) for row in rows)
            else:
                self._usage.extend(_usage_rows(result))
        planned = result.status == "planned" and bool((result.plan or {}).get("routes"))
        envelope = result.sidecar()
        envelope["planning_mechanism"] = (
            "literature_route_planning" if planned else "strategy_planner_fallback"
        )
        path = self._write("ROUTE_PLANNING.json", envelope)
        if not planned:
            self._event(
                "route_planning_unavailable",
                status=result.status,
                errors=len(result.validation_errors),
            )
            return None
        # Attested here because the claim is that these axes were chosen before
        # any result existed; a hash taken at this point is what makes that
        # checkable afterwards.
        self._attest(
            path,
            "pre_execution_route_plan",
            route_count=result.route_count,
            route_ids=[route["route_id"] for route in result.plan["routes"]],
            question_digest=result.question_digest,
            literature_status=result.literature.status,
            papers=result.literature.returned,
        )
        self._event(
            "routes_planned_from_literature",
            route_count=result.route_count,
            papers=result.literature.returned,
            queries=len(result.query_result.queries),
            literature_status=result.literature.status,
            warnings=len(result.warnings),
        )
        return result

    def _plan_control_route(
        self, question: str, problem: Mapping[str, Any],
        *,
        prior_iterations: Iterable[Mapping[str, Any]] = (),
        feedback_directives: Iterable[str] = (),
        chain_id: str | None = None,
    ) -> RoutePlanResult | None:
        """Plan the one knowledge-blind control route.

        This stage is intentionally adjacent to, rather than inside, the
        literature planner.  It is called before method research is merged and
        its planner receives only the user/problem contract plus (on a
        continuation) this route's own measurements.  That ordering is the
        isolation guarantee: an empty evidence list after a literature call is
        not a control arm, because the literature model has already seen the
        papers.
        """

        if not self.config.control_route_enabled:
            return None
        planner = self.control_route_planner
        if planner is None:
            from .problem_analyzer import ArticlePlusQwenClient

            planner = QwenMemoryControlRoutePlanner(
                ArticlePlusQwenClient(role="plus")
            )
            self.control_route_planner = planner
        try:
            result = planner.plan(
                question,
                problem_analysis=dict(problem),
                force_mock=self.config.qwen_force_mock,
                prior_iterations=prior_iterations,
                feedback_directives=feedback_directives,
                chain_id=chain_id,
            )
        except TypeError:
            # Narrow injected planners from older test seams may only implement
            # the initial call.  They still get an isolated control invocation;
            # a continuation simply cannot claim to have used feedback.
            try:
                result = planner.plan(
                    question,
                    problem_analysis=dict(problem),
                    force_mock=self.config.qwen_force_mock,
                )
            except Exception as exc:
                self._event(
                    "control_route_planning_unavailable",
                    reason=f"{type(exc).__name__}: {exc}",
                )
                return None
        except Exception as exc:
            self._event(
                "control_route_planning_unavailable",
                reason=f"{type(exc).__name__}: {exc}",
            )
            return None

        with self._state_lock:
            rows = getattr(result, "usage", None)
            if rows:
                self._usage.extend(dict(row) for row in rows)
            else:
                self._usage.extend(_usage_rows(result))

        sidecar_fn = getattr(planner, "sidecar", None)
        try:
            envelope = (
                sidecar_fn(result)
                if callable(sidecar_fn)
                else result.sidecar()
            )
            envelope = dict(envelope or {})
        except Exception as exc:
            envelope = {
                "status": str(getattr(result, "status", "unavailable")),
                "validation_errors": [
                    f"control sidecar failed: {type(exc).__name__}: {exc}"
                ],
            }
        envelope.update(
            {
                "planning_mechanism": CONTROL_ROUTE_SOURCE,
                "knowledge_source": "model_prior_knowledge_only",
                "no_literature_disclosure": CONTROL_NO_LITERATURE_DISCLOSURE,
                "literature_client_invoked": False,
                "method_research_supplied": False,
                "evidence_ids_allowed": [],
            }
        )
        self.control_route_plan_envelope = envelope
        path = self._write(CONTROL_ROUTE_PLANNING_ARTIFACT, envelope)
        plan = getattr(result, "plan", None) or {}
        routes = list(plan.get("routes") or ()) if isinstance(plan, Mapping) else []
        planned = str(getattr(result, "status", "")) == "planned" and len(routes) == 1
        self.control_route_plan_result = result
        if not planned:
            self._event(
                "control_route_planning_unavailable",
                status=str(getattr(result, "status", "unavailable")),
                errors=len(getattr(result, "validation_errors", ()) or ()),
            )
            return None

        route_id = str(routes[0].get("route_id") or CONTROL_ROUTE_ID)
        literature = getattr(result, "literature", None)
        self._attest(
            path,
            "pre_execution_control_route_plan",
            route_count=1,
            route_ids=[route_id],
            question_digest=str(getattr(result, "question_digest", "")),
            literature_status=str(getattr(literature, "status", "not_consulted")),
            papers=0,
            method_research_supplied=False,
        )
        self._event(
            "control_route_planned",
            route_count=1,
            route_id=route_id,
            literature_status=str(getattr(literature, "status", "not_consulted")),
            s2_literature_included=False,
        )
        return result

    def _attest(self, path: Path, kind: str, **context: Any) -> Path:
        """Record an absolute-time, content-hashed attestation for one artifact.

        The attestation is a sibling file so the attested artifact keeps its own
        immutable contract.  Recording the sha256 of the bytes actually written
        means a later edit to the artifact no longer matches its attestation,
        which is what makes "this plan existed before execution" checkable
        instead of merely asserted.
        """
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        target = path.with_name(f"{path.stem}.ATTESTATION.json")
        atomic_write_json(
            target,
            {
                "artifact": path.relative_to(self.work_dir).as_posix(),
                "artifact_kind": kind,
                "artifact_sha256": digest,
                "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
                "elapsed_seconds": time.perf_counter() - self._started,
                "run_id": self.run_id,
                **context,
            },
        )
        relative = target.relative_to(self.work_dir).as_posix()
        with self._state_lock:
            if relative not in self._artifacts:
                self._artifacts.append(relative)
        return target

    def _event(self, event_type: str, **payload: Any) -> None:
        # R-06: the sequence number comes from an in-memory counter guarded by
        # _event_lock. The previous implementation recounted the file's lines
        # on every call; with wave threads emitting events concurrently, two
        # callers observed the same count and the log got duplicate/skipped
        # sequence numbers. Locking BOTH the counter increment and the append
        # keeps sequence unique and monotone under concurrency.
        elapsed = time.perf_counter() - self._started
        with self._event_lock:
            self._event_sequence += 1
            record = {
                "sequence": self._event_sequence,
                # Absolute UTC wall-clock alongside the relative elapsed time: the
                # audit trail must show *when* a plan was recorded, not only how
                # long after process start, so a pre-execution plan is provable
                # from the artifacts alone.
                "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
                "elapsed_seconds": elapsed,
                "event_type": event_type,
                **payload,
            }
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            with self._state_lock:
                if "RESEARCH_EVENTS.jsonl" not in self._artifacts:
                    self._artifacts.append("RESEARCH_EVENTS.jsonl")

    def _allocate_round_quota(self, route_count: int) -> None:
        """Give each route its own round quota instead of a shared pool.

        The shared pool is the older shape: one maximum_iterations count that
        every route draws from. It made the portfolio's width unreliable --
        five routes against a pool of six rounds means the first two routes
        iterate and the last three never execute at all, so an axis that was
        planned is reported as if it had been tried.

        The quota raises the run's ceiling to exactly routes x rounds, which is
        what it costs for every route to spend its own allowance. It is not a
        larger shared pool: max_rounds_per_route still caps each route on its
        own, so rounds a route does not use are not available to any other
        route. The wall-clock guard is untouched, and the ceiling is never
        lowered below the configured value.
        """

        if not self.config.per_route_round_quota_enabled:
            return
        rounds = int(self.config.max_rounds_per_route)
        ceiling = max(
            int(self.config.maximum_iterations), max(0, int(route_count)) * rounds
        )
        self._iteration_ceiling = ceiling
        self._event(
            "route_round_quota_allocated",
            routes=int(route_count),
            rounds_per_route=rounds,
            iteration_ceiling=ceiling,
            shared_iteration_pool=False,
        )

    def _audit_route_termination(
        self, tracks: Sequence[RouteTrack]
    ) -> Dict[str, Any]:
        """Check that every finished route recorded WHY it stopped. Fail-open.

        A route that says "I stopped because two rounds in a row gained less
        than 1e-3" is far more useful to a reader than one that simply stops,
        so the reason is worth auditing. It is worth auditing as a BONUS,
        though, not as a gate: the measurements a route produced are true
        whether or not the prose beside them was filled in, and discarding a
        route's results over a missing sentence would throw away the evidence
        to protect the paperwork.

        So this returns a record and never a verdict. Nothing downstream may
        branch on it; a missing reason is surfaced, counted, and left alone.
        """

        documented: list[str] = []
        missing: list[Dict[str, str]] = []
        for track in tracks:
            status = str(getattr(track, "status", "") or "")
            reason = str(getattr(track, "termination_reason", "") or "").strip()
            if status == TRACK_RACING:
                # Still racing at the end of the race: no reason is expected
                # because the route never finished, but it is worth naming.
                missing.append({"route_id": track.route_id, "status": status})
                continue
            if reason:
                documented.append(track.route_id)
            else:
                missing.append({"route_id": track.route_id, "status": status})
        for entry in missing:
            self._event(
                "route_termination_reason_missing",
                route_id=entry["route_id"],
                status=entry["status"],
                fail_open=True,
                note="recorded only; this route's results stand",
            )
        return {
            "schema_version": "route-termination-audit.v1",
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "policy": "fail_open",
            "policy_note": (
                "Recording an end reason is a bonus, not a requirement. A route "
                "with no reason is reported here and keeps every result it "
                "produced; nothing in this run branches on this audit."
            ),
            "routes_checked": len(tracks),
            "documented": documented,
            "documented_count": len(documented),
            "missing": missing,
            "missing_count": len(missing),
            "rounds_per_route": int(self.config.max_rounds_per_route),
            "iteration_ceiling": self._iteration_ceiling,
            "shared_iteration_pool": not self.config.per_route_round_quota_enabled,
            "per_route_rounds_used": {
                track.route_id: int(getattr(track, "rounds_used", 0) or 0)
                for track in tracks
            },
        }

    def _budget_remaining(self, iteration_count: int) -> bool:
        return (
            iteration_count < self._iteration_ceiling
            and time.perf_counter() - self._started < self.config.wall_time_seconds
        )

    def _budget_termination_reason(self) -> str:
        """Name the budget that actually closed the next-round gate."""

        iteration_exhausted = len(self._observations) >= self._iteration_ceiling
        wall_exhausted = (
            time.perf_counter() - self._started >= self.config.wall_time_seconds
        )
        if iteration_exhausted and wall_exhausted:
            return "run iteration and wall-time budgets exhausted"
        if wall_exhausted:
            return "run wall-time budget exhausted"
        if iteration_exhausted:
            return "run iteration budget exhausted"
        # A caller should only reach this helper after _budget_remaining() was
        # false, but keep the record conservative if a future gate changes.
        return "run budget gate closed"

    def _research(self, problem: Dict[str, Any], feedback_queries: Iterable[str] = ()) -> Any:
        kwargs = {
            "online": self.config.online_method_research,
            "explicit_queries": list(feedback_queries),
        }
        try:
            return self.method_researcher.research(problem, **kwargs)
        except TypeError:
            try:
                return self.method_researcher.research(problem, explicit_queries=list(feedback_queries))
            except TypeError:
                return self.method_researcher.research(problem)

    def _plan(
        self,
        problem: Dict[str, Any],
        research: Dict[str, Any],
        *,
        prior_iterations: Iterable[Mapping[str, Any]] = (),
        feedback_directives: Iterable[str] = (),
        chain_id: str | None = None,
    ) -> tuple[Any, dict[str, dict[str, list[str]]]]:
        try:
            result = self.strategy_planner.plan(
                problem,
                research,
                prior_iterations=prior_iterations,
                feedback_directives=feedback_directives,
                force_mock=self.config.qwen_force_mock,
                chain_id=chain_id,
            )
            # R-04: StrategyPlanningResult now has pre_declarations field
            pre_decls = getattr(result, "pre_declarations", {})
            return result, pre_decls
        except TypeError:
            # Backward-compatible seam for a narrow injected planner.
            result = self.strategy_planner.plan(
                problem,
                research,
                prior_iterations=prior_iterations,
                force_mock=self.config.qwen_force_mock,
            )
            pre_decls = getattr(result, "pre_declarations", {})
            return result, pre_decls

    def _plan_control_continuation(
        self,
        problem: Dict[str, Any],
        *,
        prior_iterations: Iterable[Mapping[str, Any]] = (),
        feedback_directives: Iterable[str] = (),
        chain_id: str | None = None,
    ) -> tuple[Any, dict[str, dict[str, list[str]]]]:
        """Replan the control chain without exposing method research."""

        planner = self.control_route_planner
        if planner is None:
            raise RuntimeError("the control route planner is not available")
        question = str(
            problem.get("original_request")
            or problem.get("normalized_request_english")
            or ""
        )
        try:
            result = planner.plan(
                question,
                problem_analysis=dict(problem),
                force_mock=self.config.qwen_force_mock,
                prior_iterations=prior_iterations,
                feedback_directives=feedback_directives,
                chain_id=chain_id,
            )
        except TypeError:
            # Compatibility for an injected first-generation control planner;
            # it remains isolated, although it cannot consume feedback until
            # its implementation accepts the continuation fields.
            result = planner.plan(
                question,
                problem_analysis=dict(problem),
                force_mock=self.config.qwen_force_mock,
            )
        pre_decls = getattr(result, "pre_declarations", {})
        return result, pre_decls

    def _prepare_track_round(
        self,
        track: RouteTrack,
        wave_index: int,
        iteration_index: int,
        pre_declarations: Dict[str, Dict[str, list[str]]],
    ) -> Dict[str, Any]:
        """R-08 Phase-1a (serial): allocate dir -> attest -> compile -> lineage.

        Everything that must NOT run concurrently (directory creation, the
        LLM compiler call, attestation and chain-local lineage writes)
        happens here. The VeriTMM execution itself moves to Phase-1b.
        """
        route = track.current_route
        iteration_id = f"iteration_{iteration_index:02d}"
        iteration_dir = self.work_dir / "iterations" / iteration_id
        iteration_dir.mkdir(parents=True, exist_ok=False)
        route_path = iteration_dir / "ROUTE.json"
        atomic_write_json(route_path, route)
        route_id = track.route_id
        # Declarations are keyed by the stable chain id: a revision keeps the
        # SAME track and the SAME id, so no lineage resolution is needed here
        # anymore -- the former rename-and-resolve patch is structurally gone.
        pre_decl = pre_declarations.get(route_id, {})
        self._attest(
            route_path,
            "pre_execution_plan",
            iteration_id=iteration_id,
            route_id=route_id,
            scientific_hypothesis=str(route.get("scientific_hypothesis") or ""),
            expected_advantages=list(route.get("expected_advantages") or []),
            known_risks=list(route.get("known_risks") or []),
            expected_observations=list(pre_decl.get("expected_observations") or []),
            stop_conditions=list(pre_decl.get("stop_conditions") or []),
        )
        self._event(
            "route_started",
            wave=wave_index,
            iteration_id=iteration_id,
            route_id=route_id,
            source=track.source,
            rounds_used=track.rounds_used,
        )

        compilation: Any | None = None
        try:
            compilation = self.task_compiler.compile(
                str(route["execution_request_english"]),
                force_mock=self.config.qwen_force_mock,
            )
            with self._state_lock:
                self._usage.extend(_usage_rows(compilation))
            compilation_payload = _mapping(compilation)
        except Exception as exc:
            compilation_payload = {
                "status": "unavailable",
                "task": None,
                "rationale": "Task compilation raised a recorded exception.",
                "validation_errors": [f"{type(exc).__name__}: {exc}"],
                "usage": [],
            }
        atomic_write_json(iteration_dir / "TASK_COMPILATION.json", compilation_payload)
        task = getattr(compilation, "task", None) if compilation is not None else None
        if task is None and isinstance(compilation_payload.get("task"), Mapping):
            from .design_task import OpticalDesignTask

            task = OpticalDesignTask.model_validate(compilation_payload["task"])
        compilation_status = str(compilation_payload.get("status") or "invalid")
        if task is not None and compilation_status == "compiled":
            task_file = iteration_dir / "COMPILED_TASK.json"
            atomic_write_json(task_file, task.model_dump(mode="json"))
            # Chain-local lineage: every adjusted round names its parent round
            # WITHIN this route's chain and carries the parent task
            # fingerprint, so P17 auditability survives parallel chains.
            task_sha256 = hashlib.sha256(task_file.read_bytes()).hexdigest()
            track.lineage_round += 1
            declared_reason = str(route.get("revision_reason") or "").strip()
            if declared_reason:
                adjustment_reason = declared_reason
            elif track.lineage_round == 1:
                adjustment_reason = "initial round: no prior round to adjust from"
            else:
                adjustment_reason = (
                    f"continuation round {track.lineage_round} of route "
                    f"{route_id}: revised after reflecting on this route's own "
                    f"prior observations"
                )
            parent_sha = track.lineage_parent_sha
            write_lineage(
                LineageRecord(
                    round=track.lineage_round,
                    parent_round=None if parent_sha is None else track.lineage_round - 1,
                    parent_task_sha256=parent_sha,
                    task_sha256=task_sha256,
                    adjustment_reason=adjustment_reason,
                ),
                iteration_dir,
            )
            lineage_relative = (
                (iteration_dir / LINEAGE_FILENAME).relative_to(self.work_dir).as_posix()
            )
            with self._state_lock:
                if lineage_relative not in self._artifacts:
                    self._artifacts.append(lineage_relative)
            track.lineage_parent_sha = task_sha256
        return {
            "track": track,
            "route": route,
            "route_id": route_id,
            "iteration_id": iteration_id,
            "iteration_dir": iteration_dir,
            "task": task,
            "compilation_status": compilation_status,
            "compilation_payload": compilation_payload,
        }

    def _run_tmm_serial_fallback(
        self,
        pre_ctx: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Run ONE compiled task through the injected/legacy harness path.

        Used when the tournament runs with an injected tmm_harness_factory
        (every existing test) or when fewer than two tasks are ready. This is
        the EXACT execution shape Phase-1 used before R-08, so injected
        factories keep working and single-route waves gain nothing from a
        spawn.
        """
        execution_dir = pre_ctx["iteration_dir"] / "tmm_run"
        harness = self.tmm_harness_factory(
            execution_dir, f"{self.run_id}.{pre_ctx['iteration_id']}"
        )
        started = time.perf_counter()
        try:
            run_result = harness.run(pre_ctx["task"])
            run_payload = _mapping(run_result)
            result_path = (
                (execution_dir / "FINAL_RESULT.json")
                .relative_to(self.work_dir)
                .as_posix()
            )
            return {
                "ok": True,
                "run_payload": run_payload,
                "result_path": result_path,
                "cpu_seconds": 0.0,
                "wall_seconds": time.perf_counter() - started,
            }
        except Exception as exc:
            error_payload = {
                "status": "failed",
                "experiment_results": [],
                "diagnoses": [
                    {
                        "category": "runtime_environment",
                        "recoverable_with_tmm": False,
                        "allowed_actions": ["stop"],
                        "explanation": "The TMM execution raised a recorded exception.",
                        "context": {
                            "exception_type": type(exc).__name__,
                            "message": str(exc)[:500],
                        },
                    }
                ],
            }
            error_file = execution_dir / "EXECUTION_ERROR.json"
            error_file.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(error_file, error_payload)
            return {
                "ok": False,
                "error_payload": error_payload,
                "result_path": error_file.relative_to(self.work_dir).as_posix(),
                "cpu_seconds": 0.0,
                "wall_seconds": time.perf_counter() - started,
            }

    def _observe_track_round(
        self,
        pre_ctx: Dict[str, Any],
        run_outcome: Dict[str, Any],
        wave_index: int,
        observations: list[ResearchIterationObservation],
    ) -> Dict[str, Any]:
        """R-08 Phase-1c (serial): normalize one finished round into ledgers.

        observation construction, per-chain score/best appends, the shared
        observations append (locked), ITERATION_OBSERVATION.json and the
        route_completed event -- identical semantics to pre-R-08 Phase 1.
        """
        track: RouteTrack = pre_ctx["track"]
        route: Dict[str, Any] = pre_ctx["route"]
        route_id = pre_ctx["route_id"]
        iteration_dir = pre_ctx["iteration_dir"]
        compilation_status = pre_ctx["compilation_status"]
        compilation_payload = pre_ctx["compilation_payload"]
        if run_outcome.get("ok"):
            run_payload = dict(run_outcome.get("run_payload") or {})
            result_path = run_outcome.get("result_path")
        elif "error_payload" in run_outcome:
            run_payload = dict(run_outcome["error_payload"])
            result_path = run_outcome.get("result_path")
        else:
            # Compiled but never executed (budget-blocked batch slot).
            run_payload = {}
            result_path = None
        task = pre_ctx.get("task")
        task_path = None
        compiled_file = iteration_dir / "COMPILED_TASK.json"
        if task is not None and compilation_status == "compiled" and compiled_file.exists():
            task_path = compiled_file.relative_to(self.work_dir).as_posix()
        observation = observation_from_run_result(
            iteration_id=pre_ctx["iteration_id"],
            route_id=route_id,
            route_title=str(route["title"]),
            compilation_status=compilation_status,
            compilation_rationale=str(compilation_payload.get("rationale") or ""),
            compilation_errors=tuple(
                str(item)
                for item in compilation_payload.get("validation_errors", []) or []
            ),
            run_result=run_payload,
            work_dir=iteration_dir.relative_to(self.work_dir).as_posix(),
            task_path=task_path,
            result_path=result_path,
            compiled_task=(
                task.model_dump(mode="json") if task is not None else None
            ),
        )
        # Per-chain ledgers live on the track. These three appends carry NO
        # lock by R-06 invariant: they execute on the main thread only (Phase
        # 1a/1c are serial; the concurrent phases never touch foreign tracks).
        # `observations` IS shared -> locked append below.
        if observation.best_target_score is not None:
            track.score_history.append(float(observation.best_target_score))
        if observation.physically_valid_candidate_count > 0:
            track.best_candidate_ids.extend(
                cid for cid in observation.selected_candidate_ids if cid not in track.best_candidate_ids
            )
        track.rounds_used += 1
        with self._state_lock:
            observations.append(observation)
            self._observations = observations
        atomic_write_json(
            iteration_dir / "ITERATION_OBSERVATION.json",
            observation.model_dump(mode="json"),
        )
        self._event(
            "route_completed",
            wave=wave_index,
            iteration_id=pre_ctx["iteration_id"],
            route_id=route_id,
            run_status=observation.run_status,
            valid_candidates=observation.physically_valid_candidate_count,
            best_target_score=observation.best_target_score,
        )
        return {
            "track": track,
            "iteration_id": pre_ctx["iteration_id"],
            "iteration_dir": iteration_dir,
            "observation": observation,
            "compilation_payload": compilation_payload,
            "run_payload": run_payload,
        }

    def _execute_track_round(
        self,
        track: RouteTrack,
        wave_index: int,
        pre_declarations: Dict[str, Dict[str, list[str]]],
        observations: list[ResearchIterationObservation],
    ) -> Dict[str, Any]:
        """Backward-compatible single-round composition (1a -> serial TMM -> 1c).

        R-08 split Phase 1 into _prepare_track_round / batch execution /
        _observe_track_round. Single-route waves and injected harness
        factories keep this exact shape; the wave loop routes multi-route
        default-factory waves through the process pool instead.
        """
        with self._state_lock:
            iteration_index = len(observations) + 1
        pre_ctx = self._prepare_track_round(
            track, wave_index, iteration_index, pre_declarations,
        )
        if pre_ctx["task"] is not None and pre_ctx["compilation_status"] == "compiled":
            run_outcome = self._run_tmm_serial_fallback(pre_ctx)
            cpu = float(run_outcome.get("cpu_seconds") or 0.0)
            if cpu > 0.0:
                self._record_tmm_cpu(cpu)
        else:
            run_outcome = {}
        return self._observe_track_round(pre_ctx, run_outcome, wave_index, observations)

    def _reflect_track(
        self,
        ctx: Dict[str, Any],
        pre_declarations: Dict[str, Dict[str, list[str]]],
    ) -> Dict[str, Any]:
        """Worker-safe Phase-2 body: one LLM reflection + base sidecar.

        Runs inside the wave thread pool. It mutates NOTHING shared except
        through locked helpers; disagreement resolution happens afterwards on
        the main thread so ledger mutations stay serialized.
        """
        track: RouteTrack = ctx["track"]
        observation: ResearchIterationObservation = ctx["observation"]
        pre_decl = pre_declarations.get(track.route_id, {})
        epsilon = DEFAULT_MINIMUM_SCORE_IMPROVEMENT
        try:
            reflection = reflect_on_route(
                self._reflection_client,
                pre_declarations=pre_decl,
                observation=observation.model_dump(mode="json"),
                score_history=list(track.score_history),
                epsilon=epsilon,
                force_mock=self.config.qwen_force_mock,
            )
            reflection_available = reflection.degraded_reason == ""
        except Exception as exc:
            # Engine-side failure of the REFLECTION infrastructure is treated
            # exactly like a degraded response (vote ABSENT, never a stop).
            reflection = RouteReflection.degraded(
                reason=f"reflection infrastructure failure: {type(exc).__name__}: {exc}"[:300]
            )
            reflection_available = False
        observed_metrics_snapshot = {
            "best_target_score": observation.best_target_score,
            "valid_candidates": observation.physically_valid_candidate_count,
            "run_status": observation.run_status,
            "tightest_margin": observation.best_robustness_score,
        }
        attestation_path = ctx["iteration_dir"] / "ROUTE.ATTESTATION.json"
        write_reflection_sidecar(
            ctx["iteration_dir"],
            reflection,
            attestation_path,
            observed_metrics_snapshot,
            reflection_available,
        )
        refl_rel = (ctx["iteration_dir"] / "ROUTE.REFLECTION.json").relative_to(
            self.work_dir
        ).as_posix()
        with self._state_lock:
            if refl_rel not in self._artifacts:
                self._artifacts.append(refl_rel)
        self._event(
            "reflection_completed",
            iteration_id=ctx["iteration_id"],
            route_id=track.route_id,
            reflection_available=reflection_available,
        )
        return {
            "reflection": reflection,
            "reflection_available": reflection_available,
            "epsilon": epsilon,
        }

    def _resolve_track_verdict(
        self,
        ctx: Dict[str, Any],
        refl_info: Dict[str, Any],
        budget_ok: bool,
    ) -> Dict[str, Any]:
        """Main-thread Phase-3: conservative exploration policy.

        All ledger mutations (sidecar patch and route status) happen here on the
        main thread so the concurrent reflection phase cannot race them.
        Red line 7: "eliminated_physics" is produced ONLY from an
        authoritative VeriTMMResult.outcome == "physics_rejected"; engine
        errors and compilation failures remain retryable until a hard resource
        boundary is reached -- never to elimination.
        """
        track: RouteTrack = ctx["track"]
        observation: ResearchIterationObservation = ctx["observation"]
        reflection = refl_info["reflection"]
        reflection_available = refl_info["reflection_available"]
        epsilon = refl_info["epsilon"]

        status: str | None = None
        termination_reason = ""
        halt_run = False
        outcome_raw = str(ctx["run_payload"].get("outcome") or "")

        compilation_failed = observation.compilation_status != "compiled"
        engine_failed = outcome_raw == "engine_error"
        budget_blocked = outcome_raw == "budget_blocked"
        if observation.run_status == "needs_higher_fidelity" or (
            "outside_tmm_domain" in observation.failure_categories
        ):
            status = TRACK_ERROR_UNRECOVERABLE
            termination_reason = "requested physics is outside the declared TMM boundary"
            halt_run = True
        elif outcome_raw == "physics_rejected":
            # The ONLY elimination path. The engine attributed the failure
            # source; a string category never eliminates by itself.
            status = TRACK_ELIMINATED_PHYSICS
            termination_reason = (
                "VeriTMMResult.outcome == physics_rejected (is_route_eliminable "
                "semantics): the scientific hypothesis was refuted by experiment"
            )

        if status is not None:
            decision_action = (
                "stop_completed"
                if track.best_candidate_ids
                else "stop_best_effort"
            )
            decision = ResearchFeedbackDecision(
                action=decision_action,
                reason=f"Route {track.route_id} left the race as {status}: {termination_reason}",
                preserve_candidate_ids=tuple(track.best_candidate_ids),
            )
            self._event(
                "track_status",
                wave=ctx.get("wave_index", 0),
                route_id=track.route_id,
                status=status,
                reason=termination_reason,
            )
            return {
                "status": status,
                "termination_reason": termination_reason,
                "decision": decision,
                "directives": (),
                "replan_mode": None,
                "halt_run": halt_run,
                "disagreement": None,
            }

        # ---- conservative exploration policy ------------------------------
        max_rounds_per_route = int(self.config.max_rounds_per_route)
        # A round is consumed when the route was allocated and observed, even
        # if compilation or physics admission produced no score.  Counting
        # only score-bearing rounds lets a failing route exceed the declared
        # six-round cap through repeated unscored retries.
        rounds_this_route = int(track.rounds_used)
        gate_max_rounds = rounds_this_route < max_rounds_per_route
        gate_budget = budget_ok
        # Physics gate at THIS layer stays open (R-04FIX D-1 ruling kept):
        # string categories cannot attribute failure sources; elimination is
        # handled above exclusively through the engine-attributed outcome.
        gate_physics = True
        stagnation_stalled, stagnation_observed_gain = evaluate_stagnation(
            track.score_history,
            patience_rounds=STAGNATION_WINDOW_ROUNDS,
            minimum_score_improvement=epsilon,
        )
        gate_stagnation = not stagnation_stalled

        # Stagnation is deliberately advisory.  It remains in the sidecar so
        # the reader can see the deterministic signal, but it is not allowed
        # to terminate a route: a flat score can still justify a new physical
        # initialization or topology.
        deterministic_continue = gate_max_rounds and gate_budget and gate_physics
        llm_continue = reflection.continue_recommended
        stop_basis = str(getattr(reflection, "stop_basis", "") or "").strip()
        stop_rationale = str(reflection.continue_rationale or "").strip()
        minimum_rounds = max(
            1, int(getattr(self.config, "minimum_rounds_before_llm_stop", 2))
        )
        explicit_no_benefit_stop = bool(
            reflection_available
            and not llm_continue
            and rounds_this_route >= minimum_rounds
            and stop_basis
            in {"physically_infeasible", "marginal_gains_too_low"}
            and stop_rationale
        )
        early_llm_stop = bool(
            reflection_available
            and not llm_continue
            and rounds_this_route < minimum_rounds
        )
        ambiguous_llm_stop = bool(
            reflection_available
            and not llm_continue
            and not explicit_no_benefit_stop
            and not early_llm_stop
        )

        disagreement = {
            "present": False,
            # Degraded reflection: the LLM vote is ABSENT (None), never a
            # silent "stop".
            "llm_recommendation": llm_continue if reflection_available else None,
            "deterministic_verdict": "continue" if deterministic_continue else "stop",
            "deterministic_readings": {
                "gate_max_rounds": gate_max_rounds,
                "gate_budget": gate_budget,
                "gate_physics": gate_physics,
                "gate_stagnation": gate_stagnation,
                "observed_gain": stagnation_observed_gain,
                "epsilon": epsilon,
                "window_scores": (
                    track.score_history[-STAGNATION_WINDOW_ROUNDS:]
                    if len(track.score_history) >= STAGNATION_WINDOW_ROUNDS
                    else track.score_history
                ),
                "stagnation_is_advisory_only": True,
            },
            "llm_stop_policy": {
                "minimum_rounds_required": minimum_rounds,
                "rounds_executed": rounds_this_route,
                "stop_basis": stop_basis,
                "explicit_no_benefit_stop": explicit_no_benefit_stop,
            },
            "resolution": "",
        }

        def _blocked_by() -> str:
            if not gate_max_rounds:
                return "blocked_by_max_rounds"
            if not gate_budget:
                return "blocked_by_budget"
            if not gate_physics:
                return "blocked_by_physics_rejected"
            return "stagnation_advisory_only"

        if not reflection_available:
            disagreement["resolution"] = (
                _blocked_by() if not deterministic_continue else "reflection_unavailable_continue"
            )
            if not deterministic_continue:
                disagreement["present"] = True
        elif not llm_continue:
            disagreement["present"] = True
            if explicit_no_benefit_stop:
                disagreement["resolution"] = "llm_stop_honored_after_minimum_rounds"
            elif early_llm_stop:
                disagreement["resolution"] = "early_llm_stop_ignored"
            else:
                disagreement["resolution"] = "llm_stop_missing_explicit_no_benefit"
        elif not deterministic_continue:
            disagreement["present"] = True
            disagreement["resolution"] = _blocked_by()

        if disagreement["present"]:
            refl_path = ctx["iteration_dir"] / "ROUTE.REFLECTION.json"
            refl_data = json.loads(refl_path.read_text(encoding="utf-8"))
            refl_data["disagreement"] = disagreement
            atomic_write_json(refl_path, refl_data)

        directives: tuple[str, ...] = ()
        replan_mode: str | None = None
        if not gate_max_rounds:
            status = TRACK_STOPPED_ROUND_LIMIT
            termination_reason = (
                f"reached the per-route cap of {max_rounds_per_route} executed rounds"
            )
        elif not gate_budget:
            status = TRACK_STOPPED_BUDGET
            termination_reason = self._budget_termination_reason()
        elif explicit_no_benefit_stop:
            status = TRACK_STOPPED_LLM_ADVICE
            termination_reason = (
                "LLM explicitly assessed no further benefit after "
                f"{rounds_this_route} executed rounds "
                f"(stop_basis={stop_basis})"
            )
        else:
            # Every other non-hard-stop case continues.  In particular this
            # branch intentionally covers an early LLM stop, an ambiguous stop
            # without the typed basis, stagnation, compilation failure, an
            # engine error, and a round with no physically valid candidate.
            replan_mode = "continue"
            base_directives = [
                "Do not stop this route from the current observation; scientific exploration remains open until a hard scheduler boundary.",
                "Preserve the verified scientific principle while making a concrete, executable change for the next round.",
            ]
            if compilation_failed:
                diagnostics = " ".join(
                    [
                        str(ctx["compilation_payload"].get("rationale") or ""),
                        *[str(item) for item in observation.compilation_errors],
                    ]
                ).strip()
                base_directives.append(
                    "Repair the recorded compilation failure and return a legal bounded TMM task; do not repeat the invalid parameterization. Diagnostic: "
                    + diagnostics[:600]
                )
            if engine_failed:
                base_directives.append(
                    "Treat the VeriTMM engine error as retryable: change the executable task or optimizer load and preserve the scientific question."
                )
            if budget_blocked:
                base_directives.append(
                    "The inner TMM budget blocked this attempt; reduce per-attempt search load or alter the initialization so a physical evaluation can run."
                )
            if observation.physically_valid_candidate_count <= 0:
                base_directives.append(
                    "No physically valid candidate survived this round; try a materially different legal initialization or topology rather than declaring the route infeasible."
                )
            if not reflection_available:
                base_directives.append(
                    "Reflection was unavailable, so no stop vote is admissible; choose and record the next repair from the measured failure."
                )
            elif not llm_continue:
                if early_llm_stop:
                    base_directives.append(
                        f"The LLM stop vote is early at {rounds_this_route} executed round(s); ignore it and continue until at least {minimum_rounds} rounds have evidence."
                    )
                else:
                    base_directives.append(
                        "The LLM did not provide the required explicit no-benefit basis; its stop vote is not admissible. Continue and supply a concrete test."
                    )
            if not gate_stagnation:
                base_directives.append(
                    "The deterministic stagnation signal is advisory only; test a new physical direction instead of stopping on score flatness."
                )
            insight = str(reflection.insight_for_next or "").strip()
            if insight:
                base_directives.append(
                    "Ground the revision in this route's reflected deviation mechanism: "
                    + insight
                )
            else:
                base_directives.append(
                    "Use a concrete next-round change: alter the thickness initialization, legal bounds, topology family, or optimizer strategy, and state which one was changed."
                )
            directives = tuple(base_directives)

        if status is None:
            if not reflection_available:
                vote_description = "LLM vote absent (degraded reflection)"
            elif llm_continue:
                vote_description = "LLM concurs"
            elif early_llm_stop:
                vote_description = (
                    "LLM stop ignored before the minimum executed-round boundary"
                )
            else:
                vote_description = (
                    "LLM stop not admissible without an explicit typed no-benefit basis"
                )
            decision = ResearchFeedbackDecision(
                action="refine_route",
                reason=(
                    "Route keeps racing: hard gates open and "
                    + vote_description
                ),
                preserve_candidate_ids=tuple(observation.selected_candidate_ids)
                if observation.physically_valid_candidate_count > 0
                else tuple(),
                feedback_for_planner=directives,
            )
        else:
            decision = ResearchFeedbackDecision(
                action=(
                    "stop_completed"
                    if track.best_candidate_ids
                    else "stop_best_effort"
                ),
                reason=f"Route {track.route_id} left the race as {status}: {termination_reason}",
                preserve_candidate_ids=tuple(track.best_candidate_ids),
            )
        if status is not None:
            self._event(
                "track_status",
                wave=ctx.get("wave_index", 0),
                route_id=track.route_id,
                status=status,
                reason=termination_reason,
            )
        return {
            "status": status,
            "termination_reason": termination_reason,
            "decision": decision,
            "directives": directives,
            "replan_mode": replan_mode,
            "halt_run": False,
            "disagreement": disagreement,
        }

    def _improve_track(
        self,
        track: RouteTrack,
        problem: Dict[str, Any],
        merged_research: Dict[str, Any],
        directives: Iterable[str],
        wave_index: int,
        pre_declarations: Dict[str, Dict[str, list[str]]],
    ) -> Dict[str, Any]:
        """Phase-4 worker:承接式 improvement of ONE chain (its OWN history only).

        Red line 6: prior_iterations contains exclusively this route's own
        observations. A revision reusing the chain id keeps the SAME
        RouteTrack (only current_route is replaced); a revision whose request
        hash duplicates any earlier version of this chain is marked
        non-substantive so the scheduler can retry the current route within its
        hard round budget.
        """
        own_rows = [
            row.model_dump(mode="json")
            for row in list(self._observations)
            if str(row.route_id) == track.route_id
        ]
        try:
            if track.source == CONTROL_ROUTE_SOURCE:
                replanning, replan_pre_declarations = self._plan_control_continuation(
                    problem,
                    prior_iterations=own_rows,
                    feedback_directives=directives,
                    chain_id=track.route_id,
                )
            else:
                replanning, replan_pre_declarations = self._plan(
                    problem,
                    merged_research,
                    prior_iterations=own_rows,
                    feedback_directives=directives,
                    chain_id=track.route_id,
                )
        except Exception as exc:
            return {
                "ok": False,
                "reason": f"replanning raised {type(exc).__name__}: {exc}"[:200],
            }
        with self._state_lock:
            self._usage.extend(_usage_rows(replanning))
            pre_declarations.update(replan_pre_declarations)
        replanning_status, revised_plan, replanning_envelope = _unwrap(replanning, "plan")
        if replanning_status != "planned":
            # The planner keeps the rejected text off its dumped contract so it
            # cannot disturb the content-addressed pipeline identity, so record
            # it here instead.  A failure record that carries only the
            # validator's one-line message cannot say whether the model broke a
            # rule or a guard misread compliant prose.
            rejected_plan = str(getattr(replanning, "rejected_plan", "") or "")
            if rejected_plan:
                replanning_envelope = {
                    **replanning_envelope,
                    "rejected_plan": rejected_plan,
                }
        safe_track_id = "".join(
            ch if ch.isalnum() or ch in "-_" else "_" for ch in track.route_id
        )
        self._write(
            f"STRATEGY_REPLAN_W{wave_index}_{safe_track_id}.json",
            replanning_envelope,
        )
        if replanning_status != "planned":
            return {"ok": False, "reason": "replanning did not produce a valid plan"}
        revised: Dict[str, Any] | None = None
        for raw in revised_plan.get("routes", []) or []:
            candidate = DesignRoute.model_validate(raw).model_dump(mode="json")
            if str(candidate["route_id"]) == track.route_id:
                revised = candidate
                break
            # Real planner renames revisions to route_01/02/03 and records
            # the original chain id in parent_route_id.  Accept the match and
            # rewrite route_id back to the stable chain id so all downstream
            # ledgers (pre_declarations, observations, score_history) remain
            # keyed consistently by track.route_id.
            if str(candidate.get("parent_route_id") or "") == track.route_id:
                renamed_id = str(candidate["route_id"])
                candidate["route_id"] = track.route_id
                # Re-key pre_declarations emitted under the renamed id so
                # _execute_track_round and _reflect_track find them on the
                # next round.  Only copy when the stable id has no entry yet
                # (the original declarations remain valid if the planner
                # emitted nothing new for this chain).
                with self._state_lock:
                    if (
                        renamed_id in pre_declarations
                        and track.route_id not in pre_declarations
                    ):
                        pre_declarations[track.route_id] = pre_declarations[renamed_id]
                revised = candidate
                break
        if revised is None:
            # Fix B: the LLM planner generated valid routes but none declared
            # parent_route_id == track.route_id (e.g. exp_* chains whose replan
            # returns route_01/02 with parent_route_id: null).  Bind the
            # highest-priority route to this chain as a last resort so the round
            # can proceed rather than dying with error_unrecoverable.
            candidates_fb: list[Dict[str, Any]] = []
            for raw in revised_plan.get("routes", []) or []:
                try:
                    candidates_fb.append(
                        DesignRoute.model_validate(raw).model_dump(mode="json")
                    )
                except Exception:
                    continue
            if candidates_fb:
                candidates_fb.sort(key=lambda c: int(c.get("priority") or 0))
                best = candidates_fb[0]
                renamed_id = str(best["route_id"])
                best["route_id"] = track.route_id
                best["parent_route_id"] = track.route_id
                with self._state_lock:
                    if (
                        renamed_id in pre_declarations
                        and track.route_id not in pre_declarations
                    ):
                        pre_declarations[track.route_id] = pre_declarations[renamed_id]
                revised = best
        if revised is None:
            return {
                "ok": False,
                "reason": (
                    "planner returned no continuation for route "
                    + track.route_id
                ),
            }
        digest = _route_hash(revised)
        if digest in track.version_hashes:
            return {
                "ok": False,
                "reason": (
                    "revision is substantively duplicated (route_hash matches an "
                    "earlier version of this chain); retry the current route "
                    "within its hard round budget"
                ),
                "duplicate": True,
                "retry_existing": True,
            }
        return {"ok": True, "revised": revised, "digest": digest}

    def _write_tournament_state(
        self,
        ordered_tracks: list[RouteTrack],
        wave_index: int,
        iteration_count: int,
    ) -> None:
        """Overwrite TOURNAMENT_STATE.json once per wave (R-07's input)."""
        elapsed = time.perf_counter() - self._started
        payload = {
            "schema_version": "tournament-state.v1",
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "wave": wave_index,
            "budget_snapshot": {
                "iterations_used": iteration_count,
                # The ceiling in force, which is the configured value unless a
                # per-route quota raised it; the configured value is kept
                # alongside so a reader can see which regime the run was under.
                "maximum_iterations": self._iteration_ceiling,
                "configured_maximum_iterations": int(self.config.maximum_iterations),
                "rounds_per_route": int(self.config.max_rounds_per_route),
                "shared_iteration_pool": not self.config.per_route_round_quota_enabled,
                "wall_seconds_elapsed": round(elapsed, 3),
            },
            "tracks": [
                {
                    "route_id": t.route_id,
                    "source": t.source,
                    "status": t.status,
                    "termination_reason": t.termination_reason,
                    "rounds_used": t.rounds_used,
                    "score_history": list(t.score_history),
                    "best_candidate_ids": list(t.best_candidate_ids),
                    "current_route": dict(t.current_route),
                }
                for t in sorted(ordered_tracks, key=lambda item: item.route_id)
            ],
        }
        self._write("TOURNAMENT_STATE.json", payload)

    @staticmethod
    def _load_json_path(path: Path) -> Any:
        """Load one checkpoint artifact with a useful error at the boundary."""

        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(
                f"cannot load checkpoint artifact {path.name}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    def resume_from_checkpoint(
        self,
        checkpoint_dir: str | Path,
        *,
        question: str | None = None,
        route_ids: Iterable[str] = (),
    ) -> TMMResearchHarnessResult:
        """Resume a saved tournament in a new output directory.

        The checkpoint is copied, never edited in place.  The copied
        observations, route ledgers, declarations and frozen scoring standard
        become the prefix of the new run; only reactivated route lineages
        consume new rounds.  By default this reactivates literature-planned
        routes that stopped before their configured cap.  ``route_ids`` can be
        supplied for an explicit subset (for example, a route that failed
        compilation).
        """

        checkpoint = Path(checkpoint_dir).resolve()
        if not checkpoint.is_dir():
            raise FileNotFoundError(f"resume checkpoint is not a directory: {checkpoint}")
        if checkpoint == self.work_dir:
            raise ValueError(
                "resume requires a separate output directory so the checkpoint "
                "remains immutable"
            )
        if any(self.work_dir.iterdir()):
            raise FileExistsError(
                "resume output directory must be new or empty; refusing to overwrite it"
            )
        shutil.copytree(checkpoint, self.work_dir, dirs_exist_ok=True)
        self.events_path = self.work_dir / "RESEARCH_EVENTS.jsonl"
        if self.events_path.exists():
            with self.events_path.open("r", encoding="utf-8") as handle:
                self._event_sequence = sum(1 for line in handle if line.strip())
        else:
            self._event_sequence = 0
        return self._resume_loaded_checkpoint(
            checkpoint,
            question=question,
            route_ids=route_ids,
        )

    def _resume_loaded_checkpoint(
        self,
        checkpoint: Path,
        *,
        question: str | None,
        route_ids: Iterable[str],
    ) -> TMMResearchHarnessResult:
        required = (
            "REQUEST.json",
            "PROBLEM_ANALYSIS.json",
            "METHOD_RESEARCH.json",
            "STRATEGY_PLAN.json",
            "SCORING_STANDARD.json",
            "TOURNAMENT_STATE.json",
            "ITERATION_HISTORY.json",
            "FEEDBACK_HISTORY.json",
        )
        missing = [name for name in required if not (self.work_dir / name).exists()]
        if missing:
            raise ValueError(
                "checkpoint is missing required artifacts: " + ", ".join(missing)
            )

        request = self._load_json_path(self.work_dir / "REQUEST.json")
        checkpoint_question = str(request.get("question") or "").strip()
        resumed_question = str(question or checkpoint_question).strip()
        if not resumed_question:
            raise ValueError("checkpoint request has no question")
        if question is not None and resumed_question != checkpoint_question:
            raise ValueError(
                "resume question must exactly match the checkpoint question; "
                "a changed question would invalidate the frozen standard"
            )

        parent_result_path = self.work_dir / "RESEARCH_RESULT.json"
        parent_result = (
            self._load_json_path(parent_result_path)
            if parent_result_path.exists()
            else {}
        )
        parent_request_path = self.work_dir / "PARENT_REQUEST.json"
        if not parent_request_path.exists():
            shutil.copy2(self.work_dir / "REQUEST.json", parent_request_path)
        if parent_result_path.exists():
            parent_copy = self.work_dir / "PARENT_RESEARCH_RESULT.json"
            if not parent_copy.exists():
                shutil.copy2(parent_result_path, parent_copy)

        self._started = time.perf_counter()
        self._resume_parent_run_id = str(
            request.get("run_id") or parent_result.get("run_id") or checkpoint.name
        )
        self._resume_parent_telemetry = dict(parent_result.get("telemetry") or {})
        self._artifacts = [
            str(value) for value in (parent_result.get("artifacts") or [])
        ]
        self._usage = []
        self._service_telemetry = []
        self._observations = []
        self._write(
            "REQUEST.json",
            {
                "run_id": self.run_id,
                "question": resumed_question,
                "resumed_from_run_id": self._resume_parent_run_id,
            },
        )

        problem_envelope = self._load_json_path(self.work_dir / "PROBLEM_ANALYSIS.json")
        problem = dict(problem_envelope.get("analysis") or problem_envelope)
        method_envelope = self._load_json_path(self.work_dir / "METHOD_RESEARCH.json")
        merged_research = dict(method_envelope.get("report") or method_envelope)
        # The parent telemetry below already contains the historical method
        # research service totals.  This continuation does not re-run S2, so
        # do not count that service twice.

        strategy_envelope = self._load_json_path(self.work_dir / "STRATEGY_PLAN.json")
        plan = dict(strategy_envelope.get("plan") or {})
        standard_envelope = self._load_json_path(self.work_dir / "SCORING_STANDARD.json")
        standard_payload = standard_envelope.get("standard")
        if not isinstance(standard_payload, Mapping):
            raise ValueError("checkpoint has no valid frozen scoring standard")
        self.scoring_standard = ScoringStandard.model_validate(standard_payload)

        raw_observations = self._load_json_path(self.work_dir / "ITERATION_HISTORY.json")
        observations = [
            ResearchIterationObservation.model_validate(item)
            for item in (raw_observations if isinstance(raw_observations, list) else [])
        ]
        if not observations:
            raise ValueError("checkpoint has no iteration history to resume")
        raw_feedback = self._load_json_path(self.work_dir / "FEEDBACK_HISTORY.json")
        feedback_history = [
            ResearchFeedbackDecision.model_validate(item)
            for item in (raw_feedback if isinstance(raw_feedback, list) else [])
        ]

        # The latest route attestation is the authoritative pre-execution
        # declaration for each chain.  Loading these rather than regenerating
        # them keeps the resumed prompt/stop boundary tied to the historical
        # route contract.
        pre_declarations: Dict[str, Dict[str, list[str]]] = {}
        iteration_root = self.work_dir / "iterations"
        for attestation in sorted(iteration_root.glob("iteration_*/ROUTE.ATTESTATION.json")):
            payload = self._load_json_path(attestation)
            route_id = str(payload.get("route_id") or "").strip()
            if route_id:
                pre_declarations[route_id] = {
                    "expected_observations": list(payload.get("expected_observations") or []),
                    "stop_conditions": list(payload.get("stop_conditions") or []),
                }
        for artifact_name in ("ROUTE_PLANNING.json", "CONTROL_ROUTE_PLANNING.json"):
            artifact = self.work_dir / artifact_name
            if not artifact.exists():
                continue
            payload = self._load_json_path(artifact)
            for route_id, declarations in dict(payload.get("pre_declarations") or {}).items():
                pre_declarations.setdefault(
                    str(route_id),
                    {
                        "expected_observations": list(
                            declarations.get("expected_observations") or []
                        ),
                        "stop_conditions": list(declarations.get("stop_conditions") or []),
                    },
                )

        state = self._load_json_path(self.work_dir / "TOURNAMENT_STATE.json")
        raw_tracks = list(state.get("tracks") or [])
        route_sources = dict(strategy_envelope.get("route_sources") or {})
        tracks: Dict[str, RouteTrack] = {}
        ordered_track_ids: list[str] = []
        for raw_track in raw_tracks:
            route_id = str(raw_track.get("route_id") or "").strip()
            current_route = dict(raw_track.get("current_route") or {})
            if not route_id or not current_route:
                continue
            version_hashes: set[str] = set()
            lineage_round = 0
            lineage_parent_sha: str | None = None
            for iteration_dir in sorted(iteration_root.glob("iteration_*")):
                route_path = iteration_dir / "ROUTE.json"
                if route_path.exists():
                    try:
                        route_payload = self._load_json_path(route_path)
                    except ValueError:
                        route_payload = {}
                    if str(route_payload.get("route_id") or "") == route_id:
                        version_hashes.add(_route_hash(route_payload))
                compiled_path = iteration_dir / "COMPILED_TASK.json"
                if compiled_path.exists():
                    try:
                        observation = next(
                            row
                            for row in observations
                            if row.iteration_id == iteration_dir.name
                            and row.route_id == route_id
                        )
                    except StopIteration:
                        observation = None
                    if observation is not None and observation.compilation_status == "compiled":
                        lineage_round += 1
                        lineage_parent_sha = hashlib.sha256(
                            compiled_path.read_bytes()
                        ).hexdigest()
            if not version_hashes:
                version_hashes.add(_route_hash(current_route))
            track = RouteTrack(
                route_id=route_id,
                source=str(
                    raw_track.get("source")
                    or route_sources.get(route_id)
                    or "planned"
                ),
                rounds_used=int(raw_track.get("rounds_used") or 0),
                score_history=[
                    float(value) for value in (raw_track.get("score_history") or [])
                ],
                best_candidate_ids=[
                    str(value) for value in (raw_track.get("best_candidate_ids") or [])
                ],
                status=str(raw_track.get("status") or TRACK_RACING),
                termination_reason=str(raw_track.get("termination_reason") or ""),
                current_route=current_route,
                lineage_round=lineage_round,
                lineage_parent_sha=lineage_parent_sha,
                version_hashes=version_hashes,
            )
            tracks[route_id] = track
            ordered_track_ids.append(route_id)
        if not tracks:
            raise ValueError("checkpoint has no resumable tournament tracks")

        requested_ids = {str(value) for value in route_ids if str(value).strip()}
        if requested_ids:
            unknown = sorted(requested_ids - set(tracks))
            if unknown:
                raise ValueError("requested resume route(s) not in checkpoint: " + ", ".join(unknown))
            selected_ids = requested_ids
        else:
            selected_ids = {
                track.route_id
                for track in tracks.values()
                if track.source == LITERATURE_ROUTE_SOURCE
                and track.status not in {TRACK_ELIMINATED_PHYSICS}
                and track.rounds_used < int(self.config.max_rounds_per_route)
            }
        if not selected_ids:
            raise ValueError(
                "checkpoint has no route eligible for continuation under the configured "
                "max_rounds_per_route"
            )
        for route_id in sorted(selected_ids):
            track = tracks[route_id]
            if track.rounds_used >= int(self.config.max_rounds_per_route):
                continue
            track.status = TRACK_RACING
            track.termination_reason = ""
            self._event(
                "route_reactivated_from_checkpoint",
                route_id=route_id,
                previous_rounds=track.rounds_used,
                previous_status=next(
                    (
                        item.get("status")
                        for item in raw_tracks
                        if str(item.get("route_id") or "") == route_id
                    ),
                    "",
                ),
            )

        plan["resumed_from_run_id"] = self._resume_parent_run_id
        plan["resume_reactivated_routes"] = sorted(selected_ids)
        self._write(
            "RESUME_METADATA.json",
            {
                "schema_version": "tmm-research-resume.v1",
                "parent_run_id": self._resume_parent_run_id,
                "parent_checkpoint": str(checkpoint),
                "prior_iterations": len(observations),
                "reactivated_routes": sorted(selected_ids),
                "policy": "reactivate literature routes below cap; ignore early LLM stop votes",
            },
        )
        self.tournament_tracks = tracks
        self._observations = observations
        self._allocate_round_quota(len(ordered_track_ids))
        self._event(
            "resume_loaded",
            parent_run_id=self._resume_parent_run_id,
            prior_iterations=len(observations),
            reactivated_routes=sorted(selected_ids),
            iteration_ceiling=self._iteration_ceiling,
        )
        return self._resume_tournament(
            resumed_question,
            problem,
            merged_research,
            plan,
            pre_declarations,
            tracks,
            ordered_track_ids,
            observations,
            feedback_history,
            start_wave=int(state.get("wave") or 0),
        )

    def _resume_tournament(
        self,
        question: str,
        problem: Dict[str, Any],
        merged_research: Dict[str, Any],
        plan: Dict[str, Any],
        pre_declarations: Dict[str, Dict[str, list[str]]],
        tracks: Dict[str, RouteTrack],
        ordered_track_ids: list[str],
        observations: list[ResearchIterationObservation],
        feedback_history: list[ResearchFeedbackDecision],
        *,
        start_wave: int,
    ) -> TMMResearchHarnessResult:
        """Run the same phase order for a loaded checkpoint.

        Continuation is intentionally serial at the wave level.  This keeps
        the already-written parent iterations untouched and makes each retry's
        route/observation/feedback ordering obvious in the child event log.
        The underlying phases are unchanged: compile, execute, observe,
        reflect, decide, then replan.
        """

        self.tournament_tracks = tracks
        self._observations = observations
        final_decision = (
            feedback_history[-1]
            if feedback_history
            else ResearchFeedbackDecision(
                action="stop_best_effort", reason="No continuation round executed."
            )
        )
        wave_index = int(start_wave)

        def racing() -> list[RouteTrack]:
            return [
                tracks[route_id]
                for route_id in ordered_track_ids
                if tracks[route_id].status == TRACK_RACING
            ]

        while racing():
            if not self._budget_remaining(len(observations)):
                reason = self._budget_termination_reason()
                for track in racing():
                    track.status = TRACK_STOPPED_BUDGET
                    track.termination_reason = (
                        "resume continuation stopped before the next round: " + reason
                    )
                    self._event(
                        "track_status",
                        wave=wave_index + 1,
                        route_id=track.route_id,
                        status=track.status,
                        reason=track.termination_reason,
                    )
                break
            wave_index += 1
            self._event(
                "wave_started",
                wave=wave_index,
                racing=[track.route_id for track in racing()],
                resumed=True,
            )
            for track in list(racing()):
                if not self._budget_remaining(len(observations)):
                    break
                ctx = self._execute_track_round(
                    track,
                    wave_index,
                    pre_declarations,
                    observations,
                )
                ctx["wave_index"] = wave_index
                try:
                    refl_info = self._reflect_track(ctx, pre_declarations)
                except Exception as exc:
                    refl_info = {
                        "reflection": RouteReflection.degraded(
                            f"reflection worker failed: {type(exc).__name__}: {exc}"
                        ),
                        "reflection_available": False,
                        "epsilon": DEFAULT_MINIMUM_SCORE_IMPROVEMENT,
                    }
                verdict = self._resolve_track_verdict(
                    ctx,
                    refl_info,
                    budget_ok=self._budget_remaining(len(observations)),
                )
                feedback_history.append(verdict["decision"])
                atomic_write_json(
                    ctx["iteration_dir"] / "FEEDBACK_DECISION.json",
                    verdict["decision"].model_dump(mode="json"),
                )
                self._event(
                    "feedback_decided",
                    iteration_id=ctx["iteration_id"],
                    route_id=track.route_id,
                    action=verdict["decision"].action,
                    status=verdict["status"],
                    resumed=True,
                )
                final_decision = verdict["decision"]
                if verdict["status"] is not None:
                    track.status = verdict["status"]
                    track.termination_reason = verdict["termination_reason"]
                    continue
                if verdict["replan_mode"] is None:
                    continue
                outcome = self._improve_track(
                    track,
                    problem,
                    merged_research,
                    verdict["directives"],
                    wave_index,
                    pre_declarations,
                )
                if outcome.get("ok"):
                    track.current_route = outcome["revised"]
                    track.version_hashes.add(outcome["digest"])
                    self._event(
                        "route_revised",
                        wave=wave_index,
                        route_id=track.route_id,
                        revision_reason=str(
                            outcome["revised"].get("revision_reason") or ""
                        )[:200],
                        resumed=True,
                    )
                    continue
                reason = str(outcome.get("reason") or "replanning failed")
                can_retry = (
                    int(track.rounds_used) < int(self.config.max_rounds_per_route)
                    and self._budget_remaining(len(observations))
                )
                if can_retry:
                    retry_decision = ResearchFeedbackDecision(
                        action="refine_route",
                        reason=(
                            f"Route {track.route_id} replan did not yield a new executable "
                            f"version; retry the current route within the hard budget: {reason}"
                        ),
                        preserve_candidate_ids=tuple(track.best_candidate_ids),
                        feedback_for_planner=(
                            "Do not stop after the replan failure. Retry the current route or return a materially different legal executable version.",
                            reason,
                        ),
                    )
                    feedback_history.append(retry_decision)
                    final_decision = retry_decision
                    self._event(
                        "route_retry_scheduled",
                        wave=wave_index,
                        route_id=track.route_id,
                        reason=reason,
                        duplicate=bool(outcome.get("duplicate")),
                        resumed=True,
                    )
                else:
                    track.status = (
                        TRACK_STOPPED_BUDGET
                        if not self._budget_remaining(len(observations))
                        else TRACK_STOPPED_ROUND_LIMIT
                    )
                    track.termination_reason = (
                        self._budget_termination_reason()
                        if track.status == TRACK_STOPPED_BUDGET
                        else (
                            f"replan failure reached the per-route cap of "
                            f"{self.config.max_rounds_per_route} executed rounds: {reason}"
                        )
                    )
                    final_decision = ResearchFeedbackDecision(
                        action=(
                            "stop_completed"
                            if track.best_candidate_ids
                            else "stop_best_effort"
                        ),
                        reason=(
                            f"Route {track.route_id} left the race as {track.status}: "
                            f"{track.termination_reason}"
                        ),
                        preserve_candidate_ids=tuple(track.best_candidate_ids),
                    )
                    feedback_history.append(final_decision)
                    self._event(
                        "track_status",
                        wave=wave_index,
                        route_id=track.route_id,
                        status=track.status,
                        reason=track.termination_reason,
                    )
            self._write_tournament_state(
                [tracks[route_id] for route_id in ordered_track_ids],
                wave_index,
                len(observations),
            )

        return self._finish_tournament(
            question=question,
            problem=problem,
            merged_research=merged_research,
            plan=plan,
            tracks=tracks,
            ordered_track_ids=ordered_track_ids,
            observations=observations,
            feedback_history=feedback_history,
            final_decision=final_decision,
        )

    def _finish_tournament(
        self,
        *,
        question: str,
        problem: Dict[str, Any],
        merged_research: Dict[str, Any],
        plan: Dict[str, Any],
        tracks: Dict[str, RouteTrack],
        ordered_track_ids: list[str],
        observations: list[ResearchIterationObservation],
        feedback_history: list[ResearchFeedbackDecision],
        final_decision: ResearchFeedbackDecision,
    ) -> TMMResearchHarnessResult:
        """Write the common final ledgers for fresh and resumed races."""

        self._write(
            "ROUTE_TERMINATION_AUDIT.json",
            self._audit_route_termination(
                [tracks[route_id] for route_id in ordered_track_ids]
            ),
        )
        candidates_by_route: Dict[str, List[Dict[str, Any]]] = {}
        for row in observations:
            bucket = candidates_by_route.setdefault(str(row.route_id), [])
            for item in row.candidate_summaries:
                entry = dict(item)
                entry["physically_admissible"] = True
                entry["iteration_id"] = str(row.iteration_id)
                bucket.append(entry)
        scoring_ranking: dict[str, Any] | None = None
        if self.scoring_standard is not None:
            scoring_ranking = self._rank_by_scoring_standard(candidates_by_route)
        tournament_summary = summarize_tournament(
            [tracks[route_id] for route_id in ordered_track_ids],
            observations,
            candidates_by_route,
            scoring_ranking=scoring_ranking,
        )
        self._write("TOURNAMENT_SUMMARY.json", tournament_summary)
        self._event(
            "tournament_summarized",
            frontier=len(tournament_summary["pareto_frontier"]),
            resumed=bool(self._resume_parent_run_id),
        )
        if scoring_ranking is not None:
            self._write("SCORING_RANKING.json", scoring_ranking)
        if not self._budget_remaining(len(observations)) and final_decision.action not in {
            "stop_completed",
            "needs_higher_fidelity",
        }:
            final_decision = ResearchFeedbackDecision(
                action="stop_best_effort",
                reason="The bounded research budget ended; verified candidates are preserved.",
                preserve_candidate_ids=tuple(
                    dict.fromkeys(
                        candidate
                        for row in observations
                        for candidate in row.selected_candidate_ids
                    )
                ),
            )
        final_status = (
            "needs_higher_fidelity"
            if final_decision.action == "needs_higher_fidelity"
            else "completed"
            if any(row.physically_valid_candidate_count for row in observations)
            else "completed_best_effort_no_verified_candidate"
        )
        combined_plan = {
            **plan,
            "routes": [
                tracks[route_id].current_route
                for route_id in sorted(ordered_track_ids)
            ],
            "route_count_after_feedback": len(ordered_track_ids),
            "route_sources": {
                route_id: tracks[route_id].source
                for route_id in sorted(ordered_track_ids)
            },
            "planning_source_comparison": tournament_summary.get(
                "planning_source_comparison"
            ),
        }
        reporter_extras: dict[str, Any] = {}
        if self.scoring_standard is not None:
            reporter_extras["scoring_standard"] = self.scoring_standard
        answer = self.reporter.build(
            problem_analysis=problem,
            method_research=merged_research,
            strategy_plan=combined_plan,
            iterations=observations,
            stop_decision=final_decision,
            status=final_status,
            **reporter_extras,
        )
        self._write("FINAL_ANSWER.json", answer)
        (self.work_dir / "FINAL_ANSWER.md").write_text(
            answer.markdown, encoding="utf-8"
        )
        self._artifacts.append("FINAL_ANSWER.md")
        self._write(
            "ITERATION_HISTORY.json",
            [row.model_dump(mode="json") for row in observations],
        )
        self._write(
            "FEEDBACK_HISTORY.json",
            [row.model_dump(mode="json") for row in feedback_history],
        )
        result = TMMResearchHarnessResult(
            run_id=self.run_id,
            status=final_status,
            stage="finished",
            question=question,
            problem_analysis=problem,
            method_research=merged_research,
            strategy_plan=combined_plan,
            iterations=tuple(observations),
            feedback_history=tuple(feedback_history),
            final_answer=answer,
            telemetry=self._telemetry(),
            artifacts=tuple(dict.fromkeys(self._artifacts)),
        )
        self._write("RESEARCH_RESULT.json", result)
        self._event("research_finished", status=final_status, resumed=bool(self._resume_parent_run_id))
        return result

    def run(self, question: str) -> TMMResearchHarnessResult:
        question = str(question or "").strip()
        if not question:
            raise ValueError("question must not be empty")
        if any(self.work_dir.iterdir()):
            raise FileExistsError("research harness work_dir must be new or empty")
        self._started = time.perf_counter()
        self._write("REQUEST.json", {"run_id": self.run_id, "question": question})
        self._event("request_received")

        analysis_result = self.problem_analyzer.analyze(
            question, force_mock=self.config.qwen_force_mock
        )
        self._usage.extend(_usage_rows(analysis_result))
        analysis_status, problem, analysis_envelope = _unwrap(
            analysis_result, "analysis"
        )
        self._write("PROBLEM_ANALYSIS.json", analysis_envelope)
        self._event("problem_analyzed", status=analysis_status)
        compatibility = str(problem.get("compatibility") or "ambiguous")
        if analysis_status not in {"completed", "analyzed", "success"} or compatibility == "incompatible":
            return self._early_finish(
                question,
                problem,
                status="needs_higher_fidelity" if compatibility == "incompatible" else "analysis_failed",
                stage="problem_analysis",
                reason=str(problem.get("compatibility_reason") or analysis_status),
            )

        # Before any route exists, so no result can influence what counts as a
        # good result. Every route is then measured on these numbers, which is
        # what makes the routes' scores mean the same thing.
        self.scoring_standard = self._establish_scoring_standard(question, problem)

        # Also before any route exists, and from the request rather than from a
        # configured width: this decides WHICH axes the study can reach at all.
        self.route_plan_result = self._plan_routes_from_literature(question, problem)
        # The control arm is planned in the same pre-execution window, but it
        # deliberately does not wait for (or receive) method research.  It is
        # therefore a genuine memory-only baseline rather than a literature
        # plan with its citations removed after the fact.
        self.control_route_plan_result = self._plan_control_route(question, problem)

        method_reports: list[dict[str, Any]] = []
        research_result = self._research(problem)
        self._usage.extend(_usage_rows(research_result))
        _, method_report, method_envelope = _unwrap(research_result, "report")
        method_reports.append(method_report)
        self._service_telemetry.append(dict(method_report.get("telemetry") or {}))
        merged_research = _merge_method_reports(method_reports)
        self._write("METHOD_RESEARCH.json", method_envelope)
        self._event(
            "method_research_completed",
            status=merged_research.get("status"),
            evidence_count=len(merged_research.get("evidence", [])),
        )

        # R-05/R-06: provenance markers for the tournament when seeding is on;
        # legacy planner paths carry no source markers ("planned").
        seeded_sources: Dict[str, str] | None = None
        literature_routes = self.route_plan_result is not None
        if literature_routes:
            # The portfolio was already planned, before method research ran, from
            # the request and the papers retrieved for it. Method research still
            # runs and still reaches the report and the per-route reflection; it
            # simply no longer decides how many axes the study has.
            route_plan = self.route_plan_result
            plan = dict(route_plan.plan or {})
            pre_declarations = {
                route_id: {key: list(value) for key, value in declarations.items()}
                for route_id, declarations in route_plan.pre_declarations.items()
            }
            planning_status = "planned"
            planning_envelope = {
                "status": "planned",
                "plan": plan,
                "attempts": route_plan.attempts,
                "normalization_warnings": list(route_plan.warnings),
                "usage": [dict(row) for row in route_plan.planning_usage],
                "model_name": route_plan.model_name,
                "planning_mechanism": "literature_route_planning",
            }
            # Provenance marker for the tournament, alongside seeding's
            # evidence_derived/experience_derived: a reader of TOURNAMENT_SUMMARY
            # can then tell which stage proposed the axis they are looking at.
            seeded_sources = {
                str(route.get("route_id")): "literature_planned"
                for route in plan.get("routes") or ()
            }
        elif self.config.portfolio_seeding_enabled:
            # R-05: dual-source seeding replaces the single-shot initial plan.
            # The strategy planner remains authoritative for feedback-driven
            # REPLANNING inside the tournament (the in-loop improver since
            # R-06); seeding only owns the initial portfolio.
            # R-09 fix: the seeder needs the harness client PROTOCOL
            # (.call(messages, max_tokens=, force_mock=)); get_qwen_client
            # returns the raw OpenAI SDK object, which has no .call. Route
            # through the same plus-tier adapter the analyzer uses.
            from .problem_analyzer import ArticlePlusQwenClient

            seeder = self.portfolio_seeder or QwenTMPPortfolioSeeder(
                ArticlePlusQwenClient(role="plus")
            )
            seeded = seeder.seed(
                problem_analysis=problem,
                method_research=merged_research,
                max_routes=self.config.max_routes,
                force_mock=self.config.qwen_force_mock,
            )
            self._write("PORTFOLIO_SEEDING.json", seeded.sidecar)
            self._usage.extend(seeded.usage_rows)
            self._event(
                "portfolio_seeded",
                evidence_derived=len(seeded.sidecar["evidence_derived"]),
                experience_derived=len(seeded.sidecar["experience_derived"]),
                selected=len(seeded.routes),
                insufficient=seeded.insufficient,
            )
            if seeded.insufficient:
                # R-05: never silently continue with a degenerate single-route
                # tournament. Regeneration vs reporting is the caller's call;
                # here the caller reports and stops.
                return self._early_finish(
                    question,
                    problem,
                    method_research=merged_research,
                    status="planning_failed",
                    stage="portfolio_seeding",
                    reason=(
                        "Portfolio seeding produced fewer than 2 executable "
                        "routes after deduplication; see PORTFOLIO_SEEDING.json."
                    ),
                )
            plan = seeded.plan
            pre_declarations = seeded.pre_declarations
            seeded_sources = dict(seeded.sources)
            planning_status = "planned"
            planning_envelope = {
                "status": "planned",
                "plan": plan,
                "attempts": 1,
                "normalization_warnings": [],
                "usage": seeded.usage_rows,
                "model_name": PORTFOLIO_SEEDING_MODEL,
            }
        else:
            planning_result, pre_declarations = self._plan(problem, merged_research)
            self._usage.extend(_usage_rows(planning_result))
            planning_status, plan, planning_envelope = _unwrap(planning_result, "plan")
        if planning_status != "planned" or not plan.get("routes"):
            return self._early_finish(
                question,
                problem,
                method_research=merged_research,
                strategy_plan=plan,
                status="planning_failed",
                stage="strategy_planning",
                reason="No valid TMM research route was produced.",
            )

        # Preserve the normal planner's portfolio separately from the control
        # arm.  The normal width is selected exactly as before; the one control
        # route is appended afterwards and therefore cannot consume a normal
        # route slot or silently reduce the literature-derived axes.
        normal_routes = list(plan.get("routes") or ())
        route_sources: Dict[str, str] = {
            str(route.get("route_id")): (seeded_sources or {}).get(
                str(route.get("route_id")),
                LITERATURE_ROUTE_SOURCE if literature_routes else "planned",
            )
            for route in normal_routes
            if isinstance(route, Mapping)
        }
        control_route: Dict[str, Any] | None = None
        if self.control_route_plan_result is not None:
            control_plan = self.control_route_plan_result.plan or {}
            raw_control = list(control_plan.get("routes") or ())
            if len(raw_control) == 1:
                try:
                    control_route = DesignRoute.model_validate(
                        raw_control[0]
                    ).model_dump(mode="json")
                except Exception as exc:
                    self._event(
                        "control_route_rejected_before_execution",
                        reason=f"{type(exc).__name__}: {exc}",
                    )
                else:
                    original_control_id = str(
                        control_route.get("route_id") or CONTROL_ROUTE_ID
                    )
                    control_route["route_id"] = CONTROL_ROUTE_ID
                    control_route["priority"] = 1
                    control_route["parent_route_id"] = None
                    control_route["evidence_ids"] = []
                    route_sources[CONTROL_ROUTE_ID] = CONTROL_ROUTE_SOURCE
                    declarations = self.control_route_plan_result.pre_declarations
                    pre_declarations[CONTROL_ROUTE_ID] = {
                        key: list(value)
                        for key, value in (
                            declarations.get(original_control_id)
                            or declarations.get(CONTROL_ROUTE_ID)
                            or {
                                "expected_observations": [],
                                "stop_conditions": [],
                            }
                        ).items()
                    }

        plan = dict(plan)
        plan["route_sources"] = dict(route_sources)
        plan["normal_route_count"] = len(normal_routes)
        plan["control_route_count"] = int(control_route is not None)
        if control_route is not None:
            plan["routes"] = [*normal_routes, control_route]
            plan["control_route"] = {
                "route_id": CONTROL_ROUTE_ID,
                "source": CONTROL_ROUTE_SOURCE,
                "knowledge_source": "model_prior_knowledge_only",
                "s2_literature_included": False,
                "method_research_supplied": False,
            }
        planning_envelope = dict(planning_envelope)
        if self.control_route_plan_envelope is not None:
            # Keep an unavailable/invalid control attempt visible as well; a
            # missing control artifact would make a failed comparison look as
            # if the arm had never been configured.
            planning_envelope["control_route_planning"] = (
                self.control_route_plan_envelope
            )
        planning_envelope["plan"] = plan
        planning_envelope["route_sources"] = dict(route_sources)
        planning_envelope["normal_route_count"] = len(normal_routes)
        planning_envelope["control_route_count"] = int(control_route is not None)
        self._write("STRATEGY_PLAN.json", planning_envelope)
        self._event(
            "strategy_planned",
            status=planning_status,
            normal_route_count=len(normal_routes),
            control_route_count=int(control_route is not None),
        )

        # ------------------------------------------------------------------
        # R-06 tournament scheduler: every portfolio member races its own
        # iteration chain. Each wave advances all racing tracks one round;
        # VeriTMM execution stays serial, LLM-side reflection/replanning run
        # in a bounded thread pool. portfolio_seeding_enabled remains OFF by
        # default per ruling -- flipping it on is a real-run-data decision,
        # not something this scheduler does implicitly.
        # ------------------------------------------------------------------
        portfolio_width = (
            # The planner already answered this question, bounded by
            # route_planning_maximum_routes, and cutting its answer to a
            # configured width here would undo exactly what that stage is for.
            len(normal_routes)
            if literature_routes
            else self.config.max_routes
            if self.config.portfolio_seeding_enabled
            else self.config.maximum_initial_routes
        )
        tracks: Dict[str, RouteTrack] = {}
        ordered_track_ids: list[str] = []
        seen_request_hashes: set[str] = set()
        observations: list[ResearchIterationObservation] = []
        feedback_history: list[ResearchFeedbackDecision] = []
        # Bookkeeping counters kept for telemetry parity with the legacy loop.
        refinement_rounds = 0
        final_decision = ResearchFeedbackDecision(
            action="stop_best_effort", reason="No experiment was executed."
        )

        selected_routes: list[Dict[str, Any]] = [
            dict(raw) for raw in normal_routes[:portfolio_width]
        ]
        if control_route is not None:
            selected_routes.append(dict(control_route))
        for raw in selected_routes:
            route = DesignRoute.model_validate(raw).model_dump(mode="json")
            digest = _route_hash(route)
            route_id = str(route["route_id"])
            source = route_sources.get(route_id, "planned")
            if digest in seen_request_hashes and source != CONTROL_ROUTE_SOURCE:
                self._event("duplicate_route_skipped", route_id=route_id)
                continue
            if digest in seen_request_hashes and source == CONTROL_ROUTE_SOURCE:
                # A duplicate control request is still retained: its provenance
                # is the experimental variable, and dropping it would make the
                # requested comparison disappear without an explicit failure.
                self._event(
                    "control_route_duplicate_retained",
                    route_id=route_id,
                    reason="retained for planning-source comparison",
                )
            seen_request_hashes.add(digest)
            tracks[route_id] = RouteTrack(
                route_id=route_id,
                source=source,
                current_route=route,
                version_hashes={digest},
            )
            ordered_track_ids.append(route_id)
        self.tournament_tracks = tracks
        self._allocate_round_quota(len(ordered_track_ids))

        def _racing() -> list[RouteTrack]:
            return [
                tracks[tid]
                for tid in ordered_track_ids
                if tracks[tid].status == TRACK_RACING
            ]

        wave_index = 0
        halt_run = False
        while True:
            racing_now = _racing()
            if not racing_now:
                break
            budget_ok = self._budget_remaining(len(observations))
            if not budget_ok:
                for track in racing_now:
                    track.status = TRACK_STOPPED_BUDGET
                    track.termination_reason = (
                        "run iteration/wall-time budget exhausted before this wave"
                    )
                    self._event(
                        "track_status",
                        wave=wave_index + 1,
                        route_id=track.route_id,
                        status=track.status,
                        reason=track.termination_reason,
                    )
                break
            wave_index += 1
            # Wave-boundary truncation admits a WAVE, but Phase 1 below runs one
            # round per racing track, so a wave of N tracks consumes N
            # iterations against a gate that only checked that at least ONE
            # remained. With maximum_iterations=6 and 3 tracks the run reaches 8
            # — the overrun is proportional to the portfolio width, i.e. it grows
            # exactly as R-05/R-06 widen the race. Admit only as many tracks as
            # the remaining iteration budget can pay for; the rest keep racing
            # and are picked up by the next wave (or marked stopped_budget by the
            # gate above once nothing is left). This preserves the approved
            # "no mid-wave interruption" ruling: an admitted round always runs to
            # completion.
            iterations_left = max(0, self._iteration_ceiling - len(observations))
            if len(racing_now) > iterations_left:
                deferred = racing_now[iterations_left:]
                racing_now = racing_now[:iterations_left]
                self._event(
                    "wave_admission_truncated",
                    wave=wave_index,
                    admitted=[t.route_id for t in racing_now],
                    deferred=[t.route_id for t in deferred],
                    iterations_left=iterations_left,
                    reason=(
                        "iteration budget cannot pay for one round on every "
                        "racing track this wave"
                    ),
                )
            self._event(
                "wave_started",
                wave=wave_index,
                racing=[t.route_id for t in racing_now],
            )

            # ---- R-08 Phase 1a (serial): allocate / attest / compile -------
            base_index = len(observations)
            pre_ctxs: list[Dict[str, Any]] = []
            for slot, track in enumerate(racing_now):
                pre_ctxs.append(
                    self._prepare_track_round(
                        track,
                        wave_index,
                        base_index + 1 + slot,
                        pre_declarations,
                    )
                )

            # ---- R-08 Phase 1b: VeriTMM in a process pool ------------------
            compiled_ctxs = [
                ctx
                for ctx in pre_ctxs
                if ctx["task"] is not None
                and ctx["compilation_status"] == "compiled"
            ]
            factory_is_default = (
                getattr(self.tmm_harness_factory, "__func__", None)
                is TMMResearchHarness._default_tmm_factory
            )
            use_pool = (
                bool(getattr(self.config, "parallel_tmm", False))
                and factory_is_default
            )
            run_outcomes: Dict[int, Dict[str, Any]] = {}
            if use_pool and len(compiled_ctxs) >= 2:
                jobs: list[tuple[str, Path]] = []
                for ctx in compiled_ctxs:
                    execution_dir = ctx["iteration_dir"] / "tmm_run"
                    job_payload = {
                        "work_dir": str(execution_dir),
                        "run_id": self.run_id + "." + ctx["iteration_id"],
                        "config": {
                            "use_qwen_policy": bool(self.config.use_qwen_policy_inside_tmm),
                            "qwen_force_mock": bool(self.config.qwen_force_mock),
                        },
                        "task": ctx["task"].model_dump(mode="json"),
                    }
                    jobs.append((
                        json.dumps(job_payload, ensure_ascii=False),
                        execution_dir,
                    ))
                batch_results = self._tmm_batch_fn(jobs)
                if len(batch_results) != len(jobs):
                    raise RuntimeError(
                        "tmm batch executor returned misaligned results"
                    )
                for ctx, outcome in zip(compiled_ctxs, batch_results):
                    result_path = None
                    candidate = (
                        ctx["iteration_dir"] / "tmm_run" / "FINAL_RESULT.json"
                    )
                    if candidate.exists():
                        result_path = candidate.relative_to(self.work_dir).as_posix()
                    if outcome.get("ok"):
                        run_outcomes[id(ctx)] = {
                            "ok": True,
                            "run_payload": dict(outcome.get("final_result") or {}),
                            "result_path": result_path,
                            "cpu_seconds": float(outcome.get("cpu_seconds") or 0.0),
                        }
                    elif outcome.get("budget_blocked"):
                        run_outcomes[id(ctx)] = {
                            "ok": True,
                            "run_payload": {},
                            "result_path": None,
                            "cpu_seconds": 0.0,
                        }
                    else:
                        error_file = (
                            ctx["iteration_dir"] / "tmm_run" / "EXECUTION_ERROR.json"
                        )
                        error_file.parent.mkdir(parents=True, exist_ok=True)
                        error_payload = {
                            "status": "failed",
                            "experiment_results": [],
                            "diagnoses": [
                                {
                                    "category": "runtime_environment",
                                    "recoverable_with_tmm": False,
                                    "allowed_actions": ["stop"],
                                    "explanation": (
                                        "The TMM execution raised a recorded exception."
                                    ),
                                    "context": {
                                        "exception_type": str(
                                            outcome.get("error_type") or "Exception"
                                        ),
                                        "message": str(
                                            outcome.get("message") or ""
                                        )[:500],
                                    },
                                }
                            ],
                        }
                        atomic_write_json(error_file, error_payload)
                        run_outcomes[id(ctx)] = {
                            "ok": False,
                            "error_payload": error_payload,
                            "result_path": error_file.relative_to(
                                self.work_dir
                            ).as_posix(),
                            "cpu_seconds": float(
                                outcome.get("cpu_seconds") or 0.0
                            ),
                        }
                self._event(
                    "tmm_batch_executed",
                    wave=wave_index,
                    tasks=len(jobs),
                    pool=True,
                )
            else:
                # Legacy shape: injected factories, single-route waves, or
                # parallel disabled -- identical to pre-R-08 semantics.
                for ctx in pre_ctxs:
                    if ctx["task"] is not None and ctx["compilation_status"] == "compiled":
                        run_outcomes[id(ctx)] = self._run_tmm_serial_fallback(ctx)
                    else:
                        run_outcomes[id(ctx)] = {}

            # ---- R-08 Phase 1c (serial): observations into ledgers ---------
            contexts: list[Dict[str, Any]] = []
            for ctx in pre_ctxs:
                observed = self._observe_track_round(
                    ctx,
                    run_outcomes.get(id(ctx), {}),
                    wave_index,
                    observations,
                )
                observed["wave_index"] = wave_index
                contexts.append(observed)
            # ---- Phase 2 (concurrent): LLM reflections ---------------------
            def _safe_reflect(c: Dict[str, Any]) -> Dict[str, Any]:
                try:
                    return self._reflect_track(c, pre_declarations)
                except Exception as exc:
                    # One broken reflection must never sink the wave: treat it
                    # as a degraded response (vote ABSENT), never a stop vote.
                    return {
                        "reflection": RouteReflection.degraded(
                            reason=(
                                "reflection worker failed: "
                                f"{type(exc).__name__}: {exc}"
                            )[:300]
                        ),
                        "reflection_available": False,
                        "epsilon": DEFAULT_MINIMUM_SCORE_IMPROVEMENT,
                    }

            workers = min(len(contexts), MAX_CONCURRENT_LLM_WORKERS)
            refl_results: Dict[str, Dict[str, Any]] = {}
            if workers > 1:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = [
                        (ctx, pool.submit(_safe_reflect, ctx)) for ctx in contexts
                    ]
                    for ctx, future in futures:
                        refl_results[ctx["track"].route_id] = future.result()
            else:
                for ctx in contexts:
                    refl_results[ctx["track"].route_id] = _safe_reflect(ctx)

            # ---- Phase 3 (serial): deterministic verdicts + ledger writes --
            improvements: list[tuple[RouteTrack, Tuple[str, ...], Dict[str, Any]]] = []
            for ctx in contexts:
                track = ctx["track"]
                verdict = self._resolve_track_verdict(
                    ctx,
                    refl_results[track.route_id],
                    budget_ok=self._budget_remaining(len(observations)),
                )
                feedback_history.append(verdict["decision"])
                atomic_write_json(
                    ctx["iteration_dir"] / "FEEDBACK_DECISION.json",
                    verdict["decision"].model_dump(mode="json"),
                )
                self._event(
                    "feedback_decided",
                    iteration_id=ctx["iteration_id"],
                    route_id=track.route_id,
                    action=verdict["decision"].action,
                    status=verdict["status"],
                )
                final_decision = verdict["decision"]
                if verdict["halt_run"]:
                    halt_run = True
                if verdict["status"] is not None:
                    track.status = verdict["status"]
                    track.termination_reason = verdict["termination_reason"]
                elif verdict["replan_mode"] is not None:
                    improvements.append((track, verdict["directives"], ctx))

            self._write_tournament_state(
                [tracks[tid] for tid in ordered_track_ids],
                wave_index,
                len(observations),
            )
            if halt_run:
                # Whoever halted the run already has its own status and reason.
                # The tracks that were merely racing alongside it have neither,
                # and a ledger that leaves them "racing" says they are still
                # going. Close them here, naming the cause they actually had.
                for track in _racing():
                    track.status = TRACK_STOPPED_RUN_HALTED
                    track.termination_reason = (
                        "the run halted before this route's next round; see the "
                        "route whose status is error_unrecoverable"
                    )
                    self._event(
                        "track_status",
                        wave=wave_index,
                        route_id=track.route_id,
                        status=track.status,
                        reason=track.termination_reason,
                    )
                self._write_tournament_state(
                    [tracks[tid] for tid in ordered_track_ids],
                    wave_index,
                    len(observations),
                )
                break
            if not improvements:
                # An empty improvements list does NOT mean the tournament is
                # over: budget rationing above may have DEFERRED racing tracks,
                # which produced no context this wave and therefore no
                # improvement entry. Breaking here froze them in "racing"
                # forever -- a status R-07 reads as "still in the race" -- with
                # an empty termination_reason. Re-enter the loop instead: the
                # wave gate at the top either admits them next wave or marks
                # them stopped_budget. When nothing is racing, that same gate
                # breaks immediately, so this cannot spin: every admitted wave
                # consumes at least one iteration.
                continue

            # ---- Phase 4 (concurrent): 承接式 replanning per chain ----------
            refinement_rounds += len(improvements)

            def _safe_improve(
                track: RouteTrack, directives: Tuple[str, ...]
            ) -> Dict[str, Any]:
                try:
                    return self._improve_track(
                        track,
                        problem,
                        merged_research,
                        directives,
                        wave_index,
                        pre_declarations,
                    )
                except Exception as exc:
                    return {
                        "ok": False,
                        "reason": (
                            f"replan worker raised {type(exc).__name__}: {exc}"
                        )[:200],
                    }

            results_map: Dict[str, Dict[str, Any]] = {}
            workers_replan = min(len(improvements), MAX_CONCURRENT_LLM_WORKERS)
            if workers_replan > 1:
                with ThreadPoolExecutor(max_workers=workers_replan) as pool:
                    futures_replan = [
                        (item[0], pool.submit(_safe_improve, item[0], item[1]))
                        for item in improvements
                    ]
                    for track, future in futures_replan:
                        results_map[track.route_id] = future.result()
            else:
                for track, directives, _ctx in improvements:
                    results_map[track.route_id] = _safe_improve(track, directives)

            any_replan_ok = False
            for track, _directives, ctx in improvements:
                outcome = results_map[track.route_id]
                if outcome.get("ok"):
                    revised = outcome["revised"]
                    track.current_route = revised
                    track.version_hashes.add(outcome["digest"])
                    any_replan_ok = True
                    self._event(
                        "route_revised",
                        wave=wave_index,
                        route_id=track.route_id,
                        revision_reason=str(revised.get("revision_reason") or "")[:200],
                    )
                    continue
                termination_reason = str(outcome.get("reason") or "replanning failed")
                # A planner/API failure or a non-substantive revision is an
                # execution/replanning failure, not evidence that the
                # scientific route has no value. Keep the same track alive and
                # retry the current executable route until the hard round or
                # wall-time boundary. This is what makes a compile/replan
                # failure recoverable from a checkpoint instead of terminal.
                can_retry = (
                    int(track.rounds_used) < int(self.config.max_rounds_per_route)
                    and self._budget_remaining(len(observations))
                )
                if can_retry:
                    track.status = TRACK_RACING
                    track.termination_reason = ""
                    retry_decision = ResearchFeedbackDecision(
                        action="refine_route",
                        reason=(
                            f"Route {track.route_id} replan did not yield a new executable "
                            f"version; retry the current route within the hard budget: "
                            f"{termination_reason}"
                        ),
                        preserve_candidate_ids=tuple(track.best_candidate_ids),
                        feedback_for_planner=(
                            "Do not stop after the replan failure. Retry the current route or return a materially different legal executable version.",
                            termination_reason,
                        ),
                    )
                    feedback_history.append(retry_decision)
                    final_decision = retry_decision
                    self._event(
                        "route_retry_scheduled",
                        wave=wave_index,
                        route_id=track.route_id,
                        reason=termination_reason,
                        duplicate=bool(outcome.get("duplicate")),
                    )
                    continue

                if not self._budget_remaining(len(observations)):
                    track.status = TRACK_STOPPED_BUDGET
                    track.termination_reason = self._budget_termination_reason()
                else:
                    track.status = TRACK_STOPPED_ROUND_LIMIT
                    track.termination_reason = (
                        f"replan failure reached the per-route cap of "
                        f"{self.config.max_rounds_per_route} executed rounds: "
                        f"{termination_reason}"
                    )
                decision = ResearchFeedbackDecision(
                    action=(
                        "stop_completed"
                        if track.best_candidate_ids
                        else "stop_best_effort"
                    ),
                    reason=(
                        f"Route {track.route_id} left the race as "
                        f"{track.status}: {track.termination_reason}"
                    ),
                    preserve_candidate_ids=tuple(track.best_candidate_ids),
                )
                feedback_history.append(decision)
                # Deliberately NO rewrite of this iteration's
                # FEEDBACK_DECISION.json: that file records what was decided
                # AT this iteration. Terminal outcomes live in
                # feedback_history / TOURNAMENT_STATE.
                final_decision = decision
                self._event(
                    "track_status",
                    wave=wave_index,
                    route_id=track.route_id,
                    status=track.status,
                    reason=track.termination_reason,
                )
            self._write_tournament_state(
                [tracks[tid] for tid in ordered_track_ids],
                wave_index,
                len(observations),
            )

        # Fail-open bookkeeping: who said why they stopped. Written before the
        # summary so the summary's own route_comparison[].termination_reason can
        # be read against the audit's count of how many were filled in.
        self._write(
            "ROUTE_TERMINATION_AUDIT.json",
            self._audit_route_termination(
                [tracks[tid] for tid in ordered_track_ids]
            ),
        )

        # ---- R-07: cross-route Pareto summary of the finished race --------
        # Candidate summaries recorded by the inner harness are already
        # admission-filtered upstream; the summary module re-checks the flag
        # so constructed/injected data cannot leak inadmissible solutions.
        # iteration_id is stamped in here: candidate_summaries carry only
        # experiment_id, and the optimizer restarts its candidate_NN numbering
        # every run, so without the round the summary cannot tell two rounds'
        # solutions apart (R-07 audit).
        candidates_by_route: Dict[str, List[Dict[str, Any]]] = {}
        for row in observations:
            bucket = candidates_by_route.setdefault(str(row.route_id), [])
            for item in row.candidate_summaries:
                entry = dict(item)
                entry["physically_admissible"] = True
                entry["iteration_id"] = str(row.iteration_id)
                bucket.append(entry)
        scoring_ranking: dict[str, Any] | None = None
        if self.scoring_standard is not None:
            scoring_ranking = self._rank_by_scoring_standard(candidates_by_route)
        tournament_summary = summarize_tournament(
            [tracks[tid] for tid in ordered_track_ids],
            observations,
            candidates_by_route,
            scoring_ranking=scoring_ranking,
        )
        self._write("TOURNAMENT_SUMMARY.json", tournament_summary)
        self._event("tournament_summarized", frontier=len(tournament_summary["pareto_frontier"]))

        if scoring_ranking is not None:
            self._write("SCORING_RANKING.json", scoring_ranking)

        if not self._budget_remaining(len(observations)) and final_decision.action not in {
            "stop_completed",
            "needs_higher_fidelity",
        }:
            final_decision = ResearchFeedbackDecision(
                action="stop_best_effort",
                reason="The bounded research budget ended; verified candidates are preserved.",
                preserve_candidate_ids=tuple(
                    dict.fromkeys(
                        candidate
                        for row in observations
                        for candidate in row.selected_candidate_ids
                    )
                ),
            )

        final_status = (
            "needs_higher_fidelity"
            if final_decision.action == "needs_higher_fidelity"
            else "completed"
            if any(row.physically_valid_candidate_count for row in observations)
            else "completed_best_effort_no_verified_candidate"
        )
        combined_plan = {
            **plan,
            "routes": [tracks[tid].current_route for tid in sorted(ordered_track_ids)],
            "route_count_after_feedback": len(ordered_track_ids),
            "route_sources": {
                route_id: tracks[route_id].source
                for route_id in sorted(ordered_track_ids)
            },
            "planning_source_comparison": tournament_summary.get(
                "planning_source_comparison"
            ),
        }
        # Only forwarded when a standard exists, so an injected reporter that
        # predates this argument keeps working on runs that do not use one.
        reporter_extras: dict[str, Any] = {}
        if self.scoring_standard is not None:
            reporter_extras["scoring_standard"] = self.scoring_standard
        answer = self.reporter.build(
            problem_analysis=problem,
            method_research=merged_research,
            strategy_plan=combined_plan,
            iterations=observations,
            stop_decision=final_decision,
            status=final_status,
            **reporter_extras,
        )
        self._write("FINAL_ANSWER.json", answer)
        markdown_path = self.work_dir / "FINAL_ANSWER.md"
        markdown_path.write_text(answer.markdown, encoding="utf-8")
        self._artifacts.append("FINAL_ANSWER.md")
        self._write("ITERATION_HISTORY.json", [row.model_dump(mode="json") for row in observations])
        self._write("FEEDBACK_HISTORY.json", [row.model_dump(mode="json") for row in feedback_history])
        result = TMMResearchHarnessResult(
            run_id=self.run_id,
            status=final_status,
            stage="finished",
            question=question,
            problem_analysis=problem,
            method_research=merged_research,
            strategy_plan=combined_plan,
            iterations=tuple(observations),
            feedback_history=tuple(feedback_history),
            final_answer=answer,
            telemetry=self._telemetry(),
            artifacts=tuple(dict.fromkeys(self._artifacts)),
        )
        self._write("RESEARCH_RESULT.json", result)
        self._event("research_finished", status=final_status)
        return result

    @staticmethod
    def _row_tokens(row: Mapping[str, Any]) -> tuple[int, int]:
        """Read one usage row's token counts across the accepted key spellings.

        OpenAI-compatible DashScope responses report prompt_tokens /
        completion_tokens; other seams in this codebase emit input_tokens /
        output_tokens or the estimated_* variants.  Reading only one spelling
        silently reported zero tokens and zero cost for real paid calls, so all
        three are accepted here.
        """
        input_tokens = int(
            row.get("input_tokens")
            or row.get("prompt_tokens")
            or row.get("estimated_input_tokens")
            or 0
        )
        output_tokens = int(
            row.get("output_tokens")
            or row.get("completion_tokens")
            or row.get("estimated_output_tokens")
            or 0
        )
        return input_tokens, output_tokens

    def _telemetry(self) -> Dict[str, Any]:
        token_pairs = [self._row_tokens(row) for row in self._usage]
        input_tokens = sum(pair[0] for pair in token_pairs)
        output_tokens = sum(pair[1] for pair in token_pairs)
        estimated_cost_cny = sum(
            estimate_call_cost_cny(
                str(row.get("model_name") or "unknown"),
                pair[0],
                pair[1],
            )
            for row, pair in zip(self._usage, token_pairs)
        )
        service_totals: dict[str, float] = {}
        for row in self._service_telemetry:
            for key, value in row.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    service_totals[key] = service_totals.get(key, 0.0) + float(value)
        parent = self._resume_parent_telemetry
        parent_service_totals = dict(
            parent.get("method_research_service_totals") or {}
        )
        for key, value in parent_service_totals.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                service_totals[key] = service_totals.get(key, 0.0) + float(value)
        parent_models = {
            str(value) for value in (parent.get("models") or ()) if value
        }
        current_models = {
            str(row.get("model_name"))
            for row in self._usage
            if row.get("model_name")
        }
        current_wall = time.perf_counter() - self._started
        return {
            "wall_seconds": float(parent.get("wall_seconds") or 0.0) + current_wall,
            "continuation_wall_seconds": current_wall,
            "qwen_calls": int(parent.get("qwen_calls") or 0) + len(self._usage),
            "qwen_input_tokens": int(parent.get("qwen_input_tokens") or 0) + input_tokens,
            "qwen_output_tokens": int(parent.get("qwen_output_tokens") or 0) + output_tokens,
            "estimated_qwen_cost_cny": float(parent.get("estimated_qwen_cost_cny") or 0.0) + estimated_cost_cny,
            "models": sorted(parent_models | current_models),
            "forward_evaluations": sum(
                int(row.budget_usage.get("forward_evaluations", 0) or 0)
                for row in self._observations
            ),
            "optimizer_runs": sum(
                int(row.budget_usage.get("optimizer_runs", 0) or 0)
                for row in self._observations
            ),
            "method_research_service_totals": service_totals,
            "performance_targets_used_as_gates": False,
        }

    def _early_finish(
        self,
        question: str,
        problem: Dict[str, Any],
        *,
        method_research: Dict[str, Any] | None = None,
        strategy_plan: Dict[str, Any] | None = None,
        status: str,
        stage: str,
        reason: str,
    ) -> TMMResearchHarnessResult:
        self._event("research_stopped", status=status, stage=stage, reason=reason)
        result = TMMResearchHarnessResult(
            run_id=self.run_id,
            status=status,
            stage=stage,
            question=question,
            problem_analysis=problem,
            method_research=method_research or {},
            strategy_plan=strategy_plan or {},
            telemetry=self._telemetry(),
            artifacts=tuple(dict.fromkeys(self._artifacts)),
        )
        self._write("RESEARCH_RESULT.json", result)
        return result


__all__ = ["TMMResearchHarness", "TMMResearchHarnessConfig", "TMMResearchHarnessResult"]
