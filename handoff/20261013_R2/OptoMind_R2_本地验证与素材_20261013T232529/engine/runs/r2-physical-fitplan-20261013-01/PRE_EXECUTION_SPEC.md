# R2 physical fit and measurement-plan freeze

- `run_id`: `r2-physical-fitplan-20261013-01`
- `status`: frozen before synthetic observation generation and CLI execution
- `source_repo`: `D:\OptoMind-Article-3\repos\VeriTMM`
- `source_commit`: `dd9f27bf7b3f8150fdb37dc65f169ef6aab333f1`
- `embedded_article_root`: `D:\OptoMind-Article-3\repos\OptoMind-Article\veritmm`
- `python`: `D:\OptoMind-Article-3\local-integration\20261013\envs\tensor\Scripts\python.exe`
- `model`: none; no network/API calls; cost `0`; no hardware, RCWA, FDFD, or other external solver

## Physical task

Use the existing planar coherent TMM forward model with incident `constant_n=1.0`,
two layers, and substrate `constant_n=1.5`:

1. fixed non-target layer: `constant_n=2.1`, `thickness_nm=25.0`;
2. target layer: `constant_n=2.1`, true thickness
   `118.7448027095621 nm`.

The target layer is the only fit parameter. Its name is the custom non-numeric
name `target_film`, with explicit `layer_index=1`, bounds `[100.0, 135.0]`, and
initial guess `118.0`. The fixed layer's thickness, material, and label remain
unchanged in every rebuild and perturbation.

The target thickness is defined from the phase-branch control point

`d_branch = 550 / (2 * 2.1 * sqrt(1 - (sin(60 deg) / 2.1)^2))`

and `target_layer_1 = d_branch - 25`. The two equal-index layers make the total
film thickness equal to the known Delta branch point while retaining a separate
non-target layer for binding checks.

## Frozen observations and candidates

Generate four `R` observations at wavelengths `450, 500, 550, 650 nm`, angle
`0 deg`, polarization `unpolarized`, and uncertainty `0.02`, by one fixed
forward computation at the true structure. Store the exact values in the task
JSON and a raw CSV. These are `synthetic forward-computed` observations only;
they are not measured data, manufacturing evidence, or AI-designed evidence.

Use the frozen candidate pool:

- `R`, `500 nm`, `0 deg`, `unpolarized`, `sigma=0.01`;
- `R`, `600 nm`, `0 deg`, `unpolarized`, `sigma=0.01`;
- `Delta`, `550 nm`, `60 deg`, `unpolarized`, `sigma=0.01`.

Run `plan-measurement` with criterion `d_optimal` and `n_select=1`.

## Acceptance metrics and controls

- fit exit code `0`, `converged=true`;
- recovered `target_film` within `1e-6 nm` of the frozen truth;
- fit RMSE `<=1e-8`;
- identifiability status `well_determined`;
- non-target layer 0 remains exactly `25.0 nm`, `constant_n=2.1`, and its label;
- planner has finite candidate rows, baseline/final rank one, and retains all
  candidate scores plus selected and rejected alternatives;
- an independent forward-model calculation recomputes every candidate row,
  including the unweighted plus/minus values and sigma weighting; the maximum
  absolute planner discrepancy must be `<=1e-8` (relative tolerance `1e-6`);
- Delta independent difference uses the circular residual difference. At the
  branch candidate the raw Delta separation must exceed `6 rad`, while the
  wrapped derivative remains finite and below `1 rad/nm`;
- a physical narrow-boundary sidecar uses the same model and a `[low, high]`
  interval of `1e-8 nm` with the fitted value at the lower edge. It must retain
  the actual floating-point interval, use two distinct feasible points, and
  produce a finite non-zero Jacobian row.

Controls are the bundled one-layer `fit_synthetic_ar_coating.json` CLI path and
the same frozen two-layer task with parameter name `thickness_layer_1` but the
same explicit `layer_index=1`. Both controls are comparison evidence only.

## Frozen output set

Only this run directory may receive new files. Planned outputs are
`fit_task.json`, `synthetic_observations.csv`, `fit_result.json`,
`candidates.json`, `measurement_plan.json`,
`independent_jacobian_review.json`, `narrow_boundary_review.json`,
`RUN_MANIFEST.json`, `run.log`, and optional editable `metrics.svg` plus
`metrics.png` after results are known. No production source, branch, commit,
historical result, or F-drive file may be changed.
