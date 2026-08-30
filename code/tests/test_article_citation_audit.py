from __future__ import annotations

import json
from pathlib import Path

import pytest

from optomind_optics.harness.article_citation_audit import (
    CitationAuditDecision,
    CitationAuditError,
    CitationAuditResult,
    QwenCitationAuditor,
    apply_citation_audit,
    build_citation_audit_request,
    load_citation_audit,
    validate_citation_audit,
    write_citation_audit,
)
from optomind_optics.harness.article_presentation import build_article_presentation

from test_article_presentation import _biblio, _chain, _citation_response, _provider


def _package(tmp_path: Path):
    ctx = _chain(tmp_path)
    return ctx, build_article_presentation(
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
        bibliographic_metadata=_biblio(),
        citation_provider=_provider(_citation_response),
        front_matter_provider=_provider(
            {
                "title": "Safe title",
                "abstract_sentences": [
                    {
                        "sentence": "The verified evidence supports the design.",
                        "paragraph_aliases": [ctx["manuscript"].source_map[0].paragraph_id],
                    }
                ],
                "keywords": ["optics"],
            }
        ),
    )


def test_request_contains_sentence_table_and_evidence_excerpt(tmp_path: Path) -> None:
    ctx, package = _package(tmp_path)
    request = build_citation_audit_request(
        package,
        ctx["manuscript"],
        {item.evidence_id: item for item in ctx["evidence"]},
    )
    assert request["placements"]
    row = request["placements"][0]
    assert row["sentences"][0]["sentence_position"] == 0
    assert row["evidence_excerpts"][0]["excerpt"]
    assert len(row["evidence_excerpts"][0]["excerpt"]) <= 1200


def test_validate_rejects_unknown_duplicate_and_invalid_position(tmp_path: Path) -> None:
    _, package = _package(tmp_path)
    placement = package.placements[0]
    base = {
        "audit_id": "audit-1",
        "source_presentation_id": package.package_id,
    }
    with pytest.raises(CitationAuditError, match="unknown pair"):
        validate_citation_audit(
            {
                **base,
                "decisions": [
                    {
                        "paragraph_id": "unknown",
                        "reference_alias": placement.reference_alias,
                        "action": "keep",
                        "sentence_position": 0,
                        "reason": "bad",
                    }
                ],
            },
            package,
        )
    duplicate = {
        **base,
        "decisions": [
            {
                "paragraph_id": placement.paragraph_id,
                "reference_alias": placement.reference_alias,
                "action": "keep",
                "sentence_position": 0,
                "reason": "one",
            },
            {
                "paragraph_id": placement.paragraph_id,
                "reference_alias": placement.reference_alias,
                "action": "keep",
                "sentence_position": 0,
                "reason": "two",
            },
        ],
    }
    with pytest.raises(CitationAuditError, match="repeats pair"):
        validate_citation_audit(duplicate, package)


def test_apply_drop_is_idempotent_and_rebuilds_references(tmp_path: Path) -> None:
    ctx, package = _package(tmp_path)
    placement = package.placements[0]
    audit = CitationAuditResult(
        audit_id="audit-drop",
        source_presentation_id=package.package_id,
        decisions=[
            CitationAuditDecision(
                paragraph_id=placement.paragraph_id,
                reference_alias=placement.reference_alias,
                action="drop",
                sentence_position=None,
                reason="the supplied excerpt does not support this sentence",
            )
        ],
    )
    applied = apply_citation_audit(package, ctx["manuscript"], audit)
    assert applied.citations == []
    assert applied.references == []
    assert applied.placements == []
    second = apply_citation_audit(applied, ctx["manuscript"], audit)
    assert second.citations == []
    assert second.references == []
    assert second.reader_markdown == applied.reader_markdown


def test_persistence_is_conflict_safe_and_resumable(tmp_path: Path) -> None:
    _, package = _package(tmp_path)
    placement = package.placements[0]
    audit = CitationAuditResult(
        audit_id="audit-save",
        source_presentation_id=package.package_id,
        decisions=[
            {
                "paragraph_id": placement.paragraph_id,
                "reference_alias": placement.reference_alias,
                "action": "move",
                "sentence_position": 0,
                "reason": "first sentence is the supported method statement",
            }
        ],
    )
    path = write_citation_audit(tmp_path / "ARTICLE_CITATION_AUDIT.json", audit)
    loaded = load_citation_audit(path)
    assert loaded.audit_id == audit.audit_id
    write_citation_audit(path, audit)
    conflict = audit.model_copy(update={"audit_id": "audit-other"})
    with pytest.raises(CitationAuditError, match="conflicting"):
        write_citation_audit(path, conflict)


def test_qwen_provider_parses_bounded_json_contract(tmp_path: Path) -> None:
    ctx, package = _package(tmp_path)
    placement = package.placements[0]

    class FakeClient:
        def call(self, messages, **kwargs):
            assert kwargs["max_tokens"] == 5000
            assert "evidence_excerpts" in messages[1]["content"]
            return {
                "content": json.dumps(
                    {
                        "decisions": [
                            {
                                "paragraph_id": placement.paragraph_id,
                                "reference_alias": placement.reference_alias,
                                "action": "keep",
                                "sentence_position": 0,
                                "reason": "the excerpt supports the first sentence",
                            }
                        ]
                    }
                ),
                "_llm_usage": {"input_tokens": 10, "output_tokens": 10},
            }

    result = QwenCitationAuditor(client=FakeClient())(
        package,
        ctx["manuscript"],
        {item.evidence_id: item for item in ctx["evidence"]},
    )
    assert result.status == "ready"
    assert result.decisions[0].action == "keep"


def test_qwen_provider_normalizes_unused_drop_position(tmp_path: Path) -> None:
    ctx, package = _package(tmp_path)
    placement = package.placements[0]

    class FakeClient:
        def call(self, messages, **kwargs):
            del messages, kwargs
            return {
                "content": json.dumps(
                    {
                        "decisions": [
                            {
                                "paragraph_id": placement.paragraph_id,
                                "reference_alias": placement.reference_alias,
                                "action": "drop",
                                "sentence_position": 3,
                                "reason": "unsupported local result",
                            }
                        ]
                    }
                ),
                "_llm_usage": {},
            }

    result = QwenCitationAuditor(client=FakeClient())(
        package,
        ctx["manuscript"],
        {item.evidence_id: item for item in ctx["evidence"]},
    )
    assert result.decisions[0].action == "drop"
    assert result.decisions[0].sentence_position is None
    assert result.warnings
