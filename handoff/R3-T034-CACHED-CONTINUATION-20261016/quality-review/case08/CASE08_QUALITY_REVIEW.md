# T034 case08 quality review

This is a read-only audit of the terminal `e2e-08-advanced-packaging-uv` archive against fixed source SHA `8a38b2d0d7b18c4308c4f5978691a234f6ab3732`. Route, search, revision, and refine all exited 0. No new model, retrieval, HTTP, physics, solver, route generation, tests, installation, or source/runtime/case changes were made.

## Result

The saved refined archive contains 64 unique certified designs. Every checked design satisfies the declared air → Ta2O5/SiO2 → fused-silica contract: only the two allowed materials, at most 36 layers, 20–350 nm thicknesses, normal incidence, and unpolarized objectives over 355–365 nm, 420–700 nm, and 1030–1080 nm. The 68 saved evaluation rows are complete and physics accepted; all use VeritMM 2.0.0 with `smatrix`, and all saved independent Byrnes checks passed.

The active production evidence input is `routes/evidence_items.json`. The routes, search, and refined copies are byte identical, each containing the same 48 selected original items. The selected evidence hash is `d3790e538b44221c5f88f1b881b0905121cfe130b6e61b1fb0be0544fb6c5448`; 20 of 68 source items were deferred for text budget. Evidence selection and route acceptance do not establish semantic or scientific truth: D1, D2, and D4 have verified quotes but unverified semantic support and untested physical explanations, while D3 is an unverified paraphrase.

## Route and evidence audit

The route response has 10 routes. Nine were accepted (`R1,R2,R3,R4,R5,R6,R7,R8,R10`), five remain `needs_revision` (`R4-R8`), and four are executable (`R1,R2,R3,R10`). R9 was preserved as a rejected raw route because one instance thickness was outside its family range. The route report remains `scientific_status=untested` and `simulation_performed=false`.

The source binding is verified at `D:\OptoMind-Article-3\r3t034a` with the same tested Git HEAD SHA. Search and refine have identical `input_bindings.json`, accepted contract, task sheet, constraints, optical-media, wavelength-grid, and selected evidence hashes. The downstream binding names the production route evidence file as the active search argument.

## Search and refine counts

| Stage | Candidate rows | Unique certified designs | New certified work | Deferred candidates | Forward rows / cap | Claim scope |
|---|---:|---:|---:|---:|---:|---|
| Search | 683 | 32 | 32/32 = 8 seed + 24 candidates | 658 | 871 / 2048 | certified sampled grid only |
| Refine | 814 | 64 | 32/32 children, plus 32 inherited | 779 | 1080 / 2048 | certified sampled grid only |

The archive has 33 search evaluation rows and 68 refined evaluation rows because cache-hit/reference rows are retained. The final 64-design constraint scan found zero violations; layer counts are 15×16, 3×18, 44×20, and 2×23, with observed thickness range 20.0–300.0 nm. `Rproxy` is absent from the route, search, revision, and refined JSON archives.

## Parent → revision → actual outcome

The exact parent aliases, diagnoses, local derivative rows, and child deltas are recorded in [CASE08_QUALITY_REVIEW.json](D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T034\quality-review\case08\CASE08_QUALITY_REVIEW.json). The original `revision/plan_report.json` normalization audit has one warning for accepted plan index 0 / P002: `unverified_reasoning: missing, foreign, or invalid observation references`. P002 remains an accepted, traceable parent/action execution, but diagnostic provenance is not verified for that row.

- **P002 / `refine_layers`:** parent vector `(G1=0.001567753921039963, G2=0.8914960811869094, G3=0.1663323448172479)`. All 30 newly certified children changed all 20 layers. G1 increased in 30/30 (delta range `0.00103777872409572..0.23467552238267`), G2 increased in 28/30 and decreased in 2/30, and G3 decreased in 30/30. The planned G1-unchanged behavior was not maintained in the measured finite steps; this is an observed objective change.

- **P021 / `refine_layers`, diagnosis D010:** parent vector `(0.038444250644077936, 0.9779151547246004, 0.5335691882134569)`. Only the cached parent row is present, so there are zero newly certified children and no finite-step prediction is tested. The saved d3/d6/d9 derivative entries are preserved as local numeric feedback only.

- **P012 / `refine_family`, diagnosis D011:** parent vector `(0.23732333557539592, 0.7908043769829444, 0.07985775901712044)`. The saved raw claim that decreasing `dH_NIR` improves all three locally matches the supplied derivative signs: `(dG1,dG2,dG3)=(+1.3171051385804982,-0.05593573545682664,+0.12034759473107162)` for `(minimize,maximize,minimize)`, so decreasing `dH_NIR` lowers G1/G3 and raises G2 locally. This is a saved local hypothesis, not a solver or finite-step guarantee. Two newly certified children changed all 20 layers. Both lowered G1 and raised G2, while both raised G3. The saved expectation of lower G3 was unmet in these two measured children; the result is a measured three-goal tradeoff.

## Certified representatives

The four structures below are actual certified rows. They are separate objective extrema and a parent-child tradeoff example, not a joint optimum. Full material/thickness vectors and cache references are in [representative_certified_structures.json](D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T034\quality-review\case08\representative_certified_structures.json).

| Representative | Design ID | Layers | G1 | G2 | G3 |
|---|---|---:|---:|---:|---:|
| Overall G1 minimum | `design:2f8a3a...2cfd98` | 18 | 0.0012076933759331133 | 0.8628748652104887 | 0.9556001316528503 |
| Overall G2 maximum | `design:3b4692...5dfea` | 20 | 0.01516532783258782 | 0.9783681900649386 | 0.10919106049676668 |
| Overall G3 minimum | `design:adf966...15da8` | 20 | 0.03704591633890975 | 0.8696015197733848 | 0.04791088849135833 |
| P012 refinement child tradeoff | `design:dd0b83...61efc3` | 20 | 0.0014247275190740123 | 0.8841924994056092 | 0.14007461847218702 |

## Fact, hypothesis, and unmet prediction separation

**Facts:** terminal stage exits, fixed source and input bindings, selected evidence IDs, route statuses, candidate/certification counts, saved objective vectors, certificate statuses, and constraint checks are recorded in the JSON report. The route model’s evidence clues and route claims remain distinct from numerical certification.

**Hypotheses:** route transfer assumptions, literature-derived explanations, local derivative directions, and the three refinement expected-change vectors. They are not proofs of physical mechanisms or global behavior.

**Unmet or untested:** P002 did not maintain G1 unchanged; P021 has no certified child; P012 did not lower G3 in either sampled child; R9 was rejected and R4–R8 were not executable. No exact-float equality was used to call a scientific expectation falsified.

## Changed, verify, blockers

**Changed:** only these new files were written under the owned directory:

- `D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T034\quality-review\case08\CASE08_QUALITY_REVIEW.json`
- `D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T034\quality-review\case08\CASE08_QUALITY_REVIEW.md`
- `D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T034\quality-review\case08\representative_certified_structures.json`

**Verify:** the two owned JSON files parse; all 64 refined designs join to saved numerical metrics; all 68 saved certificates are completed and accepted; evidence and binding hashes are unchanged across routes/search/refine. No active case02 or case09 output was read or written.

**Blockers:** the archive is limited to certified sampled-grid evidence with deferred candidates. It does not provide a global optimum, continuous-band guarantee, mechanism proof, uncertainty quantification, experiment, or device-level throughput claim.
