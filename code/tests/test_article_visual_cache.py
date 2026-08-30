from __future__ import annotations

from pathlib import Path

from optomind_optics.harness.article_visual_cache import (
    build_visual_cache_index,
    ingest_presentation_visuals,
    materialize_conceptual_gaps,
    write_visual_cache_index,
)
from optomind_optics.harness.article_visual_planner import (
    VisualAssetRecord,
    VisualGap,
    VisualPlanResult,
)


def _plan() -> VisualPlanResult:
    record = VisualAssetRecord(
        asset_id="planned-a",
        source_kind="local_artifact",
        article_ids=["article-1"],
        figure_id="figure-1",
        caption="caption",
        claim_ids=["claim-1"],
        permission_state="inherited_source_contract",
    )
    return VisualPlanResult(
        result_id="visual-plan-1",
        source_full_structure_id="full-1",
        source_architecture_id="architecture-1",
        source_review_id="review-1",
        source_manuscript_package_id="package-1",
        story_id="story-1",
        placements=[],
        gaps=[],
        cache_records=[record],
        model_status="available",
    )


def test_cache_keeps_unresolved_asset_planned(tmp_path: Path) -> None:
    index = build_visual_cache_index(
        _plan(), available_paths={"planned-a": tmp_path / "missing.png"}
    )
    assert index.entries[0].status == "planned"
    assert index.entries[0].sha256 == ""
    assert index.warnings


def test_cache_requires_permission_before_available(tmp_path: Path) -> None:
    asset = tmp_path / "figure.png"
    asset.write_bytes(b"visual-bytes")
    index = build_visual_cache_index(
        _plan(),
        available_paths={"planned-a": asset},
        permission_states={"planned-a": "unknown"},
    )
    assert index.entries[0].status == "planned"
    assert index.entries[0].sha256 == ""
    index = build_visual_cache_index(
        _plan(),
        available_paths={"planned-a": asset},
        permission_states={"planned-a": "personal-learning-only"},
        vector_refs={"planned-a": "vec-1"},
    )
    assert index.entries[0].status == "available"
    assert len(index.entries[0].sha256) == 64
    assert index.entries[0].visual_embedding_ref == "vec-1"


def test_cache_writer_is_conflict_safe(tmp_path: Path) -> None:
    index = build_visual_cache_index(_plan())
    path = tmp_path / "visual-cache.json"
    write_visual_cache_index(index, path)
    write_visual_cache_index(index, path)
    path.write_text("{}", encoding="utf-8")
    try:
        write_visual_cache_index(index, path)
    except ValueError as exc:
        assert "conflicting" in str(exc)
    else:
        raise AssertionError("conflicting visual cache content was overwritten")


def test_materialize_conceptual_gap_is_non_measured_and_hashed(tmp_path: Path) -> None:
    plan = _plan().model_copy(
        update={
            "gaps": [
                VisualGap(
                    gap_id="mechanism-1",
                    section_id="section-1",
                    visual_role="mechanism",
                    description="A conceptual mechanism is absent.",
                    unique_contribution="Explain the flow.",
                    expected_value="Improve comprehension.",
                    stop_reason="Stop if it adds no distinct role.",
                )
            ]
        }
    )
    records = materialize_conceptual_gaps(plan, tmp_path)
    assert len(records) == 1
    assert records[0].status == "available"
    assert records[0].source_kind == "generated_conceptual"
    assert records[0].permission_state == "programmatic-conceptual"
    assert records[0].asset_path.endswith("mechanism-1.svg")
    assert "not measured data" in records[0].caption
    assert len(records[0].sha256) == 64
    assert "not measured data" in Path(records[0].asset_path).read_text(
        encoding="utf-8"
    )


def test_ingest_presentation_panels_keeps_table_panels_independent(
    tmp_path: Path,
) -> None:
    figures = tmp_path / "figures"
    tables = tmp_path / "tables"
    figures.mkdir()
    tables.mkdir()
    (figures / "panel.svg").write_text("<svg/>", encoding="utf-8")
    (tables / "panel.md").write_text(
        "| candidate | score |\n| --- | --- |\n| GC02 | 0.5 |",
        encoding="utf-8",
    )
    records = ingest_presentation_visuals(
        {
            "visuals": [
                {
                    "contract_figure_id": "figure-1",
                    "caption": "Verified comparison",
                    "claim_ids": ["claim-1"],
                    "panels": [
                        {"asset_path": "figures/panel.svg"},
                        {"asset_path": "tables/panel.md"},
                    ],
                }
            ]
        },
        tmp_path,
        article_id="article-1",
    )
    assert len(records) == 2
    assert {record.asset_format for record in records} == {"svg", "table"}
    assert all(record.status == "available" for record in records)
    assert all(record.approval_state == "approved" for record in records)
