# TMM research and design result

Run status: completed. Acceptance: accepted.

## Problem interpretation

Design a planar two-layer antireflection coating on a semi-infinite glass substrate (refractive index 1.52, lossless) with air incidence (refractive index 1.0). The stack must consist of exactly one layer of lossless SiO2 (n=1.46) and one layer of lossless TiO2 (n=2.40) in either order. Each layer thickness is constrained between 40 nm and 160 nm. The objective is to minimize the arithmetic mean power reflectance at normal incidence with s-polarization, sampled from 450 nm to 700 nm in 5 nm steps using deterministic Transfer Matrix Method. A mean reflectance below 0.03 is a soft scoring preference. The optimization allows at most two research rounds with one route and up to 64 solver evaluations per round using seed 17. Decisions to adjust or stop must be based on observed results and independent numeric checks.

## Method research

- No reusable literature method was available; planning used explicit optical theory assumptions.

## Routes executed

- **Two-Layer AR Coating Optimization: Order and Thickness Sweep** — status `completed`, source `literature_planned`, verified candidates 5, best soft score 0.9324.
- **Quarter-Wave Initialized Optimization and Order Verification** — status `completed`, source `literature_planned`, verified candidates 5, best soft score 0.9071.

## Planning-source comparison

The memory-only control and literature-planned routes are separated by source. Frozen-standard scores are used for a cross-source verdict only when both arms have scoreable representatives; score deltas inside a route are diagnostics.
- Frozen-standard comparison: `control_or_literature_arm_missing`.
- `literature_planned`: 1 route(s), 1 with verified candidates, 2 executed round(s).

## Recommended candidate portfolio

Every route was measured on the same quantities, chosen from the request before any route ran, and ranked by the one fixed expression `-abs(mean_reflectance_450_700nm - 0.03)`, so these rows are directly comparable.
- **Best performance**: `order2_sio2_tio2__baseline` from Two-Layer AR Coating Optimization: Order and Thickness Sweep.
- **Most robust**: `topology_a_sio2_tio2_opt__gradient_thickness__01` from Quarter-Wave Initialized Optimization and Order Verification.
- **Simplest verified**: `order2_sio2_tio2__baseline` from Two-Layer AR Coating Optimization: Order and Thickness Sweep.

- `order2_sio2_tio2__baseline` stack (2 layers): n=1.46 / n=2.4.
- `topology_a_sio2_tio2_opt__gradient_thickness__01` stack (2 layers): n=1.46 / n=2.4.
- `topology_a_sio2_tio2_opt__gradient_thickness__05` stack (2 layers): n=1.46 / n=2.4.
- `order1_tio2_sio2__gradient_thickness__01` stack (2 layers): n=2.4 / n=1.46.
- `order1_tio2_sio2__baseline` stack (2 layers): n=2.4 / n=1.46.
- `topology_b_tio2_sio2_opt__baseline` stack (2 layers): n=2.4 / n=1.46.

| Candidate | Route | Frozen standard score | Comparable spectral summary | Robustness | Thicknesses (nm) |
|---|---|---:|---|---:|---|
| `order2_sio2_tio2__baseline` | Two-Layer AR Coating Optimization: Order and Thickness Sweep | -0.0376 | mean_reflectance_450_700nm=0.0676 | n/a | 80.00, 80.00 |
| `topology_a_sio2_tio2_opt__gradient_thickness__01` | Quarter-Wave Initialized Optimization and Order Verification | -0.0629 | mean_reflectance_450_700nm=0.0929 | 0.3511 | 94.00, 69.00 |
| `topology_a_sio2_tio2_opt__gradient_thickness__05` | Quarter-Wave Initialized Optimization and Order Verification | -0.0792 | mean_reflectance_450_700nm=0.1092 | n/a | 74.82, 67.05 |
| `order1_tio2_sio2__gradient_thickness__01` | Two-Layer AR Coating Optimization: Order and Thickness Sweep | -0.2119 | mean_reflectance_450_700nm=0.2419 | 0.1691 | 86.23, 93.12 |
| `order1_tio2_sio2__baseline` | Two-Layer AR Coating Optimization: Order and Thickness Sweep | -0.2560 | mean_reflectance_450_700nm=0.2860 | n/a | 80.00, 80.00 |
| `topology_b_tio2_sio2_opt__baseline` | Quarter-Wave Initialized Optimization and Order Verification | -0.3277 | mean_reflectance_450_700nm=0.3577 | n/a | 60.00, 99.00 |

## Manufacturing uncertainty

- `topology_a_sio2_tio2_opt__gradient_thickness__01`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_R mean=0.128898 ± 0.002146; target_domain_mean_R mean=0.092439 ± 0.001731; target_domain_min_R mean=0.071419 ± 0.001667.
- `order1_tio2_sio2__gradient_thickness__01`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_R mean=0.352777 ± 0.003071; target_domain_mean_R mean=0.245944 ± 0.009055; target_domain_min_R mean=0.060560 ± 0.009851.

## Feedback and stopping decision

The loop stopped with `stop_completed`: Route route_01 left the race as stopped_round_limit: reached the per-route cap of 2 executed rounds

## Limitations

- Whether the constant-index approximation provided in the prompt (n=1.46, n=2.40) is sufficient for final validation without dispersion data.
- No traceable literature item directly influenced the executed route; the result is theory-guided.

## Literature provenance

- No literature reference was used by the executed route.
