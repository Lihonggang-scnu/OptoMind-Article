# Validation philosophy

VeriTMM separates **target quality** from **physical admissibility**. A design
can miss an ambitious target and still be a valid simulation; it may not pass
the physics gate by merely achieving a high optimization score. The v0.5
protocol adds deterministic transport, experiment identity, scientific-study
semantics, and failure contracts around the TMM
calculation; it does not replace the TMM checks.

The governing roles are: **AI proposes, TMM computes, and the verifier
certifies**. An optimizer is never its own certificate authority.

## Protocol-level validation

The public command surface is intentionally small and machine-readable:

```text
veritmm describe --json
veritmm schema simulation
veritmm schema optimization
veritmm schema sweep
veritmm schema sensitivity
veritmm schema tolerance
veritmm preflight task.json --json
veritmm run task.json --output-dir outputs/run --json
veritmm benchmark --offline --json
```

`preflight` checks the task contract, capability boundary, material coverage on
the complete declared wavelength grid, backend routing, and numerical risk notices. It does
**not** run a complete spectrum or optimization. `run` repeats the preflight
before computation and always produces `RUN_RESULT.json` for a normal run or a
preflight rejection. Typed failures carry action safety metadata; a recoverable
failure is not permission to apply a scientific change automatically.

## Acceptance layers

1. **Contract** — the JSON task must match the simulation or optimization
   contract and the runtime task model.
2. **Capability boundary** — the task must be planar, one-dimensional,
   isotropic, linear, and compatible with a plane-wave frequency-domain model.
3. **Material validity** — every optical-constant dataset must cover the
   requested wavelength range. Extrapolation is not enabled automatically; a
   range exception requires an explicit scientific decision.
4. **Preflight routing** — the requested outputs and coherence model must map to
   a supported backend before any full spectrum is executed.
5. **Raw numerical checks** — non-finite spectra, negative passive absorption,
   and energy-balance violations are surfaced rather than clipped away.
6. **Spectral convergence** — the result is repeated on refined wavelength grids.
7. **Independent comparison** — coherent results are compared with the Byrnes
   implementation when the task supports it. Optimization proposals are
   independently recomputed before acceptance.
8. **Certificate and envelope** — checks, tolerances, material identities,
   hashes, and limitations are serialized in
   `PHYSICS_ACCEPTANCE_CERTIFICATE.json`; `RUN_RESULT.json` exposes the status,
   failure actions, and artifact references.

## First-read artifacts

Read `RUN_RESULT.json` first. It is the run envelope, not a replacement for the
scientific outputs. Read `RESULT_SUMMARY.json` next for compact physics and
spectral features so an agent does not have to ingest the full
`SIMULATION_RESULT.json` or `SPECTRA.csv` before choosing its next action. Read
the certificate and full spectra when detailed scientific inspection is needed.

## Current deterministic coverage

The regression suite contains tests for:

- Fresnel and lossless-stack invariants;
- S-matrix/characteristic/reference-solver agreement;
- random passive multilayer regression;
- finite and mixed-coherence stacks;
- material ambiguity, dataset identity, interpolation, and range rejection;
- differentiable forward agreement and gradient optimization when PyTorch is installed;
- protocol capability manifests, JSON Schema exports, preflight without solver execution,
  typed failure actions, and single-object CLI output;
- run-result envelopes, artifact hashes, compact summaries, and preflight rejection;
- task serialization and command-line execution;
- experiment identity, cache provenance, lineage, compare, sweep and resume;
- autodiff sensitivity independently audited by NumPy finite differences;
- seeded tolerance sampling with separate conditional-yield and operational-success
  denominators, typed computational failures, and Wilson intervals over completed samples;
- robust candidate evaluation separated from nominal physics certificates, including a
  zero-failure completeness gate that prevents survivor-biased robust selection;
- offline AgentBench case contracts, reproducibility and unsupported false-acceptance;
- a published DBR/defect-cavity trend reproduction based on PMC9147317.

The literature case checks stop-band overlap, cavity-dip position, and field
enhancement trends. It is a trend-level reproduction, not a claim of exact
fabrication-level replication.

## Deliberate protocol boundaries

Sweep, sensitivity, tolerance, and robust optimization are formal v0.5 study
operations, but their reports remain distinct from physical validity. The
protocol does not execute external solvers or contain an LLM kernel. AgentBench
is evaluation evidence rather than a physics certificate, and the MCP
surface is a read-mostly, agent-safe projection (stdio, simulate-only)
without any new physics path. These boundaries do not weaken the TMM checks; they
define what each artifact is allowed to claim.

## Independent energy accounting (workbench audit)

The declared channels define absorption algebraically, `A = 1 - R - T`, so the
legacy audit metric `|R + T + A - 1|` is an identity: it surfaces NaNs and
bound violations but can never, by construction, expose a wrong absorption
model when `R` and `T` are self-consistent. The workbench therefore computes a
second, independent ledger — `A_independent(w)` per channel, integrated from
layer-resolved dissipation without consuming the solver's R/T outputs or the
closure formula:

- Byrnes backend: the sum of `absorp_in_each_layer` / `inc_absorp_in_each_layer`
  over the finite layers (incoherent layers included through the same
  incoherent absorption path already used for `layer_absorption`).
- Internal S-matrix/characteristic solvers: the decrease of the net complex
  Poynting flux across each finite film in the tangential-field admittance
  framework (s: `Y = n cos(theta)`, p: `Y = n / cos(theta)`), evaluated by the
  routine's own transfer-matrix solve; the reflection amplitude only fixes the
  field scale. It is anchored against the exact Airy closed forms of
  `analytic_oracle.single_film_power_rt` to ~1e-15.

The audit reports `energy_independent_max_abs_error`
(= max |1 - R - T - A_independent|), its worst channel and wavelength, and
`absorption_independence_class`: `layer_absorption_integral` when the
per-layer integrals cover every channel, or `unavailable` when they cannot be
produced (honest degradation, never a silent pass). These fields are computed
by `_audit_channels` but are kept in the additive
`ForwardSimulationResult.independence_audit` block rather than `result.audit`,
so persisted evidence, certificates, and `certificate_id` values stay
byte-identical until a declared promotion into the certificate lands.

## Certificate energy accounting and the verifier dependency graph

The acceptance decision consumes two recorded energy residuals:

- `residual_closure` — max |R + T + A_closure − 1| with `A_closure = 1 − R − T`
  (the historical algebraic self-consistency check);
- `residual_independent` — max |1 − R − T − A_independent| against the
  field-integrated absorption of the independent ledger.

When the independent ledger is available, both residuals must individually
stay within `energy_tolerance`; the failure context names which ledger
triggered (`triggered_by: closure | independent`). When the ledger could not
be produced, the certificate records the degradation explicitly
(`independence_class: derived_closure_fallback` plus `fallback_reason`) — a
fallback is declared, never silent. Evidence written before the ledger
existed (no `energy_accounting` field) loads as closure-only; the strict
evidence loader treats that single field as optional-on-load so historical run
directories keep replaying.

Every accepted certificate therefore carries an `energy_accounting` block:
the method (`layer_absorption_integral`), the independence class, worst-case
value-plus-location summaries of `R`, `T`, `A_closure`, and `A_independent`
(no spectra), both residuals, and the worst-case locations. What each check
may consume is declared statically in `VERIFIER_DEPENDENCY_GRAPH`
(`tmm_engine.acceptance`): the energy checks depend on
`solver.R`, `solver.T`, and `independent_absorption.layer_integral`, and
declare `A_closure` as a forbidden dependency —
`tests/test_energy_accounting_certificate.py` enforces mechanically (AST
scan) that the decision path never rebuilds absorption by subtraction.

The independent-solver comparison reports each ledger separately instead of
comparing only closure-consistent triples: `R` agreement, `T` agreement,
`A_independent` agreement (each implementation integrates its own absorption),
and full `energy_accounting` agreement. A sub-ledger that cannot be produced
on both sides is reported `unavailable` rather than silently dropped. This
split is what surfaced (and now regression-pins) a multi-layer field
propagation defect: an implementation that closes energy algebraically but
dissipates it in the wrong layers fails the `A_independent` agreement.

The regression tests in `tests/test_energy_independence.py` pin the contract:

1. *Lossless film* — `A_independent` vanishes and agrees with the closure to
   1e-9 on both solver families: no false alarms on physical results.
2. *Absorbing film* — `A_independent` matches the implementation-independent
   analytic oracle (`1 - R - T` from exact Airy closed forms) to 1e-9: the
   integral is a true physical anchor, not a second self-consistent loop.
3. *Injected absorption fault* — with the per-layer absorption scaled by 1.05
   and R/T untouched, the independent error exceeds 1e-3 while the legacy
   closure stays below 1e-12: the new ledger catches what the old one is
   blind to by construction.
4. *Injected R/T fault* — with `R` (or `T`) scaled by 1.02 and the absorption
   integral correct, the independent error exceeds 1e-3. The legacy closure
   remains exactly zero — absorption is *defined* from the same corrupted
   arrays — which is precisely the algebraic-closure defect this accounting
   removes; the assertion documents that blindness explicitly.

Since the certificate promotion, `tests/test_energy_accounting_certificate.py`
additionally pins that an injected absorption fault now drives an actual
acceptance **rejection** (`energy_conservation_failure` with
`triggered_by: independent`) under default tolerances, that unavailable and
pre-ledger evidence degrade explicitly with recorded reasons, and that the
cross-implementation ledger split catches a reference-side absorption fault
through `solver_disagreement`.

## Reciprocity verification (opt-in, CI-anchored)

Linear, isotropic, non-magneto-optic planar stacks satisfy Lorentz reciprocity:
the power transmittance of the forward stack at `theta` equals that of the
reversed stack (layer order reversed, incident and exit media swapped, built
through the public constructors so validation applies) at the mirrored angle
under conservation of the in-plane wave vector,
`sin(theta_b) = n_incident sin(theta) / n_exit`. For identical surroundings
this reduces to the same numeric angle. `tmm_engine.reciprocity.
check_reciprocity` compares the two directions on a bounded uniform
subsample (at most 11 points of the declared grid) and returns
`{status: passed | failed | unavailable, max_relative_deviation,
worst_wavelength_nm, reason}`.

`unavailable` is used honestly and enumerated: mixed-coherence stacks (outside
the certified check in this release), absorbing incident or exit media (the
mirrored angle is ill-defined for complex indices, and the solvers' power
normalization is not valid for a complex-index incident medium), a mirrored
angle beyond the exit medium's light cone, and non-finite output. An
`unavailable` result is never a disguised pass: it is recorded verbatim in
the evidence and, when requested, in the certificate.

The dimension is wired as `AcceptanceSettings.require_reciprocity`, **off by
default**: the check roughly doubles the simulations of the acceptance pass
(every sampled wavelength is simulated in both directions), and the default
acceptance policy is kept byte-stable — certificates of default runs carry no
reciprocity block and the golden verification-equivalence fixtures are
untouched. When enabled, a failure raises the new `reciprocity_failure` code
(a numerical/implementation defect, so it deliberately carries no
solver-handoff routing), and the certificate gains an additive
`reciprocity_check` block. The flag travels in the persisted policy artifact
(policies written before the flag load as `False`, so historical runs keep
replaying). CI anchors in `tests/test_reciprocity.py` run the check
unconditionally on both solvers: lossless film and DBR, absorbing layers,
oblique s/p and unpolarized channels, a tampered-backward-transmission fault
injection, an exact-oracle anchor for the mirrored direction, and the
unavailable enumeration.

## verify-run certification classification (v1.1 derived rule)

`verify-run` reports `certification_status: uncertified` when the certificate
records that **both** the spectral-convergence audit and the independent-solver
comparison were `not_requested` — the signature of a `skip_certificate` run.
This is a **derived classification rule (v1.1)**: it is inferred from the
certificate content because the certificate schema does not yet persist the
acceptance policy that produced it.  A future release is expected to persist
the decision explicitly (for example `certification_mode: full | bypassed` or
a `required_evidence` block in the policy artifact); until then, an operator
who legitimately configures a reduced policy with both checks disabled will
also be classified `uncertified`, which is the honest reading of that scope.

## Reproducibility levels (frozen 1.1 semantics)

Every run envelope carries a `reproducibility` block with the environment
fingerprint (Python, NumPy, best-effort BLAS description, platform, byte
order) and two declared levels:

- `identity_level: byte_identical_within_scheme` — normalized tasks, schema
  documents, canonical metadata, and every canonical-JSON-derived hash are
  byte-identical across platforms under the declared
  `identity_scheme`.  The guarantee is scoped to that scheme and the
  protocol semantics; a canonicalization change bumps the scheme rather
  than silently redefining it.  Enforced cross-platform by the
  `cross-platform` CI jobs (Windows and macOS) against committed golden
  task identities.
- `numerical_level: tolerance_equivalent` — R/T/A, fields, optimization
  results, and verifier inputs agree across platforms within the declared
  acceptance tolerances (the cross-solver tolerance class).  Bit-identical
  solver output is **not** claimed; that would require full environment
  pinning (Python/NumPy/BLAS versions plus CPU architecture) and is a
  separate future contract.  Enforced by committed golden R/T/A values
  compared within the tolerance class on the same CI jobs.

## Parallel execution semantics (V-12)

`ExecutionSettings.workers` (default 1) enables CPU parallelism for study
loops whose units are independent: sweep children today, with the generic
payload-map primitive (`tmm_engine.parallel.map_payloads`) available for
verified batch executors.  The default serial path is untouched — workers=1
runs the original code unchanged, so the two-level reproducibility contract
(`identity_level: byte_identical_within_scheme`,
`numerical_level: tolerance_equivalent`) holds as before.  Under workers > 1:

- every work unit is a pure function of its payload (task JSON, output path,
  logical index, derived seed); nothing depends on completion order, so
  identities stay byte-identical and numerics stay tolerance-equivalent
  regardless of scheduling;
- child seeds, where a study uses them, derive deterministically as
  `int(sha256(canonical {parent_seed, child_index, domain})[:16], 16)`
  (`derive_child_seed`);
- child processes pin BLAS to one thread (`OMP/MKL/OPENBLAS_NUM_THREADS=1`)
  to avoid nested-parallel oversubscription;
- `tests/test_parallel_equivalence.py` executes the same sweep with
  workers=1 and workers=4 and asserts identical per-child scientific
  fingerprints (normalized child task, simulation science fields,
  certificate content identity, logical index), explicitly excluding run
  ids, timestamps, output paths, and completion order.

Scope note: tolerance/robustness Monte Carlo currently draws all samples from
one sequential in-process RNG stream.  Parallelizing it would change the
sample set — a scientific semantics change requiring a declared per-sample
seed contract — so it stays serial in this release and is reported here
rather than silently approximated.

## Evidence summary helper (for harness integrators)

Consumers that hold only a physics certificate — for example a harness with
an inner workbench+certify layout and no managed-run artifacts — get the
nine-dimension evidence ledger with one call:

```python
from tmm_engine.protocol.evidence import build_evidence_summary

summary = build_evidence_summary("outputs/run/PHYSICS_ACCEPTANCE_CERTIFICATE.json")
# or: build_evidence_summary(certificate_dict)
summary["schema_version"]      # "veritmm-evidence-summary-v1"
summary["certificate_id"]      # echoed identity
summary["accepted"] / summary["status"]
summary["evidence_coverage"]   # the ledger, field-by-field identical to the
                               # evidence_coverage embedded in RESULT_SUMMARY.json
```

The function is a pure projection: it reuses
`EvidenceCoverage.from_certificate` — the single authoritative definition of
evidence-coverage semantics — and never writes artifacts or changes schema.
Golden consistency is pinned by `tests/test_evidence_summary.py`: for the
same managed run, the helper's ledger and the one embedded in
`RESULT_SUMMARY.json` agree field by field, through both input forms.  This
is the recommended wiring for OptoMind O-10 consumers.
