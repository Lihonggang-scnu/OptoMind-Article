"""Recoverable Article gap-task queue for asynchronous retrieval workers."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Tuple

from pydantic import BaseModel, ConfigDict, Field

from optomind_research.runtime.artifact_store import atomic_write_json

from .article_structure_gap_tasks import StructureGapTaskCompilationResult
from .article_structure_gap_queries import GapQueryPlanningResult


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GapQueueItem(_StrictModel):
    schema_version: Literal["article-gap-queue-item.v1"] = "article-gap-queue-item.v1"
    queue_item_id: str
    source_task_id: str
    status: Literal["pending", "claimed", "completed", "failed"]
    worker_id: str = ""
    attempt: int = Field(default=0, ge=0)
    max_attempts: int = Field(ge=1, le=1, default=1)
    dispatch_id: str = ""
    persistence_id: str = ""
    error: str = ""


class GapTaskQueueState(_StrictModel):
    schema_version: Literal["article-gap-task-queue.v1"] = "article-gap-task-queue.v1"
    queue_id: str
    source_compilation_id: str
    source_query_plan_id: str = ""
    items: List[GapQueueItem] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    validation_errors: List[str] = Field(default_factory=list)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()[:24]


def build_gap_task_queue(
    compilation: StructureGapTaskCompilationResult | Mapping[str, Any],
    query_plan: GapQueryPlanningResult | Mapping[str, Any] | None = None,
) -> GapTaskQueueState:
    compiled = compilation if isinstance(compilation, StructureGapTaskCompilationResult) else StructureGapTaskCompilationResult.model_validate(compilation)
    queries = query_plan if isinstance(query_plan, GapQueryPlanningResult) else GapQueryPlanningResult.model_validate(query_plan) if query_plan is not None else None
    if queries is not None and queries.source_compilation_id != compiled.result_id:
        raise ValueError("query plan does not match gap compilation")
    task_ids = {item.source_task_id for item in (queries.queries if queries else [])}
    items = [
        GapQueueItem(
            queue_item_id=f"gap-queue-item-{index:03d}",
            source_task_id=task.task_id,
            status="pending",
            max_attempts=task.max_rounds,
        )
        for index, task in enumerate(compiled.tasks, start=1)
        if task.status == "planned" and (queries is None or task.task_id in task_ids)
    ]
    payload = {
        "source_compilation_id": compiled.result_id,
        "source_query_plan_id": queries.result_id if queries else "",
        "items": [item.model_dump(mode="json") for item in items],
    }
    return GapTaskQueueState(
        queue_id="gap-queue-" + _digest(payload),
        source_compilation_id=compiled.result_id,
        source_query_plan_id=queries.result_id if queries else "",
        items=items,
    )


def claim_next_task(state: GapTaskQueueState | Mapping[str, Any], worker_id: str) -> Tuple[GapTaskQueueState, GapQueueItem | None]:
    model = state if isinstance(state, GapTaskQueueState) else GapTaskQueueState.model_validate(state)
    worker = str(worker_id or "").strip()
    if not worker:
        raise ValueError("worker_id must be non-empty")
    index = next((i for i, item in enumerate(model.items) if item.status == "pending"), None)
    if index is None:
        return model, None
    item = model.items[index]
    claimed = item.model_copy(update={"status": "claimed", "worker_id": worker, "attempt": item.attempt + 1})
    items = list(model.items)
    items[index] = claimed
    return model.model_copy(update={"items": items}), claimed


def complete_task(
    state: GapTaskQueueState | Mapping[str, Any],
    queue_item_id: str,
    *,
    dispatch_id: str,
    persistence_id: str,
) -> GapTaskQueueState:
    model = state if isinstance(state, GapTaskQueueState) else GapTaskQueueState.model_validate(state)
    items = list(model.items)
    for index, item in enumerate(items):
        if item.queue_item_id != queue_item_id:
            continue
        if item.status == "completed":
            if item.dispatch_id == dispatch_id and item.persistence_id == persistence_id:
                return model
            raise ValueError(f"completed queue item {queue_item_id!r} has conflicting result")
        if item.status != "claimed":
            raise ValueError(f"queue item {queue_item_id!r} is not claimed")
        items[index] = item.model_copy(update={"status": "completed", "dispatch_id": dispatch_id, "persistence_id": persistence_id})
        return model.model_copy(update={"items": items})
    raise ValueError(f"unknown queue item {queue_item_id!r}")


def fail_task(
    state: GapTaskQueueState | Mapping[str, Any],
    queue_item_id: str,
    *,
    error: str,
) -> GapTaskQueueState:
    model = state if isinstance(state, GapTaskQueueState) else GapTaskQueueState.model_validate(state)
    items = list(model.items)
    for index, item in enumerate(items):
        if item.queue_item_id != queue_item_id:
            continue
        if item.status != "claimed":
            raise ValueError(f"queue item {queue_item_id!r} is not claimed")
        items[index] = item.model_copy(update={"status": "failed", "error": str(error)[:1000]})
        return model.model_copy(update={"items": items})
    raise ValueError(f"unknown queue item {queue_item_id!r}")


def write_gap_task_queue(state: GapTaskQueueState | Mapping[str, Any], path: str | Path) -> Path:
    model = state if isinstance(state, GapTaskQueueState) else GapTaskQueueState.model_validate(state)
    target = Path(path)
    payload = model.model_dump(mode="json")
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
        if existing != payload:
            raise ValueError(f"refusing to overwrite conflicting gap queue {target}")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(target, payload)
    return target


__all__ = [
    "GapQueueItem",
    "GapTaskQueueState",
    "build_gap_task_queue",
    "claim_next_task",
    "complete_task",
    "fail_task",
    "write_gap_task_queue",
]
