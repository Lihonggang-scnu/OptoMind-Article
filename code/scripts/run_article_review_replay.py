#!/usr/bin/env python3
"""Revalidate a persisted Article draft, then replay review and manuscript."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optomind_optics.harness.article_architecture import (  # noqa: E402
    ArticleArchitectureResult,
)
from optomind_optics.harness.article_continuation import (  # noqa: E402
    _aggregate_usage,
    _contracted_inventory,
    _scoped_story_values,
    load_source_pipeline,
)
from optomind_optics.harness.article_manuscript import (  # noqa: E402
    build_article_manuscript,
)
from optomind_optics.harness.article_result_synthesis import (  # noqa: E402
    ArticleResultSynthesisResult,
)
from optomind_optics.harness.article_review import (  # noqa: E402
    ArticleReviewResult,
    QwenAuthorReviser,
    QwenExpressionReviewer,
    QwenGlobalAdviceRouter,
    QwenGlobalConsistencyReviewer,
    QwenScientificReviewer,
    ReviewerProviderResult,
    _reconstruct_section_draft,
    build_article_review,
)


class ForcedFindingsProvider:
    """Replay already accepted reviewer findings into the author revision loop."""

    def __init__(self, findings) -> None:
        self.findings = list(findings)

    def __call__(self, request):
        paragraph_ids = {
            str(item.get("paragraph_id") or "")
            for item in request.get("paragraphs") or []
        }
        rows = []
        for finding in self.findings:
            if (
                finding.paragraph_id not in paragraph_ids
                or not finding.suggested_action.strip()
            ):
                continue
            rows.append(
                {
                    "paragraph_id": finding.paragraph_id,
                    "span": finding.span,
                    "severity": finding.severity.value,
                    "kind": finding.kind,
                    "reason": finding.reason,
                    "suggested_action": finding.suggested_action,
                    "claim_aliases": [],
                }
            )
        return ReviewerProviderResult(
            response={"findings": rows, "advice": []},
            usage={},
            provider_model="persisted-review-findings",
            mock_llm=True,
        )


from optomind_optics.harness.article_writing import (  # noqa: E402
    ArticleDraftBundle,
    build_writing_alias_maps,
    compute_bundle_id,
    validate_writing_inputs,
)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: object) -> None:
    payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Revalidate a persisted Stage 10 writing checkpoint under the "
            "current local contracts, then replay review and manuscript only."
        )
    )
    parser.add_argument("--source-pipeline-dir", type=Path, required=True)
    parser.add_argument("--synthesis-path", type=Path, required=True)
    parser.add_argument("--architecture-path", type=Path, required=True)
    parser.add_argument("--writing-path", type=Path, required=True)
    parser.add_argument("--review-path", type=Path)
    parser.add_argument("--forced-findings-review-path", type=Path)
    parser.add_argument(
        "--global-only",
        action="store_true",
        help=(
            "Use final section drafts from --review-path, skip ordinary "
            "section reviewers, and run only the post-section global "
            "consistency review plus targeted author revision."
        ),
    )
    parser.add_argument("--work-dir", type=Path, required=True)
    return parser


def _revalidate_bundle(
    *,
    synthesis: ArticleResultSynthesisResult,
    architecture: ArticleArchitectureResult,
    writing: ArticleDraftBundle,
    values: tuple,
) -> ArticleDraftBundle:
    assert synthesis.derived_plan is not None and synthesis.ledger is not None
    errors: list[str] = []
    warnings: list[str] = []
    story, fact_by_claim = validate_writing_inputs(
        synthesis.derived_plan,
        synthesis.ledger,
        architecture,
        writing.story_id,
        values,
        errors,
        warnings,
    )
    if errors or story is None:
        raise ValueError("writing checkpoint input is invalid: " + "; ".join(errors))
    aliases = build_writing_alias_maps(
        story,
        synthesis.ledger,
        values,
        fact_by_claim,
    )
    value_records_by_key = {
        (record.artifact_id, record.field): record for record in values
    }
    draft_by_section = {item.section_id: item for item in writing.sections}
    sections = []
    for section in story.section_contracts:
        saved = draft_by_section.get(section.section_id)
        if saved is None:
            raise ValueError(f"writing checkpoint omits section {section.section_id}")
        reconstructed, reconstruction_errors = _reconstruct_section_draft(
            plan=synthesis.derived_plan,
            ledger=synthesis.ledger,
            architecture=architecture,
            story=story,
            section=section,
            aliases=aliases,
            value_records_by_key=value_records_by_key,
            fact_by_claim=fact_by_claim,
            section_draft=saved,
        )
        if reconstructed is None or reconstruction_errors:
            raise ValueError(
                f"section {section.section_id} cannot be revalidated: "
                + "; ".join(reconstruction_errors)
            )
        sections.append(reconstructed)

    source_ledger = [entry for section in sections for entry in section.source_ledger]
    deferred_claims = sorted(
        {claim_id for section in sections for claim_id in section.deferred_claim_ids}
    )
    publishable_section_ids = [
        section.section_id for section in sections if section.status == "publishable"
    ]
    publishable = bool(sections) and len(publishable_section_ids) == len(sections)
    bundle_id = compute_bundle_id(
        synthesis.derived_plan.plan_id,
        synthesis.ledger.ledger_id,
        architecture.architecture_id,
        story.story_id,
        sections,
    )
    return ArticleDraftBundle(
        bundle_id=bundle_id,
        plan_id=synthesis.derived_plan.plan_id,
        ledger_id=synthesis.ledger.ledger_id,
        architecture_id=architecture.architecture_id,
        story_id=story.story_id,
        sections=sections,
        source_ledger=source_ledger,
        deferred_claims=deferred_claims,
        warnings=[
            *writing.warnings,
            "persisted writing checkpoint revalidated under current local contracts",
            *warnings,
        ],
        errors=[],
        publishable=publishable,
        publishable_section_ids=publishable_section_ids,
        usage=dict(writing.usage),
        semantic_model=writing.semantic_model,
        model_status=writing.model_status,
        attempts=writing.attempts,
        claim_alias_map=dict(aliases["claim_alias_to_id"]),
        fact_alias_map=dict(aliases["fact_alias_map"]),
        value_alias_map={
            alias: dict(info) for alias, info in aliases["value_alias_map"].items()
        },
        figure_alias_map=dict(aliases["figure_alias_to_id"]),
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    work_dir = args.work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=False)
    bundle = load_source_pipeline(args.source_pipeline_dir)
    synthesis = ArticleResultSynthesisResult.model_validate(
        _read_json(args.synthesis_path)
    )
    if synthesis.derived_plan is None or synthesis.ledger is None:
        raise ValueError("synthesis snapshot has no derived plan or claim ledger")
    architecture = ArticleArchitectureResult.model_validate(
        _read_json(args.architecture_path)
    )
    persisted_writing = ArticleDraftBundle.model_validate(_read_json(args.writing_path))
    prior_review = None
    if args.global_only:
        if args.review_path is None:
            raise ValueError("--global-only requires --review-path")
        prior_review = ArticleReviewResult.model_validate(_read_json(args.review_path))
        if (
            prior_review.architecture_id != architecture.architecture_id
            or prior_review.story_id != persisted_writing.story_id
        ):
            raise ValueError(
                "prior review does not match the supplied architecture/writing"
            )
        persisted_writing = persisted_writing.model_copy(
            update={
                "sections": [section.section_draft for section in prior_review.sections]
            }
        )
    _, all_values = _contracted_inventory(synthesis, bundle)
    values = _scoped_story_values(
        architecture,
        persisted_writing.story_id,
        all_values,
        synthesis.ledger,
    )
    writing = _revalidate_bundle(
        synthesis=synthesis,
        architecture=architecture,
        writing=persisted_writing,
        values=values,
    )
    _write_json(work_dir / "03-writing-revalidated.json", writing)
    if not writing.publishable:
        raise ValueError("revalidated writing checkpoint is not fully publishable")

    forced_review = (
        ArticleReviewResult.model_validate(_read_json(args.forced_findings_review_path))
        if args.forced_findings_review_path is not None
        else None
    )
    global_provider = (
        ForcedFindingsProvider(forced_review.scientific_findings)
        if forced_review is not None
        else QwenGlobalConsistencyReviewer()
    )
    review = build_article_review(
        synthesis.derived_plan,
        synthesis.ledger,
        architecture,
        writing,
        writing.story_id,
        values,
        scientific_reviewer=(None if args.global_only else QwenScientificReviewer()),
        expression_reviewer=(None if args.global_only else QwenExpressionReviewer()),
        global_consistency_reviewer=global_provider,
        global_advice_router=(
            None if forced_review is not None else QwenGlobalAdviceRouter()
        ),
        global_revision_reviewer=(
            QwenScientificReviewer() if args.global_only else None
        ),
        author_reviser=QwenAuthorReviser(),
    )
    _write_json(work_dir / "04-review.json", review)
    manuscript = build_article_manuscript(
        synthesis.derived_plan,
        synthesis.ledger,
        architecture,
        review,
        writing.story_id,
        values,
        output_dir=work_dir / "manuscript",
    )
    _write_json(work_dir / "05-manuscript.json", manuscript)
    blocker_messages = set(review.hard_blockers)
    blocker_messages.update(
        message
        for handoff in manuscript.blocked_handoff
        for message in handoff.hard_blockers
    )
    usage = _aggregate_usage(
        [
            ("architecture", architecture),
            ("writing", writing),
            ("review", review),
        ]
    )
    summary = {
        "status": manuscript.body.status,
        "story_id": writing.story_id,
        "findings": len(synthesis.findings),
        "claims": len(synthesis.ledger.claims),
        "sections": len(manuscript.body.sections),
        "paragraphs": sum(
            len(section.paragraphs) for section in manuscript.body.sections
        ),
        "words": sum(
            len(re.findall(r"\S+", paragraph.rendered_text))
            for section in manuscript.body.sections
            for paragraph in section.paragraphs
        ),
        "scientific_findings": len(review.scientific_findings),
        "expression_findings": len(review.expression_findings),
        "blockers": len(blocker_messages),
        "incremental_review_usage": dict(review.usage),
        "lineage_usage": usage,
    }
    _write_json(work_dir / "REVIEW_REPLAY_SUMMARY.json", summary)
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
