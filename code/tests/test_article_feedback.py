from __future__ import annotations

import json

import pytest

from optomind_optics.harness.article_contracts import (
    ArticleDecision,
    ArticleStage,
    CoverageMatrix,
    CoverageRow,
    CoverageStatus,
    HypothesisCard,
    HypothesisStatus,
    ObservationCard,
)
from optomind_optics.harness.article_director import ArticleDirector
from optomind_optics.harness.article_feedback import (
    ArticleFeedbackController,
    ArticleFeedbackError,
    ArticleFeedbackResult,
    CoverageUpdate,
    HypothesisUpdateDecision,
    ObservationContext,
    RouteSchedule,
)
from optomind_optics.harness.article_memory import ArticleMemoryStore
from optomind_optics.harness.contracts import ExperimentStatus
from optomind_optics.harness.experiment_graph import ExperimentGraph
from optomind_optics.harness.method_research import (
    MethodResearchReport,
    MethodResearchStatus,
)
from optomind_optics.harness.problem_analyzer import (
    OpticalProblemAnalysis,
    ResearchIntent,
    TMMCompatibility,
)


def _analysis() -> OpticalProblemAnalysis:
    return OpticalProblemAnalysis(
        problem_id="problem-1",
        original_request="Design a broadband AR coating over 450-700 nm.",
        normalized_request_english=(
            "Design a broadband one-dimensional antireflection coating for "
            "fused silica in air over 450-700 nm."
        ),
        primary_intent=ResearchIntent.design,
        compatibility=TMMCompatibility.compatible,
        compatibility_reason="planar multilayer stack within the TMM domain",
        needs_method_research=True,
        wavelengths_nm=[(450.0, 700.0)],
        target_observables=["mean reflectance"],
    )


def _report() -> MethodResearchReport:
    return MethodResearchReport(
        problem_id="problem-1", status=MethodResearchStatus.completed
    )


def _plan():
    result = ArticleDirector().plan(
        "Design a broadband AR coating over 450-700 nm.",
        _analysis(),
        _report(),
        force_mock=True,
    )
    assert result.status == "planned" and result.plan is not None
    return result.plan


def _entry(
    hypothesis_id: str,
    to_status: str,
    kind: str,
    reason: str,
    *,
    from_status: str | None = None,
) -> dict:
    entry = {
        "hypothesis_id": hypothesis_id,
        "to_status": to_status,
        "evidence_kind": kind,
        "reason": reason,
    }
    if from_status is not None:
        entry["from_status"] = from_status
    return entry


def _obs(
    observation_id: str,
    status: ExperimentStatus = ExperimentStatus.physically_valid,
    *,
    route_id: str = "baseline",
    metrics: dict | None = None,
    entries: list[dict] | None = None,
    created_at: str | None = None,
) -> ObservationCard:
    payload = {"route_id": route_id}
    if metrics:
        payload.update(metrics)
    return ObservationCard(
        observation_id=observation_id,
        experiment_id="exp-1",
        status=status,
        metrics=payload,
        artifact_ids=["FINAL_RESULT.json"],
        hypothesis_updates=entries or [],
        summary="observation",
        created_at=created_at,
    )


def _discriminator_metrics(matched: bool) -> dict:
    return {
        "R_mean": 0.004 if matched else 0.02,
        "discriminator_match": {
            "hyp-01": {
                "matched": matched,
                "metric_keys": ["R_mean"],
            }
        },
    }


def test_empty_history_schedules_baseline_deterministic() -> None:
    controller = ArticleFeedbackController()
    plan = _plan()
    first = controller.update(plan, [])
    second = controller.update(plan, [])
    assert first.stop_decision == ArticleDecision.continue_run
    assert first.next_routes == [
        RouteSchedule(
            route_id="baseline",
            stage=ArticleStage.baseline_experiments,
            priority=1,
            reason="no observation exists for the baseline route",
        )
    ]
    assert first.hypothesis_updates == []
    assert first.coverage_updates == []
    assert first.validation_errors == []
    assert first.model_dump_json() == second.model_dump_json()
    assert first.controller_id == second.controller_id


def test_partial_support_never_confirms() -> None:
    controller = ArticleFeedbackController()
    observation = _obs(
        "obs-1",
        metrics={"R_mean": 0.004},
        entries=[
            _entry(
                "hyp-01",
                "partially_supported",
                "partial_support",
                "declared observable improved over baseline",
            )
        ],
    )
    result = controller.update(_plan(), [observation])
    assert result.stop_decision == ArticleDecision.continue_run
    assert len(result.hypothesis_updates) == 1
    update = result.hypothesis_updates[0]
    assert update.from_status == HypothesisStatus.proposed
    assert update.to_status == HypothesisStatus.partially_supported
    assert update.hypothesis_id == "hyp-01"
    assert update.observation_ids == ["obs-1"]
    assert update.experiment_ids == ["exp-1"]
    assert update.artifact_ids == ["FINAL_RESULT.json"]
    assert update.route_ids == ["baseline"]
    assert update.to_status != HypothesisStatus.confirmed


def test_no_evidence_or_failed_run_never_confirms_or_refutes() -> None:
    controller = ArticleFeedbackController()
    valid = _obs("obs-1", metrics={"R_mean": 0.004})
    failed = _obs("obs-2", status=ExperimentStatus.rejected_physics)
    result = controller.update(_plan(), [valid, failed])
    assert result.hypothesis_updates == []
    assert all(
        update.to_status not in {HypothesisStatus.confirmed, HypothesisStatus.refuted}
        for update in result.hypothesis_updates
    )


def test_execution_failure_maps_to_under_test_not_refuted() -> None:
    controller = ArticleFeedbackController()
    observation = _obs(
        "obs-1",
        status=ExperimentStatus.rejected_physics,
        entries=[
            _entry(
                "hyp-01",
                "under_test",
                "execution_failure",
                "physics rejection is an execution outcome, not a refutation",
            )
        ],
    )
    result = controller.update(_plan(), [observation])
    update = result.hypothesis_updates[0]
    assert update.to_status == HypothesisStatus.under_test
    assert update.to_status != HypothesisStatus.refuted
    assert "execution" in update.reason


def test_explicit_discriminator_confirms_and_disconfirms() -> None:
    prior_support = [
        HypothesisCard(
            hypothesis_id="hyp-01",
            statement="s",
            status=HypothesisStatus.partially_supported,
        )
    ]
    controller = ArticleFeedbackController()
    confirmed_obs = _obs(
        "obs-confirm",
        metrics=_discriminator_metrics(matched=True),
        entries=[
            _entry(
                "hyp-01",
                "confirmed",
                "discriminator_confirmed",
                "declared discriminator matched in trusted metrics",
            )
        ],
    )
    result = controller.update(
        _plan(),
        [confirmed_obs],
        experiment_context=_context(),
        existing_hypotheses=prior_support,
    )
    assert result.hypothesis_updates[0].to_status == HypothesisStatus.confirmed

    controller_refute = ArticleFeedbackController()
    refuted_obs = _obs(
        "obs-refute",
        metrics=_discriminator_metrics(matched=False),
        entries=[
            _entry(
                "hyp-01",
                "refuted",
                "disconfirming",
                "declared discriminator did not match",
            )
        ],
    )
    refuted = controller_refute.update(
        _plan(),
        [refuted_obs],
        experiment_context=_context(),
        existing_hypotheses=prior_support,
    )
    assert refuted.hypothesis_updates[0].to_status == HypothesisStatus.refuted


def test_non_success_statuses_map_coverage_truthfully() -> None:
    controller = ArticleFeedbackController()
    observations = [
        _obs("obs-f", status=ExperimentStatus.failed, route_id="exploration"),
        _obs("obs-c", status=ExperimentStatus.cancelled, route_id="robustness_ablation"),
        _obs(
            "obs-h",
            status=ExperimentStatus.needs_higher_fidelity,
            route_id="discriminative_experiments",
        ),
    ]
    result = controller.update(_plan(), observations)
    coverage = {item.route_id: item.to_status for item in result.coverage_updates}
    assert coverage["exploration"] == CoverageStatus.failed
    assert coverage["robustness_ablation"] == CoverageStatus.not_run
    assert coverage["discriminative_experiments"] == CoverageStatus.not_run


def test_coverage_updates_and_no_reschedule_of_completed_routes() -> None:
    controller = ArticleFeedbackController()
    plan = _plan()
    first = controller.update(
        plan,
        [_obs("obs-baseline", route_id="baseline")],
    )
    assert first.coverage_updates[0].to_status == CoverageStatus.completed
    assert first.next_routes[0].route_id == "controlled_improvement"

    second = controller.update(
        plan,
        [
            _obs("obs-baseline", route_id="baseline"),
            _obs(
                "obs-improve",
                route_id="controlled_improvement",
                entries=[
                    _entry(
                        "hyp-01",
                        "partially_supported",
                        "partial_support",
                        "improvement observed",
                    )
                ],
            ),
        ],
    )
    assert "baseline" not in [item.route_id for item in second.next_routes]
    assert second.next_routes[0].route_id == "robustness_ablation"


def test_max_next_routes_is_enforced() -> None:
    plan = _plan()
    observations = [
        _obs("obs-b", route_id="baseline"),
        _obs(
            "obs-c",
            route_id="controlled_improvement",
            entries=[
                _entry(
                    hyp,
                    "partially_supported",
                    "partial_support",
                    "improvement observed",
                )
                for hyp in ("hyp-01", "hyp-02")
            ],
        ),
    ]
    bounded = ArticleFeedbackController(max_next_routes=1).update(plan, observations)
    assert len(bounded.next_routes) == 1
    assert bounded.next_routes[0].route_id == "discriminative_experiments"
    wide = ArticleFeedbackController(max_next_routes=2).update(plan, observations)
    assert [item.route_id for item in wide.next_routes] == [
        "discriminative_experiments",
        "robustness_ablation",
    ]


def test_budget_exhausted_stops() -> None:
    controller = ArticleFeedbackController()
    result = controller.update(_plan(), [], budget_exhausted=True)
    assert result.stop_decision == ArticleDecision.stop_budget_exhausted
    assert "budget" in result.stop_reason.lower()


def test_no_progress_is_per_hypothesis_and_consecutive_across_rounds() -> None:
    controller = ArticleFeedbackController(max_no_progress=2)
    entry = _entry(
        "hyp-01",
        "partially_supported",
        "partial_support",
        "no additional evidence",
    )
    plan = _plan()
    prior = [
        HypothesisCard(
            hypothesis_id="hyp-01",
            statement="s",
            status=HypothesisStatus.partially_supported,
        )
    ]
    # Round 1: both hypotheses evaluated, both unchanged -> counters 1 each.
    first_round = controller.update(
        plan,
        [
            _obs(
                "obs-1",
                entries=[
                    entry,
                    _entry(
                        "hyp-02",
                        "partially_supported",
                        "partial_support",
                        "no additional evidence",
                    ),
                ],
            )
        ],
        existing_hypotheses=prior
        + [
            HypothesisCard(
                hypothesis_id="hyp-02",
                statement="s",
                status=HypothesisStatus.partially_supported,
            )
        ],
    )
    assert first_round.stop_decision == ArticleDecision.continue_run
    # Round 2: unchanged again -> counters reach the configured limit.
    second_round = controller.update(
        plan,
        [
            _obs(
                "obs-2",
                entries=[
                    entry,
                    _entry(
                        "hyp-02",
                        "partially_supported",
                        "partial_support",
                        "no additional evidence",
                    ),
                ],
            )
        ],
        existing_hypotheses=prior
        + [
            HypothesisCard(
                hypothesis_id="hyp-02",
                statement="s",
                status=HypothesisStatus.partially_supported,
            )
        ],
    )
    assert second_round.stop_decision == ArticleDecision.stop_no_progress
    assert "without hypothesis progress" in second_round.stop_reason


def test_no_progress_counter_resets_after_progress() -> None:
    controller = ArticleFeedbackController(max_no_progress=2)
    plan = _plan()
    unchanged = _entry(
        "hyp-01",
        "partially_supported",
        "partial_support",
        "no additional evidence",
    )
    prior = [
        HypothesisCard(
            hypothesis_id="hyp-01",
            statement="s",
            status=HypothesisStatus.partially_supported,
        )
    ]
    first = controller.update(
        plan,
        [_obs("obs-1", entries=[unchanged])],
        existing_hypotheses=prior,
    )
    assert first.stop_decision == ArticleDecision.continue_run
    progressing = controller.update(
        plan,
        [
            _obs(
                "obs-2",
                metrics=_discriminator_metrics(matched=True),
                entries=[
                    _entry(
                        "hyp-01",
                        "confirmed",
                        "discriminator_confirmed",
                        "matched",
                    )
                ],
            )
          ],
          experiment_context=_context(),
          existing_hypotheses=prior,
      )
    assert progressing.stop_decision == ArticleDecision.continue_run
    third = controller.update(
        plan,
        [_obs("obs-3", entries=[unchanged])],
        existing_hypotheses=prior,
    )
    assert third.stop_decision == ArticleDecision.continue_run


def test_all_required_routes_complete_stops_completed() -> None:
    controller = ArticleFeedbackController()
    route_ids = [
        "baseline",
        "exploration",
        "controlled_improvement",
        "discriminative_experiments",
        "robustness_ablation",
    ]
    observations = [
        _obs(f"obs-{index}", route_id=route_id, created_at=f"2026-08-15T00:00:0{index}Z")
        for index, route_id in enumerate(route_ids, start=1)
    ]
    result = controller.update(_plan(), observations)
    assert result.stop_decision == ArticleDecision.stop_completed
    assert result.next_routes == []
    assert len(result.coverage_updates) == 5


def test_no_legal_route_remains_stops_route_exhausted() -> None:
    plan = _plan().model_copy(
        update={
            "coverage_matrix": CoverageMatrix(
                matrix_id="matrix-minimal",
                rows=[
                    CoverageRow(
                        route_id="baseline",
                        title="Baseline experiments",
                        coverage_status=CoverageStatus.planned,
                    )
                ],
            )
        }
    )
    controller = ArticleFeedbackController()
    observation = _obs("obs-1", status=ExperimentStatus.rejected_physics)
    result = controller.update(plan, [observation])
    assert result.coverage_updates[0].to_status == CoverageStatus.failed
    assert result.stop_decision == ArticleDecision.stop_route_exhausted


def test_unknown_ids_illegal_transitions_and_unknown_routes_are_hard_blockers() -> None:
    controller = ArticleFeedbackController()
    plan = _plan()
    unknown_hypothesis = controller.update(
        plan,
        [
            _obs(
                "obs-1",
                entries=[_entry("hyp-99", "active", "partial_support", "n/a")],
            )
        ],
    )
    assert unknown_hypothesis.stop_decision == ArticleDecision.stop_hard_blocker
    assert any("unknown hypothesis" in item for item in unknown_hypothesis.validation_errors)

    missing_discriminator = controller.update(
        plan,
        [
            _obs(
                "obs-2",
                metrics={"R_mean": 0.004},
                entries=[
                    _entry("hyp-01", "confirmed", "discriminator_confirmed", "n/a")
                ],
            )
        ],
        existing_hypotheses=[
            HypothesisCard(
                hypothesis_id="hyp-01",
                statement="s",
                status=HypothesisStatus.partially_supported,
            )
        ],
    )
    assert missing_discriminator.stop_decision == ArticleDecision.stop_hard_blocker
    assert any("discriminator" in item for item in missing_discriminator.validation_errors)

    from_status_mismatch = controller.update(
        plan,
        [
            _obs(
                "obs-3",
                entries=[
                    _entry(
                        "hyp-01",
                        "active",
                        "partial_support",
                        "n/a",
                        from_status="confirmed",
                    )
                ],
            )
        ],
    )
    assert from_status_mismatch.stop_decision == ArticleDecision.stop_hard_blocker
    assert any("from_status mismatch" in item for item in from_status_mismatch.validation_errors)

    unknown_route = controller.update(
        plan,
        [_obs("obs-4", route_id="ghost")],
    )
    assert unknown_route.stop_decision == ArticleDecision.stop_hard_blocker
    assert any("unknown route" in item for item in unknown_route.validation_errors)


def test_terminal_hypothesis_cannot_move_backward() -> None:
    controller = ArticleFeedbackController()
    existing = [
        HypothesisCard(
            hypothesis_id="hyp-01",
            statement="s",
            status=HypothesisStatus.confirmed,
        )
    ]
    result = controller.update(
        _plan(),
        [
            _obs(
                "obs-1",
                entries=[
                    _entry("hyp-01", "under_test", "partial_support", "n/a")
                ],
            )
        ],
        existing_hypotheses=existing,
    )
    assert result.stop_decision == ArticleDecision.stop_hard_blocker
    assert any("terminal" in item for item in result.validation_errors)


def test_inconsistent_observation_order_is_hard_blocker() -> None:
    controller = ArticleFeedbackController()
    result = controller.update(
        _plan(),
        [
            _obs("obs-1", created_at="2026-08-15T00:00:02Z"),
            _obs("obs-2", created_at="2026-08-15T00:00:01Z"),
        ],
    )
    assert result.stop_decision == ArticleDecision.stop_hard_blocker
    assert any("observation order" in item for item in result.validation_errors)


def test_persistence_is_idempotent_and_rolls_back_on_graph_failure(tmp_path) -> None:
    controller = ArticleFeedbackController()
    plan = _plan()
    observation = _obs(
        "obs-1",
        metrics=_discriminator_metrics(matched=True),
        entries=[
            _entry("hyp-01", "confirmed", "discriminator_confirmed", "matched")
        ],
    )
    memory = ArticleMemoryStore(tmp_path / "memory.sqlite")
    graph = ExperimentGraph(tmp_path / "graph.sqlite", "run-1")

    first = controller.update(
        plan,
        [observation],
        experiment_context=_context(),
        existing_hypotheses=[
            HypothesisCard(
                hypothesis_id="hyp-01",
                statement="s",
                status=HypothesisStatus.partially_supported,
            )
        ],
        memory_store=memory,
        graph=graph,
        run_id="run-1",
    )
    memory_count_after_first = len(memory.run_memory_records())
    node = graph.article_node(f"feedback-{first.controller_id}")
    event_types = [item["event_type"] for item in node["history"]]
    assert "article.hypothesis_update" in event_types
    assert "article.coverage" in event_types
    assert "article.decision" in event_types

    second = controller.update(
        plan,
        [observation],
        experiment_context=_context(),
        progress_state={},
        existing_hypotheses=[
            HypothesisCard(
                hypothesis_id="hyp-01",
                statement="s",
                status=HypothesisStatus.partially_supported,
            )
        ],
        memory_store=memory,
        graph=graph,
        run_id="run-1",
    )
    assert second.model_dump_json() == first.model_dump_json()
    assert len(memory.run_memory_records()) == memory_count_after_first
    assert len(graph.article_node(f"feedback-{first.controller_id}")["history"]) == len(
        node["history"]
    )

    fresh_memory = ArticleMemoryStore(tmp_path / "memory2.sqlite")
    fresh_graph = ExperimentGraph(tmp_path / "graph2.sqlite", "run-1")
    original_create = fresh_graph.create_article_node

    def fail_create(*args, **kwargs):
        raise RuntimeError("graph write failed")

    fresh_graph.create_article_node = fail_create  # type: ignore[method-assign]
    with pytest.raises(ArticleFeedbackError, match="persistence failed"):
        controller.update(
            plan,
            [observation],
            experiment_context=_context(),
            existing_hypotheses=[
                HypothesisCard(
                    hypothesis_id="hyp-01",
                    statement="s",
                    status=HypothesisStatus.partially_supported,
                )
            ],
            memory_store=fresh_memory,
            graph=fresh_graph,
            run_id="run-1",
        )
    assert fresh_memory.run_memory_records() == []
    fresh_graph.create_article_node = original_create  # type: ignore[method-assign]


def test_validation_failure_writes_nothing(tmp_path) -> None:
    controller = ArticleFeedbackController()
    memory = ArticleMemoryStore(tmp_path / "memory.sqlite")
    graph = ExperimentGraph(tmp_path / "graph.sqlite", "run-1")
    result = controller.update(
        _plan(),
        [
            _obs(
                "obs-1",
                entries=[_entry("hyp-99", "active", "partial_support", "n/a")],
            )
        ],
        memory_store=memory,
        graph=graph,
        run_id="run-1",
    )
    assert result.stop_decision == ArticleDecision.stop_hard_blocker
    assert memory.run_memory_records() == []
    assert graph.export()["article_nodes"] == []


def test_deterministic_serialization_and_provenance_order() -> None:
    controller = ArticleFeedbackController()
    observations = [
        _obs("obs-1", route_id="baseline"),
        _obs(
            "obs-2",
            route_id="controlled_improvement",
            entries=[
                _entry("hyp-01", "partially_supported", "partial_support", "ok")
            ],
        ),
    ]
    first = controller.update(_plan(), observations)
    second = ArticleFeedbackController().update(_plan(), observations)
    assert first.model_dump_json() == second.model_dump_json()
    assert first.provenance_observation_ids == ["obs-1", "obs-2"]


def _context(
    *,
    experiment_id: str = "exp-1",
    hypothesis_ids: list[str] | None = None,
    route_id: str = "baseline",
    expected_discriminator: dict | None = None,
) -> ObservationContext:
    return ObservationContext(
        experiment_id=experiment_id,
        hypothesis_ids=hypothesis_ids or ["hyp-01"],
        route_id=route_id,
        expected_discriminator=(
            {"metric_keys": ["R_mean"]}
            if expected_discriminator is None
            else expected_discriminator
        ),
    )


def test_context_validates_identity_hypotheses_route_and_discriminator() -> None:
    controller = ArticleFeedbackController()
    plan = _plan()

    mismatch_experiment = controller.update(
        plan,
        [_obs("obs-1")],
        experiment_context=_context(experiment_id="exp-OTHER"),
    )
    assert mismatch_experiment.stop_decision == ArticleDecision.stop_hard_blocker
    assert any(
        "does not match context" in item
        for item in mismatch_experiment.validation_errors
    )

    unknown_hypotheses = controller.update(
        plan,
        [_obs("obs-1")],
        experiment_context=_context(hypothesis_ids=["hyp-99"]),
    )
    assert unknown_hypotheses.stop_decision == ArticleDecision.stop_hard_blocker
    assert any(
        "unknown hypotheses" in item
        for item in unknown_hypotheses.validation_errors
    )

    unknown_route = controller.update(
        plan,
        [_obs("obs-1")],
        experiment_context=_context(route_id="ghost"),
    )
    assert unknown_route.stop_decision == ArticleDecision.stop_hard_blocker
    assert any("unknown route" in item for item in unknown_route.validation_errors)

    route_binding = controller.update(
        plan,
        [_obs("obs-1", route_id="controlled_improvement")],
        experiment_context=_context(),
    )
    assert route_binding.stop_decision == ArticleDecision.stop_hard_blocker
    assert any(
        "does not match experiment context route" in item
        for item in route_binding.validation_errors
    )

    no_discriminator_contract = controller.update(
        plan,
        [
            _obs(
                "obs-1",
                metrics=_discriminator_metrics(matched=True),
                entries=[
                    _entry("hyp-01", "confirmed", "discriminator_confirmed", "ok")
                ],
            )
        ],
        experiment_context=_context(expected_discriminator={}),
        existing_hypotheses=[
            HypothesisCard(
                hypothesis_id="hyp-01",
                statement="s",
                status=HypothesisStatus.partially_supported,
            )
        ],
    )
    assert no_discriminator_contract.stop_decision == ArticleDecision.stop_hard_blocker
    assert any(
        "expected discriminator" in item
        for item in no_discriminator_contract.validation_errors
    )


def test_empty_hypothesis_updates_derive_decisions_from_context() -> None:
    controller = ArticleFeedbackController()
    plan = _plan()
    prior = [
        HypothesisCard(
            hypothesis_id="hyp-01",
            statement="s",
            status=HypothesisStatus.partially_supported,
        )
    ]

    confirmed = controller.update(
        plan,
        [_obs("obs-confirm", metrics=_discriminator_metrics(matched=True))],
        experiment_context=_context(),
        existing_hypotheses=prior,
    )
    assert confirmed.hypothesis_updates[0].to_status == HypothesisStatus.confirmed

    failed = controller.update(
        plan,
        [_obs("obs-fail", status=ExperimentStatus.rejected_physics)],
        experiment_context=_context(),
    )
    assert failed.hypothesis_updates[0].to_status == HypothesisStatus.under_test
    assert failed.hypothesis_updates[0].to_status != HypothesisStatus.refuted

    partial = controller.update(
        plan,
        [_obs("obs-partial", metrics={"R_mean": 0.004})],
        experiment_context=_context(),
    )
    assert partial.hypothesis_updates[0].to_status == HypothesisStatus.partially_supported


def test_experiment_card_context_derives_route_from_stage() -> None:
    from optomind_optics.harness.article_contracts import ExperimentCard

    card = ExperimentCard(
        experiment_id="exp-1",
        hypothesis_ids=["hyp-01"],
        action_type="run_solver",
        task_hash="task-hash",
        stage=ArticleStage.baseline_experiments,
        expected_discriminator={"metric_keys": ["R_mean"]},
    )
    controller = ArticleFeedbackController()
    result = controller.update(
        _plan(),
        [_obs("obs-1", metrics={"R_mean": 0.004})],
        experiment_context=card,
    )
    assert result.hypothesis_updates[0].to_status == HypothesisStatus.partially_supported


def test_journal_recovers_graph_failure_then_memory_failure(tmp_path) -> None:
    controller = ArticleFeedbackController()
    plan = _plan()
    observation = _obs(
        "obs-1",
        metrics=_discriminator_metrics(matched=True),
        entries=[
            _entry("hyp-01", "confirmed", "discriminator_confirmed", "matched")
        ],
    )
    prior = [
        HypothesisCard(
            hypothesis_id="hyp-01",
            statement="s",
            status=HypothesisStatus.partially_supported,
        )
    ]

    # Graph failure: nothing is persisted, journal records the in-progress intent.
    memory = ArticleMemoryStore(tmp_path / "mem1.sqlite")
    graph = ExperimentGraph(tmp_path / "graph1.sqlite", "run-1")
    journal = tmp_path / "journal1.json"
    original_create = graph.create_article_node
    graph.create_article_node = lambda *a, **k: (_ for _ in ()).throw(  # type: ignore[method-assign]
        RuntimeError("graph write failed")
    )
    with pytest.raises(ArticleFeedbackError, match="persistence failed"):
        controller.update(
            plan,
            [observation],
            experiment_context=_context(),
            existing_hypotheses=prior,
            memory_store=memory,
            graph=graph,
            run_id="run-1",
            journal_path=journal,
        )
    state = json.loads(journal.read_text(encoding="utf-8"))
    entry = state[list(state)[0]]
    assert entry["status"] == "in_progress"
    assert entry["graph_written"] is False
    assert memory.run_memory_records() == []
    graph.create_article_node = original_create  # type: ignore[method-assign]

    # Retry: journal-driven resume completes both stores and the journal.
    result = controller.update(
        plan,
        [observation],
        experiment_context=_context(),
        existing_hypotheses=prior,
        memory_store=memory,
        graph=graph,
        run_id="run-1",
        journal_path=journal,
    )
    assert result.stop_decision != ArticleDecision.stop_hard_blocker
    assert memory.run_memory_records()
    assert graph.article_node(f"feedback-{result.controller_id}") is not None
    entry = json.loads(journal.read_text(encoding="utf-8"))[result.controller_id]
    assert entry["status"] == "completed"
    memory_count = len(memory.run_memory_records())
    graph_history = len(
        graph.article_node(f"feedback-{result.controller_id}")["history"]
    )

    # Idempotent retry after completion: no duplicated writes.
    controller.update(
        plan,
        [observation],
        experiment_context=_context(),
        progress_state={},
        existing_hypotheses=prior,
        memory_store=memory,
        graph=graph,
        run_id="run-1",
        journal_path=journal,
    )
    assert len(memory.run_memory_records()) == memory_count
    assert (
        len(graph.article_node(f"feedback-{result.controller_id}")["history"])
        == graph_history
    )

    # Memory failure after graph success: journal keeps graph_written, retry resumes.
    memory2 = ArticleMemoryStore(tmp_path / "mem2.sqlite")
    graph2 = ExperimentGraph(tmp_path / "graph2.sqlite", "run-1")
    journal2 = tmp_path / "journal2.json"
    original_add = memory2.add_run_memory
    memory2.add_run_memory = lambda *a, **k: (_ for _ in ()).throw(  # type: ignore[method-assign]
        RuntimeError("memory write failed")
    )
    with pytest.raises(ArticleFeedbackError, match="persistence failed"):
        controller.update(
            plan,
            [observation],
            experiment_context=_context(),
            existing_hypotheses=prior,
            memory_store=memory2,
            graph=graph2,
            run_id="run-1",
            journal_path=journal2,
        )
    state = json.loads(journal2.read_text(encoding="utf-8"))
    entry = state[list(state)[0]]
    assert entry["graph_written"] is True
    assert entry["memory_written"] is False
    assert memory2.run_memory_records() == []
    memory2.add_run_memory = original_add  # type: ignore[method-assign]

    controller.update(
        plan,
        [observation],
        experiment_context=_context(),
        existing_hypotheses=prior,
        memory_store=memory2,
        graph=graph2,
        run_id="run-1",
        journal_path=journal2,
    )
    assert memory2.run_memory_records()
    entry = json.loads(journal2.read_text(encoding="utf-8"))
    assert entry[list(entry)[0]]["status"] == "completed"


def _confirmed_setup(tmp_path):
    controller = ArticleFeedbackController()
    plan = _plan()
    observation = _obs(
        "obs-1",
        metrics=_discriminator_metrics(matched=True),
        entries=[
            _entry("hyp-01", "confirmed", "discriminator_confirmed", "matched")
        ],
    )
    prior = [
        HypothesisCard(
            hypothesis_id="hyp-01",
            statement="s",
            status=HypothesisStatus.partially_supported,
        )
    ]
    return controller, plan, observation, prior


def test_graph_replay_completes_missing_events_without_duplicates(tmp_path) -> None:
    controller, plan, observation, prior = _confirmed_setup(tmp_path)
    memory = ArticleMemoryStore(tmp_path / "mem.sqlite")
    graph = ExperimentGraph(tmp_path / "graph.sqlite", "run-1")
    journal = tmp_path / "journal.json"
    original = graph.record_hypothesis_update
    calls = {"count": 0}

    def failing_hypothesis_event(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("event append failed")
        return original(*args, **kwargs)

    graph.record_hypothesis_update = failing_hypothesis_event  # type: ignore[method-assign]
    with pytest.raises(ArticleFeedbackError, match="persistence failed"):
        controller.update(
            plan,
            [observation],
            experiment_context=_context(),
            existing_hypotheses=prior,
            memory_store=memory,
            graph=graph,
            run_id="run-1",
            journal_path=journal,
        )
    controller_id = next(
        key
        for key in json.loads(journal.read_text(encoding="utf-8"))
        if key != "progress_state"
    )
    node = graph.article_node(f"feedback-{controller_id}")
    assert not any(
        item["event_type"] == "article.hypothesis_update" for item in node["history"]
    )
    graph.record_hypothesis_update = original  # type: ignore[method-assign]

    result = controller.update(
        plan,
        [observation],
        experiment_context=_context(),
        existing_hypotheses=prior,
        memory_store=memory,
        graph=graph,
        run_id="run-1",
        journal_path=journal,
    )
    final = graph.article_node(f"feedback-{result.controller_id}")
    assert len(
        [e for e in final["history"] if e["event_type"] == "article.hypothesis_update"]
    ) == 1
    assert len(
        [e for e in final["history"] if e["event_type"] == "article.coverage"]
    ) == 1
    assert len(
        [e for e in final["history"] if e["event_type"] == "article.observation"]
    ) == 1
    assert len(
        [e for e in final["history"] if e["event_type"] == "article.decision"]
    ) == 1
    assert json.loads(journal.read_text(encoding="utf-8"))[result.controller_id][
        "status"
    ] == "completed"


def test_graph_replay_appends_only_missing_events_after_partial_events(
    tmp_path,
) -> None:
    controller, plan, observation, prior = _confirmed_setup(tmp_path)
    memory = ArticleMemoryStore(tmp_path / "mem.sqlite")
    graph = ExperimentGraph(tmp_path / "graph.sqlite", "run-1")
    journal = tmp_path / "journal.json"
    original_coverage = graph.record_coverage
    calls = {"count": 0}

    def failing_coverage_event(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("coverage event failed")
        return original_coverage(*args, **kwargs)

    graph.record_coverage = failing_coverage_event  # type: ignore[method-assign]
    with pytest.raises(ArticleFeedbackError, match="persistence failed"):
        controller.update(
            plan,
            [observation],
            experiment_context=_context(),
            existing_hypotheses=prior,
            memory_store=memory,
            graph=graph,
            run_id="run-1",
            journal_path=journal,
        )
    controller_id = next(
        key
        for key in json.loads(journal.read_text(encoding="utf-8"))
        if key != "progress_state"
    )
    before = graph.article_node(f"feedback-{controller_id}")["history"]
    assert len(
        [e for e in before if e["event_type"] == "article.hypothesis_update"]
    ) == 1
    assert not any(
        e["event_type"] == "article.coverage" for e in before
    )
    graph.record_coverage = original_coverage  # type: ignore[method-assign]

    result = controller.update(
        plan,
        [observation],
        experiment_context=_context(),
        existing_hypotheses=prior,
        memory_store=memory,
        graph=graph,
        run_id="run-1",
        journal_path=journal,
    )
    final = graph.article_node(f"feedback-{result.controller_id}")["history"]
    assert len(
        [e for e in final if e["event_type"] == "article.hypothesis_update"]
    ) == 1
    assert len([e for e in final if e["event_type"] == "article.coverage"]) == 1
    assert len([e for e in final if e["event_type"] == "article.decision"]) == 1


def test_graph_replay_rejects_conflicting_events(tmp_path) -> None:
    controller, plan, observation, prior = _confirmed_setup(tmp_path)
    memory = ArticleMemoryStore(tmp_path / "mem.sqlite")
    graph = ExperimentGraph(tmp_path / "graph.sqlite", "run-1")
    journal = tmp_path / "journal.json"
    first = controller.update(
        plan,
        [observation],
        experiment_context=_context(),
        existing_hypotheses=prior,
        memory_store=memory,
        graph=graph,
        run_id="run-1",
        journal_path=journal,
    )
    node_id = f"feedback-{first.controller_id}"
    graph.record_hypothesis_update(
        node_id,
        "hyp-01",
        "partially_supported",
        "confirmed",
        reason="tampered reason",
    )
    with pytest.raises(ArticleFeedbackError, match="conflicting article.hypothesis_update"):
        controller.update(
            plan,
            [observation],
            experiment_context=_context(),
            progress_state={},
            existing_hypotheses=prior,
            memory_store=memory,
            graph=graph,
            run_id="run-1",
            journal_path=tmp_path / "journal2.json",
        )


def test_no_progress_state_survives_fresh_controller(tmp_path) -> None:
    plan = _plan()
    prior = [
        HypothesisCard(
            hypothesis_id="hyp-01",
            statement="s",
            status=HypothesisStatus.partially_supported,
        )
    ]
    unchanged = _entry(
        "hyp-01",
        "partially_supported",
        "partial_support",
        "no additional evidence",
    )
    key = f"{plan.plan_id}:hyp-01"
    journal = tmp_path / "progress.json"

    first_controller = ArticleFeedbackController(max_no_progress=2)
    first = first_controller.update(
        plan,
        [_obs("obs-1", entries=[unchanged])],
        existing_hypotheses=prior,
        journal_path=journal,
    )
    assert first.progress_state[key] == 1

    fresh_controller = ArticleFeedbackController(max_no_progress=2)
    second = fresh_controller.update(
        plan,
        [_obs("obs-2", entries=[unchanged])],
        existing_hypotheses=prior,
        journal_path=journal,
    )
    assert second.stop_decision == ArticleDecision.stop_no_progress
    assert second.progress_state[key] == 2

    third = fresh_controller.update(
        plan,
        [
            _obs(
                "obs-3",
                metrics=_discriminator_metrics(matched=True),
                entries=[
                    _entry("hyp-01", "confirmed", "discriminator_confirmed", "matched")
                ],
            )
        ],
        experiment_context=_context(),
        existing_hypotheses=prior,
        journal_path=journal,
    )
    assert third.progress_state[key] == 0

    explicit = ArticleFeedbackController(max_no_progress=2).update(
        plan,
        [_obs("obs-4", entries=[unchanged])],
        existing_hypotheses=prior,
        progress_state=first.progress_state,
    )
    assert explicit.stop_decision == ArticleDecision.stop_no_progress


def test_stale_counters_from_other_plan_are_ignored(tmp_path) -> None:
    plan = _plan()
    prior = [
        HypothesisCard(
            hypothesis_id="hyp-01",
            statement="s",
            status=HypothesisStatus.partially_supported,
        )
    ]
    unchanged = _entry(
        "hyp-01",
        "partially_supported",
        "partial_support",
        "no additional evidence",
    )
    other_plan_key = f"{plan.plan_id}-other:hyp-x"
    journal = tmp_path / "journal.json"
    journal.write_text(
        json.dumps({"progress_state": {other_plan_key: 99}}), encoding="utf-8"
    )
    controller = ArticleFeedbackController(max_no_progress=2)
    first = controller.update(
        plan,
        [_obs("obs-1", entries=[unchanged])],
        existing_hypotheses=prior,
        journal_path=journal,
    )
    assert first.stop_decision == ArticleDecision.continue_run
    assert first.progress_state == {f"{plan.plan_id}:hyp-01": 1}
    second = controller.update(
        plan,
        [_obs("obs-2", entries=[unchanged])],
        existing_hypotheses=prior,
        journal_path=journal,
    )
    assert second.stop_decision == ArticleDecision.stop_no_progress


def test_controller_id_accounts_for_progress_state_and_retry_is_idempotent(
    tmp_path,
) -> None:
    plan = _plan()
    prior = [
        HypothesisCard(
            hypothesis_id="hyp-01",
            statement="s",
            status=HypothesisStatus.partially_supported,
        )
    ]
    unchanged = _entry(
        "hyp-01",
        "partially_supported",
        "partial_support",
        "no additional evidence",
    )
    controller = ArticleFeedbackController(max_no_progress=2)
    first_round = controller.update(
        plan,
        [_obs("obs-1", entries=[unchanged])],
        existing_hypotheses=prior,
    )
    second_round = controller.update(
        plan,
        [_obs("obs-1", entries=[unchanged])],
        existing_hypotheses=prior,
    )
    assert first_round.stop_decision == ArticleDecision.continue_run
    assert second_round.stop_decision == ArticleDecision.stop_no_progress
    assert first_round.controller_id != second_round.controller_id

    # An exact retry of round 1 (same observation, same pre-state) is idempotent.
    fresh = ArticleFeedbackController(max_no_progress=2)
    retry = fresh.update(
        plan,
        [_obs("obs-1", entries=[unchanged])],
        existing_hypotheses=prior,
        progress_state={},
    )
    assert retry.controller_id == first_round.controller_id
    assert retry.model_dump_json() == first_round.model_dump_json()

    # Distinct rounds must not collide in persistence.
    memory = ArticleMemoryStore(tmp_path / "memory.sqlite")
    graph = ExperimentGraph(tmp_path / "graph.sqlite", "run-1")
    journal = tmp_path / "rounds.json"
    persistent = ArticleFeedbackController(max_no_progress=2)
    first = persistent.update(
        plan,
        [_obs("obs-1", entries=[unchanged])],
        existing_hypotheses=prior,
        memory_store=memory,
        graph=graph,
        run_id="run-1",
        journal_path=journal,
    )
    second = persistent.update(
        plan,
        [_obs("obs-1", entries=[unchanged])],
        existing_hypotheses=prior,
        memory_store=memory,
        graph=graph,
        run_id="run-1",
        journal_path=journal,
    )
    assert first.controller_id != second.controller_id
    assert graph.article_node(f"feedback-{first.controller_id}")["node_id"]
    assert graph.article_node(f"feedback-{second.controller_id}")["node_id"]
    assert len(memory.run_memory_records()) >= 2


def _no_progress_round(tmp_path):
    controller = ArticleFeedbackController(max_no_progress=2)
    plan = _plan()
    prior = [
        HypothesisCard(
            hypothesis_id="hyp-01",
            statement="s",
            status=HypothesisStatus.partially_supported,
        )
    ]
    unchanged = _entry(
        "hyp-01",
        "partially_supported",
        "partial_support",
        "no additional evidence",
    )
    key = f"{plan.plan_id}:hyp-01"
    memory = ArticleMemoryStore(tmp_path / "memory.sqlite")
    graph = ExperimentGraph(tmp_path / "graph.sqlite", "run-1")
    journal = tmp_path / "journal.json"
    return controller, plan, prior, unchanged, key, memory, graph, journal


def test_no_progress_persistence_failure_rolls_back_and_resumes_same_controller(
    tmp_path,
) -> None:
    controller, plan, prior, unchanged, key, memory, graph, journal = (
        _no_progress_round(tmp_path)
    )
    original_create = graph.create_article_node

    def failing_create(*args, **kwargs):
        raise RuntimeError("graph write failed")

    graph.create_article_node = failing_create  # type: ignore[method-assign]
    with pytest.raises(ArticleFeedbackError, match="persistence failed"):
        controller.update(
            plan,
            [_obs("obs-1", entries=[unchanged])],
            existing_hypotheses=prior,
            memory_store=memory,
            graph=graph,
            run_id="run-1",
            journal_path=journal,
        )
    payload = json.loads(journal.read_text(encoding="utf-8"))
    controller_id = next(key for key in payload if key != "progress_state")
    assert payload["progress_state"] == {}
    assert payload[controller_id]["status"] == "in_progress"
    assert payload[controller_id]["pending_progress_state"] == {key: 1}
    assert memory.run_memory_records() == []
    graph.create_article_node = original_create  # type: ignore[method-assign]

    result = controller.update(
        plan,
        [_obs("obs-1", entries=[unchanged])],
        existing_hypotheses=prior,
        memory_store=memory,
        graph=graph,
        run_id="run-1",
        journal_path=journal,
    )
    assert result.controller_id == controller_id
    assert result.stop_decision == ArticleDecision.continue_run
    assert result.progress_state == {key: 1}
    assert json.loads(journal.read_text(encoding="utf-8"))[controller_id][
        "status"
    ] == "completed"
    assert graph.article_node(f"feedback-{controller_id}") is not None
    assert memory.run_memory_records()

    second = controller.update(
        plan,
        [_obs("obs-2", entries=[unchanged])],
        existing_hypotheses=prior,
        memory_store=memory,
        graph=graph,
        run_id="run-1",
        journal_path=journal,
    )
    assert second.stop_decision == ArticleDecision.stop_no_progress


def test_no_progress_persistence_failure_resumes_with_fresh_controller(
    tmp_path,
) -> None:
    controller, plan, prior, unchanged, key, memory, graph, journal = (
        _no_progress_round(tmp_path)
    )
    original_create = graph.create_article_node

    def failing_create(*args, **kwargs):
        raise RuntimeError("graph write failed")

    graph.create_article_node = failing_create  # type: ignore[method-assign]
    with pytest.raises(ArticleFeedbackError, match="persistence failed"):
        controller.update(
            plan,
            [_obs("obs-1", entries=[unchanged])],
            existing_hypotheses=prior,
            memory_store=memory,
            graph=graph,
            run_id="run-1",
            journal_path=journal,
        )
    controller_id = next(
        key
        for key in json.loads(journal.read_text(encoding="utf-8"))
        if key != "progress_state"
    )
    graph.create_article_node = original_create  # type: ignore[method-assign]

    fresh = ArticleFeedbackController(max_no_progress=2)
    result = fresh.update(
        plan,
        [_obs("obs-1", entries=[unchanged])],
        existing_hypotheses=prior,
        memory_store=memory,
        graph=graph,
        run_id="run-1",
        journal_path=journal,
    )
    assert result.controller_id == controller_id
    assert result.stop_decision == ArticleDecision.continue_run
    assert result.progress_state == {key: 1}
    assert json.loads(journal.read_text(encoding="utf-8"))[controller_id][
        "status"
    ] == "completed"
    assert graph.article_node(f"feedback-{controller_id}") is not None
    assert memory.run_memory_records()
