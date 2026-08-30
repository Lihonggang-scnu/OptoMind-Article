# Article Continuation

## Purpose

`article_continuation.py` is an additive, production-oriented, resumable
continuation from an accepted eight-stage `ArticlePipeline` run into the next
bounded milestone:

1. `result_synthesis` — `synthesize_article_results` (derived plan, claim
   ledger, contracted evidence observations)
2. `architecture` — `build_article_architecture` + deterministic story
   selection
3. `writing` — `build_article_draft_bundle`
4. `review` — `build_article_review`
5. `manuscript` — `build_article_manuscript`

It calls the accepted existing APIs only and never copies their scientific
logic.  Reproducibility/fresh replay, presentation, and delivery are
intentionally not part of this milestone.

## Source trust boundary

`load_source_pipeline(source_dir)` reads, without mutation:

- `REQUEST.json` and `FINAL_PIPELINE_RESULT.json` (result_id recomputed and
  verified)
- `ROUTE_PROGRESS.json` plus every committed `route-execution-*.json` and
  `route-asset-*.json`
- Every snapshot's SHA256 and payload digest are recomputed and compared
  against route progress; request/task/run/asset identities are cross-checked
  against the strict models and `FINAL_PIPELINE_RESULT.json`.

Missing, duplicate, cross-wired, stale, or extra committed route data fails
closed before any provider is called.

## Continuation work directory

`ArticleContinuation.run(request)` requires an empty work directory and
persists:

- `CONTINUATION_REQUEST.json`
- per-stage snapshots `NN-<stage>.json`
- `CONTINUATION_EVENTS.jsonl`
- `CONTINUATION_ATTEMPTS.jsonl` (append-only retryable attempt audit)
- `CONTINUATION_VERSIONS.jsonl` (append-only final-version audit)
- `checkpoint-NN-<stage>.json` records and `CONTINUATION_LEDGER.json`
  (request/source digest, runtime fingerprint, previous-ID chain)
- `FINAL_CONTINUATION_RESULT.json`
- `manuscript/` (manuscript package files)

`ArticleContinuation.resume(request)` validates the whole committed chain and
skips provider calls for accepted completed stages.  A completed run with a
valid final result is returned idempotently with zero adapter calls.  A crash
between stages resumes at the first uncommitted stage; committed snapshots are
never re-executed.

## Source/work isolation

Paths are resolved before anything is written.  `work_dir` equal to or nested
under `source_pipeline_dir` is rejected, and an overlap attempt writes zero
bytes to the source tree.

## Retryable attempts and accepted checkpoints

The stage chain is an accepted-output chain, not an attempt chain:

- Accepted stages persist strict model snapshots, an event, a checkpoint, and
  the ledger.  Failed/unavailable attempts never checkpoint arbitrary error
  dicts under a scientific stage; they are recorded in the append-only
  `CONTINUATION_ATTEMPTS.jsonl` audit and are retried on resume.
- Resume retries only the first stage without an accepted checkpoint and
  never repeats an accepted earlier provider.

## Event authority and crash windows

`CONTINUATION_EVENTS.jsonl` is the append-only event authority.  Every
event's `event_id`, sequence/stage/status/payload digest, and each
checkpoint's event-prefix digest are recomputed on resume.  Altered, missing,
duplicated, extra, reordered, or malformed events fail closed before any
provider runs.

Deterministic fault-injection hooks (`fault_hook`) exercise the four commit
boundaries:

- after snapshot write: the uncommitted snapshot is reused and the stage
  safely reruns;
- after event write: the exact snapshot/event finish the commit without a
  provider re-call;
- after checkpoint write: the exact orphan checkpoint is promoted without a
  provider re-call;
- after ledger write: the committed stage continues normally.

No duplicate events or provider charges are produced, and evidence is never
deleted to recover.

## Final result progression

An old partial/unavailable `FINAL_CONTINUATION_RESULT.json` never blocks
resume or remains stale; it is atomically replaced after the next accepted
run.  A completed final is accepted only after being reconstructed in full
from the committed typed payloads (stage payloads, selected story,
candidates/rationale, counts, usage, warnings/errors/status) and compared.
Version records are appended to `CONTINUATION_VERSIONS.jsonl`.

## Fail-open / fail-closed

- Ordinary provider unavailability or malformed responses fail open per
  stage, are recorded honestly, and stop at the first stage lacking usable
  output without deleting previous assets.
- Source/identity/hash/path conflicts, unknown selected story, contracted
  inventory ambiguity (duplicate artifact IDs across routes), and final
  result tampering fail closed.
- Advisory review findings do not stop manuscript assembly; deterministic
  source/integrity blockers retain partial/blocked outputs honestly.

## Story selection

An explicit `selected_story_id` is honored; otherwise the valid story with
the highest `recommendation_score` is chosen deterministically, tie-broken by
`story_id`.  The rationale and all candidates are recorded in the result.

## CLI

`code/scripts/run_article_continuation.py`:

```
--source-pipeline-dir DIR --work-dir DIR --run-id ID
[--branch-id root] [--selected-story-id ID] [--resume]
```

The CLI assembles the locked `qwen3.7-flash` adapters
(`QwenArticleResultClaimSynthesizer`, `QwenArticleArchitecturePlanner`,
`QwenSectionWriter`, `QwenFormatRepair`, `QwenScientificReviewer`,
`QwenExpressionReviewer`, `QwenAuthorReviser`); credentials are read only by
the existing Qwen client environment behavior.  It prints a compact JSON
summary and returns 0 for completed/partial, 2 for unavailable, 1 for
failed/configuration errors.

## Output files

- `NN-<stage>.json` — strict stage snapshots
- `checkpoint-NN-<stage>.json`, `CONTINUATION_LEDGER.json` — tamper-evident
  resume state
- `FINAL_CONTINUATION_RESULT.json` — `ContinuationResult` with statuses, IDs,
  story selection, counts, and aggregated Qwen usage (no double counting)
- `manuscript/` — manuscript body, package, and source map
