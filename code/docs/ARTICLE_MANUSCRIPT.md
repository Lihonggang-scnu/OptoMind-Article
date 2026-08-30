# Article Manuscript Body - Stage 12A Design Note

Date: 2026-08-16
Module: `optomind_optics/harness/article_manuscript.py`

## Purpose

Stage 12A is a deterministic manuscript-body assembly boundary.  It consumes
an accepted Stage 11 `ArticleReviewResult` plus the upstream
plan/ledger/architecture/story identities and the same `TrustedValueRecord`s
used by Stages 10/11, and produces a content-addressed body package with a
paragraph-level source map.  It performs no model, network, or tool calls.

This is not the final publication renderer: no title, abstract, citations,
figures, or references are invented here.  Later stages enrich the body.

## Identity and revalidation

- Public Stage 11 helpers `compute_review_id` and `compute_review_result_id`
  are now used by `build_article_review` itself and are exposed for
  downstream verification.
- The public `validate_review_result` helper rechecks a Stage 11 result
  without invoking reviewers: plan/ledger/architecture/story IDs, exact story
  section order vs result section order, `review_id`/`result_id`
  recomputation, derivability of original/final source-ledger aggregates,
  aggregate audit/scientific/expression findings and hard blockers, section
  status consistency, the exact aggregate review status recomputed from
  ordered section statuses (no sections/all blocked -> blocked; mixed
  blocked -> partial; otherwise any ready_with_findings -> ready_with_findings;
  else ready), wrapper section/story/architecture identity vs the section
  drafts, and (for `ready`/`ready_with_findings` sections) reconstruction of
  the final section with the Stage 10 assembler plus full-field comparison of
  authoritative paragraph/prose/source data.  Every non-blocked section must
  also match the recomputed deterministic audit findings exactly (normally
  empty); a forged or missing audit finding is an integrity error.  Hard
  audit failures stay fail-closed, while soft reviewer findings remain
  fail-open.
- Blocked sections are not trusted on their stored handoff alone: the same
  deterministic audit (and reconstruction comparison when the stored draft is
  publishable) is re-run, the expected audit findings/hard blockers are
  derived and compared, the blocked original and final drafts must be
  identical, soft findings/revisions must be empty, and cross-section
  paragraph identity is checked across blocked and accepted sections.
- Assembly reuses Stage 11 internals; upstream identity/provenance/
  reconstruction failure blocks assembly before any content is emitted.

## Assembly rules

- Only sections with Stage 11 status `ready` or `ready_with_findings` are
  assembled, in selected-story order, preserving final paragraph order and
  exact final rendered prose.
- Blocked section IDs and exact hard blockers are recorded as a
  `BlockedSectionHandoff`; a partial review produces a useful partial body.
- Ordinary unresolved soft findings are retained and attached only to their
  target paragraph/section (`finding_ids`), never treated as blockers.
- Body Markdown uses local sanitized headings that preserve human-readable
  text, capitalization, Unicode scientific notation, and cross-disciplinary
  names; only Markdown/control injection (newlines, control characters, and
  leading heading-marker abuse) is neutralized, with a safe local fallback.
  Paragraph text remains byte-identical and internal hash aliases never
  appear in reader-facing prose.

## Source map and IDs

- `ParagraphManuscriptSource` records paragraph ID, section ID, exact
  rendered prose, Claim/Fact/Artifact/Figure/Value token IDs, scope/scopes/
  limits/roles, inference metadata, and unresolved advisory finding IDs.
- `body_id` is content-addressed over all scientific body content, order,
  source map, findings, blocked handoff, and upstream identities; the outer
  `package_id` additionally covers the rendered Markdown.  Reordering or
  tampering changes the identity or fails validation.

## Atomic fixed-name writer

`write_manuscript_package` writes `ARTICLE_MANUSCRIPT_BODY.md`,
`ARTICLE_MANUSCRIPT_PACKAGE.json`, and `ARTICLE_SOURCE_MAP.json` atomically.
It never silently overwrites conflicting content under the same package
identity: existing files are compared (JSON structurally, Markdown
byte-for-byte) and a conflict raises `ArticleManuscriptIntegrityError`.
Exact replay is idempotent; no directory deletion occurs.

## Fail-open / fail-closed

- Fail-open: soft findings and blocked sibling sections remain deliverable
  records; the package reports them without blocking the valid body.
- Fail-closed: unknown IDs, mismatched provenance, invalid Stage 11 identity,
  source-ledger drift, unsafe numeric/value content, reconstruction failure,
  and conflicting persisted content block assembly or writing.
