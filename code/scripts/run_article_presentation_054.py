#!/usr/bin/env python3
"""Build the 054 Presentation package from the resolved 063 replay handoff."""

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
from optomind_optics.harness.article_manuscript import ArticleManuscriptPackage
from optomind_optics.harness.article_presentation import (
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
    parser = argparse.ArgumentParser(
        description="Build 054 Presentation from 063 replay handoff."
    )
    parser.add_argument("--source-pipeline-dir", type=Path, required=True)
    parser.add_argument("--synthesis-path", type=Path, required=True)
    parser.add_argument("--architecture-path", type=Path, required=True)
    parser.add_argument("--review-path", type=Path, required=True)
    parser.add_argument("--manuscript-path", type=Path, required=True)
    parser.add_argument("--reproducibility-path", type=Path, required=True)
    parser.add_argument("--literature-report-path", type=Path)
    parser.add_argument("--literature-supplement-path", type=Path)
    parser.add_argument(
        "--citation-audit",
        action="store_true",
        help="run the independent Qwen citation auditor after placement",
    )
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
    if args.literature_report_path:
        report = MethodResearchReport.model_validate(
            _read(args.literature_report_path.resolve())
        )
        evidence = [
            MethodEvidence.model_validate(item.model_dump(mode="json"))
            for item in report.evidence
        ]
    else:
        evidence = list(
            bundle.result.method_research.evidence
            if bundle.result.method_research
            else ()
        )
    bibliographic_metadata = None
    if args.literature_report_path:
        evidence_by_id = {item.evidence_id: item for item in evidence}
        required_evidence_ids = {
            evidence_id
            for paragraph in manuscript.source_map
            for evidence_id in paragraph.literature_evidence_ids
        }
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
    artifact_roots = [
        Path(item.source_run_dir)
        for item in reproducibility.replay_records
        if item.source_run_dir
    ]
    output = args.output_dir.resolve()
    package = build_article_presentation(
        synthesis.derived_plan,
        synthesis.ledger,
        architecture,
        review,
        manuscript,
        reproducibility,
        manuscript.story_id,
        values,
        evidence,
        artifact_roots,
        bibliographic_metadata=bibliographic_metadata,
        literature_supplement=literature_supplement,
        citation_provider=(
            QwenCitationPlacer()
            if args.citation_audit or args.literature_report_path
            else None
        ),
        front_matter_provider=(
            QwenFrontMatterWriter()
            if args.citation_audit or args.literature_report_path
            else None
        ),
        citation_auditor=QwenCitationAuditor() if args.citation_audit else None,
        output_dir=output,
    )
    output.mkdir(parents=True, exist_ok=True)
    (output / "ARTICLE_PRESENTATION_PACKAGE.json").write_text(
        json.dumps(package.model_dump(mode="json"), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "package_id": package.package_id,
                "status": package.status,
                "visuals": len(package.visuals),
                "citations": len(package.citations),
                "references": len(package.references),
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
