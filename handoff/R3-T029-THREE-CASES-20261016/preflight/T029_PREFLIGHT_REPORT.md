# Result

T029 zero-fee preflight completed in isolated `D:\OptoMind-Article-3\r3t029a` at `e1eba28e5350632ed7cf387c643f8d16ede1ef72`. The exact original request and question files for 09, 08, 10, and 02 match `D:\OptoMind-Article-3\r3t029a\focused_research\campaigns\t029\INPUT_PROVENANCE.json` and the Article source commit `032bd651b32dfce075a046f2214248c5dcbb491a`; each `FRESH_QUESTION.txt` has only the required terminal newline. Use each case's `FRESH_REQUEST.json` and `FRESH_QUESTION.txt` under `D:\OptoMind-Article-3\r3t029a\focused_research\campaigns\t029\inputs\<case_id>\`.

The three executable cases are material-ready on their complete finite grids with extrapolation disabled:

- 09 `1260..1565 nm`, step `1 nm`: Ta2O5 Gao page `415`, SiO2 Lemarchand page `411`, Si Pierce page `608`; all n/k finite and in range.
- 08 `355..1080 nm`, step `1 nm`: Ta2O5 Gao `415`, SiO2 Lemarchand `411`, fused silica Malitson `835`; all requested samples in range and finite. Page 835 has no extinction series; the provider reports `k=0` by its lossless/no-k convention.
- 02 `400..1565 nm`, step `1 nm`: local `hfo2.csv`, SiO2 Lemarchand `411`, fused silica Malitson `835`; all requested samples in range and finite, with the same fused-silica loss convention.

Case 10 remains the handoff's no-fee blocked record and was not dispatched or treated as one of the three eligible cases.

# Changed

Only task-owned preflight artifacts were added under `D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T029\preflight` and `scripts`:

- `FOCUSED_TESTS.json`
- `T029_MATERIAL_INPUT_PREFLIGHT.json`
- `T029_QKD02_BOUNDS_LOCATOR.json`
- `T029_PREFLIGHT_REPORT.md`
- `run_material_preflight.py`
- `locate_qkd02_bounds.py`

Canonical read-only sources to wire into the new worktree are `D:\OptoMind-Article-3\r3t029a\veritmm\tmm_engine\material_registry.py`, `D:\OptoMind-Article-3\r3t029a\veritmm\tmm_engine\rii_cache.db`, `D:\OptoMind-Article-3\r3t029a\veritmm\tmm_engine\materials\hfo2.csv`, and the resolver's explicit RII page IDs `415`, `411`, `608`, and `835`. Use the current `r3t029a` resolver/provider imports with `veritmm` first on `PYTHONPATH`; do not import `r3t027a` or an `F:` planner/engine. Pin `provider` and `dataset_id` in route material references, especially fused silica page `835` and local HfO2 `hfo2`.

The historical T027 manifest at `D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T027\MATERIAL_PREFLIGHT.json` records the same RII database hash and pages `421`/`410`/`835`; T028 reused that manifest. Those historical selections are retained as provenance and were not substituted for the T029 case-specific pages above.

# Verify

Focused tests passed with the original tensor interpreter: `test_local_campaign_guard.py` 21 passed and `test_decision_context.py` 11 passed, both exit 0. Import origins and exact commands are in `FOCUSED_TESTS.json`.

The material report records source SHA-256 values, catalog/source ranges, point counts, n/k finiteness, full-grid sample counts, and `extrapolated_count=0` for every selected material. The RII database was opened read-only; no database copy or solver execution occurred.

Recorded common n/k ranges are Gao415 `0.35–1.8 µm`, Lemarchand411 `0.25–2.5 µm`, Pierce608 `0.1033–2.066 µm`; fused silica Malitson835 has n `0.21–6.7 µm` and no k series; local HfO2 is n/k `0.2–25.0 µm` (2481 points). The local SiO2 CSV is inventoried with its hash but is not selected for these three case checks.

Material preflight command: `D:\OptoMind-Article-3\local-integration\20261013\envs\tensor\Scripts\python.exe D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T029\scripts\run_material_preflight.py` with `PYTHONPATH=D:\OptoMind-Article-3\r3t029a\veritmm;D:\OptoMind-Article-3\r3t029a\code`; exit `0`.

The QKD02 locator found no original compiled task or bounds artifact in the bounded old/current roots. The replay's only manufacturing constraint is total layer count `<=30`; its historical local-refinement titles are provenance only. Current route schema requires finite positive per-family `[min_nm,max_nm]` hypotheses and enforces them, but has no user global thickness-range field. Parent may proceed only with explicitly labeled model-proposed per-family domains; if the actual launcher requires an externally frozen global range, stop 02 and request it.

# Blockers

No preflight blocker for 09, 08, or 02 material coverage. Fused silica page `835` supplies n only (`has_k=false`); `k=0` is a disclosed provider convention, not an accuracy proof. QKD02 has no recovered user global thickness bounds, so any launcher that insists on such a bound remains blocked; do not invent `20–400 nm` or reuse historical champion/local-refinement values.
