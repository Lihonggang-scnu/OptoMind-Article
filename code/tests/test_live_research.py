from __future__ import annotations

import json
import time
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from optomind_optics.harness.live_research import (
    LiveRunAuthorizationError,
    LiveRunManager,
    LiveRunValidationError,
)
from optomind_optics.harness.research_console import ResearchConsoleHTTPServer
from optomind_optics.harness.static_replay import ReplayCatalog

PROJECT_ROOT = Path(__file__).resolve().parents[1]


FAKE_RUNNER = r'''from __future__ import annotations
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--question-file")
parser.add_argument("--output-dir")
parser.add_argument("--run-id")
args, _ = parser.parse_known_args()
out = Path(args.output_dir)
question = Path(args.question_file).read_text(encoding="utf-8")
events = [
    {"sequence": 1, "elapsed_seconds": 0.01, "event_type": "request_received"},
    {"sequence": 2, "elapsed_seconds": 0.02, "event_type": "problem_analyzed", "status": "analyzed"},
    {"sequence": 3, "elapsed_seconds": 0.03, "event_type": "research_finished", "status": "completed"},
]
(out / "RESEARCH_EVENTS.jsonl").write_text(
    "\n".join(json.dumps(row) for row in events) + "\n", encoding="utf-8"
)
for name, payload in {
    "REQUEST.json": {"question": question},
    "SCORING_STANDARD.json": {"standard": {"locked": True}},
    "ITERATION_HISTORY.json": [],
    "SCORING_RANKING.json": {"leaderboard": []},
}.items():
    (out / name).write_text(json.dumps(payload), encoding="utf-8")
(out / "RESEARCH_RESULT.json").write_text(
    json.dumps({"run_id": args.run_id, "status": "completed", "stage": "finished", "telemetry": {"qwen_calls": 1}}),
    encoding="utf-8",
)
(out / "FINAL_ANSWER.md").write_text("# complete\n", encoding="utf-8")
'''


def _manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **kwargs) -> LiveRunManager:
    project_root = tmp_path / "code"
    scripts = project_root / "scripts"
    scripts.mkdir(parents=True)
    runner = scripts / "fake_runner.py"
    runner.write_text(FAKE_RUNNER, encoding="utf-8")
    (tmp_path / "veritmm").mkdir()
    monkeypatch.setenv("QWEN_API_KEY", "test-only-key")
    allow_unauthenticated = kwargs.pop("allow_unauthenticated_mutations", True)
    return LiveRunManager(
        project_root=project_root,
        output_root=project_root / "outputs" / "tmm_research_harness",
        runner_path=runner,
        allow_unauthenticated_mutations=allow_unauthenticated,
        **kwargs,
    )


def test_live_manager_launches_fixed_runner_and_recovers_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    question = "请为一个真实近红外成像窗口设计双波段平面多层介质膜。"

    started = manager.start({"question": question})
    deadline = time.monotonic() + 10
    snapshot = started
    while snapshot["status"] in {"starting", "running", "stopping"}:
        assert time.monotonic() < deadline
        time.sleep(0.05)
        snapshot = manager.snapshot(started["run_id"])

    assert snapshot["status"] == "completed"
    assert snapshot["result_status"] == "completed"
    assert snapshot["event_count"] == 3
    assert snapshot["event_types"] == [
        "problem_analyzed",
        "request_received",
        "research_finished",
    ]
    assert snapshot["replay_available"] is True
    assert snapshot["final_answer_available"] is True
    assert snapshot["telemetry"] == {"qwen_calls": 1}
    manifest = (
        manager.output_root / started["run_id"] / "LIVE_RUN_REQUEST.json"
    ).read_text(encoding="utf-8")
    assert question in manifest
    assert "test-only-key" not in manifest

    recovered = LiveRunManager(
        project_root=manager.project_root,
        output_root=manager.output_root,
        runner_path=manager.runner_path,
        allow_unauthenticated_mutations=True,
    )
    assert recovered.snapshot(started["run_id"])["status"] == "completed"


def test_live_manager_rejects_unbounded_or_short_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)

    with pytest.raises(LiveRunValidationError, match="至少需要"):
        manager.start({"question": "太短"})
    with pytest.raises(LiveRunValidationError, match="1–10"):
        manager.start(
            {
                "question": "这是一个长度足够但轮次参数超出边界的真实光学研究问题。",
                "max_rounds_per_route": 99,
            }
        )


def test_remote_mutations_require_the_configured_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(
        tmp_path,
        monkeypatch,
        access_token="deployment-token",
        allow_unauthenticated_mutations=False,
    )

    assert manager.access_required is True
    with pytest.raises(LiveRunAuthorizationError):
        manager.authorize("")
    with pytest.raises(LiveRunAuthorizationError):
        manager.authorize("wrong")
    manager.authorize("deployment-token")


def _http_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, object] | None = None,
    token: str = "",
) -> tuple[int, dict[str, object]]:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        f"{base_url}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=5) as response:
            return int(response.status), json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return int(exc.code), json.loads(exc.read().decode("utf-8"))


def _serve_test_console(manager: LiveRunManager) -> tuple[ResearchConsoleHTTPServer, Thread]:
    server = ResearchConsoleHTTPServer(
        ("127.0.0.1", 0),
        catalog=ReplayCatalog(manager.output_root),
        live_manager=manager,
        ui_root=PROJECT_ROOT / "replay_ui",
    )
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02})
    thread.start()
    return server, thread


def _shutdown_test_console(server: ResearchConsoleHTTPServer, thread: Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_http_console_starts_and_reports_a_real_manager_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    server, thread = _serve_test_console(manager)
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        status, readiness = _http_json(base_url, "/api/live/readiness")
        assert status == 200
        assert readiness["ready_for_real_run"] is True

        status, started = _http_json(
            base_url,
            "/api/live/runs",
            method="POST",
            payload={
                "question": "请为近红外成像窗口设计一个真实的双波段平面多层介质膜。"
            },
        )
        assert status == 202
        run_id = str(started["run_id"])

        deadline = time.monotonic() + 10
        status, snapshot = _http_json(base_url, f"/api/live/runs/{run_id}")
        while snapshot["status"] in {"starting", "running", "stopping"}:
            assert time.monotonic() < deadline
            time.sleep(0.05)
            status, snapshot = _http_json(base_url, f"/api/live/runs/{run_id}")
        assert status == 200
        assert snapshot["status"] == "completed"
        assert snapshot["result_status"] == "completed"
    finally:
        _shutdown_test_console(server, thread)


def test_http_console_protects_remote_mutations_with_bearer_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(
        tmp_path,
        monkeypatch,
        access_token="deployment-token",
        allow_unauthenticated_mutations=False,
    )
    server, thread = _serve_test_console(manager)
    base_url = f"http://127.0.0.1:{server.server_port}"
    payload = {"question": "请为近红外成像窗口设计一个真实的双波段平面多层介质膜。"}
    try:
        status, denied = _http_json(
            base_url,
            "/api/live/runs",
            method="POST",
            payload=payload,
        )
        assert status == 403
        assert "口令" in str(denied["error"])

        status, started = _http_json(
            base_url,
            "/api/live/runs",
            method="POST",
            payload=payload,
            token="deployment-token",
        )
        assert status == 202
        run_id = str(started["run_id"])
        deadline = time.monotonic() + 10
        snapshot = started
        while snapshot["status"] in {"starting", "running", "stopping"}:
            assert time.monotonic() < deadline
            time.sleep(0.05)
            _, snapshot = _http_json(base_url, f"/api/live/runs/{run_id}")
        assert snapshot["status"] == "completed"
    finally:
        _shutdown_test_console(server, thread)
