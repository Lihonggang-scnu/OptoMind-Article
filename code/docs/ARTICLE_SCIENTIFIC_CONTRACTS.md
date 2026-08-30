# Article Scientific Contracts — Phase 2 Design Note

Date: 2026-08-15
Scope: typed scientific data contracts + backward-compatible Experiment Graph
extension.  No TMM solver, compiler, or research-orchestrator behavior changes.

## Purpose

Phase 2 gives the Article Scientific Harness a typed, versioned vocabulary for
the scientific workflow that will eventually make the Experiment Graph the
single authoritative state source.  This phase only adds contracts and graph
capabilities; run-directory artifacts remain authoritative for now and all
existing consumers are preserved.

## Contracts (`optomind_optics/harness/article_contracts.py`)

Typed pydantic v2 cards, each with a literal `schema_version`:

- `ResearchCharter` — question, scope, goals, budget, deliverables, stage.
- `HypothesisCard` — hypothesis statement, status, lineage, evidence IDs,
  hypothesis-update trail.
- `ExperimentCard` — hypothesis IDs, atomic change, expected discriminator,
  budget lease reference, parent experiments, status.
- `ObservationCard` — experiment status, metrics, artifacts, failure records,
  failure diagnosis, hypothesis updates.
- `ClaimCard` — evidence-bound claim with strength, scope, and counter-evidence.
- `FigureCard` — figure story role, chart spec, data-source artifacts.
- `ReviewCard` — scientific/expression/fact/integrity/safety review with
  severity and decision (soft findings never block publication by themselves).
- `CoverageMatrix` / `CoverageRow` — planned vs executed vs `not_run` route
  coverage, the regression contract for the broadband-AR route discrepancy.
- `ArticleNodePayload` — the versioned payload an article graph node carries:
  stage, hypothesis IDs, atomic change, expected discriminator, observation
  and artifact references, hypothesis update, budget lease reference, failure
  diagnosis, and stop/recovery decision.

Enums: `ArticleStage`, `ArticleDecision`, `HypothesisStatus`, `ClaimStatus`,
`ClaimStrength`, `ReviewKind`, `ReviewSeverity`, `ReviewStatus`,
`FigureStatus`, `CoverageStatus`, `ArticleEventType`.

Rules:
- Required fields reject missing/malformed input (`ValidationError`).
- Optional fields default to empty containers and survive JSON round-trips.
- `schema_version` is a `Literal`; mismatched versions are rejected.
- Unknown extra fields are ignored for forward tolerance.
- `validate_article_event(event_type, payload)` validates event type,
  schema version, and payload shape, returning a normalized deterministic
  dict.  Unknown event types and malformed payloads raise
  `ArticleEventValidationError`.

## Graph extension (`optomind_optics/harness/experiment_graph.py`)

Backward compatibility:
- The `nodes` table gains additive columns (`node_kind`, `article_json`) via
  idempotent `ALTER TABLE` migration; existing rows keep `node_kind='tmm'`.
- `create_node`, `record_event`, `set_status`, `node`, `frontier`, and the
  legacy export keys (`schema_version`, `run_id`, `nodes`) behave as before.
- `node()` output shape is unchanged for TMM nodes.
- `frontier()` returns TMM leaves considering only TMM children: an Article
  child never removes a TMM node from the TMM frontier.
- `create_node` and `create_article_node` validate every parent id against the
  same run before inserting anything; missing or cross-run parents raise
  `KeyError` and leave no partial node, edge, or event behind.

New article APIs:
- `create_article_node(payload, parent_ids=..., node_id=...)` — creates an
  article node with a versioned payload; parents may be TMM or article nodes
  (cross-kind lineage).  Records an initial `proposed` status event and, if a
  stage is present, an initial `article.stage` event.
- `record_article_event(node_id, event_type, payload)` — validates and appends
  an `article.*` event.  Unknown node/run IDs raise `KeyError`; events on TMM
  nodes, unknown event types, and bad payloads raise `ValueError` /
  `ArticleEventValidationError`.
- `set_article_stage`, `set_article_decision`, `record_hypothesis_update`,
  `record_observation`, `record_coverage`, `record_charter` — typed append-only
  event helpers.
- `article_node(node_id)` — replayed Article view: payload, parent/child
  lineage, latest status, latest stage, latest decision, hypothesis-update
  list, and full append-only history.
- `article_frontier()` — leaf article nodes.
- `export()` — keeps existing keys and adds `article_schema_version` +
  `article_nodes` (versioned Article-compatible graph view).

## Invariants

- Append-only: every transition is a new event; nothing is ever rewritten.
- Latest status/stage/decision are always replayed from events, never stored
  as mutable state.
- Article events are schema-versioned and type-validated before persistence.
- Physical certification and scientific integrity remain separate from prose
  quality; review cards are soft findings unless marked blocking by integrity
  rules.
- Planned routes must reach an explicit terminal marker (`completed` or
  `not_run`) before a run may be considered complete.
