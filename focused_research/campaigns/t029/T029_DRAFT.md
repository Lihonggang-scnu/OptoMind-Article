# T029 three original-case research test — release instructions

Execute only after the accompanying ready message binds the reviewed implementation SHA. The T028 review found usable feedback decisions and remaining hypotheses; differences of opinion do not block this new-case test. Execution order: 09 silicon AR, 08 packaging UV, 02 QKD. Case10 is preserved solely as a no-fee blocked record. These are three different applications; do not substitute duplicate01/03 or02/07 to inflate coverage.

## Inputs and provenance

`INPUT_PROVENANCE.json` binds exact original question strings and files to Article commit `032bd651b32dfce075a046f2214248c5dcbb491a`, `replay_data/runs/<case_id>.json#summary.question`. Every FRESH_REQUEST.question was compared directly with git show at that commit. FRESH_QUESTION.txt has the same text plus one terminal newline. Use the JSON question field for program-owned verbatim original_question. No old champion structures, scores or answers are in this packet.

T029 builds on tested T028 implementation b6f641edea1fe124c98921251bc5f14a3dc53466. Use the final T029 SHA bound by C2L-R3-0043, in a new independent directory; record the local wrapper hash and imports before any paid call. Do not import an older F: planner by accident.

## Original physical goals and protocol choices

09: minimize average R separately in1260–1360 and1530–1565 nm; report T entering semi-infinite silicon. Air,0deg,unpolarized,Ta2O5/SiO2,≤12layers,20–400nm/layer are explicit. Report-only T must not silently become a third maximization objective. Propose wavelength-integral mean for the unspecified meaning of average, explicitly labeled evaluation protocol, not original wording.

08: minimize average T separately in355–365 and1030–1080 nm; maximize average T420–700nm. Air,0deg,unpolarized,fused silica,Ta2O5/SiO2,≤36layers,20–350nm/layer are explicit. The contextual355nm excitation is not a fourth point objective. Same disclosed integral-mean evaluation convention. Preserve transmission suppression, not silently replace with reflection maximization in potentially lossy material data.

02: maximize T1530–1565nm and R400–700nm; air,fused silica,HfO2/SiO2,≤30layers explicit. Parent selects0deg and equal-s/p unpolarized as new execution assumptions, not claimed original facts. Integral band means are a disclosed evaluation convention. No globally binding thickness bounds were recovered from compact original replay. Do NOT invent20–400nm or infer bounds from champion min/max. A historical route title mentions±15nm local refinement around its existing best, which is not a reusable cold-start bound. First look for the original local compiled task/bounds and retain source identity. If unavailable, preserve absence of user manufacturing bounds: model-proposed finite per-family variable domains are design hypotheses, explicitly labeled as such; if current pipeline requires an externally frozen global range, stop02 before dispatch and report that blocker. Do not silently invent a user constraint to satisfy a validator.

For all cases: no crosstalk/system-performance proxies. Keep all original goals, original condition spans, separate assumptions and unsupported requirements. Unspecified target thresholds or weights stay unspecified. A program-completed run is not performance acceptance.

## Zero-fee material preflight before each case's first model call

Verify the actual local database/CSV identity, dataset names, n/k coverage at all planned wavelengths, substrate mapping and extrapolation-disabled behavior; save a small report before dispatch. Historical metadata is only a locator:

- Ta2O5 Gao415 complex n/k350–1800nm; SiO2 Lemarchand411 complex n/k250–2500nm: covers08 and09.
- Si Pierce608 complex n/k103.3–2066nm: covers09 substrate. Report power entering semi-infinite substrate, not through-device transmission.
- QKD HfO2 existing local hfo2.csv reports200–25000nm n/k; inspect actual source/provenance and current range, do not claim metadata alone proves physical accuracy. SiO2/Lemarchand covers400–1565nm.
- Fused silica substrate must use a verified actual fused-silica record with disclosed loss convention; no arbitrary constant replacements. Air n=1,k=0 is the explicit air model.

Suggested finite sampling, recorded as numerical protocol:09 includes1260–1360 and1530–1565 at1nm plus any needed intervening points;08 355–1080 at1nm;02 400–1565 at1nm. Exact endpoints and interior points are required. A finite grid is not a continuous-spectrum or manufacturing guarantee. Data must cover every grid point, not merely target bands. Any source mismatch or extrapolation requirement blocks that case before fees; continue independent eligible cases.

## Money, attempts and numerical budgets

The accompanying ready message authorizes the allocation in BUDGET_PROPOSAL.json; without that ready message this document alone does not dispatch work. Each case≤CNY3 and≤4actual modelHTTP attempts; three cases≤CNY9. Standard calls: task sheet, route generation, search revision. Allow at most ONE explicit contract-format repair shared across task-sheet and route generation; search revise has no retry. Both generation functions presently permit their own repair, so the external audited wrapper MUST enforce the shared repair token, stage labels and aggregate4HTTP ceiling before every dispatch. Zero transport retries. Reserve the revision attempt rather than allowing two independent format repairs to consume it. Preserve malformed/incomplete originals.

Per case search and refine together≤4096forward design rows and≤64new certifications. Stage1≤2048rows/32certifications; stage2≤2048rows/32certifications;≤2certification processes. Failed evaluations and finite differences count. Keep channel rows/solver work/backprop counts separate. No budget reset, silent quota transfer or hidden retry.

T028 settled0.0548232 CNY; the last reported project cumulative is18.3087206 CNY, original15-CNY chain spent0.9461448 and20-CNY pool spent0.9794536. These snapshots require fresh reconciliation; T029 maximum9 CNY fits the last known chain remainder14.0538552 CNY. Before release and every dispatch, reconcile original ledgers' settled+pending+held+uncertain and enforce all intersecting limits atomically, including current cumulativeCNY120 (never stale140), original chain/subpool and per-case caps. Historical actual18.3087206 is not a live balance. Keys remain local. If enforcement cannot be proven, stop paid steps.

## Existing CLI / injected entry points

Use the existing audited local planner and original accounting wrapper, not bare shell planning: `run.main plan` would otherwise construct its own planner. These are actual current API/CLI shapes; paths in capitals must be replaced with verified paths by the local launcher.

1. Load focused_research/run.py first using the established local launcher (it installs the vendored import paths). Then:

```python
question = json.loads(Path('INPUT/FRESH_REQUEST.json').read_text(encoding='utf-8'))['question']
output = run.prepare_output(Path('CASE/plan'))
outcome = run.generate_plan(question, output, planner=existing_audited_planner)
```

The local planner may attach separately labeled parent-approved protocol assumptions to its request context, while retaining the exact question argument; save that request and require the returned analysis to distinguish these from explicit constraints. Do not manually write the model's scientific task sheet, routes or reasoning. Only continue if status is ok, original_question matches byte-for-byte, and goals/conditions are faithful. If assumption injection is not supported by the existing launcher, report the blocker rather than modifying original text.

2. Prefer verified relevant caches; otherwise one bounded existing free retrieval path:

```text
python focused_research/run.py retrieve --plan-file CASE/plan/query_plan.json --output CASE/retrieval --per-query-limit 10 --retrieval-seconds 60
```

No new paid retrieval/PDF service, download/parser campaign or installation. Save rate-limit/failure/incomplete states honestly. Zero hits or unavailable sources permit explicit model_knowledge routes; they are not evidence of no literature. Reuse only relevant identity-compatible evidence. Material archive copies must be bounded with real block selections, not truncated invented text.

3. Prepare constraints/media/grid from actual verified source meanings; bind constraint_source_ids to generated task-sheet IDs (never reuse RGB C-numbers). Layer-count/material/bounds restrictions must match the original or separately labeled approved protocol. Use an honest empty evidence payload accepted by the existing contract if no evidence exists.

```python
route_stage.main(['generate','--task-sheet','CASE/plan/task_sheet.json',
 '--evidence-file','CASE/evidence_items.json','--constraints-file','CASE/design_constraints.json',
 '--output','CASE/routes'], planner=existing_audited_planner)
```

4. Before any solver call, enforce original user thickness bounds as a launcher preflight: for09 every variable interval and fixed thickness must stay within20–400nm; for08 within20–350nm. The current design_constraints schema has no thickness-range field, so do not assume the route compiler alone enforces these original restrictions. Reject only violating routes with evidence; do not clip or rewrite model values. Other valid routes may proceed. For02 no user thickness range is invented; require finite positive per-family domains and explicitly call them model search hypotheses.

Only after ready executable routes and material preflight:

```text
python -m focused_research.search_stage search --task-sheet CASE/plan/task_sheet.json --evidence-file CASE/evidence_items.json --constraints-file CASE/design_constraints.json --routes-file CASE/routes_for_search.json --optical-media CASE/optical_media.json --wavelengths CASE/wavelengths.json --cache-dir CASE/cache --max-forward-evaluations 2048 --max-new-evals 32 --max-workers 2 --backend torch --parameterization family --seed 42 --execute --output CASE/search
```

All seeds/search failures or no certified archive: retain failures; skip paid revision and continue next case. Partial certification is usable without rerunning to exhaust limits.

```python
search_stage.main(['revise','--previous-search','CASE/search','--execute',
 '--output','CASE/revision'], planner=existing_audited_planner)
```

```text
python -m focused_research.search_stage refine --previous-search CASE/search --revision-dir CASE/revision --cache-dir CASE/cache --max-forward-evaluations 2048 --max-new-evals 32 --max-workers 2 --backend torch --seed 43 --execute --output CASE/refined
```

No valid revision plans: preserve raw response, stop that case without an extra model call. Sequential cases, no overlapping paid tasks; continue other eligible cases after local case failure, except an uncertain global ledger/guard failure blocks all paid work.

## Deliverable and stopping condition

Stop each case after one search→model decision→refinement cycle or honest blocker. Preserve seeds, certified incumbent/parent, child, spectra, certificates and raw prompts/responses. Show concrete observation→model hypothesis→chosen parent/variables→numerical result, with expectation direction checked against metric sense. Separate improvement, tradeoff, regression and unsupported explanation. Preserve good earlier designs. Return per-case status, actual attempts/cost/liabilities, numerical counters, source hashes and limitations; aggregate distinct application count. No claim of≥3worthy outcomes merely because3cases ran, no performance guarantee, no extra iteration to conceal failure. Root reviews results before any publication.

## T029 guard integration (reviewed offline; local integration required)

`focused_research/local_campaign_guard.py` supplies CampaignPlannerGuard and check_route_thickness_bounds. It does not replace the original money accounting, establish actual HTTP counts, create credentials or reset budgets. Admission must establish a genuinely new case and reconcile persisted original ledger state. Never automatically recreate the object after a crash/failure or restart a partially dispatched case; this initial-run adapter has no resume protocol.

```python
from focused_research.local_campaign_guard import CampaignPlannerGuard, check_route_thickness_bounds

def persist_guard_audit(snapshot):
    # Use the local launcher's existing durable JSON writer and task-local path.
    # It must persist or raise, before dispatch is allowed.
    existing_durable_json_writer(guard_audit_path, snapshot)

guard = CampaignPlannerGuard(existing_audited_planner, case_id=case_id,
                             audit_sink=persist_guard_audit)
guard.set_stage('task_sheet')
try:
    outcome = run.generate_plan(question, output, planner=guard)
finally:
    persist_guard_audit(guard.audit)
# Validate sheet and prerequisites, then:
guard.set_stage('route_generation')
try:
    route_stage.main(route_arguments, planner=guard)
finally:
    persist_guard_audit(guard.audit)
# Run existing full contract validation and additional original thickness check:
bounds_report = check_route_thickness_bounds(accepted_routes, case_id=case_id)
# Save report; exclude failing routes as explicitly rejected, without editing
# their raw contracts. Revalidate the selected accepted contract before search.
import copy
selected_routes = copy.deepcopy(accepted_routes)
allowed_indices = {r['route_index'] for r in bounds_report['route_results'] if r['accepted']}
selected_routes['routes'] = [r for i, r in enumerate(selected_routes['routes']) if i in allowed_indices]
# Persist this DERIVED selection as CASE/routes_for_search.json and keep the
# original routes/research_routes.json unchanged. If no routes remain, skip
# search/revision for this case and report the rejection evidence.
# Only after certified observations exist:
guard.set_stage('search_revision')
try:
    search_stage.main(revision_arguments, planner=guard)
finally:
    persist_guard_audit(guard.audit)
```

The guard injects separate execution_protocol into a deep copy of actual forwarded user_payload, retaining the original question and raw prompt unchanged. Its audit includes BOTH original and actual forwarded prompts and canonical hashes. Production upstream prompt_hash describes its own upstream prompt; it must not be presented as the actual post-injection request hash. The audit sink receives the actual forwarded snapshot before the underlying planner is called. Audit or delegate exceptions fail the guard closed, consuming one conservative dispatch_attempt; actual networkHTTP/cost are established only by the original transport ledger. A second sheet/route attempt requires explicit previous_response and validation_error and consumes the single shared repair token. Stage reset, skip and extra revision calls are rejected.

The pure thickness check verifies each route's fixed layer values and entire variable intervals against09[20,400] or08[20,350]. It supplies no QKD manufacturing range and does not replace full route-schema/instance validation. Valid routes can continue after individual failures; preserve every original rejected contract and reason. Local launcher must wire this guard explicitly; merely adding the module changes no existing pipeline execution behavior.


## 用户最新验收口径

允许模型提出不同观点、经验判断和大胆假设，不因与云端观点不一致或假设未实现而判失败。不要围绕“必须”“必要”等措辞反复修改或追加调用。优先修可核实的数据读反、虚构已发生结果、违反明确约束、程序与计划不一致。保存原文并标明哪些是观点、哪些是观测即可。性能退步与未兑现预期可以构成有效研究过程，不阻塞其他案例；不得将这种宽容扩展为放宽用户硬约束或伪造通过。

## 交接保持现状

按用户最新指令，原 Agent 继续执行，Drive 为主，GitHub 备用 codex/r3-handoff-relay/handoff/relay。候选代理暂不使用。C2L-R3-0042 本地优先协议已撤回；不要应用其路径优先级或切换收件流程。本任务编号 R3-T029，配套 ready 使用 C2L-R3-0043。
