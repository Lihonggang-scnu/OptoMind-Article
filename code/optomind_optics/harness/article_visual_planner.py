"""Stage 13B: source-bound visual asset planning.

This layer chooses existing FigureContracts or records a bounded conceptual
visual gap. It does not create images, alter prose, or authorize a source.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from optomind_optics.harness.article_architecture import ArticleArchitectureResult
from optomind_optics.harness.article_full_structure import FullStructureResult
from optomind_optics.harness.article_manuscript import ArticleManuscriptPackage
from optomind_optics.harness.article_review import ArticleReviewResult
from optomind_optics.harness.qwen_policy import QwenFlashOnlyClient


VISUAL_PLAN_SCHEMA_VERSION = "article-visual-plan-result.v1"
MODEL_NAME = "qwen3.7-flash"
DEFAULT_MAX_TOKENS = 8000
DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "optical_harness"
    / "Article Visual Asset Planner.txt"
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _ProviderModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class VisualPlacement(_StrictModel):
    schema_version: Literal["visual-placement.v1"] = "visual-placement.v1"
    placement_id: str
    section_id: str
    after_paragraph_id: str = ""
    source_kind: Literal["existing_figure_contract", "generated_conceptual"]
    figure_id: str = ""
    visual_role: Literal[
        "data", "comparison", "mechanism", "workflow", "limitation", "overview"
    ]
    rationale: str
    caption: str
    claim_ids: List[str] = Field(default_factory=list)
    artifact_ids: List[str] = Field(default_factory=list)
    permission_state: str = "inherited_source_contract"


class VisualGap(_StrictModel):
    schema_version: Literal["visual-gap.v1"] = "visual-gap.v1"
    gap_id: str
    section_id: str
    visual_role: Literal["mechanism", "workflow", "overview", "data"]
    description: str
    unique_contribution: str
    expected_value: str
    stop_reason: str
    generation_prompt: str = ""
    retrieval_query: str = ""


class VisualAssetRecord(_StrictModel):
    schema_version: Literal["visual-asset-record.v1"] = "visual-asset-record.v1"
    asset_id: str
    source_kind: Literal[
        "oa_figure", "s2_figure", "generated_conceptual", "local_artifact"
    ]
    article_ids: List[str] = Field(default_factory=list)
    figure_id: str = ""
    caption: str = ""
    claim_ids: List[str] = Field(default_factory=list)
    permission_state: str
    visual_embedding_ref: str = ""
    sha256: str = ""
    asset_path: str = ""
    asset_format: Literal["image", "svg", "pdf", "table"] = "image"
    status: Literal["planned", "available", "rejected", "superseded"] = "planned"
    approval_state: Literal["unreviewed", "approved", "rejected"] = "unreviewed"


class VisualPlanResult(_StrictModel):
    schema_version: Literal["article-visual-plan-result.v1"] = (
        VISUAL_PLAN_SCHEMA_VERSION
    )
    result_id: str
    source_full_structure_id: str
    source_architecture_id: str
    source_review_id: str
    source_manuscript_package_id: str
    story_id: str
    placements: List[VisualPlacement] = Field(default_factory=list)
    gaps: List[VisualGap] = Field(default_factory=list)
    cache_records: List[VisualAssetRecord] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    validation_errors: List[str] = Field(default_factory=list)
    model_status: Literal["available", "partial", "unavailable"]
    usage: Dict[str, Any] = Field(default_factory=dict)
    semantic_model: str = "none"


class VisualPlannerProviderResult(_StrictModel):
    schema_version: Literal["visual-planner-provider-result.v1"] = (
        "visual-planner-provider-result.v1"
    )
    response: Dict[str, Any]
    usage: Dict[str, Any] = Field(default_factory=dict)
    provider_model: str = "unknown"
    mock_llm: bool = False


class _PlacementDraft(_ProviderModel):
    section_id: str
    after_paragraph_id: str = ""
    source_kind: str = "existing_figure_contract"
    figure_id: str = ""
    visual_role: str = "data"
    rationale: str = ""
    caption: str = ""


class _GapDraft(_ProviderModel):
    section_id: str
    visual_role: str = "mechanism"
    description: str = ""
    unique_contribution: str = ""
    expected_value: str = ""
    stop_reason: str = ""
    generation_prompt: str = ""
    retrieval_query: str = ""


def _digest(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def build_visual_plan_payload(
    full_structure: FullStructureResult,
    architecture: ArticleArchitectureResult,
    review: ArticleReviewResult,
    manuscript: ArticleManuscriptPackage,
) -> List[Dict[str, Any]]:
    story = next(
        item
        for item in architecture.stories
        if item.story_id == full_structure.story_id
    )
    figure_contracts = [
        {
            "figure_id": figure.figure_id,
            "role_key": figure.role_key,
            "kind": figure.kind,
            "section_target": figure.section_target,
            "claim_ids": list(figure.claim_ids),
            "fact_ids": list(figure.fact_ids),
            "artifact_ids": [
                binding.artifact_id for binding in figure.artifact_bindings
            ],
            "caption_intent": figure.caption_intent,
            "source_mode": figure.source_mode,
        }
        for figure in story.figure_contracts
    ]
    sections = [
        {
            "section_id": section.section_id,
            "heading": section.heading,
            "paragraphs": [
                {
                    "paragraph_id": paragraph.paragraph_id,
                    "text": paragraph.rendered_text,
                    "claim_ids": list(paragraph.claim_ids),
                    "figure_ids": list(paragraph.figure_ids),
                }
                for paragraph in section.paragraphs
            ],
        }
        for section in manuscript.body.sections
    ]
    return [
        {
            "task": "Plan source-bound visual assets for a complete Article.",
            "story_id": full_structure.story_id,
            "global_thesis": full_structure.global_thesis,
            "section_order": [
                item.model_dump(mode="json") for item in full_structure.section_order
            ],
            "sections": sections,
            "figure_contracts": figure_contracts,
            "review_findings": [
                {
                    "paragraph_id": finding.paragraph_id,
                    "severity": finding.severity.value,
                    "reason": finding.reason,
                }
                for finding in review.scientific_findings + review.expression_findings
            ],
            "constraints": {
                "max_placements_per_section": 2,
                "placement_can_be_zero": True,
                "prefer_existing_local_assets": True,
                "no_image_generation_in_this_stage": True,
                "do_not_change_prose_or_claim_bindings": True,
            },
        }
    ]


class QwenArticleVisualPlanner:
    def __init__(
        self,
        *,
        prompt_path: str | Path = DEFAULT_PROMPT_PATH,
        client: QwenFlashOnlyClient | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self.prompt_path = Path(prompt_path)
        self.client = client or QwenFlashOnlyClient(
            agent_name="ArticleVisualAssetPlanner"
        )
        self.max_tokens = int(max_tokens)

    def __call__(
        self, requests: Sequence[Mapping[str, Any]]
    ) -> List[VisualPlannerProviderResult]:
        results = []
        for request in requests:
            response = self.client.call(
                [
                    {
                        "role": "system",
                        "content": self.prompt_path.read_text(encoding="utf-8"),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(request, ensure_ascii=False),
                    },
                ],
                max_tokens=self.max_tokens,
                force_mock=False,
            )
            text = str(response.get("content") or "").strip()
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                start, end = text.find("{"), text.rfind("}")
                parsed = (
                    json.loads(text[start : end + 1])
                    if start >= 0 and end > start
                    else {}
                )
            results.append(
                VisualPlannerProviderResult(
                    response=parsed if isinstance(parsed, dict) else {},
                    usage=dict(response.get("_llm_usage") or {}),
                    provider_model=MODEL_NAME,
                    mock_llm=bool((response.get("_llm_usage") or {}).get("mock_llm")),
                )
            )
        return results


def build_visual_plan(
    full_structure: FullStructureResult | Mapping[str, Any],
    architecture: ArticleArchitectureResult | Mapping[str, Any],
    review: ArticleReviewResult | Mapping[str, Any],
    manuscript: ArticleManuscriptPackage | Mapping[str, Any],
    *,
    provider: Optional[QwenArticleVisualPlanner] = None,
) -> VisualPlanResult:
    full = (
        full_structure
        if isinstance(full_structure, FullStructureResult)
        else FullStructureResult.model_validate(full_structure)
    )
    arch = (
        architecture
        if isinstance(architecture, ArticleArchitectureResult)
        else ArticleArchitectureResult.model_validate(architecture)
    )
    rev = (
        review
        if isinstance(review, ArticleReviewResult)
        else ArticleReviewResult.model_validate(review)
    )
    manuscript_model = (
        manuscript
        if isinstance(manuscript, ArticleManuscriptPackage)
        else ArticleManuscriptPackage.model_validate(manuscript)
    )
    lineage_pairs = [
        ("full_structure.architecture_id", full.source_architecture_id, arch.architecture_id),
        ("full_structure.review_id", full.source_review_id, rev.review_id),
        ("full_structure.manuscript_package_id", full.source_manuscript_package_id, manuscript_model.package_id),
        ("full_structure.plan_id", full.source_plan_id, rev.plan_id),
        ("architecture.source_plan_id", arch.source_plan_id, rev.plan_id),
        ("review.plan_id", rev.plan_id, arch.source_plan_id),
        ("review.ledger_id", rev.ledger_id, arch.source_ledger_id),
        ("review.architecture_id", rev.architecture_id, arch.architecture_id),
        ("review.story_id", rev.story_id, full.story_id),
        ("manuscript.plan_id", manuscript_model.plan_id, rev.plan_id),
        ("manuscript.ledger_id", manuscript_model.ledger_id, rev.ledger_id),
        ("manuscript.architecture_id", manuscript_model.architecture_id, arch.architecture_id),
        ("manuscript.review_id", manuscript_model.review_id, rev.review_id),
        ("manuscript.story_id", manuscript_model.story_id, full.story_id),
    ]
    lineage_errors = [
        f"{field}: {actual!r} != {expected!r}"
        for field, actual, expected in lineage_pairs
        if actual != expected
    ]
    if lineage_errors:
        raise ValueError(
            "visual plan lineage mismatch; refusing mixed visual inputs: "
            + "; ".join(lineage_errors)
        )
    story = next(item for item in arch.stories if item.story_id == full.story_id)
    known_sections = {section.section_id for section in manuscript_model.body.sections}
    known_paragraphs = {
        paragraph.paragraph_id
        for section in manuscript_model.body.sections
        for paragraph in section.paragraphs
    }
    paragraph_section = {
        paragraph.paragraph_id: section.section_id
        for section in manuscript_model.body.sections
        for paragraph in section.paragraphs
    }
    figures = {figure.figure_id: figure for figure in story.figure_contracts}
    payload = build_visual_plan_payload(full, arch, rev, manuscript_model)
    raw: Dict[str, Any] = {}
    warnings: List[str] = []
    errors: List[str] = []
    usage: Dict[str, Any] = {}
    status: Literal["available", "partial", "unavailable"] = "unavailable"
    if provider is not None:
        try:
            results = list(provider(payload))
            if len(results) == 1 and isinstance(
                results[0], VisualPlannerProviderResult
            ):
                raw = results[0].response
                usage = dict(results[0].usage or {})
                status = "available" if raw else "partial"
            else:
                warnings.append("visual planner returned an invalid envelope")
        except Exception as exc:
            warnings.append(f"visual planner unavailable: {exc}")
    placements: List[VisualPlacement] = []
    counts: Dict[str, int] = {}
    for index, item in enumerate(raw.get("placements") or (), start=1):
        try:
            draft = _PlacementDraft.model_validate(item)
        except ValidationError as exc:
            warnings.append(f"malformed visual placement {index}: {exc}")
            continue
        if draft.section_id not in known_sections or draft.figure_id not in figures:
            warnings.append(
                f"visual placement {index} references unknown section/figure"
            )
            continue
        if (
            draft.after_paragraph_id
            and draft.after_paragraph_id not in known_paragraphs
        ):
            warnings.append(
                f"visual placement {index} references unknown paragraph; reset to section end"
            )
            after_paragraph_id = ""
        elif (
            draft.after_paragraph_id
            and paragraph_section.get(draft.after_paragraph_id) != draft.section_id
        ):
            warnings.append(
                f"visual placement {index} paragraph belongs to another section; reset to section end"
            )
            after_paragraph_id = ""
        else:
            after_paragraph_id = draft.after_paragraph_id
        if counts.get(draft.section_id, 0) >= 2:
            warnings.append(
                f"section {draft.section_id!r} exceeded two visual placements; extra ignored"
            )
            continue
        figure = figures[draft.figure_id]
        placements.append(
            VisualPlacement(
                placement_id=f"visual-placement-{len(placements)+1:02d}",
                section_id=draft.section_id,
                after_paragraph_id=after_paragraph_id,
                source_kind="existing_figure_contract",
                figure_id=figure.figure_id,
                visual_role=(
                    draft.visual_role
                    if draft.visual_role
                    in {
                        "data",
                        "comparison",
                        "mechanism",
                        "workflow",
                        "limitation",
                        "overview",
                    }
                    else "data"
                ),
                rationale=draft.rationale,
                caption=draft.caption or figure.caption_intent,
                claim_ids=list(figure.claim_ids),
                artifact_ids=[
                    binding.artifact_id for binding in figure.artifact_bindings
                ],
            )
        )
        counts[draft.section_id] = counts.get(draft.section_id, 0) + 1
    gaps: List[VisualGap] = []
    for index, item in enumerate(raw.get("gaps") or (), start=1):
        try:
            draft = _GapDraft.model_validate(item)
        except ValidationError as exc:
            warnings.append(f"malformed visual gap {index}: {exc}")
            continue
        if draft.section_id not in known_sections:
            warnings.append(f"visual gap {index} references unknown section; ignored")
            continue
        role = (
            draft.visual_role
            if draft.visual_role in {"mechanism", "workflow", "overview", "data"}
            else "mechanism"
        )
        gaps.append(
            VisualGap(
                gap_id=f"visual-gap-{index:02d}",
                section_id=draft.section_id,
                visual_role=role,
                description=draft.description,
                unique_contribution=draft.unique_contribution,
                expected_value=draft.expected_value,
                stop_reason=draft.stop_reason,
                generation_prompt=draft.generation_prompt,
                retrieval_query=draft.retrieval_query,
            )
        )
    ordered_section_ids = [item.source_section_id for item in full.section_order]
    gap_roles = {gap.visual_role for gap in gaps}
    placement_roles = {placement.visual_role for placement in placements}
    for role, description, contribution, prompt in (
        (
            "mechanism",
            "No source-bound mechanism visual is available for the Article.",
            "Provide a clearly labeled conceptual explanation of the optical design mechanism without adding measured data.",
            "Create a non-measured conceptual mechanism diagram for the Article's stated optical design workflow; label it as AI-generated and do not add numerical results.",
        ),
        (
            "workflow",
            "No source-bound workflow visual is available for the Article.",
            "Show the progression from problem definition through bounded simulation, evidence review, and Article synthesis.",
            "Create a non-measured conceptual workflow diagram of the Article research process; label it as AI-generated and do not add numerical results.",
        ),
    ):
        if role in gap_roles or role in placement_roles:
            continue
        target_section = next(
            (
                item.source_section_id
                for item in full.section_order
                if role == "workflow"
                and any(
                    token in item.whole_article_role.lower()
                    for token in ("method", "framing", "overview")
                )
            ),
            ordered_section_ids[0] if ordered_section_ids else "",
        )
        if target_section:
            gaps.append(
                VisualGap(
                    gap_id=f"visual-gap-auto-{role}",
                    section_id=target_section,
                    visual_role=role,
                    description=description,
                    unique_contribution=contribution,
                    expected_value="Add a reader-facing visual role not covered by existing data figures.",
                    stop_reason="Stop if a trusted source visual or a later human review finds no distinct contribution.",
                    generation_prompt=prompt,
                    retrieval_query="",
                )
            )
            warnings.append(f"visual planner materialized missing {role} gap")
    cache_records = [
        VisualAssetRecord(
            asset_id=f"planned-{placement.placement_id}",
            source_kind="local_artifact",
            article_ids=[full.result_id],
            figure_id=placement.figure_id,
            caption=placement.caption,
            claim_ids=list(placement.claim_ids),
            permission_state=placement.permission_state,
            status="planned",
        )
        for placement in placements
    ]
    result_payload = {
        "source_full_structure_id": full.result_id,
        "source_architecture_id": arch.architecture_id,
        "source_review_id": rev.review_id,
        "source_manuscript_package_id": manuscript_model.package_id,
        "story_id": full.story_id,
        "placements": [item.model_dump(mode="json") for item in placements],
        "gaps": [item.model_dump(mode="json") for item in gaps],
        "cache_records": [item.model_dump(mode="json") for item in cache_records],
    }
    return VisualPlanResult(
        result_id=_digest(result_payload),
        source_full_structure_id=full.result_id,
        source_architecture_id=arch.architecture_id,
        source_review_id=rev.review_id,
        source_manuscript_package_id=manuscript_model.package_id,
        story_id=full.story_id,
        placements=placements,
        gaps=gaps,
        cache_records=cache_records,
        warnings=warnings,
        validation_errors=errors,
        model_status=status,
        usage=usage,
        semantic_model=MODEL_NAME if provider is not None else "none",
    )


__all__ = [
    "QwenArticleVisualPlanner",
    "VisualAssetRecord",
    "VisualGap",
    "VisualPlacement",
    "VisualPlanResult",
    "VisualPlannerProviderResult",
    "build_visual_plan",
    "build_visual_plan_payload",
]
