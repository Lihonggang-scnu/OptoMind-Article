# Cookbook

Verified end-to-end walkthroughs. Every snippet below was executed against
the released behavior; outputs are abbreviated to the decision-critical
fields.

## 1. Simulate task: preflight → run → verify (CLI)

Task file (`example_task.json`, bundled in the packaged Agent Skill):

```json
{
  "mode": "simulate",
  "simulation": {
    "stack": {
      "layers": [
        {"material": "tio2", "thickness_nm": 60.0},
        {"material": "sio2", "thickness_nm": 90.0}
      ],
      "incident": {"material": null, "constant_n": 1.0},
      "exit": {"material": "sio2"}
    },
    "spectrum": {"start_nm": 500.0, "stop_nm": 600.0, "points": 11},
    "illumination": {"angles_deg": [0.0], "polarizations": ["s"]},
    "solver": "smatrix",
    "requested_outputs": ["R", "T", "A"]
  }
}
```

Chain the three steps (bundled script:
`tmm_engine/skills/veritmm-tmm/scripts/preflight_run_verify.py`):

```text
$ preflight_run_verify.py example_task.json outputs/demo
run: completed | certificate: db8c267c...
verify-run: valid certified passed unsigned -> valid
```

`verify-run` reports the four independent states and the overall summary:
this run is integrity-valid, certified, replay-passed, and (in 1.1) unsigned.

## 2. Python API

```python
from tmm_engine import (
    IlluminationSpec, LayerSpec, MaterialRegistry, MediumSpec,
    SimulationTask, SpectralGrid, StackSpec, TMMWorkbench,
)

task = SimulationTask(
    stack=StackSpec(
        layers=(
            LayerSpec("tio2", 62.5),
            LayerSpec("sio2", 94.8),
        ) * 4,
        incident=MediumSpec.air(),
        exit=MediumSpec("sio2"),
    ),
    spectrum=SpectralGrid(values_nm=(450.0, 550.0, 650.0, 750.0)),
    illumination=IlluminationSpec((0.0, 30.0), ("s", "p")),
)
result = TMMWorkbench(MaterialRegistry()).simulate(task)
print(result.audit["energy_conservation_max_abs_error"])
```

## 3. Agent-safe MCP surface

```bash
pip install "veritmm[mcp]"
veritmm-mcp --artifact-root ./mcp-artifacts --task-root ./mcp-tasks
```

Connect any MCP host over stdio. The server exposes exactly ten tools
(`describe`, `schema`, `examples`, `preflight`, `run`, `history`,
`inspect`, `lineage`, `compare`, `verify_run`); operator parameters
(`skip_certificate`, `physics_python`, convergence tolerances, `device`,
output/store locations) do not exist on this surface. Tasks are accepted
inline, as `veritmm://task/...` resources, or as paths inside the
server-owned task root. Large artifacts are served as
`veritmm://run/{run_id}/{path}` resources.

## 4. Install the Agent Skill into a host

```bash
veritmm install-skill ~/.claude/skills
# destination already exists? explicit confirmation required:
veritmm install-skill ~/.claude/skills --force
```

The skill installs as `<target>/veritmm-tmm` with `SKILL.md`,
`references/` (protocol, artifacts, certificate, boundaries), and
`scripts/`.

## 5. Independent verification of someone else's run

```bash
veritmm verify-run PATH/TO/RUN_DIR --json
```

```json
{
  "integrity_status": "valid",
  "certification_status": "certified",
  "replay_status": "passed",
  "authenticity_status": "unsigned",
  "overall_status": "valid"
}
```

Interpretation rules: a cache replay is `certified` with
`replay_status: unavailable` (normal, not a failure); `uncertified` marks
results that never went through full acceptance checks; an
`integrity_status: invalid` run must not be interpreted scientifically.

## 6. Historical published-v1.0 runs

`verify-run` recognizes published v1.0.0 run directories automatically. When
the historical artifacts carry enough observation data, the verdict is
re-derived through the `LegacyEvidenceAdapter` and compared on its
scientific core; when they do not, the run stays
`certification_status: certified` with `replay_status: unavailable` — the
historical certificate is never rewritten.
