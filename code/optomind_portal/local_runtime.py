"""Local-only portal that gates real research behind verified diagnostics."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


CODE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = CODE_ROOT.parent
STATIC_REPLAY_MODULE = CODE_ROOT / "optomind_optics" / "harness" / "static_replay.py"
PROBE_SCRIPT = CODE_ROOT / "scripts" / "probe_local_connectivity.py"
REQUIRED_REPLAY_FILES = (
    "REQUEST.json",
    "SCORING_STANDARD.json",
    "ITERATION_HISTORY.json",
    "SCORING_RANKING.json",
)


def _load_static_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "optomind_static_replay_local", STATIC_REPLAY_MODULE
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法载入静态回放模块：{STATIC_REPLAY_MODULE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_STATIC = _load_static_module()
ReplayCatalog = _STATIC.ReplayCatalog
ReplayHTTPServer = _STATIC.ReplayHTTPServer
ReplayRequestHandler = _STATIC.ReplayRequestHandler


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_error(exc: BaseException) -> str:
    text = str(exc).replace("\r", " ").replace("\n", " ").strip()
    return f"{type(exc).__name__}: {text[:280]}" if text else type(exc).__name__


def _last_event(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    last: dict[str, Any] = {}
    try:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, Mapping) and row.get("event_type"):
                    last = {
                        key: row[key]
                        for key in (
                            "sequence",
                            "elapsed_seconds",
                            "event_type",
                            "status",
                            "route_id",
                            "iteration_id",
                            "action",
                            "reason",
                        )
                        if key in row
                    }
    except OSError:
        return {}
    return last


def _event_progress(event_type: str, status: str) -> int:
    if status == "completed":
        return 100
    if status in {"failed", "cancelled"}:
        return 100
    ordered = {
        "request_received": 4,
        "problem_analyzed": 12,
        "scoring_standard_fixed": 20,
        "method_research_completed": 28,
        "routes_planned_from_literature": 34,
        "control_route_planned": 38,
        "strategy_planned": 42,
        "iteration_started": 48,
        "task_compiled": 54,
        "iteration_observed": 65,
        "feedback_decided": 72,
        "route_replanned": 78,
        "route_finished": 86,
        "scoring_completed": 94,
        "research_finished": 100,
    }
    return ordered.get(event_type, 8 if event_type else 2)


class LocalRuntimeController:
    """Own diagnostics and at most one evaluator-created research process."""

    def __init__(
        self,
        *,
        project_root: Path,
        code_root: Path,
        key_dir: Path,
        local_run_root: Path,
        runtime_preparer: Callable[[], Path],
        credential_resolver: Callable[[Path], tuple[Path, Path]],
        light_command_builder: Callable[..., list[str]],
        full_command_builder: Callable[..., list[str]],
    ) -> None:
        self.project_root = project_root.resolve()
        self.code_root = code_root.resolve()
        self.key_dir = key_dir.resolve()
        self.local_run_root = local_run_root.resolve()
        self.runtime_preparer = runtime_preparer
        self.credential_resolver = credential_resolver
        self.light_command_builder = light_command_builder
        self.full_command_builder = full_command_builder
        self._lock = threading.RLock()
        self._runtime_python: Path | None = None
        self._credentials: tuple[Path, Path] | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._process_log: Any = None
        self._started_monotonic: float | None = None
        self.diagnostics: dict[str, Any] = {
            "status": "idle",
            "ready": False,
            "started_at_utc": None,
            "finished_at_utc": None,
            "checks": [],
        }
        self.active_run: dict[str, Any] | None = None

    def _set_check(self, check_id: str, label: str, status: str, detail: str) -> None:
        with self._lock:
            rows = list(self.diagnostics.get("checks") or [])
            payload = {
                "id": check_id,
                "label": label,
                "status": status,
                "detail": detail,
            }
            for index, row in enumerate(rows):
                if row.get("id") == check_id:
                    rows[index] = payload
                    break
            else:
                rows.append(payload)
            self.diagnostics["checks"] = rows

    def start_diagnostics(self) -> None:
        with self._lock:
            if self.diagnostics.get("status") == "running":
                return
            if self._process is not None and self._process.poll() is None:
                raise RuntimeError("真实研究正在运行，不能同时重新检查环境。")
            self.diagnostics = {
                "status": "running",
                "ready": False,
                "started_at_utc": _utc_now(),
                "finished_at_utc": None,
                "checks": [
                    {
                        "id": "project_assets",
                        "label": "项目与物理资产",
                        "status": "running",
                        "detail": "正在核对主链路、材料数据库和回放资产。",
                    },
                    {
                        "id": "credentials",
                        "label": "本地服务密钥",
                        "status": "pending",
                        "detail": "等待检查。",
                    },
                    {
                        "id": "runtime",
                        "label": "隔离运行环境",
                        "status": "pending",
                        "detail": "等待检查。",
                    },
                    {
                        "id": "qwen",
                        "label": "Qwen 模型服务",
                        "status": "pending",
                        "detail": "等待实际连通测试。",
                    },
                    {
                        "id": "semantic_scholar",
                        "label": "Semantic Scholar 文献服务",
                        "status": "pending",
                        "detail": "等待实际连通测试。",
                    },
                ],
            }
        threading.Thread(target=self._run_diagnostics, daemon=True).start()

    def _run_diagnostics(self) -> None:
        try:
            required_assets = (
                self.code_root / "scripts" / "run_tmm_research_harness.py",
                self.code_root / "tmm_engine" / "rii_cache.db",
                self.project_root / "veritmm" / "tmm_engine" / "rii_cache.db",
                self.code_root / "outputs" / "tmm_research_harness",
            )
            missing = [path.name for path in required_assets if not path.exists()]
            if missing:
                raise RuntimeError("缺少项目资产：" + "、".join(missing))
            self._set_check(
                "project_assets",
                "项目与物理资产",
                "passed",
                "主 harness、材料数据库、VeriTMM 与正式回放目录均已找到。",
            )

            self._set_check(
                "credentials", "本地服务密钥", "running", "正在核对密钥文件。"
            )
            self._credentials = self.credential_resolver(self.key_dir)
            self._set_check(
                "credentials",
                "本地服务密钥",
                "passed",
                "Qwen 与 Semantic Scholar 密钥文件均非空；未读取到界面或日志。",
            )

            self._set_check(
                "runtime",
                "隔离运行环境",
                "running",
                "正在检查或准备 Python 科研依赖。",
            )
            self._runtime_python = Path(self.runtime_preparer()).resolve()
            self._set_check(
                "runtime",
                "隔离运行环境",
                "passed",
                f"科研依赖已经就绪（{self._runtime_python.name}）。",
            )

            qwen_key, s2_key = self._credentials
            env = os.environ.copy()
            env.update(
                {
                    "PYTHONUTF8": "1",
                    "QWEN_API_KEY_FILE": str(qwen_key),
                    "SEMANTIC_SCHOLAR_API_KEYS_FILE": str(s2_key),
                }
            )
            self._set_check(
                "qwen", "Qwen 模型服务", "running", "正在发起最小真实模型请求。"
            )
            self._set_check(
                "semantic_scholar",
                "Semantic Scholar 文献服务",
                "running",
                "正在发起最小真实文献请求。",
            )
            completed = subprocess.run(
                [str(self._runtime_python), "-u", str(PROBE_SCRIPT)],
                cwd=self.project_root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
            )
            lines = [line for line in completed.stdout.splitlines() if line.strip()]
            payload = json.loads(lines[-1]) if lines else {}
            for check_id, label in (
                ("qwen", "Qwen 模型服务"),
                ("semantic_scholar", "Semantic Scholar 文献服务"),
            ):
                row = next(
                    (
                        item
                        for item in payload.get("checks") or []
                        if item.get("id") == check_id
                    ),
                    {},
                )
                status = str(row.get("status") or "failed")
                detail = str(row.get("detail") or "连通测试没有返回有效状态。")
                self._set_check(check_id, label, status, detail)
            with self._lock:
                ready = completed.returncode == 0 and all(
                    row.get("status") == "passed"
                    for row in self.diagnostics.get("checks") or []
                )
                self.diagnostics["ready"] = ready
                self.diagnostics["status"] = "ready" if ready else "failed"
                self.diagnostics["finished_at_utc"] = _utc_now()
        except BaseException as exc:  # keep diagnostics honest even on bootstrap failure
            with self._lock:
                running = [
                    row
                    for row in self.diagnostics.get("checks") or []
                    if row.get("status") in {"running", "pending"}
                ]
            failed_id = str(running[0].get("id")) if running else "runtime"
            failed_label = str(running[0].get("label")) if running else "运行准备"
            self._set_check(failed_id, failed_label, "failed", _safe_error(exc))
            with self._lock:
                for row in list(self.diagnostics.get("checks") or []):
                    if row.get("status") in {"running", "pending"}:
                        self._set_check(
                            str(row.get("id")),
                            str(row.get("label")),
                            "failed",
                            "前置检查未通过，因此未执行此项。",
                        )
                self.diagnostics["ready"] = False
                self.diagnostics["status"] = "failed"
                self.diagnostics["finished_at_utc"] = _utc_now()

    def start_run(self, *, question: str, profile: str) -> None:
        normalized = " ".join(question.split())
        if len(normalized) < 12 or len(normalized) > 4000:
            raise ValueError("用户问题必须包含 12 至 4000 个字符。")
        if profile not in {"quick", "full"}:
            raise ValueError("运行模式必须是 quick 或 full。")
        with self._lock:
            if not self.diagnostics.get("ready"):
                raise RuntimeError("运行准备检查尚未全部通过。")
            if self._process is not None and self._process.poll() is None:
                raise RuntimeError("已有真实研究正在运行；本地入口一次只执行一个任务。")
            if self._runtime_python is None or self._credentials is None:
                raise RuntimeError("运行环境状态已失效，请重新检查。")

            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            run_id = f"local-{profile}-{timestamp}-{uuid.uuid4().hex[:6]}"
            question_dir = self.local_run_root / "_questions"
            log_dir = self.local_run_root / "_portal_logs"
            question_dir.mkdir(parents=True, exist_ok=True)
            log_dir.mkdir(parents=True, exist_ok=True)
            question_file = question_dir / f"{run_id}.txt"
            question_file.write_text(question.strip() + "\n", encoding="utf-8")
            output_dir = self.local_run_root / run_id
            builder = (
                self.light_command_builder if profile == "quick" else self.full_command_builder
            )
            command = builder(
                self._runtime_python,
                question_file=question_file,
                output_dir=output_dir,
            )
            qwen_key, s2_key = self._credentials
            env = os.environ.copy()
            env.update(
                {
                    "PYTHONUTF8": "1",
                    "QWEN_API_KEY_FILE": str(qwen_key),
                    "DASHSCOPE_API_KEY_FILE": str(qwen_key),
                    "SEMANTIC_SCHOLAR_API_KEYS_FILE": str(s2_key),
                    "QWEN_HTTP_TIMEOUT_SEC": env.get("QWEN_HTTP_TIMEOUT_SEC", "60"),
                }
            )
            log_path = log_dir / f"{run_id}.log"
            self._process_log = log_path.open("wb")
            self._process = subprocess.Popen(
                command,
                cwd=self.project_root,
                env=env,
                stdout=self._process_log,
                stderr=subprocess.STDOUT,
            )
            self._started_monotonic = time.monotonic()
            self.active_run = {
                "run_id": run_id,
                "profile": profile,
                "question": question.strip(),
                "status": "running",
                "started_at_utc": _utc_now(),
                "finished_at_utc": None,
                "elapsed_seconds": 0.0,
                "output_label": f"local_runs/{run_id}",
                "output_dir": str(output_dir),
                "log_path": str(log_path),
                "last_event": {},
                "progress": 2,
                "message": "研究进程已经启动，等待第一条阶段事件。",
                "replay_available": False,
                "exit_code": None,
            }
        threading.Thread(target=self._watch_process, daemon=True).start()

    def _watch_process(self) -> None:
        with self._lock:
            process = self._process
        if process is None:
            return
        exit_code = process.wait()
        with self._lock:
            if self._process_log is not None:
                self._process_log.close()
                self._process_log = None
            if self.active_run is None:
                return
            output_dir = Path(str(self.active_run["output_dir"]))
            replay_available = all((output_dir / name).is_file() for name in REQUIRED_REPLAY_FILES)
            if self.active_run.get("status") != "cancelled":
                self.active_run["status"] = (
                    "completed" if exit_code == 0 and replay_available else "failed"
                )
            self.active_run["exit_code"] = exit_code
            self.active_run["finished_at_utc"] = _utc_now()
            self.active_run["replay_available"] = replay_available
            self.active_run["progress"] = 100
            self.active_run["message"] = (
                "完整结果已经写入本地运行目录。"
                if self.active_run["status"] == "completed"
                else "研究进程已经结束；已写入的日志和中间产物均被保留。"
            )

    def cancel_run(self) -> None:
        with self._lock:
            if self._process is None or self._process.poll() is not None:
                raise RuntimeError("当前没有正在运行的研究任务。")
            self._process.terminate()
            if self.active_run is not None:
                self.active_run["status"] = "cancelled"
                self.active_run["finished_at_utc"] = _utc_now()
                self.active_run["message"] = "停止信号已发送；已有产物会被保留。"

    def _refresh_active_run(self) -> None:
        with self._lock:
            if self.active_run is None:
                return
            if self._started_monotonic is not None and self.active_run.get("status") == "running":
                self.active_run["elapsed_seconds"] = round(
                    time.monotonic() - self._started_monotonic, 1
                )
            output_dir = Path(str(self.active_run["output_dir"]))
            event = _last_event(output_dir / "RESEARCH_EVENTS.jsonl")
            if event:
                self.active_run["last_event"] = event
                self.active_run["progress"] = max(
                    int(self.active_run.get("progress") or 0),
                    _event_progress(
                        str(event.get("event_type") or ""),
                        str(self.active_run.get("status") or ""),
                    ),
                )

    def snapshot(self) -> dict[str, Any]:
        self._refresh_active_run()
        with self._lock:
            active = None
            if self.active_run is not None:
                active = {
                    key: value
                    for key, value in self.active_run.items()
                    if key not in {"output_dir", "log_path"}
                }
            return {
                "schema_version": "optomind-local-portal.v1",
                "mode": "local_verified_execution",
                "live_enabled": True,
                "diagnostics": json.loads(json.dumps(self.diagnostics)),
                "active_run": active,
            }


class LocalPortalHTTPServer(ReplayHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        catalog: Any,
        ui_root: Path,
        controller: LocalRuntimeController,
    ) -> None:
        self.controller = controller
        super().__init__(
            server_address,
            catalog,
            ui_root,
            handler_class=LocalPortalRequestHandler,
        )


class LocalPortalRequestHandler(ReplayRequestHandler):
    server: LocalPortalHTTPServer

    def _request_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("无效的请求长度。") from exc
        if length < 0 or length > 16 * 1024:
            raise ValueError("请求内容不能超过 16 KiB。")
        if length == 0:
            return {}
        if "application/json" not in str(self.headers.get("Content-Type") or ""):
            raise ValueError("本地接口只接受 application/json。")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("请求内容必须是 JSON 对象。")
        return payload

    def do_GET(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path)
        if path == "/api/local/status":
            self._send_json(self.server.controller.snapshot())
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path)
        try:
            payload = self._request_json()
            if path == "/api/local/diagnostics":
                self.server.controller.start_diagnostics()
            elif path == "/api/local/runs":
                self.server.controller.start_run(
                    question=str(payload.get("question") or ""),
                    profile=str(payload.get("profile") or "quick"),
                )
            elif path == "/api/local/runs/current/cancel":
                self.server.controller.cancel_run()
            else:
                self._send_error_json(HTTPStatus.NOT_FOUND, "本地接口不存在")
                return
        except (json.JSONDecodeError, OSError, RuntimeError, ValueError) as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._send_json(self.server.controller.snapshot(), status=HTTPStatus.ACCEPTED)


def serve_local_portal(
    *,
    formal_output_root: Path,
    local_output_root: Path,
    ui_root: Path,
    controller: LocalRuntimeController,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("真实提问入口只能监听本机回环地址。")
    catalog = ReplayCatalog(
        formal_output_root,
        additional_roots=(local_output_root,),
    )
    try:
        server = LocalPortalHTTPServer((host, port), catalog, ui_root, controller)
    except OSError:
        if port == 0:
            raise
        server = LocalPortalHTTPServer((host, 0), catalog, ui_root, controller)
        print(f"端口 {port} 不可用，已自动选择本机可用端口。")
    url = f"http://{host}:{server.server_port}/"
    print(f"OptoMind 本地统一前端已启动：{url}")
    print("静态回放立即可用；真实提问将在连通检查全部通过后激活。")
    if open_browser:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


__all__ = [
    "LocalPortalHTTPServer",
    "LocalPortalRequestHandler",
    "LocalRuntimeController",
    "serve_local_portal",
]
