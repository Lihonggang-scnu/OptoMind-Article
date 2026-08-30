# Article Section Writing - Stage 10 Design Note

Date: 2026-08-16
Module: `optomind_optics/harness/article_writing.py`
Prompts: `Article Section Writer.txt`, `Article Section Format Repair.txt`

## Purpose

Stage 10 converts one explicitly selected Stage 9 `StoryCandidate` into
traceable section drafts.  Each section is written independently against a
bounded local payload, and every paragraph records an exact local source
ledger (claims, facts, artifacts, value tokens, figures) so prose can be
traced back to Stage 8 claims/facts and Stage 9 figure contracts.

## Inputs and identity

- `ArticleDirectorPlan`, trusted `ClaimLedgerResult`, accepted
  `ArticleArchitectureResult`, an explicit `selected_story_id`, and
  caller-supplied `TrustedValueRecord`s for exact artifact fields.
- The module revalidates before any provider call: ledger
  `validation_errors`, architecture `validation_errors`, ledger
  `source_plan_id` vs plan id, architecture `deterministic_inventory` vs the
  live plan/ledger, story membership, and every selected story
  claim/fact/figure/artifact relationship (including Stage 9's positive
  claim-to-fact chain and artifact authorization).  Foreign plan/ledger/
  architecture inputs hard-block without invoking the provider.
- Stage 9 now carries `source_plan_id`, `source_ledger_id`, and the full
  caller-asserted `artifact_inventory` on `ArticleArchitectureResult`, and
  exposes the public content-addressed `compute_architecture_id`.  Stage 10
  requires matching source IDs and recomputes/verifies the architecture ID
  from the carried inventory, handoffs, and stories before any provider call,
  so a changed thesis/contracts with an old ID are rejected.  Legacy
  serialized Stage 9 data without these identity fields remains loadable but
  is not trusted for writing.
- Every trusted value record must match the Stage 9 artifact inventory
  (artifact present, field declared), correspond to a Stage 9 artifact-field
  binding in the selected story whose artifact is authorized by the bound
  claims/facts, and duplicate `(artifact_id, field)` records are rejected.
  For `prose_safe=True` the descriptor must carry a validated `sha256` and
  the record's `source_hash` must match it exactly; the `rendered_value` must
  be one finite scalar numeric literal (no prose/markup/NaN/inf; the unit is
  separate metadata).  Figure-only records may be non-scalar but can never
  enter prose.

## Trusted values and aliases

- `TrustedValueRecord` carries `artifact_id`, `field`, `rendered_value`,
  optional `unit`, `source_hash`/`derivation`, an optional semantic `label`,
  and `prose_safe`.  Arrays/curves are marked `prose_safe=False` and remain
  figure-only; using them in a value token is a hard error.
- Local code issues deterministic semantic aliases: `C01_<words>` for claims,
  `V01_<FIELD>` for values, `FIG01_<role>` for figures.  The bundle exposes
  reversible alias maps (`claim_alias_map`, `fact_alias_map`,
  `value_alias_map`, `figure_alias_map`).  The model never creates aliases.
- Authorization is section-local: claim aliases (including deferred claims)
  must belong to the section's `claim_bindings`, figure aliases to the
  section's `figure_ids`, and value tokens to artifact-field bindings of
  figures assigned to that section.  A globally valid alias owned by another
  section is a controlled section error, never a silent cross-section bind.
- A paragraph using `[VALUE:...]` must cite at least one claim alias that
  authorizes that exact artifact-field binding through a figure assigned to
  the section; background/transition paragraphs cannot carry naked value
  tokens, and a value cannot support an unrelated claim.  Authorization is
  computed per artifact binding: in a multi-claim or multi-artifact figure,
  only claims whose Claim/Fact provenance names that exact artifact may
  authorize its fields.
- The model never sees exact scalars: the request contains only the token
  alias, label, unit, and meaning.  Local rendering replaces validated
  `[VALUE:...]` tokens with the exact `rendered_value` (+ unit) after all
  checks pass.

## Section requests and responses

- One request per section: original question, charter scope, story shape/
  thesis, the section contract (heading, purpose, target word range, claim
  bindings with roles, figure aliases), full statements/scopes/limits/FactRecord
  linkage for the section's claims, the section's figure contracts, prose-safe
  value tokens, and compact outlines of the other sections.
- The model fills only high-information content: paragraphs
  (`text_with_value_tokens`, `claim_aliases`, `figure_aliases`,
  `paragraph_role`, `inference_kind`, `inference_note`),
  `deferred_claim_aliases`, and `author_notes`.  Local code supplies section
  ID/title/schema/status/aliases/source maps.

## Local validation and numeric safety

- Unknown/misspelled claim/figure aliases or value tokens are hard errors for
  that section.  No fuzzy matching is ever used to bind a source.
- `bounded_inference` requires at least one bound claim and a non-empty
  `inference_note`.  `unsupported` inference is flagged honestly.  Result,
  method, and limitation paragraphs must cite claim aliases; background and
  transition prose is not over-policed.
- The selected story contract itself is revalidated as immutable input:
  unique section/figure IDs, legal section/figure claim binding roles,
  figure `claim_ids` equal to `claim_bindings`, positive quantitative
  claim-to-FactRecord chains, figure artifact authorization, section figure
  ownership and `section_target`, and duplicate/conflicting bindings are all
  checked before provider calls.
- Measurement-like raw numbers (decimals/scientific notation/percentages/
  comparison thresholds/number+unit) typed by the model outside an allowed
  `[VALUE:...]` token are a section hard error.  Plain structural integers
  (`stage 2`, `3 routes`, `Figure 1`, `2D`) are warnings at most.
- Assigned-but-unused claims become explicit deferrals (when listed in
  `deferred_claim_aliases`) or warnings, never a whole-bundle blocker.
- Word count is guidance: the target range is derived from the section
  purpose and repeated in the request; an unusually short section is warned,
  never failed for length.  Ranges are substantial (background/introduction
  700-1200; methods/results/discussion 800-1500; limitations 300-700;
  conclusion 250-500; default 500-1000) and the prompt repeats them.

## Fail-open workflow and repair

- Input provenance/integrity errors fail the bundle before provider calls.
- A provider failure or one malformed/unsafe section never erases valid
  sibling sections or Stage 8/9 assets: the affected section is marked
  `blocked` (provider failure) or `needs_revision` (validation errors), any
  safe paragraphs are retained as non-publishable prose, and processing
  continues.
- At most one compact format/source repair round is attempted for failed
  sections using the optional repair provider.  Repaired output is accepted
  only if it becomes publishable or strictly reduces the error count; a
  different error set with the same count is not progress, the original
  findings are retained, and processing stops for that section.

## Output and persistence

- `ArticleSectionDraft` carries tokenized and rendered prose, paragraph
  drafts, a `ParagraphSourceLedger` per paragraph (claim/fact/artifact/value
  token/figure IDs, inference kind/note, charter scope, claim-specific
  `scopes`, limits, roles), deferred claims, warnings/errors, word count,
  target range, model/usage, attempts, and status
  (`publishable`/`needs_revision`/`blocked`).  `artifact_ids` include the
  source artifacts of every cited claim/fact, not only value-token or
  cited-figure artifacts.
- `ArticleDraftBundle` aggregates sections, the full source ledger, deferred
  claims, reversible alias maps, truthful aggregated model/usage, and a
  `publishable` flag; only fully source-safe sections are publishable.
- Optional persistence is append-only, idempotent, journal-recoverable across
  `ArticleMemoryStore` and `ExperimentGraph`: a `bundle-<id>` node records one
  `article.section` event per section, and the memory store receives full
  bundle/source-ledger/section payloads.  `completed` is written only after
  both stores finish; retries replay missing records/events without
  duplicates and reject conflicting full payloads.  Memory records index the
  actual artifact IDs from each section/source ledger and the bundle union.

## Qwen integration

- `QwenSectionWriter` and `QwenFormatRepair` are concrete `qwen3.7-flash`
  adapters with JSON-object output, configurable non-tiny `max_tokens`, one
  request per section, and per-call usage preserved and aggregated.  When
  provider usage has tokens but no cost, the concrete adapters add
  `estimated_cost_cny` from the existing local cost ledger
  (`estimate_call_cost_cny`); bundle aggregation sums writer+repair costs and
  `attempts` truthfully.  Injected fake providers carry truthful
  `provider_model` labels and are never priced as Qwen unless explicitly
  labeled; the envelope default is `unknown`, never `qwen3.7-flash`.  No
  fallback model exists.

## Stage 11 handoff

The publishable section drafts feed Stage 11 claim/fact/expression review:
each section's rendered prose plus its source ledger is the audit input, and
`needs_revision` sections are queued for author revision with their exact
errors.
