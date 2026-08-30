from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest
from pydantic import ValidationError

from optomind_optics.harness.article_architecture import (
    ArchitectureProviderResult,
    ArtifactDescriptor,
    ArticleArchitectureResult,
    MissingWorkHandoff,
    QwenArticleArchitecturePlanner,
    StoryCandidate,
    build_article_architecture,
    build_architecture_payload,
    compute_architecture_id,
)
from optomind_optics.harness.article_claims import (
    ArticleCompletionAudit,
    CompletionAuditRow,
    build_claim_ledger,
)
from optomind_optics.harness.article_contracts import (
    ClaimStatus,
    HypothesisStatus,
    ObservationCard,
)
from optomind_optics.harness.article_director import ArticleDirector
from optomind_optics.harness.article_feedback import ArticleFeedbackController
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
        preferred_behaviors=["reflectance below target"],
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


def _ledger():
    plan = _plan()
    observation = ObservationCard(
        observation_id="obs-1",
        experiment_id="exp-1",
        status=ExperimentStatus.physically_valid,
        metrics={
            "route_id": "baseline",
            "R_mean": 0.004,
        },
        artifact_ids=["FINAL_RESULT.json"],
        hypothesis_updates=[
            {
                "hypothesis_id": "hyp-01",
                "to_status": "partially_supported",
                "evidence_kind": "partial_support",
                "reason": "improved",
            }
        ],
        summary="observation",
    )
    feedback = ArticleFeedbackController().update(plan, [observation])
    ledger = build_claim_ledger(plan, [feedback], [observation])
    assert ledger.validation_errors == []
    return plan, ledger


def _two_claim_ledger():
    plan = _plan()
    obs_1 = ObservationCard(
        observation_id="obs-1",
        experiment_id="exp-1",
        status=ExperimentStatus.physically_valid,
        metrics={"route_id": "baseline", "R_mean": 0.004},
        artifact_ids=["A.json"],
        hypothesis_updates=[
            {
                "hypothesis_id": "hyp-01",
                "to_status": "partially_supported",
                "evidence_kind": "partial_support",
                "reason": "improved",
            }
        ],
        summary="first",
    )
    obs_2 = ObservationCard(
        observation_id="obs-2",
        experiment_id="exp-2",
        status=ExperimentStatus.physically_valid,
        metrics={"route_id": "exploration", "worst_case": 0.02},
        artifact_ids=["B.json"],
        hypothesis_updates=[
            {
                "hypothesis_id": "hyp-02",
                "to_status": "partially_supported",
                "evidence_kind": "partial_support",
                "reason": "improved",
            }
        ],
        summary="second",
    )
    feedback = ArticleFeedbackController().update(plan, [obs_1, obs_2])
    assert feedback.validation_errors == []
    ledger = build_claim_ledger(plan, [feedback], [obs_1, obs_2])
    assert ledger.validation_errors == []
    return plan, ledger


def _refuted_ledger():
    plan = _plan()
    obs_a = ObservationCard(
        observation_id="obs-a",
        experiment_id="exp-a",
        status=ExperimentStatus.physically_valid,
        metrics={"route_id": "exploration", "R_mean": 0.05},
        artifact_ids=["EXPLORE.json"],
        hypothesis_updates=[
            {
                "hypothesis_id": "hyp-02",
                "to_status": "active",
                "evidence_kind": "partial_support",
                "reason": "first signal",
            }
        ],
        summary="first signal",
    )
    obs_b = ObservationCard(
        observation_id="obs-b",
        experiment_id="exp-b",
        status=ExperimentStatus.physically_valid,
        metrics={
            "route_id": "exploration",
            "R_mean": 0.2,
            "discriminator_match": {
                "hyp-02": {"matched": False, "metric_keys": ["R_mean"]}
            },
        },
        artifact_ids=["FAILURE.json"],
        hypothesis_updates=[
            {
                "hypothesis_id": "hyp-02",
                "to_status": "refuted",
                "evidence_kind": "disconfirming",
                "reason": "explicit disconfirming discriminator",
            }
        ],
        summary="refuting observation",
    )
    feedback = ArticleFeedbackController().update(plan, [obs_a, obs_b])
    assert feedback.validation_errors == []
    ledger = build_claim_ledger(plan, [feedback], [obs_a, obs_b])
    assert ledger.validation_errors == []
    return plan, ledger


def _manifest() -> list[ArtifactDescriptor]:
    return [
        ArtifactDescriptor(
            artifact_id="FINAL_RESULT.json",
            path="runs/example/FINAL_RESULT.json",
            fields=["R_mean", "worst_case"],
            artifact_type="simulation",
            media_type="application/json",
            content_summary="Verified solver spectrum for the baseline route.",
            field_descriptions={
                "R_mean": "mean reflectance over the declared band",
                "worst_case": "worst-case reflectance",
            },
            source_experiment_ids=["exp-1"],
            source_observation_ids=["obs-1"],
        )
    ]


def _value_shapes() -> Dict[str, Dict[str, str]]:
    return {
        "FINAL_RESULT.json": {
            "R_mean": "scalar",
            "worst_case": "scalar",
        }
    }


def _story_draft(
    ledger,
    *,
    variant: str,
    claim_role: str = "positive",
    claim_ids: list[str] | None = None,
    fact_ids: list[str] | None = None,
    artifact_bindings: list[dict] | None = None,
    figure_kind: str = "quantitative",
    omitted: list[dict] | None = None,
    extra: dict | None = None,
    thesis: str | None = None,
    role_key: str = "spectrum",
) -> dict:
    if claim_ids is None:
        positive = [
            c for c in ledger.claims if c.status == ClaimStatus.partially_supported
        ]
        assert positive, "fixture requires a positive claim when claim_ids omitted"
        claim_id = positive[0].claim_id
    else:
        claim_id = claim_ids[0]
    fact = next(
        (f for f in ledger.facts if f.metadata.get("claim_id") == claim_id),
        None,
    )
    figure = {
        "role_key": role_key,
        "kind": figure_kind,
        "story_role": "spectral response",
        "panel_intents": ["panel"],
        "caption_intent": "verified spectrum",
        "claim_bindings": [{"claim_id": claim_id, "role": claim_role}],
        "fact_ids": (
            fact_ids if fact_ids is not None else ([fact.fact_id] if fact else [])
        ),
        "artifact_bindings": (
            artifact_bindings
            if artifact_bindings is not None
            else (
                [{"artifact_id": "FINAL_RESULT.json", "selected_fields": ["R_mean"]}]
                if figure_kind in {"quantitative", "table"}
                else []
            )
        ),
        "limitations": ["solver only"],
    }
    if extra:
        figure.update(extra)
    return {
        "story_shape": f"shape-{variant}",
        "central_thesis": thesis
        or f"Thesis {variant}: an evidence-bound AR design story.",
        "sections": [
            {
                "heading": f"Results {variant}",
                "purpose": "present the verified evidence",
                "key_messages": ["key message"],
                "transitions": ["next"],
                "claim_bindings": [{"claim_id": claim_id, "role": claim_role}],
                "figure_roles": [role_key],
            }
        ],
        "figures": [figure],
        "omitted_claims": omitted or [],
        "exclusions": [f"excluded-{variant}"],
        "strengths": [f"strength-{variant}"],
        "risks": [f"risk-{variant}"],
        "recommendation_rationale": f"rationale-{variant}",
        "recommendation_score": 0.6,
    }


def _provider(
    *story_dicts,
    model="fake-test-provider",
    captured=None,
    usage=None,
):
    def provider(requests):
        if captured is not None:
            captured.extend(requests)
        return [
            ArchitectureProviderResult(
                stories=list(story_dicts),
                provider_model=model,
                usage=(
                    {
                        "estimated_input_tokens": 10,
                        "estimated_output_tokens": 20,
                    }
                    if usage is None
                    else dict(usage)
                ),
            )
        ]

    return provider


class RepairCapableArchitectureProvider:
    """Fake provider with one bounded repair round and per-call usage."""

    def __init__(
        self,
        first_stories: list[dict],
        repair_stories: list[dict],
        *,
        first_usage: dict | None = None,
        repair_usage: dict | None = None,
    ) -> None:
        self.first_stories = list(first_stories)
        self.repair_stories = list(repair_stories)
        self.first_usage = first_usage or {
            "estimated_input_tokens": 10,
            "estimated_output_tokens": 20,
        }
        self.repair_usage = repair_usage or {
            "estimated_input_tokens": 30,
            "estimated_output_tokens": 40,
        }
        self.calls = 0
        self.repair_calls = 0
        self.repair_requests: list[dict] = []

    def __call__(self, requests: Sequence[Any]) -> List[ArchitectureProviderResult]:
        self.calls += 1
        return [
            ArchitectureProviderResult(
                stories=list(self.first_stories),
                provider_model="fake-test-provider",
                usage=dict(self.first_usage),
            )
        ]

    def repair(
        self, requests: Sequence[Any], repair_request: Mapping[str, Any]
    ) -> List[ArchitectureProviderResult]:
        self.repair_calls += 1
        self.repair_requests.append(dict(repair_request))
        return [
            ArchitectureProviderResult(
                stories=list(self.repair_stories),
                provider_model="fake-test-provider",
                usage=dict(self.repair_usage),
            )
        ]


class ProgressiveRepairProvider:
    """Fake provider with sequential single-candidate repair rounds."""

    def __init__(
        self,
        first_stories: list[dict],
        repair_rounds: list[list[dict]],
        *,
        first_usage: dict | None = None,
        repair_usages: list[dict] | None = None,
    ) -> None:
        self.first_stories = list(first_stories)
        self.repair_rounds = [list(item) for item in repair_rounds]
        self.first_usage = first_usage or {
            "model_name": "fake-provider",
            "input_tokens": 1,
            "output_tokens": 2,
        }
        self.repair_usages = list(
            repair_usages
            or [
                {
                    "model_name": "fake-provider",
                    "input_tokens": 3,
                    "output_tokens": 4,
                },
                {
                    "model_name": "fake-provider",
                    "input_tokens": 5,
                    "output_tokens": 6,
                },
            ]
        )
        self.calls = 0
        self.repair_calls = 0
        self.repair_requests: list[dict] = []

    def __call__(self, requests: Sequence[Any]) -> List[ArchitectureProviderResult]:
        self.calls += 1
        return [
            ArchitectureProviderResult(
                stories=list(self.first_stories),
                provider_model="fake-test-provider",
                usage=dict(self.first_usage),
            )
        ]

    def repair(
        self, requests: Sequence[Any], repair_request: Mapping[str, Any]
    ) -> List[ArchitectureProviderResult]:
        self.repair_calls += 1
        self.repair_requests.append(dict(repair_request))
        stories = self.repair_rounds.pop(0) if self.repair_rounds else []
        usage = dict(self.repair_usages.pop(0)) if self.repair_usages else {}
        return [
            ArchitectureProviderResult(
                stories=stories,
                provider_model="fake-test-provider",
                usage=usage,
            )
        ]


def test_valid_fixture_yields_three_distinct_stories() -> None:
    plan, ledger = _ledger()
    provider = _provider(
        _story_draft(ledger, variant="a"),
        _story_draft(ledger, variant="b", role_key="portfolio"),
        _story_draft(ledger, variant="c", role_key="robustness"),
    )
    result = build_article_architecture(
        plan, ledger, _manifest(), architecture_provider=provider
    )
    assert result.validation_errors == []
    assert result.model_status == "available"
    assert len(result.stories) == 3
    assert result.semantic_model == "fake-test-provider"
    assert result.usage["estimated_input_tokens"] == 10
    for story in result.stories:
        assert story.section_contracts
        assert story.figure_contracts
        figure = story.figure_contracts[0]
        assert figure.kind == "quantitative"
        assert figure.artifact_bindings[0].artifact_id == "FINAL_RESULT.json"
        assert figure.artifact_bindings[0].selected_fields == ["R_mean"]
        for claim_id in figure.claim_ids:
            fact = next(
                f for f in ledger.facts if f.metadata.get("claim_id") == claim_id
            )
            assert set(fact.source_artifact_ids) == {
                item.artifact_id for item in figure.artifact_bindings
            }
        assert any(item.role == "positive" for item in story.claim_assignments)


def test_architecture_payload_carries_local_story_completion_contract() -> None:
    plan, ledger = _ledger()
    claim = ledger.claims[0]
    expanded_claims = [
        claim.model_copy(update={"claim_id": f"claim-{index:02d}"})
        for index in range(20)
    ]
    expanded_ledger = ledger.model_copy(update={"claims": expanded_claims})

    payload = build_architecture_payload(plan, expanded_ledger, _manifest())[0]

    assert payload["story_completion_contract"] == {
        "writable_positive_claim_count": 20,
        "minimum_assigned_claim_count": 19,
        "maximum_omitted_claim_count": 1,
        "target_coverage_fraction": 0.95,
        "policy": payload["story_completion_contract"]["policy"],
    }
    assert "another route" in payload["story_completion_contract"]["policy"]


def test_unambiguous_claim_binding_key_value_inversion_is_normalized() -> None:
    plan, ledger = _ledger()
    claim_id = ledger.claims[0].claim_id
    draft = _story_draft(ledger, variant="inverted")
    draft["sections"][0]["claim_bindings"] = [{claim_id: "positive"}]

    result = build_article_architecture(
        plan,
        ledger,
        _manifest(),
        architecture_provider=_provider(draft),
    )

    assert result.validation_errors == []
    assert result.stories[0].section_contracts[0].claim_bindings[0].claim_id == claim_id
    assert any(
        "single-entry claim_id-to-role mapping" in warning
        for warning in result.warnings
    )


def test_figure_fact_ids_are_reconciled_from_bound_claims() -> None:
    plan, ledger = _ledger()
    claim = ledger.claims[0]
    expected_fact = next(
        fact
        for fact in ledger.facts
        if fact.metadata.get("claim_id") == claim.claim_id
    )
    draft = _story_draft(
        ledger,
        variant="fact-reconciliation",
        fact_ids=["fact-orphan-from-another-figure"],
    )

    result = build_article_architecture(
        plan,
        ledger,
        _manifest(),
        architecture_provider=_provider(draft),
    )

    assert result.validation_errors == []
    assert result.stories[0].figure_contracts[0].fact_ids == [
        expected_fact.fact_id
    ]
    assert any("removed orphan fact_ids" in item for item in result.warnings)
    assert any("restored fact_id" in item for item in result.warnings)


def test_named_result_subject_requires_same_section_claim_binding() -> None:
    plan, ledger = _ledger()
    claim = ledger.claims[0]
    metadata = dict(claim.metadata)
    metadata["synthesis_contract"] = {
        "subject_aliases": ["GC01"],
        "route_alias": "R01",
    }
    ledger = ledger.model_copy(
        update={
            "claims": [claim.model_copy(update={"metadata": metadata})],
        }
    )
    draft = _story_draft(ledger, variant="unbound-conclusion")
    draft["sections"][0]["claim_bindings"] = []
    draft["sections"][0]["key_messages"] = ["GC01 is the recommended design from R01."]

    result = build_article_architecture(
        plan,
        ledger,
        _manifest(),
        architecture_provider=_provider(draft),
    )

    assert result.stories == []
    assert any(
        "names result subject 'GC01' but binds no Claim" in error
        for error in result.validation_errors
    )
    assert any(
        "names result subject 'R01' but binds no Claim" in error
        for error in result.validation_errors
    )


def test_architecture_id_is_content_sensitive_and_exact_replay_stable() -> None:
    plan, ledger = _ledger()
    first = build_article_architecture(
        plan,
        ledger,
        _manifest(),
        architecture_provider=_provider(_story_draft(ledger, variant="a")),
    )
    second = build_article_architecture(
        plan,
        ledger,
        _manifest(),
        architecture_provider=_provider(_story_draft(ledger, variant="a")),
    )
    assert first.architecture_id == second.architecture_id
    changed = _story_draft(ledger, variant="a", thesis="A completely different thesis.")
    third = build_article_architecture(
        plan,
        ledger,
        _manifest(),
        architecture_provider=_provider(changed),
    )
    assert third.architecture_id != first.architecture_id


def test_architecture_result_carries_provenance_identity_and_inventory() -> None:
    plan, ledger = _ledger()
    result = build_article_architecture(
        plan,
        ledger,
        _manifest(),
        architecture_provider=_provider(_story_draft(ledger, variant="a")),
    )
    assert result.source_plan_id == plan.plan_id
    assert result.source_ledger_id == ledger.ledger_id
    assert [item.artifact_id for item in result.artifact_inventory] == [
        "FINAL_RESULT.json"
    ]
    recomputed = compute_architecture_id(
        plan.plan_id,
        ledger.ledger_id,
        result.artifact_inventory,
        result.missing_work_handoffs,
        result.stories,
    )
    assert recomputed == result.architecture_id


def test_artifact_descriptor_sha256_format_is_validated() -> None:
    with pytest.raises(ValidationError):
        ArtifactDescriptor(
            artifact_id="X.json",
            path="x.json",
            fields=["a"],
            sha256="not-a-hex-digest",
        )
    upper = ArtifactDescriptor(
        artifact_id="X.json",
        path="x.json",
        fields=["a"],
        sha256=("A" * 63 + "B"),
    )
    assert upper.sha256 == ("a" * 63 + "b")


def test_memory_ids_are_namespaced_across_architectures(tmp_path) -> None:
    plan_a, ledger_a = _ledger()
    plan_b_result = ArticleDirector().plan(
        "A different question about a different coating.",
        _analysis(),
        _report(),
        force_mock=True,
    )
    assert plan_b_result.status == "planned" and plan_b_result.plan is not None
    plan_b = plan_b_result.plan
    assert plan_b.plan_id != plan_a.plan_id
    observation_b = ObservationCard(
        observation_id="obs-1",
        experiment_id="exp-1",
        status=ExperimentStatus.physically_valid,
        metrics={"route_id": "baseline", "R_mean": 0.004},
        artifact_ids=["FINAL_RESULT.json"],
        hypothesis_updates=[
            {
                "hypothesis_id": "hyp-01",
                "to_status": "partially_supported",
                "evidence_kind": "partial_support",
                "reason": "improved",
            }
        ],
        summary="observation",
    )
    feedback_b = ArticleFeedbackController().update(plan_b, [observation_b])
    ledger_b = build_claim_ledger(plan_b, [feedback_b], [observation_b])
    assert ledger_a.ledger_id != ledger_b.ledger_id

    memory_a = ArticleMemoryStore(tmp_path / "a.sqlite")
    memory_b = ArticleMemoryStore(tmp_path / "b.sqlite")
    graph_a = ExperimentGraph(tmp_path / "ga.sqlite", "run-1")
    graph_b = ExperimentGraph(tmp_path / "gb.sqlite", "run-1")
    result_a = build_article_architecture(
        plan_a,
        ledger_a,
        _manifest(),
        architecture_provider=_provider(_story_draft(ledger_a, variant="a")),
        memory_store=memory_a,
        graph=graph_a,
        run_id="run-1",
    )
    result_b = build_article_architecture(
        plan_b,
        ledger_b,
        _manifest(),
        architecture_provider=_provider(_story_draft(ledger_b, variant="a")),
        memory_store=memory_b,
        graph=graph_b,
        run_id="run-1",
    )
    ids_a = {item.memory_id for item in memory_a.run_memory_records()}
    ids_b = {item.memory_id for item in memory_b.run_memory_records()}
    assert ids_a.isdisjoint(ids_b)
    assert result_a.architecture_id != result_b.architecture_id


def test_foreign_plan_and_ledger_validation_errors_are_rejected() -> None:
    plan_a, ledger_a = _ledger()
    plan_b_result = ArticleDirector().plan(
        "Another question about a different coating.",
        _analysis(),
        _report(),
        force_mock=True,
    )
    assert plan_b_result.status == "planned" and plan_b_result.plan is not None
    plan_b = plan_b_result.plan
    assert plan_b.plan_id != plan_a.plan_id
    foreign = build_article_architecture(
        plan_b, ledger_a, _manifest(), architecture_provider=None
    )
    assert any("source_plan_id" in item for item in foreign.validation_errors)
    assert foreign.stories == []

    errored_ledger = ledger_a.model_copy(update={"validation_errors": ["tampered"]})
    invalid = build_article_architecture(
        plan_a, errored_ledger, _manifest(), architecture_provider=None
    )
    assert any(
        "carries validation errors" in item for item in invalid.validation_errors
    )
    assert invalid.stories == []


def test_claim_hypothesis_and_statement_are_validated_against_plan() -> None:
    plan, ledger = _ledger()
    tampered_claims = [
        claim.model_copy(update={"statement": "Unrelated statement."})
        for claim in ledger.claims
    ]
    tampered_ledger = ledger.model_copy(update={"claims": tampered_claims})
    result = build_article_architecture(
        plan, tampered_ledger, _manifest(), architecture_provider=None
    )
    assert any("statement does not match" in item for item in result.validation_errors)
    assert result.stories == []


def test_unrelated_trusted_artifact_cannot_support_quantitative_figure() -> None:
    plan, ledger = _ledger()
    manifest = _manifest() + [
        ArtifactDescriptor(
            artifact_id="UNRELATED.json",
            path="runs/other/UNRELATED.json",
            fields=["x"],
            content_summary="Trusted but unrelated artifact.",
        )
    ]
    draft = _story_draft(
        ledger,
        variant="a",
        artifact_bindings=[{"artifact_id": "UNRELATED.json", "selected_fields": ["x"]}],
    )
    result = build_article_architecture(
        plan, ledger, manifest, architecture_provider=_provider(draft)
    )
    assert any("unrelated artifacts" in item for item in result.validation_errors)
    assert result.stories == []


def _lineage_claim_ledger() -> Any:
    """Ledger whose writable claim carries TV43-style value lineage."""

    plan, ledger = _ledger()
    claim = next(
        claim
        for claim in ledger.claims
        if claim.status == ClaimStatus.partially_supported
    )
    enriched = claim.model_copy(
        update={
            "metadata": {
                **claim.metadata,
                "value_lineage": [
                    {
                        "artifact_id": "FINAL_RESULT.json",
                        "field": "R_mean",
                        "label": "Mean reflectance",
                        "unit": "",
                        "source_alias": "TV43",
                    }
                ],
            }
        }
    )
    ledger = ledger.model_copy(
        update={
            "claims": [
                enriched if item.claim_id == claim.claim_id else item
                for item in ledger.claims
            ]
        }
    )
    return plan, ledger


def _two_lineage_claim_ledger() -> Any:
    """Two writable claims on FINAL_RESULT.json, one field each in lineage."""

    plan = _plan()
    obs_1 = ObservationCard(
        observation_id="obs-1",
        experiment_id="exp-1",
        status=ExperimentStatus.physically_valid,
        metrics={"route_id": "baseline", "R_mean": 0.004},
        artifact_ids=["FINAL_RESULT.json"],
        hypothesis_updates=[
            {
                "hypothesis_id": "hyp-01",
                "to_status": "partially_supported",
                "evidence_kind": "partial_support",
                "reason": "improved",
            }
        ],
        summary="first",
    )
    obs_2 = ObservationCard(
        observation_id="obs-2",
        experiment_id="exp-2",
        status=ExperimentStatus.physically_valid,
        metrics={"route_id": "exploration", "worst_case": 0.02},
        artifact_ids=["FINAL_RESULT.json"],
        hypothesis_updates=[
            {
                "hypothesis_id": "hyp-02",
                "to_status": "partially_supported",
                "evidence_kind": "partial_support",
                "reason": "improved",
            }
        ],
        summary="second",
    )
    feedback = ArticleFeedbackController().update(plan, [obs_1, obs_2])
    assert feedback.validation_errors == []
    ledger = build_claim_ledger(plan, [feedback], [obs_1, obs_2])
    assert ledger.validation_errors == []

    def enrich(claim, field: str) -> Any:
        return claim.model_copy(
            update={
                "metadata": {
                    **claim.metadata,
                    "value_lineage": [
                        {
                            "artifact_id": "FINAL_RESULT.json",
                            "field": field,
                            "label": field,
                            "unit": "",
                            "source_alias": "TV43",
                        }
                    ],
                }
            }
        )

    by_hypothesis = {
        claim.metadata.get("hypothesis_id"): claim for claim in ledger.claims
    }
    enriched_claims = [
        (
            enrich(by_hypothesis["hyp-01"], "R_mean")
            if claim.claim_id == by_hypothesis["hyp-01"].claim_id
            else (
                enrich(by_hypothesis["hyp-02"], "worst_case")
                if claim.claim_id == by_hypothesis["hyp-02"].claim_id
                else claim
            )
        )
        for claim in ledger.claims
    ]
    ledger = ledger.model_copy(update={"claims": enriched_claims})
    return plan, ledger


def test_numeric_claim_requires_its_authorized_value_field() -> None:
    """Probe 031: another field in the same artifact is not sufficient."""

    plan, ledger = _lineage_claim_ledger()
    manifest = _manifest()
    wrong_field = _story_draft(
        ledger,
        variant="a",
        artifact_bindings=[
            {"artifact_id": "FINAL_RESULT.json", "selected_fields": ["worst_case"]}
        ],
    )
    result = build_article_architecture(
        plan, ledger, manifest, architecture_provider=_provider(wrong_field)
    )
    assert result.stories == []
    assert any(
        "not bound to any authorized value field" in item and "R_mean" in item
        for item in result.validation_errors
    )


def test_numeric_claim_passes_when_authorized_value_field_is_bound() -> None:
    plan, ledger = _lineage_claim_ledger()
    manifest = _manifest()
    correct = _story_draft(
        ledger,
        variant="a",
        artifact_bindings=[
            {"artifact_id": "FINAL_RESULT.json", "selected_fields": ["R_mean"]}
        ],
    )
    result = build_article_architecture(
        plan, ledger, manifest, architecture_provider=_provider(correct)
    )
    assert result.validation_errors == []
    assert result.stories


def test_architecture_payload_exposes_claim_authorized_value_fields() -> None:
    plan, ledger = _lineage_claim_ledger()
    contract = {
        "kind": "result_synthesis",
        "comparison_scope": "route",
        "scope_limits": "Only within route R01.",
    }
    ledger = ledger.model_copy(
        update={
            "claims": [
                claim.model_copy(
                    update={
                        "metadata": {
                            **claim.metadata,
                            "synthesis_contract": contract,
                        }
                    }
                )
                for claim in ledger.claims
            ]
        }
    )
    captured: list[dict] = []
    build_article_architecture(
        plan,
        ledger,
        _manifest(),
        architecture_provider=_provider(captured=captured),
    )
    assert captured
    claims = [
        *captured[0]["positive_claims"],
        *captured[0]["limitation_claims"],
    ]
    lineage_claims = [claim for claim in claims if claim.get("authorized_value_fields")]
    assert lineage_claims
    assert {
        (item["artifact_id"], field)
        for claim in lineage_claims
        for item in claim["authorized_value_fields"]
        for field in item["fields"]
    } == {("FINAL_RESULT.json", "R_mean")}
    assert all(claim["synthesis_contract"] == contract for claim in claims)


def test_conceptual_figure_with_numeric_claim_needs_no_scalar_binding() -> None:
    """Value-field binding is required only for quantitative/table figures."""

    plan, ledger = _lineage_claim_ledger()
    manifest = _manifest()
    draft = _story_draft(
        ledger,
        variant="a",
        figure_kind="conceptual",
        artifact_bindings=[],
    )
    result = build_article_architecture(
        plan, ledger, manifest, architecture_provider=_provider(draft)
    )
    assert result.validation_errors == []
    assert result.stories
    assert result.stories[0].figure_contracts[0].kind == "conceptual"


def test_quantitative_figure_rejects_extra_unauthorized_scalar_field() -> None:
    """Reproduced case: authorized scalar plus sibling scalar is rejected."""

    plan, ledger = _lineage_claim_ledger()
    manifest = _manifest()
    draft = _story_draft(
        ledger,
        variant="a",
        artifact_bindings=[
            {
                "artifact_id": "FINAL_RESULT.json",
                "selected_fields": ["R_mean", "worst_case"],
            }
        ],
    )
    result = build_article_architecture(
        plan,
        ledger,
        manifest,
        architecture_provider=_provider(draft),
        value_shapes=_value_shapes(),
    )
    assert result.stories == []
    assert any(
        "unauthorized scalar value field" in item and "worst_case" in item
        for item in result.validation_errors
    )


def test_quantitative_figure_allows_authorized_scalar_with_auxiliary_axis() -> None:
    plan, ledger = _lineage_claim_ledger()
    manifest = [
        ArtifactDescriptor(
            artifact_id="FINAL_RESULT.json",
            path="runs/example/FINAL_RESULT.json",
            fields=["R_mean", "wavelengths_nm"],
            artifact_type="simulation",
            media_type="application/json",
            content_summary="Verified spectrum result.",
            field_descriptions={
                "R_mean": "mean reflectance",
                "wavelengths_nm": "wavelength grid",
            },
            sha256="a" * 64,
            source_experiment_ids=["exp-1"],
            source_observation_ids=["obs-1"],
        )
    ]
    draft = _story_draft(
        ledger,
        variant="a",
        artifact_bindings=[
            {
                "artifact_id": "FINAL_RESULT.json",
                "selected_fields": ["R_mean", "wavelengths_nm"],
            }
        ],
    )
    result = build_article_architecture(
        plan,
        ledger,
        manifest,
        architecture_provider=_provider(draft),
        value_shapes={
            "FINAL_RESULT.json": {
                "R_mean": "scalar",
                "wavelengths_nm": "series",
            }
        },
    )
    assert result.validation_errors == []
    assert result.stories


def test_quantitative_figure_uses_union_of_bound_claim_lineage() -> None:
    plan, ledger = _two_lineage_claim_ledger()
    claim_a = next(
        claim
        for claim in ledger.claims
        if claim.metadata.get("hypothesis_id") == "hyp-01"
    )
    claim_b = next(
        claim
        for claim in ledger.claims
        if claim.metadata.get("hypothesis_id") == "hyp-02"
    )
    fact_a = next(
        fact
        for fact in ledger.facts
        if fact.metadata.get("claim_id") == claim_a.claim_id
    )
    fact_b = next(
        fact
        for fact in ledger.facts
        if fact.metadata.get("claim_id") == claim_b.claim_id
    )
    draft = {
        "story_shape": "shape-two-lineage",
        "central_thesis": "Two lineage claims in one figure.",
        "sections": [
            {
                "heading": "Results two-lineage",
                "purpose": "present the verified evidence",
                "key_messages": ["key"],
                "transitions": ["next"],
                "claim_bindings": [
                    {"claim_id": claim_a.claim_id, "role": "positive"},
                    {"claim_id": claim_b.claim_id, "role": "positive"},
                ],
                "figure_roles": ["spectrum"],
            }
        ],
        "figures": [
            {
                "role_key": "spectrum",
                "kind": "quantitative",
                "story_role": "spectral response",
                "panel_intents": ["panel"],
                "caption_intent": "verified spectra",
                "claim_bindings": [
                    {"claim_id": claim_a.claim_id, "role": "positive"},
                    {"claim_id": claim_b.claim_id, "role": "positive"},
                ],
                "fact_ids": [fact_a.fact_id, fact_b.fact_id],
                "artifact_bindings": [
                    {
                        "artifact_id": "FINAL_RESULT.json",
                        "selected_fields": ["R_mean", "worst_case"],
                    }
                ],
                "limitations": ["solver only"],
            }
        ],
        "omitted_claims": [],
        "exclusions": ["excluded-two-lineage"],
        "strengths": ["strength-two-lineage"],
        "risks": ["risk-two-lineage"],
        "recommendation_rationale": "rationale-two-lineage",
        "recommendation_score": 0.6,
    }
    result = build_article_architecture(
        plan,
        ledger,
        _manifest(),
        architecture_provider=_provider(draft),
        value_shapes=_value_shapes(),
    )
    assert result.validation_errors == []
    assert result.stories


def test_quantitative_figure_rejects_field_authorized_by_unbound_claim() -> None:
    plan, ledger = _two_lineage_claim_ledger()
    claim_a = next(
        claim
        for claim in ledger.claims
        if claim.metadata.get("hypothesis_id") == "hyp-01"
    )
    fact_a = next(
        fact
        for fact in ledger.facts
        if fact.metadata.get("claim_id") == claim_a.claim_id
    )
    draft = _story_draft(
        ledger,
        variant="a",
        claim_ids=[claim_a.claim_id],
        fact_ids=[fact_a.fact_id],
        artifact_bindings=[
            {
                "artifact_id": "FINAL_RESULT.json",
                "selected_fields": ["R_mean", "worst_case"],
            }
        ],
    )
    result = build_article_architecture(
        plan,
        ledger,
        _manifest(),
        architecture_provider=_provider(draft),
        value_shapes=_value_shapes(),
    )
    assert result.stories == []
    assert any(
        "unauthorized scalar value field" in item and "worst_case" in item
        for item in result.validation_errors
    )


def test_quantitative_figure_payload_exposes_field_shapes() -> None:
    plan, ledger = _lineage_claim_ledger()
    captured: list[dict] = []
    build_article_architecture(
        plan,
        ledger,
        _manifest(),
        architecture_provider=_provider(captured=captured),
        value_shapes=_value_shapes(),
    )
    assert captured
    artifact = next(
        item
        for item in captured[0]["artifacts"]
        if item["artifact_id"] == "FINAL_RESULT.json"
    )
    assert artifact["field_shapes"] == {
        "R_mean": "scalar",
        "worst_case": "scalar",
    }
    assert "rendered_value" not in str(artifact)


def test_no_lineage_claim_keeps_legacy_scalar_bindings() -> None:
    plan, ledger = _ledger()
    draft = _story_draft(
        ledger,
        variant="a",
        artifact_bindings=[
            {
                "artifact_id": "FINAL_RESULT.json",
                "selected_fields": ["R_mean", "worst_case"],
            }
        ],
    )
    result = build_article_architecture(
        plan, ledger, _manifest(), architecture_provider=_provider(draft)
    )
    assert result.validation_errors == []
    assert result.stories


def test_multi_artifact_per_field_bindings_work() -> None:
    plan = _plan()
    observation = ObservationCard(
        observation_id="obs-1",
        experiment_id="exp-1",
        status=ExperimentStatus.physically_valid,
        metrics={"route_id": "baseline", "R_mean": 0.004, "worst_case": 0.02},
        artifact_ids=["A.json", "B.json"],
        hypothesis_updates=[
            {
                "hypothesis_id": "hyp-01",
                "to_status": "partially_supported",
                "evidence_kind": "partial_support",
                "reason": "improved",
            }
        ],
        summary="observation",
    )
    feedback = ArticleFeedbackController().update(plan, [observation])
    ledger = build_claim_ledger(plan, [feedback], [observation])
    assert ledger.validation_errors == []
    manifest = [
        ArtifactDescriptor(
            artifact_id="A.json",
            path="a.json",
            fields=["R_mean"],
            content_summary="artifact A",
        ),
        ArtifactDescriptor(
            artifact_id="B.json",
            path="b.json",
            fields=["worst_case"],
            content_summary="artifact B",
        ),
    ]
    draft = _story_draft(
        ledger,
        variant="a",
        artifact_bindings=[
            {"artifact_id": "A.json", "selected_fields": ["R_mean"]},
            {"artifact_id": "B.json", "selected_fields": ["worst_case"]},
        ],
    )
    result = build_article_architecture(
        plan, ledger, manifest, architecture_provider=_provider(draft)
    )
    assert result.validation_errors == []
    bindings = result.stories[0].figure_contracts[0].artifact_bindings
    assert {item.artifact_id for item in bindings} == {"A.json", "B.json"}
    assert [item.selected_fields for item in bindings] == [
        ["R_mean"],
        ["worst_case"],
    ]


def test_missing_work_handoffs_are_structured_and_preserved(tmp_path) -> None:
    plan, ledger = _ledger()
    captured: list[dict] = []
    build_article_architecture(
        plan,
        ledger,
        _manifest(),
        architecture_provider=_provider(
            _story_draft(ledger, variant="a"),
            captured=captured,
        ),
    )
    # The analysis has a preferred behavior, so a success criterion exists; no
    # provider is used below, so audit rows fall back to unknown and become
    # structured handoffs.
    result = build_article_architecture(
        plan, ledger, _manifest(), architecture_provider=None
    )
    assert result.missing_work_handoffs
    handoff = result.missing_work_handoffs[0]
    assert isinstance(handoff, MissingWorkHandoff)
    assert handoff.goal_id
    assert handoff.kind in {"goal", "success_criterion"}
    assert handoff.coverage in {
        "covered",
        "partial",
        "gap",
        "unknown",
        "not_applicable",
    }
    payload_handoffs = captured[0]["missing_work_handoffs"] if captured else []
    if payload_handoffs:
        assert set(payload_handoffs[0].keys()) == {
            "goal_id",
            "goal_label",
            "kind",
            "coverage",
            "claim_ids",
            "unique_contribution",
            "expected_value_of_more_work",
            "stop_reason",
            "rationale",
        }


def test_partial_audit_rows_are_missing_work_handoffs() -> None:
    plan, ledger = _ledger()
    partial_audit = ArticleCompletionAudit(
        audit_id="audit-partial",
        rows=[
            CompletionAuditRow(
                goal_id="goal-1",
                goal_label="mean reflectance",
                kind="goal",
                coverage="partial",
                claim_ids=["claim-x"],
                unique_contribution="partial coverage",
                expected_value_of_more_work="additional runs",
                stop_reason="in progress",
                rationale="some evidence mapped",
            )
        ],
        semantic_coverage_available=True,
    )
    tampered = ledger.model_copy(update={"audit": partial_audit})
    captured: list[dict] = []
    build_article_architecture(
        plan,
        tampered,
        _manifest(),
        architecture_provider=_provider(
            _story_draft(ledger, variant="a"),
            captured=captured,
        ),
    )
    result = build_article_architecture(
        plan, tampered, _manifest(), architecture_provider=None
    )
    assert any(item.coverage == "partial" for item in result.missing_work_handoffs)
    payload_handoffs = captured[0]["missing_work_handoffs"]
    assert any(item["coverage"] == "partial" for item in payload_handoffs)


class FakeQwenClient:
    def __init__(self, content: str, usage: dict | None = None) -> None:
        self.content = content
        self.usage = usage or {
            "model_name": "qwen3.7-flash",
            "mock_llm": False,
            "estimated_input_tokens": 12,
            "estimated_output_tokens": 34,
            "estimated_cost_cny": 0.0,
        }
        self.messages: list[dict[str, str]] = []
        self.kwargs: dict = {}

    def call(self, messages, **kwargs) -> dict:
        self.messages = messages
        self.kwargs = dict(kwargs)
        return {"content": self.content, "_llm_usage": dict(self.usage)}


def test_qwen_adapter_parses_stories_object_and_preserves_usage() -> None:
    plan, ledger = _ledger()
    story = _story_draft(ledger, variant="a")
    client = FakeQwenClient(
        json.dumps({"stories": [story]}),
        usage={
            "model_name": "qwen3.7-flash",
            "mock_llm": False,
            "estimated_input_tokens": 12,
            "estimated_output_tokens": 34,
            "estimated_cost_cny": 0.01,
        },
    )
    planner = QwenArticleArchitecturePlanner(client=client)
    result = build_article_architecture(
        plan, ledger, _manifest(), architecture_provider=planner
    )
    assert result.semantic_model == "qwen3.7-flash"
    assert result.usage["estimated_input_tokens"] == 12
    assert result.usage["estimated_output_tokens"] == 34
    assert result.usage["estimated_cost_cny"] == 0.01
    assert result.stories
    prompt = client.messages[0]["content"]
    assert '"stories"' in prompt


def test_qwen_max_tokens_is_configurable() -> None:
    plan, ledger = _ledger()
    story = _story_draft(ledger, variant="a")
    client = FakeQwenClient(json.dumps({"stories": [story]}))
    planner = QwenArticleArchitecturePlanner(client=client)
    result = build_article_architecture(
        plan, ledger, _manifest(), architecture_provider=planner
    )
    assert result.stories
    assert client.kwargs["max_tokens"] == 24000

    reduced = QwenArticleArchitecturePlanner(client=client, max_tokens=2000)
    build_article_architecture(plan, ledger, _manifest(), architecture_provider=reduced)
    assert client.kwargs["max_tokens"] == 2000


def test_provider_model_default_is_unknown() -> None:
    plan, ledger = _ledger()

    def provider(requests):
        return [
            ArchitectureProviderResult(
                stories=[_story_draft(ledger, variant="a")],
                usage={"estimated_input_tokens": 1},
            )
        ]

    result = build_article_architecture(
        plan, ledger, _manifest(), architecture_provider=provider
    )
    assert result.semantic_model == "unknown"


def test_malformed_semantic_candidate_does_not_erase_valid_candidates() -> None:
    plan, ledger = _ledger()
    good_a = _story_draft(ledger, variant="a")
    good_b = _story_draft(ledger, variant="b", role_key="portfolio")
    malformed = _story_draft(ledger, variant="c")
    del malformed["central_thesis"]
    facts_before = {fact.fact_id: fact.model_dump(mode="json") for fact in ledger.facts}
    result = build_article_architecture(
        plan,
        ledger,
        _manifest(),
        architecture_provider=_provider(good_a, malformed, good_b),
    )
    assert result.validation_errors == []
    assert len(result.stories) == 2
    assert any("malformed story candidate skipped" in item for item in result.warnings)
    facts_after = {fact.fact_id: fact.model_dump(mode="json") for fact in ledger.facts}
    assert facts_before == facts_after
    assert all(
        fact_id in facts_before
        for story in result.stories
        for figure in story.figure_contracts
        for fact_id in figure.fact_ids
    )


def test_single_text_list_fields_are_normalized_without_losing_candidate() -> None:
    plan, ledger = _ledger()
    draft = _story_draft(ledger, variant="a")
    draft["strengths"] = "One concrete strength."
    draft["risks"] = "One bounded risk."
    draft["sections"][0]["key_messages"] = "One section message."
    draft["figures"][0]["panel_intents"] = "One panel intent."

    result = build_article_architecture(
        plan, ledger, _manifest(), architecture_provider=_provider(draft)
    )

    assert len(result.stories) == 1
    story = result.stories[0]
    assert story.strengths == ["One concrete strength."]
    assert story.risks == ["One bounded risk."]
    assert story.section_contracts[0].key_messages == ["One section message."]
    assert story.figure_contracts[0].panel_intents == ["One panel intent."]


def test_negative_claim_cannot_be_positive_support() -> None:
    plan, ledger = _ledger()
    limitation = [
        claim for claim in ledger.claims if claim.status == ClaimStatus.draft
    ][0]
    bad = _story_draft(ledger, variant="a", claim_role="positive")
    bad["sections"][0]["claim_bindings"] = [
        {"claim_id": limitation.claim_id, "role": "positive"}
    ]
    bad["figures"][0]["claim_bindings"] = [
        {"claim_id": limitation.claim_id, "role": "positive"}
    ]
    bad["figures"][0]["fact_ids"] = []
    result = build_article_architecture(
        plan, ledger, _manifest(), architecture_provider=_provider(bad)
    )
    assert any("non-positive claim" in item for item in result.validation_errors)
    assert result.stories == []


def test_positive_claim_limitation_role_is_organizational_warning() -> None:
    plan, ledger = _ledger()
    for role in ("limitation", "counterevidence"):
        draft = _story_draft(ledger, variant="a", claim_role=role)
        result = build_article_architecture(
            plan, ledger, _manifest(), architecture_provider=_provider(draft)
        )
        assert result.validation_errors == []
        assert len(result.stories) == 1
        assert any("organizational framing" in item for item in result.warnings)


def test_payload_claims_expose_authorized_artifact_ids() -> None:
    plan, ledger = _ledger()
    captured: list[dict] = []
    provider = _provider(
        _story_draft(ledger, variant="a"),
        captured=captured,
    )
    build_article_architecture(
        plan,
        ledger,
        _manifest(),
        architecture_provider=provider,
    )
    payload = captured[0]
    positive = payload["positive_claims"][0]
    assert "authorized_artifact_ids" in positive
    assert "FINAL_RESULT.json" in positive["authorized_artifact_ids"]
    assert all(
        "authorized_artifact_ids" in item for item in payload["limitation_claims"]
    )


def test_charter_numeric_constant_passes_and_new_measurement_fails() -> None:
    plan_result = ArticleDirector().plan(
        "Design an emitter with layer thickness up to 1500 nm.",
        _analysis(),
        _report(),
        force_mock=True,
    )
    assert plan_result.status == "planned" and plan_result.plan is not None
    plan = plan_result.plan
    observation = ObservationCard(
        observation_id="obs-1",
        experiment_id="exp-1",
        status=ExperimentStatus.physically_valid,
        metrics={"route_id": "baseline", "R_mean": 0.004},
        artifact_ids=["FINAL_RESULT.json"],
        hypothesis_updates=[
            {
                "hypothesis_id": "hyp-01",
                "to_status": "partially_supported",
                "evidence_kind": "partial_support",
                "reason": "improved",
            }
        ],
        summary="observation",
    )
    feedback = ArticleFeedbackController().update(plan, [observation])
    ledger = build_claim_ledger(plan, [feedback], [observation])
    assert ledger.validation_errors == []

    ok = _story_draft(ledger, variant="a", thesis="Thickness may reach 1500 nm.")
    ok_result = build_article_architecture(
        plan,
        ledger,
        _manifest(),
        architecture_provider=_provider(ok),
    )
    assert ok_result.validation_errors == []
    assert ok_result.stories

    bad = _story_draft(ledger, variant="b", thesis="Thickness may reach 999 nm.")
    bad_result = build_article_architecture(
        plan,
        ledger,
        _manifest(),
        architecture_provider=_provider(bad),
    )
    assert bad_result.stories == []
    assert any(
        "invented numeric content" in item for item in bad_result.validation_errors
    )


def test_non_writable_omitted_claim_ignored_with_warning() -> None:
    plan, ledger = _ledger()
    draft_claim = [c for c in ledger.claims if c.status == ClaimStatus.draft][0]
    draft = _story_draft(ledger, variant="a")
    draft["omitted_claims"] = [
        {"claim_id": draft_claim.claim_id, "reason": "organization note"}
    ]
    result = build_article_architecture(
        plan,
        ledger,
        _manifest(),
        architecture_provider=_provider(draft),
    )
    assert result.validation_errors == []
    assert result.stories
    assert any("organization-only metadata" in item for item in result.warnings)


def test_repair_request_receives_role_validation_errors() -> None:
    plan, ledger = _ledger()
    bad = _story_draft(
        ledger, variant="a", thesis="Reflectance improves to 999 percent."
    )
    good = _story_draft(ledger, variant="b", role_key="portfolio")
    provider = RepairCapableArchitectureProvider([bad], [good])

    result = build_article_architecture(
        plan, ledger, _manifest(), architecture_provider=provider
    )

    assert result.validation_errors == []
    assert len(result.stories) == 1
    assert provider.repair_calls == 1
    repair_request = provider.repair_requests[0]
    assert repair_request["candidate_index"] == 1
    assert any("invented numeric content" in item for item in repair_request["errors"])


def test_repaired_candidate_with_numeric_error_hard_blocks() -> None:
    plan, ledger = _ledger()
    bad_first = _story_draft(
        ledger, variant="a", thesis="Reflectance improves to 999 percent."
    )
    bad_repair = _story_draft(
        ledger, variant="b", thesis="Absorptance improves to 888 percent."
    )
    provider = RepairCapableArchitectureProvider([bad_first], [bad_repair])

    result = build_article_architecture(
        plan, ledger, _manifest(), architecture_provider=provider
    )

    assert result.stories == []
    assert provider.calls == 1
    assert provider.repair_calls == 1
    assert any("invented numeric content" in item for item in result.validation_errors)
    assert any("invented numeric content" in item for item in result.validation_errors)
    assert len(result.usage["rows"]) == 2


def test_quantitative_figure_fact_chain_is_repaired_from_ledger() -> None:
    plan, ledger = _ledger()
    missing = _story_draft(ledger, variant="a", fact_ids=[])
    result = build_article_architecture(
        plan, ledger, _manifest(), architecture_provider=_provider(missing)
    )
    expected_fact = next(
        fact
        for fact in ledger.facts
        if fact.metadata.get("claim_id") == ledger.claims[0].claim_id
    )
    assert result.validation_errors == []
    assert result.stories[0].figure_contracts[0].fact_ids == [
        expected_fact.fact_id
    ]
    assert any("restored fact_id" in item for item in result.warnings)

    plan_two, ledger_two = _two_claim_ledger()
    claim_a = [
        c for c in ledger_two.claims if c.metadata.get("hypothesis_id") == "hyp-01"
    ][0]
    fact_b = next(
        f for f in ledger_two.facts if f.metadata.get("hypothesis_id") == "hyp-02"
    )
    cross_wired = _story_draft(ledger_two, variant="a", claim_ids=[claim_a.claim_id])
    cross_wired["figures"][0]["fact_ids"] = [fact_b.fact_id]
    cross_wired["figures"][0]["artifact_bindings"] = [
        {"artifact_id": "A.json", "selected_fields": ["R_mean"]}
    ]
    manifest_two = [
        ArtifactDescriptor(
            artifact_id="A.json",
            path="a.json",
            fields=["R_mean"],
            content_summary="artifact A",
        ),
        ArtifactDescriptor(
            artifact_id="B.json",
            path="b.json",
            fields=["worst_case"],
            content_summary="artifact B",
        ),
    ]
    result = build_article_architecture(
        plan_two,
        ledger_two,
        manifest_two,
        architecture_provider=_provider(cross_wired),
    )
    expected_fact_a = next(
        fact
        for fact in ledger_two.facts
        if fact.metadata.get("claim_id") == claim_a.claim_id
    )
    assert result.validation_errors == []
    assert result.stories[0].figure_contracts[0].fact_ids == [
        expected_fact_a.fact_id
    ]
    assert any("removed orphan fact_ids" in item for item in result.warnings)
    assert any("restored fact_id" in item for item in result.warnings)


def test_limitation_figure_with_counter_evidence_artifacts_is_allowed() -> None:
    plan, ledger = _refuted_ledger()
    refuted = [c for c in ledger.claims if c.status == ClaimStatus.refuted][0]
    manifest = _manifest() + [
        ArtifactDescriptor(
            artifact_id="FAILURE.json",
            path="runs/example/FAILURE.json",
            fields=["R_mean"],
            content_summary="Counter-evidence artifact.",
        )
    ]
    draft = _story_draft(
        ledger,
        variant="a",
        claim_ids=[refuted.claim_id],
        claim_role="limitation",
        fact_ids=[],
        artifact_bindings=[
            {"artifact_id": "FAILURE.json", "selected_fields": ["R_mean"]}
        ],
    )
    result = build_article_architecture(
        plan, ledger, manifest, architecture_provider=_provider(draft)
    )
    assert result.validation_errors == []
    story = result.stories[0]
    figure = story.figure_contracts[0]
    assert figure.source_mode == "trusted_artifact"
    assert figure.claim_bindings[0].role == "limitation"
    assignment = next(
        item for item in story.claim_assignments if item.claim_id == refuted.claim_id
    )
    assert assignment.role == "limitation"


def test_counterevidence_role_and_conceptual_artifact_provenance_are_preserved() -> (
    None
):
    plan, ledger = _refuted_ledger()
    refuted = [c for c in ledger.claims if c.status == ClaimStatus.refuted][0]
    manifest = _manifest() + [
        ArtifactDescriptor(
            artifact_id="FAILURE.json",
            path="runs/example/FAILURE.json",
            fields=["R_mean"],
            content_summary="Counter-evidence artifact.",
        ),
        ArtifactDescriptor(
            artifact_id="UNRELATED.json",
            path="runs/example/UNRELATED.json",
            fields=["R_mean"],
            content_summary="Unrelated artifact.",
        ),
    ]
    draft = _story_draft(
        ledger,
        variant="a",
        figure_kind="conceptual",
        claim_ids=[refuted.claim_id],
        claim_role="counterevidence",
        fact_ids=[],
        artifact_bindings=[
            {"artifact_id": "FAILURE.json", "selected_fields": ["R_mean"]}
        ],
    )
    result = build_article_architecture(
        plan, ledger, manifest, architecture_provider=_provider(draft)
    )
    assert result.validation_errors == []
    figure = result.stories[0].figure_contracts[0]
    assert figure.source_mode == "trusted_artifact"
    assignment = next(
        item
        for item in result.stories[0].claim_assignments
        if item.claim_id == refuted.claim_id
    )
    assert assignment.role == "counterevidence"

    draft["figures"][0]["artifact_bindings"] = [
        {"artifact_id": "UNRELATED.json", "selected_fields": ["R_mean"]}
    ]
    rejected = build_article_architecture(
        plan, ledger, manifest, architecture_provider=_provider(draft)
    )
    assert any("unrelated artifacts" in item for item in rejected.validation_errors)
    assert rejected.stories == []


def test_assigned_and_omitted_conflict_warns_and_assignment_wins() -> None:
    plan, ledger = _ledger()
    positive = [
        c for c in ledger.claims if c.status == ClaimStatus.partially_supported
    ][0]
    draft = _story_draft(ledger, variant="a")
    draft["omitted_claims"] = [{"claim_id": positive.claim_id, "reason": "low value"}]
    result = build_article_architecture(
        plan, ledger, _manifest(), architecture_provider=_provider(draft)
    )
    assert result.validation_errors == []
    story = result.stories[0]
    assert story.omitted_claims == []
    assert any("both assigned and omitted" in item for item in result.warnings)


def test_empty_story_candidate_is_not_available() -> None:
    plan, ledger = _ledger()
    empty = _story_draft(ledger, variant="a")
    empty["figures"] = []
    result = build_article_architecture(
        plan, ledger, _manifest(), architecture_provider=_provider(empty)
    )
    assert result.stories == []
    assert result.model_status in {"partial", "unavailable"}
    assert any("malformed story candidate skipped" in item for item in result.warnings)


def test_structurally_duplicate_stories_are_warned() -> None:
    plan, ledger = _ledger()
    a = _story_draft(ledger, variant="a")
    b = _story_draft(ledger, variant="a", thesis="Different wording entirely.")
    result = build_article_architecture(
        plan, ledger, _manifest(), architecture_provider=_provider(a, b)
    )
    assert result.validation_errors == []
    assert any("structurally duplicate" in item for item in result.warnings)


def test_more_than_five_candidates_are_bounded_to_five() -> None:
    plan, ledger = _ledger()
    role_keys = ["spectrum", "portfolio", "robustness", "r4", "r5", "r6"]
    drafts = [
        _story_draft(ledger, variant=f"v{index}", role_key=role_key)
        for index, role_key in enumerate(role_keys)
    ]
    result = build_article_architecture(
        plan, ledger, _manifest(), architecture_provider=_provider(*drafts)
    )
    assert result.validation_errors == []
    assert len(result.stories) == 5
    assert any("kept the first 5" in item for item in result.warnings)


def test_invented_numeric_string_rejected_while_bound_number_passes() -> None:
    plan, ledger = _ledger()
    ok = _story_draft(ledger, variant="a")
    ok["central_thesis"] = "The band from 450 to 700 nm is covered by the claim."
    result = build_article_architecture(
        plan, ledger, _manifest(), architecture_provider=_provider(ok)
    )
    assert result.validation_errors == []
    assert result.stories

    bad = _story_draft(
        ledger, variant="b", thesis="Reflectance improves to 999 percent."
    )
    result = build_article_architecture(
        plan, ledger, _manifest(), architecture_provider=_provider(bad)
    )
    assert any("invented numeric content" in item for item in result.validation_errors)
    assert result.stories == []

    bad_decimal = _story_draft(
        ledger, variant="c", thesis="The measured reflectance was 0.123."
    )
    result = build_article_architecture(
        plan, ledger, _manifest(), architecture_provider=_provider(bad_decimal)
    )
    assert any("invented numeric content" in item for item in result.validation_errors)
    assert result.stories == []


def test_invalid_candidate_rejected_while_valid_sibling_survives() -> None:
    plan, ledger = _ledger()
    bad = _story_draft(
        ledger, variant="a", thesis="Reflectance improves to 999 percent."
    )
    good = _story_draft(ledger, variant="b", role_key="portfolio")
    result = build_article_architecture(
        plan, ledger, _manifest(), architecture_provider=_provider(bad, good)
    )
    assert result.validation_errors == []
    assert len(result.stories) == 1
    assert result.stories[0].story_shape == "shape-b"
    assert result.stories[0].central_thesis.endswith("story.")
    assert any(
        "story candidate 1 rejected" in item and "invented numeric content" in item
        for item in result.warnings
    )
    assert not any("999 percent" in item.central_thesis for item in result.stories)


def test_provenance_invalid_candidate_rejected_while_valid_sibling_survives() -> None:
    plan, ledger = _refuted_ledger()
    refuted = [c for c in ledger.claims if c.status == ClaimStatus.refuted][0]
    manifest = _manifest() + [
        ArtifactDescriptor(
            artifact_id="FAILURE.json",
            path="runs/example/FAILURE.json",
            fields=["R_mean"],
            content_summary="Counter-evidence artifact.",
        ),
        ArtifactDescriptor(
            artifact_id="UNRELATED.json",
            path="runs/example/UNRELATED.json",
            fields=["R_mean"],
            content_summary="Unrelated artifact.",
        ),
    ]
    bad = _story_draft(
        ledger,
        variant="a",
        claim_ids=[refuted.claim_id],
        claim_role="limitation",
        fact_ids=[],
        artifact_bindings=[
            {"artifact_id": "UNRELATED.json", "selected_fields": ["R_mean"]}
        ],
    )
    good = _story_draft(
        ledger,
        variant="b",
        role_key="portfolio",
        claim_ids=[refuted.claim_id],
        claim_role="counterevidence",
        fact_ids=[],
        figure_kind="conceptual",
    )
    result = build_article_architecture(
        plan,
        ledger,
        manifest,
        architecture_provider=_provider(bad, good),
    )
    assert result.validation_errors == []
    assert len(result.stories) == 1
    assert result.stories[0].story_shape == "shape-b"
    assert any(
        "story candidate 1 rejected" in item and "unrelated artifacts" in item
        for item in result.warnings
    )


def test_sole_invalid_candidate_hard_block_preserves_provider_usage() -> None:
    plan, ledger = _ledger()
    bad = _story_draft(
        ledger, variant="a", thesis="Reflectance improves to 999 percent."
    )
    usage = {
        "estimated_input_tokens": 123,
        "estimated_output_tokens": 45,
        "attempts": 1,
    }
    result = build_article_architecture(
        plan,
        ledger,
        _manifest(),
        architecture_provider=_provider(bad, usage=usage),
    )
    assert result.stories == []
    assert any("invented numeric content" in item for item in result.validation_errors)
    assert result.model_status == "unavailable"
    assert result.semantic_model == "fake-test-provider"
    assert result.usage == usage


def test_repair_round_fixes_invalid_candidate_and_retains_both_usage_rows() -> None:
    plan, ledger = _ledger()
    bad = _story_draft(
        ledger, variant="a", thesis="Reflectance improves to 999 percent."
    )
    good = _story_draft(ledger, variant="b", role_key="portfolio")
    provider = RepairCapableArchitectureProvider(
        [bad],
        [good],
        first_usage={
            "model_name": "fake-provider",
            "input_tokens": 10,
            "output_tokens": 20,
        },
        repair_usage={
            "model_name": "fake-provider",
            "input_tokens": 30,
            "output_tokens": 40,
        },
    )

    result = build_article_architecture(
        plan, ledger, _manifest(), architecture_provider=provider
    )

    assert result.validation_errors == []
    assert len(result.stories) == 1
    assert result.stories[0].story_shape == "shape-b"
    assert result.model_status == "partial"
    assert provider.calls == 1
    assert provider.repair_calls == 1
    assert len(provider.repair_requests) == 1
    repair_request = provider.repair_requests[0]
    assert repair_request["candidate_index"] == 1
    assert repair_request["candidate"]["story_shape"] == "shape-a"
    assert "rejected_candidates" not in repair_request
    assert any("invented numeric content" in item for item in repair_request["errors"])
    assert any("repair round" in item for item in result.warnings)
    assert any(
        "story candidate 1 rejected" in item and "invented numeric content" in item
        for item in result.warnings
    )
    assert len(result.usage["rows"]) == 2
    assert result.usage["request_attempt_count"] == 2
    assert result.usage["input_tokens"] == 40
    assert result.usage["output_tokens"] == 60


def test_repair_selects_fewest_errors_then_score_then_index() -> None:
    plan, ledger = _ledger()
    good = _story_draft(ledger, variant="z", role_key="portfolio")

    def first_repair_request(first_stories: list[dict]) -> dict:
        provider = RepairCapableArchitectureProvider(first_stories, [good])
        result = build_article_architecture(
            plan, ledger, _manifest(), architecture_provider=provider
        )
        assert result.validation_errors == []
        assert len(result.stories) == 1
        assert provider.calls == 1
        assert provider.repair_calls == 1
        assert len(provider.repair_requests) == 1
        return provider.repair_requests[0]

    few_errors = _story_draft(
        ledger, variant="a", thesis="Reflectance improves to 999 percent."
    )
    many_errors = _story_draft(
        ledger,
        variant="b",
        thesis="Reflectance improves to 999 percent and 888 percent.",
    )
    request = first_repair_request([few_errors, many_errors])
    assert request["candidate_index"] == 1
    assert request["candidate"]["story_shape"] == "shape-a"
    assert len(request["errors"]) == 1

    low_score = _story_draft(
        ledger, variant="a", thesis="Reflectance improves to 999 percent."
    )
    low_score["recommendation_score"] = 0.4
    high_score = _story_draft(
        ledger, variant="b", thesis="Reflectance improves to 888 percent."
    )
    high_score["recommendation_score"] = 0.9
    request = first_repair_request([low_score, high_score])
    assert request["candidate_index"] == 2
    assert request["candidate"]["story_shape"] == "shape-b"
    assert len(request["errors"]) == 1

    first = _story_draft(
        ledger, variant="a", thesis="Reflectance improves to 999 percent."
    )
    second = _story_draft(
        ledger, variant="b", thesis="Reflectance improves to 888 percent."
    )
    request = first_repair_request([first, second])
    assert request["candidate_index"] == 1
    assert request["candidate"]["story_shape"] == "shape-a"
    assert len(request["errors"]) == 1


def test_second_repair_round_succeeds_after_strict_progress() -> None:
    plan, ledger = _ledger()
    first = _story_draft(
        ledger,
        variant="a",
        thesis="Reflectance improves to 999 percent, 888 percent, and 777 percent.",
    )
    round_one = _story_draft(
        ledger,
        variant="b",
        thesis="Reflectance improves to 999 percent and 888 percent.",
    )
    round_two = _story_draft(ledger, variant="z", role_key="portfolio")
    provider = ProgressiveRepairProvider(
        [first],
        [[round_one], [round_two]],
        first_usage={
            "model_name": "fake-provider",
            "input_tokens": 1,
            "output_tokens": 2,
        },
        repair_usages=[
            {
                "model_name": "fake-provider",
                "input_tokens": 3,
                "output_tokens": 4,
            },
            {
                "model_name": "fake-provider",
                "input_tokens": 5,
                "output_tokens": 6,
            },
        ],
    )

    result = build_article_architecture(
        plan, ledger, _manifest(), architecture_provider=provider
    )

    assert result.validation_errors == []
    assert len(result.stories) == 1
    assert result.stories[0].story_shape == "shape-z"
    assert result.model_status == "partial"
    assert provider.calls == 1
    assert provider.repair_calls == 2
    assert len(provider.repair_requests) == 2
    round_two_request = provider.repair_requests[1]
    assert round_two_request["candidate_index"] == 2
    assert round_two_request["candidate"]["story_shape"] == "shape-b"
    assert len(round_two_request["errors"]) == 2
    assert len(result.usage["rows"]) == 3
    assert result.usage["request_attempt_count"] == 3
    assert result.usage["input_tokens"] == 9
    assert result.usage["output_tokens"] == 12


def test_no_progress_repair_stops_after_one_round() -> None:
    plan, ledger = _ledger()
    first = _story_draft(
        ledger,
        variant="a",
        thesis="Reflectance improves to 999 percent and 888 percent.",
    )
    same_errors = _story_draft(
        ledger,
        variant="b",
        thesis="Reflectance improves to 999 percent and 888 percent.",
    )
    provider = ProgressiveRepairProvider(
        [first],
        [[same_errors]],
    )

    result = build_article_architecture(
        plan, ledger, _manifest(), architecture_provider=provider
    )

    assert result.stories == []
    assert provider.calls == 1
    assert provider.repair_calls == 1
    assert len(provider.repair_requests) == 1
    assert len(result.validation_errors) == 4
    assert len(result.usage["rows"]) == 2
    assert result.usage["request_attempt_count"] == 2
    assert any(
        "repaired story candidate 2 rejected" in item for item in result.warnings
    )


def test_repair_round_two_failure_hard_blocks_with_all_diagnostics() -> None:
    plan, ledger = _ledger()
    first = _story_draft(
        ledger,
        variant="a",
        thesis="Reflectance improves to 999 percent, 888 percent, and 777 percent.",
    )
    round_one = _story_draft(
        ledger,
        variant="b",
        thesis="Reflectance improves to 999 percent and 888 percent.",
    )
    round_two = _story_draft(
        ledger,
        variant="c",
        thesis="Absorptance improves to 888 percent and 777 percent.",
    )
    provider = ProgressiveRepairProvider(
        [first],
        [[round_one], [round_two]],
    )

    result = build_article_architecture(
        plan, ledger, _manifest(), architecture_provider=provider
    )

    assert result.stories == []
    assert provider.calls == 1
    assert provider.repair_calls == 2
    assert len(provider.repair_requests) == 2
    assert len(result.validation_errors) == 7
    assert any(
        "repaired story candidate 3 rejected" in item for item in result.warnings
    )
    assert len(result.usage["rows"]) == 3
    assert result.usage["request_attempt_count"] == 3
    assert result.usage["input_tokens"] == 9
    assert result.usage["output_tokens"] == 12


def test_valid_first_response_does_not_call_repair() -> None:
    plan, ledger = _ledger()
    good_a = _story_draft(ledger, variant="a")
    good_b = _story_draft(ledger, variant="b", role_key="portfolio")
    provider = RepairCapableArchitectureProvider([good_a, good_b], [])

    result = build_article_architecture(
        plan, ledger, _manifest(), architecture_provider=provider
    )

    assert result.validation_errors == []
    assert len(result.stories) == 2
    assert result.model_status == "available"
    assert provider.calls == 1
    assert provider.repair_calls == 0
    assert "rows" not in result.usage


def test_valid_but_undercovered_stories_receive_bounded_completion_repair() -> None:
    plan, ledger = _two_claim_ledger()
    positive = [
        claim
        for claim in ledger.claims
        if claim.status == ClaimStatus.partially_supported
    ]
    assert len(positive) == 2
    omitted = [{"claim_id": positive[1].claim_id, "reason": "secondary route"}]
    narrow_a = _story_draft(
        ledger,
        variant="a",
        claim_ids=[positive[0].claim_id],
        figure_kind="conceptual",
        omitted=omitted,
    )
    narrow_b = _story_draft(
        ledger,
        variant="b",
        claim_ids=[positive[0].claim_id],
        figure_kind="conceptual",
        omitted=omitted,
        role_key="portfolio",
    )
    complete = _story_draft(
        ledger,
        variant="complete",
        claim_ids=[positive[0].claim_id],
        figure_kind="conceptual",
    )
    complete["sections"][0]["claim_bindings"].append(
        {"claim_id": positive[1].claim_id, "role": "positive"}
    )
    provider = RepairCapableArchitectureProvider(
        [narrow_a, narrow_b],
        [complete],
    )
    manifest = [
        ArtifactDescriptor(
            artifact_id="A.json",
            path="a.json",
            fields=["R_mean"],
            content_summary="artifact A",
        ),
        ArtifactDescriptor(
            artifact_id="B.json",
            path="b.json",
            fields=["worst_case"],
            content_summary="artifact B",
        ),
    ]

    result = build_article_architecture(
        plan,
        ledger,
        manifest,
        architecture_provider=provider,
    )

    assert result.validation_errors == []
    assert len(result.stories) == 2
    assert all(len(story.claim_assignments) == 2 for story in result.stories)
    assert provider.repair_calls == 2
    assert all(
        request["story_completion_contract"]["minimum_assigned_claim_count"] == 2
        for request in provider.repair_requests
    )
    assert any("completion repair improved" in warning for warning in result.warnings)


def test_completion_repair_reuses_form_returned_by_format_repair() -> None:
    plan, ledger = _two_claim_ledger()
    positive = [
        claim
        for claim in ledger.claims
        if claim.status == ClaimStatus.partially_supported
    ]
    omitted = [{"claim_id": positive[1].claim_id, "reason": "secondary route"}]
    narrow = _story_draft(
        ledger,
        variant="narrow",
        claim_ids=[positive[0].claim_id],
        figure_kind="conceptual",
        omitted=omitted,
    )
    malformed = _story_draft(
        ledger,
        variant="malformed",
        claim_ids=[positive[0].claim_id],
        figure_kind="conceptual",
        omitted=omitted,
        role_key="malformed",
    )
    del malformed["sections"]
    format_repaired = _story_draft(
        ledger,
        variant="format-repaired",
        claim_ids=[positive[0].claim_id],
        figure_kind="conceptual",
        omitted=omitted,
        role_key="portfolio",
    )
    format_repaired["recommendation_score"] = 0.95
    complete = _story_draft(
        ledger,
        variant="complete",
        claim_ids=[positive[0].claim_id],
        figure_kind="conceptual",
        role_key="complete",
    )
    complete["sections"][0]["claim_bindings"].append(
        {"claim_id": positive[1].claim_id, "role": "positive"}
    )
    provider = ProgressiveRepairProvider(
        [narrow, malformed],
        [[format_repaired], [complete], [complete]],
    )
    manifest = [
        ArtifactDescriptor(
            artifact_id="A.json",
            path="a.json",
            fields=["R_mean"],
            content_summary="artifact A",
        ),
        ArtifactDescriptor(
            artifact_id="B.json",
            path="b.json",
            fields=["worst_case"],
            content_summary="artifact B",
        ),
    ]

    result = build_article_architecture(
        plan,
        ledger,
        manifest,
        architecture_provider=provider,
    )

    assert result.validation_errors == []
    assert provider.repair_calls == 3
    repaired = next(story for story in result.stories if story.story_id == "story-03")
    assert len(repaired.claim_assignments) == 2
    assert provider.repair_requests[1]["candidate_index"] == 3
    assert any(
        "completion repair improved story-03" in warning for warning in result.warnings
    )


def test_one_valid_sibling_still_repairs_best_rejected_candidate() -> None:
    plan, ledger = _ledger()
    good = _story_draft(ledger, variant="a")
    bad = _story_draft(
        ledger, variant="b", thesis="Reflectance improves to 999 percent."
    )
    repaired = _story_draft(ledger, variant="c", role_key="robustness")
    provider = RepairCapableArchitectureProvider([good, bad], [repaired])

    result = build_article_architecture(
        plan, ledger, _manifest(), architecture_provider=provider
    )

    assert result.validation_errors == []
    assert len(result.stories) == 2
    assert result.model_status == "available"
    assert provider.calls == 1
    assert provider.repair_calls == 1
    assert {story.story_shape for story in result.stories} == {
        "shape-a",
        "shape-c",
    }


def test_one_valid_sibling_repairs_malformed_model_form() -> None:
    plan, ledger = _ledger()
    good = _story_draft(ledger, variant="a")
    malformed = _story_draft(ledger, variant="b", role_key="portfolio")
    del malformed["sections"]
    repaired = _story_draft(ledger, variant="c", role_key="robustness")
    provider = RepairCapableArchitectureProvider(
        [good, malformed],
        [repaired],
    )

    result = build_article_architecture(
        plan, ledger, _manifest(), architecture_provider=provider
    )

    assert result.validation_errors == []
    assert len(result.stories) == 2
    assert provider.repair_calls == 1
    assert any(
        "malformed story candidate skipped" in warning for warning in result.warnings
    )
    assert {story.story_shape for story in result.stories} == {
        "shape-a",
        "shape-c",
    }


def test_invalid_first_and_invalid_repair_hard_blocks_with_both_rounds() -> None:
    plan, ledger = _ledger()
    bad_a = _story_draft(
        ledger, variant="a", thesis="Reflectance improves to 999 percent."
    )
    bad_b = _story_draft(
        ledger, variant="b", thesis="Absorptance improves to 888 percent."
    )
    provider = RepairCapableArchitectureProvider([bad_a], [bad_b])

    result = build_article_architecture(
        plan, ledger, _manifest(), architecture_provider=provider
    )

    assert result.stories == []
    assert result.model_status == "unavailable"
    assert provider.calls == 1
    assert provider.repair_calls == 1
    assert len(result.validation_errors) == 2
    assert all("invented numeric content" in item for item in result.validation_errors)
    assert any(
        "repaired story candidate 2 rejected" in item
        and "invented numeric content" in item
        for item in result.warnings
    )
    assert len(result.usage["rows"]) == 2


def test_global_input_integrity_error_never_calls_provider_or_repair() -> None:
    plan_a, ledger_a = _ledger()
    plan_b_result = ArticleDirector().plan(
        "Another question about a different coating.",
        _analysis(),
        _report(),
        force_mock=True,
    )
    assert plan_b_result.status == "planned" and plan_b_result.plan is not None
    provider = RepairCapableArchitectureProvider([], [])

    result = build_article_architecture(
        plan_b_result.plan, ledger_a, _manifest(), architecture_provider=provider
    )

    assert any("source_plan_id" in item for item in result.validation_errors)
    assert result.stories == []
    assert provider.calls == 0
    assert provider.repair_calls == 0


def test_plain_structural_integers_do_not_hard_block() -> None:
    plan, ledger = _ledger()
    draft = _story_draft(
        ledger,
        variant="a",
        thesis="Stage 2 compares 3 routes in Figure 1 with a 2D map.",
    )
    result = build_article_architecture(
        plan, ledger, _manifest(), architecture_provider=_provider(draft)
    )
    assert result.validation_errors == []
    assert result.stories


def test_unknown_artifact_yields_controlled_error_not_keyerror() -> None:
    plan, ledger = _ledger()
    draft = _story_draft(
        ledger,
        variant="a",
        artifact_bindings=[{"artifact_id": "GHOST.json", "selected_fields": ["x"]}],
    )
    result = build_article_architecture(
        plan, ledger, _manifest(), architecture_provider=_provider(draft)
    )
    assert any("unknown artifact" in item for item in result.validation_errors)
    assert result.stories == []


def test_input_integrity_duplicate_ids_and_artifact_fields() -> None:
    plan, ledger = _ledger()

    dup_claims = ledger.model_copy(
        update={"claims": ledger.claims + [ledger.claims[0]]}
    )
    result = build_article_architecture(
        plan, dup_claims, _manifest(), architecture_provider=None
    )
    assert any("duplicate claim IDs" in item for item in result.validation_errors)

    dup_facts = ledger.model_copy(update={"facts": ledger.facts + [ledger.facts[0]]})
    result = build_article_architecture(
        plan, dup_facts, _manifest(), architecture_provider=None
    )
    assert any("duplicate fact IDs" in item for item in result.validation_errors)

    draft_claim = ledger.claims[1]
    stolen = draft_claim.model_copy(
        update={
            "metadata": {
                **draft_claim.metadata,
                "fact_id": ledger.facts[0].fact_id,
            }
        }
    )
    ambiguous = ledger.model_copy(update={"claims": [ledger.claims[0], stolen]})
    result = build_article_architecture(
        plan, ambiguous, _manifest(), architecture_provider=None
    )
    assert any("ambiguous claim ownership" in item for item in result.validation_errors)

    dup_fields = build_article_architecture(
        plan,
        ledger,
        [{"artifact_id": "X.json", "path": "x.json", "fields": ["a", "a"]}],
        architecture_provider=None,
    )
    assert any(
        "artifact_manifest[0]" in item and "unique" in item
        for item in dup_fields.validation_errors
    )

    empty_field = build_article_architecture(
        plan,
        ledger,
        [{"artifact_id": "X.json", "path": "x.json", "fields": [""]}],
        architecture_provider=None,
    )
    assert any(
        "artifact_manifest[0]" in item and "non-empty" in item
        for item in empty_field.validation_errors
    )

    bad_descriptions = _manifest()[0].model_copy(
        update={"field_descriptions": {"ghost": "not declared"}}
    )
    result = build_article_architecture(
        plan, ledger, [bad_descriptions], architecture_provider=None
    )
    assert any("undeclared fields" in item for item in result.validation_errors)


def test_payload_contains_full_inventories_and_local_fields_are_fixed() -> None:
    plan, ledger = _ledger()
    captured: list[dict] = []
    provider = _provider(
        _story_draft(ledger, variant="a"),
        _story_draft(ledger, variant="b", role_key="portfolio"),
        _story_draft(ledger, variant="c", role_key="robustness"),
        captured=captured,
    )
    build_article_architecture(
        plan, ledger, _manifest(), architecture_provider=provider
    )
    payload = captured[0]
    positive_ids = {
        c.claim_id for c in ledger.claims if c.status == ClaimStatus.partially_supported
    }
    assert {item["claim_id"] for item in payload["positive_claims"]} == positive_ids
    assert {item["artifact_id"] for item in payload["artifacts"]} == {
        "FINAL_RESULT.json"
    }
    assert payload["artifacts"][0]["content_summary"]
    assert "allowed_fields" in payload["artifacts"][0]

    forged = _story_draft(ledger, variant="a")
    forged["figures"][0]["source_mode"] = "conceptual"
    forged["figures"][0]["artifact_bindings"][0]["field_shapes"] = ["scalar"]
    result = build_article_architecture(
        plan, ledger, _manifest(), architecture_provider=_provider(forged)
    )
    assert result.validation_errors == []
    assert len(result.stories) == 1
    assert result.stories[0].figure_contracts[0].source_mode == "trusted_artifact"
    assert any(
        "ignored redundant provider fields" in item
        and "source_mode" in item
        and "field_shapes" in item
        for item in result.warnings
    )


def test_persistence_is_idempotent_and_resumes_after_failure(tmp_path) -> None:
    plan, ledger = _ledger()
    provider = _provider(
        _story_draft(ledger, variant="a"),
        _story_draft(ledger, variant="b", role_key="portfolio"),
        _story_draft(ledger, variant="c", role_key="robustness"),
    )
    memory = ArticleMemoryStore(tmp_path / "memory.sqlite")
    graph = ExperimentGraph(tmp_path / "graph.sqlite", "run-1")
    journal = tmp_path / "journal.json"
    original_create = graph.create_article_node

    def failing_create(*args, **kwargs):
        raise RuntimeError("graph write failed")

    graph.create_article_node = failing_create  # type: ignore[method-assign]
    with pytest.raises(Exception, match="architecture persistence failed"):
        build_article_architecture(
            plan,
            ledger,
            _manifest(),
            architecture_provider=provider,
            memory_store=memory,
            graph=graph,
            run_id="run-1",
            journal_path=journal,
        )
    graph.create_article_node = original_create  # type: ignore[method-assign]
    architecture_id = next(
        key for key in json.loads(journal.read_text(encoding="utf-8"))
    )
    result = build_article_architecture(
        plan,
        ledger,
        _manifest(),
        architecture_provider=provider,
        memory_store=memory,
        graph=graph,
        run_id="run-1",
        journal_path=journal,
    )
    assert result.architecture_id == architecture_id
    node = graph.article_node(f"architecture-{architecture_id}")
    assert len([e for e in node["history"] if e["event_type"] == "article.figure"]) == 3
    memory_count = len(memory.run_memory_records())
    history_len = len(node["history"])

    retry = build_article_architecture(
        plan,
        ledger,
        _manifest(),
        architecture_provider=provider,
        memory_store=memory,
        graph=graph,
        run_id="run-1",
        journal_path=journal,
    )
    assert retry.architecture_id == result.architecture_id
    assert len(memory.run_memory_records()) == memory_count
    assert (
        len(graph.article_node(f"architecture-{architecture_id}")["history"])
        == history_len
    )
