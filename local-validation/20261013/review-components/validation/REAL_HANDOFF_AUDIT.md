# Real handoff audit

## Result

- `formal-01` completed with `planning_status=complete`, `material_parser_mode=docling_basic`, `chapter_material_view=full_A_B`, and planning revision enabled. `CURRENT_PLAN.json` points to `formal-01.unstrengthened/revisions/26e3c121f4cb88cfc226`; its manifest is `original_valid_plan` with CH1/CH2/CH3 packets.
- The finalized scope and chapter order match the completed plan: `物理界限：色散工程中的延迟 - 带宽积与视场饱和效应`; `设计机制：色散补偿策略与离轴效率损耗的微观起源`; `实证表现：已制备器件的性能边界与评估基准的批判`.
- Canonical identity is present in the top-level `source_identity_map` and each `writer_packets/CH*.json`: P0001 = `dd2dc3ec9c62d3fea69db49d6c9a4c78eb1a6f49`, DOI `10.1002/lpor.201600295`, title `Metalenses at visible wavelengths: past, present, perspectives`; P0002 = `84c74988ea8a29fd3a5a26d2655fc7505820f72e`, DOI `10.1051/epjam/2022012`, title `Bandwidth bounds for wide-field-of-view dispersion-engineered achromatic metalenses`.
- Every chapter packet retains `study_summary_A`, `review_planning_B`, `deep_read_material`, `paper_id`, `source_handle`, title, DOI, and `material_depth=fulltext` for the routed material. Local navigation records 2 indexed papers and 238 segments; P0001 routes to CH1-CH3 and P0002 routes to CH1-CH2. The packet still carries P0002 in CH3 while CH3 candidate navigation reports `routed_to_chapter=false`; this is a routing metadata/content discrepancy to resolve before treating routing as authoritative.
- Docling consumption is evidenced by the reader prompts: P0002 has 36,637 fulltext characters plus 1,511 structured formula characters across 6 recognized regions; P0001 has 63,332 fulltext characters plus 235 structured formula characters across 1 recognized region. `INPUT.json`, `DIRECTED_READING.json`, and final packet `deep_read_material` share the same source hashes and `material_ready=true`. Formula cache bindings are valid: the P0002 old-root `CURRENT.json` points to a file whose SHA256 equals the current copied cache and whose canonical/PDF/raw-document binding matches; this is expected cache-origin reuse, not a content-integrity gap.
- The card-level limits are present in both source cards: allowed uses are background/objectives/method summaries/reported findings/leads; prohibited uses include upgrading snippets, inferring unstated methods/controls/limitations, treating metadata as an abstract, and certifying semantic truth from a quote; the unknown rule is explicit. In the final plan and packets, `usage_constraints` exists only as empty arrays, and tool-material `allowed_use`/`limits` are empty. The exact card `usage_limits`, `prohibited`, and `unknown_rule` block is absent. Source-specific study limits under `study_summary_A.contribution_and_limits[*].limits` survive, but they are not the card usage contract. `candidate_navigation` carries A/B/local/deep fields; `case_enrichment` carries 12 source-handle/contribution additions without the card contract.

## Changed(none)

Only this audit file was written. No source, cache, plan, packet, or formula artifact was edited.

## Verify

- `PLANNING_RUN_REPORT.json`, `CURRENT_PLAN.json`, `HANDOFF_MANIFEST.json`, final `DETAILED_REVIEW_PLAN.json`, and all three writer packets were read at field level.
- Reader `PROMPT.json`, `INPUT.json`, `DIRECTED_READING.json`, Docling snapshots, formula caches, and `LOCAL_INDEX_BUILD.json` were compared by identity/source hash and bounded counts.
- Production resume feedback syntax for the requested generic CH1 change is:

```json
{"edits":[{"chapter_id":"CH1","feedback":"Improve clarity and organization of CH1 while preserving the finalized scope and supplied source evidence; do not add new scientific claims or new sources."}]}
```

- Resume must keep the same topic, pool, plan, output root, and planning-revision mode; inspect `stages/affected_chapter_revision.json` and its CH1 cache input, then expect changed feedback under `editorial_feedback_for_chapter` and prior attempts under the revision `history` directory.

## Blockers

- The card usage contract is lost before writer handoff; empty planner `usage_constraints` must not be interpreted as equivalent protection.
- CH3/P0002 routing metadata disagrees with the actual packet material assignment.
