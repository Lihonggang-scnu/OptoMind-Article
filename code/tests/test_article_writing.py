from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from optomind_optics.harness.article_architecture import (
    ArchitectureProviderResult,
    ArtifactDescriptor,
    ClaimPlacement,
    build_article_architecture,
    compute_architecture_id,
)
from optomind_optics.harness.article_claims import build_claim_ledger
from optomind_optics.harness.article_contracts import (
    ClaimStatus,
    ExperimentStatus,
    ObservationCard,
)
from optomind_optics.harness.article_director import ArticleDirector
from optomind_optics.harness.article_feedback import ArticleFeedbackController
from optomind_optics.harness.article_memory import ArticleMemoryStore
from optomind_optics.harness.article_writing import (
    _fact_by_claim,
    _validate_story_contract,
    ArticleDraftBundle,
    QwenFormatRepair,
    QwenSectionWriter,
    TrustedValueRecord,
    WriterProviderResult,
    build_article_draft_bundle,
)
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


def _custom_plan_ledger(question: str):
    result = ArticleDirector().plan(
        question,
        _analysis(),
        _report(),
        force_mock=True,
    )
    assert result.status == "planned" and result.plan is not None
    plan = result.plan
    observation = _observation()
    feedback = ArticleFeedbackController().update(plan, [observation])
    ledger = build_claim_ledger(plan, [feedback], [observation])
    assert ledger.validation_errors == []
    return plan, ledger


def _observation(observation_id="obs-1", experiment_id="exp-1") -> ObservationCard:
    return ObservationCard(
        observation_id=observation_id,
        experiment_id=experiment_id,
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


def _ledger():
    plan = _plan()
    observation = _observation()
    feedback = ArticleFeedbackController().update(plan, [observation])
    ledger = build_claim_ledger(plan, [feedback], [observation])
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
            sha256="a" * 64,
            source_experiment_ids=["exp-1"],
            source_observation_ids=["obs-1"],
        )
    ]


def _failure_manifest() -> list[ArtifactDescriptor]:
    return _manifest() + [
        ArtifactDescriptor(
            artifact_id="FAILURE.json",
            path="runs/example/FAILURE.json",
            fields=["R_mean"],
            content_summary="Counter-evidence artifact.",
        )
    ]


def _story_draft(
    ledger,
    *,
    variant: str = "a",
    claim_role: str = "positive",
    claim_id: str | None = None,
    figure_artifact: str = "FINAL_RESULT.json",
    figure_fields: list[str] | None = None,
) -> dict:
    positive = [c for c in ledger.claims if c.status == ClaimStatus.partially_supported]
    if claim_id is None:
        assert positive, "fixture requires a positive claim when claim_id omitted"
        claim_id = positive[0].claim_id
    fact = next(
        (f for f in ledger.facts if f.metadata.get("claim_id") == claim_id),
        None,
    )
    figure = {
        "role_key": "spectrum",
        "kind": "quantitative",
        "story_role": "spectral response",
        "panel_intents": ["panel"],
        "caption_intent": "verified spectrum",
        "claim_bindings": [{"claim_id": claim_id, "role": claim_role}],
        "fact_ids": [fact.fact_id] if fact is not None else [],
        "artifact_bindings": [
            {
                "artifact_id": figure_artifact,
                "selected_fields": figure_fields or ["R_mean", "worst_case"],
            }
        ],
        "limitations": ["solver only"],
    }
    binding = {"claim_id": claim_id, "role": claim_role}
    sections = [
        {
            "heading": f"Results {variant}",
            "purpose": "present the verified result evidence",
            "key_messages": ["key"],
            "transitions": ["next"],
            "claim_bindings": [binding],
            "figure_roles": ["spectrum"],
        },
        {
            "heading": f"Methods {variant}",
            "purpose": "describe the method",
            "key_messages": ["key"],
            "transitions": ["next"],
            "claim_bindings": [binding],
            "figure_roles": [],
        },
        {
            "heading": f"Limitations {variant}",
            "purpose": "state the limitations",
            "key_messages": ["key"],
            "transitions": ["next"],
            "claim_bindings": [binding],
            "figure_roles": [],
        },
    ]
    return {
        "story_shape": f"shape-{variant}",
        "central_thesis": f"Thesis {variant}: an evidence-bound AR design story.",
        "sections": sections,
        "figures": [figure],
        "omitted_claims": [],
        "exclusions": [f"excluded-{variant}"],
        "strengths": [f"strength-{variant}"],
        "risks": [f"risk-{variant}"],
        "recommendation_rationale": f"rationale-{variant}",
        "recommendation_score": 0.6,
    }


def _split_claims_story_draft(ledger) -> dict:
    positive = [
        c for c in ledger.claims if c.status == ClaimStatus.partially_supported
    ][0]
    draft_claim = [c for c in ledger.claims if c.status == ClaimStatus.draft][0]
    fact = next(
        f for f in ledger.facts if f.metadata.get("claim_id") == positive.claim_id
    )
    figure = {
        "role_key": "spectrum",
        "kind": "quantitative",
        "story_role": "spectral response",
        "panel_intents": ["panel"],
        "caption_intent": "verified spectrum",
        "claim_bindings": [{"claim_id": positive.claim_id, "role": "positive"}],
        "fact_ids": [fact.fact_id],
        "artifact_bindings": [
            {
                "artifact_id": "FINAL_RESULT.json",
                "selected_fields": ["R_mean", "worst_case"],
            }
        ],
        "limitations": ["solver only"],
    }
    return {
        "story_shape": "shape-split",
        "central_thesis": "A split-claims evidence-bound story.",
        "sections": [
            {
                "heading": "Results split",
                "purpose": "present the verified result evidence",
                "key_messages": ["key"],
                "transitions": ["next"],
                "claim_bindings": [{"claim_id": positive.claim_id, "role": "positive"}],
                "figure_roles": ["spectrum"],
            },
            {
                "heading": "Limitations split",
                "purpose": "state the limitations",
                "key_messages": ["key"],
                "transitions": ["next"],
                "claim_bindings": [
                    {"claim_id": draft_claim.claim_id, "role": "limitation"}
                ],
                "figure_roles": [],
            },
        ],
        "figures": [figure],
        "omitted_claims": [],
        "exclusions": ["excluded-split"],
        "strengths": ["strength-split"],
        "risks": ["risk-split"],
        "recommendation_rationale": "rationale-split",
        "recommendation_score": 0.6,
    }


def _with_identity(architecture, plan, ledger, story) -> Any:
    updated = architecture.model_copy(update={"stories": [story]})
    new_id = compute_architecture_id(
        plan.plan_id,
        ledger.ledger_id,
        updated.artifact_inventory,
        updated.missing_work_handoffs,
        updated.stories,
    )
    return updated.model_copy(update={"architecture_id": new_id})


def _architecture_provider(story_draft: dict):
    def provider(requests):
        return [
            ArchitectureProviderResult(
                stories=[story_draft],
                provider_model="fake-architecture-provider",
                usage={"estimated_input_tokens": 5, "estimated_output_tokens": 5},
            )
        ]

    return provider


def _architecture(plan, ledger, *, manifest=None, story_draft=None):
    result = build_article_architecture(
        plan,
        ledger,
        manifest if manifest is not None else _manifest(),
        architecture_provider=_architecture_provider(
            story_draft if story_draft is not None else _story_draft(ledger)
        ),
    )
    assert result.validation_errors == []
    assert len(result.stories) == 1
    return result, result.stories[0].story_id


def _value_records() -> list[TrustedValueRecord]:
    return [
        TrustedValueRecord(
            artifact_id="FINAL_RESULT.json",
            field="R_mean",
            rendered_value="0.004",
            unit="",
            source_hash="a" * 64,
            derivation="FINAL_RESULT.json:R_mean",
            label="mean reflectance",
            prose_safe=True,
        ),
        TrustedValueRecord(
            artifact_id="FINAL_RESULT.json",
            field="worst_case",
            rendered_value="0.02",
            unit="",
            label="worst-case reflectance array",
            prose_safe=False,
        ),
    ]


def _good_response(
    request,
    *,
    role: str = "result",
    kind: str = "bounded_inference",
    cite: bool = True,
    text: str | None = None,
    tokens: bool = True,
    note: str = "local bounded inference from the cited claim",
) -> dict:
    claim_aliases = [b["claim_alias"] for b in request["section"]["claim_bindings"]]
    figure_aliases = list(request["section"]["figure_aliases"])
    value_aliases = [item["alias"] for item in request["values"]]
    body = (
        text
        if text is not None
        else "The verified evidence supports the stated design claim within the "
        "declared scope."
    )
    if tokens:
        for alias in value_aliases:
            body = f"{body} [VALUE:{alias}]"
    return {
        "paragraphs": [
            {
                "text_with_value_tokens": body,
                "claim_aliases": claim_aliases if cite else [],
                "figure_aliases": figure_aliases,
                "paragraph_role": role,
                "inference_kind": kind,
                "inference_note": note if kind == "bounded_inference" else "",
            }
        ],
        "deferred_claim_aliases": [],
        "author_notes": [],
    }


def _writer(responses=None, *, model="fake-writer", captured=None):
    if responses is not None and not callable(responses):
        responses = list(responses)

    def provider(request):
        if captured is not None:
            captured.append(dict(request))
        if callable(responses):
            raw = responses(request)
        else:
            item = responses.pop(0)
            raw = item(request) if callable(item) else item
        if isinstance(raw, Exception):
            raise raw
        return WriterProviderResult(
            response=raw,
            usage={"estimated_input_tokens": 10, "estimated_output_tokens": 20},
            provider_model=model,
        )

    return provider


def _repairer(builder, *, model="fake-repair", captured=None):
    def provider(request):
        if captured is not None:
            captured.append(dict(request))
        return WriterProviderResult(
            response=builder(request),
            usage={"estimated_input_tokens": 3, "estimated_output_tokens": 4},
            provider_model=model,
        )

    return provider


def _sequence_repairer(responses, *, model="fake-repair", captured=None):
    responses = list(responses)

    def provider(request):
        if captured is not None:
            captured.append(dict(request))
        raw = responses.pop(0)
        if isinstance(raw, Exception):
            raise raw
        response = raw(request) if callable(raw) else raw
        return WriterProviderResult(
            response=response,
            usage={"estimated_input_tokens": 3, "estimated_output_tokens": 4},
            provider_model=model,
        )

    return provider


def test_multi_section_writes_independently_with_reversible_alias_maps() -> None:
    plan, ledger = _ledger()
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
    architecture, story_id = _architecture(
        plan,
        ledger,
        story_draft=_story_draft(ledger, figure_fields=["R_mean", "worst_case"]),
    )
    captured: list[dict] = []
    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(_good_response, captured=captured),
    )
    assert bundle.errors == []
    assert bundle.publishable
    assert len(bundle.sections) == 3
    assert all(item.status == "publishable" for item in bundle.sections)
    assert len(captured) == 3
    for alias, claim_id in bundle.claim_alias_map.items():
        assert alias.startswith("C")
        assert any(item.claim_id == claim_id for item in ledger.claims)
    for alias, fact_id in bundle.fact_alias_map.items():
        claim_id = bundle.claim_alias_map[alias]
        claim = next(item for item in ledger.claims if item.claim_id == claim_id)
        assert claim.metadata.get("fact_id") == fact_id
    for alias, info in bundle.value_alias_map.items():
        assert alias.startswith("V")
        assert info["artifact_id"] == "FINAL_RESULT.json"
    for alias, figure_id in bundle.figure_alias_map.items():
        assert alias.startswith("FIG")
        assert any(
            item.figure_id == figure_id
            for item in architecture.stories[0].figure_contracts
        )
    for section in bundle.sections:
        for entry in section.source_ledger:
            assert entry.claim_ids
            assert entry.fact_ids
            if section.section_id.endswith("-section-01"):
                assert entry.value_token_ids == ["V01_R_MEAN"]
                assert entry.figure_ids == ["story-01-figure-01"]
            else:
                assert entry.value_token_ids == []
                assert entry.figure_ids == []
    rendered = bundle.sections[0].rendered_prose
    assert "0.004" in rendered
    assert "[VALUE:" not in rendered
    request_serialized = json.dumps(captured[0], sort_keys=True)
    assert "rendered_value" not in request_serialized
    assert "0.004" not in request_serialized
    assert len(captured[0]["other_sections_outline"]) == 2
    assert len(captured[0]["claims"]) == 1
    assert captured[0]["claims"][0]["synthesis_contract"] == contract


def test_selected_story_id_required_and_validated() -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(plan, ledger)
    captured: list[dict] = []
    missing = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        "",
        _value_records(),
        section_writer=_writer(_good_response, captured=captured),
    )
    assert any("selected_story_id is required" in item for item in missing.errors)
    assert captured == []

    unknown = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        "story-99",
        _value_records(),
        section_writer=_writer(_good_response, captured=captured),
    )
    assert any("not present in the architecture" in item for item in unknown.errors)
    assert captured == []


def test_foreign_plan_ledger_architecture_hard_block_before_provider() -> None:
    plan_a, ledger_a = _ledger()
    architecture_a, story_id = _architecture(plan_a, ledger_a)
    plan_b_result = ArticleDirector().plan(
        "Another question about a different coating.",
        _analysis(),
        _report(),
        force_mock=True,
    )
    assert plan_b_result.status == "planned" and plan_b_result.plan is not None
    plan_b = plan_b_result.plan
    assert plan_b.plan_id != plan_a.plan_id
    captured: list[dict] = []

    foreign = build_article_draft_bundle(
        plan_b,
        ledger_a,
        architecture_a,
        story_id,
        _value_records(),
        section_writer=_writer(_good_response, captured=captured),
    )
    assert any("source_plan_id" in item for item in foreign.errors)
    assert captured == []

    errored = architecture_a.model_copy(update={"validation_errors": ["tampered"]})
    invalid = build_article_draft_bundle(
        plan_a,
        ledger_a,
        errored,
        story_id,
        _value_records(),
        section_writer=_writer(_good_response, captured=captured),
    )
    assert any("carries validation errors" in item for item in invalid.errors)
    assert captured == []

    mismatched_inventory = architecture_a.model_copy(
        update={
            "deterministic_inventory": {
                **architecture_a.deterministic_inventory,
                "fact_count": 999,
            }
        }
    )
    inventory_bad = build_article_draft_bundle(
        plan_a,
        ledger_a,
        mismatched_inventory,
        story_id,
        _value_records(),
        section_writer=_writer(_good_response, captured=captured),
    )
    assert any("does not match plan/ledger" in item for item in inventory_bad.errors)
    assert captured == []


def test_unknown_claim_fact_artifact_figure_value_links_rejected() -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(plan, ledger)

    result = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(
            lambda request: {
                **_good_response(request),
                "paragraphs": [
                    {
                        **_good_response(request)["paragraphs"][0],
                        "claim_aliases": ["C99_bogus"],
                    }
                ],
            }
        ),
    )
    assert any("unknown claim alias" in item for item in result.sections[0].errors)

    result = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(
            lambda request: {
                **_good_response(request),
                "paragraphs": [
                    {
                        **_good_response(request)["paragraphs"][0],
                        "figure_aliases": ["FIG99_bogus"],
                    }
                ],
            }
        ),
    )
    assert any("unknown figure alias" in item for item in result.sections[0].errors)

    result = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(
            lambda request: {
                **_good_response(request),
                "paragraphs": [
                    {
                        **_good_response(request)["paragraphs"][0],
                        "text_with_value_tokens": "Value is [VALUE:V99_BOGUS].",
                    }
                ],
            }
        ),
    )
    assert any(
        "unknown value token alias" in item for item in result.sections[0].errors
    )

    captured: list[dict] = []
    ghost_records = _value_records() + [
        TrustedValueRecord(
            artifact_id="GHOST.json",
            field="x",
            rendered_value="1",
            prose_safe=True,
        )
    ]
    result = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        ghost_records,
        section_writer=_writer(_good_response, captured=captured),
    )
    assert any(
        "not present in the Stage 9 artifact inventory" in item
        for item in result.errors
    )
    assert captured == []

    story = architecture.stories[0]
    figure = story.figure_contracts[0]
    tampered_figure = figure.model_copy(update={"fact_ids": ["fact-bogus"]})
    tampered_story = story.model_copy(update={"figure_contracts": [tampered_figure]})
    tampered_architecture = architecture.model_copy(
        update={"stories": [tampered_story]}
    )
    result = build_article_draft_bundle(
        plan,
        ledger,
        tampered_architecture,
        story_id,
        _value_records(),
        section_writer=_writer(_good_response, captured=captured),
    )
    assert any("does not correspond to a bound claim" in item for item in result.errors)
    assert captured == []


def test_literature_alias_binding_is_local_and_unknown_alias_fails_open() -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(plan, ledger)

    def response(request):
        row = _good_response(request)["paragraphs"][0]
        return {
            **_good_response(request),
            "paragraphs": [
                {
                    **row,
                    "literature_evidence_aliases": ["E01_coating", "E99_unknown"],
                }
            ],
        }

    result = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(response),
        literature_context={
            "evidence": [
                {"alias": "E01_coating", "title": "Coating methods", "excerpt": "x"}
            ]
        },
        literature_evidence_alias_map={"E01_coating": "ev-literature-1"},
    )

    paragraph = result.sections[0].paragraphs[0]
    source = result.sections[0].source_ledger[0]
    assert paragraph.literature_evidence_ids == ["ev-literature-1"]
    assert source.literature_evidence_ids == ["ev-literature-1"]
    assert result.sections[0].status == "publishable"
    assert any(
        "unknown literature evidence alias" in item for item in paragraph.warnings
    )


def test_literature_supported_method_paragraph_needs_no_experimental_claim() -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(plan, ledger)

    def response(request):
        row = _good_response(
            request,
            role="method",
            kind="none_required",
            cite=False,
            tokens=False,
        )["paragraphs"][0]
        return {
            "paragraphs": [
                {
                    **row,
                    "literature_evidence_aliases": ["E01_method"],
                }
            ],
            "deferred_claim_aliases": [],
            "author_notes": [],
        }

    result = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(response),
        literature_context={
            "evidence": [{"alias": "E01_method", "title": "Method", "excerpt": "x"}]
        },
        literature_evidence_alias_map={"E01_method": "ev-method-1"},
    )

    section = result.sections[0]
    assert section.status == "publishable"
    assert section.paragraphs[0].claim_ids == []
    assert section.paragraphs[0].literature_evidence_ids == ["ev-method-1"]
    assert any(
        "literature-supported method paragraph" in warning
        for warning in section.paragraphs[0].warnings
    )


def test_exact_numeric_literal_from_cited_claim_is_allowed_without_value_token() -> (
    None
):
    plan, ledger = _ledger()
    claim = ledger.claims[0]
    statement = (
        "Candidate GC01 was evaluated over 8000-13000 nm with an observed "
        "score of 0.85."
    )
    hypothesis_id = claim.metadata["hypothesis_id"]
    plan = plan.model_copy(
        update={
            "hypotheses": [
                (
                    hypothesis.model_copy(update={"statement": statement})
                    if hypothesis.hypothesis_id == hypothesis_id
                    else hypothesis
                )
                for hypothesis in plan.hypotheses
            ]
        }
    )
    ledger = ledger.model_copy(
        update={
            "claims": [claim.model_copy(update={"statement": statement})],
        }
    )
    architecture, story_id = _architecture(plan, ledger)

    result = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(
            lambda request: _good_response(
                request,
                text=statement,
                tokens=False,
            )
        ),
    )

    section = result.sections[0]
    assert section.status == "publishable"
    assert not any("invented numeric content" in error for error in section.errors)


def test_wrapped_value_tokens_require_exact_issued_alias() -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(plan, ledger)

    def with_text(text: str):
        def builder(request):
            response = _good_response(
                request, role="background", kind="none_required", note=""
            )
            response["paragraphs"][0]["text_with_value_tokens"] = text
            return response

        return builder

    cases = {
        "dotted": "[VALUE:V01_0af40736334b.simplicity_score].",
        "near_spelling": "[VALUE:V01_R_MEANN].",
        "whitespace": "[VALUE: V01_R_MEAN].",
        "hyphen": "[VALUE:V01-R-MEAN].",
        "empty": "[VALUE:].",
    }
    for label, text in cases.items():
        bundle = build_article_draft_bundle(
            plan,
            ledger,
            architecture,
            story_id,
            _value_records(),
            section_writer=_writer(with_text(text)),
        )
        section = bundle.sections[0]
        assert section.status == "needs_revision", label
        assert any(
            "unknown value token alias" in item for item in section.errors
        ), label

    legal = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(with_text("Mean is [VALUE:V01_R_MEAN].")),
    )
    assert legal.sections[0].status == "publishable"
    assert "[VALUE:" not in legal.sections[0].rendered_prose
    assert "0.004" in legal.sections[0].rendered_prose

    def dotted_first_section(request):
        if request["section"]["section_id"].endswith("-section-01"):
            return with_text("[VALUE:V01_0af40736334b.simplicity_score].")(request)
        return _good_response(request)

    captured: list[dict] = []
    repaired = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(dotted_first_section),
        format_repair=_sequence_repairer(
            [
                lambda request: _good_response(
                    request,
                    role="background",
                    kind="none_required",
                    note="",
                )
            ],
            captured=captured,
        ),
    )
    assert repaired.sections[0].status == "publishable"
    assert len(captured) == 1
    assert any(
        "V01_0af40736334b.simplicity_score" in item for item in captured[0]["errors"]
    )


def test_source_bound_numeric_literals_allowed_but_invented_results_fail() -> None:
    plan, ledger = _custom_plan_ledger(
        "Design an emitter with mean absorptance at least 85 percent, "
        "worst-case absorptance at most 2 percent, layer thicknesses up to "
        "1500 nm, over 8-13 um."
    )
    architecture, story_id = _architecture(plan, ledger)

    def with_text(text: str):
        def builder(request):
            response = _good_response(
                request, role="background", kind="none_required", note=""
            )
            response["paragraphs"][0]["text_with_value_tokens"] = text
            return response

        return builder

    allowed = [
        "Mean absorptance target is 85 percent.",
        "Mean absorptance target is 85%.",
        "Worst-case absorptance target is 2 percent.",
        "Worst-case absorptance target is 2%.",
        "Layer thickness may reach 1500 nm.",
        "The spectral window spans 8 um to 13 um.",
    ]
    for text in allowed:
        bundle = build_article_draft_bundle(
            plan,
            ledger,
            architecture,
            story_id,
            _value_records(),
            section_writer=_writer(with_text(text)),
        )
        assert bundle.sections[0].status == "publishable", text

    disallowed = [
        "Measured mean absorptance reached 95 percent.",
        "The prototype achieved 40% reflectance.",
        "Observed result value was 0.123.",
    ]
    for text in disallowed:
        bundle = build_article_draft_bundle(
            plan,
            ledger,
            architecture,
            story_id,
            _value_records(),
            section_writer=_writer(with_text(text)),
        )
        section = bundle.sections[0]
        assert section.status == "needs_revision", text
        assert any("invented numeric content" in item for item in section.errors), text


def test_numbers_only_in_unaccepted_capability_fields_are_not_allowed() -> None:
    plan, ledger = _custom_plan_ledger(
        "Design an emitter with mean absorptance at least 85 percent."
    )
    capability = plan.capability.model_copy(
        update={
            "unsupported_requirements": ["the 95 percent band is unsupported"],
            "clarification_questions": ["Is a 95 percent target acceptable?"],
            "recommended_next_action": "clarify the 95 percent target",
        }
    )
    plan_copy = plan.model_copy(update={"capability": capability})
    architecture, story_id = _architecture(plan_copy, ledger)

    def with_text(text: str):
        def builder(request):
            response = _good_response(
                request, role="background", kind="none_required", note=""
            )
            response["paragraphs"][0]["text_with_value_tokens"] = text
            return response

        return builder

    bundle = build_article_draft_bundle(
        plan_copy,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(with_text("The target is 95 percent.")),
    )
    section = bundle.sections[0]
    assert section.status == "needs_revision"
    assert any("invented numeric content" in item for item in section.errors)


def test_comparator_forms_canonicalize_to_source_unit_literals() -> None:
    plan, ledger = _custom_plan_ledger(
        "Design an emitter with mean absorptance at or above 85 percent, "
        "worst-case absorptance at or below 20 percent, layer thicknesses up "
        "to 1500 nm."
    )
    architecture, story_id = _architecture(plan, ledger)

    def with_text(text: str):
        def builder(request):
            response = _good_response(
                request, role="background", kind="none_required", note=""
            )
            response["paragraphs"][0]["text_with_value_tokens"] = text
            return response

        return builder

    allowed = [
        "The target is >=85%.",
        "The target is below 20 percent.",
        "The target is at or above 85 percent.",
        "Thickness may reach 1500 nm.",
    ]
    for text in allowed:
        bundle = build_article_draft_bundle(
            plan,
            ledger,
            architecture,
            story_id,
            _value_records(),
            section_writer=_writer(with_text(text)),
        )
        assert bundle.sections[0].status == "publishable", text

    disallowed = [
        "The measured value is 95 percent.",
        "The result reached 40%.",
        "The measured value is >=60%.",
        "The threshold is above 95.",
        "The threshold is >=60.",
        "The scale reached 1e3.",
    ]
    for text in disallowed:
        bundle = build_article_draft_bundle(
            plan,
            ledger,
            architecture,
            story_id,
            _value_records(),
            section_writer=_writer(with_text(text)),
        )
        section = bundle.sections[0]
        assert section.status == "needs_revision", text
        assert any("invented numeric content" in item for item in section.errors), text


def test_unitless_comparator_source_literals_are_allowed() -> None:
    plan, ledger = _custom_plan_ledger(
        "Design an emitter with a quality score at or above 85 and a cutoff "
        "of at least 60."
    )
    architecture, story_id = _architecture(plan, ledger)

    def with_text(text: str):
        def builder(request):
            response = _good_response(
                request, role="background", kind="none_required", note=""
            )
            response["paragraphs"][0]["text_with_value_tokens"] = text
            return response

        return builder

    allowed = [
        "The score threshold is >=85.",
        "The cutoff is at least 60.",
    ]
    for text in allowed:
        bundle = build_article_draft_bundle(
            plan,
            ledger,
            architecture,
            story_id,
            _value_records(),
            section_writer=_writer(with_text(text)),
        )
        assert bundle.sections[0].status == "publishable", text

    disallowed = [
        "The cutoff is >=95.",
        "The score reached 1e3.",
    ]
    for text in disallowed:
        bundle = build_article_draft_bundle(
            plan,
            ledger,
            architecture,
            story_id,
            _value_records(),
            section_writer=_writer(with_text(text)),
        )
        section = bundle.sections[0]
        assert section.status == "needs_revision", text
        assert any("invented numeric content" in item for item in section.errors), text


def test_value_token_renders_exactly_and_figure_only_value_is_rejected() -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(
        plan,
        ledger,
        story_draft=_story_draft(ledger, figure_fields=["R_mean", "worst_case"]),
    )
    captured: list[dict] = []
    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(_good_response, captured=captured),
    )
    assert "0.004" in bundle.sections[0].rendered_prose
    assert "0.02" not in bundle.sections[0].rendered_prose

    figure_only = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(
            lambda request: {
                **_good_response(request),
                "paragraphs": [
                    {
                        **_good_response(request)["paragraphs"][0],
                        "text_with_value_tokens": (
                            "The worst-case curve is [VALUE:V02_WORST_CASE]."
                        ),
                    }
                ],
            }
        ),
    )
    assert any(
        "figure-only value token" in item for item in figure_only.sections[0].errors
    )


def test_invented_measurements_rejected_while_structural_integers_pass() -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(plan, ledger)

    decimal = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(
            lambda request: _good_response(
                request, text="The measured reflectance was 0.123."
            )
        ),
    )
    assert any(
        "invented numeric content" in item for item in decimal.sections[0].errors
    )

    percent = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(
            lambda request: _good_response(
                request, text="The design reaches 99 percent of the target."
            )
        ),
    )
    assert any(
        "invented numeric content" in item for item in percent.sections[0].errors
    )

    structural = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(
            lambda request: _good_response(
                request,
                text="Figure 2 compares stage 3 routes with a 2D map.",
            )
        ),
    )
    assert structural.sections[0].errors == []
    assert any("structural integer" in item for item in structural.sections[0].warnings)


def test_bounded_inference_and_unsupported_flagging() -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(plan, ledger)

    no_claim = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(
            lambda request: _good_response(
                request, cite=False, kind="bounded_inference"
            )
        ),
    )
    assert any(
        "bounded_inference requires" in item for item in no_claim.sections[0].errors
    )

    no_note = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(
            lambda request: _good_response(request, kind="bounded_inference", note="")
        ),
    )
    assert any(
        "bounded_inference requires" in item for item in no_note.sections[0].errors
    )

    unsupported = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(
            lambda request: _good_response(request, kind="unsupported", note="")
        ),
    )
    assert unsupported.sections[0].errors == []
    assert any(
        "declares unsupported inference" in item
        for item in unsupported.sections[0].warnings
    )

    background = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(
            lambda request: _good_response(
                request,
                role="background",
                kind="none_required",
                cite=False,
                note="",
                tokens=False,
            )
        ),
    )
    assert background.sections[0].status == "publishable"


def test_negative_claim_roles_preserved_in_source_ledger() -> None:
    plan, ledger = _refuted_ledger()
    refuted = [c for c in ledger.claims if c.status == ClaimStatus.refuted][0]
    architecture, story_id = _architecture(
        plan,
        ledger,
        manifest=_failure_manifest(),
        story_draft=_story_draft(
            ledger,
            claim_role="limitation",
            claim_id=refuted.claim_id,
            figure_artifact="FAILURE.json",
            figure_fields=["R_mean"],
        ),
    )
    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        [],
        section_writer=_writer(
            lambda request: _good_response(request, role="limitation")
        ),
    )
    assert bundle.publishable
    section = bundle.sections[0]
    assert section.source_ledger
    assert "limitation" in section.source_ledger[0].roles
    assert "positive" not in section.source_ledger[0].roles


def test_positive_claim_limitation_role_is_organizational_warning() -> None:
    plan, ledger = _ledger()
    draft = _story_draft(ledger, claim_role="limitation")
    architecture, story_id = _architecture(plan, ledger, story_draft=draft)
    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(_good_response),
    )
    assert bundle.sections[0].status == "publishable"
    assert any("organizational framing" in item for item in bundle.warnings)
    section_roles = set()
    for entry in bundle.sections[0].source_ledger:
        section_roles.update(entry.roles)
    assert "limitation" in section_roles


def test_negative_claim_as_positive_support_still_hard_blocks_in_writing() -> None:
    plan, ledger = _ledger()
    draft_claim = [c for c in ledger.claims if c.status == ClaimStatus.draft][0]
    architecture, _ = _architecture(plan, ledger)
    story = architecture.stories[0]
    section = story.section_contracts[0]
    bad_section = section.model_copy(
        update={
            "claim_bindings": [
                ClaimPlacement(
                    claim_id=draft_claim.claim_id,
                    role="positive",
                )
            ]
        }
    )
    bad_story = story.model_copy(update={"section_contracts": [bad_section]})
    errors: list[str] = []
    warnings: list[str] = []
    fact_by_claim = _fact_by_claim(ledger, errors)
    _validate_story_contract(
        bad_story,
        ledger,
        fact_by_claim,
        errors,
        warnings,
    )
    assert any("non-positive claim" in item for item in errors)


def test_mixed_section_outcomes_and_single_repair_round() -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(plan, ledger)

    def bad_response(request):
        return {
            **_good_response(request),
            "paragraphs": [
                {
                    **_good_response(request)["paragraphs"][0],
                    "claim_aliases": ["C99_bogus"],
                }
            ],
        }

    writer = _writer([_good_response, bad_response, RuntimeError("provider down")])
    repair_captured: list[dict] = []
    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=writer,
        format_repair=_repairer(_good_response, captured=repair_captured),
    )
    assert bundle.sections[0].status == "publishable"
    assert bundle.sections[1].status == "publishable"
    assert bundle.sections[1].repair_rounds == 1
    assert bundle.sections[2].status == "blocked"
    assert any("provider unavailable" in item for item in bundle.sections[2].errors)
    assert len(repair_captured) == 1

    writer = _writer([_good_response, bad_response, RuntimeError("provider down")])
    repair_captured = []
    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=writer,
        format_repair=_repairer(bad_response, captured=repair_captured),
    )
    assert bundle.sections[1].status == "needs_revision"
    assert any(
        "repair round made no progress" in item for item in bundle.sections[1].warnings
    )
    assert len(repair_captured) == 1


def test_assigned_but_unused_claim_warns_or_defers() -> None:
    plan, ledger = _ledger()
    positive = [
        c for c in ledger.claims if c.status == ClaimStatus.partially_supported
    ][0]
    draft_claim = [c for c in ledger.claims if c.status == ClaimStatus.draft][0]
    story_draft = _story_draft(ledger)
    story_draft["sections"][0]["claim_bindings"] = [
        {"claim_id": positive.claim_id, "role": "positive"},
        {"claim_id": draft_claim.claim_id, "role": "limitation"},
    ]
    architecture, story_id = _architecture(plan, ledger, story_draft=story_draft)

    def cite_only_first(request):
        response = _good_response(request)
        if len(request["section"]["claim_bindings"]) == 2:
            first_alias = request["section"]["claim_bindings"][0]["claim_alias"]
            second_alias = request["section"]["claim_bindings"][1]["claim_alias"]
            response["paragraphs"][0]["claim_aliases"] = [first_alias]
            response["deferred_claim_aliases"] = [second_alias]
        return response

    deferred = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(cite_only_first),
    )
    assert deferred.publishable
    assert draft_claim.claim_id in deferred.deferred_claims
    assert not any("neither cited nor deferred" in item for item in deferred.warnings)

    def cite_only_first_no_defer(request):
        response = _good_response(request)
        if len(request["section"]["claim_bindings"]) == 2:
            first_alias = request["section"]["claim_bindings"][0]["claim_alias"]
            response["paragraphs"][0]["claim_aliases"] = [first_alias]
        return response

    warned = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(cite_only_first_no_defer),
    )
    assert warned.publishable
    assert any("neither cited nor deferred" in item for item in warned.warnings)


def test_provider_labels_and_usage_aggregation_are_truthful() -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(plan, ledger)
    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(_good_response, model="fake-writer"),
    )
    assert bundle.semantic_model == "fake-writer"
    assert bundle.usage["estimated_input_tokens"] == 30
    assert bundle.usage["estimated_output_tokens"] == 60
    assert "estimated_cost_cny" not in bundle.usage
    assert "qwen3.7-flash" not in bundle.semantic_model
    assert bundle.attempts == 3


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


def test_qwen_section_writer_adapter_parses_and_preserves_usage() -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(plan, ledger)
    content = json.dumps(
        {
            "paragraphs": [
                {
                    "text_with_value_tokens": (
                        "This section provides background context."
                    ),
                    "claim_aliases": [],
                    "figure_aliases": [],
                    "paragraph_role": "background",
                    "inference_kind": "none_required",
                    "inference_note": "",
                }
            ],
            "deferred_claim_aliases": [],
            "author_notes": [],
        }
    )
    client = FakeQwenClient(content)
    writer = QwenSectionWriter(client=client)
    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=writer,
    )
    assert bundle.semantic_model == "qwen3.7-flash"
    assert bundle.usage["estimated_input_tokens"] == 36
    assert bundle.usage["estimated_output_tokens"] == 102
    assert bundle.usage["estimated_cost_cny"] > 0
    assert bundle.attempts == 3
    assert client.kwargs["max_tokens"] == 6000
    assert '"paragraphs"' in client.messages[0]["content"]

    reduced = QwenSectionWriter(client=client, max_tokens=2000)
    build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=reduced,
    )
    assert client.kwargs["max_tokens"] == 2000


def test_persistence_is_idempotent_and_resumes_after_failure(tmp_path) -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(plan, ledger)
    memory = ArticleMemoryStore(tmp_path / "memory.sqlite")
    graph = ExperimentGraph(tmp_path / "graph.sqlite", "run-1")
    journal = tmp_path / "journal.json"
    original_create = graph.create_article_node

    def failing_create(*args, **kwargs):
        raise RuntimeError("graph write failed")

    graph.create_article_node = failing_create  # type: ignore[method-assign]
    with pytest.raises(Exception, match="section writing persistence failed"):
        build_article_draft_bundle(
            plan,
            ledger,
            architecture,
            story_id,
            _value_records(),
            section_writer=_writer(_good_response),
            memory_store=memory,
            graph=graph,
            run_id="run-1",
            journal_path=journal,
        )
    graph.create_article_node = original_create  # type: ignore[method-assign]
    bundle_id = next(key for key in json.loads(journal.read_text(encoding="utf-8")))
    result = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(_good_response),
        memory_store=memory,
        graph=graph,
        run_id="run-1",
        journal_path=journal,
    )
    assert result.bundle_id == bundle_id
    node = graph.article_node(f"bundle-{bundle_id}")
    assert (
        len([e for e in node["history"] if e["event_type"] == "article.section"]) == 3
    )
    memory_count = len(memory.run_memory_records())
    history_len = len(node["history"])

    retry = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(_good_response),
        memory_store=memory,
        graph=graph,
        run_id="run-1",
        journal_path=journal,
    )
    assert retry.bundle_id == result.bundle_id
    assert len(memory.run_memory_records()) == memory_count
    assert len(graph.article_node(f"bundle-{bundle_id}")["history"]) == history_len
    records = {item.memory_id: item for item in memory.run_memory_records()}
    assert "FINAL_RESULT.json" in records[f"bundle-{bundle_id}"].artifact_ids
    assert "FINAL_RESULT.json" in records[f"source-ledger-{bundle_id}"].artifact_ids
    assert (
        "FINAL_RESULT.json"
        in records[f"section-{bundle_id}-story-01-section-01"].artifact_ids
    )

    changed = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(
            lambda request: _good_response(
                request, text="A materially different section draft."
            )
        ),
    )
    assert changed.bundle_id != result.bundle_id


def test_section_local_alias_authorization() -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(
        plan, ledger, story_draft=_split_claims_story_draft(ledger)
    )
    captured: list[dict] = []

    def cross_section_claim(request):
        captured.append(dict(request))
        if request["section"]["section_id"].endswith("-section-02"):
            other_alias = captured[0]["section"]["claim_bindings"][0]["claim_alias"]
            response = _good_response(request, role="limitation")
            response["paragraphs"][0]["claim_aliases"] = [other_alias]
            return response
        return _good_response(request)

    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(cross_section_claim),
    )
    assert any(
        "not assigned to this section" in item for item in bundle.sections[1].errors
    )

    def cross_section_figure(request):
        if request["section"]["section_id"].endswith("-section-02"):
            response = _good_response(request, role="limitation")
            response["paragraphs"][0]["figure_aliases"] = ["FIG01_spectrum"]
            return response
        return _good_response(request)

    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(cross_section_figure),
    )
    assert any(
        "not assigned to this section" in item for item in bundle.sections[1].errors
    )

    def cross_section_value(request):
        if request["section"]["section_id"].endswith("-section-02"):
            response = _good_response(request, role="limitation")
            response["paragraphs"][0][
                "text_with_value_tokens"
            ] = "Value [VALUE:V01_R_MEAN]."
            return response
        return _good_response(request)

    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(cross_section_value),
    )
    assert any(
        "not authorized by any figure binding or claim-value lineage" in item
        for item in bundle.sections[1].errors
    )


def test_value_token_requires_authorizing_claim() -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(plan, ledger)

    def naked_value(request):
        response = _good_response(
            request,
            role="background",
            kind="none_required",
            cite=False,
            note="",
        )
        response["paragraphs"][0][
            "text_with_value_tokens"
        ] = "Background [VALUE:V01_R_MEAN]."
        return response

    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(naked_value),
    )
    assert any(
        "not authorized by any cited claim" in item
        for item in bundle.sections[0].errors
    )

    positive = [
        c for c in ledger.claims if c.status == ClaimStatus.partially_supported
    ][0]
    draft_claim = [c for c in ledger.claims if c.status == ClaimStatus.draft][0]
    story_draft = _story_draft(ledger)
    story_draft["sections"][0]["claim_bindings"] = [
        {"claim_id": positive.claim_id, "role": "positive"},
        {"claim_id": draft_claim.claim_id, "role": "limitation"},
    ]
    architecture2, story_id2 = _architecture(plan, ledger, story_draft=story_draft)

    def unrelated_claim(request):
        response = _good_response(request)
        draft_alias = request["section"]["claim_bindings"][1]["claim_alias"]
        response["paragraphs"][0]["claim_aliases"] = [draft_alias]
        response["paragraphs"][0][
            "text_with_value_tokens"
        ] = "The draft claim has a value [VALUE:V01_R_MEAN]."
        return response

    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture2,
        story_id2,
        _value_records(),
        section_writer=_writer(unrelated_claim),
    )
    assert any(
        "not authorized by any cited claim" in item
        for item in bundle.sections[0].errors
    )


def test_stage9_provenance_identity_required_and_architecture_id_recomputed() -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(plan, ledger)
    captured: list[dict] = []

    legacy = architecture.model_copy(
        update={"source_plan_id": None, "source_ledger_id": None}
    )
    result = build_article_draft_bundle(
        plan,
        ledger,
        legacy,
        story_id,
        _value_records(),
        section_writer=_writer(_good_response, captured=captured),
    )
    assert any("lacks Stage 9 source_plan_id" in item for item in result.errors)
    assert any("lacks Stage 9 source_ledger_id" in item for item in result.errors)
    assert captured == []

    story = architecture.stories[0]
    tampered = story.model_copy(update={"central_thesis": "A changed thesis."})
    tampered_arch = architecture.model_copy(update={"stories": [tampered]})
    result = build_article_draft_bundle(
        plan,
        ledger,
        tampered_arch,
        story_id,
        _value_records(),
        section_writer=_writer(_good_response, captured=captured),
    )
    assert any("does not match recomputed identity" in item for item in result.errors)
    assert captured == []


def test_value_integrity_against_artifact_inventory() -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(plan, ledger)

    wrong_hash = _value_records()
    wrong_hash[0] = wrong_hash[0].model_copy(update={"source_hash": "b" * 64})
    result = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        wrong_hash,
        section_writer=None,
    )
    assert any("source_hash does not match" in item for item in result.errors)

    no_hash_manifest = ArtifactDescriptor(
        artifact_id="FINAL_RESULT.json",
        path="x.json",
        fields=["R_mean", "worst_case"],
        content_summary="no hash",
    )
    arch_no_hash, sid_no_hash = _architecture(plan, ledger, manifest=[no_hash_manifest])
    result = build_article_draft_bundle(
        plan,
        ledger,
        arch_no_hash,
        sid_no_hash,
        _value_records(),
        section_writer=None,
    )
    assert any("has no sha256" in item for item in result.errors)

    injected = _value_records()
    injected[0] = injected[0].model_copy(
        update={"rendered_value": "<script>alert(1)</script>"}
    )
    result = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        injected,
        section_writer=None,
    )
    assert any("finite scalar numeric literal" in item for item in result.errors)

    nan = _value_records()
    nan[0] = nan[0].model_copy(update={"rendered_value": "NaN"})
    result = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        nan,
        section_writer=None,
    )
    assert any("finite scalar numeric literal" in item for item in result.errors)

    manifest2 = _manifest() + [
        ArtifactDescriptor(
            artifact_id="OTHER.json",
            path="other.json",
            fields=["R_mean"],
            sha256="b" * 64,
            content_summary="other artifact with the same field",
        )
    ]
    arch2, sid2 = _architecture(plan, ledger, manifest=manifest2)
    wrong_artifact = _value_records() + [
        TrustedValueRecord(
            artifact_id="OTHER.json",
            field="R_mean",
            rendered_value="0.5",
            source_hash="b" * 64,
            prose_safe=True,
        )
    ]
    result = build_article_draft_bundle(
        plan,
        ledger,
        arch2,
        sid2,
        wrong_artifact,
        section_writer=None,
    )
    assert any(
        "does not correspond to any Stage 9 artifact-field binding" in item
        for item in result.errors
    )


def test_story_contract_revalidation() -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(plan, ledger)
    story = architecture.stories[0]
    captured: list[dict] = []

    s1 = story.section_contracts[0]
    dup_sections = story.model_copy(update={"section_contracts": [s1, s1]})
    result = build_article_draft_bundle(
        plan,
        ledger,
        _with_identity(architecture, plan, ledger, dup_sections),
        story_id,
        _value_records(),
        section_writer=_writer(_good_response, captured=captured),
    )
    assert any("duplicate section IDs" in item for item in result.errors)
    assert captured == []

    positive = [
        c for c in ledger.claims if c.status == ClaimStatus.partially_supported
    ][0]
    binding = s1.claim_bindings[0]
    conflicting = s1.model_copy(
        update={
            "claim_bindings": [
                binding,
                binding.model_copy(update={"role": "limitation"}),
            ]
        }
    )
    conflicting_story = story.model_copy(
        update={"section_contracts": [conflicting, *story.section_contracts[1:]]}
    )
    result = build_article_draft_bundle(
        plan,
        ledger,
        _with_identity(architecture, plan, ledger, conflicting_story),
        story_id,
        _value_records(),
        section_writer=_writer(_good_response, captured=captured),
    )
    assert any("conflicting roles" in item for item in result.errors)
    assert captured == []

    figure = story.figure_contracts[0]
    mismatched_figure = figure.model_copy(update={"claim_ids": []})
    mismatched_story = story.model_copy(
        update={"figure_contracts": [mismatched_figure]}
    )
    result = build_article_draft_bundle(
        plan,
        ledger,
        _with_identity(architecture, plan, ledger, mismatched_story),
        story_id,
        _value_records(),
        section_writer=_writer(_good_response, captured=captured),
    )
    assert any("claim_ids do not match" in item for item in result.errors)
    assert captured == []

    orphan_sections = [
        section.model_copy(update={"figure_ids": []})
        for section in story.section_contracts
    ]
    orphan_figure = figure.model_copy(update={"section_target": ""})
    orphan_story = story.model_copy(
        update={
            "section_contracts": orphan_sections,
            "figure_contracts": [orphan_figure],
        }
    )
    result = build_article_draft_bundle(
        plan,
        ledger,
        _with_identity(architecture, plan, ledger, orphan_story),
        story_id,
        _value_records(),
        section_writer=_writer(_good_response, captured=captured),
    )
    assert any("not assigned to any section" in item for item in result.errors)
    assert captured == []

    wrong_target = figure.model_copy(update={"section_target": "story-01-section-99"})
    wrong_target_story = story.model_copy(update={"figure_contracts": [wrong_target]})
    result = build_article_draft_bundle(
        plan,
        ledger,
        _with_identity(architecture, plan, ledger, wrong_target_story),
        story_id,
        _value_records(),
        section_writer=_writer(_good_response, captured=captured),
    )
    assert any("section_target" in item for item in result.errors)
    assert captured == []


def test_paragraph_source_ledger_includes_claim_fact_artifacts_and_scopes() -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(plan, ledger)
    positive = [
        c for c in ledger.claims if c.status == ClaimStatus.partially_supported
    ][0]

    def claim_only(request):
        response = _good_response(request)
        response["paragraphs"][0]["figure_aliases"] = []
        response["paragraphs"][0]["text_with_value_tokens"] = (
            "The claim is supported by the verified spectrum without quoting "
            "numbers."
        )
        return response

    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(claim_only),
    )
    entry = bundle.sections[0].source_ledger[0]
    assert entry.claim_ids == [positive.claim_id]
    assert entry.fact_ids == [positive.metadata["fact_id"]]
    assert entry.artifact_ids == ["FINAL_RESULT.json"]
    assert entry.value_token_ids == []
    assert entry.figure_ids == []
    assert entry.scope == plan.charter.scope
    assert entry.scopes == [positive.scope]
    assert entry.roles == ["positive"]


def test_repair_progress_requires_strict_improvement() -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(plan, ledger)

    def two_errors(request):
        response = _good_response(
            request, role="background", kind="none_required", note=""
        )
        response["paragraphs"][0]["claim_aliases"] = ["C99_bogus"]
        response["paragraphs"][0][
            "text_with_value_tokens"
        ] = "Value 0.123 outside token."
        return response

    def different_two_errors(request):
        response = _good_response(
            request, role="background", kind="none_required", note=""
        )
        response["paragraphs"][0]["figure_aliases"] = ["FIG99_bogus"]
        response["paragraphs"][0]["text_with_value_tokens"] = "Uses [VALUE:V99_BOGUS]."
        return response

    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer([two_errors, _good_response, _good_response]),
        format_repair=_repairer(different_two_errors),
    )
    section = bundle.sections[0]
    assert section.status == "needs_revision"
    assert any("C99_bogus" in item for item in section.errors)
    assert any("repair round made no progress" in item for item in section.warnings)
    assert section.attempts == 2

    def one_error(request):
        response = _good_response(
            request, role="background", kind="none_required", note=""
        )
        response["paragraphs"][0]["claim_aliases"] = ["C99_bogus"]
        response["paragraphs"][0]["text_with_value_tokens"] = "Clean text."
        return response

    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer([two_errors, _good_response, _good_response]),
        format_repair=_repairer(one_error),
    )
    section = bundle.sections[0]
    assert section.repair_rounds == 1
    assert len(section.errors) == 1
    assert any("C99_bogus" in item for item in section.errors)


def test_publishable_first_response_triggers_zero_repair() -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(plan, ledger)
    captured: list[dict] = []
    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(_good_response),
        format_repair=_sequence_repairer([], captured=captured),
    )
    assert all(item.repair_rounds == 0 for item in bundle.sections)
    assert all(item.attempts == 1 for item in bundle.sections)
    assert captured == []


def test_second_repair_round_publishable_after_strict_progress() -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(plan, ledger)

    def three_errors(request):
        response = _good_response(
            request, role="background", kind="none_required", note=""
        )
        response["paragraphs"][0]["claim_aliases"] = ["C99_bogus"]
        response["paragraphs"][0]["figure_aliases"] = ["FIG99_bogus"]
        response["paragraphs"][0][
            "text_with_value_tokens"
        ] = "Value 0.123 outside token."
        return response

    def two_errors(request):
        response = _good_response(
            request, role="background", kind="none_required", note=""
        )
        response["paragraphs"][0]["claim_aliases"] = ["C99_bogus"]
        response["paragraphs"][0][
            "text_with_value_tokens"
        ] = "Value 0.123 outside token."
        return response

    captured: list[dict] = []
    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer([three_errors, _good_response, _good_response]),
        format_repair=_sequence_repairer(
            [two_errors, _good_response], captured=captured
        ),
    )
    section = bundle.sections[0]
    assert section.status == "publishable"
    assert section.repair_rounds == 2
    assert section.attempts == 3
    assert len(captured) == 2
    assert len(captured[0]["errors"]) == 3
    assert len(captured[1]["errors"]) == 2
    assert any("C99_bogus" in item for item in captured[0]["errors"])
    assert "failed_response" not in captured[0]
    assert "failed_response" not in captured[1]
    section_id = captured[0]["section"]["section_id"]
    assert captured[1]["targeted_paragraphs"] == [
        {
            **two_errors(captured[0])["paragraphs"][0],
            "paragraph_id": f"{section_id}-p01",
        }
    ]
    assert section.usage["estimated_input_tokens"] == 16
    assert section.usage["estimated_output_tokens"] == 28


def test_no_progress_repair_round_stops_and_retains_original() -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(plan, ledger)

    def two_errors(request):
        response = _good_response(
            request, role="background", kind="none_required", note=""
        )
        response["paragraphs"][0]["claim_aliases"] = ["C99_bogus"]
        response["paragraphs"][0][
            "text_with_value_tokens"
        ] = "Value 0.123 outside token."
        return response

    def different_two_errors(request):
        response = _good_response(
            request, role="background", kind="none_required", note=""
        )
        response["paragraphs"][0]["figure_aliases"] = ["FIG99_bogus"]
        response["paragraphs"][0]["text_with_value_tokens"] = "Uses [VALUE:V99_BOGUS]."
        return response

    captured: list[dict] = []
    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer([two_errors, _good_response, _good_response]),
        format_repair=_sequence_repairer([different_two_errors], captured=captured),
    )
    section = bundle.sections[0]
    assert section.status == "needs_revision"
    assert section.repair_rounds == 0
    assert section.attempts == 2
    assert len(captured) == 1
    assert any("repair round made no progress" in item for item in section.warnings)
    assert any("C99_bogus" in item for item in section.errors)
    assert section.usage["estimated_input_tokens"] == 13
    assert section.usage["estimated_output_tokens"] == 24


def test_round1_progress_round2_no_progress_retains_improved_draft() -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(plan, ledger)

    def three_errors(request):
        response = _good_response(
            request, role="background", kind="none_required", note=""
        )
        response["paragraphs"][0]["claim_aliases"] = ["C99_bogus"]
        response["paragraphs"][0]["figure_aliases"] = ["FIG99_bogus"]
        response["paragraphs"][0][
            "text_with_value_tokens"
        ] = "Value 0.123 outside token."
        return response

    def one_error(request):
        response = _good_response(
            request, role="background", kind="none_required", note=""
        )
        response["paragraphs"][0]["claim_aliases"] = ["C99_bogus"]
        response["paragraphs"][0]["text_with_value_tokens"] = "Clean text."
        return response

    def different_one_error(request):
        response = _good_response(
            request, role="background", kind="none_required", note=""
        )
        response["paragraphs"][0]["figure_aliases"] = ["FIG99_bogus"]
        response["paragraphs"][0]["text_with_value_tokens"] = "Clean text."
        return response

    captured: list[dict] = []
    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer([three_errors, _good_response, _good_response]),
        format_repair=_sequence_repairer(
            [one_error, different_one_error], captured=captured
        ),
    )
    section = bundle.sections[0]
    assert section.status == "needs_revision"
    assert section.repair_rounds == 1
    assert section.attempts == 3
    assert len(captured) == 2
    assert len(captured[1]["errors"]) == 1
    assert "failed_response" not in captured[1]
    section_id = captured[0]["section"]["section_id"]
    assert captured[1]["targeted_paragraphs"] == [
        {
            **one_error(captured[0])["paragraphs"][0],
            "paragraph_id": f"{section_id}-p01",
        }
    ]
    assert any("C99_bogus" in item for item in section.errors)
    assert not any("FIG99_bogus" in item for item in section.errors)
    assert any("repair round made no progress" in item for item in section.warnings)
    assert section.usage["estimated_input_tokens"] == 16
    assert section.usage["estimated_output_tokens"] == 28


def test_repair_outage_after_progress_retains_last_safe_draft() -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(plan, ledger)

    def three_errors(request):
        response = _good_response(
            request, role="background", kind="none_required", note=""
        )
        response["paragraphs"][0]["claim_aliases"] = ["C99_bogus"]
        response["paragraphs"][0]["figure_aliases"] = ["FIG99_bogus"]
        response["paragraphs"][0][
            "text_with_value_tokens"
        ] = "Value 0.123 outside token."
        return response

    def one_error(request):
        response = _good_response(
            request, role="background", kind="none_required", note=""
        )
        response["paragraphs"][0]["claim_aliases"] = ["C99_bogus"]
        response["paragraphs"][0]["text_with_value_tokens"] = "Clean text."
        return response

    captured: list[dict] = []
    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer([three_errors, _good_response, _good_response]),
        format_repair=_sequence_repairer(
            [one_error, RuntimeError("repair down")], captured=captured
        ),
    )
    section = bundle.sections[0]
    assert section.status == "needs_revision"
    assert section.repair_rounds == 1
    assert section.attempts == 3
    assert len(captured) == 2
    assert any("format repair unavailable" in item for item in section.warnings)
    assert any("C99_bogus" in item for item in section.errors)
    assert section.usage["estimated_input_tokens"] == 13
    assert section.usage["estimated_output_tokens"] == 24


def _two_paragraph_bad(request):
    response = _good_response(request, role="background", kind="none_required", note="")
    response["paragraphs"] = [
        response["paragraphs"][0],
        {
            "text_with_value_tokens": "Bad value [VALUE:V99_BOGUS].",
            "claim_aliases": ["C99_bogus"],
            "figure_aliases": [],
            "paragraph_role": "result",
            "inference_kind": "bounded_inference",
            "inference_note": "bad",
        },
    ]
    return response


def _two_paragraph_bad_first(request):
    if request["section"]["section_id"].endswith("-section-01"):
        return _two_paragraph_bad(request)
    return _good_response(request, role="background", kind="none_required", note="")


def _targeted_fix(request):
    target = request["targeted_paragraphs"][0]
    return {
        "targeted_paragraphs": [
            {
                **{
                    key: value for key, value in target.items() if key != "paragraph_id"
                },
                "paragraph_id": target["paragraph_id"],
                "text_with_value_tokens": "Fixed result paragraph.",
                "claim_aliases": [
                    request["section"]["claim_bindings"][0]["claim_alias"]
                ],
                "figure_aliases": [],
                "paragraph_role": "result",
                "inference_kind": "bounded_inference",
                "inference_note": "fixed",
            }
        ]
    }


def test_targeted_paragraph_repair_merges_and_preserves_untouched_rows() -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(plan, ledger)
    captured: list[dict] = []
    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(_two_paragraph_bad_first),
        format_repair=_sequence_repairer([_targeted_fix], captured=captured),
    )
    section = bundle.sections[0]
    assert section.status == "publishable"
    assert section.attempts == 2
    assert len(captured) == 1
    request = captured[0]
    assert request["repair_mode"] == "targeted_paragraphs"
    assert "failed_response" not in request
    assert "targeted paragraph" in request["task"]
    assert "paragraph_id" in request["response_contract"]["targeted_paragraphs"][0]
    assert "paragraphs" not in request["response_contract"]
    expected_p02 = f"{section.section_id}-p02"
    assert request["targeted_paragraph_ids"] == [expected_p02]
    assert len(request["targeted_paragraphs"]) == 1
    assert request["targeted_paragraphs"][0]["paragraph_id"] == expected_p02
    assert section.paragraphs[0].text_with_value_tokens == (
        _two_paragraph_bad(request)["paragraphs"][0]["text_with_value_tokens"]
    )
    assert section.paragraphs[1].text_with_value_tokens == ("Fixed result paragraph.")
    assert section.usage["estimated_input_tokens"] == 13


def test_targeted_repair_rejects_unknown_duplicate_missing_ids() -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(plan, ledger)

    def unknown_target(request):
        target = request["targeted_paragraphs"][0]
        row = {key: value for key, value in target.items() if key != "paragraph_id"}
        return {"targeted_paragraphs": [{**row, "paragraph_id": "ghost-p99"}]}

    def duplicate_target(request):
        target = request["targeted_paragraphs"][0]
        row = {key: value for key, value in target.items() if key != "paragraph_id"}
        return {
            "targeted_paragraphs": [
                {**row, "paragraph_id": target["paragraph_id"]},
                {**row, "paragraph_id": target["paragraph_id"]},
            ]
        }

    def missing_target(request):
        target = request["targeted_paragraphs"][0]
        row = {key: value for key, value in target.items() if key != "paragraph_id"}
        return {
            "targeted_paragraphs": [
                {
                    **row,
                    "paragraph_id": f"{request['section']['section_id']}-p01",
                }
            ]
        }

    def extra_target(request):
        target = request["targeted_paragraphs"][0]
        row = {key: value for key, value in target.items() if key != "paragraph_id"}
        return {
            "targeted_paragraphs": [
                {**row, "paragraph_id": target["paragraph_id"]},
                {
                    **row,
                    "paragraph_id": f"{request['section']['section_id']}-p01",
                    "text_with_value_tokens": "Hijacked paragraph.",
                },
            ]
        }

    for bad in (
        unknown_target,
        duplicate_target,
        missing_target,
        extra_target,
    ):
        captured: list[dict] = []
        bundle = build_article_draft_bundle(
            plan,
            ledger,
            architecture,
            story_id,
            _value_records(),
            section_writer=_writer(_two_paragraph_bad_first),
            format_repair=_sequence_repairer([bad], captured=captured),
        )
        section = bundle.sections[0]
        assert section.status == "needs_revision"
        assert section.attempts == 2
        assert len(captured) == 1
        assert any(
            "targeted repair response invalid" in item for item in section.warnings
        )
        assert any("V99_BOGUS" in item for item in section.errors)
        if bad is extra_target:
            assert "Hijacked paragraph." not in (
                section.paragraphs[0].text_with_value_tokens
            )
            assert "The verified evidence supports" in (
                section.paragraphs[0].text_with_value_tokens
            )


def test_legacy_full_response_repair_still_supported_in_targeted_mode() -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(plan, ledger)
    captured: list[dict] = []
    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(_two_paragraph_bad_first),
        format_repair=_sequence_repairer(
            [
                lambda request: _good_response(
                    request,
                    role="background",
                    kind="none_required",
                    note="",
                )
            ],
            captured=captured,
        ),
    )
    section = bundle.sections[0]
    assert section.status == "publishable"
    assert len(captured) == 1
    assert captured[0]["repair_mode"] == "targeted_paragraphs"


def test_malformed_whole_response_uses_full_response_repair() -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(plan, ledger)
    captured: list[dict] = []

    def malformed_writer(request):
        if request["section"]["section_id"].endswith("-section-01"):
            return {
                "paragraphs": [],
                "deferred_claim_aliases": [],
                "author_notes": [],
            }
        return _good_response(request, role="background", kind="none_required", note="")

    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(malformed_writer),
        format_repair=_sequence_repairer(
            [
                lambda request: _good_response(
                    request,
                    role="background",
                    kind="none_required",
                    note="",
                )
            ],
            captured=captured,
        ),
    )
    section = bundle.sections[0]
    assert section.status == "publishable"
    assert len(captured) == 1
    assert captured[0]["repair_mode"] == "full_response"
    assert "failed_response" in captured[0]
    assert "paragraphs" in captured[0]["response_contract"]


def test_word_count_guidance_updated_and_soft() -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(plan, ledger)
    captured: list[dict] = []
    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(_good_response, captured=captured),
    )
    ranges = {
        item["section"]["section_id"]: item["section"]["target_word_range"]
        for item in captured
    }
    assert ranges["story-01-section-01"] == [800, 1500]
    assert ranges["story-01-section-02"] == [800, 1500]
    assert ranges["story-01-section-03"] == [300, 700]
    assert bundle.publishable
    assert any("unusually short" in item for item in bundle.warnings)


def test_value_authorization_is_exact_per_claim_fact_artifact() -> None:
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
    claim_a = next(
        c for c in ledger.claims if c.metadata.get("hypothesis_id") == "hyp-01"
    )
    claim_b = next(
        c for c in ledger.claims if c.metadata.get("hypothesis_id") == "hyp-02"
    )
    fact_a = next(
        f for f in ledger.facts if f.metadata.get("hypothesis_id") == "hyp-01"
    )
    fact_b = next(
        f for f in ledger.facts if f.metadata.get("hypothesis_id") == "hyp-02"
    )
    manifest = [
        ArtifactDescriptor(
            artifact_id="A.json",
            path="a.json",
            fields=["R_mean"],
            sha256="a" * 64,
            content_summary="artifact A",
        ),
        ArtifactDescriptor(
            artifact_id="B.json",
            path="b.json",
            fields=["worst_case"],
            sha256="b" * 64,
            content_summary="artifact B",
        ),
    ]
    story_draft = {
        "story_shape": "shape-two-claims",
        "central_thesis": "Two claims from two artifacts in one figure.",
        "sections": [
            {
                "heading": "Results two-claims",
                "purpose": "present the verified result evidence",
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
                    {"artifact_id": "A.json", "selected_fields": ["R_mean"]},
                    {"artifact_id": "B.json", "selected_fields": ["worst_case"]},
                ],
                "limitations": ["solver only"],
            }
        ],
        "omitted_claims": [],
        "exclusions": ["excluded-two-claims"],
        "strengths": ["strength-two-claims"],
        "risks": ["risk-two-claims"],
        "recommendation_rationale": "rationale-two-claims",
        "recommendation_score": 0.6,
    }
    architecture, story_id = _architecture(
        plan, ledger, manifest=manifest, story_draft=story_draft
    )
    records = [
        TrustedValueRecord(
            artifact_id="A.json",
            field="R_mean",
            rendered_value="0.004",
            source_hash="a" * 64,
            label="mean reflectance",
            prose_safe=True,
        ),
        TrustedValueRecord(
            artifact_id="B.json",
            field="worst_case",
            rendered_value="0.02",
            source_hash="b" * 64,
            label="worst-case reflectance",
            prose_safe=True,
        ),
    ]

    def alias_for(request, hypothesis_id):
        statement = next(
            c.statement
            for c in ledger.claims
            if c.metadata.get("hypothesis_id") == hypothesis_id
        )
        return next(
            item["claim_alias"]
            for item in request["claims"]
            if item["statement"] == statement
        )

    def cite_a_with_a_value(request):
        response = _good_response(request)
        response["paragraphs"][0]["claim_aliases"] = [alias_for(request, "hyp-01")]
        response["paragraphs"][0][
            "text_with_value_tokens"
        ] = "Artifact A value [VALUE:V01_R_MEAN]."
        return response

    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        records,
        section_writer=_writer(cite_a_with_a_value),
    )
    assert bundle.sections[0].errors == []
    assert bundle.sections[0].status == "publishable"

    def cite_b_with_a_value(request):
        response = _good_response(request)
        response["paragraphs"][0]["claim_aliases"] = [alias_for(request, "hyp-02")]
        response["paragraphs"][0][
            "text_with_value_tokens"
        ] = "Artifact A value [VALUE:V01_R_MEAN]."
        return response

    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        records,
        section_writer=_writer(cite_b_with_a_value),
    )
    assert any(
        "not authorized by any cited claim" in item
        for item in bundle.sections[0].errors
    )

    def cite_a_with_b_value(request):
        response = _good_response(request)
        response["paragraphs"][0]["claim_aliases"] = [alias_for(request, "hyp-01")]
        response["paragraphs"][0][
            "text_with_value_tokens"
        ] = "Artifact B value [VALUE:V02_WORST_CASE]."
        return response

    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        records,
        section_writer=_writer(cite_a_with_b_value),
    )
    assert any(
        "not authorized by any cited claim" in item
        for item in bundle.sections[0].errors
    )

    def cite_b_with_b_value(request):
        response = _good_response(request)
        response["paragraphs"][0]["claim_aliases"] = [alias_for(request, "hyp-02")]
        response["paragraphs"][0][
            "text_with_value_tokens"
        ] = "Artifact B value [VALUE:V02_WORST_CASE]."
        return response

    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        records,
        section_writer=_writer(cite_b_with_b_value),
    )
    assert bundle.sections[0].errors == []
    assert bundle.sections[0].status == "publishable"


def _lineage_ledger():
    """Ledger whose writable claim carries TV43-style value lineage."""

    plan, ledger = _ledger()
    positive = [
        claim
        for claim in ledger.claims
        if claim.status == ClaimStatus.partially_supported
    ][0]
    enriched = positive.model_copy(
        update={
            "metadata": {
                **positive.metadata,
                "value_lineage": [
                    {
                        "artifact_id": "FINAL_RESULT.json",
                        "field": "R_mean",
                        "label": "mean reflectance",
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
                enriched if claim.claim_id == positive.claim_id else claim
                for claim in ledger.claims
            ]
        }
    )
    return plan, ledger


def _claim_section_story_draft(ledger, claim_id, *, extra_bindings=()):
    fact = next(
        fact for fact in ledger.facts if fact.metadata.get("claim_id") == claim_id
    )
    return {
        "story_shape": "shape-claim-section",
        "central_thesis": "An evidence-bound story with a figure-free section.",
        "sections": [
            {
                "heading": "Results figure",
                "purpose": "present the verified result evidence",
                "key_messages": ["key"],
                "transitions": ["next"],
                "claim_bindings": [
                    {"claim_id": claim_id, "role": "positive"},
                ],
                "figure_roles": ["spectrum"],
            },
            {
                "heading": "Limitations figure-free",
                "purpose": "state the limitations",
                "key_messages": ["key"],
                "transitions": ["next"],
                "claim_bindings": [
                    {"claim_id": claim_id, "role": "limitation"},
                    *extra_bindings,
                ],
                "figure_roles": [],
            },
        ],
        "figures": [
            {
                "role_key": "spectrum",
                "kind": "quantitative",
                "story_role": "spectral response",
                "panel_intents": ["panel"],
                "caption_intent": "verified spectrum",
                "claim_bindings": [{"claim_id": claim_id, "role": "positive"}],
                "fact_ids": [fact.fact_id],
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
        "exclusions": [],
        "strengths": [],
        "risks": [],
        "recommendation_rationale": "rationale",
        "recommendation_score": 0.6,
    }


def test_claim_authorized_value_token_without_figure() -> None:
    """Probe 031: a paragraph citing the claim may use its value token."""

    plan, ledger = _lineage_ledger()
    positive = [
        claim
        for claim in ledger.claims
        if claim.status == ClaimStatus.partially_supported
    ][0]
    architecture, story_id = _architecture(
        plan,
        ledger,
        story_draft=_claim_section_story_draft(ledger, positive.claim_id),
    )

    def cite_claim_with_value(request):
        if request["section"]["section_id"].endswith("-section-01"):
            return _good_response(request)
        claim_alias = request["section"]["claim_bindings"][0]["claim_alias"]
        return {
            "paragraphs": [
                {
                    "text_with_value_tokens": (
                        "The measured mean reflectance reached " "[VALUE:V01_R_MEAN]."
                    ),
                    "claim_aliases": [claim_alias],
                    "figure_aliases": [],
                    "paragraph_role": "limitation",
                    "inference_kind": "bounded_inference",
                    "inference_note": "bound to the cited claim",
                }
            ],
            "deferred_claim_aliases": [],
            "author_notes": [],
        }

    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        [record for record in _value_records() if record.field == "R_mean"],
        section_writer=_writer(cite_claim_with_value),
    )
    claim_section = bundle.sections[1]
    assert claim_section.status == "publishable"
    assert claim_section.errors == []
    assert "0.004" in claim_section.paragraphs[0].rendered_text

    def raw_value_without_token(request):
        if request["section"]["section_id"].endswith("-section-01"):
            return _good_response(request)
        claim_alias = request["section"]["claim_bindings"][0]["claim_alias"]
        return {
            "paragraphs": [
                {
                    "text_with_value_tokens": (
                        "The measured mean reflectance reached 0.004."
                    ),
                    "claim_aliases": [claim_alias],
                    "figure_aliases": [],
                    "paragraph_role": "limitation",
                    "inference_kind": "bounded_inference",
                    "inference_note": "bound to the cited claim",
                }
            ],
            "deferred_claim_aliases": [],
            "author_notes": [],
        }

    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        [record for record in _value_records() if record.field == "R_mean"],
        section_writer=_writer(raw_value_without_token),
    )
    assert any("invented numeric content" in item for item in bundle.sections[1].errors)


def test_wrong_claim_cannot_use_claim_authorized_value_token() -> None:
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
    enriched = claim_a.model_copy(
        update={
            "metadata": {
                **claim_a.metadata,
                "value_lineage": [
                    {
                        "artifact_id": "FINAL_RESULT.json",
                        "field": "R_mean",
                        "label": "mean reflectance",
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
                enriched if claim.claim_id == claim_a.claim_id else claim
                for claim in ledger.claims
            ]
        }
    )
    manifest = _manifest() + [
        ArtifactDescriptor(
            artifact_id="B.json",
            path="b.json",
            fields=["worst_case"],
            sha256="b" * 64,
            content_summary="artifact B",
        )
    ]
    story_draft = _claim_section_story_draft(
        ledger,
        claim_a.claim_id,
        extra_bindings=[{"claim_id": claim_b.claim_id, "role": "positive"}],
    )
    architecture, story_id = _architecture(
        plan, ledger, manifest=manifest, story_draft=story_draft
    )
    records = [record for record in _value_records() if record.field == "R_mean"]

    def statement_alias(request, hypothesis_id):
        statement = next(
            claim.statement
            for claim in ledger.claims
            if claim.metadata.get("hypothesis_id") == hypothesis_id
        )
        return next(
            item["claim_alias"]
            for item in request["claims"]
            if item["statement"] == statement
        )

    def cite_claim_b_with_a_value(request):
        if request["section"]["section_id"].endswith("-section-01"):
            return _good_response(request)
        return {
            "paragraphs": [
                {
                    "text_with_value_tokens": ("Claim B uses [VALUE:V01_R_MEAN]."),
                    "claim_aliases": [statement_alias(request, "hyp-02")],
                    "figure_aliases": [],
                    "paragraph_role": "limitation",
                    "inference_kind": "bounded_inference",
                    "inference_note": "bound to the cited claim",
                }
            ],
            "deferred_claim_aliases": [],
            "author_notes": [],
        }

    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        records,
        section_writer=_writer(cite_claim_b_with_a_value),
    )
    assert any(
        "not authorized by any cited claim" in item
        for item in bundle.sections[1].errors
    )


def _three_paragraph_two_bad(request):
    response = _good_response(request, role="background", kind="none_required", note="")
    response["paragraphs"] = [
        response["paragraphs"][0],
        {
            "text_with_value_tokens": "Bad value [VALUE:V99_BOGUS].",
            "claim_aliases": ["C99_bogus"],
            "figure_aliases": [],
            "paragraph_role": "result",
            "inference_kind": "bounded_inference",
            "inference_note": "bad",
        },
        {
            "text_with_value_tokens": "Another bad value [VALUE:V98_BOGUS].",
            "claim_aliases": [],
            "figure_aliases": [],
            "paragraph_role": "result",
            "inference_kind": "bounded_inference",
            "inference_note": "bad",
        },
    ]
    return response


def _three_paragraph_two_bad_first(request):
    if request["section"]["section_id"].endswith("-section-01"):
        return _three_paragraph_two_bad(request)
    return _good_response(request, role="background", kind="none_required", note="")


def test_targeted_repair_accepts_order_only_missing_paragraph_ids() -> None:
    """Probe 031: all-missing IDs with exact count are injected in order."""

    plan, ledger = _ledger()
    architecture, story_id = _architecture(plan, ledger)
    captured: list[dict] = []

    def ordered_repair(request):
        rows = []
        for target in request["targeted_paragraphs"]:
            row = {key: value for key, value in target.items() if key != "paragraph_id"}
            row["text_with_value_tokens"] = "Fixed result paragraph."
            row["claim_aliases"] = [
                request["section"]["claim_bindings"][0]["claim_alias"]
            ]
            row["figure_aliases"] = []
            row["paragraph_role"] = "result"
            row["inference_kind"] = "bounded_inference"
            row["inference_note"] = "fixed"
            rows.append(row)
        return {"targeted_paragraphs": rows}

    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(_three_paragraph_two_bad_first),
        format_repair=_sequence_repairer([ordered_repair], captured=captured),
    )
    section = bundle.sections[0]
    assert section.status == "publishable"
    assert section.attempts == 2
    assert len(captured) == 1
    request = captured[0]
    assert request["repair_mode"] == "targeted_paragraphs"
    assert request["targeted_paragraph_ids"] == [
        f"{section.section_id}-p02",
        f"{section.section_id}-p03",
    ]
    assert all("paragraph_id" in row for row in request["targeted_paragraphs"])
    assert section.paragraphs[0].text_with_value_tokens == (
        _three_paragraph_two_bad(request)["paragraphs"][0]["text_with_value_tokens"]
    )
    assert section.paragraphs[1].text_with_value_tokens == ("Fixed result paragraph.")
    assert section.paragraphs[2].text_with_value_tokens == ("Fixed result paragraph.")


def test_targeted_repair_rejects_mixed_and_count_mismatch_ids() -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(plan, ledger)

    def mixed_ids(request):
        targets = request["targeted_paragraphs"]
        rows = []
        for index, target in enumerate(targets):
            row = {key: value for key, value in target.items() if key != "paragraph_id"}
            if index == 0:
                row["paragraph_id"] = target["paragraph_id"]
            rows.append(row)
        return {"targeted_paragraphs": rows}

    def too_few_rows(request):
        target = request["targeted_paragraphs"][0]
        row = {key: value for key, value in target.items() if key != "paragraph_id"}
        return {"targeted_paragraphs": [row]}

    def too_many_rows(request):
        target = request["targeted_paragraphs"][0]
        row = {key: value for key, value in target.items() if key != "paragraph_id"}
        return {"targeted_paragraphs": [row, dict(row), dict(row)]}

    for bad in (mixed_ids, too_few_rows, too_many_rows):
        captured: list[dict] = []
        bundle = build_article_draft_bundle(
            plan,
            ledger,
            architecture,
            story_id,
            _value_records(),
            section_writer=_writer(_three_paragraph_two_bad_first),
            format_repair=_sequence_repairer([bad], captured=captured),
        )
        section = bundle.sections[0]
        assert section.status == "needs_revision"
        assert section.attempts == 2
        assert len(captured) == 1
        assert any(
            "targeted repair response invalid" in item for item in section.warnings
        )


def test_zero_claim_section_contract_restricts_core_roles() -> None:
    plan, ledger = _ledger()
    positive = [
        claim
        for claim in ledger.claims
        if claim.status == ClaimStatus.partially_supported
    ][0]
    story_draft = _story_draft(ledger)
    story_draft["sections"].append(
        {
            "heading": "Background",
            "purpose": "introduce the research context",
            "key_messages": ["context"],
            "transitions": ["into results"],
            "claim_bindings": [],
            "figure_roles": [],
        }
    )
    architecture, story_id = _architecture(plan, ledger, story_draft=story_draft)
    captured: list[dict] = []

    def role_aware_writer(request):
        captured.append(dict(request))
        response = _good_response(request)
        if request["section"]["claim_bindings"] == []:
            response["paragraphs"][0]["claim_aliases"] = []
            response["paragraphs"][0]["paragraph_role"] = "background"
            response["paragraphs"][0]["inference_kind"] = "none_required"
            response["paragraphs"][0]["inference_note"] = ""
        return response

    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(role_aware_writer),
    )
    zero_claim = next(
        request for request in captured if request["section"]["claim_bindings"] == []
    )
    assert zero_claim["section_roles_allowed"] == ("background|transition|discussion")
    assert (
        zero_claim["response_contract"]["paragraphs"][0]["paragraph_role"]
        == "background|transition|discussion"
    )
    assert zero_claim["claims"] == []
    assert zero_claim["values"] == []
    assert bundle.sections[-1].status == "publishable"
    assert positive.claim_id


def test_value_rows_expose_authorized_claim_aliases() -> None:
    """Qwen sees semantic claim aliases, never opaque claim IDs."""

    plan, ledger = _lineage_ledger()
    positive = [
        claim
        for claim in ledger.claims
        if claim.status == ClaimStatus.partially_supported
    ][0]
    architecture, story_id = _architecture(
        plan,
        ledger,
        story_draft=_claim_section_story_draft(ledger, positive.claim_id),
    )
    captured: list[dict] = []
    build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        [record for record in _value_records() if record.field == "R_mean"],
        section_writer=_writer(_good_response, captured=captured),
    )
    assert captured
    claim_ids = {claim.claim_id for claim in ledger.claims}
    for request in captured:
        for row in request["values"]:
            assert row["authorized_claim_aliases"]
            assert all(
                __import__("re").fullmatch(r"C\d{2}_.+", alias)
                for alias in row["authorized_claim_aliases"]
            )
            assert not any(claim_id in str(row) for claim_id in claim_ids)
    claim_section = next(
        request
        for request in captured
        if request["section"]["section_id"].endswith("-section-02")
    )
    claim_row = next(
        row
        for row in claim_section["values"]
        if row["meaning"] == "claim-authorizing value"
    )
    assert claim_row["authorized_claim_aliases"] == [
        claim_section["section"]["claim_bindings"][0]["claim_alias"]
    ]


def test_lineage_claim_cannot_use_other_scalar_field_in_same_artifact() -> None:
    """A lineage claim authorizes only its exact field, not sibling scalars."""

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
    enriched = claim_a.model_copy(
        update={
            "metadata": {
                **claim_a.metadata,
                "value_lineage": [
                    {
                        "artifact_id": "FINAL_RESULT.json",
                        "field": "R_mean",
                        "label": "mean reflectance",
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
                enriched if claim.claim_id == claim_a.claim_id else claim
                for claim in ledger.claims
            ]
        }
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
    story_draft = {
        "story_shape": "shape-same-artifact",
        "central_thesis": "Two claims from the same artifact.",
        "sections": [
            {
                "heading": "Results same-artifact",
                "purpose": "present the verified result evidence",
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
        "exclusions": ["excluded-same-artifact"],
        "strengths": ["strength-same-artifact"],
        "risks": ["risk-same-artifact"],
        "recommendation_rationale": "rationale-same-artifact",
        "recommendation_score": 0.6,
    }
    architecture, story_id = _architecture(plan, ledger, story_draft=story_draft)
    records = [
        TrustedValueRecord(
            artifact_id="FINAL_RESULT.json",
            field="R_mean",
            rendered_value="0.004",
            source_hash="a" * 64,
            label="mean reflectance",
            prose_safe=True,
        ),
        TrustedValueRecord(
            artifact_id="FINAL_RESULT.json",
            field="worst_case",
            rendered_value="0.02",
            source_hash="a" * 64,
            label="worst-case reflectance",
            prose_safe=True,
        ),
    ]

    def alias_for(request, hypothesis_id):
        statement = next(
            claim.statement
            for claim in ledger.claims
            if claim.metadata.get("hypothesis_id") == hypothesis_id
        )
        return next(
            item["claim_alias"]
            for item in request["claims"]
            if item["statement"] == statement
        )

    def cite_a_with_worst_case(request):
        response = _good_response(request)
        response["paragraphs"][0]["claim_aliases"] = [alias_for(request, "hyp-01")]
        response["paragraphs"][0][
            "text_with_value_tokens"
        ] = "Claim A uses the sibling scalar [VALUE:V02_WORST_CASE]."
        return response

    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        records,
        section_writer=_writer(cite_a_with_worst_case),
    )
    assert any(
        "not authorized by any cited claim" in item
        for item in bundle.sections[0].errors
    )

    def cite_a_with_r_mean(request):
        response = _good_response(request)
        response["paragraphs"][0]["claim_aliases"] = [alias_for(request, "hyp-01")]
        response["paragraphs"][0][
            "text_with_value_tokens"
        ] = "Claim A uses its lineage value [VALUE:V01_R_MEAN]."
        return response

    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        records,
        section_writer=_writer(cite_a_with_r_mean),
    )
    assert bundle.sections[0].errors == []
    assert bundle.sections[0].status == "publishable"
