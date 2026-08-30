#!/usr/bin/env python3
"""Build the 054 Article Delivery package from the resolved Presentation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optomind_optics.harness.article_architecture import ArticleArchitectureResult
from optomind_optics.harness.article_citation_audit import QwenCitationAuditor
from optomind_optics.harness.article_continuation import (
    _contracted_inventory,
    _scoped_story_values,
    load_source_pipeline,
)
from optomind_optics.harness.article_literature import load_literature_supplement
from optomind_optics.harness.article_delivery import (
    PublicationAuthor,
    PublicationMetadata,
    build_article_delivery,
)
from optomind_optics.harness.article_manuscript import ArticleManuscriptPackage
from optomind_optics.harness.article_presentation import (
    ArticlePresentationPackage,
    QwenCitationPlacer,
    QwenFrontMatterWriter,
    build_article_presentation,
)
from optomind_optics.harness.article_reproducibility import (
    ArticleReproducibilityPackage,
)
from optomind_optics.harness.article_result_synthesis import (
    ArticleResultSynthesisResult,
)
from optomind_optics.harness.article_review import ArticleReviewResult
from optomind_optics.harness.method_research import MethodEvidence, MethodResearchReport
from optomind_research.runtime.latex_publication_renderer import _s2_metadata_for_ids


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build 054 Article Delivery package.")
    parser.add_argument("--source-pipeline-dir", type=Path, required=True)
    parser.add_argument("--synthesis-path", type=Path, required=True)
    parser.add_argument("--architecture-path", type=Path, required=True)
    parser.add_argument("--review-path", type=Path, required=True)
    parser.add_argument("--manuscript-path", type=Path, required=True)
    parser.add_argument("--reproducibility-path", type=Path, required=True)
    parser.add_argument("--presentation-path", type=Path, required=True)
    parser.add_argument("--literature-report-path", type=Path)
    parser.add_argument("--literature-supplement-path", type=Path)
    parser.add_argument("--citation-audit", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    source = args.source_pipeline_dir.resolve()
    bundle = load_source_pipeline(source)
    synthesis = ArticleResultSynthesisResult.model_validate(
        _read(args.synthesis_path.resolve())
    )
    if synthesis.derived_plan is None or synthesis.ledger is None:
        raise ValueError("synthesis has no derived plan/ledger")
    architecture = ArticleArchitectureResult.model_validate(
        _read(args.architecture_path.resolve())
    )
    review = ArticleReviewResult.model_validate(_read(args.review_path.resolve()))
    manuscript = ArticleManuscriptPackage.model_validate(
        _read(args.manuscript_path.resolve())
    )
    reproducibility = ArticleReproducibilityPackage.model_validate(
        _read(args.reproducibility_path.resolve())
    )
    presentation = ArticlePresentationPackage.model_validate(
        _read(args.presentation_path.resolve())
    )
    literature_supplement = None
    if args.literature_supplement_path:
        supplement_dir = args.literature_supplement_path.resolve()
        literature_supplement = load_literature_supplement(
            supplement_dir / "METHOD_RESEARCH_REPORT.json",
            supplement_dir / "ARTICLE_DIRECTOR_SUPPLEMENT_ALIAS_FINAL.json",
            expected_source_pipeline_result_id=bundle.result.result_id,
            expected_old_director_plan_id=synthesis.derived_plan.plan_id,
        )
    _, values = _contracted_inventory(synthesis, bundle)
    values = _scoped_story_values(
        architecture, manuscript.story_id, values, synthesis.ledger
    )
    bibliographic_metadata = None
    if args.literature_report_path:
        report = MethodResearchReport.model_validate(
            _read(args.literature_report_path.resolve())
        )
        evidence = [
            MethodEvidence.model_validate(item.model_dump(mode="json"))
            for item in report.evidence
        ]
        evidence_by_id = {item.evidence_id: item for item in evidence}
        claims_by_id = {claim.claim_id: claim for claim in synthesis.ledger.claims}
        hypotheses = {
            item.hypothesis_id: item for item in synthesis.derived_plan.hypotheses
        }
        required_evidence_ids = {
            evidence_id
            for paragraph in manuscript.source_map
            for evidence_id in paragraph.literature_evidence_ids
        }
        for paragraph in manuscript.source_map:
            for claim_id in paragraph.claim_ids:
                claim = claims_by_id.get(claim_id)
                if claim is None:
                    continue
                hypothesis = hypotheses.get(
                    str(claim.metadata.get("hypothesis_id") or "")
                )
                if hypothesis is not None:
                    required_evidence_ids.update(hypothesis.evidence_ids)
        paper_ids = sorted(
            {
                evidence_by_id[evidence_id].paper_id
                for evidence_id in required_evidence_ids
                if evidence_id in evidence_by_id
            }
        )
        remote = _s2_metadata_for_ids(paper_ids)
        bibliographic_metadata = {}
        for paper_id in paper_ids:
            row = dict(remote.get(paper_id) or {})
            authors = [
                str(author).strip()
                for author in row.get("authors", [])
                if str(author).strip()
            ]
            if not authors:
                continue
            metadata = {
                "authors": authors,
                "venue": str(row.get("venue") or ""),
                "url": str(row.get("url") or ""),
            }
            if row.get("year") not in (None, ""):
                metadata["year"] = int(row["year"])
            bibliographic_metadata[paper_id] = metadata
        presentation = build_article_presentation(
            synthesis.derived_plan,
            synthesis.ledger,
            architecture,
            review,
            manuscript,
            reproducibility,
            manuscript.story_id,
            values,
            evidence,
            [
                Path(item.source_run_dir)
                for item in reproducibility.replay_records
                if item.source_run_dir
            ],
            bibliographic_metadata=bibliographic_metadata,
            citation_provider=QwenCitationPlacer(),
            front_matter_provider=QwenFrontMatterWriter(),
            literature_supplement=literature_supplement,
            citation_auditor=QwenCitationAuditor() if args.citation_audit else None,
        )
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.literature_report_path:
        (output / "REBUILT_PRESENTATION_PACKAGE.json").write_text(
            json.dumps(
                presentation.model_dump(mode="json"), ensure_ascii=False, indent=2
            )
            + "\n",
            encoding="utf-8",
        )
    package = build_article_delivery(
        synthesis.derived_plan,
        synthesis.ledger,
        architecture,
        review,
        manuscript,
        reproducibility,
        presentation,
        manuscript.story_id,
        values,
        PublicationMetadata(
            authors=[
                PublicationAuthor(
                    name="OptoMind Research Team",
                    affiliations=["OptoMind"],
                    corresponding=True,
                )
            ],
            draft=True,
        ),
        compile_pdf=True,
        output_dir=output,
    )
    (output / "ARTICLE_DELIVERY_PACKAGE.json").write_text(
        json.dumps(package.model_dump(mode="json"), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "package_id": package.package_id,
                "status": package.status,
                "visuals": package.figure_count,
                "references": package.reference_count,
                "blockers": len(package.blockers),
                "warnings": package.warnings,
                "errors": package.errors,
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
