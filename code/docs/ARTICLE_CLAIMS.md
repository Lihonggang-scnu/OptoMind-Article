# Article Claim Ledger & Completion Audit — Stage 8 Design Note

Date: 2026-08-15
Module: `optomind_optics/harness/article_claims.py`
Prompt: `prompts/optical_harness/Article Claim Coverage Auditor.txt`

## Purpose

Stage 8 turns the Stage 7 hypothesis/observation history into a deterministic
Claim Ledger: source-bound `ClaimCard` + immutable `FactRecord` pairs for
writable hypotheses, plus a research-question completion audit over every
charter goal and success criterion.  Qwen is optional and semantic-only for
goal-to-claim coverage mapping; it never creates claims, facts, strengths,
statuses, or evidence.

## Deterministic ledger rules

- Inputs: `ArticleDirectorPlan`, ordered `ArticleFeedbackResult` records, and
  trusted `ObservationCard` records.  Feedback is replayed deterministically
  (from proposed, applying `from_status` to `to_status` in order) using the
  Stage 7 forward-only transition authority: illegal transitions
  (`proposed` to `confirmed`, or any exit from terminal
  `confirmed`/`refuted`/`superseded`/`retired`) are rejected.  Feedback results
  carrying `validation_errors` or `stop_hard_blocker` are never trusted ledger
  input.  Every referenced hypothesis, observation, experiment, artifact, and
  route is validated; unknown or mismatched provenance is a hard integrity
  blocker with no claims, facts, or persistence.
- Provenance is authoritative and union-based: an evidence-bearing decision
  must reference real observations; artifact IDs must be a subset of the UNION
  of artifacts on the referenced observations (not each observation
  individually); experiment IDs must agree with the resolved observations;
  duplicate observation IDs (inside one update or across the input collection)
  are rejected.  Authoritative observation and experiment provenance is
  derived from the resolved `ObservationCard`s.
- A writable claim requires a `partially_supported` or `confirmed` hypothesis
  AND at least one real source artifact bound through validated trusted
  observations.  Missing artifacts make the claim non-writable (draft) with an
  explicit warning — never a fabricated `FactRecord`.
- Refuted hypotheses produce a `refuted` claim with counter-evidence but no
  active positive fact; proposed/under-test hypotheses are visible non-writable
  drafts.  Their positive statements never enter the fact registry.  Refuted
  claims preserve counter-evidence observation/experiment/artifact provenance
  in metadata (and carry the counter-evidence artifacts as `source_artifact_ids`
  where semantically clear).
- `FactRecord.source_artifact_ids` come exactly from validated observation
  provenance.  Claim metadata preserves ALL contributing positive evidence
  (partial-support plus confirmation rounds): hypothesis/observation/
  experiment/route IDs, evidence kinds, scope, limits, counter-evidence, and
  claim/fact IDs.  `discriminator_confirmed` is revalidated against the
  referenced ObservationCard metrics (`matched is True`, declared metric keys
  present, `physically_valid`); forged provenance hard-blocks.  Strength is
  capped locally: partial support at most `medium`; confirmation is `high`
  only when the `ResearchCharter.scope` itself is non-empty and the claim has
  revalidated `discriminator_confirmed` evidence, otherwise `medium`.
- Claim/fact/ledger IDs include the plan id plus stable semantic/provenance
  content (including `hypothesis.statement`), so IDs never collide across
  plans or across changed semantics under a reused plan id.  Scope is scientific
  (`ResearchCharter.scope` plus route/experiment bounds), and the writable
  FactRecord statement itself is scope-bounded.
- Refuted ClaimCard IDs also include the refutation/counter-evidence
  provenance (refuted observation/experiment/artifact IDs), so a changed
  refutation cannot collide with an old persisted refuted claim.
- FactRecord metadata carries the complete validated provenance
  (hypothesis/claim/observation/experiment/route IDs, evidence kinds, source
  artifacts, scope, limits, counter-evidence).  `limits` is derived from
  hypothesis `risk_notes` plus charter constraints and capability assumptions
  rather than an empty placeholder.

## Completion audit

- Every charter goal (`goal-NN`) and success criterion (`criterion-NN`)
  appears in `ArticleCompletionAudit.rows` with coverage in
  `covered/partial/gap/unknown/not_applicable`, unique contribution, expected
  value of more work, stop reason, and rationale.  Audit IDs hash the full
  audit content.
- The optional semantic provider receives locally prepared bounded batches
  (`build_coverage_batches`, 20 goals per batch; nothing is silently
  truncated).  Each batch carries ONE local read-only positive claim table at
  the batch top level together with the research `question` and
  `charter_scope` (writable/evidence-bound claims only: id, statement, scope,
  status, strength, source count); each goal carries only goal fields plus
  `allowed_positive_claim_ids`; refuted/draft claims are never offered as
  positive evidence.  The provider must return, per goal, claim IDs plus
  `coverage`, `unique_contribution`, `expected_value_of_more_work`, `stop_reason`,
  and `rationale`.  Locally, responses are validated: unknown goal/claim IDs,
  invalid levels, or `covered`/`partial` with empty claim IDs mark the row
  `unknown` and add a semantic warning (fail open, deterministic claims
  unaffected).  Semantic availability truthfully reflects whether usable
  (non-unknown) rows exist.  If the provider is unavailable or returns the
  wrong number of batches, the audit marks rows `unknown` and keeps
  deterministic claims.  Non-unknown rows with omitted
  `unique_contribution`/`rationale` receive conservative local fills so
  downstream fields stay useful; soft omissions never hard-block.
- Coverage gaps are explicit handoff assets, not blockers; only
  source/artifact/integrity errors hard-block.

## Persistence (optional, append-only, journal-recoverable)

- When `memory_store`/`graph`/`journal_path` are supplied, a stable
  `claims-<ledger_id>` graph node records one `article.claim` event per claim,
  and the memory store receives immutable `FactRecord`s, append-only
  `RunMemoryRecord` copies of the FULL `ClaimCard` payload, and an append-only
  `ArticleCompletionAudit` record keyed by `audit_id` (so alternative semantic
  audits can coexist).  Stored records are fully reconstructable on reopen.
  `completed` is written to the journal only after both stores finish; a
  mid-write failure (including after one claim event, one fact/claim record,
  or the audit record) raises `ClaimLedgerError` and the retry replays missing
  records/events without duplicates.  Conflicting existing claim events,
  facts, or full memory record payloads (claims or audits) fail closed with
  `ClaimIntegrityError`.

## Stage 9 handoff

The claim/fact pairs and completion audit feed figure-first planning: verified
claims become figure contracts and section story roles, while `gap`/`unknown`
audit rows become explicit "missing work" handoff assets for the remaining
research stages.
