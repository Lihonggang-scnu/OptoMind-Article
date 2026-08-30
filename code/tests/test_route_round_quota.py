"""Per-route round quotas, and the fail-open audit of why routes stopped.

Two properties, and they pull in opposite directions on purpose.

The first is a budget one: a portfolio's width only means something if every
route in it can actually run.  Under the older shared pool, five routes against
a pool of six rounds meant the first routes iterated and the last ones never
executed at all -- so an axis that was planned got reported as if it had been
tried.  Each route needs its own allowance, and an allowance a route does not
spend must not be handed to another route.

The second is a bookkeeping one, and it must never become a gate.  A route that
records why it stopped is more useful than one that just stops, so the reason is
audited -- but the numbers a route produced are true whether or not the sentence
beside them was written, so a missing reason is reported and nothing else.
Discarding measurements to protect paperwork is the failure mode being
prevented here.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from optomind_optics.harness.dev_fixtures import build_dev_optical_design_task
from optomind_optics.harness.research_orchestrator import (
    TRACK_RACING,
    TRACK_STOPPED_RUN_HALTED,
    RouteTrack,
    TMMResearchHarness,
    TMMResearchHarnessConfig,
)


QUESTION = "Reflect 300-800 nm and absorb the 5-13 um window."


# ---------------------------------------------------------------------------
# Scripted stages; nothing here reaches the network.
# ---------------------------------------------------------------------------


class _Analyzer:
    def analyze(self, question, force_mock=None):
        del force_mock
        return {
            "status": "completed",
            "analysis": {
                "problem_id": "p1",
                "original_request": question,
                "normalized_request_english": question,
                "primary_intent": "design",
                "compatibility": "compatible",
                "compatibility_reason": "planar stack",
                "ambiguities": [],
            },
            "usage": [],
        }


class _Researcher:
    def research(self, problem, **kwargs):
        del kwargs
        return {
            "status": "completed",
            "report": {
                "problem_id": problem["problem_id"],
                "queries": [],
                "evidence": [],
                "method_findings": [],
                "unresolved_questions": [],
                "telemetry": {},
            },
        }


def _route(route_id: str) -> dict:
    return {
        "route_id": route_id,
        "title": route_id,
        "route_kind": "periodic_stack",
        "scientific_hypothesis": "Alternating indices form a stop band.",
        "design_principle": "Use interference.",
        "proposed_materials": ["SiO2", "TiO2"],
        # Distinct per route, so N routes really are N axes rather than one
        # axis proposed N times and deduplicated back down.
        "proposed_topology": f"alternating stack for {route_id}",
        "design_variables": ["thickness"],
        "soft_objectives": ["maximize reflectance"],
        "manufacturing_considerations": [],
        "evidence_ids": [],
        "theory_basis": [],
        "expected_advantages": [],
        "known_risks": [],
        "execution_request_english": f"Optimize the stack for {route_id}.",
        "priority": 1,
        "parent_route_id": None,
        "revision_reason": None,
    }


class _Planner:
    """Plans `width` routes initially, and revises one route on replanning."""

    def __init__(self, width: int) -> None:
        self.width = width
        self.replans: Dict[str, int] = {}

    def plan(self, problem, research, **kwargs):
        del problem, research
        # `chain_id` is the seam the orchestrator uses to ask for one route's
        # continuation; its absence means this is the initial portfolio.
        parent = str(kwargs.get("chain_id") or "")
        if parent:
            index = self.replans.get(parent, 0) + 1
            self.replans[parent] = index
            revised = _route(parent)
            # Counted per chain, because the request hash is compared against
            # every earlier version of THAT chain: a repeated text is rejected
            # as a non-substantive revision and would end the route early.
            revised["execution_request_english"] = (
                f"Optimize the stack for {parent}, revision {index}, "
                "widening the thickness search range."
            )
            revised["parent_route_id"] = parent
            revised["revision_reason"] = f"revision {index}"
            return {
                "status": "planned",
                "plan": {
                    "problem_id": "p1",
                    "planning_summary": "revised one axis",
                    "routes": [revised],
                    "research_influence": [],
                    "unresolved_decisions": [],
                    "stop_if_all_routes_fail": "return best effort",
                },
                "usage": [],
            }
        return {
            "status": "planned",
            "plan": {
                "problem_id": "p1",
                "planning_summary": f"{self.width} independent axes",
                "routes": [_route(f"r{index}") for index in range(1, self.width + 1)],
                "research_influence": [],
                "unresolved_decisions": [],
                "stop_if_all_routes_fail": "return best effort",
            },
            "usage": [],
        }


class _Compiler:
    def compile(self, question, force_mock=None):
        del question, force_mock
        task = build_dev_optical_design_task("DEV01")
        return SimpleNamespace(
            status="compiled",
            task=task,
            usage=(),
            model_dump=lambda mode="json": {
                "status": "compiled",
                "task": task.model_dump(mode="json"),
                "usage": [],
            },
        )


class _Counter:
    """Shared, locked round counter -- waves run tracks concurrently."""

    def __init__(self) -> None:
        import threading

        self._lock = threading.Lock()
        self.rounds = 0

    def bump(self) -> int:
        with self._lock:
            self.rounds += 1
            return self.rounds


class _Harness:
    """A VeriTMM stand-in whose score always improves, so nothing stagnates."""

    def __init__(self, counter: _Counter, *, outside_domain_at: int | None = None) -> None:
        self.counter = counter
        self.outside_domain_at = outside_domain_at

    def run(self, task):
        del task
        index = self.counter.bump()
        if self.outside_domain_at is not None and index == self.outside_domain_at:
            # The one condition that halts a whole run: physics the engine
            # declares outside its domain.
            return {
                "status": "needs_higher_fidelity",
                "experiment_results": [
                    {
                        "experiment_id": f"e{index}",
                        "mode": "optimize",
                        "physically_valid_candidate_count": 0,
                        "failure_categories": ["outside_tmm_domain"],
                        "portfolio": {"candidates": [], "selected_roles": {}},
                    }
                ],
            }
        # A strictly rising score keeps the stagnation gate open, so the round
        # cap is the only thing that can end a route.
        candidate = {
            "candidate_id": f"candidate_{index}",
            "physically_admissible": True,
            "target_score": min(0.99, 0.10 + 0.03 * index),
            "robustness_score": 0.6,
            "simplicity_score": 0.8,
            "metadata": {"thicknesses_nm": [100.0, 200.0]},
            "artifact_ids": [],
        }
        return {
            "status": "completed",
            "experiment_results": [
                {
                    "experiment_id": f"e{index}",
                    "mode": "optimize",
                    "physically_valid_candidate_count": 1,
                    "portfolio": {
                        "candidates": [candidate],
                        "selected_roles": {"best_target_score": candidate["candidate_id"]},
                    },
                }
            ],
        }


class _KeepGoingReflector:
    """Reflection that always advises another round, so gates decide the end."""

    model_name = "scripted-turbo"

    def call(self, messages, *, max_tokens=4000, force_mock=None):
        del messages, max_tokens, force_mock
        body = {
            "continue_recommended": True,
            "reason": "the axis still has room; widen the thickness range",
            "observations": ["best_target_score rose again this round"],
            "next_directives": ["widen the thickness search range"],
        }
        return {
            "content": json.dumps(body),
            "_llm_usage": {"total_tokens": 12},
        }


def _run(tmp_path, *, width: int, outside_domain_at: int | None = None, **config: Any):
    counter = _Counter()
    planner = _Planner(width)
    settings = {
        # The legacy planner path trims the portfolio to this width, and a file
        # about per-route budgets must not have its width trimmed underneath it.
        "maximum_initial_routes": width,
        "max_rounds_per_route": 4,
        "maximum_iterations": 6,
        # Deliberately far above anything these tests reach: this file is about
        # the round quota, so no other ceiling may be what ends a route.
        "maximum_refinement_rounds": 60,
        "qwen_force_mock": True,
    }
    settings.update(config)
    harness = TMMResearchHarness(
        tmp_path / "run",
        problem_analyzer=_Analyzer(),
        method_researcher=_Researcher(),
        strategy_planner=planner,
        task_compiler=_Compiler(),
        tmm_harness_factory=lambda path, run_id: _Harness(
            counter, outside_domain_at=outside_domain_at
        ),
        config=TMMResearchHarnessConfig(**settings),
    )
    harness._reflection_client = _KeepGoingReflector()
    result = harness.run(QUESTION)
    return harness, result, counter


def _artifact(tmp_path, name: str) -> dict:
    return json.loads((tmp_path / "run" / name).read_text(encoding="utf-8"))


def _events(tmp_path) -> List[dict]:
    path = tmp_path / "run" / "RESEARCH_EVENTS.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _rounds_by_route(harness) -> Dict[str, int]:
    return {
        route_id: int(track.rounds_used)
        for route_id, track in harness.tournament_tracks.items()
    }


# ---------------------------------------------------------------------------
# 1. The switch
# ---------------------------------------------------------------------------


class TestTheSwitch:
    def test_on_by_default(self) -> None:
        # Each route owning its quota is the intended path: sharing one pool
        # lets the first routes spend it and leaves the last ones never run.
        assert TMMResearchHarnessConfig().per_route_round_quota_enabled is True

    def test_off_leaves_the_configured_pool_as_the_ceiling(self, tmp_path) -> None:
        harness, _, _ = _run(
            tmp_path,
            width=3,
            maximum_iterations=5,
            per_route_round_quota_enabled=False,
        )
        assert harness._iteration_ceiling == 5

    def test_on_raises_the_ceiling_to_one_quota_per_route(self, tmp_path) -> None:
        harness, _, _ = _run(
            tmp_path,
            width=3,
            maximum_iterations=5,
            per_route_round_quota_enabled=True,
        )
        # Three routes, four rounds each -- not the pool of five.
        assert harness._iteration_ceiling == 12

    def test_the_ceiling_is_never_lowered_below_the_configured_pool(self, tmp_path) -> None:
        harness, _, _ = _run(
            tmp_path,
            width=1,
            maximum_iterations=9,
            per_route_round_quota_enabled=True,
        )
        assert harness._iteration_ceiling == 9

    def test_the_allocation_is_announced(self, tmp_path) -> None:
        _run(tmp_path, width=3, per_route_round_quota_enabled=True)

        allocated = [
            event
            for event in _events(tmp_path)
            if event["event_type"] == "route_round_quota_allocated"
        ]
        assert len(allocated) == 1
        assert allocated[0]["routes"] == 3
        assert allocated[0]["rounds_per_route"] == 4
        assert allocated[0]["iteration_ceiling"] == 12
        assert allocated[0]["shared_iteration_pool"] is False

    def test_nothing_is_announced_when_the_pool_stays_shared(self, tmp_path) -> None:
        _run(tmp_path, width=3, per_route_round_quota_enabled=False)

        types = [event["event_type"] for event in _events(tmp_path)]
        assert "route_round_quota_allocated" not in types


# ---------------------------------------------------------------------------
# 2. A shared pool starves the last routes; a quota does not
# ---------------------------------------------------------------------------


class TestNoRouteIsStarved:
    def test_the_shared_pool_leaves_later_routes_with_no_rounds(self, tmp_path) -> None:
        """The defect this task exists to fix, pinned so it cannot come back.

        Five routes drawing on a pool of six rounds: some route ends the run
        having executed nothing, which is a planned axis reported as tried.
        The shared pool is asked for explicitly, because it is no longer the
        default -- this test is what retiring that default was for.
        """

        harness, _, counter = _run(
            tmp_path,
            width=5,
            maximum_iterations=3,
            per_route_round_quota_enabled=False,
        )

        rounds = _rounds_by_route(harness)
        assert len(rounds) == 5
        assert counter.rounds <= 3
        # Two of the five planned axes were never executed at all.
        assert min(rounds.values()) == 0
        assert sorted(rounds.values()).count(0) == 2

    def test_a_quota_gives_every_route_its_own_rounds(self, tmp_path) -> None:
        harness, _, counter = _run(
            tmp_path,
            width=5,
            maximum_iterations=3,
            per_route_round_quota_enabled=True,
        )

        rounds = _rounds_by_route(harness)
        assert len(rounds) == 5
        assert harness._iteration_ceiling == 20
        # Every route ran, and none exceeded its own allowance.
        assert min(rounds.values()) >= 1
        assert max(rounds.values()) <= 4
        assert counter.rounds == sum(rounds.values())

    def test_each_route_can_spend_its_whole_allowance(self, tmp_path) -> None:
        """Nothing stagnates and reflection always continues, so 4 means 4."""

        harness, _, counter = _run(
            tmp_path,
            width=3,
            maximum_iterations=6,
            per_route_round_quota_enabled=True,
        )

        assert _rounds_by_route(harness) == {"r1": 4, "r2": 4, "r3": 4}
        assert counter.rounds == 12

    def test_the_quota_is_a_per_route_cap_not_a_bigger_shared_pool(self, tmp_path) -> None:
        """A route that stops early must not fund another route's fifth round.

        The ceiling is routes x rounds, so if the allowance were shared, a
        route stopping early would leave slack for another to overrun. The
        per-route cap is what prevents that, so it is asserted directly.
        """

        harness, _, counter = _run(
            tmp_path,
            width=2,
            maximum_iterations=2,
            max_rounds_per_route=3,
            per_route_round_quota_enabled=True,
        )

        rounds = _rounds_by_route(harness)
        assert harness._iteration_ceiling == 6
        assert all(value <= 3 for value in rounds.values())
        assert counter.rounds <= 6

    def test_the_wall_clock_guard_still_applies(self, tmp_path) -> None:
        """The quota buys rounds, not time; a zero time budget still stops."""

        harness, result, counter = _run(
            tmp_path,
            width=3,
            wall_time_seconds=0.001,
            per_route_round_quota_enabled=True,
        )

        assert harness._iteration_ceiling == 12
        assert counter.rounds == 0
        # The run still finishes and still reports; it just has nothing to show.
        assert result.status == "completed_best_effort_no_verified_candidate"


# ---------------------------------------------------------------------------
# 3. The state file says which regime the run was under
# ---------------------------------------------------------------------------


class TestTheBudgetSnapshot:
    def test_the_snapshot_reports_the_quota_regime(self, tmp_path) -> None:
        _run(tmp_path, width=3, maximum_iterations=6, per_route_round_quota_enabled=True)

        snapshot = _artifact(tmp_path, "TOURNAMENT_STATE.json")["budget_snapshot"]
        assert snapshot["maximum_iterations"] == 12
        assert snapshot["configured_maximum_iterations"] == 6
        assert snapshot["rounds_per_route"] == 4
        assert snapshot["shared_iteration_pool"] is False

    def test_the_snapshot_reports_the_shared_regime(self, tmp_path) -> None:
        _run(
            tmp_path,
            width=2,
            maximum_iterations=6,
            per_route_round_quota_enabled=False,
        )

        snapshot = _artifact(tmp_path, "TOURNAMENT_STATE.json")["budget_snapshot"]
        assert snapshot["maximum_iterations"] == 6
        assert snapshot["configured_maximum_iterations"] == 6
        assert snapshot["shared_iteration_pool"] is True


# ---------------------------------------------------------------------------
# 4. Every route that stops says why -- audited, never enforced
# ---------------------------------------------------------------------------


class TestTheTerminationAudit:
    def test_the_audit_is_always_written(self, tmp_path) -> None:
        _, result, _ = _run(tmp_path, width=2, per_route_round_quota_enabled=True)

        audit = _artifact(tmp_path, "ROUTE_TERMINATION_AUDIT.json")
        assert audit["schema_version"] == "route-termination-audit.v1"
        assert audit["policy"] == "fail_open"
        assert audit["routes_checked"] == 2
        assert "ROUTE_TERMINATION_AUDIT.json" in result.artifacts

    def test_a_route_that_used_up_its_rounds_says_so(self, tmp_path) -> None:
        harness, _, _ = _run(
            tmp_path, width=2, per_route_round_quota_enabled=True
        )

        audit = _artifact(tmp_path, "ROUTE_TERMINATION_AUDIT.json")
        assert sorted(audit["documented"]) == ["r1", "r2"]
        assert audit["missing"] == []
        assert audit["missing_count"] == 0
        for track in harness.tournament_tracks.values():
            assert "per-route cap of 4" in track.termination_reason

    def test_the_audit_records_the_rounds_each_route_actually_used(self, tmp_path) -> None:
        harness, _, _ = _run(tmp_path, width=3, per_route_round_quota_enabled=True)

        audit = _artifact(tmp_path, "ROUTE_TERMINATION_AUDIT.json")
        assert audit["per_route_rounds_used"] == _rounds_by_route(harness)
        assert audit["rounds_per_route"] == 4
        assert audit["iteration_ceiling"] == 12
        assert audit["shared_iteration_pool"] is False

    def test_the_reasons_reach_the_comparison_table(self, tmp_path) -> None:
        _run(tmp_path, width=2, per_route_round_quota_enabled=True)

        summary = _artifact(tmp_path, "TOURNAMENT_SUMMARY.json")
        reasons = {
            row["route_id"]: row["termination_reason"]
            for row in summary["route_comparison"]
        }
        assert set(reasons) == {"r1", "r2"}
        assert all(reason for reason in reasons.values())

    def test_a_missing_reason_is_reported_and_nothing_else(self, tmp_path) -> None:
        """Fail-open, checked directly on the audit rather than through a run.

        Driving a real run into a blank reason would mean introducing a code
        path that leaves one blank, which is the opposite of the fix. What the
        audit owes is a policy, so the policy is what gets exercised: an
        undocumented route is listed, counted, and keeps everything else.
        """

        harness, _, _ = _run(tmp_path, width=1, per_route_round_quota_enabled=True)
        blank = RouteTrack(
            route_id="r_blank",
            source="planned",
            status="stopped_stagnant",
            termination_reason="   ",
            rounds_used=2,
        )
        spoken = RouteTrack(
            route_id="r_spoken",
            source="planned",
            status="stopped_round_limit",
            termination_reason="reached the per-route cap of 4 executed rounds",
            rounds_used=4,
        )

        audit = harness._audit_route_termination([blank, spoken])

        assert audit["policy"] == "fail_open"
        assert audit["documented"] == ["r_spoken"]
        assert audit["missing"] == [
            {"route_id": "r_blank", "status": "stopped_stagnant"}
        ]
        assert audit["missing_count"] == 1
        assert audit["routes_checked"] == 2
        # The undocumented route keeps its own accounting; nothing is voided.
        assert audit["per_route_rounds_used"] == {"r_blank": 2, "r_spoken": 4}

    def test_a_missing_reason_is_announced_as_fail_open(self, tmp_path) -> None:
        harness, _, _ = _run(tmp_path, width=1, per_route_round_quota_enabled=True)
        harness._audit_route_termination(
            [
                RouteTrack(
                    route_id="r_blank",
                    source="planned",
                    status="stopped_stagnant",
                    termination_reason="",
                )
            ]
        )

        emitted = [
            event
            for event in _events(tmp_path)
            if event["event_type"] == "route_termination_reason_missing"
        ]
        assert len(emitted) == 1
        assert emitted[0]["route_id"] == "r_blank"
        assert emitted[0]["fail_open"] is True

    def test_a_route_still_racing_is_named_without_being_penalised(self, tmp_path) -> None:
        harness, _, _ = _run(tmp_path, width=1, per_route_round_quota_enabled=True)

        audit = harness._audit_route_termination(
            [RouteTrack(route_id="r_open", source="planned", status=TRACK_RACING)]
        )

        assert audit["missing"] == [{"route_id": "r_open", "status": TRACK_RACING}]
        assert audit["documented"] == []
        assert audit["policy"] == "fail_open"


# ---------------------------------------------------------------------------
# 5. A halted run closes the routes it was carrying
# ---------------------------------------------------------------------------


class TestAHaltedRun:
    def test_bystander_routes_are_closed_with_a_reason(self, tmp_path) -> None:
        """The whole run stops on out-of-domain physics; the others get closed.

        Left alone they keep the status "racing", which a reader takes to mean
        the route is still going, and they carry no end reason at all.
        """

        harness, result, _ = _run(
            tmp_path,
            width=3,
            outside_domain_at=1,
            per_route_round_quota_enabled=True,
        )

        statuses = {
            route_id: track.status
            for route_id, track in harness.tournament_tracks.items()
        }
        assert "error_unrecoverable" in statuses.values()
        assert TRACK_RACING not in statuses.values()
        halted = [
            track
            for track in harness.tournament_tracks.values()
            if track.status == TRACK_STOPPED_RUN_HALTED
        ]
        assert halted
        for track in halted:
            assert "the run halted" in track.termination_reason
        assert result.status in {"needs_higher_fidelity", "completed"}

    def test_a_halted_run_still_documents_every_route(self, tmp_path) -> None:
        _run(
            tmp_path,
            width=3,
            outside_domain_at=1,
            per_route_round_quota_enabled=True,
        )

        audit = _artifact(tmp_path, "ROUTE_TERMINATION_AUDIT.json")
        assert audit["routes_checked"] == 3
        assert audit["missing"] == []
        assert sorted(audit["documented"]) == ["r1", "r2", "r3"]
