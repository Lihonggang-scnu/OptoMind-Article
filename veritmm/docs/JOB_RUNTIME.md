# SimulationJob runtime

`tmm_engine.job_runtime` upgrades "one call" into "one job": an explicit state
machine, a batch entry with failure isolation, and a tool-fingerprint cache
layer over the existing content-addressed cache.  It is the execution
substrate for "submit → check → decide" tool loops; the MCP surface (V-11)
projects the same entry points.

## State machine

```text
queued ──▶ running ──▶ verifying ──▶ completed
   │           │             │
   └───────────┴──▶ cancelled └──▶ failed
```

Transitions are closed (`JOB_TRANSITIONS`): any transition not listed is
rejected with the typed code `invalid_job_transition`; unknown states with
`invalid_job_state`.  `cancelled` is reachable only from `queued`/`running`.

A `SimulationJob` carries `job_id`, `config_hash` (task identity),
`capability_id` (tool fingerprint), `status`, `inputs` (references, not
inlined arrays), `outputs` (artifact references: `run_id`,
`certificate_id`), `diagnostics` (`failure_code`, `attempts`),
`numerical_flags`, `runtime` timestamps, `provenance`
(`engine_fingerprint`, `identity_scheme`), and coarse `progress` (0–100;
`verifying` is reported at 90 — convergence/.cross-solver granularity, never
per-wavelength).

## Synchronous semantics (honest, no pretend-async)

Submission runs to completion before returning, so `status` is mainly useful
for terminal states and `cancel(job_id)` honestly reports
`{supported: false, reason: "synchronous execution..."}` for jobs that
already finished.  `run_batch(requests, workers=1)` executes jobs in order
with per-job failure isolation (one bad task cannot fail the batch) and
aggregates a report (completed/failed counts, per-entry status,
`certificate_id`, `failure_code`).  `workers > 1` is rejected with
`workers_unsupported` until process-level parallelism (V-12) lands — no
threads pretending to be parallelism.

## Tool fingerprint cache

```text
fingerprint = sha256( capability declaration
                    + source hashes of implementing modules
                    + parameter schema hash )
```

This is the ToolUniverse semantics: the key combines *what the tool claims*
with *the code that implements it*, so any substantive change — a tolerance,
a solver, a schema — produces a new key and old entries can never be
presented as current.  The runtime records `{execution_identity →
fingerprint}` in `veritmm-job-fingerprints.json` inside the experiment store
root.  On resubmission of the same identity, the recorded fingerprint is
compared with the current one:

- match → the existing content cache may serve the run;
- mismatch or first sight → the run executes with the content cache disabled
  (fail-closed) and the fresh fingerprint is recorded.

Net effect: unchanged retries and resumes reuse the cache; "the engine
changed but the cache is still warm" cannot happen.  Measured on the
regression fixture (25-point DBR, warm store): first submission ~0.96 s,
fingerprint-verified resubmission ~0.32 s (cache hit), a tampered-fingerprint
resubmission re-executes in full.

## Experiment store integration

Jobs run through `execute_managed_task`, so every completed job is recorded
in the `ExperimentStore` exactly like a CLI run: `store.get_run(run_id)`,
`store.get_lineage(run_id)`, and `history` all see job-produced runs.  There
is no second recording path and no second cache.

## MCP reservation (V-11)

`submit_job` / `status` / `result` / `run_batch` are shape-compatible with a
tool namespace (`submit`, `status`, `result`, `batch`); the synchronous
`cancel` answer already carries the machine-readable refusal reason.  When
asynchronous execution arrives, only the runtime internals change — the state
machine, fingerprint semantics, and report shapes are the stable surface.
