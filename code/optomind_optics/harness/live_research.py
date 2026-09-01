"""Managed, cross-platform launcher for real TMM research-harness runs.

The browser never receives provider credentials and cannot construct an
arbitrary command.  A bounded JSON request is translated into one fixed
``run_tmm_research_harness.py`` subprocess invocation.  Runtime state is
projected from the same append-only artifacts used by the static replay UI.
"""

from __future__ import annotations

import hmac
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, TextIO


JsonObject = dict[str, Any]

LIVE_REQUEST_FILE = "LIVE_RUN_REQUEST.json"
LIVE_CONSOLE_FILE = "LIVE_RUN_CONSOLE.txt"
_REPLAY_REQUIRED_FILES = (
    "REQUEST.json",
    "SCORING_STANDARD.json",
    "ITERATION_HISTORY.json",
    "SCORING_RANKING.json",
)

_DEFAULTS: JsonObject = {
    "maximum_iterations": 6,
    "maximum_initial_routes": 5,
    "route_planning_maximum_routes": 4,
    "max_rounds_per_route": 6,
    "minimum_rounds_before_llm_stop": 2,
    "maximum_refinement_rounds": 1,
    "maximum_method_research_rounds": 2,
    "method_research_wall_time_seconds": 360.0,
    "s2_request_budget_seconds": 75.0,
    "wall_time_seconds": 10800.0,
    "task_compiler_tier": "turbo",
    "online_method_research": True,
    "qwen_method_synthesis": True,
    "control_route": True,
}

_INT_LIMITS: dict[str, tuple[int, int]] = {
    "maximum_iterations": (1, 12),
    "maximum_initial_routes": (1, 8),
    "route_planning_maximum_routes": (1, 6),
    "max_rounds_per_route": (1, 10),
    "minimum_rounds_before_llm_stop": (1, 6),
    "maximum_refinement_rounds": (0, 4),
    "maximum_method_research_rounds": (1, 4),
}

_FLOAT_LIMITS: dict[str, tuple[float, float]] = {
    "method_research_wall_time_seconds": (30.0, 1800.0),
    "s2_request_budget_seconds": (10.0, 300.0),
    "wall_time_seconds": (60.0, 21600.0),
}

_PUBLIC_TELEMETRY_FIELDS = {
    "wall_seconds",
    "qwen_calls",
    "qwen_input_tokens",
    "qwen_output_tokens",
    "qwen_total_tokens",
    "estimated_qwen_cost_cny",
    "forward_evaluations",
    "optimizer_runs",
}


class LiveRunValidationError(ValueError):
    """The submitted research request is outside the web-launch contract."""


class LiveRunBusyError(RuntimeError):
    """The configured concurrent-run capacity has been reached."""


class LiveRunAuthorizationError(PermissionError):
    """A mutation request did not provide the configured access token."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> JsonObject:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _read_events(path: Path) -> list[JsonObject]:
    if not path.is_file():
        return []
    rows: list[JsonObject] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            for raw in stream:
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, Mapping):
                    rows.append(dict(value))
    except OSError:
        return []
    return rows


def _bounded_int(payload: Mapping[str, Any], key: str) -> int:
    lower, upper = _INT_LIMITS[key]
    raw = payload.get(key, _DEFAULTS[key])
    if isinstance(raw, bool):
        raise LiveRunValidationError(f"{key} 必须是整数")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise LiveRunValidationError(f"{key} 必须是整数") from exc
    if value < lower or value > upper:
        raise LiveRunValidationError(f"{key} 必须在 {lower}–{upper} 之间")
    return value


def _bounded_float(payload: Mapping[str, Any], key: str) -> float:
    lower, upper = _FLOAT_LIMITS[key]
    raw = payload.get(key, _DEFAULTS[key])
    if isinstance(raw, bool):
        raise LiveRunValidationError(f"{key} 必须是数值")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise LiveRunValidationError(f"{key} 必须是数值") from exc
    if value < lower or value > upper:
        raise LiveRunValidationError(f"{key} 必须在 {lower:g}–{upper:g} 之间")
    return value


def _boolean(payload: Mapping[str, Any], key: str) -> bool:
    value = payload.get(key, _DEFAULTS[key])
    if not isinstance(value, bool):
        raise LiveRunValidationError(f"{key} 必须是布尔值")
    return value


def _validated_request(payload: Mapping[str, Any]) -> tuple[str, JsonObject]:
    question = str(payload.get("question") or "").strip()
    if len(question) < 12:
        raise LiveRunValidationError("研究问题至少需要 12 个字符")
    if len(question) > 6000:
        raise LiveRunValidationError("研究问题不能超过 6000 个字符")
    if "\x00" in question:
        raise LiveRunValidationError("研究问题包含非法空字符")

    config: JsonObject = {key: _bounded_int(payload, key) for key in _INT_LIMITS}
    config.update({key: _bounded_float(payload, key) for key in _FLOAT_LIMITS})
    config.update(
        {
            key: _boolean(payload, key)
            for key in (
                "online_method_research",
                "qwen_method_synthesis",
                "control_route",
            )
        }
    )
    tier = str(payload.get("task_compiler_tier", _DEFAULTS["task_compiler_tier"]))
    if tier not in {"turbo", "plus"}:
        raise LiveRunValidationError("task_compiler_tier 只能是 turbo 或 plus")
    config["task_compiler_tier"] = tier
    if config["route_planning_maximum_routes"] > config["maximum_initial_routes"]:
        raise LiveRunValidationError("文献规划路线上限不能大于初始路线候选上限")
    if config["minimum_rounds_before_llm_stop"] > config["max_rounds_per_route"]:
        raise LiveRunValidationError("允许停止前的最少轮数不能大于单路线轮次上限")
    return question, config


def _configured_secret_file(path: Path) -> bool:
    try:
        return path.is_file() and bool(path.read_text(encoding="utf-8").strip())
    except (OSError, UnicodeDecodeError):
        return False


@dataclass
class _LiveRunRecord:
    run_id: str
    question: str
    config: JsonObject
    output_dir: Path
    created_at_utc: str
    status: str = "starting"
    process: subprocess.Popen[str] | None = None
    console: TextIO | None = None
    return_code: int | None = None
    started_monotonic: float = field(default_factory=time.monotonic)
    finished_at_utc: str | None = None
    stop_requested: bool = False
    launch_error: str = ""


class LiveRunManager:
    """Launch and observe a bounded number of real research runs."""

    def __init__(
        self,
        *,
        project_root: Path,
        output_root: Path,
        runner_path: Path | None = None,
        access_token: str | None = None,
        allow_unauthenticated_mutations: bool = False,
        max_concurrent_runs: int = 1,
    ) -> None:
        self.project_root = project_root.resolve()
        self.output_root = output_root.resolve()
        self.runner_path = (
            runner_path.resolve()
            if runner_path is not None
            else self.project_root / "scripts" / "run_tmm_research_harness.py"
        )
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.request_root = self.output_root / ".live_requests"
        self.request_root.mkdir(parents=True, exist_ok=True)
        self.access_token = str(access_token or "").strip()
        self.allow_unauthenticated_mutations = bool(allow_unauthenticated_mutations)
        self.max_concurrent_runs = max(1, min(int(max_concurrent_runs), 4))
        self._records: dict[str, _LiveRunRecord] = {}
        self._lock = threading.RLock()
        self._recover_persisted_runs()

    @property
    def defaults(self) -> JsonObject:
        return dict(_DEFAULTS)

    @property
    def access_required(self) -> bool:
        return not self.allow_unauthenticated_mutations

    def authorize(self, provided_token: str | None) -> None:
        if self.allow_unauthenticated_mutations:
            return
        if not self.access_token:
            raise LiveRunAuthorizationError(
                "远程真实运行未启用：请在服务端配置 OPTOMIND_UI_ACCESS_TOKEN"
            )
        if not hmac.compare_digest(str(provided_token or ""), self.access_token):
            raise LiveRunAuthorizationError("访问口令无效")

    def readiness(self) -> JsonObject:
        qwen_file = self.project_root / "api_keys" / "qwen-api-key.txt"
        s2_file = self.project_root / "api_keys" / "semantic-scholar-api-key.txt"
        qwen_ready = bool(
            str(os.environ.get("QWEN_API_KEY") or "").strip()
            or str(os.environ.get("DASHSCOPE_API_KEY") or "").strip()
            or _configured_secret_file(qwen_file)
        )
        s2_ready = bool(
            str(os.environ.get("SEMANTIC_SCHOLAR_API_KEY") or "").strip()
            or str(os.environ.get("SEMANTIC_SCHOLAR_API_KEYS") or "").strip()
            or _configured_secret_file(s2_file)
        )
        veritmm_root = self.project_root.parent / "veritmm"
        return {
            "ready_for_real_run": bool(qwen_ready and self.runner_path.is_file()),
            "qwen_configured": qwen_ready,
            "semantic_scholar_configured": s2_ready,
            "veritmm_available": veritmm_root.is_dir(),
            "runner_available": self.runner_path.is_file(),
            "output_root_writable": os.access(self.output_root, os.W_OK),
            "access_token_required": self.access_required,
            "maximum_concurrent_runs": self.max_concurrent_runs,
            "defaults": self.defaults,
        }

    def _recover_persisted_runs(self) -> None:
        for child in self.output_root.iterdir():
            if not child.is_dir() or child.name.startswith("."):
                continue
            manifest = _read_json(child / LIVE_REQUEST_FILE)
            if not manifest:
                continue
            result = _read_json(child / "RESEARCH_RESULT.json")
            status = "completed" if result.get("status") == "completed" else "interrupted"
            if result and status != "completed":
                status = "failed"
            self._records[child.name] = _LiveRunRecord(
                run_id=child.name,
                question=str(manifest.get("question") or ""),
                config=dict(manifest.get("configuration") or {}),
                output_dir=child,
                created_at_utc=str(manifest.get("created_at_utc") or ""),
                status=status,
                return_code=manifest.get("return_code"),
                finished_at_utc=str(manifest.get("finished_at_utc") or "") or None,
            )

    def _active_count(self) -> int:
        count = 0
        for record in self._records.values():
            self._refresh_process(record)
            if record.status in {"starting", "running", "stopping"}:
                count += 1
        return count

    def _new_run_id(self) -> str:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return f"live-{stamp}-{uuid.uuid4().hex[:8]}"

    def start(self, payload: Mapping[str, Any]) -> JsonObject:
        question, config = _validated_request(payload)
        if not self.readiness()["qwen_configured"]:
            raise LiveRunValidationError(
                "服务端尚未配置 Qwen 密钥，不能把本次任务标记为真实运行"
            )
        with self._lock:
            if self._active_count() >= self.max_concurrent_runs:
                raise LiveRunBusyError("已有真实研究正在运行，请等待完成或先停止当前任务")
            run_id = self._new_run_id()
            output_dir = self.output_root / run_id
            if output_dir.exists():
                raise LiveRunBusyError("新运行目录发生冲突，请重试")
            output_dir.mkdir(parents=False)
            question_path = self.request_root / f"{run_id}.txt"
            question_path.write_text(question, encoding="utf-8")
            manifest = {
                "schema_version": "optomind-live-run-request.v1",
                "run_id": run_id,
                "question": question,
                "configuration": config,
                "created_at_utc": _utc_now(),
            }
            (output_dir / LIVE_REQUEST_FILE).write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            record = _LiveRunRecord(
                run_id=run_id,
                question=question,
                config=config,
                output_dir=output_dir,
                created_at_utc=str(manifest["created_at_utc"]),
            )
            self._records[run_id] = record
            command = self._command(record, question_path)
            console = (output_dir / LIVE_CONSOLE_FILE).open(
                "w", encoding="utf-8", buffering=1
            )
            record.console = console
            popen_kwargs: JsonObject = {
                "cwd": str(self.project_root),
                "stdin": subprocess.DEVNULL,
                "stdout": console,
                "stderr": subprocess.STDOUT,
                "text": True,
                "env": os.environ.copy(),
            }
            if os.name == "nt":
                popen_kwargs["creationflags"] = getattr(
                    subprocess, "CREATE_NEW_PROCESS_GROUP", 0
                )
            else:
                popen_kwargs["start_new_session"] = True
            try:
                record.process = subprocess.Popen(command, **popen_kwargs)
                record.status = "running"
            except Exception as exc:
                record.status = "failed"
                record.launch_error = f"{type(exc).__name__}: {exc}"
                record.finished_at_utc = _utc_now()
                console.close()
                record.console = None
                self._persist_terminal_state(record)
                raise RuntimeError("无法启动研究进程") from exc
            threading.Thread(
                target=self._monitor,
                args=(record.run_id, question_path),
                name=f"optomind-live-{run_id}",
                daemon=True,
            ).start()
            return self.snapshot(run_id)

    def _command(self, record: _LiveRunRecord, question_path: Path) -> list[str]:
        config = record.config
        command = [
            sys.executable,
            "-u",
            str(self.runner_path),
            "--question-file",
            str(question_path),
            "--output-dir",
            str(record.output_dir),
            "--run-id",
            record.run_id,
        ]
        option_map = {
            "maximum_iterations": "--maximum-iterations",
            "maximum_initial_routes": "--maximum-initial-routes",
            "route_planning_maximum_routes": "--route-planning-maximum-routes",
            "max_rounds_per_route": "--max-rounds-per-route",
            "minimum_rounds_before_llm_stop": "--minimum-rounds-before-llm-stop",
            "maximum_refinement_rounds": "--maximum-refinement-rounds",
            "maximum_method_research_rounds": "--maximum-method-research-rounds",
            "method_research_wall_time_seconds": "--method-research-wall-time-seconds",
            "s2_request_budget_seconds": "--s2-request-budget-seconds",
            "wall_time_seconds": "--wall-time-seconds",
            "task_compiler_tier": "--task-compiler-tier",
        }
        for key, option in option_map.items():
            command.extend([option, str(config[key])])
        boolean_options = {
            "online_method_research": "--online-method-research",
            "qwen_method_synthesis": "--qwen-method-synthesis",
            "control_route": "--control-route",
        }
        for key, option in boolean_options.items():
            command.append(option if config[key] else f"--no-{option[2:]}")
        return command

    def _monitor(self, run_id: str, question_path: Path) -> None:
        record = self._records[run_id]
        process = record.process
        if process is None:
            return
        return_code = process.wait()
        with self._lock:
            record.return_code = int(return_code)
            result = _read_json(record.output_dir / "RESEARCH_RESULT.json")
            if record.stop_requested:
                record.status = "stopped"
            elif result.get("status") == "completed" and return_code == 0:
                record.status = "completed"
            else:
                record.status = "failed"
            record.finished_at_utc = _utc_now()
            if record.console is not None:
                record.console.close()
                record.console = None
            self._persist_terminal_state(record)
        try:
            question_path.unlink()
        except OSError:
            pass

    def _persist_terminal_state(self, record: _LiveRunRecord) -> None:
        path = record.output_dir / LIVE_REQUEST_FILE
        payload = _read_json(path)
        payload.update(
            {
                "status": record.status,
                "return_code": record.return_code,
                "finished_at_utc": record.finished_at_utc,
                "launch_error": record.launch_error,
            }
        )
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _refresh_process(self, record: _LiveRunRecord) -> None:
        process = record.process
        if process is None or record.status not in {"starting", "running", "stopping"}:
            return
        return_code = process.poll()
        if return_code is not None and record.return_code is None:
            record.return_code = int(return_code)

    def list_runs(self) -> list[JsonObject]:
        with self._lock:
            rows = [self._snapshot(record) for record in self._records.values()]
        return sorted(rows, key=lambda item: str(item.get("created_at_utc") or ""), reverse=True)

    def snapshot(self, run_id: str) -> JsonObject:
        with self._lock:
            record = self._records.get(run_id)
            if record is None:
                raise KeyError(run_id)
            return self._snapshot(record)

    def _snapshot(self, record: _LiveRunRecord) -> JsonObject:
        self._refresh_process(record)
        events = _read_events(record.output_dir / "RESEARCH_EVENTS.jsonl")
        latest = events[-1] if events else {}
        result = _read_json(record.output_dir / "RESEARCH_RESULT.json")
        telemetry = dict(result.get("telemetry") or {})
        public_telemetry = {
            key: telemetry.get(key)
            for key in _PUBLIC_TELEMETRY_FIELDS
            if key in telemetry
        }
        iteration_root = record.output_dir / "iterations"
        iteration_count = (
            len([item for item in iteration_root.iterdir() if item.is_dir()])
            if iteration_root.is_dir()
            else 0
        )
        route_events = [row for row in events if row.get("event_type") == "route_completed"]
        valid_candidates = sum(int(row.get("valid_candidates") or 0) for row in route_events)
        completed_executions = sum(
            1 for row in route_events if row.get("run_status") == "completed"
        )
        elapsed = float(latest.get("elapsed_seconds") or 0.0)
        if record.status in {"starting", "running", "stopping"}:
            elapsed = max(elapsed, time.monotonic() - record.started_monotonic)
        replay_available = all(
            (record.output_dir / name).is_file() for name in _REPLAY_REQUIRED_FILES
        )
        return {
            "run_id": record.run_id,
            "question": record.question,
            "configuration": dict(record.config),
            "status": record.status,
            "result_status": result.get("status"),
            "result_stage": result.get("stage"),
            "created_at_utc": record.created_at_utc,
            "finished_at_utc": record.finished_at_utc,
            "return_code": record.return_code,
            "elapsed_seconds": elapsed,
            "event_count": len(events),
            "iteration_count": iteration_count,
            "completed_execution_count": completed_executions,
            "physically_valid_candidate_count": valid_candidates,
            "latest_event": dict(latest),
            "event_types": sorted(
                {str(row.get("event_type")) for row in events if row.get("event_type")}
            ),
            "recent_events": events[-30:],
            "telemetry": public_telemetry,
            "replay_available": replay_available,
            "final_answer_available": (record.output_dir / "FINAL_ANSWER.md").is_file(),
            "stop_requested": record.stop_requested,
            "launch_error": record.launch_error,
        }

    def stop(self, run_id: str) -> JsonObject:
        with self._lock:
            record = self._records.get(run_id)
            if record is None:
                raise KeyError(run_id)
            process = record.process
            self._refresh_process(record)
            if process is None or record.status not in {"starting", "running"}:
                return self._snapshot(record)
            record.stop_requested = True
            record.status = "stopping"
            try:
                if os.name != "nt":
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                else:
                    process.terminate()
            except (OSError, ProcessLookupError):
                pass
            threading.Thread(
                target=self._kill_after_grace,
                args=(record.run_id,),
                name=f"optomind-stop-{run_id}",
                daemon=True,
            ).start()
            return self._snapshot(record)

    def _kill_after_grace(self, run_id: str) -> None:
        time.sleep(10.0)
        with self._lock:
            record = self._records.get(run_id)
            process = record.process if record else None
            if process is None or process.poll() is not None:
                return
            try:
                if os.name != "nt":
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                else:
                    process.kill()
            except (OSError, ProcessLookupError):
                pass


__all__ = [
    "LIVE_CONSOLE_FILE",
    "LIVE_REQUEST_FILE",
    "LiveRunAuthorizationError",
    "LiveRunBusyError",
    "LiveRunManager",
    "LiveRunValidationError",
]
