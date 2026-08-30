# Article Memory & Runtime — Phase 3A Design Note

Date: 2026-08-15
Scope: separated evidence/run-memory/fact stores, versioned runtime
checkpoints, a thin budget adapter, and branch fork isolation.  Reuses
existing budget/provenance/replay/cost-ledger infrastructure; no solver,
compiler, or orchestrator behavior changes.

## Memory domains (`optomind_optics/harness/article_memory.py`)

One append-only SQLite store, three strictly separated typed domains:

- `MethodEvidence` — source, scope, query, excerpt hash, evidence level, time,
  optional artifact reference, and metadata.  Used for literature/evidence
  retrieval records; can never authorize a task or physics certificate.
- `RunMemoryRecord` — run/event/graph/artifact references and operational
  notes only.  It is not a scientific fact and is never returned by the fact
  registry API.
- `FactRecord` — immutable fact statement that MUST carry at least one source
  artifact ID (`source_artifact_ids` has `min_length=1`).  Corrections append a
  superseding fact via `supersede_fact`; the old payload row is never mutated
  (verified byte-for-byte in tests) and its superseded status is derived from
  an append-only `fact_status_events` table.

Invariants: duplicate identities raise `DuplicateRecordError`; in-place fact
mutation is impossible (no update API); lineage queries
(`fact_lineage`) are deterministic (origin → latest); records serialize
deterministically and round-trip through JSON; unknown extra fields are
dropped so secret-like payloads are not persisted.

Cross-process consistency:
- Fact corrections run inside a `BEGIN IMMEDIATE` SQLite write transaction
  with a unique invariant `fact_status_events(fact_id)` (one supersede event
  per fact).  Exactly one concurrent supersede wins; losers fail with
  `FactMutationError` and roll back their corrected fact row and event, so no
  orphan rows remain.
- `snapshot()` and `fact_records()` read through one SQLite connection inside
  a single read transaction, so evidence, run memory, and facts are returned
  from one consistent view with deterministic ordering.

## Runtime (`optomind_optics/harness/article_runtime.py`)

### Checkpoints

`ArticleCheckpoint` (schema `article-checkpoint.v1`) carries run_id, branch_id,
stage, graph export + digest, budget snapshot, runtime lock token, random
seeds, artifact IDs/hashes, memory path, previous-checkpoint reference, and a
separate runtime fingerprint.  New checkpoints built through
`ArticleCheckpointManager.build` always carry both the writer lock token and a
real runtime fingerprint computed from the existing Article runtime-fingerprint
authority (`runtime_fingerprint.py`: source-tree SHA-256 plus interpreter and
dependency metadata); callers that omit one get the authority value
automatically.  `ArticleCheckpoint.runtime_fingerprint` stays optional only so
legacy checkpoints can still be loaded.  Resume validation checks the writer
token and the runtime fingerprint independently.
`ArticleCheckpointManager.save` writes atomically (temp + rename via the
existing `artifact_store.atomic_write_json`); `load` rejects schema, run,
writer-token, runtime-fingerprint, graph-digest, budget-completeness, and
artifact-hash mismatches before resume.  Budget completeness is validated
against the existing `BudgetScheduler` checkpoint format (limits + append-only
event sequence) and artifact completeness requires `artifact_ids` and
`artifact_hashes` to match; no physics validation is invented.

### Budget adapter

`ArticleBudgetAdapter` wraps `BudgetScheduler` without changing its behavior:
reserve/commit/release delegate directly, reserved/consumed ledgers are read
from the scheduler snapshot, and the released ledger is derived from the
scheduler's own append-only events.  `three_ledgers()` and `snapshot()` derive
all ledgers from one scheduler snapshot per call, so callers never observe
inconsistent reserved/consumed/released state.  No second arithmetic ledger is
created.

### Runtime lock

`RuntimeLock` is a single-writer token file per branch.  Acquisition uses
atomic exclusive-create (`O_CREAT|O_EXCL`), which is safe across processes (no
exists-then-write race); ownership is token-checked, and failed acquisitions
clean up only the lock file they created.  Checkpoint resume validates the
writer token.

### Branch fork

`ArticleBranchManager` maintains an append-only `BRANCHES.json` registry.
`fork` creates a new branch head (an initial checkpoint), an isolated output
namespace under `branches/<id>/outputs`, and a reference to the shared
read-only input namespace (`shared_inputs`); normal branch output operations
never write to shared inputs.  Duplicate branch IDs are rejected before any
directory or lock is created, and if checkpoint or registry updates fail, all
artifacts created by the failed fork are rolled back.  Registry updates are
serialized by an exclusive-create file lock (cross-process safe) and the
registry is validated before every write: malformed JSON, wrong schema/run,
missing `branch_id`, or conflicting duplicate branch records are rejected
instead of silently overwritten.  There is no automatic stale-lock recovery: a
lock held by another process is never deleted, even past any age threshold;
acquisition waits until timeout and fails closed.  Branch IDs are validated
before any filesystem use (empty, dot/dot-dot, NUL, separators, absolute paths,
and paths resolving outside the branch root are rejected).  The parent branch
state and its output namespace are never modified.

## Reused infrastructure

- `BudgetScheduler` — reservation/commit/release authority (adapter only).
- `ArtifactLineageStore` — file-integrity authority; memory/checkpoint records
  reference artifact IDs rather than duplicating hashing semantics.
- `replay_completed_run` / `_scientific_digest` — replay and digest helpers
  remain the replay authority; checkpoints store digests for comparison.
- `CostLedger` — cost authority; checkpoints reference budget snapshots and
  never recompute or duplicate cost arithmetic.

## No-secret rule

All new records and checkpoints use `extra="ignore"` pydantic models; secret-like
unknown fields are dropped before persistence, and tests assert the secret
string never appears in store, checkpoint, registry, or lock files.
