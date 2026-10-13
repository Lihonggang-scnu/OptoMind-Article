# B1 prediction wiring result

Status: completed under the frozen SPEC. All observations are
`V1_synthetic`; this is synthetic wiring only. No real-device, autonomous
discovery, FPR, 80% detection, or manufacturing claim is made.

## Results

| Case | Preview | Run | Cost / paid / network | Validation state | Evaluation | Timing |
|---|---:|---:|---:|---|---|---:|
| F0 model-in | 0 | 0 | 2 / 0 / 0 | `supported_within_frozen_noise_envelope` | `supported`, statistic `-0.00375455464857519`, no violating indices | CLI `30.5115129 s`; ledger `19.2659163095523 s` |
| F3 model-out | 0 | 0 | 2 / 0 / 0 | `supported_within_frozen_noise_envelope` | `supported`, statistic `-0.000799059748271519`, no violating indices | CLI `10.9622719 s`; ledger `6.11100879870355 s` |

F0 passed its frozen model-in wiring criterion. F3 failed its frozen
model-out criterion: the required verdict was `violated`, but the actual
verdict was `supported`. The optional budget-3 Cauchy extension was therefore
not run.

## F3 diagnosis

The F3 training adequacy record did detect a model-family problem:

- `state=model_family_inadequate`
- `route=propose_structure`
- `p_value=0.05`
- `training_discrepancy=44.49577944536407`
- `null_refit_count=19`
- `unknown_exploration_limit=0` and `model_expansion_enabled=false`, so no alternative was fitted or selected.

The final 19-point holdout still passed the declared plug-in envelope. Its
maximum absolute residual was about `0.005926555`, while the envelope radius
was about `0.006725615`, leaving a minimum inside margin of
`0.000799059748271519`. The F3 dispersion was therefore detectable by the
training adequacy diagnostic but not rejected by this single held-out
prediction under the frozen noise envelope. This is a metric/sensitivity
result, not a reason to relax the verdict.

The fitted inference state reached `film.n=2.0342196935143` and
`film.thickness_nm=40.000000000000014` (the lower bound); the terminal design
choice `135 nm` is a separate finite-pool design output and does not turn the
holdout into a mechanism refutation.

## Commit and identity evidence

For both cases the final prediction object was committed at repository
sequence `20`; its validation observation and acquisition were committed at
sequence `21`. Thus `prediction_before_acquisition=true`.

The final `PredictionSpec` used:

- `statistic=simultaneous_envelope`;
- `raw_channel=R`, 19 validation coordinates;
- tolerance source `declared Gaussian acquisition noise; plug-in parameters; no global coverage guarantee`.

In both cases `candidate_ref` and `program_ref` were absent from the persisted
PredictionSpec. The sequence and typed observation checks passed, but the
candidate/program identity branches in `compare_bound_observation` were not
activated. This remains a separately recorded contract gap.

## Artifacts

- Frozen inputs: `FROZEN_SPEC.md`, `case_in_f0.json`, `case_out_f3.json`.
- Commands: `COMMANDS.log`.
- Import and source commit: `IMPORT_COMMIT.txt`.
- Raw preview and run stdout/stderr, exit codes, and timing metadata: `runs\in-f0\` and `runs\out-f3\`.
- Local SQLite receipts and repository: `provider.sqlite`, `jobs.sqlite`, `programs.sqlite`, and `science\` under each run directory. These DB files are local and are not for upload.

The source repository remained at commit `9e335a9b92daa345baddad1f78db4ff3167e20e6` with a clean worktree. No production source, Git metadata, F drive, network, paid provider, or package installation was changed.
