# VeriTMM

**An AI-ready, verifier-first transfer-matrix tool for autonomous
multilayer-optics research.**

VeriTMM executes passive, isotropic, planar 1-D multilayer optics and issues
a machine-readable physics acceptance certificate for every result. The
division of labor is fixed: **the agent proposes, TMM computes, and the
deterministic verifier certifies.** Neither an AI agent nor an optimizer may
certify its own output.

```text
agent request → safe managed execution → evidence → policy → certificate → independent verify-run
```

## Highlights

- **Verifier-first physics**: energy conservation, passivity, spectral
  convergence, independent cross-solver agreement, and a high-precision
  mpmath referee — checked before a result is accepted.
- **AI-facing protocol**: capability discovery, JSON Schema task contracts,
  no-spectrum preflight, typed failures, compact run envelopes.
- **Portable evidence**: persisted `VERIFICATION_EVIDENCE.json` and
  `VERIFICATION_POLICY.json` let a third party replay the verdict offline
  with `verify-run` (four independent states: integrity, certification,
  replay, authenticity).
- **Agent-safe MCP stdio surface** with an explicit allowlist
  (`veritmm-mcp`), a packaged Agent Skill (`veritmm skill-path` /
  `install-skill`), and an offline AgentBench with zero unsupported-physics
  false acceptance.
- **Reproducibility semantics**: byte-identical identities under the
  declared canonical scheme, tolerance-equivalent numerics, environment
  fingerprints, and cross-platform CI enforcement.

## Quick start

```bash
pip install veritmm
veritmm describe --json
veritmm preflight TASK.json --json
veritmm run TASK.json --output-dir outputs/demo --json
veritmm verify-run outputs/demo --json
```

The [cookbook](cookbook.md) walks through the full chain; the
[architecture](ARCHITECTURE.md) page explains how the pieces fit together.

## Where to go next

- [Cookbook](cookbook.md) — verified end-to-end walkthroughs (CLI, Python,
  MCP, Agent Skill).
- [Architecture](ARCHITECTURE.md) — the verifier-first design.
- [Validation](VALIDATION.md) — acceptance layers and the two
  reproducibility levels.
- [Agent Skill](AGENT_SKILL.md) — the packaged skill for AI hosts.
- [v1.1 proposal (中文)](UPGRADE_PROPOSAL.zh-CN.md) — the planning document
  behind this release line.
