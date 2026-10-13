# Attempt 01 failure record

The CLI fit command returned exit code `0` and `converged=true`, but the
frozen acceptance metrics failed: `target_film=100.00000000000033 nm`,
weighted RMSE `3.6260120686355757`, and `fit_quality=poor`.

Cause: the one-off synthetic forward generator called
`build_simulation_task(..., params={"target_film": truth})` without passing the
explicit `FitParameter` definition. The legacy string binding rule therefore
used the parameter position and changed layer 0. The failed input, result, and
CLI log are preserved in this directory. No production code or frozen metric
was changed.

Correction: regenerate the same observations with the explicit
`FitParameter(name="target_film", layer_index=1, ...)` supplied to the forward
task, then rerun the CLI and record the corrected attempt separately.
