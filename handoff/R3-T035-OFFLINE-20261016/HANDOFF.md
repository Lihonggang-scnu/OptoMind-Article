# T035: retain complete saved derivative vectors for selected parameters

## Scope and queue

Base: `8a38b2d0d7b18c4308c4f5978691a234f6ab3732`. This narrow repair concerns `family_optimization.py` and `decision_context.py`: preserve every already-computed goal derivative for the **same selected parameter union**, without changing per-goal top-k rankings or adding parameters, model calls or solver work. Cloud verification is complete; full original local-source acceptance is pending. Use the final implementation SHA supplied by the accompanying ready message after this packet is committed. The base SHA is not the new release.

T033 is running; T032 is queued on its existing `8a38b2d` release. Do not interrupt either task or deploy this change into their active checkout. After T032 is terminal, the accompanying fixed-SHA ready message releases **zero-cost T035 local offline acceptance**. No new paid dispatch or live model/physical search belongs to T035. Any future scientific test requires separate authorization and limits.

## Specific factual correction from T034

Source result: `94fc308`, case `e2e-02-space-qkd-cband`; actual request `r3-a6875425ad8d42ba803c`; parent P002 is stable design `design:0dfa0da6805d956226e86249295b9c269130a4ed0247971be4bbc9df5382a2ea`.

The actual model-facing request lists dL with only its G2 derivative and `missing_goal_derivatives: ["G1"]`. Its dC2 row similarly lacks G2. The model's statement that the displayed dL information lacked a complete G1 derivative accurately reflects that request. The quality report's `P002_dL_derivative` criticism compared this statement with the fuller saved Jacobian, which the compact request had not exposed. Correct that specific attribution; do not label this omission a model factual error. Preserve the original request, response and quality report and add a correction note. This does not clear the separate P012 sign/band or P003 action-identity findings.

The saved dL G1 derivative is **+0.0005033902132708982 per normalized parameter coordinate**. For the recorded dL bounds `[135, 155]` nm, the span is 20 nm, so it is approximately **+0.00002516951066354491 per nm**. G2 is −0.041632459628660946 normalized, or −0.0020816229814330472 per nm. Do not quote normalized-coordinate values as per-nm slopes. These local derivatives are uncertified first-order information, not proof of a finite-step improvement or mechanism.

## Repair contract

- Keep the existing per-goal top-k ranking and its parameter union unchanged. For each parameter already in that union, carry the complete vector from the same saved finite Jacobian, with the same variable order, current value and bounds. P002's union remains dH, dL, dC1 and dC2.
- Preserve zero derivatives as zero, objective senses, normalized/per-nm units, effective-window scope and original family bounds. No new goals, variables, weights, physical constraints or execution policy.
- The decision context reads this complete selected-variable vector without dropping a goal merely because that parameter fell outside that goal's top-k. Do not silently merge inconsistent parameter identities, values, bounds or conflicting derivatives.
- A legacy compact summary cannot recreate missing numbers by itself. If the matching original full Jacobian and exact coordinate metadata are available, recompute only a **derived summary** from those saved numbers. Otherwise retain explicit missing/unknown status. No invented zero, interpolation, sign inference, finite-difference probe or new solver call.
- Preserve all raw data and historical output. Changed summaries/facts and their provenance are separate derived artifacts. A better context does not establish that a future model answer will be correct.

## Cloud verification and remaining local check

`OFFLINE_DERIVATIVE_REPLAY.json` reconstructs the legacy facts exactly as sent, then restores the two existing missing components on the same four selected parameters. Only the local-direction facts change in the compared model-facing payload; its recorded UTF-8 size is 78,323 → 78,438 bytes (+115). This is a derived payload comparison, not a new request or measured token/cost claim. The replay uses saved values and adds zero model calls, forward rows or certifications; full original local Jacobian identity verification remains the acceptance step below.

The final frozen socket-disabled full suite passed: **681 passed, 4 skipped, 6 subtests, 44.04 seconds, exit 0**; compileall and diff checks passed. The worker also recorded 57 passed / 4 skipped in focused checks and 24 passed in integration checks; the skips are unavailable Torch tests. Commands/results remain in the audit. Cloud regression success does not establish full local-source acceptance or a scientific improvement.

## Offline acceptance after T032, then stop

1. Verify the fixed release SHA and imports. Locate the original full saved P002 evaluation/Jacobian and bind it to the exact parent, parameterization, evaluation identity, parameter order, values and bounds. Use `FULL_LOCAL_ARCHIVE_REFERENCES.json`; a compact quality-report transcription is useful cloud fixture evidence, not a substitute for full local source verification.
2. Preserve hashes of the original request, response, summary and source evaluation. Confirm the actual transmitted P002 dL row lacked G1. Save an additive correction note explaining the report/request distinction.
3. Run `family_optimization.summarize_local_derivatives(row, variable_names, bounds_nm, goals, ...)` only on the verified saved Jacobian. Then run `decision_context.local_direction_facts(summary, objectives)` and inspect the derived decision view. These are saved-data arithmetic/projection steps; do not call `optimize_family`, an engine, a certification executor or a live planner.
4. Demonstrate unchanged top-k rankings and selected parameter union, complete existing goal vectors on that union, correct unit conversion, unchanged goals/bounds/metrics, and zero new forward rows. For the saved P002 fixture, dL gains its already-computed G1 and dC2 gains its already-computed G2; do not fabricate either from a legacy summary lacking the full source.
5. Check legacy missing-data behavior, zero values, minimizing-goal orientation and inconsistent/nonfinite supplements with offline tests. Use the frozen `test_t035_complete_derivative_vectors.py` for the pure local regression check:

   ```text
   python -m pytest -q focused_research/tests/test_t035_complete_derivative_vectors.py --disable-socket
   ```

   No local acceptance pass is asserted here. Keep existing dependencies; an unavailable source or prerequisite is an honest blocker, not permission to regenerate data.
6. Return completed / partial / blocked with source hashes, old-versus-derived summary/facts, the precise attribution correction, actual test evidence and **0 new HTTP / 0 physical evaluations or certifications / 0 CNY**. Stop. Do not rerun the model, old search, refinement or the three already-completed T034 cases.

Original raw records, certified results, numerical counters, spend and ledger history stay unchanged. No new client, credentials, spending pool or accounting mutation. Any later paid test is a distinct root decision. Keep sensitive headers/credentials and full private databases out of the receipt.
