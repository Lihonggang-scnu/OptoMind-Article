from optomind_optics.harness.research_feedback import (
    ResearchFeedbackDecision,
    ResearchIterationObservation,
)
from optomind_optics.harness.research_report import DeterministicTMMResearchReporter


def test_report_contains_methods_candidates_failures_and_provenance():
    answer = DeterministicTMMResearchReporter().build(
        problem_analysis={
            "problem_id": "p1",
            "normalized_request_english": "Design a dielectric reflector.",
            "ambiguities": ["Deposition limits were not specified."],
        },
        method_research={
            "method_findings": [
                {"name": "Quarter-wave stack", "reusable_principle": "Use optical thickness near lambda/4."}
            ],
            "evidence": [
                {
                    "evidence_id": "ev1",
                    "paper_id": "p",
                    "title": "A paper",
                    "doi": "10.1/x",
                    "year": 2024,
                    "source_route": "s2",
                    "content_depth": "s2_snippet",
                    "allowed_use": "method_guidance",
                }
            ],
        },
        strategy_plan={
            "routes": [
                {"route_id": "r1", "scientific_hypothesis": "x", "evidence_ids": ["ev1"]}
            ],
            "unresolved_decisions": [],
        },
        iterations=[
            ResearchIterationObservation(
                iteration_id="i1",
                route_id="r1",
                route_title="Periodic route",
                compilation_status="compiled",
                run_status="completed",
                physically_valid_candidate_count=1,
                best_target_score=0.8,
                best_robustness_score=0.7,
                selected_candidate_ids=("c1",),
                candidate_summaries=(
                    {
                        "candidate_id": "c1",
                        "target_score": 0.8,
                        "robustness_score": 0.7,
                        "simplicity_score": 0.6,
                        "thicknesses_nm": [100, 200],
                    },
                ),
                work_dir="run/i1",
                task_path="run/i1/TASK.json",
                result_path="run/i1/FINAL_RESULT.json",
            )
        ],
        stop_decision=ResearchFeedbackDecision(
            action="stop_completed", reason="bounded convergence", preserve_candidate_ids=("c1",)
        ),
        status="completed",
    )
    assert answer.recommended_candidates[0]["candidate_id"] == "c1"
    assert answer.references[0]["evidence_id"] == "ev1"
    assert "100.00, 200.00" in answer.markdown
    assert "bounded convergence" in answer.markdown


def test_same_local_candidate_id_from_two_iterations_is_not_collapsed():
    def observation(iteration_id: str, route_id: str, score: float):
        return ResearchIterationObservation(
            iteration_id=iteration_id,
            route_id=route_id,
            route_title=route_id,
            compilation_status="compiled",
            run_status="completed",
            physically_valid_candidate_count=1,
            best_target_score=score,
            selected_candidate_ids=("candidate",),
            candidate_summaries=(
                {
                    "candidate_id": "candidate",
                    "target_score": score,
                    "robustness_score": 0.5,
                    "simplicity_score": 0.5,
                    "thicknesses_nm": [100.0],
                },
            ),
            work_dir=iteration_id,
        )

    answer = DeterministicTMMResearchReporter().build(
        problem_analysis={"problem_id": "p1", "normalized_request_english": "Design a stack."},
        method_research={"evidence": [], "method_findings": []},
        strategy_plan={
            "routes": [
                {"route_id": "r1", "evidence_ids": []},
                {"route_id": "r2", "evidence_ids": []},
            ]
        },
        iterations=[observation("i1", "r1", 0.7), observation("i2", "r2", 0.8)],
        stop_decision=ResearchFeedbackDecision(action="stop_completed", reason="done"),
        status="completed",
    )

    assert len(answer.recommended_candidates) == 2
    assert {item["candidate_key"] for item in answer.recommended_candidates} == {
        "i1::candidate",
        "i2::candidate",
    }


def test_report_repairs_scientific_controls_in_replayed_raw_dicts():
    answer = DeterministicTMMResearchReporter().build(
        problem_analysis={"problem_id": "p1", "normalized_request_english": "Analyze a stack."},
        method_research={
            "evidence": [],
            "method_findings": [
                {
                    "name": "Quarter-wave baseline",
                    "reusable_principle": "Use d = θ/(4n) and n = \text{sqrt}(n_s).",
                }
            ],
        },
        strategy_plan={"routes": []},
        iterations=[],
        stop_decision=ResearchFeedbackDecision(action="stop_completed", reason="done"),
        status="completed",
    )

    assert "\t" not in answer.markdown
    assert r"\text" in answer.markdown


def test_report_keeps_one_verified_portfolio_per_incomparable_route():
    def observation(iteration_id: str, route_id: str, score: float, metric: float):
        return ResearchIterationObservation(
            iteration_id=iteration_id,
            route_id=route_id,
            route_title=route_id,
            compilation_status="compiled",
            run_status="completed",
            physically_valid_candidate_count=1,
            best_target_score=score,
            selected_candidate_ids=("candidate",),
            candidate_summaries=(
                {
                        "candidate_id": "candidate",
                        "target_score": score,
                        "robustness_score": score - 0.1,
                        "simplicity_score": 0.5,
                        "thicknesses_nm": [100.0],
                    "objective_report": {
                        "target_attainment": {
                            "mean_reflectance": {
                                "observable": "R",
                                "observed": metric,
                                "role": "report_only",
                            }
                        }
                    },
                },
            ),
            work_dir=iteration_id,
        )

    answer = DeterministicTMMResearchReporter().build(
        problem_analysis={"problem_id": "p1", "normalized_request_english": "Compare routes."},
        method_research={"evidence": [], "method_findings": []},
        strategy_plan={"routes": [{"route_id": "r1"}, {"route_id": "r2"}]},
        iterations=[
            observation("i1", "r1", 0.9, 0.03),
            observation("i2", "r2", 0.0, 0.0003),
        ],
        stop_decision=ResearchFeedbackDecision(action="stop_completed", reason="done"),
        status="completed",
    )

    assert len(answer.recommended_candidates) == 2
    assert all(item["ranking_scope"] == "within_route_only" for item in answer.recommended_candidates)
    assert {item["reported_metrics"][0]["observed"] for item in answer.recommended_candidates} == {
        0.03,
        0.0003,
    }
    assert "not a global leaderboard" in answer.markdown


def test_report_prints_every_target_and_labels_method_only_evidence() -> None:
    observation = ResearchIterationObservation(
        iteration_id="i1",
        route_id="r1",
        route_title="Three-target route",
        compilation_status="compiled",
        run_status="completed",
        physically_valid_candidate_count=1,
        best_target_score=0.8,
        selected_candidate_ids=("candidate",),
        candidate_summaries=(
            {
                "candidate_id": "candidate",
                "target_score": 0.8,
                "thicknesses_nm": [100.0],
                "objective_report": {
                    "target_attainment": {
                        "center": {
                            "observable": "T",
                            "observed": 0.98,
                            "target": 0.95,
                            "constraint": "at_least",
                        },
                        "lower_stop": {
                            "observable": "T",
                            "observed": 0.03,
                            "target": 0.05,
                            "constraint": "at_most",
                        },
                        "upper_stop": {
                            "observable": "T",
                            "observed": 0.08,
                            "target": 0.05,
                            "constraint": "at_most",
                        },
                    }
                },
            },
        ),
        work_dir="i1",
    )
    answer = DeterministicTMMResearchReporter().build(
        problem_analysis={"problem_id": "p1", "normalized_request_english": "Design."},
        method_research={
            "method_findings": [],
            "evidence": [
                {
                    "evidence_id": "ev1",
                    "paper_id": "CorpusId:1",
                    "title": "Cross-application thin-film method",
                    "source_route": "s2_snippet_search",
                    "content_depth": "s2_snippet",
                    "allowed_use": "method_guidance",
                }
            ],
        },
        strategy_plan={"routes": [{"route_id": "r1", "evidence_ids": ["ev1"]}]},
        iterations=[observation],
        stop_decision=ResearchFeedbackDecision(action="stop_completed", reason="done"),
        status="completed",
    )

    assert "center=0.980000" in answer.markdown
    assert "lower_stop=0.030000" in answer.markdown
    assert "upper_stop=0.080000" in answer.markdown
    assert "at_least 0.950000; met" in answer.markdown
    assert "at_most 0.050000; trade-off" in answer.markdown
    assert "use=method_guidance" in answer.markdown
    assert "source=s2_snippet_search" in answer.markdown


def test_report_compares_routes_only_under_identical_canonical_contracts():
    def observation(iteration_id: str, route_id: str, score: float, observed: float):
        return ResearchIterationObservation(
            iteration_id=iteration_id,
            route_id=route_id,
            route_title=route_id,
            compilation_status="compiled",
            run_status="completed",
            physically_valid_candidate_count=1,
            best_target_score=score,
            selected_candidate_ids=("candidate",),
            candidate_summaries=(
                {
                    "candidate_id": "candidate",
                    "target_score": score,
                    "robustness_score": score - 0.1,
                    "simplicity_score": 0.5,
                    "thicknesses_nm": [100.0],
                    "objective_report": {
                        "target_attainment": {
                            "canonical_mean_r": {
                                "observable": "R",
                                "observed": observed,
                                "target": 0.01,
                                "constraint": "at_most",
                                "aggregation": "mean",
                                "weight": 1.0,
                            }
                        }
                    },
                },
            ),
            work_dir=iteration_id,
        )

    answer = DeterministicTMMResearchReporter().build(
        problem_analysis={"problem_id": "p1", "normalized_request_english": "Compare."},
        method_research={"evidence": [], "method_findings": []},
        strategy_plan={"routes": [{"route_id": "r1"}, {"route_id": "r2"}]},
        iterations=[
            observation("i1", "r1", 0.8, 0.015),
            observation("i2", "r2", 0.9, 0.005),
        ],
        stop_decision=ResearchFeedbackDecision(action="stop_completed", reason="done"),
        status="completed",
    )

    assert answer.recommended_candidates[0]["route_id"] == "r2"
    assert all(
        item["ranking_scope"] == "shared_user_contract"
        for item in answer.recommended_candidates
    )
    assert "directly comparable" in answer.markdown
    assert "mean R=0.500%" in answer.markdown
    assert "**Best performance**" in answer.markdown
    assert "**Most robust**" in answer.markdown
    assert "**Simplest verified**" in answer.markdown
    assert "best_performance" in answer.recommended_candidates[0]["recommendation_roles"]
