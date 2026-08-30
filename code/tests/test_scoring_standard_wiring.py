"""The frozen scoring standard, as it behaves inside a whole research run.

`test_scoring_standard.py` covers the mechanism in isolation.  What can still
fail once it is wired in is the ordering and the plumbing: the criteria have to
be fixed before any route runs, every route has to be measured on them, the
winner has to be chosen by them rather than by whatever each route optimised
for, and a route that comes back empty must not take the ranking down with it.
Those are the properties here.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from optomind_optics.harness.dev_fixtures import build_dev_optical_design_task
from optomind_optics.harness.research_orchestrator import (
    TMMResearchHarness,
    TMMResearchHarnessConfig,
)
from optomind_optics.harness.scoring_standard import QwenScoringStandardBuilder


VISIBLE_VARIABLE = "mean_reflectance_300_800nm"
INFRARED_VARIABLE = "mean_absorption_5000_13000nm"
FORMULA = f"{VISIBLE_VARIABLE} + {INFRARED_VARIABLE}"
QUESTION = "Reflect 300-800 nm and absorb the 5-13 um window."


# ---------------------------------------------------------------------------
# Stand-ins for the stages a run drives.  Every LLM here is scripted: this file
# must never reach the network.
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


def _route(route_id: str, request: str) -> dict:
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
        "evidence_ids": [],
        "theory_basis": [],
        "expected_advantages": [],
        "known_risks": [],
        "execution_request_english": request,
        "priority": 1,
        "parent_route_id": None,
        "revision_reason": None,
    }


class _Planner:
    def plan(self, problem, research, **kwargs):
        del problem, research, kwargs
        return {
            "status": "planned",
            "plan": {
                "problem_id": "p1",
                "planning_summary": "two independent axes",
                "routes": [_route("r1", "request one"), _route("r2", "request two")],
                "research_influence": [],
                "unresolved_decisions": [],
                "stop_if_all_routes_fail": "return best effort",
            },
            "usage": [],
        }


class _Compiler:
    """Accepts a standard and hands back a fixed task, as the real one would."""

    def __init__(self) -> None:
        self.adopted: list = []

    def adopt_scoring_standard(self, standard) -> None:
        self.adopted.append(standard)

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


class _StandardClient:
    """Answers the two scoring stages from a script instead of the network."""

    model_name = "scripted-standard"

    def __init__(self) -> None:
        self.calls = 0

    def call(self, messages, *, max_tokens=4000, force_mock=None):
        del messages, max_tokens, force_mock
        self.calls += 1
        if self.calls == 1:
            body = {
                "metrics": [
                    {
                        "metric": "mean_reflectance",
                        "sense": "maximize",
                        "wavelength_unit": "nm",
                        "region": {"wavelength_nm": [300, 800]},
                    },
                    {
                        "metric": "mean_absorption",
                        "sense": "maximize",
                        "wavelength_unit": "um",
                        "region": {"wavelength_nm": [5, 13]},
                    },
                ],
                "rationale": "the request names both bands",
            }
        else:
            body = {"formula": FORMULA, "rationale": "equal weight"}
        return {"content": json.dumps(body), "_llm_usage": {"total_tokens": 7}}


class _BrokenBuilder:
    def build(self, question, *, problem_analysis=None, force_mock=None):
        del question, problem_analysis, force_mock
        raise RuntimeError("the scoring service is down")


class _Harness:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def run(self, task):
        del task
        return self.payload


def _measured(visible: float, infrared: float, *, soft: float = 0.5) -> dict:
    """A finished round whose candidate carries the standard's measurements."""

    report = {
        "target_attainment": {
            f"fixedscore.{VISIBLE_VARIABLE}": {
                "metric": "mean_reflectance",
                "observed": visible,
                "region": {"wavelength_nm": [300.0, 800.0]},
            },
            f"fixedscore.{INFRARED_VARIABLE}": {
                "metric": "mean_absorption",
                "observed": infrared,
                "region": {"wavelength_nm": [5000.0, 13000.0]},
            },
        }
    }
    return _payload(
        {
            "candidate_id": "candidate",
            "physically_admissible": True,
            "target_score": soft,
            "robustness_score": 0.6,
            "simplicity_score": 0.8,
            "metadata": {
                "thicknesses_nm": [100.0, 200.0],
                "objective_report": report,
            },
            "artifact_ids": [],
        }
    )


def _unmeasured(soft: float = 0.8) -> dict:
    """A round that produced a design but never reported the ranked numbers."""

    return _payload(
        {
            "candidate_id": "candidate",
            "physically_admissible": True,
            "target_score": soft,
            "robustness_score": 0.6,
            "simplicity_score": 0.8,
            "metadata": {"thicknesses_nm": [120.0]},
            "artifact_ids": [],
        }
    )


def _payload(candidate: dict) -> dict:
    return {
        "status": "completed",
        "experiment_results": [
            {
                "experiment_id": "e1",
                "mode": "optimize",
                "physically_valid_candidate_count": 1,
                "portfolio": {
                    "candidates": [candidate],
                    "selected_roles": {"best_target_score": candidate["candidate_id"]},
                },
            }
        ],
    }


def _run(tmp_path, payloads, *, builder=None, enabled=True, compiler=None):
    stream = iter(payloads)
    harness = TMMResearchHarness(
        tmp_path / "run",
        problem_analyzer=_Analyzer(),
        method_researcher=_Researcher(),
        strategy_planner=_Planner(),
        task_compiler=compiler if compiler is not None else _Compiler(),
        scoring_standard_builder=(
            builder
            if builder is not None
            else QwenScoringStandardBuilder(_StandardClient())
        ),
        tmm_harness_factory=lambda path, run_id: _Harness(next(stream)),
        config=TMMResearchHarnessConfig(
            maximum_refinement_rounds=0,
            scoring_standard_enabled=enabled,
            # The route reflection client is constructed by the harness itself,
            # so mock mode is the only way to keep this file off the network.
            qwen_force_mock=True,
        ),
    )
    return harness, harness.run(QUESTION)


def _artifact(tmp_path, name: str) -> dict:
    return json.loads((tmp_path / "run" / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The criteria are fixed, recorded, and dated before any result exists
# ---------------------------------------------------------------------------


def test_the_standard_is_built_once_and_recorded(tmp_path) -> None:
    harness, result = _run(tmp_path, [_measured(0.9, 0.4), _measured(0.5, 0.5)])

    assert harness.scoring_standard is not None
    envelope = _artifact(tmp_path, "SCORING_STANDARD.json")
    assert envelope["status"] == "standardized"
    assert envelope["ranking_mechanism"] == "frozen_scoring_standard"
    assert envelope["standard"]["formula"] == FORMULA
    assert "SCORING_STANDARD.json" in result.artifacts


def test_the_standard_is_attested_so_its_timing_is_checkable(tmp_path) -> None:
    """The claim is that the criteria predate the results, so it gets hashed."""

    _, result = _run(tmp_path, [_measured(0.9, 0.4), _measured(0.5, 0.5)])

    attestation = _artifact(tmp_path, "SCORING_STANDARD.ATTESTATION.json")
    assert attestation["artifact_kind"] == "pre_execution_scoring_standard"
    assert attestation["artifact_sha256"]
    assert attestation["formula"] == FORMULA
    assert "SCORING_STANDARD.ATTESTATION.json" in result.artifacts


def test_the_compiler_adopts_the_standard_exactly_once(tmp_path) -> None:
    compiler = _Compiler()
    _run(tmp_path, [_measured(0.9, 0.4), _measured(0.5, 0.5)], compiler=compiler)

    assert len(compiler.adopted) == 1
    assert compiler.adopted[0].formula == FORMULA


def test_both_scoring_stages_are_metered(tmp_path) -> None:
    _, result = _run(tmp_path, [_measured(0.9, 0.4), _measured(0.5, 0.5)])

    envelope = _artifact(tmp_path, "SCORING_STANDARD.json")
    assert len(envelope["selection"]["usage"]) == 1
    assert len(envelope["formula"]["usage"]) == 1
    assert result.telemetry["qwen_calls"] >= 2


# ---------------------------------------------------------------------------
# The winner is chosen by the frozen expression, not by each route's own target
# ---------------------------------------------------------------------------


def test_the_frozen_formula_overrules_the_routes_own_soft_scores(tmp_path) -> None:
    """The two verdicts are deliberately opposed.

    Route one all but ignores the infrared band and scores 0.99 against its own
    declared target; route two splits the difference and scores 0.10 against
    its own.  Under the run's criteria route two is better, 1.3 against 0.95,
    and that is the verdict that has to survive.
    """

    _, result = _run(
        tmp_path,
        [_measured(0.9, 0.05, soft=0.99), _measured(0.5, 0.8, soft=0.10)],
    )

    ranking = _artifact(tmp_path, "SCORING_RANKING.json")
    leaderboard = ranking["leaderboard"]
    assert [row["rank"] for row in leaderboard] == [1, 2]
    assert leaderboard[0]["score"] == pytest.approx(1.3)
    assert leaderboard[1]["score"] == pytest.approx(0.95)
    assert ranking["winner"] == leaderboard[0]["route_id"]
    assert result.status == "completed"


def test_the_ranking_artifact_records_the_expression_it_used(tmp_path) -> None:
    _, _ = _run(tmp_path, [_measured(0.9, 0.4), _measured(0.5, 0.5)])

    ranking = _artifact(tmp_path, "SCORING_RANKING.json")
    assert ranking["formula"] == FORMULA
    assert ranking["metrics"] == [
        "mean_reflectance@300-800nm",
        "mean_absorption@5000-13000nm",
    ]
    assert ranking["question_digest"]


def test_the_report_ranks_by_the_frozen_expression(tmp_path) -> None:
    _, result = _run(
        tmp_path,
        [_measured(0.9, 0.05, soft=0.99), _measured(0.5, 0.8, soft=0.10)],
    )

    ranked = list(result.final_answer.recommended_candidates)
    assert ranked
    assert all(item["ranking_scope"] == "frozen_scoring_standard" for item in ranked)
    assert ranked[0]["frozen_score"] == pytest.approx(1.3)
    assert ranked[0]["cross_route_rank"] == 1
    assert "one fixed expression" in result.final_answer.markdown


def test_each_candidates_score_inputs_are_kept_for_recomputation(tmp_path) -> None:
    _, result = _run(tmp_path, [_measured(0.9, 0.4), _measured(0.5, 0.5)])

    first = list(result.final_answer.recommended_candidates)[0]
    inputs = first["frozen_score_inputs"]
    assert set(inputs) == {VISIBLE_VARIABLE, INFRARED_VARIABLE}
    assert sum(inputs.values()) == pytest.approx(first["frozen_score"])


# ---------------------------------------------------------------------------
# Partial failure stays partial
# ---------------------------------------------------------------------------


def test_one_usable_route_is_enough_and_the_empty_one_keeps_its_reason(
    tmp_path,
) -> None:
    """Several routes race precisely so that one of them may come back empty."""

    _, result = _run(tmp_path, [_measured(0.9, 0.4), _unmeasured()])

    ranking = _artifact(tmp_path, "SCORING_RANKING.json")
    assert len(ranking["leaderboard"]) == 1
    assert len(ranking["routes_without_a_scoreable_result"]) == 1
    orphan = next(row for row in ranking["routes"] if row["representative"] is None)
    assert "missing" in orphan["unscoreable_reason"]
    assert result.status == "completed"


def test_an_unscoreable_route_does_not_outrank_a_scored_one(tmp_path) -> None:
    """A missing measurement is not a zero, and it is not a win either."""

    _, result = _run(tmp_path, [_unmeasured(soft=0.99), _measured(0.1, 0.1, soft=0.01)])

    ranked = list(result.final_answer.recommended_candidates)
    assert ranked[0]["frozen_score"] == pytest.approx(0.2)
    assert ranked[-1]["frozen_score"] is None


def test_a_scoring_service_failure_degrades_rather_than_ends_the_run(tmp_path) -> None:
    harness, result = _run(
        tmp_path, [_unmeasured(0.7), _unmeasured(0.72)], builder=_BrokenBuilder()
    )

    assert harness.scoring_standard is None
    envelope = _artifact(tmp_path, "SCORING_STANDARD.json")
    assert envelope["status"] == "unavailable"
    assert envelope["ranking_mechanism"] == "first_route_objective_freeze"
    assert result.status == "completed"
    assert not (tmp_path / "run" / "SCORING_RANKING.json").exists()


def test_switching_the_standard_off_leaves_the_older_behaviour_intact(
    tmp_path,
) -> None:
    harness, result = _run(
        tmp_path, [_unmeasured(0.7), _unmeasured(0.72)], enabled=False
    )

    assert harness.scoring_standard is None
    assert not (tmp_path / "run" / "SCORING_STANDARD.json").exists()
    assert not (tmp_path / "run" / "SCORING_RANKING.json").exists()
    assert result.status == "completed"
    ranked = list(result.final_answer.recommended_candidates)
    assert all("frozen_score" not in item for item in ranked)
