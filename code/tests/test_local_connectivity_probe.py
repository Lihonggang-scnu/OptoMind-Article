from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "optomind_connectivity_probe",
    ROOT / "code" / "scripts" / "probe_local_connectivity.py",
)
assert SPEC is not None and SPEC.loader is not None
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


def test_key_pool_parser_accepts_lines_separators_and_assignments(tmp_path: Path) -> None:
    path = tmp_path / "keys.txt"
    path.write_text(
        "# private pool\nQWEN_API_KEY=key-a\nkey-b,key-c;key-b\n",
        encoding="utf-8",
    )

    assert PROBE._keys(str(path)) == ["key-a", "key-b", "key-c"]


def test_connectivity_error_text_never_contains_secret() -> None:
    error = OSError("opaque transport failure")

    detail = PROBE._http_error("服务", error)

    assert detail == "服务连接失败：OSError。"
    assert "opaque transport failure" not in detail
