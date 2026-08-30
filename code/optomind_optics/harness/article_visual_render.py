"""Stage 13E: render approved visual mounts into a reader-facing overlay."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Mapping

from pydantic import BaseModel, ConfigDict, Field

from optomind_optics.harness.article_full_structure import FullStructureResult
from optomind_optics.harness.article_manuscript import ArticleManuscriptPackage
from optomind_optics.harness.article_visual_bridge import VisualMountManifest
from optomind_optics.harness.article_visual_cache import VisualCacheIndex


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VisualReaderOverlay(_StrictModel):
    schema_version: str = "visual-reader-overlay.v1"
    overlay_id: str
    source_mount_manifest_id: str
    source_full_structure_id: str
    body_markdown: str
    copied_assets: List[str] = Field(default_factory=list)
    withheld_asset_ids: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    validation_errors: List[str] = Field(default_factory=list)


def render_visual_reader_overlay(
    full_structure: FullStructureResult | Mapping[str, Any],
    manuscript: ArticleManuscriptPackage | Mapping[str, Any],
    cache: VisualCacheIndex | Mapping[str, Any],
    mounts: VisualMountManifest | Mapping[str, Any],
    output_dir: str | Path,
) -> VisualReaderOverlay:
    full = (
        full_structure
        if isinstance(full_structure, FullStructureResult)
        else FullStructureResult.model_validate(full_structure)
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
    output = Path(output_dir)
    assets_dir = output / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    entries = {entry.asset_id: entry for entry in cache_model.entries}
    mounts_by_paragraph = {mount.after_paragraph_id: mount for mount in manifest.mounts}
    copied: List[str] = []
    warnings = list(manifest.warnings)
    errors: List[str] = []
    for mount in manifest.mounts:
        for asset_id in [mount.asset_id, *mount.panel_asset_ids]:
            entry = entries.get(asset_id)
            if (
                entry is None
                or entry.status != "available"
                or entry.approval_state != "approved"
            ):
                errors.append(
                    f"mount {mount.mount_id!r} does not reference an approved asset"
                )
                continue
            source = Path(entry.asset_path)
            if not source.is_file():
                errors.append(
                    f"approved visual source is missing: {entry.asset_path!r}"
                )
                continue
            if (
                entry.sha256
                and hashlib.sha256(source.read_bytes()).hexdigest() != entry.sha256
            ):
                errors.append(f"approved visual SHA256 mismatch: {entry.asset_id!r}")
                continue
            suffix = source.suffix.lower() or ".asset"
            destination = assets_dir / f"{entry.asset_id}{suffix}"
            if destination.exists() and destination.read_bytes() != source.read_bytes():
                errors.append(f"visual overlay refuses conflicting asset {destination}")
                continue
            if not destination.exists():
                shutil.copyfile(source, destination)
            copied.append(destination.relative_to(output).as_posix())
    sections = {
        section.section_id: section for section in manuscript_model.body.sections
    }
    section_order = [item.source_section_id for item in full.section_order]
    chunks: List[str] = []
    for section_id in section_order:
        section = sections.get(section_id)
        if section is None:
            warnings.append(
                f"overlay skipped unknown manuscript section {section_id!r}"
            )
            continue
        chunks.append(f"## {section.heading}\n\n")
        for paragraph in section.paragraphs:
            chunks.append(paragraph.rendered_text.strip() + "\n\n")
            mount = mounts_by_paragraph.get(paragraph.paragraph_id)
            if mount is None:
                continue
            first = True
            for asset_id in [mount.asset_id, *mount.panel_asset_ids]:
                entry = entries.get(asset_id)
                if entry is None or entry.asset_id not in {
                    Path(path).stem for path in copied
                }:
                    continue
                relative = next(
                    path for path in copied if Path(path).stem == entry.asset_id
                )
                caption = mount.caption if first else ""
                if entry.asset_format == "table":
                    table_text = (output / relative).read_text(encoding="utf-8")
                    if caption:
                        chunks.append(f"**{caption}**\n\n")
                    chunks.append(f"{table_text.strip()}\n\n")
                else:
                    if caption:
                        chunks.append(f"![{caption}]({relative})\n\n**{caption}**\n\n")
                    else:
                        chunks.append(f"![]({relative})\n\n")
                first = False
    body = "".join(chunks).strip() + "\n"
    payload = {
        "source_mount_manifest_id": manifest.manifest_id,
        "source_full_structure_id": full.result_id,
        "body_markdown": body,
        "copied_assets": copied,
        "withheld_asset_ids": manifest.withheld_asset_ids,
    }
    overlay_id = hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()[:24]
    return VisualReaderOverlay(
        overlay_id=overlay_id,
        source_mount_manifest_id=manifest.manifest_id,
        source_full_structure_id=full.result_id,
        body_markdown=body,
        copied_assets=copied,
        withheld_asset_ids=manifest.withheld_asset_ids,
        warnings=warnings,
        validation_errors=errors,
    )


__all__ = ["VisualReaderOverlay", "render_visual_reader_overlay"]
