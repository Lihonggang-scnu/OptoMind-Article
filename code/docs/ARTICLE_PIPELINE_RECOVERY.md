# Article Pipeline Recovery (Stage 17A checkpoint/resume)

Date: 2026-08-16
Modules: `optomind_optics/harness/article_pipeline.py`,
`optomind_optics/harness/article_pipeline_recovery.py`

## Purpose

Stage 17A adds production checkpoint/resume and idempotent continuation for
the accepted eight-stage `ArticlePipeline`.  `ArticlePipeline.run` remains
the write-once new-run entry; `ArticlePipeline.resume` accepts only the same
immutable request and an existing `work_dir` and continues from the first
uncommitted stage.

The recovery ledger is deliberately thin: it indexes the pipeline's own
`StageReceipt`s, stage snapshot hashes, event-log prefixes, and
execution/asset route progress.  It is not a second physics, budget, graph,
or artifact authority, and it never duplicates the trusted upstream
validators.

## What is persisted per committed stage

After every committed stage (including failed, unavailable, partial, and
skipped terminal outcomes) the runner writes:

- `checkpoint-NN-stage.json`: an immutable `PipelineCheckpointRecord` with
  schema version, request identity digest (excluding `work_dir`), runtime
  fingerprint, stage sequence/name/status, the exact `StageReceipt`, the
  stage snapshot filename/SHA256/payload digest, the event-log prefix digest,
  the previous checkpoint ID, a recomputed checkpoint ID, and a hard-failure
  flag.  Committed records are never rewritten.
- `RECOVERY_LEDGER.json`: the latest pointer plus the committed checkpoint
  file list, atomically overwritten on each commit.
- `PIPELINE_EVENTS.jsonl` and the canonical stage snapshot, as before.

Execution (stage 7) and asset compilation (stage 8) additionally persist
route-level progress in `ROUTE_PROGRESS.json`, keyed by
`request_id`/`task_hash` and asset result identity, with canonical
`route-execution-*.json` / `route-asset-*.json` snapshots.  An interruption
after route 1 of N therefore never re-invokes route 1.

## Resume validation (fail-closed, before any adapter runs)

`validate_recovery_state` rejects the resume before any downstream adapter
call when:

- `REQUEST.json` is missing, invalid, or differs from the immutable resume
  request;
- the runtime fingerprint does not match the ledger/checkpoints;
- the checkpoint chain is out of order, breaks `previous_checkpoint_id`
  links, or a `checkpoint_id` does not recompute from its content;
- a snapshot, event prefix, receipt field, or route-progress digest does not
  match; a committed file is missing, or an extra committed artifact
  (checkpoint/snapshot/route file) appears;
- execution/asset route snapshots are missing, have wrong hashes/payloads,
  or reference unknown requests;
- the runtime lock is held by another writer (stale locks are never
  auto-deleted or stolen).

The pipeline then rebuilds payloads as the existing strict Pydantic models
and re-applies the same cross-stage identity validation (analysis question,
research/strategy problem ids, director question/charter/capability, binding
and planning contracts) for committed usable stages.  Route progress is
validated against the current compiled requests/executions, and every
restored asset passes the public `validate_asset_compilation_result` with
its request/execution.  Re-hashed JSON is never trusted by itself.

## Crash-consistent stage and route commits

Each atomically written `checkpoint-NN-stage.json` is the durable stage
commit record; `RECOVERY_LEDGER.json` is only a recoverable latest
pointer/cache.  Under the runtime lock, resume first reconciles:

- every ledger-listed checkpoint is validated normally;
- if exactly the next checkpoint exists outside the ledger, it is returned
  as a provisional ``pending`` checkpoint only after its full chain,
  request/runtime identity, snapshot bytes/digest, receipt, event prefix,
  stage order, and stage-specific route-progress digest validate; the ledger
  pointer is NOT mutated during validation;
- after the pipeline rebuilds the pending payload as a strict Pydantic model
  and passes all cross-stage/route semantic identity validation,
  ``promote_pending_checkpoint`` re-checks the current ledger against the
  expected pre-promotion state and atomically appends only that pending
  checkpoint.  If semantic validation fails, the ledger bytes and committed
  list remain byte-for-byte unchanged, and the same failure recurs on every
  resume (no third checkpoint is ever observed);
- a stage event/snapshot without a committed checkpoint is uncommitted local
  output: the event log is truncated to the committed checkpoint prefix and
  the next stage overwrites its deterministic snapshot (never reused as
  committed work);
- conflicting, malformed, out-of-order, or multiple unexpected checkpoints,
  and missing ledger-listed records, fail closed;
- a valid `REQUEST.json` plus a crash before the first ledger/checkpoint may
  be initialized to an empty ledger and resume at stage 1; arbitrary
  non-empty/junk directories still fail closed.

For routes, `ROUTE_PROGRESS.json` is the atomic commit authority.  A route
snapshot not referenced by progress is uncommitted and is never reused; it
may be deterministically overwritten when that route reruns.  Once progress
contains a route key, a repeat write with byte/semantic-identical snapshot
and entry is a no-op, while different payload/identity/warnings for the same
route key is a fail-closed `RecoveryIntegrityError` that leaves the original
unchanged.  The no-op is granted only when the committed entry's stored
SHA256 equals the exact canonical snapshot bytes AND the on-disk bytes still
match that SHA; semantically equivalent reformatting or a corrupted stored
SHA is a fail-closed conflict.  `request_id` is the unique route identity:
a repeat write with the same `request_id` but a different `task_hash` is
rejected before mutation, and duplicate `request_id` entries (even with
different hashes) already present in progress fail closed before any adapter
runs.  An asset commit additionally requires exactly one committed execution
progress entry matching request/task/run/branch identity with a verified
snapshot; an orphan execution snapshot never authorizes an asset commit.

It remains documented that a process can die after an external solver or
compiler side effect but before any local route commit record; deterministic
request IDs and the underlying executor remain the final idempotency
authority for that unavoidable gap.

## Terminal and interrupted runs

- All eight stages committed but `FINAL_PIPELINE_RESULT.json` missing (crash
  during the final write): resume rebuilds the exact final result with zero
  adapter calls.
- Final result present and valid: repeated resume returns the identical
  result/`result_id` with zero adapter calls, including `partial` and
  soft-`failed` terminal runs (provider failures are never silently retried;
  a future explicit retry policy is separate).
- Interrupted mid-run: committed stages are never re-executed; resume starts
  at the first uncommitted stage.  Route-level progress prevents re-running
  committed execution/asset routes.

## Interruption and locking

`run`/`resume` acquire the single-writer `RuntimeLock` (`PIPELINE_RUNTIME.lock`)
and release it in `finally`.  A `BaseException`-style injected interruption
propagates after leaving a valid recoverable ledger and releasing only this
process's lock; it is never misreported as a provider failure.  Ordinary
`Exception` behavior is unchanged (fail-open per the accepted pipeline).

The unavoidable process-death window between an external side effect (for
example a solver or asset call) and its atomic route-progress commit is
documented: deterministic request IDs and the underlying executor remain the
final idempotency authority, and a crash inside that window leaves the prior
committed ledger intact.

## Determinism and identity

No wall-clock time, credentials, lock tokens, or `work_dir` paths enter
checkpoint IDs or result IDs.  Identical deterministic runs in different
empty directories produce identical checkpoint records and identical
`result_id`s.
