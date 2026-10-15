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
