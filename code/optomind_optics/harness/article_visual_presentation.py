"""Stage 13F: augment an Article Presentation with approved conceptual visuals."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, List, Mapping

from optomind_optics.harness.article_manuscript import ArticleManuscriptPackage
from optomind_optics.harness.article_presentation import (
    ArticlePresentationPackage,
    PanelAsset,
    RenderedVisual,
    _render_reader_manuscript,
    compute_presentation_package_id,
)
from optomind_optics.harness.article_visual_bridge import VisualMountManifest
from optomind_optics.harness.article_visual_cache import VisualCacheIndex


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def augment_presentation_with_conceptual_visuals(
    presentation: ArticlePresentationPackage | Mapping[str, Any],
    manuscript: ArticleManuscriptPackage | Mapping[str, Any],
    cache: VisualCacheIndex | Mapping[str, Any],
    mounts: VisualMountManifest | Mapping[str, Any],
) -> ArticlePresentationPackage:
    package = (
        presentation
        if isinstance(presentation, ArticlePresentationPackage)
        else ArticlePresentationPackage.model_validate(presentation)
    )
    manuscript_model = (
        manuscript
        if isinstance(manuscript, ArticleManuscriptPackage)
        else ArticleManuscriptPackage.model_validate(manuscript)
    )
    cache_model = (
        cache
        if isinstance(cache, VisualCacheIndex)
        else VisualCacheIndex.model_validate(cache)
    )
    manifest = (
        mounts
        if isinstance(mounts, VisualMountManifest)
        else VisualMountManifest.model_validate(mounts)
    )
    entries = {entry.asset_id: entry for entry in cache_model.entries}
    visuals: List[RenderedVisual] = list(package.visuals)
    next_figure_number = (
        max(
            (
                visual.figure_number
                for visual in visuals
                if visual.asset_kind == "figure"
            ),
            default=0,
        )
        + 1
    )
    for mount in manifest.mounts:
        entry = entries.get(mount.asset_id)
        if (
            entry is None
            or entry.source_kind != "generated_conceptual"
            or entry.status != "available"
            or entry.approval_state != "approved"
        ):
            continue
        source = Path(entry.asset_path)
        if not source.is_file():
            continue
        content = source.read_text(encoding="utf-8")
        sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if entry.sha256 and sha256 != entry.sha256:
            continue
        panel = PanelAsset(
            label="a",
            asset_path=f"figures/{entry.asset_id}.svg",
            encoding="utf-8",
            media_type="image/svg+xml",
            asset_content=content,
            sha256=sha256,
        )
        visual_sha = hashlib.sha256(
            _canonical([panel.model_dump(mode="json")]).encode("utf-8")
        ).hexdigest()
        caption = entry.caption or mount.caption
        visual = RenderedVisual(
            visual_id=f"fig-{visual_sha[:24]}",
            asset_kind="figure",
            contract_figure_id=f"generated-{entry.asset_id}",
            section_id=mount.section_id,
            figure_number=next_figure_number,
            after_paragraph_id=mount.after_paragraph_id,
            panels=[panel],
            source_mode="synthesized_claims",
            provenance="synthesized",
            caption=caption,
            claim_ids=[],
            fact_ids=[],
            artifact_ids=[],
            limitations=[
                "Programmatic conceptual diagram; not measured data or empirical evidence."
            ],
            sha256=visual_sha,
            block_markdown=(
                f"![{caption}]({panel.asset_path})\n\n"
                f"**Figure {next_figure_number}. {caption}** "
                "(synthesized conceptual diagram; not measured data)"
            ),
        )
        visuals.append(visual)
        next_figure_number += 1
    if package.front_matter is None:
        return package
    reader_markdown = _render_reader_manuscript(
        front_matter=package.front_matter,
        manuscript=manuscript_model,
        citations=package.citations,
        references=package.references,
        placements=package.placements,
        visuals=visuals,
    )
    package_id = compute_presentation_package_id(
        plan_id=package.plan_id,
        ledger_id=package.ledger_id,
        architecture_id=package.architecture_id,
        review_id=package.review_id,
        result_id=package.result_id,
        manuscript_body_id=package.manuscript_body_id,
        reproducibility_package_id=package.reproducibility_package_id,
        story_id=package.story_id,
        status=package.status,
        citations=package.citations,
        references=package.references,
        placements=package.placements,
        front_matter=package.front_matter,
        visuals=visuals,
        reader_markdown=reader_markdown,
        blockers=package.blockers,
        warnings=package.warnings,
        errors=package.errors,
        attempts=package.attempts,
    )
    return package.model_copy(
        update={
            "package_id": package_id,
            "visuals": visuals,
            "reader_markdown": reader_markdown,
        }
    )


__all__ = ["augment_presentation_with_conceptual_visuals"]
