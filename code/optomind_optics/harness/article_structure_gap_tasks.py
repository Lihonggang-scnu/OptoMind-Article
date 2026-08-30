"""Compile whole-Article structure gaps into bounded retrieval handoffs."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .article_full_structure import FullStructureResult


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StructureGapRetrievalTask(_StrictModel):
    schema_version: Literal["article-structure-gap-retrieval-task.v1"] = (
        "article-structure-gap-retrieval-task.v1"
    )
    task_id: str
    source_full_structure_id: str
    source_gap_id: str
    task_type: Literal["chapter_argument_gap", "structure_gap"]
    query_scope: Literal["section_local", "whole_article_structure"]
    description: str
    unique_contribution: str
    expected_value: str
    stop_reason: str
    recommended_next_action: str
    related_section_ids: List[str] = Field(default_factory=list)
    related_claim_ids: List[str] = Field(default_factory=list)
    context_inputs: List[str] = Field(default_factory=list)
    max_rounds: int = Field(ge=1, le=1, default=1)
    max_s2_items: int = Field(ge=0)
    max_oa_items: int = Field(ge=0)
    max_abstract_items: int = Field(ge=0)
    dedupe_key: str
    status: Literal["planned", "record_only"]


class StructureGapTaskCompilationResult(_StrictModel):
    schema_version: Literal["article-structure-gap-task-compilation.v1"] = (
        "article-structure-gap-task-compilation.v1"
    )
    result_id: str
    source_full_structure_id: str
    status: Literal["no_gaps", "planned", "record_only"]
    tasks: List[StructureGapRetrievalTask] = Field(default_factory=list)
    ignored_gap_ids: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    validation_errors: List[str] = Field(default_factory=list)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()[:24]


def _dedupe_key(task_type: str, description: str, section_ids: Sequence[str]) -> str:
    normalized = re.sub(r"\s+", " ", description.casefold()).strip()
    return _digest([task_type, normalized, sorted(set(section_ids))])


def compile_structure_gap_tasks(
    full_structure: FullStructureResult | Mapping[str, Any],
) -> StructureGapTaskCompilationResult:
    full = (
        full_structure
        if isinstance(full_structure, FullStructureResult)
        else FullStructureResult.model_validate(full_structure)
    )
    tasks: List[StructureGapRetrievalTask] = []
    ignored: List[str] = []
    warnings: List[str] = []
    seen: set[str] = set()
    gap_rows = [
        (
            "chapter_argument_gap",
            "section_local",
            gap.gap_id,
            gap.description,
            gap.unique_contribution,
            gap.expected_value,
            gap.stop_reason,
            gap.recommended_next_action,
            [gap.section_id] if gap.section_id else [],
            list(gap.related_claim_ids),
        )
        for gap in full.chapter_argument_gaps
    ] + [
        (
            "structure_gap",
            "whole_article_structure",
            gap.gap_id,
            gap.description,
            gap.unique_contribution,
            gap.expected_value,
            gap.stop_reason,
            gap.recommended_next_action,
            list(gap.related_section_ids),
            [],
        )
        for gap in full.structure_gaps
    ]
    for row in gap_rows:
        task_type, query_scope, gap_id, description, contribution, value, stop, action, section_ids, claim_ids = row
        key = _dedupe_key(task_type, description, section_ids)
        if key in seen:
            ignored.append(gap_id)
            warnings.append(f"duplicate structure gap {gap_id!r} ignored")
            continue
        seen.add(key)
        has_action = bool(str(action or "").strip())
        if not has_action:
            ignored.append(gap_id)
            warnings.append(f"gap {gap_id!r} retained as record_only because no retrieval action was recommended")
        if task_type == "chapter_argument_gap":
            limits = (6, 8, 12)
            context = [
                "user_question_and_charter",
                "target_section_and_related_claims",
                "existing_section_sources_and_review_findings",
            ]
        else:
            limits = (12, 16, 24)
            context = [
                "user_question_and_charter",
                "full_section_order_and_story_shape",
                "all_chapter_claim_roles_and_review_findings",
                "existing_article_and_reference_inventory",
            ]
        tasks.append(
            StructureGapRetrievalTask(
                task_id=f"structure-gap-task-{len(tasks)+1:03d}",
                source_full_structure_id=full.result_id,
                source_gap_id=gap_id,
                task_type=task_type,  # type: ignore[arg-type]
                query_scope=query_scope,  # type: ignore[arg-type]
                description=description,
                unique_contribution=contribution,
                expected_value=value,
                stop_reason=stop,
                recommended_next_action=action,
                related_section_ids=list(dict.fromkeys(section_ids)),
                related_claim_ids=list(dict.fromkeys(claim_ids)),
                context_inputs=context,
                max_s2_items=limits[0],
                max_oa_items=limits[1],
                max_abstract_items=limits[2],
                dedupe_key=key,
                status="planned" if has_action else "record_only",
            )
        )
    status: Literal["no_gaps", "planned", "record_only"] = (
        "no_gaps"
        if not gap_rows
        else "planned"
        if any(item.status == "planned" for item in tasks)
        else "record_only"
    )
    payload = {
        "source_full_structure_id": full.result_id,
        "status": status,
        "tasks": [item.model_dump(mode="json") for item in tasks],
        "ignored_gap_ids": sorted(ignored),
    }
    return StructureGapTaskCompilationResult(
        result_id="structure-gap-compilation-" + _digest(payload),
        source_full_structure_id=full.result_id,
        status=status,
        tasks=tasks,
        ignored_gap_ids=sorted(ignored),
        warnings=warnings,
        validation_errors=[],
    )


__all__ = [
    "StructureGapRetrievalTask",
    "StructureGapTaskCompilationResult",
    "compile_structure_gap_tasks",
]
