from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pytest

from optomind_optics.harness.article_architecture import (
    ArchitectureProviderResult,
    ArtifactDescriptor,
    build_article_architecture,
)
from optomind_optics.harness.article_claims import build_claim_ledger
from optomind_optics.harness.article_contracts import (
    ClaimStatus,
    ExperimentStatus,
    ObservationCard,
)
from optomind_optics.harness.article_director import ArticleDirector
from optomind_optics.harness.article_feedback import ArticleFeedbackController
from optomind_optics.harness.article_memory import ArticleMemoryStore, RunMemoryRecord
from optomind_optics.harness.article_review import (
    ArticleReviewResult,
    QwenExpressionReviewer,
    QwenScientificReviewer,
    ReviewSeverity,
    ReviewerProviderResult,
    _audit_section,
    _revision_preserves_bindings,
    build_article_review,
)
from optomind_optics.harness.article_writing import (
    TrustedValueRecord,
    WriterProviderResult,
    build_article_draft_bundle,
    build_writing_alias_maps,
    validate_writing_inputs,
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


def _ledger():
    plan = _plan()
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
    return plan, ledger


def _custom_plan_ledger(question: str):
    result = ArticleDirector().plan(
        question,
        _analysis(),
        _report(),
        force_mock=True,
    )
    assert result.status == "planned" and result.plan is not None
    plan = result.plan
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


def _value_records() -> list[TrustedValueRecord]:
    return [
        TrustedValueRecord(
            artifact_id="FINAL_RESULT.json",
            field="R_mean",
            rendered_value="0.004",
            source_hash="a" * 64,
            label="mean reflectance",
            prose_safe=True,
        )
    ]


def _story_draft(ledger, *, two_sections: bool = False) -> dict:
    positive = [
        c for c in ledger.claims if c.status == ClaimStatus.partially_supported
    ][0]
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
    binding = {"claim_id": positive.claim_id, "role": "positive"}
    sections = [
        {
            "heading": "Results",
            "purpose": "present the verified result evidence",
            "key_messages": ["key"],
            "transitions": ["next"],
            "claim_bindings": [binding],
            "figure_roles": ["spectrum"],
        }
    ]
    if two_sections:
        sections.append(
            {
                "heading": "Methods",
                "purpose": "describe the method",
                "key_messages": ["key"],
                "transitions": ["next"],
                "claim_bindings": [binding],
                "figure_roles": [],
            }
        )
    return {
        "story_shape": "shape-a",
        "central_thesis": "An evidence-bound AR design story.",
        "sections": sections,
        "figures": [figure],
        "omitted_claims": [],
        "exclusions": ["excluded"],
        "strengths": ["strength"],
        "risks": ["risk"],
        "recommendation_rationale": "rationale",
        "recommendation_score": 0.6,
    }


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


def _architecture(plan, ledger, *, story_draft=None):
    result = build_article_architecture(
        plan,
        ledger,
        _manifest(),
        architecture_provider=_architecture_provider(
            story_draft if story_draft is not None else _story_draft(ledger)
        ),
    )
    assert result.validation_errors == []
    return result, result.stories[0].story_id


def _writer_response(request, *, paragraphs: int = 2) -> dict:
    claim_aliases = [b["claim_alias"] for b in request["section"]["claim_bindings"]]
    figure_aliases = list(request["section"]["figure_aliases"])
    value_aliases = [item["alias"] for item in request["values"]]
    p1_text = (
        "The verified evidence supports the design claim within the declared " "scope."
    )
    if value_aliases:
        p1_text = f"{p1_text} [VALUE:{value_aliases[0]}]"
    body = [
        {
            "text_with_value_tokens": p1_text,
            "claim_aliases": claim_aliases,
            "figure_aliases": figure_aliases,
            "paragraph_role": "result",
            "inference_kind": "bounded_inference",
            "inference_note": "local inference from the cited claim",
        }
    ]
    if paragraphs > 1:
        body.append(
            {
                "text_with_value_tokens": (
                    "Additional context paragraph without numbers."
                ),
                "claim_aliases": claim_aliases,
                "figure_aliases": [],
                "paragraph_role": "discussion",
                "inference_kind": "none_required",
                "inference_note": "",
            }
        )
    return {
        "paragraphs": body,
        "deferred_claim_aliases": [],
        "author_notes": [],
    }


def _writer(builder: Callable[[dict], dict], *, captured: list | None = None):
    def provider(request):
        if captured is not None:
            captured.append(dict(request))
        return WriterProviderResult(
            response=builder(request),
            usage={"estimated_input_tokens": 10, "estimated_output_tokens": 20},
            provider_model="fake-writer",
        )

    return provider


def _bundle(plan, ledger, *, two_sections: bool = False, writer_builder=None):
    architecture, story_id = _architecture(
        plan, ledger, story_draft=_story_draft(ledger, two_sections=two_sections)
    )
    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(
            writer_builder
            if writer_builder is not None
            else (lambda request: _writer_response(request))
        ),
    )
    return plan, ledger, architecture, bundle, story_id


def _fixture(two_sections: bool = False, writer_builder=None):
    plan, ledger = _ledger()
    return _bundle(
        plan,
        ledger,
        two_sections=two_sections,
        writer_builder=writer_builder,
    )


def _finding(
    request: dict,
    *,
    paragraph_index: int = 0,
    severity: str = "minor",
    kind: str = "overclaim",
    reason: str = "The sentence claims more than the cited evidence supports.",
    suggested: str = "Tighten the claim wording.",
    claim_aliases: list[str] | None = None,
    span: str = "",
) -> dict:
    paragraph = request["paragraphs"][paragraph_index]
    aliases = (
        list(claim_aliases)
        if claim_aliases is not None
        else list(paragraph["claim_aliases"])
    )
    return {
        "paragraph_id": paragraph["paragraph_id"],
        "span": span,
        "severity": severity,
        "kind": kind,
        "reason": reason,
        "suggested_action": suggested,
        "claim_aliases": aliases,
    }


def _split_two_section_story(ledger) -> dict:
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
        "story_shape": "shape-split-two",
        "central_thesis": "Split sections story.",
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
                "heading": "Methods split",
                "purpose": "describe the method",
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
        "exclusions": ["excluded"],
        "strengths": ["strength"],
        "risks": ["risk"],
        "recommendation_rationale": "rationale",
        "recommendation_score": 0.6,
    }


def _reviewer(
    builder: Callable[[dict], Any] | Any,
    *,
    model: str = "fake-reviewer",
    captured: list | None = None,
):
    def provider(request):
        if captured is not None:
            captured.append(dict(request))
        if callable(builder):
            raw = builder(request)
        else:
            raw = builder
        if isinstance(raw, Exception):
            raise raw
        return ReviewerProviderResult(
            response=raw,
            usage={"estimated_input_tokens": 7, "estimated_output_tokens": 9},
            provider_model=model,
        )

    return provider


def _reviser(
    builder: Callable[[dict], dict],
    *,
    model: str = "fake-reviser",
    captured: list | None = None,
):
    def provider(request):
        if captured is not None:
            captured.append(dict(request))
        return ReviewerProviderResult(
            response=builder(request),
            usage={"estimated_input_tokens": 5, "estimated_output_tokens": 6},
            provider_model=model,
        )

    return provider


def _empty_response(request) -> dict:
    return {"findings": [], "advice": []}


def _revise_first(request) -> dict:
    pid = request["findings"][0]["paragraph_id"]
    alias = request["values"][0]["alias"] if request["values"] else None
    text = "The corrected sentence addresses the finding."
    if alias:
        text = f"{text} [VALUE:{alias}]"
    return {
        "revised_paragraphs": [{"paragraph_id": pid, "text_with_value_tokens": text}],
        "author_notes": ["fixed"],
    }


def test_bundle_tampering_fails_before_provider() -> None:
    plan, ledger, architecture, bundle, story_id = _fixture()
    captured: list[dict] = []
    tampered = bundle.model_copy(update={"bundle_id": "bundle-deadbeef"})
    result = build_article_review(
        plan,
        ledger,
        architecture,
        tampered,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(_empty_response, captured=captured),
        expression_reviewer=_reviewer(_empty_response, captured=captured),
    )
    assert any(
        "does not match recomputed identity" in item for item in result.hard_blockers
    )
    assert captured == []

    section = bundle.sections[0]
    tampered_section = section.model_copy(
        update={
            "rendered_prose": section.rendered_prose + " tampered",
            "word_count": section.word_count + 1,
        }
    )
    tampered_bundle = bundle.model_copy(update={"sections": [tampered_section]})
    result = build_article_review(
        plan,
        ledger,
        architecture,
        tampered_bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(_empty_response, captured=captured),
        expression_reviewer=_reviewer(_empty_response, captured=captured),
    )
    assert any(
        "does not match recomputed identity" in item for item in result.hard_blockers
    )
    assert captured == []


def test_clean_section_ready_and_not_rewritten() -> None:
    plan, ledger, architecture, bundle, story_id = _fixture()
    reviser_captured: list[dict] = []
    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(_empty_response),
        expression_reviewer=_reviewer(_empty_response),
        author_reviser=_reviser(_revise_first, captured=reviser_captured),
    )
    assert result.status == "ready"
    assert result.sections[0].status.value == "ready"
    assert result.sections[0].revisions == []
    assert reviser_captured == []
    assert (
        result.sections[0].section_draft.rendered_prose
        == bundle.sections[0].rendered_prose
    )


def test_advisory_findings_without_reviser_ready_with_findings() -> None:
    plan, ledger, architecture, bundle, story_id = _fixture()
    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(
            lambda request: {
                "findings": [_finding(request, severity="major")],
                "advice": ["keep the scope tight"],
            }
        ),
        expression_reviewer=_reviewer(_empty_response),
        author_reviser=None,
    )
    assert result.status == "ready_with_findings"
    section = result.sections[0]
    assert section.status.value == "ready_with_findings"
    assert len(section.findings) == 1
    assert section.findings[0].severity == ReviewSeverity.major
    assert section.revisions == []
    assert section.section_draft.rendered_prose == bundle.sections[0].rendered_prose
    assert any("keep the scope tight" in item for item in result.retained_advice)


def test_reviewer_malformed_and_unavailable_fail_open() -> None:
    plan, ledger, architecture, bundle, story_id = _fixture()
    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(RuntimeError("reviewer down")),
        expression_reviewer=_reviewer(
            lambda request: {
                "findings": [
                    {
                        "paragraph_id": "unknown-p",
                        "severity": "major",
                        "kind": "clarity",
                        "reason": "",
                        "suggested_action": "",
                    }
                ],
                "advice": [],
            }
        ),
    )
    section = result.sections[0]
    assert section.status.value == "ready"
    assert section.findings == []
    assert any("reviewer unavailable" in item for item in result.warnings)
    assert any("malformed" in item for item in result.warnings)
    assert result.status == "ready"


def test_revision_only_targets_named_paragraphs_and_preserves_others() -> None:
    plan, ledger, architecture, bundle, story_id = _fixture()
    state = {"calls": 0}

    def sci(request):
        state["calls"] += 1
        if state["calls"] == 1:
            return {
                "findings": [
                    _finding(request, paragraph_index=0, suggested="Revise p1.")
                ],
                "advice": [],
            }
        return {"findings": [], "advice": []}

    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(sci),
        expression_reviewer=_reviewer(_empty_response),
        author_reviser=_reviser(_revise_first),
    )
    section = result.sections[0]
    assert section.status.value == "ready"
    assert section.revisions
    assert section.revisions[0].revised_paragraph_ids == [
        bundle.sections[0].paragraphs[0].paragraph_id
    ]
    assert section.revisions[0].resolved_finding_ids
    original_p2 = bundle.sections[0].paragraphs[1]
    final_p2 = next(
        p
        for p in section.section_draft.paragraphs
        if p.paragraph_id == original_p2.paragraph_id
    )
    assert final_p2.model_dump(mode="json") == original_p2.model_dump(mode="json")


def test_revision_and_reconstruction_preserve_literature_bindings() -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(plan, ledger)

    def writer_response(request):
        response = _writer_response(request)
        response["paragraphs"][0]["literature_evidence_aliases"] = ["E01_prior"]
        return response

    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(writer_response),
        literature_context={"evidence": [{"alias": "E01_prior", "title": "Prior"}]},
        literature_evidence_alias_map={"E01_prior": "ev-prior"},
    )
    state = {"calls": 0}

    def sci(request):
        state["calls"] += 1
        if state["calls"] == 1:
            return {
                "findings": [
                    _finding(request, paragraph_index=0, suggested="Revise p1.")
                ],
                "advice": [],
            }
        return {"findings": [], "advice": []}

    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(sci),
        expression_reviewer=_reviewer(_empty_response),
        author_reviser=_reviser(_revise_first),
    )
    section = result.sections[0]
    original = bundle.sections[0]
    assert section.status.value == "ready"
    assert section.revisions
    assert section.section_draft.paragraphs[0].literature_evidence_ids == ["ev-prior"]
    assert section.section_draft.source_ledger[0].literature_evidence_ids == [
        "ev-prior"
    ]

    tampered_paragraph = section.section_draft.paragraphs[0].model_copy(
        update={"literature_evidence_ids": []}
    )
    tampered = section.section_draft.model_copy(
        update={
            "paragraphs": [tampered_paragraph]
            + list(section.section_draft.paragraphs[1:])
        }
    )
    assert "changed literature_evidence_ids" in (
        _revision_preserves_bindings(
            original,
            tampered,
            [original.paragraphs[0].paragraph_id],
        )
        or ""
    )


def test_revision_accepts_removed_major_span_and_retains_new_advice() -> None:
    plan, ledger, architecture, bundle, story_id = _fixture()
    state = {"calls": 0}
    original_span = bundle.sections[0].paragraphs[0].rendered_text

    def sci(request):
        state["calls"] += 1
        if state["calls"] == 1:
            return {
                "findings": [
                    _finding(
                        request,
                        severity="major",
                        span=original_span,
                        suggested="Remove the unsupported major statement.",
                    )
                ],
                "advice": [],
            }
        return {
            "findings": [
                _finding(
                    request,
                    severity="major",
                    reason="A different advisory issue remains.",
                    suggested="Consider another bounded revision.",
                ),
                _finding(
                    request,
                    severity="minor",
                    reason="Minor wording advice.",
                    suggested="Polish the wording.",
                ),
            ],
            "advice": [],
        }

    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(sci),
        expression_reviewer=_reviewer(_empty_response),
        author_reviser=_reviser(_revise_first),
    )

    section = result.sections[0]
    assert section.revisions[0].progress is True
    assert section.section_draft.rendered_prose != bundle.sections[0].rendered_prose
    assert len(section.findings) == 2
    assert any(
        "accepted after removing the exact span" in item for item in result.warnings
    )


def test_unsafe_revision_rejected_and_last_safe_draft_retained() -> None:
    plan, ledger, architecture, bundle, story_id = _fixture()

    def sci(request):
        return {
            "findings": [_finding(request, paragraph_index=0, suggested="Revise p1.")],
            "advice": [],
        }

    def unsafe_reviser(request):
        pid = request["findings"][0]["paragraph_id"]
        return {
            "revised_paragraphs": [
                {
                    "paragraph_id": pid,
                    "text_with_value_tokens": "Fabricated 0.123 number.",
                }
            ],
            "author_notes": [],
        }

    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(sci),
        expression_reviewer=_reviewer(_empty_response),
        author_reviser=_reviser(unsafe_reviser),
    )
    section = result.sections[0]
    assert section.status.value == "ready_with_findings"
    assert any("last safe draft retained" in item for item in section.warnings) or any(
        "last safe draft retained" in item for item in result.warnings
    )
    assert section.section_draft.rendered_prose == bundle.sections[0].rendered_prose
    assert len(section.findings) == 1

    def unknown_target_reviser(request):
        return {
            "revised_paragraphs": [
                {
                    "paragraph_id": "story-01-section-01-p99",
                    "text_with_value_tokens": "wrong target",
                }
            ],
            "author_notes": [],
        }

    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(sci),
        expression_reviewer=_reviewer(_empty_response),
        author_reviser=_reviser(unknown_target_reviser),
    )
    section = result.sections[0]
    assert section.status.value == "ready_with_findings"
    assert section.section_draft.rendered_prose == bundle.sections[0].rendered_prose


def test_review_accepts_stage10_source_literals() -> None:
    plan, ledger = _custom_plan_ledger(
        "Design an emitter with mean absorptance at or above 85 percent, "
        "worst-case absorptance at or below 20 percent, layer thicknesses up "
        "to 1500 nm, and a minimum target of 60 percent."
    )

    def writer_builder(request):
        response = _writer_response(request)
        response["paragraphs"][0]["text_with_value_tokens"] = (
            "Target is >=85%; worst case below 20 percent; thickness 1500 nm; "
            "minimum 60 percent."
        )
        return response

    plan, ledger, architecture, bundle, story_id = _bundle(
        plan,
        ledger,
        writer_builder=writer_builder,
    )
    assert bundle.sections[0].status == "publishable"

    def noop_reviser(request):
        return {"revised_paragraphs": [], "author_notes": []}

    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(_empty_response),
        expression_reviewer=_reviewer(_empty_response),
        author_reviser=_reviser(noop_reviser),
    )
    section = result.sections[0]
    assert section.status.value in {"ready", "ready_with_findings"}
    assert not any("invented numeric content" in item for item in section.hard_blockers)


def test_review_rejects_invented_values_via_revision() -> None:
    plan, ledger = _custom_plan_ledger(
        "Design an emitter with mean absorptance at or above 85 percent, "
        "worst-case absorptance at or below 20 percent, layer thicknesses up "
        "to 1500 nm."
    )
    plan, ledger, architecture, bundle, story_id = _bundle(plan, ledger)

    def sci(request):
        return {
            "findings": [
                _finding(
                    request,
                    paragraph_index=0,
                    suggested="Revise p1.",
                )
            ],
            "advice": [],
        }

    for text in (
        "The result reached 95%.",
        "The result reached 40%.",
        "Measured 0.123.",
        "Scale 1e3.",
    ):

        def unsafe_reviser(request, text=text):
            pid = request["findings"][0]["paragraph_id"]
            return {
                "revised_paragraphs": [
                    {
                        "paragraph_id": pid,
                        "text_with_value_tokens": (f"{text} [VALUE:V01_R_MEAN]"),
                    }
                ],
                "author_notes": [],
            }

        result = build_article_review(
            plan,
            ledger,
            architecture,
            bundle,
            story_id,
            _value_records(),
            scientific_reviewer=_reviewer(sci),
            expression_reviewer=_reviewer(_empty_response),
            author_reviser=_reviser(unsafe_reviser),
        )
        section = result.sections[0]
        assert any(
            "last safe draft retained" in item for item in section.warnings
        ) or any("last safe draft retained" in item for item in result.warnings), text
        assert (
            section.section_draft.rendered_prose == bundle.sections[0].rendered_prose
        ), text


def test_review_does_not_whitelist_from_untrusted_source_field() -> None:
    plan, ledger = _custom_plan_ledger(
        "Design an emitter with mean absorptance at or above 85 percent."
    )
    capability = plan.capability.model_copy(
        update={"clarification_questions": ["Is 95 percent acceptable?"]}
    )
    plan = plan.model_copy(update={"capability": capability})
    plan, ledger, architecture, bundle, story_id = _bundle(plan, ledger)

    def sci(request):
        return {
            "findings": [
                _finding(
                    request,
                    paragraph_index=0,
                    suggested="Revise p1.",
                )
            ],
            "advice": [],
        }

    def unsafe_reviser(request):
        pid = request["findings"][0]["paragraph_id"]
        return {
            "revised_paragraphs": [
                {
                    "paragraph_id": pid,
                    "text_with_value_tokens": (
                        "The result reached 95%. [VALUE:V01_R_MEAN]"
                    ),
                }
            ],
            "author_notes": [],
        }

    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(sci),
        expression_reviewer=_reviewer(_empty_response),
        author_reviser=_reviser(unsafe_reviser),
    )
    section = result.sections[0]
    assert any("last safe draft retained" in item for item in section.warnings) or any(
        "last safe draft retained" in item for item in result.warnings
    )
    assert section.section_draft.rendered_prose == bundle.sections[0].rendered_prose


def test_negative_role_preserved_after_revision() -> None:
    plan = _plan()
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
    positive = [
        c for c in ledger.claims if c.status == ClaimStatus.partially_supported
    ][0]
    draft_claim = [c for c in ledger.claims if c.status == ClaimStatus.draft][0]
    fact = next(
        f for f in ledger.facts if f.metadata.get("claim_id") == positive.claim_id
    )
    story_draft = {
        "story_shape": "shape-split",
        "central_thesis": "Split roles story.",
        "sections": [
            {
                "heading": "Results",
                "purpose": "present the verified result evidence",
                "key_messages": ["key"],
                "transitions": ["next"],
                "claim_bindings": [
                    {"claim_id": positive.claim_id, "role": "positive"},
                    {"claim_id": draft_claim.claim_id, "role": "limitation"},
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
        ],
        "omitted_claims": [],
        "exclusions": ["excluded"],
        "strengths": ["strength"],
        "risks": ["risk"],
        "recommendation_rationale": "rationale",
        "recommendation_score": 0.6,
    }
    architecture, story_id = _architecture(plan, ledger, story_draft=story_draft)
    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(
            lambda request: {
                "paragraphs": [
                    {
                        "text_with_value_tokens": (
                            "Positive evidence [VALUE:V01_R_MEAN]."
                        ),
                        "claim_aliases": [
                            b["claim_alias"]
                            for b in request["section"]["claim_bindings"]
                        ],
                        "figure_aliases": list(request["section"]["figure_aliases"]),
                        "paragraph_role": "result",
                        "inference_kind": "bounded_inference",
                        "inference_note": "bounded",
                    }
                ],
                "deferred_claim_aliases": [],
                "author_notes": [],
            }
        ),
    )
    assert bundle.publishable

    state = {"calls": 0}

    def sci(request):
        state["calls"] += 1
        if state["calls"] == 1:
            return {
                "findings": [_finding(request, suggested="Revise p1.")],
                "advice": [],
            }
        return {"findings": [], "advice": []}

    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(sci),
        expression_reviewer=_reviewer(_empty_response),
        author_reviser=_reviser(_revise_first),
    )
    section = result.sections[0]
    assert section.status.value == "ready"
    assert section.section_draft.source_ledger[0].roles == ["limitation", "positive"]


def test_no_progress_and_repeated_content_and_max_rounds() -> None:
    plan, ledger, architecture, bundle, story_id = _fixture()

    def sci_swap(request):
        return {
            "findings": [
                _finding(
                    request,
                    reason="First reason wording.",
                    suggested="Revise p1.",
                )
            ],
            "advice": [],
        }

    def sci_swap_after(request):
        return {
            "findings": [
                _finding(
                    request,
                    reason="Second reason wording.",
                    suggested="Revise p1 again.",
                )
            ],
            "advice": [],
        }

    state = {"round": 0}

    def sci_stateful(request):
        state["round"] += 1
        if state["round"] <= 1:
            return sci_swap(request)
        return sci_swap_after(request)

    def always_change(request):
        pid = request["findings"][0]["paragraph_id"]
        state["revision"] = state.get("revision", 0) + 1
        alias = request["values"][0]["alias"] if request["values"] else None
        text = f"Corrected sentence revision {state['revision']}."
        if alias:
            text = f"{text} [VALUE:{alias}]"
        return {
            "revised_paragraphs": [
                {
                    "paragraph_id": pid,
                    "text_with_value_tokens": text,
                }
            ],
            "author_notes": [],
        }

    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(sci_stateful),
        expression_reviewer=_reviewer(_empty_response),
        author_reviser=_reviser(always_change),
    )
    section = result.sections[0]
    assert len(section.revisions) == 1
    assert section.revisions[0].progress is False
    assert any("no material progress" in item for item in result.warnings)

    def same_content(request):
        pid = request["findings"][0]["paragraph_id"]
        original_text = request["paragraphs"][0]["text_with_value_tokens"]
        return {
            "revised_paragraphs": [
                {"paragraph_id": pid, "text_with_value_tokens": original_text}
            ],
            "author_notes": [],
        }

    state = {"round": 0}

    def sci_repeat(request):
        state["round"] += 1
        if state["round"] <= 1:
            return sci_swap(request)
        return sci_swap_after(request)

    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(sci_repeat),
        expression_reviewer=_reviewer(_empty_response),
        author_reviser=_reviser(same_content),
    )
    section = result.sections[0]
    assert len(section.revisions) == 1
    assert any("repeated content identity" in item for item in result.warnings)

    counts = [3, 2, 1, 1]
    state = {"calls": 0}

    def sci_counted(request):
        call = state["calls"]
        state["calls"] += 1
        count = counts[call] if call < len(counts) else 1
        findings = []
        for index in range(count):
            findings.append(
                _finding(
                    request,
                    reason=f"Finding reason {index} of round {call}.",
                    kind="overclaim",
                    severity="major",
                    suggested=f"Revise p1 aspect {index}.",
                )
            )
        return {"findings": findings, "advice": []}

    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(sci_counted),
        expression_reviewer=_reviewer(_empty_response),
        author_reviser=_reviser(always_change),
    )
    section = result.sections[0]
    assert len(section.revisions) == 3
    assert all(round_item.progress for round_item in section.revisions[:2])


def test_one_blocked_section_does_not_erase_siblings() -> None:
    def bad_second(request):
        if request["section"]["section_id"].endswith("-section-02"):
            response = _writer_response(request, paragraphs=1)
            response["paragraphs"][0]["claim_aliases"] = ["C99_bogus"]
            return response
        return _writer_response(request, paragraphs=1)

    plan, ledger, architecture, bundle, story_id = _fixture(
        two_sections=True, writer_builder=bad_second
    )
    assert len(bundle.sections) == 2
    assert bundle.sections[1].status == "needs_revision"
    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(_empty_response),
        expression_reviewer=_reviewer(_empty_response),
    )
    assert result.status == "partial"
    assert result.sections[0].status.value == "ready"
    assert result.sections[1].status.value == "blocked"
    assert any("not publishable" in item for item in result.sections[1].hard_blockers)


def test_prompt_uses_aliases_not_claim_hashes() -> None:
    plan, ledger, architecture, bundle, story_id = _fixture()
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
    build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(_empty_response, captured=captured),
        expression_reviewer=_reviewer(_empty_response),
    )
    serialized = json.dumps(captured[0], sort_keys=True)
    assert "C01_" in serialized
    assert "claim_ids" not in serialized
    assert "paragraph_id" in serialized
    assert captured[0]["claims"][0]["synthesis_contract"] == contract


def test_global_consistency_review_receives_all_sections_once() -> None:
    plan, ledger, architecture, bundle, story_id = _fixture(two_sections=True)
    global_requests: list[dict] = []
    scientific_requests: list[dict] = []
    expression_requests: list[dict] = []

    build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(
            _empty_response,
            captured=scientific_requests,
        ),
        expression_reviewer=_reviewer(
            _empty_response,
            captured=expression_requests,
        ),
        global_consistency_reviewer=_reviewer(
            _empty_response,
            captured=global_requests,
        ),
    )

    assert len(global_requests) == 2
    assert {request["focus"] for request in global_requests} == {
        "recommendation",
        "cross_metric",
    }
    for global_request in global_requests:
        assert global_request["review_role"] == "global_consistency"
        assert global_request["paragraphs"]
        assert len(global_request["claims"]) == len(
            architecture.stories[0].claim_assignments
        )
        assert all(
            "section_id" in paragraph for paragraph in global_request["paragraphs"]
        )
    first_scientific = next(
        request
        for request in scientific_requests
        if request["section"]["section_id"].endswith("-section-01")
    )
    assert "story_claim_memory" not in first_scientific
    first_expression = next(
        request
        for request in expression_requests
        if request["section"]["section_id"].endswith("-section-01")
    )
    assert "story_claim_memory" not in first_expression


def test_global_consistency_finding_is_routed_to_section_reviser() -> None:
    plan, ledger, architecture, bundle, story_id = _fixture()
    reviser_requests: list[dict] = []

    def global_review(request):
        return {
            "findings": [
                _finding(
                    request,
                    severity="major",
                    kind="cross_metric_contradiction",
                    suggested="Remove the unsupported whole-Article conclusion.",
                    claim_aliases=[],
                )
            ],
            "advice": [],
        }

    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(_empty_response),
        expression_reviewer=_reviewer(_empty_response),
        global_consistency_reviewer=_reviewer(global_review),
        author_reviser=_reviser(_revise_first, captured=reviser_requests),
    )

    assert reviser_requests
    assert any(
        finding["kind"] == "cross_metric_contradiction"
        for finding in reviser_requests[0]["findings"]
    )
    assert result.sections[0].revisions


def test_global_concrete_advice_is_promoted_by_router() -> None:
    plan, ledger, architecture, bundle, story_id = _fixture()
    reviser_requests: list[dict] = []
    router_requests: list[dict] = []

    def global_review(_request):
        return {
            "findings": [],
            "advice": [
                "The conclusion makes a concrete unsupported candidate "
                "recommendation and must be narrowed."
            ],
        }

    def route_advice(request):
        return {
            "findings": [
                _finding(
                    request,
                    severity="major",
                    kind="unsupported_recommendation",
                    suggested="Remove the unsupported recommendation.",
                    claim_aliases=[],
                )
            ],
            "advice": [],
        }

    build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(_empty_response),
        expression_reviewer=_reviewer(_empty_response),
        global_consistency_reviewer=_reviewer(global_review),
        global_advice_router=_reviewer(
            route_advice,
            captured=router_requests,
        ),
        author_reviser=_reviser(_revise_first, captured=reviser_requests),
    )

    assert router_requests
    assert all(request["advice_to_route"] for request in router_requests)
    assert reviser_requests
    assert any(
        finding["kind"] == "unsupported_recommendation"
        for request in reviser_requests
        for finding in request["findings"]
    )


def test_usage_attempts_and_semantic_model_truthful() -> None:
    plan, ledger, architecture, bundle, story_id = _fixture()
    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(_empty_response, model="fake-reviewer"),
        expression_reviewer=_reviewer(_empty_response, model="fake-reviewer"),
    )
    assert result.semantic_model == "fake-reviewer"
    assert result.attempts == 2
    assert result.usage["estimated_input_tokens"] == 14
    assert result.usage["estimated_output_tokens"] == 18
    assert "estimated_cost_cny" not in result.usage


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


def test_qwen_scientific_reviewer_adapter_parses_and_preserves_usage() -> None:
    plan, ledger, architecture, bundle, story_id = _fixture()
    sci_client = FakeQwenClient(
        json.dumps({"findings": [], "advice": ["general advice"]})
    )
    expr_client = FakeQwenClient(json.dumps({"findings": [], "advice": []}))
    reviewer = QwenScientificReviewer(client=sci_client)
    expression_reviewer = QwenExpressionReviewer(client=expr_client)
    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=reviewer,
        expression_reviewer=expression_reviewer,
    )
    assert result.semantic_model == "qwen3.7-flash"
    assert result.usage["estimated_cost_cny"] > 0
    assert sci_client.kwargs["max_tokens"] == 6000
    assert expr_client.kwargs["max_tokens"] == 5000
    assert '"findings"' in sci_client.messages[0]["content"]


def test_persistence_replay_and_conflict(tmp_path) -> None:
    plan, ledger, architecture, bundle, story_id = _fixture()
    memory = ArticleMemoryStore(tmp_path / "memory.sqlite")
    graph = ExperimentGraph(tmp_path / "graph.sqlite", "run-1")
    journal = tmp_path / "journal.json"
    original_create = graph.create_article_node

    def failing_create(*args, **kwargs):
        raise RuntimeError("graph write failed")

    graph.create_article_node = failing_create  # type: ignore[method-assign]
    with pytest.raises(Exception, match="review persistence failed"):
        build_article_review(
            plan,
            ledger,
            architecture,
            bundle,
            story_id,
            _value_records(),
            scientific_reviewer=_reviewer(_empty_response),
            expression_reviewer=_reviewer(_empty_response),
            memory_store=memory,
            graph=graph,
            run_id="run-1",
            journal_path=journal,
        )
    graph.create_article_node = original_create  # type: ignore[method-assign]
    journal_result_id = next(
        key for key in json.loads(journal.read_text(encoding="utf-8"))
    )
    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(_empty_response),
        expression_reviewer=_reviewer(_empty_response),
        memory_store=memory,
        graph=graph,
        run_id="run-1",
        journal_path=journal,
    )
    assert result.result_id == journal_result_id
    node = graph.article_node(f"review-{result.result_id}")
    memory_count = len(memory.run_memory_records())
    history_len = len(node["history"])
    retry = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(_empty_response),
        expression_reviewer=_reviewer(_empty_response),
        memory_store=memory,
        graph=graph,
        run_id="run-1",
        journal_path=journal,
    )
    assert retry.result_id == result.result_id
    assert len(memory.run_memory_records()) == memory_count
    assert (
        len(graph.article_node(f"review-{result.result_id}")["history"]) == history_len
    )

    probe = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(_empty_response),
        expression_reviewer=_reviewer(_empty_response),
    )
    conflict_memory = ArticleMemoryStore(tmp_path / "conflict.sqlite")
    conflict_graph = ExperimentGraph(tmp_path / "conflict-graph.sqlite", "run-1")
    conflict_memory.add_run_memory(
        RunMemoryRecord(
            memory_id=f"review-{probe.result_id}",
            run_id="run-1",
            event_type="article_review",
            operational_note="tampered conflicting payload",
        )
    )
    with pytest.raises(Exception, match="different content"):
        build_article_review(
            plan,
            ledger,
            architecture,
            bundle,
            story_id,
            _value_records(),
            scientific_reviewer=_reviewer(_empty_response),
            expression_reviewer=_reviewer(_empty_response),
            memory_store=conflict_memory,
            graph=conflict_graph,
            run_id="run-1",
            journal_path=tmp_path / "conflict-journal.json",
        )


def test_ghost_artifact_and_aggregate_prose_tamper_fails() -> None:
    from optomind_optics.harness.article_writing import compute_bundle_id

    plan, ledger, architecture, bundle, story_id = _fixture()
    section = bundle.sections[0]
    entry = section.source_ledger[0]
    tampered_entry = entry.model_copy(
        update={"artifact_ids": sorted(set(entry.artifact_ids) | {"GHOST.json"})}
    )
    tampered_section = section.model_copy(
        update={
            "source_ledger": [tampered_entry],
            "tokenized_prose": section.tokenized_prose + " tampered",
            "rendered_prose": section.rendered_prose + " tampered",
            "word_count": section.word_count + 1,
        }
    )
    tampered_bundle = bundle.model_copy(
        update={
            "sections": [tampered_section],
            "source_ledger": [tampered_entry],
        }
    )
    new_bundle_id = compute_bundle_id(
        plan.plan_id,
        ledger.ledger_id,
        architecture.architecture_id,
        story_id,
        tampered_bundle.sections,
    )
    tampered_bundle = tampered_bundle.model_copy(update={"bundle_id": new_bundle_id})
    captured: list[dict] = []
    result = build_article_review(
        plan,
        ledger,
        architecture,
        tampered_bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(_empty_response, captured=captured),
        expression_reviewer=_reviewer(_empty_response, captured=captured),
    )
    assert result.status == "blocked"
    assert any("source_ledger mismatch" in item for item in result.hard_blockers)
    assert any("tokenized_prose mismatch" in item for item in result.hard_blockers)
    assert captured == []


def test_extra_ledger_entry_wrong_fields_and_identity_fail() -> None:
    from optomind_optics.harness.article_writing import compute_bundle_id

    plan, ledger, architecture, bundle, story_id = _fixture()
    section = bundle.sections[0]
    entry = section.source_ledger[0]
    captured: list[dict] = []

    def rebuild(section_model):
        updated_bundle = bundle.model_copy(
            update={
                "sections": [section_model],
                "source_ledger": list(section_model.source_ledger),
            }
        )
        new_bundle_id = compute_bundle_id(
            plan.plan_id,
            ledger.ledger_id,
            architecture.architecture_id,
            story_id,
            updated_bundle.sections,
        )
        return updated_bundle.model_copy(update={"bundle_id": new_bundle_id})

    extra_entry = entry.model_copy(update={"paragraph_id": "story-01-section-01-p99"})
    tampered = section.model_copy(
        update={"source_ledger": section.source_ledger + [extra_entry]}
    )
    result = build_article_review(
        plan,
        ledger,
        architecture,
        rebuild(tampered),
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(_empty_response, captured=captured),
        expression_reviewer=_reviewer(_empty_response, captured=captured),
    )
    assert any("source_ledger mismatch" in item for item in result.hard_blockers)
    assert captured == []

    wrong_entry = entry.model_copy(
        update={
            "fact_ids": ["fact-bogus"],
            "scopes": ["wrong scope"],
            "roles": ["positive"],
        }
    )
    tampered = section.model_copy(update={"source_ledger": [wrong_entry]})
    result = build_article_review(
        plan,
        ledger,
        architecture,
        rebuild(tampered),
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(_empty_response, captured=captured),
        expression_reviewer=_reviewer(_empty_response, captured=captured),
    )
    assert any("source_ledger mismatch" in item for item in result.hard_blockers)
    assert captured == []

    tampered = section.model_copy(
        update={"story_id": "story-99", "architecture_id": "architecture-99"}
    )
    result = build_article_review(
        plan,
        ledger,
        architecture,
        rebuild(tampered),
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(_empty_response, captured=captured),
        expression_reviewer=_reviewer(_empty_response, captured=captured),
    )
    assert any("story_id mismatch" in item for item in result.hard_blockers)
    assert any("architecture_id mismatch" in item for item in result.hard_blockers)
    assert captured == []


def test_revision_cannot_remove_or_duplicate_value_bindings() -> None:
    plan, ledger, architecture, bundle, story_id = _fixture()
    state = {"calls": 0}

    def sci(request):
        state["calls"] += 1
        if state["calls"] == 1:
            return {
                "findings": [_finding(request, suggested="Revise p1.")],
                "advice": [],
            }
        return {"findings": [], "advice": []}

    def remove_token(request):
        pid = request["findings"][0]["paragraph_id"]
        return {
            "revised_paragraphs": [
                {
                    "paragraph_id": pid,
                    "text_with_value_tokens": "No value token here.",
                }
            ],
            "author_notes": [],
        }

    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(sci),
        expression_reviewer=_reviewer(_empty_response),
        author_reviser=_reviser(remove_token),
    )
    section = result.sections[0]
    assert section.status.value == "ready_with_findings"
    assert section.revisions == []
    assert any(
        "value_token_ids" in item and "revision rejected" in item
        for item in result.warnings
    )
    assert section.section_draft.rendered_prose == bundle.sections[0].rendered_prose
    state["calls"] = 0

    def duplicate_targets(request):
        pid = request["findings"][0]["paragraph_id"]
        return {
            "revised_paragraphs": [
                {"paragraph_id": pid, "text_with_value_tokens": "First."},
                {"paragraph_id": pid, "text_with_value_tokens": "Second."},
            ],
            "author_notes": [],
        }

    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(sci),
        expression_reviewer=_reviewer(_empty_response),
        author_reviser=_reviser(duplicate_targets),
    )
    section = result.sections[0]
    assert section.status.value == "ready_with_findings"
    assert any("duplicate paragraph target" in item for item in result.warnings)
    assert section.section_draft.rendered_prose == bundle.sections[0].rendered_prose


def test_reviewer_outcomes_are_truthful() -> None:
    plan, ledger, architecture, bundle, story_id = _fixture()

    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(RuntimeError("sci down")),
        expression_reviewer=_reviewer(RuntimeError("expr down")),
    )
    assert result.model_status == "unavailable"
    assert result.attempts == 2
    assert result.sections[0].reviewer_status == {
        "scientific": "unavailable",
        "expression": "unavailable",
    }
    assert result.sections[0].status.value == "ready"

    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(_empty_response),
        expression_reviewer=_reviewer(RuntimeError("expr down")),
    )
    assert result.model_status == "partial"
    assert result.attempts == 2
    assert result.sections[0].reviewer_status == {
        "scientific": "valid",
        "expression": "unavailable",
    }


def test_re_review_failure_retains_findings_and_stops() -> None:
    plan, ledger, architecture, bundle, story_id = _fixture()
    state = {"calls": 0}

    def sci(request):
        state["calls"] += 1
        if state["calls"] == 1:
            return {
                "findings": [_finding(request, suggested="Revise p1.")],
                "advice": [],
            }
        raise RuntimeError("re-review down")

    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(sci),
        expression_reviewer=_reviewer(_empty_response),
        author_reviser=_reviser(_revise_first),
    )
    section = result.sections[0]
    assert section.status.value == "ready_with_findings"
    assert len(section.findings) == 1
    assert section.revisions and section.revisions[-1].progress is False
    assert section.section_draft.rendered_prose == bundle.sections[0].rendered_prose
    assert any("re-review did not succeed" in item for item in result.warnings)
    assert section.reviewer_status == {
        "scientific": "unavailable",
        "expression": "valid",
    }
    assert result.model_status == "partial"

    state2 = {"calls": 0}

    def sci_malformed(request):
        state2["calls"] += 1
        if state2["calls"] == 1:
            return {
                "findings": [_finding(request, suggested="Revise p1.")],
                "advice": [],
            }
        return {"findings": [{"bad": "shape"}], "advice": []}

    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(sci_malformed),
        expression_reviewer=_reviewer(_empty_response),
        author_reviser=_reviser(_revise_first),
    )
    section = result.sections[0]
    assert section.status.value == "ready_with_findings"
    assert len(section.findings) == 1
    assert section.section_draft.rendered_prose == bundle.sections[0].rendered_prose
    assert section.reviewer_status == {
        "scientific": "malformed",
        "expression": "valid",
    }
    assert result.model_status == "partial"


def test_hard_blockers_aggregate_and_section_warnings_are_local() -> None:
    def bad_second(request):
        if request["section"]["section_id"].endswith("-section-02"):
            response = _writer_response(request, paragraphs=1)
            response["paragraphs"][0]["claim_aliases"] = ["C99_bogus"]
            return response
        return _writer_response(request, paragraphs=1)

    plan, ledger, architecture, bundle, story_id = _fixture(
        two_sections=True, writer_builder=bad_second
    )
    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(RuntimeError("sci down")),
        expression_reviewer=_reviewer(_empty_response),
    )
    assert result.status == "partial"
    assert any("not publishable" in item for item in result.hard_blockers)
    assert any("not publishable" in item for item in result.sections[1].hard_blockers)
    assert any(
        "scientific reviewer unavailable" in item
        for item in result.sections[0].warnings
    )


def test_persistence_versioned_distinct_results_same_review_id(tmp_path) -> None:
    plan, ledger, architecture, bundle, story_id = _fixture()
    graph = ExperimentGraph(tmp_path / "graph.sqlite", "run-1")
    memory_a = ArticleMemoryStore(tmp_path / "a.sqlite")
    memory_b = ArticleMemoryStore(tmp_path / "b.sqlite")
    state = {"calls": 0}

    def sci(request):
        state["calls"] += 1
        return {
            "findings": [_finding(request, reason=f"version {state['calls']}")],
            "advice": [],
        }

    first = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(sci),
        expression_reviewer=_reviewer(_empty_response),
        memory_store=memory_a,
        graph=graph,
        run_id="run-1",
    )
    second = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(sci),
        expression_reviewer=_reviewer(_empty_response),
        memory_store=memory_b,
        graph=graph,
        run_id="run-1",
    )
    assert first.review_id == second.review_id
    assert first.result_id != second.result_id
    assert graph.article_node(f"review-{first.result_id}")
    assert graph.article_node(f"review-{second.result_id}")
    assert f"review-{first.result_id}" in {
        item.memory_id for item in memory_a.run_memory_records()
    }
    assert f"review-{second.result_id}" in {
        item.memory_id for item in memory_b.run_memory_records()
    }


def test_scientific_only_review_can_resolve_finding() -> None:
    plan, ledger, architecture, bundle, story_id = _fixture()
    state = {"calls": 0}

    def sci(request):
        state["calls"] += 1
        if state["calls"] == 1:
            return {
                "findings": [_finding(request, suggested="Revise p1.")],
                "advice": [],
            }
        return {"findings": [], "advice": []}

    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(sci),
        expression_reviewer=None,
        author_reviser=_reviser(_revise_first),
    )
    section = result.sections[0]
    assert section.status.value == "ready"
    assert section.findings == []
    assert section.revisions and section.revisions[-1].progress is True
    assert section.reviewer_status == {
        "scientific": "valid",
        "expression": "unavailable",
    }
    assert result.model_status == "partial"


def test_mixed_roles_required_role_failure_blocks_correction() -> None:
    plan, ledger, architecture, bundle, story_id = _fixture()
    sci_state = {"calls": 0}
    expr_state = {"calls": 0}

    def sci(request):
        sci_state["calls"] += 1
        if sci_state["calls"] == 1:
            return {
                "findings": [_finding(request, suggested="Revise p1.")],
                "advice": [],
            }
        raise RuntimeError("scientific re-review down")

    def expr(request):
        expr_state["calls"] += 1
        if expr_state["calls"] == 1:
            return {
                "findings": [
                    _finding(
                        request,
                        kind="clarity",
                        reason="Unclear phrasing.",
                        suggested="Clarify p1.",
                    )
                ],
                "advice": [],
            }
        return {"findings": [], "advice": []}

    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(sci),
        expression_reviewer=_reviewer(expr),
        author_reviser=_reviser(_revise_first),
    )
    section = result.sections[0]
    assert section.status.value == "ready_with_findings"
    assert len(section.findings) == 2
    assert section.revisions and section.revisions[-1].progress is False
    assert section.section_draft.rendered_prose == bundle.sections[0].rendered_prose
    assert section.reviewer_status == {
        "scientific": "unavailable",
        "expression": "valid",
    }
    assert result.model_status == "partial"


def test_section_order_must_match_story() -> None:
    from optomind_optics.harness.article_writing import compute_bundle_id

    plan, ledger, architecture, bundle, story_id = _fixture(two_sections=True)
    reversed_sections = [bundle.sections[1], bundle.sections[0]]
    reversed_ledger = [
        entry for section in reversed_sections for entry in section.source_ledger
    ]
    tampered = bundle.model_copy(
        update={
            "sections": reversed_sections,
            "source_ledger": reversed_ledger,
        }
    )
    new_bundle_id = compute_bundle_id(
        plan.plan_id,
        ledger.ledger_id,
        architecture.architecture_id,
        story_id,
        reversed_sections,
    )
    tampered = tampered.model_copy(update={"bundle_id": new_bundle_id})
    captured: list[dict] = []
    result = build_article_review(
        plan,
        ledger,
        architecture,
        tampered,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(_empty_response, captured=captured),
        expression_reviewer=_reviewer(_empty_response, captured=captured),
    )
    assert any("section order" in item for item in result.hard_blockers)
    assert captured == []


def test_review_request_includes_claim_fact_artifacts_for_figureless_section() -> None:
    plan, ledger, architecture, bundle, story_id = _fixture(two_sections=True)
    captured: list[dict] = []
    build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(_empty_response, captured=captured),
        expression_reviewer=_reviewer(_empty_response),
    )
    requests_by_section = {item["section"]["section_id"]: item for item in captured}
    section_2 = requests_by_section["story-01-section-02"]
    assert all(item["figure_aliases"] == [] for item in section_2["paragraphs"])
    artifact_ids = {item["artifact_id"] for item in section_2["artifacts"]}
    assert "FINAL_RESULT.json" in artifact_ids
    assert section_2["values"] == []
    section_1 = requests_by_section["story-01-section-01"]
    assert "FINAL_RESULT.json" in {
        item["artifact_id"] for item in section_1["artifacts"]
    }
    assert any(item["alias"].startswith("V") for item in section_1["values"])


def test_cross_section_claim_alias_not_resolved() -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(
        plan, ledger, story_draft=_split_two_section_story(ledger)
    )
    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(_writer_response),
    )
    draft_claim = [c for c in ledger.claims if c.status == ClaimStatus.draft][0]
    draft_alias = next(
        alias
        for alias, claim_id in bundle.claim_alias_map.items()
        if claim_id == draft_claim.claim_id
    )

    def sci(request):
        if request["section"]["section_id"].endswith("-section-01"):
            return {
                "findings": [_finding(request, claim_aliases=[draft_alias])],
                "advice": [],
            }
        return {"findings": [], "advice": []}

    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(sci),
        expression_reviewer=_reviewer(_empty_response),
    )
    finding = result.sections[0].findings[0]
    assert finding.claim_aliases == []
    assert finding.claim_ids == []
    assert any("dropped non-section claim aliases" in item for item in result.warnings)


def test_finding_span_absent_is_cleared_and_valid_span_kept() -> None:
    plan, ledger, architecture, bundle, story_id = _fixture()
    tokenized = bundle.sections[0].paragraphs[0].text_with_value_tokens
    valid_span = tokenized[:20]

    def sci(request):
        return {
            "findings": [
                _finding(request, span="absent span text"),
                _finding(
                    request,
                    span=valid_span,
                    kind="contradiction",
                    reason="valid span reason",
                ),
            ],
            "advice": [],
        }

    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(sci),
        expression_reviewer=_reviewer(_empty_response),
    )
    findings = result.sections[0].findings
    assert findings[0].span == ""
    assert findings[1].span == valid_span
    assert any("span cleared" in item for item in result.warnings)


def test_duplicate_findings_deduplicated() -> None:
    plan, ledger, architecture, bundle, story_id = _fixture()

    def sci(request):
        finding = _finding(request, reason="same reason")
        return {
            "findings": [finding, dict(finding)],
            "advice": [],
        }

    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(sci),
        expression_reviewer=_reviewer(_empty_response),
    )
    section = result.sections[0]
    assert len(section.findings) == 1
    assert any("deduplicated" in item for item in result.warnings)


def _lineage_ledger():
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


def _claim_section_story_draft(ledger, claim_id):
    fact = next(
        fact for fact in ledger.facts if fact.metadata.get("claim_id") == claim_id
    )
    figure = {
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
                "selected_fields": ["R_mean"],
            }
        ],
        "limitations": ["solver only"],
    }
    return {
        "story_shape": "shape-claim-lineage",
        "central_thesis": "A story with a figure-free claim-lineage section.",
        "sections": [
            {
                "heading": "Results",
                "purpose": "present the verified result evidence",
                "key_messages": ["key"],
                "transitions": ["next"],
                "claim_bindings": [{"claim_id": claim_id, "role": "positive"}],
                "figure_roles": ["spectrum"],
            },
            {
                "heading": "Limitations figure-free",
                "purpose": "state the limitations",
                "key_messages": ["key"],
                "transitions": ["next"],
                "claim_bindings": [{"claim_id": claim_id, "role": "limitation"}],
                "figure_roles": [],
            },
        ],
        "figures": [figure],
        "omitted_claims": [],
        "exclusions": [],
        "strengths": [],
        "risks": [],
        "recommendation_rationale": "rationale",
        "recommendation_score": 0.6,
    }


def _direct_lineage_writer():
    def writer(request):
        if request["section"]["section_id"].endswith("-section-02"):
            claim_alias = request["section"]["claim_bindings"][0]["claim_alias"]
            return {
                "paragraphs": [
                    {
                        "text_with_value_tokens": (
                            "The verified mean reached [VALUE:V01_R_MEAN]."
                        ),
                        "claim_aliases": [claim_alias],
                        "figure_aliases": [],
                        "paragraph_role": "limitation",
                        "inference_kind": "bounded_inference",
                        "inference_note": "directly bound by the cited claim",
                    }
                ],
                "deferred_claim_aliases": [],
                "author_notes": [],
            }
        return _writer_response(request)

    return writer


def _lineage_fixture(value_records=None):
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
    records = value_records if value_records is not None else _value_records()
    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        records,
        section_writer=_writer(_direct_lineage_writer()),
    )
    story = next(item for item in architecture.stories if item.story_id == story_id)
    section = next(
        item
        for item in story.section_contracts
        if item.section_id == bundle.sections[1].section_id
    )
    errors: list[str] = []
    warnings: list[str] = []
    validated_story, fact_by_claim = validate_writing_inputs(
        plan,
        ledger,
        architecture,
        story_id,
        records,
        errors,
        warnings,
    )
    assert not errors
    assert validated_story is not None
    aliases = build_writing_alias_maps(validated_story, ledger, records, fact_by_claim)
    value_records_by_key = {
        (record.artifact_id, record.field): record for record in records
    }
    return (
        plan,
        ledger,
        architecture,
        story_id,
        validated_story,
        section,
        aliases,
        value_records_by_key,
        fact_by_claim,
        bundle,
    )


def test_review_accepts_direct_claim_lineage_value_without_figure() -> None:
    """Probe 032: exact claim-lineage token passes without a figure."""

    (
        plan,
        ledger,
        architecture,
        story_id,
        _,
        _,
        _,
        _,
        _,
        bundle,
    ) = _lineage_fixture()
    assert bundle.sections[1].status == "publishable"
    result = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(_empty_response),
        expression_reviewer=_reviewer(_empty_response),
        author_reviser=_reviser(_revise_first),
    )
    assert result.status in {"ready", "ready_with_findings"}
    assert not any(
        "not bound by any section figure or claim-value lineage" in item
        for item in result.sections[1].hard_blockers
    )
    assert not any(
        "not authorized by a cited claim" in item
        for item in result.sections[1].hard_blockers
    )


def test_review_payload_keeps_direct_lineage_value_labels() -> None:
    (
        plan,
        ledger,
        architecture,
        story_id,
        story,
        section,
        _,
        _,
        _,
        bundle,
    ) = _lineage_fixture()
    captured: list[dict] = []
    build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(_empty_response, captured=captured),
        expression_reviewer=_reviewer(_empty_response),
        author_reviser=_reviser(_revise_first),
    )
    request = next(
        item for item in captured if item["section"]["section_id"] == section.section_id
    )
    assert any(
        item["alias"] == "V01_R_MEAN" and item["label"] == "mean reflectance"
        for item in request["values"]
    )


def test_audit_rejects_direct_lineage_value_without_cited_claim() -> None:
    (
        plan,
        ledger,
        _,
        _,
        story,
        section,
        aliases,
        value_records_by_key,
        fact_by_claim,
        bundle,
    ) = _lineage_fixture()
    draft = bundle.sections[1]
    paragraph = draft.paragraphs[0]
    entry = draft.source_ledger[0]
    modified = draft.model_copy(
        update={
            "paragraphs": [
                paragraph.model_copy(
                    update={
                        "claim_ids": [],
                        "rendered_text": "The verified mean reached 0.004.",
                    }
                )
            ],
            "source_ledger": [
                entry.model_copy(update={"claim_ids": [], "fact_ids": []})
            ],
        }
    )
    _, hard = _audit_section(
        plan=plan,
        ledger=ledger,
        story=story,
        section=section,
        aliases=aliases,
        value_records_by_key=value_records_by_key,
        fact_by_claim=fact_by_claim,
        section_draft=modified,
        paragraph_ids_seen=set(),
    )
    assert any("not authorized by a cited claim" in item for item in hard)


def test_audit_rejects_same_artifact_wrong_field_direct_value() -> None:
    (
        plan,
        ledger,
        _,
        _,
        story,
        section,
        aliases,
        value_records_by_key,
        fact_by_claim,
        bundle,
    ) = _lineage_fixture()
    aliases = {
        **aliases,
        "value_alias_map": {
            **aliases["value_alias_map"],
            "V02_WORST_CASE": {
                "artifact_id": "FINAL_RESULT.json",
                "field": "worst_case",
                "label": "worst-case reflectance",
                "unit": "",
                "prose_safe": True,
            },
        },
    }
    value_records_by_key = {
        **value_records_by_key,
        ("FINAL_RESULT.json", "worst_case"): TrustedValueRecord(
            artifact_id="FINAL_RESULT.json",
            field="worst_case",
            rendered_value="0.02",
            source_hash="a" * 64,
            label="worst-case reflectance",
            prose_safe=True,
        ),
    }
    draft = bundle.sections[1]
    paragraph = draft.paragraphs[0]
    entry = draft.source_ledger[0]
    modified = draft.model_copy(
        update={
            "paragraphs": [
                paragraph.model_copy(
                    update={
                        "text_with_value_tokens": (
                            "Wrong field [VALUE:V02_WORST_CASE]."
                        ),
                        "rendered_text": "Wrong field 0.02.",
                        "value_token_ids": ["V02_WORST_CASE"],
                    }
                )
            ],
            "source_ledger": [
                entry.model_copy(update={"value_token_ids": ["V02_WORST_CASE"]})
            ],
        }
    )
    _, hard = _audit_section(
        plan=plan,
        ledger=ledger,
        story=story,
        section=section,
        aliases=aliases,
        value_records_by_key=value_records_by_key,
        fact_by_claim=fact_by_claim,
        section_draft=modified,
        paragraph_ids_seen=set(),
    )
    assert any(
        "not bound by any section figure or claim-value lineage" in item
        for item in hard
    )


def test_audit_rejects_direct_value_authorized_by_different_section_claim() -> None:
    (
        plan,
        ledger,
        _,
        _,
        story,
        section,
        aliases,
        value_records_by_key,
        fact_by_claim,
        bundle,
    ) = _lineage_fixture()
    draft = bundle.sections[1]
    paragraph = draft.paragraphs[0]
    entry = draft.source_ledger[0]
    other_claim = [
        claim for claim in ledger.claims if claim.status == ClaimStatus.draft
    ][0]
    other_claim_id = other_claim.claim_id
    modified = draft.model_copy(
        update={
            "paragraphs": [
                paragraph.model_copy(
                    update={
                        "claim_ids": [other_claim_id],
                        "rendered_text": ("The verified mean reached 0.004."),
                    }
                )
            ],
            "source_ledger": [entry.model_copy(update={"claim_ids": [other_claim_id]})],
        }
    )
    _, hard = _audit_section(
        plan=plan,
        ledger=ledger,
        story=story,
        section=section,
        aliases=aliases,
        value_records_by_key=value_records_by_key,
        fact_by_claim=fact_by_claim,
        section_draft=modified,
        paragraph_ids_seen=set(),
    )
    assert any("not authorized by a cited claim" in item for item in hard)
