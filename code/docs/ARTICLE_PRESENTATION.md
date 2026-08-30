# Article Presentation - Stage 12C Design Note

Date: 2026-08-16
Module: `optomind_optics/harness/article_presentation.py`
Prompts: `Article Citation Placer.txt`, `Article Front Matter Writer.txt`

## Purpose

Stage 12C builds the reader-facing presentation from accepted Stages 9-12B
outputs: exact literature citation restoration, trusted Figure/Table
rendering with deterministic placement, and provenance-bound
title/abstract/keywords.  The deterministic core makes no model or network
call; Qwen (`qwen3.7-flash`) is advisory only for citation placement and
front-matter form filling, with conservative deterministic fallbacks.
Production visual rendering reuses the existing local figure processor for
trusted raster/PDF assets and renders numeric assets only from declared
fields.

## Inputs and revalidation

- `ArticleDirectorPlan`, `ClaimLedgerResult`, `ArticleArchitectureResult`,
  `ArticleReviewResult`, `ArticleManuscriptPackage`,
  `ArticleReproducibilityPackage`, selected story, the same
  `TrustedValueRecord`s as prior stages, `MethodEvidence` records, and trusted
  artifact roots.
- Public Stage 11/12A validators revalidate the review and manuscript; the
  reproducibility package identity is recomputed with
  `compute_reproducibility_package_id`, its upstream IDs must match, and a
  blocked reproducibility package blocks presentation.  Used quantitative
  artifacts are verified against the trusted roots, their descriptor SHA-256,
  and the accepted Stage 12B lineage at render time.

## Citations

- The only citation chain is: final paragraph -> claim_id ->
  `ClaimCard.metadata.hypothesis_id` -> `ArticleDirectorPlan.hypotheses[]`
  `.evidence_ids` -> `MethodEvidence` -> `paper_id`.  Observation IDs
  (`ClaimCard.evidence_ids`) are never treated as paper IDs.
- `ArticleDirectorPlan` now carries a deterministic `evidence_identity`
  manifest (evidence_id, paper_id, DOI/title/year, source route, content
  depth, allowed use, and SHA-256 of the exact evidence text), and the plan
  identity binds that manifest.  Stage 12C compares every cited
  `MethodEvidence` against the manifest exactly; a legacy plan without bound
  evidence identity blocks literature citation with a clear diagnostic, and
  swapping paper/text under the same evidence_id is rejected.
- `discovery` evidence is not citeable support and is excluded with a
  warning.  Support semantics (`background`/`method_guidance`/`direct_fact`)
  and content depth are recorded.  Missing expected evidence, unknown IDs,
  malformed/conflicting DOI or paper identity are source integrity errors.
  Incomplete optional bibliographic metadata (missing DOI/year) is a draft
  finding, not a chain-killing error.
- References deduplicate by DOI when present, otherwise by `paper_id`, while
  retaining all evidence IDs and paragraph/claim/hypothesis bindings.
- Qwen receives semantic aliases (title/year/use), never internal hashes, and
  returns only placement fields.  Local code inserts `[REF:...]` markers;
  markers are inserted by offsets into the original string, so removing them
  reproduces the accepted paragraph byte-for-byte including whitespace and
  newlines.  A malformed/unavailable provider, unknown alias, or invalid
  sentence position rejects that advisory response and uses a conservative
  deterministic end-of-paragraph fallback with a warning (fail-open); unsafe
  content that would survive into the final package is fail-closed.

## Front matter

- The front-matter request sends full accepted paragraph texts (bounded by
  actual manuscript size) plus section/story context, not first-sentence
  fragments.  Every abstract sentence must cite at least one paragraph alias;
  exact numeric text is allowed only when the identical token occurs in at
  least one cited source paragraph.  Unknown aliases, forbidden markers, or
  invented numbers reject the advisory response and fall back deterministically
  (`ready_with_findings`).  The output-token limit is configurable (default
  12000).

## Reader manuscript and references

- The reader manuscript contains the title, abstract, keywords, the unchanged
  cited body with numbered Figure/Table blocks placed after the earliest
  citing paragraph (section-end fallback with a warning), and a References
  section that resolves every semantic `[REF:...]` alias deterministically.
- References deduplicate by normalized DOI (merging different paper IDs while
  retaining every paper/evidence/paragraph/claim/hypothesis binding) and block
  true conflicts (same paper ID with conflicting DOI/title, or one DOI with
  incompatible titles).  Optional caller-supplied authors/venue/URL metadata is
  validated against evidence identities.  `metadata_complete` requires title,
  authors, year, and DOI or venue; incomplete metadata is `ready_with_findings`.

## Figures and tables

- `trusted_artifact` figures/tables verify the real file is inside the
  trusted roots, its SHA-256 matches the descriptor, and the artifact is
  matched in the accepted Stage 12B lineage by exact artifact ID, exact source
  SHA, and a compatible source experiment (path-only matching is forbidden; a
  missing descriptor SHA is a hard blocker) before rendering.
  Raster/PDF assets reuse `prepare_publication_figure` read-only; JSON/CSV/
  TSV numeric assets render only selected declared fields into a deterministic
  SVG plot or Markdown table.  Positive quantitative missing/hash/cross-source
  failures are hard blockers.
- `synthesized_claims`/conceptual contracts produce a deterministic local SVG
  diagram from panel intents and bound Claim statements, explicitly labeled
  synthesized (never measured data); optional render failure is fail-open.
- Figures/tables are inserted deterministically into their target sections as
  separate numbered blocks placed after the earliest citing paragraph, without
  altering accepted scientific paragraph prose; one FigureContract with
  several artifacts remains one numbered figure group with panel assets,
  caption, and callout.  Manifest records retain figure number, panel labels,
  `after_paragraph_id`, and claim/fact/artifact/section/source-mode bindings.
  A table stays a table asset.
- All visual assets are replayably persistable (text or base64 content plus
  encoding/media metadata), asset filenames are sanitized and contained under
  `figures/` or `tables/`, persisted assets are verified against their final
  hashes, and conflicting existing asset bytes are rejected.  SVG/Markdown
  output escapes XML/Markdown injection and wraps long labels.

## Outputs and persistence

- Fixed outputs: `ARTICLE_READER_MANUSCRIPT.md`,
  `ARTICLE_PRESENTATION_PACKAGE.json`, `ARTICLE_CITATION_MAP.json`,
  `ARTICLE_REFERENCES.json`, `ARTICLE_FIGURE_TABLE_MANIFEST.json`, plus
  deterministic `figures/` and `tables/` assets.  Writing is atomic, exact
  replay is idempotent, and conflicting content is refused.
- Status: `ready`/`ready_with_findings`/`blocked`.  Fail-open for ordinary
  wording, model formatting, incomplete optional metadata, and optional
  conceptual visuals; fail-closed for upstream identity, paragraph mutation,
  unknown/cross-wired citations, unsupported exact numbers, used quantitative
  artifact path/hash/source failures, and persistence conflicts.
- The public `validate_reproducibility_package` rechecks a persisted Stage 12B
  package (derived status, upstream IDs, unique identities, a completed replay
  per critical experiment, replay/check counts, lineage consistency, and
  package ID) before rendering; a self-inconsistent package fails closed.
- A late hard failure returns a blocked package that preserves the validated
  upstream identities and all safe citations/front matter/body diagnostics
  accumulated before the failure, including the reader manuscript assembled
  from the safe body/citations/front matter and already completed safe
  visuals (the failed visual is excluded).  Model telemetry (provider model,
  usage, attempts) is retained even when advisory advice is rejected; fakes
  keep their truthful labels and only the concrete adapters report
  `qwen3.7-flash`.
- Marker insertion is offset-based from the end of the string, so multiple
  references at different sentence positions never shift one another; a final
  exact-body invariant removes only the registered markers and asserts
  byte-for-byte equality with the Stage 12A paragraph, that every reader
  marker resolves to an allowed placement/reference, and that each
  `(paragraph_id, reference_alias)` placement is unique with its marker
  appearing exactly once in that paragraph's locally rendered text.  The same
  reference alias may legitimately appear in several paragraphs, so the
  document-wide expected count of a marker equals the number of placements
  carrying it.  This runs before `ready` and in the public
  `validate_presentation_package`.
- Same `paper_id` with conflicting normalized DOI blocks; title/DOI
  normalization is case/whitespace only (scientifically meaningful title
  differences are preserved).  Caller bibliographic metadata may be keyed by
  paper ID or normalized DOI, requires non-empty string authors, and must
  agree with evidence year/DOI/title when supplied; conflicting metadata
  blocks.
- Title and keywords are part of front-matter safety: internal markers,
  control/newline characters, and Markdown structural injection reject the
  advisory response and fall back.  All model/scientific text used in
  Markdown structure (title, keywords, captions, reference authors/title/
  venue/URL) is escaped; accepted body paragraphs remain unchanged.
- `RenderedVisual.sha256` is the full 64-hex digest of its ordered panel
  manifest; `PanelAsset` validates encoding/content exclusivity, base64
  validity, safe relative paths, and 64-hex hashes.  Asset filenames carry a
  short content suffix to avoid sanitization collisions.
- `write_presentation_package` preflights the entire package (recomputed ID,
  derived status, relationships, panel decode/hash, path containment, and all
  existing-file conflicts) before any write, so a conflicting asset never
  leaves newly written core files.  The public `validate_presentation_package`
  provides the same deterministic checks for Stage 12D without network/model
  calls.  `build_article_presentation(..., output_dir=...)` passes the full
  validated upstream model set (plan/ledger/architecture/review/manuscript/
  reproducibility, selected story, value records) to the writer so exact-body
  provenance is revalidated; when no manuscript is supplied, the validator
  explicitly reports that exact Stage 12A body provenance was not revalidated
  instead of implying full provenance coverage.
- The strengthened `validate_reproducibility_package` requires every critical
  replay manifest to be successful with non-empty all-matched checks, valid
  64-hex identity hashes, consistent run IDs, no replay error, unique lineage
  identities, and exact per-experiment artifact lineage; unexpected completed
  replay records conflict and are rejected.
- Stage 12C has not been validated against real Qwen or real TMM replay
  services in this repository; those remain integration items for the final
  environment.
