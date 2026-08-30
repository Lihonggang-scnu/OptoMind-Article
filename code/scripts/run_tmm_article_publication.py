"""Publish one completed TMM research run as an article and LaTeX/PDF package."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optomind_optics.harness.article_publication import (  # noqa: E402
    build_tmm_article_publication,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--force-mock", action="store_true")
    parser.add_argument("--no-compile", action="store_true")
    parser.add_argument("--no-preview", action="store_true")
    parser.add_argument("--no-reference-enrichment", action="store_true")
    parser.add_argument("--draft-path", default="")
    parser.add_argument("--bibliography-cache", default="")
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    output_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else run_dir / "article_publication"
    )
    report = build_tmm_article_publication(
        run_dir=run_dir,
        output_dir=output_dir,
        force_mock=True if args.force_mock else None,
        compile_pdf=not args.no_compile,
        render_previews=not args.no_preview,
        enrich_references=not args.no_reference_enrichment,
        draft_path=Path(args.draft_path).resolve() if args.draft_path else None,
        bibliography_cache_path=(
            Path(args.bibliography_cache).resolve() if args.bibliography_cache else None
        ),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
