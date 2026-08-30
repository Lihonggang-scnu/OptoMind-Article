# TMM Article Publication

## Purpose

`TMMArticlePublisher` converts one completed and accepted TMM research run into
an English computational article package. It is a reusable final-mile module,
not a question-specific template.

The numerical chain remains authoritative:

```text
accepted TMM run
  -> immutable fact registry
  -> deterministic figures, tables and numerical sections
  -> bounded Qwen title/narrative audit
  -> citation and fact-token validation
  -> existing LaTeX renderer
  -> Markdown + JSON + TeX + PDF + arXiv source bundle
```

Qwen is fixed to `qwen3.7-flash`. It may improve the title and audit the
scientific narrative, but it cannot invent or alter a solver value. Exact
stacks, thicknesses, scores, robustness statistics and route outcomes are
reconstructed from run artifacts after generation.

## Entry point

```powershell
py -3.11 scripts/run_tmm_article_publication.py `
  --run-dir outputs/tmm_research_harness/<accepted-run> `
  --output-dir outputs/tmm_article_publication/<publication-run>
```

Useful controlled variants:

- `--force-mock`: deterministic offline contract test.
- `--draft-path`: rerender an already generated article without a writing call.
- `--bibliography-cache`: reuse prior metadata enrichment during a zero-Qwen rerender.
- `--no-reference-enrichment`: prohibit network metadata enrichment.
- `--no-compile`: generate source artifacts without compiling PDF.

## Required input

The run directory must contain `RESEARCH_RESULT.json` with a terminal accepted
status and traceable iteration artifacts. Incomplete, unverified or failed
runs are rejected rather than presented as articles.

## Main outputs

- `TMM_ARTICLE.md`: readable manuscript with fact and citation markers resolved.
- `TMM_ARTICLE.json`: structured manuscript and provenance.
- `TMM_ARTICLE_FACTS.json`: immutable numerical fact registry.
- `TMM_ARTICLE_AUDIT.json`: deterministic and model-assisted audit record.
- `TMM_ARTICLE_PUBLICATION_REPORT.json`: status, cost, elapsed time and file map.
- `figures/`: solver-derived spectrum, portfolio and robustness graphics.
- `latex_en/main.tex`: compilable article source.
- `latex_en/main.pdf`: rendered article.
- `latex_en/arxiv-source.zip`: portable source package.

## Publication states

- `compiled`: scientific and publication checks passed, including supplied author metadata.
- `compiled_awaiting_metadata`: the scientific article passed, but author/affiliation data are placeholders.
- `failed`: a numerical, citation, rendering or integrity gate failed.

`compiled_awaiting_metadata` must not be represented as submission-ready. It is
a complete computational article draft awaiting real author information.

## Quality boundaries

- Exact scientific values come only from accepted solver artifacts.
- Incomplete reference metadata are omitted rather than rendered as placeholders.
- Internal workflow vocabulary and unsupported experimental claims are blocked.
- Existing figures are regenerated from traceable arrays; no chart is fabricated by an image model.
- The renderer validates scientific symbols and compiles the final PDF before success is reported.
