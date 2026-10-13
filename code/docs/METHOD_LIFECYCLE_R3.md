# Executable method lifecycle and bounded generation-policy search

## Implemented path

`finalize_completed_research` examines a completed canonical research result. It requires a supported independent source check and an acceptable final result. Diagnostic sources additionally require a single retained mechanism after all-family refits, a bound/calibrated numerical outcome, and a separate supported confirmation.

The function discovers the forward-ordered decision/program/prediction/observation/evaluation/changed-decision trace and calls the existing `extract_program_template`. It does not manufacture an extractor or copy a fitted answer into a method. The resulting `ResearchMethodAsset` preserves its exact source protocol, sample-role mapping, source frontend, and canonical evidence. Grid slots and their finite domain are derived from the pre-action model and acquisition arguments. Their derivation is checked again at every consumer boundary.

Registration grants `task_validated` status only. The source task is not cross-task promotion evidence. The production provider still requires promoted evidence unless an explicit bounded trial is requested.

`FrozenMethodProvider.execute_program` instantiates a fresh program, applies only declared parameter slots, recompiles capabilities and resource bounds, freezes predictions through the host, and executes the existing `ProgramRunner`/`ExecutionGate`. It returns domain, budget, and unresolved-execution branches without adding an alternate agent platform. Post-execution routing uses canonical evaluations and actual all-family refit evidence; a violated prequential prediction alone is not proof that a model family is inadequate.

The legacy `OpenResearchLoop` and the diagnostic research loop invoke the lifecycle automatically at completion. `diagnostic_action_from_method` restores the exact learned observation operator for actual downstream acquisition. New-task model parameters are refitted; source fitted parameters are not transferred as answers.

## E07 scope

`MethodGenerationPolicy` changes an executable, finite wavelength-grid generation rule: uniform spacing, greedy mean-pair separation, or greedy worst-pair separation. It also has a bounded nuisance aggregation choice. Candidate generation uses public model prediction sets, not evaluator truth. This is bounded generation-policy optimization, not recursive self-improvement, autonomous source-code modification, or a new learning platform.

The search uses separate training (T), validation/selection (V), promotion holdout (P), and audit (A) provenance groups. It evaluates each candidate through fresh acquisitions and full mechanism refits, freezes baseline and selected policy before P/A, and never selects from test scores. The same method-library hash, physical model, acquisition budget, point count, and compute caps are used for both arms. Actual prediction calls, solver points, and subset enumerations are recorded separately; no compute-cost saving is inferred from equal measurement spending.

If validation does not support a policy change, the baseline is retained. A comparison reporting no gain is a valid outcome and must not be described as successful policy improvement.

## Offline commands

From the repository root, with the normal scientific Python dependencies installed:

```bash
export PYTHONPATH=veritmm:code
python -m pytest -q code/tests/test_science_method_lifecycle.py code/tests/test_science_c15_method_runtime.py code/tests/test_science_c15_methods.py
python -m optomind_optics.harness.science.method_cli --offline --output /absolute/new/method-campaign --policy-trials 2
```

The second command creates a fresh public synthetic source protocol, automatically extracts and registers a method, executes five fresh-process replays, performs five cross-material/layer holdouts, and runs two separately seeded policy-search trajectories. All directories must be new. No LLM, network, or paid model invocation is constructed.

To run the entire chain starting from the automatically designed diagnostic frontend instead:

```bash
python -m optomind_optics.harness.science.method_cli --offline --output /absolute/new/diagnostic-method-campaign --diagnostic-config PUBLIC_CONFIG.json --diagnostic-world SEALED_SYNTHETIC_WORLD.json --policy-trials 2
```

The world file is used only by the evaluator-side source provider. Public planning never receives it. The method campaign can also use a completed qualified source library:

```bash
python -m optomind_optics.harness.science.method_cli --offline --library /absolute/completed-source/science --output /absolute/new/reuse-campaign --policy-trials 2
```

The library receives append-only canonical target-task records needed by the frozen provider. No existing evidence object or execution journal is overwritten. Assets are not automatically promoted by these diagnostics.

## Artifacts and limits

- `FROZEN_METHOD_PROTOCOL.json`: cases, targets, candidate policies, and source-code fingerprints before outcomes.
- `METHOD_LIFECYCLE.json`: qualification or explicit rejection, source result, canonical asset/evaluation references.
- `METHOD_TRANSFER_RESULT.json`: exact instantiated program, frontend, library/asset identities, raw-acquisition-linked evaluations, all-family fits, costs, compute use, and evaluator-only outcome.
- `FROZEN_TASKS.json` / `FROZEN_SELECTED_POLICY.json`: split tasks and policy frozen before heldout evaluation.
- `POLICY_COMPARISON.json`: every actual forward execution, T/V ranks, heldout/audit scores, resource accounting, and bounded search scope.
- `METHOD_CAPABILITY_RESULT.json`: replay, transfer, and policy outcomes without combining them into a single capability score.

These are small synthetic diagnostics. Five successful transfers do not establish population accuracy. The evaluator uses an independent coherent Byrnes TMM implementation for target acquisitions; no physical hardware, manufacturing yield, calibrated global rejection rate, or global mechanism-impossibility certificate is claimed. The current transfer adapter supports one non-destructive specimen acquisition and coherent source frontends. Larger adaptive programs remain supported by the core program consumer but are not silently projected into this narrow diagnostic adapter.
