"""HTTP service combining real research execution with artifact replay."""

from __future__ import annotations

import json
import os
import threading
import webbrowser
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlparse

from .live_research import (
    LiveRunAuthorizationError,
    LiveRunBusyError,
    LiveRunManager,
    LiveRunValidationError,
)
from .static_replay import ReplayCatalog, ReplayRequestHandler


def _env_flag(name: str, default: bool = False) -> bool:
    value = str(os.environ.get(name) or "").strip().casefold()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _loopback_host(host: str) -> bool:
    return str(host).strip().casefold() in {"127.0.0.1", "localhost", "::1"}


class ResearchConsoleHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        catalog: ReplayCatalog,
        live_manager: LiveRunManager,
        ui_root: Path,
    ) -> None:
        self.catalog = catalog
        self.live_manager = live_manager
        self.ui_root = ui_root.resolve()
        super().__init__(server_address, ResearchConsoleRequestHandler)


class ResearchConsoleRequestHandler(ReplayRequestHandler):
    server: ResearchConsoleHTTPServer

    def _bearer_token(self) -> str:
        header = str(self.headers.get("Authorization") or "")
        prefix, separator, value = header.partition(" ")
        if separator and prefix.casefold() == "bearer":
            return value.strip()
        return ""

    def _authorize_mutation(self) -> bool:
        try:
            self.server.live_manager.authorize(self._bearer_token())
        except LiveRunAuthorizationError as exc:
            self._send_error_json(HTTPStatus.FORBIDDEN, str(exc))
            return False
        return True

    def _read_json_body(self) -> Mapping[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "Content-Length 无效")
            return None
        if length <= 0 or length > 65536:
            self._send_error_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "请求体必须是 1–65536 字节的 JSON",
            )
            return None
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_error_json(HTTPStatus.BAD_REQUEST, "请求体不是有效 UTF-8 JSON")
            return None
        if not isinstance(payload, Mapping):
            self._send_error_json(HTTPStatus.BAD_REQUEST, "请求体必须是 JSON 对象")
            return None
        return payload

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = unquote(urlparse(self.path).path)
        if path == "/healthz":
            readiness = self.server.live_manager.readiness()
            self._send_json(
                {
                    "status": "ok",
                    "mode": "research_console",
                    "ready_for_real_run": readiness["ready_for_real_run"],
                }
            )
            return
        if path == "/api/live/readiness":
            self._send_json(self.server.live_manager.readiness())
            return
        if path == "/api/live/runs":
            self._send_json({"runs": self.server.live_manager.list_runs()})
            return
        if path.startswith("/api/live/runs/"):
            run_id = path[len("/api/live/runs/") :].strip("/")
            try:
                payload = self.server.live_manager.snapshot(run_id)
            except KeyError:
                self._send_error_json(HTTPStatus.NOT_FOUND, "未找到该真实运行")
                return
            self._send_json(payload)
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = unquote(urlparse(self.path).path)
        if path == "/api/live/runs":
            if not self._authorize_mutation():
                return
            payload = self._read_json_body()
            if payload is None:
                return
            try:
                snapshot = self.server.live_manager.start(payload)
            except LiveRunValidationError as exc:
                self._send_error_json(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc))
                return
            except LiveRunBusyError as exc:
                self._send_error_json(HTTPStatus.CONFLICT, str(exc))
                return
            except RuntimeError as exc:
                self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            self._send_json(snapshot, status=HTTPStatus.ACCEPTED)
            return
        if path.startswith("/api/live/runs/") and path.endswith("/stop"):
            if not self._authorize_mutation():
                return
            run_id = path[len("/api/live/runs/") : -len("/stop")].strip("/")
            try:
                snapshot = self.server.live_manager.stop(run_id)
            except KeyError:
                self._send_error_json(HTTPStatus.NOT_FOUND, "未找到该真实运行")
                return
            self._send_json(snapshot, status=HTTPStatus.ACCEPTED)
            return
        self._send_error_json(HTTPStatus.NOT_FOUND, "接口不存在")


def serve_research_console(
    *,
    project_root: Path,
    output_root: Path,
    ui_root: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    """Serve the replay archive and bounded live-run API from one origin."""

    project_root = project_root.resolve()
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    if not (ui_root / "index.html").is_file():
        raise RuntimeError(f"未找到研究控制台页面：{ui_root}")

    access_token = str(os.environ.get("OPTOMIND_UI_ACCESS_TOKEN") or "").strip()
    explicit_unauthenticated = _env_flag(
        "OPTOMIND_ALLOW_UNAUTHENTICATED_RUNS", default=False
    )
    allow_unauthenticated = explicit_unauthenticated or (
        _loopback_host(host) and not access_token
    )
    try:
        max_concurrent = int(os.environ.get("OPTOMIND_MAX_CONCURRENT_RUNS", "1"))
    except ValueError:
        max_concurrent = 1
    configured_runner = str(os.environ.get("OPTOMIND_RUNNER_PATH") or "").strip()
    manager = LiveRunManager(
        project_root=project_root,
        output_root=output_root,
        runner_path=Path(configured_runner) if configured_runner else None,
        access_token=access_token,
        allow_unauthenticated_mutations=allow_unauthenticated,
        max_concurrent_runs=max_concurrent,
    )
    catalog = ReplayCatalog(output_root)
    try:
        server = ResearchConsoleHTTPServer(
            (host, port),
            catalog=catalog,
            live_manager=manager,
            ui_root=ui_root,
        )
    except OSError:
        if port == 0:
            raise
        server = ResearchConsoleHTTPServer(
            (host, 0),
            catalog=catalog,
            live_manager=manager,
            ui_root=ui_root,
        )
        print(f"端口 {port} 不可用，已自动选择本机可用端口。")

    display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    url = f"http://{display_host}:{server.server_port}/"
    print(f"OptoMind 研究控制台已启动：{url}")
    print(
        f"已发现 {len(catalog.discover_run_ids())} 组可回放运行；"
        "真实任务由固定参数接口启动。"
    )
    if not allow_unauthenticated and not access_token:
        print(
            "远程真实运行当前关闭。请配置 OPTOMIND_UI_ACCESS_TOKEN 后重启服务。"
        )
    if open_browser:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


__all__ = [
    "ResearchConsoleHTTPServer",
    "ResearchConsoleRequestHandler",
    "serve_research_console",
]
