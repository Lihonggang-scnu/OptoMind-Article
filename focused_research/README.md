# Focused Research: provisional task sheets, evidence and candidate routes

The default flow is **natural question → research task sheet → validated query adapter → retrieval**. This first version is fixed-domain (optical thin-film research), not a universal taxonomy. The new standalone route stage turns source-linked passages into decision clues and parameterized candidate routes. It stops at prepared, unexecuted structures and directional expectations. No full-paper extraction, design execution, PDF generation, citation/reference expansion or downstream T018 changes are implemented. The earlier T019 task-sheet checkout remains unchanged.

## Query quality is provisional: empirical tuning is still pending

**A polished query is not evidence that Semantic Scholar retrieves useful papers.** Review's tuned records are an initial baseline, not a guarantee that those formulations transfer to this optical design task. Offline tests establish contract/execution behavior only.

The next-stage plan has two steps: (1) empirically compare query formulations using actual retrieved metadata, abstracts and snippets, favoring useful analogous designs/simulations/fabrication cases over background-only hits; (2) tune the Qwen prompt to produce the effective formulations. Final acceptance must exercise **Qwen-generated task sheet/queries → actual retrieval**, not just hand-written queries or a plausible model response.

For that future experiment, start with 5–10 hits per query, then compare depths 15 and 20 for marginal new useful information, duplicates, relevance and latency/cost. Diminishing returns must be observed, not assumed. The retrieval default is now 10 results per query. Set `--per-query-limit` explicitly for other depths (1..200). This is not an implemented tuning loop or authorization for live calls.

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

`task_sheet.py` defines required keys and validates types, IDs, references, source spans, the narrow executable objective vocabulary, query budget and adapter fidelity. `task_sheet_prompt.txt` gives concise fillable requirements, without requiring the model to know internal system names. `examples/rgb_nir_task_sheet.json` is injected into the real runtime prompt.

### Versioned demonstration question

- `examples/rgb_nir_question.v1.json` preserves the complete historical question verbatim from `a8053a849298f88cb18dc4f05b519928ee9c7fa3`, including its crosstalk requirement and unspecified polarization/aggregation.
- `examples/rgb_nir_question.v2.json` is the user-authorized new demonstration question: remove the unsupported crosstalk requirement, explicitly add nonpolarized light with equal s/p weights, and define band means as wavelength integral divided by band width. All original bands (450–650 and 700–1000 nm), 45-degree incidence, substrate, materials and layer limit remain unchanged.
- The runtime worked example uses **v2**. The new conditions are stated in this new question; they are not defaults silently attributed to v1. Historical v1 cannot be represented as executable by hiding crosstalk in application context or importing v2's conditions.

Fields:
- `task_type`: design/optimization, explanation, diagnosis, comparison, feasibility, mixed/unresolved
- `original_question`, `user_objective`, and distinct source-grounded `subgoals`
- Each subgoal has an `executable_metric` mapping directly to the existing `ObjectivePreference` consumer: `objective_id` equal to subgoal ID, `metric`, `sense`, `mean_reduction`, and `region` containing `wavelength_nm`, `angle_deg`, `polarization`
- Narrow allowed metrics: `mean_reflectance`, `mean_transmittance`, `mean_absorption`; direction: `minimize`/`maximize`; reduction: `sample_mean`/`wavelength_integral_mean`; explicit finite positive increasing band, finite angle in [0,90), and `s`/`p`/`unpolarized`
- `application_intents`: genuine application context, source spans and subgoal links; never a place to hide an unsupported requested outcome
- `unsupported_requirements`: source-grounded requested outcomes or missing required objective conditions, with reasons; nothing silently dropped or replaced by a proxy
- `readiness`: `ready` only for a complete supported objective mapping; otherwise `needs_revision`. This does not certify physical feasibility, complete simulation setup, retrieval quality or a scoring standard.
- `goal_relationships`: requested composition, nullable priority/tradeoff and separate possible physical `coupling`
- `explicit_constraints`: individually retained with exact source spans, including materials/substrate/layer constraints separate from metric definitions
- `known_data`, `unknowns_assumptions`, `research_questions`, `candidate_mechanisms`, and exact linked `search_queries`

Task-sheet schema is `focused_research.task_sheet.v2`; old v1 sheets require explicit regeneration/review. The adapter envelope remains v1 because its execution shape is unchanged. All keys remain present. Subgoals contain only complete supported metrics; qualitative/unresolved/unsupported requests remain in `unsupported_requirements`, not fabricated numeric objectives. If none can be mapped, subgoals and their research/mechanism/query arrays can be empty. A structurally valid `needs_revision` sheet is saved once for review without automatic format repair, emits no executable plan, and is rejected by the retrieval adapter before any provider call.

For v2, G1 maximizes visible-band mean reflectance and G2 maximizes NIR-band mean transmittance. They are distinct, independently definable spectral outcomes but can be physically coupled through the same coating. `mode=all` means both are sought, not that tradeoffs are absent. No weights, combined score, thresholds or crosstalk proxy are created. General research tasks are not assumed numeric: requirements outside this deliberately restricted demonstration vocabulary remain explicit and need revision rather than forced conversion.

### Actual consumer boundary

Metric objects target `code/optomind_optics/harness/design_task.py::ObjectivePreference` and `code/optomind_optics/harness/objectives.py::evaluate_declared_objectives`. The latter maps the three metric names to R/T/A, selects the explicit channel and calls the existing spectral reducer. `sample_mean` is arithmetic sample averaging; `wavelength_integral_mean` uses wavelength integration divided by band width. They are **not interchangeable** with an unqualified `mean`, including the separate research evaluator's arithmetic mean. This change maps objective definitions and tests the existing consumer; it does not launch physical evaluation or wire a new scientific pipeline.

Validation rejects unknown expressions, extra metric fields, incomplete metric conditions and duplicate metric definitions. Source spans and structural checks cannot prove that a model preserved every user requirement or interpreted its meaning correctly; semantic review is still required. Simulation material data, sampling and other execution prerequisites remain outside this task-sheet step. The existing consumer must have matching simulated channels; missing channels fail rather than default to another polarization or angle. For wavelength-integral means, later forward integration must supply finite ordered samples including both exact declared band endpoints and adequate interior resolution. The existing consumer filters in-band samples and divides by the selected samples' first-to-last span; it does not manufacture missing endpoints or guarantee the requested band's coverage. Our tests explicitly expose this behavior; task-sheet readiness does not certify a forward grid. The downstream caller must check this prerequisite before claiming the declared whole-band mean.

`ObjectivePreference` has a legacy default weight of 1 and can aggregate multiple preferences. This sheet supplies no weight or combined-score formula; that default is not a user-approved equal-weight tradeoff. Consumer tests evaluate one objective at a time. Combining objectives or freezing a ranking standard remains outside this change and needs its own explicit definition.

Query guidance prioritizes analogous complete design/simulation/fabrication cases, then material/structure evidence, transferable methods and mechanisms where useful. It does not force category ratios or coverage of every query purpose. Exact numeric requirements remain in the sheet for later evaluation instead of being stuffed into every query. This is **provisional and pending empirical retrieval tuning**; offline tests cannot demonstrate relevance, recall or improved scientific outcomes.

## Executable consumer and bounds

The deterministic adapter maps each task query to one collector execution facet. These facets are execution slots, not a new decomposition of user goals. Exact query strings are never regenerated from titles. The adapter keeps task-query IDs and full source links on exported queries, provider audits, retrieval hits, materials and unique-material source mappings. Retrieval rejects edited adapter text or mappings that differ from the validated sheet before calling a provider. Original Review-v2 plans remain accepted only for legacy replay/compatibility under their original exact validation; the default planner emits task sheets.

- 1–6 task queries total, never padding targets or six per subgoal; either endpoint alone is valid without filler queries
- Default 10 results per query; configurable 1–200
- Shared monotonic deadline, default 300 seconds; configurable 1–1800
- Socket timeout at most 30 seconds or remaining time, transport attempts at most 2
- Paper relevance search pages at most 100 rows/request and never requests beyond its first 1,000-result window; duplicate IDs may cause extra bounded pages
- Snippet search requests the configured limit once; no invented pagination or 120→360→400 broadening
- Exactly identical repeat queries reuse their response while retaining every original query/source mapping
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
- `evidence_items.json`: original passages, explicit types, source locators, merged query/goal links and usability diagnostics
- `raw_retrieval_records.json`: original public gateway rows, including retained but unaccepted over-limit rows
- `summary.json`, `provenance.json`: scope, budgets and vendored source hashes

Failed calls, later-page failures, result-window/deadline cutoffs and missing execution slots are explicit; partial runs retain successful hits. All-provider failure is `failed`; healthy empty search is `no_results`. Review-v2 metadata filters/exclusions remain unapplied and cause `partial` when requested. Task-sheet constraints are retained separately for later scientific evaluation; retrieval does not claim filtering or compliance. Identity deduplication uses S2 ID, DOI and corpus ID, not title guessing.

## Isolation, provenance and verification

`PROVENANCE.json` retains original source hashes and current vendored hashes. The copied gateway adds `corpusId` for identity joins and now corrects the inherited 10,000-offset loop to the ordinary relevance endpoint's 1,000-result window. The new task-sheet implementation is outside the vendor tree. Existing Article code/configuration and T018 are unchanged.

Configure existing `QWEN_API_KEY`, `SEMANTIC_SCHOLAR_API_KEY`/`SEMANTIC_SCHOLAR_API_KEYS_FILE` outside committed files. Model/provider settings remain under `vendor/review/config`; original project-relative credential lookup resolves there, not `code/api_keys`. No credentials are included. Run standalone, not in a process already importing Article's generic `config`, `llm` or `optomind_research` packages.

```bash
PYTHONPATH=/tmp/optomind-core-testdeps python -m pytest -q --disable-socket focused_research/tests
```

The local dependency path is a test-environment convenience. Tests inject responses with sockets disabled and cover actual planner wiring, versioned example, unsupported/needs-revision behavior, strict metric conditions, relationships/candidate status, validation/repair, exact task-query execution and provenance, no-network rejection, 200-result pagination, relevance-window exhaustion, deduplication and failure states. An isolated subprocess also tests the actual objective consumer with sockets disabled: all three allowed R/T/A means distinguish arithmetic .30 from wavelength-integral .36 on uneven spectral samples, honor explicit channel selection, and reject missing channels. These are deterministic consumer fixtures, not physical simulations. **No paid calls or live provider tests were run.** Online availability and retrieval quality are unverified.


## Candidate-route stage (offline preparation)

Read [ROUTE_MODULE_DESIGN.md](ROUTE_MODULE_DESIGN.md) for the design and known limits. This module is provisional and requires actual model/retrieval tuning. It does not claim that a generated route works.

`route_stage.py` performs only offline prompt preparation, contract validation or deterministic stack materialization. It never constructs a model client. Inputs are the upstream task sheet, the retrieval output `evidence_items.json`, and a source-linked structural configuration. The latter is explicit because the upstream sheet stores layer/material constraints as natural language; parsing a configuration does not prove its semantic correspondence to the question. The RGB/NIR example configuration records air incidence as a configuration assumption.

```bash
python focused_research/route_stage.py prompt --task-sheet focused_research/examples/rgb_nir_task_sheet.json --evidence-file local_runs/focused_materials/evidence_items.json --constraints-file focused_research/examples/rgb_nir_design_constraints.json --output local_runs/route_prompt
python focused_research/route_stage.py validate --task-sheet focused_research/examples/rgb_nir_task_sheet.json --evidence-file local_runs/focused_materials/evidence_items.json --constraints-file focused_research/examples/rgb_nir_design_constraints.json --routes-file local_runs/model_routes.json --output local_runs/route_validation
python focused_research/route_stage.py materialize --task-sheet focused_research/examples/rgb_nir_task_sheet.json --evidence-file local_runs/focused_materials/evidence_items.json --constraints-file focused_research/examples/rgb_nir_design_constraints.json --routes-file local_runs/model_routes.json --route-id R1 --output local_runs/prepared_stacks
```

All outputs use new directories and retain exact inputs plus their hashes. `contract_validated` and `prepared` are syntax/provenance milestones, not scientific acceptance or permission to execute. Incomplete routes remain `needs_revision`; they cannot be materialized.

For the later local metered caller, `research_routes.generate(sheet, evidence, planner, design_constraints=config, max_repairs=1)` accepts an explicitly injected original Review-compatible planner. It retains raw/effective responses, validation failures and usage and rejects mock/fallback results. Set up the same standalone import paths as `run.py` before importing the vendor client. At most one format-repair round is allowed, but that is not a transport-attempt or monetary cap: the original local budget wrapper must cover every HTTP retry. No new paid task is authorized by this README.

`route_consumer.prepare_tmm_inputs` takes a prepared instance, explicit optical-medium mappings and an explicit wavelength grid. It preserves task objectives without adding weights/targets and checks exact band endpoints plus interior samples. It constructs the existing VeriTMM task shape; it does not query optical constants or run a solver. Material dataset coverage, convergence, appropriate sampling density and physical verification remain prerequisites for future execution. The accompanying subprocess test uses the actual existing `simulation_task_from_dict` and `ObjectivePreference` consumers, without computation.

First-version route expectations compare parameter settings within the same explicit layer sequence; topology-changing comparisons, adaptive budget allocation and intervention-response archives are future work. A route need not invent an expected direction for every goal. The task's remaining goals must still be evaluated by any later design selection.
