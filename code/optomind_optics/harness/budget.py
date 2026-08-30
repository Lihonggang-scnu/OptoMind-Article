"""Deterministic operational budgets for the optical experiment harness.

This module only accounts for execution resources.  It does not inspect a
task, a solver, an optimizer implementation, or an experiment result.
"""

from __future__ import annotations

import copy
import json
import math
import threading
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from os import PathLike
from pathlib import Path
from typing import Any, Callable, ClassVar, Mapping


_RESOURCE_NAMES = (
    "wall_time_seconds",
    "forward_evaluations",
    "optimizer_runs",
    "qwen_calls",
    "qwen_input_tokens",
    "qwen_output_tokens",
    "qwen_cost_cny",
)
_COUNT_RESOURCES = frozenset(
    {
        "forward_evaluations",
        "optimizer_runs",
        "qwen_calls",
        "qwen_input_tokens",
        "qwen_output_tokens",
    }
)
_DECIMAL_RESOURCES = frozenset({"wall_time_seconds", "qwen_cost_cny"})

_RESOURCE_ALIASES = {
    "wall_time_seconds": (
        "wall_seconds",
        "wall_time_s",
        "wall_time_limit_seconds",
        "max_wall_time_seconds",
        "maximum_wall_time_seconds",
        "max_wall_seconds",
        "maximum_wall_seconds",
    ),
    "forward_evaluations": (
        "max_forward_evaluations",
        "maximum_forward_evaluations",
        "forward_evaluation_limit",
    ),
    "optimizer_runs": (
        "max_optimizer_runs",
        "maximum_optimizer_runs",
        "optimizer_run_limit",
    ),
    "qwen_calls": (
        "max_qwen_calls",
        "maximum_qwen_calls",
        "qwen_call_limit",
    ),
    "qwen_input_tokens": (
        "max_qwen_input_tokens",
        "maximum_qwen_input_tokens",
        "qwen_input_token_limit",
    ),
    "qwen_output_tokens": (
        "max_qwen_output_tokens",
        "maximum_qwen_output_tokens",
        "qwen_output_token_limit",
    ),
    "qwen_cost_cny": (
        "max_qwen_cost_cny",
        "maximum_qwen_cost_cny",
        "qwen_cost_limit_cny",
    ),
}
_ALIAS_TO_RESOURCE = {
    resource: resource
    for resource in _RESOURCE_NAMES
}
for _resource, _aliases in _RESOURCE_ALIASES.items():
    _ALIAS_TO_RESOURCE.update({alias: _resource for alias in _aliases})


class BudgetError(ValueError):
    """Base class for invalid budget operations."""


class BudgetValidationError(BudgetError):
    """Raised when a limit, resource name, or amount is invalid."""


class BudgetOversubscriptionError(BudgetError):
    """Raised when a reservation would exceed an available limit."""


class BudgetStateError(BudgetError):
    """Raised when an action lifecycle operation is not valid."""


class DuplicateActionError(BudgetStateError):
    """Raised when an action identifier has already been used."""


def _canonical_resource(name: str) -> str:
    if not isinstance(name, str):
        raise BudgetValidationError("budget resource names must be strings")
    try:
        return _ALIAS_TO_RESOURCE[name]
    except KeyError as exc:
        raise BudgetValidationError(f"unknown budget resource: {name!r}") from exc


def _validate_limit(resource: str, value: Any) -> int | float | None:
    if value is None:
        return None
    if resource in _COUNT_RESOURCES:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{resource} must be a positive integer or None")
        if value <= 0:
            raise ValueError(f"{resource} must be positive or None")
        return value

    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise TypeError(f"{resource} must be a positive finite number or None")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise TypeError(f"{resource} must be a positive finite number or None") from exc
    if not number.is_finite() or number <= 0:
        raise ValueError(f"{resource} must be positive and finite or None")
    normalized = float(number)
    if not math.isfinite(normalized):
        raise ValueError(f"{resource} must be positive and finite or None")
    return normalized


@dataclass(frozen=True, slots=True, init=False)
class BudgetLimits:
    """Immutable optional positive limits for operational resources.

    ``None`` means that a resource is not capped.  The constructor accepts a
    small set of compatibility aliases such as ``wall_seconds`` and
    ``maximum_forward_evaluations``; unknown fields are rejected.
    """

    wall_time_seconds: float | None = None
    forward_evaluations: int | None = None
    optimizer_runs: int | None = None
    qwen_calls: int | None = None
    qwen_input_tokens: int | None = None
    qwen_output_tokens: int | None = None
    qwen_cost_cny: float | None = None

    _RESOURCE_NAMES: ClassVar[tuple[str, ...]] = _RESOURCE_NAMES

    def __init__(
        self,
        wall_time_seconds: float | None = None,
        forward_evaluations: int | None = None,
        optimizer_runs: int | None = None,
        qwen_calls: int | None = None,
        qwen_input_tokens: int | None = None,
        qwen_output_tokens: int | None = None,
        qwen_cost_cny: float | None = None,
        **aliases: Any,
    ) -> None:
        values: dict[str, Any] = {
            "wall_time_seconds": wall_time_seconds,
            "forward_evaluations": forward_evaluations,
            "optimizer_runs": optimizer_runs,
            "qwen_calls": qwen_calls,
            "qwen_input_tokens": qwen_input_tokens,
            "qwen_output_tokens": qwen_output_tokens,
            "qwen_cost_cny": qwen_cost_cny,
        }
        for alias, value in aliases.items():
            resource = _ALIAS_TO_RESOURCE.get(alias)
            if resource is None:
                raise TypeError(f"unexpected budget limit field: {alias!r}")
            if values[resource] is not None:
                raise TypeError(f"duplicate budget limit fields for {resource!r}")
            values[resource] = value

        for resource in _RESOURCE_NAMES:
            object.__setattr__(self, resource, _validate_limit(resource, values[resource]))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BudgetLimits":
        if not isinstance(payload, Mapping):
            raise BudgetValidationError("budget limits checkpoint value must be an object")
        return cls(**dict(payload))

    def to_dict(self) -> dict[str, int | float | None]:
        return {resource: getattr(self, resource) for resource in _RESOURCE_NAMES}

    @property
    def wall_seconds(self) -> float | None:
        return self.wall_time_seconds

    @property
    def max_wall_time_seconds(self) -> float | None:
        return self.wall_time_seconds

    @property
    def maximum_wall_time_seconds(self) -> float | None:
        return self.wall_time_seconds

    @property
    def max_forward_evaluations(self) -> int | None:
        return self.forward_evaluations

    @property
    def maximum_forward_evaluations(self) -> int | None:
        return self.forward_evaluations

    @property
    def max_optimizer_runs(self) -> int | None:
        return self.optimizer_runs

    @property
    def maximum_optimizer_runs(self) -> int | None:
        return self.optimizer_runs

    @property
    def max_qwen_calls(self) -> int | None:
        return self.qwen_calls

    @property
    def maximum_qwen_calls(self) -> int | None:
        return self.qwen_calls

    @property
    def max_qwen_input_tokens(self) -> int | None:
        return self.qwen_input_tokens

    @property
    def maximum_qwen_input_tokens(self) -> int | None:
        return self.qwen_input_tokens

    @property
    def max_qwen_output_tokens(self) -> int | None:
        return self.qwen_output_tokens

    @property
    def maximum_qwen_output_tokens(self) -> int | None:
        return self.qwen_output_tokens

    @property
    def max_qwen_cost_cny(self) -> float | None:
        return self.qwen_cost_cny

    @property
    def maximum_qwen_cost_cny(self) -> float | None:
        return self.qwen_cost_cny


def _zero_amount(resource: str) -> int | Decimal:
    return Decimal("0") if resource in _DECIMAL_RESOURCES else 0


def _normalize_amount(resource: str, value: Any) -> int | Decimal:
    if isinstance(value, bool):
        raise TypeError(f"{resource} amount must be non-negative")
    if resource in _COUNT_RESOURCES:
        if not isinstance(value, int):
            raise TypeError(f"{resource} amount must be a non-negative integer")
        if value < 0:
            raise ValueError(f"{resource} amount must be non-negative")
        return value

    if not isinstance(value, (int, float, Decimal)):
        raise TypeError(f"{resource} amount must be a non-negative finite number")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise TypeError(f"{resource} amount must be a non-negative finite number") from exc
    if not number.is_finite() or number < 0:
        raise ValueError(f"{resource} amount must be non-negative and finite")
    return number


def _normalize_usage(payload: Mapping[str, Any] | None) -> dict[str, int | Decimal]:
    if payload is None:
        payload = {}
    if not isinstance(payload, Mapping):
        raise TypeError("budget usage must be a mapping")

    normalized = {resource: _zero_amount(resource) for resource in _RESOURCE_NAMES}
    seen: set[str] = set()
    for raw_name, raw_value in payload.items():
        resource = _canonical_resource(raw_name)
        if resource in seen:
            raise BudgetValidationError(f"duplicate budget resource: {resource!r}")
        seen.add(resource)
        normalized[resource] = _normalize_amount(resource, raw_value)
    return normalized


def _copy_vector(vector: Mapping[str, int | Decimal]) -> dict[str, int | Decimal]:
    return {resource: vector[resource] for resource in _RESOURCE_NAMES}


def _add_vector(
    destination: dict[str, int | Decimal],
    source: Mapping[str, int | Decimal],
) -> None:
    for resource in _RESOURCE_NAMES:
        destination[resource] += source[resource]


def _subtract_vector(
    destination: dict[str, int | Decimal],
    source: Mapping[str, int | Decimal],
) -> None:
    for resource in _RESOURCE_NAMES:
        destination[resource] -= source[resource]


def _json_number(value: int | Decimal) -> int | float:
    if isinstance(value, Decimal):
        return float(value)
    return value


def _json_vector(vector: Mapping[str, int | Decimal]) -> dict[str, int | float]:
    return {resource: _json_number(vector[resource]) for resource in _RESOURCE_NAMES}


@dataclass(slots=True)
class _ActionState:
    status: str
    reserved_usage: dict[str, int | Decimal]
    actual_usage: dict[str, int | Decimal] | None = None


class BudgetScheduler:
    """Thread-safe reservation ledger for bounded operational resources."""

    CHECKPOINT_SCHEMA_VERSION: ClassVar[int] = 1

    def __init__(
        self,
        limits: BudgetLimits | Mapping[str, Any] | None = None,
        *,
        checkpoint_path: str | PathLike[str] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._clock = clock or time.monotonic
        if not callable(self._clock):
            raise TypeError("clock must be callable")
        self._checkpoint_path = Path(checkpoint_path) if checkpoint_path is not None else None
        self._events: list[dict[str, Any]] = []
        self._actions: dict[str, _ActionState] = {}
        self._committed_usage = {
            resource: _zero_amount(resource) for resource in _RESOURCE_NAMES
        }
        self._reserved_usage = {
            resource: _zero_amount(resource) for resource in _RESOURCE_NAMES
        }

        now = self._read_clock()
        checkpoint = self._read_checkpoint() if self._checkpoint_path and self._checkpoint_path.exists() else None
        checkpoint_limits: BudgetLimits | None = None
        if checkpoint is not None:
            checkpoint_limits = BudgetLimits.from_dict(checkpoint["limits"])

        if limits is None:
            if checkpoint_limits is None:
                raise TypeError("limits are required when no checkpoint is available")
            self._limits = checkpoint_limits
        elif isinstance(limits, BudgetLimits):
            self._limits = limits
        elif isinstance(limits, Mapping):
            self._limits = BudgetLimits(**dict(limits))
        else:
            raise TypeError("limits must be BudgetLimits, a mapping, or None")

        if checkpoint_limits is not None and checkpoint_limits != self._limits:
            raise BudgetValidationError("checkpoint limits do not match supplied limits")

        if checkpoint is None:
            self._started_at_monotonic = now
            self._write_checkpoint_unlocked()
        else:
            elapsed = checkpoint.get("elapsed_wall_time_seconds", 0.0)
            if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)):
                raise BudgetValidationError("checkpoint elapsed wall time must be numeric")
            if not math.isfinite(float(elapsed)) or float(elapsed) < 0:
                raise BudgetValidationError("checkpoint elapsed wall time must be finite and non-negative")
            self._started_at_monotonic = now - float(elapsed)
            self._restore_events(checkpoint.get("events"))

    @property
    def limits(self) -> BudgetLimits:
        return self._limits

    @property
    def checkpoint_path(self) -> Path | None:
        return self._checkpoint_path

    def can_reserve(
        self,
        action_id: str,
        usage: Mapping[str, Any] | None = None,
        **amounts: Any,
    ) -> bool:
        """Return whether a new action can be admitted atomically."""

        self._validate_action_id(action_id)
        requested = self._usage_argument(usage, amounts)
        with self._lock:
            if action_id in self._actions:
                return False
            return self._can_reserve_unlocked(requested, self._elapsed_unlocked())

    def reserve(
        self,
        action_id: str,
        usage: Mapping[str, Any] | None = None,
        **amounts: Any,
    ) -> bool:
        """Reserve resources for a unique action identifier."""

        self._validate_action_id(action_id)
        requested = self._usage_argument(usage, amounts)
        with self._lock:
            if action_id in self._actions:
                raise DuplicateActionError(f"action_id already exists: {action_id!r}")
            elapsed = self._elapsed_unlocked()
            if not self._can_reserve_unlocked(requested, elapsed):
                raise BudgetOversubscriptionError("reservation exceeds available budget")

            event = self._new_event_unlocked(
                "reserve",
                action_id,
                reserved_usage=requested,
                usage=requested,
            )
            self._events.append(event)
            self._actions[action_id] = _ActionState(
                status="reserved",
                reserved_usage=_copy_vector(requested),
            )
            _add_vector(self._reserved_usage, requested)
            self._write_checkpoint_unlocked()
            return True

    def commit(
        self,
        action_id: str,
        usage: Mapping[str, Any] | None = None,
        **amounts: Any,
    ) -> bool:
        """Commit honest actual usage for a reserved action.

        Actual usage is deliberately not clipped to the reservation.  A
        completed action can therefore make the scheduler report an overrun.
        """

        self._validate_action_id(action_id)
        supplied = usage is not None or bool(amounts)
        actual = self._usage_argument(usage, amounts) if supplied else None
        with self._lock:
            state = self._require_reserved_unlocked(action_id)
            if actual is None:
                actual = _copy_vector(state.reserved_usage)
            event = self._new_event_unlocked(
                "commit",
                action_id,
                reserved_usage=state.reserved_usage,
                actual_usage=actual,
                usage=actual,
            )
            self._events.append(event)
            _subtract_vector(self._reserved_usage, state.reserved_usage)
            _add_vector(self._committed_usage, actual)
            state.status = "committed"
            state.actual_usage = _copy_vector(actual)
            self._write_checkpoint_unlocked()
            return True

    def release(self, action_id: str) -> bool:
        """Release an outstanding reservation without recording usage."""

        self._validate_action_id(action_id)
        with self._lock:
            state = self._require_reserved_unlocked(action_id)
            event = self._new_event_unlocked(
                "release",
                action_id,
                reserved_usage=state.reserved_usage,
                released_usage=state.reserved_usage,
                usage=state.reserved_usage,
            )
            self._events.append(event)
            _subtract_vector(self._reserved_usage, state.reserved_usage)
            state.status = "released"
            self._write_checkpoint_unlocked()
            return True

    def remaining(self, resource: str | None = None) -> dict[str, int | float | None] | int | float | None:
        """Return available amounts, including outstanding reservations.

        Passing a resource name returns only that value.  With no argument a
        complete resource mapping is returned; ``None`` denotes no cap.
        Negative finite values expose an actual overrun honestly.
        """

        with self._lock:
            result = self._remaining_unlocked(self._elapsed_unlocked())
            if resource is None:
                return result
            return result[_canonical_resource(resource)]

    def snapshot(self) -> dict[str, Any]:
        """Return a detached, JSON-compatible view of scheduler state."""

        with self._lock:
            return self._snapshot_unlocked()

    def _usage_argument(
        self,
        usage: Mapping[str, Any] | None,
        amounts: Mapping[str, Any],
    ) -> dict[str, int | Decimal]:
        if usage is not None and amounts:
            raise TypeError("provide budget usage either as a mapping or keyword amounts")
        return _normalize_usage(usage if usage is not None else amounts)

    @staticmethod
    def _validate_action_id(action_id: str) -> None:
        if not isinstance(action_id, str) or not action_id.strip():
            raise BudgetValidationError("action_id must be a non-empty string")

    def _read_clock(self) -> float:
        try:
            value = float(self._clock())
        except (TypeError, ValueError) as exc:
            raise BudgetValidationError("clock must return a finite number") from exc
        if not math.isfinite(value):
            raise BudgetValidationError("clock must return a finite number")
        return value

    def _elapsed_unlocked(self) -> float:
        return max(0.0, self._read_clock() - self._started_at_monotonic)

    def _limit_value(self, resource: str) -> int | Decimal | None:
        limit = getattr(self._limits, resource)
        if limit is None:
            return None
        if resource in _DECIMAL_RESOURCES:
            return Decimal(str(limit))
        return limit

    def _can_reserve_unlocked(
        self,
        requested: Mapping[str, int | Decimal],
        elapsed: float,
    ) -> bool:
        for resource in _RESOURCE_NAMES:
            limit = self._limit_value(resource)
            if limit is None:
                continue
            consumed = Decimal(str(elapsed)) if resource == "wall_time_seconds" else self._committed_usage[resource]
            if resource == "wall_time_seconds" and consumed >= limit:
                return False
            projected = consumed + self._reserved_usage[resource] + requested[resource]
            if projected > limit:
                return False
        return True

    def _require_reserved_unlocked(self, action_id: str) -> _ActionState:
        try:
            state = self._actions[action_id]
        except KeyError as exc:
            raise BudgetStateError(f"unknown action_id: {action_id!r}") from exc
        if state.status != "reserved":
            raise BudgetStateError(
                f"action_id {action_id!r} is already {state.status}; expected reserved"
            )
        return state

    def _new_event_unlocked(
        self,
        event_type: str,
        action_id: str,
        **payload: Any,
    ) -> dict[str, Any]:
        elapsed = self._elapsed_unlocked()
        event: dict[str, Any] = {
            "sequence": len(self._events) + 1,
            "event_id": f"event-{len(self._events) + 1:08d}",
            "event_type": event_type,
            "type": event_type,
            "action_id": action_id,
            "elapsed_wall_time_seconds": elapsed,
        }
        for key, value in payload.items():
            if isinstance(value, Mapping) and set(value) == set(_RESOURCE_NAMES):
                event[key] = _json_vector(value)
            else:
                event[key] = copy.deepcopy(value)
        return event

    def _remaining_unlocked(self, elapsed: float) -> dict[str, int | float | None]:
        result: dict[str, int | float | None] = {}
        for resource in _RESOURCE_NAMES:
            limit = self._limit_value(resource)
            if limit is None:
                result[resource] = None
                continue
            consumed = Decimal(str(elapsed)) if resource == "wall_time_seconds" else self._committed_usage[resource]
            result[resource] = _json_number(limit - consumed - self._reserved_usage[resource])
        return result

    def _snapshot_unlocked(self) -> dict[str, Any]:
        elapsed = self._elapsed_unlocked()
        remaining = self._remaining_unlocked(elapsed)
        measured_usage: dict[str, int | float] = _json_vector(self._committed_usage)
        measured_usage["wall_time_seconds"] = elapsed
        overruns: dict[str, int | float] = {}
        for resource in _RESOURCE_NAMES:
            limit = self._limit_value(resource)
            if limit is None:
                continue
            consumed = Decimal(str(elapsed)) if resource == "wall_time_seconds" else self._committed_usage[resource]
            if consumed > limit:
                overruns[resource] = _json_number(consumed - limit)

        exhausted = bool(overruns) or any(
            value is not None and value <= 0 for value in remaining.values()
        )
        events = copy.deepcopy(self._events)
        actions = {
            action_id: {
                "status": state.status,
                "reserved_usage": _json_vector(state.reserved_usage),
                "actual_usage": (
                    _json_vector(state.actual_usage) if state.actual_usage is not None else None
                ),
            }
            for action_id, state in self._actions.items()
        }
        active_reservations = {
            action_id: _json_vector(state.reserved_usage)
            for action_id, state in self._actions.items()
            if state.status == "reserved"
        }
        return {
            "schema_version": self.CHECKPOINT_SCHEMA_VERSION,
            "limits": self._limits.to_dict(),
            "started_at_monotonic": self._started_at_monotonic,
            "elapsed_wall_time_seconds": elapsed,
            "usage": _json_vector(self._committed_usage),
            "actual_usage": _json_vector(self._committed_usage),
            "committed": _json_vector(self._committed_usage),
            "measured_usage": measured_usage,
            "reserved": _json_vector(self._reserved_usage),
            "remaining": remaining,
            "active_reservations": active_reservations,
            "actions": actions,
            "events": events,
            "history": copy.deepcopy(events),
            "event_count": len(events),
            "overrun": bool(overruns),
            "overruns": overruns,
            "exhausted": exhausted,
            "status": "overrun" if overruns else ("exhausted" if exhausted else "running"),
        }

    def _write_checkpoint_unlocked(self) -> None:
        if self._checkpoint_path is None:
            return
        payload = {
            "schema_version": self.CHECKPOINT_SCHEMA_VERSION,
            "limits": self._limits.to_dict(),
            "elapsed_wall_time_seconds": self._elapsed_unlocked(),
            "events": copy.deepcopy(self._events),
        }
        from optomind_research.runtime.artifact_store import atomic_write_json

        atomic_write_json(self._checkpoint_path, payload)

    def _read_checkpoint(self) -> Mapping[str, Any]:
        assert self._checkpoint_path is not None
        try:
            payload = json.loads(self._checkpoint_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise BudgetValidationError(f"could not read budget checkpoint: {self._checkpoint_path}") from exc
        if not isinstance(payload, Mapping):
            raise BudgetValidationError("budget checkpoint must be a JSON object")
        if payload.get("schema_version") != self.CHECKPOINT_SCHEMA_VERSION:
            raise BudgetValidationError("unsupported budget checkpoint schema")
        if "limits" not in payload:
            raise BudgetValidationError("budget checkpoint has no limits")
        return payload

    def _restore_events(self, raw_events: Any) -> None:
        if not isinstance(raw_events, list):
            raise BudgetValidationError("budget checkpoint events must be a list")
        for raw_event in raw_events:
            if not isinstance(raw_event, Mapping):
                raise BudgetValidationError("budget checkpoint event must be an object")
            event = copy.deepcopy(dict(raw_event))
            expected_sequence = len(self._events) + 1
            if event.get("sequence") != expected_sequence:
                raise BudgetValidationError("budget checkpoint event sequence is not append-only")
            event_type = event.get("event_type", event.get("type"))
            action_id = event.get("action_id")
            self._validate_action_id(action_id)

            if event_type == "reserve":
                if action_id in self._actions:
                    raise BudgetValidationError("budget checkpoint reuses an action_id")
                usage = _normalize_usage(event.get("reserved_usage", event.get("usage")))
                self._actions[action_id] = _ActionState(
                    status="reserved",
                    reserved_usage=_copy_vector(usage),
                )
                _add_vector(self._reserved_usage, usage)
            elif event_type == "commit":
                state = self._require_reserved_unlocked(action_id)
                actual = _normalize_usage(event.get("actual_usage", event.get("usage")))
                _subtract_vector(self._reserved_usage, state.reserved_usage)
                _add_vector(self._committed_usage, actual)
                state.status = "committed"
                state.actual_usage = _copy_vector(actual)
            elif event_type == "release":
                state = self._require_reserved_unlocked(action_id)
                _subtract_vector(self._reserved_usage, state.reserved_usage)
                state.status = "released"
            else:
                raise BudgetValidationError(f"unknown budget checkpoint event type: {event_type!r}")
            self._events.append(event)


__all__ = [
    "BudgetError",
    "BudgetLimits",
    "BudgetOversubscriptionError",
    "BudgetScheduler",
    "BudgetStateError",
    "BudgetValidationError",
    "DuplicateActionError",
]
