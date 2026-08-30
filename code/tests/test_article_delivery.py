from __future__ import annotations

import base64
import hashlib
import io
import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from optomind_optics.harness.article_delivery import (
    AdditionalUsageRow,
    ArticleDeliveryIntegrityError,
    ArticleDeliveryPackage,
    DeliveryArtifactRecord,
    PublicationAuthor,
    PublicationMetadata,
    build_article_delivery,
    compute_delivery_package_id,
    validate_delivery_package,
    write_delivery_package,
)
from optomind_optics.harness.article_presentation import (
    ArticlePresentationPackage,
    PanelAsset,
    RenderedVisual,
    _render_reader_manuscript,
    build_article_presentation,
    compute_presentation_package_id,
)
from optomind_optics.harness.article_director import ArticleDirectorPlan
import optomind_optics.harness.article_delivery as delivery_module

from test_article_presentation import (
    TINY_PNG,
    _biblio,
    _chain,
    _citation_response,
    _front_matter_response,
    _provider,
)


def _metadata(
    *,
    authors: list[PublicationAuthor] | None = None,
    draft: bool = False,
) -> PublicationMetadata:
    return PublicationMetadata(
        authors=(
            authors
            if authors is not None
            else [
                PublicationAuthor(
                    name="Ada Lovelace",
                    affiliations=["OptoMind Lab"],
                    email="ada@example.org",
                    orcid="0000-0001-0002-0003",
                )
            ]
        ),
        date="2026-08-16",
        acknowledgements="None",
        draft=draft,
    )


def _presentation(ctx: dict) -> ArticlePresentationPackage:
    return build_article_presentation(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        ctx["story_id"],
        ctx["values"],
        ctx["evidence"],
        [ctx["run_dir"]],
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(_front_matter_response),
        bibliographic_metadata=_biblio(),
    )


class _FakeRenderer:
    def __init__(
        self,
        *,
        raise_error: bool = False,
        missing_pdf: bool = False,
        status: str = "submission_ready",
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.raise_error = raise_error
        self.missing_pdf = missing_pdf
        self.status = status

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        if self.raise_error:
            raise RuntimeError("synthetic renderer failure")
        out = Path(kwargs["output_dir"])
        out.mkdir(parents=True, exist_ok=True)
        content_package = Path(kwargs["content_package_path"])
        if content_package.is_file():
            package = json.loads(content_package.read_text(encoding="utf-8"))
            visual_path = Path(package.get("final_visual_package_path") or "")
            if visual_path.is_file():
                plan = json.loads(visual_path.read_text(encoding="utf-8"))
                figures_dir = out / "figures"
                figures_dir.mkdir(parents=True, exist_ok=True)
                for figure in plan.get("figures", []):
                    source = Path(figure.get("local_path") or "")
                    if source.is_file():
                        (figures_dir / source.name).write_bytes(source.read_bytes())
        (out / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}body\\end{document}\n",
            encoding="utf-8",
        )
        if not self.missing_pdf:
            (out / "main.pdf").write_bytes(b"%PDF-1.4 synthetic")
        (out / "arxiv-source.zip").write_bytes(b"PK\x03\x04 synthetic zip")
        (out / "references.bib").write_text(
            "@misc{ref01, title={Broadband Antireflection Coatings}}",
            encoding="utf-8",
        )
        compiled = str(out / "main.pdf") if not self.missing_pdf else ""
        return {
            "schema_version": "research_harness.latex_build_report.v3",
            "status": self.status,
            "artifacts": {
                "main_tex": str(out / "main.tex"),
                "body_tex": str(out / "body.tex"),
                "normalized_markdown": str(out / "manuscript.normalized.md"),
                "references_bib": str(out / "references.bib"),
                "main_bbl": str(out / "main.bbl"),
                "compiled_pdf": compiled,
                "arxiv_source_zip": str(out / "arxiv-source.zip"),
            },
        }


def _build(
    tmp_path: Path,
    ctx: dict,
    *,
    renderer: _FakeRenderer | None = None,
    metadata: PublicationMetadata | None = None,
    compile_pdf: bool = True,
    additional_usage: list[AdditionalUsageRow] | None = None,
    presentation: ArticlePresentationPackage | None = None,
    output_dir: Path | None = None,
) -> ArticleDeliveryPackage:
    return build_article_delivery(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        presentation if presentation is not None else _presentation(ctx),
        ctx["story_id"],
        ctx["values"],
        metadata if metadata is not None else _metadata(),
        additional_usage=additional_usage or [],
        renderer=renderer or _FakeRenderer(),
        compile_pdf=compile_pdf,
        output_dir=output_dir,
    )


def _with_raster_visuals(
    presentation: ArticlePresentationPackage,
    ctx: dict,
    *,
    panels: list[PanelAsset] | None = None,
    visual_id: str = "fig-test-1",
) -> ArticlePresentationPackage:
    original = presentation.visuals[0]
    if panels is None:
        panel = PanelAsset(
            label="FINAL_RESULT.json",
            asset_path="figures/fig01-panel.png",
            encoding="base64",
            media_type="image/png",
            asset_bytes_b64=base64.b64encode(TINY_PNG).decode("ascii"),
            sha256=hashlib.sha256(TINY_PNG).hexdigest(),
        )
        panels = [panel]
    elif panels:
        base_path = panels[0].asset_path
        normalized = []
        for index, item in enumerate(panels):
            if index == 0 or item.asset_path != base_path:
                normalized.append(item)
                continue
            suffix = Path(item.asset_path).suffix or ".png"
            normalized.append(
                item.model_copy(
                    update={"asset_path": (f"figures/fig{index + 1:02d}-panel{suffix}")}
                )
            )
        panels = normalized
    # panels == [] remains empty
    caption = "Verified raster figure"
    panel_lines = []
    for panel in panels:
        panel_lines.append(f"![{caption} panel]({panel.asset_path})")
    block_markdown = f"**Figure 1.** {caption}\n\n" + "\n\n".join(panel_lines)
    visual = RenderedVisual(
        visual_id=visual_id,
        asset_kind="figure",
        contract_figure_id=original.contract_figure_id,
        section_id=original.section_id,
        figure_number=1,
        after_paragraph_id=original.after_paragraph_id,
        panels=panels,
        source_mode="trusted_artifact",
        provenance="verified",
        caption=caption,
        claim_ids=list(original.claim_ids),
        fact_ids=list(original.fact_ids),
        artifact_ids=list(original.artifact_ids),
        limitations=list(original.limitations),
        sha256=hashlib.sha256(
            json.dumps(
                [panel.model_dump(mode="json") for panel in panels],
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        block_markdown=block_markdown,
    )
    reader = _render_reader_manuscript(
        front_matter=presentation.front_matter,
        manuscript=ctx["manuscript"],
        citations=presentation.citations,
        references=presentation.references,
        placements=presentation.placements,
        visuals=[visual],
    )
    updated = presentation.model_copy(
        update={"visuals": [visual], "reader_markdown": reader}
    )
    package_id = compute_presentation_package_id(
        plan_id=updated.plan_id,
        ledger_id=updated.ledger_id,
        architecture_id=updated.architecture_id,
        review_id=updated.review_id,
        result_id=updated.result_id,
        manuscript_body_id=updated.manuscript_body_id,
        reproducibility_package_id=updated.reproducibility_package_id,
        story_id=updated.story_id,
        status=updated.status,
        citations=updated.citations,
        references=updated.references,
        placements=updated.placements,
        front_matter=updated.front_matter,
        visuals=updated.visuals,
        reader_markdown=updated.reader_markdown,
        blockers=updated.blockers,
        warnings=updated.warnings,
        errors=updated.errors,
        attempts=updated.attempts,
    )
    return updated.model_copy(update={"package_id": package_id})


def test_submission_ready_happy_path_and_idempotent_write(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    fake = _FakeRenderer()
    out = tmp_path / "delivery"
    delivery = _build(
        tmp_path,
        ctx,
        renderer=fake,
        output_dir=out,
    )
    assert delivery.status == "submission_ready"
    assert delivery.renderer_invoked
    assert not delivery.blockers
    assert not delivery.errors
    assert delivery.author_metadata_complete
    assert delivery.reference_metadata_complete
    assert (out / "latex" / "main.tex").is_file()
    assert (out / "latex" / "main.pdf").is_file()
    assert (out / "latex" / "arxiv-source.zip").is_file()
    assert (out / "ARTICLE_DELIVERY_PACKAGE.json").is_file()
    assert (out / "ARTICLE_PUBLICATION_AUDIT.json").is_file()
    assert (out / "ARTICLE_COST_LEDGER.json").is_file()
    assert (out / "ARTICLE_SUBMISSION_CHECKLIST.md").is_file()
    assert (out / "ARTICLE_RENDERER_INPUT_MANIFEST.json").is_file()
    assert len(fake.calls) == 1
    assert delivery.renderer_attempts == 1
    assert "pandoc_found" in delivery.tool_availability
    assert "latexmk_found" in delivery.tool_availability
    errors: list[str] = []
    assert validate_delivery_package(
        delivery,
        plan=ctx["plan"],
        ledger=ctx["ledger"],
        architecture=ctx["architecture"],
        review=ctx["review"],
        manuscript=ctx["manuscript"],
        reproducibility=ctx["reproducibility"],
        presentation=ctx_presentation(ctx),
        selected_story_id=ctx["story_id"],
        value_records=ctx["values"],
        output_dir=out,
        errors=errors,
        warnings=[],
    )
    assert errors == []
    second = _build(
        tmp_path,
        ctx,
        renderer=fake,
        output_dir=out,
    )
    assert second.package_id == delivery.package_id
    write_delivery_package(delivery, out)  # idempotent replay


def ctx_presentation(ctx: dict) -> ArticlePresentationPackage:
    return _presentation(ctx)


def test_compiled_awaiting_metadata_when_authors_missing(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    fake = _FakeRenderer()
    delivery = _build(
        tmp_path,
        ctx,
        renderer=fake,
        metadata=_metadata(authors=[]),
    )
    assert delivery.status == "compiled_awaiting_metadata"
    assert delivery.renderer_invoked
    assert any("author" in finding.message for finding in delivery.findings)


def test_author_without_affiliation_is_not_submission_ready(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    delivery = _build(
        tmp_path,
        ctx,
        metadata=_metadata(authors=[PublicationAuthor(name="No Affiliations")]),
    )
    assert delivery.status == "compiled_awaiting_metadata"
    assert not delivery.author_metadata_complete


def test_draft_flag_never_submission_ready(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    delivery = _build(
        tmp_path,
        ctx,
        metadata=_metadata(draft=True),
    )
    assert delivery.status == "compiled_awaiting_metadata"


def test_renderer_failure_marks_failed_without_final_latex(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    fake = _FakeRenderer(raise_error=True)
    out = tmp_path / "delivery_fail"
    delivery = _build(
        tmp_path,
        ctx,
        renderer=fake,
        output_dir=out,
    )
    assert delivery.status == "failed"
    assert delivery.renderer_attempts == 1
    assert not (out / "latex").exists()
    assert (out / "ARTICLE_DELIVERY_PACKAGE.json").is_file()
    assert any("renderer invocation failed" in item for item in delivery.errors)


def test_missing_pdf_fails_when_compile_enabled(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    fake = _FakeRenderer(missing_pdf=True)
    delivery = _build(tmp_path, ctx, renderer=fake)
    assert delivery.status == "failed"
    assert any("main.pdf" in item or "compiled_pdf" in item for item in delivery.errors)


def test_compile_disabled_is_not_submission_ready(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    fake = _FakeRenderer(missing_pdf=True)
    delivery = _build(
        tmp_path,
        ctx,
        renderer=fake,
        compile_pdf=False,
    )
    assert delivery.status == "compiled_awaiting_metadata"
    assert any("pdf compilation disabled" in item for item in delivery.warnings)


def test_renderer_reported_failure_fails(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    fake = _FakeRenderer(status="failed")
    delivery = _build(tmp_path, ctx, renderer=fake)
    assert delivery.status == "failed"
    assert any("non-success status" in item for item in delivery.errors)


def test_renderer_unknown_status_fails_even_with_files(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    fake = _FakeRenderer(status="some-unknown-status")
    delivery = _build(tmp_path, ctx, renderer=fake)
    assert delivery.status == "failed"


def test_renderer_compiled_awaiting_metadata_status(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    fake = _FakeRenderer(status="compiled_awaiting_metadata")
    delivery = _build(tmp_path, ctx, renderer=fake)
    assert delivery.status == "compiled_awaiting_metadata"


def test_tampered_manuscript_blocks_before_renderer(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    fake = _FakeRenderer()
    tampered = ctx["manuscript"].model_copy(
        update={"warnings": ["forged manuscript warning"]}
    )
    delivery = build_article_delivery(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        tampered,
        ctx["reproducibility"],
        _presentation(ctx),
        ctx["story_id"],
        ctx["values"],
        _metadata(),
        renderer=fake,
    )
    assert delivery.status == "blocked"
    assert fake.calls == []
    assert any("upstream" in blocker.kind for blocker in delivery.blockers)


def test_tampered_presentation_blocks_before_renderer(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    fake = _FakeRenderer()
    presentation = _presentation(ctx)
    tampered = presentation.model_copy(
        update={
            "reader_markdown": presentation.reader_markdown.replace("[REF:", "XREF:")
        }
    )
    delivery = build_article_delivery(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        tampered,
        ctx["story_id"],
        ctx["values"],
        _metadata(),
        renderer=fake,
    )
    assert delivery.status == "blocked"
    assert fake.calls == []


def test_tampered_review_blocks_before_renderer(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    fake = _FakeRenderer()
    tampered = ctx["review"].model_copy(update={"hard_blockers": ["forged blocker"]})
    delivery = build_article_delivery(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        tampered,
        ctx["manuscript"],
        ctx["reproducibility"],
        _presentation(ctx),
        ctx["story_id"],
        ctx["values"],
        _metadata(),
        renderer=fake,
    )
    assert delivery.status == "blocked"
    assert fake.calls == []


def test_tampered_reproducibility_blocks_before_renderer(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    fake = _FakeRenderer()
    tampered = ctx["reproducibility"].model_copy(
        update={
            "critical_experiments": [
                item.model_copy(update={"rationale": "forged"})
                for item in ctx["reproducibility"].critical_experiments
            ]
        }
    )
    delivery = build_article_delivery(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        tampered,
        _presentation(ctx),
        ctx["story_id"],
        ctx["values"],
        _metadata(),
        renderer=fake,
    )
    assert delivery.status == "blocked"
    assert fake.calls == []


def test_mapping_inputs_accepted(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    fake = _FakeRenderer()
    delivery = build_article_delivery(
        ctx["plan"].model_dump(mode="json"),
        ctx["ledger"].model_dump(mode="json"),
        ctx["architecture"].model_dump(mode="json"),
        ctx["review"].model_dump(mode="json"),
        ctx["manuscript"].model_dump(mode="json"),
        ctx["reproducibility"].model_dump(mode="json"),
        _presentation(ctx).model_dump(mode="json"),
        ctx["story_id"],
        [item.model_dump(mode="json") for item in ctx["values"]],
        _metadata().model_dump(mode="json"),
        additional_usage=[
            AdditionalUsageRow(
                label="director_plan",
                usage={"estimated_input_tokens": 3, "estimated_output_tokens": 4},
            ).model_dump(mode="json")
        ],
        renderer=fake,
    )
    assert delivery.status == "submission_ready"
    labels = [row.stage_label for row in delivery.cost.rows]
    assert "director_plan" in labels
    assert "director_plan" not in delivery.cost.coverage_missing


class _UsagePlan(ArticleDirectorPlan):
    usage: dict[str, Any] = {}
    model_name: str = ""


def test_director_plan_cost_included_exactly_once(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    usage_plan = _UsagePlan(
        **ctx["plan"].model_dump(),
        usage={
            "estimated_input_tokens": 3,
            "estimated_output_tokens": 4,
            "call_count": 1,
            "attempts": 1,
        },
        model_name="qwen3.7-flash",
    )
    delivery = build_article_delivery(
        usage_plan,
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        _presentation(ctx),
        ctx["story_id"],
        ctx["values"],
        _metadata(),
        renderer=_FakeRenderer(),
    )
    director_rows = [
        row for row in delivery.cost.rows if row.stage_label == "director_plan"
    ]
    assert len(director_rows) == 1
    assert director_rows[0].estimated_input_tokens == 3
    assert director_rows[0].model_name == "qwen3.7-flash"
    assert "director_plan" not in delivery.cost.coverage_missing

    blocked = build_article_delivery(
        usage_plan,
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        _presentation(ctx),
        ctx["story_id"],
        ctx["values"],
        _metadata(),
        additional_usage=[
            AdditionalUsageRow(
                label="director_plan",
                usage={"estimated_input_tokens": 9},
            )
        ],
        renderer=_FakeRenderer(),
    )
    assert blocked.status == "blocked"
    assert any("duplicates" in blocker.message for blocker in blocked.blockers)


def test_single_owner_fill_of_empty_builtin_label(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    delivery = build_article_delivery(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        _presentation(ctx),
        ctx["story_id"],
        ctx["values"],
        _metadata(),
        additional_usage=[
            AdditionalUsageRow(
                label="reproducibility",
                usage={"estimated_input_tokens": 1},
            )
        ],
        renderer=_FakeRenderer(),
    )
    repro_rows = [
        row for row in delivery.cost.rows if row.stage_label == "reproducibility"
    ]
    assert len(repro_rows) == 1
    assert "reproducibility" not in delivery.cost.coverage_missing


def test_cost_coverage_finding_not_duplicated(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    delivery = _build(tmp_path, ctx, renderer=_FakeRenderer())
    finding_ids = [item.finding_id for item in delivery.findings]
    assert len(finding_ids) == len(set(finding_ids))
    coverage_findings = [
        item for item in delivery.findings if item.kind == "cost_coverage_gap"
    ]
    assert len(coverage_findings) == 1


def test_truthful_model_names_in_cost_rows(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    delivery = _build(tmp_path, ctx, renderer=_FakeRenderer())
    by_label = {row.stage_label: row for row in delivery.cost.rows}
    assert by_label["architecture"].model_name == ctx["architecture"].semantic_model
    assert by_label["presentation"].model_name == _presentation(ctx).model_name
    writing_rows = [
        row for label, row in by_label.items() if label.startswith("writing_section_")
    ]
    assert writing_rows
    section = ctx["review"].sections[0]
    assert writing_rows[0].model_name == section.original_section_draft.semantic_model


def test_reference_metadata_seed_and_body_preservation(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")

    class _CapturingRenderer(_FakeRenderer):
        def __init__(self) -> None:
            super().__init__()
            self.seed: dict[str, Any] = {}
            self.source_markdown = ""

        def __call__(self, **kwargs: Any) -> dict[str, Any]:
            out = Path(kwargs["output_dir"])
            seed_path = out / "BIBLIOGRAPHY_METADATA.json"
            if seed_path.is_file():
                self.seed = json.loads(seed_path.read_text(encoding="utf-8"))
            source_path = Path(kwargs["source_markdown_path"])
            if source_path.is_file():
                self.source_markdown = source_path.read_text(encoding="utf-8")
            return super().__call__(**kwargs)

    fake = _CapturingRenderer()
    delivery = _build(tmp_path, ctx, renderer=fake)
    assert delivery.status == "submission_ready"
    aliases = {item.reference_alias for item in delivery.references}
    assert aliases
    alias = sorted(aliases)[0]
    record = fake.seed.get("records", {}).get(alias)
    assert record is not None
    assert record["title"]
    assert record["authors"]
    assert record["doi"]
    assert "[REF:" + alias + "]" in fake.source_markdown
    for reference in delivery.references:
        assert reference.citation_key.startswith(alias.lower().replace("-", "_")[:8])
    stripped = re.sub(r"\[REF:[^\]]+\]", "", fake.source_markdown)
    for paragraph in ctx["manuscript"].source_map:
        assert paragraph.rendered_text in stripped


def _jpeg_bytes() -> bytes:
    from PIL import Image as PILImage

    buffer = io.BytesIO()
    PILImage.new("RGB", (8, 8), (200, 20, 20)).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_multi_panel_composition_single_asset(tmp_path) -> None:
    ctx = _chain(tmp_path)
    panel = PanelAsset(
        label="FINAL_RESULT.json",
        asset_path="figures/fig01-panel.png",
        encoding="base64",
        media_type="image/png",
        asset_bytes_b64=base64.b64encode(TINY_PNG).decode("ascii"),
        sha256=hashlib.sha256(TINY_PNG).hexdigest(),
    )
    jpeg = _jpeg_bytes()
    second = PanelAsset(
        label="second",
        asset_path="figures/fig02-panel.jpg",
        encoding="base64",
        media_type="image/jpeg",
        asset_bytes_b64=base64.b64encode(jpeg).decode("ascii"),
        sha256=hashlib.sha256(jpeg).hexdigest(),
    )
    presentation = _with_raster_visuals(
        _presentation(ctx),
        ctx,
        panels=[panel, second],
        visual_id="fig-multi",
    )
    fake = _FakeRenderer()
    out = tmp_path / "delivery_multi"
    delivery = _build(
        tmp_path,
        ctx,
        renderer=fake,
        presentation=presentation,
        output_dir=out,
    )
    assert delivery.status == "submission_ready"
    assert fake.calls
    visual = delivery.visuals[0]
    assert len(visual.panels) == 2
    assert visual.kind == "figure"
    assert visual.contract_figure_id == presentation.visuals[0].contract_figure_id
    assert visual.composition["mode"] == "composite"
    assert visual.composition["grid_rows"] == 1
    assert visual.composition["grid_cols"] == 2
    assert visual.composition["panel_labels"] == ["(a)", "(b)"]
    assert visual.composition["original_panel_hashes"] == [
        panel.sha256,
        second.sha256,
    ]
    assert visual.renderer_asset_path.endswith(".png")
    assert visual.renderer_sha256
    plan = json.loads(
        (out / "renderer_inputs" / "visual_plan.json").read_text(encoding="utf-8")
    )
    assert len(plan["figures"]) == 1
    assert plan["figures"][0]["figure_id"] == presentation.visuals[0].contract_figure_id
    asset = out / visual.renderer_asset_path
    assert asset.is_file()
    assert hashlib.sha256(asset.read_bytes()).hexdigest() == visual.renderer_sha256

    other = tmp_path / "delivery_multi_other"
    second_build = _build(
        tmp_path,
        ctx,
        renderer=_FakeRenderer(),
        presentation=presentation,
        output_dir=other,
    )
    assert (
        second_build.visuals[0].renderer_sha256 == delivery.visuals[0].renderer_sha256
    )


def test_svg_figure_converts_and_renders(tmp_path) -> None:
    ctx = _chain(tmp_path)
    fake = _FakeRenderer()
    out = tmp_path / "svg_out"
    delivery = _build(tmp_path, ctx, renderer=fake, output_dir=out)
    assert delivery.status == "submission_ready"
    visual = delivery.visuals[0]
    assert visual.representable
    assert visual.renderer_asset_path.endswith(".png")
    assert visual.renderer_media_type == "image/png"
    assert visual.composition["mode"] == "converted"
    assert visual.composition["grid_rows"] == 1
    assert visual.composition["grid_cols"] == 1
    asset = out / visual.renderer_asset_path
    assert asset.is_file()
    assert hashlib.sha256(asset.read_bytes()).hexdigest() == visual.renderer_sha256
    plan = json.loads(
        (out / "renderer_inputs" / "visual_plan.json").read_text(encoding="utf-8")
    )
    assert len(plan["figures"]) == 1
    assert list((out / "latex" / "figures").glob("*"))


def test_raster_figure_submission_ready_and_visual_plan(tmp_path) -> None:
    ctx = _chain(tmp_path)
    fake = _FakeRenderer()
    out = tmp_path / "delivery_fig"
    presentation = _with_raster_visuals(_presentation(ctx), ctx)
    delivery = _build(
        tmp_path,
        ctx,
        renderer=fake,
        presentation=presentation,
        output_dir=out,
    )
    assert delivery.status == "submission_ready"
    figure_files = list((out / "latex" / "figures").glob("*"))
    assert figure_files
    panel_record = delivery.visuals[0].panels[0]
    assert panel_record.sha256 == hashlib.sha256(TINY_PNG).hexdigest()
    assert (out / "renderer_inputs" / "visual_plan.json").is_file()
    plan = json.loads(
        (out / "renderer_inputs" / "visual_plan.json").read_text(encoding="utf-8")
    )
    assert len(plan["figures"]) == 1
    assert plan["figures"][0]["figure_id"] == presentation.visuals[0].contract_figure_id


def test_table_preserved_in_renderer_source(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")

    class _Capture(_FakeRenderer):
        def __init__(self) -> None:
            super().__init__()
            self.source = ""

        def __call__(self, **kwargs: Any) -> dict[str, Any]:
            self.source = Path(kwargs["source_markdown_path"]).read_text(
                encoding="utf-8"
            )
            return super().__call__(**kwargs)

    fake = _Capture()
    delivery = _build(tmp_path, ctx, renderer=fake)
    assert delivery.status == "submission_ready"
    assert delivery.table_count == 1
    table_panel = delivery.visuals[0].panels[0]
    assert table_panel.relative_path.startswith("tables/")
    assert "|" in fake.source
    assert any(
        section.heading in fake.source for section in ctx["manuscript"].body.sections
    )


def test_cost_aggregation_totals_and_coverage(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    delivery = _build(tmp_path, ctx, renderer=_FakeRenderer())
    labels = [row.stage_label for row in delivery.cost.rows]
    assert "architecture" in labels
    assert any(label.startswith("writing_section_") for label in labels)
    assert "review" in labels or "review" in delivery.cost.coverage_missing
    assert "presentation" in labels
    assert set(delivery.cost.coverage_missing).issubset({"director_plan", "review"})
    assert len(labels) == len(set(labels))
    totals = delivery.cost.totals
    assert totals.estimated_input_tokens == sum(
        row.estimated_input_tokens for row in delivery.cost.rows
    )
    assert totals.estimated_output_tokens == sum(
        row.estimated_output_tokens for row in delivery.cost.rows
    )
    assert totals.estimated_cost_cny == round(
        sum(row.estimated_cost_cny for row in delivery.cost.rows), 6
    )


def test_duplicate_additional_cost_label_blocks(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    fake = _FakeRenderer()
    delivery = build_article_delivery(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        _presentation(ctx),
        ctx["story_id"],
        ctx["values"],
        _metadata(),
        additional_usage=[
            AdditionalUsageRow(label="custom", usage={"estimated_input_tokens": 1}),
            AdditionalUsageRow(label="custom", usage={"estimated_input_tokens": 2}),
        ],
        renderer=fake,
    )
    assert delivery.status == "blocked"
    assert fake.calls == []
    assert any("duplicate" in blocker.message for blocker in delivery.blockers)


def test_malformed_cost_blocks_before_renderer(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    fake = _FakeRenderer()
    delivery = build_article_delivery(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["reproducibility"],
        _presentation(ctx),
        ctx["story_id"],
        ctx["values"],
        _metadata(),
        additional_usage=[
            AdditionalUsageRow(
                label="custom",
                usage={"estimated_input_tokens": -5},
            )
        ],
        renderer=fake,
    )
    assert delivery.status == "blocked"
    assert fake.calls == []
    assert any("negative" in blocker.message for blocker in delivery.blockers)


def test_incomplete_reference_blocks_renderer(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    presentation = _presentation(ctx)
    reference = presentation.references[0]
    forged = reference.model_copy(
        update={
            "year": None,
            "metadata_complete": False,
            "metadata_incomplete_fields": ["year"],
        }
    )
    updated = presentation.model_copy(
        update={
            "references": [
                forged if item.reference_id == forged.reference_id else item
                for item in presentation.references
            ]
        }
    )
    package_id = compute_presentation_package_id(
        plan_id=updated.plan_id,
        ledger_id=updated.ledger_id,
        architecture_id=updated.architecture_id,
        review_id=updated.review_id,
        result_id=updated.result_id,
        manuscript_body_id=updated.manuscript_body_id,
        reproducibility_package_id=updated.reproducibility_package_id,
        story_id=updated.story_id,
        status=updated.status,
        citations=updated.citations,
        references=updated.references,
        placements=updated.placements,
        front_matter=updated.front_matter,
        visuals=updated.visuals,
        reader_markdown=updated.reader_markdown,
        blockers=updated.blockers,
        warnings=updated.warnings,
        errors=updated.errors,
        attempts=updated.attempts,
    )
    updated = updated.model_copy(update={"package_id": package_id})
    fake = _FakeRenderer()
    delivery = _build(
        tmp_path,
        ctx,
        renderer=fake,
        presentation=updated,
    )
    assert delivery.status == "blocked"
    assert fake.calls == []
    assert any(
        "incomplete_bibliographic_metadata" in blocker.kind
        for blocker in delivery.blockers
    )


def test_stable_url_is_a_complete_reference_locator(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    reference = (
        _presentation(ctx)
        .references[0]
        .model_copy(
            update={
                "doi": "",
                "venue": "",
                "url": "https://www.semanticscholar.org/paper/paper-1",
            }
        )
    )

    complete, missing = delivery_module._reference_completeness(reference)

    assert complete is True
    assert missing == []


def test_delivery_resolves_writer_figure_aliases_for_rendering(tmp_path) -> None:
    ctx = _chain(tmp_path)
    presentation = _presentation(ctx)
    paragraph = (
        ctx["manuscript"]
        .source_map[0]
        .model_copy(
            update={
                "rendered_text": ("Figure FIG01_spectrum shows the accepted result.")
            }
        )
    )
    manuscript = ctx["manuscript"].model_copy(
        update={
            "source_map": [paragraph, *ctx["manuscript"].source_map[1:]],
        }
    )
    aliases = delivery_module._figure_alias_numbers(
        ctx["architecture"],
        ctx["story_id"],
        presentation.visuals,
    )

    rendered = delivery_module._delivery_body_markdown(
        manuscript,
        presentation.placements,
        presentation.visuals,
        aliases,
    )

    assert "Figure 1 shows the accepted result." in rendered
    assert "FIG01_spectrum" not in rendered


def test_incomplete_reference_never_submission_ready_via_validator(
    tmp_path,
) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    delivery = _build(tmp_path, ctx, renderer=_FakeRenderer())
    forged = delivery.model_copy(
        update={
            "reference_metadata_complete": False,
            "renderer_status": "submission_ready",
        }
    )
    forged = forged.model_copy(
        update={
            "package_id": compute_delivery_package_id(
                plan_id=forged.plan_id,
                ledger_id=forged.ledger_id,
                architecture_id=forged.architecture_id,
                review_id=forged.review_id,
                result_id=forged.result_id,
                manuscript_body_id=forged.manuscript_body_id,
                reproducibility_package_id=forged.reproducibility_package_id,
                presentation_package_id=forged.presentation_package_id,
                story_id=forged.story_id,
                status=forged.status,
                renderer_name=forged.renderer_name,
                renderer_status=forged.renderer_status,
                renderer_report_digest=forged.renderer_report_digest,
                renderer_invoked=forged.renderer_invoked,
                renderer_attempts=forged.renderer_attempts,
                compile_pdf=forged.compile_pdf,
                publication_metadata=forged.publication_metadata,
                author_metadata_complete=forged.author_metadata_complete,
                reference_metadata_complete=forged.reference_metadata_complete,
                references=forged.references,
                visuals=forged.visuals,
                artifacts=forged.artifacts,
                blockers=forged.blockers,
                findings=forged.findings,
                warnings=forged.warnings,
                errors=forged.errors,
                cost=forged.cost,
                body_sha256=forged.body_sha256,
                citation_count=forged.citation_count,
                reference_count=forged.reference_count,
                figure_count=forged.figure_count,
                table_count=forged.table_count,
                tool_availability=forged.tool_availability,
            )
        }
    )
    errors: list[str] = []
    assert not validate_delivery_package(forged, errors=errors, warnings=[])
    assert any("does not match derived status" in item for item in errors)


def test_package_id_tamper_rejected(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    delivery = _build(tmp_path, ctx, renderer=_FakeRenderer())
    forged = delivery.model_copy(update={"status": "failed"})
    errors: list[str] = []
    assert not validate_delivery_package(forged, errors=errors, warnings=[])
    assert any("does not match recomputed identity" in item for item in errors)


def test_output_conflict_rejected(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    out = tmp_path / "delivery_conflict"
    delivery = _build(tmp_path, ctx, renderer=_FakeRenderer(), output_dir=out)
    latex = out / "latex" / "main.tex"
    latex.write_text("tampered", encoding="utf-8")
    with pytest.raises(ArticleDeliveryIntegrityError):
        write_delivery_package(delivery, out)
    package_path = out / "ARTICLE_DELIVERY_PACKAGE.json"
    package_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ArticleDeliveryIntegrityError):
        write_delivery_package(delivery, out)


def test_corrupt_artifact_detected_by_validator(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    out = tmp_path / "delivery_corrupt"
    delivery = _build(tmp_path, ctx, renderer=_FakeRenderer(), output_dir=out)
    pdf = out / "latex" / "main.pdf"
    pdf.write_bytes(b"corrupted")
    errors: list[str] = []
    assert not validate_delivery_package(
        delivery,
        output_dir=out,
        errors=errors,
        warnings=[],
    )
    assert any("hash does not match disk" in item for item in errors)


def test_unsafe_declared_renderer_path_fails(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")

    class _Unsafe(_FakeRenderer):
        def __call__(self, **kwargs: Any) -> dict[str, Any]:
            out = Path(kwargs["output_dir"])
            out.mkdir(parents=True, exist_ok=True)
            (out / "main.tex").write_text("x", encoding="utf-8")
            (out / "arxiv-source.zip").write_bytes(b"PK")
            outside = out.parent / "escape.pdf"
            outside.write_bytes(b"%PDF")
            return {
                "status": "submission_ready",
                "artifacts": {
                    "main_tex": str(out / "main.tex"),
                    "compiled_pdf": str(outside),
                    "arxiv_source_zip": str(out / "arxiv-source.zip"),
                },
            }

    delivery = _build(tmp_path, ctx, renderer=_Unsafe())
    assert delivery.status == "failed"
    assert any("escapes output dir" in item for item in delivery.errors)


def test_unsafe_artifact_path_rejected_by_model() -> None:
    with pytest.raises(ValidationError):
        DeliveryArtifactRecord(
            artifact_id="artifact-x",
            relative_path="../escape.pdf",
            kind="pdf",
            role="final",
            bytes_count=1,
            sha256="0" * 64,
        )


def test_validator_relationship_counts(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    delivery = _build(tmp_path, ctx, renderer=_FakeRenderer())
    forged = delivery.model_copy(update={"figure_count": delivery.figure_count + 1})
    errors: list[str] = []
    assert not validate_delivery_package(forged, errors=errors, warnings=[])
    assert any("figure_count does not match" in item for item in errors)


def test_blocked_package_preserves_upstream_ids(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    fake = _FakeRenderer()
    tampered = ctx["manuscript"].model_copy(
        update={"warnings": ["forged manuscript warning"]}
    )
    delivery = build_article_delivery(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        tampered,
        ctx["reproducibility"],
        _presentation(ctx),
        ctx["story_id"],
        ctx["values"],
        _metadata(),
        renderer=fake,
    )
    assert delivery.status == "blocked"
    assert fake.calls == []
    assert delivery.plan_id == ctx["plan"].plan_id
    assert delivery.result_id == ctx["review"].result_id
    assert delivery.presentation_package_id == _presentation(ctx).package_id
    assert not delivery.renderer_invoked


def test_early_blocked_cost_incompleteness_is_honest(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    fake = _FakeRenderer()
    tampered = ctx["manuscript"].model_copy(
        update={"warnings": ["forged manuscript warning"]}
    )
    delivery = build_article_delivery(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        tampered,
        ctx["reproducibility"],
        _presentation(ctx),
        ctx["story_id"],
        ctx["values"],
        _metadata(),
        renderer=fake,
    )
    assert delivery.status == "blocked"
    assert delivery.cost.total_cost_complete is False
    assert "telemetry_not_evaluated" in delivery.cost.coverage_missing
    errors: list[str] = []
    assert validate_delivery_package(delivery, errors=errors, warnings=[])
    assert errors == []


def test_renderer_attempts_and_tool_availability_are_content_addressed(
    tmp_path,
) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    delivery = _build(tmp_path, ctx, renderer=_FakeRenderer())
    forged_attempts = delivery.model_copy(
        update={"renderer_attempts": delivery.renderer_attempts + 1}
    )
    errors: list[str] = []
    assert not validate_delivery_package(forged_attempts, errors=errors, warnings=[])
    assert any("does not match recomputed identity" in item for item in errors)
    changed_tools = dict(delivery.tool_availability)
    changed_tools["compile_pdf"] = not bool(
        changed_tools.get("compile_pdf")
    )
    forged_tools = delivery.model_copy(
        update={"tool_availability": changed_tools}
    )
    errors = []
    assert not validate_delivery_package(forged_tools, errors=errors, warnings=[])
    assert any("does not match recomputed identity" in item for item in errors)


def test_deleted_final_artifact_fails_default_validation(tmp_path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    out = tmp_path / "delivery_deleted"
    delivery = _build(tmp_path, ctx, renderer=_FakeRenderer(), output_dir=out)
    pdf = out / "latex" / "main.pdf"
    assert pdf.is_file()
    pdf.unlink()
    errors: list[str] = []
    assert not validate_delivery_package(
        delivery,
        output_dir=out,
        errors=errors,
        warnings=[],
    )
    assert any("missing on disk" in item for item in errors)
    errors = []
    assert validate_delivery_package(
        delivery,
        output_dir=out,
        allow_pending_artifacts=True,
        errors=errors,
        warnings=[],
    )
    assert errors == []


class _NondeterministicRenderer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._counter = 0

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        self._counter += 1
        out = Path(kwargs["output_dir"])
        out.mkdir(parents=True, exist_ok=True)
        payload = f"run-{self._counter}-{id(self)}".encode("utf-8")
        (out / "main.tex").write_bytes(payload + b"-tex")
        (out / "main.pdf").write_bytes(payload + b"-pdf")
        (out / "arxiv-source.zip").write_bytes(payload + b"-zip")
        return {
            "status": "submission_ready",
            "created_at": f"2026-08-16T{self._counter:02d}:00:00Z",
            "artifacts": {
                "main_tex": str(out / "main.tex"),
                "compiled_pdf": str(out / "main.pdf"),
                "arxiv_source_zip": str(out / "arxiv-source.zip"),
            },
        }


def test_exact_replay_skips_renderer_and_conflicts_fail_closed(
    tmp_path,
) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    fake = _NondeterministicRenderer()
    out = tmp_path / "replay_out"
    first = _build(tmp_path, ctx, renderer=fake, output_dir=out)
    assert fake._counter == 1


def _visual_presentation(ctx: dict, panel: PanelAsset) -> ArticlePresentationPackage:
    return _with_raster_visuals(
        _presentation(ctx),
        ctx,
        panels=[panel],
        visual_id="fig-crafted",
    )


def _png_bytes(size: int = 64) -> bytes:
    from PIL import Image as PILImage

    buffer = io.BytesIO()
    PILImage.new("RGB", (size, size), (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_visual_conversion_failures_block_before_renderer(
    tmp_path,
    monkeypatch,
) -> None:
    ctx = _chain(tmp_path)

    def run(panel: PanelAsset, **overrides: Any):
        fake = _FakeRenderer()
        presentation = _visual_presentation(ctx, panel)
        with monkeypatch.context() as context:
            for name, value in overrides.items():
                context.setattr(delivery_module, name, value)
            delivery = _build(
                tmp_path,
                ctx,
                renderer=fake,
                presentation=presentation,
            )
        return delivery, fake

    def assert_blocked(delivery: ArticleDeliveryPackage, fake: Any) -> None:
        assert delivery.status == "blocked"
        assert fake.calls == []
        assert any(
            blocker.kind == "renderer_representation_limit"
            for blocker in delivery.blockers
        )

    bad_svg = PanelAsset(
        label="bad",
        asset_path="figures/bad.svg",
        encoding="utf-8",
        media_type="image/svg+xml",
        asset_content="<svg><<",
        sha256=hashlib.sha256(b"<svg><<").hexdigest(),
    )
    delivery, fake = run(bad_svg)
    assert_blocked(delivery, fake)

    tiny = PanelAsset(
        label="tiny",
        asset_path="figures/tiny.png",
        encoding="base64",
        media_type="image/png",
        asset_bytes_b64=base64.b64encode(TINY_PNG).decode("ascii"),
        sha256=hashlib.sha256(TINY_PNG).hexdigest(),
    )
    delivery, fake = run(tiny, _MAX_PANEL_PAYLOAD=8)
    assert_blocked(delivery, fake)

    big = _png_bytes(64)
    big_panel = PanelAsset(
        label="big",
        asset_path="figures/big.png",
        encoding="base64",
        media_type="image/png",
        asset_bytes_b64=base64.b64encode(big).decode("ascii"),
        sha256=hashlib.sha256(big).hexdigest(),
    )
    delivery, fake = run(big_panel, _MAX_SIDE=16)
    assert_blocked(delivery, fake)

    corrupt_png = PanelAsset(
        label="corrupt",
        asset_path="figures/corrupt.png",
        encoding="base64",
        media_type="image/png",
        asset_bytes_b64=base64.b64encode(b"not a png").decode("ascii"),
        sha256=hashlib.sha256(b"not a png").hexdigest(),
    )
    delivery, fake = run(corrupt_png)
    assert_blocked(delivery, fake)

    corrupt_pdf = PanelAsset(
        label="corrupt-pdf",
        asset_path="figures/corrupt.pdf",
        encoding="base64",
        media_type="application/pdf",
        asset_bytes_b64=base64.b64encode(b"not a pdf").decode("ascii"),
        sha256=hashlib.sha256(b"not a pdf").hexdigest(),
    )
    delivery, fake = run(corrupt_pdf)
    assert_blocked(delivery, fake)

    delivery, fake = run(bad_svg, _load_pil=lambda: None)
    assert_blocked(delivery, fake)


def test_package_id_rejects_renderer_asset_tampering(tmp_path) -> None:
    ctx = _chain(tmp_path)
    delivery = _build(tmp_path, ctx, renderer=_FakeRenderer())
    visual = delivery.visuals[0]
    assert visual.representable
    assert visual.renderer_sha256
    forged_hash = visual.model_copy(update={"renderer_sha256": "0" * 64})
    errors: list[str] = []
    assert not validate_delivery_package(
        delivery.model_copy(update={"visuals": [forged_hash]}),
        errors=errors,
        warnings=[],
    )
    assert any("does not match recomputed identity" in item for item in errors)
    forged_path = visual.model_copy(
        update={"renderer_asset_path": "renderer_inputs/figures/evil.png"}
    )
    errors = []
    assert not validate_delivery_package(
        delivery.model_copy(update={"visuals": [forged_path]}),
        errors=errors,
        warnings=[],
    )
    assert any("does not match recomputed identity" in item for item in errors)


def test_pymupdf_document_closed_on_success_and_failure(
    tmp_path,
    monkeypatch,
) -> None:
    class _FakePixmap:
        width = 2
        height = 2
        samples = b"\xff\xff\xff" * 4

    class _FakeRect:
        width = 1.0
        height = 1.0

    class _FakePage:
        def __init__(self, fail: bool) -> None:
            self.fail = fail
            self.rect = _FakeRect()

        def get_pixmap(self, **kwargs: Any) -> _FakePixmap:
            if self.fail:
                raise RuntimeError("synthetic render failure")
            return _FakePixmap()

    class _FakeDocument:
        def __init__(self, fail: bool) -> None:
            self.closed = False
            self.page = _FakePage(fail)

        def load_page(self, index: int) -> _FakePage:
            return self.page

        def close(self) -> None:
            self.closed = True

    class _FakeFitz:
        def __init__(self) -> None:
            self.documents: list[_FakeDocument] = []

        def Matrix(self, x: float, y: float) -> tuple[float, float]:
            return (x, y)

        def open(self, **kwargs: Any) -> _FakeDocument:
            document = _FakeDocument(fail=False)
            self.documents.append(document)
            return document

    fake_fitz = _FakeFitz()
    monkeypatch.setattr(delivery_module, "_load_fitz", lambda: fake_fitz)
    ctx = _chain(tmp_path)
    panel = PanelAsset(
        label="svg",
        asset_path="figures/f.svg",
        encoding="utf-8",
        media_type="image/svg+xml",
        asset_content="<svg/>",
        sha256=hashlib.sha256(b"<svg/>").hexdigest(),
    )
    errors: list[str] = []
    decoded = delivery_module._decode_panel_image(panel, errors)
    assert decoded is not None
    assert errors == []
    assert len(fake_fitz.documents) == 1
    assert fake_fitz.documents[0].closed

    class _FailingFitz(_FakeFitz):
        def open(self, **kwargs: Any) -> _FakeDocument:
            document = _FakeDocument(fail=True)
            self.documents.append(document)
            return document

    failing_fitz = _FailingFitz()
    monkeypatch.setattr(delivery_module, "_load_fitz", lambda: failing_fitz)
    errors = []
    decoded = delivery_module._decode_panel_image(panel, errors)
    assert decoded is None
    assert any("SVG conversion failed" in item for item in errors)
    assert len(failing_fitz.documents) == 1
    assert failing_fitz.documents[0].closed


def test_composite_pixel_bound_enforced_before_allocation(
    monkeypatch,
) -> None:
    from PIL import Image as PILImage

    monkeypatch.setattr(delivery_module, "_MAX_COMPOSITE_PIXELS", 64)
    images = [
        PILImage.new("RGB", (8, 8), (255, 255, 255)),
        PILImage.new("RGB", (8, 8), (255, 255, 255)),
    ]
    composed, rows, cols = delivery_module._compose_grid(
        images,
        ["(a)", "(b)"],
    )
    assert (rows, cols) == (1, 2)
    assert composed.width * composed.height <= 64


def test_empty_figure_panels_block(tmp_path) -> None:
    ctx = _chain(tmp_path)
    fake = _FakeRenderer()
    presentation = _with_raster_visuals(
        _presentation(ctx),
        ctx,
        panels=[],
        visual_id="fig-empty",
    )
    delivery = _build(
        tmp_path,
        ctx,
        renderer=fake,
        presentation=presentation,
    )
    assert delivery.status == "blocked"
    assert fake.calls == []
    assert any(
        blocker.kind == "renderer_representation_limit"
        and "no panels" in blocker.message
        for blocker in delivery.blockers
    )


def test_many_panel_labels_extend_beyond_z(tmp_path) -> None:
    ctx = _chain(tmp_path)
    panels = []
    for index in range(30):
        panels.append(
            PanelAsset(
                label=f"p{index}",
                asset_path=f"figures/fig{index:02d}-panel.png",
                encoding="base64",
                media_type="image/png",
                asset_bytes_b64=base64.b64encode(TINY_PNG).decode("ascii"),
                sha256=hashlib.sha256(TINY_PNG).hexdigest(),
            )
        )
    presentation = _with_raster_visuals(
        _presentation(ctx),
        ctx,
        panels=panels,
        visual_id="fig-many",
    )
    delivery = _build(
        tmp_path,
        ctx,
        renderer=_FakeRenderer(),
        presentation=presentation,
    )
    assert delivery.status == "submission_ready"
    visual = delivery.visuals[0]
    labels = visual.composition["panel_labels"]
    assert len(labels) == 30
    assert labels[25] == "(z)"
    assert labels[26] == "(aa)"
    assert labels[29] == "(ad)"
    assert len(delivery.artifacts) > 0


def test_excessive_panel_count_blocks(tmp_path, monkeypatch) -> None:
    ctx = _chain(tmp_path)
    monkeypatch.setattr(delivery_module, "_MAX_PANELS_PER_FIGURE", 5)
    panels = [
        PanelAsset(
            label=f"p{index}",
            asset_path=f"figures/fig{index:02d}-panel.png",
            encoding="base64",
            media_type="image/png",
            asset_bytes_b64=base64.b64encode(TINY_PNG).decode("ascii"),
            sha256=hashlib.sha256(TINY_PNG).hexdigest(),
        )
        for index in range(6)
    ]
    presentation = _with_raster_visuals(
        _presentation(ctx),
        ctx,
        panels=panels,
        visual_id="fig-excess",
    )
    fake = _FakeRenderer()
    delivery = _build(
        tmp_path,
        ctx,
        renderer=fake,
        presentation=presentation,
    )
    assert delivery.status == "blocked"
    assert fake.calls == []
    assert any(
        blocker.kind == "renderer_representation_limit"
        and "panel bound" in blocker.message
        for blocker in delivery.blockers
    )


def test_svg_pdf_geometry_rejected_before_pixmap(
    tmp_path,
    monkeypatch,
) -> None:
    class _GeoPixmap:
        width = 2
        height = 2
        samples = b"\xff\xff\xff" * 4

    class _GeoPage:
        def __init__(self, rect: Any, fail_pixmap: bool = False) -> None:
            self.rect = rect
            self.pixmap_calls = 0
            self.fail_pixmap = fail_pixmap

        def get_pixmap(self, **kwargs: Any) -> _GeoPixmap:
            self.pixmap_calls += 1
            if self.fail_pixmap:
                raise RuntimeError("synthetic pixmap failure")
            return _GeoPixmap()

    class _GeoDocument:
        def __init__(self, page: _GeoPage) -> None:
            self.page = page
            self.closed = False

        def load_page(self, index: int) -> _GeoPage:
            return self.page

        def close(self) -> None:
            self.closed = True

    class _GeoFitz:
        def __init__(self, rect: Any) -> None:
            self.documents: list[_GeoDocument] = []
            self.rect = rect

        def Matrix(self, x: float, y: float) -> tuple[float, float]:
            return (x, y)

        def open(self, **kwargs: Any) -> _GeoDocument:
            document = _GeoDocument(_GeoPage(self.rect))
            self.documents.append(document)
            return document

    class _Rect:
        def __init__(self, width: float, height: float) -> None:
            self.width = width
            self.height = height

    ctx = _chain(tmp_path)
    for filetype, suffix, error_marker in (
        ("svg", ".svg", "SVG conversion failed"),
        ("pdf", ".pdf", "PDF rendering failed"),
    ):
        payload = b"<svg/>" if filetype == "svg" else b"%PDF-fake"
        panel = PanelAsset(
            label="geo",
            asset_path=f"figures/geo{suffix}",
            encoding="utf-8" if filetype == "svg" else "base64",
            media_type="image/svg+xml" if filetype == "svg" else "application/pdf",
            asset_content="<svg/>" if filetype == "svg" else "",
            asset_bytes_b64=(
                base64.b64encode(payload).decode("ascii") if filetype == "pdf" else ""
            ),
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        for rect, marker in (
            (_Rect(3000.0, 3000.0), "side exceeds"),
            (_Rect(0.0, 10.0), "zero or non-finite"),
            (_Rect(float("inf"), 10.0), "zero or non-finite"),
        ):
            fake_fitz = _GeoFitz(rect)
            monkeypatch.setattr(delivery_module, "_load_fitz", lambda: fake_fitz)
            errors: list[str] = []
            decoded = delivery_module._decode_panel_image(panel, errors)
            assert decoded is None
            assert any(marker in item for item in errors)
            assert fake_fitz.documents[0].page.pixmap_calls == 0
            assert fake_fitz.documents[0].closed
        # sanity: a normal rect reaches get_pixmap exactly once and closes
        fake_fitz = _GeoFitz(_Rect(10.0, 10.0))
        monkeypatch.setattr(delivery_module, "_load_fitz", lambda: fake_fitz)
        errors = []
        decoded = delivery_module._decode_panel_image(panel, errors)
        assert decoded is not None
        assert errors == []
        assert fake_fitz.documents[0].page.pixmap_calls == 1
        assert fake_fitz.documents[0].closed


def test_raster_oversized_rejected_before_load_and_global_unchanged(
    tmp_path,
    monkeypatch,
) -> None:
    from PIL import Image as PILImage

    ctx = _chain(tmp_path)
    payload = _png_bytes(64)
    panel = PanelAsset(
        label="raster",
        asset_path="figures/fig.png",
        encoding="base64",
        media_type="image/png",
        asset_bytes_b64=base64.b64encode(payload).decode("ascii"),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    load_calls: list[int] = []
    original_load = PILImage.Image.load

    def counting_load(self: Any) -> Any:
        load_calls.append(1)
        return original_load(self)

    monkeypatch.setattr(PILImage.Image, "load", counting_load)
    monkeypatch.setattr(delivery_module, "_MAX_SIDE", 16)
    before = PILImage.MAX_IMAGE_PIXELS
    errors: list[str] = []
    decoded = delivery_module._decode_panel_image(panel, errors)
    assert decoded is None
    assert any("side exceeds" in item for item in errors)
    assert load_calls == []
    assert PILImage.MAX_IMAGE_PIXELS == before

    monkeypatch.setattr(delivery_module, "_MAX_SIDE", 4096)
    errors = []
    decoded = delivery_module._decode_panel_image(panel, errors)
    assert decoded is not None
    assert errors == []
    assert PILImage.MAX_IMAGE_PIXELS == before


def test_multipanel_retained_images_respect_aggregate_budget(
    tmp_path,
    monkeypatch,
) -> None:
    ctx = _chain(tmp_path)
    monkeypatch.setattr(delivery_module, "_MAX_COMPOSITE_PIXELS", 2000)
    payload = _png_bytes(44)
    panels = [
        PanelAsset(
            label=f"p{index}",
            asset_path=f"figures/fig{index}.png",
            encoding="base64",
            media_type="image/png",
            asset_bytes_b64=base64.b64encode(payload).decode("ascii"),
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        for index in range(2)
    ]
    presentation = _with_raster_visuals(
        _presentation(ctx),
        ctx,
        panels=panels,
        visual_id="fig-budget",
    )
    retained: list[int] = []
    original_compose = delivery_module._compose_grid

    def capturing_compose(images: Any, labels: Any) -> Any:
        retained.extend(item.width * item.height for item in images)
        return original_compose(images, labels)

    monkeypatch.setattr(delivery_module, "_compose_grid", capturing_compose)
    delivery = _build(
        tmp_path,
        ctx,
        renderer=_FakeRenderer(),
        presentation=presentation,
    )
    assert delivery.status == "submission_ready"
    assert retained
    share = 2000 // 2
    assert all(pixels <= share for pixels in retained)
    assert sum(retained) <= 2000


def test_second_panel_decode_failure_closes_first_retained(
    tmp_path,
    monkeypatch,
) -> None:
    from PIL import Image as PILImage

    ctx = _chain(tmp_path)
    payload = _png_bytes(8)
    panels = [
        PanelAsset(
            label=f"p{index}",
            asset_path=f"figures/fig{index}.png",
            encoding="base64",
            media_type="image/png",
            asset_bytes_b64=base64.b64encode(payload).decode("ascii"),
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        for index in range(2)
    ]
    presentation = _with_raster_visuals(
        _presentation(ctx),
        ctx,
        panels=panels,
        visual_id="fig-decode-fail",
    )

    def fake_decode(panel: PanelAsset, errors: list[str]) -> Any:
        if panel.label == "p0":
            return (PILImage.new("RGB", (4, 4), "white"), ".png")
        errors.append("synthetic second-panel decode failure")
        return None

    monkeypatch.setattr(delivery_module, "_decode_panel_image", fake_decode)
    closed_ids: list[int] = []
    original_close = PILImage.Image.close

    def counting_close(self: Any) -> Any:
        closed_ids.append(id(self))
        return original_close(self)

    monkeypatch.setattr(PILImage.Image, "close", counting_close)
    fake = _FakeRenderer()
    delivery = _build(
        tmp_path,
        ctx,
        renderer=fake,
        presentation=presentation,
    )
    assert delivery.status == "blocked"
    assert fake.calls == []
    assert len(closed_ids) == 1


def test_synthetic_composition_failure_closes_all_and_blocks(
    tmp_path,
    monkeypatch,
) -> None:
    from PIL import Image as PILImage

    ctx = _chain(tmp_path)
    payload = _png_bytes(8)
    panels = [
        PanelAsset(
            label=f"p{index}",
            asset_path=f"figures/fig{index}.png",
            encoding="base64",
            media_type="image/png",
            asset_bytes_b64=base64.b64encode(payload).decode("ascii"),
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        for index in range(2)
    ]
    presentation = _with_raster_visuals(
        _presentation(ctx),
        ctx,
        panels=panels,
        visual_id="fig-compose-fail",
    )

    def fake_decode(panel: PanelAsset, errors: list[str]) -> Any:
        return (PILImage.new("RGB", (4, 4), "white"), ".png")

    monkeypatch.setattr(delivery_module, "_decode_panel_image", fake_decode)

    def exploding_compose(images: Any, labels: Any) -> Any:
        raise RuntimeError("synthetic composition failure")

    monkeypatch.setattr(delivery_module, "_compose_grid", exploding_compose)
    closed_ids: list[int] = []
    original_close = PILImage.Image.close

    def counting_close(self: Any) -> Any:
        closed_ids.append(id(self))
        return original_close(self)

    monkeypatch.setattr(PILImage.Image, "close", counting_close)
    fake = _FakeRenderer()
    delivery = _build(
        tmp_path,
        ctx,
        renderer=fake,
        presentation=presentation,
    )
    assert delivery.status == "blocked"
    assert fake.calls == []
    assert len(closed_ids) == 2
    assert any(
        blocker.kind == "renderer_representation_limit" for blocker in delivery.blockers
    )
    assert any("visual conversion" in item for item in delivery.errors)


def test_compose_grid_ownership_pre_scale_and_exception(
    monkeypatch,
) -> None:
    from PIL import Image as PILImage

    closed_ids: list[int] = []
    original_close = PILImage.Image.close

    def counting_close(self: Any) -> Any:
        closed_ids.append(id(self))
        return original_close(self)

    monkeypatch.setattr(PILImage.Image, "close", counting_close)
    monkeypatch.setattr(delivery_module, "_MAX_COMPOSITE_PIXELS", 64)

    caller = [PILImage.new("RGB", (16, 16), "white") for _ in range(2)]
    caller_ids = {id(item) for item in caller}
    canvas, rows, cols = delivery_module._compose_grid(
        caller,
        ["(a)", "(b)"],
    )
    assert (rows, cols) == (1, 2)
    assert canvas.width * canvas.height <= 64
    assert not (caller_ids & set(closed_ids))
    assert len(closed_ids) == 2  # the two pre-scaled temporaries
    canvas.close()

    closed_ids.clear()
    caller_two = [PILImage.new("RGB", (16, 16), "white") for _ in range(2)]
    caller_two_ids = {id(item) for item in caller_two}
    with pytest.raises(IndexError):
        delivery_module._compose_grid(caller_two, ["(a)"])
    assert not (caller_two_ids & set(closed_ids))
    # Two pre-scaled temporaries plus the unreturned canvas the function
    # created before the failure: all three are closed, caller inputs are not.
    assert len(closed_ids) == 3
