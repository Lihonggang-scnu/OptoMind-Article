# Focused Research: provisional task-sheet retrieval

The default flow is **natural question → research task sheet → validated query adapter → retrieval**. This first version is fixed-domain (optical thin-film research), not a universal taxonomy. It stops at retrieved metadata, abstracts and snippets. No full-paper extraction, evidence cards, scientific answer, design execution, PDF generation, citation/reference expansion or downstream T018 changes are implemented.

## Run

From the repository root, Python 3.10+ with `ftfy` and `PyYAML`:

```bash
python focused_research/run.py plan --question 'Your complete research question' --output local_runs/focused_plan
python focused_research/run.py retrieve --plan-file local_runs/focused_plan/task_sheet.json --output local_runs/focused_materials
# query_plan.json is also accepted; its adapter must match the embedded sheet exactly.
python focused_research/run.py run --question 'Your complete research question' --output local_runs/focused_run
```

Output directories must be new/empty. Exit 0 means planned or retrieval completed in scope; exit 2 means invalid configuration/planning, partial retrieval, failure or no results. A retrieval `complete` status means calls/coverage completed, **not** that scientific constraints are satisfied or the hits are relevant. Every retrieval summary says `relevance_validated=false` and `constraints_evaluated=false`.

Planning uses Review's original Qwen client, model-tier policy, JSON response mode and credential resolution through `_call_query_plan_model`; the new task planner requests a bounded 7,000-token output and permits at most one explicit contract repair. It retains both raw responses, errors, usage and transport outcomes. Mock/fallback/transport-failed plans never proceed to retrieval. This replaces the old Review prompt as the default; it is a substantive contract change, not numeric-only tuning.

## Fixed contract

`task_sheet.py` defines the strict required keys and validates types, IDs, references, original spans, query budget and adapter fidelity. `task_sheet_prompt.txt` supplies the planning guidance. `examples/rgb_nir_task_sheet.json` is a complete worked example with the **full actual RGB/NIR question**. The example is injected into the actual runtime prompt, not an unused fixture.

Fields:
- `task_type`: design/optimization, explanation, diagnosis, comparison, feasibility, mixed/unresolved
- `original_question`, `user_objective`, and source-grounded `subgoals`
- `goal_relationships`: all, any, priorities, tradeoff, mixed/unresolved; nullable priority order/tradeoff
- `explicit_constraints`: individually retained with verbatim original spans
- `known_data`: source-grounded statements, separated from `unknowns_assumptions`
- `research_questions` linked to subgoals
- `candidate_mechanisms` linked to subgoals, explicitly candidates rather than proven conclusions
- `search_queries`: exact executable text, endpoint, subgoal/mechanism/research-question links, retrieval purpose and rationale

All keys remain present. Empty lists represent no information/not applicable; nullable fields represent unspecified/not applicable values. Missing mechanisms are permitted; their query references are then empty. Priorities and weights must not be invented. For the example, both spectral bands and crosstalk are simultaneous objectives; polarization is unknown, and “as high as possible” is not an invented 99% threshold. Validation establishes structural consistency and exact source-span occurrence, not that every user intent was correctly interpreted. Human/empirical review remains necessary.

Query guidance prioritizes analogous complete design/simulation/fabrication cases, then material/structure evidence, transferable methods and mechanisms where useful. It does not force category ratios or coverage of every query purpose. Exact numeric requirements remain in the sheet for later evaluation instead of being stuffed into every query. This is **provisional and pending empirical retrieval tuning**; offline tests cannot demonstrate relevance, recall or improved scientific outcomes.

## Executable consumer and bounds

The deterministic adapter maps each task query to one collector execution facet. These facets are execution slots, not a new decomposition of user goals. Exact query strings are never regenerated from titles. The adapter keeps task-query IDs and full source links on exported queries, provider audits, retrieval hits, materials and unique-material source mappings. Retrieval rejects edited adapter text or mappings that differ from the validated sheet before calling a provider. Original Review-v2 plans remain accepted only for legacy replay/compatibility under their original exact validation; the default planner emits task sheets.

- 1–6 task queries total, never padding targets or six per subgoal; either endpoint alone is valid without filler queries
- Default 200 results per query; configurable 1–200
- Shared monotonic deadline, default 300 seconds; configurable 1–1800
- Socket timeout at most 30 seconds or remaining time, transport attempts at most 2
- Paper relevance search pages at most 100 rows/request and never requests beyond its first 1,000-result window; duplicate IDs may cause extra bounded pages
- Snippet search requests the configured limit once; no invented pagination or 120→360→400 broadening
- Normalized repeat queries reuse their response while retaining every original query/source mapping
- No metadata enrichment, seed expansion, references/citations traversal or downstream model calls

The supported ordinary Semantic Scholar `/paper/search` and `/snippet/search` endpoints specify **no special query syntax**. Paper queries use compact plain-text scholarly terms; snippet queries use natural-language questions. The provider recommends replacing hyphens with spaces for ordinary paper queries: prompt guidance does this before generation, without silently rewriting executable strings. `/paper/search/bulk` is a distinct endpoint with its own Boolean/phrase operators; it is not added or implicitly substituted here. See the [official Graph API documentation](https://api.semanticscholar.org/api-docs/) and [snippet documentation](https://api.semanticscholar.org/api-docs/snippets). Endpoint-aware phrase/question guidance is an engineering heuristic, not a claim that the API requires those linguistic forms.

## Outputs and honest status

- `task_sheet.json`: complete intent, constraints, research questions, candidate mechanisms and executable queries
- `query_plan.json`: exact deterministic adapter with embedded sheet and mappings
- `queries.json`, `subproblems.json`: execution queries and adapter slots; use the task sheet for user subgoals
- `planning.json`: raw/effective planning results, validation/repair history and usage
- `candidate_corpus.json`: deduplicated paper identities, all query hits, audits and preserved constraints for later evaluation
- `materials.json`, `unique_materials.json`: readable metadata/abstract/snippet material and all source mappings; every row declares `fulltext_retrieved=false`
- `snippets.sqlite`: actual snippet text keyed by hit ID, with hash/locator in corpus
- `provider_calls.json`: limits, endpoint method, status, cache, pagination, query reuse and missing-result reasons
- `summary.json`, `provenance.json`: scope, budgets and vendored source hashes

Failed calls, later-page failures, result-window/deadline cutoffs and missing execution slots are explicit; partial runs retain successful hits. All-provider failure is `failed`; healthy empty search is `no_results`. Review-v2 metadata filters/exclusions remain unapplied and cause `partial` when requested. Task-sheet constraints are retained separately for later scientific evaluation; retrieval does not claim filtering or compliance. Identity deduplication uses S2 ID, DOI and corpus ID, not title guessing.

## Isolation, provenance and verification

`PROVENANCE.json` retains original source hashes and current vendored hashes. The copied gateway adds `corpusId` for identity joins and now corrects the inherited 10,000-offset loop to the ordinary relevance endpoint's 1,000-result window. The new task-sheet implementation is outside the vendor tree. Existing Article code/configuration and T018 are unchanged.

Configure existing `QWEN_API_KEY`, `SEMANTIC_SCHOLAR_API_KEY`/`SEMANTIC_SCHOLAR_API_KEYS_FILE` outside committed files. Model/provider settings remain under `vendor/review/config`; original project-relative credential lookup resolves there, not `code/api_keys`. No credentials are included. Run standalone, not in a process already importing Article's generic `config`, `llm` or `optomind_research` packages.

```bash
PYTHONPATH=/tmp/optomind-core-testdeps python -m pytest -q --disable-socket focused_research/tests
```

The local dependency path is a test-environment convenience. Tests inject responses with sockets disabled and cover actual planner wiring, full example, nullable/unresolved shapes, relationships/candidate status, validation/repair, exact task-query execution and provenance, no-network rejection, 200-result pagination, relevance-window exhaustion, deduplication and failure states. **No paid calls or live provider tests were run.** Online availability and retrieval quality are unverified.
