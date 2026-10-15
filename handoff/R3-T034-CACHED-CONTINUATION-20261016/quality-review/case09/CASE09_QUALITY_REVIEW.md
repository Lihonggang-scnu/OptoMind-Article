# T034 case09 quality review

This is a read-only audit of the completed old case `e2e-09-si-photonics-ar-dualband` against fixed source SHA `8a38b2d0d7b18c4308c4f5978691a234f6ab3732`. The audit did not call a model, retrieval, HTTP, physics solver, route generation, or tests, and did not modify the runtime, source, or case archive.

## Result

The bounded cycle is terminal with route, search, revision, and refine exit code 0. The archive is traceable and the saved certified rows satisfy the declared air → Ta2O5/SiO2 → semi-infinite Si contract on the sampled grid. Scientific interpretation remains conditional: the saved scope is `certified_sampled_grid_only`, mechanism is unproven, and no global or continuous-band optimum is established.

The production evidence input is `routes/evidence_items.json`. It contains 41 selected items, all marked eligible with empty `missing_reasons`; the same selected hash is bound into search and refine. The selected IDs are preserved in `CASE09_QUALITY_REVIEW.json`. Route clue records still say `unverified_paraphrase_or_inference`, `quote_verified=false`, and `semantic_support=not_verified`, so eligibility is not literature verification.

## Route and evidence audit

The raw route response has 10 routes. Nine are accepted (`R1,R3,R4,R5,R6,R7,R8,R9,R10`), five are executable (`R1,R3,R4,R5,R8`), and `R6,R7,R9,R10` remain `needs_revision`. `R2` is retained as a raw partial failure and rejected because an instance thickness was outside its family range. No route scientific claim was tested in the route stage.

The raw/compiled R4 record preserves a factual contract mismatch: it declares `d1` through `d12` changed, while the production candidate report derives actual changes only for `d1` through `d9` and `d12`; `d10` and `d11` are omitted.

## Search and refine counts

| Stage | Candidate rows | Certified rows | New certified evaluations | Deferred candidates | Forward rows / cap | Claim scope |
|---|---:|---:|---:|---:|---:|---|
| Search | 234 | 32 (10 seeds + 22 candidates) | 32 / 32 total (10 seeds + 22 candidates) | 212 | 1230 / 2048 | certified sampled grid only |
| Refine | 565 | 64 (32 inherited + 32 children) | 32 / 32 | 533 | 1707 / 2048 | certified sampled grid only |

The read-only contract scan found zero violations among 32 search certified rows and 64 refined archive certified rows: no disallowed material, layer-count, 20–400 nm thickness, incident-medium, or silicon-exit mismatch. The refined ancestry is asymmetric: P031 has 125 prepared rows and zero certified children; P004 has 194 prepared and 16 certified children; P001 has 246 prepared and 16 certified children.

## Parent → revision → actual outcome

The current alias checks are exact: `P031 → design:fd7d84...5702ac`, `P004 → design:22fc72...2bb750`, `P001 → design:0edb57...e0c08`; diagnoses are `D011`, `D012`, and `D022` with the exact IDs recorded in the JSON report. The revision parent manifest and refined parent manifest are both `6b862f6c31f82a814f15f055a2c88d5c02aba92ce76f17ee9b2b406463d11bc4`.

- **P031 / D011, `refine_layers`:** parent vector is `(G1=6.962699562748557e-05, G2=3.736439777488701e-04)`. The saved uncertified derivative supports an `dH4` increase as a local first-order direction for both goals (`+0.0013115489`, `+0.0079681618` normalized improvement rates). The derivative signs show that decreasing `dH3` lowers G1 but raises G2; the revision sentence saying it benefits both is a factual sign-level misreading. No P031 child was certified, so the finite-step prediction is untested.

- **P004 / D012, `refine_layers`:** parent vector is `(2.1154627836427947e-04, 3.004081859939466e-06)`. The local d10 signs support the stated tradeoff: increasing d10 improves G2 and worsens G1; no common local direction was found. Among 16 certified children, G1 decreased in 16/16. G2 ranged from `2.8913645596970554e-06` to `1.635042445765639e-04`; 15/16 children worsened G2 and 1/16 improved it. The minimum-G1 child raised G2 by `1.605001627166244e-04`. G2 was not consistently unchanged; this is a measured scientific G1/G2 tradeoff, not a system failure.

- **P001 / D022, `refine_family`:** parent vector is `(1.2012319810182612e-04, 2.0229018145602028e-04)`. The parent identity and metrics are verified, but no detailed local derivative row for P001 was supplied in the revision prompt’s representative decision set; the global-search rationale remains a hypothesis. Among 16 certified children, G1 decreased in 15/16 and increased in 1/16, while G2 decreased in 16/16. The both-decrease expectation is supported by most sampled children, not guaranteed for every finite step.

## Certified representatives

The five structures below are actual certified rows selected for parent tradeoff and final-archive objective extrema. Full material/thickness vectors and cache/certificate references are in [representative_certified_structures.json](D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T034\quality-review\case09\representative_certified_structures.json). T is report-only transmission into the semi-infinite Si exit medium, computed from the saved unpolarized spectrum using the same wavelength-integral mean; no new evaluation was made.

| Representative | Design ID | Layers | G1 | G2 | T(G1) | T(G2) |
|---|---|---:|---:|---:|---:|---:|
| P031 parent | `design:fd7d84...5702ac` | 8 | 6.962699562748557e-05 | 3.736439777488701e-04 | 0.9999303730043725 | 0.9996263560222506 |
| P004 parent | `design:22fc72...2bb750` | 12 | 2.1154627836427947e-04 | 3.004081859939466e-06 | 0.9997884537216357 | 0.9999969959181395 |
| P001 parent | `design:0edb57...e0c08` | 12 | 1.2012319810182612e-04 | 2.0229018145602028e-04 | 0.9998798768018983 | 0.9997977098185432 |
| Refined overall G1 minimum | `design:5c00ab...d4f058` | 12 | 2.705849117550392e-05 | 1.635042445765639e-04 | 0.9999729415088247 | 0.9998364957554228 |
| Refined overall G2 minimum | `design:191f74...c5a3a5` | 12 | 2.0879097628124051e-04 | 2.8913645596970554e-06 | 0.9997912090237189 | 0.9999971086354401 |

These are separate extrema/tradeoff points. They do not constitute an invented joint optimum.

## Fact, hypothesis, and falsified or unmet prediction

**Facts:** stage exits, source and input bindings, selected evidence IDs, current aliases, actual candidate/certification counts, saved objective vectors, saved T spectra, and zero contract violations are recorded in the JSON report. The refined archive has 64 certified designs but only 32 newly certified children; 533 candidates remain deferred.

**Hypotheses:** route transfer assumptions, evidence-based physical explanations, local derivative directions, and the P001 global-search rationale. The route stage explicitly reports simulation not performed and scientific validity not established.

**Falsified or unmet:** R2 was rejected; R4’s declared changed-variable list is wrong; the P031 dH3 “benefits both” sentence conflicts with derivative signs; P004’s G2-unchanged expectation is not consistently maintained across the measured child tradeoff (15/16 worsen and 1/16 improves); P031’s finite-step prediction has no certified child.

## Changed, verify, blockers

**Changed:** only these new audit files were written:

- `D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T034\quality-review\case09\CASE09_QUALITY_REVIEW.json`
- `D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T034\quality-review\case09\CASE09_QUALITY_REVIEW.md`
- `D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T034\quality-review\case09\representative_certified_structures.json`

**Verify:** JSON parsing passed for both owned JSON files. All claims above point to the preserved old route, revision, search, refined, diagnostics, ancestry, and cache archives; no active case08 or case02 output was read or written.

**Blockers:** no global optimum, continuous-band guarantee, mechanism proof, uncertainty quantification, experiment, or device-level throughput claim is available. Parent review should use the full archive references and preserve the raw partial routes and factual mismatches.
