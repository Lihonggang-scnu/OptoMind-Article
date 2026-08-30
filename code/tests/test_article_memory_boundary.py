from __future__ import annotations

import json

import pytest

from optomind_optics.harness.article_memory_boundary import (
    ArticleMemoryBoundaryError,
    initialize_article_memory_workspace,
    resolve_article_memory_path,
)


def test_article_memory_defaults_inside_work_dir(tmp_path):
    path = initialize_article_memory_workspace(tmp_path / "run")
    assert path == (tmp_path / "run" / "ARTICLE_MEMORY.sqlite").resolve()
    manifest = json.loads(
        (tmp_path / "run" / "ARTICLE_MEMORY_MANIFEST.json").read_text(encoding="utf-8")
    )
    assert manifest["domain"] == "article"
    assert manifest["review_memory_imported"] is False
    assert manifest["review_kb_mode"] == "read_only_external_input"


def test_article_memory_rejects_external_or_wrong_database_path(tmp_path):
    with pytest.raises(ArticleMemoryBoundaryError, match="outside Article work_dir"):
        resolve_article_memory_path(tmp_path / "run", tmp_path / "outside" / "ARTICLE_MEMORY.sqlite")
    with pytest.raises(ArticleMemoryBoundaryError, match="must be named"):
        resolve_article_memory_path(tmp_path / "run", tmp_path / "run" / "shared.sqlite")


def test_article_memory_manifest_is_conflict_safe(tmp_path):
    work_dir = tmp_path / "run"
    initialize_article_memory_workspace(work_dir)
    path = work_dir / "ARTICLE_MEMORY_MANIFEST.json"
    path.write_text(json.dumps({"domain": "review"}), encoding="utf-8")
    with pytest.raises(ArticleMemoryBoundaryError, match="conflicting"):
        initialize_article_memory_workspace(work_dir)
