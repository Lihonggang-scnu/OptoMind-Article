# Focused Review retrieval (isolated entrypoint)

This transplant stops at subproblems, executable queries, and retrieved materials with source mappings. It does not answer the scientific question, read full papers, create cards/plans, or produce PDFs. The existing Article/T018 consumer is unchanged.

## Run

From the repository root, using Python 3.10+ with `ftfy` and `PyYAML` installed:

```bash
python focused_research/run.py plan --question 'Your single research question' --output local_runs/focused_plan
python focused_research/run.py retrieve --plan-file local_runs/focused_plan/query_plan.json --output local_runs/focused_materials
# Or execute both stages:
python focused_research/run.py run --question 'Your single research question' --output local_runs/focused_run
```

Every output directory must be new/empty. Exit 0 means planned or retrieval completed within the declared scope; exit 2 means failure, partial retrieval, no results, or invalid configuration. Inspect `summary.json` rather than interpreting the exit status as scientific success. A successful retrieval is not a relevance/evidence acceptance verdict.

The planner uses the original Review Qwen client, model-tier policy, JSON response contract, repair limit, and secret resolution. The retrieval uses the original Review Semantic Scholar transport, key router, cache, and pagination implementation. This is real executable provider wiring, not a synthetic pipeline; **no live or paid calls were made during implementation**. Offline tests inject provider responses; they do not validate online availability or scientific relevance.

Configure existing supported environment variables such as `QWEN_API_KEY` and `SEMANTIC_SCHOLAR_API_KEY`/`SEMANTIC_SCHOLAR_API_KEYS_FILE` outside committed files. Original model/provider settings are isolated under `vendor/review/config`; existing `code/config` is neither imported nor overwritten. Original project-relative credential lookup now resolves relative to this vendored root, so an existing key file in `code/api_keys` is not silently reused; use environment variables or the supported explicit key-file setting. No credentials are included. Do not import this runner into a process already using Article's generic `config`, `llm`, or `optomind_research` packages; launch it as the standalone process above.

## Numeric breadth/depth profile

| Control | Original Review | Focused default |
|---|---:|---:|
| Facets | 1–10 | 1–2 |
| Keyword queries per facet | 1–3 | 1 |
| Semantic questions per facet | 1–3 | 1–2 |
| Maximum query occurrences | 60 | 6 |
| Initial per-query results | 100 | 200 |
| Snippet distinct-paper expansion target | 60 | 0 (disabled) |
| Seed allowance | 3, only user-supplied | unchanged; no seed expansion executed |

These are upper bounds, never padding targets. The `plan` stage preserves the original combined STEP 1 relation/subproblem decomposition and STEP 2 query generation, then exports each separately; this is not an invented extra LLM decomposition pass. Original raw plans, validation/revision rounds and final effective plans are retained in `planning.json`, including any upstream validator truncation. Retrieval refuses a supplied plan if validation would change it.

Depth here means more metadata/abstract candidates or body snippets per fixed query: 100 → 200. It does not mean deeper full-text reading. Paper search pages at at most 100 records per request, so 200 distinct results normally requires two pages; duplicate identities can require further pages within the original gateway's 10,000-offset bound and the shared deadline. Semantic search requests `limit=200` once; it does not pretend to paginate. Provider exhaustion may return fewer. Failed/rejected requests, later-page failures and deadline cutoffs are recorded explicitly. The original snippet broadening 120→360→400 is disabled rather than relabeled as depth.

`--per-query-limit` is bounded to 1–200, default 200. `--retrieval-seconds` is bounded to 1–1800, default 300. Each retrieval request uses the same monotonic deadline; actual socket timeout is at most 30 seconds or time remaining. The original transport's attempts are capped at 2. Exact normalized repeated queries reuse the same response without another network call; each facet/query mapping is still kept. No metadata-enrichment, full-text, citation-walk or downstream model calls occur.

## Artifacts and honesty

- `subproblems.json`, `queries.json`, `query_plan.json`: subquestions and exact dual-channel queries.
- `planning.json`: original tuned planner output, validation, repair, usage, raw/effective plans.
- `candidate_corpus.json`: unique papers, per-query/per-facet hits, identity-merge diagnostics and response audits.
- `snippets.sqlite`: actual retrieved snippet text keyed by hit ID, with hash/locator retained in corpus JSON.
- `materials.json`: readable per-hit metadata/abstract/snippet data. Every row declares material type and `fulltext_retrieved=false`.
- `unique_materials.json`: deduplicated material text plus all source mappings. Distinct passages from one paper remain distinct material.
- `provider_calls.json`: limits, HTTP/category, cache flag, pagination, query reuse and missing-result reasons. Exception messages are not saved because they can contain credentials.
- `summary.json`, `provenance.json`: outcome/budgets and source hashes.

Partial failures retain successful materials. All-provider failure is `failed`, healthy empty search is `no_results`, and a missing facet is `partial`. A metadata-only hit is explicitly labeled and is not represented as retrieved full text. Identity deduplication uses S2 ID, DOI, corpus ID, not title guessing. Duplicate S2 IDs sharing DOI preserve merged aliases in upstream statistics.

Review's candidate collector does not execute facet metadata filters or semantic exclusions. This entrypoint preserves them, marks them not applied and reports `partial` when an explicit filter or additional constraint needs later acceptance. It does not claim filtered compliance. Semantic relevance checking and scientific answering remain out of scope.

## Exact transplantation and necessary adapters

`PROVENANCE.json` records SHA-256 of each original and vendored file, Review commit `775329cee02d4dacd108bf41969b1d21d26750e0`, and Article base `59e10e64794f431e6de7d7656af9f5307710f4a9`.

Prompt content is retained except the numeric phrase `Use up to three only` → `Use up to two only`; numeric field-dictionary limits above also change the rendered prompt. No scientific wording, examples, exclusion logic, canonical-object rules or complementary-form guidance was rewritten.

Necessary nonnumeric changes, explicitly not described as numeric-only:
1. New isolated process/CLI and minimal package initializers prevent replacing active Article configuration and importing downstream Review modules.
2. New orchestration exports the existing combined planner as subproblem/query artifacts, enforces limits and exact-plan validation, rejects mocked planning, records provenance, and stops at retrieval.
3. New gateway wrapper preserves errors/partial results/deadline/query reuse, plus explicit material formats and source mappings. Upstream corpus otherwise drops much of the transport status.
4. The copied gateway's discovery field list adds `corpusId`, connecting paper-search IDs to snippet-search IDs without extra enrichment requests. Other gateway code is unchanged.
5. The wrapper removes the inherited 500–800 survey corpus target from the focused output; the copied source constant is retained as provenance, not a runtime target.

## Offline checks

```bash
PYTHONPATH=/tmp/optomind-core-testdeps python -m pytest -q --disable-socket focused_research/tests
```

The local dependency directory is a test-environment convenience, not shipped runtime dependencies. Provider socket access is disabled. Tests cover actual planner contract wiring, numeric prompts, 200-result paper pagination, snippet limit forwarding, identity and material deduplication, source mapping, failure/partial/empty distinctions, deadline cutoff, mock rejection, hash provenance, output reuse and budget validation.
