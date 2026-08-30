"""Focused tests for the Article Qwen shared key-pool bridge."""

from __future__ import annotations

from pathlib import Path

import pytest

import config.qwen_config as qwen_config
import config.secret_pool as secret_pool
from config.secret_pool import SecretCandidate


def _isolate_pools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir(exist_ok=True)
    monkeypatch.setattr(secret_pool, "API_KEYS_DIR", empty)
    monkeypatch.setattr(secret_pool, "DESKTOP", empty)
    for name in (
        "QWEN_API_KEY",
        "DASHSCOPE_API_KEY",
        "QWEN_API_KEY_FILE",
        "DASHSCOPE_API_KEY_FILE",
    ):
        monkeypatch.delenv(name, raising=False)


def test_shared_pool_fallback_resolves_files_and_dedups(
    tmp_path: Path,
) -> None:
    pool = tmp_path / "api_keys"
    pool.mkdir()
    (pool / "qwen-api-key.txt").write_text(
        "key-a\nkey-b\nkey-a\n# comment\n\n",
        encoding="utf-8",
    )
    (pool / "qwen-beiyong.txt").write_text(
        "key-c\nkey-a\n",
        encoding="utf-8",
    )
    (pool / "unrelated.txt").write_text(
        "not a key",
        encoding="utf-8",
    )
    candidates = qwen_config._shared_pool_fallback_candidates([pool])
    assert [item.value for item in candidates] == [
        "key-a",
        "key-b",
        "key-c",
    ]


def test_default_candidates_fall_back_to_shared_when_local_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_pools(monkeypatch, tmp_path)
    calls: list[object] = []

    def fake_fallback(dirs=None):
        calls.append(dirs)
        return [SecretCandidate(value="shared-key", source="shared#1")]

    monkeypatch.setattr(
        qwen_config,
        "_shared_pool_fallback_candidates",
        fake_fallback,
    )
    candidates = qwen_config._qwen_key_candidates(shuffle=False)
    assert [item.value for item in candidates] == ["shared-key"]
    assert calls == [None]


def test_env_key_precedence_skips_shared_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_pools(monkeypatch, tmp_path)
    monkeypatch.setenv("QWEN_API_KEY", "env-key")

    def fail_fallback(*args, **kwargs):
        raise AssertionError("shared fallback must not run when env key exists")

    monkeypatch.setattr(
        qwen_config,
        "_shared_pool_fallback_candidates",
        fail_fallback,
    )
    candidates = qwen_config._qwen_key_candidates(shuffle=False)
    assert [item.value for item in candidates] == ["env-key"]


def test_explicit_key_file_precedence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_pools(monkeypatch, tmp_path)
    key_file = tmp_path / "explicit.txt"
    key_file.write_text("file-key\n", encoding="utf-8")

    def fail_fallback(*args, **kwargs):
        raise AssertionError("shared fallback must not run with explicit file")

    monkeypatch.setattr(
        qwen_config,
        "_shared_pool_fallback_candidates",
        fail_fallback,
    )
    assert [
        item.value
        for item in qwen_config._qwen_key_candidates(
            key_file=key_file,
            shuffle=False,
        )
    ] == ["file-key"]

    monkeypatch.setenv("QWEN_API_KEY_FILE", str(key_file))
    assert [
        item.value
        for item in qwen_config._qwen_key_candidates(shuffle=False)
    ] == ["file-key"]


def test_no_key_stays_mock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_pools(monkeypatch, tmp_path)
    monkeypatch.setattr(
        qwen_config,
        "_shared_pool_fallback_candidates",
        lambda *args, **kwargs: [],
    )
    key, source = qwen_config._load_qwen_api_key_with_source()
    assert key is None
    assert source == "mock_llm"


def test_shared_pool_resolution_does_not_log_secrets(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    pool = tmp_path / "api_keys"
    pool.mkdir()
    (pool / "qwen-api-key.txt").write_text(
        "super-secret-value\n",
        encoding="utf-8",
    )
    qwen_config._shared_pool_fallback_candidates([pool])
    captured = capsys.readouterr()
    assert "super-secret-value" not in captured.out
    assert "super-secret-value" not in captured.err


def test_client_config_reports_full_shared_pool(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_pools(monkeypatch, tmp_path)
    keys = ["pool-a", "pool-b", "pool-c"]
    monkeypatch.setattr(
        qwen_config,
        "_shared_pool_fallback_candidates",
        lambda *args, **kwargs: [
            SecretCandidate(value=value, source=f"shared#{index}")
            for index, value in enumerate(keys, 1)
        ],
    )
    config = qwen_config.get_qwen_client_config("b_plus_model")
    assert config["api_key_candidate_count"] == 3
    assert [item["api_key"] for item in config["api_key_candidates"]] == keys
    assert config["api_key"] == keys[0]
    assert all(
        item["api_key_masked"] != item["api_key"]
        for item in config["api_key_candidates"]
    )
    second = qwen_config.get_qwen_client_config("b_plus_model")
    assert second["api_key_candidate_count"] == 3


def test_client_config_env_key_yields_single_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_pools(monkeypatch, tmp_path)
    monkeypatch.setenv("QWEN_API_KEY", "env-key")

    def fail_fallback(*args, **kwargs):
        raise AssertionError("shared fallback must not run with env key")

    monkeypatch.setattr(
        qwen_config,
        "_shared_pool_fallback_candidates",
        fail_fallback,
    )
    config = qwen_config.get_qwen_client_config("b_plus_model")
    assert config["api_key_candidate_count"] == 1
    assert [item["api_key"] for item in config["api_key_candidates"]] == [
        "env-key"
    ]
