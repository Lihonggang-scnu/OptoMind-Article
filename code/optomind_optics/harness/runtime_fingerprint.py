"""Stable source and dependency fingerprint for TMM Harness provenance."""

from __future__ import annotations

import hashlib
import importlib.metadata
import platform
import sys
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIRECTORIES = ("optomind_optics/harness", "tmm_engine")
DEPENDENCIES = ("numpy", "scipy", "pydantic", "tmm")


def _source_files(root: Path, directories: Iterable[str]) -> tuple[Path, ...]:
    files: list[Path] = []
    for relative in directories:
        directory = root / relative
        if directory.exists():
            files.extend(path for path in directory.rglob("*.py") if path.is_file())
    return tuple(sorted(files, key=lambda path: path.relative_to(root).as_posix()))


def source_tree_sha256(
    *,
    project_root: str | Path = PROJECT_ROOT,
    source_directories: Iterable[str] = SOURCE_DIRECTORIES,
) -> tuple[str, int]:
    """Hash relative paths and bytes of every relevant Python source file."""

    root = Path(project_root).resolve()
    files = _source_files(root, source_directories)
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest(), len(files)


def _dependency_versions(names: Iterable[str] = DEPENDENCIES) -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for name in names:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def build_runtime_fingerprint() -> dict[str, object]:
    source_hash, file_count = source_tree_sha256()
    return {
        "source_tree_sha256": source_hash,
        "source_file_count": file_count,
        "source_directories": list(SOURCE_DIRECTORIES),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "dependency_versions": _dependency_versions(),
    }


__all__ = [
    "DEPENDENCIES",
    "PROJECT_ROOT",
    "SOURCE_DIRECTORIES",
    "build_runtime_fingerprint",
    "source_tree_sha256",
]
