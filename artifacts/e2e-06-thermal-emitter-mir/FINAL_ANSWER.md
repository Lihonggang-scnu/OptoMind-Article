# TMM research and design result

## Problem interpretation

Design a planar multilayer dielectric film for a thermophotovoltaic system cold emitter using only HfO2 and SiO2 on a unspecified material substrate with unspecified material incidence. Target high emissivity (absorptance) in 8000-13000 nm (8-13 micrometers) and low emissivity in 3000-5000 nm (3-5 micrometers). Total layers must not exceed 24.

## Method research

- **Evolutionary Thickness and Sequence Optimization**: Use genetic or memetic algorithms to optimize layer thicknesses and sequence from a fixed material library (e.g., HfO2, SiO2) to match a target emissivity profile calculated via Transfer Matrix Method. The optimization iteratively selects, mutates, and crosses over design parameters to minimize an objective function defining spectral targets.
- **Low-Extinction Material Selection**: Select dielectric materials based on minimal extinction coefficient in the target infrared band to maintain high spectral contrast between emission and suppression bands. Materials are chosen from a candidate list based on chemical resistance and optical loss characteristics.
- **Aperiodic Spectral Shaping**: Employ aperiodic layer thicknesses optimized via algorithm rather than periodic quarter-wave stacks to control emission peak width and suppress long-wavelength radiation. This allows shaping the thermal emission spectrum in terms of peak shape and emitted power.

## Routes executed

- **Periodic Quarter-Wave Bragg Stack for 8-13 μm High Emissivity** — status `completed`, source `literature_planned`, verified candidates 9, best soft score 0.8903.
- **Aperiodic Thickness-Graded Stack for Broadened 8-13 μm Response** — status `completed`, source `literature_planned`, verified candidates 8, best soft score 0.6195.
- **Defect Cavity Microresonator for Narrowband 8-13 μm Peak** — status `not_run`, source `literature_planned`, verified candidates 0, best soft score n/a.
- **DBR-based spectral emissivity selector tuned to 3-5 μm reflection band** — status `completed`, source `llm_memory_control`, verified candidates 9, best soft score 0.8022.
- **Quarter-Wave Initialized 12-Layer Stack with Localized Perturbation Optimization** — status `completed`, source `literature_planned`, verified candidates 9, best soft score 0.8409.
- **Bounded Aperiodic Stack with Quarter-Wave Initialized Thicknesses (24L)** — status `failed`, source `literature_planned`, verified candidates 0, best soft score n/a.
- **17-Layer Defect Cavity with Central SiO2 Defect** — status `completed`, source `literature_planned`, verified candidates 9, best soft score 0.9235.
- **HfO2/SiO2 Gradient Thickness Stack with QWOT Regularization on Si Substrate** — status `completed`, source `llm_memory_control`, verified candidates 9, best soft score 0.9027.
- **Quarter-wave HfO2/SiO2 stack with bounded perturbation refinement** — status `completed`, source `llm_memory_control`, verified candidates 9, best soft score 0.7772.
- **Thickness-Bounded 12-Layer Search (352-448 nm)** — status `completed`, source `literature_planned`, verified candidates 9, best soft score 0.8270.
- **Reduced 16-Layer Quarter-Wave Stack Initialized at 10.5 μm** — status `completed`, source `literature_planned`, verified candidates 9, best soft score 0.9057.
- **17-Layer Defect Cavity with Narrowed Bounds and Perturbation** — status `completed`, source `literature_planned`, verified candidates 9, best soft score 0.9183.
- **Fine-Grained 16-Layer Optimization with Reduced Perturbation and Smoothness Penalty** — status `completed`, source `literature_planned`, verified candidates 9, best soft score 0.8907.
- **20-layer HfO2/SiO2 thickness-optimized emissivity discriminator on Si** — status `completed`, source `llm_memory_control`, verified candidates 6, best soft score 0.9136.
- **12-Layer Stack with Perturbed Initialization and Outer Layer Clamping** — status `completed`, source `literature_planned`, verified candidates 9, best soft score 0.8392.
- **17-Layer Defect Cavity with Constrained Mutation and Round-1 Seeding** — status `completed`, source `literature_planned`, verified candidates 9, best soft score 0.9237.
- **L-BFGS Local Refinement with ±12% Quarter-Wave Initialization (16L)** — status `completed`, source `literature_planned`, verified candidates 9, best soft score 0.8907.
- **HfO2/SiO2 20-layer emissivity selective stack with refined thickness perturbation** — status `completed`, source `llm_memory_control`, verified candidates 4, best soft score 0.9254.
- **16-Layer Quarter-Wave Initialized Stack with ±8% Bounded Optimization** — status `completed`, source `literature_planned`, verified candidates 9, best soft score 0.6470.
- **Constrained Central Cavity with Narrowed Spacer Bounds (250-420 nm)** — status `completed`, source `literature_planned`, verified candidates 9, best soft score 0.9093.
- **HfO2/SiO2 20-layer emissivity selective stack on Si with gradient thickness optimization** — status `completed`, source `llm_memory_control`, verified candidates 9, best soft score 0.8733.

## Planning-source comparison

The memory-only control and literature-planned routes are separated by source. Frozen-standard scores are used for a cross-source verdict only when both arms have scoreable representatives; score deltas inside a route are diagnostics.
- Frozen-standard comparison: `memory_control_higher`.
- Control minus best literature frozen score: `0.011839`.
- `literature_planned`: 3 route(s), 3 with verified candidates, 15 executed round(s).
- `llm_memory_control`: 1 route(s), 1 with verified candidates, 6 executed round(s).

## Recommended candidate portfolio

Every route was measured on the same quantities, chosen from the request before any route ran, and ranked by the one fixed expression `mean_emissivity_8000_13000nm - mean_emissivity_3000_5000nm`, so these rows are directly comparable.
- **Best performance**: `opt_20l_hfo2_sio2_si_mir__gradient_thickness__01` from DBR-based spectral emissivity selector tuned to 3-5 μm reflection band.
- **Most robust**: `hfso2_si_defect_opt_v1__baseline` from 17-Layer Defect Cavity with Narrowed Bounds and Perturbation.
- **Simplest verified**: `emis_control_hfosio2_si__gradient_thickness__01` from Periodic Quarter-Wave Bragg Stack for 8-13 μm High Emissivity.

- `opt_20l_hfo2_sio2_si_mir__gradient_thickness__01` stack (20 layers): hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2.
- `midir_emissivity_20layer_opt__diff__cb2063dacf0c` stack (20 layers): hfo2 / SiO2 (Kischkat) / hfo2 / SiO2 (Kischkat) / hfo2 / SiO2 (Kischkat) / hfo2 / SiO2 (Kischkat) / hfo2 / SiO2 (Kischkat) / hfo2 / SiO2 (Kischkat) / hfo2 / SiO2 (Kischkat) / hfo2 / SiO2 (Kischkat) / hfo2 / SiO2 (Kischkat) / hfo2 / SiO2 (Kischkat).
- `opt_17l_hfo2_sio2_emissivity__grad__c646e103070c` stack (17 layers): hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2.
- `opt_17l_hfo2_sio2_emissivity__grad__e92de32f4152` stack (17 layers): hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2.
- `optimise_17layer_cavity_ir__gradie__52b4226d9de6` stack (17 layers): hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2.
- `hfso2_si_defect_opt_v1__gradient_thickness__01` stack (17 layers): hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / sio2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2.
- `midir_emissivity_20layer_opt__diff__e7a0e06c75b7` stack (20 layers): hfo2 / SiO2 (Kischkat) / hfo2 / SiO2 (Kischkat) / hfo2 / SiO2 (Kischkat) / hfo2 / SiO2 (Kischkat) / hfo2 / SiO2 (Kischkat) / hfo2 / SiO2 (Kischkat) / hfo2 / SiO2 (Kischkat) / hfo2 / SiO2 (Kischkat) / hfo2 / SiO2 (Kischkat) / hfo2 / SiO2 (Kischkat).
- `hfso2_si_defect_opt_v1__baseline` stack (17 layers): hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / sio2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2.
- `hfo2_sio2_20layer_emissivity_opt____1d8ba87e7733` stack (20 layers): hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2.
- `hfso2_si_defect_opt_v1__differenti__2672ccaa422a` stack (17 layers): hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / sio2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2.
- `midir_emissivity_20layer_opt__diff__9ba0a7f32ab1` stack (20 layers): hfo2 / SiO2 (Kischkat) / hfo2 / SiO2 (Kischkat) / hfo2 / SiO2 (Kischkat) / hfo2 / SiO2 (Kischkat) / hfo2 / SiO2 (Kischkat) / hfo2 / SiO2 (Kischkat) / hfo2 / SiO2 (Kischkat) / hfo2 / SiO2 (Kischkat) / hfo2 / SiO2 (Kischkat) / hfo2 / SiO2 (Kischkat).
- `hfso2_cavity_midir_abs__gradient_thickness__01` stack (17 layers): hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2 / hfo2 / sio2.

| Candidate | Route | Frozen standard score | Comparable spectral summary | Robustness | Thicknesses (nm) |
|---|---|---:|---|---:|---|
| `opt_20l_hfo2_sio2_si_mir__gradient_thickness__01` | DBR-based spectral emissivity selector tuned to 3-5 μm reflection band | 0.8591 | mean_emissivity_8000_13000nm=0.9277; mean_emissivity_3000_5000nm=0.0686 | 0.4507 | 1283.00, 314.00, 1246.00, 537.00, 1186.00, 619.00, 1144.00, 615.00, 1616.00, 744.00, 1011.00, 703.00, 239.00, 898.00, 717.00, 1586.00, 1397.00, 1597.00, 1061.00, 1267.00 |
| `midir_emissivity_20layer_opt__diff__cb2063dacf0c` | HfO2/SiO2 20-layer emissivity selective stack with refined thickness perturbation | 0.8508 | mean_emissivity_8000_13000nm=0.8664; mean_emissivity_3000_5000nm=0.0156 | 0.6197 | 1438.47, 269.86, 962.40, 1889.06, 588.57, 1261.19, 1168.01, 2677.33, 2800.83, 484.50, 1788.26, 1111.04, 2088.28, 1908.87, 1977.45, 712.93, 2662.87, 1168.27, 1690.11, 2325.96 |
| `opt_17l_hfo2_sio2_emissivity__grad__c646e103070c` | 17-Layer Defect Cavity with Constrained Mutation and Round-1 Seeding | 0.8473 | mean_emissivity_8000_13000nm=0.9228; mean_emissivity_3000_5000nm=0.0755 | 0.5446 | 546.00, 229.00, 559.00, 427.00, 413.00, 606.00, 427.00, 812.00, 812.00, 297.00, 1695.00, 271.00, 2783.00, 217.00, 2851.00, 1034.00, 1374.00 |
| `opt_17l_hfo2_sio2_emissivity__grad__e92de32f4152` | 17-Layer Defect Cavity with Constrained Mutation and Round-1 Seeding | 0.8472 | mean_emissivity_8000_13000nm=0.9215; mean_emissivity_3000_5000nm=0.0743 | 0.5466 | 548.77, 235.98, 541.65, 455.91, 411.83, 614.55, 411.00, 488.11, 1154.61, 280.70, 1714.80, 264.50, 2678.35, 219.17, 2790.16, 1012.87, 1333.71 |
| `optimise_17layer_cavity_ir__gradie__52b4226d9de6` | 17-Layer Defect Cavity with Central SiO2 Defect | 0.8470 | mean_emissivity_8000_13000nm=0.9229; mean_emissivity_3000_5000nm=0.0759 | n/a | 550.00, 235.00, 557.00, 421.00, 414.00, 607.00, 413.00, 844.00, 844.00, 254.00, 1705.00, 284.00, 2741.00, 305.00, 2785.00, 1056.00, 1422.00 |
| `hfso2_si_defect_opt_v1__gradient_thickness__01` | 17-Layer Defect Cavity with Narrowed Bounds and Perturbation | 0.8365 | mean_emissivity_8000_13000nm=0.8857; mean_emissivity_3000_5000nm=0.0492 | 0.9493 | 516.10, 284.94, 528.03, 508.34, 312.70, 772.00, 319.14, 848.91, 848.91, 497.42, 1578.12, 347.44, 2705.93, 526.61, 1666.01, 1160.06, 1568.10 |
| `midir_emissivity_20layer_opt__diff__e7a0e06c75b7` | HfO2/SiO2 20-layer emissivity selective stack with refined thickness perturbation | 0.8330 | mean_emissivity_8000_13000nm=0.8508; mean_emissivity_3000_5000nm=0.0178 | 0.6051 | 2445.37, 403.05, 666.49, 2819.26, 1964.33, 854.50, 547.99, 2129.45, 1069.03, 693.52, 305.62, 2843.24, 2563.16, 1042.29, 1756.60, 2186.22, 1879.26, 2956.03, 2773.99, 2460.46 |
| `hfso2_si_defect_opt_v1__baseline` | 17-Layer Defect Cavity with Narrowed Bounds and Perturbation | 0.8283 | mean_emissivity_8000_13000nm=0.8685; mean_emissivity_3000_5000nm=0.0402 | 0.9530 | 550.00, 300.00, 557.00, 421.00, 414.00, 607.00, 413.00, 844.00, 844.00, 300.00, 1650.00, 284.00, 2741.00, 305.00, 1550.00, 1056.00, 1422.00 |
| `hfo2_sio2_20layer_emissivity_opt____1d8ba87e7733` | 20-layer HfO2/SiO2 thickness-optimized emissivity discriminator on Si | 0.8273 | mean_emissivity_8000_13000nm=0.9088; mean_emissivity_3000_5000nm=0.0815 | 0.4271 | 1170.00, 547.00, 1187.00, 668.00, 1127.00, 1384.00, 537.00, 759.00, 1665.00, 672.00, 904.00, 749.00, 916.00, 784.00, 2496.00, 740.00, 1784.00, 1448.00, 2018.00, 1270.00 |
| `hfso2_si_defect_opt_v1__differenti__2672ccaa422a` | 17-Layer Defect Cavity with Narrowed Bounds and Perturbation | 0.8230 | mean_emissivity_8000_13000nm=0.8773; mean_emissivity_3000_5000nm=0.0543 | n/a | 370.65, 338.57, 407.30, 622.97, 420.84, 699.62, 311.26, 387.75, 783.01, 834.82, 1561.22, 692.20, 2761.52, 560.04, 1605.36, 1156.60, 1614.51 |
| `midir_emissivity_20layer_opt__diff__9ba0a7f32ab1` | HfO2/SiO2 20-layer emissivity selective stack with refined thickness perturbation | 0.8201 | mean_emissivity_8000_13000nm=0.8348; mean_emissivity_3000_5000nm=0.0147 | 0.6205 | 1465.82, 291.60, 2159.14, 216.76, 919.17, 1231.57, 730.91, 1202.68, 1009.32, 2212.30, 544.24, 809.32, 1564.51, 1755.04, 2288.90, 2910.54, 2384.66, 417.33, 583.66, 1600.95 |
| `hfso2_cavity_midir_abs__gradient_thickness__01` | Constrained Central Cavity with Narrowed Spacer Bounds (250-420 nm) | 0.8187 | mean_emissivity_8000_13000nm=0.9014; mean_emissivity_3000_5000nm=0.0827 | 0.4249 | 1215.66, 431.00, 419.99, 419.17, 419.86, 419.33, 272.24, 1479.59, 1479.59, 532.06, 1102.35, 1417.15, 1273.46, 1663.43, 1139.45, 1164.92, 1188.80 |

## Manufacturing uncertainty

- `opt_20l_hfo2_sio2_si_mir__gradient_thickness__01`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.995262 ± 0.000123; target_domain_mean_A mean=0.681817 ± 0.000027; target_domain_min_A mean=0.028671 ± 0.000055.
- `midir_emissivity_20layer_opt__diff__cb2063dacf0c`: model=relative_uniform, sigma_nm=0.000, relative_fraction=0.0015, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.999680 ± 0.000029; target_domain_mean_A mean=0.623143 ± 0.000052; target_domain_min_A mean=0.002298 ± 0.000008.
- `opt_17l_hfo2_sio2_emissivity__grad__c646e103070c`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.997206 ± 0.000055; target_domain_mean_A mean=0.680334 ± 0.000022; target_domain_min_A mean=0.037244 ± 0.000334.
- `opt_17l_hfo2_sio2_emissivity__grad__e92de32f4152`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.994902 ± 0.000067; target_domain_mean_A mean=0.678994 ± 0.000022; target_domain_min_A mean=0.036881 ± 0.000314.
- `hfso2_si_defect_opt_v1__gradient_thickness__01`: model=relative_uniform, sigma_nm=1.000, relative_fraction=0.0300, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.984762 ± 0.001406; target_domain_mean_A mean=0.645510 ± 0.000680; target_domain_min_A mean=0.016856 ± 0.001246.
- `midir_emissivity_20layer_opt__diff__e7a0e06c75b7`: model=relative_uniform, sigma_nm=0.000, relative_fraction=0.0015, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.994350 ± 0.000105; target_domain_mean_A mean=0.612446 ± 0.000042; target_domain_min_A mean=0.003514 ± 0.000029.
- `hfso2_si_defect_opt_v1__baseline`: model=relative_uniform, sigma_nm=1.000, relative_fraction=0.0300, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.995725 ± 0.000670; target_domain_mean_A mean=0.630268 ± 0.000960; target_domain_min_A mean=0.009769 ± 0.000709.
- `hfo2_sio2_20layer_emissivity_opt____1d8ba87e7733`: model=relative_uniform, sigma_nm=0.000, relative_fraction=0.0020, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.988023 ± 0.000077; target_domain_mean_A mean=0.671975 ± 0.000085; target_domain_min_A mean=0.033539 ± 0.000099.
- `midir_emissivity_20layer_opt__diff__9ba0a7f32ab1`: model=relative_uniform, sigma_nm=0.000, relative_fraction=0.0015, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.989099 ± 0.000064; target_domain_mean_A mean=0.600260 ± 0.000076; target_domain_min_A mean=0.004211 ± 0.000018.
- `hfso2_cavity_midir_abs__gradient_thickness__01`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.997081 ± 0.000045; target_domain_mean_A mean=0.667028 ± 0.000083; target_domain_min_A mean=0.045062 ± 0.000100.
- `hfso2_sio2_16_layer_selective_opt___66d0a7adc0db`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.994451 ± 0.000080; target_domain_mean_A mean=0.675019 ± 0.000102; target_domain_min_A mean=0.035657 ± 0.000125.
- `hfso2_sio2_16_layer_selective_opt___6318848ae3a3`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.990634 ± 0.000085; target_domain_mean_A mean=0.665986 ± 0.000073; target_domain_min_A mean=0.036771 ± 0.000075.
- `opt_20l_stark_emissivity__differen__9efd2dfafea6`: model=relative_uniform, sigma_nm=0.000, relative_fraction=0.0200, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.999691 ± 0.000109; target_domain_mean_A mean=0.619438 ± 0.000621; target_domain_min_A mean=0.013228 ± 0.000316.
- `optimize_hfosio2_16l_on_si__differ__cb8f09a832f4`: model=relative_uniform, sigma_nm=0.000, relative_fraction=0.1200, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.979533 ± 0.006741; target_domain_mean_A mean=0.662185 ± 0.005110; target_domain_min_A mean=0.016317 ± 0.002860.
- `ir_abs_opt_16l_hfosio2_si__differe__582a48a1c5af`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.978588 ± 0.000242; target_domain_mean_A mean=0.663802 ± 0.000076; target_domain_min_A mean=0.014448 ± 0.000036.
- `emis_control_hfosio2_si__gradient_thickness__01`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.960318 ± 0.000040; target_domain_mean_A mean=0.644414 ± 0.000056; target_domain_min_A mean=0.031357 ± 0.000058.
- `optimize_hfosio2_16l_on_si__differ__661dcb579a06`: model=relative_uniform, sigma_nm=0.000, relative_fraction=0.1200, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.990984 ± 0.006592; target_domain_mean_A mean=0.671772 ± 0.005968; target_domain_min_A mean=0.025952 ± 0.004215.
- `ir_abs_opt_16l_hfosio2_si__differe__d319a2b43339`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.993727 ± 0.000175; target_domain_mean_A mean=0.672097 ± 0.000082; target_domain_min_A mean=0.030616 ± 0.000108.
- `hfso2_si_emissivity_contrast_opt____e4fecc5660be`: model=relative_uniform, sigma_nm=0.000, relative_fraction=0.0020, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.999808 ± 0.000034; target_domain_mean_A mean=0.929002 ± 0.000083; target_domain_min_A mean=0.766696 ± 0.000511.
- `hfso2_sio2_16_layer_selective_opt___115daf0e6d7d`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.974763 ± 0.000186; target_domain_mean_A mean=0.617741 ± 0.000112; target_domain_min_A mean=0.019575 ± 0.000067.
- `emis_control_hfosio2_si__different__3b1b4e7aeace`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.958603 ± 0.000075; target_domain_mean_A mean=0.626830 ± 0.000051; target_domain_min_A mean=0.024680 ± 0.000046.
- `optimise_17layer_cavity_ir__baseline`: model=relative_uniform, sigma_nm=1.000, relative_fraction=0.0100, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.994488 ± 0.000265; target_domain_mean_A mean=0.578603 ± 0.000406; target_domain_min_A mean=0.004041 ± 0.000054.
- `hfo2_sio2_20layer_emissivity_opt__baseline`: model=relative_uniform, sigma_nm=0.000, relative_fraction=0.0020, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.946064 ± 0.000086; target_domain_mean_A mean=0.667625 ± 0.000085; target_domain_min_A mean=0.048934 ± 0.000075.
- `chirped_dbr_opt_24l__gradient_thickness__01`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.961496 ± 0.000068; target_domain_mean_A mean=0.779053 ± 0.000070; target_domain_min_A mean=0.053633 ± 0.000134.
- `optimize_hfosio2_dualband_abs__dif__e7b861853afe`: model=absolute_normal, sigma_nm=20.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.982738 ± 0.001222; target_domain_mean_A mean=0.503884 ± 0.001458; target_domain_min_A mean=0.007122 ± 0.000382.
- `chirped_dbr_opt_24l__differential___da8dab93f344`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.980027 ± 0.000028; target_domain_mean_A mean=0.802350 ± 0.000079; target_domain_min_A mean=0.028233 ± 0.000100.
- `opt_12layer_hfosio2_sisubstrate__d__666f38dee1f6`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.972705 ± 0.000100; target_domain_mean_A mean=0.491824 ± 0.000105; target_domain_min_A mean=0.007303 ± 0.000024.
- `opt_12layer_hfo2_sio2_si__gradient_thickness__01`: model=relative_uniform, sigma_nm=0.000, relative_fraction=0.1200, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.965296 ± 0.003631; target_domain_mean_A mean=0.477605 ± 0.001673; target_domain_min_A mean=0.008051 ± 0.000691.
- `optimize_hfosio2_dualband_abs__baseline`: model=absolute_normal, sigma_nm=20.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.960994 ± 0.004564; target_domain_mean_A mean=0.479706 ± 0.002372; target_domain_min_A mean=0.007195 ± 0.000509.
- `opt_20layer_dielectric_mirror__gra__c9cf83c8454e`: model=relative_uniform, sigma_nm=0.000, relative_fraction=0.0050, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.998941 ± 0.000095; target_domain_mean_A mean=0.610259 ± 0.000180; target_domain_min_A mean=0.043816 ± 0.000388.
- `opt_20layer_dielectric_mirror__gra__2e1145bbbc71`: model=relative_uniform, sigma_nm=0.000, relative_fraction=0.0050, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.998924 ± 0.000099; target_domain_mean_A mean=0.610233 ± 0.000178; target_domain_min_A mean=0.043768 ± 0.000401.
- `hf_sio2_16_on_si_opt__gradient_thickness__04`: model=relative_uniform, sigma_nm=1.000, relative_fraction=0.0800, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.840365 ± 0.002348; target_domain_mean_A mean=0.386412 ± 0.000871; target_domain_min_A mean=0.006183 ± 0.000104.
- `hf_sio2_16_on_si_opt__differential__8f3aee19430b`: model=relative_uniform, sigma_nm=1.000, relative_fraction=0.0800, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_A mean=0.835873 ± 0.003156; target_domain_mean_A mean=0.383245 ± 0.001165; target_domain_min_A mean=0.005842 ± 0.000112.

## Feedback and stopping decision

The loop stopped with `stop_completed`: Route control_route_01 left the race as stopped_budget: run wall-time budget exhausted

## Limitations

- Incidence angle not explicitly specified
- Polarization state not explicitly specified
- Refractive index data sources for IR range not provided
- Incidence angle was not specified in the request; all routes assume normal incidence (0 degrees) as a baseline hypothesis that could be tested in future work
- Polarization state was not specified; all routes use unpolarized (TE+TM average) as the default for thermal emission applications
- The exact refractive index datasets for hfo2 and sio2 in the 3-13 μm range will be resolved by the material catalog at execution time
- Some planned routes produced recorded failures or capability limits; see the iteration artifacts.

## Literature provenance

- [s2-chunk:CorpusId:271432151:s2chunk:271432151:8442:10694:ffaee05868889c7f] Record nighttime electric power generation at a density of 350 mW/m$^2$ via radiative cooling (n.d.; CorpusId:271432151); use=method_guidance, source=s2_snippet_search, depth=s2_snippet.
- [s2-chunk:CorpusId:284897971:s2chunk:284897971:2899:5008:7a5ec08c6adb0915] Efficiency Enhancement of GaAs Thermophotovoltaic Cells System using Integrated TiO2/SiO2 1D Photonic Crystal Distributed Bragg Reflectors (n.d.; CorpusId:284897971); use=method_guidance, source=s2_snippet_search, depth=s2_snippet.
- [s2-chunk:CorpusId:254823355:s2chunk:254823355:34368:36239:3c089577cc582ced] Heterostructure Films of SiO2 and HfO2 for High-Power Laser Optics Prepared by Plasma-Enhanced Atomic Layer Deposition (n.d.; CorpusId:254823355); use=method_guidance, source=s2_snippet_search, depth=s2_snippet.
- [s2-chunk:CorpusId:11834683:s2chunk:11834683:19297:21346:f13e973aab3fb109] A Planarized Thermophotovoltaic Emitter With Idealized Selective Emission (n.d.; CorpusId:11834683); use=method_guidance, source=s2_snippet_search, depth=s2_snippet.
- [s2-chunk:CorpusId:243942968:s2chunk:243942968:5158:7375:a7146be8d8d7d557] Highly suppressed solar absorption in a daytime radiative cooler designed by genetic algorithm (n.d.; CorpusId:243942968); use=method_guidance, source=s2_snippet_search, depth=s2_snippet.
