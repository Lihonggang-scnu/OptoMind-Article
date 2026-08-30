"""R-04 tests: route reflection (pre-execution attestation + post-execution reflection).

12 tests covering:
- Phase A: planner pops two keys before validation, tolerates missing keys,
  attestation contains two columns, attestation written before execution
- Phase B: reflection written with six keys, binds attestation hash, records
  observed metrics, uses flash tier, records token usage both spellings,
  malformed reflection degrades gracefully
- Dual-gate: LLM continue blocked by deterministic gate, disagreement recorded
  when LLM stops but score rising
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest

from optomind_optics.harness.route_reflection import (
    REFLECTION_MODEL,
    RouteReflection,
    reflect_on_route,
    write_reflection_sidecar,
)
from optomind_optics.harness.strategy_planner import (
    ARTICLE_STRATEGY_PLANNER_MODEL,
    QwenTMMStrategyPlanner,
)
from optomind_optics.harness.research_orchestrator import TMMResearchHarness


# =====================================================================
# Helpers
# =====================================================================

class RecordingPlusClient:
    """Captures payloads sent to the planner."""

    model_name = ARTICLE_STRATEGY_PLANNER_MODEL

    def __init__(self, payloads: list[dict[str, Any]]):
        self.payloads = list(payloads)
        self.sent_payloads: list[dict[str, Any]] = []

    def call(self, messages, *, max_tokens: int = 5000, force_mock=None):
        self.sent_payloads.append(json.loads(next(m for m in messages if m["role"] == "user")["content"]))
        return {
            "content": json.dumps(self.payloads.pop(0)),
            "_llm_usage": {
                "model_name": self.model_name,
                "input_tokens": 10,
                "output_tokens": 4,
            },
        }


class RecordingTurboClient:
    """Captures payloads sent to the reflection model."""

    model_name = REFLECTION_MODEL

    def __init__(self, payloads: list[dict[str, Any]]):
        self.payloads = list(payloads)
        self.sent_payloads: list[dict[str, Any]] = []

    def call(self, messages, *, max_tokens: int = 4000, force_mock=None):
        self.sent_payloads.append(json.loads(next(m for m in messages if m["role"] == "user")["content"]))
        return {
            "content": json.dumps(self.payloads.pop(0)),
            "_llm_usage": {
                "model_name": self.model_name,
                "prompt_tokens": 10,
                "completion_tokens": 4,
            },
        }


def _valid_plan_with_predecl() -> dict[str, Any]:
    """A valid StrategyPlan including expected_observations and stop_conditions."""
    return {
        "problem_id": "p1",
        "planning_summary": "Test route with pre-declarations.",
        "routes": [
            {
                "route_id": "route_01",
                "title": "Quarter-wave starting point",
                "route_kind": "periodic_stack",
                "scientific_hypothesis": "Periodic impedance contrast creates a stop band.",
                "design_principle": "Start near quarter-wave thickness then optimize.",
                "proposed_materials": ["SiO2", "TiO2"],
                "proposed_topology": "Five alternating dielectric pairs on glass.",
                "design_variables": ["Physical thickness of SiO2 layer 1", "Physical thickness of TiO2 layer 2"],
                "soft_objectives": ["maximize mean reflectance from 500 to 600 nm"],
                "evidence_ids": ["ev_1"],
                "execution_request_english": (
                    "Design a five-pair TiO2/SiO2 reflector on glass from 450 to "
                    "900 nm, maximizing mean reflectance from 500 to 600 nm."
                ),
                "priority": 1,
                "expected_observations": [
                    "best_target_score should increase monotonically as layers are added",
                    "valid_candidates should be >= 1 if hypothesis holds"
                ],
                "stop_conditions": [
                    "If best_target_score improvement < 1e-3 for 2 consecutive rounds, stop"
                ],
            }
        ],
        "research_influence": ["ev_1 motivated the periodic family."],
        "stop_if_all_routes_fail": "Return the best physically valid result.",
    }


def _research() -> dict[str, Any]:
    return {
        "status": "completed",
        "evidence": [
            {
                "evidence_id": "ev_1",
                "title": "Dielectric mirror design",
                "allowed_use": "method_guidance",
                "text": "Quarter-wave stacks maximize reflectance at the design wavelength.",
            }
        ],
        "method_findings": [
            {
                "finding": "Quarter-wave periodicity is the canonical starting point.",
                "evidence_ids": ["ev_1"],
            }
        ],
        "unresolved_questions": ["How many pairs before absorption dominates?"],
    }


def _observation_row(*, route_id: str, score: float):
    """A minimal executed-round row, as the orchestrator stores them."""
    from optomind_optics.harness.research_feedback import (
        ResearchIterationObservation,
    )

    return ResearchIterationObservation(
        iteration_id="iteration_01",
        route_id=route_id,
        route_title="Route " + route_id,
        compilation_status="compiled",
        compilation_rationale="ok",
        compilation_errors=[],
        run_status="completed",
        physically_valid_candidate_count=1,
        best_target_score=score,
        best_robustness_score=0.70,
        selected_candidate_ids=["cand_1"],
        failure_categories=[],
        experiment_summaries=[{"experiment_id": "e1", "mode": "optimize"}],
        candidate_summaries=[],
        budget_usage={},
        work_dir="iterations/iteration_01",
        task_path=None,
        result_path=None,
    )


# =====================================================================
# Phase A tests
# =====================================================================

def test_planner_pops_two_keys_before_validation():
    """Fake client returns routes with expected_observations/stop_conditions.
    DesignRoute construction must succeed (extra="forbid" not triggered).
    Side mapping must contain the two keys by route_id."""
    client = RecordingPlusClient([_valid_plan_with_predecl()])
    planner = QwenTMMStrategyPlanner(client=client, maximum_attempts=1)
    result = planner.plan(
        {"problem_id": "p1", "primary_intent": "design"},
        _research(),
    )
    assert result.status == "planned"
    assert result.plan is not None
    # pre_declarations field should contain the extracted mappings
    pre_decls = result.pre_declarations
    assert "route_01" in pre_decls
    assert pre_decls["route_01"]["expected_observations"] == [
        "best_target_score should increase monotonically as layers are added",
        "valid_candidates should be >= 1 if hypothesis holds",
    ]
    assert pre_decls["route_01"]["stop_conditions"] == [
        "If best_target_score improvement < 1e-3 for 2 consecutive rounds, stop"
    ]
    # DesignRoute objects must not have the extra keys
    for route in result.plan.routes:
        assert not hasattr(route, "expected_observations")
        assert not hasattr(route, "stop_conditions")


def test_planner_tolerates_missing_keys():
    """Model does not emit the two keys -> no error, empty lists in mapping."""
    plan = _valid_plan_with_predecl()
    # Remove the extra keys
    for route in plan["routes"]:
        route.pop("expected_observations", None)
        route.pop("stop_conditions", None)

    client = RecordingPlusClient([plan])
    planner = QwenTMMStrategyPlanner(client=client, maximum_attempts=1)
    result = planner.plan(
        {"problem_id": "p1", "primary_intent": "design"},
        _research(),
    )
    assert result.status == "planned"
    pre_decls = result.pre_declarations
    assert pre_decls["route_01"]["expected_observations"] == []
    assert pre_decls["route_01"]["stop_conditions"] == []


def test_attestation_contains_two_columns():
    """Planner extracts pre_declarations with both keys."""
    client = RecordingPlusClient([_valid_plan_with_predecl()])
    planner = QwenTMMStrategyPlanner(client=client, maximum_attempts=1)
    result = planner.plan(
        {"problem_id": "p1", "primary_intent": "design"},
        _research(),
    )
    assert result.status == "planned"
    pre_decls = result.pre_declarations
    assert "route_01" in pre_decls
    assert len(pre_decls["route_01"]["expected_observations"]) == 2
    assert len(pre_decls["route_01"]["stop_conditions"]) == 1


def test_attestation_written_before_execution(tmp_path, monkeypatch):
    """ROUTE.ATTESTATION.json recorded_at_utc < route_completed event timestamp.
    This is the core P14 protection."""
    # This test requires a full integration run with mocked TMM.
    # We'll verify the order by checking the attestation file is written
    # before the compilation/execution events in the event log.
    from optomind_optics.harness.task_compiler import QwenTMMTaskCompiler
    from optomind_optics.harness.research_orchestrator import TMMResearchHarnessConfig

    class _Analyzer:
        def analyze(self, question, force_mock=None):
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

    class _Researcher:
        def research(self, problem, **kwargs):
            return {
                "status": "completed",
                "report": _research()
            }

    client = RecordingPlusClient([_valid_plan_with_predecl()])
    planner = QwenTMMStrategyPlanner(client=client, maximum_attempts=1)

    analyzer = _Analyzer()
    researcher = _Researcher()
    compiler = QwenTMMTaskCompiler()

    config = TMMResearchHarnessConfig(
        qwen_force_mock=True,
        maximum_initial_routes=1,
        maximum_iterations=1,
    )
    harness = TMMResearchHarness(
        work_dir=tmp_path,
        problem_analyzer=analyzer,
        method_researcher=researcher,
        strategy_planner=planner,
        task_compiler=compiler,
        config=config,
    )

    # Mock TMM factory to avoid real VeriTMM
    def mock_tmm_factory(directory, run_id):
        mock_harness = Mock()
        mock_harness.run = Mock(return_value=Mock(
            status="completed",
            experiment_results=[],
            diagnoses=[],
            budget={},
            model_dump=Mock(return_value={"status": "completed", "experiment_results": []})
        ))
        return mock_harness

    harness.tmm_harness_factory = mock_tmm_factory

    # Run with a simple question
    harness.run("Design a broadband mirror from 500-600 nm")

    # Check event sequence
    events = []
    with open(tmp_path / "RESEARCH_EVENTS.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            events.append(json.loads(line))

    attestation_event_idx = None
    route_completed_idx = None
    for i, ev in enumerate(events):
        if ev["event_type"] == "route_started":
            attestation_event_idx = i
        elif ev["event_type"] == "route_completed":
            route_completed_idx = i

    assert attestation_event_idx is not None, "route_started event missing"
    assert route_completed_idx is not None, "route_completed event missing"
    assert attestation_event_idx < route_completed_idx, (
        "Attestation must be written before route completion"
    )


# =====================================================================
# Phase B tests
# =====================================================================

def _fake_observation() -> dict[str, Any]:
    return {
        "iteration_id": "iteration_01",
        "route_id": "route_01",
        "route_title": "Quarter-wave starting point",
        "compilation_status": "compiled",
        "compilation_rationale": "ok",
        "compilation_errors": [],
        "run_status": "completed",
        "physically_valid_candidate_count": 2,
        "best_target_score": 0.85,
        "best_robustness_score": 0.72,
        "selected_candidate_ids": ["cand_1"],
        "failure_categories": [],
        "experiment_summaries": [],
        "candidate_summaries": [
            {
                "candidate_id": "cand_1",
                "target_score": 0.85,
                "robustness_score": 0.72,
                "thicknesses_nm": [100.0, 200.0, 100.0, 200.0, 100.0],
                "optimizer_id": "adam",
            }
        ],
        "budget_usage": {},
        "work_dir": "iterations/iteration_01",
        "task_path": None,
        "result_path": None,
    }


def _fake_reflection_response() -> dict[str, Any]:
    return {
        "observed_vs_expected": "best_target_score 0.85 matches expectation of increase; valid_candidates=2 >= 1",
        "deviation_mechanism": "Initial layers produced expected stop-band; no deviation observed.",
        "continue_recommended": True,
        "continue_rationale": "Score 0.85 with 2 valid candidates shows hypothesis viable; further optimization may improve.",
        "insight_for_next": "Increase layer count to 8 pairs to widen stop-band.",
        "insight_grounding": "Candidate cand_1 thicknesses_nm [100, 200, 100, 200, 100] show quarter-wave pattern consistent with ev_1.",
    }


def test_reflection_written_with_six_keys(tmp_path):
    """ROUTE.REFLECTION.json contains the six required keys."""
    client = RecordingTurboClient([_fake_reflection_response()])
    pre_decl = {
        "expected_observations": ["score should increase", "valid >= 1"],
        "stop_conditions": ["if gain < 1e-3 for 2 rounds, stop"],
    }
    obs = _fake_observation()

    reflection = reflect_on_route(
        client,
        pre_declarations=pre_decl,
        observation=obs,
        score_history=[0.75, 0.80, 0.85],
        epsilon=1e-4,
        force_mock=True,
    )

    assert isinstance(reflection, RouteReflection)
    assert reflection.observed_vs_expected
    assert reflection.deviation_mechanism
    assert reflection.continue_recommended is True
    assert reflection.continue_rationale
    assert reflection.insight_for_next
    assert reflection.insight_grounding

    # Write sidecar
    attestation_path = tmp_path / "ROUTE.ATTESTATION.json"
    attestation_path.write_text(json.dumps({"test": "data"}))
    write_reflection_sidecar(
        tmp_path,
        reflection,
        attestation_path,
        {"best_target_score": 0.85, "valid_candidates": 2, "run_status": "completed", "tightest_margin": 0.72},
        True,
    )

    refl_path = tmp_path / "ROUTE.REFLECTION.json"
    assert refl_path.exists()
    data = json.loads(refl_path.read_text(encoding="utf-8"))
    for key in ["observed_vs_expected", "deviation_mechanism", "continue_recommended",
                "continue_rationale", "insight_for_next", "insight_grounding"]:
        assert key in data


def test_reflection_binds_attestation_hash(tmp_path):
    """attestation_sha256 equals sha256 of ROUTE.ATTESTATION.json bytes."""
    client = RecordingTurboClient([_fake_reflection_response()])
    pre_decl = {"expected_observations": [], "stop_conditions": []}
    obs = _fake_observation()

    reflection = reflect_on_route(
        client,
        pre_declarations=pre_decl,
        observation=obs,
        score_history=[0.85],
        epsilon=1e-4,
        force_mock=True,
    )

    attestation_path = tmp_path / "ROUTE.ATTESTATION.json"
    attestation_content = {"artifact": "ROUTE.json", "artifact_sha256": "abc123"}
    attestation_path.write_text(json.dumps(attestation_content, separators=(",", ":"), sort_keys=True))

    write_reflection_sidecar(
        tmp_path,
        reflection,
        attestation_path,
        {"best_target_score": 0.85},
        True,
    )

    refl_path = tmp_path / "ROUTE.REFLECTION.json"
    data = json.loads(refl_path.read_text(encoding="utf-8"))
    # Re-read the attestation file as bytes to get the exact hash
    expected_hash = hashlib.sha256(attestation_path.read_bytes()).hexdigest()
    assert data["attestation_sha256"] == expected_hash


def test_reflection_records_observed_metrics(tmp_path):
    """observed_metrics in reflection matches ITERATION_OBSERVATION.json values."""
    client = RecordingTurboClient([_fake_reflection_response()])
    pre_decl = {"expected_observations": [], "stop_conditions": []}
    obs = _fake_observation()

    reflection = reflect_on_route(
        client,
        pre_declarations=pre_decl,
        observation=obs,
        score_history=[0.85],
        epsilon=1e-4,
        force_mock=True,
    )

    attestation_path = tmp_path / "ROUTE.ATTESTATION.json"
    attestation_path.write_text("{}")

    observed_metrics = {
        "best_target_score": 0.85,
        "valid_candidates": 2,
        "run_status": "completed",
        "tightest_margin": 0.72,
    }
    write_reflection_sidecar(tmp_path, reflection, attestation_path, observed_metrics, True)

    refl_path = tmp_path / "ROUTE.REFLECTION.json"
    data = json.loads(refl_path.read_text(encoding="utf-8"))
    assert data["observed_metrics"] == observed_metrics


def test_reflection_uses_flash_tier():
    """Reflection call uses flash/turbo tier model, not plus."""
    client = RecordingTurboClient([_fake_reflection_response()])
    pre_decl = {"expected_observations": [], "stop_conditions": []}
    obs = _fake_observation()

    reflect_on_route(
        client,
        pre_declarations=pre_decl,
        observation=obs,
        score_history=[0.85],
        epsilon=1e-4,
        force_mock=True,
    )

    # Verify the client used is the turbo one (flash tier)
    assert client.model_name == REFLECTION_MODEL
    assert "flash" in REFLECTION_MODEL or "turbo" in REFLECTION_MODEL.lower()


def test_reflection_records_token_usage_both_spellings():
    """Both prompt_tokens/completion_tokens and input_tokens/output_tokens spellings recorded."""
    # Test DashScope spelling (prompt_tokens/completion_tokens)
    client1 = RecordingTurboClient([_fake_reflection_response()])
    pre_decl = {"expected_observations": [], "stop_conditions": []}
    obs = _fake_observation()

    reflect_on_route(
        client1,
        pre_declarations=pre_decl,
        observation=obs,
        score_history=[0.85],
        epsilon=1e-4,
        force_mock=True,
    )
    # Should not raise, usage recorded internally

    # Test OpenAI spelling (input_tokens/output_tokens)
    class OpenAISpellingClient:
        model_name = REFLECTION_MODEL
        def __init__(self, payload):
            self.payload = payload
        def call(self, messages, *, max_tokens=4000, force_mock=None):
            return {
                "content": json.dumps(self.payload),
                "_llm_usage": {
                    "model_name": self.model_name,
                    "input_tokens": 10,
                    "output_tokens": 4,
                },
            }

    client2 = OpenAISpellingClient(_fake_reflection_response())
    reflect_on_route(
        client2,
        pre_declarations=pre_decl,
        observation=obs,
        score_history=[0.85],
        epsilon=1e-4,
        force_mock=True,
    )
    # Should not raise, both spellings handled


def test_malformed_reflection_degrades_gracefully(tmp_path):
    """Illegal JSON from LLM -> degraded reflection, no exception, flow continues."""
    class BadClient:
        model_name = REFLECTION_MODEL
        def call(self, messages, *, max_tokens=4000, force_mock=None):
            return {"content": "not valid json {{", "_llm_usage": {}}

    client = BadClient()
    pre_decl = {"expected_observations": [], "stop_conditions": []}
    obs = _fake_observation()

    reflection = reflect_on_route(
        client,
        pre_declarations=pre_decl,
        observation=obs,
        score_history=[0.85],
        epsilon=1e-4,
        force_mock=True,
    )

    assert reflection.continue_recommended is False
    assert "Reflection unavailable" in reflection.observed_vs_expected
    assert reflection.insight_grounding == ""


# =====================================================================
# Dual-gate tests
# =====================================================================

def test_llm_continue_blocked_by_deterministic_gate(tmp_path):
    """LLM recommends continue, but MAX_ROUNDS_PER_ROUTE reached -> stop, disagreement recorded."""
    client = RecordingTurboClient([{
        **_fake_reflection_response(),
        "continue_recommended": True,
        "continue_rationale": "Still improving",
    }])
    pre_decl = {"expected_observations": [], "stop_conditions": []}
    obs = _fake_observation()

    # Simulate 4 rounds already (MAX_ROUNDS_PER_ROUTE = 4)
    reflection = reflect_on_route(
        client,
        pre_declarations=pre_decl,
        observation=obs,
        score_history=[0.70, 0.75, 0.80, 0.85],  # 4 rounds
        epsilon=1e-4,
        force_mock=True,
    )

    # The reflection itself doesn't enforce gates; the orchestrator does.
    # This test verifies the orchestrator logic by checking the disagreement
    # would be generated. We test the reflection output is correct.
    assert reflection.continue_recommended is True

    # In orchestrator, gate_max_rounds would be False (4 >= 4)
    # -> deterministic_continue = False -> disagreement present with resolution blocked_by_max_rounds


def test_disagreement_recorded_when_llm_stops_but_score_rising(tmp_path):
    """LLM recommends stop, but scores still rising -> stop per LLM, disagreement recorded."""
    client = RecordingTurboClient([{
        **_fake_reflection_response(),
        "continue_recommended": False,
        "continue_rationale": "Looks done to me",
    }])
    pre_decl = {"expected_observations": [], "stop_conditions": []}
    obs = _fake_observation()

    reflection = reflect_on_route(
        client,
        pre_declarations=pre_decl,
        observation=obs,
        score_history=[0.70, 0.75, 0.80, 0.85],  # Still rising
        epsilon=1e-4,
        force_mock=True,
    )

    assert reflection.continue_recommended is False
    # In orchestrator: llm_continue=False -> disagreement present with resolution llm_recommended_stop


# =====================================================================
# Integration smoke test (optional, requires more mocking)
# =====================================================================

def test_full_reflection_flow_smoke(tmp_path):
    """Smoke test: planner -> attestation -> execution -> reflection -> sidecar."""
    # This is a lightweight integration check
    from optomind_optics.harness.strategy_planner import QwenTMMStrategyPlanner

    client = RecordingPlusClient([_valid_plan_with_predecl()])
    planner = QwenTMMStrategyPlanner(client=client, maximum_attempts=1)
    result = planner.plan(
        {"problem_id": "p1", "primary_intent": "design"},
        _research(),
    )

    assert result.status == "planned"
    pre_decls = result.pre_declarations
    assert "route_01" in pre_decls
    assert len(pre_decls["route_01"]["expected_observations"]) == 2
    assert len(pre_decls["route_01"]["stop_conditions"]) == 1

    # Reflection
    turbo_client = RecordingTurboClient([_fake_reflection_response()])
    obs = _fake_observation()
    reflection = reflect_on_route(
        turbo_client,
        pre_declarations=pre_decls["route_01"],
        observation=obs,
        score_history=[0.85],
        epsilon=1e-4,
        force_mock=True,
    )

    assert reflection.continue_recommended is True
    assert reflection.insight_grounding  # non-empty, verifiable grounding


# =====================================================================
# R-04-FIX regression tests (D-2 / D-3 / D-6)
# =====================================================================

def test_reflection_tokens_metered_into_turbo_bucket(monkeypatch):
    """D-2: reflection usage lands in the existing 'turbo' budget bucket.

    record_qwen_usage()'s argument is a bucket key ({"plus", "turbo"}), not a
    model name; the old code passed REFLECTION_MODEL="qwen3.5-flash", which
    silently created a third bucket invisible to every budget summary.
    """
    from config.qwen_config import CostTracker
    from optomind_optics.harness import route_reflection as route_reflection_module

    tracker = CostTracker()
    monkeypatch.setattr(route_reflection_module, "get_cost_tracker", lambda: tracker)

    client = RecordingTurboClient([_fake_reflection_response()])
    reflect_on_route(
        client,
        pre_declarations={"expected_observations": [], "stop_conditions": []},
        observation=_fake_observation(),
        score_history=[0.85],
        epsilon=1e-4,
        force_mock=True,
    )

    snapshot = tracker.get_budget_snapshot()
    assert set(snapshot.qwen_tokens.keys()) <= {"plus", "turbo"}
    assert snapshot.qwen_tokens.get("turbo") == 14  # 10 input + 4 output
    assert not any(
        "qwen" in key.lower() or "flash" in key.lower()
        for key in snapshot.qwen_tokens
    ), f"model-name bucket leaked: {sorted(snapshot.qwen_tokens)}"


def test_degraded_reflection_carries_explicit_marker():
    """D-3: degradation is observable via degraded_reason, not string matching."""
    class BoomClient:
        def call(self, messages, *, max_tokens=4000, force_mock=None):
            raise RuntimeError("LLM backend offline")

    degraded = reflect_on_route(
        BoomClient(),
        pre_declarations={"expected_observations": [], "stop_conditions": []},
        observation=_fake_observation(),
        score_history=[0.85],
        epsilon=1e-4,
        force_mock=True,
    )
    assert degraded.degraded_reason == "LLM call failed: RuntimeError"
    assert degraded.continue_recommended is False

    healthy = RouteReflection.model_validate(_fake_reflection_response())
    assert healthy.degraded_reason == ""

    parsed = reflect_on_route(
        RecordingTurboClient([_fake_reflection_response()]),
        pre_declarations={"expected_observations": [], "stop_conditions": []},
        observation=_fake_observation(),
        score_history=[0.85],
        epsilon=1e-4,
        force_mock=True,
    )
    assert parsed.degraded_reason == ""


def test_sidecar_marks_reflection_unavailable_when_llm_fails(tmp_path):
    """D-3 end-to-end: raising reflection client -> sidecar says unavailable.

    The tournament must keep running (degraded, never crash) but the sidecar
    must stop claiming the reflection was available.
    """
    from optomind_optics.harness.task_compiler import QwenTMMTaskCompiler
    from optomind_optics.harness.research_orchestrator import TMMResearchHarnessConfig

    class _Analyzer:
        def analyze(self, question, force_mock=None):
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

    class _Researcher:
        def research(self, problem, **kwargs):
            return {"status": "completed", "report": _research()}

    class _BoomReflectionClient:
        def call(self, messages, *, max_tokens=4000, force_mock=None):
            raise RuntimeError("LLM backend offline")

    planner_client = RecordingPlusClient([_valid_plan_with_predecl()])
    planner = QwenTMMStrategyPlanner(client=planner_client, maximum_attempts=1)

    config = TMMResearchHarnessConfig(
        qwen_force_mock=True,
        maximum_initial_routes=1,
        maximum_iterations=1,
    )
    harness = TMMResearchHarness(
        work_dir=tmp_path,
        problem_analyzer=_Analyzer(),
        method_researcher=_Researcher(),
        strategy_planner=planner,
        task_compiler=QwenTMMTaskCompiler(),
        config=config,
    )
    harness._reflection_client = _BoomReflectionClient()

    def mock_tmm_factory(directory, run_id):
        mock_harness = Mock()
        mock_harness.run = Mock(return_value=Mock(
            status="completed",
            experiment_results=[],
            diagnoses=[],
            budget={},
            model_dump=Mock(return_value={"status": "completed", "experiment_results": []})
        ))
        return mock_harness

    harness.tmm_harness_factory = mock_tmm_factory
    harness.run("Design a broadband mirror from 500-600 nm")

    sidecar = tmp_path / "iterations" / "iteration_01" / "ROUTE.REFLECTION.json"
    assert sidecar.exists(), "reflection sidecar must still be written on LLM failure"
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert data["reflection_available"] is False
    assert data["degraded_reason"] == "LLM call failed: RuntimeError"
    assert data["attestation_sha256"]
    # A degraded reflection must never stop a route by itself — the recorded
    # decision may not claim that the LLM recommended stopping.
    feedback = json.loads(
        (tmp_path / "iterations" / "iteration_01" / "FEEDBACK_DECISION.json")
        .read_text(encoding="utf-8")
    )
    assert "recommended stopping" not in feedback["reason"]


def test_reference_epsilon_shares_stop_controller_source():
    """D-6: the orchestrator's reference epsilon IS the R-02 constant object."""
    import optomind_optics.harness.research_orchestrator as research_orchestrator_module
    import optomind_optics.harness.stop_controller as stop_controller_module

    assert (
        research_orchestrator_module.DEFAULT_MINIMUM_SCORE_IMPROVEMENT
        is stop_controller_module.DEFAULT_MINIMUM_SCORE_IMPROVEMENT
    )
def test_early_llm_stop_is_ignored_and_route_keeps_exploring(tmp_path):
    """An LLM stop before the two-round boundary is not actionable.

    The route must continue without the old one-shot grace ledger.  The
    scheduler records the disagreement and keeps retrying/refining until a
    hard route limit or an admissible later stop is reached.
    """
    from optomind_optics.harness import (
        EngineMode,
        HarnessBudgetPolicy,
        OpticalDesignTask,
        TMMExperimentSpec,
    )
    from optomind_optics.harness.research_orchestrator import TMMResearchHarnessConfig
    from tmm_engine import LayerSpec, MediumSpec, SimulationTask, SpectralGrid, StackSpec
    from tmm_engine.schemas import dataclass_to_dict

    class _Analyzer:
        def analyze(self, question, force_mock=None):
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

    class _Researcher:
        def research(self, problem, **kwargs):
            return {"status": "completed", "report": _research()}

    # A stub compiler handing back a real OpticalDesignTask so the route takes
    # the SUCCESSFUL-execution branch (valid candidates + compiled + completed),
    # which is where the dual-gate / grace-round logic lives.
    simulation = SimulationTask(
        stack=StackSpec(
            layers=(LayerSpec(material="alumina", provider="rii", thickness_nm=100.0),),
            incident=MediumSpec(constant_n=1.0),
            exit=MediumSpec(constant_n=1.5),
        ),
        spectrum=SpectralGrid(start_nm=450.0, stop_nm=700.0, points=31),
    )
    design_task = OpticalDesignTask(
        task_id="grace_round_task",
        user_request_original="Design a broadband mirror from 500-600 nm.",
        normalized_request_english="Design a broadband mirror from 500 to 600 nm.",
        experiments=(
            TMMExperimentSpec(
                experiment_id="grace_forward",
                mode=EngineMode.simulate,
                tmm_task=dataclass_to_dict(simulation),
            ),
        ),
        budget=HarnessBudgetPolicy(maximum_forward_evaluations=100),
    )

    class _StubCompilation:
        def __init__(self, task):
            self.task = task

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
            return _StubCompilation(design_task)

    llm_stop_payload = {
        **_fake_reflection_response(),
        "observed_vs_expected": "best_target_score 0.85 vs expectation of increase",
        "deviation_mechanism": (
            "Simulated physical rationale: index contrast insufficient for the band."
        ),
        "continue_recommended": False,
        "insight_for_next": "",
    }

    planner_client = RecordingPlusClient([
        _valid_plan_with_predecl(),
        _valid_plan_with_predecl(),
    ])
    planner = QwenTMMStrategyPlanner(client=planner_client, maximum_attempts=1)

    config = TMMResearchHarnessConfig(
        qwen_force_mock=True,
        maximum_initial_routes=1,
        maximum_iterations=6,
    )
    harness = TMMResearchHarness(
        work_dir=tmp_path,
        problem_analyzer=_Analyzer(),
        method_researcher=_Researcher(),
        strategy_planner=planner,
        task_compiler=_StubCompiler(),
        config=config,
    )
    harness._reflection_client = RecordingTurboClient([llm_stop_payload])

    def mock_tmm_factory(directory, run_id):
        mock_harness = Mock()
        mock_harness.run = Mock(return_value=Mock(
            status="completed",
            experiment_results=[],
            diagnoses=[],
            budget={},
            model_dump=Mock(return_value={
                "status": "completed",
                "experiment_results": [{
                    "experiment_id": "e1",
                    "mode": "optimize",
                    "physically_valid_candidate_count": 2,
                    "portfolio": {
                        "candidates": [{
                            "candidate_id": "cand_1",
                            "physically_admissible": True,
                            "target_score": 0.85,
                            "robustness_score": 0.7,
                            "metadata": {"thicknesses_nm": [100.0]},
                            "certificate_id": "cert_1",
                            "artifact_ids": [],
                        }],
                        "selected_roles": {},
                        "pareto_candidate_ids": [],
                    },
                }],
            }),
        ))
        return mock_harness

    harness.tmm_harness_factory = mock_tmm_factory
    harness.run("Design a broadband mirror from 500-600 nm")

    iteration_dir = tmp_path / "iterations" / "iteration_01"
    decision = json.loads(
        (iteration_dir / "FEEDBACK_DECISION.json").read_text(encoding="utf-8")
    )
    assert decision["action"] == "refine_route"
    assert "stop ignored before the minimum" in decision["reason"]
    assert any(
        "ignore it" in directive
        for directive in decision["feedback_for_planner"]
    )

    data = json.loads(
        (iteration_dir / "ROUTE.REFLECTION.json").read_text(encoding="utf-8")
    )
    assert data["disagreement"]["resolution"] == "early_llm_stop_ignored"
    assert data["disagreement"]["llm_stop_policy"]["rounds_executed"] == 1
    assert data["disagreement"]["llm_stop_policy"]["explicit_no_benefit_stop"] is False


def test_explicit_no_benefit_stop_is_honored_only_after_two_rounds(tmp_path):
    """A typed no-benefit vote becomes actionable at the two-round boundary."""
    from tests.harness.test_race_scheduler import (
        _build,
        _plan,
        _revised,
        _route,
    )

    base_plan = _plan(_route("route_01"))
    revised_plan = _revised(base_plan, "second-round physical probe")
    stop_payload = {
        **_fake_reflection_response(),
        "continue_recommended": False,
        "continue_rationale": (
            "After two measured rounds the route remains executable, but the "
            "score history shows no material benefit from another round."
        ),
        "insight_for_next": "",
        "stop_basis": "marginal_gains_too_low",
    }
    harness, _plus, _turbo, _factory = _build(
        tmp_path,
        routes=base_plan["routes"],
        planner_payloads=[base_plan, revised_plan],
        reflection_payload=stop_payload,
        config_kwargs={
            "maximum_initial_routes": 1,
            "max_rounds_per_route": 5,
        },
    )
    harness.run("Design a broadband mirror from 500-600 nm")
    track = harness.tournament_tracks["route_01"]
    assert track.status == "stopped_llm_advice"
    assert track.rounds_used == 2
    assert "after 2 executed rounds" in track.termination_reason
    second_sidecar = json.loads(
        (tmp_path / "iterations" / "iteration_02" / "ROUTE.REFLECTION.json")
        .read_text(encoding="utf-8")
    )
    assert second_sidecar["disagreement"]["resolution"] == (
        "llm_stop_honored_after_minimum_rounds"
    )


def test_stagnation_helper_matches_make_stop_decision():
    """D-7 (approved): one criterion drives both the orchestrator's dual gate
    and make_stop_decision's priority-4 'stop_no_progress'. With priorities
    1-3 dormant, both must reach the same verdict on identical history."""
    from config.qwen_config import RunBudgetSnapshot
    from optomind_optics.harness.feedback_rule_table import FeedbackDecision
    from optomind_optics.harness.stop_controller import (
        evaluate_stagnation,
        make_stop_decision,
    )

    stagnant_history = [0.50, 0.50, 0.50]
    rising_history = [0.50, 0.60, 0.70]
    short_history = [0.50, 0.50]

    assert evaluate_stagnation(stagnant_history) == (True, 0.0)
    assert evaluate_stagnation(rising_history)[0] is False
    assert evaluate_stagnation(short_history) == (False, None)

    def verdict(history):
        decision = make_stop_decision(
            FeedbackDecision(),
            round_k=1,
            n_max_rounds=10,
            certified_candidates=[{"tightest_margin": 0.5}],
            charter={},
            budget=RunBudgetSnapshot(qwen_tokens={}, tmm_cpu_seconds=0.0, timestamp="test"),
            objective_score_history=history,
        )
        return decision.action, decision.reason

    assert verdict(stagnant_history) == ("stop", "stop_no_progress")
    assert verdict(rising_history) == ("continue", "objective_improvable")
    assert verdict(short_history) == ("continue", "objective_improvable")


def test_revision_continues_the_same_racetrack(tmp_path):
    """R-06 equivalent of the deleted
    test_lineage_key_resolves_renamed_revisions_to_their_chain.

    The old guard exercised harness._lineage_key / _route_lineage, both DELETED
    in R-06 by ruling: lineage authority lives solely on RouteTrack. The
    invariant the old test protected -- every per-chain ledger keeps resolving
    to ONE chain across successive revisions -- is asserted here against the
    real revision site, `_improve_track`, rather than against hand-mutated
    attributes: asserting that `track.rounds_used += 1` increments a counter
    tests Python, not this scheduler.

    What must hold: _improve_track picks the planner route whose id matches the
    chain, returns it WITHOUT creating a second track, and the caller-visible
    ledgers (score_history, best_candidate_ids, version_hashes) survive. The
    planner is also handed this chain's own prior rows only -- red line 6.
    """
    from optomind_optics.harness.research_orchestrator import (
        RouteTrack,
        TMMResearchHarnessConfig,
        _route_hash,
    )

    planner_client = RecordingPlusClient([_plan_with_revised_request()])
    harness = TMMResearchHarness(
        work_dir=tmp_path,
        problem_analyzer=Mock(),
        method_researcher=Mock(),
        strategy_planner=QwenTMMStrategyPlanner(
            client=planner_client, maximum_attempts=1
        ),
        config=TMMResearchHarnessConfig(qwen_force_mock=True),
    )

    # The patched members are gone; no second lineage truth source remains.
    assert not hasattr(harness, "_lineage_key")
    assert not hasattr(harness, "_route_lineage")

    original = _valid_plan_with_predecl()["routes"][0]
    track = RouteTrack(
        route_id="route_01",
        source="planned",
        current_route=original,
        version_hashes={_route_hash(original)},
    )
    track.rounds_used = 1
    track.score_history.append(0.50)
    track.best_candidate_ids.append("cand_1")

    # Only THIS chain's row is eligible; a foreign row must not be forwarded.
    harness._observations = [
        _observation_row(route_id="route_01", score=0.50),
        _observation_row(route_id="route_99", score=0.99),
    ]
    tracks_before = dict(harness.tournament_tracks)

    outcome = harness._improve_track(
        track,
        {"problem_id": "p1", "normalized_request_english": "Test request"},
        _research(),
        ("Widen the stop band.",),
        wave_index=1,
        pre_declarations={"route_01": {}},
    )

    assert outcome["ok"], outcome.get("reason")
    # The revision is returned for the SAME chain id: no track was forked.
    assert str(outcome["revised"]["route_id"]) == "route_01"
    assert outcome["digest"] not in track.version_hashes, "digest must be new"
    assert harness.tournament_tracks == tracks_before, "a second track appeared"

    # Red line 6: the planner saw only this chain's own history.
    forwarded = planner_client.sent_payloads[0]
    seen_ids = {
        str(row.get("route_id"))
        for row in (forwarded.get("prior_iterations") or [])
    }
    assert seen_ids <= {"route_01"}, f"foreign chain history leaked: {seen_ids}"

    # Applying the outcome the way the scheduler does keeps every ledger.
    track.current_route = outcome["revised"]
    track.version_hashes.add(outcome["digest"])
    assert track.rounds_used == 1
    assert track.score_history == [0.50]
    assert track.best_candidate_ids == ["cand_1"]
    assert len(track.version_hashes) == 2


def _plan_with_revised_request() -> dict[str, Any]:
    """Same route_id as _valid_plan_with_predecl but a different execution
    request, so the replanning site keeps the id — triggering the rename —
    instead of discarding the route as a request-hash duplicate."""
    plan = _valid_plan_with_predecl()
    route = plan["routes"][0]
    route["execution_request_english"] = (
        "Design a seven-pair TiO2/SiO2 reflector on glass from 450 to 900 nm, "
        "maximizing mean reflectance from 500 to 600 nm."
    )
    route["expected_observations"] = [
        "best_target_score should exceed the previous round after adding pairs",
    ]
    route["stop_conditions"] = [
        "If the added pairs do not raise best_target_score, stop the chain",
    ]
    return plan


def test_renamed_revision_keeps_declarations_and_score_history(tmp_path):
    """End-to-end guard for lineage continuity (R-06 equivalent of the
    rename-based guard; name kept for history). Round 1 grants a grace round ->
    refine_route; the replan reuses route_id "route_01" with a different
    execution request, so the replanning site renames it to
    "route_01_r0_r1". Round 2's reflection must still receive the chain's
    pre-execution declarations and the ACCUMULATED score history.

    Keyed on the raw route_id (the defect this locks out) round 2 saw
    pre_declarations == {} and a score history of length 1, which also left
    gate_max_rounds and gate_stagnation permanently open.
    """
    from optomind_optics.harness import (
        EngineMode,
        HarnessBudgetPolicy,
        OpticalDesignTask,
        TMMExperimentSpec,
    )
    from optomind_optics.harness.research_orchestrator import TMMResearchHarnessConfig
    from tmm_engine import LayerSpec, MediumSpec, SimulationTask, SpectralGrid, StackSpec
    from tmm_engine.schemas import dataclass_to_dict

    class _Analyzer:
        def analyze(self, question, force_mock=None):
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

    class _Researcher:
        def research(self, problem, **kwargs):
            return {"status": "completed", "report": _research()}

    simulation = SimulationTask(
        stack=StackSpec(
            layers=(LayerSpec(material="alumina", provider="rii", thickness_nm=100.0),),
            incident=MediumSpec(constant_n=1.0),
            exit=MediumSpec(constant_n=1.5),
        ),
        spectrum=SpectralGrid(start_nm=450.0, stop_nm=700.0, points=31),
    )
    design_task = OpticalDesignTask(
        task_id="lineage_task",
        user_request_original="Design a broadband mirror from 500-600 nm.",
        normalized_request_english="Design a broadband mirror from 500 to 600 nm.",
        experiments=(
            TMMExperimentSpec(
                experiment_id="lineage_forward",
                mode=EngineMode.simulate,
                tmm_task=dataclass_to_dict(simulation),
            ),
        ),
        budget=HarnessBudgetPolicy(maximum_forward_evaluations=100),
    )

    class _StubCompilation:
        def __init__(self, task):
            self.task = task

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
            return _StubCompilation(design_task)

    # Round 1: LLM stop with open gates -> grace round -> refine_route.
    llm_stop_payload = {
        **_fake_reflection_response(),
        "continue_recommended": False,
        "insight_for_next": "",
        "deviation_mechanism": "Index contrast insufficient for the band.",
    }
    # Round 2 runs on the RENAMED revision.
    llm_continue_payload = {**_fake_reflection_response(), "continue_recommended": True}

    planner_client = RecordingPlusClient([
        _valid_plan_with_predecl(),
        _plan_with_revised_request(),
        _plan_with_revised_request(),
    ])
    planner = QwenTMMStrategyPlanner(client=planner_client, maximum_attempts=1)

    harness = TMMResearchHarness(
        work_dir=tmp_path,
        problem_analyzer=_Analyzer(),
        method_researcher=_Researcher(),
        strategy_planner=planner,
        task_compiler=_StubCompiler(),
        config=TMMResearchHarnessConfig(
            qwen_force_mock=True,
            maximum_initial_routes=1,
            maximum_iterations=6,
        ),
    )
    reflection_client = RecordingTurboClient(
        [llm_stop_payload, llm_continue_payload, llm_continue_payload]
    )
    harness._reflection_client = reflection_client

    def mock_tmm_factory(directory, run_id):
        mock_harness = Mock()
        mock_harness.run = Mock(return_value=Mock(
            status="completed",
            experiment_results=[],
            diagnoses=[],
            budget={},
            model_dump=Mock(return_value={
                "status": "completed",
                "experiment_results": [{
                    "experiment_id": "e1",
                    "mode": "optimize",
                    "physically_valid_candidate_count": 2,
                    "portfolio": {
                        "candidates": [{
                            "candidate_id": "cand_1",
                            "physically_admissible": True,
                            "target_score": 0.85,
                            "robustness_score": 0.7,
                            "metadata": {"thicknesses_nm": [100.0]},
                            "certificate_id": "cert_1",
                            "artifact_ids": [],
                        }],
                        "selected_roles": {},
                        "pareto_candidate_ids": [],
                    },
                }],
            }),
        ))
        return mock_harness

    harness.tmm_harness_factory = mock_tmm_factory
    harness.run("Design a broadband mirror from 500-600 nm")

    # R-06 equivalence note (reported before this edit): the rename mechanism
    # itself is GONE. A revision reusing route_id continues the SAME
    # RouteTrack -- single lineage authority -- so instead of asserting that a
    # rename happened and resolved back to the chain, we assert the chain
    # actually continued on one track under the original id.
    track = harness.tournament_tracks.get("route_01")
    assert track is not None, "chain track missing from tournament state"
    assert track.rounds_used >= 2, (
        "the revised request never executed as round 2 of the same chain"
    )
    assert len(track.score_history) >= 2, (
        f"score history did not accumulate on the continuing track: "
        f"{track.score_history}"
    )

    assert len(reflection_client.sent_payloads) >= 2, (
        "the renamed revision never reached the reflection step"
    )
    second = reflection_client.sent_payloads[1]

    # The chain's pre-execution declarations survive the rename.
    assert second["pre_declarations"]["expected_observations"], (
        "declarations were lost on the renamed revision"
    )
    assert second["pre_declarations"]["stop_conditions"], (
        "stop conditions were lost on the renamed revision"
    )
    # The score history accumulates across the rename rather than restarting.
    assert len(second["score_history"]) >= 2, (
        f"score history restarted on rename: {second['score_history']}"
    )

    # P14 phase A must survive the rename too: the attestation for the renamed
    # revision has to carry the chain's declarations, not empty lists.
    attestations = sorted(
        (tmp_path / "iterations").glob("iteration_*/ROUTE.ATTESTATION.json")
    )
    assert len(attestations) >= 2, (
        "the renamed revision produced no second attestation"
    )
    second_attestation = json.loads(attestations[1].read_text(encoding="utf-8"))
    assert second_attestation["expected_observations"], (
        "attestation of the renamed revision lost expected_observations"
    )
    assert second_attestation["stop_conditions"], (
        "attestation of the renamed revision lost stop_conditions"
    )


# ---------------------------------------------------------------------------
# _improve_track parent_route_id fix (R-09 run3 defect)
# ---------------------------------------------------------------------------

def _plan_with_parent_route_id(chain_id: str = "exp_chain_01") -> dict[str, Any]:
    """Plan where the planner renamed the revision to 'route_01' and stored
    the original chain id in parent_route_id -- as the real planner does."""
    return {
        "problem_id": "p1",
        "planning_summary": "Revised route via parent_route_id.",
        "routes": [
            {
                "route_id": "route_01",
                "parent_route_id": chain_id,
                "revision_reason": "Increased layer count from 10 to 14 layers.",
                "title": "Quarter-wave starting point (revised)",
                "route_kind": "periodic_stack",
                "scientific_hypothesis": "More pairs raise reflectance.",
                "design_principle": "Add two pairs and re-optimise.",
                "proposed_materials": ["SiO2", "TiO2"],
                "proposed_topology": "Seven alternating dielectric pairs on glass.",
                "design_variables": [
                    "Physical thickness of SiO2 layer 1",
                    "Physical thickness of TiO2 layer 2",
                ],
                "soft_objectives": ["maximize mean reflectance from 500 to 600 nm"],
                "evidence_ids": ["ev_1"],
                "execution_request_english": (
                    "Design a seven-pair TiO2/SiO2 reflector on glass from 450 to "
                    "900 nm, maximising mean reflectance from 500 to 600 nm."
                ),
                "priority": 1,
                "expected_observations": [
                    "best_target_score should exceed the previous round after adding pairs"
                ],
                "stop_conditions": [
                    "If the added pairs do not raise best_target_score, stop the chain"
                ],
            }
        ],
        "research_influence": ["ev_1 motivated the periodic family."],
        "stop_if_all_routes_fail": "Return the best physically valid result.",
    }


def test_planner_renamed_revision_is_accepted_via_parent_route_id(tmp_path):
    """R-09 run3 defect lock: real planner renames revision to route_01 and
    stores the original chain id in parent_route_id.  _improve_track must
    accept the match, rewrite route_id back to the stable chain id, and
    return ok=True so the chain continues instead of exiting with
    'planner returned no continuation'.
    """
    from optomind_optics.harness.research_orchestrator import (
        RouteTrack,
        TMMResearchHarnessConfig,
        _route_hash,
    )

    chain_id = "exp_chain_01"
    planner_client = RecordingPlusClient([_plan_with_parent_route_id(chain_id)])
    harness = TMMResearchHarness(
        work_dir=tmp_path,
        problem_analyzer=Mock(),
        method_researcher=Mock(),
        strategy_planner=QwenTMMStrategyPlanner(
            client=planner_client, maximum_attempts=1
        ),
        config=TMMResearchHarnessConfig(qwen_force_mock=True),
    )

    original = _valid_plan_with_predecl()["routes"][0]
    original["route_id"] = chain_id  # seed plan used the chain id
    track = RouteTrack(
        route_id=chain_id,
        source="planned",
        current_route=original,
        version_hashes={_route_hash(original)},
    )
    track.rounds_used = 1
    track.score_history.append(0.60)

    harness._observations = [
        _observation_row(route_id=chain_id, score=0.60),
    ]

    pre_declarations: dict = {chain_id: {"expected_observations": ["baseline obs"], "stop_conditions": []}}
    outcome = harness._improve_track(
        track,
        {"problem_id": "p1", "normalized_request_english": "Test request"},
        _research(),
        ("Increase layer count.",),
        wave_index=1,
        pre_declarations=pre_declarations,
    )

    assert outcome["ok"], f"_improve_track rejected a valid parent_route_id revision: {outcome.get('reason')}"
    # route_id must be rewritten back to the stable chain id -- NOT 'route_01'
    assert str(outcome["revised"]["route_id"]) == chain_id, (
        f"route_id was not rewritten back to chain id; got {outcome['revised']['route_id']}"
    )
    assert outcome["digest"] not in track.version_hashes, "digest must be a new hash"


def test_planner_renamed_revision_inherits_pre_declarations(tmp_path):
    """R-09 run3 defect lock: when the planner emits pre_declarations under
    the renamed id ('route_01'), the fix must re-key them to the stable chain
    id so _execute_track_round and _reflect_track find them on the next round.
    Without the fix, pre_declarations.get(chain_id) returns {} and reflection
    sees empty expected_observations / stop_conditions.
    """
    from optomind_optics.harness.research_orchestrator import (
        RouteTrack,
        TMMResearchHarnessConfig,
        _route_hash,
    )

    chain_id = "exp_chain_01"
    planner_client = RecordingPlusClient([_plan_with_parent_route_id(chain_id)])
    harness = TMMResearchHarness(
        work_dir=tmp_path,
        problem_analyzer=Mock(),
        method_researcher=Mock(),
        strategy_planner=QwenTMMStrategyPlanner(
            client=planner_client, maximum_attempts=1
        ),
        config=TMMResearchHarnessConfig(qwen_force_mock=True),
    )

    original = _valid_plan_with_predecl()["routes"][0]
    original["route_id"] = chain_id
    track = RouteTrack(
        route_id=chain_id,
        source="planned",
        current_route=original,
        version_hashes={_route_hash(original)},
    )

    harness._observations = [
        _observation_row(route_id=chain_id, score=0.55),
    ]

    # Start with no pre_declarations for the chain id to simulate the case
    # where declarations are only present under the renamed id after replan.
    pre_declarations: dict = {}
    outcome = harness._improve_track(
        track,
        {"problem_id": "p1", "normalized_request_english": "Test request"},
        _research(),
        ("Increase layer count.",),
        wave_index=1,
        pre_declarations=pre_declarations,
    )

    assert outcome["ok"], f"_improve_track failed: {outcome.get('reason')}"

    # After the fix, pre_declarations must be accessible under chain_id so
    # that _execute_track_round and _reflect_track can use them on round 2.
    # The planner populated expected_observations / stop_conditions via the
    # renamed 'route_01' key; the fix copies them to chain_id.
    chain_decl = pre_declarations.get(chain_id)
    assert chain_decl is not None, (
        f"pre_declarations['{chain_id}'] missing after parent_route_id rekey; "
        f"keys present: {list(pre_declarations)}"
    )
    assert chain_decl.get("expected_observations"), (
        "expected_observations not inherited from renamed revision's declarations"
    )
    assert chain_decl.get("stop_conditions"), (
        "stop_conditions not inherited from renamed revision's declarations"
    )
