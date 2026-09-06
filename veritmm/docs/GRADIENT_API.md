# Gradient / Sensitivity / Batch API

`tmm_engine.gradient_api` is the proposal side of **simulate → gradient →
decide**: from one validated simulation task it returns per-layer thickness
gradients of a band objective, normalized sensitivities, and batched proposal
forwards — so a caller can move from "try one design and look at it" to
"compute gradients, decide which layer is worth changing, propose the next
candidate".

## Boundary: proposal evidence, never physics validity

Gradients and sensitivities are **proposal evidence**.  They say where the
objective moves fastest; they say nothing about whether a design is physically
valid.  No output of this API constitutes, upgrades, or replaces a physics
acceptance certificate — verification claims live only in
`PHYSICS_ACCEPTANCE_CERTIFICATE.json` and its verification evidence.  Any
downstream workflow (including the OptoMind feedback loop) must keep this
separation: propose with gradients, decide with certificates.

## Entry points

### `compute_gradient(task, objective, variables=None, *, registry=None, device="cpu", custom_objective=None) -> GradientResult`

- `objective`: `"mean_R"`, `"mean_T"`, `"mean_A"` with an optional
  `:min..max` band in nanometres (for example `"mean_T:500..700"`); without a
  band the full declared grid is used.  The first declared (angle,
  polarization) pair of the task is used.  `mean_A` follows the closure
  convention `A = 1 - R - T` (acceptable for gradient purposes); the result
  notes carry an independence hint.
- `custom_objective`: a Python callable `f(observables, wavelengths_nm) ->
  torch scalar`.  **Python API only** — never exposed through JSON or the
  CLI.
- `variables`: optional list of layer indices (default: all layers marked
  `optimizable`).
- Method: autodiff through the torch differentiable backend, with a built-in
  central-difference cross-check through the same backend
  (`finite_difference_max_relative_deviation`).

`GradientResult.layer_gradients` holds per-layer
`gradient_per_nm` (d objective / d thickness in nanometres), the finite
difference value and deviations, and `top_influential_layers` is the
|gradient|-sorted ranking.

### `compute_sensitivity(task, variables=None, *, objective="mean_R", characteristic_scale_nm=10.0, ...) -> SensitivityResult`

Normalized sensitivity = gradient × `characteristic_scale_nm` — the objective
change for a +10 nm thickness move by default.  The per-nm gradients and the
built-in finite-difference cross-check align with the thickness-sensitivity
audit in `tmm_engine.scientific_analysis`: that audit runs on a certified
nominal result and gates every autodiff derivative against an independent
NumPy central difference under explicit tolerances, while this API is the
lightweight proposal-stage view of the same quantities.

### `batch_simulate(tasks, *, registry=None, settings=None) -> BatchSimulateResult`

Batched proposal forwards: tasks sharing media, grid, and illumination get one
batched torch forward (phase one), then every candidate is independently
certified via `certify_simulation` under the default acceptance settings
(phase two) — the same "batch proposals, certify each candidate" pattern as
the research batch executors.  Each entry reports the proposal band means, the
certified means, `proposal_matches_certified`, and the certificate identity.
Batch execution never changes a verification decision.

## Typed failures

Requests fail with `GradientRequestError` carrying a machine-readable code —
`no_optimizable_layers`, `incoherent_stack_unsupported`, `unknown_variable`,
`variable_not_optimizable`, `objective_unsupported`, `band_outside_grid`,
`empty_batch`, and `backend_unavailable`.  The torch backend is required for
gradients; when it is absent the API reports `backend_unavailable` and is
**never** silently downgraded to a finite-difference numpy path.

## CLI

```bash
veritmm gradient TASK.json --objective mean_T:500..700 --json
veritmm gradient TASK.json --objective mean_R --variables 0,2 --json
veritmm sensitivity TASK.json --objective mean_R:580..620 \
  --characteristic-scale-nm 10 --json
```

Both commands follow the protocol conventions: one compact JSON object,
typed failures with `ok: false` and failure codes, exit code 0 on success and
2 on a typed failure.  `custom` objectives are rejected on the CLI by design.

## OptoMind feedback loop

The feedback loop consumes `top_influential_layers` as the machine-readable
answer to "which layer is worth changing next": rank by
|d objective / d thickness|, propose the next candidate on the top layer(s),
and gate every proposed design through `verify_run`/the acceptance
certificate before it may be called valid.  The loop is propose-with-gradients,
decide-with-certificates — the gradient API deliberately has no authority over
acceptance.
