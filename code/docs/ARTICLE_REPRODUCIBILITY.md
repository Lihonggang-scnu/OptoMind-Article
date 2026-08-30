# Article Reproducibility - Stage 12B Design Note

Date: 2026-08-16
Module: `optomind_optics/harness/article_reproducibility.py`

## Purpose

Stage 12B selects only the experiments that materially support the assembled
Stage 12A manuscript, fresh-replays those critical completed runs through the
existing replay authority, produces immutable run/artifact lineage, and
generates an honest negative/failed/not-run appendix.  Stage 12B itself makes
no model or network call; production fresh replay does invoke the existing
local TMM execution authority via `replay.replay_completed_run` (never
`replace_existing=True`).

## Inputs and identity

- `ArticleDirectorPlan`, `ClaimLedgerResult`, `ArticleArchitectureResult`,
  `ArticleReviewResult`, `ArticleManuscriptPackage`, the selected story ID,
  the same `TrustedValueRecord`s used by Stages 10-12A, trusted
  `ArticleExecutionResult` records, a `runs_root`, and an injectable
  `ReplayProvider` (production default:
  `replay.replay_completed_run`, never `replace_existing=True`).
- The manuscript package is revalidated by the public
  `validate_manuscript_package` helper (Markdown re-render, flattened source
  map, repeated field consistency, derived status, recomputed
  `body_id`/`package_id`, upstream identities), and the Stage 11 review is
  revalidated by `validate_review_result`; manuscript `review_id`/`result_id`
  must match the supplied review.  After review revalidation, the expected
  manuscript is deterministically rebuilt with `build_article_manuscript` from
  the supplied review/inputs, and the supplied package must equal that
  rebuild exactly (model equality) before any replay; a self-consistent but
  forged manuscript with recomputed IDs is rejected.

## Critical experiment discovery

- Discovery starts only from assembled paragraph provenance:
  - paragraph `claim_ids` -> `ClaimCard.evidence_ids` (observation IDs) ->
    `ArticleExecutionResult.observation.experiment_id`;
  - paragraph `fact_ids` -> `FactRecord.source_artifact_ids` ->
    `ArtifactDescriptor` source experiment/observation;
  - paragraph `artifact_ids` -> `ArtifactDescriptor.source_experiment_ids` /
    `source_observation_ids`;
  - paragraph `figure_ids` -> FigureContract claim bindings (via claims) and
    artifact bindings (via the inventory).
- Every reason records paragraph/claim/fact/figure/artifact/observation IDs.
  Discovery is deduplicated and deterministic; unrelated runs are never
  replayed; literature/paper IDs are never treated as TMM experiments.
- For every manuscript-used descriptor, `source_observation_ids` must resolve
  and agree with `source_experiment_ids`; unknown or cross-wired sources
  block instead of silently disappearing.

## Hard validation

- Unique claims/facts/artifacts/observations/experiments and every
  cross-reference are validated.  A positive manuscript claim/figure that
  resolves to a missing execution, mismatched observation/experiment/task
  identity, non-physical source, missing source run, failed/mismatched
  replay, or source/replay hash mismatch is a publication blocker with
  manuscript-linked diagnostics.
- Only manuscript-used artifacts (`record.artifact_ids`, plus fact-derived
  manuscript artifacts) require an `ArtifactDescriptor` and its declared
  SHA-256; a missing SHA-256 there is a hard publication blocker.  Real Stage
  6 observation refs such as `TASK.json`, `EXPERIMENT_GRAPH.json`,
  `RUN_STATE.json`, execution markers, and certificates are validated as safe
  run-relative lineage refs when present, without pretending they are writing
  inventory assets.
- Artifact bytes resolve by preferring a matching run-local artifact-id
  reference, then the descriptor path inside the source run, then a
  global-relative descriptor path constrained to `runs_root` (so the common
  upstream `path='runs/example/FINAL_RESULT.json'` shape maps safely to the
  real run-local `FINAL_RESULT.json`).  All containment is checked after
  symlink resolution; source runs must be directories within `runs_root`.
- `physically_valid` and `physically_valid_with_limits` are replayable
  physical sources; limitation semantics are preserved in warnings.
- Source run and artifact paths are validated against traversal and
  containment within `runs_root`; existing manuscript artifact files are
  SHA-256 verified against the artifact inventory.
- Stage 6 `GatewayAdapterResult` receipts are parsed with canonical nested
  `telemetry` (`task_hash`, `request_id`, `run_id`, `run_dir`) plus any legacy
  top-level aliases; conflicting duplicate locations block.  A present
  `EXECUTION_MARKER.json` is an identity authority: it must be a JSON object
  with `task_hash`, `request_id`, `run_id`, and `status == "completed"`,
  matching the execution identity; malformed/incomplete/mismatched markers
  block before replay, and the marker file must resolve inside the source run
  directory (after symlink resolution) before it is read.  Receipt `run_dir`
  paths resolve directly when absolute and against the trusted `runs_root`
  when relative (matching the real adapter's `relative_to(work_root)` shape);
  relative paths that escape `runs_root` are rejected.  Run IDs from receipt,
  marker, and `FINAL_RESULT.json` are compared pairwise; any conflict blocks.
- Replay manifests are required to be internally consistent and successful:
  `source_task_sha256` is verified against the actual source `TASK.json`
  bytes, `source_run_id` against the trusted source run identity (receipt /
  `FINAL_RESULT.json`) when available, checks must be non-empty with unique
  safe paths, and `matched`/`total` counts must agree with the checks.  There
  is no caller-supplied manifest bypass; a forged "success" manifest is
  rejected.  A `source_run_id` mismatch makes the `ReplayRecord` `failed`
  (never `completed`); `replay_run_id` must be non-empty and task/check
  hashes must be 64-character hex digests.  Per-artifact lineage records
  distinguish byte identity (only when both actual files hash equal
  byte-for-byte) from the replay comparator's canonical scientific JSON
  identity.  Lineage is matched per experiment by `artifact_id` +
  `experiment_id`, then the record's `(relative_path, source_sha256)` must
  equal a successful manifest check (the artifact ID need not equal the
  filename); duplicate `(artifact_id, experiment_id, relative_path)`
  identities are rejected even when their lineage IDs differ.
- Any provenance blocker tied to a critical experiment or one of its
  manuscript artifact/observation/claim IDs (including `missing_hash` and
  `source_provenance_mismatch`) removes that experiment from the replayable
  set, so the provider is never called for a source-cross-wired or unhashed
  critical artifact.

## Appendix

Observations with `rejected_physics`/`needs_higher_fidelity`/`failed`/
`cancelled` status and CoverageMatrix rows explicitly marked
`failed`/`not_run`/`superseded` are preserved (deduplicated), including real
summaries, failure records, and artifact refs.  A planned route is never
silently relabeled `not_run`.  Negative/failed routes are scientific assets
and do not block a safe manuscript.

## Outputs and persistence

- `CriticalExperimentRecord.source_run_dir` is populated from the validated
  execution result and participates in the content-addressed identity.
- `ArticleReproducibilityPackage` carries critical-experiment records, replay
  records, artifact lineage, appendix records, publication blockers,
  warnings/errors, attempts, and a content-addressed `package_id` that covers
  the complete result (status, warnings, errors, blockers, attempts, and all
  record content); status is `ready`/`ready_with_findings`/`blocked`.
- `_hard_blocker` uses the same full-content computation, so different
  invalid upstream inputs never share one package ID.
- Fixed local outputs: `ARTICLE_REPRODUCIBILITY_PACKAGE.json`,
  `ARTICLE_RUN_LINEAGE.json`, and `ARTICLE_NEGATIVE_RESULTS_APPENDIX.md`.
  Writing is atomic, exact replay is idempotent, and conflicting content is
  rejected without deleting anything.

## Fail-open / fail-closed

- Fail-open: ordinary research findings and negative/failed routes are
  deliverable records.
- Fail-closed: critical replay/provenance/hash/identity failures, forged or
  tampered manuscript content, and conflicting persisted content block the
  publication package while preserving the manuscript draft and returning
  actionable diagnostics.
