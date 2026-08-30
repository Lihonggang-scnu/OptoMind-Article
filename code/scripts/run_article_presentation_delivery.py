"""Offline Stage 12C/12D integration entry point for the Article handoff.

This script reconstructs the accepted real chain from persisted Stage 11/12A/
12B artifacts (continuation checkpoints, review probe 014, manuscript probe
015, reproducibility probe 018) and runs the deterministic Presentation ->
Delivery builders with no model/network calls.  A deterministic fake LaTeX
renderer is used unless a real toolchain is detected; tool availability is
reported explicitly.  Identity/provenance failures remain fail-closed and are
reported as diagnostics instead of fabricating inputs.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import shutil
import sys
import warnings
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

# Ensure the repo root (code/) is on sys.path so optomind_* packages resolve
# when this script is executed directly (e.g. via subprocess in tests).
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

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
)
from optomind_optics.harness.article_presentation import (
    build_article_presentation,
)
from optomind_optics.harness.article_result_synthesis import (
    ArticleResultSynthesisResult,
)
from optomind_research.runtime.artifact_store import atomic_write_json


REPO_ROOT = Path(__file__).resolve().parents[2]

CONTINUATION_DIR = (
    REPO_ROOT
    / "stage17_real_integration"
    / "article_continuation_007_tworepair"
)
REVIEW_PATH = (
    REPO_ROOT
    / "stage17_real_integration"
    / "article_review_probe_014_derived_plan_replay"
    / "ARTICLE_REVIEW_RESULT.json"
)
MANUSCRIPT_PATH = (
    REPO_ROOT
    / "stage17_real_integration"
    / "article_manuscript_probe_015_review014"
    / "ARTICLE_MANUSCRIPT_PACKAGE.json"
)
REPRODUCIBILITY_PATH = (
    REPO_ROOT
    / "stage17_real_integration"
    / "article_reproducibility_probe_018_revalidated_fresh_replay"
    / "ARTICLE_REPRODUCIBILITY_PACKAGE.json"
)
SOURCE_PIPELINE_DIR = (
    REPO_ROOT
    / "stage17_real_integration"
    / "selective_emitter_006"
    / "pipeline"
)


class ChainLoadError(RuntimeError):
    """Persisted upstream inputs are missing or unreadable."""


class ChainIdentityError(RuntimeError):
    """Persisted upstream identities do not form one accepted chain."""


def latex_toolchain_status() -> Dict[str, Any]:
    return {
        "pdflatex": shutil.which("pdflatex") is not None,
        "latexmk": shutil.which("latexmk") is not None,
    }


def load_real_chain(
    root: str | Path = REPO_ROOT,
) -> Dict[str, Any]:
    """Load and identity-check the persisted 014/015/018 chain."""

    root_path = Path(root).resolve()
    required = {
        "continuation_final": root_path
        / CONTINUATION_DIR.relative_to(REPO_ROOT)
        / "FINAL_CONTINUATION_RESULT.json",
        "architecture_checkpoint": root_path
        / CONTINUATION_DIR.relative_to(REPO_ROOT)
        / "02-architecture.json",
        "review": root_path / REVIEW_PATH.relative_to(REPO_ROOT),
        "manuscript": root_path / MANUSCRIPT_PATH.relative_to(REPO_ROOT),
        "reproducibility": root_path
        / REPRODUCIBILITY_PATH.relative_to(REPO_ROOT),
        "source_pipeline": root_path
        / SOURCE_PIPELINE_DIR.relative_to(REPO_ROOT),
    }
    missing = [
        str(path)
        for name, path in required.items()
        if not (path.is_dir() if name == "source_pipeline" else path.is_file())
    ]
    if missing:
        raise ChainLoadError(
            "missing persisted upstream inputs: " + "; ".join(missing)
        )
    continuation = json.loads(
        required["continuation_final"].read_text(encoding="utf-8")
    )
    architecture = json.loads(
        required["architecture_checkpoint"].read_text(encoding="utf-8")
    )
    review = json.loads(required["review"].read_text(encoding="utf-8"))
    manuscript = json.loads(
        required["manuscript"].read_text(encoding="utf-8")
    )
    reproducibility = json.loads(
        required["reproducibility"].read_text(encoding="utf-8")
    )
    synthesis = continuation["stage_payloads"]["result_synthesis"]
    plan = synthesis["derived_plan"]
    ledger = synthesis["ledger"]
    story_id = str(continuation.get("selected_story_id") or "")

    identity_errors = check_chain_identity(
        plan=plan,
        architecture=architecture,
        review=review,
        manuscript=manuscript,
        reproducibility=reproducibility,
        story_id=story_id,
    )
    if identity_errors:
        raise ChainIdentityError("; ".join(identity_errors))

    bundle = load_source_pipeline(required["source_pipeline"])
    arch_model = ArticleArchitectureResult.model_validate(architecture)
    syn_model = ArticleResultSynthesisResult.model_validate(synthesis)
    _, values = _contracted_inventory(syn_model, bundle)
    scoped_values = _scoped_story_values(arch_model, story_id, values)
    evidence = list(
        bundle.result.method_research.evidence
        if bundle.result.method_research is not None
        else ()
    )
    run_dirs = [
        Path(item["source_run_dir"])
        for item in reproducibility.get("replay_records") or []
        if item.get("source_run_dir")
    ]
    return {
        "plan": plan,
        "ledger": ledger,
        "architecture": architecture,
        "review": review,
        "manuscript": manuscript,
        "reproducibility": reproducibility,
        "story_id": story_id,
        "value_records": scoped_values,
        "method_evidence": evidence,
        "artifact_roots": run_dirs,
        "source_pipeline_dir": str(required["source_pipeline"]),
    }


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return dict(value)


def check_chain_identity(
    *,
    plan: Mapping[str, Any],
    architecture: Mapping[str, Any],
    review: Mapping[str, Any],
    manuscript: Mapping[str, Any],
    reproducibility: Mapping[str, Any],
    story_id: str,
) -> List[str]:
    errors: List[str] = []
    plan_map = _as_mapping(plan)
    architecture_map = _as_mapping(architecture)
    review_map = _as_mapping(review)
    manuscript_map = _as_mapping(manuscript)
    reproducibility_map = _as_mapping(reproducibility)
    plan_id = plan_map.get("plan_id")
    if architecture_map.get("source_plan_id") != plan_id:
        errors.append(
            "architecture source_plan_id does not match the derived plan"
        )
    if architecture_map.get("architecture_id") != reproducibility_map.get(
        "architecture_id"
    ):
        errors.append(
            "reproducibility architecture_id does not match architecture"
        )
    if review_map.get("review_id") != reproducibility_map.get("review_id"):
        errors.append("reproducibility review_id does not match review")
    if review_map.get("result_id") != reproducibility_map.get("result_id"):
        errors.append("reproducibility result_id does not match review")
    if manuscript_map.get("body_id") != reproducibility_map.get(
        "manuscript_body_id"
    ):
        errors.append(
            "reproducibility manuscript_body_id does not match manuscript"
        )
    if story_id != reproducibility_map.get("story_id"):
        errors.append("reproducibility story_id does not match selection")
    return errors


class DeterministicRenderer:
    """Offline fake LaTeX renderer; no external toolchain required."""

    def __init__(self, *, compile_pdf: bool = False) -> None:
        self.compile_pdf = compile_pdf
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(dict(kwargs))
        output_dir = Path(kwargs["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}body\\end{document}\n",
            encoding="utf-8",
        )
        (output_dir / "body.tex").write_text(
            "body\n", encoding="utf-8"
        )
        (output_dir / "manuscript.normalized.md").write_text(
            "body\n", encoding="utf-8"
        )
        (output_dir / "references.bib").write_text(
            "@misc{ref01, title={Article Reference}}",
            encoding="utf-8",
        )
        (output_dir / "main.bbl").write_text(
            "\\begin{thebibliography}{1}\\end{thebibliography}\n",
            encoding="utf-8",
        )
        (output_dir / "arxiv-source.zip").write_bytes(
            b"PK\x03\x04 synthetic zip"
        )
        compiled_pdf = ""
        if self.compile_pdf:
            (output_dir / "main.pdf").write_bytes(b"%PDF-1.4 synthetic")
            compiled_pdf = str(output_dir / "main.pdf")
        return {
            "schema_version": "research_harness.latex_build_report.v3",
            "status": (
                "submission_ready"
                if self.compile_pdf
                else "compiled_awaiting_metadata"
            ),
            "artifacts": {
                "main_tex": str(output_dir / "main.tex"),
                "body_tex": str(output_dir / "body.tex"),
                "normalized_markdown": str(
                    output_dir / "manuscript.normalized.md"
                ),
                "references_bib": str(output_dir / "references.bib"),
                "main_bbl": str(output_dir / "main.bbl"),
                "compiled_pdf": compiled_pdf,
                "arxiv_source_zip": str(output_dir / "arxiv-source.zip"),
            },
        }


def _publication_metadata() -> PublicationMetadata:
    return PublicationMetadata(
        authors=[
            PublicationAuthor(
                name="Article Integration Runner",
                affiliations=["OptoMind Lab"],
                email="",
                orcid="",
            )
        ],
        date="2026-08-16",
        acknowledgements="",
        draft=True,
    )


def run_presentation_delivery(
    inputs: Mapping[str, Any],
    *,
    output_dir: str | Path,
    compile_pdf: bool = False,
    renderer: Optional[Any] = None,
    bibliographic_metadata: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build Presentation then Delivery; return a JSON-safe summary."""

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    identity_errors = check_chain_identity(
        plan=inputs["plan"],
        architecture=inputs["architecture"],
        review=inputs["review"],
        manuscript=inputs["manuscript"],
        reproducibility=inputs["reproducibility"],
        story_id=str(inputs["story_id"]),
    )
    if identity_errors:
        summary = {
            "status": "identity_failed",
            "identity_errors": identity_errors,
            "latex_toolchain": latex_toolchain_status(),
        }
        atomic_write_json(
            output / "INTEGRATION_SUMMARY.json", summary
        )
        return summary

    presentation = build_article_presentation(
        inputs["plan"],
        inputs["ledger"],
        inputs["architecture"],
        inputs["review"],
        inputs["manuscript"],
        inputs["reproducibility"],
        inputs["story_id"],
        inputs["value_records"],
        inputs["method_evidence"],
        inputs["artifact_roots"],
        bibliographic_metadata=bibliographic_metadata,
        output_dir=output / "presentation",
    )
    delivery = build_article_delivery(
        inputs["plan"],
        inputs["ledger"],
        inputs["architecture"],
        inputs["review"],
        inputs["manuscript"],
        inputs["reproducibility"],
        presentation,
        inputs["story_id"],
        inputs["value_records"],
        _publication_metadata(),
        renderer=renderer or DeterministicRenderer(compile_pdf=compile_pdf),
        compile_pdf=compile_pdf,
        output_dir=output / "delivery",
    )
    summary = {
        "status": "blocked"
        if presentation.blockers or delivery.blockers or delivery.errors
        else ("ready" if delivery.status == "submission_ready" else delivery.status),
        "presentation": presentation.model_dump(mode="json"),
        "delivery": delivery.model_dump(mode="json"),
        "latex_toolchain": latex_toolchain_status(),
        "renderer_invoked": bool(delivery.renderer_invoked),
    }
    atomic_write_json(output / "INTEGRATION_SUMMARY.json", summary)
    return summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    warnings.filterwarnings(
        "ignore",
        message=r"The `fitz` API is deprecated.*",
    )
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser(
        description="Offline Article Stage 12C/12D integration run"
    )
    parser.add_argument(
        "--root",
        default=str(REPO_ROOT),
        help="repository root containing stage17_real_integration",
    )
    parser.add_argument(
        "--output-dir",
        default=".output/article_presentation_delivery",
        help="work directory for presentation/delivery outputs",
    )
    parser.add_argument(
        "--compile-pdf",
        action="store_true",
        help="ask the deterministic renderer to emit a synthetic PDF",
    )
    args = parser.parse_args(argv)
    try:
        inputs = load_real_chain(args.root)
    except ChainIdentityError as exc:
        summary = {
            "status": "identity_failed",
            "identity_errors": [str(exc)],
            "latex_toolchain": latex_toolchain_status(),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 2
    except ChainLoadError as exc:
        summary = {
            "status": "missing_inputs",
            "errors": [str(exc)],
            "latex_toolchain": latex_toolchain_status(),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        summary = run_presentation_delivery(
            inputs,
            output_dir=args.output_dir,
            compile_pdf=args.compile_pdf,
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 3 if summary["status"] in {"blocked", "identity_failed"} else 0


if __name__ == "__main__":
    sys.exit(main())
