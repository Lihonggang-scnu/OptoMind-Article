#!/usr/bin/env python3
"""Run or resume the Article continuation (synthesis->manuscript)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

VERITMM_ROOT = ROOT.parent / "veritmm"
if str(VERITMM_ROOT) not in sys.path:
    sys.path.insert(0, str(VERITMM_ROOT))
if os.environ.get("OPTOMIND_REVIEW_ROOT"):
    sys.path.append(os.environ["OPTOMIND_REVIEW_ROOT"])

from optomind_optics.harness.article_architecture import (  # noqa: E402
    QwenArticleArchitecturePlanner,
)
from optomind_optics.harness.article_continuation import (  # noqa: E402
    ArticleContinuation,
    ContinuationRequest,
)
from optomind_optics.harness.article_result_synthesis import (  # noqa: E402
    QwenArticleResultClaimSynthesizer,
)
from optomind_optics.harness.article_review import (  # noqa: E402
    QwenAuthorReviser,
    QwenExpressionReviewer,
    QwenGlobalAdviceRouter,
    QwenGlobalConsistencyReviewer,
    QwenScientificReviewer,
)
from optomind_optics.harness.article_writing import (  # noqa: E402
    QwenFormatRepair,
    QwenSectionWriter,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run or resume the Article continuation from an accepted "
            "eight-stage pipeline run through result synthesis, "
            "architecture, writing, review, and manuscript assembly."
        )
    )
    parser.add_argument("--source-pipeline-dir", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--branch-id", default="root")
    parser.add_argument("--selected-story-id", default="")
    parser.add_argument("--literature-supplement-path", type=Path)
    parser.add_argument("--resume", action="store_true")
    science = parser.add_mutually_exclusive_group()
    parser.add_argument("--source-kind", choices=["legacy_pipeline", "scientific_result"], default="legacy_pipeline")
    science.add_argument("--scientific-selection", type=Path, help="Repository-backed AuthoringSelection JSON")
    science.add_argument("--disable-scientific-selection", action="store_true", help="Explicit ordinary legacy authoring path")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preview", action="store_true", help="Validate inputs with no providers or output writes (default)")
    mode.add_argument("--run", action="store_true", help="Explicitly run existing production author providers; may incur cost")
    parser.add_argument("--review-root", type=Path, help="Optional explicit sibling Review repository, or OPTOMIND_REVIEW_ROOT")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.review_root:
        sys.path.append(str(args.review_root.resolve()))
        from pkgutil import extend_path
        import optomind_research, optomind_research.runtime
        optomind_research.__path__ = extend_path(optomind_research.__path__, optomind_research.__name__)
        optomind_research.runtime.__path__ = extend_path(optomind_research.runtime.__path__, optomind_research.runtime.__name__)
    try:
        request = ContinuationRequest(
            run_id=args.run_id,
            branch_id=args.branch_id,
            source_pipeline_dir=str(Path(args.source_pipeline_dir).resolve()),
            work_dir=str(Path(args.work_dir).resolve()),
            selected_story_id=args.selected_story_id,
            source_kind=args.source_kind,
            scientific_selection=(json.loads(args.scientific_selection.read_text()) if args.scientific_selection else None),
            literature_supplement_path=(
                str(Path(args.literature_supplement_path).resolve())
                if args.literature_supplement_path
                else ""
            ),
        )
        if not args.run:
            from optomind_optics.harness.article_continuation import load_continuation_source, continuation_request_digest
            from optomind_optics.harness.science.authoring_selection import resolve_authoring_selection
            try:
                bundle = load_continuation_source(request, Path(request.source_pipeline_dir))
                context = resolve_authoring_selection(request.scientific_selection, bundle.assets) if request.scientific_selection else None
                if args.resume:
                    prior_path = Path(request.work_dir) / "CONTINUATION_REQUEST.json"
                    prior = ContinuationRequest.model_validate_json(prior_path.read_text())
                    if continuation_request_digest(prior) != continuation_request_digest(request):
                        raise ValueError("resume request identity mismatch")
            except Exception as exc:
                print(json.dumps({"status":"failed","mode":"preview","error":str(exc),"provider_calls":0}))
                return 1
            print(json.dumps({"status":"preview_ready","source_result_id":bundle.result.result_id,
                "adopted_result_ref":context["adopted_result_ref"] if context else None,
                "provider_calls":0,"output_writes":0,"resume_checkpoint_chain_validated":False}))
            return 0
        continuation = ArticleContinuation(
            result_synthesis_provider=QwenArticleResultClaimSynthesizer(),
            architecture_provider=QwenArticleArchitecturePlanner(),
            section_writer=QwenSectionWriter(),
            format_repair=QwenFormatRepair(),
            scientific_reviewer=QwenScientificReviewer(),
            expression_reviewer=QwenExpressionReviewer(),
            global_consistency_reviewer=QwenGlobalConsistencyReviewer(),
            global_advice_router=QwenGlobalAdviceRouter(),
            author_reviser=QwenAuthorReviser(),
        )
        result = (
            continuation.resume(request) if args.resume else continuation.run(request)
        )
        print(
            json.dumps(
                {
                    "status": result.status,
                    "result_id": result.result_id,
                    "run_id": result.run_id,
                    "source_pipeline_dir": result.source_pipeline_dir,
                    "selected_story_id": result.selected_story_id,
                    "counts": result.counts,
                    "usage_totals": result.usage.get("totals", {}),
                    "warnings": result.warnings,
                    "errors": result.errors,
                    "receipts": [
                        {
                            "sequence": item.sequence,
                            "stage": item.stage,
                            "status": item.status,
                        }
                        for item in result.receipts
                    ],
                },
                ensure_ascii=True,
                indent=2,
            )
        )
        if result.status == "completed":
            return 0
        if result.status == "partial":
            return 0
        if result.status == "unavailable":
            return 2
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(
            json.dumps(
                {
                    "status": "configuration_error",
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:800],
                },
                ensure_ascii=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
