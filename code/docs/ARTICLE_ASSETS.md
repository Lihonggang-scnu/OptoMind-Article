# Article Assets (trusted TMM-to-Article asset compiler)

Date: 2026-08-16
Module: `optomind_optics/harness/article_assets.py`

## Purpose

Stage 6.5 asset compiler: converts an already-completed
`ArticleExecutionResult` plus its matching `CompiledExperimentRequest` and an
immutable TMM run directory into verified `ArtifactDescriptor` records,
`TrustedValueRecord` records, and one enriched `ObservationCard` for the
existing Claim Ledger, Figure-first, writing, replay, and presentation
layers.  The compiler never calls a model, never creates derivative
scientific data files, and never mutates the TMM run.

## Inputs and identity binding

- `CompiledExperimentRequest` (model or mapping) with a non-empty
  `task_digest`; the compiler requires the canonical task identity because
  this is the execution-bound path.
- `ArticleExecutionResult` whose `request_id`/`task_hash` match the request,
  whose `run_dir` resolves to the run root, and whose receipt reports
  `adapter_completed`, and whose `outcome` equals
  `observation.status.value`.
- The immutable run root containing `TASK.json`, `FINAL_RESULT.json`, and
  `ARTIFACT_MANIFEST.json`.
- Optional `ArticleCompilationAuthority`; when supplied its `authority_id`
  must match the request and its HMAC attestation must verify.

Verification is fail-closed: request/task/run/experiment identity, canonical
`TASK.json` digest, whole-task required action, `FINAL_RESULT.json` status,
manifest verification via `ArtifactLineageStore.from_disk(...).verify_all()`,
file hashes, path containment, certificate acceptance, and candidate
identity files.  A failed/rejected run, wrong identity, tampered manifest,
path escape, duplicate artifact/candidate identity, malformed certificate,
or cross-wired candidate never yields trusted assets.

## Observation authority

The authoritative observation is `ArticleExecutionResult.observation`, and
its status must equal the status derived from `FINAL_RESULT.json` by the
same `normalize_observation_status` mapping used by the execution layer.
The optional `observation` argument is retained only for compatibility: it
must be canonical-content equivalent to `execution_result.observation`, so a
caller can never promote a failed/rejected/limited observation or change its
metrics/failures.  A genuine `needs_higher_fidelity` `FINAL_RESULT` that
still contains verified usable assets is supported as an explicit `partial`
with a fidelity warning; it is never caller-chosen and never promoted to
`ready`.

`ArticleAssetCompilationResult.experiment_id` identifies the physical/source
TMM experiment used for candidate and artifact paths. Its embedded
`observation.experiment_id` remains the distinct Article experiment identity.
Both are checked against their respective fields in the compiled request;
legacy requests where the two IDs are equal remain valid.

## Manifest semantics

`artifact_id` is the stable logical identity and `relative_path` is the
physical location.  `ARTIFACT_PATH_INDEX.json` (when present) is validated
one-to-one and used for long-path layouts; `experiments/<id>` is only a
fallback, never an assumption.  The compiler never creates the manifest or
any run file.

Candidate selection starts from `selected_roles` plus
`pareto_candidate_ids`, deduplicated.  Baseline is a valid candidate.
Duplicate selected roles pointing at one candidate produce one verified
candidate record with the union of role keys.  Optional ROBUSTNESS absence
is an explicit warning and yields `partial` status; it never drops a
otherwise-valid candidate.

Candidate cross-wiring fails closed: `artifact_id` (logical) and
`relative_path` (physical) are resolved separately, and the certificate,
objective report, simulation result, optional robustness report, and
non-baseline identity must all live in the exact same physical candidate
directory inside the experiment directory from `ARTIFACT_PATH_INDEX` (or the
`experiments/<id>` fallback).  Baseline is the actual experiment baseline
directory with an `initial_baseline` source; a candidate whose id merely
ends in `__baseline`, or whose reports come from another candidate, is
rejected.  A non-baseline `IDENTITY.json` must bind candidate/experiment and
its declared `physical_directory` must match its manifest parent.

## Outputs

`ArticleAssetCompilationResult` carries `ready` / `partial` / `unavailable` /
`invalid` semantics:

- `ready`: no errors/warnings, usable descriptors, candidates, and trusted
  values.
- `partial`: no errors but explicit warnings (for example missing
  ROBUSTNESS, or `needs_higher_fidelity` observation).
- `unavailable`: inputs valid but no usable scientific assets.
- `invalid`: any identity/provenance/integrity violation; no trusted assets.

Descriptors are compiled for every JSON manifest artifact and declare both
raw JSON keys and the derived trusted-value field names (for example
`<candidate_id>.target_score`, `objective_report.aggregate_soft_score`,
`physics_certificate.physics_audit.maximum_observable`,
`robustness_report.mean_soft_score`) so the writing/presentation layers can
verify every trusted value against its artifact descriptor.  Spectra
descriptors declare `wavelengths_nm` plus
`channels.angle=45|pol=s.R`-style series fields.

`TrustedValueRecord` is emitted only from finite scalar values actually
present in verified artifacts; arrays, spectra, hashes, statuses, booleans
and opaque identifiers are never prose-safe.  `source_hash` always equals
the manifest/descriptor SHA256.

The enriched `ObservationCard` preserves the original `observation_id`,
`experiment_id`, status, metrics, failures, budget, and summary, and adds
verified candidate/role/Pareto metadata plus the compiled artifact IDs.

`compute_asset_compilation_result_id` is the canonical content ID.
`validate_asset_compilation_result` recomputes it, re-derives the status,
and when a run root is supplied reopens `ArtifactLineageStore` and
re-verifies the manifest, compares the on-disk `ARTIFACT_MANIFEST.json`
SHA256/head hash, requires an exact manifest record (artifact_id, relative
path, SHA256, type) for every descriptor, re-checks candidate artifact
relationships and the FINAL_RESULT-derived observation status, re-checks
descriptor hashes, and re-derives every trusted scalar from its source
artifact.  A matching file hash alone is never sufficient.  Upstream
request/execution identity is re-verified when those inputs are supplied.

## Presentation loader

`article_presentation._load_numeric_rows` now supports genuine columnar/
nested `SIMULATION_RESULT.json`: `wavelengths_nm` plus channels such as
`channels.angle=45|pol=s.R`, `.T`, and p equivalents.  Rows are aligned to
the wavelength grid; unequal lengths, non-finite values, malformed series,
and mixing scalar metadata with spectrum columns are rejected instead of
silently shifting data.  Existing list/data-record JSON and CSV/TSV behavior
is unchanged.  The narrow SVG plotting path uses `wavelengths_nm` as the
x-axis with actual wavelength scaling, plots the selected channel fields as
y-series, never plots the wavelength column itself, and rejects a
wavelength-only plot as having no numeric response series.

## Boundaries

- No Qwen, network, solver, or TMM execution; no new physics.
- No mutation of `accepted_examples`, run artifacts, or upstream contracts.
- No small fixed cap on verified candidates or artifacts.
