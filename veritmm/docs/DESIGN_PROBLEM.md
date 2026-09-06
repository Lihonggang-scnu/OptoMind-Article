# Declarative OptimizationProblem

`tmm_engine.design_problem` is the **declaration layer plus compiler** between
an agent and the existing differentiable optimization chain.  An agent
declares *what it wants* — stack skeleton, band objectives, constraints,
tunable parameters — and `compile_design_problem` emits an ordinary
`OptimizationTask` that runs the existing optimize chain with its existing
independent acceptance verification.  No optimizer behavior is added or
changed here.

Positioning (see the planned IntentSpec layer):

```text
IntentSpec   — scientific intent (what question is being asked)      [planned]
OptimizationProblem — machine-understandable design space (this file)
OptimizationTask    — executable contract consumed by the optimize chain
```

## Declarative objects

All objects are strict pydantic models (unknown fields rejected, fully JSON
serializable); the JSON Schema is exported via
`veritmm schema design-problem`.

- `stack` — the structural skeleton: media (named dataset or constant
  index), layer order, and starting thicknesses (`ProblemStack`,
  `ProblemLayer`, `ProblemMedium`).
- `objectives` — band objectives (`ProblemObjective`): `metric`
  (`MeanReflectance` / `MeanTransmittance` / `MeanAbsorption` /
  `BandContrast` / `StopbandRipple`), `band_nm`, optional
  `contrast_band_nm` (required for `BandContrast`), `angle_deg`, `pol`,
  `direction` (`maximize` / `minimize`), `weight` (positive).

  Metric mapping (compiled into `SpectralTarget` entries):

  | metric | mapping |
  |---|---|
  | `Mean*` | one target over the band: `maximize` → `target=1.0, at_least`; `minimize` → `target=0.0, match` (drive-to-zero) |
  | `StopbandRipple` | same direction mapping with `aggregation="worst_case"` — worst in-band R maximized (or minimized) is in-band ripple suppression |
  | `BandContrast` | **dual-band composite**: the objective weight is split evenly; `R(in-band)` `at_least 1` + `R(contrast band)` `match 0` for `maximize`, inverted for `minimize`. The weighted surrogate `(1-R_A)^2 + R_B^2` is the monotone contrast surrogate on [0,1] observables |

- `constraints` — boundaries, not penalties
  (`ProblemConstraint`): `total_thickness_max`, `layer_thickness_min`,
  `layer_thickness_max`, `layer_count_max`, `material_count_max`,
  `forbidden_materials`.  Thickness constraints are compiled into the
  per-layer optimization box, so every reachable point is feasible by
  construction (`total_thickness_max` proportionally shrinks the free span
  until `sum(max) <= value`); structural constraints are compile-time
  validations whose violations are typed rejections
  (`DesignProblemError`, codes `constraint_violated` /
  `constraint_infeasible`) before any optimization runs.  A constant-index
  medium counts as one material identity `(n, k)`.
- `parameters` — tunable layers (`ProblemParameter`): `layer_selector` is
  explicit `indices`, `"alternating"` (every second layer from 0),
  `"cavity"` (the middle layer of an odd-count stack), or `"all"`; the
  selected layers become the optimizable ones with `bounds_nm` clamps.
- `solver` — the existing `OptimizerSpec` fields; `robustness` — the
  existing `RobustnessSpec` fields (optional).  Both reuse the task-level
  specs and their validators unchanged.
- `spectrum` — optional `(start_nm, stop_nm, points)`; when omitted it is
  derived deterministically from the objective bands (union plus a 10%
  margin, 101 points).

## CLI

```bash
veritmm schema design-problem
veritmm optimize-problem PROBLEM.json --output-dir outputs/run --json
```

`optimize-problem` compiles and immediately runs the existing optimize chain;
the response carries a bounded problem summary plus the ordinary run
envelope (status, `certificate_id`, artifact references).  Compile-time
constraint violations exit 2 with typed failure codes.

## Verification semantics

Compilation adds no verification semantics: the compiled task runs the
existing optimize chain, every optimized candidate is recomputed
independently, and acceptance is decided solely by the existing verifier and
certificate.  A rejected constraint at compile time is cheaper than an
invalid candidate at the certificate boundary — that is the verifier-first
ordering this layer encodes.
