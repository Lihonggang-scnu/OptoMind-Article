# T030: Qwen-directed branch search and evidence-fed reflection

## Purpose and baseline

T030 makes a coarse scientific hypothesis affect the actual numerical starting points. A locally injected, audited Qwen 3.7 Flash or Qwen 3.5 Plus planner translates the hypothesis into concrete thickness values and optional narrower search intervals. The numerical engine tests those proposals; a subsequent model call receives real branch outcomes before suggesting another round.

This is an independent development branch from `e1eba28`. Before edits, the baseline was backed up and its tracked-tree restoration verified; the baseline tag is `r3-stable-before-t030-20261015`. The existing search/revision path remains available. Default optimizer initialization remains unchanged for existing callers.

The new modules do not construct a paid client or obtain credentials. Expansion and reflection require an explicitly supplied planner. Numerical execution calls no model. Caller-side monetary authorization and accounting remain separate from numerical evaluation limits.

## Actual execution path

### 1. Expand coarse intent with the model

`focused_research.direction_expansion.expand_directions(previous_search, plans, *, output, planner, max_forward_evaluations)` reads a manifest-bound prior search and builds a compact request containing:

- The exact original question, all original objectives and subgoals, their relationships, and explicit/configured constraints.
- Selected parents' actual metrics, current parameter values, original bounds, and original coarse hypotheses.
- A deduplicated family catalog giving actual materials, layer-to-variable mapping, fixed thicknesses, stack conditions, and simulation conditions.

Unselected archive designs, old numerical histories, unrelated routes, and paper bodies are excluded. `context_bindings.json` retains hashes of the complete source context for audit. A synthetic 126-design, 24-layer, five-plan fixture checks that the prompt stays compact while preserving goals and conditions; this is a context/contract test, not a scientific performance comparison.

There is at most one injected planner call, with no automatic retry. If no supplied plan is executable, the stage records `no_executable_plan` and `planner_calls: 0`. Otherwise the planner returns concrete branches with a `branch_id`, `plan_index`, `parameters_nm`, optional `search_bounds_nm`, and explanatory rationale. The prompt welcomes ten or more useful branches when the shared budget permits; there is no hard candidate-count ceiling.

`validate_expansion(payload, previous, coarse_plans)` compiles independent jobs. Each job carries its original route, one parent-derived seed envelope, parameterization, parent/hypothesis/plan identity, and optional bounds. Invalid individual branches are retained in the rejection report without discarding valid siblings.

The program does not translate phrases such as “make this thinner” into a fixed formula. The model supplies the concrete numerical proposal. Missing redundant parent/hypothesis/parameterization fields may be bound deterministically from a valid plan index, with an audit. Explicit conflicts are rejected. Unknown explanatory fields are retained in the raw response and listed as ignored, never executed as physical commands.

### 2. Validate, reserve, and search branches in parallel

`focused_research.directional_stage.run_directional_search(previous_search, expansion_payload, coarse_plans, *, output, cache_dir, max_forward_evaluations, max_new_evals, search_workers=2, max_workers=2, backend='auto', seed=42)` revalidates the raw proposals against the prior search.

The CLI uses `read_expansion()` to check the expansion manifest and original-parent snapshot binding. It executes the parsed response only after revalidation, rather than trusting saved compiled jobs. The CLI requires explicit `--execute`.

The runner divides the single search-row budget across accepted branches and persists reservations before starting workers. Up to two spawn-based processes run independent branch optimizations. A failed reservation is not silently recycled or retried; incomplete metering is reported. This is branch-level process parallelism, not a newly implemented vectorized screen over all seeds.

Each worker calls `family_optimization.optimize_family()` with:

- `initialization_policy='provided_only'`, disabling the generic Latin-hypercube starts for this path.
- The model-generated seed, retaining actual ancestry.
- `search_bounds_nm`, a partial override of existing variable bounds that must stay inside the immutable original family bounds.
- Its allocated `max_forward_evaluations` and original objectives.

The existing L-BFGS-B refinement and multi-objective search scalarizations remain. Thus model intent controls the seeds and, when supplied, the local search intervals; the optimizer still determines subsequent numerical steps. Without narrowed intervals, refinement can move throughout the original admissible bounds. Search weights are exploration devices, not inferred user preferences. Default weights are one balanced direction plus one emphasis per objective, rather than always three directions.

### 3. Independently certify and retain designs

Search candidates remain uncertified. Before certification, the runner validates unchanged materials/conditions, fixed layers, every original objective, actual seed ancestry, original bounds, and declared branch bounds.

A zero-new-evaluation cache probe checks exact archived rediscoveries. The existing certification selection policy schedules candidates across their objective vectors; deferred candidates remain explicitly unvalidated. One positive-budget certification batch serves all branches after numerical workers finish, with up to two certification workers and the separate `max_new_evals` limit.

Only accepted certification results enter the archive. Existing physical designs remain; rediscoveries append branch attribution rather than inventing a new physical ancestor. Full search records, certification decisions, failures, ancestry, and retained designs are saved and manifest-bound.

### 4. Feed actual branch outcomes back to Qwen

`directional_stage.build_branch_feedback()` writes `branch_feedback.json`. For each branch it records the original hypothesis and parent, actual proposed parameters/intervals, work counts, failures, cache reuse, deferred candidates, and certified outcome counts relative to that parent.

Selected examples are actual certified designs, each with its complete goal vector. The summary selects objective extrema and deduplicates their design identities; it does not combine separate designs' best metric values into a fictitious design. “All improve” means no goal worsens and at least one improves under exact signed metric deltas. These categories are descriptive, not statistical significance tests.

`focused_research.directional_reflection.reflect_on_branches(previous_search, *, output, planner)` validates the saved search and feedback binding, then wraps the existing audited `search_stage.revise()` call. It supplies branch feedback alongside the normal decision context and asks the model to distinguish observations, interpretations, and future hypotheses.

The actual forwarded request is saved in `directional_reflection_prompt.json`, with its feedback and prompt hashes in `directional_reflection_binding.json`; `revision_prompt.json` remains the base request. The returned coarse plans are unexecuted. Starting another expansion/search round is an explicit orchestration step, not an implicit recursive loop.

## Hard contract, soft scientific interpretation

The initial implementation keeps material identities, layer count, fixed layers, original goals, and physical conditions unchanged. `family` preserves variable ties; `layerwise_refinement` uses independent `layer_<index>:<original_variable>` names and original layer bounds. A parent whose tied layers already differ cannot be silently retied into a family branch.

Concrete parameters must be complete, finite, and within effective bounds. Optional intervals cannot expand the original admissible space or exclude their own seed. These are execution checks, not opinion checks. Speculative, contradictory, weakly supported, or unsuccessful hypotheses remain legitimate inputs. A rationale is neither a certified observation nor a prerequisite for scientific agreement.

## Cache and budget scope

- Numerical search caches evaluated thicknesses within one optimizer invocation. There is no cross-branch or persistent numerical-search cache in this implementation; duplicate branch seeds may repeat numerical work.
- The certification executor deduplicates identical physical tasks within its batch and supports persistent reuse under matching physics, source/material/verifier identities. Archive membership alone does not prove a usable cache hit.
- Cached certification reuse is explicitly counted and is not an independent new experiment. Objective scoring retains the inherited objective definitions.
- Search design rows and certification tasks have separate budgets. Attempted numerical work includes failed evaluations and numerical probes; branches share one pre-reserved search cap.
- Worker failures preserve their reservation and expose incomplete metering rather than reporting invented exact usage. Worker CPU, parent CPU, and wall time have distinct scopes; serial worker time overlaps parent CPU and must not be added twice.
- These numerical limits do not enforce a currency budget. The injected planner's surrounding authorized execution controls monetary spending.

## Interpretation limits

An accepted expansion is an executable proposal, not a verified mechanism. Completed execution does not mean every candidate was certified, every goal improved, or the original hypothesis was correct. Check certification coverage and branch failures separately.

The reported optical outcomes are limited to the certified sampled grid and supplied conditions. They do not establish continuous-band behavior, manufacturing robustness, a global optimum, a unique physical mechanism, or universal improvement over the existing search. A finite-budget failure to find a better design does not disprove the hypothesis. Likewise, an improved refined design does not prove that the model's verbal explanation caused or correctly explained that improvement.
