#!/usr/bin/env python3
"""Run or resume the production eight-stage Article/TMM pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optomind_optics.harness.article_pipeline_integration import (  # noqa: E402
    ArticleIntegrationError,
    ArticleIntegrationOptions,
    execute_article_pipeline_integration,
    integration_exit_code,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run or resume the accepted eight-stage Article pipeline using "
            "the production factory. Credentials are read only by existing "
            "environment-based providers."
        )
    )
    question = parser.add_mutually_exclusive_group(required=True)
    question.add_argument("--question", help="Natural-language research question")
    question.add_argument(
        "--question-file", type=Path, help="UTF-8 text file containing the question"
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--branch-id", default="root")
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--execution-root", type=Path, required=True)
    parser.add_argument(
        "--article-memory-path",
        type=Path,
        help="Optional Article-owned memory path; defaults to work-dir/ARTICLE_MEMORY.sqlite",
    )
    parser.add_argument(
        "--review-kb",
        action="append",
        default=[],
        type=Path,
        help="Read-only ReviewKnowledgeBase SQLite path; repeat as needed",
    )
    parser.add_argument("--maximum-routes", type=int, default=4)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--force-mock", action="store_true")
    parser.add_argument("--online-research", action="store_true")
    return parser


def _question_from_args(args: argparse.Namespace) -> str:
    if args.question is not None:
        question = str(args.question).strip()
    else:
        try:
            question = args.question_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ArticleIntegrationError(
                f"cannot read question file: {type(exc).__name__}: {exc}"
            ) from exc
    if not question:
        raise ArticleIntegrationError("question must not be empty")
    return question


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        execution = execute_article_pipeline_integration(
            ArticleIntegrationOptions(
                question=_question_from_args(args),
                run_id=args.run_id,
                branch_id=args.branch_id,
                work_dir=str(args.work_dir),
                execution_root=str(args.execution_root),
                article_memory_path=(
                    str(args.article_memory_path) if args.article_memory_path else None
                ),
                review_kb_paths=tuple(str(path) for path in args.review_kb),
                maximum_routes=args.maximum_routes,
                resume=args.resume,
                force_mock=True if args.force_mock else None,
                online_research=args.online_research,
            )
        )
    except (ArticleIntegrationError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "configuration_error",
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:800],
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        print(
            json.dumps(
                {
                    "status": "interrupted",
                    "message": "Run interrupted; use --resume with the same immutable request.",
                }
            ),
            file=sys.stderr,
        )
        return 130
    except Exception as exc:  # defensive CLI boundary; stages handle provider errors
        print(
            json.dumps(
                {
                    "status": "integration_error",
                    "error_type": type(exc).__name__,
                    "message": "Integration failed; inspect preserved local artifacts.",
                }
            ),
            file=sys.stderr,
        )
        return 1

    totals = execution.summary["qwen_usage"]["totals"]
    print(
        json.dumps(
            {
                "status": execution.result.status,
                "run_id": execution.result.run_id,
                "result_id": execution.result.result_id,
                "summary_path": execution.summary_path,
                "execution_count": execution.result.execution_count,
                "trusted_descriptor_count": execution.summary[
                    "trusted_descriptor_count"
                ],
                "qwen_billable_total_tokens": totals["billable_total_tokens"],
                "qwen_estimated_list_price_cost_cny": totals[
                    "estimated_list_price_cost_cny"
                ],
                "elapsed_seconds": execution.summary["elapsed_seconds"],
            },
            ensure_ascii=False,
        )
    )
    return integration_exit_code(execution.result.status)


if __name__ == "__main__":
    raise SystemExit(main())
