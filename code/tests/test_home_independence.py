"""No-home import and optional legacy lookup regressions."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.parametrize("failure_type", ["RuntimeError", "OSError"])
def test_imports_and_optional_legacy_lookups_survive_unavailable_home(
    failure_type: str,
) -> None:
    """Import all HOME-sensitive modules without reading real key files."""

    code_root = Path(__file__).resolve().parents[1]
    script = textwrap.dedent(
        f"""
        import os
        from pathlib import Path

        def unavailable_home():
            raise {failure_type}("synthetic unavailable HOME")

        Path.home = staticmethod(unavailable_home)

        from config import qwen_config, secret_pool
        from tools.academic_backends import openalex_backend, semantic_scholar_backend

        missing = Path.cwd() / (".home-regression-missing-" + str(os.getpid()))
        assert not missing.exists()
        secret_pool.API_KEYS_DIR = missing
        secret_pool.DESKTOP = None
        assert secret_pool.secret_candidates((), ("never-read.txt",)) == []

        qwen_config._shared_pool_fallback_candidates = lambda *args, **kwargs: []
        assert qwen_config._qwen_key_candidates(shuffle=False) == []

        openalex_backend.DEFAULT_OPENALEX_KEYS_FILE = missing / "openalex.txt"
        assert openalex_backend._openalex_api_keys() == []

        semantic_scholar_backend.NEW_PROJECT_S2_KEYS_FILE = missing / "s2-new.txt"
        semantic_scholar_backend.DEFAULT_S2_KEYS_FILE = missing / "s2-default.txt"
        assert semantic_scholar_backend._api_keys() == []

        print("home-independent")
        """
    )
    env = {
        "PYTHONPATH": str(code_root),
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": "",
        "USERPROFILE": "",
    }
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(code_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "home-independent"
