# B1 prediction wiring TODO

- **Blocked by frozen gate:** F3 returned `supported`, not the required `violated`; do not run the optional budget-3 Cauchy variant and do not relax the current SPEC.
- **Needs a new approved experiment design:** choose a stronger or independently justified out-of-family observable/world and freeze it before rerun. Any change to family, grid, noise, seed, envelope, or budget is a new case, not a repair of this result.
- **Contract gap:** Article `_predictions` currently persists neither typed `candidate_ref` nor `program_ref`; candidate/program identity binding remains unverified.
- **Evidence limit:** these two runs cannot estimate FPR or 80% detection power. Repeated pre-registered seeds and a separate evaluation plan would be required.
- Keep all SQLite files local; do not upload or merge them into production evidence.
