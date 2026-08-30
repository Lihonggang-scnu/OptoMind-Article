# TMM Research Harness v2

## Purpose

This is a reusable research runtime for planar, laterally uniform, linear,
isotropic multilayer optics. It turns one natural-language task into a bounded
sequence of problem analysis, literature method research, multi-route planning,
immutable TMM execution, failure-driven adjustment, and traceable reporting.

The asset is the chain, not any one optimized coating. Performance requirements
are soft scoring objectives and never decide physical admissibility. The
runtime returns verified candidates and their trade-offs even when an ambitious
target is not reached.

## Capability boundary

Supported physics:

- planar one-dimensional layer stacks;
- isotropic scalar optical constants;
- coherent and mixed-coherence propagation;
- s, p, and unpolarized plane-wave excitation;
- spectral and angular R/T/A analysis;
- amplitudes, phase, group delay, and group-delay dispersion where declared;
- layer absorption and emissivity bookkeeping;
- continuous thickness optimization, population search, quantization, and
  perturbation-based robustness checks.

Out-of-scope tasks fail closed or request a higher-fidelity solver. Examples are
lateral gratings, metasurfaces, anisotropic layers, nonlinear/time-domain
physics, and arbitrary three-dimensional scattering. The system must not force
these tasks through TMM.

## Model policy

Every LLM-facing node in this Harness is locked to `qwen3.7-flash` with model
fallback disabled. Qwen proposes bounded, schema-validated scientific actions;
it never certifies physics. TMM execution, convergence checks, energy checks,
candidate admission, budgets, and stopping are deterministic.

## Runtime flow

### 1. Problem analysis

`problem_analyzer.py` identifies the task intent (analysis, design,
optimization, reproduction, comparison, or robustness), extracts bands,
angles, polarizations, materials, manufacturing constraints, ambiguities, and
the declared TMM compatibility boundary. It separates user facts from route
hypotheses and does not silently turn unspecified values into user constraints.

### 2. Literature method research

`method_research.py` searches in this order:

1. local ReviewKnowledgeBase text chunks;
2. Semantic Scholar Snippet Search;
3. Semantic Scholar paper search only when snippet retrieval yields no usable
   body passage;
4. OpenAlex only as a complementary fallback.

Online Semantic Scholar body snippets are first-class method evidence after a
TMM scope and passage-quality gate. A pure outlook sentence such as “future
work includes multilayers” is not method guidance. Metadata and abstracts may
support discovery or background, but not an executable method claim.

Important Semantic Scholar distinction:

- the Datasets API exposes bulk release files for self-hosted corpus-scale
  processing; it is not an online per-paper semantic full-text query service;
- Snippet Search is the targeted online route that returns relevance-ranked
  passages from title, abstract, and body text.

The default online client therefore uses Snippet Search directly. It does not
download the bulk corpus and does not perform metadata batch enrichment unless
explicitly enabled. Queries stop early once enough permitted method evidence is
available.

### 3. Strategy planning

`strategy_planner.py` proposes one to four distinct, TMM-compatible routes.
Each route records its hypothesis, topology, materials, variables, soft
objectives, manufacturing considerations, evidence IDs, theory basis, risks,
and executable request. Literature evidence IDs are allowlisted; invented IDs
are rejected.

No route may contain a hard performance admission threshold. The only valid
global stop instruction is bounded exhaustion or stagnation followed by honest
best-effort reporting.

### 4. Immutable compilation and execution

`task_compiler.py` converts a route into the frozen `OpticalDesignTask`
protocol. The verifier-first TMM environment then runs simulations and
optimizers, checks capability compatibility, numerical convergence, finite
values, and energy behavior, and emits physics acceptance certificates.

### 5. Feedback and stopping

`research_feedback.py` distinguishes material-data failure, capability mismatch,
invalid task compilation, optimizer stagnation, target conflict, and
physically valid but limited performance. It can preserve candidates, refine a
route, request a new literature method, try a new topology, stop completed, or
stop at the TMM capability boundary.

Scores are comparable only within the same route/objective contract. A
report-only route with score zero is not worse than an optimized route. Final
selection preserves verified candidates per route and reports the underlying
physical metrics.

### 6. Reporting and observability

Every run writes:

- `REQUEST.json`
- `PROBLEM_ANALYSIS.json`
- `METHOD_RESEARCH.json`
- `STRATEGY_PLAN.json`
- per-iteration compiled task and TMM result artifacts
- `ITERATION_HISTORY.json`
- `FEEDBACK_HISTORY.json`
- `RESEARCH_EVENTS.jsonl`
- `FINAL_ANSWER.json` and `FINAL_ANSWER.md`
- `RESEARCH_RESULT.json`

`RESEARCH_RESULT.json.telemetry` contains wall time, Qwen calls and token usage,
estimated CNY cost, model names, S2/OpenAlex counters, TMM forward evaluations,
optimizer runs, and confirmation that performance targets were not used as
admission gates.

## Command-line use

```powershell
py -3.11 scripts/run_tmm_research_harness.py `
  "Design a dielectric multilayer reflector over 500-650 nm and compare angular robustness." `
  --online-method-research `
  --qwen-method-synthesis
```

Without `--kb-sqlite`, the CLI narrowly auto-discovers the active high-quality
ReviewKnowledgeBase under `outputs/review_knowledge_base`. Optional explicit
knowledge bases can be supplied by repeating `--kb-sqlite`; explicit paths
replace automatic discovery. The output directory must be new or empty so a
run cannot silently mix assets from an earlier question.

## Evaluation discipline

The frozen benchmark contains five development tasks (`DEV01`-`DEV05`) and five
holdouts (`HOLDOUT06`-`HOLDOUT10`). Only development tasks may influence code or
prompt tuning. Holdout files require an explicit environment opt-in and must be
opened one at a time only for final blind evaluation.

The development portfolio covers single-layer antireflection inverse design,
angle/polarization analysis of a dielectric Bragg reflector, defect-cavity
resonance and phase dispersion, lossy selective-absorber optimization, and a
mixed-coherence coated finite substrate.

## Current acceptance evidence

- 202 TMM-specific tests pass.
- One real end-to-end Qwen/S2/TMM run completed with five Qwen calls, 21,334
  input tokens, 6,008 output tokens, estimated Qwen cost of about CNY 0.091,
  176 forward evaluations, and two optimizer runs.
- The same real run returned both a practical MgF2 solution and a theoretical
  low-index limit without comparing their incompatible soft scores globally.
- A final minimal S2 smoke required one Snippet Search call, no Qwen call, no
  metadata batch call, and no OpenAlex call.

These measurements are acceptance observations, not fixed production cost
promises. Cache state, question breadth, provider rate limits, and requested
optimizer budgets change runtime cost.
