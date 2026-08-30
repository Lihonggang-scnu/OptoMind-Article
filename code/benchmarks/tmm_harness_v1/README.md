# TMM Harness v1 frozen benchmark split

This directory contains the frozen, TMM-domain-only benchmark split for the optical Harness.
It has five development tasks (`DEV01`-`DEV05`) and five sealed holdout tasks
(`HOLDOUT06`-`HOLDOUT10`). The task JSON files contain questions and artifact contracts,
not expected spectra or winning thicknesses.

Only `DEV01`-`DEV05` may influence implementation or prompt tuning. `HOLDOUT06`-`HOLDOUT10`
are sealed for final/user randomized evaluation and must not be used for implementation or
prompt tuning.

Performance targets are soft scores. Deterministic physics validity is the only admission
gate for every task; a target score is never a physics-validity gate.

The only allowed LLM in later Harness work is `qwen3.7-flash`. This benchmark layer is
deterministic and uses no model: loading tasks performs no simulation and makes no LLM call.

`split_manifest.json` records the stable IDs and SHA-256 digest for each task file. The
loader checks the selected file against that manifest before returning immutable task models.
Holdout loading requires both an explicit `allow_holdout=True` argument and
`OPTOMIND_ALLOW_TMM_HOLDOUT=1` in the environment.
