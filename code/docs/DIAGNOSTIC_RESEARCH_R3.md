# Bounded diagnostic research, R3

This opt-in production-callable slice uses the existing Article ModelIR compiler and VeriTMM. It does not add another optical solver, call an LLM, or modify legacy F3 evidence.

## What executes

1. Fit every declared mechanism to the same original sample acquisitions. Thickness and variable-index/Cauchy parameters remain in the sample ModelIR.
2. Enumerate removable reference layers, air gaps, wavelength grids, angles and polarization. A reference front end is a separate known observation operator, never a replacement sample.
3. Screen finite fitted basins and front-end tolerance corners using a bounded sparse linear readout. Propagate the full declared covariance, including shared offset and gain covariance. Normalize weights by L1 norm, bound term count, and retain the raw-vector information comparison.
4. Refit every shortlisted competing mechanism adversarially against existing evidence and prospective observations, including bounded per-session offset/gain. Report every optimizer receipt and the remaining search limitations. Divide the finite-search discrimination score by acquisition and mounting/calibration cost.
5. Commit program, sample/session/frontend/node identities, every model's predictions, and readout before acquisition. One acquired observation evaluates multiple model versions.
6. Keep those prequential scores immutable. Refit all mechanisms on old plus new data; retain feasible basins and report numerical failure separately from model inadequacy. A distinct confirmation acquisition remains outside refitting.
7. Export selected-result failure semantics, raw acquisitions, candidate rejection reasons, immutable snapshots and acquisition sequence receipts. The method lifecycle qualifies only supported source trajectories.

The existing OpenResearchLoop also now refits every carried competitor after selection data, retains its old predictive scores separately, carries unresolved family failure to SelectedResearchResult, and exports snapshot receipts. An unknown exploration limit of zero is explicitly recorded as not authorized. A locally supported predictive envelope does not cancel training family inadequacy.

## Run the deterministic offline example

From the Article root, with the project's installed Python environment:

```sh
export PYTHONPATH="$PWD/veritmm:$PWD/code"
python -m optomind_optics.harness.science.diagnostic_cli --write-example --output /tmp/diagnostic-inputs
python -m optomind_optics.harness.science.diagnostic_cli --offline \
  --config /tmp/diagnostic-inputs/PUBLIC_CONFIG.json \
  --world /tmp/diagnostic-inputs/SEALED_SYNTHETIC_WORLD.json \
  --output /tmp/diagnostic-run
```

Use a fresh output directory. The public config contains only the mechanism hypotheses, parameter domains, search axes, noise model and finite budgets. The separate sealed file is read only by the synthetic provider; its answer, parameters and noise seed do not enter the selector. The provider is an in-process offline fixture, not an OS security boundary or hardware driver.

The example begins with reciprocal reversed-layer intensity ambiguity. The system selects a reference front end and conditions, buys an independently noisy synthetic observation, refits both mechanisms, and takes a separate confirmation if the acquisition budget permits.

## Plan from real or externally acquired data

```sh
python -m optomind_optics.harness.science.diagnostic_cli \
  --config PUBLIC_CONFIG.json --observations RAW_OBSERVATIONS.json \
  --output /tmp/next-diagnostic
```

Planning mode forbids `--world`. Observation JSON is an array of `DiagnosticDatum` records: `acquisition_id`, complete `DiagnosticAction`, `values`, and positive-definite raw-noise `covariance`. Use the run's `raw_data` records as the serialization example. Noise calibration and instrument identity remain the caller's responsibility. This produces all-family fits and `NEXT_MEASUREMENT.json`; it does not operate hardware.

Production orchestration may instantiate `DiagnosticResearchLoop` with an authorized provider implementing `acquire(action, request_id=...) -> DiagnosticDatum`. Providers must validate program/physical conditions and implement durable request identity before real acquisitions. The loop refuses to resume a nonempty repository: after an interrupted acquisition, reconcile the provider receipt explicitly rather than repeating the action.

## Contracts and boundaries

- Search is finite discrete material/thickness/gap/angle/polarization/grid enumeration with bounded multistart inner optimization. It is not a global optimizer or a global mechanism exclusion certificate.
- Noise is a declared Gaussian raw covariance plus bounded explicit session gain/offset nuisance. The discrepancy gate uses a frozen chi-square threshold with data-count degrees of freedom; it is not a population-calibrated, family-wise or sequential false-rejection guarantee.
- Robustness uses declared front-end tolerance corners, three-point spectral band averaging and angular quadrature. It is not a manufacturing yield estimate.
- Readout combines spectral intensity channels from one action. It does not establish an E04 improvement percentage or create information beyond the complete raw vector.
- Bare angle/polarization scans and a fixed reference front end are costed candidate baselines. A phase/ellipsometry instrument baseline is unavailable in this slice and is not claimed beaten.
- Numerical failure retains uncertainty. If every converged family is inadequate, the result withholds a winner and reports `model_family_inadequate`; mechanism-set expansion requires a separately declared policy/budget.
- No claim of >=95% classification, >=90% tolerance success, >=30% cost reduction, cross-task transfer or new physical principle follows from a single synthetic case.
- Historical unbound objects remain byte/hash-compatible and explicitly project `legacy_unbound`; no old object is retroactively certified.

## Tests

```sh
python -m pytest -q code/tests/test_science_diagnostic_identity.py code/tests/test_science_diagnostic_design.py
python -m pytest -q code/tests/test_science_c13_open_loop.py
```

The small escape test is important: the wrong mechanism violates a frozen point prediction, then successfully refits within its allowed domain. The new fitter preserves that mechanism instead of treating the old point's failure as global refutation.

## Same-case visual evidence

The offline CLI also writes `materials/01_frozen_predictions.{png,svg}`, `02_refits_and_action_cost.{png,svg}`, `action_benefit_cost.csv` and `case_timeline.json`. These read committed observations, prediction envelopes, fit receipts and sequence identities. The first panel explicitly labels initial training fits; the diagnostic panel uses the original frozen pre-acquisition predictions. Plotting never adds a likelihood term.

To regenerate only visual evidence from an existing run:

```sh
python -m optomind_optics.harness.science.diagnostic_artifacts --run /tmp/diagnostic-run
```

On shared compute hosts, `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1` can reduce excessive tiny-matrix thread overhead. This changes CPU threading, not the frozen scientific budget or measurement policy.

The new diagnostic synthetic provider shares the production TMM forward implementation; its blinded parameters and independently drawn noise are separate from the policy, but this is not an independent-solver validation. In the checked default case, the fixed reference baseline can win the automated comparison. Such a result demonstrates executed search and evidence-driven selection, not superiority of a newly generated front end.
