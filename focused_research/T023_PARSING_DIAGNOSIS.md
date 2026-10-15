# T023 parsing/evidence bridge: offline diagnosis and bounded repair

## Evidence and verification boundary

Source: Article result commit `eab06932b96fe73563044f0175a43865580be123`, directory `handoff/R3-T022-QUICK-PARSE-20261015`. Inspected DETAILED_REPORT, CORE_BRIDGE_EXCLUSION, FULLTEXT_RESULT, EVIDENCE_VIEW_PREFLIGHT, selected evidence, BATCH_RESULT and WORKER_HISTORY. Runtime Review remains pinned to `d2deb4dbe18b23837d949822ad731afbaba9414b` and was not edited. No paid models, providers, PDF parsing, simulation, downloads of weights or environment tuning were performed.

The original copyrighted body fixtures stay outside the code checkout. Regression fixtures are synthetic.

## Prompt projection

`prompt_evidence_view` v2 separates source and snapshot context from whole evidence items. Readers resolve `source_ref` in `sources` and `snapshot_ref` in `snapshots`. Scientific text, evidence order/IDs, table structures/cells, formula fidelity, cross references and usage limits remain. Snapshot asset gaps with identical wording are encoded as `asset_gap_groups`, each containing the complete `gap` and its `asset_ids`; ordinary `known_gaps` remain explicit. The original archive and selected-evidence format are unchanged.

Only prompt presentation changes: repeated source documents, local snapshot paths, parser-run metadata and duplicate Docling geometry are omitted; selected source hashes, original PDF/wire hashes, page/bbox/region/XML handles and multipage `locations` remain. Non-list formula hash bindings remain intact. Differing restrictions are content-addressed separately rather than merged by a shared paper ID. Budget overflow still fails rather than truncating text.

Actual T022 offline replay: **171,222 → 51,315 characters (70.0% reduction)** using `len(json.dumps(view, ensure_ascii=False))`. All **12,487 original text characters**, **12 evidence IDs and their order** are unchanged. The view has 7 source records and 4 snapshot contexts. This is a character measurement, not a token/cost forecast or a new model run.

Original dominant repeated fields: approximately 64k source mappings, 39k reading policy and 30k known gaps. The former copied the entire manifest source-document inventory into every block; the latter appeared both directly and inside reading policy.

## CORE eligibility and identity

The former bridge allowlist omitted `core_text_jats`. Review `material_acquisition.py` resolves candidate identities as follows:
- `consider()` around lines 1040–1043 appends conflicts only for **rejected** search candidates.
- Lines 1136–1139 retain this aggregate history and can label the final provisional identity `conflict`.
- CORE `core_fulltext` route construction around lines 1355–1362 independently requires `match_identity(identity, row)['verified']`.
- Synthetic CORE projection is explicitly `input_attributed` in `document_snapshot.py`, never independently verified.

T022's aggregate conflicts name OpenAlex/Crossref/arXiv; CORE audit is matched and the selected CORE route is usable. Therefore a blanket rejection of aggregate `conflict` would incorrectly equate rejected candidates with a conflict in selected material.

The bridge now accepts the parser while rejecting policy-ineligible scopes and explicit selected-snapshot identity conflicts. A narrow aggregate-history exception requires the pinned native CORE route, matching snapshot ID, CORE matched audit and usable CORE attempt, explicit `input_attributed` snapshot identity with empty conflicts, and only rejected other-provider conflict prefixes. Unbound/CORE/unknown conflicts stay blocked with an explicit reason. Original identity and limitation records are never rewritten.

Conditional offline replay of the actual acquisition object and published CORE reading policy returns eligible, **assuming** a native snapshot `identity_check` with status `input_attributed` and `conflicts=[]`. The CORE native manifest is not included in the published handoff; a proxy was used for this policy test. Adding a selected-snapshot DOI conflict correctly returns `selected_source_identity_conflict`. This is **not** a new PreparedSnapshotProvider verification of the remote original. Actual native acceptance remains for the offline local re-export below.

## Offline re-export

`fulltext_stage.py --reexport-result OLD/FULLTEXT_RESULT.json --output-root NEW --review-root PINNED_REVIEW --review-python PYTHON`

This separate worker branch imports only the native snapshot loader, never instantiates MaterialAcquirer/providers/Docling/formula enhancement. It binds the old result SHA256 into the ordinary request identity, validates the fixed Review commit, limits the record inventory to five, and re-runs PreparedSnapshotProvider.load(). Final Docling acquisition and existing export snapshot bindings supersede intermediate fitz snapshots. Existing export `verified` flags are not trusted. Selected source identity conflicts or unreadable/mismatched snapshots produce explicit blocked rows and partial/empty status. Loaded native canonical paper identity is additionally bound to the row and acquisition identity (DOI URI aliases normalize; explicit DOI contradictions fail). New exports are written to a new/empty independent output directory; source results and snapshots are read-only. Wrapper preflight rejects source/snapshot ancestor, equal and descendant paths before creating the request. Direct worker calls also reject existing artifacts; only current-request-bound wrapper metadata/logs are allowed. Mixed discovery, parsing, enrichment or same-directory re-export is rejected.

Synthetic regression tests cover native loader execution, final Docling selection, CORE identity preservation, stale source hashes, mixed mode rejection, mismatched bindings, and partial completion without acquisition calls.

## Docling latency: diagnosis only

Actual T022: workers configured=3, nonempty shards=2, 12 and 3 pages, total 1309.203 seconds, GPU peak 3984 MiB, no native crash/restart. Two worker histories start at 1792060822.7405658 and finish 1.675 seconds apart. Per-document elapsed is 846.125 and 843.766 seconds; worker wall is about 1308.540 and 1306.865 seconds. Thus roughly 462–463 seconds per worker falls outside the per-document timer. Imports, configuration and converter construction precede that timer; current evidence cannot isolate their costs, conversion-internal lazy initialization, shutdown or disk contention.

F-drive model-cache paths are present, but I/O as the root cause is unproven. There is real parallel execution. Table structure is still enabled in accurate mode; OCR, formulas, code, picture and chart enrichment are disabled.

Review `parse_docling_batch.py:699` samples child wall time only after all children finish, producing identical 1309.203 child summaries. Suggested future instrumentation: record first observed child exit, imports/configuration/converter initialization, first conversion, export and shutdown separately; preserve original history. Test two fake children ending at different times to ensure the short worker excludes waiting for the long worker. Do not tune workers, GPU limits or safe-parser settings on this evidence alone. Review production/environment remain unchanged.

## Tests

Targeted material/bridge tests after identity/output hardening: 36 passed. A focused suite run before the final small source-hash/inventory hardening: 246 passed, 6 subtests passed. The final targeted rerun passed again; the parent owns the final combined-suite verification with concurrent route changes.

Final integrated verification by parent (2026-10-15): **253 passed, 6 subtests passed**, 7.62 seconds, sockets disabled; `git diff --check` clean. Independent review rechecked output non-overwrite, native source identity binding and runtime checkout provenance. Real local snapshot re-export and new-model scientific quality remain unverified.
