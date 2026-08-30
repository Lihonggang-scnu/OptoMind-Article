# Article Pipeline Production Assembly (Stages 17B/17C)

Date: 2026-08-16
Module: `optomind_optics/harness/article_pipeline_factory.py`

## Purpose

This module is an explicit production assembly layer for the accepted
eight-stage `ArticlePipeline`.  It binds one immutable
`ArticlePipelineRequest` (fixed `run_id`, `branch_id`, `work_dir`) and wires
the existing trusted components as the pipeline adapters.  It copies no
scientific logic: problem analysis, method research, strategy planning, the
article director, route/task compilation, experiment planning, execution,
and asset compilation all remain the accepted upstream implementations.

## Wired components

- `analyze_optical_problem` with an injectable `ProblemAnalyzerClient`.
- `research_tmm_methods`, local-first, with injectable `review_kb_paths`,
  online client/flag, synthesis callback, and bounded adapter options.
- `QwenTMMStrategyPlanner` with an injectable client (receives
  `ProblemAnalysisResult.analysis`, never the wrapper).
- `ArticleDirector` with an injectable client.
- `QwenTMMTaskCompiler`: one `compile(route.execution_request_english)` per
  selected `DesignRoute`, producing one `RouteTaskBinding` per route with honest
  `compiled`/`unavailable`/`failed` status and preserved compiler usage;
  tasks are never invented. The complete strategy remains in the Stage 3
  artifact, while Stage 5 deterministically selects at most
  `request.maximum_routes` by `(priority, route_id)` before any compiler call.
  The route bound is therefore a real cost/execution control, not a later
  rejection of an already compiled portfolio.
- `QwenArticleExperimentPlanner` + `plan_article_experiments`.  After ready
  compiled requests exist, the exact route binding is located and
  `binding.task` is registered in `LocalTaskRegistry` under
  `request.task_hash`; route/task/digest identity mismatches become an
  explicit invalid planning result, fail the pipeline closed, and never
  register a wrong task.
- `LocalTaskRegistry`, `ArticleToolGateway`, `ArticleTMMExecutionAdapter`,
  `ArticleExecutionCoordinator`, and `compile_article_assets`, all using the
  same explicit caller-injected `ArticleCompilationAuthority` and the bound
  request identity.

Budget scheduler/adapter, harness factory, clients, online research
controls, and callbacks are injectable so tests run with zero network/Qwen
cost.  `ProductionAssemblyConfig` provides conservative typed defaults with
no credentials and no unbounded budgets.

Every supported budget resource must have an explicit finite positive limit.
Injected schedulers are checked against the same rule before assembly.
Factory-owned research arguments (`online`, clients, KB paths, and synthesis)
cannot be shadowed through `research_options`; such mistakes fail as local
configuration errors rather than being mislabeled as provider outages.

## Assembly object and identity

`ArticlePipelineAssembly` exposes the existing `ArticlePipeline` plus the
trusted local runtime components (`registry`, `gateway`, `adapter`,
`coordinator`, `scheduler`, `budget_adapter`, `task_compiler`,
`strategy_planner`, `director`, `planner`) for inspection/testing, and
`run()`/`resume()` conveniences.  It never serializes the authority key; the
authority is required as an explicit caller injection and is never derived
from model/request content.

The factory rejects request/authority/configuration identity mistakes early
and clearly (`PipelineAssemblyIntegrityError`).

## Fail-open / fail-closed boundaries at the assembly boundary

- Provider/format availability fails open: a service-unavailable or
  malformed provider preserves prior valid stages as `partial`/`unavailable`
  and never crashes the run.
- Missing local material is represented honestly (`no_accepted_evidence`)
  and never fabricates evidence.
- Budget exhaustion yields an explicit execution failure/rejection and no
  trusted assets.
- `rejected_physics`/solver disagreement cannot yield trusted assets.
- An asset-compilation exception is a route-specific soft failure that
  preserves successful routes.
- Interruption plus `ArticlePipeline.resume` reuses committed stages/routes
  and does not duplicate execution; checkpoint/resume remains the accepted
  Stage 17A machinery.
- Authority/request identity mismatches fail closed.

Renderer (LaTeX/PDF/arXiv) failure is outside this accepted eight-stage
assembly; it belongs to the later publication adapter and is not tested
here.

## Narrow mechanism correction proven by production integration

The factory integration test exposed a real end-to-end gap: planner-compiled
requests carry a derived `experiment_id`, while the TMM run keys
`FINAL_RESULT.json` experiment rows by the task's experiment id.  The narrow
correction keeps the two identities distinct and verifies both:

- `ObservationCard.experiment_id` remains the Article experiment identity
  (`request.experiment.experiment_id`) so Stage 7
  (`ArticleFeedbackController`) normalization never sees a mismatch.
- `compile_article_assets` uses `request.parameters["experiment_id"]`
  (falling back to the request card id for legacy same-ID requests) as the
  physical/source TMM experiment identity for locating `FINAL_RESULT` rows,
  artifact directories, candidate records, and
  `ArticleAssetCompilationResult.experiment_id`, while verifying the
  observation against `request.experiment.experiment_id` (Article identity).
- `validate_asset_compilation_result` verifies both identities explicitly:
  the enriched observation against the Article identity and the asset
  result/candidates against the physical source identity.

`ArticlePipeline` also accepts an optional injected `authority` so its
deterministic planning validation recompiles ready requests with the same
authority identity used to compile them (default behavior unchanged when
omitted).  Without this, requests attested under a real caller authority can
never pass the pipeline's planning recompilation check.

`ProductionAssemblyConfig` deliberately has no `force_mock`: the immutable
`ArticlePipelineRequest.force_mock` is the single authority.
