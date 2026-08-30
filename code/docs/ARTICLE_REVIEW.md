# Article Review - Stage 11 Design Note

Date: 2026-08-16
Module: `optomind_optics/harness/article_review.py`
Prompts: `Article Scientific Reviewer.txt`, `Article Expression Reviewer.txt`,
`Article Author Reviser.txt`

## Purpose

Stage 11 runs a deterministic fact audit over accepted Stage 10
evidence-token section drafts, then advisory scientific/expression review and
bounded author revision.  Only the deterministic fact audit can block a
section; reviewer/reviser findings are advisory and can never block on their
own.

## Inputs and identity

- `ArticleDirectorPlan`, `ClaimLedgerResult`, `ArticleArchitectureResult`,
  `ArticleDraftBundle`, the selected story identity, and the same
  `TrustedValueRecord`s used in Stage 10.
- Before any provider call, Stage 11 revalidates: bundle `plan_id`/`ledger_id`/
  `architecture_id`/`story_id` vs the inputs, the content-addressed
  `compute_bundle_id` recomputed from the bundle sections, the exact ordered
  bundle section ID list equal to the selected story section order, the aggregate
  source ledger recomputed from sections, and the alias maps rebuilt from
  story/ledger/value records.  It then runs the full Stage 10 input
  revalidation (plan/ledger/architecture identity, architecture-ID
  recomputation, story contract, value integrity).  Any mismatch is a bundle
  hard blocker before providers are invoked.
- Every publishable section is additionally reconstructed deterministically
  from its stored paragraph response and the authoritative plan/ledger/story/
  value inputs using the same Stage 10 assembler, then compared field by
  field: section/story/architecture identity, contract title and figure IDs,
  paragraph order/IDs/prose/bindings/inference/warnings/errors, exact source
  ledger entries (no extras or omissions, including fact/artifact IDs,
  scopes/limits/roles), tokenized/rendered prose aggregates, deferred
  IDs/aliases, word count/target, and publishable status.  A recomputed
  public content ID cannot authorize an internally inconsistent object.
- Public Stage 10 helpers added for this: `compute_bundle_id`,
  `validate_writing_inputs`, `build_writing_alias_maps`, and
  `revalidate_section_draft`.

## Deterministic fact audit (hard authority)

Per section/paragraph it verifies:
- unique section/paragraph/source-ledger identities;
- `ParagraphDraft` vs `ParagraphSourceLedger` consistency (claims, figures,
  value tokens, inference metadata);
- claim->fact->artifact provenance, including that ledger `artifact_ids`
  contain the source artifacts of every cited claim/fact;
- section-local claim/figure/value authorization, including the exact
  value->cited-claim->fact/claim->artifact rule (a value is authorized only by
  figure-bound claims whose Fact/Claim source artifacts include that artifact);
- tokenized-to-rendered exact value substitution;
- numeric safety (measurement-like raw numbers outside allowed tokens);
- inference metadata rules and Stage 10 section status.

Unknown/cross-wired IDs, artifact/hash/source mismatches, invented
measurements, wrong rendering, persistence conflicts, and upstream identity
mismatch are hard blockers for the affected section (or the whole bundle for
upstream identity).

## Advisory review and revision

- Scientific reviewer (`qwen3.7-flash`): scope drift, overclaim,
  contradiction, misuse of negative evidence, illogical inference.  Minor or
  major findings only.
- Review requests show artifact descriptors for the union of the section's
  source-ledger artifacts, the artifacts backing every section-visible
  Claim/Fact, and the section's figure artifact bindings, so a figure-less
  section whose claim is backed by an artifact still shows that descriptor.
  These two concerns stay separate: artifact descriptors use the broad union,
  while value aliases/labels/units remain limited to artifact fields
  authorized by the section's Figure contracts (the existing Stage 10 value
  logic).  A figure-less section may see its supporting artifact descriptor
  but is never offered a value alias merely because that artifact has a
  global trusted value record.
- Finding claim aliases are resolved only against aliases visible to the
  current section; hallucinated or cross-section aliases are dropped (with a
  warning) and never create a cross-section `claim_id` binding.  A non-empty
  finding span absent from the target paragraph's rendered and tokenized text
  is cleared with a warning, fail-open.  Exact duplicate findings in one
  response are deduplicated preserving first-seen order with a concise
  warning, so they cannot inflate severity or progress.
- Expression reviewer (`qwen3.7-flash`): clarity, terminology, repetition,
  flow, rhetoric.  Cannot block.
- Author reviser (`qwen3.7-flash`): edits only paragraphs named by actionable
  findings; unaffected paragraphs must remain byte-for-byte identical.
  Revised sections pass the same Stage 10 assembler/validation plus the
  deterministic fact audit.  A revision that removes/changes source bindings,
  introduces unauthorized values, fabricates numbers, flips negative/positive
  roles, removes/ adds/ swaps value tokens, or lists duplicate paragraph
  targets is rejected and the last safe draft retained.  For every revised
  paragraph, claim IDs, fact/artifact/value token IDs, figure IDs, roles,
  scopes/limits, paragraph role, and inference metadata are preserved exactly.
- At most 3 revision rounds.  Stopping conditions: no actionable findings, no
  material progress (content changed but findings only swapped, or identical
  content identity), provider failure, or round limit.  Progress requires
  changed content AND a strictly reduced finding count or severity weight.
- Reviewer outcomes are explicit per role (`valid`/`unavailable`/`malformed`)
  and every attempted call is counted even when it raises.  A finding is
  resolved only by a valid re-review response from its own reviewer role.
  Re-review requirements are role-specific: the required roles are those of
  the actionable findings being revised, so a never-enabled or non-actionable
  reviewer role never vetoes another role's correction.  Fresh findings are
  used per role only when that role's re-review is valid; otherwise that
  role's prior findings are retained.  If a required role is unavailable or
  malformed, the last previously reviewed safe draft and all previous
  findings are retained and processing stops without claiming progress.
  Initial reviewer failure remains fail-open with warnings.
  `ReviewedSection.reviewer_status` exposes the latest required review
  outcome per role, and bundle `model_status` is computed from those final
  per-section role outcomes, not only from initial calls.

## Statuses and outputs

- Section status: `ready`, `ready_with_findings`, `blocked`.  Bundle status:
  `ready`, `ready_with_findings`, `blocked`, `partial`.
- `ArticleReviewResult` carries deterministic audit findings, structured
  reviewer findings (target paragraph, span, reason, suggested action, claim
  aliases/ids), per-round before/after content IDs and resolved/retained
  finding identities, final reviewed sections, original and final source
  ledgers, hard blockers (bundle-level plus every affected section's),
  retained advice, truthful model/usage/cost/attempts, and a
  content-addressed `result_id`.  Per-section warnings are preserved locally
  on `ReviewedSection` as well as in the bundle warnings.

## Persistence

Optional append-only, idempotent, journal-recoverable persistence to
`ArticleMemoryStore` and `ExperimentGraph`, versioned by `result_id` (the
input `review_id` remains the review-task identity): a `review-<result_id>`
node records one `article.review` event per finding (finding_id as the event
review_id, blocking severity for deterministic audit findings), and memory
stores full review/section/ledger payloads with real artifact IDs indexed.
Exact same-result replay is idempotent; a distinct result for the same
`review_id` creates a distinct version rather than silently skipping or
conflicting.  `completed` is written only after both stores finish; retries
replay missing records/events without duplicates and reject conflicting full
payloads.  Cross-store atomicity is not claimed.

## Stage 12 handoff

`ready`/`ready_with_findings` sections plus their final source ledgers feed
Stage 12 claim/fact finalization and publication packaging; `blocked`
sections are routed back to Stage 10/11 with their exact hard-blocker
messages.
