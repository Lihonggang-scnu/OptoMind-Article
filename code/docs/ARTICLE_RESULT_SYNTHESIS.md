# Article Result Synthesis Bridge

## Purpose

`article_result_synthesis.py` is an additive post-experiment bridge between
the validated eight-stage Article pipeline outputs and the existing
`ArticleFeedbackController` / `build_claim_ledger` contracts.  It turns
verified `ArticleAssetCompilationResult` records plus their matching
experiment-planning rows into:

- source-bound `SynthesisFinding` records (statement tokens, aliases, role,
  rationale, scope/limits),
- a content-addressed derived `ArticleDirectorPlan` that keeps the original
  charter, capability, coverage matrix, stage plan, and original hypotheses
  unchanged and appends locally-IDed result-grounded hypotheses,
- cloned enriched `ObservationCard` records that retain the original
  observation/experiment IDs and add only locally validated `partial_support`
  hypothesis updates plus a canonical coverage route.  Their `artifact_ids`
  are contracted to the route-local union of artifacts actually referenced by
  surviving findings (trusted-value aliases contribute their backing
  artifact; explicit artifact aliases contribute their artifact).  Root run
  bookkeeping files such as `EXECUTION_MARKER.json` are never automatically
  bound; the deterministic `metrics["synthesis_omitted_artifact_ids"]` key
  records the original artifact IDs that were omitted,
- an `ArticleFeedbackResult` from the existing deterministic controller and
  a `ClaimLedgerResult` from the existing claim builder.

The bridge never rewrites existing validation and never claims confirmation.
Synthesized findings enter as `partial_support` (medium ceiling).  Negative
and limited results are legitimate findings.

## Contracts

- `ResultSynthesisProviderInput`: one bounded per-route payload.  The model
  sees only semantic aliases (`R01`, `H01`, `AV01`, `TV01`, `CV01`), the
  immutable question/charter, capability scope, original hypotheses,
  proposal context, observation summary, candidate summaries, artifact
  summaries, and trusted scalar views.  No credentials, raw files, hashes, or
  hash-like IDs are sent.
- `ResultSynthesisProviderResult`: findings rows plus truthful provider/model
  telemetry.
- `SynthesisFinding`: the only writable unit.  Local code assigns
  `finding_id` and `synthesized_hypothesis_id` deterministically.
- `SynthesisAliasRecord` / `alias_manifest`: local alias ownership map;
  per-route aliases are stored route-scoped (`R01.TV01`) and resolved into a
  per-route local manifest before validation and token resolution.
- `ArticleResultSynthesisResult`: status `ready` / `partial` / `unavailable`
  / `invalid`, deterministic `result_id`, derived plan, cloned observations,
  feedback results, ledger, alias manifest, findings, provider usage,
  warnings/errors.

## Join and identity rules

- Each usable asset (`ready` or `partial`) must join to exactly one ready
  planning row by `request_id` + `task_hash`.
- Verified: request `task_digest`/`run_id`, observation `experiment_id`
  against the request experiment card, proposal hypothesis ids against the
  plan, request-experiment hypothesis ids against the proposal, cells stage
  against proposal stage, and the proposal stage's canonical coverage route
  against the plan coverage matrix.
- Duplicate ready rows, mismatched identity, duplicate observation IDs, or
  unknown coverage routes fail closed (`invalid`).
- Every trusted value must reference an artifact present in that route's
  verified descriptor inventory; a value whose backing artifact is absent is
  a source-integrity failure (`invalid`).
- `invalid`/`unavailable` assets are skipped with warnings; their warnings
  propagate so a partial asset makes the synthesis result `partial`.

## Provider findings validation (fail-open per row)

- Roles are restricted to `method`, `result`, `limitation`, `robustness`.
- Statement tokens must be present in `source_value_aliases`; aliases must
  exist in the route's local manifest (cross-route or unknown aliases are
  rejected per row).
- Every cited trusted value must be `prose_safe` and finite.
- Every cited artifact/value alias must back an artifact present in the
  route's verified descriptor inventory; otherwise the finding row is
  rejected (fail-open per row, defense in depth over the join-time check).
- Bare numeric literals in a statement must appear in the local structural
  allowlist derived from the immutable question/charter/capability/hypothesis/
  proposal/request context.  Invented numbers reject the row.
- Duplicate findings are dropped; one bad row never discards valid siblings.
- A provider exception or `validation_errors` marks only that route
  unavailable/partial.  No provider yields `unavailable` with no fabricated
  facts.

## Qwen provider

`QwenArticleResultClaimSynthesizer` uses `QwenFlashOnlyClient` locked to
`qwen3.7-flash`, reads the external English prompt
`code/prompts/optical_harness/Article Result Claim Synthesizer.txt`, and
returns `ResultSynthesisProviderResult`.  Usage is captured with the
repository's estimated list-price cost helper; no network calls are made by
tests.

## Usage

```python
result = synthesize_article_results(
    plan, planning, assets,
    provider=QwenArticleResultClaimSynthesizer(),
    run_id="stage17-real-selective-emitter-006",
)
```

The returned `derived_plan`, `observations`, `feedback_results`, and `ledger`
feed directly into the existing Stage 7/8 contracts and downstream writing,
review, manuscript, reproducibility, presentation, and delivery stages.
Claim facts bind only the contracted evidence artifact IDs; original run
root files remain available only for later reproducibility/audit, not as
automatic scientific claim evidence.
