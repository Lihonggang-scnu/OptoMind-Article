from __future__ import annotations

import json
from pathlib import Path

import pytest

from optomind_optics.harness.article_claims import (
    ArticleCompletionAudit,
    ClaimCard,
    ClaimIntegrityError,
    ClaimLedgerError,
    ClaimLedgerResult,
    build_coverage_batches,
    build_claim_ledger,
)
from optomind_optics.harness.article_contracts import (
    ArticleDecision,
    ClaimStatus,
    ClaimStrength,
    HypothesisCard,
    HypothesisStatus,
    ObservationCard,
)
from optomind_optics.harness.article_director import ArticleDirector
from optomind_optics.harness.article_feedback import (
    ArticleFeedbackController,
    ArticleFeedbackResult,
    HypothesisUpdateDecision,
    ObservationContext,
)
from optomind_optics.harness.article_memory import (
    ArticleMemoryStore,
    FactRecord,
    FactStatus,
    RunMemoryRecord,
)
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
        target_observables=["mean reflectance", "worst-case reflectance"],
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


def _obs(
    observation_id: str,
    *,
    status: ExperimentStatus = ExperimentStatus.physically_valid,
    route_id: str = "baseline",
    metrics: dict | None = None,
    entries: list[dict] | None = None,
    artifact_ids: list[str] | None = None,
) -> ObservationCard:
    payload = {"route_id": route_id}
    if metrics:
        payload.update(metrics)
    return ObservationCard(
        observation_id=observation_id,
        experiment_id="exp-1",
        status=status,
        metrics=payload,
        artifact_ids=(
            artifact_ids if artifact_ids is not None else ["FINAL_RESULT.json"]
        ),
        hypothesis_updates=entries or [],
        summary="observation",
    )


def _context() -> ObservationContext:
    return ObservationContext(
        experiment_id="exp-1",
        hypothesis_ids=["hyp-01", "hyp-02"],
        route_id="baseline",
        expected_discriminator={"metric_keys": ["R_mean"]},
    )


def _entry(
    hypothesis_id: str,
    to_status: str,
    kind: str,
    reason: str,
) -> dict:
    return {
        "hypothesis_id": hypothesis_id,
        "to_status": to_status,
        "evidence_kind": kind,
        "reason": reason,
    }


def _prior(hypothesis_id: str, status: HypothesisStatus) -> HypothesisCard:
    return HypothesisCard(
        hypothesis_id=hypothesis_id, statement="s", status=status
    )


def _controller_feedback(
    plan,
    observation: ObservationCard,
    *,
    context=None,
    prior=None,
) -> ArticleFeedbackResult:
    return ArticleFeedbackController().update(
        plan,
        [observation],
        experiment_context=context,
        existing_hypotheses=prior or [],
    )


def _manual_feedback(
    *,
    hypothesis_id: str = "hyp-01",
    from_status: str = "proposed",
    to_status: str = "partially_supported",
    observation_id: str = "obs-1",
    observation_ids: tuple[str, ...] | None = None,
    experiment_id: str = "exp-1",
    artifacts: tuple[str, ...] = ("FINAL_RESULT.json",),
    route_id: str = "baseline",
    kind: str = "partial_support",
) -> ArticleFeedbackResult:
    obs_ids = list(observation_ids) if observation_ids is not None else (
        [observation_id] if observation_id else []
    )
    return ArticleFeedbackResult(
        controller_id=f"manual-{hypothesis_id}-{to_status}",
        hypothesis_updates=[
            HypothesisUpdateDecision(
                hypothesis_id=hypothesis_id,
                from_status=HypothesisStatus(from_status),
                to_status=HypothesisStatus(to_status),
                reason="manual",
                observation_ids=obs_ids,
                experiment_ids=[experiment_id] if experiment_id else [],
                artifact_ids=list(artifacts),
                route_ids=[route_id] if route_id else [],
                evidence_summary=f"evidence_kind={kind}",
            )
        ],
        coverage_updates=[],
        next_routes=[],
        stop_decision=ArticleDecision.continue_run,
        stop_reason="",
        provenance_observation_ids=obs_ids,
    )


def test_supported_and_confirmed_claims_become_source_bound_pairs() -> None:
    plan = _plan()
    support_obs = _obs(
        "obs-support",
        metrics={"R_mean": 0.004},
        entries=[
            _entry("hyp-01", "partially_supported", "partial_support", "improved")
        ],
    )
    confirmed_obs = _obs(
        "obs-confirm",
        metrics={
            "R_mean": 0.004,
            "discriminator_match": {
                "hyp-01": {"matched": True, "metric_keys": ["R_mean"]}
            },
        },
        entries=[
            _entry("hyp-01", "confirmed", "discriminator_confirmed", "matched")
        ],
    )
    partial_obs = _obs(
        "obs-partial",
        metrics={"R_mean": 0.004},
        entries=[
            _entry("hyp-02", "partially_supported", "partial_support", "improved")
        ],
    )
    feedback = [
        _controller_feedback(
            plan,
            support_obs,
        ),
        _controller_feedback(
            plan,
            confirmed_obs,
            context=_context(),
            prior=[_prior("hyp-01", HypothesisStatus.partially_supported)],
        ),
        _controller_feedback(plan, partial_obs),
    ]
    result = build_claim_ledger(
        plan, feedback, [support_obs, confirmed_obs, partial_obs]
    )
    assert isinstance(result, ClaimLedgerResult)
    assert result.validation_errors == []
    assert len(result.claims) == 2
    assert len(result.facts) == 2

    confirmed = next(
        item for item in result.claims if item.metadata["hypothesis_id"] == "hyp-01"
    )
    assert confirmed.status == ClaimStatus.supported
    assert confirmed.strength == ClaimStrength.high
    assert confirmed.source_artifact_ids == ["FINAL_RESULT.json"]
    assert confirmed.metadata["evidence_kinds"] == [
        "discriminator_confirmed",
        "partial_support",
    ]
    assert confirmed.metadata["observation_ids"] == ["obs-confirm", "obs-support"]
    assert confirmed.metadata["experiment_ids"] == ["exp-1"]
    assert confirmed.metadata["route_ids"] == ["baseline"]
    assert "Design a broadband" in confirmed.scope
    assert confirmed.metadata["writable"] is True
    assert confirmed.metadata["claim_id"] == confirmed.claim_id
    confirmed_fact = next(
        item for item in result.facts if item.fact_id == confirmed.metadata["fact_id"]
    )
    assert confirmed_fact.source_artifact_ids == ["FINAL_RESULT.json"]
    assert "scope:" in confirmed_fact.statement
    assert confirmed_fact.metadata["hypothesis_id"] == "hyp-01"
    assert confirmed_fact.metadata["claim_id"] == confirmed.claim_id
    assert confirmed_fact.metadata["observation_ids"] == [
        "obs-confirm",
        "obs-support",
    ]
    assert confirmed_fact.metadata["experiment_ids"] == ["exp-1"]
    assert confirmed_fact.metadata["route_ids"] == ["baseline"]
    assert confirmed_fact.metadata["evidence_kinds"] == [
        "discriminator_confirmed",
        "partial_support",
    ]
    assert confirmed_fact.metadata["source_artifact_ids"] == [
        "FINAL_RESULT.json"
    ]
    assert confirmed_fact.metadata["scope"]
    assert confirmed_fact.metadata["limits"]
    assert confirmed_fact.metadata["counterevidence"] == []

    partial = next(
        item for item in result.claims if item.metadata["hypothesis_id"] == "hyp-02"
    )
    assert partial.status == ClaimStatus.partially_supported
    assert partial.strength == ClaimStrength.medium
    fact = next(item for item in result.facts if item.fact_id == partial.metadata["fact_id"])
    assert fact.source_artifact_ids == ["FINAL_RESULT.json"]
    assert fact.status == FactStatus.active
    assert fact.metadata["claim_id"] == partial.claim_id


def test_confirmed_without_discriminator_evidence_is_capped_at_medium() -> None:
    plan = _plan()
    observation = _obs("obs-1", metrics={"R_mean": 0.004})
    feedback = [
        _manual_feedback(
            to_status="partially_supported",
            kind="partial_support",
        ),
        _manual_feedback(
            from_status="partially_supported",
            to_status="confirmed",
            kind="partial_support",
        ),
    ]
    result = build_claim_ledger(plan, feedback, [observation])
    claim = result.claims[0]
    assert claim.status == ClaimStatus.supported
    assert claim.strength == ClaimStrength.medium


def test_missing_source_artifact_is_non_writable_without_fact() -> None:
    plan = _plan()
    observation = _obs(
        "obs-1",
        metrics={"R_mean": 0.004},
        artifact_ids=[],
        entries=[
            _entry("hyp-01", "partially_supported", "partial_support", "improved")
        ],
    )
    feedback = [_controller_feedback(plan, observation)]
    result = build_claim_ledger(plan, feedback, [observation])
    claim = result.claims[0]
    assert claim.status == ClaimStatus.draft
    assert claim.source_artifact_ids == []
    assert claim.metadata["writable"] is False
    assert result.facts == []
    assert any("no source artifact" in item for item in result.normalization_warnings)


@pytest.mark.parametrize(
    "feedback_kwargs,error_fragment",
    [
        ({"observation_id": "ghost"}, "unknown observation"),
        (
            {"observation_id": "", "artifacts": ("FINAL_RESULT.json",)},
            "artifact_ids but no observation_ids",
        ),
        (
            {"observation_ids": ("obs-1", "obs-1")},
            "duplicate observation IDs",
        ),
        (
            {"experiment_id": "exp-WRONG"},
            "do not agree with resolved observations",
        ),
        (
            {"artifacts": ("NOT_IN_OBS.json",)},
            "are not in the union of",
        ),
        ({"route_id": "ghost"}, "unknown route"),
    ],
)
def test_unknown_or_mismatched_provenance_hard_blocks_without_persistence(
    tmp_path, feedback_kwargs, error_fragment
) -> None:
    plan = _plan()
    observation = _obs("obs-1", metrics={"R_mean": 0.004})
    feedback = [_manual_feedback(**feedback_kwargs)]
    memory = ArticleMemoryStore(tmp_path / "memory.sqlite")
    graph = ExperimentGraph(tmp_path / "graph.sqlite", "run-1")
    journal = tmp_path / "journal.json"
    result = build_claim_ledger(
        plan,
        feedback,
        [observation],
        memory_store=memory,
        graph=graph,
        run_id="run-1",
        journal_path=journal,
    )
    assert any(error_fragment in item for item in result.validation_errors)
    assert result.claims == []
    assert result.facts == []
    assert memory.run_memory_records() == []
    assert graph.export()["article_nodes"] == []
    assert not journal.exists()


def test_refuted_hypothesis_does_not_leak_as_active_fact() -> None:
    plan = _plan()
    support_obs = _obs(
        "obs-support",
        metrics={"R_mean": 0.004},
        entries=[
            _entry("hyp-01", "partially_supported", "partial_support", "improved")
        ],
    )
    observation = _obs(
        "obs-refute",
        metrics={
            "R_mean": 0.02,
            "discriminator_match": {
                "hyp-01": {"matched": False, "metric_keys": ["R_mean"]}
            },
        },
        entries=[
            _entry("hyp-01", "refuted", "disconfirming", "did not match")
        ],
    )
    feedback = [
        _controller_feedback(plan, support_obs),
        _controller_feedback(
            plan,
            observation,
            context=_context(),
            prior=[_prior("hyp-01", HypothesisStatus.partially_supported)],
        )
    ]
    result = build_claim_ledger(plan, feedback, [support_obs, observation])
    claim = result.claims[0]
    assert claim.status == ClaimStatus.refuted
    assert claim.metadata["writable"] is False
    assert claim.counter_evidence_ids == ["obs-refute"]
    assert claim.source_artifact_ids == ["FINAL_RESULT.json"]
    assert claim.metadata["counter_evidence_provenance"] == {
        "observation_ids": ["obs-refute"],
        "experiment_ids": ["exp-1"],
        "artifact_ids": ["FINAL_RESULT.json"],
    }
    assert result.facts == []


def test_fake_semantic_provider_validates_ids_and_fails_open(tmp_path) -> None:
    plan = _plan()
    plan = plan.model_copy(
        update={
            "charter": plan.charter.model_copy(
                update={"success_criteria": ["success criterion"]}
            )
        }
    )
    observation = _obs(
        "obs-1",
        metrics={"R_mean": 0.004},
        entries=[
            _entry("hyp-01", "partially_supported", "partial_support", "improved")
        ],
    )
    feedback = [_controller_feedback(plan, observation)]
    captured: list[dict] = []
    positive_claim_id = build_claim_ledger(plan, feedback, [observation]).claims[0].claim_id

    def provider(requests):
        captured.extend(requests)
        responses = []
        for batch in requests:
            goals = {}
            for goal in batch["goals"]:
                if goal["goal_id"] == "goal-01":
                    goals[goal["goal_id"]] = {
                        "claim_ids": [positive_claim_id],
                        "coverage": "covered",
                        "rationale": "claim supports this goal",
                        "unique_contribution": "verified claim",
                        "missing_work": "none",
                        "stop_reason": "done",
                    }
                elif goal["goal_id"] == "criterion-01":
                    goals[goal["goal_id"]] = {
                        "claim_ids": ["claim-hallucinated"],
                            "coverage": "covered",
                            "rationale": "bad",
                            "expected_value_of_more_work": "recheck",
                            "missing_work": "recheck",
                            "stop_reason": "done",
                    }
                else:
                    goals[goal["goal_id"]] = {
                        "claim_ids": [],
                        "coverage": "gap",
                        "rationale": "not covered",
                        "missing_work": "more work",
                        "stop_reason": "gap",
                    }
            responses.append({"goals": goals})
        return responses

    result = build_claim_ledger(
        plan,
        feedback,
        [observation],
        semantic_provider=provider,
    )
    assert result.semantic_coverage_available is True
    assert result.claims
    audit_rows = result.audit.rows
    goal_ids = [row.goal_id for row in audit_rows]
    assert "goal-01" in goal_ids and "criterion-01" in goal_ids
    goal = next(row for row in audit_rows if row.goal_id == "goal-01")
    assert goal.coverage == "covered"
    assert goal.claim_ids == [positive_claim_id]
    assert goal.unique_contribution == "verified claim"
    criterion = next(row for row in audit_rows if row.goal_id == "criterion-01")
    assert criterion.coverage == "unknown"
    assert any("unknown claim IDs" in item for item in result.audit.semantic_warnings)
    assert any(
        "hallucinated" in item for item in result.audit.semantic_warnings
    )


def test_batching_handles_more_than_one_batch_without_truncation() -> None:
    plan = _plan()
    plan = plan.model_copy(
        update={
            "charter": plan.charter.model_copy(
                update={
                    "success_criteria": [
                        f"success criterion {index}" for index in range(45)
                    ]
                }
            )
        }
    )
    observation = _obs(
        "obs-1",
        metrics={"R_mean": 0.004},
        entries=[
            _entry("hyp-01", "partially_supported", "partial_support", "improved")
        ],
    )
    feedback = [_controller_feedback(plan, observation)]
    captured: list[dict] = []

    def provider(requests):
        captured.extend(requests)
        return [
            {
                "goals": {
                    goal["goal_id"]: {
                        "claim_ids": [],
                        "coverage": "gap",
                        "rationale": "uncovered",
                        "missing_work": "more work",
                        "stop_reason": "gap",
                    }
                    for goal in batch["goals"]
                }
            }
            for batch in requests
        ]

    result = build_claim_ledger(
        plan,
        feedback,
        [observation],
        semantic_provider=provider,
    )
    assert len(captured) == 3
    assert all(batch["batch_count"] == 3 for batch in captured)
    positive = captured[0]["claims"]
    assert positive
    assert all(
        item["status"] in {"partially_supported", "supported"}
        and item["source_count"] >= 1
        for item in positive
    )
    assert "existing_claims" not in captured[0]["goals"][0]
    assert set(captured[0]["goals"][0].keys()) == {
        "goal_id",
        "label",
        "kind",
        "allowed_positive_claim_ids",
        "allowed_coverage_levels",
    }
    assert captured[0]["goals"][0]["allowed_positive_claim_ids"] == [
        item["claim_id"] for item in positive
    ]
    assert len(result.audit.rows) == 2 + 45
    goal_ids = [row.goal_id for row in result.audit.rows]
    assert "criterion-45" in goal_ids
    assert result.audit.semantic_coverage_available is True


def test_covered_or_partial_requires_claim_ids() -> None:
    plan = _plan()
    plan = plan.model_copy(
        update={
            "charter": plan.charter.model_copy(
                update={"success_criteria": ["success criterion"]}
            )
        }
    )
    observation = _obs(
        "obs-1",
        metrics={"R_mean": 0.004},
        entries=[
            _entry("hyp-01", "partially_supported", "partial_support", "improved")
        ],
    )
    feedback = [_controller_feedback(plan, observation)]

    def provider(requests):
        return [
            {
                "goals": {
                    goal["goal_id"]: {
                        "claim_ids": [],
                        "coverage": "covered",
                        "rationale": "empty claims",
                        "missing_work": "none",
                        "stop_reason": "done",
                    }
                    for goal in batch["goals"]
                }
            }
            for batch in requests
        ]

    result = build_claim_ledger(
        plan,
        feedback,
        [observation],
        semantic_provider=provider,
    )
    assert result.claims
    assert all(row.coverage == "unknown" for row in result.audit.rows)
    assert result.audit.semantic_coverage_available is False
    assert any("requires claim_ids" in item for item in result.audit.semantic_warnings)


def test_multi_observation_artifact_union_is_authoritative() -> None:
    plan = _plan()
    first_obs = _obs(
        "obs-a",
        metrics={"R_mean": 0.004},
        artifact_ids=["FINAL_RESULT.json"],
    )
    second_obs = _obs(
        "obs-b",
        metrics={"R_mean": 0.004},
        artifact_ids=["PHYSICS_ACCEPTANCE_CERTIFICATE.json"],
    )
    feedback = [
        _manual_feedback(
            observation_ids=("obs-a", "obs-b"),
            artifacts=(
                "FINAL_RESULT.json",
                "PHYSICS_ACCEPTANCE_CERTIFICATE.json",
            ),
        )
    ]
    result = build_claim_ledger(plan, feedback, [first_obs, second_obs])
    assert result.validation_errors == []
    claim = result.claims[0]
    assert claim.metadata["writable"] is True
    assert claim.source_artifact_ids == [
        "FINAL_RESULT.json",
        "PHYSICS_ACCEPTANCE_CERTIFICATE.json",
    ]
    assert claim.metadata["observation_ids"] == ["obs-a", "obs-b"]


def test_forged_discriminator_confirmed_never_high() -> None:
    plan = _plan()
    observation = _obs(
        "obs-1",
        metrics={"R_mean": 0.02},
    )
    feedback = [
        _manual_feedback(to_status="partially_supported", kind="partial_support"),
        _manual_feedback(
            from_status="partially_supported",
            to_status="confirmed",
            kind="discriminator_confirmed",
        ),
    ]
    result = build_claim_ledger(plan, feedback, [observation])
    assert any(
        "forged discriminator_confirmed provenance" in item
        for item in result.validation_errors
    )
    assert result.claims == []
    assert result.facts == []


def test_claim_and_fact_ids_do_not_collide_across_plans() -> None:
    first_plan = _plan()
    second_result = ArticleDirector().plan(
        "Design a polarizing beamsplitter over 500-650 nm.",
        OpticalProblemAnalysis(
            problem_id="problem-2",
            original_request="Design a polarizing beamsplitter.",
            normalized_request_english=(
                "Design a polarizing beamsplitter over 500-650 nm."
            ),
            primary_intent=ResearchIntent.design,
            compatibility=TMMCompatibility.compatible,
            compatibility_reason="planar multilayer stack within the TMM domain",
            needs_method_research=True,
            wavelengths_nm=[(500.0, 650.0)],
            target_observables=["TE reflectance", "TM transmittance"],
        ),
        _report(),
        force_mock=True,
    )
    assert second_result.status == "planned" and second_result.plan is not None
    second_plan = second_result.plan
    assert first_plan.plan_id != second_plan.plan_id

    first_obs = _obs(
        "obs-1",
        metrics={"R_mean": 0.004},
        entries=[
            _entry("hyp-01", "partially_supported", "partial_support", "improved")
        ],
    )
    first_feedback = [_controller_feedback(first_plan, first_obs)]
    second_obs = _obs(
        "obs-1",
        metrics={"R_mean": 0.004},
        entries=[
            _entry("hyp-01", "partially_supported", "partial_support", "improved")
        ],
    )
    second_feedback = [_controller_feedback(second_plan, second_obs)]
    first_ledger = build_claim_ledger(
        first_plan, first_feedback, [first_obs]
    )
    second_ledger = build_claim_ledger(
        second_plan, second_feedback, [second_obs]
    )
    assert first_ledger.ledger_id != second_ledger.ledger_id
    assert (
        first_ledger.claims[0].claim_id != second_ledger.claims[0].claim_id
    )
    assert first_ledger.facts[0].fact_id != second_ledger.facts[0].fact_id


def test_persistence_resume_after_partial_claim_events(tmp_path) -> None:
    plan = _plan()
    observations = [
        _obs(
            "obs-1",
            metrics={"R_mean": 0.004},
            entries=[
                _entry("hyp-01", "partially_supported", "partial_support", "a")
            ],
        ),
        _obs(
            "obs-2",
            metrics={"R_mean": 0.004},
            entries=[
                _entry("hyp-02", "partially_supported", "partial_support", "b")
            ],
        ),
    ]
    feedback = [
        _controller_feedback(plan, observations[0]),
        _controller_feedback(plan, observations[1]),
    ]
    memory = ArticleMemoryStore(tmp_path / "memory.sqlite")
    graph = ExperimentGraph(tmp_path / "graph.sqlite", "run-1")
    journal = tmp_path / "journal.json"
    original_event = graph.record_article_event
    calls = {"count": 0}

    def failing_event(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("claim event failed")
        return original_event(*args, **kwargs)

    graph.record_article_event = failing_event  # type: ignore[method-assign]
    with pytest.raises(ClaimLedgerError, match="claim persistence failed"):
        build_claim_ledger(
            plan,
            feedback,
            observations,
            memory_store=memory,
            graph=graph,
            run_id="run-1",
            journal_path=journal,
        )
    ledger_id = next(
        key for key in json.loads(journal.read_text(encoding="utf-8"))
    )
    node = graph.article_node(f"claims-{ledger_id}")
    assert len(
        [e for e in node["history"] if e["event_type"] == "article.claim"]
    ) == 1
    graph.record_article_event = original_event  # type: ignore[method-assign]

    result = build_claim_ledger(
        plan,
        feedback,
        observations,
        memory_store=memory,
        graph=graph,
        run_id="run-1",
        journal_path=journal,
    )
    final_node = graph.article_node(f"claims-{ledger_id}")
    assert len(
        [e for e in final_node["history"] if e["event_type"] == "article.claim"]
    ) == 2
    assert len(memory.fact_records()) == 2
    assert json.loads(journal.read_text(encoding="utf-8"))[ledger_id][
        "status"
    ] == "completed"


def test_persistence_resume_after_partial_memory_records(tmp_path) -> None:
    plan = _plan()
    observations = [
        _obs(
            "obs-1",
            metrics={"R_mean": 0.004},
            entries=[
                _entry("hyp-01", "partially_supported", "partial_support", "a")
            ],
        ),
        _obs(
            "obs-2",
            metrics={"R_mean": 0.004},
            entries=[
                _entry("hyp-02", "partially_supported", "partial_support", "b")
            ],
        ),
    ]
    feedback = [
        _controller_feedback(plan, observations[0]),
        _controller_feedback(plan, observations[1]),
    ]
    memory = ArticleMemoryStore(tmp_path / "memory.sqlite")
    graph = ExperimentGraph(tmp_path / "graph.sqlite", "run-1")
    journal = tmp_path / "journal.json"
    original_add = memory.add_run_memory
    calls = {"count": 0}

    def failing_add(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("claim memory record failed")
        return original_add(*args, **kwargs)

    memory.add_run_memory = failing_add  # type: ignore[method-assign]
    with pytest.raises(ClaimLedgerError, match="claim persistence failed"):
        build_claim_ledger(
            plan,
            feedback,
            observations,
            memory_store=memory,
            graph=graph,
            run_id="run-1",
            journal_path=journal,
        )
    ledger_id = next(
        key for key in json.loads(journal.read_text(encoding="utf-8"))
    )
    assert len(memory.run_memory_records()) == 1
    assert len(memory.fact_records()) == 2
    memory.add_run_memory = original_add  # type: ignore[method-assign]

    build_claim_ledger(
        plan,
        feedback,
        observations,
        memory_store=memory,
        graph=graph,
        run_id="run-1",
        journal_path=journal,
    )
    assert len(memory.run_memory_records()) == 3  # 2 claim copies + 1 audit
    assert len(memory.fact_records()) == 2
    assert json.loads(journal.read_text(encoding="utf-8"))[ledger_id][
        "status"
    ] == "completed"


def test_memory_full_payload_conflict_fails_closed(tmp_path) -> None:
    plan = _plan()
    observation = _obs(
        "obs-1",
        metrics={"R_mean": 0.004},
        entries=[
            _entry("hyp-01", "partially_supported", "partial_support", "improved")
        ],
    )
    feedback = [_controller_feedback(plan, observation)]
    preflight = build_claim_ledger(plan, feedback, [observation])
    claim_id = preflight.claims[0].claim_id
    memory = ArticleMemoryStore(tmp_path / "memory.sqlite")
    graph = ExperimentGraph(tmp_path / "graph.sqlite", "run-1")
    journal = tmp_path / "journal.json"
    memory.add_run_memory(
        RunMemoryRecord(
            memory_id=f"claim-{claim_id}",
            run_id="run-1",
            event_type="article_claim_ledger",
            graph_node_id=f"claims-{preflight.ledger_id}",
            artifact_ids=["TAMPERED.json"],
            operational_note="same note",
        )
    )
    with pytest.raises(ClaimLedgerError):
        build_claim_ledger(
            plan,
            feedback,
            [observation],
            memory_store=memory,
            graph=graph,
            run_id="run-1",
            journal_path=journal,
        )


def test_prompt_and_payload_contract_agree(tmp_path) -> None:
    prompt_path = (
        Path(__file__).resolve().parents[1]
        / "prompts"
        / "optical_harness"
        / "Article Claim Coverage Auditor.txt"
    )
    text = prompt_path.read_text(encoding="utf-8")
    assert 'a "goals" object' in text
    assert 'a single read-only "claims" table' in text or '"claims" table' in text
    assert '"question"' in text and '"charter_scope"' in text
    assert '"allowed_positive_claim_ids"' in text
    assert '"unique_contribution"' in text
    assert '"expected_value_of_more_work"' in text
    assert '"stop_reason"' in text
    assert '"rationale"' in text
    assert "existing_claim_ids" not in text

    plan = _plan()
    observation = _obs(
        "obs-1",
        metrics={"R_mean": 0.004},
        entries=[
            _entry("hyp-01", "partially_supported", "partial_support", "improved")
        ],
    )
    feedback = [_controller_feedback(plan, observation)]
    result = build_claim_ledger(plan, feedback, [observation])
    batch = build_coverage_batches(plan, result.claims)[0]
    assert set(batch.keys()) == {
        "task",
        "batch_index",
        "batch_count",
        "question",
        "charter_scope",
        "claims",
        "goals",
    }
    assert batch["question"] == plan.charter.question
    assert batch["charter_scope"] == plan.charter.scope
    assert set(batch["goals"][0].keys()) == {
        "goal_id",
        "label",
        "kind",
        "allowed_positive_claim_ids",
        "allowed_coverage_levels",
    }


def test_reopen_reconstructs_full_claims_and_audit(tmp_path) -> None:
    plan = _plan()
    observation = _obs(
        "obs-1",
        metrics={"R_mean": 0.004},
        entries=[
            _entry("hyp-01", "partially_supported", "partial_support", "improved")
        ],
    )
    feedback = [_controller_feedback(plan, observation)]
    memory = ArticleMemoryStore(tmp_path / "memory.sqlite")
    graph = ExperimentGraph(tmp_path / "graph.sqlite", "run-1")
    result = build_claim_ledger(
        plan,
        feedback,
        [observation],
        memory_store=memory,
        graph=graph,
        run_id="run-1",
    )
    reopened = ArticleMemoryStore(tmp_path / "memory.sqlite")
    claim_records = [
        item
        for item in reopened.run_memory_records()
        if item.event_type == "article_claim_ledger"
    ]
    reconstructed_claims = [
        ClaimCard.model_validate_json(item.operational_note)
        for item in claim_records
    ]
    reconstructed_payloads = sorted(
        (item.model_dump(mode="json") for item in reconstructed_claims),
        key=lambda payload: payload["claim_id"],
    )
    expected_payloads = sorted(
        (item.model_dump(mode="json") for item in result.claims),
        key=lambda payload: payload["claim_id"],
    )
    assert reconstructed_payloads == expected_payloads
    audit_records = [
        item
        for item in reopened.run_memory_records()
        if item.event_type == "article_completion_audit"
    ]
    assert len(audit_records) == 1
    reconstructed_audit = ArticleCompletionAudit.model_validate_json(
        audit_records[0].operational_note
    )
    assert reconstructed_audit.model_dump(mode="json") == result.audit.model_dump(
        mode="json"
    )


def test_high_strength_requires_non_empty_charter_scope() -> None:
    plan = _plan()
    plan = plan.model_copy(
        update={"charter": plan.charter.model_copy(update={"scope": ""})}
    )
    observation = _obs(
        "obs-1",
        metrics={
            "R_mean": 0.004,
            "discriminator_match": {
                "hyp-01": {"matched": True, "metric_keys": ["R_mean"]}
            },
        },
        entries=[
            _entry("hyp-01", "confirmed", "discriminator_confirmed", "matched")
        ],
    )
    feedback = [
        _manual_feedback(to_status="partially_supported", kind="partial_support"),
        _manual_feedback(
            from_status="partially_supported",
            to_status="confirmed",
            kind="discriminator_confirmed",
        ),
    ]
    result = build_claim_ledger(plan, feedback, [observation])
    assert result.validation_errors == []
    claim = result.claims[0]
    assert claim.status == ClaimStatus.supported
    assert claim.strength == ClaimStrength.medium


def test_claim_ids_include_statement_semantics() -> None:
    plan = _plan()
    hypothesis = plan.hypotheses[0]
    changed = hypothesis.model_copy(
        update={"statement": "A completely different scientific statement."}
    )
    changed_plan = plan.model_copy(
        update={
            "hypotheses": [
                changed if item.hypothesis_id == hypothesis.hypothesis_id else item
                for item in plan.hypotheses
            ]
        }
    )
    assert changed_plan.plan_id == plan.plan_id
    observation = _obs(
        "obs-1",
        metrics={"R_mean": 0.004},
        entries=[
            _entry("hyp-01", "partially_supported", "partial_support", "improved")
        ],
    )
    first = build_claim_ledger(
        plan, [_controller_feedback(plan, observation)], [observation]
    )
    second = build_claim_ledger(
        changed_plan,
        [_controller_feedback(changed_plan, observation)],
        [observation],
    )
    assert first.claims[0].claim_id != second.claims[0].claim_id
    assert first.facts[0].fact_id != second.facts[0].fact_id


def test_duplicate_input_observation_ids_hard_block(tmp_path) -> None:
    plan = _plan()
    observation = _obs(
        "obs-1",
        metrics={"R_mean": 0.004},
        entries=[
            _entry("hyp-01", "partially_supported", "partial_support", "improved")
        ],
    )
    feedback = [_controller_feedback(plan, observation)]
    result = build_claim_ledger(plan, feedback, [observation, observation])
    assert any("duplicate ObservationCard IDs in input" in item for item in result.validation_errors)
    assert result.claims == []


def test_audit_persistence_recovers_and_is_idempotent(tmp_path) -> None:
    plan = _plan()
    observation = _obs(
        "obs-1",
        metrics={"R_mean": 0.004},
        entries=[
            _entry("hyp-01", "partially_supported", "partial_support", "improved")
        ],
    )
    feedback = [_controller_feedback(plan, observation)]
    memory = ArticleMemoryStore(tmp_path / "memory.sqlite")
    graph = ExperimentGraph(tmp_path / "graph.sqlite", "run-1")
    journal = tmp_path / "journal.json"
    original_add = memory.add_run_memory
    calls = {"count": 0}

    def failing_audit(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:  # claim copy ok, audit record fails
            raise RuntimeError("audit record failed")
        return original_add(*args, **kwargs)

    memory.add_run_memory = failing_audit  # type: ignore[method-assign]
    with pytest.raises(ClaimLedgerError, match="claim persistence failed"):
        build_claim_ledger(
            plan,
            feedback,
            [observation],
            memory_store=memory,
            graph=graph,
            run_id="run-1",
            journal_path=journal,
        )
    assert len(memory.fact_records()) == 1
    audit_records = [
        item
        for item in memory.run_memory_records()
        if item.event_type == "article_completion_audit"
    ]
    assert audit_records == []
    memory.add_run_memory = original_add  # type: ignore[method-assign]

    result = build_claim_ledger(
        plan,
        feedback,
        [observation],
        memory_store=memory,
        graph=graph,
        run_id="run-1",
        journal_path=journal,
    )
    reopened = ArticleMemoryStore(tmp_path / "memory.sqlite")
    audit_records = [
        item
        for item in reopened.run_memory_records()
        if item.event_type == "article_completion_audit"
    ]
    assert len(audit_records) == 1
    assert ArticleCompletionAudit.model_validate_json(
        audit_records[0].operational_note
    ).model_dump(mode="json") == result.audit.model_dump(mode="json")

    # Idempotent exact retry: no duplicate audit record.
    build_claim_ledger(
        plan,
        feedback,
        [observation],
        memory_store=memory,
        graph=graph,
        run_id="run-1",
        journal_path=journal,
    )
    reopened = ArticleMemoryStore(tmp_path / "memory.sqlite")
    audit_records = [
        item
        for item in reopened.run_memory_records()
        if item.event_type == "article_completion_audit"
    ]
    assert len(audit_records) == 1


def test_audit_full_payload_conflict_fails_closed(tmp_path) -> None:
    plan = _plan()
    observation = _obs(
        "obs-1",
        metrics={"R_mean": 0.004},
        entries=[
            _entry("hyp-01", "partially_supported", "partial_support", "improved")
        ],
    )
    feedback = [_controller_feedback(plan, observation)]
    preflight = build_claim_ledger(plan, feedback, [observation])
    memory = ArticleMemoryStore(tmp_path / "memory.sqlite")
    graph = ExperimentGraph(tmp_path / "graph.sqlite", "run-1")
    memory.add_run_memory(
        RunMemoryRecord(
            memory_id=f"audit-{preflight.audit.audit_id}",
            run_id="run-1",
            event_type="article_completion_audit",
            graph_node_id=f"claims-{preflight.ledger_id}",
            artifact_ids=[],
            operational_note=json.dumps({"tampered": True}),
        )
    )
    with pytest.raises(ClaimLedgerError):
        build_claim_ledger(
            plan,
            feedback,
            [observation],
            memory_store=memory,
            graph=graph,
            run_id="run-1",
            journal_path=tmp_path / "journal.json",
        )


def test_illegal_stage7_transitions_hard_block_without_persistence(tmp_path) -> None:
    plan = _plan()
    observation = _obs("obs-1", metrics={"R_mean": 0.004})
    memory = ArticleMemoryStore(tmp_path / "memory.sqlite")
    graph = ExperimentGraph(tmp_path / "graph.sqlite", "run-1")
    journal = tmp_path / "journal.json"

    direct_confirm = build_claim_ledger(
        plan,
        [_manual_feedback(to_status="confirmed", kind="partial_support")],
        [observation],
        memory_store=memory,
        graph=graph,
        run_id="run-1",
        journal_path=journal,
    )
    assert any("illegal transition proposed -> confirmed" in item for item in direct_confirm.validation_errors)
    assert direct_confirm.claims == []
    assert direct_confirm.facts == []
    assert memory.run_memory_records() == []
    assert graph.export()["article_nodes"] == []
    assert not journal.exists()

    terminal_exit = build_claim_ledger(
        plan,
        [
            _manual_feedback(to_status="partially_supported", kind="partial_support"),
            _manual_feedback(
                from_status="partially_supported",
                to_status="confirmed",
                kind="partial_support",
            ),
            _manual_feedback(
                from_status="confirmed",
                to_status="active",
                kind="partial_support",
            ),
        ],
        [observation],
    )
    assert any(
        "illegal transition confirmed -> active" in item
        for item in terminal_exit.validation_errors
    )
    assert terminal_exit.claims == []


def test_untrusted_feedback_results_are_rejected(tmp_path) -> None:
    plan = _plan()
    observation = _obs(
        "obs-1",
        metrics={"R_mean": 0.004},
        entries=[
            _entry("hyp-01", "partially_supported", "partial_support", "improved")
        ],
    )
    with_errors = _manual_feedback().model_copy(
        update={"validation_errors": ["tampered"]}
    )
    result = build_claim_ledger(plan, [with_errors], [observation])
    assert any("not trusted ledger input" in item for item in result.validation_errors)
    assert result.claims == []

    blocker = _manual_feedback().model_copy(
        update={"stop_decision": ArticleDecision.stop_hard_blocker}
    )
    result = build_claim_ledger(plan, [blocker], [observation])
    assert any("not trusted ledger input" in item for item in result.validation_errors)
    assert result.claims == []


def test_refuted_claim_ids_include_refutation_provenance() -> None:
    plan = _plan()
    support = _obs(
        "obs-support",
        metrics={"R_mean": 0.004},
        entries=[
            _entry("hyp-01", "partially_supported", "partial_support", "improved")
        ],
    )
    refute_a = _obs(
        "obs-refute-a",
        metrics={
            "R_mean": 0.02,
            "discriminator_match": {
                "hyp-01": {"matched": False, "metric_keys": ["R_mean"]}
            },
        },
        artifact_ids=["CERT_A.json"],
        entries=[
            _entry("hyp-01", "refuted", "disconfirming", "did not match")
        ],
    )
    refute_b = _obs(
        "obs-refute-b",
        metrics={
            "R_mean": 0.03,
            "discriminator_match": {
                "hyp-01": {"matched": False, "metric_keys": ["R_mean"]}
            },
        },
        artifact_ids=["CERT_B.json"],
        entries=[
            _entry("hyp-01", "refuted", "disconfirming", "did not match")
        ],
    )
    first = build_claim_ledger(
        plan,
        [
            _controller_feedback(plan, support),
            _controller_feedback(
                plan,
                refute_a,
                context=_context(),
                prior=[_prior("hyp-01", HypothesisStatus.partially_supported)],
            ),
        ],
        [support, refute_a],
    )
    second = build_claim_ledger(
        plan,
        [
            _controller_feedback(plan, support),
            _controller_feedback(
                plan,
                refute_b,
                context=_context(),
                prior=[_prior("hyp-01", HypothesisStatus.partially_supported)],
            ),
        ],
        [support, refute_b],
    )
    assert first.claims[0].status == ClaimStatus.refuted
    assert second.claims[0].status == ClaimStatus.refuted
    assert first.claims[0].claim_id != second.claims[0].claim_id


def test_non_unknown_semantic_rows_get_conservative_fill() -> None:
    plan = _plan()
    plan = plan.model_copy(
        update={
            "charter": plan.charter.model_copy(
                update={"success_criteria": ["success criterion"]}
            )
        }
    )
    observation = _obs(
        "obs-1",
        metrics={"R_mean": 0.004},
        entries=[
            _entry("hyp-01", "partially_supported", "partial_support", "improved")
        ],
    )
    feedback = [_controller_feedback(plan, observation)]

    def provider(requests):
        return [
            {
                "goals": {
                    goal["goal_id"]: {
                        "claim_ids": [],
                        "coverage": "gap",
                        "missing_work": "more work",
                        "stop_reason": "gap",
                    }
                    for goal in batch["goals"]
                }
            }
            for batch in requests
        ]

    result = build_claim_ledger(
        plan,
        feedback,
        [observation],
        semantic_provider=provider,
    )
    gap_rows = [row for row in result.audit.rows if row.coverage == "gap"]
    assert gap_rows
    assert all(row.unique_contribution for row in gap_rows)
    assert all(row.rationale for row in gap_rows)
    assert any(
        "source-bound claims" in row.unique_contribution for row in gap_rows
    )


def test_persistence_is_idempotent_and_resumes_after_failure(tmp_path) -> None:
    plan = _plan()
    observation = _obs(
        "obs-1",
        metrics={"R_mean": 0.004},
        entries=[
            _entry("hyp-01", "partially_supported", "partial_support", "improved")
        ],
    )
    feedback = [_controller_feedback(plan, observation)]
    memory = ArticleMemoryStore(tmp_path / "memory.sqlite")
    graph = ExperimentGraph(tmp_path / "graph.sqlite", "run-1")
    journal = tmp_path / "journal.json"
    original_create = graph.create_article_node

    def failing_create(*args, **kwargs):
        raise RuntimeError("graph write failed")

    graph.create_article_node = failing_create  # type: ignore[method-assign]
    with pytest.raises(ClaimLedgerError, match="claim persistence failed"):
        build_claim_ledger(
            plan,
            feedback,
            [observation],
            memory_store=memory,
            graph=graph,
            run_id="run-1",
            journal_path=journal,
        )
    state = json.loads(journal.read_text(encoding="utf-8"))
    ledger_id = list(state)[0]
    assert state[ledger_id]["status"] == "in_progress"
    assert state[ledger_id]["graph_written"] is False
    assert memory.run_memory_records() == []
    graph.create_article_node = original_create  # type: ignore[method-assign]

    result = build_claim_ledger(
        plan,
        feedback,
        [observation],
        memory_store=memory,
        graph=graph,
        run_id="run-1",
        journal_path=journal,
    )
    assert result.ledger_id == ledger_id
    assert json.loads(journal.read_text(encoding="utf-8"))[ledger_id][
        "status"
    ] == "completed"
    node = graph.article_node(f"claims-{ledger_id}")
    assert len(
        [e for e in node["history"] if e["event_type"] == "article.claim"]
    ) == len(result.claims)
    assert len(memory.fact_records()) == len(result.facts)
    memory_count = len(memory.run_memory_records())
    history_len = len(node["history"])

    retry = build_claim_ledger(
        plan,
        feedback,
        [observation],
        memory_store=memory,
        graph=graph,
        run_id="run-1",
        journal_path=journal,
    )
    assert retry.ledger_id == result.ledger_id
    assert len(memory.run_memory_records()) == memory_count
    assert len(graph.article_node(f"claims-{ledger_id}")["history"]) == history_len


def test_conflicting_fact_or_claim_event_fails_closed(tmp_path) -> None:
    plan = _plan()
    observation = _obs(
        "obs-1",
        metrics={"R_mean": 0.004},
        entries=[
            _entry("hyp-01", "partially_supported", "partial_support", "improved")
        ],
    )
    feedback = [_controller_feedback(plan, observation)]
    memory = ArticleMemoryStore(tmp_path / "memory.sqlite")
    graph = ExperimentGraph(tmp_path / "graph.sqlite", "run-1")
    journal = tmp_path / "journal.json"
    first_result = build_claim_ledger(
        plan,
        feedback,
        [observation],
        memory_store=memory,
        graph=graph,
        run_id="run-1",
        journal_path=journal,
    )
    ledger_id = next(
        key
        for key in json.loads(journal.read_text(encoding="utf-8"))
    )
    node_id = f"claims-{ledger_id}"
    graph.record_article_event(
        node_id,
        "article.claim",
        {
            "schema_version": "article-event.v1",
            "claim_id": first_result.claims[0].claim_id,
            "status": "refuted",
        },
    )
    with pytest.raises(ClaimLedgerError):
        build_claim_ledger(
            plan,
            feedback,
            [observation],
            memory_store=memory,
            graph=graph,
            run_id="run-1",
            journal_path=tmp_path / "journal2.json",
        )


def test_deterministic_serialization_and_exact_retry() -> None:
    plan = _plan()
    observation = _obs(
        "obs-1",
        metrics={"R_mean": 0.004},
        entries=[
            _entry("hyp-01", "partially_supported", "partial_support", "improved")
        ],
    )
    feedback = [_controller_feedback(plan, observation)]
    first = build_claim_ledger(plan, feedback, [observation])
    second = build_claim_ledger(plan, feedback, [observation])
    assert first.ledger_id == second.ledger_id
    assert first.model_dump_json() == second.model_dump_json()
    assert first.audit.audit_id == second.audit.audit_id
