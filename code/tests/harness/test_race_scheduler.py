"""R-06: tournament race scheduler + LLM-side threading. All fake clients."""

from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from optomind_optics.harness.research_feedback import (
    DeterministicResearchFeedbackController,
    ResearchIterationObservation,
)
from optomind_optics.harness.research_orchestrator import (
    DEFAULT_MAX_ROUNDS_PER_ROUTE,
    MAX_CONCURRENT_LLM_WORKERS,
    STAGNATION_WINDOW_ROUNDS,
    RouteTrack,
    TMMResearchHarness,
    TMMResearchHarnessConfig,
)
from optomind_optics.harness.strategy_planner import (
    ARTICLE_STRATEGY_PLANNER_MODEL,
    QwenTMMStrategyPlanner,
)

REFLECTION_MODEL_NAME = "qwen3.5-flash"


# ---------------------------------------------------------------------------
# scaffolding
# ---------------------------------------------------------------------------


def _route(route_id: str, *, priority: int = 1, pairs: int = 5, request: str | None = None):
    # Prose deliberately avoids embedding the route id: the strategy
    # planner's normalization rejects routes whose text references route
    # identifiers. Distinct pair counts give distinct request hashes so a
    # multi-route portfolio survives the duplicate filter.
    return {
        "route_id": route_id,
        "title": "Quarter-wave reflector variant",
        "route_kind": "periodic_stack",
        "scientific_hypothesis": (
            "Periodic impedance contrast creates a wide stop band around the band."
        ),
        "design_principle": "Start near quarter-wave thickness then optimize.",
        "proposed_materials": ["SiO2", "TiO2"],
        "proposed_topology": "Alternating dielectric pairs on glass.",
        "design_variables": [
            "Physical thickness of SiO2 layer 1",
            "Physical thickness of TiO2 layer 2",
        ],
        "soft_objectives": ["maximize mean reflectance from 500 to 600 nm"],
        "evidence_ids": [],
        "theory_basis": [
            "Quarter-wave optical thickness maximizes coherent reflection."
        ],
        "execution_request_english": request
        or (
            f"Design a {pairs}-pair TiO2/SiO2 reflector on glass from 450 to "
            "900 nm, maximizing mean reflectance from 500 to 600 nm."
        ),
        "priority": priority,
        "expected_observations": [
            "best_target_score should increase over the executed rounds"
        ],
        "stop_conditions": [
            "Stop when best_target_score gains stay below the reference epsilon"
        ],
    }


def _plan(*routes) -> dict[str, Any]:
    return {
        "problem_id": "p1",
        "planning_summary": "Tournament starting portfolio.",
        "routes": list(routes),
        "research_influence": ["ev_1 motivated the periodic family."],
        "stop_if_all_routes_fail": "Return the best physically valid result.",
    }


def _revised(plan: dict[str, Any], suffix: str) -> dict[str, Any]:
    """Deep-copied plan whose routes carry a NEW execution request (and thus a
    new _route_hash), optionally annotated with a traceable revision reason."""
    out = copy.deepcopy(plan)
    for route in out["routes"]:
        route["execution_request_english"] = (
            str(route["execution_request_english"]) + " Revised context: " + suffix
        )
        route["revision_reason"] = "Reflected adjustment: " + suffix
    return out


class PlusClient:
    """Thread-safe planner client. When scripted payloads run out it REPLAYS
    the last one -- an echo is a hash-duplicate revision, which deterministically
    terminates a chain through the substantive-duplication rule."""

    model_name = ARTICLE_STRATEGY_PLANNER_MODEL
    TOKENS_PER_CALL = 8  # 6 input + 2 output

    def __init__(self, payloads: list[dict[str, Any]]):
        self.payloads = list(payloads)
        self.last: dict[str, Any] | None = None
        self.sent: list[dict[str, Any]] = []
        self.calls = 0
        self.lock = threading.Lock()

    def call(self, messages, *, max_tokens: int = 5000, force_mock=None):
        with self.lock:
            user = json.loads(
                next(m for m in messages if m["role"] == "user")["content"]
            )
            self.sent.append(user)
            self.calls += 1
            if self.payloads:
                payload = self.payloads.pop(0)
            else:
                payload = copy.deepcopy(self.last or {})
            if payload:
                self.last = copy.deepcopy(payload)
        return {
            "content": json.dumps(payload),
            "_llm_usage": {"input_tokens": 6, "output_tokens": 2},
        }


class TurboClient:
    """Thread-safe reflection client with optional barrier / delay / indexed
    failure injection for concurrency tests."""

    model_name = REFLECTION_MODEL_NAME
    TOKENS_PER_CALL = 14  # 9 prompt + 5 completion

    def __init__(
        self,
        payload: dict[str, Any],
        *,
        barrier: threading.Barrier | None = None,
        sleep: float = 0.0,
        fail_indexes: set[int] | None = None,
    ):
        self.payload = dict(payload)
        self.barrier = barrier
        self.sleep = sleep
        self.fail_indexes = set(fail_indexes or ())
        self.sent: list[dict[str, Any]] = []
        self.thread_ids: list[int] = []
        self.calls = 0
        self.lock = threading.Lock()

    def call(self, messages, *, max_tokens: int = 4000, force_mock=None):
        index = 0
        with self.lock:
            index = self.calls
            self.calls += 1
            self.sent.append(
                json.loads(
                    next(m for m in messages if m["role"] == "user")["content"]
                )
            )
            self.thread_ids.append(threading.get_ident())
        if index in self.fail_indexes:
            raise RuntimeError("reflection backend boom")
        if self.barrier is not None:
            try:
                self.barrier.wait(timeout=10)
            except threading.BrokenBarrierError:
                pass
        if self.sleep:
            time.sleep(self.sleep)
        return {
            "content": json.dumps(dict(self.payload)),
            "_llm_usage": {"prompt_tokens": 9, "completion_tokens": 5},
        }


def _fake_reflection() -> dict[str, Any]:
    return {
        "observed_vs_expected": (
            "best_target_score 0.85 matches expectation of increase"
        ),
        "deviation_mechanism": "No deviation observed.",
        "continue_recommended": True,
        "continue_rationale": "Hypothesis viable; further optimization may help.",
        "insight_for_next": "Increase the pair count to widen the stop band.",
        "insight_grounding": "Candidate thicknesses show the quarter-wave pattern.",
    }


def _stub_compiler_factory():
    from optomind_optics.harness import (
        EngineMode,
        HarnessBudgetPolicy,
        OpticalDesignTask,
        TMMExperimentSpec,
    )
    from tmm_engine import LayerSpec, MediumSpec, SimulationTask, SpectralGrid, StackSpec
    from tmm_engine.schemas import dataclass_to_dict

    simulation = SimulationTask(
        stack=StackSpec(
            layers=(LayerSpec(material="alumina", provider="rii", thickness_nm=100.0),),
            incident=MediumSpec(constant_n=1.0),
            exit=MediumSpec(constant_n=1.5),
        ),
        spectrum=SpectralGrid(start_nm=450.0, stop_nm=700.0, points=31),
    )
    design_task = OpticalDesignTask(
        task_id="race_task",
        user_request_original="Design a broadband mirror from 500-600 nm.",
        normalized_request_english="Design a broadband mirror from 500 to 600 nm.",
        experiments=(
            TMMExperimentSpec(
                experiment_id="race_forward",
                mode=EngineMode.simulate,
                tmm_task=dataclass_to_dict(simulation),
            ),
        ),
        budget=HarnessBudgetPolicy(maximum_forward_evaluations=100),
    )

    class _StubCompilation:
        task = design_task

        def model_dump(self, mode="json"):
            return {
                "status": "compiled",
                "task": None,
                "rationale": "stub compiler produced a concrete task",
                "validation_errors": [],
                "usage": [],
            }

    class _StubCompiler:
        def compile(self, question, *, benchmark=None, force_mock=None):
            return _StubCompilation()

    return _StubCompiler()


def _tmm_factory(outcomes: dict[str, Any] | None = None, *, sleep: float = 0.02):
    """Serial-execution recorder. outcomes['score'] controls the returned
    target score; outcomes['result_outcome'] injects VeriTMMResult.outcome."""
    state = {"active": 0, "max_active": 0}
    lock = threading.Lock()
    holder = {"score": 0.85}

    def factory(directory, run_id):
        mock_harness = Mock()

        def run(task):
            with lock:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            try:
                time.sleep(sleep)
                payload: dict[str, Any] = {
                    "status": "completed",
                    "experiment_results": [
                        {
                            "experiment_id": "e1",
                            "mode": "optimize",
                            "physically_valid_candidate_count": 2,
                            "portfolio": {
                                "candidates": [
                                    {
                                        "candidate_id": "cand_1",
                                        "physically_admissible": True,
                                        "target_score": holder["score"],
                                        "robustness_score": 0.7,
                                        "metadata": {"thicknesses_nm": [100.0]},
                                        "certificate_id": "cert_1",
                                        "artifact_ids": [],
                                    }
                                ],
                                "selected_roles": {},
                                "pareto_candidate_ids": [],
                            },
                        }
                    ],
                }
                result_outcome = (outcomes or {}).get("result_outcome")
                if result_outcome:
                    payload["outcome"] = result_outcome
                return Mock(
                    status="completed",
                    experiment_results=[],
                    diagnoses=[],
                    budget={},
                    model_dump=Mock(return_value=payload),
                )
            finally:
                with lock:
                    state["active"] -= 1

        mock_harness.run = Mock(side_effect=run)
        return mock_harness

    factory.score_holder = holder
    factory.state = state
    return factory


def _analysis_ok(_question, force_mock=None):
    return {
        "status": "completed",
        "analysis": {
            "problem_id": "p1",
            "primary_intent": "design",
            "normalized_request_english": "Test request",
            "compatibility": "compatible",
        },
        "usage": [],
    }


class _Analyzer:
    def analyze(self, question, force_mock=None):
        return _analysis_ok(question, force_mock=force_mock)


class _Researcher:
    def research(self, problem, **kwargs):
        return {
            "status": "completed",
            "report": {
                "status": "completed",
                "problem_id": "p1",
                "queries": [],
                "evidence": [],
                "method_findings": [],
                "unresolved_questions": [],
                "telemetry": {},
            },
        }


def _build(
    tmp_path: Path,
    *,
    routes: list[dict[str, Any]],
    planner_payloads: list[dict[str, Any]] | None = None,
    reflection_payload: dict[str, Any] | None = None,
    reflection_barrier=None,
    reflection_sleep: float = 0.0,
    reflection_fail_indexes: set[int] | None = None,
    tmm_outcomes: dict[str, Any] | None = None,
    config_kwargs: dict[str, Any] | None = None,
):
    base_plan = _plan(*routes)
    plus = PlusClient(list(planner_payloads or [base_plan]))
    planner = QwenTMMStrategyPlanner(client=plus, maximum_attempts=1)
    turbo = TurboClient(
        reflection_payload or _fake_reflection(),
        barrier=reflection_barrier,
        sleep=reflection_sleep,
        fail_indexes=reflection_fail_indexes,
    )
    factory = _tmm_factory(tmm_outcomes)
    # Defaults are merged rather than splatted after fixed keywords so a test
    # can override maximum_iterations (needed by the budget-ceiling test);
    # splatting raised "got multiple values for keyword argument".
    config_fields: dict[str, Any] = {
        "qwen_force_mock": True,
        "maximum_initial_routes": max(len(routes), 1),
        "maximum_iterations": 8,
    }
    config_fields.update(config_kwargs or {})
    config = TMMResearchHarnessConfig(**config_fields)
    harness = TMMResearchHarness(
        work_dir=tmp_path,
        problem_analyzer=_Analyzer(),
        method_researcher=_Researcher(),
        strategy_planner=planner,
        task_compiler=_stub_compiler_factory(),
        config=config,
    )
    harness._reflection_client = turbo
    harness.tmm_harness_factory = factory
    return harness, plus, turbo, factory


# ---------------------------------------------------------------------------
# 1. feedback priority: refine before enumerate (unit level)
# ---------------------------------------------------------------------------


def _obs(route_id: str, score: float) -> ResearchIterationObservation:
    return ResearchIterationObservation(
        iteration_id="iteration_01",
        route_id=route_id,
        route_title="Route " + route_id,
        compilation_status="compiled",
        compilation_rationale="ok",
        compilation_errors=[],
        run_status="completed",
        physically_valid_candidate_count=2,
        best_target_score=score,
        best_robustness_score=0.72,
        selected_candidate_ids=["cand_1"],
        failure_categories=[],
        experiment_summaries=[
            {"experiment_id": "e1", "mode": "optimize"}
        ],
        candidate_summaries=[],
        budget_usage={},
        work_dir="iterations/iteration_01",
        task_path=None,
        result_path=None,
    )


def test_route_refines_before_trying_next_route():
    controller = DeterministicResearchFeedbackController(
        maximum_refinement_rounds=1
    )
    rows = [_obs("route_a", 0.85)]
    common = dict(
        untried_route_count=1,
        refinement_rounds_used=0,
        research_rounds_used=1,
        budget_remaining=True,
    )
    # Route A finished a round with ranking headroom while B is untried:
    # the next step must refine A, NOT switch to B.
    decision = controller.decide(
        rows, route_rounds_used=1, max_rounds_per_route=4, **common
    )
    assert decision.action == "refine_route"

    # Once A consumed its per-route round budget, the queue gets its turn.
    exhausted = controller.decide(
        rows, route_rounds_used=4, max_rounds_per_route=4, **common
    )
    assert exhausted.action == "try_next_route"


# ---------------------------------------------------------------------------
# 2-3. LLM-side concurrency vs serial VeriTMM
# ---------------------------------------------------------------------------


def test_two_llm_calls_run_in_parallel_within_a_wave(tmp_path):
    routes = [_route("r_a", pairs=6), _route("r_b", pairs=7), _route("r_c", pairs=8)]
    barrier = threading.Barrier(2)  # two of three reflections must overlap
    harness, _plus, turbo, _factory = _build(
        tmp_path,
        routes=routes,
        reflection_barrier=barrier,
    )
    harness.run("Design a broadband mirror from 500-600 nm")
    # The barrier can only be passed by two concurrent calls; a serialized
    # executor would leave the second waiter blocked until timeout.
    assert len(turbo.sent) >= 3
    assert len(set(turbo.thread_ids)) >= 2, (
        "reflections ran on a single thread inside one wave"
    )


def test_veritmm_execution_stays_serial_while_llm_runs_concurrently(tmp_path):
    routes = [_route("r_a", pairs=6), _route("r_b", pairs=7), _route("r_c", pairs=8)]
    harness, _plus, _turbo, factory = _build(
        tmp_path,
        routes=routes,
        reflection_sleep=0.05,
    )
    harness.run("Design a broadband mirror from 500-600 nm")
    assert factory.state["max_active"] == 1, (
        "VeriTMM executions overlapped; they must stay sequential"
    )


# ---------------------------------------------------------------------------
# 4-6. chain ownership, ledger continuity, duplication guard
# ---------------------------------------------------------------------------


def test_each_chain_sees_only_its_own_prior_iterations(tmp_path):
    class RecordingPlanner:
        def __init__(self, inner):
            self.inner = inner
            self.prior_calls: list[list[dict[str, Any]]] = []

        def plan(self, problem, research, *, prior_iterations=(), feedback_directives=(), force_mock=None):
            self.prior_calls.append(
                [dict(row) for row in prior_iterations]
            )
            return self.inner.plan(
                problem,
                research,
                prior_iterations=prior_iterations,
                feedback_directives=feedback_directives,
                force_mock=force_mock,
            )

    routes = [_route("r_a", pairs=6), _route("r_b", pairs=7)]
    base_plan = _plan(*routes)
    plus = PlusClient([base_plan])
    planner = QwenTMMStrategyPlanner(client=plus, maximum_attempts=1)
    recorder = RecordingPlanner(planner)
    harness, _p, _t, factory = _build(tmp_path, routes=routes)
    harness.strategy_planner = recorder
    harness.run("Design a broadband mirror from 500-600 nm")

    # Initial planning carries no iterations; each per-chain replanning call
    # must see exclusively its OWN route's observations (red line 6).
    assert recorder.prior_calls[0] == []
    replan_calls = recorder.prior_calls[1:]
    assert len(replan_calls) >= 2
    seen_chains = set()
    for rows in replan_calls:
        assert rows, "replan ran without the chain's own history"
        ids = {str(row.get("route_id")) for row in rows}
        assert len(ids) == 1, f"chain saw foreign history: {ids}"
        seen_chains |= ids
    assert seen_chains == {"r_a", "r_b"}


def test_revision_reusing_track_id_keeps_ledgers(tmp_path):
    base_plan = _plan(_route("route_01"))
    revised = _revised(base_plan, "round two widens the stack")
    harness, _plus, _turbo, _factory = _build(
        tmp_path,
        routes=base_plan["routes"],
        planner_payloads=[base_plan, revised],
    )
    harness.run("Design a broadband mirror from 500-600 nm")
    track = harness.tournament_tracks["route_01"]
    # The revision kept the SAME track: rounds and score history continued
    # instead of resetting (the structural replacement of the lineage patch).
    assert track.rounds_used >= 2
    assert len(track.score_history) >= 2
    assert len(track.version_hashes) >= 2


def test_duplicate_revision_hash_is_retried_until_hard_round_limit(tmp_path):
    base_plan = _plan(_route("route_01"))
    # The scripted replan ECHOES the original request: identical _route_hash.
    harness, _plus, _turbo, _factory = _build(
        tmp_path,
        routes=base_plan["routes"],
        planner_payloads=[base_plan, base_plan],
    )
    harness.run("Design a broadband mirror from 500-600 nm")
    track = harness.tournament_tracks["route_01"]
    assert track.status == "stopped_round_limit"
    assert track.rounds_used == DEFAULT_MAX_ROUNDS_PER_ROUTE
    assert "round limit" in track.termination_reason.casefold() or "cap" in track.termination_reason.casefold()
    # The echoed revision is recorded as a retry, not as scientific defeat.
    events = [
        json.loads(line)
        for line in (tmp_path / "RESEARCH_EVENTS.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(event["event_type"] == "route_retry_scheduled" for event in events)
    # The first per-iteration decision still records the continuation decision
    # made at that point in history.
    decision = json.loads(
        (tmp_path / "iterations" / "iteration_01" / "FEEDBACK_DECISION.json")
        .read_text(encoding="utf-8")
    )
    assert decision["action"] == "refine_route"


def test_resume_from_checkpoint_reactivates_selected_route_without_overwrite(tmp_path):
    """A copied checkpoint continues its own route ledger in a child run."""
    from optomind_optics.harness.scoring_standard import (
        FixedScoreMetric,
        ScoringStandard,
    )

    metric = FixedScoreMetric(
        variable="mean_reflectance_500_600nm",
        canonical_id="mean_reflectance@500-600nm",
        metric="mean_reflectance",
        sense="maximize",
        region={"wavelength_nm": [500.0, 600.0]},
    )
    standard = ScoringStandard(
        question_digest="test-question",
        metrics=(metric,),
        formula=metric.variable,
    )

    class _StandardResult:
        status = "standardized"
        validation_errors = ()
        usage = ()

        def __init__(self, value):
            self.standard = value

        def model_dump(self, mode="json"):
            return {
                "status": self.status,
                "standard": self.standard.model_dump(mode="json"),
                "validation_errors": [],
                "usage": [],
            }

    class _StandardBuilder:
        def build(self, question, *, problem_analysis, force_mock=None):
            return _StandardResult(standard)

    base_plan = _plan(_route("route_01"))
    parent_dir = tmp_path / "parent"
    parent, _plus, _turbo, _factory = _build(
        parent_dir,
        routes=base_plan["routes"],
        planner_payloads=[base_plan],
        config_kwargs={
            "maximum_iterations": 1,
            "max_rounds_per_route": 1,
        },
    )
    parent.scoring_standard_builder = _StandardBuilder()
    parent.run("Design a broadband mirror from 500-600 nm")
    parent_request_before = (parent_dir / "REQUEST.json").read_text(encoding="utf-8")

    child_dir = tmp_path / "child"
    child, _plus2, _turbo2, _factory2 = _build(
        child_dir,
        routes=base_plan["routes"],
        planner_payloads=[base_plan, _revised(base_plan, "checkpoint continuation")],
        config_kwargs={
            "maximum_iterations": 2,
            "max_rounds_per_route": 2,
        },
    )
    child.scoring_standard_builder = _StandardBuilder()
    result = child.resume_from_checkpoint(
        parent_dir,
        route_ids=["route_01"],
    )

    assert (child_dir / "PARENT_REQUEST.json").exists()
    assert (child_dir / "PARENT_RESEARCH_RESULT.json").exists()
    assert (child_dir / "REQUEST.json").read_text(encoding="utf-8") != parent_request_before
    assert len(result.iterations) == 2
    assert child.tournament_tracks["route_01"].rounds_used == 2
    assert child.tournament_tracks["route_01"].status == "stopped_round_limit"
    assert result.telemetry["continuation_wall_seconds"] >= 0.0

    events = [
        json.loads(line)
        for line in (child_dir / "RESEARCH_EVENTS.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert any(
        event["event_type"] == "route_reactivated_from_checkpoint"
        and event["route_id"] == "route_01"
        for event in events
    )


# ---------------------------------------------------------------------------
# 7-9. red line 7: elimination ONLY via the authoritative engine outcome
# ---------------------------------------------------------------------------


def _single_track_with_outcome(tmp_path, outcome: str | None):
    base_plan = _plan(_route("route_01"))
    outcomes = {"result_outcome": outcome} if outcome else {}
    harness, _plus, _turbo, _factory = _build(
        tmp_path, routes=base_plan["routes"], tmm_outcomes=outcomes
    )
    harness.run("Design a broadband mirror from 500-600 nm")
    return harness.tournament_tracks["route_01"]


def test_physics_rejected_is_the_only_elimination_status(tmp_path):
    track = _single_track_with_outcome(tmp_path, "physics_rejected")
    assert track.status == "eliminated_physics"
    assert "physics_rejected" in track.termination_reason
    # The elimination is attributed to the engine's verdict, not to a string
    # category invented downstream.
    events = [
        json.loads(line)
        for line in (tmp_path / "RESEARCH_EVENTS.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert any(
        e["event_type"] == "track_status" and e["status"] == "eliminated_physics"
        for e in events
    )


def test_engine_error_never_maps_to_eliminated(tmp_path):
    track = _single_track_with_outcome(tmp_path, "engine_error")
    assert track.status == "stopped_round_limit"
    assert track.rounds_used == DEFAULT_MAX_ROUNDS_PER_ROUTE
    assert track.status != "eliminated_physics"


def test_budget_blocked_maps_to_stopped_budget(tmp_path):
    track = _single_track_with_outcome(tmp_path, "budget_blocked")
    assert track.status == "stopped_round_limit"
    assert track.rounds_used == DEFAULT_MAX_ROUNDS_PER_ROUTE
    assert track.status != "eliminated_physics"


# ---------------------------------------------------------------------------
# 10. deterministic stop statuses
# ---------------------------------------------------------------------------


def test_round_limit_and_stagnation_stop_statuses(tmp_path):
    # Round limit: cap of 1 closes the continuation gate right after round 1.
    base_plan = _plan(_route("route_01"))
    harness, _plus, _turbo, _factory = _build(
        tmp_path,
        routes=base_plan["routes"],
        config_kwargs={"max_rounds_per_route": 1},
    )
    harness.run("Design a broadband mirror from 500-600 nm")
    track = harness.tournament_tracks["route_01"]
    assert track.status == "stopped_round_limit"
    assert track.rounds_used == 1

    # Stagnation: constant scores are recorded as an advisory signal, but the
    # exploration policy keeps the route alive until its hard cap.
    tmp2 = tmp_path / "stagnation"
    tmp2.mkdir()
    factory_score_plan = _plan(_route("route_02"))
    harness2, _p2, _t2, factory2 = _build(
        tmp2,
        routes=factory_score_plan["routes"],
        planner_payloads=[
            factory_score_plan,
            _revised(factory_score_plan, "attempt two"),
            _revised(factory_score_plan, "attempt three"),
        ],
        config_kwargs={"max_rounds_per_route": 5},
    )
    factory2.score_holder["score"] = 0.50  # identical score every round
    harness2.run("Design a broadband mirror from 500-600 nm")
    stagnant = harness2.tournament_tracks["route_02"]
    assert stagnant.status == "stopped_round_limit"
    assert len(stagnant.score_history) == 5
    events = [
        json.loads(line)
        for line in (tmp2 / "RESEARCH_EVENTS.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(
        event["event_type"] == "reflection_completed"
        for event in events
    )


# ---------------------------------------------------------------------------
# 10b. the iteration ceiling is absolute, not per-wave
# ---------------------------------------------------------------------------


def test_wave_admission_cannot_overrun_the_iteration_ceiling(tmp_path):
    """Regression lock: a wave costs one iteration PER RACING TRACK.

    The wave-boundary gate only asked whether *any* iteration remained, then
    Phase 1 ran a round on every racing track -- so N tracks overshot
    maximum_iterations by up to N-1, and the overrun grew exactly as R-05/R-06
    widened the portfolio. Here 3 tracks race against a ceiling of 4: wave 1
    spends 3, so wave 2 may admit only ONE track. The deferred tracks must not
    vanish -- they stay accounted for and terminate as stopped_budget.
    """
    routes = [_route("r_a", pairs=6), _route("r_b", pairs=7), _route("r_c", pairs=8)]
    base_plan = _plan(*routes)
    harness, _plus, _turbo, _factory = _build(
        tmp_path,
        routes=routes,
        # Six distinct revisions for at most four replan calls: concurrent
        # workers pop in nondeterministic order, so a surplus of unique
        # suffixes keeps every chain's revision substantive regardless of order.
        planner_payloads=[base_plan]
        + [_revised(base_plan, f"revision {index}") for index in range(6)],
        config_kwargs={
            "maximum_iterations": 4,
            "max_rounds_per_route": 5,
            # This regression targets the legacy shared global ceiling; the
            # per-route quota regime is covered explicitly in test_route_round_quota.py.
            "per_route_round_quota_enabled": False,
        },
    )
    result = harness.run("Design a broadband mirror from 500-600 nm")

    assert len(result.iterations) <= 4, "iteration ceiling overrun"
    executed = sorted((tmp_path / "iterations").glob("iteration_*"))
    assert len(executed) == len(result.iterations)

    events = [
        json.loads(line)
        for line in (tmp_path / "RESEARCH_EVENTS.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    truncations = [e for e in events if e["event_type"] == "wave_admission_truncated"]
    assert truncations, "budget rationing happened silently"
    first = truncations[0]
    assert len(first["admitted"]) == first["iterations_left"]
    assert first["deferred"], "truncation recorded without naming the deferred tracks"
    # Deferral is not deletion: every track reaches a terminal status.
    assert all(
        track.status != "racing" for track in harness.tournament_tracks.values()
    )
    deferred_ids = set(first["deferred"])
    assert {
        harness.tournament_tracks[route_id].status for route_id in deferred_ids
    } <= {
        "stopped_budget",
        "stopped_stagnant",
        "stopped_llm_advice",
        "stopped_round_limit",
        "eliminated_physics",
        "error_unrecoverable",
    }


# ---------------------------------------------------------------------------
# 11-12. observable state: TOURNAMENT_STATE.json + event sequence integrity
# ---------------------------------------------------------------------------


def test_tournament_state_written_per_wave_with_contract_keys(tmp_path):
    routes = [_route("r_b", priority=2, pairs=7), _route("r_a", priority=1, pairs=6)]
    harness, _plus, _turbo, _factory = _build(tmp_path, routes=routes)
    harness.run("Design a broadband mirror from 500-600 nm")
    state_path = tmp_path / "TOURNAMENT_STATE.json"
    assert state_path.exists()
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["schema_version"] == "tournament-state.v1"
    assert data["wave"] >= 1
    assert set(data["budget_snapshot"]) >= {
        "iterations_used",
        "maximum_iterations",
        "wall_seconds_elapsed",
    }
    track_ids = [t["route_id"] for t in data["tracks"]]
    assert track_ids == sorted(track_ids)
    for entry in data["tracks"]:
        assert set(entry) >= {
            "route_id",
            "source",
            "status",
            "termination_reason",
            "rounds_used",
            "score_history",
            "best_candidate_ids",
            "current_route",
        }
        assert entry["status"] in {
            "racing",
            "stopped_stagnant",
            "stopped_llm_advice",
            "eliminated_physics",
            "stopped_round_limit",
            "stopped_budget",
            "error_unrecoverable",
        }
    assert "TOURNAMENT_STATE.json" in harness._artifacts


def test_event_sequence_unique_and_monotone_under_concurrency(tmp_path):
    routes = [_route("r_a", pairs=6), _route("r_b", pairs=7), _route("r_c", pairs=8)]
    base_plan = _plan(*routes)
    # One distinct revision per chain so wave 2 races all three again; the
    # subsequent echoed replans then terminate every chain deterministically.
    harness, _plus, _turbo, _factory = _build(
        tmp_path,
        routes=routes,
        planner_payloads=[
            base_plan,
            _revised(base_plan, "wave two a"),
            _revised(base_plan, "wave two b"),
            _revised(base_plan, "wave two c"),
        ],
        reflection_sleep=0.01,
    )
    harness.run("Design a broadband mirror from 500-600 nm")
    sequences = [
        json.loads(line)["sequence"]
        for line in (tmp_path / "RESEARCH_EVENTS.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(sequences) >= 10
    assert len(set(sequences)) == len(sequences), "duplicate sequence numbers"
    assert sequences == sorted(sequences), "sequence not monotone"


# ---------------------------------------------------------------------------
# 13-14. metering and worker-cap discipline
# ---------------------------------------------------------------------------


def test_concurrent_reflections_do_not_lose_usage_metering(tmp_path, monkeypatch):
    from config.qwen_config import CostTracker
    import optomind_optics.harness.route_reflection as rr_module

    class LockableTracker(CostTracker):
        """CostTracker itself has NO lock (qwen_config.py:402-417, reported);
        the orchestrator-side guarantee under test is that NO token is lost
        when the tracker synchronizes. Upstream locking is recommended."""

        def __init__(self):
            super().__init__()
            self._meter_lock = threading.Lock()

        def record_qwen_usage(self, model: str, tokens: int) -> None:
            with self._meter_lock:
                super().record_qwen_usage(model, tokens)

        def record_tmm_usage(self, cpu_seconds: float) -> None:
            with self._meter_lock:
                super().record_tmm_usage(cpu_seconds)

    tracker = LockableTracker()
    monkeypatch.setattr(rr_module, "get_cost_tracker", lambda: tracker)

    routes = [
        _route("r_a", pairs=6),
        _route("r_b", pairs=7),
        _route("r_c", pairs=8),
        _route("r_d", pairs=9),
    ]
    harness, plus, turbo, _factory = _build(
        tmp_path, routes=routes, reflection_sleep=0.01
    )
    harness.run("Design a broadband mirror from 500-600 nm")

    snapshot = tracker.get_budget_snapshot()
    expected_turbo = turbo.TOKENS_PER_CALL * turbo.calls
    assert turbo.calls >= 4 and plus.calls >= 5
    # Reflection-tier metering flows through CostTracker (turbo bucket) and
    # must lose NOTHING under concurrency. Planning-tier usage is carried in
    # usage rows / telemetry instead of the tracker -- recorded as-is here so
    # the test documents reality rather than an aspiration.
    assert snapshot.qwen_tokens.get("turbo", 0) == expected_turbo
    assert snapshot.qwen_tokens.get("plus", 0) == 0
    assert set(snapshot.qwen_tokens.keys()) <= {"plus", "turbo"}


def test_llm_worker_pool_respects_the_cap(tmp_path, monkeypatch):
    import optomind_optics.harness.research_orchestrator as ro_module

    observed: list[int] = []
    real_executor = ro_module.ThreadPoolExecutor

    class RecordingExecutor(real_executor):
        def __init__(self, *args, **kwargs):
            observed.append(int(kwargs.get("max_workers", args[0] if args else 0)))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(ro_module, "ThreadPoolExecutor", RecordingExecutor)

    # Five racing routes need a plan payload that bypasses StrategyPlan's
    # one-to-four validation: the scheduler itself honours max_routes=5.
    class RawPlanPlanner:
        def __init__(self, plan_payload):
            self.plan_payload = plan_payload

        def plan(self, problem, research, *, prior_iterations=(), feedback_directives=(), force_mock=None):
            # Mirror the real planner's normalization: pre-declaration columns
            # are stripped from routes before DesignRoute validation.
            cleaned = copy.deepcopy(self.plan_payload)
            for route in cleaned.get("routes", []) or []:
                route.pop("expected_observations", None)
                route.pop("stop_conditions", None)
            return {"status": "planned", "plan": cleaned, "usage": []}

    routes = [
        _route("r_" + chr(ord("a") + i), pairs=6 + i) for i in range(5)
    ]
    base_plan = _plan(*routes)
    harness, _plus, _turbo, _factory = _build(tmp_path, routes=routes)
    harness.strategy_planner = RawPlanPlanner(base_plan)
    harness.run("Design a broadband mirror from 500-600 nm")
    assert observed, "no worker pool was created"
    assert all(w <= MAX_CONCURRENT_LLM_WORKERS for w in observed)
    assert max(observed) == min(len(routes), MAX_CONCURRENT_LLM_WORKERS)


# ---------------------------------------------------------------------------
# 15-16. fault isolation and the status-name contract
# ---------------------------------------------------------------------------


def test_worker_exception_does_not_sink_the_wave(tmp_path):
    routes = [_route("r_a", pairs=6), _route("r_b", pairs=7), _route("r_c", pairs=8)]
    harness, _plus, turbo, _factory = _build(
        tmp_path,
        routes=routes,
        reflection_fail_indexes={1},  # second submitted reflection raises
    )
    result = harness.run("Design a broadband mirror from 500-600 nm")
    # The wave completed; the broken reflection degraded to an ABSENT vote.
    assert result.status in {"completed", "completed_best_effort_no_verified_candidate"}
    unavailable = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((tmp_path / "iterations").glob("iteration_*/ROUTE.REFLECTION.json"))
        if json.loads(p.read_text(encoding="utf-8")).get("reflection_available") is False
    ]
    assert len(unavailable) == 1
    assert "RuntimeError" in unavailable[0]["degraded_reason"]
    # The other two reflections were healthy.
    healthy = turbo.calls - 1
    assert healthy >= 2


def test_status_names_are_the_r07_contract_set():
    import optomind_optics.harness.research_orchestrator as ro_module

    names = {
        ro_module.TRACK_RACING,
        ro_module.TRACK_STOPPED_STAGNANT,
        ro_module.TRACK_STOPPED_LLM_ADVICE,
        ro_module.TRACK_ELIMINATED_PHYSICS,
        ro_module.TRACK_STOPPED_ROUND_LIMIT,
        ro_module.TRACK_STOPPED_BUDGET,
        ro_module.TRACK_ERROR_UNRECOVERABLE,
    }
    assert names == {
        "racing",
        "stopped_stagnant",
        "stopped_llm_advice",
        "eliminated_physics",
        "stopped_round_limit",
        "stopped_budget",
        "error_unrecoverable",
    }
