# Local historical asset audit

Audit date: 2026-10-13. This is a read-only inventory of F:\OptoMind-Review-2, F:\OptoMind-Article-1, and F:\OptoMind-Article-2. The inventory used rg --files -uu while excluding .git, .venv, paths containing cache, .env*, and api_keys. API key and environment file contents were not read. SQLite contents were not read or hashed, and the 585 card bodies, raw responses, and log bodies were not bulk-hashed.

The status labels used here are:

- **Confirmed exists:** the exact local path was found.
- **Not found within search range:** absent from the allowed non-cache inventory only; this does not say anything about excluded caches or cloud storage.
- **Stale reference but actual locatable:** an exact cited path is absent while an identity-matched local path is found.
- **Content not verified:** path or metadata was checked without interpreting the body.

## Result

The historical 585-card source is locally recoverable. INPUT_POOL.jsonl has 585 rows, 585 unique absolute PAPER_READING_CARD.json references, zero duplicate references, and zero missing card files. It resolves to 574 production cards in the 20260922 microbiome batch plus 11 supplementary cards in earlier progressive-review/planning-support trees. This is why a search limited to one batch directory returns 580 card files rather than the full 585.

The two named audit files are present and their references resolve:

| Item | Bytes | SHA256 | Reference result |
| --- | ---: | --- | --- |
| F:\OptoMind-Review-2\outputs\unit_writing\20260927_astra_repair\DELIVERY_MANIFEST.json | 3,794 | 7e4ebe3f6a4860d32b1b054fa40b1394e5cc004eef387388557f48ca54cec77c | Six chapters; 12 arrangement/Markdown paths found, 12/12 present |
| F:\OptoMind-Review-2\outputs\review_v2_repair\05_real_test_20260929\attempt02_production_path\UPDATED_WRITER_PACKET.json | 637,390 | 1c3470ff492624193838da157ada1b4ba0034cb30aabddb602ba623804997e64 | 24 distinct card files present; 24 distinct reading paths present as directories |

The packet reading_path references are directories, not leaf files. A leaf-only test would falsely mark them missing. Every one of the 24 directories exists and has seven direct children: ACQUISITION_LOG.jsonl, DOCUMENT_ASSETS.json, DOCUMENT_BLOCKS.jsonl, DOCUMENT_MANIFEST.json, READING_VIEW.md, REFERENCES.json, and sources. No cited packet path needed a stale-reference substitution.

The manifest’s 12 existing references are:

- CH01: F:\OptoMind-Review-2\outputs\chapter_arrangement\20260927_astra_batch\CH01\CHAPTER_ARRANGEMENT.json and .md
- CH02: F:\OptoMind-Review-2\outputs\chapter_arrangement\20260927_offline_repair\cache_reexport_ch02\CH02\CHAPTER_ARRANGEMENT.json and .md
- CH03: F:\OptoMind-Review-2\outputs\chapter_arrangement\20260927_astra_aliasfix\CH03\CHAPTER_ARRANGEMENT.json and .md
- CH04: F:\OptoMind-Review-2\outputs\chapter_arrangement\20260927_astra_aliasfix\CH04\CHAPTER_ARRANGEMENT.json and .md
- CH05: F:\OptoMind-Review-2\outputs\chapter_arrangement\20260927_offline_repair\cache_reexport_ch05\CH05\CHAPTER_ARRANGEMENT.json and .md
- CH06: F:\OptoMind-Review-2\outputs\chapter_arrangement\20260927_astra_batch\CH06\CHAPTER_ARRANGEMENT.json and .md

## 585-card lineage

The source pool is:

F:\OptoMind-Review-2\outputs\progressive_review_plan\20260926_astra_repair\INPUT_POOL.jsonl

It is 2,983,512 bytes with SHA256 39297f0ea6909feb5bb4ad7b293d7bce156c9d1817871e23947bc21734bb07ad. The main batch root is:

F:\OptoMind-Review-2\outputs\paper_cards\20260922_microbiome_batch

That root contains 580 PAPER_READING_CARD.json files:

| Subtree | Count |
| --- | ---: |
| reading | 566 |
| corrected_reading | 2 |
| format_recovered_reading | 7 |
| format_recovered_reading_retry2 | 1 |
| pilot_reading | 4 |

BATCH_METRICS.json names 574 unique production card paths, all present. The six local cards outside that set are four pilot cards and two extra reading cards. The production metrics and acceptance records are consistent with 595 target rows, 574 ready cards, and 21 metadata-only unread records:

- READING_MANIFEST.json: 304,144 bytes; SHA256 658b060006355df7b6bc53dc96bafce2d58da0c625b04feed6b3dc555b1732e7; 595 records, finalized, zero material failures.
- BATCH_METRICS.json: 415,740 bytes; SHA256 4b8c628db5a3c6207947b41e5d1eccd43a547516cc381983e60383fafff08319; counts are success 562, insufficient material 21, failed 8, reused 4; final counts are cards-ready 574 and insufficient-material 21.
- reading\BATCH_STATE.json: 650,749 bytes; SHA256 4a6adcf10a827512f94a092c472c50786afe9c6777c19d8f5e670c9227f98a34; 595 items with the same status distribution.
- reading\BATCH_STATE.before_material_isolation.json: 422,886 bytes; SHA256 93647908f8efaca81e3a676b8d3da771299eba949f476dfb01dfd1f0f7ab849f.
- FINAL_ACCEPTANCE.json: 4,130 bytes; SHA256 e0b28b6aae63f5c3a2f025edbdeb76ca79f11b5e1d5fea237af9df91914fbab8; expected 595, selected unique cards 574, metadata-only unread 21, all target IDs accounted for, selected commit hashes verified.
- BUDGET_AUTHORIZATION.json: 385 bytes; SHA256 92b7d4ccef7c9232166e190b6914e3be978b1684887998855148950a76647fe5.

The 11 supplementary card paths are listed in historical-assets.json under historical_585.external_source_pool_cards; they were all found as exact local files. Keeping the list in JSON avoids silently collapsing the external part of the 585-card pool into the main batch count.

## Selected 200 and retry lineage

The local run reference is:

F:\OptoMind-Review-2\_publish_lihonggang_20261002\audit_materials\lihonggang_full_acceptance_20261002\run585_reference

It contains 17 files and no PAPER_READING_CARD.json copies. The cards are still locally addressable through SELECTED_SOURCE_IDS.json joined to the 585-row INPUT_POOL.jsonl:

- SELECTED_SOURCE_IDS.json: 12,651 bytes; SHA256 be283040ee2e14f225f255fc6e5d9ee04ace74ff8ce477ef55807066aab3b6b0; 200 selected IDs, 200 pool matches, zero missing IDs, zero missing card paths.
- The selected set is 193 main reading, one corrected_reading, one format_recovered_reading, one format_recovered_reading_retry2, and four external cards.
- RUN_MANIFEST.json is 1,196 bytes with SHA256 93e04a304fb9372624a5a7edf30d9481b11beb2e290196cf8bf35db942806f00; it records a coverage limit of 200 of the 585 full-field production B summaries and the same source-pool SHA256.
- RAW_RESPONSE_INVENTORY.json is 1,901 bytes with SHA256 bdd2933daeefca35dc8c836e66b0f78015f4f77109d47ea49aa2d80b21e953cd; its two inventory entries omit raw response bytes from the published package.
- complete_retry\RUN_REPORT.json is 3,761 bytes with SHA256 fae9b0853f3fe165dca4df455007f7675f755ef87912b81d78a108a8346749ad; it records selected count 50, original pool count 585, and no_old_cache=true.

## Streaming, ledgers, seals, and logs

Exact .sse.raw inventory counts are 492 under Review-2, zero under Article-1, zero under Article-2, and zero under the 20260922 585-card batch. The batch does contain 1,224 .raw files under raw_responses; these are a different file form. The zero counts are scope-specific and do not establish that streaming data never existed in excluded caches or cloud/published packages.

Two referenced SQLite ledgers exist, but only metadata was inspected:

- F:\OptoMind-Review-2\outputs\review_blueprint\20260922_phase1\budget.sqlite, 929,792 bytes, referenced by the batch state.
- F:\OptoMind-Review-2\outputs\lihonggang_acceptance_20261001_1cny\budget.sqlite, 20,480 bytes, referenced by the selected retry report.

Within the allowed non-cache inventory, searches for CACHE_SEAL, cache.?seal, SEAL_PATH, and result_seal returned zero hits. Cache directories were excluded by instruction, so cache-seal content remains unverified.

Review-2 has four logs directories with 25, 42, 3, and 2 files. Article-1 and Article-2 have no logs directory in the same search range. Log bodies were not read.

## R03 and R06

The actual R03 output exists at F:\OptoMind-Review-2\outputs\upgrade3\R03. Key hashes:

- revalidation\attempt1\GOLD_V2_SUMMARY.json, 330 bytes, 6b04918d3884e0376b0f2550d1aced6af10c94cd2508b12e08da4c1a932ba016
- revalidation\attempt1\R03_REVALIDATION.json, 849 bytes, 4c534814ba9e0e845b767af7d502e3680f212d45f6ab40080d2aaab367d07a46
- revalidation\attempt1\SOURCE_LEVEL_GOLD.json, 39,740 bytes, 7bc2afc50c23472c1fa8b5c079ac5828ef3aad01a4834290a1d715cfb687d578
- revalidation\attempt1\SCOPE_DECISIONS_B.jsonl, 4,942 bytes, 69ce208af73ed8b92e23cd0f7ef2dad7761446e7ca1160d8807a5ccd52ffc175
- revalidation\attempt1\SCOPE_DECISIONS_C.jsonl, 3,704 bytes, 4b6129d7555d738c1762727ee8898636bf5d227cf63100e67b591c2b61e5e0ec

The R06 work-order document exists at F:\OptoMind-Review-2\升级工单3\R06_依赖恢复返工.md (2,674 bytes, SHA256 12854fb2d7d3775c96a56fcd87a5a15965ac367c1ae0e3b8e77808e4257eb483). No F:\OptoMind-Review-2\outputs\upgrade3\R06 output directory was found within the search range.

## Regression copy plan

No source material was copied. If a later regression needs a local subset, freeze each selected source path, size, and SHA256 before copying; use the exact INPUT_POOL.jsonl paths, joined through SELECTED_SOURCE_IDS.json when a selected-only package is requested; retain those mapping files beside the subset; verify copied hashes; and leave every old source directory untouched. The machine-readable JSON contains the complete 11-path external list and the selected four external paths.
