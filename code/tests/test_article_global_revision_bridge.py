from __future__ import annotations

import json
from pathlib import Path

import pytest

from optomind_optics.harness.article_global_revision_bridge import (
    build_global_revision_bridge,
)
from optomind_optics.harness.article_global_revision_plan import (
    GlobalRevisionPlanResult,
)
from optomind_optics.harness.article_review import ArticleReviewResult


ROOT = Path(__file__).resolve().parents[2]
REAL = ROOT / "stage17_real_integration"
PLAN = REAL / "article_global_revision_plan_087_pbs_story05_normalized" / "GLOBAL_REVISION_PLAN.json"
REVIEW = REAL / "article_review_replay_054_advice_router" / "04-review.json"


@pytest.fixture()
def real_assets():
    if not PLAN.is_file() or not REVIEW.is_file():
        pytest.skip("global revision bridge assets are not present")
    return (
        GlobalRevisionPlanResult.model_validate(json.loads(PLAN.read_text(encoding="utf-8"))),
        ArticleReviewResult.model_validate(json.loads(REVIEW.read_text(encoding="utf-8"))),
    )


def test_bridge_limits_execution_to_explicit_author_targets(real_assets):
    plan, review = real_assets
    result = build_global_revision_bridge(plan, review, max_actions=3)
    assert len(result.executable_action_ids) <= 3
    assert len(result.forced_review.scientific_findings) >= 1
    assert result.forced_review.expression_findings == []
    assert all(item.paragraph_id for item in result.forced_review.scientific_findings)
    assert all(item.finding_id.startswith("global-plan-") for item in result.forced_review.scientific_findings)


def test_bridge_skips_local_and_article_level_actions(real_assets):
    plan, review = real_assets
    result = build_global_revision_bridge(plan, review)
    assert result.skipped_action_ids
    assert all(
        item.owner == "author_reviser"
        and item.revision_mode == "author_scope_revision"
        and item.disposition == "planned"
        and item.target_paragraph_ids
        for item in plan.actions
        if item.action_id in result.executable_action_ids
    )
    assert not any(
        item.finding_id.endswith("global-81213a4899546ef8")
        for item in result.forced_review.scientific_findings
    )
