"""Focused tests for the additive Article literature supplement contract."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from optomind_optics.harness.article_literature import (
    LiteratureSupplementIntegrityError,
    LiteratureHypothesis,
    LiteratureEvidenceIdentity,
    build_literature_provider_context,
    load_literature_supplement,
)
from optomind_optics.harness.article_result_synthesis import (
    _derive_plan,
    ResultSynthesisProviderResult,
    synthesize_article_results,
)
from optomind_optics.harness.article_presentation import _build_citations
from optomind_optics.harness.method_research import MethodResearchReport
from optomind_optics.harness.article_writing import build_article_draft_bundle
import optomind_optics.harness.article_continuation as continuation_module

from test_article_result_synthesis import (
    _asset_for,
    _director_plan,
    _planning,
)
from test_article_continuation import _request, _write_source_pipeline
from test_article_presentation import _chain
from test_article_writing import (
    _architecture,
    _good_response,
    _ledger,
    _value_records,
    _writer,
)


PROBE_DIR = (
    Path(__file__).resolve().parents[2]
    / "stage17_real_integration"
    / "article_method_research_probe027_online_s2_reclassified"
)


def _make_supplement_dir(
    tmp_path: Path,
    *,
    source_result_id: str = "result-x",
    old_plan_id: str = "old-plan-x",
    omit_sidecar: bool = False,
    wrong_source_id: bool = False,
    wrong_report_hash: bool = False,
    tamper_director_identity: bool = False,
) -> Path:
    directory = tmp_path / "supplement"
    directory.mkdir(exist_ok=True)
    report = PROBE_DIR / "METHOD_RESEARCH_REPORT.json"
    director = PROBE_DIR / "ARTICLE_DIRECTOR_SUPPLEMENT_ALIAS_FINAL.json"
    report_copy = directory / report.name
    director_copy = directory / director.name
    shutil.copyfile(report, report_copy)
    shutil.copyfile(director, director_copy)
    if tamper_director_identity:
        director_payload = json.loads(director_copy.read_text(encoding="utf-8"))
        identity_rows = director_payload.get("plan", {}).get(
            "evidence_identity",
            [],
        )
        if identity_rows:
            identity_rows[0]["title"] = "Tampered title"
        director_copy.write_text(
            json.dumps(director_payload),
            encoding="utf-8",
        )
    if omit_sidecar:
        return directory
    report_data = json.loads(report_copy.read_text(encoding="utf-8"))
    director_data = json.loads(director_copy.read_text(encoding="utf-8"))
    report_sha = hashlib.sha256(report_copy.read_bytes()).hexdigest()
    director_sha = hashlib.sha256(director_copy.read_bytes()).hexdigest()
    if wrong_report_hash:
        report_sha = "00" * 32
    sidecar = {
        "schema_version": "article-literature-supplement-metadata.v1",
        "source_pipeline_result_id": (
            "different-source" if wrong_source_id else source_result_id
        ),
        "old_director_plan_id": old_plan_id,
        "report_identity": report_data["problem_id"],
        "new_plan_id": director_data["plan"]["plan_id"],
        "report_sha256": report_sha,
        "director_sha256": director_sha,
    }
    (directory / "LITERATURE_SUPPLEMENT_METADATA.json").write_text(
        json.dumps(sidecar),
        encoding="utf-8",
    )
    return directory


def _load(
    tmp_path: Path,
    *,
    source_result_id: str = "result-x",
    old_plan_id: str = "old-plan-x",
    **kwargs: Any,
) -> Any:
    directory = _make_supplement_dir(
        tmp_path,
        source_result_id=source_result_id,
        old_plan_id=old_plan_id,
        **kwargs,
    )
    return load_literature_supplement(
        directory / "METHOD_RESEARCH_REPORT.json",
        directory / "ARTICLE_DIRECTOR_SUPPLEMENT_ALIAS_FINAL.json",
        expected_source_pipeline_result_id=source_result_id,
        expected_old_director_plan_id=old_plan_id,
    )


def test_valid_supplement_load_exposes_contract(tmp_path: Path) -> None:
    supplement = _load(tmp_path)
    assert supplement.evidence_count == len(supplement.evidence)
    assert supplement.evidence_count >= 19
    assert supplement.evidence[0].alias == "E01"
    assert supplement.evidence_aliases["E01"] == supplement.evidence[0].evidence_id
    assert supplement.method_findings
    assert supplement.new_plan_id
    assert supplement.usage.estimated_cost_cny >= 0
    assert supplement.report_sha256
    assert any(
        "supplementary context" in item for item in supplement.limits
    )


def test_missing_report_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(
        LiteratureSupplementIntegrityError,
        match="method research report is missing",
    ):
        load_literature_supplement(
            tmp_path / "missing.json",
            PROBE_DIR / "ARTICLE_DIRECTOR_SUPPLEMENT_ALIAS_FINAL.json",
        )


def test_old_plan_identity_mismatch_fails_closed(tmp_path: Path) -> None:
    _load(tmp_path)
    director_data = json.loads(
        (PROBE_DIR / "ARTICLE_DIRECTOR_SUPPLEMENT_ALIAS_FINAL.json")
        .read_text(encoding="utf-8")
    )
    new_plan_id = director_data["plan"]["plan_id"]
    with pytest.raises(
        LiteratureSupplementIntegrityError,
        match="old_director_plan_id",
    ):
        load_literature_supplement(
            tmp_path / "supplement" / "METHOD_RESEARCH_REPORT.json",
            tmp_path
            / "supplement"
            / "ARTICLE_DIRECTOR_SUPPLEMENT_ALIAS_FINAL.json",
            expected_source_pipeline_result_id="result-x",
            expected_old_director_plan_id=new_plan_id,
        )


def test_provider_context_uses_aliases_not_canonical_ids(
    tmp_path: Path,
) -> None:
    context = build_literature_provider_context(_load(tmp_path))
    assert context["evidence"]
    assert all("alias" in item for item in context["evidence"])
    assert all("evidence_id" not in item for item in context["evidence"])
    assert all(
        item["evidence_aliases"] for item in context["method_findings"]
    )


class _CaptureProvider:
    def __init__(self) -> None:
        self.inputs: list[Any] = []

    def __call__(self, payload: Any) -> ResultSynthesisProviderResult:
        self.inputs.append(payload)
        return ResultSynthesisProviderResult(
            findings=[],
            usage={},
            provider_model="fake-provider",
        )


def test_synthesis_payload_includes_literature_context(tmp_path: Path) -> None:
    plan = _director_plan()
    planning = _planning(plan)
    assets = [_asset_for(row) for row in planning.rows]
    provider = _CaptureProvider()
    synthesize_article_results(
        plan,
        planning,
        assets,
        provider=provider,
        literature_supplement=_load(tmp_path),
    )
    assert provider.inputs
    context = provider.inputs[0].literature_context
    assert context is not None
    assert context["evidence"]
    assert "evidence_id" not in context["evidence"][0]


def test_writing_payload_includes_literature_context_only_when_supplied(
    tmp_path: Path,
) -> None:
    plan, ledger = _ledger()
    architecture, story_id = _architecture(plan, ledger)
    captured: list[dict] = []
    build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(_good_response, captured=captured),
        literature_context={"evidence": [{"alias": "E01"}]},
    )
    assert captured[0]["literature_context"]["evidence"][0]["alias"] == "E01"

    captured_default: list[dict] = []
    build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(),
        section_writer=_writer(_good_response, captured=captured_default),
    )
    assert "literature_context" not in captured_default[0]


def test_sidecar_missing_fails_closed(tmp_path: Path) -> None:
    directory = _make_supplement_dir(tmp_path, omit_sidecar=True)
    with pytest.raises(
        LiteratureSupplementIntegrityError,
        match="sidecar is missing",
    ):
        load_literature_supplement(
            directory / "METHOD_RESEARCH_REPORT.json",
            directory / "ARTICLE_DIRECTOR_SUPPLEMENT_ALIAS_FINAL.json",
            expected_source_pipeline_result_id="result-x",
            expected_old_director_plan_id="old-plan-x",
        )


def test_sidecar_source_id_mismatch_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(
        LiteratureSupplementIntegrityError,
        match="source_pipeline_result_id",
    ):
        _load(tmp_path, wrong_source_id=True)


def test_sidecar_report_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(
        LiteratureSupplementIntegrityError,
        match="report_sha256",
    ):
        _load(tmp_path, wrong_report_hash=True)


def test_continuation_rejects_unbound_real_supplement_directory(
    tmp_path: Path,
) -> None:
    source_dir = _write_source_pipeline(tmp_path)
    supplement_dir = _make_supplement_dir(
        tmp_path,
        source_result_id="anything",
        old_plan_id="anything",
    )
    continuation = continuation_module.ArticleContinuation()
    result = continuation.run(
        _request(
            source_dir,
            tmp_path / "work",
            literature_supplement_path=str(supplement_dir),
        )
    )
    assert result.status == "failed"
    assert any(
        "literature supplement validation failed" in item
        for item in result.errors
    )


def test_real_sidecar_with_actual_ids_and_plan_context() -> None:
    supplement = load_literature_supplement(
        PROBE_DIR / "METHOD_RESEARCH_REPORT.json",
        PROBE_DIR / "ARTICLE_DIRECTOR_SUPPLEMENT_ALIAS_FINAL.json",
        expected_source_pipeline_result_id=(
            "e64afc4180cf677ce3b829d228c602846ce6d96404da37cba7efafd575fabd1b"
        ),
        expected_old_director_plan_id="plan-91f2c461d0266443",
    )
    assert supplement.source_pipeline_result_id == (
        "e64afc4180cf677ce3b829d228c602846ce6d96404da37cba7efafd575fabd1b"
    )
    context = build_literature_provider_context(supplement)
    assert context["new_plan_hypotheses"]
    assert all(item["hypothesis_id"] for item in context["new_plan_hypotheses"])
    assert any(
        item["evidence_aliases"]
        for item in context["new_plan_hypotheses"]
    )
    assert "research_influence" in context
    assert "unresolved_decisions" in context


def test_derive_plan_augmentation_with_real_supplement(tmp_path: Path) -> None:
    plan = _director_plan()
    supplement = _load(tmp_path)
    derived, warnings = _derive_plan(
        plan,
        [],
        {},
        literature_supplement=supplement,
    )
    assert derived.plan_id != plan.plan_id
    assert derived.evidence_identity
    matched = next(
        item for item in derived.hypotheses if item.hypothesis_id == "hyp-01"
    )
    original = next(
        item for item in plan.hypotheses if item.hypothesis_id == "hyp-01"
    )
    assert matched.statement == original.statement
    assert matched.evidence_ids
    report_evidence_ids = {item.evidence_id for item in supplement.evidence}
    assert set(matched.evidence_ids) <= report_evidence_ids
    assert all(
        item.startswith("unmatched supplemental hypotheses")
        for item in warnings
    )


def test_derive_plan_rejects_mismatched_supplement_evidence(
    tmp_path: Path,
) -> None:
    plan = _director_plan()
    supplement = _load(tmp_path)
    tampered = supplement.model_copy(
        update={
            "new_plan_hypotheses": [
                LiteratureHypothesis(
                    hypothesis_id=item.hypothesis_id,
                    statement=item.statement,
                    falsifiable_prediction=item.falsifiable_prediction,
                    evidence_ids=["ghost-id"],
                    evidence_aliases=[],
                    route_kind=item.route_kind,
                    novelty_rationale=item.novelty_rationale,
                    risk_notes=item.risk_notes,
                )
                for item in supplement.new_plan_hypotheses
            ],
            "research_influence": [
                *supplement.research_influence,
                "unmatched hypothesis hyp-99 is advisory only",
            ],
        }
    )
    derived, warnings = _derive_plan(
        plan,
        [],
        {},
        literature_supplement=tampered,
    )
    matched = next(
        item for item in derived.hypotheses if item.hypothesis_id == "hyp-01"
    )
    assert "ghost-id" not in matched.evidence_ids
    assert any("unverified evidence" in item for item in warnings)


def test_presentation_restores_real_citation_from_augmented_plan(
    tmp_path: Path,
) -> None:
    ctx = _chain(tmp_path)
    plan = ctx["plan"].model_copy(
        update={
            "hypotheses": [
                item.model_copy(update={"evidence_ids": []})
                for item in ctx["plan"].hypotheses
            ]
        }
    )
    supplement = _load(tmp_path)
    derived, _ = _derive_plan(
        plan,
        [],
        {},
        literature_supplement=supplement,
    )
    report = MethodResearchReport.model_validate(
        json.loads(
            (PROBE_DIR / "METHOD_RESEARCH_REPORT.json").read_text(
                encoding="utf-8"
            )
        )
    )
    evidence_by_id = {item.evidence_id: item for item in report.evidence}
    blockers: list[Any] = []
    warnings: list[str] = []
    citations, references, _ = _build_citations(
        plan=derived,
        ledger=ctx["ledger"],
        manuscript=ctx["manuscript"],
        evidence_by_id=evidence_by_id,
        bibliographic_metadata={},
        blockers=blockers,
        warnings=warnings,
    )
    assert blockers == []
    assert references or citations


def test_director_identity_text_hash_mismatch_fails_closed(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        LiteratureSupplementIntegrityError,
        match="evidence identity mismatch",
    ):
        _load(tmp_path, tamper_director_identity=True)


def test_old_plan_evidence_identity_collision_fails_closed(
    tmp_path: Path,
) -> None:
    plan = _director_plan()
    supplement = _load(tmp_path)
    first_evidence_id = supplement.evidence_identity[0].evidence_id
    from optomind_optics.harness.article_director import (
        EvidenceIdentityManifest,
    )

    colliding = plan.model_copy(
        update={
            "evidence_identity": [
                EvidenceIdentityManifest(
                    evidence_id=first_evidence_id,
                    paper_id="different-paper",
                    doi="",
                    title="Different title",
                    year=1999,
                    source_route="forged",
                    content_depth="metadata",
                    allowed_use="discovery",
                    text_sha256="00" * 32,
                )
            ]
        }
    )
    with pytest.raises(
        ValueError,
        match="evidence identity collision",
    ):
        _derive_plan(
            colliding,
            [],
            {},
            literature_supplement=supplement,
        )
