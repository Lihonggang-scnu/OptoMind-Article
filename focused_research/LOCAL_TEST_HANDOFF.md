# T022: S2 + complete-paper materials + knowledge-based candidate routes

This replaces T021's not-yet-started scope. If T021 is already running, preserve its in-flight call and completed results; stop at a safe stage boundary and reuse valid outputs rather than repeating paid work. Do not overwrite an active checkout. Cloud owns implementation; local performs this small integrated test and minor environment repairs.

## Fixed inputs

- Article implementation SHA: use the accompanying C2L-R3-0035 release (not the older T021 da12ffd alone).
- Full-text runtime: a separate clean local Review checkout at `d2deb4dbe18b23837d949822ad731afbaba9414b`. Reuse the existing Review Python/Docling environments and cached weights. Do not reset an active Review worktree.
- Question: unchanged complete `focused_research/examples/rgb_nir_question.v2.json`.
- Baseline retrieval report: Article `7099ba90e759fea115269eaa158524050a63cdf5/handoff/R3-T019-S2-QUALITY-20261015`.

## Budget and bounds

User raised the **whole T022 chain cap to CNY 15**, not CNY 15 per stage. Use the existing independent CNY 20 ledger AND the global CNY 140 ceiling conservatively across all existing ledgers. First reconcile settled plus pending/held/uncertain amounts; preserve reservations for uncertain calls. Reuse the proven D20 wrapper for every model call. If it cannot enforce the same task cap/counter across stages, report blocked; do not use a bare client. Cloud makes no paid calls.

One task-sheet generation and one candidate-route generation; at most one format repair each, maximum four model HTTP attempts across the chain, transport retry zero. Existing successful compatible T021 outputs consume no new call and must be reused. No extra model-based judging, model upgrade, automatic tuning loop or new paid retrieval/parser service. Existing local parsing/CodeFormula inference costs no project model-API fee. Spending the cap is not a goal.

## Run only this small chain

1. Use the admitted local launcher to call `run.main plan` with the native question, or reuse the exact-question/current-prompt compatible T021 result. The program attaches original_question; the model outputs analysis only. Preserve raw and assembled output. Do not change generated queries by hand.
2. Run actual S2 retrieval, ten entries per generated query, using original endpoint/config:

       python focused_research/run.py retrieve --plan-file <plan>/query_plan.json --output <s2-new-output> --per-query-limit 10 --retrieval-seconds 300

   S2 HTTP 429 is a pending rate-limit condition, never evidence of an empty literature result or an ineffective query. Cool down using Retry-After (or increasing backoff when absent), and retry only the unsuccessful query, up to 15 reconnections after its first request. Preserve successful query results. The S2 retry allowance is separate from the four paid-model HTTP attempts; never use it for model retries. Exhaustion is an explicit incomplete/blocked query, not a zero-hit success. Other source failures are recorded and do not authorize repeating the model generation. Keep partial/empty S2 output; the next sources do not require S2 success.
3. Run `fulltext_stage.py` against the pinned external Review environment, with the generated queries and optional provider records or existing local XML/PDF records. It uses independent configured OpenAlex/CORE searches and Review's existing acquisition sources. Other sources are used only where already configured. No Firecrawl/paid regional fallback. At most five papers for this first test; retain source failures explicitly. Existing compatible full-text/parser caches should be reused.

       python focused_research/fulltext_stage.py --queries <plan>/queries.json --records <records.json> --output-root <fulltext-output> --review-root <pinned-Review-root> --review-python <existing-Review-python> --docling-python <existing-Docling-python> --docling-config <adapted-BASIC_PARALLEL3-profile> --max-papers 5 --deadline-seconds 180

   `--records` is optional. For supplied local papers use records with canonical_paper_id and local_xml or local_pdf; use actual provider metadata, never guessed article URLs. Copy the pinned Review's BASIC_PARALLEL3_CONFIG.template.json to a task-local config and adjust only its local artifacts/cache path as needed. Preserve workers3, safe parser/native thread1, b4/t4 and GPU ceiling8192MiB. The wrapper invokes the mature batch with resume/recovery and imports its verified snapshots. Native XML is preferred. Do not describe a fitz fallback as the Docling body.
4. When a selected paper needs formula reading after basic parsing, use the dedicated second-phase entry (new output directory):

       python focused_research/fulltext_stage.py --enrich-result <fulltext-output>/FULLTEXT_RESULT.json --output-root <new-formula-output> --review-root <pinned-Review-root> --review-python <existing-Review-python> --local-formula-config <existing-hook.json> --formula-selected-id <canonical-paper-or-snapshot-ID>

   This phase does not repeat discovery/download/basic parsing and preserves the original exports. Use the returned enhanced export for the selected paper in the next step. Run selected enhancement after basic parsing completes; do not run both GPU pools together. Existing sidecars are reused; do not enhance every paper merely because the option exists. Raw PDF, weights and original snapshots remain local. Missing cached setup is a specific local blocker; no unapproved installation or replacement paid service.
5. Import verified export paths listed in FULLTEXT_RESULT.json together with S2 evidence using material_evidence.py:

       python focused_research/material_evidence.py --s2 <s2-output>/evidence_items.json --export <verified-export-1.json> --export <verified-export-2.json> --output-dir <material-archive>

   Then repeat the importer into a NEW view directory with `--selections <selection.json>` to produce selected_evidence_items.json. A selection is a JSON list of {snapshot_id, block_id}, using actual IDs in material_block_index.json. Use the enhanced export in place of the same paper's earlier ordinary export to avoid duplicate snapshot identities. Save the full archive and block index. The route prompt strips archive-only copies and enforces a bounded evidence view; if it exceeds the limit, select whole blocks explicitly rather than retrying a paid model request with an oversized payload. Select whole relevant body/method/table/formula blocks from actual contents into a bounded prompt view; preserve the selection list. Do not truncate tables or claim skipped content was read. Metadata can be incomplete. If no external source is available, use an honest empty evidence payload and allow model-knowledge routes.
6. Generate a small set of 2–3 routes (fewer allowed) via the SAME audited planner:

       import route_stage
       route_stage.main(['generate', '--task-sheet', '<plan>/task_sheet.json',
         '--evidence-file', '<selected-evidence-items.json>',
         '--constraints-file', '<checked-constraints.json>',
         '--output', '<new-route-output>'], planner=audited_planner)

   Load focused run.py first as in the tested local launcher, so the vendored planning imports resolve. Reuse rgb_nir_design_constraints.json only after matching its constraint-source IDs to this generated task sheet; if renumbered, map by the actual constraint meaning. Air incidence remains an explicit test assumption. Do not alter user bands, material choices, angle, layer cap or averaging definition.

   Route v2 permits external_evidence, model_knowledge and hybrid. Do not force proportions or reject a reasoned knowledge-based route for having no citation. Each still needs a bounded executable structure, physical reasoning and a baseline-referenced directional expectation. Never invent a literature citation or optical performance result.
7. Prepare ready instances and call route_consumer.prepare_tmm_inputs with existing explicit material aliases and a finite grid including exact band endpoints/interior points. Save prepared JSON. Missing material mappings stay pending. **Stop before VeriTMM simulation**, optimization, physical response screening, report-PDF typesetting or further iteration. Input paper PDF parsing above is in scope.

## Rapid report and upload

No repeat of all offline tests. Briefly report: actual model calls/costs/liabilities; native question preservation; per-query S2 quality versus baseline; independent-source hits; full papers acquired/parsed and failed; cache reuse and Docling/formula statuses; which actual table/formula/body blocks entered the model; each route's basis, reasoning, expected change and input-preparation result. Weak or opposite-function literature can support transferable methods, not claims of a matching device.

Upload a short report, raw model outputs, generated task/queries, source-linked evidence views, parser summaries, selected-block manifest and route/prepared-input JSON to a new independent Article handoff branch/directory; return its fixed commit and receipt, then stop. Keep raw copyrighted PDFs, model weights, local databases, keys and potentially credential-bearing process logs local. Do not force-push or overwrite main/history. Report partial results honestly; offline checks or prepared inputs are not scientific acceptance.
