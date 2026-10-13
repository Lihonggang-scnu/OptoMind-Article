# TMM research and design result

Run status: completed. Acceptance: accepted.

## Problem interpretation

Design a planar two-layer antireflection coating on a semi-infinite glass substrate (refractive index 1.52, lossless) with air incidence (index 1.0). The stack must consist of exactly one layer of SiO2 (n=1.46) and one layer of TiO2 (n=2.40) in either order, with each layer thickness constrained between 40 and 160 nm. The objective is to minimize the arithmetic mean power reflectance for s-polarized light at normal incidence over the wavelength range 450 to 700 nm (sampled every 5 nm). A mean reflectance below 0.03 is a stated soft target preference. The method must use deterministic Transfer Matrix Method (TMM) only, excluding RCWA, FDFD, surrogates, or hardware acceleration. The optimization allows up to two research rounds with one route and a maximum of 64 solver evaluations per round using seed 17. Final validation requires observing the full spectrum and performing an independent numeric check before claiming the target is met.

## Original request and target

Design a planar two-layer antireflection coating on a semi-infinite glass substrate (constant refractive index 1.52, lossless), with air incidence (index 1.0). Use only lossless constant-index SiO2 (n=1.46) and TiO2 (n=2.40), one layer of each in either order. Each layer thickness must be between 40 and 160 nm. At normal incidence with s polarization, minimize the arithmetic mean power reflectance sampled from 450 to 700 nm at 5 nm spacing. A mean reflectance below 0.03 is the stated target. Use deterministic TMM only, with no RCWA, FDFD, learned surrogate or hardware. Allow at most two executed research rounds with one route and at most 64 solver evaluations per round, seed 17. After observing the actual result, choose a justified adjustment or stop. Do not claim the target was met without the spectrum and independent numeric check.

## Method research

- No reusable literature method was available; planning used explicit optical theory assumptions.

## Routes executed

- **Optimization of Air/SiO2/TiO2/Glass Stack Thicknesses** — status `completed`, source `literature_planned`, verified candidates 5, best soft score 0.9646.
  - `iteration_01` verified layer orders: n=1.46 / n=2.4.
- **Optimization of Air/TiO2/SiO2/Glass Stack Thicknesses** — status `completed`, source `literature_planned`, verified candidates 5, best soft score 0.7713.
  - `iteration_02` verified layer orders: n=2.4 / n=1.46.

## Planning-source comparison

The memory-only control and literature-planned routes are separated by source. Frozen-standard scores are used for a cross-source verdict only when both arms have scoreable representatives; score deltas inside a route are diagnostics.
- Frozen-standard comparison: `control_or_literature_arm_missing`.
- `literature_planned`: 1 route(s), 1 with verified candidates, 2 executed round(s).

## Recommended candidate portfolio

Scores are used only within each route; rows from different contracts are not a global leaderboard.

- `ar_coating_sio2_tio2_order1__gradi__2a0f0b4bfdb3` stack (2 layers): n=1.46 / n=2.4.
- `ar_coating_sio2_tio2_order1__gradi__c3e8b026033b` stack (2 layers): n=1.46 / n=2.4.
- `ar_coating_sio2_tio2_order1__gradi__bc3e997095d4` stack (2 layers): n=1.46 / n=2.4.
- `opt_ar_coating_tio2_sio2__gradient_thickness__01` stack (2 layers): n=2.4 / n=1.46.
- `opt_ar_coating_tio2_sio2__gradient_thickness__05` stack (2 layers): n=2.4 / n=1.46.

| Candidate | Route | Within-route soft score | Comparable spectral summary | Robustness | Thicknesses (nm) |
|---|---|---:|---|---:|---|
| `ar_coating_sio2_tio2_order1__gradi__2a0f0b4bfdb3` | Optimization of Air/SiO2/TiO2/Glass Stack Thicknesses | 0.9646 | mean_reflectance_target=0.035362 | 0.5888 | 86.56, 94.48 |
| `ar_coating_sio2_tio2_order1__gradi__c3e8b026033b` | Optimization of Air/SiO2/TiO2/Glass Stack Thicknesses | 0.9634 | mean_reflectance_target=0.036559 | n/a | 87.00, 94.00 |
| `ar_coating_sio2_tio2_order1__gradi__bc3e997095d4` | Optimization of Air/SiO2/TiO2/Glass Stack Thicknesses | 0.9487 | mean_reflectance_target=0.051325 | n/a | 61.79, 91.93 |
| `opt_ar_coating_tio2_sio2__gradient_thickness__01` | Optimization of Air/TiO2/SiO2/Glass Stack Thicknesses | 0.7713 | mean_reflectance_target=0.228711 | 0.1770 | 88.00, 95.00 |
| `opt_ar_coating_tio2_sio2__gradient_thickness__05` | Optimization of Air/TiO2/SiO2/Glass Stack Thicknesses | 0.6462 | mean_reflectance_target=0.353849 | n/a | 54.37, 78.00 |

## Manufacturing uncertainty

- `ar_coating_sio2_tio2_order1__gradi__2a0f0b4bfdb3`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_R mean=0.074350 ± 0.004191; target_domain_mean_R mean=0.034974 ± 0.002115; target_domain_min_R mean=0.021919 ± 0.002414.
- `opt_ar_coating_tio2_sio2__gradient_thickness__01`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_R mean=0.348470 ± 0.003550; target_domain_mean_R mean=0.232839 ± 0.009172; target_domain_min_R mean=0.048044 ± 0.007730.

## Feedback and stopping decision

The loop stopped with `stop_completed`: Route route_01 left the race as stopped_round_limit: reached the per-route cap of 2 executed rounds

## Limitations

- Search coverage from the full recorded history: iteration_01 (completed; verified layer orders: n=1.46 / n=2.4); iteration_02 (completed; verified layer orders: n=2.4 / n=1.46).
- Finite optimizer results do not certify physical infeasibility or a global optimum. Target attainment must be assessed against the unchanged original request, not thresholds or untested alternatives proposed in the initial plan.
- No traceable literature item directly influenced the executed route; the result is theory-guided.

## Literature provenance

- No literature reference was used by the executed route.
