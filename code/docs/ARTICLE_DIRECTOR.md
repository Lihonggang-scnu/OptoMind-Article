# Article Scientific Director — Stage 4 Design Note

Date: 2026-08-15
Module: `optomind_optics/harness/article_director.py`
Prompt: `prompts/optical_harness/Article Scientific Director.txt`

## Purpose

The director is the bounded planning head of the isolated Article Scientific
Harness.  It turns one problem analysis plus its method-research evidence into
an auditable Research Charter, capability decision, candidate hypotheses,
coverage matrix, and multi-stage research plan.  It reuses the existing
contracts (`ResearchCharter`, `CoverageMatrix`/`CoverageRow`,
`CoverageStatus`, `ArticleStage`, `ArticleDecision`, `OpticalProblemAnalysis`,
`TMMCompatibility`, `MethodResearchReport`, `QwenFlashOnlyClient`) instead of
duplicating them.

## Strict director models (`article_director.py`)

Literal schema versions, `frozen=True`, `extra="forbid"`:

- `CapabilityDecision` — capability_id, status (compatible/ambiguous/
  incompatible), supported_scope, unsupported_requirements,
  accepted_assumptions, clarification_questions, recommended_next_action.
- `HypothesisCandidate` — hypothesis_id, statement, falsifiable_prediction,
  expected/disconfirming observations, evidence_ids, theory_basis, route_kind,
  parent_hypothesis_id, novelty_rationale, risk_notes.
- `DirectorStagePlanItem` — item_id, stage, objective,
  required_input_domains, outputs, stop_conditions, depends_on,
  status (planned/not_run).
- `ArticleDirectorPlan` — plan_id, question, charter, capability, hypotheses,
  coverage_matrix, stage_plan, research_influence, unresolved_decisions.
- `ArticleDirectorResult` — status (planned/invalid/unavailable), optional
  plan, attempts, validation_errors, normalization_warnings, usage,
  `model_name` locked to `qwen3.7-flash`.

## API

```python
result = ArticleDirector().plan(
    question,            # exact original question (never translated/replaced)
    analysis,            # OpticalProblemAnalysis or mapping
    method_research,     # MethodResearchReport or mapping
    prior_observations=(),  # compacted; never changes capability
    force_mock=None,     # True -> deterministic offline mock draft
)
```

## Deterministic program layer vs Qwen drafting layer

The program always creates locally: IDs, schema markers, capability decision,
charter (including every explicit user fact from the analysis: question,
scope, goals/observables, constraints, materials/wavelengths/angles/
polarizations, secondary intents, design variables, suppressed behaviors,
ambiguities, manufacturing constraints, assumptions), coverage rows, stage
plan, statuses, model name, and usage telemetry.  Qwen (locked to
`qwen3.7-flash`) drafts only:
`hypotheses`, `research_influence`, `unresolved_decisions`.  Its input is a
bounded payload (documented compact view): original question, normalized
analysis, at most 40 evidence records (id/source/paper/title/depth/allowed-use
and a 600-char excerpt), at most 20 method findings, allowed evidence IDs, and
at most 20 compacted prior observations.  No telemetry or KB dump is sent.
`prior_observations` is normalized from the iterable to a list before the
bounds are applied, so generators and tuples work.  `allowed_evidence_ids` is
derived from the evidence records actually visible in the prompt; if the
report has more than the bound, a normalization warning is emitted and the
model may only cite the visible set (local validation uses the same set).

## Honesty and fail-closed behavior

- Capability is classified deterministically from
  `OpticalProblemAnalysis.compatibility`; Qwen cannot override it.  Compatible
  -> supported scope and planned routes; ambiguous -> clarification questions
  and all experiment routes/stages marked `not_run` with a reason;
  incompatible -> unsupported requirements and an `invalid` result with no
  plan.
- Every hypothesis `evidence_id` must belong to the supplied method research;
  a theory-only candidate is allowed when no evidence exists.  Unknown IDs,
  empty statements, missing falsifiable predictions, or empty drafts produce a
  clear `invalid` result, never silent repair.
- Qwen unavailability or non-JSON output produces an honest `unavailable`
  result with usage telemetry; there is no fallback model and no silent
  hypothesis synthesis.
- Coverage rows never claim experiment evidence or certificates:
  `evidence_artifact_ids` is empty and `executed_iteration` is unset at plan
  time.  Stage plan items are never marked `completed` before execution.
- `force_mock=True` returns a deterministic, clearly-marked offline draft
  (`mock_llm: true`) so tests and dry runs need no network or credentials.

## Stage plan and coverage matrix

The deterministic stage plan covers the Article workflow through
`publication_package` in this order: charter locked, capability classified,
literature integrated, coverage matrix locked, hypotheses formed, baseline
experiments, exploration, controlled improvement, discriminative experiments,
robustness/ablation, hypothesis update, claim ledger, figure-first planning,
section writing, fact/scientific/expression review, author revision, fresh
replay, and publication package.  Stages are never marked completed before
execution.  The coverage matrix has six deterministic rows:
`baseline`, `exploration`, `controlled_improvement`,
`discriminative_experiments`, `robustness_ablation`, `fresh_replay` — all
`planned` for compatible capability, all `not_run` with an explicit reason for
ambiguous capability.
