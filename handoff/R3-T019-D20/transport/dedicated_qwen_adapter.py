"""Dedicated CNY20 transport seam for the standalone focused planner.

The focused checkout vendors a planner and a Qwen chat helper under generic
package names.  This module patches only the planner module supplied by the
caller and delegates accounting, replay, raw-response audit, reservation and
settlement to the already tested R3 budget wrapper and native runtime.

The installer intentionally does not call the old wrapper's configuration,
authorization or preparation entrypoints.  The caller owns construction of a
fresh native ``GlobalBudgetLedger`` and ``TaskBudgetGuard`` for this task.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sys
from types import ModuleType
from typing import Any, Callable, Iterator, Mapping, Sequence
import uuid


TASK_ID = "R3-T019-DEDICATED20"
TASK_LIMIT_CNY = 20.0
GLOBAL_LIMIT_CNY = 20.0
MAX_CALLS = 3
MAX_TOKENS = 7000
REPAIR_MAX_TOKENS = 1800
MAX_RETRIES = 0
MAX_KEYS = 1
CHECKPOINT_CNY = None
ALLOWED_MODELS = frozenset({"qwen3.5-plus", "qwen3.7-flash"})
JSON_RESPONSE_FORMAT = {"type": "json_object"}
ORIGINAL_LEDGER_PATH = Path(
    r"D:\OptoMind-Article-3\local-functional-trial\20261013\budget.sqlite"
)
DEFAULT_BUDGET_ENTRY = Path(
    r"D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T001-INTEGRATE-OFFLINE"
    r"\release\local_tools\budgeted_entry.py"
)
DEFAULT_TASK_GUARD = DEFAULT_BUDGET_ENTRY.with_name("task_budget_guard.py")
NATIVE_RUNTIME_SUFFIX = Path(
    r"optomind_research\runtime\upgrade3\module4\runtime.py"
)


class Dedicated20ConfigError(ValueError):
    """The installer received a configuration outside the dedicated contract."""


def _canonical_path(value: str | os.PathLike[str]) -> str:
    """Canonicalize a path without opening its contents."""

    return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(value))))


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Dedicated20ConfigError(f"{field} must be a nonempty string")
    return value


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Dedicated20ConfigError(f"{field} must be finite")
    number = float(value)
    if not math.isfinite(number):
        raise Dedicated20ConfigError(f"{field} must be finite")
    return number


def _exact_number(value: Any, expected: float, field: str) -> float:
    number = _finite_number(value, field)
    if abs(number - expected) > 1e-9:
        raise Dedicated20ConfigError(
            f"{field} must be exactly {expected:g} for dedicated20"
        )
    return expected


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _origin(path: Path) -> dict[str, str]:
    path = path.resolve(strict=True)
    return {"path": str(path), "sha256": _sha256(path)}


def _module_path(module: ModuleType, field: str) -> Path:
    value = getattr(module, "__file__", None)
    if not isinstance(value, str) or not value:
        raise Dedicated20ConfigError(f"{field} must expose a source file")
    return Path(value).resolve(strict=True)


def _load_module_from_path(path: Path, name: str) -> ModuleType:
    path = path.resolve(strict=True)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise Dedicated20ConfigError(f"cannot load source module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_native_runtime(runtime: ModuleType | str | os.PathLike[str]) -> ModuleType:
    """Return the native runtime, importing a path under a private name."""

    if isinstance(runtime, ModuleType):
        source = _module_path(runtime, "native runtime")
        return runtime
    source = Path(runtime).resolve(strict=True)
    # The suffix check prevents an accidental import of the generic Review
    # runtime or an unrelated module with the same basename.
    if not str(source).casefold().endswith(str(NATIVE_RUNTIME_SUFFIX).casefold()):
        raise Dedicated20ConfigError(
            "native runtime must be upgrade3/module4/runtime.py from the source tree"
        )
    name = "r3_t019_d20_native_runtime_" + _sha256(source)[:16]
    return _load_module_from_path(source, name)


def _load_budget_factory(
    budget_entry_path: Path,
    task_guard_path: Path,
) -> tuple[ModuleType, ModuleType]:
    """Load the tested factory without changing sys.path or generic imports."""

    guard_source = task_guard_path.resolve(strict=True)
    entry_source = budget_entry_path.resolve(strict=True)
    guard_name = "r3_t019_d20_task_budget_guard_" + _sha256(guard_source)[:16]
    entry_name = "r3_t019_d20_budgeted_entry_" + _sha256(entry_source)[:16]
    guard_module = _load_module_from_path(guard_source, guard_name)

    # budgeted_entry.py has one absolute import of task_budget_guard.  Supply
    # the exact source module only for the duration of that import, then restore
    # the caller's module table.  No package search path is reordered.
    missing = object()
    previous = sys.modules.get("task_budget_guard", missing)
    sys.modules["task_budget_guard"] = guard_module
    try:
        entry_module = _load_module_from_path(entry_source, entry_name)
    finally:
        if previous is missing:
            sys.modules.pop("task_budget_guard", None)
        else:
            sys.modules["task_budget_guard"] = previous
    if not callable(getattr(entry_module, "make_audited_client", None)):
        raise Dedicated20ConfigError(
            "tested budgeted_entry.make_audited_client is unavailable"
        )
    return entry_module, guard_module


@dataclass(frozen=True)
class Dedicated20Config:
    """Exact configuration contract for this independent budget lane."""

    ledger_path: Path
    audit_root: Path
    key_file: Path
    task_id: str = TASK_ID
    model: str = "qwen3.5-plus"
    repair_model: str = "qwen3.7-flash"
    task_limit_cny: float = TASK_LIMIT_CNY
    global_limit_cny: float = GLOBAL_LIMIT_CNY
    max_calls: int = MAX_CALLS
    checkpoint_cny: None = CHECKPOINT_CNY
    max_retries: int = MAX_RETRIES
    max_keys: int = MAX_KEYS
    json_mode: bool = True
    max_tokens: int = MAX_TOKENS

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Dedicated20Config":
        """Validate the parent launcher's nested config without opening paths.

        The launcher keeps admission, task and path settings in their native
        sections.  The tested audited-client factory only needs three paths and
        the task id, so this method projects that config into the small adapter
        contract.  Flat aliases remain accepted for offline unit tests and are
        checked against nested values when both are present.
        """
        if not isinstance(value, Mapping):
            raise Dedicated20ConfigError("dedicated20 config must be a mapping")

        paths = value.get("paths")
        if paths is None:
            paths = {}
        if not isinstance(paths, Mapping):
            raise Dedicated20ConfigError("paths must be a mapping")
        budget = value.get("budget")
        if budget is None:
            budget = {}
        if not isinstance(budget, Mapping):
            raise Dedicated20ConfigError("budget must be a mapping")
        task = value.get("task")
        if task is None:
            task = {}
        if not isinstance(task, Mapping):
            raise Dedicated20ConfigError("task must be a mapping")
        transport = value.get("transport")
        if transport is None:
            transport = {}
        if not isinstance(transport, Mapping):
            raise Dedicated20ConfigError("transport must be a mapping")

        def choose(field: str, *candidates: tuple[Mapping[str, Any], str]) -> Any:
            present = [container[key] for container, key in candidates
                       if key in container and container[key] is not None]
            if not present:
                return None
            first = present[0]
            if any(item != first for item in present[1:]):
                raise Dedicated20ConfigError(f"conflicting dedicated20 values for {field}")
            return first

        ledger_value = choose(
            "ledger_path", (paths, "ledger"), (value, "ledger_path"),
        )
        audit_value = choose(
            "audit_root", (paths, "audit_root"), (value, "audit_root"),
        )
        key_value = choose(
            "key_file", (paths, "key_file"), (value, "key_file"),
        )
        for field, candidate in (
            ("ledger_path", ledger_value),
            ("audit_root", audit_value),
            ("key_file", key_value),
        ):
            _require_text(candidate, field)

        execution_enabled = value.get("execution_enabled", True)
        if execution_enabled is not True:
            raise Dedicated20ConfigError("execution_enabled must be true")
        mode = choose("budget.mode", (budget, "mode"), (value, "budget_mode"))
        if mode is not None and mode != "dedicated":
            raise Dedicated20ConfigError("budget.mode must be exactly dedicated")

        task_id = choose("task_id", (task, "task_id"), (value, "task_id"))
        if task_id is None:
            task_id = TASK_ID
        task_id = _require_text(task_id, "task_id")
        if task_id != TASK_ID:
            raise Dedicated20ConfigError(f"task_id must be exactly {TASK_ID}")
        lane = choose("lane", (task, "lane"), (value, "lane"))
        if lane is not None and lane != "focused_plan":
            raise Dedicated20ConfigError("task.lane must be exactly focused_plan")
        model_value = choose(
            "model", (transport, "model"), (task, "model"), (value, "model"),
        )
        model = _require_text(model_value or "qwen3.5-plus", "model")
        if model not in ALLOWED_MODELS:
            raise Dedicated20ConfigError(
                f"model must be one of {sorted(ALLOWED_MODELS)}"
            )
        repair_model_value = choose(
            "repair_model", (transport, "repair_model"),
            (task, "repair_model"), (value, "repair_model"),
        )
        repair_model = _require_text(
            repair_model_value or "qwen3.7-flash", "repair_model"
        )
        if repair_model not in ALLOWED_MODELS:
            raise Dedicated20ConfigError(
                f"repair_model must be one of {sorted(ALLOWED_MODELS)}"
            )
        task_limit_value = choose(
            "task_limit_cny", (task, "limit_cny"), (value, "task_limit_cny"),
        )
        task_limit = _exact_number(
            task_limit_value if task_limit_value is not None else TASK_LIMIT_CNY,
            TASK_LIMIT_CNY,
            "task_limit_cny",
        )
        global_limit_value = choose(
            "global_limit_cny", (budget, "limit_cny"),
            (budget, "aggregate_limit_cny"), (value, "global_limit_cny"),
        )
        global_limit = _exact_number(
            global_limit_value if global_limit_value is not None else GLOBAL_LIMIT_CNY,
            GLOBAL_LIMIT_CNY,
            "global_limit_cny",
        )
        max_calls_value = choose(
            "max_calls", (budget, "max_http_attempts"),
            (task, "maximum_real_calls"), (task, "max_calls"),
            (value, "max_calls"),
        )
        max_calls = max_calls_value if max_calls_value is not None else MAX_CALLS
        if isinstance(max_calls, bool) or not isinstance(max_calls, int) or max_calls != MAX_CALLS:
            raise Dedicated20ConfigError("max_calls must be exactly 3")
        max_retries_value = choose(
            "max_retries", (transport, "max_retries"), (value, "max_retries"),
        )
        max_retries = max_retries_value if max_retries_value is not None else MAX_RETRIES
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries != MAX_RETRIES:
            raise Dedicated20ConfigError("max_retries must be exactly 0")
        max_keys_value = choose(
            "max_keys", (transport, "max_keys"), (value, "max_keys"),
        )
        max_keys = max_keys_value if max_keys_value is not None else MAX_KEYS
        if isinstance(max_keys, bool) or not isinstance(max_keys, int) or max_keys != MAX_KEYS:
            raise Dedicated20ConfigError("max_keys must be exactly 1")
        json_mode = choose(
            "json_mode", (transport, "json_mode"), (value, "json_mode"),
        )
        if json_mode is None:
            json_mode = True
        if json_mode is not True:
            raise Dedicated20ConfigError("json_mode must be true")
        max_tokens_value = choose(
            "max_tokens", (transport, "max_tokens"), (value, "max_tokens"),
        )
        max_tokens = max_tokens_value if max_tokens_value is not None else MAX_TOKENS
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens != MAX_TOKENS:
            raise Dedicated20ConfigError("max_tokens must be exactly 7000")
        checkpoint_value = choose(
            "checkpoint_cny", (budget, "checkpoint_cny"),
            (task, "checkpoint_cny"), (value, "checkpoint_cny"),
        )
        if checkpoint_value is not None:
            raise Dedicated20ConfigError("checkpoint_cny must be None")

        ledger_path = Path(ledger_value)
        canonical_ledger = _canonical_path(ledger_path)
        original = _canonical_path(ORIGINAL_LEDGER_PATH)
        if canonical_ledger == original:
            raise Dedicated20ConfigError(
                "dedicated20 ledger resolves to the original 140-CNY ledger"
            )
        # A dedicated DB is a new task-owned file.  Reusing the original path's
        # parent is allowed; only the original database identity is forbidden.
        return cls(
            ledger_path=ledger_path,
            audit_root=Path(audit_value),
            key_file=Path(key_value),
            task_id=task_id,
            model=model,
            repair_model=repair_model,
            task_limit_cny=task_limit,
            global_limit_cny=global_limit,
            max_calls=max_calls,
            checkpoint_cny=None,
            max_retries=max_retries,
            max_keys=max_keys,
            json_mode=True,
            max_tokens=max_tokens,
        )

    def factory_config(self) -> dict[str, Any]:
        """Minimal config accepted by the tested audited-client factory."""

        return {
            "execution_enabled": True,
            "paths": {
                "ledger": str(self.ledger_path),
                "key_file": str(self.key_file),
                "audit_root": str(self.audit_root),
            },
            "task": {"task_id": self.task_id},
        }


def validate_dedicated20_guard(
    guard: Any,
    config: Dedicated20Config,
) -> dict[str, Any]:
    """Validate the already-created native guard without changing its state."""

    if not callable(getattr(guard, "as_dict", None)):
        raise Dedicated20ConfigError("dedicated20 guard must expose as_dict")
    if not getattr(guard, "path", None):
        raise Dedicated20ConfigError("dedicated20 guard must bind a durable ledger")
    guard_path = _canonical_path(guard.path)
    ledger_path = _canonical_path(config.ledger_path)
    if guard_path != ledger_path:
        raise Dedicated20ConfigError("guard and dedicated20 config bind different ledgers")
    if guard_path == _canonical_path(ORIGINAL_LEDGER_PATH):
        raise Dedicated20ConfigError("guard resolves to the original 140-CNY ledger")
    snapshot = guard.as_dict()
    task = snapshot.get("task_budget") or {}
    if snapshot.get("limit_cny") != GLOBAL_LIMIT_CNY:
        raise Dedicated20ConfigError("native ledger limit must be exactly 20 CNY")
    expected = {
        "task_id": config.task_id,
        "limit_cny": TASK_LIMIT_CNY,
        "max_calls": MAX_CALLS,
        "checkpoint_cny": None,
    }
    for field, wanted in expected.items():
        if task.get(field) != wanted:
            raise Dedicated20ConfigError(
                f"guard task_budget.{field} does not match dedicated20"
            )
    return snapshot


def _focused_planner_source(planner_module: ModuleType) -> Path:
    source = _module_path(planner_module, "focused planner")
    normalized = str(source).casefold().replace("/", "\\")
    if not normalized.endswith(
        r"focused_research\vendor\review\optomind_research\query_planner.py"
    ):
        raise Dedicated20ConfigError(
            "planner module is not the exact focused_research vendored query_planner"
        )
    if not callable(getattr(planner_module, "call_qwen_chat", None)):
        raise Dedicated20ConfigError("focused planner has no call_qwen_chat seam")
    return source


def _load_vendored_model_resolver(
    planner_source: Path,
) -> tuple[Callable[[str], str], dict[str, Path]]:
    """Load the focused checkout's model policy without loading credentials."""

    vendor_root = planner_source.parents[1]
    config_root = vendor_root / "config"
    qwen_source = config_root / "qwen_config.py"
    secret_source = config_root / "secret_pool.py"
    if not qwen_source.is_file() or not secret_source.is_file():
        raise Dedicated20ConfigError(
            "focused vendored config.qwen_config source is unavailable"
        )
    qwen_name = "r3_t019_d20_vendor_qwen_config_" + _sha256(qwen_source)[:16]
    secret_name = "r3_t019_d20_vendor_secret_pool_" + _sha256(secret_source)[:16]
    secret_module = _load_module_from_path(secret_source, secret_name)

    # qwen_config imports config.secret_pool at module load time.  Provide a
    # private package binding for that import only; get_model_name reads the
    # vendored policy YAML and never calls the key-loader.
    package = ModuleType("config")
    package.__path__ = [str(config_root)]
    missing = object()
    previous_config = sys.modules.get("config", missing)
    previous_secret = sys.modules.get("config.secret_pool", missing)
    sys.modules["config"] = package
    sys.modules["config.secret_pool"] = secret_module
    try:
        qwen_module = _load_module_from_path(qwen_source, qwen_name)
    finally:
        if previous_config is missing:
            sys.modules.pop("config", None)
        else:
            sys.modules["config"] = previous_config
        if previous_secret is missing:
            sys.modules.pop("config.secret_pool", None)
        else:
            sys.modules["config.secret_pool"] = previous_secret
    resolver = getattr(qwen_module, "get_model_name", None)
    if not callable(resolver):
        raise Dedicated20ConfigError(
            "focused vendored config.qwen_config.get_model_name is unavailable"
        )
    return resolver, {
        "qwen_config": qwen_source,
        "qwen_model_policy": config_root / "model_policy.yaml",
    }


def _model_for_tier(
    model_tier: Any,
    agent_name: Any,
    configured_model: str,
    repair_model: str,
    model_resolver: Callable[[str], str],
) -> str:
    """Resolve only the focused planner's two permitted model identities."""

    tier = str(model_tier or "").strip()
    agent = str(agent_name or "").strip()
    reference = tier or agent
    if not reference:
        raise Dedicated20ConfigError(
            f"focused model tier is outside the dedicated allowlist: {tier or agent}"
        )
    try:
        resolved = str(model_resolver(reference)).strip()
    except Exception as exc:
        raise Dedicated20ConfigError(
            f"focused model policy could not resolve {reference}: {type(exc).__name__}"
        ) from exc
    if resolved not in ALLOWED_MODELS:
        raise Dedicated20ConfigError(
            f"focused model policy resolved outside the dedicated allowlist: {resolved}"
        )
    repair_call = tier in {"standard_model", "QueryPlannerFormatRepairAgent"} \
        or agent in {"QueryPlannerFormatRepairAgent"}
    expected = repair_model if repair_call else configured_model
    if resolved != expected:
        raise Dedicated20ConfigError(
            f"focused model {resolved} conflicts with configured dedicated model {expected}"
        )
    return resolved


def _has_token_usage(usage: Mapping[str, Any]) -> bool:
    return bool(usage) and (
        (usage.get("prompt_tokens") is not None or usage.get("input_tokens") is not None)
        and (
            usage.get("completion_tokens") is not None
            or usage.get("output_tokens") is not None
        )
    )


def _safe_record(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    try:
        return json.loads(json.dumps(dict(value), ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return {"record_type": type(value).__name__}


class FocusedQwenCallAdapter:
    """Callable with the vendored ``call_qwen_chat`` contract."""

    schema = "r3-t019-dedicated20-focused-qwen.v1"

    def __init__(
        self,
        *,
        audited_client_factory: Callable[..., type],
        runtime: ModuleType,
        config: Dedicated20Config,
        guard: Any,
        model_resolver: Callable[[str], str] | None = None,
    ) -> None:
        self._factory = audited_client_factory
        self._runtime = runtime
        self._config = config
        self._guard = guard
        self._model_resolver = model_resolver or (lambda reference: reference)

    def _attempt_count(self) -> int:
        try:
            snapshot = self._guard.as_dict()
            task = snapshot.get("task_budget") or {}
            value = task.get("attempted_calls", 0)
            return int(value) if isinstance(value, int) and value >= 0 else 0
        except Exception:
            return 0

    def _attempt_delta(self, before: int) -> int:
        return max(0, self._attempt_count() - before)

    def _failure(
        self,
        *,
        model: str,
        model_tier: str,
        exc: BaseException,
        attempt_count: int,
    ) -> dict[str, Any]:
        record = _safe_record(getattr(exc, "record", None))
        budget_code = getattr(exc, "code", None)
        reason = (
            str(budget_code or getattr(exc, "reason_code", "") or "").strip()
            or str(record.get("reason_code") or "").strip()
            or type(exc).__name__
        )
        reason = re.sub(r"[^A-Za-z0-9_.:-]", "_", reason)[:120]
        raw_content = record.get("content")
        content = raw_content if isinstance(raw_content, str) else ""
        provider_usage = record.get("usage")
        if not isinstance(provider_usage, Mapping):
            provider_usage = {}
        usage = {
            "module": "R3-T019-DEDICATED20",
            "agent_name": model_tier,
            "model_tier": model_tier,
            "model_name": model,
            "mock_llm": False,
            "success": False,
            "failure": True,
            "error_type": type(exc).__name__,
            "transport_error": reason,
            "budget_error": budget_code or "",
            "provider_usage": dict(provider_usage),
            "token_usage_source": "provider_response" if provider_usage else "unknown",
            "request_attempt_count": attempt_count,
            "reserved_attempt_count": attempt_count,
            "attempt_count_source": "guard.task_budget.attempted_calls_delta",
            "budget_hold_preserved": attempt_count > 0,
            "adapter_schema": self.schema,
        }
        return {
            "content": content,
            "fallback_used": False,
            "transport_error": reason,
            "budget_error": budget_code or "",
            "error_type": type(exc).__name__,
            "transport_record": record,
            "_llm_usage": usage,
            "_r3_transport": {
                "replayed": False,
                "request_key": "",
                "transport_error": reason,
                "budget_error": budget_code or "",
                "request_attempt_count": attempt_count,
                "reserved_attempt_count": attempt_count,
            },
        }

    def _success(
        self,
        result: Mapping[str, Any],
        *,
        model: str,
        model_tier: str,
        attempt_count: int,
    ) -> dict[str, Any]:
        output = dict(result)
        transport = output.get("_r3_transport")
        transport = dict(transport) if isinstance(transport, Mapping) else {}
        replayed = bool(transport.get("replayed"))
        usage = output.get("usage")
        usage = dict(usage) if isinstance(usage, Mapping) else {}
        original_usage = transport.get("original_usage")
        if replayed and isinstance(original_usage, Mapping):
            provider_usage = dict(original_usage)
        else:
            provider_usage = dict(usage)
        accounting_error = ""
        if not replayed and not _has_token_usage(provider_usage):
            # Native settlement has already placed an uncertain hold.  Keep the
            # raw content as evidence but make the planner reject the round;
            # no unknown-cost response can become a task sheet.
            accounting_error = "usage_token_counts_missing"
        cost = None
        if _has_token_usage(provider_usage):
            try:
                cost = self._runtime.estimated_cost_cny(
                    provider_usage, model=model
                )
            except Exception as exc:  # native runtime remains source of truth
                accounting_error = getattr(exc, "reason_code", "usage_cost_failed")
        usage_record = {
            "module": "R3-T019-DEDICATED20",
            "agent_name": model_tier,
            "model_tier": model_tier,
            "model_name": model,
            "mock_llm": False,
            "success": not bool(accounting_error),
            "failure": bool(accounting_error),
            "error_type": "AccountingError" if accounting_error else "",
            "transport_error": accounting_error,
            "provider_usage": provider_usage,
            "input_tokens": provider_usage.get("prompt_tokens", provider_usage.get("input_tokens", 0)),
            "output_tokens": provider_usage.get("completion_tokens", provider_usage.get("output_tokens", 0)),
            "token_usage_source": "replay" if replayed else "provider_response",
            "replayed": replayed,
            "request_attempt_count": attempt_count,
            "reserved_attempt_count": attempt_count,
            "attempt_count_source": "guard.task_budget.attempted_calls_delta",
            "budget_hold_preserved": bool(accounting_error and attempt_count),
            "adapter_schema": self.schema,
        }
        if cost is not None:
            usage_record["cost_cny"] = cost
        output["fallback_used"] = False
        output["transport_error"] = accounting_error
        output["_llm_usage"] = usage_record
        output["_r3_transport"] = transport
        if accounting_error:
            output["accounting_error"] = accounting_error
        return output

    def __call__(
        self,
        agent_name: str,
        messages: Sequence[Mapping[str, Any]],
        *,
        model_tier: str = "standard_model",
        max_retries: int = 2,
        temperature: float = 0.3,
        force_mock: bool | None = None,
        max_tokens: int = 4096,
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        timeout_seconds: float | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del max_retries  # the dedicated contract deliberately forces zero retries
        try:
            model = _model_for_tier(
                model_tier, agent_name, self._config.model, self._config.repair_model,
                self._model_resolver,
            )
            if force_mock is True:
                raise Dedicated20ConfigError("mock calls are forbidden")
            if force_mock not in (None, False):
                raise Dedicated20ConfigError("force_mock must be false")
            repair_call = (
                str(model_tier).strip() in {"standard_model", "QueryPlannerFormatRepairAgent"}
                or str(agent_name).strip() == "QueryPlannerFormatRepairAgent"
            )
            expected_tokens = REPAIR_MAX_TOKENS if repair_call else self._config.max_tokens
            if isinstance(max_tokens, bool) or max_tokens != expected_tokens:
                raise Dedicated20ConfigError(
                    f"max_tokens must remain exactly {expected_tokens} for this focused call"
                )
            if response_format != JSON_RESPONSE_FORMAT:
                raise Dedicated20ConfigError(
                    "response_format must remain {'type': 'json_object'}"
                )
            if stream:
                raise Dedicated20ConfigError("streaming is outside the focused plan contract")
            temp = _finite_number(temperature, "temperature")
            expected_temperature = 0.0 if repair_call else 0.1
            if abs(temp - expected_temperature) > 1e-9:
                raise Dedicated20ConfigError(
                    f"temperature must remain exactly {expected_temperature:g} for this focused call"
                )
            client_kwargs: dict[str, Any] = {
                "model": model,
                "max_output_tokens": expected_tokens,
                "timeout_seconds": timeout_seconds if timeout_seconds is not None else 300.0,
                "json_mode": self._config.json_mode,
                "thinking": False,
                "thinking_budget": 0,
                "max_retries": self._config.max_retries,
                "max_keys": self._config.max_keys,
            }
            # The factory overwrites transport controls with the same values;
            # passing them here makes the intent explicit for source audits.
            before_attempts = self._attempt_count()
            client = self._factory(**client_kwargs)
            result = client(
                messages,
                model=model,
                max_output_tokens=expected_tokens,
                temperature=temp,
                stream=False,
                thinking=False,
                thinking_budget=0,
                call_id="t019-d20-" + uuid.uuid4().hex[:20],
            )
            if not isinstance(result, Mapping):
                raise RuntimeError("native Qwen client returned a non-mapping result")
            return self._success(
                result,
                model=model,
                model_tier=str(model_tier),
                attempt_count=self._attempt_delta(before_attempts),
            )
        except Dedicated20ConfigError:
            raise
        except (Exception,) as exc:
            # Preserve budget/native failure identity in the focused planner's
            # existing transport fields; never synthesize model content.
            configured_model = self._config.model
            try:
                configured_model = _model_for_tier(
                    model_tier, agent_name, self._config.model, self._config.repair_model,
                    self._model_resolver,
                )
            except Exception:
                pass
            return self._failure(
                model=configured_model,
                model_tier=str(model_tier),
                exc=exc,
                attempt_count=self._attempt_delta(locals().get("before_attempts", 0)),
            )


@dataclass
class InstallationReceipt:
    """State returned by ``install_focused_transport``."""

    adapter: FocusedQwenCallAdapter
    source_origins: dict[str, Any]
    guard_snapshot: dict[str, Any]
    previous_callable: Callable[..., Any]

    @property
    def call_qwen_chat(self) -> FocusedQwenCallAdapter:
        return self.adapter

    def current_guard_snapshot(self) -> dict[str, Any]:
        return self.adapter._guard.as_dict()


@contextmanager
def install_focused_transport(
    planner_module: ModuleType,
    runtime: ModuleType | str | os.PathLike[str],
    guard: Any,
    config: Dedicated20Config | Mapping[str, Any],
    *,
    budget_entry_path: str | os.PathLike[str] = DEFAULT_BUDGET_ENTRY,
    task_guard_path: str | os.PathLike[str] = DEFAULT_TASK_GUARD,
) -> Iterator[InstallationReceipt]:
    """Install the dedicated callable into the exact focused planner module.

    The context restores the original callable on exit.  It does not alter
    ``sys.path`` and does not initialize or authorize a ledger.
    """

    focused_source = _focused_planner_source(planner_module)
    model_resolver, model_sources = _load_vendored_model_resolver(focused_source)
    native_runtime = load_native_runtime(runtime)
    native_source = _module_path(native_runtime, "native runtime")
    dedicated = (
        config
        if isinstance(config, Dedicated20Config)
        else Dedicated20Config.from_mapping(config)
    )
    if _canonical_path(dedicated.ledger_path) == _canonical_path(ORIGINAL_LEDGER_PATH):
        raise Dedicated20ConfigError("original ledger path is forbidden")
    guard_snapshot = validate_dedicated20_guard(guard, dedicated)
    budget_entry = Path(budget_entry_path).resolve(strict=True)
    task_guard = Path(task_guard_path).resolve(strict=True)
    if budget_entry.name != "budgeted_entry.py" or task_guard.name != "task_budget_guard.py":
        raise Dedicated20ConfigError("unexpected budget helper source")
    factory_module, guard_module = _load_budget_factory(budget_entry, task_guard)
    audited_client_factory = factory_module.make_audited_client(
        native_runtime, guard, dedicated.factory_config()
    )
    adapter = FocusedQwenCallAdapter(
        audited_client_factory=audited_client_factory,
        runtime=native_runtime,
        config=dedicated,
        guard=guard,
        model_resolver=model_resolver,
    )
    previous = planner_module.call_qwen_chat
    planner_module.call_qwen_chat = adapter
    origins: dict[str, Any] = {
        "focused_query_planner": _origin(focused_source),
        "native_runtime": _origin(native_source),
        "budgeted_entry": _origin(budget_entry),
        "task_budget_guard": _origin(task_guard),
        "focused_provenance": _origin(focused_source.parents[3] / "PROVENANCE.json"),
        "vendored_model_policy": {
            key: _origin(path) for key, path in model_sources.items()
        },
        "module_names": {
            "native_runtime": native_runtime.__name__,
            "budgeted_entry": factory_module.__name__,
            "task_budget_guard": guard_module.__name__,
        },
        "adapter_schema": FocusedQwenCallAdapter.schema,
    }
    receipt = InstallationReceipt(
        adapter=adapter,
        source_origins=origins,
        guard_snapshot=guard_snapshot,
        previous_callable=previous,
    )
    try:
        yield receipt
    finally:
        planner_module.call_qwen_chat = previous


def run_plan_only(run_main: Any, argv: Sequence[str]) -> Any:
    """Invoke the original focused runner only for its ``plan`` stage."""

    args = list(argv)
    if not args or args[0] != "plan":
        raise Dedicated20ConfigError(
            "dedicated20 focused transport requires the focused positional plan stage"
        )
    callable_main = getattr(run_main, "main", run_main)
    if not callable(callable_main):
        raise Dedicated20ConfigError("focused runner main is not callable")
    return callable_main(args)


__all__ = [
    "ALLOWED_MODELS",
    "CHECKPOINT_CNY",
    "Dedicated20Config",
    "Dedicated20ConfigError",
    "FocusedQwenCallAdapter",
    "GLOBAL_LIMIT_CNY",
    "InstallationReceipt",
    "MAX_CALLS",
    "MAX_KEYS",
    "MAX_RETRIES",
    "MAX_TOKENS",
    "REPAIR_MAX_TOKENS",
    "ORIGINAL_LEDGER_PATH",
    "TASK_ID",
    "TASK_LIMIT_CNY",
    "install_focused_transport",
    "load_native_runtime",
    "run_plan_only",
    "validate_dedicated20_guard",
]
