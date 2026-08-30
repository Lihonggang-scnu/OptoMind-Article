# TMM research and design result

## Problem interpretation

Design and optimize isotropic all-dielectric planar multilayer polarizing beam splitters on a fused-silica substrate with incidence from air at 45 degrees across the 500-650 nm wavelength band. The design must utilize materials selected from MgF2, SiO2, Ta2O5, and TiO2. Compare configurations with exactly 6, 10, and 14 layers, where each layer thickness is constrained between 25 nm and 220 nm. Optimize for high TE reflectance (mean >= 90%) and high TM transmittance (mean >= 85%) as soft scoring preferences, while simultaneously penalizing layer count and total physical thickness. Identify candidates that offer the best performance, highest robustness, and simplest structure The numerical performance targets are soft scoring preferences. Perform robustness analysis by evaluating sensitivity to independent normally distributed layer-thickness errors (sigma = 1.5% of nominal thickness) and a common incidence-angle uncertainty bounded by +/- 1 degree.

## Method research

- No reusable literature method was available; planning used explicit optical theory assumptions.

## Routes executed

- **Optimization of 6-Layer All-Dielectric Polarizing Beam Splitter** — status `completed`, verified candidates 9, best soft score 0.4332.
- **Optimization of 10-Layer All-Dielectric Polarizing Beam Splitter** — status `completed`, verified candidates 10, best soft score 0.4379.
- **Optimization of 14-Layer All-Dielectric Polarizing Beam Splitter** — status `completed`, verified candidates 9, best soft score 0.4349.

## Recommended candidate portfolio

All listed routes use the same canonical user target contract, so their soft scores and reported spectral metrics are directly comparable.
- **Best performance**: `pbs_10layer_opt__gradient_thickness__01` from Optimization of 10-Layer All-Dielectric Polarizing Beam Splitter.
- **Most robust**: `pbs_10layer_opt__gradient_thickness__01` from Optimization of 10-Layer All-Dielectric Polarizing Beam Splitter.
- **Simplest verified**: `opt_pbs_6layer__baseline` from Optimization of 6-Layer All-Dielectric Polarizing Beam Splitter.

- `pbs_10layer_opt__gradient_thickness__01` stack (10 layers): ta2o5 / sio2 / ta2o5 / sio2 / ta2o5 / sio2 / ta2o5 / sio2 / ta2o5 / sio2.
- `pbs_14layer_opt__gradient_thickness__01` stack (14 layers): tio2 / sio2 / tio2 / sio2 / tio2 / sio2 / tio2 / sio2 / tio2 / sio2 / tio2 / sio2 / tio2 / sio2.
- `opt_pbs_6layer__gradient_thickness__01` stack (6 layers): tio2 / sio2 / tio2 / sio2 / tio2 / sio2.
- `opt_pbs_6layer__differential_evolu__3676723dbad0` stack (6 layers): tio2 / sio2 / tio2 / sio2 / tio2 / sio2.
- `pbs_10layer_opt__differential_evol__75afeebce2ac` stack (10 layers): ta2o5 / sio2 / ta2o5 / sio2 / ta2o5 / sio2 / ta2o5 / sio2 / ta2o5 / sio2.
- `pbs_14layer_opt__differential_evol__b131bfa3c78c` stack (14 layers): tio2 / sio2 / tio2 / sio2 / tio2 / sio2 / tio2 / sio2 / tio2 / sio2 / tio2 / sio2 / tio2 / sio2.
- `opt_pbs_6layer__baseline` stack (6 layers): tio2 / sio2 / tio2 / sio2 / tio2 / sio2.
- `pbs_10layer_opt__baseline` stack (10 layers): ta2o5 / sio2 / ta2o5 / sio2 / ta2o5 / sio2 / ta2o5 / sio2 / ta2o5 / sio2.
- `pbs_14layer_opt__baseline` stack (14 layers): tio2 / sio2 / tio2 / sio2 / tio2 / sio2 / tio2 / sio2 / tio2 / sio2 / tio2 / sio2 / tio2 / sio2.

| Candidate | Route | Shared-contract soft score | Comparable spectral summary | Robustness | Thicknesses (nm) |
|---|---|---:|---|---:|---|
| `pbs_10layer_opt__gradient_thickness__01` | Optimization of 10-Layer All-Dielectric Polarizing Beam Splitter | 0.4379 | mean R=68.635%; worst-channel mean R=68.635%; worst-point R=n/a; targets=0/2 | 0.4378 | 85.63, 79.27, 45.46, 82.99, 71.44, 171.81, 94.72, 127.89, 121.98, 28.99 |
| `pbs_14layer_opt__gradient_thickness__01` | Optimization of 14-Layer All-Dielectric Polarizing Beam Splitter | 0.4349 | mean R=67.867%; worst-channel mean R=67.867%; worst-point R=n/a; targets=0/2 | 0.4350 | 52.15, 148.98, 77.39, 127.22, 102.49, 143.68, 85.98, 138.88, 109.35, 122.84, 117.04, 176.05, 120.21, 37.04 |
| `opt_pbs_6layer__gradient_thickness__01` | Optimization of 6-Layer All-Dielectric Polarizing Beam Splitter | 0.4332 | mean R=66.803%; worst-channel mean R=66.803%; worst-point R=n/a; targets=0/2 | 0.4328 | 81.40, 69.64, 37.10, 92.45, 121.39, 32.53 |
| `opt_pbs_6layer__differential_evolu__3676723dbad0` | Optimization of 6-Layer All-Dielectric Polarizing Beam Splitter | 0.4311 | mean R=65.408%; worst-channel mean R=65.408%; worst-point R=n/a; targets=0/2 | 0.4307 | 67.95, 118.56, 80.02, 171.64, 100.17, 162.94 |
| `pbs_10layer_opt__differential_evol__75afeebce2ac` | Optimization of 10-Layer All-Dielectric Polarizing Beam Splitter | 0.4304 | mean R=75.373%; worst-channel mean R=75.373%; worst-point R=n/a; targets=0/2 | n/a | 99.46, 76.57, 81.54, 27.33, 123.16, 64.97, 81.98, 210.46, 64.89, 60.52 |
| `pbs_14layer_opt__differential_evol__b131bfa3c78c` | Optimization of 14-Layer All-Dielectric Polarizing Beam Splitter | 0.4190 | mean R=67.290%; worst-channel mean R=67.290%; worst-point R=n/a; targets=0/2 | n/a | 106.87, 160.18, 34.08, 31.05, 175.85, 97.62, 178.85, 51.01, 52.70, 177.02, 208.49, 29.97, 102.97, 139.57 |
| `opt_pbs_6layer__baseline` | Optimization of 6-Layer All-Dielectric Polarizing Beam Splitter | 0.4164 | mean R=73.756%; worst-channel mean R=73.756%; worst-point R=n/a; targets=0/2 | n/a | 80.00, 110.00, 80.00, 110.00, 80.00, 110.00 |
| `pbs_10layer_opt__baseline` | Optimization of 10-Layer All-Dielectric Polarizing Beam Splitter | 0.3991 | mean R=91.538%; worst-channel mean R=91.538%; worst-point R=n/a; targets=1/2 | n/a | 80.00, 80.00, 80.00, 80.00, 80.00, 80.00, 80.00, 80.00, 80.00, 80.00 |
| `pbs_14layer_opt__baseline` | Optimization of 14-Layer All-Dielectric Polarizing Beam Splitter | 0.3953 | mean R=76.504%; worst-channel mean R=76.504%; worst-point R=n/a; targets=0/2 | n/a | 80.00, 110.00, 80.00, 110.00, 80.00, 110.00, 80.00, 110.00, 80.00, 110.00, 80.00, 110.00, 80.00, 110.00 |

## Manufacturing uncertainty

- `pbs_10layer_opt__gradient_thickness__01`: model=relative_normal, sigma_nm=0.000, relative_fraction=0.0150, common_angle_bound_deg=1.000, samples=16, failed=0; center_transmittance mean=0.682507 ± 0.020100; center_wavelength_nm mean=575.000000 ± 0.000000; passband_fwhm_nm mean=52.250000 ± 5.942432; passband_peak_transmittance mean=0.685514 ± 0.021094; passband_peak_wavelength_nm mean=572.562500 ± 7.656441; target_domain_max_R mean=0.722280 ± 0.017693; target_domain_max_T mean=0.751693 ± 0.019457; target_domain_mean_R mean=0.691254 ± 0.015288; target_domain_mean_T mean=0.671343 ± 0.013193; target_domain_min_R mean=0.610960 ± 0.023266; target_domain_min_T mean=0.637127 ± 0.008183.
- `pbs_14layer_opt__gradient_thickness__01`: model=relative_normal, sigma_nm=0.000, relative_fraction=0.0150, common_angle_bound_deg=1.000, samples=16, failed=0; center_transmittance mean=0.694546 ± 0.032128; center_wavelength_nm mean=575.000000 ± 0.000000; passband_fwhm_nm mean=62.500000 ± 31.780497; passband_peak_transmittance mean=0.704270 ± 0.030909; passband_peak_wavelength_nm mean=579.625000 ± 2.183031; target_domain_max_R mean=0.904250 ± 0.017616; target_domain_max_T mean=0.826296 ± 0.052118; target_domain_mean_R mean=0.676877 ± 0.014691; target_domain_mean_T mean=0.670282 ± 0.014433; target_domain_min_R mean=0.500514 ± 0.077788; target_domain_min_T mean=0.510026 ± 0.046916.
- `opt_pbs_6layer__gradient_thickness__01`: model=relative_normal, sigma_nm=0.000, relative_fraction=0.0150, common_angle_bound_deg=1.000, samples=32, failed=0; center_transmittance mean=0.643792 ± 0.014455; center_wavelength_nm mean=575.000000 ± 0.000000; passband_fwhm_nm mean=66.250000 ± 3.250000; passband_peak_transmittance mean=0.657820 ± 0.014115; passband_peak_wavelength_nm mean=555.000000 ± 0.000000; target_domain_max_R mean=0.732117 ± 0.012474; target_domain_max_T mean=0.759764 ± 0.013749; target_domain_mean_R mean=0.669123 ± 0.007733; target_domain_mean_T mean=0.665952 ± 0.011007; target_domain_min_R mean=0.590263 ± 0.014104; target_domain_min_T mean=0.598623 ± 0.012178.
- `opt_pbs_6layer__differential_evolu__3676723dbad0`: model=relative_normal, sigma_nm=0.000, relative_fraction=0.0150, common_angle_bound_deg=1.000, samples=32, failed=0; center_transmittance mean=0.726989 ± 0.014115; center_wavelength_nm mean=575.000000 ± 0.000000; passband_peak_transmittance mean=0.734433 ± 0.011390; passband_peak_wavelength_nm mean=584.265625 ± 3.938957; target_domain_max_R mean=0.820283 ± 0.010221; target_domain_max_T mean=0.768284 ± 0.019230; target_domain_mean_R mean=0.652101 ± 0.011282; target_domain_mean_T mean=0.671549 ± 0.009458; target_domain_min_R mean=0.542521 ± 0.025665; target_domain_min_T mean=0.501801 ± 0.016942.

## Feedback and stopping decision

The loop stopped with `stop_completed`: The bounded route portfolio is complete; preserve the best verified performance, robustness, and simplicity trade-offs.

## Limitations

- specific ordering of materials within the stack is not fixed and must be determined by optimization
- exact definition of 'simplest' beyond layer count and thickness is not specified, though layer count and total thickness are explicitly rewarded
- Whether TiO2 or Ta2O5 provides superior performance for this specific application is unknown and will be determined by the optimization results.
- The exact weighting of the composite objective function (performance vs. complexity) is subjective; the solver should use a reasonable default or report Pareto fronts if possible.
- No traceable literature item directly influenced the executed route; the result is theory-guided.

## Literature provenance

- No literature reference was used by the executed route.
