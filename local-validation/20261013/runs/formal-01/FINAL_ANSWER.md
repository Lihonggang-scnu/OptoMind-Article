# TMM research and design result

Run status: completed_best_effort_no_verified_candidate. Acceptance: unverified.

## Problem interpretation

Design a planar two-layer antireflection coating on a semi-infinite glass substrate (refractive index 1.52, lossless) with air incidence (index 1.0). The design must use exactly one layer of lossless SiO2 (n=1.46) and one layer of lossless TiO2 (n=2.40) in either stacking order. Each layer thickness is constrained between 40 nm and 160 nm. The objective is to minimize the arithmetic mean power reflectance for s-polarized light at normal incidence, sampled from 450 nm to 700 nm in 5 nm steps. A mean reflectance below 0.03 is the stated performance target. The method must use deterministic Transfer Matrix Method (TMM) only, excluding RCWA, FDFD, learned surrogates, or hardware acceleration. The process allows at most two research rounds with one route and up to 64 solver evaluations per round using seed 17. Decisions to adjust or stop must be based on observed results and independent numeric checks.

## Method research

- No reusable literature method was available; planning used explicit optical theory assumptions.

## Routes executed

- **Optimization of Low-High Index Stacking Order (Air/SiO2/TiO2/Glass)** — status `contract_rejected`, source `literature_planned`, verified candidates 0, best soft score n/a.
- **Strictly Bounded Two-Layer AR Optimization with Corrected Budget** — status `contract_rejected`, source `literature_planned`, verified candidates 0, best soft score n/a.

## Planning-source comparison

The memory-only control and literature-planned routes are separated by source. Frozen-standard scores are used for a cross-source verdict only when both arms have scoreable representatives; score deltas inside a route are diagnostics.
- Frozen-standard comparison: `control_or_literature_arm_missing`.
- `literature_planned`: 1 route(s), 0 with verified candidates, 2 executed round(s).

## Recommended candidate portfolio

No candidate passed deterministic physics verification.

## Feedback and stopping decision

The loop stopped with `stop_best_effort`: The bounded research budget ended; verified candidates are preserved.

## Limitations

- Failed or unverified rounds: iteration_01, iteration_02.
- Whether the reverse stacking order (Air/TiO2/SiO2/Glass) would yield a superior result if the primary route fails to meet the 0.03 target; this requires a second research round not permitted in the initial single-route execution.
- Some planned routes produced recorded failures or capability limits; see the iteration artifacts.
- No traceable literature item directly influenced the executed route; the result is theory-guided.

## Literature provenance

- No literature reference was used by the executed route.
