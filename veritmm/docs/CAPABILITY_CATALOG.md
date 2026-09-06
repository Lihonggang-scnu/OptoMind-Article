# Capability catalog (runtime-generated)

Generated at `2026-09-05T01:01:08.962290+00:00` by `py -3.11 -m tmm_engine.cli capability-catalog --regenerate`.  Do not edit by hand: CI compares this document against the live capability surface and fails on any drift.

| capability | provider | operations | outputs | cost | verification |
|---|---|---|---|---|---|
| `tmm.simulate.forward` | tmm_engine.workbench | preflight, simulate, certify | R, T, A, A_independent… | medium | spectral-convergence/cross-solver/energy-independent |
| `tmm.simulate.response` | tmm_engine.run_artifacts | project, inspect | compact response, RESPONSE_CONTEXT.json | cheap | — |
| `tmm.analysis.sensitivity` | tmm_engine.scientific_analysis | execute_sensitivity, execute_tolerance | autodiff_derivative_per_nm, finite_difference_derivative_per_nm, normalized_derivative | medium | spectral-convergence/cross-solver |
| `tmm.design.optimize` | tmm_engine.optimization | optimize, validate_result | OPTIMIZATION_RESULT.json, candidate designs, INDEPENDENT_VALIDATION.json | expensive | spectral-convergence/cross-solver/energy-independent |
| `tmm.research.batch` | tmm_engine.research.batch | sequential, chunked_verified | EvaluationRecord per candidate | medium | spectral-convergence/cross-solver |
| `tmm.agent.benchmark` | tmm_engine.agent_bench | run_offline_benchmark | BENCHMARK_RESULT.json, false-accept accounting | expensive | — |
| `tmm.gradient.api` | tmm_engine.gradient_api | compute_gradient, compute_sensitivity | GradientResult, SensitivityResult, top_influential_layers | medium | energy-independent |
| `tmm.intent.compile` | tmm_engine.intent | compile_intent | SimulationTask, OptimizationTask, CompilationEquivalenceCertificate | cheap | — |
| `tmm.design.compile` | tmm_engine.design_problem | compile_design_problem | OptimizationTask | cheap | — |
| `tmm.job.runtime` | tmm_engine.job_runtime | submit_job, status, cancel, result, run_batch | job state machine, aggregate batch report, engine fingerprint ledger | cheap | — |
| `tmm.backend.numpy` | tmm_engine.backends.numpy_backend | assemble | R, T, A, r… | cheap | — |
| `tmm.backend.torch` | tmm_engine.backends.torch_backend | assemble | R, T, A, r… | cheap | — |

## `tmm.simulate.forward`

```json
{
 "capability_id": "tmm.simulate.forward",
 "contract_version": "veritmm-run-result-v1",
 "deterministic": true,
 "estimated_cost_class": "medium",
 "implementation_fingerprint": "307e8864469c295f32a5889064c60cb8e8be298728ff528357d50170cf82ab6c",
 "inputs": {
  "task": "SimulationTask (JSON schema: simulation)"
 },
 "manifest_sections": [
  "solvers",
  "modes",
  "geometry",
  "excitation",
  "material_models",
  "requested_outputs",
  "mixed_coherence",
  "spectral_metrics",
  "units",
  "limitations"
 ],
 "notes": [],
 "operations": [
  "preflight",
  "simulate",
  "certify"
 ],
 "outputs": [
  "R",
  "T",
  "A",
  "A_independent",
  "E_system",
  "r/t amplitudes",
  "layer_absorption",
  "certificate",
  "metric_snapshot"
 ],
 "physics_scope": "passive, isotropic, planar 1-D multilayers",
 "provider": "tmm_engine.workbench",
 "schema_fingerprint": "acce53329d21f0705035895ce5e96746d7a3c0b0c840b3cbdb1f17104773b202",
 "supports_cancel": false,
 "verification": {
  "cross_solver": true,
  "energy_independent": true,
  "reciprocity": false,
  "spectral_convergence": true
 }
}
```
## `tmm.simulate.response`

```json
{
 "capability_id": "tmm.simulate.response",
 "contract_version": "veritmm-response-v1",
 "deterministic": true,
 "estimated_cost_class": "cheap",
 "implementation_fingerprint": "cc2bc399b47cd4f8359fe32eb2475f4447ed0baef6e5f23124cf6f630ac210ab",
 "inputs": {
  "detail": "compact|standard|full",
  "payload": "run payload"
 },
 "manifest_sections": [
  "artifact_types"
 ],
 "notes": [],
 "operations": [
  "project",
  "inspect"
 ],
 "outputs": [
  "compact response",
  "RESPONSE_CONTEXT.json"
 ],
 "physics_scope": "n/a (protocol surface)",
 "provider": "tmm_engine.run_artifacts",
 "schema_fingerprint": "e1719a9f3804d073b041b135b9a397336ad03980a1ead3e2ded504c1a5d38e33",
 "supports_cancel": false,
 "verification": {
  "cross_solver": false,
  "energy_independent": false,
  "reciprocity": false,
  "spectral_convergence": false
 }
}
```
## `tmm.analysis.sensitivity`

```json
{
 "capability_id": "tmm.analysis.sensitivity",
 "contract_version": "sensitivity-task-v1",
 "deterministic": true,
 "estimated_cost_class": "medium",
 "implementation_fingerprint": "e2cc6345d6fe9bbd28bb89aa7123952d84bd95f6f8bfb330f64a828e838e1578",
 "inputs": {
  "task": "SensitivityTaskPayload / ToleranceTaskPayload"
 },
 "manifest_sections": [
  "scientific_analysis"
 ],
 "notes": [],
 "operations": [
  "execute_sensitivity",
  "execute_tolerance"
 ],
 "outputs": [
  "autodiff_derivative_per_nm",
  "finite_difference_derivative_per_nm",
  "normalized_derivative"
 ],
 "physics_scope": "thickness sensitivity of coherent stacks",
 "provider": "tmm_engine.scientific_analysis",
 "schema_fingerprint": "2254965a2ccfee47aea0b936d4caddf936c0f995bd3b484e58ee7bc7eb0970d8",
 "supports_cancel": false,
 "verification": {
  "cross_solver": true,
  "energy_independent": false,
  "reciprocity": false,
  "spectral_convergence": true
 }
}
```

## `tmm.design.optimize`

```json
{
 "capability_id": "tmm.design.optimize",
 "contract_version": "optimization-task-v1",
 "deterministic": true,
 "estimated_cost_class": "expensive",
 "implementation_fingerprint": "f5075f434697c86a16dd359e8ec52e9904987e222d017d88ec4c96694c506a3e",
 "inputs": {
  "task": "OptimizationTask (JSON schema: optimization)"
 },
 "manifest_sections": [
  "optimization"
 ],
 "notes": [],
 "operations": [
  "optimize",
  "validate_result"
 ],
 "outputs": [
  "OPTIMIZATION_RESULT.json",
  "candidate designs",
  "INDEPENDENT_VALIDATION.json"
 ],
 "physics_scope": "differentiable multiband thickness design",
 "provider": "tmm_engine.optimization",
 "schema_fingerprint": "3f9397282f34489c7481cfa0b1af36a984c4957d44106f9c314a81d1e0d5b21b",
 "supports_cancel": false,
 "verification": {
  "cross_solver": true,
  "energy_independent": true,
  "reciprocity": false,
  "spectral_convergence": true
 }
}
```

## `tmm.research.batch`

```json
{
 "capability_id": "tmm.research.batch",
 "contract_version": "veritmm-research-v1",
 "deterministic": true,
 "estimated_cost_class": "medium",
 "implementation_fingerprint": "27bc972006755703565dc0b63cdbe0c23059bdf7e652ce27bae92434cad79470",
 "inputs": {
  "candidates": "DesignCandidate set"
 },
 "manifest_sections": [
  "research_interface"
 ],
 "notes": [],
 "operations": [
  "sequential",
  "chunked_verified"
 ],
 "outputs": [
  "EvaluationRecord per candidate"
 ],
 "physics_scope": "batched proposals with per-candidate certification",
 "provider": "tmm_engine.research.batch",
 "schema_fingerprint": null,
 "supports_cancel": false,
 "verification": {
  "cross_solver": true,
  "energy_independent": false,
  "reciprocity": false,
  "spectral_convergence": true
 }
}
```

## `tmm.agent.benchmark`

```json
{
 "capability_id": "tmm.agent.benchmark",
 "contract_version": "veritmm-agentbench-v1",
 "deterministic": true,
 "estimated_cost_class": "expensive",
 "implementation_fingerprint": "a1e05d81ecb8fb7aeca8da691952d7cea5386d9f959b6f3f60c004d5cb6b9512",
 "inputs": {
  "none": "offline case catalog"
 },
 "manifest_sections": [
  "agent_bench"
 ],
 "notes": [],
 "operations": [
  "run_offline_benchmark"
 ],
 "outputs": [
  "BENCHMARK_RESULT.json",
  "false-accept accounting"
 ],
 "physics_scope": "agent-facing protocol evaluation",
 "provider": "tmm_engine.agent_bench",
 "schema_fingerprint": null,
 "supports_cancel": false,
 "verification": {
  "cross_solver": false,
  "energy_independent": false,
  "reciprocity": false,
  "spectral_convergence": false
 }
}
```

## `tmm.gradient.api`

```json
{
 "capability_id": "tmm.gradient.api",
 "contract_version": "veritmm-gradient-result-v1",
 "deterministic": true,
 "estimated_cost_class": "medium",
 "implementation_fingerprint": "c3ef7042a8c4e7e23cfea4a893fbb96f34053e4c8af92915a81c2fbcd677b552",
 "inputs": {
  "objective": "mean_R|mean_T|mean_A[:min..max]",
  "task": "SimulationTask"
 },
 "manifest_sections": [],
 "notes": [
  "added after the v1 capability manifest; no describe section yet"
 ],
 "operations": [
  "compute_gradient",
  "compute_sensitivity"
 ],
 "outputs": [
  "GradientResult",
  "SensitivityResult",
  "top_influential_layers"
 ],
 "physics_scope": "thickness gradients of band objectives (torch backend)",
 "provider": "tmm_engine.gradient_api",
 "schema_fingerprint": "a9961fed4b5fca7fe051350dfe78d73b03b71b117abf038bb8212c6da481bd5b",
 "supports_cancel": false,
 "verification": {
  "cross_solver": false,
  "energy_independent": true,
  "reciprocity": false,
  "spectral_convergence": false
 }
}
```

## `tmm.intent.compile`

```json
{
 "capability_id": "tmm.intent.compile",
 "contract_version": "compilation-equivalence.v1",
 "deterministic": true,
 "estimated_cost_class": "cheap",
 "implementation_fingerprint": "9743c764af6df33cef4ad9c3386802e0f540c5a4785cbd512efcb1e642ebba16",
 "inputs": {
  "spec": "IntentSpec (JSON schema: intent)",
  "stack": "declared stack"
 },
 "manifest_sections": [],
 "notes": [
  "added after the v1 capability manifest; no describe section yet"
 ],
 "operations": [
  "compile_intent"
 ],
 "outputs": [
  "SimulationTask",
  "OptimizationTask",
  "CompilationEquivalenceCertificate"
 ],
 "physics_scope": "semantic IR compilation (unit/direction/constraint semantics)",
 "provider": "tmm_engine.intent",
 "schema_fingerprint": "a9961fed4b5fca7fe051350dfe78d73b03b71b117abf038bb8212c6da481bd5b",
 "supports_cancel": false,
 "verification": {
  "cross_solver": false,
  "energy_independent": false,
  "reciprocity": false,
  "spectral_convergence": false
 }
}
```

## `tmm.design.compile`

```json
{
 "capability_id": "tmm.design.compile",
 "contract_version": "veritmm-design-problem-v1",
 "deterministic": true,
 "estimated_cost_class": "cheap",
 "implementation_fingerprint": "125ea6289206304977617ed1a3d1b2db393012ace219fd86f8ed6cd0c4e549ff",
 "inputs": {
  "problem": "OptimizationProblemModel (JSON schema: design-problem)"
 },
 "manifest_sections": [],
 "notes": [
  "added after the v1 capability manifest; no describe section yet"
 ],
 "operations": [
  "compile_design_problem"
 ],
 "outputs": [
  "OptimizationTask"
 ],
 "physics_scope": "declarative design space with constraint boundaries",
 "provider": "tmm_engine.design_problem",
 "schema_fingerprint": "7ce92c31e73c095efafe010467bdcdc222d7d3ce06d4f70d688951d0f85c6c82",
 "supports_cancel": false,
 "verification": {
  "cross_solver": false,
  "energy_independent": false,
  "reciprocity": false,
  "spectral_convergence": false
 }
}
```

## `tmm.job.runtime`

```json
{
 "capability_id": "tmm.job.runtime",
 "contract_version": "veritmm-simulation-job-v1",
 "deterministic": true,
 "estimated_cost_class": "cheap",
 "implementation_fingerprint": "e9495574e81628ddb6d0eb76bcae577e5d78b835215367b57b6106fae0a7147e",
 "inputs": {
  "request": "JobRequest(mode, task, output_dir)"
 },
 "manifest_sections": [],
 "notes": [
  "cancel honestly unsupported under synchronous execution",
  "added after the v1 capability manifest; no describe section yet"
 ],
 "operations": [
  "submit_job",
  "status",
  "cancel",
  "result",
  "run_batch"
 ],
 "outputs": [
  "job state machine",
  "aggregate batch report",
  "engine fingerprint ledger"
 ],
 "physics_scope": "execution substrate (fingerprint-guarded caching)",
 "provider": "tmm_engine.job_runtime",
 "schema_fingerprint": null,
 "supports_cancel": false,
 "verification": {
  "cross_solver": false,
  "energy_independent": false,
  "reciprocity": false,
  "spectral_convergence": false
 }
}
```

## `tmm.backend.numpy`

```json
{
 "capability_id": "tmm.backend.numpy",
 "contract_version": "veritmm-backend-v1",
 "deterministic": true,
 "estimated_cost_class": "cheap",
 "implementation_fingerprint": "c1df07f027650e55c2d04ec1c2ac04a8f51bac9e7f78b9e2da0c25689430cf28",
 "inputs": {
  "kernel": "KernelSpec(nk_stack, thicknesses_nm, wavelengths_nm, angle_deg, polarization)"
 },
 "manifest_sections": [],
 "notes": [
  "discovered from the backend registry drop-in scan"
 ],
 "operations": [
  "assemble"
 ],
 "outputs": [
  "R",
  "T",
  "A",
  "r",
  "t"
 ],
 "physics_scope": "coherent planar 1-D multilayer assembly",
 "provider": "tmm_engine.backends.numpy_backend",
 "schema_fingerprint": null,
 "supports_cancel": false,
 "verification": {
  "cross_solver": false,
  "energy_independent": false,
  "reciprocity": false,
  "spectral_convergence": false
 }
}
```

## `tmm.backend.torch`

```json
{
 "capability_id": "tmm.backend.torch",
 "contract_version": "veritmm-backend-v1",
 "deterministic": true,
 "estimated_cost_class": "cheap",
 "implementation_fingerprint": "3286d308475b7ff3f85426310f25c2b8650d73c34c70d34158044fefb262e566",
 "inputs": {
  "kernel": "KernelSpec(nk_stack, thicknesses_nm, wavelengths_nm, angle_deg, polarization)"
 },
 "manifest_sections": [],
 "notes": [
  "discovered from the backend registry drop-in scan"
 ],
 "operations": [
  "assemble"
 ],
 "outputs": [
  "R",
  "T",
  "A",
  "r",
  "t"
 ],
 "physics_scope": "coherent planar 1-D multilayer assembly",
 "provider": "tmm_engine.backends.torch_backend",
 "schema_fingerprint": null,
 "supports_cancel": false,
 "verification": {
  "cross_solver": false,
  "energy_independent": false,
  "reciprocity": false,
  "spectral_convergence": false
 }
}
```

