# Article Experiment Planning (route-to-Article-experiment bridge)

Date: 2026-08-16
Module: `optomind_optics/harness/article_experiment_planning.py`
Prompt: `prompts/optical_harness/Article Experiment Planning.txt`

## Purpose

This bridge converts visible strategy routes plus their locally compiled
`OpticalDesignTask` bindings into task-bound `CompiledExperimentRequest`s.
Qwen (`qwen3.7-flash`, no fallback) fills only high-information semantic
cells from a compact local table; local code owns the schema, IDs, the actual
task, the required action, parameters, budgets, hashes, and HMAC
compilation.  It removes test-only hand construction before the later
integration orchestrator and never executes TMM.

## Inputs and identity

- An accepted `ArticleDirectorPlan`.
- One `RouteTaskBinding` per visible route: `route_id`, `DesignRoute`,
  `compiler_status` (`compiled`/`failed`/`unavailable`/`not_run`),
  `compiler_usage`, the exact `OpticalDesignTask`, and the canonical task
  digest.  Compiled bindings require a task whose digest equals
  `task_digest`; non-compiled bindings must carry no task or digest.
- `run_id`, `branch_id`, and the `ArticleCompilationAuthority`.

Duplicate route IDs, mismatched binding identities, missing/extra semantic
identities, missing run/branch identity, or a missing authority fail closed
with `status="invalid"`.

## Qwen contract

The provider request is a compact local table: semantic route aliases
(`R01`...), hypothesis aliases (`H01`...), route title/hypothesis/principle/
execution-request summaries, task mode and experiment summaries, per-route
hypothesis predictions, coverage responsibilities, and prior feedback.  No
HMAC secrets, task hashes, proposal IDs, model names, action permissions,
parameters, or budgets are exposed.  Qwen output rows contain only
`route_alias`, `hypothesis_aliases`, `stage`, `atomic_change`,
`expected_discriminator`, `rationale`, and `uncertainty`.

Local code resolves aliases to route/task and hypothesis identities, derives
`required_action_for_task` (simulate-only -> `run_solver`, any optimize ->
`run_optimizer`), derives allowed parameters from the exact task/route, and
derives `requested_budget` from the task's non-Qwen ceilings (wall time,
forward evaluations, optimizer runs).  It creates deterministic proposal IDs,
constructs `ExperimentProposal`, and compiles task-bound requests with the
existing `compile_proposal` authority.  The resulting requests pass the
execution ceiling checks, existing global budget caps, and gateway
authorization, and cannot be task-swapped.

## Per-route robustness

One provider row is expected per executable route.  Unknown or duplicate
aliases, malformed rows, invalid stages, unknown hypothesis aliases, and
missing routes preserve valid independent rows and record route-specific
errors/omissions; routes are never remapped and tasks are never invented.
One compact repair attempt with per-route validation feedback is allowed; a
successful repair replaces only the repaired rows.  Task compiler failures
become explicit `not_run` rows.  If no row survives, the result is honest
`unavailable`, never a crash and never a fabricated hypothesis assignment.

## Result and validation

`ArticleExperimentPlanningResult` carries per-route status
(`ready`/`error`/`omitted`/`not_run`/`unavailable`), route/task/proposal/
request relations, omissions, validation errors, attempts, usage, model
identity, and a deterministic `result_id`.
`compute_experiment_planning_result_id` recomputes the content ID, and
`validate_experiment_planning_result` verifies identities, per-row
proposal/request consistency, task digest binding, action derivation, budget
derivation, and (when supplied) HMAC attestation; when plan and bindings are
supplied it recompiles each ready row and compares the result, so a rehashed
or tampered nested result is rejected.

## Boundaries

No TMM execution happens here.  Ordinary provider/format issues fail open per
route; identity, alias, action, budget, and HMAC violations fail closed.  The
concrete adapter is locked to `qwen3.7-flash`; injected fake providers retain
their truthful model label, and every attempt's usage is preserved.
