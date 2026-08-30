"""Plan bounded, downstream-compatible queries for Article structure gaps."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .article_full_structure import FullStructureResult
from .article_structure_gap_tasks import (
    StructureGapRetrievalTask,
    StructureGapTaskCompilationResult,
)
from .qwen_policy import QwenFlashOnlyClient


MODEL_NAME = "qwen3.7-flash"
DEFAULT_MAX_TOKENS = 5000
DEFAULT_BATCH_SIZE = 8
DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "optical_harness"
    / "Article Structure Gap Query Planner.txt"
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _ProviderModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class GapSearchQuery(_StrictModel):
    schema_version: Literal["article-gap-search-query.v1"] = "article-gap-search-query.v1"
    query_id: str
    source_task_id: str
    protocol: Literal["s2_snippet", "s2_paper", "openalex_work", "abstract"]
    query_text: str
    direction_label: str
    max_items: int = Field(ge=1)
    dedupe_key: str


class GapQueryPlanningResult(_StrictModel):
    schema_version: Literal["article-gap-query-planning-result.v1"] = (
        "article-gap-query-planning-result.v1"
    )
    result_id: str
    source_full_structure_id: str
    source_compilation_id: str
    status: Literal["no_tasks", "planned", "partial", "unavailable"]
    queries: List[GapSearchQuery] = Field(default_factory=list)
    unhandled_task_ids: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    validation_errors: List[str] = Field(default_factory=list)
    usage: Dict[str, Any] = Field(default_factory=dict)
    model_status: Literal["available", "partial", "unavailable"]


class GapQueryProviderResult(_StrictModel):
    schema_version: Literal["article-gap-query-provider-result.v1"] = (
        "article-gap-query-provider-result.v1"
    )
    response: Dict[str, Any]
    usage: Dict[str, Any] = Field(default_factory=dict)
    provider_model: str = "unknown"
    mock_llm: bool = False


class _QueryDraft(_ProviderModel):
    source_task_id: str = ""
    protocol: str = "s2_snippet"
    query_text: str = ""
    direction_label: str = ""


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()[:24]


def _safe_json(text: str) -> Dict[str, Any]:
    text = str(text or "").strip()
    try:
        result = json.loads(text)
        return result if isinstance(result, dict) else {}
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                result = json.loads(text[start : end + 1])
                return result if isinstance(result, dict) else {}
            except json.JSONDecodeError:
                return {}
    return {}


def _normalize_query(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").casefold()).strip()


def build_gap_query_payloads(
    full_structure: FullStructureResult,
    compilation: StructureGapTaskCompilationResult,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> List[Dict[str, Any]]:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    sections = [
        {
            "section_id": str(section.get("section_id") or ""),
            "heading": str(section.get("section_id") or ""),
            "purpose": next(
                (
                    item.whole_article_role
                    for item in full_structure.section_order
                    if item.source_section_id == str(section.get("section_id") or "")
                ),
                "",
            ),
        }
        for section in full_structure.source_map
        if isinstance(section, Mapping)
    ]
    tasks = compilation.tasks
    batches = [tasks[index : index + batch_size] for index in range(0, len(tasks), batch_size)]
    return [
        {
            "task": "Generate precise gap queries for downstream Article retrieval adapters.",
            "batch_index": index + 1,
            "batch_count": len(batches),
            "full_structure_id": full_structure.result_id,
            "story_id": full_structure.story_id,
            "section_context": sections,
            "gap_tasks": [item.model_dump(mode="json") for item in batch],
            "constraints": {
                "do_not_append_full_user_question": True,
                "do_not_expand_each_topic_word": True,
                "prefer_gap_direction_under_existing_scope": True,
                "max_queries_per_task": 3,
                "protocols": ["s2_snippet", "s2_paper", "openalex_work", "abstract"],
                "output_must_match_downstream_protocol": True,
            },
        }
        for index, batch in enumerate(batches)
    ]


class QwenStructureGapQueryPlanner:
    def __init__(
        self,
        *,
        prompt_path: str | Path = DEFAULT_PROMPT_PATH,
        client: QwenFlashOnlyClient | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self.prompt_path = Path(prompt_path)
        self.client = client or QwenFlashOnlyClient(agent_name="ArticleStructureGapQueryPlanner")
        self.max_tokens = int(max_tokens)

    def __call__(self, requests: Sequence[Mapping[str, Any]]) -> List[GapQueryProviderResult]:
        results: List[GapQueryProviderResult] = []
        for request in requests:
            response = self.client.call(
                [
                    {"role": "system", "content": self.prompt_path.read_text(encoding="utf-8")},
                    {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
                ],
                max_tokens=self.max_tokens,
                force_mock=False,
            )
            usage = dict(response.get("_llm_usage") or {})
            results.append(
                GapQueryProviderResult(
                    response=_safe_json(str(response.get("content") or "")),
                    usage=usage,
                    provider_model=MODEL_NAME,
                    mock_llm=bool(usage.get("mock_llm")),
                )
            )
        return results


def build_gap_query_plan(
    full_structure: FullStructureResult | Mapping[str, Any],
    compilation: StructureGapTaskCompilationResult | Mapping[str, Any],
    *,
    provider: Optional[QwenStructureGapQueryPlanner] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> GapQueryPlanningResult:
    full = full_structure if isinstance(full_structure, FullStructureResult) else FullStructureResult.model_validate(full_structure)
    compiled = compilation if isinstance(compilation, StructureGapTaskCompilationResult) else StructureGapTaskCompilationResult.model_validate(compilation)
    if compiled.source_full_structure_id != full.result_id:
        raise ValueError("gap compilation does not match full structure")
    if not compiled.tasks:
        return GapQueryPlanningResult(
            result_id="gap-query-planning-" + _digest([full.result_id, compiled.result_id]),
            source_full_structure_id=full.result_id,
            source_compilation_id=compiled.result_id,
            status="no_tasks",
            model_status="unavailable",
        )
    known_tasks = {task.task_id: task for task in compiled.tasks if task.status == "planned"}
    warnings: List[str] = []
    usage: Dict[str, Any] = {}
    raw_queries: List[Any] = []
    model_status: Literal["available", "partial", "unavailable"] = "unavailable"
    if provider is not None:
        payloads = build_gap_query_payloads(full, compiled, batch_size=batch_size)
        try:
            results = list(provider(payloads))
            if len(results) != len(payloads):
                warnings.append(f"query planner returned {len(results)} results for {len(payloads)} batches")
            valid_results = [item for item in results if isinstance(item, GapQueryProviderResult)]
            for result in valid_results:
                raw_queries.extend(result.response.get("queries") or [])
                for key, value in (result.usage or {}).items():
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        usage[key] = usage.get(key, 0) + value
                    elif key not in usage:
                        usage[key] = value
            model_status = "available" if raw_queries else "partial"
            usage["batch_count"] = len(payloads)
        except Exception as exc:
            warnings.append(f"structure gap query planner unavailable: {exc}")
    queries: List[GapSearchQuery] = []
    seen: set[str] = set()
    handled: set[str] = set()
    protocol_limits = {
        "s2_snippet": "max_s2_items",
        "s2_paper": "max_s2_items",
        "openalex_work": "max_oa_items",
        "abstract": "max_abstract_items",
    }
    for index, raw in enumerate(raw_queries, start=1):
        try:
            draft = _QueryDraft.model_validate(raw)
        except ValidationError as exc:
            warnings.append(f"malformed gap query {index}: {exc}")
            continue
        task = known_tasks.get(draft.source_task_id.strip())
        protocol = draft.protocol.strip().casefold()
        query_text = draft.query_text.strip()
        if task is None or protocol not in protocol_limits or not query_text:
            warnings.append(f"gap query {index} references unknown task/protocol or is empty")
            continue
        key = _normalize_query(query_text)
        if key in seen:
            warnings.append(f"duplicate gap query {index} ignored")
            continue
        seen.add(key)
        handled.add(task.task_id)
        max_items = int(getattr(task, protocol_limits[protocol]))
        queries.append(
            GapSearchQuery(
                query_id=f"gap-query-{len(queries)+1:03d}",
                source_task_id=task.task_id,
                protocol=protocol,  # type: ignore[arg-type]
                query_text=query_text,
                direction_label=draft.direction_label or task.description,
                max_items=max_items,
                dedupe_key=_digest([protocol, key]),
            )
        )
    unhandled = sorted(set(known_tasks) - handled)
    if unhandled:
        warnings.append(f"{len(unhandled)} planned gap tasks have no query output")
    status: Literal["planned", "partial", "unavailable"] = (
        "planned" if queries and not unhandled else "partial" if queries else "unavailable"
    )
    payload = {
        "source_full_structure_id": full.result_id,
        "source_compilation_id": compiled.result_id,
        "queries": [item.model_dump(mode="json") for item in queries],
        "unhandled": unhandled,
    }
    return GapQueryPlanningResult(
        result_id="gap-query-planning-" + _digest(payload),
        source_full_structure_id=full.result_id,
        source_compilation_id=compiled.result_id,
        status=status,
        queries=queries,
        unhandled_task_ids=unhandled,
        warnings=warnings,
        model_status=model_status,
        usage=usage,
    )


__all__ = [
    "GapQueryPlanningResult",
    "GapQueryProviderResult",
    "GapSearchQuery",
    "QwenStructureGapQueryPlanner",
    "build_gap_query_payloads",
    "build_gap_query_plan",
]
