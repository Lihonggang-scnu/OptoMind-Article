from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

import pytest

from optomind_optics.harness.article_architecture import (
    ArchitectureProviderResult,
    ArtifactDescriptor,
    build_article_architecture,
)
from optomind_optics.harness.article_citation_audit import (
    CitationAuditDecision,
    CitationAuditResult,
)
from optomind_optics.harness.article_claims import build_claim_ledger
from optomind_optics.harness.article_contracts import (
    ClaimStatus,
    ExperimentStatus,
    ObservationCard,
)
from optomind_optics.harness.article_director import ArticleDirector
from optomind_optics.harness.article_execution import ArticleExecutionResult
from optomind_optics.harness.article_feedback import ArticleFeedbackController
from optomind_optics.harness.article_manuscript import (
    ArticleManuscriptBody,
    ArticleManuscriptPackage,
    ManuscriptSection,
    ParagraphManuscriptSource,
    build_article_manuscript,
)
from optomind_optics.harness.article_literature import (
    LiteratureEvidenceIdentity,
    LiteratureSupplement,
)
from optomind_optics.harness.article_presentation import (
    ArticlePresentationIntegrityError,
    CitationPlacement,
    FrontMatter,
    PanelAsset,
    ProviderResult,
    ReferenceRecord,
    _build_citations,
    _build_citation_section_requests,
    _load_numeric_rows,
    _render_reader_paragraph,
    _render_reader_manuscript,
    _render_synthesized_diagram,
    _render_svg_plot,
    _verify_body_invariant,
    build_article_presentation,
    compute_presentation_package_id,
    validate_presentation_package,
    write_presentation_package,
)
from optomind_optics.harness.article_reproducibility import (
    ArticleReproducibilityPackage,
    ArtifactLineageRecord,
    PublicationBlocker,
    build_article_reproducibility,
    compute_reproducibility_package_id,
    validate_reproducibility_package,
)
from optomind_optics.harness.article_review import (
    ReviewerProviderResult,
    build_article_review,
)
from optomind_optics.harness.article_writing import (
    TrustedValueRecord,
    WriterProviderResult,
    build_article_draft_bundle,
)
from optomind_optics.harness.method_research import (
    MethodAllowedUse,
    MethodContentDepth,
    MethodEvidence,
    MethodResearchReport,
    MethodResearchStatus,
)
from optomind_optics.harness.problem_analyzer import (
    OpticalProblemAnalysis,
    ResearchIntent,
    TMMCompatibility,
)
from optomind_optics.harness.replay import ReplayArtifactCheck, ReplayManifest


TASK_BYTES = json.dumps({"task_hash": "task-1"}).encode()
TASK_SHA = hashlib.sha256(TASK_BYTES).hexdigest()
DATA_BYTES = json.dumps(
    {
        "run_id": "run-1",
        "data": [
            {"R_mean": 0.004, "worst_case": 0.02},
            {"R_mean": 0.005, "worst_case": 0.021},
        ],
    }
).encode()
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "YAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)
EVIDENCE_1 = MethodEvidence(
    evidence_id="ev-1",
    paper_id="paper-1",
    title="Broadband Antireflection Coatings",
    doi="10.1000/paper-one",
    year=2020,
    source_route="abstract",
    content_depth=MethodContentDepth.fulltext,
    text="A bounded abstract summary.",
    query_ids=["q1"],
    allowed_use=MethodAllowedUse.direct_fact,
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
        problem_id="problem-1",
        status=MethodResearchStatus.completed,
        evidence=[EVIDENCE_1],
    )


def _plan():
    result = ArticleDirector().plan(
        "Design a broadband AR coating over 450-700 nm.",
        _analysis(),
        _report(),
        force_mock=True,
    )
    assert result.status == "planned" and result.plan is not None
    plan = result.plan
    assert plan.evidence_identity
    return plan.model_copy(
        update={
            "hypotheses": [
                (
                    item.model_copy(update={"evidence_ids": ["ev-1"]})
                    if item.hypothesis_id == "hyp-01"
                    else item
                )
                for item in plan.hypotheses
            ]
        }
    )


def _ledger(plan=None):
    plan = plan or _plan()
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


def _minimal_manuscript(source_map: list[ParagraphManuscriptSource]):
    body = ArticleManuscriptBody(
        body_id="body-x",
        plan_id="p",
        ledger_id="l",
        architecture_id="a",
        review_id="r",
        result_id="rr",
        story_id="story-01",
        status="assembled",
        sections=[],
        blocked_handoff=[],
        source_map=source_map,
        findings=[],
        warnings=[],
        errors=[],
    )
    return ArticleManuscriptPackage(
        package_id="pkg-x",
        body_id="body-x",
        plan_id="p",
        ledger_id="l",
        architecture_id="a",
        review_id="r",
        result_id="rr",
        story_id="story-01",
        body_markdown="",
        body=body,
        source_map=source_map,
        findings=[],
        blocked_handoff=[],
        warnings=[],
        errors=[],
    )


def _plan_with_evidence(
    evidence: MethodEvidence | list[MethodEvidence],
    evidence_ids: tuple[str, ...] = ("ev-1",),
):
    evidence_list = evidence if isinstance(evidence, list) else [evidence]
    report = MethodResearchReport(
        problem_id="problem-1",
        status=MethodResearchStatus.completed,
        evidence=evidence_list,
    )
    result = ArticleDirector().plan(
        "Design a broadband AR coating over 450-700 nm.",
        _analysis(),
        report,
        force_mock=True,
    )
    assert result.status == "planned" and result.plan is not None
    plan = result.plan
    assert plan.evidence_identity
    return plan.model_copy(
        update={
            "hypotheses": [
                (
                    item.model_copy(update={"evidence_ids": list(evidence_ids)})
                    if item.hypothesis_id == "hyp-01"
                    else item
                )
                for item in plan.hypotheses
            ]
        }
    )


def _manifest(sha256: str) -> list[ArtifactDescriptor]:
    return [
        ArtifactDescriptor(
            artifact_id="FINAL_RESULT.json",
            path="FINAL_RESULT.json",
            fields=["R_mean", "worst_case"],
            artifact_type="simulation",
            media_type="application/json",
            content_summary="Verified solver spectrum for the baseline route.",
            field_descriptions={
                "R_mean": "mean reflectance over the declared band",
                "worst_case": "worst-case reflectance",
            },
            sha256=sha256,
            source_experiment_ids=["exp-1"],
            source_observation_ids=["obs-1"],
        )
    ]


def _value_records(sha256: str) -> list[TrustedValueRecord]:
    return [
        TrustedValueRecord(
            artifact_id="FINAL_RESULT.json",
            field="R_mean",
            rendered_value="0.004",
            source_hash=sha256,
            label="mean reflectance",
            prose_safe=True,
        )
    ]


def _story_draft(
    ledger,
    *,
    figure_kind: str = "quantitative",
    source_mode: str = "trusted_artifact",
) -> dict:
    positive = [
        c for c in ledger.claims if c.status == ClaimStatus.partially_supported
    ][0]
    fact = next(
        f for f in ledger.facts if f.metadata.get("claim_id") == positive.claim_id
    )
    effective_kind = figure_kind if source_mode == "trusted_artifact" else "conceptual"
    figure = {
        "role_key": "spectrum",
        "kind": effective_kind,
        "story_role": "spectral response",
        "panel_intents": ["panel"],
        "caption_intent": "verified spectrum",
        "claim_bindings": [{"claim_id": positive.claim_id, "role": "positive"}],
        "fact_ids": [fact.fact_id],
        "artifact_bindings": (
            [
                {
                    "artifact_id": "FINAL_RESULT.json",
                    "selected_fields": ["R_mean", "worst_case"],
                }
            ]
            if source_mode == "trusted_artifact"
            else []
        ),
        "limitations": ["solver only"],
    }
    binding = {"claim_id": positive.claim_id, "role": "positive"}
    return {
        "story_shape": "shape-a",
        "central_thesis": "An evidence-bound AR design story.",
        "sections": [
            {
                "heading": "Results",
                "purpose": "present the verified result evidence",
                "key_messages": ["key"],
                "transitions": ["next"],
                "claim_bindings": [binding],
                "figure_roles": ["spectrum"],
            }
        ],
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


def _architecture(plan, ledger, *, manifest, story_draft=None):
    result = build_article_architecture(
        plan,
        ledger,
        manifest,
        architecture_provider=_architecture_provider(
            story_draft if story_draft is not None else _story_draft(ledger)
        ),
    )
    assert result.validation_errors == []
    return result, result.stories[0].story_id


def _writer_response(request, *, with_value: bool = True) -> dict:
    claim_aliases = [b["claim_alias"] for b in request["section"]["claim_bindings"]]
    figure_aliases = list(request["section"]["figure_aliases"])
    value_aliases = [item["alias"] for item in request["values"]]
    p1_text = (
        "The verified evidence supports the design claim within the declared " "scope."
    )
    if with_value and value_aliases:
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
            }
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


def _reviewer(response: dict):
    def provider(request):
        return ReviewerProviderResult(
            response=response,
            usage={"estimated_input_tokens": 7, "estimated_output_tokens": 9},
            provider_model="fake-reviewer",
        )

    return provider


def _empty_response(request) -> dict:
    return {"findings": [], "advice": []}


def _execution_result(run_dir: Path) -> ArticleExecutionResult:
    observation = ObservationCard(
        observation_id="obs-1",
        experiment_id="exp-1",
        status=ExperimentStatus.physically_valid,
        metrics={"route_id": "baseline", "R_mean": 0.004},
        artifact_ids=["FINAL_RESULT.json"],
        hypothesis_updates=[],
        summary="observation",
    )
    return ArticleExecutionResult(
        request_id="req-obs-1",
        task_hash="task-1",
        run_dir=str(run_dir),
        observation=observation,
        receipt={},
        outcome="physically_valid",
    )


def _replay_manifest(
    source_sha: str,
    *,
    checks: list[ReplayArtifactCheck] | None = None,
) -> ReplayManifest:
    check_list = (
        checks
        if checks is not None
        else [
            ReplayArtifactCheck(
                relative_path="FINAL_RESULT.json",
                source_sha256=source_sha,
                replay_sha256=source_sha,
                matched=True,
                reason="ok",
            )
        ]
    )
    return ReplayManifest(
        source_run_id="run-1",
        replay_run_id="run-1-replay",
        source_task_sha256=TASK_SHA,
        replay_task_sha256=TASK_SHA,
        checks=tuple(check_list),
        matched_artifacts=sum(1 for item in check_list if item.matched),
        total_artifacts=len(check_list),
        success=True,
    )


def _chain(
    tmp_path: Path,
    *,
    figure_kind: str = "quantitative",
    source_mode: str = "trusted_artifact",
    replay_checks: list[ReplayArtifactCheck] | None = None,
    evidence_override: list[MethodEvidence] | None = None,
):
    plan, ledger = _ledger()
    use_values = source_mode == "trusted_artifact"
    sha = hashlib.sha256(DATA_BYTES).hexdigest()
    run_dir = tmp_path / "runs" / "run-1"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "FINAL_RESULT.json").write_bytes(DATA_BYTES)
    (run_dir / "TASK.json").write_bytes(TASK_BYTES)
    architecture, story_id = _architecture(
        plan,
        ledger,
        manifest=_manifest(sha),
        story_draft=_story_draft(
            ledger, figure_kind=figure_kind, source_mode=source_mode
        ),
    )
    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(sha) if use_values else [],
        section_writer=_writer(
            lambda request: _writer_response(request, with_value=use_values)
        ),
    )
    review = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(sha) if use_values else [],
        scientific_reviewer=_reviewer(_empty_response),
        expression_reviewer=_reviewer(_empty_response),
    )
    manuscript = build_article_manuscript(
        plan,
        ledger,
        architecture,
        review,
        story_id,
        _value_records(sha) if use_values else [],
    )
    reproducibility = build_article_reproducibility(
        plan,
        ledger,
        architecture,
        review,
        manuscript,
        story_id,
        _value_records(sha) if use_values else [],
        [_execution_result(run_dir)],
        tmp_path / "runs",
        replay_provider=lambda p: _replay_manifest(sha, checks=replay_checks),
    )
    values = _value_records(sha) if use_values else []
    evidence = evidence_override if evidence_override is not None else [EVIDENCE_1]
    return {
        "plan": plan,
        "ledger": ledger,
        "architecture": architecture,
        "review": review,
        "manuscript": manuscript,
        "reproducibility": reproducibility,
        "story_id": story_id,
        "sha": sha,
        "evidence": evidence,
        "run_dir": run_dir,
        "values": values,
    }


def _provider(response_or_builder: Any, *, model: str = "fake-provider"):
    def provider(request):
        raw = (
            response_or_builder(request)
            if callable(response_or_builder)
            else response_or_builder
        )
        return ProviderResult(
            response=raw,
            usage={"estimated_input_tokens": 8, "estimated_output_tokens": 10},
            provider_model=model,
        )

    return provider


def _citation_response(request) -> dict:
    placements = []
    for paragraph in request["paragraphs"]:
        for alias in [item["reference_alias"] for item in request["references"]]:
            placements.append(
                {
                    "paragraph_id": paragraph["paragraph_id"],
                    "reference_alias": alias,
                    "sentence_position": 0,
                }
            )
    return {"placements": placements, "advice": []}


def _front_matter_response(request) -> dict:
    paragraph_ids = [
        item["paragraph_id"]
        for section in request["sections"]
        for item in section["paragraphs"]
    ]
    return {
        "title": "Broadband AR Coating Design",
        "abstract_sentences": [
            {
                "sentence": "A broadband antireflection coating design is presented.",
                "paragraph_aliases": paragraph_ids[:1],
            }
        ],
        "keywords": ["antireflection", "broadband"],
    }


def _strip_markers(text: str) -> str:
    import re

    return re.sub(r"\[REF:[A-Za-z0-9_]+\]", "", text)


def _biblio():
    return {
        "paper-1": {"authors": ["A. Author"], "venue": "J. Optics"},
    }


def test_full_citation_chain_and_verified_figure(tmp_path) -> None:
    ctx = _chain(tmp_path)
    package = build_article_presentation(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        ctx["story_id"],
        ctx["values"],
        ctx["evidence"],
        [ctx["run_dir"]],
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(_front_matter_response),
        bibliographic_metadata=_biblio(),
    )
    assert package.status == "ready"
    assert len(package.citations) == 1
    citation = package.citations[0]
    assert citation.paper_id == "paper-1"
    assert citation.hypothesis_id == "hyp-01"
    assert citation.support_semantics == "direct_fact"
    assert len(package.references) == 1
    assert package.front_matter is not None
    assert package.front_matter.title == "Broadband AR Coating Design"
    assert "# Broadband AR Coating Design" in package.reader_markdown
    assert "**Abstract.**" in package.reader_markdown
    assert "**Keywords:**" in package.reader_markdown
    assert "## References" in package.reader_markdown
    assert "[REF01_" in package.reader_markdown
    assert package.visuals
    assert package.visuals[0].provenance == "verified"
    original = ctx["manuscript"].source_map[0].rendered_text
    assert original in _strip_markers(package.reader_markdown)


def test_exact_paragraph_restoration_across_whitespace(tmp_path) -> None:
    from optomind_optics.harness.article_presentation import (
        CitationPlacement,
        _render_reader_paragraph,
    )

    original = (
        "The verified evidence supports the claim.\n\n"
        "  Extra spacing after punctuation!  e.g. 0.004\n"
        "and 450-700 nm stay intact.  "
    )
    placements = [
        CitationPlacement(
            placement_id="place-1",
            paragraph_id="s",
            reference_alias="REF01_x",
            sentence_position=0,
            marker="[REF:REF01_x]",
        )
    ]
    rendered = _render_reader_paragraph("s", original, placements)
    assert _strip_markers(rendered) == original


def test_literature_supplement_extends_legacy_plan_identity(tmp_path) -> None:
    ctx = _chain(tmp_path)
    original_manifest = ctx["plan"].evidence_identity[0]
    legacy_plan = ctx["plan"].model_copy(update={"evidence_identity": []})
    supplement = LiteratureSupplement(
        source_pipeline_result_id="pipeline-result-1",
        old_director_plan_id=legacy_plan.plan_id,
        new_plan_id="plan-literature-supplement",
        report_identity="problem-1",
        evidence_count=1,
        evidence_identity=[
            LiteratureEvidenceIdentity.model_validate(
                original_manifest.model_dump(mode="json")
            )
        ],
        evidence_aliases={"E01": EVIDENCE_1.evidence_id},
        report_sha256="a" * 64,
        supplement_sha256="b" * 64,
        metadata_sha256="c" * 64,
    )

    package = build_article_presentation(
        legacy_plan,
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        ctx["story_id"],
        ctx["values"],
        ctx["evidence"],
        [ctx["run_dir"]],
        bibliographic_metadata=_biblio(),
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(_front_matter_response),
        literature_supplement=supplement,
    )

    assert package.status == "ready"
    assert package.citations
    assert package.literature_supplement_id.startswith("literature-")
    assert validate_presentation_package(package)

    wrong_supplement = supplement.model_copy(
        update={"old_director_plan_id": "plan-other"}
    )
    blocked = build_article_presentation(
        legacy_plan,
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        ctx["story_id"],
        ctx["values"],
        ctx["evidence"],
        [ctx["run_dir"]],
        literature_supplement=wrong_supplement,
    )
    assert blocked.status == "blocked"
    assert any("old_director_plan_id" in item for item in blocked.errors)


def test_presentation_integrates_citation_auditor_and_persists_result(
    tmp_path,
) -> None:
    ctx = _chain(tmp_path)

    def auditor(package, manuscript, evidence_by_id):
        del manuscript, evidence_by_id
        placement = package.placements[0]
        return CitationAuditResult(
            audit_id="audit-mainline",
            source_presentation_id=package.package_id,
            decisions=[
                CitationAuditDecision(
                    paragraph_id=placement.paragraph_id,
                    reference_alias=placement.reference_alias,
                    action="drop",
                    sentence_position=None,
                    reason="test audit drop",
                )
            ],
        )

    output = tmp_path / "audited"
    package = build_article_presentation(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        ctx["story_id"],
        ctx["values"],
        ctx["evidence"],
        [ctx["run_dir"]],
        bibliographic_metadata=_biblio(),
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(_front_matter_response),
        citation_auditor=auditor,
        output_dir=output,
    )
    assert package.citations == []
    assert package.references == []
    assert package.placements == []
    assert (output / "ARTICLE_CITATION_AUDIT.json").is_file()
    assert validate_presentation_package(package)


def test_evidence_id_reuse_with_changed_paper_rejected(tmp_path) -> None:
    swapped = EVIDENCE_1.model_copy(
        update={"paper_id": "paper-evil", "text": "A different summary."}
    )
    ctx = _chain(tmp_path, evidence_override=[swapped])
    package = build_article_presentation(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        ctx["story_id"],
        ctx["values"],
        ctx["evidence"],
        [ctx["run_dir"]],
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(_front_matter_response),
        bibliographic_metadata=_biblio(),
    )
    assert package.status == "blocked"
    assert any(item.kind == "evidence_identity_mismatch" for item in package.blockers)


def test_observation_id_never_becomes_paper_reference(tmp_path) -> None:
    collision = EVIDENCE_1.model_copy(
        update={"paper_id": "obs-1", "doi": "10.1000/obs-collision"}
    )
    plan = _plan_with_evidence(collision)
    plan, ledger = _ledger(plan)
    claim = ledger.claims[0]
    paragraph = ParagraphManuscriptSource(
        paragraph_id="story-01-section-01-p01",
        section_id="story-01-section-01",
        rendered_text="x",
        claim_ids=[claim.claim_id],
    )
    manuscript = _minimal_manuscript([paragraph])
    blockers: list[Any] = []
    warnings: list[str] = []
    citations, references, _ = _build_citations(
        plan=plan,
        ledger=ledger,
        manuscript=manuscript,
        evidence_by_id={"ev-1": collision},
        bibliographic_metadata={},
        blockers=blockers,
        warnings=warnings,
    )
    assert blockers == []
    assert len(citations) == 1
    assert citations[0].paper_id == "obs-1"
    assert citations[0].evidence_id == "ev-1"
    assert references[0].evidence_ids == ["ev-1"]


def test_discovery_only_evidence_excluded(tmp_path) -> None:
    discovery = EVIDENCE_1.model_copy(
        update={
            "allowed_use": MethodAllowedUse.discovery,
            "content_depth": MethodContentDepth.metadata,
            "doi": "",
            "year": None,
        }
    )
    plan = _plan_with_evidence(discovery)
    plan, ledger = _ledger(plan)
    claim = ledger.claims[0]
    paragraph = ParagraphManuscriptSource(
        paragraph_id="story-01-section-01-p01",
        section_id="story-01-section-01",
        rendered_text="x",
        claim_ids=[claim.claim_id],
    )
    manuscript = _minimal_manuscript([paragraph])
    blockers: list[Any] = []
    warnings: list[str] = []
    citations, references, _ = _build_citations(
        plan=plan,
        ledger=ledger,
        manuscript=manuscript,
        evidence_by_id={"ev-1": discovery},
        bibliographic_metadata={},
        blockers=blockers,
        warnings=warnings,
    )
    assert citations == []
    assert references == []
    assert any("discovery-only" in item for item in warnings)


def test_background_paragraph_direct_literature_binding_creates_citation() -> None:
    plan = _plan_with_evidence(EVIDENCE_1, ())
    plan, ledger = _ledger(plan)
    paragraph = ParagraphManuscriptSource(
        paragraph_id="story-01-section-01-p01",
        section_id="story-01-section-01",
        rendered_text="Prior work establishes the background.",
        literature_evidence_ids=["ev-1"],
    )
    blockers: list[Any] = []
    warnings: list[str] = []
    citations, references, placements = _build_citations(
        plan=plan,
        ledger=ledger,
        manuscript=_minimal_manuscript([paragraph]),
        evidence_by_id={"ev-1": EVIDENCE_1},
        bibliographic_metadata=_biblio(),
        blockers=blockers,
        warnings=warnings,
    )

    assert blockers == []
    assert len(citations) == 1
    assert citations[0].claim_id == ""
    assert citations[0].hypothesis_id == ""
    assert references[0].claim_ids == []
    assert placements[paragraph.paragraph_id] == [citations[0].reference_alias]


def test_trusted_metadata_fills_missing_year_and_url_locator() -> None:
    sparse = EVIDENCE_1.model_copy(update={"doi": "", "year": None})
    plan = _plan_with_evidence(sparse, ())
    plan, ledger = _ledger(plan)
    paragraph = ParagraphManuscriptSource(
        paragraph_id="story-01-section-01-p01",
        section_id="story-01-section-01",
        rendered_text="Prior work establishes the method background.",
        literature_evidence_ids=["ev-1"],
    )
    blockers: list[Any] = []
    warnings: list[str] = []
    citations, references, _ = _build_citations(
        plan=plan,
        ledger=ledger,
        manuscript=_minimal_manuscript([paragraph]),
        evidence_by_id={"ev-1": sparse},
        bibliographic_metadata={
            "paper-1": {
                "authors": ["A. Author"],
                "year": 2024,
                "url": "https://www.semanticscholar.org/paper/paper-1",
            }
        },
        blockers=blockers,
        warnings=warnings,
    )

    assert blockers == []
    assert citations[0].year == 2024
    assert citations[0].metadata_complete is True
    assert references[0].year == 2024
    assert references[0].url.endswith("/paper/paper-1")
    assert references[0].metadata_incomplete_fields == []


def test_trusted_metadata_cannot_override_nonempty_evidence_year() -> None:
    plan = _plan_with_evidence(EVIDENCE_1, ())
    plan, ledger = _ledger(plan)
    paragraph = ParagraphManuscriptSource(
        paragraph_id="story-01-section-01-p01",
        section_id="story-01-section-01",
        rendered_text="Prior work establishes the method background.",
        literature_evidence_ids=["ev-1"],
    )
    blockers: list[Any] = []
    warnings: list[str] = []
    _build_citations(
        plan=plan,
        ledger=ledger,
        manuscript=_minimal_manuscript([paragraph]),
        evidence_by_id={"ev-1": EVIDENCE_1},
        bibliographic_metadata={
            "paper-1": {
                "authors": ["A. Author"],
                "year": 2024,
                "url": "https://www.semanticscholar.org/paper/paper-1",
            }
        },
        blockers=blockers,
        warnings=warnings,
    )

    assert any(item.kind == "conflicting_paper_identity" for item in blockers)


def test_direct_discovery_only_literature_binding_is_not_cited() -> None:
    discovery = EVIDENCE_1.model_copy(
        update={"allowed_use": MethodAllowedUse.discovery}
    )
    plan = _plan_with_evidence(discovery, ())
    plan, ledger = _ledger(plan)
    paragraph = ParagraphManuscriptSource(
        paragraph_id="story-01-section-01-p01",
        section_id="story-01-section-01",
        rendered_text="Discovery context.",
        literature_evidence_ids=["ev-1"],
    )
    blockers: list[Any] = []
    warnings: list[str] = []
    citations, references, _ = _build_citations(
        plan=plan,
        ledger=ledger,
        manuscript=_minimal_manuscript([paragraph]),
        evidence_by_id={"ev-1": discovery},
        bibliographic_metadata={},
        blockers=blockers,
        warnings=warnings,
    )

    assert citations == []
    assert references == []
    assert any("discovery-only" in item for item in warnings)


def test_missing_expected_evidence_blocks(tmp_path) -> None:
    ctx = _chain(tmp_path)
    tampered_plan = ctx["plan"].model_copy(
        update={
            "hypotheses": [
                (
                    item.model_copy(update={"evidence_ids": ["ev-missing"]})
                    if item.hypothesis_id == "hyp-01"
                    else item
                )
                for item in ctx["plan"].hypotheses
            ]
        }
    )
    package = build_article_presentation(
        tampered_plan,
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        ctx["story_id"],
        ctx["values"],
        ctx["evidence"],
        [ctx["run_dir"]],
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(_front_matter_response),
        bibliographic_metadata=_biblio(),
    )
    assert package.status == "blocked"
    assert any(item.kind == "missing_expected_evidence" for item in package.blockers)


def test_conflicting_paper_identity_blocks(tmp_path) -> None:
    conflicting = MethodEvidence(
        evidence_id="ev-2",
        paper_id="paper-1",
        title="A Completely Different Paper",
        doi="10.1000/two",
        year=2021,
        source_route="abstract",
        content_depth=MethodContentDepth.fulltext,
        text="Different text.",
        query_ids=["q1"],
        allowed_use=MethodAllowedUse.direct_fact,
    )
    ctx = _chain(tmp_path, evidence_override=[EVIDENCE_1, conflicting])
    package = build_article_presentation(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        ctx["story_id"],
        ctx["values"],
        ctx["evidence"],
        [ctx["run_dir"]],
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(_front_matter_response),
    )
    assert package.status == "blocked"
    assert any(item.kind == "conflicting_paper_identity" for item in package.blockers)


def test_bad_advisory_placements_fallback(tmp_path) -> None:
    ctx = _chain(tmp_path)

    def bad_placement(request):
        return {
            "placements": [
                {
                    "paragraph_id": request["paragraphs"][0]["paragraph_id"],
                    "reference_alias": "REF99_bogus",
                    "sentence_position": 0,
                }
            ],
            "advice": [],
        }

    package = build_article_presentation(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        ctx["story_id"],
        ctx["values"],
        ctx["evidence"],
        [ctx["run_dir"]],
        citation_provider=_provider(bad_placement),
        front_matter_provider=_provider(_front_matter_response),
    )
    assert package.status == "ready_with_findings"
    assert package.blockers == []
    assert any("advisory response rejected" in item for item in package.warnings)
    original = ctx["manuscript"].source_map[0].rendered_text
    assert original in _strip_markers(package.reader_markdown)


def test_abstract_supported_number_passes_and_invented_falls_back(
    tmp_path,
) -> None:
    ctx = _chain(tmp_path)

    def supported(request):
        pid = request["sections"][0]["paragraphs"][0]["paragraph_id"]
        return {
            "title": "Title",
            "abstract_sentences": [
                {
                    "sentence": "Reflectance reached 0.004 in the baseline run.",
                    "paragraph_aliases": [pid],
                }
            ],
            "keywords": [],
        }

    package = build_article_presentation(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        ctx["story_id"],
        ctx["values"],
        ctx["evidence"],
        [ctx["run_dir"]],
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(supported),
        bibliographic_metadata=_biblio(),
    )
    assert package.status == "ready"
    assert package.front_matter is not None and not package.front_matter.fallback

    def invented(request):
        pid = request["sections"][0]["paragraphs"][0]["paragraph_id"]
        return {
            "title": "Title",
            "abstract_sentences": [
                {
                    "sentence": "Reflectance reached 0.123 in the baseline run.",
                    "paragraph_aliases": [pid],
                }
            ],
            "keywords": [],
        }

    package = build_article_presentation(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        ctx["story_id"],
        ctx["values"],
        ctx["evidence"],
        [ctx["run_dir"]],
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(invented),
        bibliographic_metadata=_biblio(),
    )
    assert package.status == "ready_with_findings"
    assert package.blockers == []
    assert package.front_matter is not None and package.front_matter.fallback
    assert "0.123" not in package.reader_markdown


def test_doi_dedupe_retains_bindings_and_completeness(tmp_path) -> None:
    second = MethodEvidence(
        evidence_id="ev-2",
        paper_id="paper-1b",
        title="Broadband Antireflection Coatings",
        doi="10.1000/PAPER-ONE",
        year=2020,
        source_route="abstract",
        content_depth=MethodContentDepth.fulltext,
        text="A bounded abstract summary.",
        query_ids=["q1"],
        allowed_use=MethodAllowedUse.method_guidance,
    )
    plan = _plan_with_evidence([EVIDENCE_1, second], ("ev-1", "ev-2"))
    plan, ledger = _ledger(plan)
    claim = ledger.claims[0]
    paragraph = ParagraphManuscriptSource(
        paragraph_id="story-01-section-01-p01",
        section_id="story-01-section-01",
        rendered_text="x",
        claim_ids=[claim.claim_id],
    )
    manuscript = _minimal_manuscript([paragraph])
    blockers: list[Any] = []
    warnings: list[str] = []
    citations, references, _ = _build_citations(
        plan=plan,
        ledger=ledger,
        manuscript=manuscript,
        evidence_by_id={"ev-1": EVIDENCE_1, "ev-2": second},
        bibliographic_metadata={
            **_biblio(),
            "paper-1b": {"authors": ["B. Author"], "venue": "J. Optics"},
        },
        blockers=blockers,
        warnings=warnings,
    )
    assert blockers == []
    assert len(references) == 1
    reference = references[0]
    assert set(reference.paper_ids) == {"paper-1", "paper-1b"}
    assert reference.metadata_complete


def test_forged_ready_reproducibility_rejected(tmp_path) -> None:
    ctx = _chain(tmp_path)
    forged = ctx["reproducibility"].model_copy(
        update={
            "status": "ready",
            "blockers": [
                PublicationBlocker(
                    blocker_id="blocker-forged",
                    kind="forged",
                    message="forged blocker",
                )
            ],
            "replay_records": [
                record.model_copy(update={"status": "failed"})
                for record in ctx["reproducibility"].replay_records
            ],
        }
    )
    new_id = compute_reproducibility_package_id(
        plan_id=forged.plan_id,
        ledger_id=forged.ledger_id,
        architecture_id=forged.architecture_id,
        review_id=forged.review_id,
        result_id=forged.result_id,
        manuscript_body_id=forged.manuscript_body_id,
        story_id=forged.story_id,
        status=forged.status,
        critical_experiments=forged.critical_experiments,
        replay_records=forged.replay_records,
        lineage=forged.lineage,
        appendix=forged.appendix,
        blockers=forged.blockers,
        warnings=forged.warnings,
        errors=forged.errors,
        attempts=forged.attempts,
    )
    forged = forged.model_copy(update={"package_id": new_id})
    package = build_article_presentation(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        forged,
        ctx["story_id"],
        ctx["values"],
        ctx["evidence"],
        [ctx["run_dir"]],
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(_front_matter_response),
        bibliographic_metadata=_biblio(),
    )
    assert package.status == "blocked"
    assert any("status" in item or "replay" in item for item in package.errors)


def test_missing_descriptor_sha_and_wrong_experiment_block(tmp_path) -> None:
    ctx = _chain(tmp_path)
    no_sha_descriptor = (
        ctx["architecture"].artifact_inventory[0].model_copy(update={"sha256": None})
    )
    architecture2, story_id2 = _architecture(
        ctx["plan"], ctx["ledger"], manifest=[no_sha_descriptor]
    )
    bundle2 = build_article_draft_bundle(
        ctx["plan"],
        ctx["ledger"],
        architecture2,
        story_id2,
        [],
        section_writer=_writer(
            lambda request: _writer_response(request, with_value=False)
        ),
    )
    review2 = build_article_review(
        ctx["plan"],
        ctx["ledger"],
        architecture2,
        bundle2,
        story_id2,
        [],
        scientific_reviewer=_reviewer(_empty_response),
        expression_reviewer=_reviewer(_empty_response),
    )
    manuscript2 = build_article_manuscript(
        ctx["plan"], ctx["ledger"], architecture2, review2, story_id2, []
    )
    reproducibility2 = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        architecture2,
        review2,
        manuscript2,
        story_id2,
        [],
        [_execution_result(ctx["run_dir"])],
        tmp_path / "runs",
        replay_provider=lambda p: _replay_manifest(ctx["sha"]),
    )
    package = build_article_presentation(
        ctx["plan"],
        ctx["ledger"],
        architecture2,
        review2,
        manuscript2,
        reproducibility2,
        story_id2,
        [],
        ctx["evidence"],
        [ctx["run_dir"]],
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(_front_matter_response),
        bibliographic_metadata=_biblio(),
    )
    assert package.status == "blocked"
    assert any(
        "missing" in item or "replay" in item or "sha256" in item
        for item in package.errors
    ) or any(item.kind == "missing_artifact_hash" for item in package.blockers)


def test_table_and_synthesized_variants(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    package = build_article_presentation(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        ctx["story_id"],
        ctx["values"],
        ctx["evidence"],
        [ctx["run_dir"]],
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(_front_matter_response),
    )
    assert package.visuals[0].asset_kind == "table"
    assert package.visuals[0].panels[0].asset_content.startswith("|")

    ctx2 = _chain(tmp_path, source_mode="synthesized_claims")
    package = build_article_presentation(
        ctx2["plan"],
        ctx2["ledger"],
        ctx2["architecture"],
        ctx2["review"],
        ctx2["manuscript"],
        ctx2["reproducibility"],
        ctx2["story_id"],
        ctx2["values"],
        ctx2["evidence"],
        [ctx2["run_dir"]],
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(_front_matter_response),
    )
    assert package.visuals[0].provenance == "synthesized"
    assert "not measured data" in package.visuals[0].block_markdown
    expected_visual_sha = hashlib.sha256(
        json.dumps(
            [
                panel.model_dump(mode="json")
                for panel in package.visuals[0].panels
            ],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    assert package.visuals[0].sha256 == expected_visual_sha
    assert package.visuals[0].visual_id == f"fig-{expected_visual_sha}"


def test_deterministic_ids_and_atomic_writer(tmp_path) -> None:
    ctx = _chain(tmp_path)
    kwargs = dict(
        plan=ctx["plan"],
        ledger=ctx["ledger"],
        architecture=ctx["architecture"],
        review=ctx["review"],
        manuscript=ctx["manuscript"],
        reproducibility=ctx["reproducibility"],
        selected_story_id=ctx["story_id"],
        value_records=ctx["values"],
        method_evidence=ctx["evidence"],
        artifact_roots=[ctx["run_dir"]],
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(_front_matter_response),
        bibliographic_metadata=_biblio(),
    )
    first = build_article_presentation(**kwargs)
    second = build_article_presentation(**kwargs)
    assert first.package_id == second.package_id
    out = tmp_path / "out"
    paths = write_presentation_package(
        first,
        out,
        plan=ctx["plan"],
        ledger=ctx["ledger"],
        architecture=ctx["architecture"],
        review=ctx["review"],
        manuscript=ctx["manuscript"],
        reproducibility=ctx["reproducibility"],
        selected_story_id=ctx["story_id"],
        value_records=ctx["values"],
    )
    assert paths["reader"].exists()
    assert paths["package"].exists()
    assert (out / "figures").exists()
    assert list((out / "figures").iterdir())
    write_presentation_package(
        first,
        out,
        plan=ctx["plan"],
        ledger=ctx["ledger"],
        architecture=ctx["architecture"],
        review=ctx["review"],
        manuscript=ctx["manuscript"],
        reproducibility=ctx["reproducibility"],
        selected_story_id=ctx["story_id"],
        value_records=ctx["values"],
    )
    reader = paths["reader"]
    reader.write_text(
        reader.read_text(encoding="utf-8") + " tampered", encoding="utf-8"
    )
    with pytest.raises(ArticlePresentationIntegrityError, match="conflicting"):
        write_presentation_package(
            first,
            out,
            plan=ctx["plan"],
            ledger=ctx["ledger"],
            architecture=ctx["architecture"],
            review=ctx["review"],
            manuscript=ctx["manuscript"],
            reproducibility=ctx["reproducibility"],
            selected_story_id=ctx["story_id"],
            value_records=ctx["values"],
        )
    assert paths["package"].exists()


def test_asset_conflict_and_final_hash(tmp_path) -> None:
    ctx = _chain(tmp_path)
    package = build_article_presentation(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        ctx["story_id"],
        ctx["values"],
        ctx["evidence"],
        [ctx["run_dir"]],
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(_front_matter_response),
        bibliographic_metadata=_biblio(),
    )
    out = tmp_path / "out2"
    write_presentation_package(
        package,
        out,
        plan=ctx["plan"],
        ledger=ctx["ledger"],
        architecture=ctx["architecture"],
        review=ctx["review"],
        manuscript=ctx["manuscript"],
        reproducibility=ctx["reproducibility"],
        selected_story_id=ctx["story_id"],
        value_records=ctx["values"],
    )
    asset = out / package.visuals[0].panels[0].asset_path
    assert asset.exists()
    written = asset.read_bytes()
    expected = package.visuals[0].panels[0].asset_content.encode("utf-8")
    assert hashlib.sha256(written).hexdigest() == package.visuals[0].panels[0].sha256
    asset.write_bytes(b"tampered")
    with pytest.raises(ArticlePresentationIntegrityError, match="conflicting"):
        write_presentation_package(
            package,
            out,
            plan=ctx["plan"],
            ledger=ctx["ledger"],
            architecture=ctx["architecture"],
            review=ctx["review"],
            manuscript=ctx["manuscript"],
            reproducibility=ctx["reproducibility"],
            selected_story_id=ctx["story_id"],
            value_records=ctx["values"],
        )


def test_xml_and_markdown_injection_escaped(tmp_path) -> None:
    ctx = _chain(tmp_path)

    def injected_front_matter(request):
        pid = request["sections"][0]["paragraphs"][0]["paragraph_id"]
        return {
            "title": "Title <script>alert(1)</script>",
            "abstract_sentences": [
                {
                    "sentence": "A safe sentence <b>bold</b> with | pipe.",
                    "paragraph_aliases": [pid],
                }
            ],
            "keywords": [],
        }

    package = build_article_presentation(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        ctx["story_id"],
        ctx["values"],
        ctx["evidence"],
        [ctx["run_dir"]],
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(injected_front_matter),
        bibliographic_metadata=_biblio(),
    )
    assert package.status == "ready"
    svg_panel = next(
        panel
        for visual in package.visuals
        for panel in visual.panels
        if panel.media_type == "image/svg+xml"
    )
    assert "<script>" not in svg_panel.asset_content


def test_late_visual_failure_preserves_safe_partial(tmp_path) -> None:
    ctx = _chain(tmp_path)
    # Remove the run file so trusted artifact verification fails late (after
    # citations/front matter have been built).
    (ctx["run_dir"] / "FINAL_RESULT.json").unlink()
    package = build_article_presentation(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        ctx["story_id"],
        ctx["values"],
        ctx["evidence"],
        [ctx["run_dir"]],
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(_front_matter_response),
    )
    assert package.status == "blocked"
    assert package.plan_id == ctx["plan"].plan_id
    assert package.citations
    assert package.front_matter is not None
    assert any(
        item.kind == "missing_quantitative_artifact" for item in package.blockers
    )


def test_two_sentence_two_reference_offset_insertion() -> None:
    text = "First sentence here. Second sentence here."
    placements = [
        CitationPlacement(
            placement_id="a",
            paragraph_id="p",
            reference_alias="REF01_a",
            sentence_position=0,
            marker="[REF:REF01_a]",
        ),
        CitationPlacement(
            placement_id="b",
            paragraph_id="p",
            reference_alias="REF02_b",
            sentence_position=1,
            marker="[REF:REF02_b]",
        ),
    ]
    rendered = _render_reader_paragraph("p", text, placements)
    assert rendered == (
        "First sentence here.[REF:REF01_a] " "Second sentence here.[REF:REF02_b]"
    )
    assert _strip_markers(rendered) == text


def test_citation_marker_never_splits_decimal_number() -> None:
    text = "The verified score is 0.1414695. The next sentence is bounded."
    placement = CitationPlacement(
        placement_id="decimal",
        paragraph_id="p",
        reference_alias="REF01_decimal",
        sentence_position=0,
        marker="[REF:REF01_decimal]",
    )
    rendered = _render_reader_paragraph("p", text, [placement])
    assert rendered == (
        "The verified score is 0.1414695.[REF:REF01_decimal] "
        "The next sentence is bounded."
    )


def test_citation_request_includes_sentence_table_and_evidence_excerpt(
    tmp_path,
) -> None:
    ctx = _chain(tmp_path)
    blockers = []
    citations, references, aliases = _build_citations(
        plan=ctx["plan"],
        ledger=ctx["ledger"],
        manuscript=ctx["manuscript"],
        evidence_by_id={EVIDENCE_1.evidence_id: EVIDENCE_1},
        bibliographic_metadata=_biblio(),
        blockers=blockers,
        warnings=[],
    )
    assert citations and not blockers
    requests = _build_citation_section_requests(
        manuscript=ctx["manuscript"],
        references=references,
        allowed_aliases=aliases,
        plan=ctx["plan"],
        evidence_by_id={EVIDENCE_1.evidence_id: EVIDENCE_1},
    )
    reference = requests[0]["references"][0]
    assert reference["evidence_excerpts"][0]["excerpt"] == EVIDENCE_1.text
    sentences = requests[0]["paragraphs"][0]["sentences"]
    assert sentences
    assert sentences[0]["sentence_position"] == 0


def test_writer_only_claim_aliases_do_not_leak_to_reader() -> None:
    text = (
        "The result is supported. [C12_candidate_gc05_did_not_m] "
        "The limitation remains explicit."
    )
    rendered = _render_reader_paragraph("p", text, [])
    assert "[C12_candidate_gc05_did_not_m]" not in rendered
    assert "The result is supported." in rendered
    assert "The limitation remains explicit." in rendered


def test_synthesized_flow_diagram_wraps_long_intent_into_stages() -> None:
    intent = (
        "Illustrate the flow from material selection (MgF2, SiO2, Al2O3, "
        "Ta2O5, TiO2) to layer count optimization (4-10 layers) and final "
        "TMM verification across all declared angles and polarizations."
    )
    svg = _render_synthesized_diagram([intent], [])
    assert "STAGE 1" in svg
    assert "STAGE 2" in svg
    assert "STAGE 3" in svg
    assert 'marker-end="url(#arrow)"' in svg
    assert intent not in svg


def test_body_invariant_and_placement_uniqueness(tmp_path) -> None:
    ctx = _chain(tmp_path)
    package = build_article_presentation(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        ctx["story_id"],
        ctx["values"],
        ctx["evidence"],
        [ctx["run_dir"]],
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(_front_matter_response),
        bibliographic_metadata=_biblio(),
    )
    errors: list[str] = []
    assert validate_presentation_package(
        package, manuscript=ctx["manuscript"], errors=errors, warnings=[]
    )
    for marker in [item.marker for item in package.placements]:
        assert package.reader_markdown.count(marker) == 1
    tampered = package.model_copy(
        update={"reader_markdown": package.reader_markdown.replace("[REF:", "XREF:")}
    )
    errors = []
    assert not validate_presentation_package(
        tampered, manuscript=ctx["manuscript"], errors=errors, warnings=[]
    )
    assert any("marker" in item for item in errors)


def test_reproducibility_validator_rejects_forged_manifests(tmp_path) -> None:
    ctx = _chain(tmp_path)
    base = ctx["reproducibility"]

    def checked(pkg):
        errors: list[str] = []
        validate_reproducibility_package(
            pkg,
            ctx["plan"],
            ctx["ledger"],
            ctx["architecture"],
            ctx["review"],
            ctx["manuscript"],
            ctx["story_id"],
            ctx["values"],
            errors,
            [],
        )
        return errors

    success_false = base.model_copy(
        update={
            "replay_records": [
                record.model_copy(
                    update={
                        "manifest": {
                            **record.manifest,
                            "success": False,
                        }
                    }
                )
                for record in base.replay_records
            ]
        }
    )
    assert any("not successful" in item for item in checked(success_false))

    empty_checks = base.model_copy(
        update={
            "replay_records": [
                record.model_copy(
                    update={
                        "manifest": {
                            **record.manifest,
                            "checks": [],
                            "total_artifacts": 0,
                            "matched_artifacts": 0,
                        }
                    }
                )
                for record in base.replay_records
            ]
        }
    )
    assert any("empty checks" in item for item in checked(empty_checks))

    wrong_lineage = base.model_copy(
        update={
            "lineage": [
                item.model_copy(update={"experiment_id": "exp-other"})
                for item in base.lineage
            ]
        }
    )
    assert any("exact matched lineage" in item for item in checked(wrong_lineage))


def test_reproducibility_validator_rejects_empty_replay_source_run_id(
    tmp_path,
) -> None:
    ctx = _chain(tmp_path)
    base = ctx["reproducibility"]

    def checked(pkg):
        errors: list[str] = []
        validate_reproducibility_package(
            pkg,
            ctx["plan"],
            ctx["ledger"],
            ctx["architecture"],
            ctx["review"],
            ctx["manuscript"],
            ctx["story_id"],
            ctx["values"],
            errors,
            [],
        )
        return errors

    empty_run = base.model_copy(
        update={
            "replay_records": [
                record.model_copy(update={"source_run_id": ""})
                for record in base.replay_records
            ]
        }
    )
    errors = checked(empty_run)
    assert any("has empty source_run_id" in item for item in errors)
    # The manifest still carries a valid source_run_id, so the empty record
    # must be the explicit rejection, not a silent pass or a mismatch error.
    assert not any(
        "source_run_id does not match its manifest" in item for item in errors
    )


def test_paper_doi_conflict_and_metadata_validation(tmp_path) -> None:
    conflict = MethodEvidence(
        evidence_id="ev-2",
        paper_id="paper-1",
        title="Broadband Antireflection Coatings",
        doi="10.1000/other-doi",
        year=2020,
        source_route="abstract",
        content_depth=MethodContentDepth.fulltext,
        text="A bounded abstract summary.",
        query_ids=["q1"],
        allowed_use=MethodAllowedUse.direct_fact,
    )
    plan = _plan_with_evidence([EVIDENCE_1, conflict], ("ev-1", "ev-2"))
    plan, ledger = _ledger(plan)
    claim = ledger.claims[0]
    manuscript = _minimal_manuscript(
        [
            ParagraphManuscriptSource(
                paragraph_id="p1",
                section_id="s1",
                rendered_text="x",
                claim_ids=[claim.claim_id],
            )
        ]
    )
    blockers: list[Any] = []
    _build_citations(
        plan=plan,
        ledger=ledger,
        manuscript=manuscript,
        evidence_by_id={"ev-1": EVIDENCE_1, "ev-2": conflict},
        bibliographic_metadata={},
        blockers=blockers,
        warnings=[],
    )
    assert any("conflict on DOI" in item.message for item in blockers)

    blockers = []
    _build_citations(
        plan=plan,
        ledger=ledger,
        manuscript=manuscript,
        evidence_by_id={"ev-1": EVIDENCE_1},
        bibliographic_metadata={"paper-1": {"authors": [], "venue": "X"}},
        blockers=blockers,
        warnings=[],
    )
    assert any("invalid authors" in item.message for item in blockers)


def test_front_matter_structure_injection_falls_back(tmp_path) -> None:
    ctx = _chain(tmp_path)

    def injected(request):
        pid = request["sections"][0]["paragraphs"][0]["paragraph_id"]
        return {
            "title": "# Injected Heading",
            "abstract_sentences": [
                {
                    "sentence": "A safe sentence.",
                    "paragraph_aliases": [pid],
                }
            ],
            "keywords": ["x\n# y"],
        }

    package = build_article_presentation(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        ctx["story_id"],
        ctx["values"],
        ctx["evidence"],
        [ctx["run_dir"]],
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(injected),
        bibliographic_metadata=_biblio(),
    )
    assert package.status == "ready_with_findings"
    assert package.front_matter is not None and package.front_matter.fallback
    assert "# Injected Heading" not in package.reader_markdown


def test_front_matter_drops_only_unsupported_abstract_sentence(tmp_path) -> None:
    ctx = _chain(tmp_path)

    def partially_safe(request):
        pid = request["sections"][0]["paragraphs"][0]["paragraph_id"]
        return {
            "title": "Safe Broadband AR Title",
            "abstract_sentences": [
                {
                    "sentence": "The verified baseline is discussed.",
                    "paragraph_aliases": [pid],
                },
                {
                    "sentence": "An unsupported value of 0.123 was achieved.",
                    "paragraph_aliases": [pid],
                },
            ],
            "keywords": ["antireflection", "unsafe\nkeyword"],
        }

    package = build_article_presentation(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        ctx["story_id"],
        ctx["values"],
        ctx["evidence"],
        [ctx["run_dir"]],
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(partially_safe),
        bibliographic_metadata=_biblio(),
    )

    assert package.front_matter is not None
    assert package.front_matter.fallback is False
    assert package.front_matter.title == "Safe Broadband AR Title"
    assert len(package.front_matter.abstract_sentences) == 1
    assert "0.123" not in package.reader_markdown
    assert package.front_matter.keywords == ["antireflection"]


def test_reader_escaping_of_model_scientific_text(tmp_path) -> None:
    ctx = _chain(tmp_path)

    def injected(request):
        pid = request["sections"][0]["paragraphs"][0]["paragraph_id"]
        return {
            "title": "Title <script>alert(1)</script>",
            "abstract_sentences": [
                {
                    "sentence": "A safe sentence.",
                    "paragraph_aliases": [pid],
                }
            ],
            "keywords": [],
        }

    package = build_article_presentation(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        ctx["story_id"],
        ctx["values"],
        ctx["evidence"],
        [ctx["run_dir"]],
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(injected),
        bibliographic_metadata={
            "paper-1": {
                "authors": ["A. Author [evil](https://evil.example)"],
                "venue": "J. Optics",
            }
        },
    )
    assert "<script>" not in package.reader_markdown
    assert "[evil](https://evil.example)" not in package.reader_markdown
    assert "[REF01_" in package.reader_markdown


def test_late_failure_preserves_telemetry_and_reader(tmp_path) -> None:
    ctx = _chain(tmp_path)
    (ctx["run_dir"] / "FINAL_RESULT.json").unlink()
    package = build_article_presentation(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        ctx["story_id"],
        ctx["values"],
        ctx["evidence"],
        [ctx["run_dir"]],
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(_front_matter_response),
        bibliographic_metadata=_biblio(),
    )
    assert package.status == "blocked"
    assert package.reader_markdown
    original = ctx["manuscript"].source_map[0].rendered_text
    assert original in _strip_markers(package.reader_markdown)
    assert package.usage.get("estimated_input_tokens", 0) > 0
    assert package.attempts >= 2


def test_panel_validators() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PanelAsset(
            label="x",
            asset_path="figures/x.svg",
            encoding="utf-8",
            media_type="image/svg+xml",
            asset_content="<svg/>",
            sha256="short",
        )
    with pytest.raises(ValidationError):
        PanelAsset(
            label="x",
            asset_path="../escape.svg",
            encoding="utf-8",
            media_type="image/svg+xml",
            asset_content="<svg/>",
            sha256="a" * 64,
        )
    with pytest.raises(ValidationError):
        PanelAsset(
            label="x",
            asset_path="figures/x.png",
            encoding="base64",
            media_type="image/png",
            asset_bytes_b64="not base64!!!",
            sha256="a" * 64,
        )
    with pytest.raises(ValidationError):
        PanelAsset(
            label="x",
            asset_path="figures/x.png",
            encoding="utf-8",
            media_type="image/png",
            asset_content="<svg/>",
            asset_bytes_b64=base64.b64encode(b"x").decode(),
            sha256="a" * 64,
        )


def test_write_preflight_leaves_no_core_files_on_asset_conflict(
    tmp_path,
) -> None:
    ctx = _chain(tmp_path)
    package = build_article_presentation(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        ctx["story_id"],
        ctx["values"],
        ctx["evidence"],
        [ctx["run_dir"]],
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(_front_matter_response),
        bibliographic_metadata=_biblio(),
    )
    out = tmp_path / "preflight"
    panel_path = out / package.visuals[0].panels[0].asset_path
    panel_path.parent.mkdir(parents=True, exist_ok=True)
    panel_path.write_bytes(b"conflicting bytes")
    with pytest.raises(ArticlePresentationIntegrityError, match="conflicting"):
        write_presentation_package(
            package,
            out,
            plan=ctx["plan"],
            ledger=ctx["ledger"],
            architecture=ctx["architecture"],
            review=ctx["review"],
            manuscript=ctx["manuscript"],
            reproducibility=ctx["reproducibility"],
            selected_story_id=ctx["story_id"],
            value_records=ctx["values"],
        )
    assert not (out / "ARTICLE_READER_MANUSCRIPT.md").exists()
    assert not (out / "ARTICLE_PRESENTATION_PACKAGE.json").exists()


def test_repeated_reference_across_paragraphs() -> None:
    paragraphs = [
        ParagraphManuscriptSource(
            paragraph_id="p1",
            section_id="s1",
            rendered_text="First paragraph.",
            claim_ids=[],
        ),
        ParagraphManuscriptSource(
            paragraph_id="p2",
            section_id="s1",
            rendered_text="Second paragraph.",
            claim_ids=[],
        ),
    ]
    manuscript = _minimal_manuscript(paragraphs)
    manuscript = manuscript.model_copy(
        update={
            "body": manuscript.body.model_copy(
                update={
                    "sections": [
                        ManuscriptSection(
                            section_id="s1",
                            heading="Section",
                            story_id="story-01",
                            status="ready",
                            paragraphs=paragraphs,
                            finding_ids=[],
                        )
                    ]
                }
            )
        }
    )
    reference = ReferenceRecord(
        reference_id="ref-1",
        reference_alias="REF01_x",
        paper_ids=["paper-1"],
        title="Title",
        evidence_ids=[],
        paragraph_ids=[],
        claim_ids=[],
        hypothesis_ids=[],
        support_semantics=[],
        content_depth=[],
        metadata_incomplete_fields=[],
        metadata_complete=True,
    )
    placements = [
        CitationPlacement(
            placement_id="a",
            paragraph_id="p1",
            reference_alias="REF01_x",
            sentence_position=0,
            marker="[REF:REF01_x]",
        ),
        CitationPlacement(
            placement_id="b",
            paragraph_id="p2",
            reference_alias="REF01_x",
            sentence_position=0,
            marker="[REF:REF01_x]",
        ),
    ]
    front_matter = FrontMatter(
        title="Title",
        abstract_sentences=[],
        keywords=[],
        fallback=True,
    )
    reader = _render_reader_manuscript(
        front_matter=front_matter,
        manuscript=manuscript,
        citations=[],
        references=[reference],
        placements=placements,
        visuals=[],
    )
    blockers: list[Any] = []
    _verify_body_invariant(
        manuscript=manuscript,
        reader_markdown=reader,
        placements=placements,
        references=[reference],
        blockers=blockers,
    )
    assert blockers == []
    assert reader.count("[REF:REF01_x]") == 2
    assert "First paragraph.[REF:REF01_x]" in reader
    assert "Second paragraph.[REF:REF01_x]" in reader


def test_validate_presentation_package_accepts_mapping_inputs(tmp_path) -> None:
    ctx = _chain(tmp_path)
    package = build_article_presentation(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        ctx["story_id"],
        ctx["values"],
        ctx["evidence"],
        [ctx["run_dir"]],
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(_front_matter_response),
        bibliographic_metadata=_biblio(),
    )
    errors: list[str] = []
    warnings: list[str] = []
    ok = validate_presentation_package(
        package.model_dump(mode="json"),
        plan=ctx["plan"].model_dump(mode="json"),
        ledger=ctx["ledger"].model_dump(mode="json"),
        architecture=ctx["architecture"].model_dump(mode="json"),
        review=ctx["review"].model_dump(mode="json"),
        manuscript=ctx["manuscript"].model_dump(mode="json"),
        reproducibility=ctx["reproducibility"].model_dump(mode="json"),
        selected_story_id=ctx["story_id"],
        value_records=[item.model_dump(mode="json") for item in ctx["values"]],
        errors=errors,
        warnings=warnings,
    )
    assert ok
    assert errors == []


def test_complete_chain_rejects_tampered_manuscript_with_stale_id(
    tmp_path,
) -> None:
    ctx = _chain(tmp_path)
    package = build_article_presentation(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        ctx["story_id"],
        ctx["values"],
        ctx["evidence"],
        [ctx["run_dir"]],
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(_front_matter_response),
        bibliographic_metadata=_biblio(),
    )
    tampered = ctx["manuscript"].model_copy(
        update={"warnings": ["forged manuscript warning"]}
    )
    partial_errors: list[str] = []
    assert validate_presentation_package(
        package,
        manuscript=tampered,
        errors=partial_errors,
        warnings=[],
    )
    assert partial_errors == []
    errors: list[str] = []
    assert not validate_presentation_package(
        package,
        plan=ctx["plan"],
        ledger=ctx["ledger"],
        architecture=ctx["architecture"],
        review=ctx["review"],
        manuscript=tampered,
        reproducibility=ctx["reproducibility"],
        selected_story_id=ctx["story_id"],
        value_records=ctx["values"],
        errors=errors,
        warnings=[],
    )
    assert any("warnings do not match" in item for item in errors)


def test_complete_chain_rejects_tampered_review_with_stale_id(
    tmp_path,
) -> None:
    ctx = _chain(tmp_path)
    package = build_article_presentation(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        ctx["story_id"],
        ctx["values"],
        ctx["evidence"],
        [ctx["run_dir"]],
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(_front_matter_response),
        bibliographic_metadata=_biblio(),
    )
    tampered = ctx["review"].model_copy(update={"hard_blockers": ["forged blocker"]})
    partial_errors: list[str] = []
    assert validate_presentation_package(
        package,
        review=tampered,
        errors=partial_errors,
        warnings=[],
    )
    assert partial_errors == []
    errors: list[str] = []
    assert not validate_presentation_package(
        package,
        plan=ctx["plan"],
        ledger=ctx["ledger"],
        architecture=ctx["architecture"],
        review=tampered,
        manuscript=ctx["manuscript"],
        reproducibility=ctx["reproducibility"],
        selected_story_id=ctx["story_id"],
        value_records=ctx["values"],
        errors=errors,
        warnings=[],
    )
    assert any("hard_blockers are not derivable" in item for item in errors)


def test_complete_chain_rejects_tampered_reproducibility_with_stale_id(
    tmp_path,
) -> None:
    ctx = _chain(tmp_path)
    package = build_article_presentation(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        ctx["story_id"],
        ctx["values"],
        ctx["evidence"],
        [ctx["run_dir"]],
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(_front_matter_response),
        bibliographic_metadata=_biblio(),
    )
    tampered = ctx["reproducibility"].model_copy(
        update={
            "critical_experiments": [
                item.model_copy(update={"rationale": "forged rationale"})
                for item in ctx["reproducibility"].critical_experiments
            ]
        }
    )
    partial_errors: list[str] = []
    assert validate_presentation_package(
        package,
        reproducibility=tampered,
        errors=partial_errors,
        warnings=[],
    )
    assert partial_errors == []
    errors: list[str] = []
    assert not validate_presentation_package(
        package,
        plan=ctx["plan"],
        ledger=ctx["ledger"],
        architecture=ctx["architecture"],
        review=ctx["review"],
        manuscript=ctx["manuscript"],
        reproducibility=tampered,
        selected_story_id=ctx["story_id"],
        value_records=ctx["values"],
        errors=errors,
        warnings=[],
    )
    assert any(
        "package_id does not match recomputed identity" in item for item in errors
    )


def test_complete_chain_reports_invalid_value_records(tmp_path) -> None:
    ctx = _chain(tmp_path)
    package = build_article_presentation(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        ctx["story_id"],
        ctx["values"],
        ctx["evidence"],
        [ctx["run_dir"]],
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(_front_matter_response),
        bibliographic_metadata=_biblio(),
    )
    errors: list[str] = []
    assert not validate_presentation_package(
        package,
        plan=ctx["plan"],
        ledger=ctx["ledger"],
        architecture=ctx["architecture"],
        review=ctx["review"],
        manuscript=ctx["manuscript"],
        reproducibility=ctx["reproducibility"],
        selected_story_id=ctx["story_id"],
        value_records=[{"artifact_id": "missing-fields"}],
        errors=errors,
        warnings=[],
    )
    assert any("value_records[0] is invalid" in item for item in errors)


def test_manuscript_provenance_warning_only_when_required(tmp_path) -> None:
    ctx = _chain(tmp_path)
    package = build_article_presentation(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        ctx["story_id"],
        ctx["values"],
        ctx["evidence"],
        [ctx["run_dir"]],
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(_front_matter_response),
        bibliographic_metadata=_biblio(),
    )
    warnings: list[str] = []
    errors: list[str] = []
    ok = validate_presentation_package(
        package,
        warnings=warnings,
        errors=errors,
    )
    assert ok
    assert not any("body provenance was not revalidated" in item for item in warnings)
    warnings2: list[str] = []
    ok = validate_presentation_package(
        package,
        require_body_provenance=True,
        warnings=warnings2,
        errors=errors,
    )
    assert ok
    assert any("body provenance was not revalidated" in item for item in warnings2)
    out = tmp_path / "draft_out"
    with pytest.warns(UserWarning, match="body provenance was not revalidated"):
        write_presentation_package(package, out)


def test_repro_lineage_artifact_id_differs_from_path_and_task_mismatch(
    tmp_path,
) -> None:
    ctx = _chain(tmp_path)
    base = ctx["reproducibility"]
    sha = ctx["sha"]

    def recompute(pkg):
        return pkg.model_copy(
            update={
                "package_id": compute_reproducibility_package_id(
                    plan_id=pkg.plan_id,
                    ledger_id=pkg.ledger_id,
                    architecture_id=pkg.architecture_id,
                    review_id=pkg.review_id,
                    result_id=pkg.result_id,
                    manuscript_body_id=pkg.manuscript_body_id,
                    story_id=pkg.story_id,
                    status=pkg.status,
                    critical_experiments=pkg.critical_experiments,
                    replay_records=pkg.replay_records,
                    lineage=pkg.lineage,
                    appendix=pkg.appendix,
                    blockers=pkg.blockers,
                    warnings=pkg.warnings,
                    errors=pkg.errors,
                    attempts=pkg.attempts,
                )
            }
        )

    def checked(pkg):
        errors: list[str] = []
        validate_reproducibility_package(
            pkg,
            ctx["plan"],
            ctx["ledger"],
            ctx["architecture"],
            ctx["review"],
            ctx["manuscript"],
            ctx["story_id"],
            ctx["values"],
            errors,
            [],
        )
        return errors

    crafted = recompute(
        base.model_copy(
            update={
                "lineage": [
                    ArtifactLineageRecord(
                        lineage_id="l1",
                        artifact_id="FINAL_RESULT.json",
                        experiment_id="exp-1",
                        relative_path="DATA.json",
                        source_sha256=sha,
                        replay_sha256=sha,
                        identity_kind="canonical_scientific_identity",
                        matched=True,
                    )
                ],
                "replay_records": [
                    record.model_copy(
                        update={
                            "manifest": {
                                **record.manifest,
                                "checks": [
                                    ReplayArtifactCheck(
                                        relative_path="DATA.json",
                                        source_sha256=sha,
                                        replay_sha256=sha,
                                        matched=True,
                                        reason="ok",
                                    ).model_dump(mode="json")
                                ],
                                "total_artifacts": 1,
                                "matched_artifacts": 1,
                            }
                        }
                    )
                    for record in base.replay_records
                ],
            }
        )
    )
    assert checked(crafted) == []

    task_mismatch = recompute(
        base.model_copy(
            update={
                "replay_records": [
                    record.model_copy(
                        update={
                            "source_task_sha256": "b" * 64,
                            "manifest": {
                                **record.manifest,
                                "source_task_sha256": "a" * 64,
                            },
                        }
                    )
                    for record in base.replay_records
                ]
            }
        )
    )
    assert any("does not match its manifest" in item for item in checked(task_mismatch))

    run_mismatch = recompute(
        base.model_copy(
            update={
                "replay_records": [
                    record.model_copy(
                        update={
                            "source_run_id": "run-other",
                            "manifest": {
                                **record.manifest,
                                "source_run_id": "run-1",
                            },
                        }
                    )
                    for record in base.replay_records
                ]
            }
        )
    )
    assert any(
        "source_run_id" in item and "manifest" in item for item in checked(run_mismatch)
    )


def test_orphan_citation_and_placement_detected(tmp_path) -> None:
    ctx = _chain(tmp_path)
    package = build_article_presentation(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        ctx["story_id"],
        ctx["values"],
        ctx["evidence"],
        [ctx["run_dir"]],
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(_front_matter_response),
        bibliographic_metadata=_biblio(),
    )
    citation = package.citations[0]
    orphan_citation = package.model_copy(
        update={
            "citations": package.citations
            + [
                citation.model_copy(
                    update={
                        "citation_id": "cite-orphan",
                        "paragraph_id": "story-01-section-01-p01",
                        "reference_alias": "REF01_orphan",
                    }
                )
            ]
        }
    )
    orphan_citation = orphan_citation.model_copy(
        update={
            "package_id": compute_presentation_package_id(
                plan_id=orphan_citation.plan_id,
                ledger_id=orphan_citation.ledger_id,
                architecture_id=orphan_citation.architecture_id,
                review_id=orphan_citation.review_id,
                result_id=orphan_citation.result_id,
                manuscript_body_id=orphan_citation.manuscript_body_id,
                reproducibility_package_id=orphan_citation.reproducibility_package_id,
                story_id=orphan_citation.story_id,
                status=orphan_citation.status,
                citations=orphan_citation.citations,
                references=orphan_citation.references,
                placements=orphan_citation.placements,
                front_matter=orphan_citation.front_matter,
                visuals=orphan_citation.visuals,
                reader_markdown=orphan_citation.reader_markdown,
                blockers=orphan_citation.blockers,
                warnings=orphan_citation.warnings,
                errors=orphan_citation.errors,
                attempts=orphan_citation.attempts,
            )
        }
    )
    errors: list[str] = []
    validate_presentation_package(
        orphan_citation, manuscript=ctx["manuscript"], errors=errors, warnings=[]
    )
    assert any("has no matching placement" in item for item in errors)

    placement = package.placements[0]
    orphan_placement = package.model_copy(
        update={
            "placements": package.placements
            + [
                placement.model_copy(
                    update={
                        "placement_id": "place-orphan",
                        "reference_alias": "REF01_orphan",
                    }
                )
            ]
        }
    )
    orphan_placement = orphan_placement.model_copy(
        update={
            "package_id": compute_presentation_package_id(
                plan_id=orphan_placement.plan_id,
                ledger_id=orphan_placement.ledger_id,
                architecture_id=orphan_placement.architecture_id,
                review_id=orphan_placement.review_id,
                result_id=orphan_placement.result_id,
                manuscript_body_id=orphan_placement.manuscript_body_id,
                reproducibility_package_id=orphan_placement.reproducibility_package_id,
                story_id=orphan_placement.story_id,
                status=orphan_placement.status,
                citations=orphan_placement.citations,
                references=orphan_placement.references,
                placements=orphan_placement.placements,
                front_matter=orphan_placement.front_matter,
                visuals=orphan_placement.visuals,
                reader_markdown=orphan_placement.reader_markdown,
                blockers=orphan_placement.blockers,
                warnings=orphan_placement.warnings,
                errors=orphan_placement.errors,
                attempts=orphan_placement.attempts,
            )
        }
    )
    errors = []
    validate_presentation_package(
        orphan_placement,
        manuscript=ctx["manuscript"],
        errors=errors,
        warnings=[],
    )
    assert any("has no matching citation" in item for item in errors)


def test_unknown_paragraph_and_alias_targets_detected(tmp_path) -> None:
    ctx = _chain(tmp_path)
    package = build_article_presentation(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        ctx["story_id"],
        ctx["values"],
        ctx["evidence"],
        [ctx["run_dir"]],
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(_front_matter_response),
        bibliographic_metadata=_biblio(),
    )
    placement = package.placements[0]
    forged = package.model_copy(
        update={
            "placements": package.placements
            + [
                placement.model_copy(
                    update={
                        "placement_id": "place-unknown",
                        "paragraph_id": "no-such-paragraph",
                        "reference_alias": "REF99_unknown",
                        "marker": "[REF:REF99_unknown]",
                    }
                )
            ]
        }
    )
    forged = forged.model_copy(
        update={
            "package_id": compute_presentation_package_id(
                plan_id=forged.plan_id,
                ledger_id=forged.ledger_id,
                architecture_id=forged.architecture_id,
                review_id=forged.review_id,
                result_id=forged.result_id,
                manuscript_body_id=forged.manuscript_body_id,
                reproducibility_package_id=forged.reproducibility_package_id,
                story_id=forged.story_id,
                status=forged.status,
                citations=forged.citations,
                references=forged.references,
                placements=forged.placements,
                front_matter=forged.front_matter,
                visuals=forged.visuals,
                reader_markdown=forged.reader_markdown,
                blockers=forged.blockers,
                warnings=forged.warnings,
                errors=forged.errors,
                attempts=forged.attempts,
            )
        }
    )
    errors: list[str] = []
    validate_presentation_package(
        forged,
        manuscript=ctx["manuscript"],
        errors=errors,
        warnings=[],
    )
    assert any("targets unknown paragraph" in item for item in errors)
    assert any("references unknown alias" in item for item in errors)


def _spectrum_payload() -> dict[str, Any]:
    return {
        "wavelengths_nm": [500.0, 575.0, 650.0],
        "channels": {
            "angle=45|pol=s": {
                "R": [0.9487, 0.9101, 0.6037],
                "T": [0.0509, 0.0898, 0.3962],
            },
            "angle=45|pol=p": {
                "R": [0.7332, 0.5124, 0.0876],
                "T": [0.2662, 0.4875, 0.9123],
            },
        },
        "solver": "smatrix",
    }


def test_load_numeric_rows_supports_columnar_simulation_result(
    tmp_path,
) -> None:
    path = tmp_path / "SIMULATION_RESULT.json"
    path.write_text(json.dumps(_spectrum_payload()), encoding="utf-8")
    rows = _load_numeric_rows(
        path,
        [
            "wavelengths_nm",
            "channels.angle=45|pol=s.R",
            "channels.angle=45|pol=p.T",
        ],
    )
    assert len(rows) == 3
    assert rows[0] == {
        "wavelengths_nm": 500.0,
        "channels.angle=45|pol=s.R": 0.9487,
        "channels.angle=45|pol=p.T": 0.2662,
    }
    assert rows[2]["channels.angle=45|pol=p.T"] == 0.9123


def test_load_numeric_rows_rejects_unequal_spectrum_columns(tmp_path) -> None:
    payload = _spectrum_payload()
    payload["channels"]["angle=45|pol=s"]["R"] = [0.9, 0.8]
    path = tmp_path / "SIMULATION_RESULT.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="length"):
        _load_numeric_rows(path, ["wavelengths_nm", "channels.angle=45|pol=s.R"])


def test_load_numeric_rows_rejects_non_finite_spectrum_values(
    tmp_path,
) -> None:
    payload = _spectrum_payload()
    payload["channels"]["angle=45|pol=p"]["T"][1] = float("nan")
    path = tmp_path / "SIMULATION_RESULT.json"
    path.write_text(json.dumps(payload, allow_nan=True), encoding="utf-8")
    with pytest.raises(ValueError, match="non-finite"):
        _load_numeric_rows(path, ["wavelengths_nm", "channels.angle=45|pol=p.T"])


def test_load_numeric_rows_rejects_scalar_metadata_mixed_with_spectrum(
    tmp_path,
) -> None:
    path = tmp_path / "SIMULATION_RESULT.json"
    path.write_text(json.dumps(_spectrum_payload()), encoding="utf-8")
    with pytest.raises(ValueError, match="scalar metadata"):
        _load_numeric_rows(
            path, ["wavelengths_nm", "channels.angle=45|pol=s.R", "solver"]
        )


def test_load_numeric_rows_rejects_unknown_channel(tmp_path) -> None:
    path = tmp_path / "SIMULATION_RESULT.json"
    path.write_text(json.dumps(_spectrum_payload()), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown channel"):
        _load_numeric_rows(path, ["channels.angle=60|pol=s.R"])


def test_load_numeric_rows_retains_list_and_csv_behavior(tmp_path) -> None:
    json_path = tmp_path / "data.json"
    json_path.write_text(
        json.dumps(
            [
                {"R_mean": 0.1, "label": "a"},
                {"R_mean": 0.2, "label": "b"},
            ]
        ),
        encoding="utf-8",
    )
    rows = _load_numeric_rows(json_path, ["R_mean", "label"])
    assert rows == [
        {"R_mean": 0.1, "label": "a"},
        {"R_mean": 0.2, "label": "b"},
    ]
    csv_path = tmp_path / "data.csv"
    csv_path.write_text("R_mean,label\n0.1,a\n0.2,b\n", encoding="utf-8")
    csv_rows = _load_numeric_rows(csv_path, ["R_mean", "label"])
    assert csv_rows == [
        {"R_mean": "0.1", "label": "a"},
        {"R_mean": "0.2", "label": "b"},
    ]


def _uneven_spectrum_payload() -> dict[str, Any]:
    return {
        "wavelengths_nm": [500.0, 550.0, 650.0],
        "channels": {
            "angle=45|pol=s": {
                "R": [0.9, 0.95, 0.6],
                "T": [0.1, 0.05, 0.4],
            }
        },
        "solver": "smatrix",
    }


def test_spectrum_svg_uses_wavelength_x_axis(tmp_path) -> None:
    path = tmp_path / "SIMULATION_RESULT.json"
    path.write_text(json.dumps(_uneven_spectrum_payload()), encoding="utf-8")
    fields = [
        "wavelengths_nm",
        "channels.angle=45|pol=s.R",
        "channels.angle=45|pol=s.T",
    ]
    rows = _load_numeric_rows(path, fields)
    svg = _render_svg_plot(rows, fields, "spectrum")
    polylines = re.findall(r'<polyline[^>]*points="([^"]+)"', svg)
    assert len(polylines) == 2
    margin = 56
    plot_width = 640 - 2 * margin
    expected_x = [
        margin + (wavelength - 500.0) / 150.0 * plot_width
        for wavelength in (500.0, 550.0, 650.0)
    ]
    for polyline in polylines:
        points = [
            tuple(float(value) for value in point.split(","))
            for point in polyline.split()
        ]
        assert len(points) == 3
        assert all(
            abs(point[0] - expected) < 0.1
            for point, expected in zip(points, expected_x)
        )
    assert not any(
        "wavelengths_nm" in line for line in svg.splitlines() if "<text" in line
    )
    assert svg.count("<polyline") == 2
    assert polylines[0] != polylines[1]


def test_spectrum_svg_rejects_wavelength_only_plot(tmp_path) -> None:
    path = tmp_path / "SIMULATION_RESULT.json"
    path.write_text(json.dumps(_uneven_spectrum_payload()), encoding="utf-8")
    rows = _load_numeric_rows(path, ["wavelengths_nm"])
    with pytest.raises(ValueError, match="no numeric response series"):
        _render_svg_plot(rows, ["wavelengths_nm"], "wavelength only")
