# T037 case04 quality audit

Result: the saved original case04 run is correctly BLOCKED at planning. The task-sheet validator rejected both model rounds; no accepted task sheet or plan exists. The guard records two task-sheet dispatches, zero route-generation/search-revision stages, and shared contract repair used once.

The exact repeated failure is subgoals[2].original_span. Both raw rounds supplied "尽可能抑制 700–1100 nm 近红外杂散光", but the original question contains "700–1100 nm 近红外杂散光" after the conjunction "和"; the repeated "尽可能抑制" is not an exact native span. The source validator therefore fails closed. All other inspected goal spans and C1–C9 constraint spans are exact.

| Round | Field | Raw span | Exact? | Validator result | Saved call |
|---|---|---|---|---|---|
| 1 | subgoals[2].original_span | 尽可能抑制 700–1100 nm 近红外杂散光 | no (-1) | task sheet subgoal.original_span: must be an exact span of original_question | D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T037\cases\e2e-04-solarblind-uv-triple\private\audit\af1ba83840442493cb30\calls\r3-2098857a987f47178bed |
| 2 | subgoals[2].original_span | 尽可能抑制 700–1100 nm 近红外杂散光 | no (-1) | task sheet subgoal.original_span: must be an exact span of original_question | D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T037\cases\e2e-04-solarblind-uv-triple\private\audit\af1ba83840442493cb30\calls\r3-0cdc395c22374d4cbe40 |

The proposed goals otherwise map faithfully to the question: G1 maximizes mean transmission over 255–280 nm; G2 and G3 minimize mean transmission over 300–700 nm and 700–1100 nm. Air incidence, fused silica, metal-free planar dielectric construction, and the <=30-layer bound are preserved. The broad ordinary-inorganic-material permission remains broad; no named-material whitelist is introduced. Zero-degree, equal-s/p unpolarized, wavelength-integral means, and suppression-as-transmission-minimization are separately labeled execution assumptions/interpretations. Thickness bounds, material inventory and thresholds remain unresolved unknowns.

One nonfatal wording issue is visible in the English user_objective summary: it shortens “conventional depositable inorganic dielectric materials” to “conventional inorganic materials.” The authoritative C7 constraint retains the exact native wording; because the contract was rejected before acceptance, this summary loss did not reach downstream execution.

The raw candidate mechanisms and coupling language remain untested hypotheses. The normalization audit also records that Q5 has no linked research-question IDs for its three direct subgoals; this is an advisory linkage issue, not the fatal validator error. No retrieval, route generation, physics, search, certification or scientific acceptance occurred. Actual case exposure was 0.0371824 CNY across two HTTP attempts.

Minimal reproduction used only the saved raw responses, FRESH_REQUEST.json, and the current source validator; request/response headers and credentials were not copied. JSON details and hashes are in T037_CASE04_QUALITY_AUDIT.json.
