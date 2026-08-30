"""Finalize a persisted Article continuation into a reproducible PDF package.

The entry point is intentionally read-only with respect to TMM execution.  It
revalidates persisted fresh-replay manifests and fails if they are absent or
changed; it never falls back to executing an experiment again.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from optomind_optics.harness.article_architecture import (
    ArticleArchitectureResult,
)
from optomind_optics.harness.article_continuation import (
    _contracted_inventory,
    _scoped_story_values,
    load_source_pipeline,
)
from optomind_optics.harness.article_delivery import (
    PublicationAuthor,
    PublicationMetadata,
    build_article_delivery,
    validate_delivery_package,
)
from optomind_optics.harness.article_manuscript import (
    ArticleManuscriptPackage,
)
from optomind_optics.harness.article_presentation import (
    ArticlePresentationPackage,
    QwenCitationPlacer,
    QwenFrontMatterWriter,
    build_article_presentation,
    validate_presentation_package,
    write_presentation_package,
)
from optomind_optics.harness.article_reproducibility import (
    ArticleReproducibilityPackage,
    build_article_reproducibility,
    validate_reproducibility_package,
)
from optomind_optics.harness.article_result_synthesis import (
    ArticleResultSynthesisResult,
)
from optomind_optics.harness.article_review import ArticleReviewResult
from optomind_optics.harness.article_writing import ArticleDraftBundle
from optomind_optics.harness.method_research import MethodResearchReport
from optomind_research.runtime.artifact_store import atomic_write_json
from optomind_research.runtime.latex_publication_renderer import (
    _s2_metadata_for_ids,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
INTEGRATION_ROOT = REPO_ROOT / "stage17_real_integration"
DEFAULT_CONTINUATION_DIR = (
    INTEGRATION_ROOT / "article_continuation_035c_format_tolerant"
)
DEFAULT_SOURCE_PIPELINE_DIR = INTEGRATION_ROOT / "selective_emitter_006" / "pipeline"
DEFAULT_RUNS_ROOT = INTEGRATION_ROOT / "selective_emitter_006" / "execution"
DEFAULT_REPLAY_PACKAGE = (
    INTEGRATION_ROOT
    / "article_reproducibility_probe_018_revalidated_fresh_replay"
    / "ARTICLE_REPRODUCIBILITY_PACKAGE.json"
)
DEFAULT_METHOD_RESEARCH_REPORT = (
    INTEGRATION_ROOT
    / "article_method_research_probe027_online_s2_reclassified"
    / "METHOD_RESEARCH_REPORT.json"
)
DEFAULT_OUTPUT_DIR = INTEGRATION_ROOT / "article_finalization_036"


class FinalizationError(RuntimeError):
    """The persisted Article chain cannot be finalized safely."""


def _read_json(path: Path) -> Any:
    if not path.is_file():
        raise FinalizationError(f"required input is missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FinalizationError(f"cannot read JSON input {path}: {exc}") from exc


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _load_continuation(directory: Path) -> Dict[str, Any]:
    synthesis = ArticleResultSynthesisResult.model_validate(
        _read_json(directory / "01-result_synthesis.json")
    )
    architecture = ArticleArchitectureResult.model_validate(
        _read_json(directory / "02-architecture.json")
    )
    writing = ArticleDraftBundle.model_validate(
        _read_json(directory / "03-writing.json")
    )
    review = ArticleReviewResult.model_validate(
        _read_json(directory / "04-review.json")
    )
    manuscript = ArticleManuscriptPackage.model_validate(
        _read_json(directory / "05-manuscript.json")
    )
    final = _read_json(directory / "FINAL_CONTINUATION_RESULT.json")
    plan = synthesis.derived_plan
    ledger = synthesis.ledger
    story_id = manuscript.story_id
    expected = {
        "architecture.source_plan_id": (architecture.source_plan_id, plan.plan_id),
        "architecture.source_ledger_id": (
            architecture.source_ledger_id,
            ledger.ledger_id,
        ),
        "writing.plan_id": (writing.plan_id, plan.plan_id),
        "writing.ledger_id": (writing.ledger_id, ledger.ledger_id),
        "writing.architecture_id": (
            writing.architecture_id,
            architecture.architecture_id,
        ),
        "writing.story_id": (writing.story_id, story_id),
        "review.plan_id": (review.plan_id, plan.plan_id),
        "review.ledger_id": (review.ledger_id, ledger.ledger_id),
        "review.architecture_id": (
            review.architecture_id,
            architecture.architecture_id,
        ),
        "review.bundle_id": (review.bundle_id, writing.bundle_id),
        "review.story_id": (review.story_id, story_id),
        "manuscript.plan_id": (manuscript.plan_id, plan.plan_id),
        "manuscript.ledger_id": (manuscript.ledger_id, ledger.ledger_id),
        "manuscript.architecture_id": (
            manuscript.architecture_id,
            architecture.architecture_id,
        ),
        "manuscript.review_id": (manuscript.review_id, review.review_id),
        "manuscript.result_id": (manuscript.result_id, review.result_id),
        "final.selected_story_id": (
            str(final.get("selected_story_id") or ""),
            story_id,
        ),
    }
    failures = [
        f"{label}: {actual!r} != {wanted!r}"
        for label, (actual, wanted) in expected.items()
        if actual != wanted
    ]
    if failures:
        raise FinalizationError(
            "continuation identity chain is inconsistent: " + "; ".join(failures)
        )
    return {
        "synthesis": synthesis,
        "plan": plan,
        "ledger": ledger,
        "architecture": architecture,
        "writing": writing,
        "review": review,
        "manuscript": manuscript,
        "story_id": story_id,
    }


def _persisted_replay_provider(
    package_path: Path,
):
    accepted = ArticleReproducibilityPackage.model_validate(_read_json(package_path))
    manifest_by_run = {
        Path(record.source_run_dir).resolve(): dict(record.manifest)
        for record in accepted.replay_records
        if record.status == "completed" and record.source_run_dir and record.manifest
    }
    if not manifest_by_run:
        raise FinalizationError(
            f"accepted replay package has no completed manifests: {package_path}"
        )

    def provider(source_run_dir: str | Path) -> Mapping[str, Any]:
        run_dir = Path(source_run_dir).resolve()
        stored = manifest_by_run.get(run_dir)
        if stored is None:
            raise FinalizationError(
                f"no accepted replay manifest exists for source run {run_dir}"
            )
        run_manifest_path = run_dir / "REPLAY_MANIFEST.json"
        on_disk = _read_json(run_manifest_path)
        if _canonical_json(on_disk) != _canonical_json(stored):
            raise FinalizationError(
                f"source replay manifest changed after acceptance: {run_manifest_path}"
            )
        return stored

    return provider


def _required_evidence_ids(
    chain: Mapping[str, Any],
) -> set[str]:
    plan = chain["plan"]
    ledger = chain["ledger"]
    manuscript = chain["manuscript"]
    claims = {item.claim_id: item for item in ledger.claims}
    hypotheses = {item.hypothesis_id: item for item in plan.hypotheses}
    required: set[str] = set()
    for paragraph in manuscript.source_map:
        required.update(paragraph.literature_evidence_ids)
        for claim_id in paragraph.claim_ids:
            claim = claims.get(claim_id)
            if claim is None:
                continue
            hypothesis = hypotheses.get(str(claim.metadata.get("hypothesis_id") or ""))
            if hypothesis is not None:
                required.update(hypothesis.evidence_ids)
    return required


def _resolve_bibliographic_metadata(
    evidence_by_id: Mapping[str, Any],
    required_evidence_ids: set[str],
    *,
    allow_s2: bool,
) -> tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    evidence = [
        evidence_by_id[evidence_id]
        for evidence_id in sorted(required_evidence_ids)
        if evidence_id in evidence_by_id
    ]
    missing_evidence = sorted(required_evidence_ids - set(evidence_by_id))
    paper_ids = sorted({item.paper_id for item in evidence})
    remote = _s2_metadata_for_ids(paper_ids) if allow_s2 else {}
    resolved: Dict[str, Dict[str, Any]] = {}
    unresolved: list[str] = []
    for item in evidence:
        row = dict(remote.get(item.paper_id, {}) or {})
        authors = [
            str(author).strip()
            for author in row.get("authors", [])
            if str(author).strip()
        ]
        if not authors:
            unresolved.append(item.paper_id)
            continue
        metadata: Dict[str, Any] = {
            "authors": authors,
            "venue": str(row.get("venue") or ""),
            "url": str(row.get("url") or ""),
        }
        if row.get("year") not in (None, ""):
            metadata["year"] = int(row["year"])
        # Non-empty evidence identities remain authoritative.  Supplying DOI
        # or title here would create a second identity authority, so the
        # presentation layer receives only enrichment fields.
        resolved[item.paper_id] = metadata
    audit = {
        "schema_version": "article-bibliographic-resolution.v1",
        "required_evidence_ids": sorted(required_evidence_ids),
        "required_paper_ids": paper_ids,
        "resolved_paper_ids": sorted(resolved),
        "unresolved_paper_ids": sorted(set(unresolved)),
        "missing_evidence_ids": missing_evidence,
        "s2_enabled": allow_s2,
        "s2_records_returned": len(remote),
        "metadata": resolved,
    }
    return resolved, audit


def _publication_metadata(path: Optional[Path]) -> PublicationMetadata:
    if path is not None:
        return PublicationMetadata.model_validate(_read_json(path))
    return PublicationMetadata(
        authors=[
            PublicationAuthor(
                name="OptoMind Research Team",
                affiliations=["OptoMind"],
                corresponding=True,
            )
        ],
        date="2026-08-17",
        acknowledgements="",
        draft=True,
    )


def run_finalization(
    *,
    continuation_dir: Path,
    source_pipeline_dir: Path,
    runs_root: Path,
    replay_package: Path,
    method_research_report: Path,
    output_dir: Path,
    publication_metadata_path: Optional[Path] = None,
    reuse_presentation_path: Optional[Path] = None,
    use_qwen: bool = True,
    allow_s2_metadata: bool = True,
    compile_pdf: bool = True,
) -> Dict[str, Any]:
    started = time.perf_counter()
    timings: Dict[str, float] = {}

    stage_started = time.perf_counter()
    chain = _load_continuation(continuation_dir.resolve())
    bundle = load_source_pipeline(source_pipeline_dir.resolve())
    report = MethodResearchReport.model_validate(_read_json(method_research_report))
    evidence_by_id = {item.evidence_id: item for item in report.evidence}
    _, all_values = _contracted_inventory(chain["synthesis"], bundle)
    values = _scoped_story_values(
        chain["architecture"],
        chain["story_id"],
        all_values,
        ledger=chain["ledger"],
    )
    timings["load_and_identity_seconds"] = round(time.perf_counter() - stage_started, 6)

    output_dir.mkdir(parents=True, exist_ok=True)
    stage_started = time.perf_counter()
    reproducibility = build_article_reproducibility(
        chain["plan"],
        chain["ledger"],
        chain["architecture"],
        chain["review"],
        chain["manuscript"],
        chain["story_id"],
        values,
        bundle.executions,
        runs_root.resolve(),
        replay_provider=_persisted_replay_provider(replay_package.resolve()),
        output_dir=output_dir / "reproducibility",
    )
    repro_errors: list[str] = []
    repro_warnings: list[str] = []
    repro_valid = validate_reproducibility_package(
        reproducibility,
        chain["plan"],
        chain["ledger"],
        chain["architecture"],
        chain["review"],
        chain["manuscript"],
        chain["story_id"],
        values,
        repro_errors,
        repro_warnings,
    )
    if not repro_valid or reproducibility.status == "blocked":
        raise FinalizationError(
            "reproducibility validation failed: "
            + "; ".join(
                repro_errors
                or reproducibility.errors
                or [item.message for item in reproducibility.blockers]
            )
        )
    timings["reproducibility_seconds"] = round(time.perf_counter() - stage_started, 6)

    stage_started = time.perf_counter()
    required_evidence_ids = _required_evidence_ids(chain)
    metadata, metadata_audit = _resolve_bibliographic_metadata(
        evidence_by_id,
        required_evidence_ids,
        allow_s2=allow_s2_metadata,
    )
    atomic_write_json(output_dir / "BIBLIOGRAPHIC_RESOLUTION.json", metadata_audit)
    timings["bibliographic_resolution_seconds"] = round(
        time.perf_counter() - stage_started, 6
    )

    stage_started = time.perf_counter()
    if reuse_presentation_path is not None:
        presentation = ArticlePresentationPackage.model_validate(
            _read_json(reuse_presentation_path.resolve())
        )
    else:
        presentation = build_article_presentation(
            chain["plan"],
            chain["ledger"],
            chain["architecture"],
            chain["review"],
            chain["manuscript"],
            reproducibility,
            chain["story_id"],
            values,
            report.evidence,
            [item.source_run_dir for item in reproducibility.replay_records],
            bibliographic_metadata=metadata,
            citation_provider=QwenCitationPlacer() if use_qwen else None,
            front_matter_provider=QwenFrontMatterWriter() if use_qwen else None,
            output_dir=output_dir / "presentation",
        )
    presentation_errors: list[str] = []
    presentation_warnings: list[str] = []
    presentation_valid = validate_presentation_package(
        presentation,
        plan=chain["plan"],
        ledger=chain["ledger"],
        architecture=chain["architecture"],
        review=chain["review"],
        manuscript=chain["manuscript"],
        reproducibility=reproducibility,
        selected_story_id=chain["story_id"],
        value_records=values,
        require_body_provenance=True,
        errors=presentation_errors,
        warnings=presentation_warnings,
    )
    if not presentation_valid or presentation.status == "blocked":
        raise FinalizationError(
            "presentation validation failed: "
            + "; ".join(
                presentation_errors
                or presentation.errors
                or [item.message for item in presentation.blockers]
            )
        )
    if reuse_presentation_path is not None:
        write_presentation_package(
            presentation,
            output_dir / "presentation",
            plan=chain["plan"],
            ledger=chain["ledger"],
            architecture=chain["architecture"],
            review=chain["review"],
            manuscript=chain["manuscript"],
            reproducibility=reproducibility,
            selected_story_id=chain["story_id"],
            value_records=values,
        )
    timings["presentation_seconds"] = round(time.perf_counter() - stage_started, 6)

    stage_started = time.perf_counter()
    delivery = build_article_delivery(
        chain["plan"],
        chain["ledger"],
        chain["architecture"],
        chain["review"],
        chain["manuscript"],
        reproducibility,
        presentation,
        chain["story_id"],
        values,
        _publication_metadata(publication_metadata_path),
        renderer=None,
        compile_pdf=compile_pdf,
        output_dir=output_dir / "delivery",
    )
    delivery_errors: list[str] = []
    delivery_warnings: list[str] = []
    delivery_valid = validate_delivery_package(
        delivery,
        plan=chain["plan"],
        ledger=chain["ledger"],
        architecture=chain["architecture"],
        review=chain["review"],
        manuscript=chain["manuscript"],
        reproducibility=reproducibility,
        presentation=presentation,
        selected_story_id=chain["story_id"],
        value_records=values,
        output_dir=output_dir / "delivery",
        errors=delivery_errors,
        warnings=delivery_warnings,
    )
    timings["delivery_seconds"] = round(time.perf_counter() - stage_started, 6)
    timings["total_seconds"] = round(time.perf_counter() - started, 6)

    status = delivery.status
    if not delivery_valid and status not in {"blocked", "failed"}:
        status = "failed"
    summary = {
        "schema_version": "article-finalization-summary.v1",
        "status": status,
        "input_identity": {
            "plan_id": chain["plan"].plan_id,
            "ledger_id": chain["ledger"].ledger_id,
            "architecture_id": chain["architecture"].architecture_id,
            "review_id": chain["review"].review_id,
            "result_id": chain["review"].result_id,
            "manuscript_body_id": chain["manuscript"].body_id,
            "story_id": chain["story_id"],
        },
        "reproducibility": {
            "package_id": reproducibility.package_id,
            "status": reproducibility.status,
            "critical_experiments": len(reproducibility.critical_experiments),
            "completed_replays": sum(
                item.status == "completed" for item in reproducibility.replay_records
            ),
            "lineage_records": len(reproducibility.lineage),
            "blockers": len(reproducibility.blockers),
            "warnings": list(reproducibility.warnings),
        },
        "presentation": {
            "package_id": presentation.package_id,
            "status": presentation.status,
            "citations": len(presentation.citations),
            "references": len(presentation.references),
            "visuals": len(presentation.visuals),
            "model_name": presentation.model_name,
            "attempts": presentation.attempts,
            "usage": dict(presentation.usage),
            "blockers": len(presentation.blockers),
            "warnings": list(presentation.warnings),
        },
        "delivery": {
            "package_id": delivery.package_id,
            "status": delivery.status,
            "renderer_name": delivery.renderer_name,
            "renderer_status": delivery.renderer_status,
            "renderer_invoked": delivery.renderer_invoked,
            "compile_pdf": delivery.compile_pdf,
            "citation_count": delivery.citation_count,
            "reference_count": delivery.reference_count,
            "figure_count": delivery.figure_count,
            "table_count": delivery.table_count,
            "reference_metadata_complete": delivery.reference_metadata_complete,
            "cost": delivery.cost.model_dump(mode="json"),
            "blockers": [item.model_dump(mode="json") for item in delivery.blockers],
            "warnings": list(delivery.warnings),
            "errors": list(delivery.errors),
        },
        "validation": {
            "reproducibility_valid": repro_valid,
            "presentation_valid": presentation_valid,
            "delivery_valid": delivery_valid,
            "warnings": {
                "reproducibility": repro_warnings,
                "presentation": presentation_warnings,
                "delivery": delivery_warnings,
            },
            "errors": {
                "reproducibility": repro_errors,
                "presentation": presentation_errors,
                "delivery": delivery_errors,
            },
        },
        "bibliographic_resolution": metadata_audit,
        "resume": {
            "presentation_reused": reuse_presentation_path is not None,
            "presentation_source": (
                str(reuse_presentation_path.resolve())
                if reuse_presentation_path is not None
                else ""
            ),
        },
        "timings": timings,
    }
    atomic_write_json(output_dir / "ARTICLE_FINALIZATION_SUMMARY.json", summary)
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Finalize a persisted Article continuation without rerunning TMM"
    )
    parser.add_argument("--continuation-dir", default=str(DEFAULT_CONTINUATION_DIR))
    parser.add_argument(
        "--source-pipeline-dir", default=str(DEFAULT_SOURCE_PIPELINE_DIR)
    )
    parser.add_argument("--runs-root", default=str(DEFAULT_RUNS_ROOT))
    parser.add_argument("--replay-package", default=str(DEFAULT_REPLAY_PACKAGE))
    parser.add_argument(
        "--method-research-report",
        default=str(DEFAULT_METHOD_RESEARCH_REPORT),
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--publication-metadata", default="")
    parser.add_argument(
        "--reuse-presentation",
        default="",
        help=(
            "reuse and revalidate a persisted Presentation package instead "
            "of calling Qwen again"
        ),
    )
    parser.add_argument(
        "--skip-qwen",
        action="store_true",
        help="use deterministic citation/front-matter fallbacks",
    )
    parser.add_argument(
        "--skip-s2-metadata",
        action="store_true",
        help="do not resolve missing bibliography metadata through S2",
    )
    parser.add_argument(
        "--no-compile-pdf",
        action="store_true",
        help="render TeX and audits without compiling a PDF",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = _parser().parse_args(argv)
    output_dir = Path(args.output_dir).resolve()
    try:
        summary = run_finalization(
            continuation_dir=Path(args.continuation_dir),
            source_pipeline_dir=Path(args.source_pipeline_dir),
            runs_root=Path(args.runs_root),
            replay_package=Path(args.replay_package),
            method_research_report=Path(args.method_research_report),
            output_dir=output_dir,
            publication_metadata_path=(
                Path(args.publication_metadata)
                if str(args.publication_metadata).strip()
                else None
            ),
            reuse_presentation_path=(
                Path(args.reuse_presentation)
                if str(args.reuse_presentation).strip()
                else None
            ),
            use_qwen=not args.skip_qwen,
            allow_s2_metadata=not args.skip_s2_metadata,
            compile_pdf=not args.no_compile_pdf,
        )
    except Exception as exc:
        output_dir.mkdir(parents=True, exist_ok=True)
        failure = {
            "schema_version": "article-finalization-summary.v1",
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        atomic_write_json(output_dir / "ARTICLE_FINALIZATION_FAILURE.json", failure)
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return (
        0
        if summary["status"]
        in {
            "submission_ready",
            "compiled_awaiting_metadata",
        }
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
