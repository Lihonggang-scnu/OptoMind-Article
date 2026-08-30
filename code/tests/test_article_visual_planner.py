from __future__ import annotations

import json
from pathlib import Path

import pytest

from optomind_optics.harness.article_architecture import ArticleArchitectureResult
from optomind_optics.harness.article_full_structure import FullStructureResult
from optomind_optics.harness.article_manuscript import ArticleManuscriptPackage
from optomind_optics.harness.article_result_synthesis import (
    ArticleResultSynthesisResult,
)
from optomind_optics.harness.article_review import ArticleReviewResult
from optomind_optics.harness.article_visual_planner import (
    VisualPlannerProviderResult,
    build_visual_plan,
)


ROOT = Path(__file__).resolve().parents[2]
REAL = ROOT / "stage17_real_integration"
FULL = REAL / "article_full_structure_055_qwen" / "FULL_ARTICLE_STRUCTURE.json"
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


class FakeVisualPlanner:
    def __init__(self, response: dict) -> None:
        self.response = response

    def __call__(self, requests):
        assert requests[0]["constraints"]["max_placements_per_section"] == 2
        return [
            VisualPlannerProviderResult(
                response=self.response,
                provider_model="fake-visual-planner",
                usage={"input_tokens": 9, "output_tokens": 4},
            )
        ]


@pytest.fixture()
def assets():
    paths = [FULL, SYNTHESIS, ARCHITECTURE, REVIEW, MANUSCRIPT]
    if not all(path.is_file() for path in paths):
        pytest.skip("055/054 Article assets are not present")
    synthesis = ArticleResultSynthesisResult.model_validate(
        json.loads(SYNTHESIS.read_text(encoding="utf-8"))
    )
    assert synthesis.derived_plan is not None and synthesis.ledger is not None
    return (
        FullStructureResult.model_validate(
            json.loads(FULL.read_text(encoding="utf-8"))
        ),
        ArticleArchitectureResult.model_validate(
            json.loads(ARCHITECTURE.read_text(encoding="utf-8"))
        ),
        ArticleReviewResult.model_validate(
            json.loads(REVIEW.read_text(encoding="utf-8"))
        ),
        ArticleManuscriptPackage.model_validate(
            json.loads(MANUSCRIPT.read_text(encoding="utf-8"))
        ),
    )


def test_visual_plan_selects_existing_contract_and_keeps_zero_or_gap_semantics(assets):
    full, architecture, review, manuscript = assets
    story = architecture.stories[0]
    figure = story.figure_contracts[0]
    section = manuscript.body.sections[0]
    response = {
        "placements": [
            {
                "section_id": figure.section_target,
                "after_paragraph_id": section.paragraphs[0].paragraph_id,
                "source_kind": "existing_figure_contract",
                "figure_id": figure.figure_id,
                "visual_role": "comparison",
                "rationale": "The trusted table compares the primary result.",
                "caption": "Source-bound comparison under the declared conditions.",
            }
        ],
        "gaps": [
            {
                "section_id": section.section_id,
                "visual_role": "mechanism",
                "description": "No source-bound mechanism diagram is available.",
                "unique_contribution": "Explain the conceptual workflow without adding data.",
                "expected_value": "Improves comprehension.",
                "stop_reason": "Stop if the existing table is sufficient.",
                "generation_prompt": "A non-measured conceptual workflow diagram.",
                "retrieval_query": "",
            }
        ],
    }
    result = build_visual_plan(
        full,
        architecture,
        review,
        manuscript,
        provider=FakeVisualPlanner(response),
    )
    assert result.validation_errors == []
    assert len(result.placements) == 1
    assert result.placements[0].figure_id == figure.figure_id
    assert result.placements[0].claim_ids == figure.claim_ids
    assert len(result.gaps) >= 2
    assert {gap.visual_role for gap in result.gaps} >= {"mechanism", "workflow"}
    assert result.cache_records[0].status == "planned"


def test_visual_plan_rejects_mixed_lineage(assets):
    full, architecture, review, manuscript = assets
    mixed = full.model_copy(update={"source_manuscript_package_id": "mixed-manuscript"})
    with pytest.raises(ValueError, match="visual plan lineage mismatch"):
        build_visual_plan(mixed, architecture, review, manuscript, provider=None)


def test_visual_plan_skips_unknown_assets_and_caps_two_per_section(assets):
    full, architecture, review, manuscript = assets
    section = manuscript.body.sections[0]
    figures = architecture.stories[0].figure_contracts
    response = {
        "placements": [
            {
                "section_id": section.section_id,
                "figure_id": figures[0].figure_id,
                "visual_role": "data",
                "rationale": "one",
                "caption": "one",
            },
            {
                "section_id": section.section_id,
                "figure_id": figures[0].figure_id,
                "visual_role": "data",
                "rationale": "two",
                "caption": "two",
            },
            {
                "section_id": section.section_id,
                "figure_id": figures[0].figure_id,
                "visual_role": "data",
                "rationale": "three",
                "caption": "three",
            },
            {
                "section_id": "unknown-section",
                "figure_id": "unknown-figure",
                "visual_role": "data",
                "rationale": "bad",
                "caption": "bad",
            },
        ]
    }
    result = build_visual_plan(
        full,
        architecture,
        review,
        manuscript,
        provider=FakeVisualPlanner(response),
    )
    assert len(result.placements) == 2
    assert len(result.warnings) >= 2
