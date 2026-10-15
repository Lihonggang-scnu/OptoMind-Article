"""Offline checks for the focused dedicated20 transport seam."""
from __future__ import annotations

from email.message import Message
import importlib
import importlib.util
import json
from pathlib import Path
import socket
import sqlite3
import sys
from typing import Any

import pytest


TRANSPORT_ROOT = Path(__file__).resolve().parent
ARTICLE_ROOT = Path(r"D:\OptoMind-Article-3")
FOCUSED_ROOT = ARTICLE_ROOT / "r3t019a" / "focused_research"
NATIVE_RUNTIME = ARTICLE_ROOT / "r3r15r" / "optomind_research" / "runtime" / "upgrade3" / "module4" / "runtime.py"
BUDGET_ENTRY = ARTICLE_ROOT / "local-r3-handoff" / "tasks" / "R3-T001-INTEGRATE-OFFLINE" / "release" / "local_tools" / "budgeted_entry.py"
TASK_GUARD = BUDGET_ENTRY.with_name("task_budget_guard.py")

sys.path.insert(0, str(TRANSPORT_ROOT))
import dedicated_qwen_adapter as adapter  # noqa: E402


QUESTION = (
    "我正在为多光谱成像相机设计一片可见-近红外双波段分光膜。要求可见光波段 "
    "450-650 nm 尽可能高的反射率（用于 RGB 通道成像），同时近红外波段 "
    "700-1000 nm 尽可能高的透过率（用于 NIR 通道）。请在 45 度入射、熔融石英基底条件下，"
    "仅使用 TiO2 和 SiO2 设计一个平面多层介质膜，膜系总层数不超过 24 层。"
    "采用非偏振光（s、p 偏振等权平均）；各波段目标均定义为按波长积分后除以波段宽度的平均值。"
)


def _load_focused_run_and_planner() -> tuple[Any, Any]:
    name = "r3_t019_d20_focused_run"
    spec = importlib.util.spec_from_file_location(name, FOCUSED_ROOT / "run.py")
    assert spec is not None and spec.loader is not None
    run_module = importlib.util.module_from_spec(spec)
    sys.modules[name] = run_module
    spec.loader.exec_module(run_module)
    planner_module = importlib.import_module("optomind_research.query_planner")
    return run_module, planner_module


def _new_guard(tmp_path: Path) -> tuple[Any, Any, Any, Any]:
    runtime = adapter.load_native_runtime(NATIVE_RUNTIME)
    factory_module, guard_module = adapter._load_budget_factory(BUDGET_ENTRY, TASK_GUARD)
    ledger_path = tmp_path / "private" / "budget.sqlite"
    ledger = runtime.GlobalBudgetLedger(limit_cny=20.0, path=ledger_path)
    guard = guard_module.TaskBudgetGuard(
        ledger,
        task_id=adapter.TASK_ID,
        lane="focused_plan",
        task_limit_cny=20.0,
        max_calls=3,
        checkpoint_cny=None,
        global_limit_cny=20.0,
    )
    key_file = tmp_path / "synthetic-key.txt"
    key_file.write_text("synthetic-key\n", encoding="utf-8")
    config = adapter.Dedicated20Config.from_mapping(
        {
            "execution_enabled": True,
            "budget": {
                "mode": "dedicated",
                "limit_cny": 20,
                "max_http_attempts": 3,
                "checkpoint_cny": None,
            },
            "task": {
                "task_id": adapter.TASK_ID,
                "lane": "focused_plan",
                "limit_cny": 20,
            },
            "paths": {
                "ledger": str(ledger_path),
                "audit_root": str(tmp_path / "audit"),
                "key_file": str(key_file),
            },
            "transport": {
                "model": "qwen3.5-plus",
                "max_retries": 0,
                "max_keys": 1,
                "json_mode": True,
                "max_tokens": 7000,
            },
        }
    )
    return runtime, guard, config, factory_module


class _Response:
    status = 200

    def __init__(self, payload: dict[str, Any]):
        self._raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.headers = Message()

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def read(self) -> bytes:
        return self._raw


class _Opener:
    def __init__(self, payloads: list[dict[str, Any]], calls: list[Any], ledger_path: Path):
        self.payloads = payloads
        self.calls = calls
        self.ledger_path = ledger_path

    def open(self, request: Any, timeout: float | None = None) -> _Response:
        with sqlite3.connect(self.ledger_path.as_uri() + '?mode=ro', uri=True) as db:
            assert db.execute("SELECT COUNT(*) FROM reservations WHERE status='reserved'").fetchone()[0] == 1, 'Reservation must precede HTTP'
        self.calls.append((request, timeout))
        if not self.payloads:
            raise AssertionError("unexpected native HTTP attempt")
        return _Response(self.payloads.pop(0))


def _payload(content: str, *, usage: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": "synthetic-response",
        "model": "qwen3.5-plus",
        "choices": [{
            "message": {"content": content},
            "finish_reason": "stop",
        }],
    }
    if usage is not None:
        result["usage"] = usage
    return result


def _valid_task_sheet() -> dict[str, Any]:
    path = FOCUSED_ROOT / "examples" / "rgb_nir_task_sheet.json"
    sheet = json.loads(path.read_text(encoding="utf-8"))
    sheet["original_question"] = QUESTION
    return sheet


def test_nested_dedicated20_contract_and_positional_plan_gate(tmp_path: Path) -> None:
    _runtime, _guard, config, _factory = _new_guard(tmp_path)
    assert config.ledger_path == tmp_path / "private" / "budget.sqlite"
    assert config.global_limit_cny == 20.0
    assert config.task_limit_cny == 20.0
    assert config.max_calls == 3

    with pytest.raises(adapter.Dedicated20ConfigError, match="original 140-CNY ledger"):
        adapter.Dedicated20Config.from_mapping(
            {
                "paths": {
                    "ledger": str(adapter.ORIGINAL_LEDGER_PATH),
                    "audit_root": str(tmp_path / "audit"),
                    "key_file": str(tmp_path / "key"),
                }
            }
        )

    seen: list[list[str]] = []

    def fake_main(argv: list[str]) -> int:
        seen.append(argv)
        return 0

    assert adapter.run_plan_only(fake_main, ["plan", "--output", "empty"]) == 0
    assert seen == [["plan", "--output", "empty"]]
    with pytest.raises(adapter.Dedicated20ConfigError):
        adapter.run_plan_only(fake_main, ["retrieve", "--output", "empty"])


def test_original_run_main_plan_uses_native_http_and_preserves_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, guard, config, _factory = _new_guard(tmp_path)
    run_module, planner_module = _load_focused_run_and_planner()

    valid = json.dumps(_valid_task_sheet(), ensure_ascii=False)
    payloads = [
        _payload("{}", usage={"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}),
        _payload(valid, usage={"prompt_tokens": 120, "completion_tokens": 420, "total_tokens": 540}),
    ]
    calls: list[Any] = []
    opener = _Opener(payloads, calls, guard.path)
    monkeypatch.setattr(runtime.urllib.request, "build_opener", lambda *_a, **_k: opener)
    monkeypatch.setattr(socket, "create_connection", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("socket use")))

    output = tmp_path / "plan-output"
    with adapter.install_focused_transport(planner_module, runtime, guard, config) as receipt:
        code = adapter.run_plan_only(
            run_module,
            ["plan", "--question", QUESTION, "--output", str(output)],
        )
        assert receipt.source_origins["focused_query_planner"]["path"].endswith(
            r"focused_research\vendor\review\optomind_research\query_planner.py"
        )
        assert receipt.source_origins["vendored_model_policy"]["qwen_model_policy"]["path"].endswith(
            r"focused_research\vendor\review\config\model_policy.yaml"
        )
    assert code == 0
    assert len(calls) == 2
    planning = json.loads((output / "planning.json").read_text(encoding="utf-8"))
    assert planning["status"] == "ok"
    assert planning["task_sheet"]["original_question"] == QUESTION
    snapshot = guard.as_dict()
    assert snapshot["task_budget"]["attempted_calls"] == 2
    assert snapshot["task_budget"]["unresolved_count"] == 0


def test_vendor_standard_model_is_rejected_instead_of_remapped(
    tmp_path: Path,
) -> None:
    runtime, guard, config, _factory = _new_guard(tmp_path)
    _run_module, planner_module = _load_focused_run_and_planner()
    with adapter.install_focused_transport(planner_module, runtime, guard, config):
        with pytest.raises(
            adapter.Dedicated20ConfigError,
            match="outside the dedicated allowlist",
        ):
            planner_module.call_qwen_chat(
                "QueryPlannerFormatRepairAgent",
                [{"role": "user", "content": "return JSON"}],
                model_tier="standard_model",
                temperature=0.0,
                force_mock=False,
                max_tokens=1800,
                response_format={"type": "json_object"},
            )
    assert guard.as_dict()["task_budget"]["attempted_calls"] == 0


def test_missing_usage_holds_budget_and_blocks_next_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, guard, config, _factory = _new_guard(tmp_path)
    _run_module, planner_module = _load_focused_run_and_planner()
    payloads = [_payload('{"raw": "kept"}')]
    calls: list[Any] = []
    opener = _Opener(payloads, calls, guard.path)
    monkeypatch.setattr(runtime.urllib.request, "build_opener", lambda *_a, **_k: opener)

    with adapter.install_focused_transport(planner_module, runtime, guard, config):
        first = planner_module.call_qwen_chat(
            "QueryPlannerAgent",
            [{"role": "user", "content": "return JSON"}],
            model_tier="QueryPlannerAgent",
            temperature=0.1,
            force_mock=False,
            max_tokens=7000,
            response_format={"type": "json_object"},
        )
        second = planner_module.call_qwen_chat(
            "QueryPlannerAgent",
            [{"role": "user", "content": "return JSON"}],
            model_tier="QueryPlannerAgent",
            temperature=0.1,
            force_mock=False,
            max_tokens=7000,
            response_format={"type": "json_object"},
        )
    assert first["transport_error"] == "request_not_settled"
    assert first["budget_error"] == "request_not_settled"
    assert first["_llm_usage"]["request_attempt_count"] == 1
    assert first["_llm_usage"]["budget_hold_preserved"] is True
    assert second["transport_error"] in {"request_not_settled", "unresolved_global_reservation"}
    assert second["_llm_usage"]["request_attempt_count"] == 0
    assert second["_llm_usage"]["budget_hold_preserved"] is False
    assert len(calls) == 1
    snapshot = guard.as_dict()
    assert snapshot["task_budget"]["attempted_calls"] == 1
    assert snapshot["task_budget"]["unresolved_count"] == 1
    response_files = list((tmp_path / 'audit').glob('*/calls/*/RESPONSE.json'))
    assert len(response_files) == 1
    assert json.loads(response_files[0].read_text(encoding='utf-8'))['content'] == '{"raw": "kept"}'


def test_three_distinct_calls_are_admitted_and_fourth_is_denied_before_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, guard, config, _factory = _new_guard(tmp_path)
    _run_module, planner_module = _load_focused_run_and_planner()
    payloads = [
        _payload("{\"ok\": 1}", usage={"prompt_tokens": 80, "completion_tokens": 40, "total_tokens": 120})
        for _ in range(3)
    ]
    calls: list[Any] = []
    opener = _Opener(payloads, calls, guard.path)
    monkeypatch.setattr(runtime.urllib.request, "build_opener", lambda *_a, **_k: opener)

    with adapter.install_focused_transport(planner_module, runtime, guard, config):
        results = []
        for index in range(4):
            results.append(
                planner_module.call_qwen_chat(
                    "QueryPlannerAgent",
                    [{"role": "user", "content": f"return JSON {index}"}],
                    model_tier="QueryPlannerAgent",
                    temperature=0.1,
                    force_mock=False,
                    max_tokens=7000,
                    response_format={"type": "json_object"},
                )
            )
    assert all(not item.get("transport_error") for item in results[:3])
    assert results[3]["transport_error"] == "task_max_calls_exceeded"
    assert results[3]["_llm_usage"]["request_attempt_count"] == 0
    assert len(calls) == 3
    snapshot = guard.as_dict()
    assert snapshot["task_budget"]["attempted_calls"] == 3
    assert snapshot["task_budget"]["unresolved_count"] == 0


def test_timeout_keeps_reserved_attempt_and_blocks_followup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, guard, config, _factory = _new_guard(tmp_path)
    _run_module, planner_module = _load_focused_run_and_planner()
    calls: list[Any] = []

    class TimeoutOpener:
        def open(self, request: Any, timeout: float | None = None) -> Any:
            calls.append((request, timeout))
            raise TimeoutError("synthetic timeout")

    monkeypatch.setattr(runtime.urllib.request, "build_opener", lambda *_a, **_k: TimeoutOpener())
    with adapter.install_focused_transport(planner_module, runtime, guard, config):
        result = planner_module.call_qwen_chat(
            "QueryPlannerAgent",
            [{"role": "user", "content": "return JSON"}],
            model_tier="QueryPlannerAgent",
            temperature=0.1,
            force_mock=False,
            max_tokens=7000,
            response_format={"type": "json_object"},
        )
    assert result["_llm_usage"]["request_attempt_count"] == 1
    assert result["_llm_usage"]["budget_hold_preserved"] is True
    assert result["transport_error"]
    assert len(calls) == 1
    snapshot = guard.as_dict()
    assert snapshot["task_budget"]["attempted_calls"] == 1
    assert snapshot["task_budget"]["unresolved_count"] == 1


def test_dedicated_twenty_cny_balance_blocks_before_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, guard, config, _factory = _new_guard(tmp_path)
    _run_module, planner_module = _load_focused_run_and_planner()
    native = runtime.GlobalBudgetLedger(limit_cny=20, path=guard.path)
    historical = native.reserve(19.99, 'synthetic-existing-dedicated-spend')
    native.settle(historical['reservation_id'], 19.99)
    calls: list[Any] = []
    opener = _Opener([], calls, guard.path)
    monkeypatch.setattr(runtime.urllib.request, 'build_opener', lambda *_a, **_k: opener)
    with adapter.install_focused_transport(planner_module, runtime, guard, config):
        result = planner_module.call_qwen_chat('QueryPlannerAgent',
            [{'role': 'user', 'content': 'return JSON'}], model_tier='QueryPlannerAgent',
            temperature=0.1, force_mock=False, max_tokens=7000,
            response_format={'type': 'json_object'})
    assert result['transport_error'] == 'global_budget_exceeded'
    assert result['_llm_usage']['request_attempt_count'] == 0
    assert not calls
    assert guard.as_dict()['task_budget']['attempted_calls'] == 0
