# T032 case01 final quality review

The saved case is terminal for the bounded engineering cycle: all six stage exits are zero, source SHA is `8a38b2d0d7b18c4308c4f5978691a234f6ab3732`, and `scientific_status` remains `inspect_actual_results_and_predictions`.

Question, constraints, and the five stage task-sheet copies agree. The executable contract is G1 mean unpolarized T over 1000–1700 nm and G2 mean unpolarized R over 300–450 nm, at 0 degrees as an execution assumption, air/fused-silica, HfO2/SiO2, 12 layers in the saved designs, and no user global thickness bound. Material bindings are air n=1, HfO2 local CSV `hfo2`, SiO2 RII dataset `411`, and fused silica RII dataset `835`; extrapolation is disabled.

Initial retrieval returned 33 papers, 43 hits, and 68 eligible evidence items. Full text was not retrieved, and relevance/constraint validation remained false. Route compilation accepted R1/R9 only; D5 had an unknown evidence ID and the remaining rejections were recorded as compiler/input-contract causes. Route scientific validity was not established.

## Revision identity and finite outcomes

| parent alias | stable design ID | action | expected G1/G2 | certified children | audit status |
|---|---|---|---|---:|---|
| P027 | `design:e9be80aaea80e6d3161f787315678ce2db6d972907241ed2efeb27711bf179a7` | refine_family | increase / increase | 0 | untested_no_child_certification |
| P023 | `design:c4bbca735d29226605d06282cd327a7152b766c47ace2ea4add9686fb378a926` | refine_family | increase / unchanged | 0 | untested_no_child_certification |
| P006 | `design:2a5ffc067851bcc430064178769ef1f5230ad940eb4f37bba8e0cfc4a34d3e24` | refine_layers | increase / increase | 32 | observed_finite_sample |
| P018 | `design:ac974ff506d5876859570f008ae16ad8b5236a3395ccd5b6462584fa9007a52b` | refine_family | increase / increase | 0 | untested_no_child_certification |

The alias map and plan stable IDs match exactly. All 32 refined children point to stable P006 `design:2a5ffc067851bcc430064178769ef1f5230ad940eb4f37bba8e0cfc4a34d3e24`. P027, P023, and P018 have no certified children, so their expected changes remain untested. The request-level derivative claims are local hypotheses in normalized parameter coordinates, not per-nm values; saved local derivatives are uncertified, shared-variable derivatives do not identify individual layers, and no finite-step guarantee follows.

For P006, the saved parent vector is `{'G1': 0.9262439432316809, 'G2': 0.720180345479017}`. All 32 children increase G2; 24 also increase G1 and 8 decrease G1. The G1 delta range is `[-0.008071195796018671, 0.02644429386261371]` and G2 delta range is `[0.03313157170648762, 0.08328995197862865]`.

- Initial observed max-G1 structure `design:e9be80aaea80e6d3161f787315678ce2db6d972907241ed2efeb27711bf179a7` / P027: `{'G1': 0.9273426420989448, 'G2': 0.12533388143827562}`.
- Initial observed max-G2 structure `design:c4bbca735d29226605d06282cd327a7152b766c47ace2ea4add9686fb378a926` / P023: `{'G1': 0.9139186771054552, 'G2': 0.7448563968430084}`.
- Refined observed max-G1 child `design:8fe94b56df396dd32641f1401094c34f5fb708c37f4c2bb867a71eb9171e06d2`: `{'G1': 0.9526882370942946, 'G2': 0.7533119171855046}`.
- Refined observed max-G2 child `design:4ed71cbbea60412a4d1f5813283b8190e9945b298d2947376c140e4dbd0c56b8`: `{'G1': 0.9181727474356622, 'G2': 0.8034702974576456}`.

The two refined extrema are different structures; no combined-extrema or optimization benefit claim is made. The initial 32 designs and their metric vectors remain in the final 64-design archive. The final archive has 182 refinement candidate rows, 34 completed rows (32 new plus 2 cache hits), and 148 deferred-unvalidated rows; deferred rows are not failures.

The audit only read saved artifacts and wrote this report. It made zero model, network, physics, or test calls and did not modify source/runtime files. Exact hashes, IDs, vectors, layer arrays, and artifact bindings are in `FINAL_QUALITY_REVIEW.json`.
