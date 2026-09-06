# Changelog

## 2.0.0 — Unreleased

The unreleased 1.1.0 line was folded into this version (it was never tagged
separately); its entries are preserved below as "Carried over from the
unreleased 1.1.0 line".

### 2.0 highlights (V-round)

- Independent absorption accounting: the verifier now derives A from
  layer-resolved dissipation instead of the `1 - R - T` closure, and the
  certificate carries an `energy_accounting` ledger judged on two residuals.
- Opt-in Lorentz reciprocity verification with CI anchors and honest
  `unavailable` degradation.
- Unified physics backend registry: the same KernelSpec runs on the numpy
  reference and the torch differentiable backend; backends are drop-in files.
- First-class gradient/sensitivity/batch API with finite-difference
  cross-checks and `top_influential_layers`.
- Declarative OptimizationProblem: constraints compiled into the search box
  (boundaries, not penalties), objectives mapped to the existing chain.
- SimulationJob runtime: explicit state machine, failure-isolated batches,
  and a tool-fingerprint cache that invalidates on any engine change.
- Provenance graph v1 with `explain_superiority` evidence chains.
- ScientificIntentSpec semantic IR with compilation equivalence certificates
  and a 50-case semantic regression set.
- Runtime-generated capability catalog with a CI drift gate.
- Namespaced MCP tool grid (25 tools), compact discovery, and effect
  annotations.
- Deterministic CPU parallel execution for sweep children (spawn, pinned
  BLAS threads, seed derivation).

### Evidence summary helper

- `build_evidence_summary(source)` (protocol/evidence.py): one-call
  nine-dimension evidence summary from a certificate path or parsed
  certificate, reusing `EvidenceCoverage.from_certificate` as the single
  authoritative semantics.

### Carried over from the unreleased 1.1.0 line


### Trust model (M0: canonical identity)

- Canonical-JSON hashing converged on `tmm_engine.hashing` as the single entry
  point (`canonical_json_bytes` / `canonical_json_dumps` / `stable_sha256` /
  `file_sha256`); `acceptance.py` certificate identities, artifact indexes,
  cache identity, research content IDs, measurement-action IDs, and JSONL
  batch/dataset indexes now share one convention with `ensure_ascii=False` and
  `allow_nan=False`.  ASCII payloads keep their previous digests; payloads
  containing non-ASCII text (µm, Greek letters, CJK) no longer fork between
  an ASCII-escaped and a UTF-8 convention.  This intentionally invalidates
  prior cache entries for non-ASCII tasks and changes those task identities;
  the convention is machine-readably declared as
  `identity_scheme: "veritmm-canonical-json-v1"` in run envelopes, result
  summaries, and cache identity.
- `RunResultEnvelope` carries the optional `identity_scheme` field, and the
  response-context rebase preserves it across cache replay.

### Verification architecture (evidence / decision split)

- Split acceptance into evidence collection and judgement:
  `collect_verification_evidence()` records a versioned
  `VerificationEvidence` (`veritmm-verification-evidence-v1`) holding only
  raw decision inputs — task identity, capability assessment, physics audit,
  spectral-convergence and cross-solver reports, tightest margin, high-
  precision referee report, material provenance, and runtime — while the new
  pure `evaluate_evidence(evidence, settings)` recomputes `accepted`,
  `status`, failures, evidence coverage, and the certificate identity from
  that evidence.  `certify_simulation()` is now thin orchestration over the
  two halves; its certificates are byte-for-byte equivalent to the previous
  monolithic implementation, pinned by golden fixtures
  (`tests/fixtures/verification_equivalence/`).

### Persisted verification evidence and policy (replay-ready)

- Simulate-mode runs now persist `VERIFICATION_EVIDENCE.json` and
  `VERIFICATION_POLICY.json` (`veritmm-verification-policy-v1`) alongside the
  certificate: evidence is the observed facts, the policy is the judgement
  rule (tolerances and audit switches actually used), and the certificate is
  the verdict.  Both artifacts are indexed in `RUN_RESULT` with hashes and
  sizes and reference no other artifact, so their hashes stay
  self-contained.
- Evidence binds `schema_version`, `identity_scheme`, `run_id`, and
  `task_sha256`; `load_verification_evidence()` re-validates all four and
  rejects unknown/missing fields, foreign schemes, and mismatched run or
  task bindings, so evidence from one run cannot be spliced onto another.
  Policy parsing is equally strict.  Replaying `evaluate_evidence` from the
  persisted pair reproduces the persisted certificate exactly, including
  `certificate_id`, and stays valid when default tolerances change in future
  releases.
- Cache replays never present the source run's verification artifacts as
  their own: the copied pair is dropped before replayed artifact indexes and
  response contexts are rebuilt, so a replay carries no stale-bound evidence.
- Fixed a Windows-only race in `find_cache_source`: reading a freshly
  archived `RUN_RESULT.json` could transiently fail while the file was still
  being scanned by external readers, silently downgrading a valid cache hit
  to a full re-execution.  As a Windows transient-read mitigation the read
  now backs off briefly and retries before falling back to a safe miss; the
  scan-lock hypothesis is consistent with the observed symptoms but has not
  been confirmed as the only cause.

### LegacyEvidenceAdapter (published v1.0.0 replay)

- `verify-run` now recognizes published v1.0.0 run directories (envelope
  `veritmm-run-result-v1` without `identity_scheme`) and maps their
  observation artifacts onto the evidence model:
  `tmm_engine.legacy_evidence.build_legacy_evidence()` reads only fields that
  really exist in the historical artifacts — the task payload from
  `NORMALIZED_TASK.json`, channels/extras/audit/raw material provenance from
  `SIMULATION_RESULT.json`, and the recorded audit reports from the
  certificate — and never derives evidence from `accepted`, `status`,
  `failures`, `evidence_coverage`, or `certificate_id`.
- The mapped task payload and raw provenance must re-hash, under
  `veritmm-canonical-json-v1`, to the identities recorded in the legacy
  certificate; historical non-ASCII tasks hashed under the pre-1.1
  ASCII-escaping convention are honestly reported as
  `replay_status: unavailable` instead of being re-hashed into agreement.
  When the mapping succeeds, the replayed verdict is compared on its
  scientific core (schema evolution tolerated, reported as
  `comparison_scope: verdict_core`); any scientific difference — including a
  tampered historical verdict — fails the replay.  Insufficient legacy
  evidence leaves the historical certificate untouched as
  `certification_status: certified` with `replay_status: unavailable` and
  `overall_status: incomplete`.

### Four-state `verify-run` (offline independent verification)

- New `veritmm verify-run RUN_DIR --json` command and
  `tmm_engine.verify_run.verify_run_dir()`: a third party holding only a run
  directory can independently verify it.  The fixed evaluation order is
  envelope parse → structure → per-artifact hash/size integrity →
  certification determination → evidence/policy load →
  `evaluate_evidence()` replay → comparison with the persisted certificate →
  authenticity.  On any integrity failure the report stops with typed
  diagnostics and marks the remaining states `not_evaluated`: tampered
  material is never re-judged.
- The report keeps four independent states — `integrity_status`,
  `certification_status`, `replay_status`, `authenticity_status` — plus one
  machine-consumable `overall_status` (`valid | invalid | incomplete`); it is
  never collapsed into a single boolean.  The states are independent by
  contract: a cache replay is `certified` with `replay_status: unavailable`
  because it carries a certificate issued by an earlier run and no evidence
  of its own, while `uncertified` is reserved for results that never went
  through full acceptance checks (detected from the certificate itself when
  both spectral convergence and the independent solver were not requested —
  the `skip_certificate` case).  Authenticity is fixed `unsigned` until the
  1.2 DSSE signing layer.
- Binding chain across scopes: the envelope carries the normalized-operation
  task identity while the certificate and evidence carry the raw-task
  identity; verify-run binds envelope↔certificate via `certificate_id`,
  evidence↔run via `run_id`, and evidence↔certificate via the shared
  raw-task hash, so spliced evidence from another run fails closed.  Exit
  codes: 0 valid, 2 invalid, 3 incomplete.

### Agent Skill packaging (`veritmm-tmm`)

- New packaged Agent Skill `tmm_engine/skills/veritmm-tmm/` (shipped in the
  wheel): `SKILL.md` with spec-compliant frontmatter and progressive
  disclosure, four `references/` documents (protocol surface, artifact sets,
  certificate and the four verify-run states, capability boundaries), and
  minimal scripts (an example task plus a CLI-fallback
  preflight→run→verify-run workflow).  The skill documents exactly the
  MCP-exposed capability set — MCP tools first, CLI fallback — and teaches
  the fail-closed boundaries without advertising outside-MCP capabilities.
- `veritmm skill-path` prints the packaged skill location;
  `veritmm install-skill TARGET` installs it as `<TARGET>/veritmm-tmm` for
  host discovery.  The source is fixed by construction (no source parameter),
  symlinked targets are refused, and overwriting an existing installation
  requires the explicit `--force` confirmation; copied content is verified
  against the package.

### Agent-safe MCP stdio surface (`veritmm-mcp`)

- New optional extra `[mcp]` (`mcp>=2,<3`, official SDK v2) and entry point
  `veritmm-mcp` running the agent-safe projection over **stdio**.  The tool
  layer (`tmm_engine/mcp_server.py`, no SDK types at the boundary) exposes
  `describe / schema / examples / preflight / run / history / inspect /
  lineage / compare / verify_run` and delegates exclusively to the existing
  managed execution, experiment store, and `verify-run` entry points — there
  is no second execution path and no new physics authority.
- Explicit allowlist: operator and debug parameters (`skip_certificate`,
  `physics_python`, convergence tolerances, `device`, `output_dir`,
  `store_dir`, `user_metadata_json`, ...) do not exist on this surface.
  `run` is simulate-mode only with the default acceptance settings; the
  server owns the artifact root, the task root, and the experiment store.
- Tasks are accepted inline (`{"simulation": {...}}`), as
  `veritmm://task/...` resources, or as paths inside the server-owned task
  root; every file reference is containment- and symlink-checked.  Large
  artifacts are served as MCP resources (`veritmm://run/{run_id}/{path}`)
  while tool responses carry only envelopes and artifact references.
- `verify_run` is read-only (run identity only, no policy parameters) and
  returns the four-state report.
- Golden consistency gate: the same task through CLI defaults and the MCP
  surface produces identical normalized task identity, identical
  `certificate_id`, and byte-identical `SIMULATION_RESULT.json`.

### Solver handoff on unsupported physics (informational routing)

- Unsupported-physics typed failures now carry machine-readable routing:
  `suggested_solver_family` (refined per boundary), `handoff_hints`
  (`required_inputs` + `notes` naming what an external solver family would
  need beyond a 1-D stack), and `informational_only: true` — injected exactly
  when routing information is present.  Mapping: lateral-periodic geometry →
  `rcwa`; arbitrary 2-D/3-D → `fdtd_or_fem`; anisotropic → `berreman_4x4`;
  magneto-optic → `magneto_optic_4x4`; nonlinear → `nonlinear_time_domain`;
  finite beam → `beam_propagation`; dipole → `dipole_near_field`; mode
  source → `eigenmode_expansion`; time-domain requests → `fdtd`.  Failures
  without a reliable recommendation (for example mixed-coherence output
  combinations) keep their historical serialization shape with no routing.
  The handoff adds routing information only: no capability decision, no
  verdict, and no certificate acceptance changes, nothing is executed, and
  no external solver is endorsed.  The anisotropic equivalence fixture was
  regenerated for the additive failure fields; accepted-run certificates are
  byte-identical.

### Analytic validation oracle suite (CI anchors)

- New `tmm_engine/analytic_oracle.py`: exact closed forms implemented from
  first principles in the tangential-field admittance framework (s:
  `Y = n cos(theta)`, p: `Y = n / cos(theta)`), fully independent of every
  engine solver — single-interface Fresnel power R/T, single-film Airy R/T
  (absorbing films supported, lossless surroundings), quarter-wave-stack
  reflectance at the design wavelength, infinite-stack stopband edges, and
  Fabry-Perot resonance wavelengths.
- 21 CI anchor tests: the engine (thin-film interface limit, lossless and
  absorbing films at oblique incidence in both polarizations, quarter-wave
  closed form, Fabry-Perot unity transmission, off-resonance energy balance)
  and the mpmath referee agree with the oracle to `1e-9` relative; the
  stopband edges bound the high-reflectance region of a finite stack.  The
  oracle is CI-only by contract and a test asserts no runtime module imports
  it; a runtime oracle contract remains out of scope for 1.1.
- Two test-fidelity fixes surfaced by the anchors: the
  `absorbing_stack_energy_failure` equivalence fixture had silently been an
  `invalid_task` rejection (complex `constant_n` never passes layer
  validation) and now uses the supported `constant_k` interface, exercising
  the energy-failure path its name claims; the quarter-wave tests now use
  quarter-wave optical thickness (`design/(4n)`) and the correct exit index.

### Cross-platform reproducibility semantics (frozen 1.1 levels)

- Run envelopes now carry a `reproducibility` traceability block: the
  environment fingerprint (Python, NumPy, best-effort BLAS description,
  platform, byte order) plus two declared levels —
  `identity_level: byte_identical_within_scheme` (canonical-JSON-derived
  identities are byte-identical across platforms under the declared
  identity scheme) and `numerical_level: tolerance_equivalent`
  (numerical artifacts agree across platforms within the declared
  acceptance tolerances).  Bit-identical solver output is deliberately not
  claimed without full environment pinning.  The block travels through
  cache replay and the compact response projection.
- New `cross-platform` CI matrix jobs (Windows and macOS) enforce both
  levels against committed golden values: fixed task identities
  (byte-identical) and fixed simulations' R/T/A (within the cross-solver
  tolerance class) plus a core physics smoke subset.
- `docs/VALIDATION.md` records the two-level semantics and their evidence.

### Documentation site, cookbook, and release engineering

- MkDocs Material documentation site (`mkdocs.yml`, `docs/`): landing page,
  verified cookbook (CLI chain, Python API, MCP surface, skill install,
  four-state verification, legacy runs), the full documentation set, and a
  selected `mkdocstrings` API reference built from the defining modules.
  Built and validated locally (`mkdocs build`, zero errors).
- New `docs` GitHub Actions workflow building and deploying the site to
  GitHub Pages; `docs` optional dependency extra
  (`mkdocs-material`, `mkdocstrings[python]`).
- `Development Status` classifier moved to `4 - Beta` for the 1.1 line.
- `CONTRIBUTING.md` documents the release/DOI process (Zenodo GitHub
  integration mints versioned DOIs; `CITATION.cff` stays in lockstep with
  the runtime version through the `version-identity` CI gate).
- The MCP golden-consistency test retries a transient CLI process failure
  once (Windows fresh-file scanning); a content mismatch is never retried.

### Backfilled changes already present in the source tree

- Fixed a sign-convention defect in the Abeles characteristic-matrix
  off-diagonal term shared by the diagnostic characteristic backend and the
  mpmath high-precision referee (`+i·sin δ` → `-i·sin δ`): an absorbing layer
  amplified instead of attenuated, so the referee could certify gain as loss.
  Passivity and cross-solver agreement are now regression-tested for every
  declared solver, and the referee suite covers absorbing films.
- Added the material candidate-ranking feed (`material-candidate-ranking.v1`)
  with semantic aliases, Unicode subscript normalization, and parenthesized
  dataset-selector parsing.
- Added `SpectralMetricManifest` to the capability manifest, declaring band
  reductions (`band_mean`, `band_worst_case`), interval semantics, and the
  non-validity of reductions before a run is spent.

## 1.0.0 — Initial public release

First stable release of VeriTMM: an AI-ready, verifier-first transfer-matrix
tool for autonomous multilayer-optics research. The solver proposes; an
independent verifier certifies. Neither the optimiser nor a calling agent can
certify its own result.

### Core physics and solving

- Verifier-first physics pipeline: energy conservation, cross-solver agreement,
  high-precision mpmath referee, and machine-readable acceptance certificates
- Full oblique-incidence TMM (s/p polarisation, angle sweeps)
- Differentiable TMM via PyTorch autograd for gradient-based inverse design
- Sobol / Latin-hypercube / grid parameter sampling with explicit fallback policy
- Robust optimisation with tolerance budgets and yield statistics, including a
  CVaR tail-risk objective whose reported value is recomputed on an independent
  final Monte Carlo ensemble
- Sensitivity analysis (autodiff + finite-difference cross-check)

### Verification

- Local prioritised spectral and angular refinement rather than uniform midpoint
  insertion, leaving declared grids, result arrays, and task hashes
  byte-identical; an exhausted refinement budget is reported as exhausted, never
  as convergence
- Active challenge verification: a deterministic, replayable bounded search for
  weak evidence that emits a minimised task suitable for regression hardening

### Evidence and uncertainty

- `EvidenceCoverage`: a typed additive ledger over nine independent dimensions
  with `verified` / `not_evaluated` / `unavailable` / `failed` states, so an
  unevaluated dimension is never reported as confidence
- `UncertaintyBudget`: a GUM/NIST-style ledger across numerical, material,
  parameter, and sampling components that keeps categorical applicability gaps
  (for example unmodelled anisotropy) separate from numerical uncertainty
  instead of collapsing both into one number

### Experiment loop

- Persistent experiment store with lineage tracking and run cache
- `FitTask` with `IdentifiabilityReport`: bounded least-squares fitting plus
  Jacobian SVD, condition number, effective rank, and parameter correlation; a
  fit result explicitly does not carry a physics certificate
- `MeasurementPlan`: deterministic D-optimal / A-optimal next-measurement
  selection from local Fisher information, retaining rejected alternatives and
  labelled as local linearisation rather than global Bayesian design

### Provenance and archival

- Single runtime version source (`tmm_engine/_version.py`) with a CI gate
  asserting that runtime, `pyproject.toml`, and `CITATION.cff` agree
- Archive schema registry (`archive_schema_version = 2`) with read-time v1→v2
  migration that never rewrites stored artifacts, and a fail-closed
  `SchemaTooNewError` for unknown future schemas
- Immutable export profile emitting `EXPORT_MANIFEST.json` with byte-identical
  artifact hashes

### Interfaces and research infrastructure

- `veritmm` CLI with JSON Schema contracts, no-spectrum preflight, typed
  failures, compact responses, and AgentBench for offline agent evaluation
- Interactive AgentBench with a nine-state environment and checkpoint scoring,
  gated on zero unsupported false accepts
- Research interface: dataset factory, design space, evaluator, batch runner
- `CandidateSet` contract with Pareto filtering, tolerance-based deduplication,
  distance metrics, and selection provenance
- Chunked verified batch execution: batched proposal forward passes with
  independent per-candidate certification, proven equivalent to scalar
  evaluation
- Apache License 2.0, machine-declared as `License-Expression: Apache-2.0` and
  pinned by a metadata regression test
