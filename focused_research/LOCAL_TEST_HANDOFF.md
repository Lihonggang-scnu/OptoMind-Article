# Local test handoff: RGB/NIR task-sheet generation

## Source and ownership

Start from the cloud-provided transport revision containing this document, based on `a8053a849298f88cb18dc4f05b519928ee9c7fa3`. Confirm the delivered commit/hash with the parent before testing; the base hash alone does not include this update. Do not test a stale local checkout. Cloud owns core design, implementation and prompt changes; local work is testing and minor environment fixes only. Report design/implementation defects back to cloud. This priority replaces the not-yet-started T018 run; do not start that old schedule.

The new contract is task_sheet.v2. The runtime example uses the separately versioned **rgb_nir_question.v2.json** demonstration question: no crosstalk requirement, explicitly nonpolarized equal-s/p light and wavelength-integral band means. Historical v1 remains unchanged. Check G1/G2 map to supported mean_reflectance/mean_transmittance metrics with maximize, the stated bands, 45 degrees, unpolarized, and wavelength_integral_mean. Coupling is allowed without fabricated weights/thresholds. Genuine application context is separate; an unsupported original requirement must be preserved under unsupported_requirements with readiness=needs_revision, never hidden as context or converted into a proxy. A ready sheet means objective mapping only; no physical/retrieval acceptance is implied. Later forward use must include exact band endpoints and sufficient interior sampling; the existing reducer does not fill missing endpoints. Existing default unit weights are not authorization for an equal-weight combined score.

Publication and live testing remain on hold until cloud review and the parent release the final revision. The previously stated budget bounds below are ceilings, not permission to bypass this hold.
## First stage: offline only

Read the checkout's instructions and `focused_research/README.md`. Run focused tests with sockets disabled using installed dependencies. Cloud's command is:

```bash
PYTHONPATH=/tmp/optomind-core-testdeps python -m pytest -q --disable-socket focused_research/tests
```

Use a local equivalent dependency path if needed; do not assume the cloud `/tmp` directory exists locally. Inspect the rendered prompt and example offline. `run.py plan` is a paid Qwen entrypoint, NOT an offline/dry-run command.

## Bounded plan-only test, only after budget verification

The first live test, if admitted by the existing budget controls, is exactly one task sheet from the complete revised RGB/NIR question from `focused_research/examples/rgb_nir_question.v2.json`, exactly matching the runtime example, with the original model configuration. No retrieval, physical execution or PDF. Do not run `retrieve`, `run`, a tuning loop, or unrelated tests that call providers.

Before any live/paid call, obtain a fresh ledger and reconcile pending/held/uncertain charges. This task has an explicit incremental cap of **CNY 2**, counted within the **CNY 140 total**; both limits must permit it. At most **3 HTTP attempts total**, including format repair and transport retries. The cap applies across the entire task, not per model round. Uncertain charges remain reserved until resolved.

The existing ledger/budget wrapper must actually cover the new independent focused Qwen planning entrypoint, reserve/enforce cost before dispatch, and bound all HTTP attempts including nested retries. An existing wrapper for another entrypoint is not proof of coverage. Never use a bare CLI to bypass it. If the existing wrapper cannot directly cover this entrypoint and these limits, stop with `blocked`, report the gap to cloud, and make no call. Local should not implement a new billing architecture. The stock planner's two rounds and transport retries alone do not guarantee a three-attempt task cap.

After confirmed admission, generate one plan into a new output directory through the verified wrapper. Do not improvise an unverified live command. Return source hash, offline test result, ledger admission and final charges/attempts, output location, raw/effective task sheet, validation/repair history, and a brief semantic review. Check exact v2 text, only supported G1/G2 metrics, explicit reducer/angle/polarization, no crosstalk score or disguised requirement, coupling without fabricated priorities, and exact query links. Test historical/unsupported inputs offline only and confirm needs_revision blocks execution. Flag failures honestly; no extra paid rerun beyond the stated cap/attempt allowance.

This plan-only result does not establish retrieval quality. Future query tuning first evaluates actual formulations, then tunes Qwen prompting; eventual acceptance is Qwen → actual retrieval, under separate authorization.
