from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("acceptance,failed,route_failures,exit_code", [
    ("accepted", (), (), 0),
    ("partial", ("iteration_02",), (), 2),
    ("partial", (), ({"route_id": "route_01", "status": "error_unrecoverable", "reason": "contract_violation"},), 2),
    ("unverified", (), (), 2),
])
@pytest.mark.parametrize("resume", [False, True])
def test_formal_cli_exposes_acceptance_without_discarding_candidate(
    tmp_path, monkeypatch, capsys, acceptance, failed, route_failures, exit_code, resume
):
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_tmm_research_harness.py"
    spec = importlib.util.spec_from_file_location("research_cli_acceptance", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = SimpleNamespace(
        run_id="offline_cli", status="completed", acceptance_status=acceptance,
        failed_iteration_ids=failed, stage="finished", telemetry={"provider_calls": 0},
        route_failures=route_failures,
        final_answer=object(),
    )
    harness = SimpleNamespace(run=lambda *args, **kwargs: result,
                              resume_from_checkpoint=lambda *args, **kwargs: result)
    monkeypatch.setattr(module, "TMMResearchHarness", lambda *args, **kwargs: harness)
    monkeypatch.setattr(module, "discover_review_kb_paths", lambda *args, **kwargs: [])
    for name in ("TMMMethodResearchAdapter", "ArticlePlusQwenClient", "QwenTMMTaskCompiler",
                 "QwenTMMProblemAnalyzer", "QwenTMMStrategyPlanner"):
        monkeypatch.setattr(module, name, lambda *args, **kwargs: object())
    arguments = ["Offline fixture", "--output-dir", str(tmp_path / "output")]
    if resume:
        arguments += ["--resume-from", str(tmp_path / "checkpoint")]
    assert module.main(arguments) == exit_code
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "completed"
    assert output["acceptance_status"] == acceptance
    assert output["failed_iteration_ids"] == list(failed)
    assert output["route_failures"] == list(route_failures)
    assert output["final_answer"].endswith("FINAL_ANSWER.md")
    assert output["telemetry"]["provider_calls"] == 0
