"""R-08: process-pool batch execution tests (VeriTMM + Phase 1b).

Windows-spawn rule from the work order: monkeypatching does NOT cross the
process boundary, so behavioral matrix tests inject module-level fake
functions through batch_run(_worker_fn=...). Tests that exercise the POOL
itself (bypass, broken-pool degrade, worker cap, BLAS pinning) use real
child processes with fast-failing payloads.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from concurrent.futures import BrokenExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from optomind_optics.harness.veritmm_adapter import (
    CERTIFICATE_FILENAME,
    MAX_POOL_WORKERS,
    _batch_pool_initializer,
    _normalize_certificate_result,
    batch_run,
)

def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

_seed_mod = _load("r08_seed_tests", Path("tests") / "harness" / "test_portfolio_seeding.py")
_refl_mod = _load("r08_refl_tests", Path("tests") / "harness" / "test_route_reflection.py")
_orch_mod = _load("r08_orch_tests", Path("tests") / "test_tmm_research_orchestrator.py")

from optomind_optics.harness.research_orchestrator import (
    TMMResearchHarness,
    TMMResearchHarnessConfig,
)


# ------------------------- module-level fake workers -----------------------

def fake_ok_worker(job):
    mode, payload_json, out = job
    i = json.loads(payload_json)["i"]
    return {
        "ok": True,
        "certified": bool(i % 2 == 0),
        "tightest_margin": 0.25 * (i + 1),
        "cpu_seconds": 0.01,
        "wall_seconds": 0.02,
        "certificate_path": str(Path(out) / CERTIFICATE_FILENAME),
        "raw_outputs": {CERTIFICATE_FILENAME: {"accepted": bool(i % 2 == 0)}, "i": i},
    }


def fake_slow_cpu_worker(job):
    started = time.process_time()
    target = float(json.loads(job[1])["burn"])
    while time.process_time() - started < target:
        pass
    return {
        "ok": True,
        "certified": True,
        "tightest_margin": 0.1,
        "cpu_seconds": time.process_time() - started,
        "wall_seconds": target,
        "certificate_path": str(Path(job[2]) / CERTIFICATE_FILENAME),
        "raw_outputs": {CERTIFICATE_FILENAME: {"accepted": True}},
    }


def fake_mixed_worker(job):
    mode, payload_json, out = job
    kind = json.loads(payload_json)["kind"]
    if kind == "engine_error":
        raise RuntimeError("synthetic engine crash")
    accepted = kind == "certified"
    return {
        "ok": True,
        "certified": accepted,
        "tightest_margin": 0.5 if accepted else -0.25,
        "cpu_seconds": 0.001,
        "wall_seconds": 0.002,
        "certificate_path": str(Path(out) / CERTIFICATE_FILENAME),
        "raw_outputs": {CERTIFICATE_FILENAME: {"accepted": accepted}},
    }


def _serial_payloads(tasks, worker):
    """Apply the same worker sequentially, normalizing exactly like batch."""
    payloads = []
    for spec, out_dir in tasks:
        mode = str(spec.get("mode") or "simulate").lower()
        try:
            payload = worker((mode, json.dumps(spec.get("task")), str(out_dir)))
        except Exception as exc:
            payload = {"ok": False, "error_type": type(exc).__name__, "error": str(exc)[:200]}
        payloads.append(payload)
    return payloads


def _tasks(n, tmp):
    return [
        ({"mode": "simulate", "task": {"i": i}}, Path(tmp) / ("task_" + str(i)))
        for i in range(n)
    ]


# ----------------------------- work-order items ----------------------------

def test_parallel_results_match_input_order(tmp_path):
    results = batch_run(_tasks(4, tmp_path), _worker_fn=fake_ok_worker)
    assert [r.raw_outputs["i"] for r in results] == [0, 1, 2, 3]


def test_parallel_matches_serial_results(tmp_path):
    """Same tasks: sequential application vs batch plumbing agree."""
    tasks = _tasks(4, tmp_path)
    via_batch = batch_run(tasks, _worker_fn=fake_ok_worker)
    expected = []
    for spec, out_dir in tasks:
        payload = fake_ok_worker((
            "simulate", json.dumps(spec["task"]), str(out_dir)
        ))
        result = _normalize_certificate_result(
            payload["raw_outputs"][CERTIFICATE_FILENAME],
            payload["raw_outputs"],
            payload["cpu_seconds"],
            payload["wall_seconds"],
            Path(payload["certificate_path"]),
        )
        expected.append((result.certified, result.outcome, result.tightest_margin))
    assert [
        (r.certified, r.outcome, r.tightest_margin) for r in via_batch
    ] == expected


def test_single_task_bypasses_pool(tmp_path):
    with patch("concurrent.futures.ProcessPoolExecutor") as ppe:
        results = batch_run(_tasks(1, tmp_path), _worker_fn=fake_ok_worker)
    assert len(results) == 1
    ppe.assert_not_called()


def test_one_task_failure_isolated(tmp_path):
    tasks = [
        ({"mode": "simulate", "task": {"kind": "certified"}}, tmp_path / "a"),
        ({"mode": "simulate", "task": {"kind": "engine_error"}}, tmp_path / "b"),
        ({"mode": "simulate", "task": {"kind": "physics_rejected"}}, tmp_path / "c"),
    ]
    results = batch_run(tasks, _worker_fn=fake_mixed_worker)
    assert [r.outcome for r in results] == [
        "certified",
        "engine_error",
        "physics_rejected",
    ]


def test_broken_pool_falls_back_to_serial(tmp_path):
    tasks = _tasks(3, tmp_path)
    seed_dir = tasks[0][1]
    seed_dir.mkdir(parents=True, exist_ok=True)
    (seed_dir / CERTIFICATE_FILENAME).write_text(
        json.dumps({"accepted": True, "margin": 0.42}), encoding="utf-8"
    )

    class _BrokenPool:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def submit(self, *args, **kwargs):
            raise BrokenExecutor()

    with patch("concurrent.futures.ProcessPoolExecutor", _BrokenPool):
        results = batch_run(tasks)  # real _batch_worker, fast-failing payloads
    assert len(results) == 3
    adopted = results[0]
    assert adopted.certified is True
    assert adopted.cpu_seconds == 0.0  # evidence path, never re-executed
    assert all(r.outcome in ("engine_error", "certified") for r in results[1:])


def test_cpu_seconds_sums_task_time_not_wall_clock(tmp_path):
    burn = 0.15
    tasks = [
        (
            {"mode": "simulate", "task": {"burn": burn}},
            tmp_path / ("burn_" + str(i)),
        )
        for i in range(4)
    ]
    recorded = []

    class _FakeTracker:
        def record_tmm_usage(self, seconds):
            recorded.append(seconds)

    fake_cfg = SimpleNamespace(get_cost_tracker=lambda: _FakeTracker())
    with patch.dict(sys.modules, {"config.qwen_config": fake_cfg}):
        results = batch_run(tasks, _worker_fn=fake_slow_cpu_worker)
    assert len(recorded) == 1  # ONE booking, not one per task
    per_task = sum(r.cpu_seconds for r in results)
    assert recorded[0] == per_task  # sum of CHILD-reported cpu seconds
    assert recorded[0] >= 4 * burn * 0.8  # ~4T, never collapsed to ~T


def test_outcome_classification_preserved(tmp_path):
    kinds = ["certified", "physics_rejected", "engine_error"]
    tasks = [
        ({"mode": "simulate", "task": {"kind": k}}, tmp_path / k)
        for k in kinds
    ]
    parallel = batch_run(tasks, _worker_fn=fake_mixed_worker)
    serial = _serial_payloads(tasks, fake_mixed_worker)
    expected = []
    for payload in serial:
        if not payload.get("ok"):
            expected.append("engine_error")
        elif payload["certified"]:
            expected.append("certified")
        else:
            expected.append("physics_rejected")
    assert [r.outcome for r in parallel] == expected


def test_max_workers_respects_cpu_limit(tmp_path):
    captured = {}

    class _RecordingPool:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)
            raise RuntimeError("do-not-execute")

    with patch("concurrent.futures.ProcessPoolExecutor", _RecordingPool):
        results = batch_run(_tasks(6, tmp_path))
    cpu = os.cpu_count() or 2
    expected = min(6, max(1, cpu - 1), MAX_POOL_WORKERS)
    assert captured["max_workers"] == expected
    assert captured["max_workers"] <= max(1, cpu - 1)
    assert captured["max_workers"] <= MAX_POOL_WORKERS
    assert len(results) == 6


def test_blas_thread_limit_set_in_child(tmp_path):
    """Initializer pins the vars parent-side AND every real child reports
    them back as "1" through blas_env diagnostics."""
    _batch_pool_initializer()
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        assert os.environ.get(name) == "1"
    results = batch_run(_tasks(2, tmp_path))
    assert len(results) == 2
    for r in results:
        blas = r.raw_outputs.get("blas_env")
        assert blas is not None, "child did not report blas_env diagnostics"
        for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            assert blas[name] == "1", name + "=" + str(blas[name])


def test_budget_gate_still_blocks_before_pool(tmp_path):
    snapshot = SimpleNamespace(tmm_cpu_seconds=100.0)
    with patch("concurrent.futures.ProcessPoolExecutor") as ppe:
        results = batch_run(
            _tasks(3, tmp_path),
            budget_snapshot=snapshot,
            max_cpu_seconds=50.0,
        )
    assert len(results) == 3
    assert all(r.outcome == "budget_blocked" for r in results)
    gate = results[0].raw_outputs["budget_gate"]
    assert gate["blocked"] is True
    assert gate["used_cpu_seconds"] == 100.0
    ppe.assert_not_called()



# ------------------- item 11: orchestrator Phase-1b dirs --------------------

class _StubSeeder:
    def __init__(self, prepared):
        self.prepared = prepared

    def seed(self, **kwargs):
        return self.prepared


class _UnusedPlanner:
    """Single-wave test: replanning never runs."""

    def plan(self, *args, **kwargs):
        raise AssertionError("planner must not run in a single wave")


def test_orchestrator_phase1_parallel_assigns_unique_dirs(tmp_path):
    prepared = _seed_mod.seed_portfolio(
        problem_analysis=_seed_mod._problem(),
        method_research=_seed_mod._research(),
        client=_seed_mod.SeedClient(_seed_mod._two_source_payload()),
        max_routes=2,
        force_mock=True,
    )
    config = TMMResearchHarnessConfig(
        # TWO iteration slots so wave-admission can afford BOTH routes in the
        # same wave (with 1 slot it truncates to a single-route wave and the
        # batch path never engages).
        maximum_iterations=2,
        max_routes=2,
        max_rounds_per_route=1,
        portfolio_seeding_enabled=True,
        online_method_research=False,
        use_qwen_policy_inside_tmm=False,
        qwen_force_mock=True,
        wall_time_seconds=600.0,
    )
    harness = TMMResearchHarness(
        tmp_path,
        problem_analyzer=_orch_mod._Analyzer(),
        method_researcher=_orch_mod._Researcher(),
        strategy_planner=_UnusedPlanner(),
        task_compiler=_orch_mod._Compiler(),
        portfolio_seeder=_StubSeeder(prepared),
        config=config,
    )
    assert (
        getattr(harness.tmm_harness_factory, "__func__", None)
        is TMMResearchHarness._default_tmm_factory
    )
    harness._reflection_client = _refl_mod.RecordingTurboClient([])

    jobs_seen = []

    def fake_batch(jobs, *, budget_snapshot=None, max_cpu_seconds=None):
        jobs_seen.extend(jobs)
        outcomes = []
        for n, pair in enumerate(jobs):
            execution_dir = Path(pair[1])
            cand = {
                "candidate_id": "candidate",
                "physically_admissible": True,
                "target_score": 0.7 + 0.01 * n,
                "robustness_score": 0.6,
                "simplicity_score": 0.8,
                "metadata": {"thicknesses_nm": [100.0]},
                "artifact_ids": [],
            }
            outcomes.append({
                "ok": True,
                "cpu_seconds": 0.02,
                "wall_seconds": 0.03,
                "final_result": {
                    "status": "completed",
                    "experiment_results": [{
                        "experiment_id": "exp_" + execution_dir.name,
                        "mode": "optimize",
                        "physically_valid_candidate_count": 1,
                        "portfolio": {
                            "candidates": [cand],
                            "selected_roles": {"best_target_score": "candidate"},
                        },
                    }],
                },
            })
        return outcomes

    harness._tmm_batch_fn = fake_batch
    result = harness.run("Design a broadband reflector from 500 to 600 nm.")

    assert len(jobs_seen) == 2, "expected both compiled routes in one batch"
    work_dirs = [json.loads(job[0])["work_dir"] for job in jobs_seen]
    assert len(set(work_dirs)) == 2, "iteration dirs must be unique"
    for wd in work_dirs:
        # The REAL worker creates <iter>/tmm_run; with a fake executor we
        # assert the planned dirs are uniquely placed under existing iters.
        assert Path(wd).name == "tmm_run"
        assert Path(wd).parent.is_dir()
    assert list(tmp_path.rglob("EXECUTION_ERROR.json")) == []
    assert result.status in ("completed", "stopped")
    assert len(result.iterations) == 2
    iter_dirs = sorted(p.name for p in (tmp_path / "iterations").iterdir())
    assert iter_dirs == ["iteration_01", "iteration_02"]
    events = (tmp_path / "RESEARCH_EVENTS.jsonl").read_text(encoding="utf-8")
    assert "tmm_batch_executed" in events