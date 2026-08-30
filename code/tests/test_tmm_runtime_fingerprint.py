from __future__ import annotations

from pathlib import Path

from optomind_optics.harness.runtime_fingerprint import (
    build_runtime_fingerprint,
    source_tree_sha256,
)


def test_source_tree_fingerprint_is_stable_and_content_sensitive(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    first = source_tree_sha256(project_root=tmp_path, source_directories=("pkg",))
    second = source_tree_sha256(project_root=tmp_path, source_directories=("pkg",))
    assert first == second
    assert first[1] == 1

    (package / "a.py").write_text("VALUE = 2\n", encoding="utf-8")
    changed = source_tree_sha256(project_root=tmp_path, source_directories=("pkg",))
    assert changed[0] != first[0]


def test_runtime_fingerprint_records_code_and_dependencies() -> None:
    payload = build_runtime_fingerprint()
    assert len(str(payload["source_tree_sha256"])) == 64
    assert int(payload["source_file_count"]) > 10
    assert payload["python_version"]
    assert "numpy" in payload["dependency_versions"]
