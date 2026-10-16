# T032 case05 final saved-artifact quality audit

Result: terminal bounded cycle audit complete. This report is read-only evidence review; it performed no rerun, model/network call, solver/physics call, or source/runtime write.

- Source binding: `8a38b2d0d7b18c4308c4f5978691a234f6ab3732`; all present case/stage source bindings match: **True**.
- Terminal status: `completed_bounded_cycle`; stages plan/retrieval/routes/search/revision/refine are all exit 0; case exposure `0.122060` CNY.

## Inputs and material binding

The saved question is the 400–700 nm visible / 700–2000 nm NIR split under air incidence on an aluminum substrate, with HfO2, SiO2, and Cr coating materials and at most 20 layers. `user_global_thickness_bounds` is `null`; finite positive thickness ranges belong to route-family hypotheses.

All 64 refined designs use four positive coating layers, only HfO2/SiO2/Cr, incident air n=1/k=0, Al exit dataset 22 from `rii_sqlite`, 400–2000 nm at 1 nm, R/T/A outputs, and disabled extrapolation. Observed route-family thickness ranges were HfO2 20–80 nm, Cr 5–40 nm, and SiO2 44.41441162193453–95 nm; these are observed route domains, not user-global bounds. Constraint check: **all 64 passed**.

Source material hashes: HfO2 CSV `23e31c59429e2c6b2aa75d0a5c5c061bbd0e58fe4281b8e48aca7a7010e08a04`; SiO2 CSV `17741836229c411cf7b835323238b30d804a967eef645bb582d343a33a48cad8`; Al CSV `f9652db8e7dcad1eb734a7d6e185c37991d8caf21a1e5f5d62ad20a547a957e7`; RII DB `e4c1ec34cc4f765a8400ff87bb7dee8705b6da2cdb11a229cc57ad9bdd274989`.

## Route, retrieval, and certification

Routes generated: 10. Accepted: R1/R3/R5/R6/R8/R9; executable: R1/R3/R6; rejected: R2/R4/R7/R10, each for the saved “thickness parameter outside family range” reason. Scientific route status remained untested. Retrieval produced 39 papers, 45 hits, and 75 eligible evidence items; 42 whole original items were selected. Full text, relevance, constraints, and scientific truth were not validated; D3 remains an unverified paraphrase/inference.

Initial search certified 32 designs (32 new evidence). Refined archive contains 64 designs (32 inherited + 32 new certified evidence; 69 evaluation rows). Refined selection had 74 candidates: 37 completed (32 new physical evaluations + 5 cache hits) and 37 deferred_unvalidated, all for `near_duplicate_structure_and_objectives`. Deferred rows are not certified outcomes.

All initial 32 design IDs are present in the refined archive. The separate old extrema are retained exactly: min G1 design `design:92b7c195334cd2d4faf761cc5b910ccb4926549d2e0d1f2f7bdcc969e1174fef` has (G1,G2)=(0.013147879961958481, 0.4069529830139695); max G2 design `design:dc3be58ce34748df4bbf97c630cb961f28bab2d453ae64608eabb9c846251da8` has (0.09379456458766483, 0.6386608580291324). These are separate extrema, not a combined optimum.

## Revision identity and measured parent–child outcomes

The accepted model plans use the exact current stable IDs and available actions: P017 → `refine_layers`, P028 → `refine_family`, P009 → `refine_layers`. The saved plans remain hypotheses; finite outcomes below are read from completed certified archive vectors.

| parent / action | new certified children | signed ΔG1 range (G1 minimizes) | signed ΔG2 range (G2 maximizes) | actual outcome counts |
|---|---:|---:|---:|---|
| P017 / `refine_layers` | 5 | [0.000538083342473067, 0.00348702647343966] | [0.00207602659979977, 0.0118074295453185] | G1 improved/worsened 0/5; G2 improved/worsened 5/0 |
| P028 / `refine_family` | 15 | [-0.0714227315355423, -0.00223245616679554] | [-0.419323989627383, -0.0010476151505564] | G1 improved/worsened 15/0; G2 improved/worsened 0/15 |
| P009 / `refine_layers` | 12 | [-0.00785035735141946, 0.0199678381382841] | [-0.359259383302049, 0.0546621067562911] | G1 improved/worsened 2/10; G2 improved/worsened 10/2 |

P017’s missing derivative components remained unknown and its d3 lower-bound note is correct. Its expected G2 increase occurred in all 5 children; G1 was honestly expected unknown and worsened in all 5. P028’s G1 direction narrative is sign-consistent, but expected G2 unchanged was unverified and all 15 children lowered G2 while improving G1. P009’s saved d1 derivatives (+0.0004597301331652393 for G1 and −0.004062375165109664 for G2) support decreasing d1 locally after applying the minimize/maximize senses; its finite 12-child result is mixed, with G1 improving 2/12 and G2 improving 10/12. No finite-step prediction is promoted to a fact.

## Representative certified structures

| design | parent | layers (material: nm) | G1 | G2 |
|---|---|---|---:|---:|
| `design:92b7c195334cd2d4faf761cc5b910ccb4926549d2e0d1f2f7bdcc969e1174fef` | `initial` | SiO2:66.1540041734 / Cr:5.2118263705 / HfO2:50 / Cr:10 | 0.01314787996195848 | 0.4069529830139695 |
| `design:dc3be58ce34748df4bbf97c630cb961f28bab2d453ae64608eabb9c846251da8` | `initial` | HfO2:20 / Cr:5 / HfO2:30 / Cr:5 | 0.09379456458766483 | 0.6386608580291324 |
| `design:07972cae63d5c98885f996c375928252b8456c90fa6125e2ee73a6d2a56db83c` | `design:92b7c195334cd2d4faf761cc5b910ccb4926549d2e0d1f2f7bdcc969e1174fef` | SiO2:61.4811278352 / Cr:5 / HfO2:50 / Cr:10 | 0.01663490643539814 | 0.418760412559288 |
| `design:90e07f3d682cbb3373d9e7d08b5c54ffd7b1fc67d8846c2c1023bfaa1d79e0af` | `design:dc3be58ce34748df4bbf97c630cb961f28bab2d453ae64608eabb9c846251da8` | HfO2:56.6113859131 / Cr:10.4793322855 / HfO2:61.5316489746 / Cr:9.48574902392 | 0.02237183305212256 | 0.2193368684017491 |
| `design:b921b0c89d59669c3f5bee63c13aa4f057d9485f9cad5e8cab7080432572a4ce` | `design:429669353b8c7323c22c089b82681881a5246cf2ff17f133a863e8a2cb61d526` | HfO2:25.697005263 / Cr:5 / HfO2:30 / Cr:5 | 0.06095807724584494 | 0.6176786534326218 |

## Aluminum absorption interpretation

The task assumption treats Al as a semi-infinite absorbing exit medium, with transmission into the substrate contributing to system absorptance. The saved certificate audit defines the reported finite-film channel as `A_finite_layers=1-R-T_into_exit_medium`. G1 and G2 themselves are mean-reflectance objectives only, so the saved A channel must not be substituted for either objective. The route wording “high absorption in visible/NIR via interference and multiple reflections; high IR reflection from bottom Al” is retained as a mechanism hypothesis, not as a measured absorptance or optimization result.

## Limits

- Certified coverage is the saved sampled grid only; no continuous/manufacturing optimum is established.
- 37 deferred candidates are unvalidated and are excluded from child outcome counts.
- Retrieval full text and semantic/scientific validation were not available, especially for D3.
- The report preserves model hypotheses, exact IDs, signed metric vectors, and actual tradeoffs without changing production artifacts.

Owned artifacts: `FINAL_QUALITY_REVIEW.json` and `FINAL_QUALITY_REVIEW.md` in this directory. The JSON contains hashes for the audited source artifacts.
