from __future__ import annotations

import json

import pytest

from optomind_optics.harness.article_global_quality_audit import (
    audit_article_quality,
    write_global_quality_audit,
)


def _manuscript(text: str):
    return {"source_map": [{"paragraph_id": "p1", "rendered_text": text, "claim_ids": ["c1"]}]}


def _ledger(scope: str = "route"):
    return {
        "claims": [
            {
                "claim_id": "c1",
                "metadata": {
                    "synthesis_contract": {
                        "comparison_scope": scope,
                        "route_alias": "R02",
                        "subject_aliases": ["GC01"],
                    }
                },
            },
            {
                "claim_id": "c2",
                "statement": "Candidate GC03 was included in the same comparison.",
                "metadata": {
                    "synthesis_contract": {
                        "comparison_scope": "route",
                        "route_alias": "R02",
                        "subject_aliases": ["GC03"],
                    }
                },
            },
        ]
    }


def test_global_audit_catches_route_scope_candidate_and_precision() -> None:
    report = audit_article_quality(
        article_id="pbs",
        manuscript=_manuscript(
            "GC01 is the global best overall candidate with score "
            "0.4330922726903885 and lower cost."
        ),
        ledger=_ledger(),
    )
    kinds = {item.kind for item in report.findings}
    assert report.status == "ready_with_findings"
    assert "route_scope_ambiguity" in kinds
    assert "display_precision" in kinds
    assert "metric_overinterpretation_risk" in kinds


def test_global_audit_accepts_explicit_route_scope() -> None:
    report = audit_article_quality(
        article_id="pbs",
        manuscript=_manuscript(
            "Within route R02, GC01 achieved the highest recorded score."
        ),
        ledger=_ledger(),
    )
    assert not any(item.kind == "route_scope_ambiguity" for item in report.findings)


def test_global_audit_catches_cross_route_best_claim_conflict() -> None:
    ledger = {
        "claims": [
            {
                "claim_id": "c1",
                "statement": "Candidate GC01 holds the global best target score.",
                "metadata": {
                    "synthesis_contract": {
                        "comparison_scope": "route",
                        "route_alias": "R02",
                        "subject_aliases": ["GC01"],
                    }
                },
            },
            {
                "claim_id": "c2",
                "statement": "Candidate GC05 achieved the highest target score.",
                "metadata": {
                    "synthesis_contract": {
                        "comparison_scope": "route",
                        "route_alias": "R01",
                        "subject_aliases": ["GC05"],
                    }
                },
            },
        ]
    }
    report = audit_article_quality(
        article_id="pbs",
        manuscript={
            "source_map": [
                {"paragraph_id": "p1", "rendered_text": "GC01 is best.", "claim_ids": ["c1"]},
                {"paragraph_id": "p2", "rendered_text": "GC05 is highest.", "claim_ids": ["c2"]},
            ]
        },
        ledger=ledger,
    )
    assert any(item.kind == "cross_route_best_conflict" for item in report.findings)


def test_global_audit_allows_section_context_for_candidate_synthesis() -> None:
    ledger = {
        "claims": [
            {
                "claim_id": "c1",
                "statement": "Candidate GC02 met the verified target.",
                "metadata": {
                    "synthesis_contract": {
                        "comparison_scope": "route",
                        "route_alias": "R02",
                        "subject_aliases": ["GC02"],
                    }
                },
            },
            {
                "claim_id": "c2",
                "statement": "Candidate GC03 was included in the same comparison.",
                "metadata": {
                    "synthesis_contract": {
                        "comparison_scope": "route",
                        "route_alias": "R02",
                        "subject_aliases": ["GC03"],
                    }
                },
            },
        ]
    }
    report = audit_article_quality(
        article_id="section-context",
        manuscript={
            "source_map": [
                {
                    "paragraph_id": "story-01-section-01-p1",
                    "rendered_text": "Candidate GC02 met the target.",
                    "claim_ids": ["c1", "c2"],
                },
                {
                    "paragraph_id": "story-01-section-01-p2",
                    "rendered_text": "The section compares Candidate GC02 with Candidate GC03.",
                    "claim_ids": [],
                },
            ]
        },
        ledger=ledger,
    )
    assert not any(item.kind == "candidate_binding_drift" for item in report.findings)


def test_global_audit_persistence_is_conflict_safe(tmp_path) -> None:
    report = audit_article_quality(
        article_id="clean",
        manuscript=_manuscript("The verified score was recorded."),
        ledger=_ledger(scope="global"),
    )
    path = write_global_quality_audit(tmp_path / "GLOBAL_QUALITY_AUDIT.json", report)
    assert json.loads(path.read_text(encoding="utf-8"))["audit_id"] == report.audit_id
    write_global_quality_audit(path, report)
    conflict = report.model_copy(update={"article_id": "other"})
    with pytest.raises(ValueError, match="conflicting"):
        write_global_quality_audit(path, conflict)
