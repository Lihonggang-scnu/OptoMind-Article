# Article Delivery (Stage 12D)

Stage 12D is the Article-specific publication/delivery adapter.  It consumes
the accepted Stage 12C presentation package plus the complete upstream chain
(plan, ledger, architecture, review, manuscript, reproducibility, selected
story, trusted value records) and invokes the existing read-only
`optomind_research.runtime.latex_publication_renderer.build_latex_publication`
through dependency injection.  Stage 12D itself makes no Qwen/model/network
call and never rewrites scientific prose or front matter.

## Inputs

- `ArticleDirectorPlan`, `ClaimLedgerResult`, `ArticleArchitectureResult`,
  `ArticleReviewResult`, `ArticleManuscriptPackage`,
  `ArticleReproducibilityPackage`, `ArticlePresentationPackage`,
  `selected_story_id`, `TrustedValueRecord`s.
- `PublicationMetadata`: authors (name/affiliations/email/ORCID/corresponding),
  date, acknowledgements, draft flag.  Title/abstract/keywords are always
  authoritative from the Stage 12C front matter and cannot be replaced.
- Optional explicitly labelled `AdditionalUsageRow`s for stages whose
  telemetry is unavailable upstream (for example `director_plan`, since the
  plan model does not carry usage).
- Optional injected `renderer` callable (default
  `build_latex_publication`), `compile_pdf` flag, and optional `output_dir`.

Models and mappings are both accepted and normalized locally.

## Validation before renderer invocation

`build_article_delivery` first calls `validate_presentation_package` with the
complete upstream set (the same deterministic Stage 11/12A/12B chain used by
Stage 12C).  A failure, any upstream status `blocked`, missing front matter,
an incomplete bibliography, an unrepresentable visual, a body/citation
invariant violation, or malformed costs returns a truthful `blocked` package
and the renderer is never invoked.  Partial optional-object validation keeps
the existing ID-check behavior.

## Renderer integration

The adapter builds renderer inputs locally:

- `renderer_inputs/source_markdown.md` - accepted body sections in story
  order with the exact Stage 12C citation markers and table blocks; figure
  blocks are omitted because the renderer injects its own verified figures.
- `renderer_inputs/metadata.json` - title/abstract/keywords from front matter
  plus author/delivery metadata.
- `renderer_inputs/blueprint.json` - section identity map used for figure
  placement.
- `renderer_inputs/visual_plan.json` - one entry per representable figure
  group.  Each `RenderedVisual` maps to exactly one plan entry with one safe
  PNG/PDF `local_path`; panels are never flattened into unrelated top-level
  figures.
- `renderer_inputs/content_package.json` - the renderer content package.
- `renderer_inputs/bibliography_seed.json` and the renderer's
  `BIBLIOGRAPHY_METADATA.json` - Stage 12C reference metadata keyed by
  reference alias, so the renderer produces `references.bib` without network
  enrichment (`enrich_crossref=False`).

Panel bytes are materialized under safe relative paths and verified against
their SHA256 before the renderer sees them.  Tables stay tables: their
markdown block remains in the source markdown and is never rasterized.

### Visual conversion and composition

- Single supported raster (PNG/JPEG) or PDF panel: the original panel bytes
  are kept as a direct renderer asset (`composition.mode="direct"`) with the
  original SHA256.
- Single SVG panel: converted deterministically to a publication PNG via
  local PyMuPDF (`composition.mode="converted"`); a locally computed SHA256
  covers the generated asset.
- Multi-panel figures (any mix of PNG/JPEG/PDF/SVG): each original panel is
  decoded in declared order, normalized to a white background, bounded
  (payload <= 100 MiB, rasterized side <= 4096 px, per-panel pixel count <=
  40M), and composed onto one deterministic white PNG grid (1 row for <= 3
  panels, otherwise a balanced grid) with deterministic `(a)`, `(b)`, ...
  labels.  The 40M composite-pixel bound is enforced by pre-scaling panels
  before the canvas is allocated; aspect ratio is preserved, nothing is
  cropped, and the caption is never burned into the image.
- Pre-decode safety: SVG/PDF page geometry is inspected before any pixmap is
  allocated (rasterized 2x dimensions are checked for finiteness, positivity,
  side, and pixel count), PNG/JPEG header dimensions are checked before pixel
  data is loaded, and Pillow decompression-bomb warnings/errors fail closed
  without mutating the process-global `Image.MAX_IMAGE_PIXELS` threshold
  (concurrent delivery calls never race).  PyMuPDF documents are closed on
  success and on every exception path.
- Aggregate memory: before a decoded panel is retained for composition it is
  deterministically shrunk to its per-panel share of the composite pixel
  budget (`40M / panel_count`), so a 100-panel figure never holds 100 full
  `4096x4096` RGB buffers; `_compose_grid` then enforces the exact cell-grid
  bound.  Superseded image buffers are closed as soon as they are replaced or
  consumed.
- Original panels remain auditable: every `DeliveryPanelRecord` preserves
  the original label, path, media type, encoding, byte count, and SHA256 in
  declared order, and `composition["original_panel_hashes"]` mirrors them.
  Generated assets get their own `renderer_asset_path` /
  `renderer_media_type` / `renderer_bytes` / `renderer_sha256` fields and are
  recorded in the artifact inventory with exact hashes; all of this is
  covered by the content-addressed package ID.
- Dependencies are optional local imports: Pillow for raster decode and
  composition, PyMuPDF (`fitz`) for SVG/PDF rendering.  Missing
  dependencies, invalid SVG/PDF/raster input, empty panel sets, panel counts
  above the explicit bound, payload/dimension/pixel violations, and
  decompression-bomb conditions fail closed as a
  `renderer_representation_limit` blocker before the renderer is invoked.
  No model, network, or image synthesis is used.

After the renderer call the adapter independently verifies declared and
required outputs (`main.tex`, `arxiv-source.zip`, and `main.pdf` when
compilation is enabled), path containment under the renderer output
directory, file existence, and SHA256 hashes.  The renderer report is
digested with absolute staging paths normalized away and is included in the
package identity; the report alone is never trusted.

## Statuses

- `blocked` - upstream identity/integrity failure, missing front matter,
  incomplete bibliography, or an unrepresentable visual; renderer not
  invoked.
- `failed` - renderer exception, non-success renderer report, missing/corrupt
  or unsafe artifacts, or missing PDF after a compile-enabled run.
- `compiled_awaiting_metadata` - renderer succeeded and artifacts verified,
  but author metadata is incomplete, the draft flag is set, or PDF
  compilation was disabled.
- `submission_ready` - renderer succeeded, all artifacts verified, complete
  author and reference metadata, no draft flag, no hard blockers.

Partial/source artifacts are preserved only in the controlled
`.delivery_audit` staging area; a failed/blocked run never produces a
half-successful final `latex/` bundle.

## Cost ledger

Known Qwen usage is aggregated without double counting:

- `architecture` - `ArticleArchitectureResult.usage`
- `writing_section_<section_id>` - one row per Stage 10 original section
  draft (`review.sections[].original_section_draft.usage`)
- `review` - Stage 11 review/revision aggregate `ArticleReviewResult.usage`
- `presentation` - Stage 12C `ArticlePresentationPackage.usage`
- `reproducibility` - Stage 12B usage when non-empty
- caller rows for any unavailable stage (unique labels only)

Per-stage rows and totals carry input/output tokens, call count, attempts, and
CNY.  Missing local costs are estimated from token telemetry via the existing
local cost ledger and marked `cost_estimated_locally`.  Negative,
non-finite, duplicate-label, or malformed costs are rejected (fail closed).
Coverage gaps (for example `director_plan`, which the plan model does not
carry) are reported in `coverage_missing`; total cost is never claimed
complete when telemetry is missing.

## Outputs

Stable outputs under `output_dir`:

- `ARTICLE_DELIVERY_PACKAGE.json` - content-addressed delivery package.
- `ARTICLE_PUBLICATION_AUDIT.json` - upstream IDs/statuses, exact body SHA256,
  citation/reference/figure/table counts, renderer status and report digest,
  tool availability, cost coverage, and every delivered artifact path/bytes/
  SHA256.
- `ARTICLE_COST_LEDGER.json`
- `ARTICLE_SUBMISSION_CHECKLIST.md`
- `ARTICLE_RENDERER_INPUT_MANIFEST.json`
- `renderer_inputs/` - the exact renderer inputs.
- `latex/` - verified renderer outputs (only for non-failed/non-blocked runs).

Writing is atomic per file with full preflight: every artifact and core file
is checked for conflicts before anything is written.  Exact replay is
idempotent; conflicting existing content is rejected without deleting
anything.

## Identity and validation

`compute_delivery_package_id` content-addresses all scientific identities,
status, renderer identity/status/report digest, compile flag, publication
metadata, references, visuals, artifacts, blockers, findings, warnings,
errors, cost ledger, body SHA256, and counts.  `validate_delivery_package`
is a public deterministic validator (no network/model calls) that rechecks
the package ID, upstream IDs when supplied, the full upstream chain when the
complete set is supplied, status derivation, cost ledger integrity,
relationship counts, reference uniqueness, artifact path safety, and (when an
`output_dir` is supplied) on-disk hashes.  Persisted staging artifacts are
required to exist and match; final-bundle artifacts may be pending writes
before the first publication and are verified by the writer from staging.

## Limitations

- Real Pandoc/LaTeX compilation and real TMM replay are not exercised in this
  repository; production `build_latex_publication` requires Pandoc (and
  latexmk when `compile_pdf=True`) and its PDF validation tooling.  Missing
  tooling surfaces as `failed` with truthful `tool_availability` and is never
  `submission_ready`.
- Converted/composite PNG bytes are deterministic within one environment but
  depend on the local MuPDF/Pillow versions, so byte-identical replay across
  machines with different versions is not guaranteed; direct raster/PDF
  assets remain the original bytes.
- Cost totals are complete only when every stage's telemetry is present;
  otherwise coverage gaps are reported honestly.
