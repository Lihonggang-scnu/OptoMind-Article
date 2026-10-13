# Bundled one-layer control

The existing `benchmarks/cases/fitting/fit_synthetic_ar_coating.json` CLI path
was executed unchanged. It returned `0` with `converged=true`, recovering
`thickness_layer_0=92.59404872876384 nm`; its fixture values are not the exact
forward values for the declared stack, so the reported RMSE is
`0.01999586818363428` and `fit_quality` is `poor`. This control confirms the
CLI path only and is not part of the frozen acceptance metrics.
