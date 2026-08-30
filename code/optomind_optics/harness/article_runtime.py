"""Runtime checkpoint, budget adapter, and branch isolation for the Article
Scientific Harness.

This module builds thin adapters over existing infrastructure instead of
duplicating it:

- ``ArticleBudgetAdapter`` wraps ``BudgetScheduler`` (the reservation/commit/
  release authority) and exposes reserved/consumed/released ledgers derived
  from the scheduler's own snapshot/events.
- ``ArticleCheckpointManager`` persists versioned checkpoints atomically and
  detects schema, run, writer-token, runtime-fingerprint, graph-digest,
  budget-completeness, and artifact mismatches before resume.
- ``RuntimeLock`` guards one writer per branch with atomic exclusive-create
  acquisition, safe across processes.
- ``ArticleBranchManager`` forks branches with isolated output namespaces and
  read-only shared inputs; a fork never mutates the parent branch, rejects
  duplicate branch IDs before creating anything, and rolls back its own
  artifacts if checkpoint or registry updates fail.  Registry updates are
  serialized by an exclusive-create file lock and validated before write.

Checkpoints and branch states are pydantic models with literal schema
versions, deterministic serialization, and no secret storage.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Mapping, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from optomind_optics.harness.article_contracts import ArticleStage
from optomind_optics.harness.budget import BudgetLimits, BudgetScheduler
from optomind_research.runtime.artifact_store import atomic_write_json


# ---------------------------------------------------------------------------
# Enums and exceptions
# ---------------------------------------------------------------------------


class BranchState(str, Enum):
    active = "active"
    completed = "completed"
    abandoned = "abandoned"


class RuntimeLockError(ValueError):
    """Raised when a runtime lock is unavailable or held by another token."""


class CheckpointError(ValueError):
    """Raised when a checkpoint is unreadable or malformed."""


class CheckpointMismatchError(CheckpointError):
    """Raised when a checkpoint fails a resume validation check."""


class BranchError(ValueError):
    """Raised for invalid branch operations."""


# ---------------------------------------------------------------------------
# Versioned models
# ---------------------------------------------------------------------------


class _RuntimeModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ArticleCheckpoint(_RuntimeModel):
    schema_version: Literal["article-checkpoint.v1"] = "article-checkpoint.v1"
    run_id: str
    branch_id: str
    stage: ArticleStage
    graph_digest: str
    graph_export: Optional[Dict[str, Any]] = None
    budget_snapshot: Dict[str, Any] = Field(default_factory=dict)
    budget_checkpoint_path: Optional[str] = None
    runtime_lock: str
    runtime_fingerprint: Optional[str] = None
    random_seeds: Dict[str, Any] = Field(default_factory=dict)
    artifact_ids: List[str] = Field(default_factory=list)
    artifact_hashes: Dict[str, str] = Field(default_factory=dict)
    memory_path: Optional[str] = None
    previous_checkpoint: Optional[str] = None
    created_at: Optional[str] = None


class ArticleBranchState(_RuntimeModel):
    schema_version: Literal["article-branch.v1"] = "article-branch.v1"
    branch_id: str
    run_id: str
    parent_branch_id: Optional[str] = None
    head_checkpoint: Optional[str] = None
    output_namespace: str
    input_namespace: str
    state: BranchState = BranchState.active
    created_at: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Runtime / environment fingerprint
# ---------------------------------------------------------------------------


def article_runtime_fingerprint() -> str:
    """Digest of the Article runtime fingerprint authority.

    Delegates to ``optomind_optics.harness.runtime_fingerprint``
    (source-tree SHA-256 plus interpreter/dependency metadata) and returns a
    stable hex digest.  This is an environment identity for resume validation,
    not a physics or integrity validation.
    """

    from optomind_optics.harness.runtime_fingerprint import build_runtime_fingerprint

    payload = build_runtime_fingerprint()
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


# ---------------------------------------------------------------------------
# Exclusive-create helpers
# ---------------------------------------------------------------------------


def _exclusive_create(path: Path) -> int:
    """Atomically create a file; raises FileExistsError if it already exists."""

    path.parent.mkdir(parents=True, exist_ok=True)
    return os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)


def _validate_branch_id(branch_id: str, branch_root: Path) -> str:
    """Validate a branch ID before it is used as a filesystem path component.

    Rejects empty/whitespace IDs, ``.``/``..``, NUL, path separators, absolute
    paths, and any ID whose resolved path escapes ``branch_root``.  Must run
    before any directory creation or rollback target is derived.
    """

    if not isinstance(branch_id, str) or not branch_id.strip():
        raise BranchError("branch_id must be a non-empty string")
    if branch_id != branch_id.strip():
        raise BranchError("branch_id must not contain surrounding whitespace")
    if "\x00" in branch_id:
        raise BranchError("branch_id must not contain NUL")
    if branch_id in {".", ".."}:
        raise BranchError(f"branch_id {branch_id!r} is not allowed")
    if any(separator in branch_id for separator in ("/", "\\")):
        raise BranchError(
            f"branch_id {branch_id!r} must not contain path separators"
        )
    root = branch_root.resolve()
    candidate = (branch_root / branch_id).resolve()
    if not candidate.is_relative_to(root):
        raise BranchError(
            f"branch_id {branch_id!r} resolves outside the branch root"
        )
    return branch_id


class _FileLock:
    """Cross-process advisory lock via exclusive-create, released on exit.

    There is deliberately no automatic stale-lock recovery: a lock file held
    by another process is never deleted here, so a long-running writer cannot
    have its lock stolen.  Acquisition waits until the timeout and then fails
    closed with ``BranchError``.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        timeout: float = 5.0,
        poll: float = 0.02,
    ) -> None:
        self.path = Path(path)
        self.timeout = float(timeout)
        self.poll = float(poll)
        self._held = False

    def __enter__(self) -> "_FileLock":
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fd = _exclusive_create(self.path)
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise BranchError(
                        f"Could not acquire file lock {self.path} within timeout"
                    ) from None
                time.sleep(self.poll)
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"created_at": time.time()}, fh, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            self._held = True
            return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._held:
            try:
                self.path.unlink(missing_ok=True)
            finally:
                self._held = False
        return None


# ---------------------------------------------------------------------------
# Runtime lock
# ---------------------------------------------------------------------------


class RuntimeLock:
    """Single-writer lock per branch, safe across processes.

    Acquisition uses atomic exclusive-create (``O_CREAT|O_EXCL``), so two
    processes racing to acquire the same lock never both succeed.  Ownership
    checks are token-based; release and cleanup only touch a lock file whose
    token matches the caller's.
    """

    def __init__(self, lock_path: str | Path) -> None:
        self.lock_path = Path(lock_path)
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def acquire(
        self,
        run_id: str,
        branch_id: str,
        token: Optional[str] = None,
    ) -> str:
        token = token or uuid.uuid4().hex
        with self._lock:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = _exclusive_create(self.lock_path)
            except FileExistsError:
                raise RuntimeLockError(
                    f"Runtime lock already held at {self.lock_path}"
                ) from None
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(
                        {
                            "token": token,
                            "run_id": run_id,
                            "branch_id": branch_id,
                            "created_at": time.time(),
                        },
                        fh,
                        sort_keys=True,
                    )
                    fh.flush()
                    os.fsync(fh.fileno())
            except BaseException:
                # We created this lock file; clean up only our own artifact.
                try:
                    self.lock_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
        return token

    def token(self) -> Optional[str]:
        with self._lock:
            if not self.lock_path.exists():
                return None
            try:
                payload = json.loads(self.lock_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
            return str(payload.get("token") or "")

    def is_held(self, token: str) -> bool:
        return self.token() == token

    def release(self, token: str) -> None:
        with self._lock:
            current = self.token()
            if current is None:
                raise RuntimeLockError("Runtime lock is not held")
            if current != token:
                raise RuntimeLockError(
                    "Runtime lock release requires the owning token"
                )
            self.lock_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Budget adapter
# ---------------------------------------------------------------------------


class ArticleBudgetAdapter:
    """Thin adapter over BudgetScheduler; no second arithmetic ledger.

    Reserved/consumed state is read directly from the scheduler snapshot;
    released state is derived from the scheduler's own append-only events.
    """

    def __init__(self, scheduler: BudgetScheduler) -> None:
        self.scheduler = scheduler

    def can_reserve(self, action_id: str, **amounts: Any) -> bool:
        return self.scheduler.can_reserve(action_id, amounts or None, **{})

    def reserve(self, action_id: str, **amounts: Any) -> bool:
        return self.scheduler.reserve(action_id, amounts or None, **{})

    def commit(self, action_id: str, **amounts: Any) -> bool:
        return self.scheduler.commit(action_id, amounts or None, **{})

    def release(self, action_id: str) -> bool:
        return self.scheduler.release(action_id)

    @staticmethod
    def _ledgers_from_snapshot(snapshot: Mapping[str, Any]) -> Dict[str, Any]:
        """Derive all three ledgers from one scheduler snapshot."""

        reserved_raw = snapshot.get("reserved", {})
        resource_names = list(reserved_raw)
        totals: Dict[str, float] = {name: 0.0 for name in resource_names}
        for event in snapshot.get("events", []):
            if event.get("event_type", event.get("type")) != "release":
                continue
            released = event.get("released_usage") or event.get("reserved_usage") or {}
            for name, amount in released.items():
                if name in totals:
                    totals[name] += float(amount)
        released = {
            name: (int(value) if float(value).is_integer() else value)
            for name, value in totals.items()
        }
        return {
            "reserved": dict(reserved_raw),
            "consumed": dict(snapshot.get("committed", {})),
            "released": released,
        }

    def reserved_ledger(self) -> Dict[str, Any]:
        return dict(self.scheduler.snapshot()["reserved"])

    def consumed_ledger(self) -> Dict[str, Any]:
        return dict(self.scheduler.snapshot()["committed"])

    def released_ledger(self) -> Dict[str, Any]:
        return self._ledgers_from_snapshot(self.scheduler.snapshot())["released"]

    def three_ledgers(self) -> Dict[str, Any]:
        return self._ledgers_from_snapshot(self.scheduler.snapshot())

    def snapshot(self) -> Dict[str, Any]:
        scheduler_snapshot = self.scheduler.snapshot()
        return {
            "schema_version": "article-budget-adapter.v1",
            "scheduler": scheduler_snapshot,
            "ledgers": self._ledgers_from_snapshot(scheduler_snapshot),
        }


# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------


class ArticleCheckpointManager:
    """Versioned, atomically-written runtime checkpoints with resume checks."""

    SCHEMA_VERSION = "article-checkpoint.v1"

    @staticmethod
    def compute_graph_digest(graph_export: Mapping[str, Any]) -> str:
        canonical = json.dumps(
            dict(graph_export),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def compute_file_hashes(paths: Mapping[str, str | Path]) -> Dict[str, str]:
        result: Dict[str, str] = {}
        for artifact_id, path in paths.items():
            digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            result[str(artifact_id)] = digest
        return result

    def save(self, checkpoint: ArticleCheckpoint, path: str | Path) -> ArticleCheckpoint:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(target, checkpoint.model_dump(mode="json"))
        return checkpoint

    def load(
        self,
        path: str | Path,
        *,
        expected_run_id: Optional[str] = None,
        expected_lock: Optional[str] = None,
        expected_runtime_fingerprint: Optional[str] = None,
        graph_export: Optional[Mapping[str, Any]] = None,
        artifact_paths: Optional[Mapping[str, str | Path]] = None,
    ) -> ArticleCheckpoint:
        """Load and validate a checkpoint before resume.

        Raises ``CheckpointMismatchError`` for schema, run, writer-token,
        runtime-fingerprint, graph-digest, budget-completeness, or
        artifact-hash mismatches and ``CheckpointError`` for unreadable or
        malformed files.
        """

        source = Path(path)
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CheckpointError(
                f"Could not read checkpoint {source}: {exc}"
            ) from exc
        if not isinstance(raw, Mapping):
            raise CheckpointError("Checkpoint must be a JSON object")
        if raw.get("schema_version") != self.SCHEMA_VERSION:
            raise CheckpointMismatchError(
                f"Unsupported checkpoint schema: {raw.get('schema_version')!r}"
            )
        try:
            checkpoint = ArticleCheckpoint.model_validate(raw)
        except Exception as exc:
            raise CheckpointError(f"Malformed checkpoint {source}: {exc}") from exc

        if expected_run_id is not None and checkpoint.run_id != expected_run_id:
            raise CheckpointMismatchError(
                f"Checkpoint run_id {checkpoint.run_id!r} does not match expected "
                f"{expected_run_id!r}"
            )
        if expected_lock is not None and checkpoint.runtime_lock != expected_lock:
            raise CheckpointMismatchError("Runtime lock token does not match checkpoint")
        if expected_runtime_fingerprint is not None:
            if checkpoint.runtime_fingerprint is None:
                raise CheckpointMismatchError(
                    "Checkpoint has no runtime fingerprint to validate"
                )
            if checkpoint.runtime_fingerprint != expected_runtime_fingerprint:
                raise CheckpointMismatchError(
                    "Runtime fingerprint does not match checkpoint"
                )

        self._validate_completeness(checkpoint)

        stored_export = checkpoint.graph_export
        if stored_export is not None:
            actual_digest = self.compute_graph_digest(stored_export)
            if actual_digest != checkpoint.graph_digest:
                raise CheckpointMismatchError(
                    "Checkpoint graph export digest does not match stored graph_digest"
                )
        if graph_export is not None:
            provided_digest = self.compute_graph_digest(graph_export)
            if provided_digest != checkpoint.graph_digest:
                raise CheckpointMismatchError(
                    "Provided graph export does not match checkpoint graph_digest"
                )
        if artifact_paths is not None:
            actual_hashes = self.compute_file_hashes(artifact_paths)
            for artifact_id, digest in checkpoint.artifact_hashes.items():
                if artifact_id not in actual_hashes:
                    raise CheckpointMismatchError(
                        f"Checkpoint artifact {artifact_id!r} has no path to verify"
                    )
                if actual_hashes[artifact_id] != digest:
                    raise CheckpointMismatchError(
                        f"Artifact {artifact_id!r} hash does not match checkpoint"
                    )
        return checkpoint

    def _validate_completeness(self, checkpoint: ArticleCheckpoint) -> None:
        """Validate budget and artifact completeness using existing authority."""

        budget_path = checkpoint.budget_checkpoint_path
        if budget_path is not None:
            budget_file = Path(budget_path)
            if not budget_file.is_file():
                raise CheckpointMismatchError(
                    f"Budget checkpoint path does not exist: {budget_path}"
                )
            try:
                raw = json.loads(budget_file.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise CheckpointMismatchError(
                    f"Budget checkpoint is unreadable: {budget_path}"
                ) from exc
            if not isinstance(raw, Mapping):
                raise CheckpointMismatchError("Budget checkpoint must be a JSON object")
            if raw.get("schema_version") != BudgetScheduler.CHECKPOINT_SCHEMA_VERSION:
                raise CheckpointMismatchError("Budget checkpoint schema is unsupported")
            limits_raw = raw.get("limits")
            if not isinstance(limits_raw, Mapping):
                raise CheckpointMismatchError("Budget checkpoint has no limits")
            try:
                limits = BudgetLimits.from_dict(dict(limits_raw))
                # Reconstructing the scheduler validates the append-only event
                # sequence exactly like resume would; no arithmetic is invented.
                BudgetScheduler(limits, checkpoint_path=budget_file)
            except Exception as exc:
                raise CheckpointMismatchError(
                    f"Budget checkpoint fails scheduler resume validation: {exc}"
                ) from exc

        if not budget_path and not checkpoint.budget_snapshot:
            raise CheckpointMismatchError(
                "Checkpoint has neither a budget snapshot nor a budget checkpoint path"
            )
        if checkpoint.budget_snapshot:
            for key in ("limits", "committed", "reserved", "remaining"):
                if key not in checkpoint.budget_snapshot:
                    raise CheckpointMismatchError(
                        f"Budget snapshot is incomplete: missing {key!r}"
                    )

        if set(checkpoint.artifact_ids) != set(checkpoint.artifact_hashes):
            raise CheckpointMismatchError(
                "Artifact IDs and artifact hashes do not match"
            )
        for digest in checkpoint.artifact_hashes.values():
            if not isinstance(digest, str) or not digest:
                raise CheckpointMismatchError(
                    "Artifact hashes must be non-empty strings"
                )

    def build(
        self,
        *,
        run_id: str,
        branch_id: str,
        stage: ArticleStage | str,
        graph_export: Mapping[str, Any],
        budget_snapshot: Mapping[str, Any],
        budget_checkpoint_path: Optional[str] = None,
        runtime_lock: str,
        runtime_fingerprint: Optional[str] = None,
        random_seeds: Optional[Mapping[str, Any]] = None,
        artifact_hashes: Optional[Mapping[str, str]] = None,
        memory_path: Optional[str] = None,
        previous_checkpoint: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> ArticleCheckpoint:
        stage_value = (
            stage.value if isinstance(stage, ArticleStage) else ArticleStage(stage)
        )
        if runtime_fingerprint is None:
            runtime_fingerprint = article_runtime_fingerprint()
        return ArticleCheckpoint(
            run_id=run_id,
            branch_id=branch_id,
            stage=stage_value,
            graph_digest=self.compute_graph_digest(graph_export),
            graph_export=dict(graph_export),
            budget_snapshot=dict(budget_snapshot),
            budget_checkpoint_path=budget_checkpoint_path,
            runtime_lock=runtime_lock,
            runtime_fingerprint=runtime_fingerprint,
            random_seeds=dict(random_seeds or {}),
            artifact_ids=list(artifact_hashes or {}),
            artifact_hashes=dict(artifact_hashes or {}),
            memory_path=memory_path,
            previous_checkpoint=previous_checkpoint,
            created_at=created_at,
        )


# ---------------------------------------------------------------------------
# Branch fork
# ---------------------------------------------------------------------------


class ArticleBranchManager:
    """Branch registry with isolated output namespaces and read-only inputs.

    ``input_namespace`` (``shared_inputs/``) is a shared, read-only source of
    input artifacts: forks reference it and normal branch output operations
    never write there.  Each branch owns its own ``branches/<id>/outputs``
    namespace.  Registry updates are serialized by an exclusive-create file
    lock, validated before write, and appended atomically; malformed or
    conflicting registry state is rejected instead of silently overwritten.
    """

    REGISTRY_NAME = "BRANCHES.json"
    REGISTRY_SCHEMA_VERSION = "article-branches.v1"

    def __init__(self, branch_root: str | Path, run_id: str) -> None:
        self.branch_root = Path(branch_root)
        self.branch_root.mkdir(parents=True, exist_ok=True)
        self.run_id = str(run_id)
        self.registry_path = self.branch_root / self.REGISTRY_NAME
        self._lock = threading.RLock()
        self._registry_lock = _FileLock(self.registry_path.with_suffix(".lock"))
        self.input_namespace = self.branch_root / "shared_inputs"
        self.input_namespace.mkdir(parents=True, exist_ok=True)

    def _load_registry(self) -> List[Dict[str, Any]]:
        if not self.registry_path.exists():
            return []
        try:
            raw = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise BranchError(f"Branch registry is malformed: {exc}") from exc
        if not isinstance(raw, Mapping):
            raise BranchError("Branch registry must be a JSON object")
        if raw.get("schema_version") != self.REGISTRY_SCHEMA_VERSION:
            raise BranchError(
                f"Unsupported branch registry schema: {raw.get('schema_version')!r}"
            )
        if str(raw.get("run_id") or "") != self.run_id:
            raise BranchError(
                f"Branch registry run_id does not match {self.run_id!r}"
            )
        branches = raw.get("branches")
        if not isinstance(branches, list):
            raise BranchError("Branch registry 'branches' must be a list")
        states = [dict(item) for item in branches]
        seen: set[str] = set()
        for item in states:
            branch_id = item.get("branch_id")
            if not isinstance(branch_id, str) or not branch_id:
                raise BranchError(
                    "Branch registry contains a record without branch_id"
                )
            if branch_id in seen:
                raise BranchError(
                    f"Branch registry contains duplicate branch_id: {branch_id!r}"
                )
            seen.add(branch_id)
        return states

    def _write_registry(self, branches: Sequence[Mapping[str, Any]]) -> None:
        atomic_write_json(
            self.registry_path,
            {
                "schema_version": self.REGISTRY_SCHEMA_VERSION,
                "run_id": self.run_id,
                "branches": [dict(item) for item in branches],
            },
        )

    def list_branches(self) -> List[ArticleBranchState]:
        with self._lock:
            raw = self._load_registry()
        states = [ArticleBranchState.model_validate(item) for item in raw]
        states.sort(key=lambda item: (item.created_at or "", item.branch_id))
        return states

    def get_branch(self, branch_id: str) -> ArticleBranchState:
        for branch in self.list_branches():
            if branch.branch_id == branch_id:
                return branch
        raise BranchError(f"Unknown branch_id: {branch_id}")

    def fork(
        self,
        parent_branch_id: Optional[str],
        *,
        stage: ArticleStage | str,
        graph_export: Mapping[str, Any],
        budget_snapshot: Mapping[str, Any],
        runtime_lock_token: Optional[str] = None,
        runtime_fingerprint: Optional[str] = None,
        random_seeds: Optional[Mapping[str, Any]] = None,
        branch_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> ArticleBranchState:
        """Fork a new branch head with an isolated output namespace.

        The parent branch state and its output namespace are never modified.
        The new branch references the shared input namespace (read-only shared
        artifacts; normal output operations never write there) and receives its
        own initial checkpoint as the branch head.  Duplicate branch IDs are
        rejected before any directory or lock is created, and if checkpoint or
        registry updates fail, all artifacts created by this fork are removed.
        """

        if branch_id is not None:
            new_id = _validate_branch_id(branch_id, self.branch_root)
        else:
            new_id = uuid.uuid4().hex[:16]
        branch_dir = self.branch_root / new_id
        output_namespace = branch_dir / "outputs"
        with self._lock, self._registry_lock:
            branches = self._load_registry()
            existing_ids = {item["branch_id"] for item in branches}
            if new_id in existing_ids:
                raise BranchError(f"Duplicate branch_id: {new_id!r}")
            if branch_dir.exists():
                raise BranchError(
                    f"Branch directory already exists: {branch_dir}"
                )
            if parent_branch_id is not None:
                parent_state = next(
                    (
                        item
                        for item in branches
                        if item["branch_id"] == parent_branch_id
                    ),
                    None,
                )
                if parent_state is None:
                    raise BranchError(f"Unknown branch_id: {parent_branch_id}")
                if parent_state.get("run_id") != self.run_id:
                    raise BranchError("Parent branch belongs to a different run")
            created = False
            try:
                output_namespace.mkdir(parents=True, exist_ok=True)
                created = True
                lock = RuntimeLock(branch_dir / "runtime.lock")
                token = lock.acquire(
                    self.run_id, new_id, token=runtime_lock_token
                )
                checkpoint = ArticleCheckpointManager().build(
                    run_id=self.run_id,
                    branch_id=new_id,
                    stage=stage,
                    graph_export=graph_export,
                    budget_snapshot=budget_snapshot,
                    runtime_lock=token,
                    runtime_fingerprint=(
                        runtime_fingerprint or article_runtime_fingerprint()
                    ),
                    random_seeds=random_seeds,
                    created_at=created_at,
                )
                head_path = branch_dir / "checkpoints" / "checkpoint.v1.json"
                ArticleCheckpointManager().save(checkpoint, head_path)
                state = ArticleBranchState(
                    branch_id=new_id,
                    run_id=self.run_id,
                    parent_branch_id=parent_branch_id,
                    head_checkpoint=str(head_path),
                    output_namespace=str(output_namespace),
                    input_namespace=str(self.input_namespace),
                    state=BranchState.active,
                    created_at=created_at,
                )
                branches.append(state.model_dump(mode="json"))
                self._write_registry(branches)
            except BaseException:
                if created:
                    shutil.rmtree(branch_dir, ignore_errors=True)
                raise
        return state


__all__ = [
    "ArticleBranchManager",
    "ArticleBranchState",
    "ArticleBudgetAdapter",
    "ArticleCheckpoint",
    "ArticleCheckpointManager",
    "BranchError",
    "BranchState",
    "CheckpointError",
    "CheckpointMismatchError",
    "RuntimeLock",
    "RuntimeLockError",
]
