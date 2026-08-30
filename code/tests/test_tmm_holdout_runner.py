from __future__ import annotations

import os
import importlib.util
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_tmm_harness_holdout_acceptance.py"
CONFIRM = "I_AUTHORIZE_ONE_BLIND_TMM_HOLDOUT"


def _runner_module():
    spec = importlib.util.spec_from_file_location("tmm_holdout_runner", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(tmp_path: Path, *, confirm: str, allow_env: bool) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if allow_env:
        env["OPTOMIND_ALLOW_TMM_HOLDOUT"] = "1"
    else:
        env.pop("OPTOMIND_ALLOW_TMM_HOLDOUT", None)
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--holdout-id",
            "HOLDOUT06",
            "--output-dir",
            str(tmp_path / "blind"),
            "--confirm",
            confirm,
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def test_blind_runner_rejects_wrong_confirmation_before_output(tmp_path: Path) -> None:
    result = _run(tmp_path, confirm="wrong", allow_env=True)
    assert result.returncode != 0
    assert not (tmp_path / "blind").exists()
    assert "confirmation text is incorrect" in result.stderr


def test_blind_runner_rejects_missing_environment_guard_before_output(tmp_path: Path) -> None:
    result = _run(tmp_path, confirm=CONFIRM, allow_env=False)
    assert result.returncode != 0
    assert not (tmp_path / "blind").exists()
    assert "OPTOMIND_ALLOW_TMM_HOLDOUT" in result.stderr


def test_resume_uses_real_run_state_filename_and_accepts_legacy_lock(tmp_path: Path) -> None:
    module = _runner_module()
    run_dir = tmp_path / "harness_run"
    run_dir.mkdir()
    (run_dir / "RUN_STATE.json").write_text("{}", encoding="utf-8")

    assert module._has_harness_run_state(run_dir) is True
    assert module._normalized_blind_lock(
        {
            "schema_version": "tmm-blind-run-lock.v1",
            "holdout_id": "HOLDOUT09",
            "resume": False,
        }
    ) == {
        "schema_version": "tmm-blind-run-lock.v1",
        "holdout_id": "HOLDOUT09",
    }
