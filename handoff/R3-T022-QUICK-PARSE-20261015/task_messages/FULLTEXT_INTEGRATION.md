# Full-paper materials alongside S2 (T022, provisional)

## Intended chain

Native question → generated task sheet and queries → S2 passages **and independent full-paper discovery/acquisition** → native XML or local Docling PDF snapshots → original text/table/formula blocks with source locators → candidate routes → prepared VeriTMM inputs.

S2 title/abstract/body snippets remain useful lightweight clues. They are not a substitute for every full paper. A full-paper source can be reached by its DOI, title or public provider location independently of a successful S2 search. Author/year/DOI completeness is not a gate, but actual accessible material and its source must be retained. Failure to obtain a paper remains explicit; it must not be described as parsed full text.

Model-knowledge routes are also permitted when external sources are absent. External, model-knowledge and hybrid bases are clearly marked. A knowledge-based route still explains its physical reasoning, its bounded structure and a baseline-referenced expectation. No invented citation, numeric simulation result or calibrated confidence is required or allowed.

## Reuse rather than rebuild

The isolated worker loads an explicit local Review checkout pinned to:

`d2deb4dbe18b23837d949822ad731afbaba9414b`

Read that checkout's `docs/verification/local-docling-batch-20261013/README.md` and `docs/local_materials.md`. The wrapper does not replace the mature parser or turn the older focused vendored Review into the material runtime. Its separate Python process prevents import collisions.

- Prefer native structured XML where available.
- For selected PDFs, reuse `scripts/upgrade3/parse_docling_batch.py`, the checked-in basic three-worker profile, cached weights and successful receipts.
- Basic profile: workers 3, layout/table/OCR batches 4, accelerator threads 4, safe parser, one native parser thread, child rotation after 20 documents, whole-card GPU ceiling 8192 MiB.
- Native process recovery and cache validation remain owned by Review. Do not bypass deadline, memory or operator stops.
- Import Docling native output as ordinary snapshots. Keep PDF/native JSON, body, tables, captions and page/bbox region index local.
- Selected-paper CodeFormulaV2 enhancement uses the existing local cached profile, single process/batch32; do not run formula enhancement concurrently with the basic three-worker pool. Preserve the ordinary snapshot unchanged.
- No Mistral, Mathpix, paid regional vision fallback or newly paid extraction service. Existing local dependencies/model caches are reused; missing setup is reported, not silently downloaded or replaced.

PDF input parsing is distinct from writing a report PDF. This test requires the former and does not need the latter.

## Bounded first live test

Use at most five selected papers in this first integrated run. This is a test-size limit, not a fixed production policy or relevance guarantee. Compare with the prior S2-only evidence; retain useful methods from different applications or opposite spectral functions, while labeling them as transferable leads rather than matching devices. Prefer actual body/table/method material over repeated background text when forming the route input view.

Full sources are retained separately from the prompt view. Material selection uses explicit source-bound block locators; it does not rewrite or silently truncate scientific table/formula content. If the full set is too large, select whole relevant blocks and preserve a selection manifest. Never call skipped blocks read. Captions alone do not establish that a figure's contents were interpreted.

See LOCAL_TEST_HANDOFF.md for the current exact budget, execution bounds, commands, output requirements and stopping condition. Offline fixture tests establish software interoperability; local Docling/model/S2 runs and scientific quality must be reported separately.
