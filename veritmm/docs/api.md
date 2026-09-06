# API reference

Selected public surface of `tmm_engine`. The full CLI protocol is documented
in the packaged Agent Skill's `references/protocol.md` and in
`veritmm describe --json`.

## Tasks

::: tmm_engine.schemas.SimulationTask
::: tmm_engine.schemas.StackSpec
::: tmm_engine.schemas.LayerSpec
::: tmm_engine.schemas.MediumSpec
::: tmm_engine.schemas.SpectralGrid
::: tmm_engine.schemas.IlluminationSpec

## Execution and certification

::: tmm_engine.workbench.TMMWorkbench
::: tmm_engine.acceptance.AcceptanceSettings
::: tmm_engine.acceptance.certify_simulation
::: tmm_engine.acceptance.evaluate_evidence
::: tmm_engine.acceptance.collect_verification_evidence
::: tmm_engine.acceptance.VerificationEvidence

## Independent verification

::: tmm_engine.verify_run.verify_run_dir

## Artifacts and identity

::: tmm_engine.reproducibility.reproducibility_block
::: tmm_engine.verification_artifacts.load_verification_evidence
::: tmm_engine.verification_artifacts.load_verification_policy
::: tmm_engine.verification_artifacts.write_verification_artifacts
::: tmm_engine.legacy_evidence.build_legacy_evidence
