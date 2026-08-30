from __future__ import annotations

import json
import time
from types import SimpleNamespace

from optomind_optics.harness.dev_fixtures import build_dev_optical_design_task
from optomind_optics.harness.route_planning import (
    CONTROL_ROUTE_ID,
    LiteratureHarvest,
    QwenMemoryControlRoutePlanner,
    RoutePlanResult,
)
from optomind_optics.harness.research_orchestrator import (
    TMMResearchHarness,
    TMMResearchHarnessConfig,
)
from optomind_optics.harness.task_compiler import QwenTMMTaskCompiler


def _control_route_response() -> dict:
    return {
        "problem_id": "control-test",
        "planning_summary": "A memory-only periodic-stack control axis.",
        "knowledge_source_disclosure": (
            "No retrieved literature or method research was supplied."
        ),
        "routes": [
            {
                "route_id": CONTROL_ROUTE_ID,
                "title": "Memory-only periodic stack",
                "route_kind": "periodic_stack",
                "scientific_hypothesis": "A compact alternating stack can shape the requested band.",
                "design_principle": "Interference changes with layer thickness.",
                "proposed_materials": ["TiO2", "SiO2"],
                "proposed_topology": "Air | 4 finite TiO2/SiO2 layers | fused-silica substrate",
                "design_variables": ["finite layer thicknesses"],
                "soft_objectives": ["maximize the requested response"],
                "manufacturing_considerations": ["keep thicknesses positive"],
                "evidence_ids": [],
                "theory_basis": ["The transfer matrix depends on optical phase thickness."],
                "expected_advantages": ["A small stack is easy to compare."],
                "known_risks": ["The recalled material data may be incomplete."],
                "execution_request_english": (
                    "Optimize the finite layer thicknesses of a TiO2/SiO2 stack "
                    "on a fused-silica substrate over 500-600 nm."
                ),
                "priority": 1,
                "parent_route_id": None,
                "revision_reason": None,
                "expected_observations": ["The best target score should change after a useful revision."],
                "stop_conditions": ["If the score stagnates, stop and keep the best candidate."],
            }
        ],
        "unresolved_decisions": [],
        "stop_if_all_routes_fail": "Keep the recorded best effort.",
    }


class _ControlClient:
    model_name = "scripted-plus"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def call(self, messages, *, max_tokens=4000, force_mock=None):
        del max_tokens, force_mock
        self.calls.append(json.loads(messages[1]["content"]))
        return {
            "content": json.dumps(_control_route_response()),
            "_llm_usage": {"input_tokens": 10, "output_tokens": 10},
        }


def test_memory_control_never_receives_research_and_keeps_feedback_isolated() -> None:
    client = _ControlClient()
    planner = QwenMemoryControlRoutePlanner(client, material_catalog=None)
    problem = {
        "original_request": "Optimize a bounded coating over 500-600 nm.",
        "method_research_questions": ["secret research agenda"],
        "method_research": {"secret": "paper text"},
        "literature": {"papers": ["paper text"]},
    }

    first = planner.plan("Optimize a bounded coating over 500-600 nm.", problem_analysis=problem)
    second = planner.plan(
        "Optimize a bounded coating over 500-600 nm.",
        problem_analysis=problem,
        prior_iterations=[{"iteration_id": "iteration_01", "best_target_score": 0.4}],
        feedback_directives=["increase the useful band response"],
        chain_id=CONTROL_ROUTE_ID,
    )

    assert first.status == second.status == "planned"
    assert first.plan["routes"][0]["route_id"] == CONTROL_ROUTE_ID
    assert first.plan["routes"][0]["evidence_ids"] == []
    assert "literature" not in client.calls[0]
    assert "method_research" not in client.calls[0]
    assert "method_research_questions" not in client.calls[0]["problem_analysis"]
    assert "literature" not in client.calls[1]
    assert "method_research" not in client.calls[1]
    assert client.calls[1]["prior_iterations"][0]["best_target_score"] == 0.4
    assert client.calls[1]["feedback_directives"] == ["increase the useful band response"]


def test_harness_records_control_plan_and_adds_its_quota(tmp_path) -> None:
    planner = QwenMemoryControlRoutePlanner(_ControlClient(), material_catalog=None)
    harness = TMMResearchHarness(
        tmp_path,
        problem_analyzer=SimpleNamespace(),
        method_researcher=SimpleNamespace(),
        strategy_planner=SimpleNamespace(),
        control_route_planner=planner,
        config=TMMResearchHarnessConfig(
            control_route_enabled=True,
            maximum_iterations=6,
            max_rounds_per_route=6,
        ),
    )
    harness._started = time.perf_counter()
    result = harness._plan_control_route(
        "Optimize a bounded coating over 500-600 nm.",
        {"original_request": "Optimize a bounded coating over 500-600 nm."},
    )
    harness._allocate_round_quota(4)

    assert result is not None and result.status == "planned"
    assert (tmp_path / "CONTROL_ROUTE_PLANNING.json").exists()
    assert harness.control_route_plan_envelope["literature_client_invoked"] is False
    assert harness._iteration_ceiling == 24


class _StaticRoutePlanner:
    def plan(self, question, *, problem_analysis=None, force_mock=None):
        del question, problem_analysis, force_mock
        route = dict(_control_route_response()["routes"][0])
        route["route_id"] = "route_01"
        route["evidence_ids"] = []
        route.pop("expected_observations", None)
        route.pop("stop_conditions", None)
        return RoutePlanResult(
            status="planned",
            plan={"problem_id": "normal", "routes": [route]},
            pre_declarations={
                "route_01": {
                    "expected_observations": ["the score is measured"],
                    "stop_conditions": ["stop at the round cap"],
                }
            },
            route_count=1,
            literature=LiteratureHarvest(status="harvested"),
        )


class _StaticAnalyzer:
    def analyze(self, question, force_mock=None):
        del force_mock
        return {
            "status": "completed",
            "analysis": {
                "problem_id": "p1",
                "original_request": question,
                "normalized_request_english": question,
                "compatibility": "compatible",
                "ambiguities": [],
            },
            "usage": [],
        }


class _StaticResearcher:
    def research(self, problem, **kwargs):
        del kwargs
        return {
            "status": "completed",
            "report": {
                "problem_id": problem["problem_id"],
                "evidence": [],
                "method_findings": [],
                "queries": [],
                "unresolved_questions": [],
                "telemetry": {},
            },
        }


class _StaticCompiler:
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


class _StaticTMM:
    def run(self, task):
        del task
        return {
            "status": "completed",
            "experiment_results": [
                {
                    "experiment_id": "e1",
                    "mode": "optimize",
                    "physically_valid_candidate_count": 1,
                    "portfolio": {
                        "candidates": [
                            {
                                "candidate_id": "candidate",
                                "physically_admissible": True,
                                "target_score": 0.7,
                                "robustness_score": 0.6,
                                "simplicity_score": 0.8,
                                "metadata": {"thicknesses_nm": [100.0]},
                            }
                        ],
                        "selected_roles": {"best_target_score": "candidate"},
                    },
                }
            ],
        }


def test_full_scheduler_appends_control_without_shrinking_normal_portfolio(tmp_path) -> None:
    normal = _StaticRoutePlanner()
    control = QwenMemoryControlRoutePlanner(_ControlClient(), material_catalog=None)
    harness = TMMResearchHarness(
        tmp_path,
        problem_analyzer=_StaticAnalyzer(),
        method_researcher=_StaticResearcher(),
        strategy_planner=SimpleNamespace(),
        route_planner=normal,
        control_route_planner=control,
        task_compiler=_StaticCompiler(),
        tmm_harness_factory=lambda _path, _run_id: _StaticTMM(),
        config=TMMResearchHarnessConfig(
            qwen_force_mock=True,
            control_route_enabled=True,
            scoring_standard_enabled=False,
            maximum_initial_routes=1,
            maximum_iterations=1,
            max_rounds_per_route=1,
            parallel_tmm=False,
        ),
    )
    harness._reflection_client = SimpleNamespace()
    result = harness.run("Optimize a bounded coating over 500-600 nm.")

    assert result.status == "completed"
    assert sorted(harness.tournament_tracks) == [CONTROL_ROUTE_ID, "route_01"]
    assert harness.tournament_tracks[CONTROL_ROUTE_ID].source == "llm_memory_control"
    assert harness.tournament_tracks["route_01"].source == "literature_planned"
    assert result.strategy_plan["normal_route_count"] == 1
    assert result.strategy_plan["control_route_count"] == 1
    assert result.strategy_plan["route_sources"][CONTROL_ROUTE_ID] == "llm_memory_control"
    assert result.strategy_plan["planning_source_comparison"]["groups"][
        "llm_memory_control"
    ]["route_count"] == 1


def _compiled_response() -> dict:
    source = build_dev_optical_design_task("DEV01")
    return {
        "content": json.dumps(
            {
                "status": "compiled",
                "rationale": "A bounded planar coating is supported by TMM.",
                "normalized_request_english": source.normalized_request_english,
                "experiments": [item.model_dump(mode="json") for item in source.experiments],
                "uncertainty": source.uncertainty.model_dump(mode="json"),
            }
        ),
        "_llm_usage": {"model_name": "qwen3.7-flash", "input_tokens": 10, "output_tokens": 10},
    }


class APIConnectionError(Exception):
    pass


class _FlakyCompilerClient:
    model_name = "qwen3.7-flash"

    def __init__(self) -> None:
        self.calls = 0

    def call(self, messages, *, max_tokens=4000, force_mock=None):
        del messages, max_tokens, force_mock
        self.calls += 1
        if self.calls == 1:
            raise APIConnectionError("temporary connection error")
        return _compiled_response()


def test_compiler_retries_transient_connection_without_spending_semantic_attempt() -> None:
    client = _FlakyCompilerClient()
    result = QwenTMMTaskCompiler(client=client).compile(
        "Design a single-layer antireflection coating on glass over 500-600 nm."
    )

    assert result.status == "compiled"
    assert result.attempts == 1
    assert client.calls == 2
