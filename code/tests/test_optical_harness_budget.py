from __future__ import annotations

import json
import threading
from dataclasses import FrozenInstanceError

import pytest

from optomind_optics.harness.budget import (
    BudgetLimits,
    BudgetOversubscriptionError,
    BudgetScheduler,
    BudgetStateError,
    BudgetValidationError,
    DuplicateActionError,
)


class FakeClock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_limits_are_immutable_and_reject_unknown_fields() -> None:
    limits = BudgetLimits(
        wall_time_seconds=10.0,
        forward_evaluations=4,
        optimizer_runs=2,
        qwen_calls=1,
        qwen_input_tokens=100,
        qwen_output_tokens=50,
        qwen_cost_cny=0.5,
    )
    with pytest.raises(FrozenInstanceError):
        limits.forward_evaluations = 5  # type: ignore[misc]
    with pytest.raises(TypeError):
        BudgetLimits(**{"performance_metric": 0.1})

    scheduler = BudgetScheduler(limits)
    with pytest.raises(BudgetValidationError):
        scheduler.reserve("bad-field", {"performance_metric": 0.1})


def test_concurrent_like_reservations_are_atomic() -> None:
    scheduler = BudgetScheduler(BudgetLimits(forward_evaluations=1))
    barrier = threading.Barrier(3)
    results: list[str] = []
    errors: list[Exception] = []

    def attempt(action_id: str) -> None:
        barrier.wait()
        try:
            scheduler.reserve(action_id, forward_evaluations=1)
            results.append(action_id)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [
        threading.Thread(target=attempt, args=("action-a",)),
        threading.Thread(target=attempt, args=("action-b",)),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert len(results) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], BudgetOversubscriptionError)
    assert scheduler.snapshot()["reserved"]["forward_evaluations"] == 1


def test_duplicate_action_ids_and_double_lifecycle_operations_are_rejected() -> None:
    scheduler = BudgetScheduler(BudgetLimits(forward_evaluations=3))
    scheduler.reserve("same", forward_evaluations=1)
    with pytest.raises(DuplicateActionError):
        scheduler.reserve("same", forward_evaluations=1)

    scheduler.commit("same", forward_evaluations=1)
    with pytest.raises(BudgetStateError):
        scheduler.commit("same", forward_evaluations=1)
    with pytest.raises(BudgetStateError):
        scheduler.release("same")
    with pytest.raises(DuplicateActionError):
        scheduler.reserve("same", forward_evaluations=1)


def test_release_rolls_back_a_reservation_without_usage() -> None:
    scheduler = BudgetScheduler(BudgetLimits(forward_evaluations=3))
    scheduler.reserve("rollback", forward_evaluations=3)
    assert scheduler.remaining("forward_evaluations") == 0
    scheduler.release("rollback")
    assert scheduler.remaining("forward_evaluations") == 3
    scheduler.reserve("replacement", forward_evaluations=2)
    snapshot = scheduler.snapshot()
    assert snapshot["usage"]["forward_evaluations"] == 0
    assert snapshot["reserved"]["forward_evaluations"] == 2
    assert [event["event_type"] for event in snapshot["events"]] == ["reserve", "release", "reserve"]


def test_actual_overrun_is_recorded_without_rewriting_the_reservation() -> None:
    scheduler = BudgetScheduler(BudgetLimits(forward_evaluations=4))
    scheduler.reserve("overshoot", forward_evaluations=1)
    scheduler.commit("overshoot", forward_evaluations=5)

    snapshot = scheduler.snapshot()
    assert snapshot["usage"]["forward_evaluations"] == 5
    assert snapshot["overrun"] is True
    assert snapshot["exhausted"] is True
    assert snapshot["overruns"]["forward_evaluations"] == 1
    assert snapshot["events"][0]["reserved_usage"]["forward_evaluations"] == 1
    assert snapshot["events"][1]["actual_usage"]["forward_evaluations"] == 5
    assert scheduler.can_reserve("after-overrun", forward_evaluations=1) is False


def test_wall_time_uses_scheduler_start_and_checkpoint_restarts(tmp_path) -> None:
    clock = FakeClock(100.0)
    checkpoint = tmp_path / "budget.json"
    limits = BudgetLimits(wall_time_seconds=10.0, forward_evaluations=3)
    scheduler = BudgetScheduler(limits, checkpoint_path=checkpoint, clock=clock)
    clock.advance(4.0)
    scheduler.reserve("saved", forward_evaluations=2)
    scheduler.commit("saved", forward_evaluations=2)

    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert payload["elapsed_wall_time_seconds"] == 4.0
    restarted = BudgetScheduler(limits, checkpoint_path=checkpoint, clock=clock)
    assert restarted.snapshot()["elapsed_wall_time_seconds"] == 4.0
    assert restarted.snapshot()["usage"]["forward_evaluations"] == 2

    clock.advance(6.0)
    assert restarted.snapshot()["exhausted"] is True
    assert restarted.can_reserve("too-late", forward_evaluations=1) is False
