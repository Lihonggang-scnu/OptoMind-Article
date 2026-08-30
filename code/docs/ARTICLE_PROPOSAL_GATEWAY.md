# Article Proposal Compiler & Tool Gateway — Stage 5 Design Note

Date: 2026-08-15
Modules: `optomind_optics/harness/article_proposals.py`,
`optomind_optics/harness/article_gateway.py`
Prompt: `prompts/optical_harness/Article Experiment Proposal.txt`

## Purpose

Stage 5 draws the execution boundary of the Article Scientific Harness: a
model (locked to `qwen3.7-flash`) may draft a strictly bounded
`ExperimentProposal` envelope, and nothing more.  Program code compiles that
envelope into an immutable `CompiledExperimentRequest`, and a local
`ArticleToolGateway` delegates only such compiled requests to an explicit
deterministic executor adapter.  Raw model JSON is never executable input.

## Contracts (`article_proposals.py`)

- `ExperimentProposal` (schema `experiment-proposal.v1`, frozen, extra
  forbidden): proposal_id, hypothesis_ids (1..8, unique), stage, action_type,
  bounded `parameters` (per-action key allowlist; scalars or scalar lists only;
  documented string/list/numeric bounds), `atomic_change` and
  `expected_discriminator` (bounded mappings), rationale/uncertainty (bounded
  text), `requested_budget` (only known resource keys, finite non-negative),
  locked `model_name`.  Forged fields such as results, certificates, metrics,
  permissions, or executable code are rejected by `extra="forbid"`.
  `stage` is restricted to the experimental-stage allowlist
  (`baseline_experiments`, `exploration`, `controlled_improvement`,
  `discriminative_experiments`, `robustness_ablation`); later pipeline stages
  and `fresh_replay` are never proposable.
- `CompiledExperimentRequest` (schema `compiled-experiment-request.v1`,
  frozen, extra forbidden): request_id, deterministic `task_hash`, plan_id,
  capability_id, run_id, branch_id, proposal_id, authority_id,
  compiler_attestation, normalized action `parameters`, `requested_budget`,
  optional local `budget_lease_id`, the reused `ExperimentCard`,
  allowed_action, `source="article_compiler"`, `status="compiled"`.
- `compile_proposal(proposal, *, plan, run_id, branch_id, authority,
  budget_lease_id=None, available_budget=None)` is deterministic
  and fail-closed: it rejects incompatible/ambiguous capability, unknown
  hypothesis IDs, non-whitelisted actions, non-experimental stages, budget
  overflows (documented caps: 86 400 s, 100 000 forward evaluations, 200
  optimizer runs, 100 Qwen calls, 2 000 000 input tokens, 500 000 output
  tokens, ¥100; plus a caller-supplied `available_budget` mapping when given),
  and empty `proposal_id`/`run_id`/`branch_id`.  The request preserves
  normalized action parameters and the requested budget (nothing disappears
  after compilation) and carries a local HMAC compiler attestation.  The
  program creates the request ID, task hash, experiment card, budget lease
  reference, and status; the model never does.

## Compilation authority (provenance, not just hashing)

`ArticleCompilationAuthority` is a local HMAC-SHA256 keyed attestation.  The
key is caller-supplied and never appears in Qwen input or in any serialized
request.  `compile_proposal` requires an explicit authority and signs the
canonical request content (including `authority_id`, parameters, requested
budget, lease, and experiment contract) plus the task hash.  The gateway is
constructed with the same authority and rejects missing/invalid attestations
and wrong `authority_id` before any adapter is invoked.  Manual reconstruction,
`model_copy` with a recomputed public task hash, or a different key cannot
produce a valid attestation, so a self-consistent public hash is no longer
sufficient proof of local compilation.

`request_id` is protected twice: it is covered by the HMAC attestation payload
and is deterministically recomputed from `task_hash` + `proposal_id` during
gateway authorization, so a signed request whose `request_id` was changed is
rejected before any adapter is invoked.

## Gateway (`article_gateway.py`)

- `ArticleToolGateway(authority=..., allowed_actions=..., run_id=...,
  branch_id=...)`:
  - `authorize(request)` validates that the request is a locally compiled
    request, the compiler authority matches, the task hash recomputes to the
    stored value, the HMAC attestation verifies, the action is in the gateway
    allowlist (default: the TMM work set — generate_baseline, run_solver,
    run_optimizer, run_convergence_audit, run_reference_solver,
    run_robustness_audit), and run and branch identity match.  Failures raise
    `GatewayAuthorizationError`.
  - `execute(request, adapter)` never accepts raw model envelopes: a raw
    envelope returns a structured `GatewayRejection`
    (`direct_model_execution`), and an authorized compiled request is
    delegated only to the explicit `DeterministicExecutorAdapter`.  The
    adapter result is stripped to `GatewayAdapterResult` (request_id,
    adapter_name, status, summary, reason, output_refs, telemetry) — it cannot
    carry metrics, an ObservationCard, artifacts, or a physics certificate.
  - `reject_raw_model_envelope(envelope, reason=...)` always returns a
    structured rejection; it never parses the envelope as executable input.
- The gateway does not certify physics and does not mint observation cards;
  the existing deterministic TMM compiler/orchestrator remains the execution
  and certification authority.  Stage 5 only authorizes and delegates an
  attested request to an explicit deterministic adapter; Stage 6 will add the
  concrete TMM adapter and ObservationCard normalization.

## API example

```python
from optomind_optics.harness.article_proposals import (
    ArticleCompilationAuthority,
    compile_proposal,
)
from optomind_optics.harness.article_gateway import ArticleToolGateway

authority = ArticleCompilationAuthority(b"local-caller-supplied-key")

request = compile_proposal(
    proposal,                        # validated ExperimentProposal (model draft)
    plan=plan,                       # ArticleDirectorPlan
    run_id="run-1",
    branch_id="root",
    authority=authority,             # required local compilation authority
    budget_lease_id="lease-7",       # optional, caller-supplied only
    available_budget={"forward_evaluations": 500},
)

gateway = ArticleToolGateway(
    authority=authority,             # same authority required
    allowed_actions=None,            # default TMM work allowlist
    run_id="run-1",
    branch_id="root",
)
outcome = gateway.execute(request, deterministic_adapter)
```

## Boundaries and handoff

- Soft rationale/uncertainty may be retained for later review but never
  influence execution authorization.
- Budget reservation/commit is deferred to Stage 6: the adapter pipeline will
  reserve `requested_budget` through `BudgetScheduler` under the local
  `budget_lease_id` before execution and commit/release after; the gateway
  never mutates a budget ledger itself.
- Documented bounds are deliberately not tiny: parameter strings ≤ 500 chars,
  lists ≤ 64 items, nested mappings ≤ 16 keys, output refs ≤ 64, telemetry
  keys ≤ 64.
- Stage 5 handoff: the Article director (Stage 4) supplies the plan and
  capability; proposals are compiled against it; the gateway hands an
  authorized `CompiledExperimentRequest` to a deterministic adapter whose
  normalized result must be further processed by the existing TMM authority
  before any ObservationCard or physics acceptance can exist.
