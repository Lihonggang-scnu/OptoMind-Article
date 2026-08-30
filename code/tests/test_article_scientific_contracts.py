from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from optomind_optics.harness.article_contracts import (
    ArticleDecision,
    ArticleEventValidationError,
    ArticleNodePayload,
    ArticleStage,
    ClaimCard,
    ClaimStrength,
    CoverageMatrix,
    CoverageRow,
    CoverageStatus,
    ExperimentCard,
    FigureCard,
    HypothesisCard,
    HypothesisStatus,
    ObservationCard,
    ResearchCharter,
    ReviewCard,
    ReviewKind,
    ReviewSeverity,
    validate_article_event,
)
from optomind_optics.harness.contracts import ActionType, ExperimentStatus


def _canonical(model) -> str:
    return json.dumps(model.model_dump(mode="json"), sort_keys=True)


def test_research_charter_rejects_missing_required_fields() -> None:
    with pytest.raises(ValidationError):
        ResearchCharter(question="q", scope="s", goals=["g"])  # charter_id missing
    with pytest.raises(ValidationError):
        ResearchCharter(charter_id="c1", scope="s", goals=["g"])  # question missing
    with pytest.raises(ValidationError):
        ResearchCharter(charter_id="c1", question="q", goals=["g"])  # scope missing
    with pytest.raises(ValidationError):
        ResearchCharter(charter_id="c1", question="q", scope="s")  # goals missing


def test_research_charter_rejects_malformed_goals_and_stage() -> None:
    with pytest.raises(ValidationError):
        ResearchCharter(charter_id="c1", question="q", scope="s", goals=[])
    with pytest.raises(ValidationError):
        ResearchCharter(charter_id="c1", question="q", scope="s", goals="not-a-list")
    with pytest.raises(ValidationError):
        ResearchCharter(
            charter_id="c1",
            question="q",
            scope="s",
            goals=["g"],
            stage="not_a_stage",
        )


def test_hypothesis_card_round_trip_preserves_empty_optional_fields() -> None:
    card = HypothesisCard(
        hypothesis_id="h1",
        statement="A 10-layer stack separates TE and TM.",
    )
    raw = card.model_dump_json()
    payload = json.loads(raw)
    assert payload["schema_version"] == "hypothesis-card.v1"
    assert payload["evidence_ids"] == []
    assert payload["experiment_ids"] == []
    assert payload["status"] == "proposed"
    restored = HypothesisCard.model_validate_json(raw)
    assert restored.model_dump() == card.model_dump()
    assert _canonical(restored) == _canonical(card)


def test_hypothesis_status_accepts_valid_strings_and_rejects_unknown() -> None:
    assert HypothesisCard.model_validate(
        {"hypothesis_id": "h1", "statement": "s", "status": "confirmed"}
    ).status == HypothesisStatus.confirmed
    with pytest.raises(ValidationError):
        HypothesisCard.model_validate(
            {"hypothesis_id": "h1", "statement": "s", "status": "maybe"}
        )


def test_experiment_card_requires_action_type_and_task_hash() -> None:
    with pytest.raises(ValidationError):
        ExperimentCard(experiment_id="e1", task_hash="t")  # action_type missing
    with pytest.raises(ValidationError):
        ExperimentCard(experiment_id="e1", action_type=ActionType.run_solver)  # task_hash missing
    with pytest.raises(ValidationError):
        ExperimentCard(
            experiment_id="e1",
            action_type="run_unknown_action",
            task_hash="t",
        )


def test_experiment_card_deterministic_serialization_and_empty_fields() -> None:
    fields = dict(
        experiment_id="e1",
        hypothesis_ids=["h1"],
        action_type=ActionType.run_optimizer,
        task_hash="t1",
        stage=ArticleStage.controlled_improvement,
        status=ExperimentStatus.candidate,
        parent_experiment_ids=["e0"],
        atomic_change={"variable": "thickness_layer_3", "delta_nm": 2.0},
        expected_discriminator={"metric": "R_mean", "direction": "lower"},
        budget_lease_id="lease-7",
        artifact_ids=["art-1"],
    )
    first = ExperimentCard(**fields)
    second = ExperimentCard(**fields)
    assert first.model_dump_json() == second.model_dump_json()
    assert _canonical(first) == _canonical(second)
    payload = json.loads(first.model_dump_json())
    assert payload["atomic_change"] == fields["atomic_change"]
    assert payload["expected_discriminator"] == fields["expected_discriminator"]
    restored = ExperimentCard.model_validate_json(first.model_dump_json())
    assert restored.model_dump() == first.model_dump()


def test_observation_card_round_trip_with_diagnosis_and_updates() -> None:
    card = ObservationCard(
        observation_id="o1",
        experiment_id="e1",
        status=ExperimentStatus.physically_valid_with_limits,
        metrics={"R_mean": 0.0065, "R_worst": 0.028},
        artifact_ids=["SIMULATION_RESULT.json"],
        failure_records=[{"code": "NONE", "recoverable": False}],
        failure_diagnosis={"root_class": "material_data", "code": "MATERIAL_RANGE_ERROR"},
        hypothesis_updates=[
            {
                "hypothesis_id": "h1",
                "from_status": "active",
                "to_status": "partially_supported",
                "reason": "wide-angle target not met",
            }
        ],
        summary="Verified best-effort trade-off.",
    )
    restored = ObservationCard.model_validate_json(card.model_dump_json())
    assert restored.model_dump() == card.model_dump()
    assert restored.failure_diagnosis == card.failure_diagnosis
    assert restored.hypothesis_updates == card.hypothesis_updates
    with pytest.raises(ValidationError):
        ObservationCard.model_validate(
            {"observation_id": "o2", "experiment_id": "e1", "status": "not_a_status"}
        )


def test_claim_figure_review_cards_round_trip() -> None:
    claim = ClaimCard(
        claim_id="cl1",
        statement="Mean reflectance below 0.8 percent is achievable.",
        strength=ClaimStrength.medium,
        scope="450-700 nm, 0-45 deg",
        status="evidence_bound",
        evidence_ids=["obs-1"],
        counter_evidence_ids=["obs-2"],
        source_artifact_ids=["cert-1"],
    )
    restored_claim = ClaimCard.model_validate_json(claim.model_dump_json())
    assert restored_claim.model_dump() == claim.model_dump()

    figure = FigureCard(
        figure_id="f1",
        story_role="spectral_response",
        chart_spec={"x": "wavelength_nm", "y": ["R_s", "R_p"]},
        data_source_artifact_ids=["SIMULATION_RESULT.json"],
    )
    restored_figure = FigureCard.model_validate_json(figure.model_dump_json())
    assert restored_figure.model_dump() == figure.model_dump()
    assert restored_figure.status.value == "planned"

    review = ReviewCard(
        review_id="r1",
        kind=ReviewKind.expression,
        severity=ReviewSeverity.minor,
        findings=["Rephrase the abstract."],
        decision=ArticleDecision.request_expression_revision,
        claim_ids=["cl1"],
        figure_ids=["f1"],
    )
    restored_review = ReviewCard.model_validate_json(review.model_dump_json())
    assert restored_review.model_dump() == review.model_dump()
    with pytest.raises(ValidationError):
        ReviewCard.model_validate({"review_id": "r2", "kind": "not_a_kind"})


def test_coverage_matrix_round_trip_and_not_run_semantics() -> None:
    matrix = CoverageMatrix(
        matrix_id="m1",
        rows=[
            CoverageRow(
                route_id="route_01",
                title="4-layer",
                coverage_status=CoverageStatus.completed,
                executed_iteration="iteration_01",
            ),
            CoverageRow(
                route_id="route_04",
                title="6-layer",
                coverage_status=CoverageStatus.not_run,
                not_run_reason="operational budget exhausted",
            ),
        ],
    )
    restored = CoverageMatrix.model_validate_json(matrix.model_dump_json())
    assert restored.model_dump() == matrix.model_dump()
    assert restored.rows[1].coverage_status == CoverageStatus.not_run
    assert restored.rows[1].not_run_reason == "operational budget exhausted"
    with pytest.raises(ValidationError):
        CoverageMatrix(matrix_id="m2", rows=[])
    with pytest.raises(ValidationError):
        CoverageMatrix.model_validate(
            {
                "matrix_id": "m3",
                "rows": [{"route_id": "r", "coverage_status": "maybe"}],
            }
        )


def test_article_node_payload_round_trip_and_empty_fields() -> None:
    payload = ArticleNodePayload(
        stage=ArticleStage.discriminative_experiments,
        hypothesis_ids=["h1"],
        atomic_change={"variable": "thickness_layer_3", "delta_nm": 2.0},
        expected_discriminator={"metric": "R_mean", "direction": "lower"},
        observation_ids=["o1"],
        artifact_ids=["SIMULATION_RESULT.json"],
        hypothesis_update={"from_status": "under_test", "to_status": "confirmed"},
        budget_lease_id="lease-9",
        failure_diagnosis={"code": "OPTIMIZER_FAILURE"},
        stop_decision=ArticleDecision.stop_no_progress,
        decision_reason="frontier stable",
        card_refs={"claims": ["cl1"], "figures": ["f1"], "reviews": ["r1"]},
        summary="Discriminative experiment complete.",
    )
    restored = ArticleNodePayload.model_validate_json(payload.model_dump_json())
    assert restored.model_dump() == payload.model_dump()
    raw = json.loads(payload.model_dump_json())
    assert raw["schema_version"] == "article-node.v1"
    assert raw["kind"] == "article"

    minimal = ArticleNodePayload()
    minimal_raw = json.loads(minimal.model_dump_json())
    assert minimal_raw["hypothesis_ids"] == []
    assert minimal_raw["atomic_change"] == {}
    assert minimal_raw["expected_discriminator"] == {}
    assert minimal_raw["observation_ids"] == []
    assert minimal_raw["artifact_ids"] == []
    assert minimal_raw["card_refs"] == {}
    assert ArticleNodePayload.model_validate_json(
        minimal.model_dump_json()
    ).model_dump() == minimal.model_dump()


def test_article_node_payload_rejects_bad_version_and_stage() -> None:
    with pytest.raises(ValidationError):
        ArticleNodePayload.model_validate({"schema_version": "article-node.v2"})
    with pytest.raises(ValidationError):
        ArticleNodePayload.model_validate({"stage": "not_a_stage"})


def test_article_node_payload_tolerates_unknown_extra_fields() -> None:
    payload = ArticleNodePayload.model_validate(
        {"stage": "baseline_experiments", "future_field": {"x": 1}}
    )
    assert "future_field" not in payload.model_dump()
    assert payload.stage == ArticleStage.baseline_experiments


def test_validate_article_event_rejects_unknown_type() -> None:
    with pytest.raises(ArticleEventValidationError, match="Unknown article event type"):
        validate_article_event(
            "article.not_real",
            {"schema_version": "article-event.v1"},
        )


def test_validate_article_event_rejects_wrong_schema_version() -> None:
    with pytest.raises(ArticleEventValidationError, match="schema_version"):
        validate_article_event(
            "article.stage",
            {"schema_version": "article-event.v2", "stage": "baseline_experiments"},
        )
    with pytest.raises(ArticleEventValidationError, match="schema_version"):
        validate_article_event("article.stage", {"stage": "baseline_experiments"})


def test_validate_article_event_rejects_malformed_payloads() -> None:
    with pytest.raises(ArticleEventValidationError, match="hypothesis_id"):
        validate_article_event(
            "article.hypothesis_update",
            {
                "schema_version": "article-event.v1",
                "from_status": "active",
                "to_status": "confirmed",
            },
        )
    with pytest.raises(ArticleEventValidationError, match="stage"):
        validate_article_event(
            "article.stage",
            {"schema_version": "article-event.v1", "stage": "invalid_stage"},
        )
    with pytest.raises(ArticleEventValidationError, match="observation_id"):
        validate_article_event(
            "article.observation",
            {"schema_version": "article-event.v1", "experiment_id": "e1"},
        )


def test_validate_article_event_normalizes_deterministically() -> None:
    first = validate_article_event(
        "article.decision",
        {
            "schema_version": "article-event.v1",
            "decision": ArticleDecision.stop_budget_exhausted,
            "reason": "budget used",
        },
    )
    second = validate_article_event(
        "article.decision",
        {
            "reason": "budget used",
            "decision": "stop_budget_exhausted",
            "schema_version": "article-event.v1",
        },
    )
    assert first == second
    assert first["decision"] == "stop_budget_exhausted"
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

    stage = validate_article_event(
        "article.stage",
        {"schema_version": "article-event.v1", "stage": "claim_ledger", "reason": ""},
    )
    assert stage == {
        "schema_version": "article-event.v1",
        "stage": "claim_ledger",
        "reason": "",
    }
