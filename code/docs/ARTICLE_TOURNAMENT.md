# Article Research-Strategy Tournament (Stage 16)

Stage 16 is a deterministic, offline research-strategy tournament over
immutable historical real TMM trace banks (default: the two
`accepted_examples/` banks with three executed routes each).  It compares
four named policies under identical revealed-information and route-count
budget contracts.  This is policy replay, not fresh physics execution: no
Qwen, no network, no solver, no TMM, and no fabricated or recomputed
physical result.

## Trace bank loader

`load_trace_bank(run_dir)` reads `RESEARCH_RESULT.json`,
`ITERATION_HISTORY.json`, `STRATEGY_PLAN.json`, and `FEEDBACK_HISTORY.json`
and binds each with relative path, SHA256, size, run/question identity, and
the content-addressed `trace_id` (run + question + source hashes).  It
rejects missing files, duplicate or unknown iteration/route IDs,
non-finite or malformed values, and cross-file inconsistency:

- `FEEDBACK_HISTORY.json` must equal the embedded
  `RESEARCH_RESULT.feedback_history`.
- every `RESEARCH_RESULT.strategy_plan.routes` entry must match the
  corresponding `STRATEGY_PLAN.json.plan.routes` descriptor exactly;
- every iteration's full science payload (scores, candidates, identities,
  failure categories, budget usage excluding telemetry wall time) must be
  identical between `ITERATION_HISTORY.json` and `RESEARCH_RESULT.iterations`.

Best target/robustness scores are optional; `null` scores load and are
ignored by the evaluator/oracle (never turned into zero physical results).
Planned-but-not-executed routes from `STRATEGY_PLAN.json` are preserved
separately as `not_run`.

## Public vs hidden information

- `PublicRouteDescriptor` (visible before selection): route id, title,
  priority, kind, materials, topology, layer count (design-variable count),
  design principle, hypothesis, soft objectives, and planned risks.
- `RevealedOutcome` (hidden until selected): best target/robustness scores,
  candidate counts, selected candidate identities, failure categories,
  experiment ids and selected roles, budget usage, paths, and the full
  `VerifiedCandidateRecord` list (candidate id, experiment id, optimizer id,
  certificate id, artifact ids, objective/robustness report presence, scores,
  candidate hash).

Strategies receive only a narrow `StrategySnapshot`; the full pool of hidden
outcomes is visible only to the evaluator after a trace completes.  Every
snapshot is built from deep detached JSON/model copies of revealed outcomes
and decision state, so a strategy mutating a nested `budget_usage`,
`selected_roles`, or state mapping can neither corrupt the source bank nor
change the runner state; the runner detects any in-place snapshot mutation
(canonical before/after hash) and terminates with `invalid_strategy`.
`StrategyChoice` and its `next_state` must be JSON-finite/serializable before
acceptance.  Each trace runs on a fresh strategy clone so independent budget
runs cannot leak hidden outcomes through mutable strategy instance state.

## Policies

- `legacy_template`: planned priority/source-order replay, bounded by the
  route budget.
- `staged_tree`: AI-Scientist-inspired staged exploration over public
  descriptors and revealed history only (clean deterministic
  reimplementation; no upstream code is copied or called).
- `atomic_improvement`: AIDE-inspired smallest public design delta from the
  current selected route, with a deterministic emulated parent/child
  selection record; source lineage is never rewritten.
- `optomind_hybrid`: balances public design diversity and central complexity
  first, then uses only revealed marginal gain and remaining budget for
  selection/stop.

## Fairness and budgets

Every policy runs on every bank at every route budget `1..N` where `N` is the
executed route count.  Budgets must be unique non-bool integers within
`1..N`; strategy id/version pairs must be unique.  Duplicate or unknown
selections terminate a run with `invalid_strategy`.  Stop reasons are
`budget_exhausted`, `policy_stop`, `pool_exhausted`, and `invalid_strategy`;
an early stop is scored as-is and never silently topped up.

## Metrics, composite, and Pareto

The evaluator computes a deterministic metric vector per (bank, policy,
budget) from real fields only:

- coverage (public design dimensions);
- experimental gain with explicit oracle regret (missing scores ignored);
- discrimination;
- fact yield (only certificate + artifact + objective + robustness backed
  candidate records, never candidate-count summaries);
- figure readiness (only explicit per-candidate visual/table artifact
  identities with genuine extensions; otherwise 0 with an explicit reason);
- robustness/ablation coverage;
- optimizer/ablation coverage from real candidate records;
- validity ratio;
- route cost/efficiency;
- stop quality (saved cost only for `budget_exhausted`/`policy_stop`,
  never `invalid_strategy`; for `policy_stop` the diminishing-return evidence
  is `1 - normalized(last best-so-far frontier gain / pool target scale)`, so
  stopping after a large last improvement is not rewarded and stopping after
  a no-improvement step is supported; `budget_exhausted`/`pool_exhausted`
  and `invalid_strategy` get no last-step marginal-gain component, making
  full-pool traces with the same selected set/cost order-invariant);
- checkpoint/resume equivalence (computed per policy/budget during the
  tournament, recorded as 1/0 with audit detail);
- provenance preservation (computed from the actual per-trace ledger and
  candidate identities, or marked not applicable).

Normalized values are clamped to `[0, 1]`; NA dimensions carry explicit
reasons.  The documented composite uses stable public weights that sum to 1
(renormalized over available dimensions).  Pareto membership is computed
across **all** policies for the same bank and route budget after every
policy vector exists, and vector content hashes are recomputed after the
flag is set.  No absolute single winner is forced.

## Checkpoint/resume

Versioned JSON checkpoints bind trace id, strategy id/version, budget,
selected order, revealed outcome hashes, next decision state, evaluator
contract version, and source hashes.  Before resume, validation verifies the
recomputed unkeyed checkpoint id, selected-order uniqueness/knownness/budget,
revealed keys/order exactly matching `selected_order`, every revealed outcome
byte-equal to the bank's canonical hidden outcome, structural stop legality,
and source/strategy/budget/contract identity.  A valid resume produces a
byte-equivalent canonical result.

## Result, audit, validator, and writer

`run_tournament(trace_dirs, ...)` builds a `TournamentResult` with dynamic
limitations (actual bank count and executed route counts) and per-bank
results: public pool, planned-not-run descriptors, budget curve, full-pool
oracle, a post-hoc immutable `outcome_inventory` (every executed route's
canonical revealed outcome with candidates and hashes; evaluator/result-only
and never visible to strategies), policy traces/vectors, and an exact
per-(strategy, version, budget, route) audit ledger preserving selected,
unselected, failed/negative, rejected-invalid, and not-run states with
candidate identities and hashes.

`validate_tournament_result` is a semantic validator: it reconstructs the
deterministic bank/evaluator view from the public pool, outcome inventory,
source bindings, and planned-not-run descriptors, then recomputes the
full-pool oracle, exact per-trace audit ledgers, every deterministically
derivable metric (raw/normalized/reason/composite), Pareto membership across
all policies at each budget, vector/trace/result hashes, and all identity
relations.  Checkpoint/resume equivalence is recomputed directly for the four
registered built-in strategies; an unknown custom strategy must mark the
checkpoint metric not-applicable in persisted output instead of trusting a
claimed value.  A rehashed forged metric, composite, inventory entry,
Pareto flag, or ledger row is rejected.  `write_tournament_result` validates
before writing `ARTICLE_TOURNAMENT_RESULT.json` atomically and may accept
`trace_dirs` to bind the formal output back to actual source bytes; exact
replay is idempotent and conflicting or stale content is rejected.

## Limitations

This is retrospective strategy replay over the provided historical trace
banks.  It is not fresh solver performance, not a claim of general
scientific superiority, and not a substitute for real Qwen, TMM, or
Pandoc/PDF integration validation.  Composite ranking is one documented
view; Pareto membership is reported alongside.
