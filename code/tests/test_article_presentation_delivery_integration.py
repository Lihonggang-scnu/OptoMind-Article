"""Offline Stage 12C/12D integration tests (no model/network calls)."""

from __future__ import annotations

import json
import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from optomind_optics.harness.article_architecture import ArtifactDescriptor
from optomind_optics.harness.article_presentation import (
    _compact_candidate_display,
    _compact_scalar_label,
    _load_numeric_rows,
    _render_scalar_bar_svg,
    _scalar_domain,
    _verify_quantitative_artifact,
)
from optomind_optics.harness.article_reproducibility import (
    ArtifactLineageRecord,
    ArticleReproducibilityPackage,
    CriticalExperimentRecord,
    PublicationBlocker,
)
from optomind_research.runtime.latex_publication_renderer import _render_main_tex
from optomind_research.runtime.publication_figure_processor import (
    prepare_publication_figure,
)
from test_article_presentation import _biblio, _chain


sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "scripts"),
)
import run_article_presentation_delivery as integration


def test_main_tex_omits_empty_bibliography() -> None:
    metadata = {
        "title": "A bounded article title",
        "abstract": "A bounded abstract.",
        "keywords": ["optics"],
        "authors": [{"name": "Test Author"}],
        "date": "",
        "acknowledgements": "",
    }
    without_references = _render_main_tex(
        metadata=metadata,
        body_tex="Body.",
        include_bibliography=False,
    )
    with_references = _render_main_tex(
        metadata=metadata,
        body_tex="Body.",
        include_bibliography=True,
    )
    assert "\\bibliography{references}" not in without_references
    assert "\\bibliography{references}" in with_references


def test_compact_candidate_display_and_edge_scalar_label() -> None:
    assert _compact_candidate_display(
        "opt_7layer_dbr_low_r__gradient_thickness__02"
    ) == "gradient-thickness 02"
    assert _compact_candidate_display("optimize_opaque_absorber__baseline") == "baseline"
    assert _compact_candidate_display("different__dd39b3bfb7") == "variant dd39b3bfb7"
    svg = _render_scalar_bar_svg(
        [("physics_certificate.spectral_convergence.final_points", [1001.0])],
        "certificate.json",
    )
    assert ">1001</text>" in svg
    assert 'text-anchor="end"' in svg


def test_preprocessed_article_figure_is_not_cropped_twice(tmp_path: Path) -> None:
    from PIL import Image, ImageDraw

    source = tmp_path / "source.png"
    destination = tmp_path / "publication.png"
    image = Image.new("RGB", (640, 420), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 30, 610, 200), outline="blue", width=3)
    draw.text((50, 330), "Final diagram stage", fill="black")
    image.save(source)

    audit = prepare_publication_figure(
        source,
        destination,
        {"caption_crop_policy": "preserve_preprocessed_asset"},
    )
    with Image.open(destination) as rendered:
        assert rendered.size == (640, 420)
    assert audit["caption_crop_status"] == "preserved_preprocessed_asset"


def test_duplicate_artifact_bindings_render_as_one_panel(tmp_path: Path) -> None:
    from optomind_optics.harness.article_architecture import ArtifactFieldBinding
    from optomind_optics.harness.article_presentation import _group_artifact_bindings

    warnings: list[str] = []
    grouped = _group_artifact_bindings(
        [
            ArtifactFieldBinding(
                artifact_id="portfolio.json",
                selected_fields=["candidate-a.target_score"],
            ),
            ArtifactFieldBinding(
                artifact_id="portfolio.json",
                selected_fields=["candidate-a.robustness_score"],
            ),
        ],
        figure_id="figure-1",
        warnings=warnings,
    )
    assert len(grouped) == 1
    assert grouped[0].selected_fields == [
        "candidate-a.robustness_score",
        "candidate-a.target_score",
    ]
    assert warnings


def _real_inputs() -> dict:
    return integration.load_real_chain()


def test_real_persisted_chain_reaches_compiled_awaiting_metadata(
    tmp_path: Path,
) -> None:
    inputs = _real_inputs()
    summary = integration.run_presentation_delivery(
        inputs,
        output_dir=tmp_path / "out",
        compile_pdf=False,
    )
    assert summary["status"] == "compiled_awaiting_metadata"
    assert summary["presentation"]["blockers"] == []
    assert summary["delivery"]["blockers"] == []
    assert len(summary["presentation"]["visuals"]) >= 4
    assert all(
        panel.get("asset_content")
        for visual in summary["presentation"]["visuals"]
        for panel in visual.get("panels", [])
    )
    for visual in summary["presentation"]["visuals"]:
        for panel in visual.get("panels", []):
            content = panel.get("asset_content") or ""
            if panel.get("media_type") == "image/svg+xml":
                assert content.lstrip().startswith("<svg")
                visible = _strip_svg_metadata(content)
                assert "robustness_report." not in visible
                assert "objective_report." not in visible
                assert "x/e_" not in visible
    assert "latex_toolchain" in summary
    assert "pdflatex" in summary["latex_toolchain"]
    summary_path = tmp_path / "out" / "INTEGRATION_SUMMARY.json"
    assert summary_path.is_file()
    assert json.loads(summary_path.read_text(encoding="utf-8"))[
        "status"
    ] == "compiled_awaiting_metadata"

    second = integration.run_presentation_delivery(
        inputs,
        output_dir=tmp_path / "out",
        compile_pdf=False,
    )
    assert second["presentation"]["package_id"] == summary["presentation"][
        "package_id"
    ]


def test_missing_persisted_inputs_reports_diagnostic(tmp_path: Path) -> None:
    with pytest.raises(
        integration.ChainLoadError,
        match="missing persisted upstream inputs",
    ):
        integration.load_real_chain(tmp_path)


def test_tampered_upstream_identity_fails_closed(tmp_path: Path) -> None:
    inputs = _real_inputs()
    inputs["architecture"] = {
        **inputs["architecture"],
        "architecture_id": "0" * 16,
    }
    summary = integration.run_presentation_delivery(
        inputs,
        output_dir=tmp_path / "out",
        compile_pdf=False,
    )
    assert summary["status"] == "identity_failed"
    assert summary["identity_errors"]


def test_happy_path_and_idempotent_output(tmp_path: Path) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    inputs = {
        "plan": ctx["plan"],
        "ledger": ctx["ledger"],
        "architecture": ctx["architecture"],
        "review": ctx["review"],
        "manuscript": ctx["manuscript"],
        "reproducibility": ctx["reproducibility"],
        "story_id": ctx["story_id"],
        "value_records": ctx["values"],
        "method_evidence": ctx["evidence"],
        "artifact_roots": [ctx["run_dir"]],
    }
    renderer = integration.DeterministicRenderer(compile_pdf=False)
    output_dir = tmp_path / "out"
    first = integration.run_presentation_delivery(
        inputs,
        output_dir=output_dir,
        compile_pdf=False,
        renderer=renderer,
        bibliographic_metadata=_biblio(),
    )
    assert first["status"] == "compiled_awaiting_metadata"
    assert first["presentation"]["status"] in {
        "ready",
        "ready_with_findings",
    }
    assert first["delivery"]["status"] == "compiled_awaiting_metadata"
    assert first["renderer_invoked"] is True

    second = integration.run_presentation_delivery(
        inputs,
        output_dir=output_dir,
        compile_pdf=False,
        renderer=integration.DeterministicRenderer(compile_pdf=False),
        bibliographic_metadata=_biblio(),
    )
    assert second["presentation"]["package_id"] == first["presentation"][
        "package_id"
    ]
    assert second["delivery"]["package_id"] == first["delivery"]["package_id"]
    assert second["status"] == first["status"]


def test_delivery_blocks_incomplete_bibliography_without_metadata(
    tmp_path: Path,
) -> None:
    ctx = _chain(tmp_path, figure_kind="table")
    inputs = {
        "plan": ctx["plan"],
        "ledger": ctx["ledger"],
        "architecture": ctx["architecture"],
        "review": ctx["review"],
        "manuscript": ctx["manuscript"],
        "reproducibility": ctx["reproducibility"],
        "story_id": ctx["story_id"],
        "value_records": ctx["values"],
        "method_evidence": ctx["evidence"],
        "artifact_roots": [ctx["run_dir"]],
    }
    summary = integration.run_presentation_delivery(
        inputs,
        output_dir=tmp_path / "out",
        compile_pdf=False,
        renderer=integration.DeterministicRenderer(compile_pdf=False),
    )
    assert summary["status"] == "blocked"
    assert any(
        item["kind"] == "incomplete_bibliographic_metadata"
        for item in summary["delivery"]["blockers"]
    )


def _quantitative_artifact_fixture(
    tmp_path: Path,
    *,
    physical_ids: list[str],
    critical_records: list[CriticalExperimentRecord] | None = None,
    canonical_source_sha: str | None = None,
    lineage_experiment_id: str | None = None,
):
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "x.json").write_text(
        '{"value": 1, "runtime_lock": "lease"}',
        encoding="utf-8",
    )
    sha = hashlib.sha256((run_dir / "x.json").read_bytes()).hexdigest()
    from optomind_optics.harness.replay import _scientific_digest

    canonical_sha = _scientific_digest(
        run_dir / "x.json",
        root=run_dir,
        path_index={},
    )
    assert canonical_sha != sha
    descriptor = ArtifactDescriptor(
        artifact_id="x.json",
        path="x.json",
        fields=["x"],
        sha256=sha,
        source_experiment_ids=physical_ids,
        source_observation_ids=[],
    )
    records = critical_records or [
        CriticalExperimentRecord(
            experiment_id="experiment-9f7d7dbaf9103532",
            physical_experiment_ids=["optimize_opaque_absorber"],
            source_run_dir="",
            rationale="fixture",
        )
    ]
    lineage = [
        ArtifactLineageRecord(
            lineage_id="lineage-x",
            artifact_id="x.json",
            experiment_id=lineage_experiment_id
            or "experiment-9f7d7dbaf9103532",
            relative_path="x.json",
            source_sha256=(
                canonical_source_sha
                if canonical_source_sha is not None
                else sha
            ),
            replay_sha256=(
                canonical_source_sha
                if canonical_source_sha is not None
                else sha
            ),
            identity_kind="canonical_scientific_identity",
            matched=True,
        )
    ]
    reproducibility = ArticleReproducibilityPackage(
        package_id="pkg-x",
        plan_id="plan-x",
        ledger_id="ledger-x",
        architecture_id="arch-x",
        review_id="review-x",
        result_id="result-x",
        manuscript_body_id="body-x",
        story_id="story-01",
        status="ready",
        critical_experiments=records,
        lineage=lineage,
    )
    return run_dir, descriptor, sha, reproducibility, canonical_sha


def test_physical_to_article_lineage_mapping_is_compatible(
    tmp_path: Path,
) -> None:
    run_dir, descriptor, _, reproducibility, _ = (
        _quantitative_artifact_fixture(
            tmp_path,
            physical_ids=["optimize_opaque_absorber"],
        )
    )
    blockers: list[PublicationBlocker] = []
    path, actual_sha = _verify_quantitative_artifact(
        descriptor=descriptor,
        roots=[run_dir],
        reproducibility=reproducibility,
        blockers=blockers,
        contract_figure_id="figure-x",
    )
    assert path == run_dir / "x.json"
    assert actual_sha == descriptor.sha256
    assert blockers == []


def test_unknown_physical_mapping_hard_blocks(tmp_path: Path) -> None:
    run_dir, descriptor, _, reproducibility, _ = (
        _quantitative_artifact_fixture(
            tmp_path,
            physical_ids=["optimize_absorber_5layer"],
        )
    )
    blockers: list[PublicationBlocker] = []
    path, _ = _verify_quantitative_artifact(
        descriptor=descriptor,
        roots=[run_dir],
        reproducibility=reproducibility,
        blockers=blockers,
        contract_figure_id="figure-x",
    )
    assert path is None
    assert any(
        item.kind == "artifact_lineage_missing" for item in blockers
    )


def test_ambiguous_physical_mapping_hard_blocks(tmp_path: Path) -> None:
    records = [
        CriticalExperimentRecord(
            experiment_id="experiment-a",
            physical_experiment_ids=["optimize_opaque_absorber"],
            source_run_dir="",
            rationale="fixture",
        ),
        CriticalExperimentRecord(
            experiment_id="experiment-b",
            physical_experiment_ids=["optimize_opaque_absorber"],
            source_run_dir="",
            rationale="fixture",
        ),
    ]
    run_dir, descriptor, _, reproducibility, _ = (
        _quantitative_artifact_fixture(
            tmp_path,
            physical_ids=["optimize_opaque_absorber"],
            critical_records=records,
        )
    )
    blockers: list[PublicationBlocker] = []
    path, _ = _verify_quantitative_artifact(
        descriptor=descriptor,
        roots=[run_dir],
        reproducibility=reproducibility,
        blockers=blockers,
        contract_figure_id="figure-x",
    )
    assert path is None
    assert any(
        item.kind == "artifact_lineage_missing" for item in blockers
    )


def test_canonical_lineage_digest_accepted_with_raw_descriptor(
    tmp_path: Path,
) -> None:
    _, _, _, _, canonical_sha = _quantitative_artifact_fixture(
        tmp_path,
        physical_ids=["optimize_opaque_absorber"],
    )
    run_dir, descriptor, _, reproducibility, _ = _quantitative_artifact_fixture(
        tmp_path,
        physical_ids=["optimize_opaque_absorber"],
        canonical_source_sha=canonical_sha,
    )
    blockers: list[PublicationBlocker] = []
    path, actual_sha = _verify_quantitative_artifact(
        descriptor=descriptor,
        roots=[run_dir],
        reproducibility=reproducibility,
        blockers=blockers,
        contract_figure_id="figure-x",
    )
    assert path == run_dir / "x.json"
    assert actual_sha == descriptor.sha256
    assert blockers == []


def test_wrong_canonical_digest_blocks(tmp_path: Path) -> None:
    run_dir, descriptor, _, reproducibility, _ = _quantitative_artifact_fixture(
        tmp_path,
        physical_ids=["optimize_opaque_absorber"],
        canonical_source_sha="00" * 32,
    )
    blockers: list[PublicationBlocker] = []
    path, _ = _verify_quantitative_artifact(
        descriptor=descriptor,
        roots=[run_dir],
        reproducibility=reproducibility,
        blockers=blockers,
        contract_figure_id="figure-x",
    )
    assert path is None
    assert any(
        item.kind == "artifact_lineage_missing" for item in blockers
    )


def test_changed_raw_file_still_blocks(tmp_path: Path) -> None:
    run_dir, descriptor, _, reproducibility, _ = _quantitative_artifact_fixture(
        tmp_path,
        physical_ids=["optimize_opaque_absorber"],
    )
    (run_dir / "x.json").write_text(
        '{"value": 2, "runtime_lock": "other"}',
        encoding="utf-8",
    )
    blockers: list[PublicationBlocker] = []
    path, _ = _verify_quantitative_artifact(
        descriptor=descriptor,
        roots=[run_dir],
        reproducibility=reproducibility,
        blockers=blockers,
        contract_figure_id="figure-x",
    )
    assert path is None
    assert any(
        item.kind == "artifact_hash_mismatch" for item in blockers
    )


def test_incompatible_lineage_experiment_blocks(tmp_path: Path) -> None:
    run_dir, descriptor, _, reproducibility, _ = _quantitative_artifact_fixture(
        tmp_path,
        physical_ids=["optimize_opaque_absorber"],
        lineage_experiment_id="experiment-other",
    )
    blockers: list[PublicationBlocker] = []
    path, _ = _verify_quantitative_artifact(
        descriptor=descriptor,
        roots=[run_dir],
        reproducibility=reproducibility,
        blockers=blockers,
        contract_figure_id="figure-x",
    )
    assert path is None
    assert any(
        item.kind == "artifact_lineage_missing" for item in blockers
    )


def test_cli_prints_utf8_json_summary(tmp_path: Path) -> None:
    output_dir = tmp_path / "cli-out"
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "scripts" / "run_article_presentation_delivery.py"),
            "--output-dir",
            str(output_dir),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    assert result.returncode == 0
    summary = json.loads(result.stdout)
    assert summary["status"] == "compiled_awaiting_metadata"
    assert (output_dir / "INTEGRATION_SUMMARY.json").is_file()


def _strip_svg_metadata(svg: str) -> str:
    text = svg
    for tag in ("title", "desc", "metadata"):
        text = re.sub(
            rf"<{tag}>.*?</{tag}>",
            "",
            text,
            flags=re.DOTALL,
        )
    return text


def test_scalar_domain_truthful_baseline() -> None:
    assert _scalar_domain([0.534, 0.539]) == (0.0, 1.0)
    assert _scalar_domain([0.5, 1.2]) == (0.0, 1.2)
    assert _scalar_domain([-0.5, -0.2]) == (-0.5, 0.0)
    assert _scalar_domain([-0.2, 0.5]) == (-0.2, 0.5)


def test_compact_scalar_labels() -> None:
    assert (
        _compact_scalar_label("robustness_report.mean_soft_score")
        == "Mean soft score"
    )
    assert (
        _compact_scalar_label("robustness_report.nominal_soft_score")
        == "Nominal soft score"
    )
    assert (
        _compact_scalar_label("robustness_report.robustness_score")
        == "Robustness score"
    )
    assert (
        _compact_scalar_label("robustness_report.worst_soft_score")
        == "Worst soft score"
    )
    assert (
        _compact_scalar_label(
            "objective_report.target_attainment."
            "canonical_a_8000_13000_at_least_mean_0_p_4_1.observed"
        )
        == "Mean A, 0 deg, P"
    )
    assert (
        _compact_scalar_label(
            "objective_report.target_attainment."
            "canonical_a_8000_13000_at_least_worst_case_60_s_5_1.observed"
        )
        == "Worst-case A, 60 deg, S"
    )
    assert (
        _compact_scalar_label(
            "objective_report.target_attainment."
            "canonical_a_3000_5000_at_most_mean_30_p_6_1.observed"
        )
        == "Mean A, 30 deg, P"
    )
    assert (
        _compact_scalar_label(
            "optimize_absorber_5layer__differen__0af40736334b.simplicity_score"
        )
        == "Simplicity Score"
    )


def test_scalar_svg_metadata_and_visible_text() -> None:
    svg = _render_scalar_bar_svg(
        [
            ("robustness_report.mean_soft_score", [0.534]),
            ("robustness_report.robustness_score", [0.539]),
        ],
        "x/e_abc/OBJECTIVE_REPORT.json",
    )
    assert "<title>robustness_report.mean_soft_score</title>" in svg
    assert (
        "<desc>Mean soft score = 0.534"
        " (robustness_report.mean_soft_score)</desc>" in svg
    )
    assert "x/e_abc/OBJECTIVE_REPORT.json" in svg
    visible = _strip_svg_metadata(svg)
    assert "Mean soft score" in visible
    assert "Robustness score" in visible
    assert "x/e_" not in visible
    assert "0.534" in visible
    assert "0.539" in visible


def test_scalar_svg_single_value_is_meaningful() -> None:
    svg = _render_scalar_bar_svg(
        [("portfolio_candidate.simplicity_score", [0.534])],
        "DESIGN_PORTFOLIO.json",
    )
    assert "0.534" in _strip_svg_metadata(svg)
    assert 'width="' in svg
    assert "<title>portfolio_candidate.simplicity_score</title>" in svg


def _write_json(tmp_path: Path, payload: dict, name: str = "x.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_schema_scalar_resolver_positive_cases(tmp_path: Path) -> None:
    robustness = _write_json(
        tmp_path,
        {
            "schema_version": "tmm-robustness-report.v1",
            "mean_soft_score": 0.5,
            "robustness_score": 0.4,
        },
        "robustness.json",
    )
    rows = _load_numeric_rows(
        robustness,
        [
            "robustness_report.mean_soft_score",
            "robustness_report.robustness_score",
        ],
    )
    assert rows == [
        {
            "robustness_report.mean_soft_score": 0.5,
            "robustness_report.robustness_score": 0.4,
        }
    ]

    objective = _write_json(
        tmp_path,
        {
            "schema_version": "tmm-objective-report.v1",
            "aggregate_soft_score": 0.42,
            "weighted_directional_loss": 0.21,
            "target_attainment": {
                "objective-1": {"observed": 0.85}
            },
        },
        "objective.json",
    )
    rows = _load_numeric_rows(
        objective,
        ["objective_report.target_attainment.objective-1.observed"],
    )
    assert rows == [
        {
            "objective_report.target_attainment.objective-1.observed": 0.85
        }
    ]
    rows = _load_numeric_rows(
        objective,
        [
            "objective_report.aggregate_soft_score",
            "objective_report.weighted_directional_loss",
        ],
    )
    assert rows == [
        {
            "objective_report.aggregate_soft_score": 0.42,
            "objective_report.weighted_directional_loss": 0.21,
        }
    ]

    certificate = _write_json(
        tmp_path,
        {
            "schema_version": "physics-acceptance-certificate-v1",
            "physics_audit": {
                "energy_conservation_max_abs_error": 1.0e-15,
            },
            "spectral_convergence": {"final_points": 1001},
        },
        "certificate.json",
    )
    rows = _load_numeric_rows(
        certificate,
        [
            "physics_certificate.physics_audit.energy_conservation_max_abs_error",
            "physics_certificate.spectral_convergence.final_points",
        ],
    )
    assert rows == [
        {
            "physics_certificate.physics_audit.energy_conservation_max_abs_error": 1.0e-15,
            "physics_certificate.spectral_convergence.final_points": 1001.0,
        }
    ]

    portfolio = _write_json(
        tmp_path,
        {
            "schema_version": "optical-design-portfolio.v1",
            "candidates": [
                {
                    "candidate_id": "candidate-a",
                    "simplicity_score": 0.9,
                }
            ],
        },
        "portfolio.json",
    )
    rows = _load_numeric_rows(
        portfolio,
        ["candidate-a.simplicity_score"],
    )
    assert rows == [{"candidate-a.simplicity_score": 0.9}]

    multi_portfolio = _write_json(
        tmp_path,
        {
            "schema_version": "optical-design-portfolio.v1",
            "candidates": [
                {"candidate_id": "candidate-a", "simplicity_score": 0.9},
                {"candidate_id": "candidate-b", "simplicity_score": 0.7},
            ],
        },
        "multi-portfolio.json",
    )
    rows = _load_numeric_rows(
        multi_portfolio,
        ["candidate-a.simplicity_score", "candidate-b.simplicity_score"],
    )
    assert rows == [
        {"candidate_id": "candidate-a", "candidate-a.simplicity_score": 0.9},
        {"candidate_id": "candidate-b", "candidate-b.simplicity_score": 0.7},
    ]


def test_numeric_render_adversarial_cases(tmp_path: Path) -> None:
    robustness = _write_json(
        tmp_path,
        {
            "schema_version": "tmm-robustness-report.v1",
            "mean_soft_score": 0.5,
        },
        "robustness.json",
    )
    with pytest.raises(ValueError, match="wrong semantic wrapper"):
        _load_numeric_rows(
            robustness,
            ["objective_report.mean_soft_score"],
        )

    unknown_candidate = _write_json(
        tmp_path,
        {
            "schema_version": "optical-design-portfolio.v1",
            "candidates": [{"candidate_id": "candidate-a"}],
        },
        "portfolio-unknown.json",
    )
    with pytest.raises(ValueError, match="unknown portfolio candidate"):
        _load_numeric_rows(
            unknown_candidate,
            ["candidate-b.simplicity_score"],
        )

    duplicate_candidate = _write_json(
        tmp_path,
        {
            "schema_version": "optical-design-portfolio.v1",
            "candidates": [
                {"candidate_id": "candidate-a"},
                {"candidate_id": "candidate-a"},
            ],
        },
        "portfolio-duplicate.json",
    )
    with pytest.raises(ValueError, match="duplicate candidate_id"):
        _load_numeric_rows(
            duplicate_candidate,
            ["candidate-a.simplicity_score"],
        )

    missing_nested = _write_json(
        tmp_path,
        {
            "schema_version": "tmm-objective-report.v1",
            "target_attainment": {"objective-1": {"observed": 0.85}},
        },
        "objective-missing.json",
    )
    with pytest.raises(ValueError, match="has no field"):
        _load_numeric_rows(
            missing_nested,
            ["objective_report.target_attainment.objective-1.missing"],
        )
    with pytest.raises(ValueError, match="target_attainment scalar path"):
        _load_numeric_rows(
            missing_nested,
            ["objective_report.unregistered_scalar"],
        )
    certificate = _write_json(
        tmp_path,
        {
            "schema_version": "physics-acceptance-certificate-v1",
            "physics_audit": {"backend": "internal_numpy"},
        },
        "certificate-unsafe.json",
    )
    with pytest.raises(ValueError, match="not a permitted physics certificate"):
        _load_numeric_rows(
            certificate,
            ["physics_certificate.runtime.python"],
        )

    non_finite = _write_json(
        tmp_path,
        {
            "schema_version": "tmm-robustness-report.v1",
            "mean_soft_score": float("nan"),
        },
        "robustness-nan.json",
    )
    with pytest.raises(ValueError, match="non-finite"):
        _load_numeric_rows(
            non_finite,
            ["robustness_report.mean_soft_score"],
        )

    unequal = _write_json(
        tmp_path,
        {
            "schema_version": "tmm-simulation-result.v1",
            "wavelengths_nm": [1.0, 2.0, 3.0],
            "channels": {"angle=0|pol=s.A": {"max": [1.0, 2.0]}},
        },
        "unequal.json",
    )
    with pytest.raises(ValueError, match="length"):
        _load_numeric_rows(
            unequal,
            ["channels.angle=0|pol=s.A.max"],
        )

    empty = _write_json(
        tmp_path,
        {"data": []},
        "empty.json",
    )
    with pytest.raises(ValueError, match="no numeric response series"):
        _load_numeric_rows(empty, ["A"])
