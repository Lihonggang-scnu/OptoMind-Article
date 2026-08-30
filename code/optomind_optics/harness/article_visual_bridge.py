"""Stage 13D: approved visual cache -> manuscript mount manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from optomind_optics.harness.article_full_structure import FullStructureResult
from optomind_optics.harness.article_manuscript import ArticleManuscriptPackage
from optomind_optics.harness.article_visual_cache import VisualCacheIndex
from optomind_optics.harness.article_visual_planner import VisualPlanResult


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VisualMount(_StrictModel):
    schema_version: Literal["visual-mount.v1"] = "visual-mount.v1"
    mount_id: str
    asset_id: str
    panel_asset_ids: List[str] = Field(default_factory=list)
    section_id: str
    after_paragraph_id: str = ""
    caption: str
    source_kind: str
    asset_format: Literal["image", "svg", "pdf", "table"] = "image"
    claim_ids: List[str] = Field(default_factory=list)
    artifact_ids: List[str] = Field(default_factory=list)


class VisualMountManifest(_StrictModel):
    schema_version: Literal["visual-mount-manifest.v1"] = "visual-mount-manifest.v1"
    manifest_id: str
    source_visual_plan_id: str
    source_full_structure_id: str
    source_manuscript_package_id: str
    mounts: List[VisualMount] = Field(default_factory=list)
    withheld_asset_ids: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    validation_errors: List[str] = Field(default_factory=list)


def approve_conceptual_asset(
    index: VisualCacheIndex | Mapping[str, Any], asset_id: str
) -> VisualCacheIndex:
    """Approve only a generated conceptual SVG containing the safety marker."""

    model = (
        index
        if isinstance(index, VisualCacheIndex)
        else VisualCacheIndex.model_validate(index)
    )
    entries = []
    found = False
    warnings = list(model.warnings)
    for entry in model.entries:
        if entry.asset_id != asset_id:
            entries.append(entry)
            continue
        found = True
        if entry.source_kind != "generated_conceptual" or entry.status != "available":
            warnings.append(f"asset {asset_id!r} is not an available conceptual asset")
            entries.append(entry)
            continue
        if not entry.asset_path:
            warnings.append(f"asset {asset_id!r} has no path")
            entries.append(entry)
            continue
        with Path(entry.asset_path).open("r", encoding="utf-8") as handle:
            content = handle.read()
        if "not measured data" not in content:
            warnings.append(f"asset {asset_id!r} lacks the non-measured safety marker")
            entries.append(
                entry.model_copy(
                    update={"approval_state": "rejected", "status": "rejected"}
                )
            )
            continue
        entries.append(entry.model_copy(update={"approval_state": "approved"}))
    if not found:
        warnings.append(f"asset {asset_id!r} was not found in visual cache")
    payload = {
        "source_visual_plan_id": model.source_visual_plan_id,
        "entries": [entry.model_dump(mode="json") for entry in entries],
    }
    index_id = hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()[:24]
    return model.model_copy(
        update={"index_id": index_id, "entries": entries, "warnings": warnings}
    )


def build_visual_mount_manifest(
    full_structure: FullStructureResult | Mapping[str, Any],
    plan: VisualPlanResult | Mapping[str, Any],
    cache: VisualCacheIndex | Mapping[str, Any],
    manuscript: ArticleManuscriptPackage | Mapping[str, Any],
) -> VisualMountManifest:
    full = (
        full_structure
        if isinstance(full_structure, FullStructureResult)
        else FullStructureResult.model_validate(full_structure)
    )
    visual_plan = (
        plan
        if isinstance(plan, VisualPlanResult)
        else VisualPlanResult.model_validate(plan)
    )
    cache_model = (
        cache
        if isinstance(cache, VisualCacheIndex)
        else VisualCacheIndex.model_validate(cache)
    )
    manuscript_model = (
        manuscript
        if isinstance(manuscript, ArticleManuscriptPackage)
        else ArticleManuscriptPackage.model_validate(manuscript)
    )
    lineage_pairs = [
        ("visual_plan.full_structure_id", visual_plan.source_full_structure_id, full.result_id),
        ("visual_plan.architecture_id", visual_plan.source_architecture_id, full.source_architecture_id),
        ("visual_plan.review_id", visual_plan.source_review_id, full.source_review_id),
        ("visual_plan.manuscript_package_id", visual_plan.source_manuscript_package_id, manuscript_model.package_id),
        ("cache.source_visual_plan_id", cache_model.source_visual_plan_id, visual_plan.result_id),
    ]
    lineage_errors = [
        f"{field}: {actual!r} != {expected!r}"
        for field, actual, expected in lineage_pairs
        if actual != expected
    ]
    if lineage_errors:
        raise ValueError(
            "visual mount lineage mismatch; refusing mixed assets: "
            + "; ".join(lineage_errors)
        )
    section_ids = {section.section_id for section in manuscript_model.body.sections}
    paragraph_ids = {
        paragraph.paragraph_id
        for section in manuscript_model.body.sections
        for paragraph in section.paragraphs
    }
    entries = {entry.asset_id: entry for entry in cache_model.entries}
    mounts: List[VisualMount] = []
    withheld: List[str] = []
    warnings = list(cache_model.warnings)
    for placement in visual_plan.placements:
        entry = entries.get(f"planned-{placement.placement_id}")
        panel_entries = [
            candidate
            for candidate in cache_model.entries
            if candidate.figure_id == placement.figure_id
            and candidate.status == "available"
            and candidate.approval_state == "approved"
        ]
        if entry is None and not panel_entries:
            warnings.append(f"placement {placement.placement_id!r} has no cache entry")
            continue
        if panel_entries:
            if placement.section_id not in section_ids:
                warnings.append(
                    f"placement {placement.placement_id!r} targets unknown section"
                )
                continue
            panel_entry = panel_entries[0]
            mounts.append(
                VisualMount(
                    mount_id=f"mount-{len(mounts)+1:02d}",
                    asset_id=panel_entry.asset_id,
                    panel_asset_ids=[entry.asset_id for entry in panel_entries[1:]],
                    section_id=placement.section_id,
                    after_paragraph_id=(
                        placement.after_paragraph_id
                        if placement.after_paragraph_id in paragraph_ids
                        else ""
                    ),
                    caption=panel_entry.caption,
                    source_kind=panel_entry.source_kind,
                    asset_format=panel_entry.asset_format,
                    claim_ids=list(panel_entry.claim_ids),
                )
            )
            continue
        if entry.status != "available" or entry.approval_state != "approved":
            withheld.append(entry.asset_id)
            warnings.append(
                f"asset {entry.asset_id!r} withheld until available and approved"
            )
            continue
        if placement.section_id not in section_ids:
            warnings.append(
                f"placement {placement.placement_id!r} targets unknown section"
            )
            continue
        after = (
            placement.after_paragraph_id
            if placement.after_paragraph_id in paragraph_ids
            else ""
        )
        mounts.append(
            VisualMount(
                mount_id=f"mount-{len(mounts)+1:02d}",
                asset_id=entry.asset_id,
                section_id=placement.section_id,
                after_paragraph_id=after,
                caption=entry.caption,
                source_kind=entry.source_kind,
                asset_format=entry.asset_format,
                claim_ids=list(entry.claim_ids),
            )
        )
    # Conceptual gaps become mounts only after explicit approval.
    for gap in visual_plan.gaps:
        asset_id = f"conceptual-{gap.gap_id}"
        entry = entries.get(asset_id)
        if entry is None:
            continue
        if entry.status != "available" or entry.approval_state != "approved":
            withheld.append(asset_id)
            continue
        paragraph_id = next(
            (
                paragraph.paragraph_id
                for section in manuscript_model.body.sections
                if section.section_id == gap.section_id
                for paragraph in section.paragraphs[:1]
            ),
            "",
        )
        if gap.section_id not in section_ids:
            warnings.append(f"conceptual gap {gap.gap_id!r} targets unknown section")
            continue
        mounts.append(
            VisualMount(
                mount_id=f"mount-{len(mounts)+1:02d}",
                asset_id=asset_id,
                section_id=gap.section_id,
                after_paragraph_id=paragraph_id,
                caption=entry.caption,
                source_kind=entry.source_kind,
                asset_format=entry.asset_format,
            )
        )
    payload = {
        "source_visual_plan_id": visual_plan.result_id,
        "source_full_structure_id": full.result_id,
        "source_manuscript_package_id": manuscript_model.package_id,
        "mounts": [mount.model_dump(mode="json") for mount in mounts],
        "withheld_asset_ids": sorted(set(withheld)),
    }
    manifest_id = hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()[:24]
    return VisualMountManifest(
        manifest_id=manifest_id,
        source_visual_plan_id=visual_plan.result_id,
        source_full_structure_id=full.result_id,
        source_manuscript_package_id=manuscript_model.package_id,
        mounts=mounts,
        withheld_asset_ids=sorted(set(withheld)),
        warnings=warnings,
    )


__all__ = [
    "VisualMount",
    "VisualMountManifest",
    "approve_conceptual_asset",
    "build_visual_mount_manifest",
]
