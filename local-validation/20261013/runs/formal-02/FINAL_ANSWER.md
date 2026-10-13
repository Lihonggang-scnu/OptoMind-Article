# TMM research and design result

Run status: completed_best_effort_no_verified_candidate. Acceptance: unverified.

## Problem interpretation

Design a planar two-layer antireflection coating on a semi-infinite glass substrate (refractive index 1.52, lossless) with air incidence (refractive index 1.0). The stack must consist of exactly one layer of SiO2 (n=1.46) and one layer of TiO2 (n=2.40) in either order, with each layer thickness constrained between 40 and 160 nm. The objective is to minimize the arithmetic mean power reflectance for s-polarized light at normal incidence over the wavelength range 450 to 700 nm (sampled every 5 nm). A mean reflectance below 0.03 is a stated soft target preference. The method must use deterministic Transfer Matrix Method only, excluding RCWA, FDFD, learned surrogates, or hardware acceleration. The optimization allows at most two research rounds with one route and up to 64 solver evaluations per round using seed 17. Final validation requires observing the full spectrum and performing an independent numeric check before claiming the target is met.

## Method research

- No reusable literature method was available; planning used explicit optical theory assumptions.

## Routes executed

- **Optimization of Two-Layer SiO2/TiO2 Antireflection Coating Geometry** — status `not_run`, source `literature_planned`, verified candidates 0, best soft score n/a.
- **Optimization of Air/SiO2/TiO2/Glass Two-Layer AR Coating** — status `contract_rejected`, source `literature_planned`, verified candidates 0, best soft score n/a.

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
- Some planned routes produced recorded failures or capability limits; see the iteration artifacts.
- No traceable literature item directly influenced the executed route; the result is theory-guided.

## Literature provenance

- No literature reference was used by the executed route.
