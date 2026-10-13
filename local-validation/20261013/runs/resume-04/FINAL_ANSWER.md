# TMM research and design result

Run status: completed. Acceptance: accepted.

## Problem interpretation

Design a planar two-layer antireflection coating on a semi-infinite glass substrate (refractive index 1.52, lossless) with air incidence (refractive index 1.0). The stack must consist of exactly one layer of SiO2 (n=1.46) and one layer of TiO2 (n=2.40), arranged in either order. Each layer thickness is constrained between 40 nm and 160 nm. The objective is to minimize the arithmetic mean power reflectance for s-polarized light at normal incidence, sampled from 450 nm to 700 nm in 5 nm steps. A mean reflectance below 0.03 is a stated soft target preference. The method must use deterministic Transfer Matrix Method (TMM) only, excluding RCWA, FDFD, learned surrogates, or hardware acceleration. The optimization allows at most two research rounds with one route and up to 64 solver evaluations per round using seed 17. The process requires observing results before deciding on adjustments or stopping, and claims of meeting the target require spectral evidence and independent numeric verification.

## Method research

- No reusable literature method was available; planning used explicit optical theory assumptions.

## Routes executed

- **Optimization of SiO2/TiO2 Bilayer Order and Thickness for Broadband AR** — status `completed`, source `literature_planned`, verified candidates 5, best soft score 0.9646.
- **Optimization of TiO2/SiO2 Bilayer with Analytical Quarter-Wave Initialization** — status `completed`, source `literature_planned`, verified candidates 4, best soft score 0.5299.

## Planning-source comparison

The memory-only control and literature-planned routes are separated by source. Frozen-standard scores are used for a cross-source verdict only when both arms have scoreable representatives; score deltas inside a route are diagnostics.
- Frozen-standard comparison: `control_or_literature_arm_missing`.
- `literature_planned`: 1 route(s), 1 with verified candidates, 2 executed round(s).

## Recommended candidate portfolio

Every route was measured on the same quantities, chosen from the request before any route ran, and ranked by the one fixed expression `-mean_reflectance_450_700nm`, so these rows are directly comparable.
- **Best performance**: `ar_coating_sio2_tio2_opt__gradient_thickness__01` from Optimization of SiO2/TiO2 Bilayer Order and Thickness for Broadband AR.
- **Most robust**: `ar_coating_sio2_tio2_opt__gradient_thickness__01` from Optimization of SiO2/TiO2 Bilayer Order and Thickness for Broadband AR.
- **Simplest verified**: `ar_coating_sio2_tio2_opt__gradient_thickness__02` from Optimization of SiO2/TiO2 Bilayer Order and Thickness for Broadband AR.

- `ar_coating_sio2_tio2_opt__gradient_thickness__01` stack (2 layers): n=1.46 / n=2.4.
- `ar_coating_sio2_tio2_opt__gradient_thickness__02` stack (2 layers): n=1.46 / n=2.4.
- `ar_coating_sio2_tio2_opt__gradient_thickness__04` stack (2 layers): n=1.46 / n=2.4.
- `ar_coating_tio2_sio2_opt__gradient_thickness__01` stack (2 layers): n=2.4 / n=1.46.
- `ar_coating_tio2_sio2_opt__gradient_thickness__03` stack (2 layers): n=2.4 / n=1.46.

| Candidate | Route | Frozen standard score | Comparable spectral summary | Robustness | Thicknesses (nm) |
|---|---|---:|---|---:|---|
| `ar_coating_sio2_tio2_opt__gradient_thickness__01` | Optimization of SiO2/TiO2 Bilayer Order and Thickness for Broadband AR | -0.0354 | mean_reflectance_450_700nm=0.0354 | 0.5888 | 86.56, 94.48 |
| `ar_coating_sio2_tio2_opt__gradient_thickness__02` | Optimization of SiO2/TiO2 Bilayer Order and Thickness for Broadband AR | -0.0366 | mean_reflectance_450_700nm=0.0366 | n/a | 87.00, 94.00 |
| `ar_coating_sio2_tio2_opt__gradient_thickness__04` | Optimization of SiO2/TiO2 Bilayer Order and Thickness for Broadband AR | -0.0513 | mean_reflectance_450_700nm=0.0513 | n/a | 61.79, 91.93 |
| `ar_coating_tio2_sio2_opt__gradient_thickness__01` | Optimization of TiO2/SiO2 Bilayer with Analytical Quarter-Wave Initialization | -0.3365 | mean_reflectance_450_700nm=0.3365 | 0.1302 | 46.00, 111.00 |
| `ar_coating_tio2_sio2_opt__gradient_thickness__03` | Optimization of TiO2/SiO2 Bilayer with Analytical Quarter-Wave Initialization | -0.3475 | mean_reflectance_450_700nm=0.3475 | n/a | 64.88, 113.79 |

## Manufacturing uncertainty

- `ar_coating_sio2_tio2_opt__gradient_thickness__01`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_R mean=0.074350 ± 0.004191; target_domain_mean_R mean=0.034974 ± 0.002115; target_domain_min_R mean=0.021919 ± 0.002414.
- `ar_coating_tio2_sio2_opt__gradient_thickness__01`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_R mean=0.359543 ± 0.001748; target_domain_mean_R mean=0.334123 ± 0.004840; target_domain_min_R mean=0.289973 ± 0.007167.

## Feedback and stopping decision

The loop stopped with `stop_completed`: Route route_01 left the race as stopped_round_limit: reached the per-route cap of 2 executed rounds

## Limitations

- Whether the reverse topology (Air/TiO2/SiO2/Glass) should be tested in a subsequent study if route_01 fails to achieve a mean reflectance below 0.05, as the current budget allows only one route.
- No traceable literature item directly influenced the executed route; the result is theory-guided.

## Literature provenance

- No literature reference was used by the executed route.
