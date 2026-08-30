#!/usr/bin/env python3
"""Replay Article architecture through manuscript from persisted synthesis."""

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
    QwenArticleArchitecturePlanner,
    build_article_architecture,
    value_field_shapes,
)
from optomind_optics.harness.article_continuation import (  # noqa: E402
    _aggregate_usage,
    _contracted_inventory,
    _scoped_story_values,
    _select_story,
    load_source_pipeline,
)
from optomind_optics.harness.article_literature import (  # noqa: E402
    build_literature_provider_context,
    load_literature_supplement,
)
from optomind_optics.harness.article_manuscript import (  # noqa: E402
    ArticleManuscriptPackage,
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
    build_article_review,
)
from optomind_optics.harness.article_writing import (  # noqa: E402
    ArticleDraftBundle,
    QwenFormatRepair,
    QwenSectionWriter,
    build_article_draft_bundle,
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
            "Replay architecture, writing, review, and manuscript assembly "
            "from a persisted Article result-synthesis snapshot."
        )
    )
    parser.add_argument("--source-pipeline-dir", type=Path, required=True)
    parser.add_argument("--synthesis-path", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--literature-supplement-path", type=Path, required=True)
    parser.add_argument("--selected-story-id", default="")
    parser.add_argument(
        "--resume-existing",
        action="store_true",
        help=(
            "Do not call providers; rebuild UPPER_REPLAY_SUMMARY.json from "
            "the persisted 02-architecture through 05-manuscript checkpoints."
        ),
    )
    return parser


def _build_summary(
    *,
    synthesis: ArticleResultSynthesisResult,
    architecture: ArticleArchitectureResult,
    writing: ArticleDraftBundle,
    review: ArticleReviewResult,
    manuscript: ArticleManuscriptPackage,
    synthesis_path: Path,
    story_id: str,
    rationale: str,
    candidates: tuple[dict, ...],
) -> dict:
    usage = _aggregate_usage(
        [
            ("architecture", architecture),
            ("writing", writing),
            ("review", review),
        ]
    )
    blocker_messages = set(review.hard_blockers)
    blocker_messages.update(
        message
        for handoff in manuscript.blocked_handoff
        for message in handoff.hard_blockers
    )
    return {
        "status": manuscript.body.status,
        "source_synthesis_path": str(synthesis_path.resolve()),
        "source_synthesis_result_id": synthesis.result_id,
        "story_id": story_id,
        "story_selection_rationale": rationale,
        "story_candidates": list(candidates),
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
        "usage": usage,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    work_dir = args.work_dir.resolve()
    synthesis = ArticleResultSynthesisResult.model_validate(
        _read_json(args.synthesis_path)
    )
    if synthesis.derived_plan is None or synthesis.ledger is None:
        raise ValueError("synthesis snapshot has no derived plan or claim ledger")
    if args.resume_existing:
        if not work_dir.is_dir():
            raise ValueError("resume work directory does not exist")
        architecture = ArticleArchitectureResult.model_validate(
            _read_json(work_dir / "02-architecture.json")
        )
        writing = ArticleDraftBundle.model_validate(
            _read_json(work_dir / "03-writing.json")
        )
        review = ArticleReviewResult.model_validate(
            _read_json(work_dir / "04-review.json")
        )
        manuscript = ArticleManuscriptPackage.model_validate(
            _read_json(work_dir / "05-manuscript.json")
        )
        selection_errors: list[str] = []
        story_id, rationale, candidates = _select_story(
            architecture,
            args.selected_story_id or manuscript.story_id,
            selection_errors,
        )
        if selection_errors or story_id != manuscript.story_id:
            raise ValueError(
                "persisted story selection is inconsistent: "
                + "; ".join(selection_errors)
            )
        summary = _build_summary(
            synthesis=synthesis,
            architecture=architecture,
            writing=writing,
            review=review,
            manuscript=manuscript,
            synthesis_path=args.synthesis_path,
            story_id=story_id,
            rationale=rationale,
            candidates=candidates,
        )
        _write_json(work_dir / "UPPER_REPLAY_SUMMARY.json", summary)
        print(json.dumps(summary, ensure_ascii=True, indent=2))
        return 0

    work_dir.mkdir(parents=True, exist_ok=False)
    bundle = load_source_pipeline(args.source_pipeline_dir)
    supplement_dir = args.literature_supplement_path.resolve()
    supplement = load_literature_supplement(
        supplement_dir / "METHOD_RESEARCH_REPORT.json",
        supplement_dir / "ARTICLE_DIRECTOR_SUPPLEMENT_ALIAS_FINAL.json",
        expected_source_pipeline_result_id=bundle.result.result_id,
        expected_old_director_plan_id=bundle.plan.plan_id,
    )
    descriptors, values = _contracted_inventory(synthesis, bundle)

    architecture = build_article_architecture(
        synthesis.derived_plan,
        synthesis.ledger,
        descriptors,
        architecture_provider=QwenArticleArchitecturePlanner(),
        value_shapes=value_field_shapes(values),
    )
    _write_json(work_dir / "02-architecture.json", architecture)
    selection_errors: list[str] = []
    story_id, rationale, candidates = _select_story(
        architecture,
        args.selected_story_id,
        selection_errors,
    )
    if architecture.validation_errors or not story_id:
        raise ValueError(
            "architecture replay failed: "
            + "; ".join([*architecture.validation_errors, *selection_errors])
        )

    scoped_values = _scoped_story_values(
        architecture,
        story_id,
        values,
        synthesis.ledger,
    )
    writing = build_article_draft_bundle(
        synthesis.derived_plan,
        synthesis.ledger,
        architecture,
        story_id,
        scoped_values,
        section_writer=QwenSectionWriter(),
        format_repair=QwenFormatRepair(),
        literature_context=build_literature_provider_context(supplement),
        literature_evidence_alias_map=dict(supplement.evidence_aliases),
    )
    _write_json(work_dir / "03-writing.json", writing)
    if writing.errors or not writing.sections:
        raise ValueError("writing replay failed: " + "; ".join(writing.errors))

    review = build_article_review(
        synthesis.derived_plan,
        synthesis.ledger,
        architecture,
        writing,
        story_id,
        scoped_values,
        scientific_reviewer=QwenScientificReviewer(),
        expression_reviewer=QwenExpressionReviewer(),
        global_consistency_reviewer=QwenGlobalConsistencyReviewer(),
        global_advice_router=QwenGlobalAdviceRouter(),
        author_reviser=QwenAuthorReviser(),
    )
    _write_json(work_dir / "04-review.json", review)
    if not review.sections:
        raise ValueError("review replay produced no reviewed sections")

    manuscript = build_article_manuscript(
        synthesis.derived_plan,
        synthesis.ledger,
        architecture,
        review,
        story_id,
        scoped_values,
        output_dir=work_dir / "manuscript",
    )
    _write_json(work_dir / "05-manuscript.json", manuscript)
    if manuscript.errors:
        raise ValueError("manuscript replay failed: " + "; ".join(manuscript.errors))

    summary = _build_summary(
        synthesis=synthesis,
        architecture=architecture,
        writing=writing,
        review=review,
        manuscript=manuscript,
        synthesis_path=args.synthesis_path,
        story_id=story_id,
        rationale=rationale,
        candidates=candidates,
    )
    _write_json(work_dir / "UPPER_REPLAY_SUMMARY.json", summary)
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
