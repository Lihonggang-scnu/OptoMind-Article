# T021: revised task sheet → actual S2 → grounded candidate routes

This supersedes the older plan-only handoff. The user expanded this task to one small integrated first+second module run. Do not run the separate T020 offline acceptance suite, simulation, PDF, broad regression, or another tuning cycle.

## Inputs and changes
- Baseline quality evidence: Article commit `7099ba90e759fea115269eaa158524050a63cdf5`, `handoff/R3-T019-S2-QUALITY-20261015`.
- Use the complete `focused_research/examples/rgb_nir_question.v2.json` question, unchanged.
- Cloud now attaches original_question programmatically; Qwen outputs analysis only. Preserve the raw response and merged sheet separately. Existing constraints/metric/span validators remain.
- Prompt revision disambiguates light incidence vs deposition angle and wavelength filtering vs spatial edge detection. Q1/Q2/Q5 examples retain their prior queries as useful baselines; Q3/Q4 are revised, Q5 rationale corrected. These remain provisional, not proven improvements.
- Use the supplied implementation SHA from the accompanying release, in a separate short checkout. Preserve existing work and billing integration. Do not overwrite the current developing checkout.

## Small live run
Reuse the already working local Qwen configuration and R3-T019-D20 billing bridge. Do not recreate credentials or billing architecture. First verify current settled plus pending/held/uncertain charges. Incremental task limit: CNY 2, within the existing independent CNY 20 ledger AND the user's global CNY 140 ceiling (aggregate both ledgers conservatively; this does not authorize a new allowance). No unresolved liabilities may be silently dropped. Cloud makes no paid calls.

One initial task-sheet generation and one initial route generation; each permits at most one format repair, at most four HTTP attempts total across both stages with transport retry zero, enforced by the existing wrapper before dispatch. If the wrapper cannot enforce it, report blocked without spending. No additional model-based quality judging. The CNY 2 cap is for the whole chain, not per stage. Keep the production module loaded from this delivered checkout; retain only the previously tested local billing/environment integration.

Use the existing admitted launcher to invoke `run.main plan` with this question and a new output directory. A bare plan/run CLI bypassing the wrapper is not authorized. If plan succeeds, immediately execute actual retrieval from its generated query_plan.json, with the existing S2 configuration:

    python focused_research/run.py retrieve --plan-file <new-plan-output>/query_plan.json --output <new-retrieval-output> --per-query-limit 10 --retrieval-seconds 300

Do not manually replace generated queries with the example queries. Keep the original endpoint for each query. One retrieval pass; no expansion, PDFs, metadata completion or extra tests. Record failures and retain other successful query results. No new paid retrieval service.

After retrieval, use the actual evidence_items.json and native task_sheet.json for route generation. Invoke route_stage.main with stage generate and an explicitly injected planner from the SAME metered local wrapper; the command refuses to construct an unmetered client. Use rgb_nir_design_constraints.json after checking its C5/C7/C4 references still match this generated sheet; resolve IDs by their meaning if Qwen renumbers them. Preserve its air assumption as an explicit test configuration, not a user fact. Aim for 2–3 grounded routes, fewer if evidence is insufficient. Do not use synthetic evidence fixtures. Save raw response, decision clues, routes and prepared instances.

For ready instances only, invoke route_consumer.prepare_tmm_inputs with explicit material aliases and a finite grid covering both exact band endpoints; save prepared input JSON, but do not invoke VeriTMM or fetch new material data. If mappings/conditions are missing, keep the route pending and report the specific gap; do not invent constants. This is an interface preparation check, not a physical result.

## Rapid report and upload
Keep raw Qwen response, merged task sheet, exact queries, raw S2 results, source links/IDs and snippet types. Also give a short human review of each candidate: which actual passage supports it, what is inferred, its baseline/expected direction and reason, and whether it converts to a bounded structure. Do not demand that weak evidence proves the design. Briefly compare retrieval with the fixed baseline: relevant device leads, transferable method/parameter leads, off-topic/duplicates, and the best 3–5 sources with one-line reasons. Report whether the two ambiguity problems improved; unchanged results are valid findings. Do not count opposite-function papers as matching devices. Missing metadata is allowed. Include actual costs, attempts and pending/held/uncertain totals.

Upload only these outputs and a short report to a new Article handoff directory on an independent branch; return a fixed commit link and receipt, then stop. Do not upload keys or ledger databases. No main overwrite or force push. Local handles only testing and minor environment fixes; return core defects to cloud.

### Injected route-stage call
The existing local billing launcher may call this Python API after constructing its already-audited planner. Load focused_research/run.py first (as in the previous plan launcher) so the vendored Review imports resolve, then:

    import route_stage
    route_stage.main([
        'generate', '--task-sheet', '<plan-output>/task_sheet.json',
        '--evidence-file', '<retrieval-output>/evidence_items.json',
        '--constraints-file', '<checked-constraints.json>',
        '--output', '<new-route-output>'
    ], planner=audited_planner)

The injected planner must use the same already admitted task budget and shared HTTP-attempt counter as planning. No fresh allowance between stages. The route stage writes generation_outcome.json before materialization, and prepared_instances.json for all ready routes even when others need revision. Read summary status; a zero exit with needs_revision does not mean scientific acceptance. The shell generate command without injection is intentionally blocked.
