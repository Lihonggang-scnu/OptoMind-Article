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
from optomind_optics.harness.article_manuscript import (
    ArticleManuscriptPackage,
    ArticleManuscriptIntegrityError,
    build_article_manuscript,
    compute_manuscript_body_id,
    validate_manuscript_package,
    write_manuscript_package,
    _render_body_markdown,
    _sanitize_heading,
    ManuscriptSection,
    ParagraphManuscriptSource,
)
from optomind_optics.harness.article_review import (
    DeterministicAuditFinding,
    ReviewerProviderResult,
    ReviewerFinding,
    ReviewSeverity,
    build_article_review,
    compute_review_result_id,
)
from optomind_optics.harness.article_writing import (
    TrustedValueRecord,
    WriterProviderResult,
    build_article_draft_bundle,
)
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


def _writer_response(request) -> dict:
    claim_aliases = [b["claim_alias"] for b in request["section"]["claim_bindings"]]
    figure_aliases = list(request["section"]["figure_aliases"])
    value_aliases = [item["alias"] for item in request["values"]]
    p1_text = (
        "The verified evidence supports the design claim within the declared "
        "scope."
    )
    if value_aliases:
        p1_text = f"{p1_text} [VALUE:{value_aliases[0]}]"
    return {
        "paragraphs": [
            {
                "text_with_value_tokens": p1_text,
                "claim_aliases": claim_aliases,
                "figure_aliases": figure_aliases,
                "paragraph_role": "result",
                "inference_kind": "bounded_inference",
                "inference_note": "local inference from the cited claim",
            },
            {
                "text_with_value_tokens": (
                    "Additional context paragraph without numbers."
                ),
                "claim_aliases": claim_aliases,
                "figure_aliases": [],
                "paragraph_role": "discussion",
                "inference_kind": "none_required",
                "inference_note": "",
            },
        ],
        "deferred_claim_aliases": [],
        "author_notes": [],
    }


def _writer(builder: Callable[[dict], dict]):
    def provider(request):
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
            writer_builder if writer_builder is not None else _writer_response
        ),
    )
    return plan, ledger, architecture, bundle, story_id


def _reviewer(response_or_builder, *, model: str = "fake-reviewer"):
    def provider(request):
        if callable(response_or_builder):
            raw = response_or_builder(request)
        else:
            raw = response_or_builder
        if isinstance(raw, Exception):
            raise raw
        return ReviewerProviderResult(
            response=raw,
            usage={"estimated_input_tokens": 7, "estimated_output_tokens": 9},
            provider_model=model,
        )

    return provider


def _empty_response(request) -> dict:
    return {"findings": [], "advice": []}


def _finding(request, *, reason: str = "The claim is overreaching.") -> dict:
    paragraph = request["paragraphs"][0]
    return {
        "paragraph_id": paragraph["paragraph_id"],
        "span": "",
        "severity": "minor",
        "kind": "overclaim",
        "reason": reason,
        "suggested_action": "Tighten the wording.",
        "claim_aliases": list(paragraph["claim_aliases"]),
    }


def _review(plan, ledger, architecture, bundle, story_id, *, with_finding=False):
    sci = (
        lambda request: {"findings": [_finding(request)], "advice": []}
        if with_finding
        else _empty_response
    )
    return build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(),
        scientific_reviewer=_reviewer(sci),
        expression_reviewer=_reviewer(_empty_response),
    )


def _fixture(two_sections: bool = False, with_finding: bool = False):
    plan, ledger = _ledger()
    plan, ledger, architecture, bundle, story_id = _bundle(
        plan, ledger, two_sections=two_sections
    )
    review = _review(
        plan, ledger, architecture, bundle, story_id, with_finding=with_finding
    )
    return plan, ledger, architecture, bundle, review, story_id


def test_clean_assembly_preserves_paragraphs_and_source_map() -> None:
    plan, ledger, architecture, bundle, review, story_id = _fixture()
    package = build_article_manuscript(
        plan,
        ledger,
        architecture,
        review,
        story_id,
        _value_records(),
    )
    assert package.errors == []
    assert package.body.status == "assembled"
    assert package.body_id == package.body.body_id
    section = package.body.sections[0]
    assert section.status == "ready"
    final_draft = review.sections[0].section_draft
    assert [p.rendered_text for p in section.paragraphs] == [
        p.rendered_text for p in final_draft.paragraphs
    ]
    assert len(package.source_map) == len(final_draft.paragraphs)
    first = package.source_map[0]
    assert first.rendered_text == final_draft.paragraphs[0].rendered_text
    assert first.claim_ids == final_draft.paragraphs[0].claim_ids
    assert first.value_token_ids == final_draft.paragraphs[0].value_token_ids
    assert first.roles == ["positive"]
    assert "0.004" in package.body_markdown
    assert "claim-" not in package.body_markdown
    assert "[VALUE:" not in package.body_markdown


def test_manuscript_source_map_preserves_direct_literature_binding() -> None:
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
    review = _review(plan, ledger, architecture, bundle, story_id)
    package = build_article_manuscript(
        plan, ledger, architecture, review, story_id, _value_records()
    )

    assert bundle.source_ledger[0].literature_evidence_ids == ["ev-prior"]
    assert package.source_map[0].literature_evidence_ids == ["ev-prior"]


def test_legacy_package_without_literature_field_keeps_identity() -> None:
    plan, ledger, architecture, bundle, review, story_id = _fixture()
    package = build_article_manuscript(
        plan, ledger, architecture, review, story_id, _value_records()
    )
    persisted = package.model_dump(mode="json")

    def remove_legacy_field(value):
        if isinstance(value, dict):
            return {
                key: remove_legacy_field(item)
                for key, item in value.items()
                if key != "literature_evidence_ids"
            }
        if isinstance(value, list):
            return [remove_legacy_field(item) for item in value]
        return value

    legacy_payload = remove_legacy_field(persisted)
    errors: list[str] = []
    warnings: list[str] = []
    validate_manuscript_package(
        legacy_payload,
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        errors,
        warnings,
    )
    assert errors == []


def test_nonempty_literature_binding_changes_body_identity() -> None:
    plan, ledger, architecture, bundle, review, story_id = _fixture()
    package = build_article_manuscript(
        plan, ledger, architecture, review, story_id, _value_records()
    )
    first = package.source_map[0]
    empty_identity = compute_manuscript_body_id(
        package.plan_id,
        package.ledger_id,
        package.architecture_id,
        package.review_id,
        package.result_id,
        package.story_id,
        package.body.sections,
        package.body.source_map,
        package.body.findings,
        package.body.blocked_handoff,
    )
    bound = first.model_copy(update={"literature_evidence_ids": ["ev-prior"]})
    section = package.body.sections[0].model_copy(
        update={"paragraphs": [bound]}
    )
    nonempty_identity = compute_manuscript_body_id(
        package.plan_id,
        package.ledger_id,
        package.architecture_id,
        package.review_id,
        package.result_id,
        package.story_id,
        [section],
        [bound],
        package.body.findings,
        package.body.blocked_handoff,
    )
    assert empty_identity == package.body_id
    assert nonempty_identity != empty_identity


def test_ready_with_findings_attaches_findings_to_target_paragraph() -> None:
    plan, ledger, architecture, bundle, review, story_id = _fixture(with_finding=True)
    package = build_article_manuscript(
        plan,
        ledger,
        architecture,
        review,
        story_id,
        _value_records(),
    )
    assert package.body.status == "assembled"
    section = package.body.sections[0]
    assert section.status == "ready_with_findings"
    finding = review.sections[0].findings[0]
    assert finding.finding_id in section.finding_ids
    assert finding.finding_id in package.source_map[0].finding_ids
    assert package.source_map[1].finding_ids == []
    assert [item.finding_id for item in package.findings] == [finding.finding_id]


def test_partial_assembly_keeps_siblings_and_blocked_handoff() -> None:
    def bad_second(request):
        if request["section"]["section_id"].endswith("-section-02"):
            response = _writer_response(request)
            response["paragraphs"][0]["claim_aliases"] = ["C99_bogus"]
            return response
        return _writer_response(request)

    plan, ledger = _ledger()
    plan, ledger, architecture, bundle, story_id = _bundle(
        plan, ledger, two_sections=True, writer_builder=bad_second
    )
    review = _review(plan, ledger, architecture, bundle, story_id)
    assert review.status == "partial"
    package = build_article_manuscript(
        plan,
        ledger,
        architecture,
        review,
        story_id,
        _value_records(),
    )
    assert package.body.status == "partial"
    assert [s.section_id for s in package.body.sections] == ["story-01-section-01"]
    assert package.body.blocked_handoff[0].section_id == "story-01-section-02"
    assert any(
        "not publishable" in item
        for item in package.body.blocked_handoff[0].hard_blockers
    )
    assert "## Results" in package.body_markdown
    assert "## Methods" not in package.body_markdown


def test_wrong_identity_and_reordered_sections_fail() -> None:
    plan, ledger, architecture, bundle, review, story_id = _fixture()

    wrong_plan = review.model_copy(update={"plan_id": "plan-wrong"})
    package = build_article_manuscript(
        plan,
        ledger,
        architecture,
        wrong_plan,
        story_id,
        _value_records(),
    )
    assert package.body.status == "blocked"
    assert any("plan_id" in item for item in package.errors)

    wrong_story = review.model_copy(update={"story_id": "story-99"})
    package = build_article_manuscript(
        plan,
        ledger,
        architecture,
        wrong_story,
        story_id,
        _value_records(),
    )
    assert any("story_id" in item for item in package.errors)

    plan2, ledger2, architecture2, bundle2, review2, story_id2 = _fixture(
        two_sections=True
    )
    reordered = review2.model_copy(
        update={
            "sections": [review2.sections[1], review2.sections[0]],
        }
    )
    package = build_article_manuscript(
        plan2,
        ledger2,
        architecture2,
        reordered,
        story_id2,
        _value_records(),
    )
    assert any("section order" in item for item in package.errors)


def test_tampered_aggregate_ledger_and_changed_paragraph_text_fail() -> None:
    plan, ledger, architecture, bundle, review, story_id = _fixture()

    tampered_ledger = review.final_source_ledger + [
        review.final_source_ledger[0].model_copy(
            update={"paragraph_id": "story-01-section-01-p99"}
        )
    ]
    tampered = review.model_copy(update={"final_source_ledger": tampered_ledger})
    package = build_article_manuscript(
        plan,
        ledger,
        architecture,
        tampered,
        story_id,
        _value_records(),
    )
    assert any(
        "final_source_ledger" in item for item in package.errors
    )

    reviewed_section = review.sections[0]
    draft = reviewed_section.section_draft
    paragraphs = [
        item.model_copy(update={"rendered_text": item.rendered_text + " tampered"})
        for item in draft.paragraphs
    ]
    tampered_draft = draft.model_copy(update={"paragraphs": paragraphs})
    tampered_section = reviewed_section.model_copy(
        update={"section_draft": tampered_draft}
    )
    tampered_sections = [tampered_section]
    new_result_id = compute_review_result_id(
        review.review_id,
        tampered_sections,
        review.audit_findings,
        review.scientific_findings,
        review.expression_findings,
    )
    tampered_review = review.model_copy(
        update={
            "sections": tampered_sections,
            "result_id": new_result_id,
        }
    )
    package = build_article_manuscript(
        plan,
        ledger,
        architecture,
        tampered_review,
        story_id,
        _value_records(),
    )
    assert package.body.status == "blocked"
    assert any(
        "paragraphs mismatch" in item or "rendered text" in item
        for item in package.errors
    )


def test_deterministic_id_stable_and_content_sensitive() -> None:
    plan, ledger, architecture, bundle, review, story_id = _fixture()
    first = build_article_manuscript(
        plan, ledger, architecture, review, story_id, _value_records()
    )
    second = build_article_manuscript(
        plan, ledger, architecture, review, story_id, _value_records()
    )
    assert first.package_id == second.package_id
    assert first.body_id == second.body_id

    plan2, ledger2, architecture2, bundle2, review2, story_id2 = _fixture(
        with_finding=True
    )
    changed = build_article_manuscript(
        plan2, ledger2, architecture2, review2, story_id2, _value_records()
    )
    assert changed.package_id != first.package_id
    assert changed.body_id != first.body_id


def test_atomic_writing_idempotent_and_conflict_rejected(tmp_path) -> None:
    plan, ledger, architecture, bundle, review, story_id = _fixture()
    package = build_article_manuscript(
        plan, ledger, architecture, review, story_id, _value_records()
    )
    paths = write_manuscript_package(package, tmp_path)
    assert paths["body"].exists()
    assert paths["package"].exists()
    assert paths["source_map"].exists()
    original_body = paths["body"].read_text(encoding="utf-8")
    write_manuscript_package(package, tmp_path)
    assert paths["body"].read_text(encoding="utf-8") == original_body

    paths["body"].write_text(original_body + " conflicting", encoding="utf-8")
    with pytest.raises(ArticleManuscriptIntegrityError, match="conflicting"):
        write_manuscript_package(package, tmp_path)
    assert paths["body"].exists()
    assert paths["package"].exists()
    assert paths["source_map"].exists()
    assert list(tmp_path.iterdir())


def test_forged_status_rejected_even_with_recomputed_result_id() -> None:
    plan, ledger, architecture, bundle, review, story_id = _fixture()
    forged = review.model_copy(update={"status": "blocked"})
    new_result_id = compute_review_result_id(
        forged.review_id,
        forged.sections,
        forged.audit_findings,
        forged.scientific_findings,
        forged.expression_findings,
    )
    forged = forged.model_copy(update={"result_id": new_result_id})
    package = build_article_manuscript(
        plan,
        ledger,
        architecture,
        forged,
        story_id,
        _value_records(),
    )
    assert package.body.status == "blocked"
    assert any(
        "does not match derived status" in item for item in package.errors
    )


def test_forged_blocked_handoff_and_drafts_rejected() -> None:
    def bad_second(request):
        if request["section"]["section_id"].endswith("-section-02"):
            response = _writer_response(request)
            response["paragraphs"][0]["claim_aliases"] = ["C99_bogus"]
            return response
        return _writer_response(request)

    plan, ledger = _ledger()
    plan, ledger, architecture, bundle, story_id = _bundle(
        plan, ledger, two_sections=True, writer_builder=bad_second
    )
    review = _review(plan, ledger, architecture, bundle, story_id)
    blocked = review.sections[1]
    assert blocked.status.value == "blocked"
    forged_blocked = blocked.model_copy(
        update={
            "hard_blockers": list(blocked.hard_blockers) + ["forged blocker"],
            "audit_findings": list(blocked.audit_findings)
            + [
                DeterministicAuditFinding(
                    finding_id="audit-forged",
                    section_id=blocked.section_id,
                    kind="forged",
                    message="forged audit finding",
                )
            ],
            "original_section_draft": blocked.original_section_draft.model_copy(
                update={"errors": list(blocked.original_section_draft.errors) + ["x"]}
            ),
        }
    )
    forged_sections = [review.sections[0], forged_blocked]
    forged = review.model_copy(update={"sections": forged_sections})
    new_result_id = compute_review_result_id(
        forged.review_id,
        forged_sections,
        forged.audit_findings,
        forged.scientific_findings,
        forged.expression_findings,
    )
    forged = forged.model_copy(update={"result_id": new_result_id})
    package = build_article_manuscript(
        plan,
        ledger,
        architecture,
        forged,
        story_id,
        _value_records(),
    )
    assert package.body.status == "blocked"
    assert any(
        "do not match deterministic derivation" in item
        for item in package.errors
    )
    assert any(
        "original and final drafts differ" in item for item in package.errors
    )

    with_findings = blocked.model_copy(
        update={
            "findings": [
                ReviewerFinding(
                    finding_id="review-forged",
                    reviewer="scientific",
                    severity=ReviewSeverity.minor,
                    kind="forged",
                    paragraph_id="story-01-section-02-p01",
                    reason="forged",
                    suggested_action="",
                )
            ]
        }
    )
    forged_sections = [review.sections[0], with_findings]
    forged = review.model_copy(update={"sections": forged_sections})
    new_result_id = compute_review_result_id(
        forged.review_id,
        forged_sections,
        forged.audit_findings,
        forged.scientific_findings,
        forged.expression_findings,
    )
    forged = forged.model_copy(update={"result_id": new_result_id})
    package = build_article_manuscript(
        plan,
        ledger,
        architecture,
        forged,
        story_id,
        _value_records(),
    )
    assert any("carries soft findings" in item for item in package.errors)


def test_blocked_wrapper_story_id_inconsistency_fails() -> None:
    def bad_second(request):
        if request["section"]["section_id"].endswith("-section-02"):
            response = _writer_response(request)
            response["paragraphs"][0]["claim_aliases"] = ["C99_bogus"]
            return response
        return _writer_response(request)

    plan, ledger = _ledger()
    plan, ledger, architecture, bundle, story_id = _bundle(
        plan, ledger, two_sections=True, writer_builder=bad_second
    )
    review = _review(plan, ledger, architecture, bundle, story_id)
    blocked = review.sections[1].model_copy(update={"story_id": "story-99"})
    forged_sections = [review.sections[0], blocked]
    forged = review.model_copy(update={"sections": forged_sections})
    new_result_id = compute_review_result_id(
        forged.review_id,
        forged_sections,
        forged.audit_findings,
        forged.scientific_findings,
        forged.expression_findings,
    )
    forged = forged.model_copy(update={"result_id": new_result_id})
    package = build_article_manuscript(
        plan,
        ledger,
        architecture,
        forged,
        story_id,
        _value_records(),
    )
    assert any("wrapper story_id" in item for item in package.errors)


def test_headings_preserve_human_text_and_block_injection() -> None:
    assert _sanitize_heading("Methods: ?/? Response", 2) == "Methods: ?/? Response"
    assert _sanitize_heading("Methods: \u03b1/\u03bb Response", 2) == (
        "Methods: \u03b1/\u03bb Response"
    )
    sanitized = _sanitize_heading("Results\n## Injected\nMore", 1)
    assert "\n" not in sanitized
    assert not sanitized.startswith("#")
    section = ManuscriptSection(
        section_id="story-01-section-01",
        heading="Results\n## Injected\nMore",
        story_id="story-01",
        status="ready",
        paragraphs=[
            ParagraphManuscriptSource(
                paragraph_id="story-01-section-01-p01",
                section_id="story-01-section-01",
                rendered_text="Body paragraph.",
            )
        ],
        finding_ids=[],
    )
    markdown = _render_body_markdown([section])
    assert (
        sum(1 for line in markdown.splitlines() if line.startswith("## ")) == 1
    )
    assert "\n## Injected" not in markdown
    assert "Body paragraph." in markdown

    plan, ledger, architecture, bundle, review, story_id = _fixture()
    story_draft = _story_draft(ledger)
    story_draft["sections"][0]["heading"] = "Methods: \u03b1/\u03bb Response"
    architecture2, story_id2 = _architecture(plan, ledger, story_draft=story_draft)
    bundle2 = build_article_draft_bundle(
        plan,
        ledger,
        architecture2,
        story_id2,
        _value_records(),
        section_writer=_writer(_writer_response),
    )
    review2 = _review(plan, ledger, architecture2, bundle2, story_id2)
    package = build_article_manuscript(
        plan,
        ledger,
        architecture2,
        review2,
        story_id2,
        _value_records(),
    )
    assert "## Methods: \u03b1/\u03bb Response" in package.body_markdown
    assert "## methods" not in package.body_markdown


def test_forged_ready_section_audit_finding_rejected() -> None:
    plan, ledger, architecture, bundle, review, story_id = _fixture()
    section = review.sections[0]
    forged_finding = DeterministicAuditFinding(
        finding_id="audit-forged-ready",
        section_id=section.section_id,
        kind="forged",
        message="forged audit finding on a ready section",
    )
    forged_section = section.model_copy(
        update={
            "audit_findings": list(section.audit_findings) + [forged_finding]
        }
    )
    forged_sections = [forged_section]
    forged = review.model_copy(
        update={
            "sections": forged_sections,
            "audit_findings": list(review.audit_findings) + [forged_finding],
        }
    )
    new_result_id = compute_review_result_id(
        forged.review_id,
        forged_sections,
        forged.audit_findings,
        forged.scientific_findings,
        forged.expression_findings,
    )
    forged = forged.model_copy(update={"result_id": new_result_id})
    package = build_article_manuscript(
        plan,
        ledger,
        architecture,
        forged,
        story_id,
        _value_records(),
    )
    assert package.body.status == "blocked"
    assert any(
        "audit findings do not match deterministic derivation" in item
        for item in package.errors
    )


def test_validate_manuscript_package_repeated_fields_and_partition() -> None:
    from optomind_optics.harness.article_manuscript import (
        validate_manuscript_package,
    )

    plan, ledger, architecture, bundle, review, story_id = _fixture()
    package = build_article_manuscript(
        plan, ledger, architecture, review, story_id, _value_records()
    )
    errors: list[str] = []
    warnings: list[str] = []
    story = validate_manuscript_package(
        package,
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        errors,
        warnings,
    )
    assert errors == []
    assert story is not None

    tampered_warnings = package.model_copy(update={"warnings": ["extra"]})
    errors = []
    validate_manuscript_package(
        tampered_warnings,
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        errors,
        [],
    )
    assert any("warnings do not match" in item for item in errors)

    tampered_plan = package.model_copy(update={"plan_id": "plan-wrong"})
    errors = []
    validate_manuscript_package(
        tampered_plan,
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        errors,
        [],
    )
    assert any("plan_id does not match its embedded body" in item for item in errors)

    def bad_second(request):
        if request["section"]["section_id"].endswith("-section-02"):
            response = _writer_response(request)
            response["paragraphs"][0]["claim_aliases"] = ["C99_bogus"]
            return response
        return _writer_response(request)

    plan2, ledger2 = _ledger()
    plan2, ledger2, architecture2, bundle2, story_id2 = _bundle(
        plan2, ledger2, two_sections=True, writer_builder=bad_second
    )
    review2 = _review(plan2, ledger2, architecture2, bundle2, story_id2)
    package2 = build_article_manuscript(
        plan2, ledger2, architecture2, review2, story_id2, _value_records()
    )
    assert package2.body.status == "partial"
    body2 = package2.body.model_copy(update={"blocked_handoff": []})
    tampered_partition = package2.model_copy(update={"body": body2})
    errors = []
    validate_manuscript_package(
        tampered_partition,
        plan2,
        ledger2,
        architecture2,
        story_id2,
        _value_records(),
        errors,
        [],
    )
    assert any(
        "exact non-overlapping partition" in item for item in errors
    )

    duplicate_blocked = package2.body.blocked_handoff + [
        package2.body.blocked_handoff[0]
    ]
    body3 = package2.body.model_copy(
        update={
            "blocked_handoff": duplicate_blocked,
            "status": "blocked",
        }
    )
    tampered_duplicates = package2.model_copy(
        update={
            "body": body3,
            "blocked_handoff": duplicate_blocked,
        }
    )
    errors = []
    validate_manuscript_package(
        tampered_duplicates,
        plan2,
        ledger2,
        architecture2,
        story_id2,
        _value_records(),
        errors,
        [],
    )
    assert any("duplicate section IDs" in item for item in errors)
