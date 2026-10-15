# T031: recover three T029 task sheets and continue the first research cycle

## Release, ownership and stopping point

This is a continuation of the three original T029 cases, in order **09 silicon AR → 08 packaging UV → 02 space QKD**. Use the reviewed T031 implementation commit bound by the accompanying ready message. Preparation branch: `feature/t031-task-sheet-tolerance-20261015`, base `13b238f`. The final implementation SHA is supplied by that ready message after focused tests; the base SHA alone is not the T031 release.

The original local Agent remains the executor. Keep the existing delivery route: Drive primary, `codex/r3-handoff-relay` / `handoff/relay` backup. No replacement Agent, client, credential loader or money pool is introduced. Check the live queue before starting. If T030 is running, wait for its terminal result and reconcile costs before any T031 work that competes for local execution or reservations. If T030 has not started, let the original Agent respect its queue; do not interrupt or reorder it unilaterally. No paid or numerical overlap with T030.

Recover the saved second task-sheet responses offline, then reuse the reliable T029 retrieval → route generation → initial search → one model revision → one refinement path. **Do not regenerate task sheets, rerun the whole T029 job, or use T030's directional expansion/reflection path.** Stop each case after this single cycle, or at an honest blocker. No extra iteration to obtain a better score. Case10 remains excluded; no point-goal support is added here.

## What actually happened in T029

Fixed result commit: `35c60b9f016eee6ef53a1b266b48e6366e1ec52d`; result directory in that checkout: `handoff/R3-T029-THREE-CASES-20261016`.

All three cases stopped after two real task-sheet HTTP requests each. The second responses failed the old metadata contract: 09 and 08 had `mode=all` with descriptive tradeoff text; 02 had non-verbatim `known_data.original_span`. Case09 additionally labeled report-only transmission into silicon unsupported. **No retrieval, routes, physical search, certification or scientific revision ran.** The result does not establish physical infeasibility.

Historical settled T029 fees, to retain under the same original case identities:

- 09: 0.0361368 CNY, 2 actual HTTP requests
- 08: 0.0391456 CNY, 2 actual HTTP requests
- 02: 0.034176 CNY, 2 actual HTTP requests
- Batch: 0.1094584 CNY, 6 actual HTTP requests

These are source-result facts, not a current balance. Reconcile the original live ledger and any later activity before admission.

## Scope of the offline repair

`task_sheet_recovery.py` reads round 2 from the saved `planning.json`, verifies its exact byte SHA-256 and the original question's pinned UTF-8 hash, then prepares a new derived task sheet without a model call.

- `mode=all` may retain descriptive physical tradeoff commentary. This does not introduce an executable priority, scalar weight, threshold or permission to abandon an original goal.
- A structurally valid optional `known_data` row whose quote is not exact becomes explicitly unverified model background in `unknowns_assumptions`, with the source row and transformation recorded. It is not promoted to a user statement or accepted physical truth. No invented exact quotation is substituted.
- Indirect `research_question_ids` coverage is advisory: optional or partial links are retained with an audit warning rather than forcing every query goal to be covered again through those indirect links. This clears 09 Q5 and 08 Q3 without changing query strings, direct subgoal coverage or referenced-ID validity; linked questions/mechanisms must still share a query subgoal. The warning is not proof of retrieval relevance.
- For this exact source-pinned 09 request only, reporting existing T spectra into the semi-infinite silicon exit medium moves from unsupported requirements to a report-only application intent. It is neither a third optimization goal nor through-device transmission. Other unsupported requirements remain blocking.
- Original subgoals, executable metric values, explicit hard constraints and query strings are unchanged. Bad goal/constraint provenance and unsupported metrics still fail. This repair is not a way to relax scientific constraints.

Keep original `planning.json` bytes and raw responses intact. The new `raw_task_sheet.json` is a parsed copy of the selected analysis; the byte-original response remains in the source planning artifact. Preserve `normalization_audit.json`, effective `task_sheet.json`, `query_plan.json`, `recovery.json` and `manifest.json`. Recovery readiness means the contract can continue, not that the science passed.

## Budgets: cumulative case limits, no new pool

The latest C2L-R3-0044 budget basis applies to this authorized case activity: **8 CNY cumulative per original case, 24 CNY cumulative for these three cases, 220 CNY cumulative project cap**. These totals include each case's T029 spend, every continuation attempt/retry and all applicable settled, pending, held or uncertain exposure. They are not an additional 8/24 CNY allowance. The old 120 project and 15/20 task-pool caps are superseded within this authorized scope; keep their original ledger history and ownership rather than resetting or duplicating pools. The 3/9 amounts in the original T029 preparation packet are historical too.

Before each dispatch, atomically reconcile and reserve in the **original** ledger against every applicable current cap. Any unresolved request outcome, unaccounted concurrent reservation or inability to enforce the cumulative limits blocks dependent paid work. Do not estimate spend solely from guard method counts. No transfer of unused allowance from a blocked case.

T031 permits at most **3 new actual model HTTP attempts per case**: route generation, at most one explicit route contract-format repair, and one search revision. Preserve the revision allowance. Zero transport retries, timeout resends or fallback calls. Search revision has no repair retry. Prior T029 requests stay in the history: the maximum history is 2 old + 3 new requests per case, not a reset to zero and not another four-request T029 sequence. Unused attempts are not a target to exhaust.

Per case, initial search plus refinement together use at most **4096 forward design rows and 64 new certifications**:

- Initial search: at most 2048 rows / 32 new certifications
- Refinement: at most 2048 rows / 32 new certifications
- At most 2 certification workers; `backend=torch`

Count failed evaluations and finite-difference work. Keep design rows, channel/solver work and backward counts separate. No silent reset or quota transfer. Continue eligible independent cases after a case-local failure; a global ledger or uncertain-transport problem blocks all dependent paid work.

## Before any paid continuation

Preparation evidence is recorded in `OFFLINE_REPLAY.json` and `OFFLINE_VALIDATION.json` beside this handoff. All three saved second responses recovered to ready offline, with zero new model calls; this establishes recovery behavior only. Use the recorded test scope/results as reported, and still verify the exact released checkout and original local accounting before paid execution.

1. Verify the released implementation SHA, actual imported module paths/hashes, fixed T029 source commit, original questions and the planning byte hashes below. Use a new independent T031 output tree. Do not normalize source line endings or reserialize the saved planning files before hashing.
2. Run focused offline checks, not a whole historical campaign. From the implementation checkout:

   ```text
   python -m pytest -q focused_research/tests/test_task_sheet_tolerance.py focused_research/tests/test_task_sheet_recovery.py focused_research/tests/test_native_question.py focused_research/tests/test_local_campaign_guard.py
   ```

   Record actual results; do not describe this subset as a full-suite pass. Then run all three real saved-response recoveries below, also without model, retrieval or physics calls.
3. Verify recovered manifests, original questions, unchanged subgoals/metrics and explicit constraints, and readable adapter plans. Preserve meaningful model opinions as opinions; do not gate on agreement with cloud commentary or on stylistic wording.
4. Recheck T029's actual local material preflight/source identities and full grid coverage. Reuse the verified source choices and local assets, not old numerical answers. A material mismatch or needed extrapolation blocks that case before fees.
5. Reconcile/reserve the original money ledger and verify the injected original transport has retries disabled. The recovered guard is an admission adapter, not the transport or ledger.

### Exact source byte hashes

All paths here are relative to the fixed T029 result directory. Original question hashes and request-file hashes also remain in `focused_research/campaigns/t029/INPUT_PROVENANCE.json`.

| Case | Saved planning path | SHA-256 of file bytes |
|---|---|---|
| 09 | `cases/e2e-09-si-photonics-ar-dualband/plan/planning.json` | `e6e3dff45597fc5c05d1dcbce5719130202e1ffc441f688af2a36d627c09e57e` |
| 08 | `cases/e2e-08-advanced-packaging-uv/plan/planning.json` | `00ebda66a45873229b0b80f754c7fcf1c745ff8c2dfbb7f95e860af494c8aaaf` |
| 02 | `cases/e2e-02-space-qkd-cband/plan/planning.json` | `1f6f22289fdb0fafb14bf519b1bd350cb0a980637aef72e36692220b773adfc7` |

`T029_RESULT` and `T031_OUT` below are explicit placeholders for verified local paths, not assumed directory names. Run from the released implementation checkout. Every recovery output directory must be new. These are the current CLI arguments; CLI recovery selects round 2.

```text
python -m focused_research.task_sheet_recovery --case-id e2e-09-si-photonics-ar-dualband --request T029_RESULT/original_inputs/e2e-09-si-photonics-ar-dualband/FRESH_REQUEST.json --planning T029_RESULT/cases/e2e-09-si-photonics-ar-dualband/plan/planning.json --expected-sha256 e6e3dff45597fc5c05d1dcbce5719130202e1ffc441f688af2a36d627c09e57e --output T031_OUT/e2e-09-si-photonics-ar-dualband/recovered
python -m focused_research.task_sheet_recovery --case-id e2e-08-advanced-packaging-uv --request T029_RESULT/original_inputs/e2e-08-advanced-packaging-uv/FRESH_REQUEST.json --planning T029_RESULT/cases/e2e-08-advanced-packaging-uv/plan/planning.json --expected-sha256 00ebda66a45873229b0b80f754c7fcf1c745ff8c2dfbb7f95e860af494c8aaaf --output T031_OUT/e2e-08-advanced-packaging-uv/recovered
python -m focused_research.task_sheet_recovery --case-id e2e-02-space-qkd-cband --request T029_RESULT/original_inputs/e2e-02-space-qkd-cband/FRESH_REQUEST.json --planning T029_RESULT/cases/e2e-02-space-qkd-cband/plan/planning.json --expected-sha256 1f6f22289fdb0fafb14bf519b1bd350cb0a980637aef72e36692220b773adfc7 --output T031_OUT/e2e-02-space-qkd-cband/recovered
```

Do not obtain the expected hash from a changed local file to make it pass. A mismatch requires locating the verified original bytes or reporting the blocker, not a paid regeneration. The recovery manifest uses canonical JSON object hashes for its outputs; these differ in meaning from the byte hash of the source `planning.json`.

## Original physical meaning and material prerequisites

Keep the original question from `FRESH_REQUEST.json['question']` verbatim; do not append protocol assumptions to it. `FRESH_QUESTION.txt` has a terminal newline and is not the program-owned question source.

- **09:** minimize mean R independently in 1260–1360 and 1530–1565 nm; report T entering semi-infinite silicon. Air, 0° incidence, unpolarized, Ta2O5/SiO2, at most 12 layers, 20–400 nm per layer. No transmission-maximization objective.
- **08:** minimize mean T independently in 355–365 and 1030–1080 nm; maximize mean T in 420–700 nm. Air, 0°, unpolarized, fused silica, Ta2O5/SiO2, at most 36 layers, 20–350 nm per layer. Contextual excitation near 355 nm is not an additional point goal. Do not replace T suppression with R maximization in potentially absorbing data.
- **02:** maximize mean T in 1530–1565 nm and mean R in 400–700 nm. Air, fused silica, HfO2/SiO2, at most 30 layers. 0° incidence and equal-s/p unpolarized light are separately labeled execution assumptions. No recovered global user thickness bounds exist. Require finite positive model-proposed family domains and label them design hypotheses; never invent a user manufacturing limit or infer one from an old champion.

Wavelength-integral band means are the disclosed evaluation protocol for unspecified “average,” not recovered user wording. Keep all target bands and exact endpoints, separate assumptions and unsupported requirements; leave unspecified thresholds and weights unspecified. No crosstalk/system-performance proxies.

Reuse T029's verified actual material selections: Ta2O5 Gao415, SiO2 Lemarchand411, silicon Pierce608, fused-silica835 and the existing local HfO2 source. The prior material check reported full grid coverage for 09:1260–1565 nm, 08:355–1080 nm, 02:400–1565 nm; check the actual local identity and coverage again. Fused-silica835 has no k series: its k=0 is the disclosed provider convention, not a measured zero-loss claim. Keep source/provenance, no extrapolation and no arbitrary replacement constants. Air n=1, k=0 is the air model. Preserve T029's 1 nm finite sampling including exact band endpoints; a finite grid is not a continuous-spectrum/manufacturing guarantee.

## Existing retrieval and evidence path

Use a verified relevant cache where available, otherwise the same bounded free retrieval path as T029. `CASE` below is a placeholder for one new T031 case directory containing `recovered/`.

```text
python focused_research/run.py retrieve --plan-file CASE/recovered/query_plan.json --output CASE/retrieval --per-query-limit 10 --retrieval-seconds 60
```

Do not call `run.main plan` or `run.generate_plan`; recovery already supplies the planning artifacts. No new paid retrieval/PDF service, parser campaign or installation. Save zero-hit, rate-limited, failed and incomplete states honestly. Empty evidence permits explicitly labeled model-knowledge routes; it does not prove no literature exists. Use the existing evidence contract and material archive logic, with bounded real source blocks and identity-compatible evidence.

Prepare `CASE/evidence_items.json`, `CASE/design_constraints.json`, `CASE/optical_media.json` and `CASE/wavelengths.json` through the existing T029 local preparation flow. These are prerequisite files, not files automatically created by the commands shown here. Bind constraint source IDs to this recovered task sheet, not RGB identifiers. Do not manually author the model's scientific routes or reasoning.

## Recovered guard integration: actual API

The local launcher must explicitly inject `RecoveredCampaignPlannerGuard`. Do not use the initial three-stage `CampaignPlannerGuard`: it requires a new task-sheet dispatch. Merely importing the new class does not activate accounting or continuation.

Before construction, verify all files in the recovery manifest with `search_stage.digest(search_stage.load(file))`; verify `recovery.json` against the pinned source planning bytes and selected round-2 response, the original question, effective sheet and adapter. In particular, check `task_sheet_sha256 == digest(sheet)`, matching case ID, `source_round == 2`, the fixed source commit, `new_model_calls == 0` and `physics_executed == False`. Keep the same exact recovery binding in the durable audit. The constructor only checks the matching case ID, canonical sheet digest and JSON-shaped binding; it does not independently read or authenticate source files.

The following is launcher integration, not a standalone script. `existing_audited_planner`, `existing_durable_json_writer`, `guard_audit_path`, `case_id` and local paths must come from the original audited launcher. Its writer must durably persist or raise. First load `focused_research.run` through the established launcher so its vendored import paths are installed; record the resolved source paths.

```python
from pathlib import Path
from focused_research import run  # Established import setup; do not invoke plan.
from focused_research import route_stage, search_stage
from focused_research.local_campaign_guard import (
    RecoveredCampaignPlannerGuard, check_route_thickness_bounds,
)

case_dir = Path('CASE')  # Replace with this admitted case's real new directory.
binding = search_stage.load(case_dir / 'recovered/recovery.json')
# Source/manifest checks and original-ledger admission/reservation happen first.
def persist_guard_audit(snapshot):
    existing_durable_json_writer(guard_audit_path, snapshot)

guard = RecoveredCampaignPlannerGuard(
    existing_audited_planner, case_id=case_id,
    audit_sink=persist_guard_audit, recovered_task_sheet_binding=binding,
)
guard.set_stage('route_generation')
try:
    route_status = route_stage.main([
        'generate', '--task-sheet', str(case_dir / 'recovered/task_sheet.json'),
        '--evidence-file', str(case_dir / 'evidence_items.json'),
        '--constraints-file', str(case_dir / 'design_constraints.json'),
        '--output', str(case_dir / 'routes'),
    ], planner=guard)
finally:
    persist_guard_audit(guard.audit)
# Inspect status, generation_outcome, candidate_report and accepted routes.
# Do not proceed blindly when route_status is nonzero or no ready route remains.
```

The guard allows route generation plus one explicit contract repair, then one search revision. Repair requires the recorded previous response and validation error; it is not an extra science iteration. Stage repeats/resets/skips and extra calls are rejected. The guard preserves both original and actual forwarded prompts, their hashes and the recovery binding before delegation; actual HTTP/cost facts remain in the original transport ledger. Do not report an upstream prompt hash as the final post-injection request hash. Audit/delegate exceptions fail closed and consume a conservative dispatch attempt. Persist the final audit in `finally`; do not recreate the object after a failure or crash to reset allowance. Reconcile before any separately authorized recovery from an interrupted continuation.

### Route acceptance and original thickness limits

Use the existing candidate compiler/contract validation and preserve raw proposals and rejection reasons. On accepted routes, call `check_route_thickness_bounds(accepted_routes, case_id=case_id)` and save its report. Every fixed layer value and entire variable interval must lie within 09:[20,400] or 08:[20,350] nm. The design-constraints schema alone does not enforce these original ranges. The helper does not supply a QKD global bound or replace full validation.

Create a derived `CASE/routes_for_search.json` containing only accepted, executable routes that pass the check; preserve the original `routes/research_routes.raw.json`, accepted contract and candidate reports. Do not clip or rewrite model values. Revalidate the derived selection using the existing contract/compiler. Other valid routes may proceed after individual rejection; if none remain, stop the case without search or paid revision. No route-count quota and no wording/metadata polish loop.

## One initial search, one decision, one refinement

Only after material, route, grid and budget prerequisites pass:

```text
python -m focused_research.search_stage search --task-sheet CASE/recovered/task_sheet.json --evidence-file CASE/evidence_items.json --constraints-file CASE/design_constraints.json --routes-file CASE/routes_for_search.json --optical-media CASE/optical_media.json --wavelengths CASE/wavelengths.json --cache-dir CASE/cache --max-forward-evaluations 2048 --max-new-evals 32 --max-workers 2 --backend torch --parameterization family --seed 42 --execute --output CASE/search
```

Read the real summary, failure records and certified archive. Partial certification may suffice; do not rerun to exhaust limits. If all seeds/searches fail or there is no certified observation, retain the failure and skip paid revision. For a valid saved search, `search_stage.read_search` verifies its bindings. Reconcile/settle earlier calls, then reserve the single revision in the original ledger before this injected call using the **same live guard**:

```python
search_stage.read_search(case_dir / 'search')
guard.set_stage('search_revision')
try:
    revision_status = search_stage.main([
        'revise', '--previous-search', str(case_dir / 'search'), '--execute',
        '--output', str(case_dir / 'revision'),
    ], planner=guard)
finally:
    persist_guard_audit(guard.audit)
# Read the saved outcome and plans; no additional model call on failure.
```

If no valid refinement plans result, preserve the raw response and stop the case. Otherwise run the existing baseline refinement, retaining prior good designs and binding revision to its exact parent search:

```text
python -m focused_research.search_stage refine --previous-search CASE/search --revision-dir CASE/revision --cache-dir CASE/cache --max-forward-evaluations 2048 --max-new-evals 32 --max-workers 2 --backend torch --seed 43 --execute --output CASE/refined
```

Then stop. No `directional_stage`, branch expansion, branch reflection, second search revision, new task sheet or whole-campaign replay in this continuation.

## Return evidence and interpretation

Report each case as completed / partial / blocked, separating contract/engineering success, scientific reasoning and actual objective performance. A plausible opinion, disagreement, bold hypothesis, tradeoff, regression or failed scientific prediction is allowed. Do not spend extra calls debating words such as “must” or “necessary.” Serious observable factual misreadings, invented completed results, incorrect metric direction, hard-constraint violations and plan/execution mismatches must be reported explicitly; tolerance is not permission to hide them.

Provide a compact actual chain: observation → model hypothesis → chosen parent/variables → numerical outcome. Preserve original goals and full objective vectors; distinguish certified results, unverified candidates, cached results and new evidence. Do not manufacture a common optimum from different designs or claim three successful applications merely because three cases were attempted. The 09 report must include transmission entering silicon where certified spectra support it, with the report-only meaning retained.

Return the released implementation SHA, original source/question/planning hashes, recovery manifests and audit, source/material/grid identities, actual new and historical HTTP counts, tokens, settled costs and pending/held/uncertain exposure, original-ledger readback summary, numerical counters, accepted/rejected routes, representative real parent/child spectra and certificates, and full local archive references. Keep failed responses and earlier good structures. Report remaining limits and blockers plainly.

Upload only the compact authorized result packet through the established route and verify readback before claiming delivery complete. Preserve complete originals locally. Do not upload credentials, API request headers/raw keys, ledger databases, environment files, material databases, raw PDFs or weights. This handoff contains no new authentication or accounting setup.
