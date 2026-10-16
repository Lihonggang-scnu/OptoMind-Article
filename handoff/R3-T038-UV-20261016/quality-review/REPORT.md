# T038 case04 bounded quality audit

Result: completed from immutable saved artifacts for `e2e-04-solarblind-uv-triple`. Source `366f3faa70217b6209afb1162bac6759765cd628`; all recorded stages exited 0. This audit made no rerun, model, retrieval, search, physics, source, or answer change.

## Contract and archive counts

The executed route was R1: an 11-layer air / Nb2O5–MgF2 / fused-silica family with original family bounds dH1 40–60 nm, dL1 60–90 nm, and dCav 100–150 nm. The task limits total layers to 30; the executed topology uses 11. G1/G2/G3 are mean transmission on 255–280, 300–700, and 700–1100 nm at normal incidence and unpolarized polarization.

Initial search: **19 certified** of 23 candidates, 35 forward rows, 19 archive designs, 19 Pareto IDs; 4 candidates remained deferred/unvalidated. Refined stage: **51 certified** (19 inherited + 32 new), 235 forward rows, 51 archive designs, 35 Pareto IDs. The two stages contain 270 forward rows in total. All 19 initial IDs and vectors are retained exactly.

## Objective vectors

| stage/objective | design | vector (G1, G2, G3) |
|---|---|---|
| initial G1 max | `21f966c45f86` (P003) | `[4.584452109734316e-05,0.41138583482078145,0.8801214616954078]` |
| final G1 max | `21f966c45f86` (P003) | `[4.584452109734316e-05,0.41138583482078145,0.8801214616954078]` |
| initial G2 min | `f33c97f1ce55` (P019) | `[2.9676461303009152e-06,0.24798313536616245,0.805894037681925]` |
| final G2 min | `21777e8a68a3` (no alias) | `[5.300504149386402e-06,0.15772943143233195,0.783026333126927]` |
| initial G3 min | `8f61d0e8bd44` (P012) | `[2.4836171686497126e-06,0.25101246829409013,0.7969290774072426]` |
| final G3 min | `8296a1ec2c1d` (no alias) | `[2.5967199565359245e-06,0.23826667976293506,0.6467272412817767]` |

The same single design P003 remains the G1 maximum before and after refinement with vector `[4.584452109734316e-05,0.41138583482078145,0.8801214616954078]`. P002 also retains its exact vector `[7.209620727701802e-06,0.35336722103759016,0.8350260897444005]`. Final G2 and G3 extrema are separate P008 children; no joint optimum is claimed.

## Parent, derivative, and child checks

The saved revision plans are P002 `refine_layers` (17 children), P018 `refine_family` (2), and P008 `refine_layers` (13). Their actual child sign counts, where “+” is an increase in the stored objective value and “−” a decrease, are:

| parent | saved expected changes | actual child signs G1/G2/G3 |
|---|---|---|
| P002 | `{"G1":"increase","G2":"decrease","G3":"decrease"}` | `{"G1":{"+":10,"-":7},"G2":{"-":17},"G3":{"+":1,"-":16}}` |
| P018 | `{"G1":"unknown","G2":"decrease","G3":"decrease"}` | `{"G1":{"+":1,"-":1},"G2":{"+":1,"-":1},"G3":{"-":2}}` |
| P008 | `{"G1":"increase","G2":"unchanged","G3":"decrease"}` | `{"G1":{"+":13},"G2":{"-":7,"+":6},"G3":{"-":7,"+":6}}` |

The key saved reasoning defect is directional: P002’s plan says decreasing dH1 improves all three goals, while the saved per-nm row is G1 −1.9016628175271527e−6, G2 −0.005757579421262278, G3 −0.0005397597518501094. For minimized G2/G3, those negative derivatives make a decrease locally increase the objective. P008 similarly has dH1 `(−3.854081779297095e−7, +0.002332240733365445, −0.007818096897776286)` and dCav `(+3.760354590212691e−8, −0.0006358411735257418, −0.001640259394912255)` per nm, with dH1 and dCav at their upper saved family bounds. These are saved-prompt/Jacobian interpretation issues; no mechanism is inferred.

The children show finite sampled tradeoffs rather than a universal failure: P002 improves G2 in 17/17, G3 in 16/17, and G1 in 10/17; P018 has G3 improving in 2/2 with G1/G2 mixed; P008 improves G1 in 13/13 while G2 and G3 split 7/6. The derivative rows explicitly describe the bounds as original family bounds and the bound status as an effective search window.

## Routes, evidence, and limitations

Only R1 was executable. R3 and R8 were rejected because model-knowledge routes attempted to claim external citations; R7 used a null external-evidence rationale; R6 and R10 supplied thickness values outside their family ranges. R2, R4, R5, and R9 were accepted only as needs-revision routes. These are route contract/readiness outcomes, separate from the later numerical tradeoffs.

Retrieval saved 5 queries, 45 papers, 48 hits, and 80 eligible evidence items without full text; relevance and constraint evaluation were false. Whole-item selection included 46 items and deferred 34 on text budget. The active downstream file is `routes/evidence_items.json` with 46 IDs and selected-evidence hash `ba5c412210a2bbf4b6408f27442ce0bd8e5e6360968318c3f5490da26eef57dc`. D1–D3 are directly locatable but semantic support remains unverified; D4–D5 are unverified paraphrase/inference. Saved diagnostics mark reflection-band status unknown, and the scientific claim scope remains `certified_sampled_grid_only`; no physical mechanism or global optimum is established.

## Representative certified structures

| label | design | vector (G1, G2, G3) | parent |
|---|---|---|---|
| P002 | `0e933543f2f1` | `[7.209620727701802e-06,0.35336722103759016,0.8350260897444005]` | `none` |
| P003 | `21f966c45f86` | `[4.584452109734316e-05,0.41138583482078145,0.8801214616954078]` | `none` |
| P002_child_joint_direction | `81f5d5557f74` | `[1.4041348199460047e-05,0.341234557098564,0.8091096174870386]` | `0e933543f2f1` |
| final_G2_min | `21777e8a68a3` | `[5.300504149386402e-06,0.15772943143233195,0.783026333126927]` | `634e8d8320d6` |
| final_G3_min | `8296a1ec2c1d` | `[2.5967199565359245e-06,0.23826667976293506,0.6467272412817767]` | `634e8d8320d6` |

Full 11-layer thickness vectors for these representatives are preserved in `REPORT.json`; they are sampled certified structures and are not presented as a combined design.

## Result / Changed / Verify / Blockers

**Result:** bounded quality audit complete; route and numerical archive counts, parent/child chains, vectors, derivative directions, and evidence bindings are recorded.

**Changed:** only `quality-review/REPORT.json` and `quality-review/REPORT.md` were created.

**Verify:** source/stage status, archive hashes, active evidence binding, route rejections, exact 19-ID retention, 32 refinement children, and representative vectors are in the JSON.

**Blockers:** no engineering execution blocker. Scientific interpretation remains bounded by unverified evidence semantics, unknown reflection-band diagnostics, derivative-direction mismatches in the saved revision reasoning, and sampled-grid scope.

Offline wording correction: the preserved `attempt01_failure.json` is `SystemExit:0` after successful checks; the preserved attempt log separately contains the `expected_case.files`/FRESH_REQUEST-key `KeyError`. These are helper bookkeeping/path-key records, not a failed acceptance.
