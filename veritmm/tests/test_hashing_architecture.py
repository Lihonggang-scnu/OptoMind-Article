"""Architecture gate: canonical-JSON identity hashes come only from hashing.py.

M0 hash convergence: business modules must not implement their own stable
hashes.  The canonical convention lives in ``tmm_engine.hashing`` and every
identity derived from it is declared under one ``identity_scheme``.  These
tests enforce the boundary mechanically so the convention cannot silently fork
again, and pin the hashing semantics that M0 changed.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from tmm_engine.experiment_store import ExperimentStore
from tmm_engine.hashing import (
    IDENTITY_SCHEME,
    canonical_json_bytes,
    canonical_json_dumps,
    file_sha256,
    stable_sha256,
)
from tmm_engine.research.contracts import canonical_json as research_canonical_json
from tmm_engine.run_artifacts import build_result_summary, stable_payload_sha256

ENGINE_ROOT = Path(__file__).resolve().parents[1] / "tmm_engine"
HASHING_MODULE = ENGINE_ROOT / "hashing.py"


def _python_sources() -> list[Path]:
    return sorted(
        path
        for path in ENGINE_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _dumps_calls(tree: ast.AST) -> list[ast.Call]:
    calls = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "dumps"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "json"
        ):
            calls.append(node)
    return calls


def _sha256_calls(tree: ast.AST) -> list[ast.Call]:
    calls = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "sha256"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "hashlib"
        ):
            calls.append(node)
    return calls


def _contains_json_dumps(node: ast.AST) -> bool:
    return any(
        isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and child.func.attr == "dumps"
        for child in ast.walk(node)
        if child is not node
    )


def test_business_modules_do_not_hash_canonical_json_themselves() -> None:
    violations: list[str] = []
    for path in _python_sources():
        if path == HASHING_MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for call in _sha256_calls(tree):
            args = list(call.args) + [keyword.value for keyword in call.keywords]
            if any(_contains_json_dumps(arg) for arg in args):
                violations.append(f"{path.relative_to(ENGINE_ROOT)}:{call.lineno}")
    assert not violations, (
        "self-implemented canonical-JSON hashes found; use tmm_engine.hashing: "
        f"{violations}"
    )


def test_business_modules_do_not_reimplement_the_canonical_serializer() -> None:
    violations: list[str] = []
    for path in _python_sources():
        if path == HASHING_MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for call in _dumps_calls(tree):
            keywords = {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg}
            sort_keys = keywords.get("sort_keys")
            separators = keywords.get("separators")
            compact_separators = (
                isinstance(separators, ast.Tuple)
                and len(separators.elts) == 2
                and all(
                    isinstance(element, ast.Constant) and element.value in (",", ":")
                    for element in separators.elts
                )
            )
            if isinstance(sort_keys, ast.Constant) and sort_keys.value is True and compact_separators:
                violations.append(f"{path.relative_to(ENGINE_ROOT)}:{call.lineno}")
    assert not violations, (
        "canonical-style json.dumps found outside hashing.py; "
        f"use tmm_engine.hashing: {violations}"
    )


# ---- pinned hashing semantics -----------------------------------------------


def test_identity_scheme_constant_is_pinned() -> None:
    assert IDENTITY_SCHEME == "veritmm-canonical-json-v1"


def test_ascii_payloads_hash_identically_under_the_legacy_convention() -> None:
    """Pure-ASCII payloads keep their pre-convergence digest exactly."""

    payload = {"a": 1, "b": [1.5, "x"], "c": {"d": True, "e": None}}
    legacy = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    assert stable_sha256(payload) == legacy


def test_unicode_payloads_use_the_utf8_convention() -> None:
    """Non-ASCII payloads follow ``ensure_ascii=False``; escaping would fork."""

    payload = {"material": "二氧化钛", "note": "µm band"}
    escaped = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    utf8 = hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    assert stable_sha256(payload) == utf8
    assert stable_sha256(payload) != escaped


def test_all_identity_helpers_share_one_convention() -> None:
    payload = {"k": 1, "material": "TiO₂", "band": "µm"}

    assert research_canonical_json(payload) == canonical_json_dumps(payload)
    assert stable_payload_sha256(payload) == stable_sha256(payload)
    assert stable_sha256(payload) == hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def test_file_sha256_matches_content_identity(tmp_path: Path) -> None:
    payload = {"z": 1, "a": {"b": 2}}
    target = tmp_path / "artifact.json"
    target.write_bytes(canonical_json_bytes(payload))
    assert file_sha256(target) == stable_sha256(payload)


def test_non_finite_floats_fail_closed() -> None:
    with pytest.raises(ValueError):
        canonical_json_dumps({"x": float("nan")})


def test_result_summary_declares_the_identity_scheme() -> None:
    summary = build_result_summary(
        mode="simulate",
        forward=None,
        certificate={"accepted": True, "status": "accepted"},
        run_id="run_identity_scheme",
        task_sha256="a" * 64,
    )
    assert summary["identity_scheme"] == IDENTITY_SCHEME


def test_cache_identity_declares_the_identity_scheme() -> None:
    identity = ExperimentStore.execution_identity(
        {"layers": [{"material": "tio2"}]},
        package_version="1.1.0",
        protocol_version="veritmm-protocol-v1",
        material_catalog_sha256=None,
        execution_settings={},
    )
    legacy_payload = {
        "normalized_task": {"layers": [{"material": "tio2"}]},
        "package_version": "1.1.0",
        "protocol_version": "veritmm-protocol-v1",
        "material_catalog_sha256": None,
        "execution_settings": {},
    }
    without_scheme = hashlib.sha256(
        json.dumps(legacy_payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    assert identity != without_scheme, "cache identity must include identity_scheme"
