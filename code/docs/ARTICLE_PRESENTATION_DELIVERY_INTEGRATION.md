# Article Presentation -> Delivery Offline Integration

## Handoff status

Stage 12C (presentation) and Stage 12D (delivery) are implemented and tested,
but the **real persisted chain is not yet delivery-ready**: the accepted
Stage 12B package from probe 018 lacks matched lineage entries for the
quantitative figure artifacts used by the manuscript figures, so Presentation
correctly fails closed with `missing_quantitative_artifact` /
`artifact_lineage_missing` blockers.  The integration entry point reports
this truthfully instead of inventing data.

## Persisted inputs required

`build_article_presentation` needs:

- `plan`, `ledger`, `architecture` (reconstructed from the accepted
  continuation checkpoints `article_continuation_007_tworepair`:
  `01-result_synthesis.json` supplies the derived plan and ledger,
  `02-architecture.json` supplies the architecture);
- `review` (`article_review_probe_014_derived_plan_replay/
  ARTICLE_REVIEW_RESULT.json`);
- `manuscript` (`article_manuscript_probe_015_review014/
  ARTICLE_MANUSCRIPT_PACKAGE.json`);
- `reproducibility` (`article_reproducibility_probe_018_revalidated_fresh_replay/
  ARTICLE_REPRODUCIBILITY_PACKAGE.json`);
- `selected_story_id` (`story-04` from the continuation);
- `value_records` (reconstructed from the source pipeline
  `selective_emitter_006/pipeline` via the continuation's contracted
  inventory + selected-story scoping);
- `method_evidence` (from the source pipeline `method_research.evidence`;
  currently empty in the real run);
- `artifact_roots` (the replay `source_run_dir` values from probe 018).

`build_article_delivery` additionally needs the presentation package and
caller-supplied `publication_metadata` (authors/date/acknowledgements), which
are configuration, not scientific input.

## Offline entry point

```powershell
python code/scripts/run_article_presentation_delivery.py `
  --output-dir .output/article_presentation_delivery
```

The script:

- loads and identity-checks the persisted chain (plan/architecture/review/
  manuscript/reproducibility/story ids must agree; mismatch exits 2);
- runs `build_article_presentation` with no citation/front-matter providers
  (advisory content fail-open) and `build_article_delivery` with
  `compile_pdf=false` and a deterministic fake LaTeX renderer;
- reports `pdflatex`/`latexmk` availability explicitly;
- configures UTF-8 stdout/stderr so JSON summaries print on Windows consoles
  without GBK crashes (JSON contents are unchanged);
- writes `INTEGRATION_SUMMARY.json` plus presentation/delivery outputs;
- exits 3 when identity/provenance blockers block the run, 1 for missing
  persisted inputs, 2 for identity mismatch, 0 on a delivery-ready run.

No network, model, or real TMM call is made.

## Physical -> Article experiment identity mapping

`_verify_quantitative_artifact` now resolves descriptor
`source_experiment_ids` (physical TMM IDs) through Stage 12B
`CriticalExperimentRecord.physical_experiment_ids` +
`experiment_id` before comparing against lineage `experiment_id`
(Article ID).  The mapping must be unambiguous; unknown or ambiguous physical
IDs fail closed with `artifact_lineage_missing`.  Legacy same-ID packages
still use the previous direct comparison.

## Lineage hash semantics

Stage 12B replay lineage stores canonical scientific JSON digests (volatile
fields scrubbed and path references canonicalized), while Stage 9 descriptors
and filesystem checks use raw byte SHA-256.  The presentation adapter now
accepts a lineage entry when either:

- the lineage `source_sha256` equals the raw byte SHA-256 of the on-disk
  artifact (legacy raw-hash packages); or
- the lineage `source_sha256` equals the recomputed canonical scientific JSON
  digest of the same file, reproduced with the Stage 12B replay canonicalizer
  (real replay manifests).

The raw descriptor SHA-256 must still match the file bytes exactly; a changed
raw file, wrong canonical digest, incompatible experiment, unknown/ambiguous
physical mapping, or unmatched lineage entry remains fail-closed.

## Numeric renderer contract

- `SIMULATION_RESULT.json` spectrum behavior is preserved: aligned series over
  `wavelengths_nm`, unequal-length or non-finite columns fail closed.
- `tmm-robustness-report.v1` resolves root scalars under the
  `robustness_report.` semantic prefix only when the schema matches.
- `tmm-objective-report.v1` resolves
  `objective_report.target_attainment.<id>.<attribute>` scalars.
- `optical-design-portfolio.v1` resolves `<candidate_id>.<attribute>`
  against `candidates[*].candidate_id` exactly; duplicate or unknown
  candidate IDs fail closed.
- Scalar selections render as deterministic bar/category SVG; tables require
  at least one real data row.  Missing, ambiguous, non-finite, or partial
  selections fail with `numeric_render_failed` rather than producing empty
  panels.
- Scalar charts use a truthful zero baseline: all-nonnegative values in
  `[0, 1]` use a `[0, 1]` domain, other nonnegative values use `[0, max]`,
  all-negative values use `[min, 0]`, and mixed-sign values keep an explicit
  zero axis.  Visible labels are compact and human-readable (`Mean soft
  score`, `Mean A, 0 deg, P`, `Simplicity score`, ...); exact selected field
  paths and artifact paths are preserved in SVG `title`/`desc`/`metadata`
  only, never as large overlapping chart labels.

## Current real-chain diagnostic

The real run now passes Presentation's hard gate and reaches Delivery:

- the four manuscript figure artifacts resolve through exact raw descriptor
  hashes and canonical lineage digests;
- deterministic numeric rendering resolves the real
  `tmm-robustness-report.v1`, `tmm-objective-report.v1`, and
  `optical-design-portfolio.v1` schemas, including exact
  `candidates[*].candidate_id` selection, producing all four story-04 visuals
  with nonempty data (three scalar bar/plot figures and one candidate
  table);
- no `artifact_lineage_missing`, `missing_quantitative_artifact`, or
  `numeric_render_failed` blockers are emitted;
- offline Delivery reaches `compiled_awaiting_metadata` with the
  deterministic fake LaTeX renderer; no real LaTeX/PDF compilation is
  claimed.

## Next missing input

The existing persisted 018 package is sufficient for the presentation hard
gate after the hash adapter; no new dual-hash lineage package is required.
Remaining work is outside this adapter: real citation/front-matter providers
for advisory content and a real LaTeX/PDF toolchain run for Delivery.
