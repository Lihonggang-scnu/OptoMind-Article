"""Dispatch bounded Article gap queries to the existing method-research adapter."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Literal, Mapping, Optional, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .article_structure_gap_queries import GapQueryPlanningResult, GapSearchQuery
from .method_research import (
    MethodPurpose,
    MethodResearchQuery,
    MethodResearchReport,
    MethodResearchStatus,
)


class GapResearchAdapter(Protocol):
    def research(
        self,
        problem: Mapping[str, Any],
        explicit_queries: Sequence[MethodResearchQuery],
        *,
        problem_id: str | None = None,
        online: bool | None = None,
    ) -> MethodResearchReport: ...


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GapRetrievalDispatchResult(_StrictModel):
    schema_version: Literal["article-gap-retrieval-dispatch.v1"] = (
        "article-gap-retrieval-dispatch.v1"
    )
    dispatch_id: str
    source_query_plan_id: str
    status: Literal["no_tasks", "completed", "partial", "unavailable"]
    reports: List[MethodResearchReport] = Field(default_factory=list)
    dispatched_query_ids: List[str] = Field(default_factory=list)
    unhandled_query_ids: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    validation_errors: List[str] = Field(default_factory=list)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()[:24]


def _to_method_query(query: GapSearchQuery) -> MethodResearchQuery:
    purpose = (
        MethodPurpose.failure_mode
        if query.direction_label and "gap" in query.direction_label.casefold()
        else MethodPurpose.design_family
    )
    return MethodResearchQuery(
        query_id=query.query_id,
        query_text=query.query_text,
        purpose=purpose,
        priority=5,
    )


def dispatch_gap_queries(
    query_plan: GapQueryPlanningResult | Mapping[str, Any],
    *,
    problem: Mapping[str, Any],
    adapter: GapResearchAdapter,
    problem_id: str = "",
    online: bool | None = None,
    max_queries_per_call: int = 6,
) -> GapRetrievalDispatchResult:
    plan = (
        query_plan
        if isinstance(query_plan, GapQueryPlanningResult)
        else GapQueryPlanningResult.model_validate(query_plan)
    )
    if max_queries_per_call < 1:
        raise ValueError("max_queries_per_call must be positive")
    if not plan.queries:
        return GapRetrievalDispatchResult(
            dispatch_id="gap-retrieval-dispatch-" + _digest([plan.result_id, "empty"]),
            source_query_plan_id=plan.result_id,
            status="no_tasks",
        )
    reports: List[MethodResearchReport] = []
    warnings: List[str] = []
    dispatched: List[str] = []
    unhandled: List[str] = list(plan.unhandled_task_ids)
    queries = [_to_method_query(item) for item in plan.queries]
    for index in range(0, len(queries), max_queries_per_call):
        batch = queries[index : index + max_queries_per_call]
        try:
            report = adapter.research(
                problem,
                batch,
                problem_id=problem_id or None,
                online=online,
            )
        except Exception as exc:
            warnings.append(f"retrieval batch {index // max_queries_per_call + 1} failed: {exc}")
            unhandled.extend(query.query_id for query in batch)
            continue
        reports.append(report)
        if report.status == MethodResearchStatus.unavailable:
            unhandled.extend(query.query_id for query in batch)
        dispatched.extend(query.query_id for query in batch)
        if report.reasons:
            warnings.extend(str(reason) for reason in report.reasons)
    dispatched_set = set(dispatched)
    unhandled = sorted(set(unhandled) - dispatched_set)
    if not reports:
        status: Literal["no_tasks", "completed", "partial", "unavailable"] = "unavailable"
    elif unhandled or any(report.status != MethodResearchStatus.completed for report in reports):
        status = "partial"
    else:
        status = "completed"
    payload = {
        "source_query_plan_id": plan.result_id,
        "dispatched": dispatched,
        "unhandled": unhandled,
        "report_ids": [report.report_id if hasattr(report, "report_id") else report.problem_id for report in reports],
    }
    return GapRetrievalDispatchResult(
        dispatch_id="gap-retrieval-dispatch-" + _digest(payload),
        source_query_plan_id=plan.result_id,
        status=status,
        reports=reports,
        dispatched_query_ids=dispatched,
        unhandled_query_ids=unhandled,
        warnings=list(dict.fromkeys(warnings)),
    )


__all__ = ["GapRetrievalDispatchResult", "GapResearchAdapter", "dispatch_gap_queries"]
