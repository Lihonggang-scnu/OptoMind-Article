from __future__ import annotations

import json
from pathlib import Path

import pytest

from optomind_optics.harness.article_architecture import ArticleArchitectureResult
from optomind_optics.harness.article_claims import ClaimLedgerResult
from optomind_optics.harness.article_director import ArticleDirectorPlan
from optomind_optics.harness.article_full_structure import (
    FullStructureProviderResult,
    build_full_structure,
)
from optomind_optics.harness.article_global_quality_audit import (
    audit_article_quality,
)
from optomind_optics.harness.article_manuscript import ArticleManuscriptPackage
from optomind_optics.harness.article_result_synthesis import (
    ArticleResultSynthesisResult,
)
from optomind_optics.harness.article_review import ArticleReviewResult


ROOT = Path(__file__).resolve().parents[2]
REAL = ROOT / "stage17_real_integration"
SYNTHESIS = (
    REAL
    / "article_continuation_040_structured_attainment_tolerant_full"
    / "01-result_synthesis.json"
)
ARCHITECTURE = (
    REAL / "article_upper_replay_046_section_subject_binding" / "02-architecture.json"
)
REVIEW = REAL / "article_review_replay_054_advice_router" / "04-review.json"
MANUSCRIPT = REAL / "article_review_replay_054_advice_router" / "05-manuscript.json"


class FakeCoordinator:
    def __init__(self, response: dict) -> None:
        self.response = response

    def __call__(self, requests):
        assert len(requests) == 1
        assert requests[0]["sections"]
        return [
            FullStructureProviderResult(
                response=self.response,
                provider_model="fake-full-structure",
                usage={"input_tokens": 10, "output_tokens": 5},
            )
        ]


@pytest.fixture()
def real_assets():
    required = [SYNTHESIS, ARCHITECTURE, REVIEW, MANUSCRIPT]
    if not all(path.is_file() for path in required):
        pytest.skip("real 054 Article assets are not present")
    synthesis = ArticleResultSynthesisResult.model_validate(
        json.loads(SYNTHESIS.read_text(encoding="utf-8"))
    )
    assert synthesis.derived_plan is not None and synthesis.ledger is not None
    architecture = ArticleArchitectureResult.model_validate(
        json.loads(ARCHITECTURE.read_text(encoding="utf-8"))
    )
    review = ArticleReviewResult.model_validate(
        json.loads(REVIEW.read_text(encoding="utf-8"))
    )
    manuscript = ArticleManuscriptPackage.model_validate(
        json.loads(MANUSCRIPT.read_text(encoding="utf-8"))
    )
    return synthesis.derived_plan, synthesis.ledger, architecture, review, manuscript


def test_full_structure_reorders_immutable_body_and_preserves_source_map(real_assets):
    plan, ledger, architecture, review, manuscript = real_assets
    story = architecture.stories[0]
    section_ids = [section.section_id for section in manuscript.body.sections]
    response = {
        "global_thesis": "A coordinated evidence-bound Article story.",
        "section_order": [
            {
                "source_section_id": section_id,
                "order": index,
                "whole_article_role": "result contribution",
                "reason": "test ordering",
                "transition_note": "bridge",
            }
            for index, section_id in enumerate(reversed(section_ids), start=1)
        ],
        "rhetorical_edits": [
            {
                "source_section_ids": section_ids[:2],
                "operation": "bridge",
                "instruction": "Add a transition without changing either paragraph.",
                "preserve_claim_bindings": False,
            }
        ],
        "chapter_argument_gaps": [
            {
                "section_id": section_ids[0],
                "description": "The chapter lacks a comparison boundary.",
                "unique_contribution": "Separate route-local and global roles.",
                "expected_value": "Prevents overgeneralization.",
                "stop_reason": "Stop when the comparison adds no new role.",
                "recommended_next_action": "Retrieve a bounded comparison.",
                "related_claim_ids": [story.claim_assignments[0].claim_id, "unknown"],
            }
        ],
        "structure_gaps": [
            {
                "description": "A whole-Article synthesis axis may be missing.",
                "unique_contribution": "Connect performance and limitations.",
                "expected_value": "Improves reader navigation.",
                "stop_reason": "Stop if the existing synthesis is sufficient.",
                "recommended_next_action": "No retrieval until value is confirmed.",
                "related_section_ids": [section_ids[0], "unknown-section"],
            }
        ],
    }

    result = build_full_structure(
        plan,
        ledger,
        architecture,
        review,
        manuscript,
        story.story_id,
        provider=FakeCoordinator(response),
    )

    assert result.validation_errors == []
    assert [item.source_section_id for item in result.section_order] == list(
        reversed(section_ids)
    )
    assert result.body_markdown.index(
        next(
            section.heading
            for section in manuscript.body.sections
            if section.section_id == section_ids[-1]
        )
    ) < result.body_markdown.index(
        next(
            section.heading
            for section in manuscript.body.sections
            if section.section_id == section_ids[0]
        )
    )
    assert len(result.source_map) == sum(
        len(section.paragraphs) for section in manuscript.body.sections
    )
    assert result.rhetorical_edits[0].preserve_claim_bindings is True
    assert result.chapter_argument_gaps[0].related_claim_ids == [
        story.claim_assignments[0].claim_id
    ]
    assert result.structure_gaps[0].related_section_ids == [section_ids[0]]


def test_full_structure_without_provider_keeps_original_section_order(real_assets):
    plan, ledger, architecture, review, manuscript = real_assets
    story = architecture.stories[0]
    result = build_full_structure(
        plan,
        ledger,
        architecture,
        review,
        manuscript,
        story.story_id,
        provider=None,
    )
    assert result.model_status == "unavailable"
    assert [item.source_section_id for item in result.section_order] == [
        section.section_id for section in manuscript.body.sections
    ]
    assert result.body_markdown


def test_full_structure_passes_global_audit_and_records_unhandled_findings(real_assets):
    plan, ledger, architecture, review, manuscript = real_assets
    story = architecture.stories[0]
    audit = audit_article_quality(
        article_id="pbs",
        manuscript=manuscript,
        ledger=ledger,
    )
    first = audit.findings[0]
    response = {
        "global_thesis": "A scoped Article story.",
        "section_order": [
            {
                "source_section_id": section.section_id,
                "whole_article_role": "result",
            }
            for section in manuscript.body.sections
        ],
        "rhetorical_edits": [],
        "chapter_argument_gaps": [],
        "structure_gaps": [],
        "global_quality_actions": [
            {
                "finding_id": first.finding_id,
                "handling": "planned",
                "rationale": "Preserve the route boundary in the whole-Article revision plan.",
                "scope_label": "route-local",
            },
            {"finding_id": "not-a-real-finding", "handling": "addressed"},
        ],
    }
    result = build_full_structure(
        plan,
        ledger,
        architecture,
        review,
        manuscript,
        story.story_id,
        provider=FakeCoordinator(response),
        global_quality_audit=audit,
    )
    assert result.source_global_quality_audit_id == audit.audit_id
    assert result.global_quality_audit_status == "partially_acknowledged"
    assert result.global_quality_actions[0].finding_id == first.finding_id
    assert len(result.unhandled_global_quality_finding_ids) == len(
        {item.finding_id for item in audit.findings}
    ) - 1
    assert any("not-a-real-finding" in warning for warning in result.warnings)


def test_full_structure_audit_is_visible_to_provider(real_assets):
    plan, ledger, architecture, review, manuscript = real_assets
    story = architecture.stories[0]
    audit = audit_article_quality(article_id="pbs", manuscript=manuscript, ledger=ledger)

    class InspectingCoordinator(FakeCoordinator):
        def __call__(self, requests):
            context = requests[0]["global_quality_audit"]
            assert context["audit_id"] == audit.audit_id
            assert len(context["findings"]) == len(
                {item.finding_id for item in audit.findings}
            )
            return super().__call__(requests)

    response = {
        "global_thesis": "A scoped Article story.",
        "section_order": [
            {"source_section_id": section.section_id, "whole_article_role": "result"}
            for section in manuscript.body.sections
        ],
        "rhetorical_edits": [],
        "chapter_argument_gaps": [],
        "structure_gaps": [],
        "global_quality_actions": [],
    }
    result = build_full_structure(
        plan,
        ledger,
        architecture,
        review,
        manuscript,
        story.story_id,
        provider=InspectingCoordinator(response),
        global_quality_audit=audit,
    )
    assert result.global_quality_audit_status == "unacknowledged"


def test_full_structure_rejects_mixed_lineage(real_assets):
    plan, ledger, architecture, review, manuscript = real_assets
    story = architecture.stories[0]
    mismatched_review = review.model_copy(update={"plan_id": "mixed-plan"})
    with pytest.raises(ValueError, match="lineage mismatch"):
        build_full_structure(
            plan,
            ledger,
            architecture,
            mismatched_review,
            manuscript,
            story.story_id,
            provider=None,
        )
