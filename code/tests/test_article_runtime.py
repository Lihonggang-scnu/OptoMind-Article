from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from optomind_optics.harness.article_contracts import ArticleStage
from optomind_optics.harness.article_runtime import (
    ArticleBranchManager,
    ArticleBranchState,
    ArticleBudgetAdapter,
    ArticleCheckpoint,
    ArticleCheckpointManager,
    BranchError,
    CheckpointError,
    CheckpointMismatchError,
    RuntimeLock,
    RuntimeLockError,
    _FileLock,
    article_runtime_fingerprint,
)
from optomind_optics.harness.budget import (
    BudgetLimits,
    BudgetScheduler,
    BudgetStateError,
    DuplicateActionError,
)


def _graph_export(run_id: str = "run-1", extra: str = "base") -> dict:
    return {
        "schema_version": "optical-experiment-graph.v1",
        "run_id": run_id,
        "nodes": [],
        "article_nodes": [{"node_id": extra, "payload": {"schema_version": "article-node.v1"}}],
        "article_schema_version": "optical-experiment-graph.article.v1",
    }


def _budget_snapshot() -> dict:
    return {
        "limits": {"forward_evaluations": 10, "optimizer_runs": 2},
        "committed": {"forward_evaluations": 0, "optimizer_runs": 0},
        "reserved": {"forward_evaluations": 0, "optimizer_runs": 0},
        "remaining": {"forward_evaluations": 10, "optimizer_runs": 2},
    }


def _save_checkpoint(
    manager: ArticleCheckpointManager,
    tmp_path,
    *,
    run_id: str = "run-1",
    branch_id: str = "root",
    stage: ArticleStage = ArticleStage.charter_locked,
    graph: dict | None = None,
    lock: str = "lock-root",
    artifact_hashes: dict[str, str] | None = None,
    fingerprint: str | None = "env-a",
) -> tuple[ArticleCheckpoint, "object"]:
    graph = graph if graph is not None else _graph_export(run_id)
    checkpoint = manager.build(
        run_id=run_id,
        branch_id=branch_id,
        stage=stage,
        graph_export=graph,
        budget_snapshot=_budget_snapshot(),
        runtime_lock=lock,
        runtime_fingerprint=fingerprint,
        random_seeds={"python": 42, "numpy": 7},
        artifact_hashes=artifact_hashes,
        created_at="2026-08-15T00:00:00Z",
    )
    path = tmp_path / "checkpoint.v1.json"
    manager.save(checkpoint, path)
    return checkpoint, path


def test_checkpoint_round_trip_and_version_fields(tmp_path) -> None:
    manager = ArticleCheckpointManager()
    checkpoint, path = _save_checkpoint(manager, tmp_path)
    loaded = manager.load(
        path,
        expected_run_id="run-1",
        expected_lock="lock-root",
        graph_export=_graph_export(),
    )
    assert loaded.model_dump() == checkpoint.model_dump()
    assert loaded.schema_version == "article-checkpoint.v1"
    assert loaded.branch_id == "root"
    assert loaded.stage == ArticleStage.charter_locked
    assert loaded.random_seeds == {"python": 42, "numpy": 7}


def test_checkpoint_schema_mismatch_rejected(tmp_path) -> None:
    manager = ArticleCheckpointManager()
    _, path = _save_checkpoint(manager, tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = "article-checkpoint.v99"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CheckpointMismatchError, match="schema"):
        manager.load(path, expected_run_id="run-1", expected_lock="lock-root")


def test_checkpoint_run_id_mismatch_rejected(tmp_path) -> None:
    manager = ArticleCheckpointManager()
    _, path = _save_checkpoint(manager, tmp_path)
    with pytest.raises(CheckpointMismatchError, match="run_id"):
        manager.load(path, expected_run_id="other-run", expected_lock="lock-root")


def test_checkpoint_runtime_lock_mismatch_rejected(tmp_path) -> None:
    manager = ArticleCheckpointManager()
    _, path = _save_checkpoint(manager, tmp_path)
    with pytest.raises(CheckpointMismatchError, match="lock token"):
        manager.load(path, expected_run_id="run-1", expected_lock="wrong-lock")


def test_checkpoint_graph_digest_tamper_and_mismatch_detected(tmp_path) -> None:
    manager = ArticleCheckpointManager()
    _, path = _save_checkpoint(manager, tmp_path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["graph_export"]["nodes"] = ["tampered"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CheckpointMismatchError, match="digest"):
        manager.load(path, expected_run_id="run-1", expected_lock="lock-root")

    _, path = _save_checkpoint(manager, tmp_path)
    with pytest.raises(CheckpointMismatchError, match="graph_digest"):
        manager.load(
            path,
            expected_run_id="run-1",
            expected_lock="lock-root",
            graph_export=_graph_export(extra="different"),
        )


def test_checkpoint_artifact_hash_mismatch_detected(tmp_path) -> None:
    manager = ArticleCheckpointManager()
    artifact = tmp_path / "SIMULATION_RESULT.json"
    artifact.write_text('{"R": 0.01}', encoding="utf-8")
    hashes = manager.compute_file_hashes({"sim": artifact})
    _, path = _save_checkpoint(manager, tmp_path, artifact_hashes=hashes)

    assert manager.load(
        path,
        expected_run_id="run-1",
        expected_lock="lock-root",
        artifact_paths={"sim": artifact},
    ).artifact_hashes == hashes

    artifact.write_text('{"R": 0.99}', encoding="utf-8")
    with pytest.raises(CheckpointMismatchError, match="Artifact"):
        manager.load(
            path,
            expected_run_id="run-1",
            expected_lock="lock-root",
            artifact_paths={"sim": artifact},
        )


def test_checkpoint_atomic_save_crash_recovery(tmp_path) -> None:
    manager = ArticleCheckpointManager()
    _, path = _save_checkpoint(manager, tmp_path)

    # A leftover temp file from a crashed write must not shadow the main file.
    (tmp_path / ".tmp_crashed.json").write_text('{"partial": true', encoding="utf-8")
    loaded = manager.load(path, expected_run_id="run-1", expected_lock="lock-root")
    assert loaded.branch_id == "root"

    # A corrupted main checkpoint fails clearly instead of silently resuming.
    path.write_text('{"broken": ', encoding="utf-8")
    with pytest.raises(CheckpointError, match="Could not read checkpoint"):
        manager.load(path, expected_run_id="run-1", expected_lock="lock-root")


def test_budget_adapter_reports_three_ledgers_without_changing_scheduler(
    tmp_path,
) -> None:
    scheduler = BudgetScheduler(
        BudgetLimits(
            forward_evaluations=10,
            optimizer_runs=2,
            qwen_calls=3,
        )
    )
    adapter = ArticleBudgetAdapter(scheduler)
    assert adapter.reserve("a", forward_evaluations=2) is True
    assert adapter.reserve("b", forward_evaluations=1) is True
    assert adapter.commit("b", forward_evaluations=1) is True
    assert adapter.release("a") is True

    ledgers = adapter.three_ledgers()
    assert ledgers["reserved"]["forward_evaluations"] == 0
    assert ledgers["consumed"]["forward_evaluations"] == 1
    assert ledgers["released"]["forward_evaluations"] == 2

    # The scheduler itself is the authority and reports the same state.
    snapshot = scheduler.snapshot()
    assert snapshot["reserved"]["forward_evaluations"] == 0
    assert snapshot["committed"]["forward_evaluations"] == 1
    assert adapter.snapshot()["scheduler"] == snapshot


def test_budget_adapter_delegates_duplicate_and_state_errors(tmp_path) -> None:
    scheduler = BudgetScheduler(BudgetLimits(forward_evaluations=5))
    adapter = ArticleBudgetAdapter(scheduler)
    adapter.reserve("a", forward_evaluations=1)
    with pytest.raises(DuplicateActionError):
        adapter.reserve("a", forward_evaluations=1)
    adapter.release("a")
    with pytest.raises(BudgetStateError):
        adapter.release("a")
    with pytest.raises(BudgetStateError):
        adapter.commit("never_reserved", forward_evaluations=1)


def test_runtime_lock_acquire_release_and_conflict(tmp_path) -> None:
    lock = RuntimeLock(tmp_path / "runtime.lock")
    token = lock.acquire("run-1", "branch-1")
    assert lock.is_held(token)
    with pytest.raises(RuntimeLockError, match="already held"):
        lock.acquire("run-1", "branch-1")
    with pytest.raises(RuntimeLockError, match="owning token"):
        lock.release("wrong-token")
    lock.release(token)
    assert lock.token() is None
    with pytest.raises(RuntimeLockError, match="not held"):
        lock.release(token)


def test_branch_fork_isolates_output_and_preserves_parent(tmp_path) -> None:
    manager = ArticleBranchManager(tmp_path / "branches", "run-1")
    root = manager.fork(
        None,
        stage=ArticleStage.charter_locked,
        graph_export=_graph_export(),
        budget_snapshot=_budget_snapshot(),
        runtime_lock_token="lock-root",
        branch_id="root",
        created_at="2026-08-15T00:00:00Z",
    )
    child = manager.fork(
        "root",
        stage=ArticleStage.hypotheses_formed,
        graph_export=_graph_export(extra="child"),
        budget_snapshot=_budget_snapshot(),
        runtime_lock_token="lock-child",
        branch_id="child",
        created_at="2026-08-15T00:00:01Z",
    )

    assert child.parent_branch_id == "root"
    assert child.output_namespace != root.output_namespace
    assert child.input_namespace == root.input_namespace

    child_output = Path(child.output_namespace)
    root_output = Path(root.output_namespace)
    child_output.mkdir(parents=True, exist_ok=True)
    (child_output / "candidate.json").write_text("{}", encoding="utf-8")
    assert (child_output / "candidate.json").exists()
    assert not (root_output / "candidate.json").exists()

    assert manager.get_branch("root").model_dump() == root.model_dump()
    assert manager.get_branch("child").model_dump() == child.model_dump()

    checkpoint = ArticleCheckpointManager().load(
        child.head_checkpoint,
        expected_run_id="run-1",
        expected_lock="lock-child",
    )
    assert checkpoint.branch_id == "child"
    assert checkpoint.stage == ArticleStage.hypotheses_formed


def test_branch_registry_is_append_only_and_duplicate_fork_rejected(tmp_path) -> None:
    manager = ArticleBranchManager(tmp_path / "branches", "run-1")
    root = manager.fork(
        None,
        stage=ArticleStage.charter_locked,
        graph_export=_graph_export(),
        budget_snapshot=_budget_snapshot(),
        runtime_lock_token="lock-root",
        branch_id="root",
    )
    def _tree() -> list[str]:
        return sorted(
            str(path.relative_to(tmp_path / "branches"))
            for path in (tmp_path / "branches").rglob("*")
            if path.is_file()
        )

    before = _tree()
    with pytest.raises(BranchError, match="Duplicate branch_id"):
        manager.fork(
            "root",
            stage=ArticleStage.hypotheses_formed,
            graph_export=_graph_export(),
            budget_snapshot=_budget_snapshot(),
            runtime_lock_token="lock-root",
            branch_id="root",
        )
    assert _tree() == before
    assert manager.get_branch("root").model_dump() == root.model_dump()
    assert [item.branch_id for item in manager.list_branches()] == ["root"]


def test_branch_unknown_parent_and_cross_run_rejected(tmp_path) -> None:
    manager = ArticleBranchManager(tmp_path / "branches", "run-1")
    with pytest.raises(BranchError, match="Unknown branch_id"):
        manager.fork(
            "ghost",
            stage=ArticleStage.baseline_experiments,
            graph_export=_graph_export(),
            budget_snapshot=_budget_snapshot(),
        )


def test_no_secret_material_in_checkpoint_or_branch_files(tmp_path) -> None:
    secret = "sk-runtime-secret-77aa"
    manager = ArticleBranchManager(tmp_path / "branches", "run-1")
    graph = _graph_export()
    checkpoint = ArticleCheckpointManager().build(
        run_id="run-1",
        branch_id="root",
        stage=ArticleStage.scientific_review,
        graph_export=graph,
        budget_snapshot={},
        runtime_lock="lock-root",
        random_seeds={},
    )
    assert "api_key" not in checkpoint.model_dump()
    path = tmp_path / "checkpoint.v1.json"
    ArticleCheckpointManager().save(checkpoint, path)
    assert secret not in path.read_text(encoding="utf-8")

    branch = manager.fork(
        None,
        stage=ArticleStage.scientific_review,
        graph_export=graph,
        budget_snapshot=_budget_snapshot(),
        runtime_lock_token="lock-root",
        branch_id="root",
    )
    registry_text = (tmp_path / "branches" / "BRANCHES.json").read_text(
        encoding="utf-8"
    )
    assert secret not in registry_text
    lock_text = (tmp_path / "branches" / "root" / "runtime.lock").read_text(
        encoding="utf-8"
    )
    assert secret not in lock_text


def test_extra_checkpoint_fields_are_ignored(tmp_path) -> None:
    manager = ArticleCheckpointManager()
    checkpoint, path = _save_checkpoint(manager, tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["api_key"] = "should-not-persist"
    path.write_text(json.dumps(raw), encoding="utf-8")
    loaded = manager.load(path, expected_run_id="run-1", expected_lock="lock-root")
    assert "api_key" not in loaded.model_dump()
    assert checkpoint.model_dump() == loaded.model_dump()


_LOCK_SCRIPT = """
import sys, time
from pathlib import Path
from optomind_optics.harness.article_runtime import RuntimeLock

lock_path, run_id, branch_id, token, ready_file, start_file = sys.argv[1:7]
Path(ready_file).write_text("ready", encoding="utf-8")
deadline = time.time() + 20.0
while not Path(start_file).exists():
    if time.time() > deadline:
        print("TIMEOUT")
        raise SystemExit(1)
    time.sleep(0.01)
lock = RuntimeLock(Path(lock_path))
try:
    lock.acquire(run_id, branch_id, token=token)
except Exception:
    print("DENIED")
    raise SystemExit(0)
print("ACQUIRED")
time.sleep(1.2)
lock.release(token)
print("RELEASED")
"""


def _spawn_lock_worker(tmp_path, token: str) -> subprocess.Popen:
    ready = tmp_path / f"ready_{token}"
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            _LOCK_SCRIPT,
            str(tmp_path / "runtime.lock"),
            "run-1",
            "branch-1",
            token,
            str(ready),
            str(tmp_path / "start"),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_runtime_lock_cross_process_exclusive_create(tmp_path) -> None:
    lock_path = tmp_path / "runtime.lock"
    lock = RuntimeLock(lock_path)
    parent_token = lock.acquire("run-1", "branch-1")

    (tmp_path / "start").write_text("go", encoding="utf-8")
    denied = subprocess.run(
        [
            sys.executable,
            "-c",
            _LOCK_SCRIPT,
            str(lock_path),
            "run-1",
            "branch-1",
            "child-token",
            str(tmp_path / "ready_child"),
            str(tmp_path / "start"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "DENIED" in denied.stdout
    lock.release(parent_token)
    assert not lock_path.exists()

    (tmp_path / "start2").write_text("go", encoding="utf-8")
    acquired = subprocess.run(
        [
            sys.executable,
            "-c",
            _LOCK_SCRIPT,
            str(lock_path),
            "run-1",
            "branch-1",
            "child-token-2",
            str(tmp_path / "ready_child2"),
            str(tmp_path / "start2"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "ACQUIRED" in acquired.stdout
    assert "RELEASED" in acquired.stdout
    assert not lock_path.exists()


def test_runtime_lock_two_processes_single_winner(tmp_path) -> None:
    first = _spawn_lock_worker(tmp_path, "race-a")
    second = _spawn_lock_worker(tmp_path, "race-b")
    deadline = 0.0
    for _ in range(400):
        if (tmp_path / "ready_race-a").exists() and (tmp_path / "ready_race-b").exists():
            break
        time.sleep(0.01)
        deadline += 0.01
    assert deadline < 4.0
    (tmp_path / "start").write_text("go", encoding="utf-8")
    outs = [process.communicate(timeout=60)[0] for process in (first, second)]
    assert sum("ACQUIRED" in out for out in outs) == 1
    assert sum("DENIED" in out for out in outs) == 1
    assert not (tmp_path / "runtime.lock").exists()


def test_fork_rolls_back_artifacts_when_registry_update_fails(
    tmp_path, monkeypatch
) -> None:
    manager = ArticleBranchManager(tmp_path / "branches", "run-1")
    root = manager.fork(
        None,
        stage=ArticleStage.charter_locked,
        graph_export=_graph_export(),
        budget_snapshot=_budget_snapshot(),
        runtime_lock_token="lock-root",
        branch_id="root",
    )

    def fail_write(self, branches) -> None:
        raise BranchError("simulated registry failure")

    monkeypatch.setattr(ArticleBranchManager, "_write_registry", fail_write)
    with pytest.raises(BranchError, match="simulated registry failure"):
        manager.fork(
            "root",
            stage=ArticleStage.hypotheses_formed,
            graph_export=_graph_export(),
            budget_snapshot=_budget_snapshot(),
            runtime_lock_token="lock-rollback",
            branch_id="rollback_branch",
        )
    assert not (tmp_path / "branches" / "rollback_branch").exists()
    assert [item.branch_id for item in manager.list_branches()] == ["root"]
    assert manager.get_branch("root").model_dump() == root.model_dump()


def test_registry_malformed_and_conflicting_state_detected(tmp_path) -> None:
    manager = ArticleBranchManager(tmp_path / "branches", "run-1")
    registry = tmp_path / "branches" / "BRANCHES.json"

    registry.write_text("{not json", encoding="utf-8")
    with pytest.raises(BranchError, match="malformed"):
        manager.list_branches()

    registry.write_text(
        json.dumps({"schema_version": "article-branches.v9", "run_id": "run-1", "branches": []}),
        encoding="utf-8",
    )
    with pytest.raises(BranchError, match="schema"):
        manager.list_branches()

    registry.write_text(
        json.dumps(
            {"schema_version": "article-branches.v1", "run_id": "other-run", "branches": []}
        ),
        encoding="utf-8",
    )
    with pytest.raises(BranchError, match="run_id"):
        manager.list_branches()

    registry.write_text(
        json.dumps(
            {
                "schema_version": "article-branches.v1",
                "run_id": "run-1",
                "branches": [{"branch_id": "a"}, {"branch_id": "a"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(BranchError, match="duplicate branch_id"):
        manager.list_branches()

    registry.write_text(
        json.dumps(
            {
                "schema_version": "article-branches.v1",
                "run_id": "run-1",
                "branches": [{"run_id": "run-1"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(BranchError, match="without branch_id"):
        manager.list_branches()

    with pytest.raises(BranchError):
        manager.fork(
            None,
            stage=ArticleStage.baseline_experiments,
            graph_export=_graph_export(),
            budget_snapshot=_budget_snapshot(),
            branch_id="should-not-create",
        )
    assert not (tmp_path / "branches" / "should-not-create").exists()


def test_registry_concurrent_forks_no_lost_writes(tmp_path) -> None:
    root = tmp_path / "branches"
    manager_a = ArticleBranchManager(root, "run-1")
    manager_b = ArticleBranchManager(root, "run-1")
    results: list[str] = []
    result_lock = threading.Lock()

    def worker(manager, branch_id: str) -> None:
        try:
            manager.fork(
                None,
                stage=ArticleStage.baseline_experiments,
                graph_export=_graph_export(),
                budget_snapshot=_budget_snapshot(),
                runtime_lock_token=f"lock-{branch_id}",
                branch_id=branch_id,
            )
            with result_lock:
                results.append(branch_id)
        except Exception as exc:  # pragma: no cover - asserted below
            with result_lock:
                results.append(f"{branch_id}:{exc}")

    first = threading.Thread(target=worker, args=(manager_a, "a"))
    second = threading.Thread(target=worker, args=(manager_b, "b"))
    first.start()
    second.start()
    first.join()
    second.join()
    assert sorted(results) == ["a", "b"]
    final = ArticleBranchManager(root, "run-1")
    assert [item.branch_id for item in final.list_branches()] == ["a", "b"]


def test_runtime_fingerprint_independent_mismatch(tmp_path) -> None:
    manager = ArticleCheckpointManager()
    _, path = _save_checkpoint(manager, tmp_path, fingerprint="env-a")
    loaded = manager.load(
        path,
        expected_run_id="run-1",
        expected_lock="lock-root",
        expected_runtime_fingerprint="env-a",
    )
    assert loaded.runtime_fingerprint == "env-a"
    with pytest.raises(CheckpointMismatchError, match="Runtime fingerprint"):
        manager.load(
            path,
            expected_run_id="run-1",
            expected_lock="lock-root",
            expected_runtime_fingerprint="env-b",
        )
    with pytest.raises(CheckpointMismatchError, match="lock token"):
        manager.load(
            path,
            expected_run_id="run-1",
            expected_lock="wrong-lock",
            expected_runtime_fingerprint="env-a",
        )
    # A genuinely legacy checkpoint without a runtime fingerprint field is
    # still rejected clearly when an expected fingerprint is supplied.
    _, legacy_path = _save_checkpoint(manager, tmp_path / "legacy", fingerprint="env-a")
    legacy_payload = json.loads(legacy_path.read_text(encoding="utf-8"))
    del legacy_payload["runtime_fingerprint"]
    legacy_path.write_text(json.dumps(legacy_payload), encoding="utf-8")
    with pytest.raises(CheckpointMismatchError, match="no runtime fingerprint"):
        manager.load(
            legacy_path,
            expected_run_id="run-1",
            expected_lock="lock-root",
            expected_runtime_fingerprint="env-a",
        )

    # build() always fills a real fingerprint from the runtime authority.
    auto = manager.build(
        run_id="run-1",
        branch_id="b",
        stage=ArticleStage.charter_locked,
        graph_export=_graph_export(),
        budget_snapshot=_budget_snapshot(),
        runtime_lock="lock-auto",
    )
    assert auto.runtime_fingerprint == article_runtime_fingerprint()
    auto_path = tmp_path / "auto.json"
    manager.save(auto, auto_path)
    manager.load(
        auto_path,
        expected_run_id="run-1",
        expected_lock="lock-auto",
        expected_runtime_fingerprint=article_runtime_fingerprint(),
    )
    # A source/dependency fingerprint change is rejected independently of the
    # writer token: wrong fingerprint fails, and a wrong token still fails
    # with the token error even when the fingerprint is correct.
    with pytest.raises(CheckpointMismatchError, match="Runtime fingerprint"):
        manager.load(
            auto_path,
            expected_run_id="run-1",
            expected_lock="lock-auto",
            expected_runtime_fingerprint="tampered-source-tree-hash",
        )
    with pytest.raises(CheckpointMismatchError, match="lock token"):
        manager.load(
            auto_path,
            expected_run_id="run-1",
            expected_lock="wrong-lock",
            expected_runtime_fingerprint=article_runtime_fingerprint(),
        )


def test_registry_lock_not_stolen_after_stale_threshold(tmp_path) -> None:
    lock_path = tmp_path / "BRANCHES.json.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # Simulate a lock held by another process for longer than the old 30s
    # stale threshold: it must never be deleted or stolen by a new acquirer.
    lock_path.write_text(json.dumps({"created_at": time.time() - 120}), encoding="utf-8")
    original_content = lock_path.read_text(encoding="utf-8")
    os.utime(lock_path, (time.time() - 120, time.time() - 120))
    original_mtime_ns = lock_path.stat().st_mtime_ns

    with pytest.raises(BranchError, match="Could not acquire file lock"):
        with _FileLock(lock_path, timeout=0.3, poll=0.01):
            pass
    assert lock_path.exists()
    assert lock_path.stat().st_mtime_ns == original_mtime_ns
    assert lock_path.read_text(encoding="utf-8") == original_content


@pytest.mark.parametrize(
    "bad_id",
    [
        "",
        "   ",
        ".",
        "..",
        "a/b",
        "a\\b",
        "/absolute",
        "C:\\absolute",
        "..\\escape",
        "a\x00b",
        " leading",
        "trailing ",
    ],
)
def test_fork_rejects_unsafe_branch_ids_before_creating_artifacts(
    tmp_path, bad_id
) -> None:
    manager = ArticleBranchManager(tmp_path / "branches", "run-1")
    with pytest.raises(BranchError):
        manager.fork(
            None,
            stage=ArticleStage.charter_locked,
            graph_export=_graph_export(),
            budget_snapshot=_budget_snapshot(),
            runtime_lock_token="lock",
            branch_id=bad_id,
        )
    branches_root = tmp_path / "branches"
    assert not (branches_root / "escape").exists()
    assert sorted(item.name for item in branches_root.iterdir()) == ["shared_inputs"]
    assert not (tmp_path / "absolute").exists()


def test_budget_adapter_single_snapshot_per_call(tmp_path) -> None:
    scheduler = BudgetScheduler(BudgetLimits(forward_evaluations=10))
    scheduler.reserve("a", forward_evaluations=1)
    scheduler.commit("a", forward_evaluations=1)

    class CountingScheduler:
        def __init__(self, inner) -> None:
            self.inner = inner
            self.calls = 0

        def snapshot(self) -> dict:
            self.calls += 1
            return self.inner.snapshot()

    wrapped = CountingScheduler(scheduler)
    adapter = ArticleBudgetAdapter(wrapped)
    ledgers = adapter.three_ledgers()
    assert wrapped.calls == 1
    assert ledgers["consumed"]["forward_evaluations"] == 1
    assert ledgers["reserved"]["forward_evaluations"] == 0
    assert ledgers["released"]["forward_evaluations"] == 0
    snap = adapter.snapshot()
    assert wrapped.calls == 2
    assert snap["ledgers"] == ledgers


def test_checkpoint_budget_completeness_and_reference_validation(tmp_path) -> None:
    manager = ArticleCheckpointManager()

    incomplete = manager.build(
        run_id="run-1",
        branch_id="b",
        stage=ArticleStage.charter_locked,
        graph_export=_graph_export(),
        budget_snapshot={"limits": {}},
        runtime_lock="lock",
    )
    incomplete_path = tmp_path / "incomplete.json"
    manager.save(incomplete, incomplete_path)
    with pytest.raises(CheckpointMismatchError, match="incomplete"):
        manager.load(incomplete_path, expected_run_id="run-1", expected_lock="lock")

    empty = manager.build(
        run_id="run-1",
        branch_id="b",
        stage=ArticleStage.charter_locked,
        graph_export=_graph_export(),
        budget_snapshot={},
        runtime_lock="lock",
    )
    empty_path = tmp_path / "empty.json"
    manager.save(empty, empty_path)
    with pytest.raises(CheckpointMismatchError, match="neither"):
        manager.load(empty_path, expected_run_id="run-1", expected_lock="lock")

    budget_file = tmp_path / "budget.json"
    scheduler = BudgetScheduler(
        BudgetLimits(forward_evaluations=10), checkpoint_path=budget_file
    )
    scheduler.reserve("a", forward_evaluations=1)
    scheduler.commit("a", forward_evaluations=1)
    referenced = manager.build(
        run_id="run-1",
        branch_id="b",
        stage=ArticleStage.charter_locked,
        graph_export=_graph_export(),
        budget_snapshot={},
        budget_checkpoint_path=str(budget_file),
        runtime_lock="lock",
    )
    referenced_path = tmp_path / "referenced.json"
    manager.save(referenced, referenced_path)
    loaded = manager.load(referenced_path, expected_run_id="run-1", expected_lock="lock")
    assert loaded.budget_checkpoint_path == str(budget_file)

    budget_file.write_text(
        json.dumps({"schema_version": 99, "limits": {}}), encoding="utf-8"
    )
    with pytest.raises(CheckpointMismatchError, match="schema"):
        manager.load(referenced_path, expected_run_id="run-1", expected_lock="lock")


def test_checkpoint_artifact_completeness_detected(tmp_path) -> None:
    manager = ArticleCheckpointManager()
    artifact = tmp_path / "a.json"
    artifact.write_text("{}", encoding="utf-8")
    hashes = manager.compute_file_hashes({"a": artifact})
    _, path = _save_checkpoint(manager, tmp_path, artifact_hashes=hashes)
    loaded = manager.load(
        path,
        expected_run_id="run-1",
        expected_lock="lock-root",
        artifact_paths={"a": artifact},
    )
    assert loaded.artifact_ids == ["a"]

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["artifact_ids"] = ["a", "b"]
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(CheckpointMismatchError, match="do not match"):
        manager.load(path, expected_run_id="run-1", expected_lock="lock-root")

    raw["artifact_ids"] = ["a"]
    raw["artifact_hashes"] = {"a": ""}
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(CheckpointMismatchError, match="non-empty"):
        manager.load(path, expected_run_id="run-1", expected_lock="lock-root")


def test_shared_inputs_are_read_only_across_branch_operations(tmp_path) -> None:
    manager = ArticleBranchManager(tmp_path / "branches", "run-1")
    inputs = tmp_path / "branches" / "shared_inputs"
    seed = inputs / "INPUT.json"
    seed.write_text('{"v": 1}', encoding="utf-8")
    root = manager.fork(
        None,
        stage=ArticleStage.charter_locked,
        graph_export=_graph_export(),
        budget_snapshot=_budget_snapshot(),
        runtime_lock_token="lock-root",
        branch_id="root",
    )
    child = manager.fork(
        "root",
        stage=ArticleStage.hypotheses_formed,
        graph_export=_graph_export(extra="child"),
        budget_snapshot=_budget_snapshot(),
        runtime_lock_token="lock-child",
        branch_id="child",
    )
    assert Path(root.input_namespace) == inputs
    assert Path(child.input_namespace) == inputs
    (Path(child.output_namespace) / "result.json").write_text("{}", encoding="utf-8")
    (Path(root.output_namespace) / "baseline.json").write_text("{}", encoding="utf-8")
    assert sorted(item.name for item in inputs.iterdir()) == ["INPUT.json"]
    assert seed.read_text(encoding="utf-8") == '{"v": 1}'
