# T036: resume the blocked T032 solar-blind UV case

Base: `5c50430693151d2066cf352fcc9bb80e5ee6b667`. Cloud regression verification is complete; actual original-executor inventory/preflight and case04 execution remain pending. Use the final implementation SHA supplied by the accompanying ready message after this packet is committed. The base SHA is not the new release.

## Immediate result and queue

The original Agent's `L2C-R3-T032-CASE04-BLOCKED-20261015T234512Z` establishes an engineering blocker before case04 dispatch: **0 case04 HTTP / 0 CNY**. The old contract treats `allowed_materials=null` as missing configuration and cannot express broad material-category permission separately from a finite execution inventory. Replacing that permission with HfO2/SiO2/MgF2 would change the user's request.

Keep T032 case01 moving, then independently eligible case05, on their current `8a38b2d` implementation. Do not interrupt them, change their active checkout, or rerun either case. After **T032 is terminal and T035's zero-cost offline acceptance has returned**, the separate fixed-SHA T036 ready may release **case04 only**. No simultaneous cases or paid jobs. A scientific tradeoff does not itself block an independent case; a live global ledger or shared engineering uncertainty does.

`SOURCE_BLOCKER.json` preserves the actual receipt byte-for-byte. `INPUT_PROVENANCE.json` pins it and the original T032 question. The blocked receipt's 85-test count is historical evidence from the original executor, not verification of this repair. Its ledger snapshot included **one unresolved CNY 0.8883 hold**; it is neither a current balance nor permission to reuse a previously reported zero-liability state.

## Unchanged question and execution limits

Use T032's exact `e2e-04-solarblind-uv-triple` question and original case identity. Maximize band-mean T in **255–280 nm**; minimize band-mean T separately in **300–700 nm** and **700–1100 nm**. Preserve air incidence, fused silica, a planar multilayer with at most 30 layers, ordinary depositable inorganic dielectric films and no metal layers. Suppression is not silently changed to reflection, and dielectric classification does not imply zero absorption.

Keep 0° incidence, equal-s/p unpolarized light and wavelength-integral means separately labeled as execution assumptions. Use exact endpoints and interior samples for each band. No point goals, weights, score thresholds or new global layer-thickness constraints. Model-proposed finite positive family domains are search hypotheses, not user manufacturing limits. Do not insert old champion designs or results into the cold-start context.

## Material permission and execution inventory

The repair retains the existing finite-list contract for other cases. Category mode uses:

- `allowed_materials: null` and `allowed_material_category: "ordinary_depositable_inorganic_dielectrics_no_metals"` for the original broad permission.
- `constraint_source_ids.allowed_materials: []`; `constraint_source_ids.allowed_material_category` references the actual generated task-sheet constraint IDs that support the category. Do not copy illustrative IDs or claim the finite inventory was requested by the user.
- For executable routes, `execution_materials` is a nonempty alias-to-record mapping; null remains an explicit missing-inventory blocker. Each record has exactly `material`, `provider` and `dataset_id`; use the actual local record identity. Supported providers are `local_csv` and `rii_sqlite`. No fabricated record IDs, silent defaults, automatic provider substitution or constant-index film replacement.
- `assumptions.execution_materials` explicitly describes the currently executable subset and its provenance. Inventory entries are execution support, not user constraints; there are no inventory entries in `constraint_source_ids`.
- The reviewed program-owned chemical vocabulary currently covers HfO2, SiO2, MgF2, TiO2, Al2O3, Ta2O5, Nb2O5 and ZrO2. These are recognized identities, not a claim that all eight records are available, cover the UV range or exhaust the user's allowed category. Canonical aliases must match their bound chemical identities. Unknown identities need capability review before execution; keep the broader permission intact and report the execution limitation.

The helper is `material_capabilities.validate_material_capabilities(constraints)`; ordinary route entry points remain unchanged. A well-formed inventory does not prove a record exists, has valid data or covers the numerical grid.

`route_consumer.prepare_tmm_inputs(instance, optical_media=..., wavelengths_nm=...)` reads category constraints preserved in the prepared instance. Every selected film alias must be in `execution_materials`, and `optical_media[alias]` must exactly match its declared `{material, provider, dataset_id}` record. The bundled registry resolves the actual local material identity or RII chemical book; alias/record substitution is rejected. Its `material_capability_binding` retains actual provider/dataset identity, source path and SHA256, book/page and record filepath. Its declared scope is **selected-layer record identity only**: `coverage_verified=false` and `numerical_preflight_required=true`. It does not verify every unused inventory entry, substrate coverage or spectral quality. Source hashes use a bounded 128-entry process-local cache keyed by resolved path and device/inode/size/mtime/ctime signature; changed signatures invalidate reuse and changes during hashing fail closed. This avoids rehashing an unchanged large RII source per candidate and adds no coverage claim.

After the existing engine imports are installed, the existing `tmm_engine.preflight.preflight_task("simulate", simulation_task_from_dict(output["tmm_task"]), registry=registry)` performs grid sampling. Preserve this result separately from the identity binding. A hash or a matching chemical book cannot substitute for full-grid n/k preflight, including partial-k failure. Inventory-wide availability/coverage checks before paid dispatch remain the original launcher's responsibility.

Before the first paid call, inspect the original local registry and record **identity, provider, dataset ID, source provenance/hash, n/k availability and coverage over every requested grid point**. Recheck fused-silica mapping as well as films; the explicit air model remains n=1, k=0. Preserve losses and disable extrapolation. Historical material labels or coverage examples in T032 do not replace this actual local preflight. Verify category chemistry independently of small/zero k. Preserve the preflight as a separate audit; no material database upload is needed.

An unavailable record or unsupported proposed identity is an honest execution limitation. Continue valid supported routes where possible; do not rewrite a rejected model material into another one. Poor numerical results or different scientific opinions are allowed. Correct factual reversals, fabricated observations and explicit constraint violations, rather than repairing prose just to obtain agreement.

## Cumulative admission, no new allowance

T036 is the continuation of the **same T032 case04**, not a new case or spending pool. Reconcile the original live ledger and durable dispatch history after T032 finishes and before every paid request. Include settled, pending, held and uncertain exposure, all earlier case04 history, and T032's case01/case05 spending. Reserve atomically and stop on unresolved dispatch/accounting uncertainty. Do not reset counters or erase the source hold based on elapsed time.

- Case04: **at most 4 actual HTTP attempts total across T032/T036**: task sheet, route generation, search revision, plus **one shared format-repair attempt** across the first two stages. No revision or transport retry. Failed/uncertain attempts count; reserve the revision slot.
- The original three-case T032 batch remains **at most 12 HTTP attempts**; T036 does not add a batch allowance.
- **CNY 8 cumulative per same original case; CNY 24 for the original T032 batch; CNY 220 project cumulative**, including all relevant history and liabilities. These are ceilings, not fresh balances.
- Case04 initial search plus refinement: **4096 forward design rows / 64 new certifications maximum**, split **2048 rows / 32 certifications per stage**, at most **2 certification workers**. Count failed and finite-difference evaluations. Report solver/channel/backprop counters separately.

The receipt reports no case04 dispatch. Verify that remains true before admitting it with `CampaignPlannerGuard(existing_audited_planner, case_id=..., audit_sink=...)`. This guard supports initial admission only; it is not a resumable ledger or actual HTTP counter. Do not recreate it after failure, skip guarded stages, or bypass persisted state. If additional paid-stage artifacts or uncertain dispatches are discovered, reconcile them and report the exact continuation-binding requirement before another call. No new client, provider, credential path, ledger database or unaudited planner.

## Execution after the fixed-SHA ready

1. Verify the ready's final implementation SHA, imports and hashes; retain the source receipt and exact original request. Run the final targeted offline checks and actual local material preflight. Confirm T032/T035 queue completion and live budget admission.
2. Inspect case04's existing current-campaign artifacts. Reuse any verified completed task sheet or retrieval artifacts at their valid stage; do not replan or duplicate retrieval merely because the implementation changed. The source receipt does not establish that either exists. For the recorded zero-dispatch state, the normal fresh planning path remains appropriate if no completed sheet exists. Unexpected prior paid-stage state must be reconciled as above, not silently replayed or skipped.
3. Use the existing original-provider audited planner and T032 protocol. If retrieval is still needed, perform the established bounded pass only once (per-query limit 10, 60-second retrieval budget); reuse valid current-campaign caches. Keep rate limits and missing literature explicit. Clearly labeled model-knowledge/hybrid routes remain allowed; sparse evidence does not prove scientific absence.
4. Build category constraints and the verified execution mapping, then call the existing injected route-generation entry point. Preserve full evidence separately. T034's selected active route output **`CASE/routes/evidence_items.json`** must be passed to downstream validation/materialization/search, not the full archive or compact source-reference prompt projection. Preserve raw routes and the separately filtered executable selection.
5. Execute one initial bounded search, one model revision based on real certified feedback, and one bounded refinement through the existing T032/T029 flow. Use the same recorded optical mappings, exact goals and physical constraints through revision/refinement. No T030 expansion/reflection experiment is added. Skip paid revision if no valid certified archive exists; invalid revision plans do not authorize another model call.
6. Stop after this cycle or an honest blocker. Do not rerun case01/case05, repeat the old T032 batch, add a scientific comparison or optimize until a preferred answer appears.

Existing entry points, with `CASE` an explicitly bound verified local path:

```text
python -m focused_research.search_stage search --task-sheet CASE/plan/task_sheet.json --evidence-file CASE/routes/evidence_items.json --constraints-file CASE/design_constraints.json --routes-file CASE/routes_for_search.json --optical-media CASE/optical_media.json --wavelengths CASE/wavelengths.json --cache-dir CASE/cache --max-forward-evaluations 2048 --max-new-evals 32 --max-workers 2 --backend torch --parameterization family --seed 42 --execute --output CASE/search
python -m focused_research.search_stage refine --previous-search CASE/search --revision-dir CASE/revision --cache-dir CASE/cache --max-forward-evaluations 2048 --max-new-evals 32 --max-workers 2 --backend torch --seed 43 --execute --output CASE/refined
```

The launcher must supply verified files, ledger admission and guarded model stages; these examples do not create prerequisites or authorize unaudited CLI model calls. Route generation and `search_stage.main(['revise', ...], planner=guard)` use the existing injected interfaces, with stage audits durably written before dispatch and in final cleanup. Execute only after the fixed-SHA ready, queue and live admission checks pass.

## Verification and receipt

The frozen socket-disabled full suite passed: **734 passed, 4 skipped, 6 subtests, 47.56 seconds, exit 0**; compileall and diff checks passed. Root also reports 129 passing targeted consumer/category/legacy checks and a successful actual bundled-SiO2 identity-binding smoke check. These are cloud regression/record-binding evidence, not original-executor inventory acceptance, full physical coverage or scientific performance. `OFFLINE_VALIDATION.json` records the results and actual frozen source-file hashes. No T036 production model call, physical search or certification was performed in preparing this packet.

Before local admission, run the targeted contract/consumer checks using the existing environment:

```text
python -m pytest -q focused_research/tests/test_material_capability_contract.py focused_research/tests/test_category_material_consumer.py --disable-socket
```

These tests use local fixtures and record/preflight checks; they do not replace actual case04 inventory-wide coverage verification. Do not reinstall dependencies or claim a local pass until observed. Final release binding comes from the ready message; actual local records and campaign results remain pending.

Return completed / partial / blocked with actual case04 HTTP/cost/numerical counters; original T032 batch and project live totals/liabilities; source and implementation hashes; original category permission versus actual execution subset; local record coverage/hash audit; raw/effective prompts and responses; active/full evidence mapping; accepted/rejected routes; certified spectra/metrics and parent-child findings. Distinguish improvement, tradeoff, regression and untested mechanism claims. For a blocker, state the exact action and target, verified reason, completed work and what remains pending/uncertain. Keep private headers, credentials, full ledger/material databases and caches out of the handoff.
