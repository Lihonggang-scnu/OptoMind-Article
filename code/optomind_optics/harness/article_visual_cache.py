"""Stage 13C: durable visual asset cache index with truthful availability."""

from __future__ import annotations

import hashlib
import json
from html import escape
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from optomind_optics.harness.article_visual_planner import (
    VisualAssetRecord,
    VisualPlanResult,
)


VISUAL_CACHE_SCHEMA_VERSION = "article-visual-cache-index.v1"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VisualCacheIndex(_StrictModel):
    schema_version: str = VISUAL_CACHE_SCHEMA_VERSION
    index_id: str
    source_visual_plan_id: str
    entries: List[VisualAssetRecord] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    validation_errors: List[str] = Field(default_factory=list)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _index_id(plan_id: str, entries: Sequence[VisualAssetRecord]) -> str:
    payload = json.dumps(
        {
            "source_visual_plan_id": plan_id,
            "entries": [entry.model_dump(mode="json") for entry in entries],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def build_visual_cache_index(
    plan: VisualPlanResult | Mapping[str, object],
    *,
    available_paths: Mapping[str, str | Path] | None = None,
    permission_states: Mapping[str, str] | None = None,
    vector_refs: Mapping[str, str] | None = None,
) -> VisualCacheIndex:
    plan_model = (
        plan
        if isinstance(plan, VisualPlanResult)
        else VisualPlanResult.model_validate(plan)
    )
    available_paths = available_paths or {}
    permission_states = permission_states or {}
    vector_refs = vector_refs or {}
    warnings: List[str] = []
    errors: List[str] = []
    entries: List[VisualAssetRecord] = []
    for source in plan_model.cache_records:
        path_value = available_paths.get(source.asset_id)
        permission = permission_states.get(source.asset_id, source.permission_state)
        if path_value is None:
            entries.append(
                source.model_copy(
                    update={
                        "permission_state": permission,
                        "visual_embedding_ref": vector_refs.get(
                            source.asset_id, source.visual_embedding_ref
                        ),
                        "status": "planned",
                        "approval_state": "unreviewed",
                    }
                )
            )
            continue
        path = Path(path_value)
        if not path.is_file():
            warnings.append(
                f"visual asset {source.asset_id!r} path is unavailable; kept planned"
            )
            entries.append(
                source.model_copy(
                    update={
                        "permission_state": permission,
                        "approval_state": "unreviewed",
                    }
                )
            )
            continue
        if permission in {"", "unknown", "unresolved"}:
            warnings.append(
                f"visual asset {source.asset_id!r} has no resolved permission; kept planned"
            )
            entries.append(
                source.model_copy(
                    update={"permission_state": permission or "unresolved"}
                )
            )
            continue
        entries.append(
            source.model_copy(
                update={
                    "permission_state": permission,
                    "visual_embedding_ref": vector_refs.get(
                        source.asset_id, source.visual_embedding_ref
                    ),
                    "sha256": _sha256(path),
                    "asset_path": path.as_posix(),
                    "status": "available",
                    "approval_state": "unreviewed",
                }
            )
        )
    index_id = _index_id(plan_model.result_id, entries)
    return VisualCacheIndex(
        index_id=index_id,
        source_visual_plan_id=plan_model.result_id,
        entries=entries,
        warnings=warnings,
        validation_errors=errors,
    )


def write_visual_cache_index(index: VisualCacheIndex, path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(index.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    )
    if destination.exists() and destination.read_text(encoding="utf-8") != payload:
        raise ValueError(
            f"refusing to overwrite conflicting visual cache index {destination}"
        )
    destination.write_text(payload, encoding="utf-8")


def _conceptual_svg(role: str, description: str) -> str:
    title = (
        "Conceptual mechanism"
        if role == "mechanism"
        else "Conceptual research workflow"
    )
    nodes = (
        [
            ("Incident field", 90),
            ("Layered optical stack", 330),
            ("Spectral response", 570),
        ]
        if role == "mechanism"
        else [
            ("Question", 60),
            ("Bounded experiment", 270),
            ("Evidence audit", 480),
            ("Article synthesis", 690),
        ]
    )
    rects = []
    arrows = []
    for index, (label, x) in enumerate(nodes):
        rects.append(
            f'<rect x="{x}" y="125" width="170" height="64" rx="12" fill="#e8f1fb" stroke="#235789" stroke-width="2"/>'
            f'<text x="{x + 85}" y="162" text-anchor="middle" font-family="Arial" font-size="16" fill="#102a43">{escape(label)}</text>'
        )
        if index < len(nodes) - 1:
            arrows.append(
                f'<line x1="{x + 170}" y1="157" x2="{nodes[index + 1][1]}" y2="157" stroke="#4b6584" stroke-width="3" marker-end="url(#arrow)"/>'
            )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="860" height="320" viewBox="0 0 860 320">'
        '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#4b6584"/></marker></defs>'
        f'<rect width="860" height="320" fill="#ffffff"/><text x="30" y="42" font-family="Arial" font-size="24" font-weight="700" fill="#102a43">{escape(title)}</text>'
        f'<text x="30" y="76" font-family="Arial" font-size="13" fill="#486581">{escape(description[:120])}</text>'
        + "".join(arrows)
        + "".join(rects)
        + '<text x="30" y="270" font-family="Arial" font-size="13" fill="#627d98">Conceptual diagram; not measured data; source-bound claims are not altered.</text>'
        + "</svg>"
    )


def materialize_conceptual_gaps(
    plan: VisualPlanResult | Mapping[str, object],
    output_dir: str | Path,
) -> List[VisualAssetRecord]:
    """Create deterministic, non-measured SVGs for conceptual visual gaps."""

    plan_model = (
        plan
        if isinstance(plan, VisualPlanResult)
        else VisualPlanResult.model_validate(plan)
    )
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    records: List[VisualAssetRecord] = []
    for gap in plan_model.gaps:
        if gap.visual_role not in {"mechanism", "workflow", "overview"}:
            continue
        asset_id = f"conceptual-{gap.gap_id}"
        filename = f"{asset_id}.svg"
        path = output / filename
        content = _conceptual_svg(gap.visual_role, gap.description)
        if path.exists() and path.read_text(encoding="utf-8") != content:
            raise ValueError(
                f"refusing to overwrite conflicting conceptual visual {path}"
            )
        path.write_text(content, encoding="utf-8")
        records.append(
            VisualAssetRecord(
                asset_id=asset_id,
                source_kind="generated_conceptual",
                article_ids=[plan_model.source_full_structure_id],
                caption=(f"Conceptual {gap.visual_role} diagram; not measured data."),
                permission_state="programmatic-conceptual",
                sha256=_sha256(path),
                asset_path=path.as_posix(),
                status="available",
                approval_state="unreviewed",
            )
        )
    return records


def ingest_presentation_visuals(
    presentation_package: Mapping[str, object],
    presentation_dir: str | Path,
    *,
    article_id: str,
) -> List[VisualAssetRecord]:
    """Import trusted local Presentation panels as cache records.

    These are locally rendered from verified Article artifacts, not copied
    paper images. Each panel is kept as its own cache unit so a later visual
    planner can select or suppress it independently.
    """

    root = Path(presentation_dir).resolve()
    records: List[VisualAssetRecord] = []
    for visual in presentation_package.get("visuals") or ():
        if not isinstance(visual, Mapping):
            continue
        figure_id = str(visual.get("contract_figure_id") or "")
        caption = str(visual.get("caption") or "")
        claim_ids = [str(item) for item in visual.get("claim_ids") or ()]
        for index, panel in enumerate(visual.get("panels") or (), start=1):
            if not isinstance(panel, Mapping):
                continue
            relative = str(panel.get("asset_path") or "")
            if not relative:
                continue
            path = (root / relative).resolve()
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            asset_format = (
                "table"
                if suffix == ".md"
                else (
                    "svg"
                    if suffix == ".svg"
                    else "pdf" if suffix == ".pdf" else "image"
                )
            )
            asset_id = f"presentation-{figure_id}-panel-{index:02d}"
            records.append(
                VisualAssetRecord(
                    asset_id=asset_id,
                    source_kind="local_artifact",
                    article_ids=[article_id],
                    figure_id=figure_id,
                    caption=caption,
                    claim_ids=claim_ids,
                    permission_state="local-computed",
                    visual_embedding_ref="",
                    sha256=_sha256(path),
                    asset_path=path.as_posix(),
                    asset_format=asset_format,
                    status="available",
                    approval_state="approved",
                )
            )
    return records


def extend_visual_cache_index(
    index: VisualCacheIndex | Mapping[str, object],
    records: Sequence[VisualAssetRecord],
) -> VisualCacheIndex:
    model = (
        index
        if isinstance(index, VisualCacheIndex)
        else VisualCacheIndex.model_validate(index)
    )
    by_id = {entry.asset_id: entry for entry in model.entries}
    for record in records:
        by_id[record.asset_id] = record
    entries = [by_id[key] for key in sorted(by_id)]
    index_id = _index_id(model.source_visual_plan_id, entries)
    return model.model_copy(update={"index_id": index_id, "entries": entries})


__all__ = [
    "VisualCacheIndex",
    "build_visual_cache_index",
    "materialize_conceptual_gaps",
    "ingest_presentation_visuals",
    "extend_visual_cache_index",
    "write_visual_cache_index",
]
