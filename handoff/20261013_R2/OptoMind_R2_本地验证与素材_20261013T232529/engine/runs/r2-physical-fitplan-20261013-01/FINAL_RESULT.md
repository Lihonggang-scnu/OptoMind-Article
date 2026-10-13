# R2 final local result

The accepted artifacts are under `attempt-02-explicit-binding/`. The first
attempt is preserved under `attempt-01-legacy-binding-failure/` because its
synthetic generator omitted the explicit layer binding.

Primary metrics:

- fitted `target_film` absolute error: `1.4210854715202004e-14 nm`;
- fit RMSE: `2.6020852139652106e-15`;
- maximum independent candidate-Jacobian discrepancy: `0.0`.

The fit returned `0`, converged, and was `well_determined`. The deterministic
planner returned `0`, selected `Delta` at `550 nm / 60 deg`, and retained both
R candidates as rejected alternatives. The independent check kept layer 0 at
`25.0 nm`, measured a raw Delta separation of `6.282859047188071 rad`, and
used the circular derivative `0.13737864061002827 rad/nm`.

The narrow-boundary sidecar retained the actual float interval
`1.0000007932831068e-08 nm`, two distinct points, and finite non-zero rows.

All observations are synthetic forward-computed values. They are not measured
data, manufacturing evidence, or AI-designed evidence. No real model, hardware,
network/API, or paid resource was used.
