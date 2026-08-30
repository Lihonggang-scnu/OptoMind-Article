from __future__ import annotations

import json
from pathlib import Path

import pytest

from optomind_optics.harness.article_publication import (
    QwenTMMArticleWriter,
    TMMArticleEvidenceCompiler,
    _assemble_markdown,
    _validate_draft,
    _candidate_table,
    _evidence_bound_result_sections,
)


ROOT = Path(__file__).resolve().parents[1]
REAL_FIXTURE = (
    ROOT.parent
    / "accepted_examples"
    / "research_broadband_ar_source"
)


@pytest.mark.skipif(not REAL_FIXTURE.exists(), reason="accepted TMM fixture is unavailable")
def test_evidence_compiler_reads_verified_solver_artifacts(tmp_path: Path) -> None:
    evidence = TMMArticleEvidenceCompiler(REAL_FIXTURE, tmp_path).compile()
    assert evidence["primary_candidate_id"]
    assert Path(evidence["primary_simulation_path"]).is_file()
    assert len(evidence["facts"]) >= 9
    assert evidence["references"]
    assert all(item["source_artifacts"] for item in evidence["facts"])
    scope_fact = next(item for item in evidence["facts"] if item["fact_id"] == "F_SCOPE")
    assert "from 450 to 700 nm" in scope_fact["statement"]
    assert "incidence angles 0, 30, 45 degrees" in scope_fact["statement"]
    assert "incidence angles 1" not in scope_fact["statement"]


@pytest.mark.skipif(not REAL_FIXTURE.exists(), reason="accepted TMM fixture is unavailable")
def test_mock_writer_obeys_fact_and_reference_contract(tmp_path: Path) -> None:
    evidence = TMMArticleEvidenceCompiler(REAL_FIXTURE, tmp_path).compile()
    draft, usage = QwenTMMArticleWriter(force_mock=True).write(evidence)
    assert _validate_draft(draft, evidence) == []
    assert usage["mock_llm"] is True
    markdown = _assemble_markdown(draft, evidence)
    assert "[FACT:" not in markdown
    assert "[REF:" in markdown
    assert "Author information unavailable" not in markdown


@pytest.mark.skipif(not REAL_FIXTURE.exists(), reason="accepted TMM fixture is unavailable")
def test_validator_rejects_fabricated_numbers_and_references(tmp_path: Path) -> None:
    evidence = TMMArticleEvidenceCompiler(REAL_FIXTURE, tmp_path).compile()
    draft, _ = QwenTMMArticleWriter(force_mock=True).write(evidence)
    draft["results"] += " The measured efficiency was 99.9 percent [REF:fake-paper]."
    errors = _validate_draft(draft, evidence)
    assert any("raw_numeric_claim_outside_fact_token:results" in item for item in errors)
    assert any("unknown_reference_ids:fake-paper" in item for item in errors)


def test_prompt_is_english_and_has_no_project_memory_assumption() -> None:
    prompt = (
        ROOT / "prompts" / "optical_harness" / "TMM Article Writer.txt"
    ).read_text(encoding="utf-8")
    assert not any("\u4e00" <= char <= "\u9fff" for char in prompt)
    assert "You know only the supplied task" in prompt
    assert "You create only a publication-style English title" in prompt
    assert "Do not imply fabrication" in prompt
    audit_prompt = (
        ROOT / "prompts" / "optical_harness" / "TMM Article Evidence Auditor.txt"
    ).read_text(encoding="utf-8")
    assert not any("\u4e00" <= char <= "\u9fff" for char in audit_prompt)
    assert "Judge whether every study-specific conclusion is warranted" in audit_prompt


def test_validator_rejects_unverified_scientific_assumptions() -> None:
    evidence = {"facts": [], "references": [], "writer_contract_mode": "deterministic_fact_injection"}
    draft = {
        "title": "Computational Coating Design",
        "abstract": "A " * 40,
        "introduction": "A " * 75,
        "methods": ("A " * 72) + "under normal incidence with negligible absorption.",
        "results": "A " * 75,
        "robustness": "A " * 75,
        "discussion": "A " * 75,
        "limitations": ("A " * 72) + "neglecting angle-dependent effects.",
        "conclusion": "A " * 40,
    }
    errors = _validate_draft(draft, evidence)
    assert "forbidden_reader_facing_language:methods" in errors
    assert "forbidden_reader_facing_language:limitations" in errors


def test_candidate_table_only_presents_distinct_decision_roles() -> None:
    rows = [
        {"candidate_id": "c1", "recommendation_roles": ["best_performance", "most_robust"], "layer_materials": ["MgF2"], "target_score": 0.8, "robustness_score": 0.7, "simplicity_score": 0.5, "reported_metrics": []},
        {"candidate_id": "c2", "recommendation_roles": ["simplest"], "layer_materials": ["SiO2"], "target_score": 0.5, "robustness_score": 0.4, "simplicity_score": 0.9, "reported_metrics": []},
        {"candidate_id": "c3", "route_title": "A route title that should never appear", "layer_materials": ["TiO2"], "target_score": 0.6, "robustness_score": 0.6, "simplicity_score": 0.6, "reported_metrics": []},
    ]
    table = _candidate_table(rows)
    assert "Performance + Robustness" in table
    assert "Simplicity" in table
    assert "route title" not in table


def test_result_bearing_sections_are_deterministic_and_cautious() -> None:
    sections = _evidence_bound_result_sections()
    assert set(sections) == {"results", "robustness", "discussion", "limitations", "conclusion"}
    assert "does not estimate fabrication yield" in sections["robustness"]
    assert "without asserting that layer count" in sections["discussion"]
    assert "best-effort design" in sections["conclusion"]
