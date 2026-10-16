# T037: repair Windows source hashing, then continue case04

Base: `408fef514d4ed156ffde03c868a26cea3aba9dfa`. This packet supersedes T036's cross-call source-hash cache, not its material-category contract or scientific task. Frozen cloud verification is complete; native Windows acceptance remains pending. Execution requires the accompanying final fixed-SHA ready and the local checks below. No new approval, campaign or spending pool is needed for this same authorized continuation.

## What actually failed

Fixed T036 result `a707fe691d6ce1a7cb8d8a5156d02baa4f87c908` reports **51 passed / 2 failed** in the actual Windows targeted check. Both local CSV and RII database cases changed bytes without changing size, restored mtime and retained the same ctime/signature. The LRU cache returned the old SHA256. `SOURCE_WINDOWS_REPRO.json` preserves the independent toy-file reproduction; it changed no production material. This is a content-provenance bug, not a failed UV design: **case04 still has 0 HTTP / 0 CNY / 0 production physics**, with no planning, retrieval, routes, search or revision run.

T036 nevertheless verified the category/inventory separation, selected-record binding and **seven film records on all 846 points from 255 to 1100 nm at 1 nm spacing**, with finite n/k and no extrapolation. Its failure log and successful preparation remain valid history; do not rewrite or discard them.

## Narrow repair and its cost

Remove cross-call LRU reuse of material source SHA256. Every new binding reads the actual current source bytes and computes their digest; a path/device/inode/size/mtime/ctime tuple is not a content identity. Retain signature comparison only to reject observable changes during hashing. Do not claim it detects every possible concurrent modification or provides an atomic file snapshot.

Keep exact alias/chemical/provider/dataset binding, source provenance, the separate full-grid coverage check and existing no-metal/category restrictions. A fresh hash does not prove optical validity. This correctness-first implementation rereads the approximately **12.5 MiB RII database per selected alias/binding**, including repeated preparations. It can increase I/O; no overall speedup or benchmark claim is made.

## Reuse verified local preparation

`INPUT_PROVENANCE.json` pins the actual T036 report, acceptance/failure evidence, inventory and original question, and lists the seven observed mappings and source hashes. Recheck their **actual current bytes**, record identity and requested-grid coverage before reuse. Do not redownload or rediscover cached material records merely because this repair has a new task ID.

- Local CSV films: HfO2 → `hfo2`, TiO2 → `tio2`, Al2O3 → `al2o3`.
- RII films: SiO2 → **411**, MgF2 → **204**, Ta2O5 → **414**, Nb2O5 → **397**. These are actual observed records, not illustrative IDs.
- Observed ZrO2 records **430/431** lacked complete required coverage and remain excluded from the current execution inventory. Do not enable extrapolation, silently substitute a record or describe this finite inventory as the user's full allowed set.
- Fused silica uses RII **835**, with measured/model-provided n and the existing provider's zero-k convention because that record has no k data. Keep that explicit substrate assumption; it is not a complete film n/k record. Air remains the explicit n=1, k=0 model. Preserve nonzero film losses.

Keep `allowed_materials=null`, category `ordinary_depositable_inorganic_dielectrics_no_metals`, and the separately disclosed `execution_materials` map. Category provenance points to actual task-sheet constraint IDs; inventory availability is an execution disclosure, not a new user whitelist. Consumer binding remains selected-record identity only; full-grid sampling remains separately required.

## Local zero-cost gate, then the same case04

1. Verify the fixed release, imported files and source hashes. Preserve the original Windows failure evidence. Run targeted and related offline checks in the existing Windows environment before any paid request. Both provider regressions must demonstrate a fresh digest after same-size changed bytes despite restored mtime and unchanged metadata signature; do not skip the assertion or weaken it to pass. Repeated unchanged bindings may have the same digest but must reread bytes.

   ```text
   python -m pytest -q focused_research/tests/test_material_capability_contract.py focused_research/tests/test_category_material_consumer.py focused_research/tests/test_research_routes.py focused_research/tests/test_route_consumer.py --disable-socket
   ```

   This gate permits fixture tests and local material sampling, with **0 model HTTP / 0 production search or certification / 0 CNY**. Do not reinstall dependencies or run a paid request to test the fix. If a platform/prerequisite or integrity check fails, return the exact blocker and retained evidence before dispatch.
2. Recheck the seven existing mappings and substrate using current-byte hashes and the actual grid. Keep selected identity-binding and coverage reports separate. Record any changed source honestly; do not accept an obsolete saved hash because the timestamps match.
3. Audit the original live ledger and durable same-case dispatch history. T036's historical snapshot was project **CNY 19.2288766, outstanding 0**; case01/case05 already used **6 HTTP / CNY 0.2644712** within T032. These are evidence, not current admission. Reconcile all settled/pending/held/uncertain exposure and reserve atomically before each request.
4. Resume **only original `e2e-04-solarblind-uv-triple`** through the existing T032/T036 original-Agent audited launcher. Do not rerun completed 01/05 or T035, interrupt other work or overlap paid jobs. Reuse valid current-campaign artifacts; never replan or repeat completed retrieval to reset state. The actual T036 receipt says those stages had not begun, so ordinary first planning/retrieval may still be needed. Cached material preflight is separate from literature retrieval.
5. Preserve the exact question: maximize mean T at **255–280 nm** and minimize mean T separately at **300–700** and **700–1100 nm**; air, fused silica, ≤30 layers, ordinary depositable inorganic dielectric films, no metals. Keep 0°/equal-s-p and wavelength-integral means as execution assumptions; keep positive finite family domains as model hypotheses, not invented global manufacturing bounds. Use the active selected **`CASE/routes/evidence_items.json`** for downstream search, with the full source archive separately preserved.
6. Run one bounded initial search, one model revision from real certified feedback, and one bounded refinement through the existing injected planner and search/refine interfaces. No T030 expansion/reflection branch is added. Stop after that cycle or an honest blocker; no extra attempt for a preferred scientific outcome. A legitimate tradeoff, regression or failed hypothesis is allowed and must be reported accurately.

## Same cumulative ceilings

- **At most 4 case04 HTTP attempts across T032/T036/T037 combined:** task sheet, route generation, revision, plus **one shared format repair** for the first two stages. Failed/uncertain attempts count; no transport or revision retry. Preserve the reserved revision slot. Original T032 batch remains ≤12 HTTP total.
- **CNY 8 cumulative for the same original case / CNY 24 for the original T032 batch / CNY 220 project**, including history and all liabilities. A new work order does not reset any counter or create headroom.
- **4096 forward design rows / 64 new certifications for case04 total**, split **2048/32 per initial/refinement stage**, at most two certification workers. Count failed and finite-difference evaluations.

`CampaignPlannerGuard` remains initial-admission-only, not a resume ledger. Admit a fresh guarded case only after the verified zero-dispatch history; if unexpected prior paid state exists, reconcile it instead of skipping stages or recreating a failed guard. Retain the original provider, credentials, ledger and durable before-dispatch/final audits. No new client or accounting database.

## Receipt

The frozen focused consumer/category/legacy/routes checks passed **131 tests in 1.93 seconds** with sockets disabled. The two complete-signature regressions were observed failing on the old implementation before passing with this repair. `test_source_hash_rereads_bytes_and_tracks_source_changes` retains `first != second` and equality to the actual byte hash; its read-count assertion changes because the cache is removed. `test_current_bytes_are_hashed_when_entire_source_signature_is_unchanged` covers both providers with the complete metadata signature fixed. `test_source_metadata_change_during_hash_is_rejected` retains the observable-change guard. The final frozen full suite passed **736 tests, with 4 skipped and 6 subtests, in 43.08 seconds** using `--disable-socket`. Native Windows acceptance is still pending; cloud regression success does not establish that local gate. `OFFLINE_VALIDATION.json` keeps these stages distinct. Return the actual tests/imports/hashes, unchanged-metadata reproduction outcome, verified record/coverage audit, per-stage HTTP/cost/numerical counters and cumulative original ledger exposure. Include raw prompts/responses and parent-child certified findings only if execution actually occurs. Label engineering acceptance, physical observations and untested mechanism explanations separately. For a blocker, name the action, target, observed reason, completed work and pending/uncertain state. Keep credentials, authentication headers and full private databases out of the result packet.
