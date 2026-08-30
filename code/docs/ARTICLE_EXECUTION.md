# Article Trusted Execution — Stage 6 Design Note

Date: 2026-08-15
Module: `optomind_optics/harness/article_execution.py`

## Purpose

Stage 6 connects an attested `CompiledExperimentRequest` (Stage 5) to the
existing deterministic TMM harness through a trusted local task resolver,
binds the Article budget adapter, and normalizes truthful run outputs into
`ObservationCard` records.  It does not build or modify a solver: the existing
`TMMHarnessOrchestrator` (or an injected fake with the same `run(task)`
contract) remains the only executor and physics-certificate authority.

Two experiment identities remain deliberately distinct. The emitted
`ObservationCard.experiment_id` is the Article proposal/run identity from
`request.experiment.experiment_id`, which is consumed by Stage 7 feedback.
The source TMM task experiment ID stays in
`request.parameters["experiment_id"]` and is used later only to locate the
physical run rows and artifacts.

## Trusted task resolution

- `LocalTaskRegistry` maps `request.task_hash` -> `ResolvedTask(task_hash,
  OpticalDesignTask)`.  Qwen never supplies or executes an
  `OpticalDesignTask`; `resolve()` accepts only a `CompiledExperimentRequest`
  and a raw model envelope can never provide a task.
- `ArticleTMMExecutionAdapter` requires a resolved task, verifies the binding
  hash matches the request, re-validates the task with the existing
  `OpticalDesignTask` validators, and rejects missing, mismatched, or invalid
  tasks before calling the harness.
- `LocalTaskRegistry.register` never silently overwrites: re-registering an
  equivalent deterministic task under the same task hash is idempotent, but a
  different serialized task raises `TaskIdentityMismatch`.
- Adapter `branch_id`/`run_id` values are validated as safe path components
  (non-empty, no `.`/`..`, no NUL, no `/`/`\`/`:` separators or drive
  markers), and the resolved run directory is required to stay inside the work
  root.

## Task content binding and action/ceiling authorization

- `compile_proposal(..., task=...)` binds the canonical SHA256 of the exact
  `OpticalDesignTask` content (`compute_optical_design_task_digest`) into
  `CompiledExperimentRequest.task_digest`, which is covered by `task_hash`,
  `request_id`, and the compiler HMAC attestation.  Compile-only requests
  without a task remain constructible for inspection, but the adapter fails
  closed at execution when `task_digest` is empty.
- `LocalTaskRegistry` computes and preserves the canonical digest in
  `ResolvedTask.task_digest`.  Before any run directory is created, budget
  reserved, or harness called, the adapter recomputes the digest from the
  resolved task and requires it to equal both the request digest and the
  registry digest; a task swap or post-attestation mutation is rejected with
  `TaskIdentityMismatch`.
- The whole-task required action is derived deterministically:
  simulate-only tasks require `run_solver`; any optimize experiment requires
  `run_optimizer`.  The adapter rejects specialized follow-up actions
  (`run_reference_solver`, `run_convergence_audit`, `run_robustness_audit`)
  and `generate_baseline` because it executes the complete task; mandatory
  convergence/independent-physics checks and the uncertainty audit are
  intrinsic harness safeguards, not model-proposed permissions.
- The request reservation must cover the task's operational ceilings
  (`wall_time_seconds`, `forward_evaluations`, `optimizer_runs`); missing or
  under-reserved ceilings raise `BudgetCeilingError` before any reservation.
  Qwen is disabled inside the adapter, so qwen budget use is not demanded.

## Budget binding (ArticleBudgetAdapter/BudgetScheduler is the only authority)

- Count resources (`forward_evaluations`, `optimizer_runs`, `qwen_calls`,
  `qwen_input_tokens`, `qwen_output_tokens`) must be integers before any
  reservation; non-integer requests fail before a run starts.
- Reservation happens before execution under `request.budget_lease_id` (or
  `request.request_id`).  Resolver failure, invalid task, harness exception,
  and rejected runs release the reservation.  Completed runs commit measured
  usage extracted from the trusted TMM result budget payload (`budget.usage`).
- Missing or malformed usage (non-numeric, non-finite, negative, or
  non-integer counts) is a hard adapter failure that releases the reservation;
  usage is never invented.

## Run isolation and idempotent replay

- Each run lives under `work_root/<branch_id>/run-<task_hash[:32]>`.
  `EXECUTION_MARKER.json` records the task hash, request id, run id, and
  status.
- Idempotent replay requires ALL of task hash, request id, run id, and
  `completed` status to match; any missing, malformed, or mismatched marker
  field raises `RunCollisionError` and the existing run is never reused or
  overwritten.  After `harness.run`, the trusted TMM result `run_id` must
  equal the adapter `run_id`; a mismatch is recorded as a hard failure before
  any budget commit.

## Truthful ObservationCard normalization

`observation_card_from_tmm_result` derives status and metrics only from
`TMMHarnessRunResult`/`FINAL_RESULT.json`:

| Trusted run status | ObservationCard status |
|---|---|
| completed with physically valid candidates | `physically_valid` |
| completed with none / physics rejection | `rejected_physics` |
| needs_higher_fidelity | `needs_higher_fidelity` |
| failed / exception | `failed` |
| cancelled | `cancelled` |

Metrics are a compact deterministic mapping of fields actually present:
`run_status`, `state_stage`, `stop_decision`, per-experiment rows, aggregate
valid/candidate counts, selected candidate IDs, and `measured_budget`.
`artifact_ids` reference real relative artifacts (`FINAL_RESULT.json`,
`TASK.json`, `EXPERIMENT_GRAPH.json`, `RUN_STATE.json`, certificate files when
present).  `failure_records`/`failure_diagnosis` are copied/normalized from
the run payload; the adapter references certificate files but never creates,
edits, or claims a physics certificate.

## Gateway and coordinator

- `ArticleTMMExecutionAdapter` implements the Stage 5
  `DeterministicExecutorAdapter`; `ArticleToolGateway` still returns only its
  narrow adapter receipt and still rejects raw model envelopes.
- `ArticleExecutionCoordinator` combines the gateway receipt with the locally
  stored run result into `ArticleExecutionResult` containing the
  ObservationCard.  The Stage 5 gateway is not weakened.

## Stage 7 handoff

Stage 7 will consume the ObservationCards produced here to update hypothesis
statuses and to schedule controlled/discriminative/robustness routes from the
Stage 4 coverage matrix: each route proposal compiles to an attested request,
executes through this adapter, and its observation feeds
`ArticleMemoryStore` fact/observation records and the experiment graph.
