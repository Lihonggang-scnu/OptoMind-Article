from optomind_optics.harness.research_feedback import (
    DeterministicResearchFeedbackController,
    ResearchIterationObservation,
    observation_from_run_result,
)


def _obs(**changes):
    value = dict(
        iteration_id="i1",
        route_id="r1",
        route_title="route",
        compilation_status="compiled",
        run_status="completed",
        physically_valid_candidate_count=1,
        best_target_score=0.6,
        selected_candidate_ids=("c1",),
        experiment_summaries=({"experiment_id": "e1", "mode": "optimize"},),
        work_dir="x",
    )
    value.update(changes)
    return ResearchIterationObservation(**value)


def test_untried_route_is_executed_without_using_score_as_gate():
    decision = DeterministicResearchFeedbackController().decide(
        [_obs(best_target_score=0.99)],
        untried_route_count=1,
        refinement_rounds_used=0,
        research_rounds_used=1,
        budget_remaining=True,
    )
    assert decision.action == "try_next_route"


def test_no_valid_candidate_requests_method_research_after_routes_exhausted():
    decision = DeterministicResearchFeedbackController().decide(
        [_obs(physically_valid_candidate_count=0, best_target_score=None, selected_candidate_ids=())],
        untried_route_count=0,
        refinement_rounds_used=0,
        research_rounds_used=1,
        budget_remaining=True,
    )
    assert decision.action == "research_more"


def test_failed_complementary_route_does_not_discard_earlier_valid_portfolio():
    decision = DeterministicResearchFeedbackController().decide(
        [
            _obs(iteration_id="i1", route_id="r1", selected_candidate_ids=("c1",)),
            _obs(
                iteration_id="i2",
                route_id="r2",
                run_status="failed",
                physically_valid_candidate_count=0,
                best_target_score=None,
                selected_candidate_ids=(),
                failure_categories=("runtime_environment",),
            ),
        ],
        untried_route_count=0,
        refinement_rounds_used=0,
        research_rounds_used=1,
        budget_remaining=True,
    )

    assert decision.action == "stop_completed"
    assert decision.preserve_candidate_ids == ("c1",)


def test_progress_and_headroom_enable_one_bounded_refinement():
    controller = DeterministicResearchFeedbackController()
    decision = controller.decide(
        [_obs(iteration_id="i1", best_target_score=0.50), _obs(iteration_id="i2", best_target_score=0.65)],
        untried_route_count=0,
        refinement_rounds_used=0,
        research_rounds_used=1,
        budget_remaining=True,
    )
    assert decision.action == "refine_route"


def test_stagnation_stops_with_best_effort_candidate_preserved():
    controller = DeterministicResearchFeedbackController()
    decision = controller.decide(
        [_obs(iteration_id="i1", best_target_score=0.60), _obs(iteration_id="i2", best_target_score=0.605)],
        untried_route_count=0,
        refinement_rounds_used=0,
        research_rounds_used=1,
        budget_remaining=True,
    )
    assert decision.action == "stop_completed"
    assert decision.preserve_candidate_ids == ("c1",)


def test_result_parser_reads_verified_portfolio_only():
    observation = observation_from_run_result(
        iteration_id="i1",
        route_id="r1",
        route_title="route",
        compilation_status="compiled",
        work_dir="x",
        run_result={
            "status": "completed",
            "experiment_results": [
                {
                    "experiment_id": "e1",
                    "mode": "optimize",
                    "physically_valid_candidate_count": 1,
                    "portfolio": {
                        "candidates": [
                            {"candidate_id": "bad", "physically_admissible": False, "target_score": 1.0},
                            {"candidate_id": "good", "physically_admissible": True, "target_score": 0.7, "robustness_score": 0.6},
                        ],
                        "selected_roles": {"best_target_score": "good"},
                    },
                }
            ],
        },
    )
    assert observation.best_target_score == 0.7


def test_a_budget_stop_is_not_reported_as_a_broken_environment():
    # A run that spends its whole wall clock failing physics checks stops with
    # reason "budget_exhausted".  Reporting that as "runtime_environment" made
    # a scientific outcome look like an infrastructure fault, and sent the
    # loop's literature search after stack executability instead of physics.
    observation = observation_from_run_result(
        iteration_id="i1",
        route_id="r1",
        route_title="route",
        compilation_status="compiled",
        work_dir="x",
        run_result={
            "status": "failed",
            "experiment_results": [],
            "diagnoses": [
                {"category": "physics_violation", "occurrences": 9},
                {"category": "solver_disagreement", "occurrences": 9},
            ],
            "stop_decision": {"stop": True, "reason": "budget_exhausted"},
        },
    )

    assert "runtime_environment" not in observation.failure_categories
    assert observation.failure_categories == (
        "physics_violation",
        "solver_disagreement",
        "budget_exhausted",
    )
    # No candidate survived, so the iteration contributes nothing to preserve --
    # but the categories above still tell the loop what to react to.
    assert observation.selected_candidate_ids == ()
    assert observation.physically_valid_candidate_count == 0


def test_report_only_route_does_not_compare_score_or_request_refinement():
    decision = DeterministicResearchFeedbackController().decide(
        [
            _obs(iteration_id="i1", route_id="optimization", best_target_score=0.9),
            _obs(
                iteration_id="i2",
                route_id="analysis",
                best_target_score=0.0,
                experiment_summaries=({"experiment_id": "e2", "mode": "simulate"},),
            ),
        ],
        untried_route_count=0,
        refinement_rounds_used=0,
        research_rounds_used=1,
        budget_remaining=True,
    )

    assert decision.action == "stop_completed"
    assert decision.observed_improvement is None
    assert "report-only" in decision.reason


def test_compilation_diagnostic_triggers_contract_repair_before_more_research():
    decision = DeterministicResearchFeedbackController().decide(
        [
            _obs(
                compilation_status="needs_clarification",
                compilation_rationale="Integer layer count N cannot be optimized inside one task.",
                compilation_errors=("named material Sellmeier data were not supplied",),
                run_status="not_run",
                physically_valid_candidate_count=0,
                best_target_score=None,
                selected_candidate_ids=(),
            )
        ],
        untried_route_count=0,
        refinement_rounds_used=0,
        research_rounds_used=0,
        budget_remaining=True,
    )

    assert decision.action == "refine_route"
    assert any("fixed explicit layer count" in item for item in decision.feedback_for_planner)


def test_observation_preserves_compilation_diagnostics():
    observation = observation_from_run_result(
        iteration_id="i1",
        route_id="r1",
        route_title="route",
        compilation_status="invalid",
        compilation_rationale="Malformed outer envelope.",
        compilation_errors=("status: field required",),
        work_dir="x",
        run_result=None,
    )

    assert observation.compilation_rationale == "Malformed outer envelope."
    assert observation.compilation_errors == ("status: field required",)


def test_observation_uses_measured_wall_time_from_budget_snapshot():
    observation = observation_from_run_result(
        iteration_id="i1",
        route_id="r1",
        route_title="route",
        compilation_status="compiled",
        compilation_rationale="ok",
        compilation_errors=(),
        work_dir="run/i1",
        run_result={
            "status": "completed",
            "experiment_results": [],
            "budget": {
                "usage": {"wall_time_seconds": 0.0, "forward_evaluations": 3},
                "measured_usage": {
                    "wall_time_seconds": 12.5,
                    "forward_evaluations": 3,
                },
            },
        },
    )

    assert observation.budget_usage["wall_time_seconds"] == 12.5


def test_material_stop_reason_is_promoted_to_feedback_category():
    observation = observation_from_run_result(
        iteration_id="i1",
        route_id="r1",
        route_title="route",
        compilation_status="compiled",
        work_dir="x",
        run_result={
            "status": "failed",
            "stop_decision": {"reason": "material_resolution_failed"},
        },
    )

    assert observation.failure_categories == ("material_data",)
