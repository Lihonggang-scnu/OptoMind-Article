# Article Architecture Planner - Stage 9 Design Note

Date: 2026-08-15
Module: `optomind_optics/harness/article_architecture.py`
Prompt: `prompts/optical_harness/Article Architecture Planner.txt`

## Purpose

Stage 9 turns the Stage 8 Claim Ledger (claims, immutable facts, completion
audit) plus caller-supplied trusted artifact descriptors into multiple
auditable whole-article story candidates with ordered section contracts,
rich `FigureContract`s, claim/fact assignments, and explicit gaps/omissions.

## Identity and provenance

- `architecture_id` is content-sensitive: it hashes the full canonical story
  content, full trusted artifact descriptor content, structured missing-work
  handoffs, and the plan/ledger identity.  Two different architecture outputs
  for the same upstream inputs get different IDs; exact replays stay
  identical.  The computation is centralized and public as
  `compute_architecture_id(plan_id, ledger_id, artifact_manifest,
  missing_work_handoffs, stories)` and is used by Stage 9 itself, so
  downstream stages can recompute and verify it.
- `ArticleArchitectureResult` carries backward-compatible provenance fields:
  `source_plan_id`, `source_ledger_id`, and the full caller-asserted
  `artifact_inventory` (the same `ArtifactDescriptor` list used to compute
  the ID).  Old serialized results without these fields remain loadable via
  defaults but are not trusted by later stages without identity fields.
- Memory identities are namespaced by architecture: `story-<architecture_id>-<story_id>`
  and `figure-<architecture_id>-<figure_id>`, so records never collide across
  architectures/runs.
- Plan-ledger identity is validated before planning.  `ClaimLedgerResult`
  carries a backward-compatible `source_plan_id` set by Stage 8; a ledger from
  another plan, a ledger carrying `validation_errors`, or claims whose
  `hypothesis_id`/statement do not match the supplied plan are hard integrity
  blockers.  Old serialized ledgers without `source_plan_id` remain loadable
  (`None` skips the plan match check, while claim/statement checks still run).

## Local-form / model-fill boundary

- Inputs: `ArticleDirectorPlan`, `ClaimLedgerResult`, and an artifact manifest
  of `ArtifactDescriptor`s (`artifact_id`, path, declared `fields`,
  `artifact_type`, `media_type`, `content_summary`, `field_descriptions`,
  `sha256`, source experiment/observation IDs).
- Trust boundary: descriptors are caller-asserted semantic metadata.  This
  module does not open or verify the underlying files or hashes; it only
  plans from the declared fields and summaries.  Nothing in the descriptor or
  payload contains chart values or numeric data.  When a `sha256` is
  supplied it is validated as a 64-character lowercase hex digest; consumers
  that require verified values can require and match it.
- Qwen (`qwen3.7-flash`, via `QwenArticleArchitecturePlanner`) is
  organization-only and receives one bounded payload: question, charter
  scope, positive/limitation claim tables, artifact semantic summaries with
  allowed fields, and structured `missing_work_handoffs`.  The model proposes
  story shape, thesis wording, section purposes/transitions, figure intent,
  claim placement, explicit omissions, and recommendation rationale.
  Local code owns schemas, IDs, allowlists, fixed fields, statuses,
  validation, usage, and persistence.
- The concrete adapter is locked to `qwen3.7-flash`, reads the prompt file,
  and requires a top-level JSON object `{"stories": [...]}` (matching the
  `json_object` response format).  Provider results are wrapped in an
  `ArchitectureProviderResult` envelope carrying a truthful `provider_model`
  and usage telemetry; the envelope's default `provider_model` is `unknown`,
  so an injected fake provider is never labeled `qwen3.7-flash` unless it
  declares that model, and real Qwen usage/cost/token fields are preserved.
  `max_tokens` is configurable (default 12000, sized for three full
  candidates) and can be reduced by the caller.
- The provider contract allows 2-5 story candidates.  After local validation,
  the result keeps at most five candidates deterministically; a larger
  response is truncated with a warning.

## Figure-first contracts

- Quantitative figures/tables require per-artifact `ArtifactFieldBinding`s
  (`artifact_id` + `selected_fields`); each binding is validated independently
  against that artifact's declared `fields`.  Figures carry explicit
  `claim_bindings` (`{claim_id, role}`), just like sections.  For every
  positive claim binding, the full claim -> fact -> artifact chain is
  persisted: the matching Stage 8 `fact_id` must be listed in the figure's
  `fact_ids` (no implicit lookup), and the selected artifacts must be a
  non-empty subset of the union of artifacts authorized by the bound
  claims/facts; a trusted but unrelated manifest artifact cannot be attached.
  `source_mode` is set locally to `trusted_artifact`.
- Conceptual/workflow/mechanism figures are marked `conceptual`.  Without an
  artifact binding their `source_mode` is `synthesized_claims`.  If they bind
  a real source artifact, that artifact is checked against the bound claim or
  fact and `source_mode` is `trusted_artifact`; unrelated artifacts are
  rejected for every figure kind.
- Refuted/withdrawn/draft claims may appear only as `limitation` or
  `counterevidence` figure roles, including negative-result or failure
  figures whose artifact bindings use the trusted counter-evidence artifacts
  (the claim's own source artifacts when no FactRecord exists).  A limitation
  figure cannot turn a negative claim into positive support, and
  `ClaimAssignment.role` reflects the union of section/figure binding roles.
- A fact must belong to a figure claim, and the figure's fact IDs must
  correspond to claims bound in the same figure.  Unknown or cross-wired
  claim/fact/artifact IDs are hard integrity errors (an unknown artifact is a
  controlled error, never a `KeyError`).

## Claim placement, omissions, and distinctness

- Every claim binding carries an explicit `ClaimPlacement` role: positive,
  limitation, or counterevidence.  Positive prose may use only positive
  writable claims; refuted/withdrawn/draft claims are usable only in
  limitation/counterevidence roles.  A negative claim used as positive
  support is a hard error.
- A claim that is both assigned and omitted is normalized with a clear
  warning (assignment wins).  Empty stories/sections/figures are schema
  rejected; essential structure is required, while weaker quality issues
  remain warnings.
- Distinctness compares story shape, ordered section purposes, per-section
  claim roles, figure roles, and claim distribution (not only thesis text);
  structurally duplicate candidates are warned.
- Invented measurements: measurement-like numeric expressions
  (decimals/scientific notation, percentages, comparison thresholds, and
  number-plus-unit expressions) in model-authored figure intents, captions,
  panel intents, key messages, thesis, story shape, rationale, strengths,
  risks, and exclusions must appear verbatim in the bound verified claim/fact
  text; otherwise the candidate is hard-rejected as fabricated numeric
  content.  Plain structural integers (`3 routes`, `stage 2`, `Figure 1`,
  `2D`) never hard-block; at most they produce a warning.  This is
  token-based, uses general scientific numeric patterns, and is deliberately
  bounded.

## Structured gaps and fail-open/fail-closed policy

- Completion-audit `gap`/`unknown` rows are preserved as structured
  `MissingWorkHandoff` models (`goal_id`, `goal_label`, `kind`, `coverage`,
  `claim_ids`, `unique_contribution`, `expected_value_of_more_work`,
  `stop_reason`, `rationale`) in both the provider payload and the result.
  `partial` coverage rows are treated as missing-work handoffs too.
- Input integrity is enforced before planning: duplicate claim IDs, duplicate
  fact IDs, duplicate/ambiguous claim->fact ownership, empty or duplicate
  artifact fields, and `field_descriptions` keys outside declared fields all
  fail closed with controlled validation messages.
- Fail-open: provider unavailable or malformed/partial semantic candidates
  yield an honest `unavailable`/`partial` result with warnings; valid
  candidates from the same response are retained and malformed ones are
  skipped with per-candidate warnings.  Stage 8 facts are never deleted or
  fabricated.
- Fail-closed: plan/ledger identity mismatch, ledger validation errors,
  unknown/cross-wired IDs, unrelated artifacts on quantitative figures,
  negative claims used as positive support, invented numeric content, and
  persistence conflicts produce validation errors with no stories and no
  persistence.

## Persistence (optional, append-only, journal-recoverable)

- A stable `architecture-<id>` graph node records one `article.figure` event
  per figure (completeness-aware replay; conflicting events fail closed).
- The memory store receives full architecture/story/figure `RunMemoryRecord`
  copies (full model payloads) with architecture-namespaced IDs.  `completed`
  is written to the journal only after both stores finish; a mid-write
  failure raises `ArticleArchitectureError` and retries replay missing
  events/records without duplicates.  Conflicting full payloads fail closed
  with `ArticleArchitectureIntegrityError`.

## Stage 10 handoff

The selected story candidate's section contracts and figure contracts feed
Stage 10 section writing: each section binds claim/fact tokens and figure
contracts, and each quantitative figure is rendered from its trusted artifact
descriptors and declared fields by the existing publication layer.
