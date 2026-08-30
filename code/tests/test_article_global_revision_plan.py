from __future__ import annotations

import json
from pathlib import Path

import pytest

from optomind_optics.harness.article_full_structure import FullStructureResult
from optomind_optics.harness.article_global_quality_audit import (
    GlobalQualityAuditReport,
)
from optomind_optics.harness.article_global_revision_plan import (
    GlobalRevisionPlanProviderResult,
    build_global_revision_plan,
    build_global_revision_plan_payload,
)
from optomind_optics.harness.article_review import ArticleReviewResult


ROOT = Path(__file__).resolve().parents[2]
REAL = ROOT / "stage17_real_integration"
FULL = REAL / "article_full_structure_083_global_audit_pbs_story05_final" / "FULL_ARTICLE_STRUCTURE.json"
AUDIT = REAL / "article_global_quality_audit_081_pbs_story05" / "GLOBAL_QUALITY_AUDIT.json"
REVIEW = REAL / "article_review_replay_054_advice_router" / "04-review.json"


@pytest.fixture()
def real_assets():
    required = [FULL, AUDIT, REVIEW]
    if not all(path.is_file() for path in required):
        pytest.skip("real global revision assets are not present")
    return (
        FullStructureResult.model_validate(json.loads(FULL.read_text(encoding="utf-8"))),
        GlobalQualityAuditReport.model_validate(json.loads(AUDIT.read_text(encoding="utf-8"))),
        ArticleReviewResult.model_validate(json.loads(REVIEW.read_text(encoding="utf-8"))),
    )


class FakePlanner:
    def __init__(self, response: dict) -> None:
        self.response = response

    def __call__(self, requests):
        assert requests
        assert all(request["audit_findings"] for request in requests[:-1])
        return [
            GlobalRevisionPlanProviderResult(
                response=(self.response if index == 0 else {"actions": []}),
                provider_model="fake-global-revision-planner",
                usage={"input_tokens": 10, "output_tokens": 5},
            )
            for index, _ in enumerate(requests)
        ]


def test_payload_contains_only_current_scope_and_existing_review_findings(real_assets):
    full, audit, review = real_assets
    payload = build_global_revision_plan_payload(full, audit, review)[0]
    paragraph_ids = {item["paragraph_id"] for item in payload["paragraphs"]}
    assert payload["full_structure_id"] == full.result_id
    assert payload["audit_id"] == audit.audit_id
    assert payload["audit_findings"]
    assert all(
        not item["paragraph_id"] or item["paragraph_id"] in paragraph_ids
        for item in payload["audit_findings"]
    )
    assert payload["existing_review_findings"]
    assert payload["constraints"]["do_not_change_claim_fact_value_source_or_citation_bindings"]


def test_plan_restores_target_and_uses_authoritative_claim_ids(real_assets):
    full, audit, review = real_assets
    finding = next(item for item in audit.findings if item.source_claim_ids)
    response = {
        "actions": [
            {
                "finding_id": finding.finding_id,
                "revision_mode": "author_scope_revision",
                "owner": "author_reviser",
                "target_paragraph_ids": [],
                "instruction": "Narrow the statement to the measured route and metric.",
                "rationale": "Preserve the supplied scope.",
                "disposition": "planned",
            },
            {"finding_id": "unknown", "revision_mode": "author_scope_revision"},
        ]
    }
    result = build_global_revision_plan(
        full,
        audit,
        review,
        provider=FakePlanner(response),
    )
    action = result.actions[0]
    assert action.finding_id == finding.finding_id
    assert action.source_claim_ids == finding.source_claim_ids
    if finding.paragraph_id:
        assert action.target_paragraph_ids == [finding.paragraph_id]
    assert action.preserve_claim_bindings is True
    if finding.kind == "candidate_binding_drift":
        assert "do not add a new Claim alias" in action.instruction
    assert any("unknown finding" in warning for warning in result.warnings)
    assert len(result.unhandled_finding_ids) == len(
        {item.finding_id for item in audit.findings}
    ) - 1


def test_plan_fail_open_records_missing_actions(real_assets):
    full, audit, review = real_assets
    result = build_global_revision_plan(
        full,
        audit,
        review,
        provider=FakePlanner({"actions": []}),
    )
    assert result.model_status == "partial"
    assert len(result.actions) == len({item.finding_id for item in audit.findings})
    assert all(item.disposition == "unacknowledged" for item in result.actions)
    assert result.unhandled_finding_ids
    assert result.validation_errors == []
