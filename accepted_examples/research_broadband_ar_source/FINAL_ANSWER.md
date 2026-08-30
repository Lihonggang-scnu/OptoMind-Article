# TMM research and design result

## Problem interpretation

Design and optimize broadband anti-reflection coatings on a fused-silica substrate in air across 450-700 nm using dielectric materials from {MgF2, SiO2, Al2O3, Ta2O5, TiO2}. Compare layer counts from 4 to 10. Evaluate performance at 0, 30, and 45 degrees for TE and TM polarizations. Optimize for low mean (<0.8%) and worst-case (<3%) reflectance, while penalizing layer count and thickness. Perform robustness analysis against +/- 1 degree angle error and 2 nm thickness tolerance.

## Method research

- **Global Optimization with Thickness Constraints and Needle Insertion**: A two-stage optimization strategy for broadband AR coatings: (1) Perform a global search over layer thicknesses within physical bounds (e.g., 0 to lambda_0/4) to identify candidate solutions meeting average transmittance targets; (2) Apply local 'needle' optimization or iterative layer addition to refine performance while enforcing minimum thickness constraints (e.g., >10 nm) to ensure fabricability. This approach handles the non-convex landscape of thin-film interference by combining broad exploration with precise local tuning.
- **Material Pair Selection Based on Refractive Index Contrast and Substrate Compatibility**: Select low-index (L) and high-index (H) dielectric pairs from a candidate set to maximize bandwidth and angular stability. For fused silica substrates (n ~ 1.46), materials like MgF2 (n ~ 1.38) serve as effective low-index layers, while HfO2, Ta2O5, or TiO2 serve as high-index layers. The design can start with either L or H depending on the desired phase matching, but outer layers should often be chemically durable low-index materials (e.g., SiO2 or MgF2) for environmental protection. Performance is evaluated by averaging TE and TM polarizations at multiple angles of incidence.

## Routes executed

- **Fixed 4-layer analyze known stack route (MgF2/SiO2/Ta2O5)** — status `completed`, verified candidates 9, best soft score 0.6675.
- **Fixed 7-layer analyze known stack route (MgF2/TiO2)** — status `completed`, verified candidates 9, best soft score 0.7860.
- **Fixed 10-layer optimize existing stack route (SiO2/Ta2O5)** — status `completed`, verified candidates 9, best soft score 0.6725.

## Recommended candidate portfolio

All listed routes use the same canonical user target contract, so their soft scores and reported spectral metrics are directly comparable.
- **Best performance**: `opt_7layer_mgf2_tio2_rmin__gradien__53125e899e55` from Fixed 7-layer analyze known stack route (MgF2/TiO2).
- **Most robust**: `opt_7layer_mgf2_tio2_rmin__gradien__53125e899e55` from Fixed 7-layer analyze known stack route (MgF2/TiO2).
- **Simplest verified**: `opt_4layer_mg_sio_ta_mg__gradient_thickness__01` from Fixed 4-layer analyze known stack route (MgF2/SiO2/Ta2O5).

- `opt_7layer_mgf2_tio2_rmin__gradien__53125e899e55` stack (7 layers): mgf2 / tio2 / mgf2 / tio2 / mgf2 / tio2 / mgf2.
- `opt_10layer_sio2_ta2o5_broadband_a__713e5a89a46c` stack (10 layers): sio2 / ta2o5 / sio2 / ta2o5 / sio2 / ta2o5 / sio2 / ta2o5 / sio2 / ta2o5.
- `opt_4layer_mg_sio_ta_mg__gradient_thickness__01` stack (4 layers): mgf2 / sio2 / ta2o5 / mgf2.
- `opt_10layer_sio2_ta2o5_broadband_a__c9eebb10c1aa` stack (10 layers): sio2 / ta2o5 / sio2 / ta2o5 / sio2 / ta2o5 / sio2 / ta2o5 / sio2 / ta2o5.
- `opt_4layer_mg_sio_ta_mg__different__58a37785e4b3` stack (4 layers): mgf2 / sio2 / ta2o5 / mgf2.
- `opt_7layer_mgf2_tio2_rmin__differe__89130ff6084c` stack (7 layers): mgf2 / tio2 / mgf2 / tio2 / mgf2 / tio2 / mgf2.
- `opt_10layer_sio2_ta2o5_broadband_a__46a969e7c5a2` stack (10 layers): sio2 / ta2o5 / sio2 / ta2o5 / sio2 / ta2o5 / sio2 / ta2o5 / sio2 / ta2o5.
- `opt_7layer_mgf2_tio2_rmin__baseline` stack (7 layers): mgf2 / tio2 / mgf2 / tio2 / mgf2 / tio2 / mgf2.

| Candidate | Route | Shared-contract soft score | Comparable spectral summary | Robustness | Thicknesses (nm) |
|---|---|---:|---|---:|---|
| `opt_7layer_mgf2_tio2_rmin__gradien__53125e899e55` | Fixed 7-layer analyze known stack route (MgF2/TiO2) | 0.7860 | mean R=0.880%; worst-channel mean R=1.699%; worst-point R=3.697%; targets=6/12 | 0.7455 | 103.49, 111.72, 225.78, 32.70, 38.86, 26.19, 228.24 |
| `opt_10layer_sio2_ta2o5_broadband_a__713e5a89a46c` | Fixed 10-layer optimize existing stack route (SiO2/Ta2O5) | 0.6725 | mean R=1.770%; worst-channel mean R=3.762%; worst-point R=7.827%; targets=3/12 | 0.6198 | 131.31, 37.60, 55.20, 36.59, 74.65, 36.74, 62.52, 43.78, 53.93, 31.14 |
| `opt_4layer_mg_sio_ta_mg__gradient_thickness__01` | Fixed 4-layer analyze known stack route (MgF2/SiO2/Ta2O5) | 0.6675 | mean R=1.931%; worst-channel mean R=3.137%; worst-point R=6.305%; targets=3/12 | 0.6612 | 80.00, 29.00, 127.00, 31.00 |
| `opt_10layer_sio2_ta2o5_broadband_a__c9eebb10c1aa` | Fixed 10-layer optimize existing stack route (SiO2/Ta2O5) | 0.6622 | mean R=1.724%; worst-channel mean R=3.713%; worst-point R=7.808%; targets=3/12 | 0.6224 | 131.00, 38.00, 55.00, 37.00, 75.00, 37.00, 63.00, 44.00, 54.00, 31.00 |
| `opt_4layer_mg_sio_ta_mg__different__58a37785e4b3` | Fixed 4-layer analyze known stack route (MgF2/SiO2/Ta2O5) | 0.5056 | mean R=3.655%; worst-channel mean R=6.258%; worst-point R=12.317%; targets=1/12 | n/a | 54.33, 82.92, 131.83, 216.96 |
| `opt_7layer_mgf2_tio2_rmin__differe__89130ff6084c` | Fixed 7-layer analyze known stack route (MgF2/TiO2) | 0.1475 | mean R=23.304%; worst-channel mean R=35.272%; worst-point R=58.830%; targets=0/12 | n/a | 155.44, 17.98, 213.37, 32.74, 189.92, 136.71, 56.83 |
| `opt_10layer_sio2_ta2o5_broadband_a__46a969e7c5a2` | Fixed 10-layer optimize existing stack route (SiO2/Ta2O5) | 0.1302 | mean R=25.575%; worst-channel mean R=36.819%; worst-point R=68.913%; targets=0/12 | n/a | 106.57, 82.24, 142.28, 85.75, 141.95, 87.04, 65.10, 42.35, 58.15, 39.02 |
| `opt_7layer_mgf2_tio2_rmin__baseline` | Fixed 7-layer analyze known stack route (MgF2/TiO2) | 0.0625 | mean R=68.761%; worst-channel mean R=76.132%; worst-point R=91.448%; targets=0/12 | n/a | 100.00, 50.00, 100.00, 50.00, 100.00, 50.00, 100.00 |

## Manufacturing uncertainty

- `opt_7layer_mgf2_tio2_rmin__gradien__53125e899e55`: model=absolute_normal, sigma_nm=2.000, relative_fraction=0.0000, common_angle_bound_deg=1.000, samples=16, failed=0; target_domain_max_R mean=0.048756 ± 0.011992; target_domain_mean_R mean=0.010168 ± 0.002008; target_domain_min_R mean=0.000155 ± 0.000302.
- `opt_10layer_sio2_ta2o5_broadband_a__713e5a89a46c`: model=absolute_normal, sigma_nm=2.000, relative_fraction=0.0000, common_angle_bound_deg=1.000, samples=16, failed=0; target_domain_max_R mean=0.100250 ± 0.034946; target_domain_mean_R mean=0.019567 ± 0.004027; target_domain_min_R mean=0.000046 ± 0.000056.
- `opt_4layer_mg_sio_ta_mg__gradient_thickness__01`: model=absolute_normal, sigma_nm=2.000, relative_fraction=0.0000, common_angle_bound_deg=1.000, samples=16, failed=0; target_domain_max_R mean=0.063444 ± 0.001617; target_domain_mean_R mean=0.019638 ± 0.000612; target_domain_min_R mean=0.003321 ± 0.000779.
- `opt_10layer_sio2_ta2o5_broadband_a__c9eebb10c1aa`: model=absolute_normal, sigma_nm=2.000, relative_fraction=0.0000, common_angle_bound_deg=1.000, samples=16, failed=0; target_domain_max_R mean=0.103816 ± 0.036012; target_domain_mean_R mean=0.019144 ± 0.004251; target_domain_min_R mean=0.000046 ± 0.000060.

## Feedback and stopping decision

The loop stopped with `stop_completed`: The bounded route portfolio is complete; preserve the best verified performance, robustness, and simplicity trade-offs.

## Limitations

- specific refractive index data sources for the candidate materials
- exact definition of 'simplest' beyond layer count and thickness
- weighting factors for the multi-objective optimization (performance vs complexity)
- Whether to start the stack with a low-index or high-index material is left to the optimizer in routes 03 and 04, but fixed in routes 01 and 02 for comparison. Route 01 starts with Low, Route 02 starts with Low. Route 03 starts with Low (SiO2). Route 04 starts with Low (MgF2). This consistency allows for fair comparison of layer count and material pair effects.
- The exact weighting of 'fewer layers' vs 'lower thickness' vs 'performance' is handled by the TMM task compiler's ranking logic, as these are soft objectives.

## Literature provenance

- [s2-chunk:CorpusId:120897377:s2chunk:120897377:11570:13871:4ecff8604c685a3e] Wide bandpass optical filters with TiO2 and Ta2O5 (n.d.; CorpusId:120897377); use=method_guidance, source=s2_snippet_search, depth=s2_snippet.
- [s2-chunk:CorpusId:284130796:s2chunk:284130796:10539:11647:6bd3ac6684ed60ca] Sensitivity and tolerance analysis of single-layer MgF2 and SiO2 anti-reflection coatings (n.d.; CorpusId:284130796); use=method_guidance, source=s2_snippet_search, depth=s2_snippet.
- [s2-chunk:CorpusId:283155301:s2chunk:283155301:16911:18321:96d511d2105e0454] Design methodology for high-performance and robust anti-reflective coatings for the Tetra-ARmed Super-Ifu Spectrograph (n.d.; CorpusId:283155301); use=method_guidance, source=s2_snippet_search, depth=s2_snippet.
- [s2-chunk:CorpusId:270586583:s2chunk:270586583:1377:2605:e8d20f1c243b973e] Antireflective Double Layer Coating Based on SiO2/MgF2 Films with Various Substrate BK7 Glass and Corning Glass (n.d.; CorpusId:270586583); use=method_guidance, source=s2_snippet_search, depth=s2_snippet.
- [s2-chunk:CorpusId:225461372:s2chunk:225461372:27939:30123:62f6d887b5b63324] Fabrication of Ultralow Stress TiO2/SiO2 Optical Coatings by Plasma Ion-Assisted Deposition (n.d.; CorpusId:225461372); use=method_guidance, source=s2_snippet_search, depth=s2_snippet.
