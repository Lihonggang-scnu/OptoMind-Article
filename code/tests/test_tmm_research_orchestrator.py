from __future__ import annotations

from types import SimpleNamespace

import pytest

from optomind_optics.harness.dev_fixtures import build_dev_optical_design_task
from optomind_optics.harness.research_orchestrator import (
    TMMResearchHarness,
    TMMResearchHarnessConfig,
)


class _Analyzer:
    def analyze(self, question, force_mock=None):
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
    def __init__(self):
        self.calls = 0
        self.kwargs = []

    def research(self, problem, **kwargs):
        self.calls += 1
        self.kwargs.append(dict(kwargs))
        return {
            "status": "completed",
            "report": {
                "problem_id": problem["problem_id"],
                "queries": [],
                "evidence": [
                    {
                        "evidence_id": "ev1",
                        "paper_id": "paper1",
                        "title": "Method paper",
                        "doi": "10.1/test",
                        "year": 2024,
                        "source_route": "local_kb",
                        "content_depth": "fulltext",
                        "allowed_use": "method_guidance",
                    }
                ],
                "method_findings": [
                    {"name": "periodic stack", "reusable_principle": "alternate indices"}
                ],
                "unresolved_questions": [],
                "telemetry": {},
            },
        }


def _route(route_id, request):
    return {
        "route_id": route_id,
        "title": route_id,
        "route_kind": "periodic_stack",
        "scientific_hypothesis": "Alternating indices form a stop band.",
        "design_principle": "Use interference.",
        "proposed_materials": ["SiO2", "TiO2"],
        "proposed_topology": "alternating stack",
        "design_variables": ["thickness"],
        "soft_objectives": ["maximize reflectance"],
        "manufacturing_considerations": [],
        "evidence_ids": ["ev1"],
        "theory_basis": [],
        "expected_advantages": [],
        "known_risks": [],
        "execution_request_english": request,
        "priority": 1,
        "parent_route_id": None,
        "revision_reason": None,
    }


class _Planner:
    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.calls = 0

    def plan(self, problem, research, **kwargs):
        routes = self.rounds[min(self.calls, len(self.rounds) - 1)]
        self.calls += 1
        return {
            "status": "planned",
            "plan": {
                "problem_id": "p1",
                "planning_summary": "bounded routes",
                "routes": routes,
                "research_influence": [],
                "unresolved_decisions": [],
                "stop_if_all_routes_fail": "return best effort",
            },
            "usage": [],
        }


class _Compiler:
    def compile(self, question, force_mock=None):
        return SimpleNamespace(
            status="compiled",
            task=build_dev_optical_design_task("DEV01"),
            usage=(),
            model_dump=lambda mode="json": {
                "status": "compiled",
                "task": build_dev_optical_design_task("DEV01").model_dump(mode="json"),
                "usage": [],
            },
        )


class _BenchmarkCompiler:
    def __init__(self, benchmark_id):
        self.benchmark_id = benchmark_id

    def compile(self, question, force_mock=None):
        del question, force_mock
        task = build_dev_optical_design_task(self.benchmark_id)
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


def _run_payload(valid=True, score=0.7):
    candidates = []
    selected = {}
    if valid:
        candidates = [
            {
                "candidate_id": "candidate",
                "physically_admissible": True,
                "target_score": score,
                "robustness_score": 0.6,
                "simplicity_score": 0.8,
                "metadata": {"thicknesses_nm": [100.0, 200.0]},
                "artifact_ids": [],
            }
        ]
        selected = {"best_target_score": "candidate"}
    return {
        "status": "completed" if valid else "failed",
        "experiment_results": [
            {
                "experiment_id": "e1",
                "mode": "optimize",
                "physically_valid_candidate_count": int(valid),
                "portfolio": {"candidates": candidates, "selected_roles": selected},
            }
        ],
    }


class _Harness:
    def __init__(self, payload):
        self.payload = payload

    def run(self, task):
        return self.payload


def test_two_planned_routes_are_executed_and_reported(tmp_path):
    payloads = iter([_run_payload(True, 0.7), _run_payload(True, 0.72)])
    result = TMMResearchHarness(
        tmp_path / "run",
        problem_analyzer=_Analyzer(),
        method_researcher=_Researcher(),
        strategy_planner=_Planner(
            [[_route("r1", "request one"), _route("r2", "request two")]]
        ),
        task_compiler=_Compiler(),
        tmm_harness_factory=lambda path, run_id: _Harness(next(payloads)),
        config=TMMResearchHarnessConfig(maximum_refinement_rounds=0),
    ).run("Design a reflector")
    assert result.status == "completed"
    assert len(result.iterations) == 2
    assert result.final_answer.recommended_candidates
    assert (tmp_path / "run" / "FINAL_ANSWER.md").exists()
    assert result.telemetry["performance_targets_used_as_gates"] is False


def test_failed_chain_leaves_race_recorded_and_candidates_preserved(tmp_path):
    """R-06 equivalent of the deleted
    test_failed_route_triggers_research_and_novel_replan.

    The old flow answered a failed route with GLOBAL method re-research plus a
    planner-emitted novel route -- exhaustive enumeration across the queue.
    The tournament scheduler replaced that transition: failures are handled
    per-chain and recorded, the rest of the portfolio keeps racing, and
    verified candidates are preserved. Global re-research is no longer a
    scheduler step; this is asserted explicitly so the semantic change stays
    visible instead of silently disappearing."""
    import json

    researcher = _Researcher()
    planner = _Planner(
        [[_route("r1", "failing request"), _route("r2", "winning request")]]
    )
    payloads = iter([_run_payload(False), _run_payload(True, 0.65)])
    harness = TMMResearchHarness(
        tmp_path / "run",
        problem_analyzer=_Analyzer(),
        method_researcher=researcher,
        strategy_planner=planner,
        task_compiler=_Compiler(),
        tmm_harness_factory=lambda path, run_id: _Harness(next(payloads)),
        config=TMMResearchHarnessConfig(maximum_refinement_rounds=0),
    )
    result = harness.run("Design a reflector")
    # No global re-research round: failures are chain-local now.
    assert researcher.calls == 1
    # Both portfolio members executed exactly once.
    assert len(result.iterations) == 2
    # The surviving chain's verified candidates are reported.
    assert result.iterations[-1].physically_valid_candidate_count == 1
    assert result.status == "completed"
    # The failed chain LEFT THE RACE and the leave is on the record.
    assert harness.tournament_tracks["r1"].status != "racing"
    state = json.loads(
        (tmp_path / "run" / "TOURNAMENT_STATE.json").read_text(encoding="utf-8")
    )
    statuses = {t["route_id"]: t["status"] for t in state["tracks"]}
    assert statuses["r1"] != "racing"
    assert statuses["r2"] in {
        "racing",
        "stopped_llm_advice",
        "stopped_stagnant",
        "stopped_round_limit",
    }


def test_incompatible_problem_stops_before_research(tmp_path):
    class Incompatible(_Analyzer):
        def analyze(self, question, force_mock=None):
            result = super().analyze(question, force_mock)
            result["analysis"]["compatibility"] = "incompatible"
            result["analysis"]["compatibility_reason"] = "lateral grating"
            return result

    researcher = _Researcher()
    result = TMMResearchHarness(
        tmp_path / "run",
        problem_analyzer=Incompatible(),
        method_researcher=researcher,
        strategy_planner=_Planner([[_route("r1", "x")]]),
        task_compiler=_Compiler(),
    ).run("Design a grating")
    assert result.status == "needs_higher_fidelity"
    assert researcher.calls == 0


def test_research_loop_reaches_real_tmm_optimizer_and_physics_verifier(tmp_path):
    """One bounded integration proves the outer loop uses the real TMM kernel."""

    result = TMMResearchHarness(
        tmp_path / "run",
        problem_analyzer=_Analyzer(),
        method_researcher=_Researcher(),
        strategy_planner=_Planner(
            [[_route("r1", "Optimize a single-layer antireflection coating.")]]
        ),
        task_compiler=_Compiler(),
        config=TMMResearchHarnessConfig(
            maximum_initial_routes=1,
            maximum_iterations=1,
            maximum_refinement_rounds=0,
            online_method_research=False,
        ),
    ).run("Design a single-layer antireflection coating")
    assert result.status == "completed"
    assert result.iterations[0].physically_valid_candidate_count >= 1
    assert result.telemetry["optimizer_runs"] >= 1
    assert result.telemetry["forward_evaluations"] >= 1
    assert (tmp_path / "run" / "iterations" / "iteration_01" / "tmm_run" / "FINAL_RESULT.json").exists()


def test_compiler_exception_records_failure_and_completes_best_effort(tmp_path):
    """R-06 equivalent of the deleted
    test_compiler_exception_becomes_feedback_and_scientific_research_query.

    A compilation exception is an ENGINE fault, never a physics refutation:
    the chain must leave the race as error_unrecoverable (red line 7), the
    failure stays fully recorded, and the run still completes best-effort.
    The old global re-research/replan recovery is not part of the tournament
    scheduler; repair belongs to the engine layer (R-03 diagnostics)."""
    import json

    class FlakyCompiler(_Compiler):
        def __init__(self):
            self.calls = 0

        def compile(self, question, force_mock=None):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary compiler failure")
            return super().compile(question, force_mock)

    researcher = _Researcher()
    planner = _Planner([[_route("r1", "first request")]])
    harness = TMMResearchHarness(
        tmp_path / "run",
        problem_analyzer=_Analyzer(),
        method_researcher=researcher,
        strategy_planner=planner,
        task_compiler=FlakyCompiler(),
        tmm_harness_factory=lambda path, run_id: _Harness(_run_payload(True, 0.66)),
        config=TMMResearchHarnessConfig(maximum_refinement_rounds=0),
    )
    result = harness.run("Design a reflector")

    # The failure is recorded on the observation...
    assert result.iterations[0].compilation_status == "unavailable"
    # ...no silent retry happened at the scheduler layer...
    assert researcher.calls == 1
    # ...and the run still terminates best-effort instead of crashing.
    assert result.status == "completed_best_effort_no_verified_candidate"
    track = harness.tournament_tracks["r1"]
    assert track.status == "error_unrecoverable"
    assert track.status != "eliminated_physics"
    assert "compilation failed" in track.termination_reason.casefold()
    decision = json.loads(
        (tmp_path / "run" / "iterations" / "iteration_01" / "FEEDBACK_DECISION.json")
        .read_text(encoding="utf-8")
    )
    assert decision["action"] == "stop_best_effort"
    assert "error_unrecoverable" in decision["reason"]


def test_tmm_execution_exception_is_recorded_and_next_route_continues(tmp_path):
    class RaisingHarness:
        def run(self, task):
            raise RuntimeError("solver process crashed")

    harnesses = iter([RaisingHarness(), _Harness(_run_payload(True, 0.64))])
    result = TMMResearchHarness(
        tmp_path / "run",
        problem_analyzer=_Analyzer(),
        method_researcher=_Researcher(),
        strategy_planner=_Planner(
            [[_route("r1", "first request"), _route("r2", "second request")]]
        ),
        task_compiler=_Compiler(),
        tmm_harness_factory=lambda path, run_id: next(harnesses),
        config=TMMResearchHarnessConfig(maximum_refinement_rounds=0),
    ).run("Design a reflector")

    assert result.status == "completed"
    assert result.iterations[0].run_status == "failed"
    assert "runtime_environment" in result.iterations[0].failure_categories
    assert result.iterations[1].physically_valid_candidate_count == 1
    assert (
        tmp_path
        / "run"
        / "iterations"
        / "iteration_01"
        / "tmm_run"
        / "EXECUTION_ERROR.json"
    ).exists()


@pytest.mark.parametrize("benchmark_id", ["DEV01", "DEV02", "DEV03", "DEV04", "DEV05"])
def test_full_research_loop_generalizes_across_frozen_development_tasks(
    tmp_path, benchmark_id
):
    """All exposed intents reach the same verified TMM environment contract."""

    task = build_dev_optical_design_task(benchmark_id)
    route_kind = {
        "DEV01": "optimize_existing_stack",
        "DEV02": "periodic_stack",
        "DEV03": "defect_cavity",
        "DEV04": "absorber_emitter",
        "DEV05": "mixed_coherence_stack",
    }[benchmark_id]
    route = _route(
        f"route_{benchmark_id.lower()}",
        f"Execute the frozen {benchmark_id} planar multilayer experiment.",
    )
    route["route_kind"] = route_kind
    result = TMMResearchHarness(
        tmp_path / benchmark_id,
        problem_analyzer=_Analyzer(),
        method_researcher=_Researcher(),
        strategy_planner=_Planner([[route]]),
        task_compiler=_BenchmarkCompiler(benchmark_id),
        config=TMMResearchHarnessConfig(
            maximum_initial_routes=1,
            maximum_iterations=1,
            maximum_refinement_rounds=0,
            online_method_research=False,
        ),
    ).run(task.user_request_original)

    assert result.status == "completed"
    assert result.iterations[0].physically_valid_candidate_count >= 1
    assert result.telemetry["forward_evaluations"] >= 1
    assert result.telemetry["performance_targets_used_as_gates"] is False
    assert result.final_answer is not None
    assert result.final_answer.recommended_candidates
