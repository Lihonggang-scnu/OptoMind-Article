# TMM research and design result

Run status: completed. Acceptance: accepted.

## Problem interpretation

Design a planar two-layer antireflection coating on a semi-infinite glass substrate (refractive index 1.52, lossless) with air incidence (refractive index 1.0). The design must use exactly one layer of lossless SiO2 (n=1.46) and one layer of lossless TiO2 (n=2.40) in either order. Each layer thickness is constrained between 40 nm and 160 nm. The objective is to minimize the arithmetic mean power reflectance for s-polarized light at normal incidence, sampled from 450 nm to 700 nm in 5 nm steps. The stated performance target is a mean reflectance below 0.03. The method must use deterministic Transfer Matrix Method (TMM) only. The optimization allows at most two research rounds with one route and up to 64 solver evaluations per round using seed 17. Decisions to adjust or stop must be based on observed results and independent numeric checks.

## Method research

- No reusable literature method was available; planning used explicit optical theory assumptions.

## Routes executed

- **Optimization of Low-High Index Two-Layer AR Coating** — status `completed`, source `literature_planned`, verified candidates 5, best soft score 0.9646.

## Planning-source comparison

The memory-only control and literature-planned routes are separated by source. Frozen-standard scores are used for a cross-source verdict only when both arms have scoreable representatives; score deltas inside a route are diagnostics.
- Frozen-standard comparison: `control_or_literature_arm_missing`.
- `literature_planned`: 1 route(s), 1 with verified candidates, 1 executed round(s).

## Recommended candidate portfolio

Every route was measured on the same quantities, chosen from the request before any route ran, and ranked by the one fixed expression `-mean_reflectance_450_700nm`, so these rows are directly comparable.
- **Best performance**: `opt_ar_coating_sio2_tio2__gradient_thickness__01` from Optimization of Low-High Index Two-Layer AR Coating.
- **Most robust**: `opt_ar_coating_sio2_tio2__gradient_thickness__01` from Optimization of Low-High Index Two-Layer AR Coating.
- **Simplest verified**: `opt_ar_coating_sio2_tio2__gradient_thickness__02` from Optimization of Low-High Index Two-Layer AR Coating.

- `opt_ar_coating_sio2_tio2__gradient_thickness__01` stack (2 layers): n=1.46 / n=2.4.
- `opt_ar_coating_sio2_tio2__gradient_thickness__02` stack (2 layers): n=1.46 / n=2.4.
- `opt_ar_coating_sio2_tio2__gradient_thickness__04` stack (2 layers): n=1.46 / n=2.4.

| Candidate | Route | Frozen standard score | Comparable spectral summary | Robustness | Thicknesses (nm) |
|---|---|---:|---|---:|---|
| `opt_ar_coating_sio2_tio2__gradient_thickness__01` | Optimization of Low-High Index Two-Layer AR Coating | -0.0354 | mean_reflectance_450_700nm=0.0354 | 0.5888 | 86.56, 94.48 |
| `opt_ar_coating_sio2_tio2__gradient_thickness__02` | Optimization of Low-High Index Two-Layer AR Coating | -0.0366 | mean_reflectance_450_700nm=0.0366 | n/a | 87.00, 94.00 |
| `opt_ar_coating_sio2_tio2__gradient_thickness__04` | Optimization of Low-High Index Two-Layer AR Coating | -0.0513 | mean_reflectance_450_700nm=0.0513 | n/a | 61.79, 91.93 |

## Manufacturing uncertainty

- `opt_ar_coating_sio2_tio2__gradient_thickness__01`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_R mean=0.074350 ± 0.004191; target_domain_mean_R mean=0.034974 ± 0.002115; target_domain_min_R mean=0.021919 ± 0.002414.

## Feedback and stopping decision

The loop stopped with `stop_completed`: Route route_01 left the race as stopped_budget: run iteration budget exhausted

## Limitations

- Whether the reverse ordering (TiO2/SiO2) would actually perform better despite the graded-index hypothesis; this cannot be resolved without a second route or a subsequent manual check after this route completes.
- No traceable literature item directly influenced the executed route; the result is theory-guided.

## Literature provenance

- No literature reference was used by the executed route.
