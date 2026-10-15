# Focused retrieval implementation record

Date: 2026-10-15 UTC. Branch: `feature/focused-research-20261015`.

## Scope and source

- Article clean base: `59e10e64794f431e6de7d7656af9f5307710f4a9`.
- Review read-only source: `775329cee02d4dacd108bf41969b1d21d26750e0`.
- Added only `focused_research/`; did not edit source repositories, existing `code/`, T018 consumer, or local test ticket.
- No credentials read/copied, no live/paid/provider calls, no pushes. All provider behavior in tests is injected and all tests run with sockets disabled.

## Implementation

1. Transplanted Review v2 `QueryPlannerAgent.query_plan_v2`, `runtime/upgrade3/query_plan.py`, and `candidate_corpus.py` with the required original Qwen/S2 config/client/tool dependency closure. Source and destination SHA-256 are in `PROVENANCE.json`.
2. Preserved STEP 1 relational subproblem decomposition and STEP 2 dual-channel query generation in their original combined planner invocation; exported subproblems and exact queries separately.
3. Numeric breadth profile: facets 1–10 → 1–2; keyword max3 → max1; semantic max3 → max2; total query occurrence ceiling60 → 6. No minimum-count padding. The sole literal prompt edit is `Use up to three only` → `Use up to two only`; original complementary-form instruction remains.
4. Fixed-query retrieval depth: original initial100 → default200. Snippet distinct-paper expansion target60 → 0, disabling the original120/360/400 broadening. Paper search uses the original Review pagination; snippet search requests200 directly. Runtime limit1–200, shared retrieval deadline1–1800 seconds/default300, per-request timeout30 seconds bounded by remaining time, transport attempts2.
5. Non-numeric provider identity adaptation: add `corpusId` to `DISCOVERY_FIELDS` so keyword and snippet hits can merge without separate enrichment calls. All other copied gateway code remains unchanged.
6. Added isolated plan/retrieve/run CLI, exact-plan validation, mock/fallback/transport rejection, atomic JSON writes, clean-output guard, explicit partial failures, unchanged exact query text, normalized repeat-query response reuse, material/source mapping exports, snippet side store, and deduplicated material text with all mappings preserved.
7. Disabled downstream enrichment/readers/cards/plans/PDF by construction: the entrypoint invokes only planner and candidate retrieval. Minimal package initializers cannot import downstream Review code. No automatic active-consumer integration.
8. Preserved but explicitly marked unexecuted filters/exclusions/constraints and returned `partial` for these cases. Scientific acceptance/full-text reading remains out of scope.

## Validation

Final focused command, run from workspace root:

```bash
PYTHONPATH=/tmp/optomind-core-testdeps python -m pytest -q --disable-socket r3_focused_research_20261015/focused_research/tests
```

Result: **23 passed in 0.22 seconds**, 2026-10-15 04:16 UTC.

Additional checks:
- Standalone `run.py --help` succeeds.
- `git diff --check` passes.
- Independent read-only reviewer reran the earlier20-test set with sockets disabled, passed all20, and verified every source hash against the Review checkout.
- Final added tests cover real `S2Transport` URL construction with injected opener, provider transport rejection, and valid-shaped fallback rejection. These are offline tests, not live acceptance.

Covered invariants: tuned planner contract, prompt numeric limits,200-paper two-page requests,200-snippet request limit, provider URL/timeout propagation, duplicate-query no-extra-call behavior, paper identity merge, passage/material deduplication with all provenance retained, successful material retention on partial failure, actual stored snippet text/hash, healthy empty vs failure, deadline, budgets, explicit unapplied restrictions, mock/transport rejection, raw/effective artifacts, and vendored hashes.

## Remaining limitations

- No online provider availability, credential validity, rate-limit behavior, or scientific relevance validation was performed. A later explicitly authorized online run is required for real-provider acceptance.
- Copied original provider/model configuration is isolated: it does not overwrite or automatically reuse Article project-relative credential files. Supported environment/explicit-file settings remain available. `ftfy` and `PyYAML` dependencies are declared.
- The original gateway may require more than two paper pages when duplicate identities occur; original offset cap and shared deadline bound this. A200 request is a cap, not a guarantee of200 retrieved items.
- Snippets/abstracts/metadata are not full text. All artifacts label material form and `fulltext_retrieved=false`.
- Filters and semantic exclusions are not executed by the source candidate collector; these runs explicitly remain partial pending later acceptance.
- Use a standalone process; generic vendored package names must not be mixed into an already-running Article process.

Implementation editing stopped after this record; parent owns final validation and branch commit.

## 2026-10-15 04:41 UTC — User review: provisional, revision required

Source: user message Sentinel_962a5eabf8d88191a040b9629bb92d44.

The current transplantation is an unfinished initial design for continued modification in this round, pending tuning. Offline correctness tests are not retrieval-quality acceptance. Reducing numeric breadth alone does not establish that review-tuned planning retrieves actionable problem-solving papers rather than background/survey material.

User requests consideration of flexible fields such as user objective, subgoals, constraints and potential physical effects. These are reference directions, not a mandated rigid schema or compulsory decomposition. Preserve unusual questions and user freedom; prefer LLM-selected relevant fields and retrieval routes. For the RGB/NIR example, retain visible 450–650 nm high reflection and NIR 700–1000 nm high transmission as joint design goals, with 45 degree incidence, fused silica, TiO2/SiO2 and layer cap as user constraints. Retrieval can include nearby design, simulation and fabrication studies; do not force every search query to satisfy every design constraint simultaneously.

Next design discussion must distinguish hard user requirements from tentative assumptions, and design-goal composition from search-result unions/intersections. No revised runtime schema is approved solely by recording this discussion. No paid evaluation performed. Current implementation commit: 837f5531eda3558fc6340b8ccfedef62bf953b8c.

## 2026-10-15: provisional task-sheet contract revision

- Replaced the default combined Review planner with a fixed-domain task-sheet prompt and strict contract; reused the original Qwen transport/model policy and one bounded repair. Full actual RGB/NIR question and worked JSON example are injected into the real prompt.
- Added executable search_queries linked to user subgoals, candidate mechanisms, research questions and retrieval purposes. Kept all explicit constraints with original spans; unspecified polarization/thresholds remain unknown.
- Added a deterministic adapter consumed by the existing corpus builder. Retrieval rejects adapter/query provenance changes before calls; exports retain task links across hits, materials and provider audits. Legacy plans remain an explicit compatibility input, not the default producer.
- Preserved six-query total/200-result bounds, disabled expansion/enrichment, original Article/T018 isolation. Corrected vendored ordinary paper search pagination to the documented first 1,000-result window and updated provenance hashes. Ordinary endpoints do not inherit bulk-query operators.
- Official endpoint evidence: https://api.semanticscholar.org/api-docs/ and https://api.semanticscholar.org/api-docs/snippets . No live/paid calls made. Offline structural/consumer tests cannot prove relevance; empirical retrieval tuning remains pending. Citation/reference expansion remains a future suggestion, not implemented.

## 2026-10-15: nonredundant goals and bounded local handoff

- Starting source: `a8053a849298f88cb18dc4f05b519928ee9c7fa3`. Revised the task-sheet contract to v2 (explicit breaking shape change; no silent v1 migration). The execution adapter envelope remains v1 with unchanged execution shape.
- G1 visible reflectance and G2 NIR transmittance remain the only RGB/NIR subgoals. Crosstalk is preserved as application intent A1 linked to G1/G2, with unresolved operational definition, not a duplicate third score target. The full original question, 450–650 nm band, explicit constraints and executable query strings are byte-for-byte unchanged from the starting example.
- Added nullable operational definitions (qualitative or undefined is allowed), source-grounded application intents and a coupling note separate from requested goal composition. Distinct/independently definable does not promise physically independent goals. Validator rejects obvious text duplicates and invalid fields/references; semantic redundancy remains a review obligation, not a claimed automated proof.
- Added prominent provisional query-quality guidance: first empirically tune formulations, then the Qwen prompt, with final Qwen-to-actual-retrieval acceptance. Prior Review tuning is only an initial baseline. Future depth comparisons start at 5–10 and compare 15/20 for diminishing useful information. No tuning loop implemented; default remains 200.
- Added `LOCAL_TEST_HANDOFF.md`: cloud core ownership, local tests/minor environment work, correct delivered revision, offline-first checks, then only one RGB/NIR plan if fresh ledger and existing wrapper genuinely admit the new Qwen entrypoint under CNY 2 incremental/CNY 140 total and three HTTP attempts including repairs/retries. Otherwise blocked; no bare CLI or local billing redesign. No retrieval/physics/PDF in this handoff.
- Verification: `PYTHONPATH=/tmp/optomind-core-testdeps python -m pytest -q --disable-socket focused_research/tests` → **50 passed in 0.35s**. `git diff --check` and standalone `run.py --help` passed. Compared original question and exact query texts against HEAD with assertions. No live/paid calls, commits or pushes; parent owns publication and final acceptance.

## 2026-10-15: resumed revision — supported demonstration objectives only

This supersedes the preceding provisional crosstalk-as-application-intent treatment. Following the user's additional competition-demo scope, executable subgoals now accept only the audited existing consumer's `mean_reflectance`, `mean_transmittance`, `mean_absorption`, min/max direction, explicit `sample_mean`/`wavelength_integral_mean`, finite increasing wavelength interval, explicit incidence angle and s/p/unpolarized selector. Materials/substrate/layer constraints remain separate. No new solver, proxy, combined score or weight was added.

- Preserved the complete original question as `examples/rgb_nir_question.v1.json`. Created separately versioned v2 with the user-authorized removal of crosstalk and explicit addition of equal-s/p unpolarized light and wavelength-integral band means. Runtime example now uses that complete v2 text. It does not impute these conditions to v1.
- Unsupported or insufficiently specified original requirements are retained with exact spans and reasons in `unsupported_requirements`, forcing `needs_revision`; they cannot hide as application context. Incomplete or unknown metric objects fail validation. A structurally valid needs-revision response is saved without a needless format repair and cannot be adapted/retrieved. Readiness concerns objective mapping only, not complete physical setup.
- Duplicate metric signatures are rejected even under different objective wording or int/float spellings. Physical coupling remains permitted. Qualitative requirements outside the narrow executable vocabulary are preserved explicitly rather than assigned invented numeric proxies.
- Audited boundary: `code/optomind_optics/harness/design_task.py::ObjectivePreference`; `code/optomind_optics/harness/objectives.py::evaluate_declared_objectives`; `veritmm/tmm_engine/spectral_reduction.py`. Unit weights in the existing consumer are not an approved combined objective. The separate research evaluator's unqualified arithmetic mean is not silently substituted.
- Added isolated actual-consumer tests with sockets disabled: all three R/T/A metrics distinguish sample mean .30 from wavelength-integral .36 on uneven samples; explicit angle/polarization selects only the intended channel and missing channels fail. A fourth test exposes that absent declared band endpoints are not filled or validated by the existing consumer; later forward integration must ensure exact endpoints and adequate interior sampling before whole-band acceptance. No core solver changed.
- Updated handoff to the revised v2 plan-only target and explicit publication/live-test hold pending cloud review/release; previous CNY 2 incremental/CNY 140 total/three-HTTP-attempt controls remain ceilings, not a bypass of the hold.
- Final check: `PYTHONPATH=/tmp/optomind-core-testdeps python -m pytest -q --disable-socket focused_research/tests` → **76 passed in 2.07s**, including an isolated four-test consumer subprocess. `git diff --check` passes. Only focused_research files changed; no live/paid calls, commit, upload or push.

## 2026-10-15 — evidence-guided routes, separate from ongoing T019

User authorized implementing the next module after reviewing the proposal. Created `feature/evidence-routes-20261015` in a separate worktree at `713799fb55899b78427aed56cb643bc72526c19d`; preserved the previous task-sheet checkout and prompt unchanged.

Implemented deterministic lightweight retrieval (default 10 per query), raw public-provider retention, original-text evidence with explicit type/source/query/goal links and source-aware deduplication. Missing bibliographic fields do not block usable text. Over-limit rows remain in raw diagnostics only; original task query strings reach the provider unchanged.

Implemented source-anchored decision clues and candidate route contracts, bounded named thickness variables, explicit instances, directional reference comparisons, partial/needs-revision retention, and an injected original-model-compatible generation function with one bounded format repair. Weak evidence and uncertainty alone do not disqualify a well-formed hypothesis. A route may predict only one goal; prepared instances retain every user objective. No unsupported new score or inferred equal-weight scalar objective is introduced.

Added offline prompt/validation/materialization CLI, exact input hashes, and an explicit optical-media/grid adapter exercised against the existing VeriTMM task parser and Article objective consumer. Prepared outputs state syntax/provenance scope and no execution authorization. No physical simulation or project API/model call was made. Synthetic examples are marked as such.

Independent review identified and prompted fixes for mixed material/constant-index type ambiguity and an incorrect sample constraint source ID (C6 → C7). Additional integration checks covered actual original-text retention, over-limit provider rows, all-goal preservation, endpoint samples, and the synthetic retrieval-to-route-to-consumer chain.

The first implementation deliberately supports within-family thickness comparisons only. Structural-topology interventions, response-diversity archives, adaptive budget allocation and demonstrated research gains remain future stages. Query depth, model prompts and route scientific quality still require local empirical tuning. Source-linked constraint configuration must be reviewed semantically; ID existence alone does not establish correct interpretation.

Final cloud verification: `PYTHONPATH=/tmp/optomind-core-testdeps python -m pytest -q --disable-socket focused_research/tests` — 149 passed. The independent reviewer ran the preceding 145-test revision; four final regressions cover invalid material field types paired with constant indices. The actual parser/objective subprocess checks do not invoke a solver. No live retrieval relevance or Qwen route quality is claimed.

## 2026-10-15: first empirical query revision (provisional)
User requested one revision followed by Qwen→actual S2 retrieval after local quality report at Article 7099ba90e759fea115269eaa158524050a63cdf5. Fifty returned entries/42 source IDs are baseline observations, not an accepted design. Raw Q3 shows deposition-angle confusion; Q4 includes spatial edge detection with useful thickness-optimization methods; Q5 includes opposite-direction NIR shielding with useful parameters. Preserve transferable leads while positively disambiguating query intent. Keep Q1/Q2/Q5 example wording as controls, change Q3/Q4 and Q5 rationale. No claim of improved live relevance before the next result.

Native original_question is program-owned; the model supplies analysis. Raw model output is retained. This fixes echo-only rejection without weakening the actual analysis/span checks. Only focused offline regression is requested in cloud; local runs one bounded model-generation→retrieval chain, not broad regression or T020 acceptance. See LOCAL_TEST_HANDOFF.md for limits and stopping condition.

User subsequently expanded T021 to the second module: actual retrieved evidence → Qwen decision clues/routes → prepared VeriTMM inputs. Small integrated live test, with at most four total model HTTP attempts and CNY 2 whole-task cap through the existing local wrapper; no simulation or broad offline acceptance.

Cloud checks for this revision: first-module tests plus new native-input tests 91 passed; injected route-stage generation plus existing route/consumer checks 68 passed, sockets disabled. These two selected sets are disjoint (159 total); they do not constitute a new full-system acceptance or any live generation/retrieval. Existing original model configuration and paid accounting remain local. The generated route entrypoint refuses an uninjected planner and saves partial results before input preparation.

## 2026-10-15 T022: complete-paper inputs, model knowledge and S2 cooldown

User expanded T021: paper-PDF parsing is required alongside S2; reuse Review d2deb4dbe18b23837d949822ad731afbaba9414b, with local three-worker Docling and selected cached CodeFormulaV2. Task cap becomes CNY15 within existing ledgers/global140. T021 must not acquire this expansion implicitly; separate T022 handoff supersedes it at a safe boundary and preserves compatible results.

Added isolated pinned-Review acquisition/parse bridge, independent OpenAlex/CORE discovery, verified snapshot exports, full body/table/formula evidence import and bounded whole-block prompt views. Formula enhancement can run as a second selected-only phase without redoing discovery/basic parsing. Native XML remains preferred. Tests use actual pinned native-XML snapshot code with test-only import isolation for unavailable eager runtime dependencies; the production helper has no such stubs. Cloud has not run remote providers, GPU Docling or CodeFormulaV2, and does not claim local execution.

Route schema v2 allows external_evidence, model_knowledge and hybrid. Model knowledge needs no external citation but still provides physical reasoning, executable bounds and baseline-referenced directional expectations. Legacy v1 remains external-only. Inputs and prepared outputs retain the basis label; the program does not claim natural-language scientific reasoning is proven.

S2 429 is pending transport state. Query-level cooldown honors Retry-After and permits at most15 reconnections after the initial query request, preserving successes and partial pages. It is independent of the paid model-attempt cap. Exhaustion is incomplete, never a false zero-hit/relevance judgment. This is a provisional integrated module, awaiting one bounded local real run, not accepted optical performance.

Final T022 cloud validation: `PYTHONPATH=/tmp/optomind-core-testdeps python -m pytest -q --disable-socket focused_research/tests` → **222 passed, 6 subtests passed in 4.97 s**. Includes actual vendored S2 transport/gateway with fake HTTP/clock (uncapped Retry-After and successful-page reuse), and source-mode/input-boundary regressions. No live S2/model, GPU parser or scientific simulation is implied. Separate native-XML smoke used real pinned Review snapshot code with test-side eager-import isolation, preserving source hashes/table/formula text. `git diff --check` clean.
