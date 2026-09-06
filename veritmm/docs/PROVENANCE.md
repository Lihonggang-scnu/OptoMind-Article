# Provenance graph v1

`tmm_engine.provenance_graph` answers "why is experiment E73 better than
E54" with one graph query instead of archaeology over run directories.  It is
a lightweight, AiiDA-inspired projection over the artifacts that already
exist — **the graph is a projection, not a source of truth**: the facts live
in the run artifacts, `build_graph` writes nothing, and v1 rebuilds the whole
graph from scratch (no incremental updates; fast for hundreds of runs).

## Nodes and edges

| node type | kinds | identity |
|---|---|---|
| `data` | `task`, `spectrum`, `certificate`, `metric_snapshot` | content hash (task SHA-256, artifact SHA-256, …) |
| `calculation` | `engine_run` | hash of (run identity + task hash + certificate id) |
| `decision` | `acceptance_decision` | hash of certificate verdict fields (accepted, status, failure codes) |
| `workflow` | `sweep_container` | hash of the sweep result artifact |

Edges carry `(src_hash, dst_hash, edge_type, evidence_path)` with verb-aligned
semantics: `input` (data → the process using it), `created_by` (calculation →
the data it created), `returned_by` (workflow → data it returns),
`decided_by` (certificate → decision), `part_of` (child calculation →
workflow container).  Invariant (test-pinned): the data-provenance subgraph
— Data + Calculation nodes joined by `input`/`created_by` — is a **DAG**.

## Building

```bash
veritmm lineage-graph outputs/run_a outputs/run_b --json
```

`build_graph([run_dir, ...])` first establishes each directory's integrity
with the existing `verify-run` machinery (same hash/size criterion, no
reimplementation) and records `integrity_status` per run; a tampered artifact
therefore surfaces as an invalid run report on rebuild.  Building is
idempotent: identical inputs produce byte-identical canonical JSON
(`schema_version: "veritmm-provenance-v1"`), and `save`/`load` round-trips
that form.

## Explaining superiority

```bash
veritmm explain-superiority outputs/run_a outputs/run_b --json
```

`explain_superiority(run_a, run_b)` returns the metric comparison (mean R per
run, the v1 heuristic), a verdict naming the superior run, and — per run — the
full evidence chain

```text
task → engine_run → spectrum → certificate → metric_snapshot → decision
```

with every step pointing at an existing artifact file plus its SHA-256.  The
comparison is explicitly labelled a heuristic; the acceptance certificate
remains the only validity claim.  This is the feedstock for OptoMind O-10
insight generation: the chain is the attribution ("these thicknesses produced
this spectrum, this metric, this verdict"), the difference in metric snapshots
is the reason ("mean R 0.254 → 0.556"), and the evidence pointers make every
claim checkable without trusting the narrator.
