import copy

import pytest

from optomind_optics.harness.research_feedback import (
    DeterministicResearchFeedbackController,
    ResearchIterationObservation,
    apply_scoring_standard,
    decision_score,
    observation_from_run_result,
)
from optomind_optics.harness.scoring_standard import FixedScoreMetric, ScoringStandard


def _standard(formula="R"):
    return ScoringStandard(
        formula=formula,
        metrics=(
            FixedScoreMetric(
                variable="R",
                canonical_id="mean_reflectance_400_700nm",
                metric="mean_reflectance",
                sense="minimize" if formula == "-R" else "maximize",
                region={"wavelength_nm": [400.0, 700.0]},
            ),
        ),
    )


def _candidate(candidate_id, target_score, observed, *, admitted=True):
    report = {
        "target_attainment": {
            _standard().metrics[0].objective_id: {
                "metric": "mean_reflectance",
                "region": {"wavelength_nm": [400.0, 700.0]},
                "observed": observed,
            },
        },
    }
    return {
        "candidate_id": candidate_id,
        "physically_admissible": admitted,
        "target_score": target_score,
        "metadata": {"objective_report": report},
    }


def _observation(*candidates, standard=None, compiled_task=None, iteration="i1"):
    return observation_from_run_result(
        iteration_id=iteration,
        route_id="r1",
        route_title="Route one",
        compilation_status="compiled",
        work_dir="unused",
        scoring_standard=standard,
        compiled_task=compiled_task,
        run_result={
            "status": "completed",
            "experiment_results": [
                {
                    "experiment_id": "e1",
                    "mode": "optimize",
                    "physically_valid_candidate_count": sum(
                        int(candidate["physically_admissible"]) for candidate in candidates
                    ),
                    "portfolio": {
                        "candidates": list(candidates),
                        "selected_roles": {"best_target_score": candidates[0]["candidate_id"]}
                        if candidates else {},
                    },
                },
            ],
        },
    )


def _decision(*observations):
    return DeterministicResearchFeedbackController().decide(
        observations,
        untried_route_count=0,
        refinement_rounds_used=0,
        research_rounds_used=1,
        budget_remaining=True,
    )


def _task(target=0.5, weight=1.0):
    return {
        "experiments": [
            {
                "experiment_id": "e1",
                "mode": "optimize",
                "tmm_task": {
                    "targets": [
                        {
                            "observable": "R",
                            "target": target,
                            "weight": weight,
                            "constraint": "match",
                            "wavelength_min_nm": 400,
                            "wavelength_max_nm": 700,
                        },
                    ],
                    "simulation": {
                        "spectrum": {"start_nm": 400, "stop_nm": 700, "points": 31},
                        "stack": {"layers": [{"constant_n": 1.5, "thickness_nm": 100}]},
                    },
                },
            },
        ],
    }


def test_observation_freezes_candidate_ranking_and_preserves_raw_diagnostics():
    observation = _observation(
        _candidate("raw-winner", 0.95, 0.2),
        _candidate("frozen-winner", 0.4, 0.8),
        _candidate("rejected", 1.0, 1.0, admitted=False),
        standard=_standard(),
    )

    assert observation.best_target_score == 0.95
    assert observation.best_frozen_score == 0.8
    assert observation.decision_score == 0.8
    assert decision_score(observation.model_dump(mode="json")) == 0.8
    assert observation.best_frozen_candidate_ids == ("frozen-winner",)
    assert observation.score_available is True
    assert observation.score_basis == "frozen_standard"
    assert len(observation.score_basis_fingerprint) == 64
    assert [row["target_score"] for row in observation.candidate_summaries] == [0.95, 0.4]
    assert [row["frozen_score"] for row in observation.candidate_summaries] == [0.2, 0.8]
    assert all(row["frozen_score_outcome"]["ok"] for row in observation.candidate_summaries)
    assert observation.experiment_summaries[0]["best_target_score"] == 0.95
    assert observation.experiment_summaries[0]["best_frozen_score"] == 0.8
    assert "frozen-winner" in _decision(observation).preserve_candidate_ids


def test_mutable_objective_improvement_cannot_override_frozen_score_regression():
    before = _observation(_candidate("before", 0.5, 0.8), standard=_standard())
    after = _observation(_candidate("after", 0.9, 0.2), standard=_standard(), iteration="i2")

    decision = _decision(before, after)

    assert decision.observed_improvement == pytest.approx(-0.6)
    assert decision.remaining_headroom is None
    assert decision.action == "stop_completed"


def test_frozen_progress_controls_refinement_when_raw_objective_declines():
    before = _observation(_candidate("before", 0.95, 0.3), standard=_standard())
    after = _observation(_candidate("after", 0.4, 0.7), standard=_standard(), iteration="i2")

    decision = _decision(before, after)

    assert decision.observed_improvement == pytest.approx(0.4)
    assert decision.action == "refine_route"
    assert decision.remaining_headroom is None


@pytest.mark.parametrize("formula,first,second,improvement", [("-R", 0.8, 0.5, 0.3), ("10 * R", 0.3, 0.7, 4.0)])
def test_unbounded_formula_scale_does_not_invent_normalized_headroom(formula, first, second, improvement):
    standard = _standard(formula)
    before = _observation(_candidate("before", 0.6, first), standard=standard)
    after = _observation(_candidate("after", 0.6, second), standard=standard, iteration="i2")

    decision = _decision(before, after)

    assert decision.action == "refine_route"
    assert decision.observed_improvement == pytest.approx(improvement)
    assert decision.remaining_headroom is None


@pytest.mark.parametrize("observed", [None, float("nan"), float("inf")])
def test_unscoreable_frozen_candidate_never_falls_back_to_mutable_score(observed):
    observation = _observation(_candidate("candidate", 0.9, observed), standard=_standard())

    assert observation.best_target_score == 0.9
    assert observation.best_frozen_score is None
    assert observation.best_frozen_candidate_ids == ()
    assert observation.score_available is False
    assert observation.decision_score is None
    assert decision_score(observation.model_dump()) is None
    assert observation.candidate_summaries[0]["frozen_score_outcome"]["ok"] is False
    assert observation.candidate_summaries[0]["frozen_score_outcome"]["missing"] == ["R"]
    assert _decision(observation).action != "refine_route"
    assert _decision(observation).remaining_headroom is None


def test_missing_measurement_is_explicitly_unavailable():
    candidate = _candidate("candidate", 0.9, 0.5)
    candidate["metadata"]["objective_report"] = {}
    observation = _observation(candidate, standard=_standard())

    assert observation.score_basis == "frozen_standard"
    assert observation.score_basis_fingerprint is not None
    assert observation.score_available is False
    assert observation.decision_score is None
    assert observation.candidate_summaries[0]["frozen_score_outcome"]["errors"]


def test_frozen_zero_is_an_available_score():
    observation = _observation(_candidate("candidate", 0.9, 0.0), standard=_standard())

    assert observation.decision_score == 0.0
    assert observation.score_available is True


@pytest.mark.parametrize(
    "updates",
    [
        {"score_available": False},
        {"score_basis_fingerprint": None},
        {"best_frozen_score": float("inf")},
        {"best_frozen_score": float("nan")},
    ],
)
def test_decision_accessor_rejects_unavailable_or_nonfinite_frozen_scores(updates):
    observation = _observation(_candidate("candidate", 0.9, 0.5), standard=_standard())
    observation = observation.model_copy(update=updates)

    assert observation.decision_score is None
    assert decision_score(observation.model_dump()) is None


def test_different_frozen_standards_are_not_compared():
    before = _observation(_candidate("before", 0.5, 0.5), standard=_standard("R"))
    after = _observation(_candidate("after", 0.6, 0.6), standard=_standard("10 * R"), iteration="i2")

    assert before.score_basis_fingerprint != after.score_basis_fingerprint
    assert _decision(before, after).observed_improvement is None


def test_resume_rescoring_preserves_saved_raw_values_and_does_not_mutate_input():
    original = _observation(_candidate("candidate", 0.9, 0.4))
    saved = original.model_dump(mode="json")
    reloaded = ResearchIterationObservation.model_validate(saved)

    rescored = apply_scoring_standard(reloaded, _standard())

    assert rescored.best_target_score == 0.9
    assert rescored.best_frozen_score == 0.4
    assert rescored.candidate_summaries[0]["target_score"] == 0.9
    assert reloaded.model_dump(mode="json") == saved
    assert original.model_dump(mode="json") == saved
    assert apply_scoring_standard(rescored, _standard()).model_dump() == rescored.model_dump()


def test_resume_without_saved_measurements_cannot_reuse_legacy_best():
    original = _observation(_candidate("candidate", 0.9, 0.4))
    original = original.model_copy(update={"candidate_summaries": ()})

    rescored = apply_scoring_standard(original, _standard())

    assert rescored.best_target_score == 0.9
    assert rescored.score_available is False
    assert rescored.decision_score is None


def test_legacy_accessor_retains_existing_score_without_standard():
    observation = _observation(_candidate("candidate", 0.7, 0.4))

    assert observation.score_basis == "legacy_target"
    assert observation.decision_score == 0.7
    assert decision_score({"best_target_score": 0.7}) == 0.7
    assert decision_score({"best_target_score": float("nan")}) is None


def test_same_route_identity_alone_does_not_make_legacy_objectives_comparable():
    before = _observation(_candidate("before", 0.6, 0.5))
    after = _observation(_candidate("after", 0.605, 0.6), iteration="i2")

    assert _decision(before, after).observed_improvement is None


def test_identical_legacy_objective_contract_retains_stagnation_detection():
    before = _observation(_candidate("before", 0.6, 0.5), compiled_task=_task())
    after = _observation(_candidate("after", 0.605, 0.6), compiled_task=_task(), iteration="i2")

    decision = _decision(before, after)

    assert before.score_basis_fingerprint == after.score_basis_fingerprint
    assert decision.observed_improvement == pytest.approx(0.005)
    assert decision.action == "stop_completed"


@pytest.mark.parametrize("target,weight", [(0.8, 1.0), (0.5, 2.0)])
def test_changed_legacy_objective_contract_is_not_compared(target, weight):
    before = _observation(_candidate("before", 0.6, 0.5), compiled_task=_task())
    after = _observation(_candidate("after", 0.9, 0.6), compiled_task=_task(target, weight), iteration="i2")

    assert before.score_basis_fingerprint != after.score_basis_fingerprint
    assert _decision(before, after).observed_improvement is None


def test_legacy_structure_change_keeps_same_objective_contract():
    task = _task()
    changed = copy.deepcopy(task)
    changed["experiments"][0]["tmm_task"]["simulation"]["stack"]["layers"][0]["thickness_nm"] = 150
    before = _observation(_candidate("before", 0.4, 0.5), compiled_task=task)
    after = _observation(_candidate("after", 0.6, 0.6), compiled_task=changed, iteration="i2")

    assert before.score_basis_fingerprint == after.score_basis_fingerprint
    assert _decision(before, after).observed_improvement == pytest.approx(0.2)
