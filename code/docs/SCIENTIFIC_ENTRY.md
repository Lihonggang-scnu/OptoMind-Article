# Explicit mechanism-diagnosis entry

The existing Article integration API and `run_article_pipeline_integration.py`
can now run the bounded diagnostic research loop directly. Default coating
optimization and the existing `OpenResearchLoop` host API are unchanged.

This entry **does not translate arbitrary natural language into a scientific
specification**. A human or separately validated upstream generator must supply
the complete `DiagnosticEntrySpec`. There is no inferred or fallback physical
objective. Unsupported objectives, inconsistent questions/analysis, unsupported
measurement domains, and changed resume inputs are rejected before acquisition.

## Inputs

The public `article-diagnostic-entry.v1` JSON contains:

- `kind: mechanism_diagnosis`, the exact Article `run_id` and `original_question`;
- structured `OpticalProblemAnalysis` and its `canonical_hash`, including an
  analyze/compare/reproduce intent and the explicit measurement domain;
- `objective: {"kind": "mechanism_diagnosis"}` and source references;
- 2–12 distinct `MechanismFamily` records with executable typed `ModelIR`;
- complete frozen `DiagnosticConfig`, `DiagnosticSearchSpace`, and initial
  `DiagnosticAction` values, including nuisance assumptions and finite budgets;
- a public provider descriptor: identity, evidence tier, channels, status
  support, and whether its declared noise model is validated.

Use the strict `DiagnosticEntrySpec` model to validate these inputs. Its analysis
is recorded explicitly as caller-supplied analysis, then checked against the
committed Article analysis before any acquisition. The pipeline does not claim
that an LLM independently generated that analysis or chose the hypotheses.

For an offline exercise, the synthetic world answer is a **separate** evaluator
JSON containing `family_id`, `parameters`, optional `seed`, and optional
`mismatch_layer`. It is not placed in the public spec, research task, proposal
context, or scientific repository. The host-only durable evaluator records its
raw receipts and restores the same synthetic noise/session history on restart.
This is process-level data separation, not an OS security boundary.

## CLI

Set the existing `ARTICLE_COMPILATION_AUTHORITY_KEY` through the normal approved
environment mechanism, then run from `code/`:

```sh
python scripts/run_article_pipeline_integration.py \
  --question-file question.txt --run-id my-diagnostic-run \
  --work-dir /absolute/path/to/new-run \
  --execution-root /absolute/path/to/execution-root \
  --science-entry-spec public-diagnostic-spec.json \
  --science-offline-world separate-sealed-world.json
```

The run directory must be new/empty. Resume the exact same inputs by adding
`--resume`; changed scientific inputs or changing back to default optimization
are rejected. Known completed acquisitions are recovered from durable receipts.
Unknown outcomes keep their reservation and are not automatically resubmitted.
An unresolved outcome leaves stage 7 uncommitted; the CLI reports
`pending_observation` (exit 2), and a later resume queries the same receipt.
Even a fully completed Article resume rechecks the frozen provider identity,
terminal scientific checkpoint, gate receipts, and canonical authoring source.

The explicit diagnostic path records the legacy method/optimization route
planning stages as `skipped` with a `not_applicable` reason, rather than inventing
optimizer requests. It constructs no unused Qwen planner clients. Scientific
acquisitions still use the shared execution gate and program receipts.
An explicit diagnostic spec is rejected under a legacy/shadow release selection;
it cannot silently fall back to optimization.

For an authorized non-synthetic provider, use the integration API's explicit
`science_provider_factory`. The factory must match the public descriptor and
provide `acquire`, `status`, and a fixed non-secret `recovery_identity`. There is
no arbitrary Python-module loader in the JSON or CLI, and no built-in hardware
provider is implied by this interface.

## Output and scope

The result contains precommitted predictions, raw observations, all-family
refits, bounded diagnostic selection, independent same-specimen confirmation
when available, and canonical source-bound claims. Qualified successful
programs enter the existing bounded-trial method registry. This is not automatic
production promotion or proof of transfer benefit.

`SCIENCE_OPEN_PIPELINE_HANDOFF.json` binds the canonical `SelectedResearchResult`
to the scientific authoring source. It is **not** a finished manuscript, PDF,
Review delivery, or a claim of recursive self-improvement. Synthetic results
remain synthetic. An honest confounded/inadequate result is a valid completed
research process, not a discovered mechanism.

The existing reversal fixture is exercised through the actual API and CLI in
`tests/test_science_article_entry.py`, including separate-process CLI run/resume,
fresh-instance recovery after a
provider return before local receipt commit, unknown-outcome blocking, original
task binding, and default-entry memory/write-once admission regressions.
