# Article Pipeline Real-Integration Entry Point

`scripts/run_article_pipeline_integration.py` is the production entry point for
the already accepted eight-stage `ArticlePipeline`. It does not duplicate or
replace scientific logic. It validates caller configuration, assembles
`ProductionArticlePipelineFactory`, dispatches `run()` or `resume()`, and writes
one bounded operational summary.

## Security boundary

- `ARTICLE_COMPILATION_AUTHORITY_KEY` is mandatory and read only from the
  caller environment. It is never accepted as a CLI argument or serialized.
- Qwen keys remain under the existing `QWEN_API_KEY_FILE`/secret-pool path.
- The summary whitelists telemetry. It can retain a masked key, candidate and
  rotation counts, retry counts, model names, and bounded error categories. It
  never stores API keys, authority material, key-source paths, raw prompts, or
  raw provider response bodies.
- The local ReviewKnowledgeBase is opened by the existing read-only query
  adapter. The CLI validates that every explicit path is a file.

## Usage and cost accounting

The summary gathers calls from problem analysis, method synthesis, strategy
planning, Article Director, every route task compilation, and experiment
planning. Provider `input_tokens` and `output_tokens` are authoritative when
present. Character-based `estimated_input_tokens` and
`estimated_output_tokens` are used only when provider counts are absent.

Cost is an Alibaba Cloud list-price estimate computed from those selected token
counts. It is not described as an invoice or exact charged amount. Every row
and the total state whether counts came from `provider`, `estimated`, or were
`unavailable`. Mock counts are labeled separately, remain useful for capacity
inspection, and are always excluded from billable tokens and cost.

## Example

```powershell
$env:QWEN_API_KEY_FILE = (Resolve-Path '.\code\api_keys\qwen-api-key.txt').Path
$env:ARTICLE_COMPILATION_AUTHORITY_KEY = '<caller-owned local secret>'

python `
  '.\code\scripts\run_article_pipeline_integration.py' `
  --question 'Design a broadband one-dimensional antireflection coating.' `
  --run-id 'article-ar-integration-001' `
  --branch-id 'root' `
  --work-dir '.\integration_runs\article-ar-integration-001' `
  --execution-root '.\integration_tmm_runs\article-ar-integration-001' `
  --review-kb '.\path\to\review_knowledge_base.sqlite'
```

Use `--question` instead when no text file is available. Use `--resume` only
with the same immutable question, run ID, branch ID, work directory, and route
limit. `--online-research` creates the existing bounded S2/OpenAlex client;
without it, method research remains local-first and offline.

The fixed summary is
`ARTICLE_PIPELINE_INTEGRATION_SUMMARY.json` inside the pipeline work directory.
It reports receipts, stage statuses, route/binding/planning counts, executions,
trusted descriptors/values/candidates, scheduler state, fail-open availability
signals, hard-failure signals, elapsed time, and Qwen cost telemetry.

The summary also keeps at most 20 bounded attempt records. A normal resume
replaces the top-level state with the equally or more complete recovered
result. If resume fails during integrity preflight and returns no committed
receipts, the prior stage and cost state remains authoritative while the
failed resume is appended to `attempts`; a failed preflight therefore cannot
erase earlier model usage.

Exit codes are `0` for completed, `2` for partial/unavailable (artifacts remain
valid and resumable), `1` for failed/configuration/integrity errors, and `130`
for user interruption.
