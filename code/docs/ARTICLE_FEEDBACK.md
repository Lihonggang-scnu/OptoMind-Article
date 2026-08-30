# Article Feedback Controller — Stage 7 Design Note

Date: 2026-08-15
Module: `optomind_optics/harness/article_feedback.py`

## Purpose

Stage 7 turns trusted Stage 6 `ObservationCard` history plus an
`ArticleDirectorPlan` into deterministic hypothesis updates, coverage
updates, bounded route scheduling, and an explicit stop decision.  No LLM is
involved: every transition and schedule follows documented program rules.

## Trusted experiment context

`update()` accepts an `experiment_context` (an `ObservationContext`, an
`ExperimentCard`, or an equivalent mapping).  The controller validates that
every observation's `experiment_id` matches the context, that the context
hypothesis IDs exist in the plan, that the context route exists in the
coverage matrix, and that an observation's declared `route_id` (when present)
matches the context route.  Unknown experiment/route/hypothesis bindings are
rejected with actionable errors.  A native Stage 6 `ObservationCard` whose
`hypothesis_updates` is empty is evaluated automatically from the trusted
metrics plus the context: `discriminator_match.matched is True` (with metric
keys present and a non-empty expected discriminator) confirms;
`matched is False` on a physically valid run refutes; declared observable
metric keys present yield partial support; and non-success outcomes yield
`under_test` (execution failure).  Upstream `to_status` values are never
trusted blindly — they are validated against evidence kind, context, current
status, and the forward-only transition map.

## Evidence rules (no hidden semantic inference)

- A `physically_valid` observation only proves a task ran.  It can move a
  hypothesis to `active`/`under_test`/`partially_supported` only with explicit
  `partial_support` evidence.
- Confirmation requires `discriminator_confirmed` evidence whose discriminator
  is actually represented in the trusted metrics: `metrics["discriminator_match"]
  [<hypothesis_id>]["matched"] is True` and the declared `metric_keys` are all
  present in the metrics.
- Refutation requires an explicit `disconfirming` observation on a
  physically-valid run (`discriminator_match.matched is False`).
- `rejected_physics`/`failed`/`needs_higher_fidelity`/`cancelled` are
  execution outcomes: with `execution_failure` evidence they map to
  `under_test`/`active`, never `confirmed`/`refuted`.
- Transitions follow a forward-only `HypothesisStatus` map; identity
  transitions are legal (evidence with no change counts toward
  no-progress).  Terminal states (`confirmed`/`refuted`/`superseded`/`retired`)
  cannot move backward.

## Route scheduling and stop decisions

- Next routes are bounded (`max_next_routes`, default 1) in coverage order:
  `baseline` first when no observation exists; `exploration` when baseline
  failed or produced no valid candidates; `controlled_improvement` after a
  valid baseline; `discriminative_experiments` when ≥2 hypotheses compete;
  `robustness_ablation` after a candidate is supported.  `fresh_replay` is
  deterministic infrastructure and is never auto-scheduled here.  Completed or
  superseded routes are never rescheduled.
- Stop: `stop_budget_exhausted` (caller flag), `stop_no_progress` (configured
  consecutive no-change observations), `stop_completed` (all required planned
  routes completed), `stop_route_exhausted` (no legal route remains), or
  `stop_hard_blocker` (malformed/inconsistent input).  Soft uncertainty stays
  in reasons, never deadlocks.

## Fail-closed and idempotent persistence

- Unknown hypothesis/route IDs, illegal transitions, inconsistent observation
  order, and validation failures return a hard-blocker result with no
  persistence.
- Optional persistence writes only after the whole result validates: a
  `feedback-<controller_id>` Experiment Graph article node with
  hypothesis-update/coverage/observation/decision events, plus append-only
  `RunMemoryRecord` summaries in `ArticleMemoryStore`.  No `FactRecord` is
  created here (Stage 8 owns scientific facts).
- Stable IDs make retries idempotent: an already-present equivalent node or
  memory record is detected and skipped; different content under the same ID
  raises `ArticleFeedbackError`.  Graph writes precede memory writes, so a
  failure can leave a recoverable graph node or partial event history; the
  journal never marks the result complete until every requested store is
  finished, and validation failures never write at all.
- Recovery journal: when `journal_path` is supplied, persistence is split
  from compute with an explicit recovery protocol.  The journal records
  `in_progress` with per-store `graph_written`/`memory_written` flags before
  each write and `completed` only after both stores are done.  A mid-way
  failure raises `ArticleFeedbackError` (never reports completion); retrying
  with the same journal resumes the unfinished store and completes, and a
  completed journal entry makes further retries no-ops.
- Progress is transactional: `progress_before` is the committed state and the
  top-level journal `progress_state` stays at `progress_before` until
  persistence reaches `completed`.  The pending post-round state is stored in
  the controller-specific journal entry (`pending_progress_state`), and the
  controller's in-memory counters are rolled back to `progress_before` when
  `update()` raises, so a retry (same or fresh controller) recomputes the same
  `controller_id`, resumes the same write set, increments the no-progress
  count exactly once, and never stops prematurely.  On success `progress_after`
  is committed exactly once.
- Graph event replay is completeness-aware: `_persist_graph` verifies the
  expected hypothesis/coverage/observation/decision event set against the
  node history, appends only missing equivalent events, rejects conflicting
  content for the same event identity, and only then marks the graph store
  written.  A partial event failure (after node creation or after one event)
  is therefore safely replayable without duplicates.

## No-progress semantics

`no_progress_count` is per hypothesis and counts consecutive rounds (update
calls) in which the hypothesis was evaluated without any status change.
Counters reset when a hypothesis makes progress, are keyed per plan, and
trigger `stop_no_progress` when any hypothesis reaches `max_no_progress`.
Only the current plan's counters are consulted (stale counters from other
plans in a shared journal or `progress_state` mapping are ignored), and
`progress_state` reported/persisted is scoped to the current plan.
The counters are exposed as `ArticleFeedbackResult.progress_state` and are
persisted in the recovery journal (top-level `progress_state`), so a fresh
`ArticleFeedbackController` resumes the same per-hypothesis counters across
rounds; callers may also pass `progress_state` explicitly.

`controller_id` includes the current plan's pre- and post-round progress
state, so two rounds with the same observation input but different progress
state produce distinct controller identities (distinct journal/graph/memory
records) instead of silently reusing an old completed result with a different
stop decision.  An exact retry (same inputs and same pre-state) remains
idempotent with the same controller identity.

## Stage 8 Claim Ledger handoff

Stage 8 will consume the supported/confirmed hypotheses and their
provenance (observation/experiment/artifact IDs) to build `ClaimCard`
records backed by `FactRecord`s whose statements carry source artifact IDs.
The feedback controller's `HypothesisUpdateDecision` records are the audit
trail that binds each claim to the trusted observations that produced it.
