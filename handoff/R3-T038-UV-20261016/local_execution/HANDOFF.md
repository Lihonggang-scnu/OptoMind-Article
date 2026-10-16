# T038: recover the saved UV task sheet and use only the two remaining calls

Base: `49861e169084976069d9ef1008e4fafc84b86bb8`. T038 continues the same original T032 case04; it does not restart planning or create a new budget. Both saved rounds have recovered successfully offline and frozen cloud verification is complete. The final release SHA comes from the accompanying ready; original-executor acceptance remains pending. Execute only after the accompanying fixed-SHA ready, offline recovery and original-ledger admission.

## Actual blocker and narrow repair

T037's **131 Windows tests passed**, including fresh-byte hashing despite unchanged metadata. The seven existing material records and boundary media passed the recorded source/grid checks. Those repairs do not need another scientific campaign. Case04 then consumed its first task-sheet request and **only shared format repair**: **2 HTTP / CNY 0.0371824**. Both saved responses were rejected because G3's `original_span` repeats “尽可能抑制 ” before “700–1100 nm 近红外杂散光”; the native question shares that verb across two clauses rather than repeating it. No retrieval, route generation, physics, certification, revision or refinement occurred.

The program now computes `quote_status` from the native question. An exact span is `exact_user_quote`; a nonexact span is **`unverified_model_paraphrase`**, with the original model span retained unchanged and an audit. Do not trim the saved span into a fabricated quotation, rewrite the raw answer, or trust a model-supplied status. This classifies quotation provenance only; it does not prove semantic correctness. Executable metrics, senses, regions, IDs, exact explicit constraints, query text and other strict validation remain in force. Significant factual misreading remains reportable; disagreement over hypotheses or imperfect prose is not itself a reason for another paid repair.

Both raw rounds must be replayed offline and compared with their derived sheets. Use **the second saved response** for continuation after it passes validation and adaptation. Preserve the native question, original response bytes, all goal spans, executable fields and hard constraints. Any additional pre-existing metadata normalization must remain explicit in its audit. `INPUT_PROVENANCE.json` is the source binding; cloud replay success is not local acceptance or a UV scientific result.

## Same original science and available materials

Keep the exact original `e2e-04-solarblind-uv-triple` question: maximize mean T at **255–280 nm**; minimize mean T separately at **300–700** and **700–1100 nm**; air, fused silica, planar multilayer ≤30 layers, ordinary depositable inorganic dielectric films, no metals. Keep 0° incidence, equal-s/p and wavelength-integral means as separately labeled execution assumptions. No new point goals, weights, named-material user whitelist or global thickness bounds. Finite positive family domains remain model search hypotheses.

Reuse the seven actually verified records, checking current bytes, exact identity and full-grid coverage: local HfO2/hfo2, TiO2/tio2, Al2O3/al2o3; RII SiO2/411, MgF2/204, Ta2O5/414, Nb2O5/397. Existing fused-silica 835 has n but no k; preserve the explicit provider zero-k substrate convention without presenting it as measured film n/k. Air is the explicit n=1, k=0 model. Keep film losses, the 255–1100 nm / 1 nm / 846-point preflight, disabled extrapolation and recorded ZrO2 coverage exclusion. The finite execution inventory remains separate from broad user permission. Fresh hashes and selected-record binding do not replace coverage sampling.

## Zero-model recovery and guarded continuation

1. Verify the ready's implementation, imported files and `INPUT_PROVENANCE.json`. Independently hash the actual saved T037 planning file; its verified SHA256 is `04d04afb21e081d0493b95af3e5f25449f45a0bbad2aa558680baac60a6d26e2`. Verify the exact native request as well. The updated recovery CLI supports this source-pinned T037 case separately from T029. The native question UTF-8 hash is `a072e2f7732cc73738382bb04f916bcd0a1f4814c526cb9c022290cd6bd73d12`; `a3f9e66596b0fce92d0f89753c77b6add18b48705c09b921041da851b35ca5fd` belongs to the text file with one terminal newline and must not be substituted.
2. Run the two saved rounds through the new preparation/validation path and derive round2 `task_sheet.json`, `query_plan.json`, `normalization_audit.json` and `recovery.json` in a new output directory. `raw_response.json`, `raw_task_sheet.json` and the manifest retain lossless saved-response content and artifact bindings. Use this exact CLI, with `SOURCE_PLANNING` bound to the verified local T037 planning file and `NEW_ROUND2_OUTPUT` a new directory:

   ```text
   python -m focused_research.task_sheet_recovery --case-id e2e-04-solarblind-uv-triple --request focused_research/campaigns/t032/inputs/e2e-04-solarblind-uv-triple/FRESH_REQUEST.json --planning SOURCE_PLANNING --expected-sha256 04d04afb21e081d0493b95af3e5f25449f45a0bbad2aa558680baac60a6d26e2 --source-commit 028b4955f75fb6e421e0e727c7451d38431b7970 --round-number 2 --output NEW_ROUND2_OUTPUT
   ```

   For the required round1 offline comparison, use `--round-number 1` and a different new output directory; only round2 is used downstream. Verify saved raw/derived comparisons and recovery hashes. This step uses **0 model HTTP / 0 production physics or certification / 0 CNY**. Do not call the planner, regenerate a sheet or edit an executable field to obtain acceptance.
3. Reconcile the original ledger and all durable call history. At the last T037 snapshot case04 used **2/4 HTTP and CNY 0.0371824**; original T032 batch used **8/12 HTTP and CNY 0.3016536**; project exposure was **CNY 19.266059**, outstanding 0. Recheck live settled/pending/held/uncertain exposure before every request; these historical figures are not a new allowance.
4. Construct the recovered guard with **both explicit cumulative parameters**, after independently verifying its supplied recovery manifest:

   ```python
   guard = RecoveredCampaignPlannerGuard(
       existing_audited_planner,
       case_id='e2e-04-solarblind-uv-triple',
       audit_sink=existing_durable_audit_writer,
       recovered_task_sheet_binding=verified_recovery_manifest,
       prior_dispatch_attempts=2,
       cumulative_dispatch_limit=4,
   )
   ```

   `existing_audited_planner`, the writer and manifest are explicit launcher bindings, not new clients or credentials. The constructor checks manifest shape, not the actual files; verify them before construction. Persist before-dispatch and final audits. Continuation stage counters and its local repair flag do not erase the two prior planning attempts or the already-consumed historical shared repair. The original transport/ledger remains authoritative for HTTP and money. Do not omit those two parameters: the historical three-attempt default is **not** this case's authorization. Do not recreate a failed guard or reset its history.
5. Use the recovered **`query_plan.json`** for cached-first bounded retrieval, once if needed. T037 had no retrieval; reuse valid current-campaign caches and do not repeat a completed retrieval stage. Keep the existing per-query limit 10 and 60-second retrieval budget. Report missing evidence/rate limits honestly; labeled model-knowledge/hybrid routes remain allowed. Do not retrieve prior solved designs as cold-start answer seeds.
6. `guard.set_stage('route_generation')` permits **one route-generation request only**. There is no route format-repair token left. Use verified material mappings and the ordinary injected route interface. Preserve raw routes, full evidence and the separately selected executable set. Downstream validation/materialization/search must use active **`CASE/routes/evidence_items.json`**, not the full archive or compact prompt projection.
7. Run the initial bounded search. If valid certified observations exist, `guard.set_stage('search_revision')` permits **one revision request only**, followed by one bounded refinement through the existing T032/T036 flow. No revision retry or T030 expansion/reflection experiment. If route validation fails or there is no usable certified archive, stop that path without inventing another call.

Only this original Agent/case04 continuation is released; do not rerun 01/05 or completed T035/T037 checks as a new campaign, overlap jobs, add credentials/providers or upload private databases. Stop after the initial search → revision → refinement cycle or an honest blocker. Quality tradeoffs or failed hypotheses are legitimate outcomes; no extra cycle is authorized to improve the score.

## Remaining ceilings and receipt

- **Two new HTTP maximum**, one route and one revision. Case04 remains **≤4 total** across T032/T036/T037/T038. The shared repair is already consumed; failed/uncertain dispatches count, and transport retries are disabled. Unused other-case batch capacity cannot be borrowed.
- **CNY 8 same-original-case / CNY 24 original T032 batch / CNY 220 project cumulative**, including all history and liabilities; no new pool or automatic extension.
- **4096 forward design rows / 64 new certifications total**, split **2048/32 per initial/refinement stage**, at most two certification workers. Count failed and finite-difference evaluations.

The actual two-round offline replay is ready: G1/G2 have `exact_user_quote`, G3 has `unverified_model_paraphrase`, and every existing span, metric, explicit constraint and query remains unchanged. Round2 was actually recovered at `/workspace/scratch/3538d6266403/t038_case04_recovered_round2`; that existing directory must not be overwritten. Its **canonical JSON** task-sheet digest is `70754cccfea49e884680ec01c7c3012db6d68862a8428ec646aa34a113a90893`; query-plan digest is `e8e0d0e716e49f048d3313d9317fc3020921379df6eecfce09719522b4d2ef7e`. These canonical JSON digests differ from the source planning file's byte hash.

Reported offline subsets: **25 recovery tests** (both actual UV rounds and legacy three-case recovery), **74 guard tests**, and **138 quote-related tests**; a recovery/tolerance run separately passed 60 tests. These counts overlap and must not be summed. The final frozen full suite passed **811 tests, with 4 skipped and 6 subtests, in 52.45 seconds**, with sockets disabled; compileall and diff checks passed. The first full run exposed one old `test_native_question` expectation; it was corrected for the new explicit unverified-quote classification, and its 14 related tests passed before the final rerun. This did not remove executable-field or explicit-constraint checks. Original-executor source/artifact verification remains pending. The source-pinned recovery additionally rejects changed bands, metrics, objective senses or explicit constraints. The quote label itself does not make that semantic guarantee for arbitrary new model output.

Before local admission, run the relevant frozen regression files in the existing environment:

```text
python -m pytest -q focused_research/tests/test_subgoal_quote_status.py focused_research/tests/test_task_sheet_recovery_case04.py focused_research/tests/test_task_sheet_recovery.py focused_research/tests/test_recovered_campaign_attempt_budget.py focused_research/tests/test_local_campaign_guard.py --disable-socket
```

`OFFLINE_VALIDATION.json` records the actual results and remaining local gate. Return completed / partial / blocked with actual source/recovery/import hashes, two-round audit and unchanged-field evidence, material verification, raw model exchanges if dispatched, initial/refined certified metrics, exact call/cost/numerical counters and reconciled cumulative exposure. Distinguish contract acceptance, physical observations and untested mechanisms. For a blocker, specify the action, target, verified reason, completed work and pending/uncertain state. Exclude authentication headers, credentials and full private ledger/material databases.
