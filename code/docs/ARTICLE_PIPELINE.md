# Article Pipeline (unified production orchestration shell)

Date: 2026-08-16
Module: `optomind_optics/harness/article_pipeline.py`

## Purpose

The pipeline is a typed, deterministic orchestration shell that composes the
already available Article/TMM modules without copying their scientific
logic.  It provides a stable handoff from a natural-language question through
problem analysis, method research, strategy planning, article director,
route/task binding, experiment planning, execution, and trusted asset
compilation (`compile_article_assets`), with explicit stage receipts and
honest partial/fail-open semantics.  Writing, review, and publication stages
will attach as additional adapters later; this module does not rewrite them.

## Stage order and contracts

`PIPELINE_STAGE_ORDER`:

1. `problem_analysis`
2. `method_research`
3. `strategy_planning`
4. `article_director`
5. `route_task_binding`
6. `experiment_planning`
7. `execution`
8. `asset_compilation`

Every stage runs through a caller-supplied adapter (`AnalyzeAdapter`,
`ResearchAdapter`, `StrategyAdapter`, `DirectorAdapter`,
`BindRoutesAdapter`, `PlanExperimentsAdapter`, `ExecuteAdapter`,
`CompileAssetsAdapter`).  Adapters may return the existing strict Pydantic
models or equivalent mappings; the pipeline normalizes them with
`model_validate` and never invents content.  The pipeline itself never calls
a model, never executes TMM, and never fabricates a payload.

Each stage produces a `StageReceipt` with its sequence, stage, status
(`completed`/`partial`/`unavailable`/`failed`/`skipped`), stable input and
output IDs, warnings, errors, and a deterministic `payload_digest` over the
canonical stage payload.  Receipts contain no wall-clock timestamps.

## Status and fail-open/fail-closed boundaries

- Ordinary adapter/provider failures (a callable raises, returns no payload,
  or returns an explicit `unavailable` envelope) fail open: valid earlier
  outputs are preserved, the failing stage is recorded, later stages are
  marked `skipped` with a cause, and the pipeline returns `partial` (or
  `unavailable` when nothing usable was produced).
- Identity/contract violations (invalid envelopes, `invalid` statuses,
  route/task binding errors, execution request/task identity mismatches,
  invalid asset-compilation results, or exceeding `maximum_routes`) fail
  closed: the stage is recorded `failed`, downstream stages are skipped, and
  the pipeline returns `failed`.
- Only planning rows with `status="ready"` and a compiled request are
  executed.  `not_run`/omitted rows are retained in the receipt warnings and
  never fabricated into execution.
- Every execution result must match the compiled request's `request_id` and
  `task_hash` before it is sent to the asset compiler.  Invalid asset
  results are retained as explicit records in `asset_compilations` but are
  never counted as trusted success and never drive downstream stages.
- Asset aggregation is truthful: any `invalid` asset result keeps the
  pipeline `failed` (the invalid result is retained); any `unavailable`
  asset result (or no usable assets at all) prevents `completed` and yields
  `partial` when earlier stages/executions exist, with route-specific
  warnings; a `partial` asset result keeps the pipeline `partial`.
- Asset provider failures are fail-open per route: an adapter exception or
  an empty payload for one route is recorded as a route-specific soft
  failure, successful routes are preserved, and the asset stage/pipeline
  become `partial`.  True Pydantic/identity/integrity/`invalid`-status
  errors remain hard failures that fail the pipeline.  Every retained asset
  keeps its explicit `(request, execution, asset)` association, so warnings
  and output IDs always name the correct route.
- Every normalized asset is checked with the public
  `validate_asset_compilation_result` using the current request and
  execution: the content `result_id` is recomputed, semantic status and
  relationships are checked, and the asset's request/task/run/experiment/
  observation identity must equal the upstream request/execution.  An asset
  result with a valid shape but mismatched identity or a forged `result_id`
  fails closed and is never treated as trusted.
- `maximum_routes` is enforced at both the route/task binding and the
  experiment planning boundary: a planning result with more than
  `maximum_routes` rows or ready rows is a hard contract failure, and
  execution/asset compilation are skipped without calling the executor.

## Cross-stage identity (fail-closed)

The pipeline validates deterministic cross-stage identity before any
execution:

- `problem_analysis.analysis.original_request` must equal the pipeline
  question.
- `method_research.problem_id` must equal
  `problem_analysis.analysis.problem_id`.
- `strategy_plan.plan.problem_id` must equal that same problem id.
- `director_plan.plan.question` and `charter.question` must equal the
  pipeline question, and `director_plan.plan.capability.status` must equal
  the analysis compatibility.
- Route/task bindings must have unique `route_id`s, every `route_id` must
  belong to the strategy plan, and each embedded route must exactly equal
  the corresponding strategy-plan route.
- Experiment planning rows must have unique `route_id`s, belong to the
  supplied bindings, carry `plan_id` equal to the director `plan_id`, and
  every ready row must bind to a compiled route/task binding whose
  `task_digest` matches both the row and its compiled request.

Experiment planning is validated with the public deterministic
`validate_experiment_planning_result(result, plan=director.plan,
bindings=bindings, errors=...)`, which recomputes the content ID and
re-compiles every ready row with the trusted proposal compiler (excluding
`compiler_attestation` when comparing) and performs no network/model/solver
call.  Because the deterministic recompilation is authority-sensitive, a
production pipeline injects the same caller-owned
`ArticleCompilationAuthority` used by the planning adapter. Legacy/test
callers may omit it and retain the fixed validation-authority behavior. The
pipeline keeps the explicit checks the public validator does not cover or
does not survive: a non-empty exact `result_id`, the `maximum_routes` cap, a
ready/partial claim without coverage rows, duplicate row `route_id`s, the
result-level `plan_id` equality, the pipeline-request `run_id`/`branch_id`
identity of every ready request, and the presence of the `proposal`/`cells`
required for recompilation.  Validator exceptions are captured as validation
failures rather than crashing the pipeline.

`ProblemAnalysisResult(status="analyzed")` without an analysis payload is a
hard contract failure; an explicit `unavailable` analysis may legitimately
carry no analysis.  When an asset hard failure coexists with earlier
route-specific soft provider failures, the soft diagnostics are retained in
the failed asset receipt warnings without weakening the hard failure.

Route/task materialization is robust: a scalar/non-iterable adapter payload
is a hard contract error, while an exception raised while iterating a
provider generator is a soft provider failure.

## Request, result, and persistence

`ArticlePipelineRequest` requires a non-empty `question`, `run_id`,
`branch_id`, and `work_dir`, plus an optional `force_mock` and a positive
`maximum_routes`.

`ArticlePipeline.run` is the write-once new-run entry and refuses any
non-empty `work_dir`.  `ArticlePipeline.resume` continues an interrupted run
from its committed recovery ledger; see `ARTICLE_PIPELINE_RECOVERY.md` for
the checkpoint/resume contract, the fail-closed validation performed before
any adapter runs, route-level execution/asset progress, terminal-run
idempotency, and the single-writer runtime lock.

`ArticlePipelineResult` carries the pipeline status, `run_id`, question,
typed stage payloads (`problem_analysis`, `method_research`,
`strategy_plan`, `director_plan`, `experiment_planning`), `execution_count`,
the verified `route_task_bindings`, `asset_compilations`, `receipts`,
validation errors, warnings, and a deterministic `result_id` computed over
canonical result contents with `result_id` excluded (events and elapsed
times are never part of the identity).

## Capability boundary

If problem analysis succeeds but the analyzed `OpticalProblemAnalysis`
reports `compatibility == incompatible`, the pipeline records the
`problem_analysis` stage as `unavailable` with the explicit compatibility
reason and skips method research, strategy planning, the article director,
and every later stage.  Only an explicit `incompatible` value triggers the
boundary; ambiguous or unknown compatibility is never reinterpreted as
incompatible.

Persistence is write-once:

- `REQUEST.json`
- `NN-stage.json` snapshots per stage
- `PIPELINE_EVENTS.jsonl` (append-only, deterministic event lines)
- `FINAL_PIPELINE_RESULT.json`

A non-empty `work_dir` is never overwritten; the run returns a clear failed
result instead.  Repeated runs of the same request through deterministic
adapters in two empty directories produce identical results and `result_id`s
because paths are excluded from the canonical result contents.

## Wiring

`build_default_pipeline` wires caller-supplied adapters into an
`ArticlePipeline`.  It never instantiates a Qwen client and never runs
network or solver work by itself; every adapter must be provided explicitly.
The caller may pass the existing `analyze_optical_problem`, a method
researcher, a strategy planner, an `ArticleDirector`, the experiment planner,
an executor, and `compile_article_assets` adapters.

## Future attachment points

Later writing/review/publication stages attach as additional adapters that
consume the typed payloads and `asset_compilations` produced here.  Their
integration must follow the same rules: deterministic normalization, no
invented content, and hard integrity failures fail closed while ordinary
provider issues remain partial.
