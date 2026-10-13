# TMM research and design result

Run status: completed. Acceptance: accepted.

## Problem interpretation

Design a planar two-layer antireflection coating on a semi-infinite glass substrate (refractive index 1.52, lossless) with air incidence (refractive index 1.0). The stack must consist of exactly one layer of lossless SiO2 (n=1.46) and one layer of lossless TiO2 (n=2.40), arranged in either order. Each layer thickness is constrained between 40 nm and 160 nm. The objective is to minimize the arithmetic mean power reflectance at normal incidence with s-polarization, sampled from 450 nm to 700 nm at 5 nm intervals using deterministic Transfer Matrix Method. A mean reflectance below 0.03 is a stated soft target preference. The process allows up to two research rounds with a maximum of 64 solver evaluations per round using seed 17, requiring validation via spectrum and numeric check before claiming success.

## Original request and target

Design a planar two-layer antireflection coating on a semi-infinite glass substrate (constant refractive index 1.52, lossless), with air incidence (index 1.0). Use only lossless constant-index SiO2 (n=1.46) and TiO2 (n=2.40), one layer of each in either order. Each layer thickness must be between 40 and 160 nm. At normal incidence with s polarization, minimize the arithmetic mean power reflectance sampled from 450 to 700 nm at 5 nm spacing. A mean reflectance below 0.03 is the stated target. Use deterministic TMM only, with no RCWA, FDFD, learned surrogate or hardware. Allow at most two executed research rounds with one route and at most 64 solver evaluations per round, seed 17. After observing the actual result, choose a justified adjustment or stop. Do not claim the target was met without the spectrum and independent numeric check.

## Method research

- No reusable literature method was available; planning used explicit optical theory assumptions.

## Routes executed

- **Thickness Optimization of SiO2/TiO2 Bilayer on Glass** — status `completed`, source `literature_planned`, verified candidates 5, best soft score 0.9646.
  - `iteration_01` verified layer orders: n=1.46 / n=2.4.
- **Thickness Optimization of SiO2/TiO2 Bilayer on Glass** — status `completed`, source `literature_planned`, verified candidates 5, best soft score 0.9646.
  - `iteration_02` verified layer orders: n=1.46 / n=2.4.

## Planning-source comparison

The memory-only control and literature-planned routes are separated by source. Frozen-standard scores are used for a cross-source verdict only when both arms have scoreable representatives; score deltas inside a route are diagnostics.
- Frozen-standard comparison: `control_or_literature_arm_missing`.
- `literature_planned`: 1 route(s), 1 with verified candidates, 2 executed round(s).

## Recommended candidate portfolio

Every route was measured on the same quantities, chosen from the request before any route ran, and ranked by the one fixed expression `-mean_reflectance_450_700nm_0deg_s`, so these rows are directly comparable.
- **Best performance**: `opt_ar_coating_sio2_tio2__gradient_thickness__01` from Thickness Optimization of SiO2/TiO2 Bilayer on Glass.
- **Most robust**: `opt_ar_coating_sio2_tio2__gradient_thickness__01` from Thickness Optimization of SiO2/TiO2 Bilayer on Glass.
- **Simplest verified**: `opt_ar_coating_sio2_tio2__gradient_thickness__02` from Thickness Optimization of SiO2/TiO2 Bilayer on Glass.

- `opt_ar_coating_sio2_tio2__gradient_thickness__01` stack (2 layers): n=1.46 / n=2.4.
- `opt_ar_coating_sio2_tio2__gradient_thickness__01` stack (2 layers): n=1.46 / n=2.4.
- `opt_ar_coating_sio2_tio2__gradient_thickness__02` stack (2 layers): n=1.46 / n=2.4.
- `opt_ar_coating_sio2_tio2__gradient_thickness__02` stack (2 layers): n=1.46 / n=2.4.
- `opt_ar_coating_sio2_tio2__gradient_thickness__04` stack (2 layers): n=1.46 / n=2.4.
- `opt_ar_coating_sio2_tio2__gradient_thickness__04` stack (2 layers): n=1.46 / n=2.4.

| Candidate | Route | Frozen standard score | Comparable spectral summary | Robustness | Thicknesses (nm) |
|---|---|---:|---|---:|---|
| `opt_ar_coating_sio2_tio2__gradient_thickness__01` | Thickness Optimization of SiO2/TiO2 Bilayer on Glass | -0.0354 | mean_reflectance_450_700nm_0deg_s=0.0354 | 0.5888 | 86.56, 94.48 |
| `opt_ar_coating_sio2_tio2__gradient_thickness__01` | Thickness Optimization of SiO2/TiO2 Bilayer on Glass | -0.0354 | mean_reflectance_450_700nm_0deg_s=0.0354 | 0.5888 | 86.56, 94.48 |
| `opt_ar_coating_sio2_tio2__gradient_thickness__02` | Thickness Optimization of SiO2/TiO2 Bilayer on Glass | -0.0366 | mean_reflectance_450_700nm_0deg_s=0.0366 | n/a | 87.00, 94.00 |
| `opt_ar_coating_sio2_tio2__gradient_thickness__02` | Thickness Optimization of SiO2/TiO2 Bilayer on Glass | -0.0366 | mean_reflectance_450_700nm_0deg_s=0.0366 | n/a | 87.00, 94.00 |
| `opt_ar_coating_sio2_tio2__gradient_thickness__04` | Thickness Optimization of SiO2/TiO2 Bilayer on Glass | -0.0513 | mean_reflectance_450_700nm_0deg_s=0.0513 | n/a | 61.79, 91.93 |
| `opt_ar_coating_sio2_tio2__gradient_thickness__04` | Thickness Optimization of SiO2/TiO2 Bilayer on Glass | -0.0513 | mean_reflectance_450_700nm_0deg_s=0.0513 | n/a | 61.79, 91.93 |

## Manufacturing uncertainty

- `opt_ar_coating_sio2_tio2__gradient_thickness__01`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_R mean=0.074350 ± 0.004191; target_domain_mean_R mean=0.034974 ± 0.002115; target_domain_min_R mean=0.021919 ± 0.002414.
- `opt_ar_coating_sio2_tio2__gradient_thickness__01`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_R mean=0.074350 ± 0.004191; target_domain_mean_R mean=0.034974 ± 0.002115; target_domain_min_R mean=0.021919 ± 0.002414.

## Feedback and stopping decision

The loop stopped with `stop_completed`: Route route_01 left the race as stopped_round_limit: reached the per-route cap of 2 executed rounds

## Limitations

- Search coverage from the full recorded history: iteration_01 (completed; verified layer orders: n=1.46 / n=2.4); iteration_02 (completed; verified layer orders: n=1.46 / n=2.4).
- Finite optimizer results do not certify physical infeasibility or a global optimum. Target attainment must be assessed against the unchanged original request, not thresholds or untested alternatives proposed in the initial plan.
- No traceable literature item directly influenced the executed route; the result is theory-guided.

## Literature provenance

- No literature reference was used by the executed route.
