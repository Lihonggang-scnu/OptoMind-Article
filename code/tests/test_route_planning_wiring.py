"""Literature-driven route planning, as it behaves inside a whole research run.

`test_route_planning.py` covers the mechanism in isolation.  What can still
fail once it is wired in is the plumbing and the ordering: the axes have to be
chosen before any experiment exists, the model's own count of them has to
survive into the race instead of being trimmed back to a configured width, the
provenance has to reach the comparison table, both of the stage's model calls
have to be paid for out of the run's meter, and every way this stage can fail
has to leave a finishing run behind rather than no run at all.

Those are the properties here.  Every model and every search provider in this
file is scripted; nothing reaches the network.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict, List, Mapping, Sequence

import pytest

from optomind_optics.harness import problem_analyzer as problem_analyzer_module
from optomind_optics.harness import research_orchestrator as orchestrator_module
from optomind_optics.harness import route_planning as route_planning_module
from optomind_optics.harness.dev_fixtures import build_dev_optical_design_task
from optomind_optics.harness.research_orchestrator import (
    TMMResearchHarness,
    TMMResearchHarnessConfig,
)
from optomind_optics.harness.route_planning import QwenLiteratureRoutePlanner


QUESTION = "Reflect 300-800 nm and absorb the 5-13 um window."
QUERY = "dual band selective multilayer coating design"


# ---------------------------------------------------------------------------
# Stand-ins for the stages a run drives.
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


def _legacy_route(route_id: str) -> dict:
    """A route as the strategy planner emits it -- the fallback portfolio."""

    return {
        "route_id": route_id,
        "title": route_id,
        "route_kind": "periodic_stack",
        "scientific_hypothesis": "Alternating indices form a stop band.",
        "design_principle": "Use interference.",
        "proposed_materials": ["SiO2", "TiO2"],
        # Distinct per route so the two never collapse as one axis.
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
    """The legacy planner: two routes, and a record of whether it was used."""

    def __init__(self) -> None:
        self.calls = 0

    def plan(self, problem, research, **kwargs):
        del problem, research, kwargs
        self.calls += 1
        return {
            "status": "planned",
            "plan": {
                "problem_id": "p1",
                "planning_summary": "two independent axes",
                "routes": [_legacy_route("r1"), _legacy_route("r2")],
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


class _Harness:
    """A VeriTMM stand-in that records when it ran, relative to planning."""

    def __init__(self, payload: dict, journal: List[str] | None = None) -> None:
        self.payload = payload
        self.journal = journal

    def run(self, task):
        del task
        if self.journal is not None:
            self.journal.append("execute")
        return self.payload


def _payload(score: float = 0.7) -> dict:
    candidate = {
        "candidate_id": "candidate",
        "physically_admissible": True,
        "target_score": score,
        "robustness_score": 0.6,
        "simplicity_score": 0.8,
        "metadata": {"thicknesses_nm": [100.0, 200.0]},
        "artifact_ids": [],
    }
    return {
        "status": "completed",
        "experiment_results": [
            {
                "experiment_id": "e1",
                "mode": "optimize",
                "physically_valid_candidate_count": 1,
                "portfolio": {
                    "candidates": [candidate],
                    "selected_roles": {"best_target_score": "candidate"},
                },
            }
        ],
    }


# ---------------------------------------------------------------------------
# Scripted planning stage
# ---------------------------------------------------------------------------


class _ScriptedClient:
    """Answers the two planning stages from a script; records both calls."""

    model_name = "scripted-plus"

    def __init__(self, replies: Sequence[Any], journal: List[str] | None = None) -> None:
        self.replies = list(replies)
        self.calls: List[List[Dict[str, str]]] = []
        self.journal = journal

    def call(self, messages, *, max_tokens=4000, force_mock=None):
        del max_tokens, force_mock
        self.calls.append([dict(message) for message in messages])
        if self.journal is not None:
            self.journal.append("plan")
        reply = self.replies.pop(0) if self.replies else {}
        if isinstance(reply, Exception):
            raise reply
        content = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        return {
            "content": content,
            "_llm_usage": {
                "model_name": self.model_name,
                "input_tokens": 120,
                "output_tokens": 40,
                "total_tokens": 160,
            },
        }


class _ScriptedLiterature:
    def __init__(self, papers: Sequence[Mapping[str, Any]] = (), *, error: Exception | None = None) -> None:
        self.papers = [dict(paper) for paper in papers]
        self.error = error
        self.requests: List[tuple[str, int]] = []

    def search_papers(self, query: str, *, limit: int) -> Any:
        self.requests.append((query, limit))
        if self.error is not None:
            raise self.error
        return list(self.papers)


def _paper(index: int) -> Dict[str, Any]:
    return {
        "paper_id": f"S2-{index:03d}",
        "doi": "",
        "title": f"Multilayer selective coating study {index}",
        "abstract": f"Abstract {index} about a distributed Bragg reflector.",
        "year": 2010 + index,
        "venue": "Optics Express",
        "authors": [{"name": f"Author {index}"}],
        "citation_count": index,
    }


def _planned_route(index: int, *, variables: Sequence[str] = ("layer thicknesses",), drop: Sequence[str] = ()) -> Dict[str, Any]:
    route: Dict[str, Any] = {
        "route_id": f"route_{index:02d}",
        "title": f"Axis {index}",
        "route_kind": "periodic_stack",
        "scientific_hypothesis": (
            f"Varying the axis of route {index} moves best_target_score toward the "
            "quarter-wave limit."
        ),
        "design_principle": "Quarter-wave stacking opens a stop band.",
        "proposed_materials": ["TiO2", "SiO2"],
        # Index-dependent, so N proposed routes really are N distinct axes.
        "proposed_topology": (
            f"Si substrate | {6 + index} pairs of TiO2/SiO2 | air, "
            f"{13 + 2 * index} finite layers"
        ),
        "design_variables": list(variables),
        "soft_objectives": ["high mean reflectance across 300-800nm"],
        "manufacturing_considerations": ["keep the finite layer count below 24"],
        "evidence_ids": ["L01"],
        "theory_basis": ["transfer matrix method for isotropic layered media"],
        "expected_advantages": ["a wide stop band with few layers"],
        "known_risks": ["thickness errors shift the band edge"],
        "execution_request_english": (
            f"Optimize the layer thicknesses of a TiO2/SiO2 stack on a silicon "
            f"substrate for route {index}, reporting mean reflectance over "
            "300-800nm and mean absorption over 5-13um."
        ),
        "priority": index,
        "parent_route_id": None,
        "revision_reason": None,
        "expected_observations": [
            "best_target_score should rise across rounds while tightest_margin holds"
        ],
        "stop_conditions": [
            "if best_target_score improves by less than 1e-3 for two rounds, stop"
        ],
    }
    for key in drop:
        route.pop(key, None)
    return route


def _plan_reply(routes: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    return {
        "problem_id": "dual-band-selective-coating",
        "planning_summary": "One axis per independent design decision.",
        "routes": [dict(route) for route in routes],
        "research_influence": ["L01 motivated the periodic stack"],
        "unresolved_decisions": ["whether the infrared band needs its own absorber"],
        "stop_if_all_routes_fail": "Report the best effort and record the axes tried.",
    }


def _query_reply(queries: Sequence[str] = (QUERY,)) -> Dict[str, Any]:
    return {"queries": list(queries), "rationale": "from the two bands the request names"}


def _scripted_planner(
    routes: Sequence[Mapping[str, Any]] | Sequence[Sequence[Mapping[str, Any]]],
    *,
    papers: Sequence[Mapping[str, Any]] | None = None,
    literature_error: Exception | None = None,
    journal: List[str] | None = None,
    **kwargs: Any,
) -> tuple[QwenLiteratureRoutePlanner, _ScriptedClient, _ScriptedLiterature]:
    """A real planner over a scripted model and a scripted search provider."""

    attempts: List[Sequence[Mapping[str, Any]]] = (
        list(routes) if routes and isinstance(routes[0], (list, tuple)) else [routes]  # type: ignore[arg-type]
    )
    replies: List[Any] = [_query_reply()]
    replies.extend(_plan_reply(attempt) for attempt in attempts)
    client = _ScriptedClient(replies, journal=journal)
    literature = _ScriptedLiterature(
        papers if papers is not None else [_paper(1), _paper(2)],
        error=literature_error,
    )
    planner = QwenLiteratureRoutePlanner(client, literature_client=literature, **kwargs)
    return planner, client, literature


# ---------------------------------------------------------------------------
# Run driver
# ---------------------------------------------------------------------------


def _run(
    tmp_path,
    *,
    planner: Any | None = None,
    literature_client: Any | None = None,
    enabled: bool = True,
    journal: List[str] | None = None,
    strategy_planner: Any | None = None,
    **config: Any,
):
    settings = {
        "maximum_refinement_rounds": 0,
        "route_planning_enabled": enabled,
        # The route reflection client is built by the harness itself, so mock
        # mode is the only way to keep this file off the network.
        "qwen_force_mock": True,
    }
    settings.update(config)
    harness = TMMResearchHarness(
        tmp_path / "run",
        problem_analyzer=_Analyzer(),
        method_researcher=_Researcher(),
        strategy_planner=strategy_planner if strategy_planner is not None else _Planner(),
        task_compiler=_Compiler(),
        route_planner=planner,
        route_literature_client=literature_client,
        tmm_harness_factory=lambda path, run_id: _Harness(_payload(), journal),
        config=TMMResearchHarnessConfig(**settings),
    )
    return harness, harness.run(QUESTION)


def _artifact(tmp_path, name: str) -> dict:
    return json.loads((tmp_path / "run" / name).read_text(encoding="utf-8"))


def _exists(tmp_path, name: str) -> bool:
    return (tmp_path / "run" / name).exists()


def _events(tmp_path) -> List[dict]:
    path = tmp_path / "run" / "RESEARCH_EVENTS.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ---------------------------------------------------------------------------
# 1. The switch is on, and turning it off restores the legacy planner
# ---------------------------------------------------------------------------


class TestTheSwitch:
    def test_on_by_default(self) -> None:
        # Literature planning is the intended path: with it off the portfolio
        # width comes from a configuration number rather than from the problem.
        assert TMMResearchHarnessConfig().route_planning_enabled is True

    def test_off_leaves_the_legacy_planner_in_charge(self, tmp_path) -> None:
        legacy = _Planner()
        planner, client, literature = _scripted_planner([_planned_route(1)])
        harness, result = _run(
            tmp_path, planner=planner, strategy_planner=legacy, enabled=False
        )

        assert result.status == "completed"
        assert harness.route_plan_result is None
        # Nothing about the stage ran: no model call, no search, no artifact.
        assert client.calls == []
        assert literature.requests == []
        assert not _exists(tmp_path, "ROUTE_PLANNING.json")
        # At least once, for the initial portfolio; the same planner is also
        # the in-loop improver, so the count is not 1.  The route ids are the
        # evidence of WHICH stage planned: this planner names them r1/r2, the
        # literature stage renumbers to route_01/route_02.
        assert legacy.calls >= 1
        assert sorted(harness.tournament_tracks) == ["r1", "r2"]


# ---------------------------------------------------------------------------
# 2. The model's count of axes is the run's width
# ---------------------------------------------------------------------------


class TestTheCountSurvivesIntoTheRace:
    def test_three_planned_routes_race_three_tracks(self, tmp_path) -> None:
        """The configured width must not trim the planner's answer.

        This is the whole point of the stage: `maximum_initial_routes` is 2
        here, and a run that silently dropped the third axis would be a run
        that can never reach it, because iterating inside two axes does not
        discover a third.
        """

        planner, _, _ = _scripted_planner(
            [_planned_route(1), _planned_route(2), _planned_route(3)]
        )
        harness, result = _run(
            tmp_path, planner=planner, maximum_initial_routes=2, maximum_iterations=6
        )

        assert result.status == "completed"
        assert sorted(harness.tournament_tracks) == ["route_01", "route_02", "route_03"]

    def test_one_route_is_a_legitimate_plan(self, tmp_path) -> None:
        """A single-axis problem gets one route, and the run still finishes."""

        planner, _, _ = _scripted_planner([_planned_route(1)])
        harness, result = _run(tmp_path, planner=planner)

        assert result.status == "completed"
        assert list(harness.tournament_tracks) == ["route_01"]
        assert _artifact(tmp_path, "ROUTE_PLANNING.json")["route_count"] == 1

    def test_the_ceiling_is_enforced_end_to_end(self, tmp_path) -> None:
        planner, _, _ = _scripted_planner(
            [_planned_route(1), _planned_route(2), _planned_route(3)],
            maximum_routes=2,
        )
        harness, result = _run(tmp_path, planner=planner)

        assert result.status == "completed"
        assert sorted(harness.tournament_tracks) == ["route_01", "route_02"]
        envelope = _artifact(tmp_path, "ROUTE_PLANNING.json")
        assert envelope["route_count"] == 2
        assert any("route" in warning for warning in envelope["warnings"])

    def test_the_planned_routes_reach_the_strategy_plan_artifact(self, tmp_path) -> None:
        planner, _, _ = _scripted_planner([_planned_route(1), _planned_route(2)])
        _run(tmp_path, planner=planner)

        envelope = _artifact(tmp_path, "STRATEGY_PLAN.json")
        assert envelope["planning_mechanism"] == "literature_route_planning"
        assert [route["route_id"] for route in envelope["plan"]["routes"]] == [
            "route_01",
            "route_02",
        ]


# ---------------------------------------------------------------------------
# 3. The plan is recorded, attested, and dated before any result exists
# ---------------------------------------------------------------------------


class TestTheRecord:
    def test_the_artifact_records_the_mechanism_and_its_inputs(self, tmp_path) -> None:
        planner, _, _ = _scripted_planner([_planned_route(1), _planned_route(2)])
        _, result = _run(tmp_path, planner=planner)

        envelope = _artifact(tmp_path, "ROUTE_PLANNING.json")
        assert envelope["schema_version"] == "tmm-route-planning.v1"
        assert envelope["status"] == "planned"
        assert envelope["planning_mechanism"] == "literature_route_planning"
        assert envelope["route_count"] == 2
        assert envelope["queries"]["queries"] == [QUERY]
        assert envelope["literature"]["status"] == "harvested"
        assert len(envelope["literature"]["papers"]) == 2
        assert "ROUTE_PLANNING.json" in result.artifacts

    def test_the_plan_is_attested_so_its_timing_is_checkable(self, tmp_path) -> None:
        import hashlib

        planner, _, _ = _scripted_planner([_planned_route(1), _planned_route(2)])
        _run(tmp_path, planner=planner)

        attestation = _artifact(tmp_path, "ROUTE_PLANNING.ATTESTATION.json")
        assert attestation["artifact_kind"] == "pre_execution_route_plan"
        assert attestation["route_count"] == 2
        assert attestation["route_ids"] == ["route_01", "route_02"]
        assert attestation["literature_status"] == "harvested"
        assert attestation["papers"] == 2
        assert attestation["question_digest"]
        # The hash is of the bytes actually written, which is what makes a
        # later edit to the plan detectable rather than merely discouraged.
        raw = (tmp_path / "run" / "ROUTE_PLANNING.json").read_bytes()
        assert attestation["artifact_sha256"] == hashlib.sha256(raw).hexdigest()

    def test_planning_happens_before_any_experiment_runs(self, tmp_path) -> None:
        """The claim "these axes predate the results" has to be true in order."""

        journal: List[str] = []
        planner, _, _ = _scripted_planner(
            [_planned_route(1), _planned_route(2)], journal=journal
        )
        _run(tmp_path, planner=planner, journal=journal)

        assert "execute" in journal
        assert journal.index("execute") > journal.index("plan")
        # Both model stages, then execution -- not interleaved with it.
        assert journal[:2] == ["plan", "plan"]

    def test_the_stage_announces_itself_in_the_event_stream(self, tmp_path) -> None:
        planner, _, _ = _scripted_planner([_planned_route(1), _planned_route(2)])
        _run(tmp_path, planner=planner)

        planned = [
            event
            for event in _events(tmp_path)
            if event["event_type"] == "routes_planned_from_literature"
        ]
        assert len(planned) == 1
        assert planned[0]["route_count"] == 2
        assert planned[0]["papers"] == 2
        assert planned[0]["queries"] == 1
        assert planned[0]["literature_status"] == "harvested"


# ---------------------------------------------------------------------------
# 4. Both model calls come out of the run's own meter
# ---------------------------------------------------------------------------


class TestMetering:
    def test_both_stages_are_charged_to_the_run(self, tmp_path) -> None:
        """Measured as a difference, so the other stages' calls cancel out."""

        planner, client, _ = _scripted_planner([_planned_route(1)])
        _, planned = _run(tmp_path / "on", planner=planner)
        _, baseline = _run(tmp_path / "off", enabled=False)

        assert len(client.calls) == 2
        extra = planned.telemetry["qwen_calls"] - baseline.telemetry["qwen_calls"]
        assert extra >= 2
        assert planned.telemetry["qwen_input_tokens"] >= 240
        assert planned.telemetry["qwen_output_tokens"] >= 80

    def test_the_usage_rows_are_recorded_in_the_artifact(self, tmp_path) -> None:
        planner, _, _ = _scripted_planner([_planned_route(1)])
        _run(tmp_path, planner=planner)

        envelope = _artifact(tmp_path, "ROUTE_PLANNING.json")
        # Written explicitly, because the combined `usage` is a property and a
        # caller metering from the serialised form would otherwise see nothing.
        assert len(envelope["queries"]["usage"]) == 1
        assert len(envelope["planning_usage"]) == 1


# ---------------------------------------------------------------------------
# 5. Provenance reaches the comparison table
# ---------------------------------------------------------------------------


class TestProvenance:
    def test_every_planned_route_is_marked_in_the_tournament_summary(self, tmp_path) -> None:
        planner, _, _ = _scripted_planner([_planned_route(1), _planned_route(2)])
        _run(tmp_path, planner=planner)

        summary = _artifact(tmp_path, "TOURNAMENT_SUMMARY.json")
        sources = {row["route_id"]: row["source"] for row in summary["route_comparison"]}
        assert sources == {
            "route_01": "literature_planned",
            "route_02": "literature_planned",
        }

    def test_the_legacy_path_keeps_its_own_marker(self, tmp_path) -> None:
        _run(tmp_path, enabled=False)

        summary = _artifact(tmp_path, "TOURNAMENT_SUMMARY.json")
        assert {row["source"] for row in summary["route_comparison"]} == {"planned"}


# ---------------------------------------------------------------------------
# 6. The configuration reaches the planner the harness builds for itself
# ---------------------------------------------------------------------------


class TestConfigurationReachesTheStage:
    @staticmethod
    def _patch(monkeypatch, recorded: dict, *, default_raises: Exception | None = None):
        """Replace the two objects the harness constructs on its own."""

        class _Adapter:
            model_name = "patched-plus"

            def __init__(self, *, role: str = "plus") -> None:
                recorded["role"] = role

            def call(self, messages, *, max_tokens=4000, force_mock=None):
                del messages, max_tokens, force_mock
                return {"content": "", "_llm_usage": {"total_tokens": 0}}

        class _Recorder:
            def __init__(self, client, **kwargs: Any) -> None:
                recorded["client"] = client
                recorded.update(kwargs)

            def plan(self, question, *, problem_analysis=None, force_mock=None):
                del question, problem_analysis
                recorded["force_mock"] = force_mock
                raise RuntimeError("construction is what this test inspects")

        class _DefaultLiterature:
            def __init__(self, **kwargs: Any) -> None:
                if default_raises is not None:
                    raise default_raises
                recorded["default_built"] = True

        monkeypatch.setattr(
            problem_analyzer_module, "ArticlePlusQwenClient", _Adapter
        )
        monkeypatch.setattr(
            orchestrator_module, "QwenLiteratureRoutePlanner", _Recorder
        )
        monkeypatch.setattr(
            route_planning_module, "DefaultRouteLiteratureClient", _DefaultLiterature
        )

    def test_the_configured_limits_reach_the_planner(self, tmp_path, monkeypatch) -> None:
        recorded: dict = {}
        self._patch(monkeypatch, recorded)
        literature = _ScriptedLiterature([_paper(1)])

        _, result = _run(
            tmp_path,
            literature_client=literature,
            route_planning_literature_limit=12,
            route_planning_maximum_routes=4,
        )

        assert recorded["literature_limit"] == 12
        assert recorded["maximum_routes"] == 4
        # The injected provider is preferred over the default one.
        assert recorded["literature_client"] is literature
        assert "default_built" not in recorded
        assert recorded["force_mock"] is True
        assert recorded["role"] == "plus"
        # And the failure this recorder raises still leaves a finished run.
        assert result.status == "completed"

    def test_the_configured_limit_reaches_the_search_provider(self, tmp_path) -> None:
        """With a real planner, the limit is what the provider is asked for."""

        planner, _, literature = _scripted_planner(
            [_planned_route(1)], literature_limit=12
        )
        _run(tmp_path, planner=planner)

        assert literature.requests == [(QUERY, 12)]

    def test_an_unavailable_search_gateway_does_not_lose_the_stage(
        self, tmp_path, monkeypatch
    ) -> None:
        """No key pool and no network: plan from theory, and say so."""

        recorded: dict = {}
        self._patch(monkeypatch, recorded, default_raises=RuntimeError("no key pool"))

        _, result = _run(tmp_path)

        assert recorded["literature_client"] is None
        reasons = [
            event["reason"]
            for event in _events(tmp_path)
            if event["event_type"] == "route_literature_unavailable"
        ]
        assert reasons and "no key pool" in reasons[0]
        assert result.status == "completed"


# ---------------------------------------------------------------------------
# 7. Every way this stage fails still leaves a finished run
# ---------------------------------------------------------------------------


class TestDegradation:
    def test_a_thrown_planner_falls_back_to_the_legacy_planner(self, tmp_path) -> None:
        class _Broken:
            def plan(self, question, *, problem_analysis=None, force_mock=None):
                del question, problem_analysis, force_mock
                raise RuntimeError("the planning service is down")

        legacy = _Planner()
        harness, result = _run(tmp_path, planner=_Broken(), strategy_planner=legacy)

        assert result.status == "completed"
        assert harness.route_plan_result is None
        envelope = _artifact(tmp_path, "ROUTE_PLANNING.json")
        assert envelope["status"] == "unavailable"
        assert envelope["planning_mechanism"] == "strategy_planner_fallback"
        assert any("the planning service is down" in row for row in envelope["validation_errors"])
        # The run still has a portfolio, planned the old way -- the r1/r2 ids
        # are the legacy planner's, not this stage's route_NN.
        assert legacy.calls >= 1
        assert sorted(harness.tournament_tracks) == ["r1", "r2"]

    def test_a_plan_whose_routes_all_fail_verification_falls_back(self, tmp_path) -> None:
        """Routes that never say what they tune are not strategy axes."""

        broken = _planned_route(1, drop=("design_variables",))
        planner, _, _ = _scripted_planner(
            [[broken], [broken], [broken]], maximum_attempts=3
        )
        legacy = _Planner()
        harness, result = _run(tmp_path, planner=planner, strategy_planner=legacy)

        assert result.status == "completed"
        assert harness.route_plan_result is None
        envelope = _artifact(tmp_path, "ROUTE_PLANNING.json")
        assert envelope["planning_mechanism"] == "strategy_planner_fallback"
        assert envelope["validation_errors"]
        assert legacy.calls >= 1
        assert sorted(harness.tournament_tracks) == ["r1", "r2"]

    def test_the_fallback_is_announced(self, tmp_path) -> None:
        class _Broken:
            def plan(self, question, *, problem_analysis=None, force_mock=None):
                del question, problem_analysis, force_mock
                raise RuntimeError("down")

        _run(tmp_path, planner=_Broken())

        types = [event["event_type"] for event in _events(tmp_path)]
        assert "route_planning_unavailable" in types
        assert "routes_planned_from_literature" not in types

    def test_a_failed_search_still_plans_from_theory(self, tmp_path) -> None:
        """Literature informs the axes; it is not a precondition for having any."""

        planner, _, literature = _scripted_planner(
            [_planned_route(1, variables=("layer thicknesses",)), _planned_route(2)],
            literature_error=RuntimeError("provider refused"),
        )
        harness, result = _run(tmp_path, planner=planner)

        assert result.status == "completed"
        assert sorted(harness.tournament_tracks) == ["route_01", "route_02"]
        envelope = _artifact(tmp_path, "ROUTE_PLANNING.json")
        assert envelope["planning_mechanism"] == "literature_route_planning"
        assert envelope["literature"]["status"] == "unavailable"
        assert envelope["literature"]["papers"] == []
        assert any("provider refused" in row for row in envelope["literature"]["errors"])
        assert literature.requests  # it was genuinely attempted

    def test_a_route_citing_papers_that_were_never_retrieved_still_runs(self, tmp_path) -> None:
        """A mislabelled reference is dropped; the physics is not discarded."""

        route = _planned_route(1)
        route["evidence_ids"] = ["L01", "L99"]
        planner, _, _ = _scripted_planner([route])
        harness, result = _run(tmp_path, planner=planner)

        assert result.status == "completed"
        assert list(harness.tournament_tracks) == ["route_01"]
        plan = _artifact(tmp_path, "ROUTE_PLANNING.json")["plan"]
        assert plan["routes"][0]["evidence_ids"] == ["L01"]


# ---------------------------------------------------------------------------
# 8. What the planner declared up front is kept for the reflection stage
# ---------------------------------------------------------------------------


class TestPreDeclarations:
    def test_the_declared_expectations_survive_into_the_run(self, tmp_path) -> None:
        planner, _, _ = _scripted_planner([_planned_route(1), _planned_route(2)])
        _run(tmp_path, planner=planner)

        declarations = _artifact(tmp_path, "ROUTE_PLANNING.json")["pre_declarations"]
        assert sorted(declarations) == ["route_01", "route_02"]
        for entry in declarations.values():
            assert entry["expected_observations"]
            assert entry["stop_conditions"]

    def test_the_declarations_are_not_smuggled_into_the_route_contract(self, tmp_path) -> None:
        """`DesignRoute` forbids extra fields, so they must travel separately."""

        planner, _, _ = _scripted_planner([_planned_route(1)])
        _run(tmp_path, planner=planner)

        route = _artifact(tmp_path, "ROUTE_PLANNING.json")["plan"]["routes"][0]
        assert "expected_observations" not in route
        assert "stop_conditions" not in route
