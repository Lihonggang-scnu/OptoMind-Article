# T042: original LWIR ZnS/ZnSe-on-Ge case, one bounded end-to-end cycle

Base: `84b050d53e5d07207c032f7d1fc4c150d47dcf1f`. Use the final SHA in root's accompanying ready. **Frozen cloud verification: 963 passed, 4 skipped, 6 subtests, 44.47 seconds**, sockets disabled; compileall/diff passed. The four cloud skips are Torch-related. Original-local preflight and this scientific experiment remain pending.

The queue prerequisite is satisfied: T041 returned fixed result `0e44adee12dabb32a8848c153d99049c81fc877b`, with 114 local tests, ten candidate-ready routes, 18 legal instances, two isolated instances and zero fee. T042 runs serially, without reopening or replacing T040/T041. Use the original local Agent, audited planner/transport, existing Qwen3.5plus/3.7flash configuration and original ledgers. Record the actual model chosen; no new client, credentials, provider or accounting pool.

## Original question and scientific contract

The exact `032bd651b32dfce075a046f2214248c5dcbb491a` source question is:

> 我负责制冷型碲镉汞红外焦平面探测器的长波红外窗口膜层研发。探测器工作在 8000-11000 nm 长波红外大气窗口，需要该波段尽可能高的透过率，同时尽可能抑制 3000-5000 nm 中波红外的热背景辐射，以降低焦平面的热负载。请在空气入射、锗（Ge）基底条件下，仅使用 ZnS 和 ZnSe 设计一个平面多层介质膜，膜系总层数不超过 16 层。

Use `inputs/e2e-12-lwir-ge-window/FRESH_REQUEST.json` directly. It contains only `run_id` and `question`; no historical winner, recipe, thickness, score, formula or model reasoning enters this cold start. The native question UTF-8 SHA256 is `9471a83b6c3ab5490fcb8f3ca8c414aba698f961a69b2ea05b3376df5ac66aea`. `FRESH_QUESTION.txt` adds one terminal LF and has a different file hash. `INPUT_PROVENANCE.json` pins both.

Keep two separate objectives: **maximize mean T8000–11000 nm** and **minimize mean T3000–5000 nm**. Keep air, Ge substrate, only ZnS/ZnSe films, planar multilayer and ≤16 layers. Do not replace suppression with reflection/absorption, add heat-power/blackbody objectives, combine goals into a weighted score, or require a literature/historical benchmark to pass.

Separately disclose execution assumptions: 0° incidence, equal-s/p unpolarized light, wavelength-integral band means, and an initial **3000–11000 nm / 10 nm / 801-point** grid including both bands' endpoints and the intervening gap. The grid is an initial finite numerical resolution, not a convergence or continuous-spectrum guarantee. Keep diagnostic s/p channels. The question supplies no global thickness bounds, temperature, finite Ge thickness or back-surface geometry. Require finite positive model-proposed per-family windows; do not copy UV manufacturing limits or infer a temperature from “cooled”.

**Boundary:** Ge is a semi-infinite exit medium. T is flux entering Ge, not transmission through a complete finite-thickness window. Ge's selected record has no measured k; retain the existing provider-zero convention as a disclosed model assumption, not a claim of physically lossless Ge. No invented absorption data, substrate replacement or added back-surface model.

## 1. Zero-fee source and full-grid preflight before any model call

The packet supplies ready-to-read `OPTICAL_MEDIA.json` and `WAVELENGTHS_NM.json`. The latter is a plain JSON array of 801 nm values, not an object. The former uses existing schema:

```json
{
  "air": {"constant_n": 1.0, "constant_k": 0.0},
  "Ge": {"material": "Ge", "provider": "rii_sqlite", "dataset_id": 221},
  "ZnS": {"material": "ZnS", "provider": "rii_sqlite", "dataset_id": 531},
  "ZnSe": {"material": "ZnSe", "provider": "rii_sqlite", "dataset_id": 562}
}
```

Read-only cloud reference: RII source SHA256 `e4c1ec34cc4f765a8400ff87bb7dee8705b6da2cdb11a229cc57ad9bdd274989`; ZnS531 Querry has n/k over 0.22–166.6667 µm, ZnSe562 Querry n/k over 0.5–21.7391 µm, Ge221 Burnett n over 2.25–14 µm and no k. The existing registry sampled all 801 points finitely with zero extrapolation in cloud. **Recheck actual original-local bytes, record identity and every grid point.** A matching name or metadata range is insufficient. On a missing/mismatched source or coverage gap, stop before paying; do not change the question, silently switch records, extrapolate or fabricate k.

There is no dedicated focused material-preflight CLI. Use the actual existing API below in the local environment with `veritmm` on `PYTHONPATH`. `INPUT`, `RII_DB` and `PREFLIGHT_RECEIPT` are verified local path bindings, not new APIs. The receipt path must be new. This samples optical constants only, with no design solver/certification:

```python
import hashlib, json
from dataclasses import asdict
from pathlib import Path
import numpy as np
from tmm_engine.material_registry import MaterialRegistry

grid_nm = json.loads((Path(INPUT) / 'WAVELENGTHS_NM.json').read_text())
source = Path(RII_DB)
before = hashlib.sha256(source.read_bytes()).hexdigest()
registry = MaterialRegistry(rii_db_path=source)
rows = {}
for name, dataset_id in [('ZnS', 531), ('ZnSe', 562), ('Ge', 221)]:
    ref = registry.resolve(name, provider='rii_sqlite', dataset_id=dataset_id)
    assert ref.book.casefold() == name.casefold()
    assert str(ref.dataset_id) == str(dataset_id)
    sample = registry.sample(ref, wavelengths_um=np.asarray(grid_nm) / 1000,
                             allow_extrapolation=False)
    assert len(sample.n) == len(sample.k) == 801
    assert np.isfinite(sample.n).all() and np.isfinite(sample.k).all()
    assert not sample.extrapolated_mask.any()
    assert sample.provenance['has_n'] is True
    assert sample.provenance['has_k'] is (name != 'Ge')
    rows[name] = {'ref': asdict(ref), 'provenance': sample.provenance,
                  'warnings': sample.warnings,
                  'n': sample.n.tolist(), 'k': sample.k.tolist(),
                  'extrapolated_mask': sample.extrapolated_mask.tolist()}
assert hashlib.sha256(source.read_bytes()).hexdigest() == before
with Path(PREFLIGHT_RECEIPT).open('x', encoding='utf-8') as stream:
    json.dump({'source_sha256': before, 'wavelengths_nm': grid_nm,
               'materials': rows, 'Ge_missing_k': 'provider zero, not measured losslessness',
               'new_model_http': 0, 'new_solver_calls': 0, 'new_certifications': 0},
              stream, ensure_ascii=False, indent=2, allow_nan=False)
```

Compare the actual source hash against the pinned reference and preserve failures/command logs. Air n=1,k=0 is the explicit air model. For each later materialized candidate, keep `route_consumer.prepare_tmm_inputs` and existing `tmm_engine.preflight.preflight_task('simulate', simulation_task_from_dict(envelope['tmm_task']), registry=registry)` checks before execution. This preflight samples the full declared grid and does not run a TMM solve. Do not substitute endpoint-only checks.

## 2. Admit the whole chain, then generate the task sheet once

Reconcile original settled/pending/held/uncertain exposure and actual history before admission and every model call. Last verified T041 project snapshot was **CNY 19.7726754, held 0**; it is not a live balance or proof that all current pending/uncertain amounts are zero. No R3-added call for case12 is known at drafting; check rather than assert zero when history is missing. Historical showcase outputs are not new generated results or authoritative ledger receipts.

T042 allows **at most four actual model HTTP attempts**: task sheet, candidate generation, one search revision, plus **one shared format-repair slot** across planning/candidates. Revision and transport have no retry. Count failures/uncertain dispatches; preserve the revision slot. Use one existing `CampaignPlannerGuard` throughout, never reconstruct it after a failure/reset. Existing stage helpers may request a format repair; the guard prevents a second shared repair. Optional prose, weak science or automatically normalized metadata alone should not spend a paid repair.

Load `focused_research.run` first to establish its existing vendored import paths. The named planner/audit writer below are original-launcher bindings, not replacement core code:

```python
from pathlib import Path
import json
from focused_research import run
from focused_research import route_stage, search_stage
from focused_research.local_campaign_guard import CampaignPlannerGuard, check_route_thickness_bounds

request = json.loads((Path(INPUT) / 'FRESH_REQUEST.json').read_text(encoding='utf-8'))
guard = CampaignPlannerGuard(existing_audited_planner,
    case_id=request['run_id'], audit_sink=persist_guard_audit)
guard.set_stage('task_sheet')
try:
    outcome = run.generate_plan(request['question'],
        run.prepare_output(Path(CASE) / 'plan'), planner=guard)
finally:
    persist_guard_audit(guard.audit)
```

Do not use bare shell `run.py plan`, which would construct its own planner. Continue only with a valid sheet preserving the exact question, both metrics and all explicit constraints; retain raw responses and normalization audit. The guard adds separate case12 protocol assumptions without rewriting the question. No manually authored substitute model sheet or old saved answer.

## 3. Bounded retrieval and the existing lightweight fulltext lane

```text
python focused_research/run.py retrieve --plan-file CASE/plan/query_plan.json --output CASE/retrieval --per-query-limit 10 --retrieval-seconds 60
```

Use current identity-compatible provider/parser caches where available and do not rerun a completed stage. S2 retains its existing **15 reconnects per query after the initial attempt**, Retry-After/exponential cooldown, checkpoints and honest exhausted/rate-limited states. Cooldown is outside the 60-second active-work budget and may lengthen wall time. These free retrieval attempts are recorded separately from the four model HTTP attempts. Do not stack another retry loop or hide provider failures as a scientific no-results finding.

Retain the existing lightweight fulltext workflow: pinned Review `d2deb4dbe18b23837d949822ad731afbaba9414b`, existing local interpreter/caches, at most five selected papers, 180 seconds per paper. One existing invocation is:

```text
python -m focused_research.fulltext_stage --records VERIFIED_CANDIDATE_RECORDS --queries VERIFIED_FULLTEXT_QUERIES --output-root CASE/fulltext --review-root EXISTING_REVIEW_ROOT --review-python EXISTING_REVIEW_PYTHON --max-papers 5 --deadline-seconds 180
```

`VERIFIED_CANDIDATE_RECORDS` is the existing lane's record list (or `{"records": [...]}`); `VERIFIED_FULLTEXT_QUERIES` is its query list (or `{"queries": [...]}`), derived from the current task, not historical solved designs. Reuse the already configured basic parser bindings only if the established lane requires them. Do not add fine PDF/formula/figure enhancement, paid extraction, new installs/downloads or a new PDF campaign. Missing setup/body text stays explicit; metadata is not full text. Useful model-knowledge or hybrid hypotheses remain possible without successful fulltext.

Merge successful verified exports with S2 using the existing offline bridge; repeat `--export` only for actual exports, at most five, or omit it when none exists:

```text
python -m focused_research.material_evidence --s2 CASE/retrieval/evidence_items.json --export VERIFIED_REVIEW_EXPORT --output-dir CASE/evidence
```

Use `CASE/evidence/combined_evidence_items.json` for route input. If no S2 retrieval file was produced, retain the failure and use the existing honest empty evidence schema through the established launcher; do not fabricate retrieved support. Preserve full source materials separately. Later route-stage whole-item selection decides the active evidence; skipped material must not be described as read.

## 4. Bind constraints to this newly generated sheet, then generate candidates

There is no automatic public natural-language-to-constraints API. This is existing launcher configuration, not a core-code repair. Read `CASE/plan/task_sheet.json` → `explicit_constraints` rows (`id`, `constraint`, `original_span`). For each of **ZnS/ZnSe only**, **≤16 layers**, **Ge substrate**, and **air incidence**, record the actual supporting row IDs and exact original spans in a mapping audit. One genuine row may support multiple fields. Do not assume IDs such as C1/C4, reuse RGB/UV IDs or treat a protocol assumption as an original quotation. If a required explicit fact is absent/misread, retain that planning failure; do not invent a source ID to proceed.

Write `CASE/design_constraints.json` with exactly:

- `allowed_materials`: `["ZnS", "ZnSe"]`
- `max_layers`: `16`
- `substrate`: `"Ge"`
- `incident_medium`: `"air"`
- `constraint_source_ids`: an object with those four keys, each holding the verified actual ID list
- `assumptions`: `{}` for these four explicitly stated constraints

Keep the material record mappings in supplied `OPTICAL_MEDIA.json`, not a category-mode extension. Do not add a global thickness-bound field. Validate the binding without a model request via the existing entry point:

```text
python focused_research/route_stage.py prompt --task-sheet CASE/plan/task_sheet.json --evidence-file CASE/evidence/combined_evidence_items.json --constraints-file CASE/design_constraints.json --output CASE/route_prompt_check
```

Then, in the same original guarded launcher:

```python
guard.set_stage('route_generation')
try:
    route_status = route_stage.main([
        'generate', '--task-sheet', str(Path(CASE) / 'plan/task_sheet.json'),
        '--evidence-file', str(Path(CASE) / 'evidence/combined_evidence_items.json'),
        '--constraints-file', str(Path(CASE) / 'design_constraints.json'),
        '--output', str(Path(CASE) / 'routes'),
    ], planner=guard)
finally:
    persist_guard_audit(guard.audit)
```

Around ten useful routes is encouragement, not a minimum, quota or upper count. Model-memory/weak-evidence hypotheses are allowed when labeled honestly. T041's free-text open requirements remain warnings for otherwise legal candidates; genuine structure/material/metric/bounds defects still block or isolate the affected instance. Scientific hypotheses may fail without invalidating the process. Preserve zero-change/reference-only and missing-comparison audits; do not pretend a name changed the actual layer stack.

Check the saved candidates with `check_route_thickness_bounds(..., case_id=request['run_id'])`; case12 has no global user range, but positive finite family windows/fixed values still apply. Keep all original raw routes. Use the fixed candidate compiler's saved raw `CASE/routes/research_routes.raw.json` for search, which deterministically retains legal candidates. Stop if no usable candidate exists; do not manufacture a seed or add a call.

## 5. Initial search → one model revision → one refinement

Downstream uses the **active selected** `CASE/routes/evidence_items.json`, never the full evidence archive or compact prompt projection. `INPUT` points to the supplied case12 input directory. The numerical backend below is the already tested local Torch setup; reuse it without installing/reconfiguring a backend mid-run.

```text
python -m focused_research.search_stage search --task-sheet CASE/plan/task_sheet.json --evidence-file CASE/routes/evidence_items.json --constraints-file CASE/design_constraints.json --routes-file CASE/routes/research_routes.raw.json --optical-media INPUT/OPTICAL_MEDIA.json --wavelengths INPUT/WAVELENGTHS_NM.json --cache-dir CASE/cache --max-forward-evaluations 2048 --max-new-evals 64 --max-workers 2 --backend torch --parameterization family --seed 42 --execute --output CASE/search
```

Only after usable certified observations and diagnostics exist, reconcile remaining call/money/numerical reservations and revise once:

```python
guard.set_stage('search_revision')
try:
    revision_status = search_stage.main([
        'revise', '--previous-search', str(Path(CASE) / 'search'),
        '--output', str(Path(CASE) / 'revision'), '--execute',
    ], planner=guard)
finally:
    persist_guard_audit(guard.audit)
```

Do **not** pass the old UV `--material-feedback`/`--cached-rta-feedback` files. This case's own observations, records and boundary assumptions are the relevant context. Keep stable IDs and current alias bindings; inspect available actions and the saved action-effects arithmetic. The model may explore a plausible finite-step hypothesis even when single-coordinate first-order signs show a tradeoff. Record and report factual sign reversals or invented observations; preserve raw model responses. Do not rewrite or automatically reject legal plans solely for such commentary, and do not require agreement with one favored mechanism. Local execution tests and reports the fixed implementation; it does not revise prompts to enforce agreement with model commentary.

For valid nonempty plans tied to that search snapshot:

```text
python -m focused_research.search_stage refine --previous-search CASE/search --revision-dir CASE/revision --cache-dir CASE/cache --max-forward-evaluations 2048 --max-new-evals 64 --max-workers 2 --backend torch --seed 43 --execute --output CASE/refined
```

A valid empty plan, partial results or a failed hypothesis can be an honest stopping result. No second revision, expansion/reflection experiment or extra optimization cycle is authorized.

## Ceilings and acceptance

- **CNY 8 same-original-case cumulative; CNY 8 for T042; CNY 220 project cumulative**, including all history, retries and liabilities. This task is **not part of the old T032 three-case CNY 24 batch**. Original live accounting governs; no known-history assumption grants fresh headroom.
- **Four model HTTP maximum** for the whole chain with one shared planning/candidate format repair, zero transport/revision retry. All actual failures/uncertain requests count. A guard count is conservative admission bookkeeping, not a replacement wire counter or money ledger.
- **4096 new forward design rows / 128 new certifications total**, split **2048/64 initial + 2048/64 refinement**, at most two certification workers. Count failed/finite-difference work and reconcile remaining cumulative capacity before each stage. Preserve later certification capacity; no implicit reset or quota transfer.

Before paying, run the fixed relevant tests in the original environment:

```text
python -m pytest -q focused_research/tests/test_t042_case12_admission.py focused_research/tests/test_local_campaign_guard.py focused_research/tests/test_t032_protocols.py focused_research/tests/test_task_sheet_tolerance.py focused_research/tests/test_t041_exploratory_admission.py focused_research/tests/test_s2_cooldown.py --disable-socket
```

Process acceptance requires faithful original goals/constraints, source/material/ledger admission, honest execution/certification records and interpretable feedback. **It does not require a favorable score, a matching paper or beating a historical design.** Evaluate the chain observation → hypothesis → chosen parent/action → actual finite-step outcome. Distinguish improvement, tradeoff, regression, unknown data and untested mechanism; retain the Ge-boundary/missing-k limitations.

Return a compact result packet: source/import/input hashes; material preflight and source mapping; raw model responses/normalization/guard audits; retrieval/fulltext availability and active evidence selection; legal and isolated seeds; all two-goal certified vectors/Pareto tradeoffs; current parent/child identities and spectra/certificates; reflection findings; exact model HTTP/cost and forward/certification counters; current settled/pending/held/uncertain exposure. Stop at the cycle boundary or an honest blocker, stating action, target, verified reason, completed work and uncertain state. Keep credentials, request headers, full ledgers/material databases, PDF/model caches local. Root reviews actual results before deciding any next experiment.
