"""Default and validate the Article-owned memory workspace."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping

from optomind_research.runtime.artifact_store import atomic_write_json

from .article_memory import ArticleMemoryStore


ARTICLE_MEMORY_BOUNDARY_SCHEMA_VERSION = "article-memory-boundary.v1"


class ArticleMemoryBoundaryError(ValueError):
    """Raised when an Article memory path would cross the isolation boundary."""


def resolve_article_memory_path(
    work_dir: str | Path,
    configured_path: str | Path | None = None,
) -> Path:
    root = Path(work_dir).resolve()
    candidate = (
        root / "ARTICLE_MEMORY.sqlite"
        if configured_path is None or not str(configured_path).strip()
        else Path(configured_path).resolve()
    )
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ArticleMemoryBoundaryError(
            f"Article memory path {candidate} is outside Article work_dir {root}"
        ) from exc
    if candidate.name != "ARTICLE_MEMORY.sqlite":
        raise ArticleMemoryBoundaryError(
            "Article memory database must be named ARTICLE_MEMORY.sqlite"
        )
    return candidate


def initialize_article_memory_workspace(
    work_dir: str | Path,
    configured_path: str | Path | None = None,
) -> Path:
    """Create the Article-only SQLite store and a conflict-safe manifest."""

    root = Path(work_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    database = resolve_article_memory_path(root, configured_path)
    ArticleMemoryStore(database)
    manifest = {
        "schema_version": ARTICLE_MEMORY_BOUNDARY_SCHEMA_VERSION,
        "domain": "article",
        "memory_database": database.name,
        "review_memory_imported": False,
        "review_kb_mode": "read_only_external_input",
    }
    manifest_path = root / "ARTICLE_MEMORY_MANIFEST.json"
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing != manifest:
            raise ArticleMemoryBoundaryError(
                f"refusing to overwrite conflicting Article memory manifest {manifest_path}"
            )
    else:
        atomic_write_json(manifest_path, manifest)
    return database


__all__ = [
    "ARTICLE_MEMORY_BOUNDARY_SCHEMA_VERSION",
    "ArticleMemoryBoundaryError",
    "initialize_article_memory_workspace",
    "resolve_article_memory_path",
]
