# B1 prediction wiring frozen SPEC

Status: frozen before any preview or run on 2026-10-13.

This batch is a two-case synthetic wiring check for E03. It is not evidence of
autonomous discovery, real-device measurement, false-positive rate, or 80%
detection power. The offline provider is the in-process `V1_synthetic`
facility in Article.

## Fixed scope

- Repository: `D:\OptoMind-Article-3\repos\OptoMind-Article`
- Formal entry: `python -m optomind_optics.harness.science.open_research_cli`
- Entry mode: `--offline`; `--preview` first, then one fresh `--offline` run per case.
- Fixture: `single_film`.
- World seed: `2718` for both cases.
- Fit seed: `2718` for both cases.
- Budget: `2.0` synthetic cost units per case; measurement cost `1.0`; expected two purchases (training and final validation).
- Measurement noise: `measurement_std=0.002`, `calibration_std=0.001`.
- Prediction: R channel, unit `1`, `simultaneous_envelope`, alpha `0.05`.
- Training grid: the source default `np.linspace(420, 860, 17)`.
- Final validation grid: the source default `np.linspace(433, 847, 19)`.
- `max_iterations=1`, `unknown_exploration_limit=0`, `model_expansion_enabled=false`, and `program_composition_enabled=false`.
- `fit_starts=1`, `max_nfev=40`, `max_candidates=1`, `null_replicates=19`.
- `action_loss_cost=1000000.0` keeps this wiring batch at the two-purchase budget; it is an execution bound, not a scientific threshold.

## Cases

### IN: model-in control

- `family=F0`, `run_id=e03-in-f0-s2718`.
- Source world catalog: “model-sufficient noiseless-forward reference with instrument noise.”
- Expected verdict: `PredictionEvaluation.metrics.state == "supported"`, empty `violating_indices`, and trajectory validation state `supported_within_frozen_noise_envelope`.

### OUT: obvious model-out control

- `family=F3`, `run_id=e03-out-f3-s2718`.
- Source world catalog: “dispersive constitutive law required”; the world adds `dispersion_b_um2=0.035`, while `initial_planar_model()` is constant-index.
- Expected verdict: `PredictionEvaluation.metrics.state == "violated"` with at least one violating index, and trajectory validation state `not_supported`.

## Acceptance and recording

For each case, preserve the exact config, preview stdout, run stdout/stderr,
command line, `OFFLINE_RUN_CONFIG.json`, `SCIENCE_OPEN_TRAJECTORY.json`,
`SELECTED_RESEARCH_RESULT.json`, `science/` objects, and local SQLite files.
The verdict must be `supported` for IN and `violated` for OUT. `inconclusive`,
`not_evaluable`, or a process failure is a failed wiring case and does not
permit relaxing the frozen settings.

The prediction commit must precede the observation acquisition in repository
sequence. The evaluation must retain the exact tolerance source
`declared Gaussian acquisition noise; plug-in parameters; no global coverage guarantee`.
The `PredictionSpec` currently does not populate typed `candidate_ref` or
`program_ref` in the Article `_predictions` path; this identity gap is recorded
separately and is not silently treated as passed.

No FPR or 80% detection claim is permitted from these two cases. The `19`
null replicates are the frozen-family bootstrap calibration scope only.

The optional Cauchy extension is allowed only after both base cases satisfy the
above verdicts. It gets a separate budget `3.0`,
`unknown_exploration_limit=1`, `model_expansion_enabled=true`,
`grammar=["cauchy"]`, and `max_candidates=1`; it remains a finite typed
grammar enumeration, not autonomous discovery.
