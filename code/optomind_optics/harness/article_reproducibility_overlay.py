"""Attach a deterministic display-only manuscript derivative to reproducibility."""

from __future__ import annotations

from typing import Any, Dict, Mapping, List

from optomind_optics.harness.article_manuscript import ArticleManuscriptPackage
from optomind_optics.harness.article_reproducibility import (
    ArticleReproducibilityPackage,
    compute_reproducibility_package_id,
)


def _binding_view(package: ArticleManuscriptPackage) -> Dict[str, Any]:
    return {
        item.paragraph_id: {
            "section_id": item.section_id,
            "claim_ids": list(item.claim_ids),
            "fact_ids": list(item.fact_ids),
            "artifact_ids": list(item.artifact_ids),
            "figure_ids": list(item.figure_ids),
            "value_token_ids": list(item.value_token_ids),
            "literature_evidence_ids": list(item.literature_evidence_ids),
        }
        for item in package.source_map
    }


def overlay_reproducibility_manuscript(
    reproducibility: ArticleReproducibilityPackage | Mapping[str, Any],
    base_manuscript: ArticleManuscriptPackage | Mapping[str, Any],
    display_manuscript: ArticleManuscriptPackage | Mapping[str, Any],
) -> ArticleReproducibilityPackage:
    """Update only the public manuscript body identity after strict checks."""

    repro = (
        reproducibility
        if isinstance(reproducibility, ArticleReproducibilityPackage)
        else ArticleReproducibilityPackage.model_validate(reproducibility)
    )
    base = (
        base_manuscript
        if isinstance(base_manuscript, ArticleManuscriptPackage)
        else ArticleManuscriptPackage.model_validate(base_manuscript)
    )
    display = (
        display_manuscript
        if isinstance(display_manuscript, ArticleManuscriptPackage)
        else ArticleManuscriptPackage.model_validate(display_manuscript)
    )
    identity_pairs = [
        ("plan_id", repro.plan_id, display.plan_id),
        ("ledger_id", repro.ledger_id, display.ledger_id),
        ("architecture_id", repro.architecture_id, display.architecture_id),
        ("review_id", repro.review_id, display.review_id),
        ("story_id", repro.story_id, display.story_id),
    ]
    mismatches = [
        f"{name}: {left!r} != {right!r}"
        for name, left, right in identity_pairs
        if left != right
    ]
    if mismatches:
        raise ValueError("reproducibility overlay lineage mismatch: " + "; ".join(mismatches))
    if _binding_view(base) != _binding_view(display):
        raise ValueError("reproducibility overlay changed source bindings")
    if base.body_id == display.body_id:
        raise ValueError("display manuscript is not a distinct derivative")
    package_id = compute_reproducibility_package_id(
        plan_id=repro.plan_id,
        ledger_id=repro.ledger_id,
        architecture_id=repro.architecture_id,
        review_id=repro.review_id,
        result_id=repro.result_id,
        manuscript_body_id=display.body_id,
        story_id=repro.story_id,
        status=repro.status,
        critical_experiments=repro.critical_experiments,
        replay_records=repro.replay_records,
        lineage=repro.lineage,
        appendix=repro.appendix,
        blockers=repro.blockers,
        warnings=[*repro.warnings, "display-only manuscript derivative overlaid"],
        errors=repro.errors,
        attempts=repro.attempts,
    )
    return repro.model_copy(
        update={
            "package_id": package_id,
            "manuscript_body_id": display.body_id,
            "warnings": [*repro.warnings, "display-only manuscript derivative overlaid"],
        }
    )


__all__ = ["overlay_reproducibility_manuscript"]
