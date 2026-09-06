# TMM research and design result

## Problem interpretation

Design a planar multilayer dielectric film using ZnS and ZnSe on a Ge substrate with unspecified material incidence. The film should have high transmittance in the 8000-11000 nm long-wave infrared band and suppress transmittance in the 3000-5000 nm mid-wave infrared band to reduce thermal load. The total layer count must not exceed 16 layers.

## Method research

- **dichroic double-reflection stack design**: Use structure (0.5L H 0.5L)^b where L is low-index layer with optical thickness of one-eighth center wavelength, H is high-index layer with optical thickness of one-fourth center wavelength, and b is number of periods. This basic long-wave pass edge filter structure enables broadband reflection/transmission control in multilayer dichroic coatings.
- **quarter-wavelength AR layer with intermediate refractive index**: Use antireflection layers of materials with intermediate refractive index to minimize reflection and compensate for refractive index difference between film, substrate, and air. Optical thickness set to quarter-wavelength for maximum antireflection at target wavelength.
- **mechanical-optical co-optimization for stress matching**: Choose tensile stress material to match compressive stress of high-stress film materials during multilayer preparation. Validate spectrum and stress as simultaneous performance optimizers in film design. Consider deposition parameters (assisted vs unassisted) to control absorption and stress degeneration.
- **destructive interference for metal reflection reduction**: Multilayer high refractive index dielectric layers boost transmittance by reducing metal film reflection through destructive interference. Structure combines metal film with high-index dielectric layers above and below to enhance overall transmittance while maintaining electrical conductivity.

## Routes executed

- **Periodic 8-layer quarter-wave stack for MWIR stopband** — status `failed`, source `literature_planned`, verified candidates 0, best soft score n/a.
- **Aperiodic 16-layer fully-optimized stack** — status `completed`, source `literature_planned`, verified candidates 9, best soft score 0.3133.
- **Periodic 12-layer stack for layer-count scaling study** — status `failed`, source `literature_planned`, verified candidates 0, best soft score n/a.
- **Periodic ZnS/ZnSe Quarter-Wave Stack for MWIR Suppression with LWIR Pass** — status `failed`, source `llm_memory_control`, verified candidates 0, best soft score n/a.
- **6-layer periodic quarter-wave stack with expanded thickness bounds** — status `completed`, source `literature_planned`, verified candidates 9, best soft score 0.1774.
- **Local exploitation around best 16-layer candidate with bounded perturbations** — status `completed`, source `literature_planned`, verified candidates 9, best soft score 0.3367.
- **Diagnostic 8-layer ZnS/ZnSe periodic stack with relaxed bounds** — status `completed`, source `literature_planned`, verified candidates 9, best soft score 0.2566.
- **16-layer ZnSe/ZnS alternating stack optimized for LWIR passband with MWIR suppression** — status `completed`, source `llm_memory_control`, verified candidates 9, best soft score 0.3634.
- **Quarter-wave initialized 16-layer stack at LWIR center wavelength** — status `completed`, source `literature_planned`, verified candidates 9, best soft score 0.3379.
- **8-layer quarter-wave stack with MWIR-centered initialization and bounded thickness** — status `completed`, source `literature_planned`, verified candidates 9, best soft score 0.2009.
- **12-layer ZnS/ZnSe stack with frozen outer/inner quarter-wave segments** — status `completed`, source `literature_planned`, verified candidates 9, best soft score 0.2961.
- **16-layer ZnSe/ZnS alternating stack on Ge for LWIR pass / MWIR block** — status `failed`, source `llm_memory_control`, verified candidates 0, best soft score n/a.
- **Central-layer gradient optimization with 7% jitter** — status `completed`, source `literature_planned`, verified candidates 9, best soft score 0.1679.
- **Layer-5 resonance localization around 694nm optimum** — status `completed`, source `literature_planned`, verified candidates 9, best soft score 0.1983.
- **Local refinement with ≤4nm perturbation bounds around round-3 optimum** — status `completed`, source `literature_planned`, verified candidates 9, best soft score 0.3391.
- **Control Axis: 16-Layer ZnSe/ZnS Dual-Band Filter on Ge** — status `completed`, source `llm_memory_control`, verified candidates 9, best soft score 0.2011.
- **16-layer ZnSe/ZnS alternating stack on Ge with stratified thickness initialization and L2 smoothing** — status `completed`, source `llm_memory_control`, verified candidates 9, best soft score 0.3158.
- **12-layer ZnS/ZnSe stack with 850nm thickness cap and peak re-initialization** — status `failed`, source `literature_planned`, verified candidates 0, best soft score n/a.
- **Local thickness refinement of 8-layer ZnSe/ZnS stack with ±1.5 nm bounded optimizer** — status `completed`, source `literature_planned`, verified candidates 9, best soft score 0.3272.
- **Constrained boundary sweep with locked central cavity (8 fixed + 8 variable)** — status `completed`, source `literature_planned`, verified candidates 9, best soft score 0.3387.
- **Alternating ZnSe/ZnS 16-layer stack on Ge with expanded thickness bounds for dual-band IR filtering** — status `completed`, source `llm_memory_control`, verified candidates 9, best soft score 0.3516.
- **10-layer ZnS/ZnSe stack with expanded thickness bounds for LWIR quarter-wave compatibility** — status `completed`, source `literature_planned`, verified candidates 9, best soft score 0.3070.
- **Derivative-based local refinement of 8-layer stack from R4 seed** — status `completed`, source `literature_planned`, verified candidates 9, best soft score 0.1985.
- **Decoupled boundary-layer optimization with frozen central cavity** — status `completed`, source `literature_planned`, verified candidates 9, best soft score 0.3387.
- **Selective cavity-layer tuning in 16-layer ZnSe/ZnS dual-band IR filter** — status `completed`, source `llm_memory_control`, verified candidates 9, best soft score 0.3512.
- **14-layer ZnS/ZnSe stack with LWIR quarter-wave initialization** — status `completed`, source `literature_planned`, verified candidates 9, best soft score 0.3033.
- **Tight-bounded local refinement from iteration-19 optimum with variance regularization** — status `completed`, source `literature_planned`, verified candidates 9, best soft score 0.3273.
- **12-layer ZnS/ZnSe with frozen outer layers and reduced step size** — status `completed`, source `literature_planned`, verified candidates 9, best soft score 0.1699.
- **12-layer ZnS/ZnSe stack with geometric progression initialization and constrained step size** — status `failed`, source `literature_planned`, verified candidates 0, best soft score n/a.

## Planning-source comparison

The memory-only control and literature-planned routes are separated by source. Frozen-standard scores are used for a cross-source verdict only when both arms have scoreable representatives; score deltas inside a route are diagnostics.
- Frozen-standard comparison: `literature_higher`.
- Control minus best literature frozen score: `-0.134385`.
- `literature_planned`: 3 route(s), 3 with verified candidates, 22 executed round(s).
- `llm_memory_control`: 1 route(s), 1 with verified candidates, 7 executed round(s).

## Recommended candidate portfolio

Every route was measured on the same quantities, chosen from the request before any route ran, and ranked by the one fixed expression `mean_transmittance_8000_11000nm - mean_transmittance_3000_5000nm`, so these rows are directly comparable.
- **Best performance**: `optimize_znse_zns_germanium_ir_filter__baseline` from 8-layer quarter-wave stack with MWIR-centered initialization and bounded thickness.
- **Most robust**: `lwir_swrf_thickness_opt__gradient_thickness__01` from Aperiodic 16-layer fully-optimized stack.
- **Simplest verified**: `znss_znse_optimization__baseline` from 6-layer periodic quarter-wave stack with expanded thickness bounds.

- `optimize_znse_zns_germanium_ir_filter__baseline` stack (8 layers): znse / zns / znse / zns / znse / zns / znse / zns.
- `znss_znse_optimization__baseline` stack (6 layers): zns / znse / zns / znse / zns / znse.
- `mwir_znszse_12layer_opt__gradient_thickness__01` stack (12 layers): znse / zns / znse / zns / znse / zns / znse / zns / znse / zns / znse / zns.
- `opt_10layer_znsez_se_ge_ir_filter__baseline` stack (10 layers): zns / znse / zns / znse / zns / znse / zns / znse / zns / znse.
- `opt_znse_zns_on_ge_ir_pass__differ__a3ee5d965a9c` stack (16 layers): znse / zns / znse / zns / znse / zns / znse / zns / znse / zns / znse / zns / znse / zns / znse / zns.
- `mwir_znszse_12layer_opt__different__7f451bc47d63` stack (12 layers): znse / zns / znse / zns / znse / zns / znse / zns / znse / zns / znse / zns.
- `optimize_ge_znse_zns_16layer__baseline` stack (16 layers): zns / znse / zns / znse / zns / znse / zns / znse / zns / znse / zns / znse / zns / znse / zns / znse.
- `znse_zns_16l_ir_optimization__grad__44272faeb678` stack (16 layers): znse / zns / znse / zns / znse / zns / znse / zns / znse / zns / znse / zns / znse / zns / znse / zns.
- `lwir_swrf_thickness_opt__baseline` stack (16 layers): zns / znse / zns / znse / zns / znse / zns / znse / zns / znse / zns / znse / zns / znse / zns / znse.
- `znzs_znze_ge_opt_ir__baseline` stack (12 layers): ZnS / ZnSe / ZnS / ZnSe / ZnS / ZnSe / ZnS / ZnSe / ZnS / ZnSe / ZnS / ZnSe.
- `lwir_swrf_thickness_opt__different__2ef898c2f7f8` stack (16 layers): zns / znse / zns / znse / zns / znse / zns / znse / zns / znse / zns / znse / zns / znse / zns / znse.
- `opt_zns_znse_ge_mir__differential___68f033378384` stack (12 layers): zns / znse / zns / zns / znse / zns / znse / zns / znse / zns / znse / zns.

| Candidate | Route | Frozen standard score | Comparable spectral summary | Robustness | Thicknesses (nm) |
|---|---|---:|---|---:|---|
| `optimize_znse_zns_germanium_ir_filter__baseline` | 8-layer quarter-wave stack with MWIR-centered initialization and bounded thickness | 0.1884 | mean_transmittance_8000_11000nm=0.8953; mean_transmittance_3000_5000nm=0.7069 | 0.0595 | 340.00, 410.00, 340.00, 410.00, 340.00, 410.00, 340.00, 410.00 |
| `znss_znse_optimization__baseline` | 6-layer periodic quarter-wave stack with expanded thickness bounds | 0.1152 | mean_transmittance_8000_11000nm=0.9022; mean_transmittance_3000_5000nm=0.7869 | 0.0826 | 1000.00, 1000.00, 1000.00, 1000.00, 1000.00, 1000.00 |
| `mwir_znszse_12layer_opt__gradient_thickness__01` | 12-layer ZnS/ZnSe stack with frozen outer/inner quarter-wave segments | 0.0582 | mean_transmittance_8000_11000nm=0.7330; mean_transmittance_3000_5000nm=0.6748 | 0.0965 | 417.00, 455.00, 500.00, 696.00, 1000.00, 1000.00, 553.00, 510.00, 593.00, 501.00, 417.00, 455.00 |
| `opt_10layer_znsez_se_ge_ir_filter__baseline` | 10-layer ZnS/ZnSe stack with expanded thickness bounds for LWIR quarter-wave compatibility | 0.0573 | mean_transmittance_8000_11000nm=0.8574; mean_transmittance_3000_5000nm=0.8001 | 0.0570 | 1000.00, 1000.00, 1000.00, 1000.00, 1000.00, 1000.00, 1000.00, 1000.00, 1000.00, 1000.00 |
| `opt_znse_zns_on_ge_ir_pass__differ__a3ee5d965a9c` | 16-layer ZnSe/ZnS alternating stack on Ge with stratified thickness initialization and L2 smoothing | 0.0498 | mean_transmittance_8000_11000nm=0.7454; mean_transmittance_3000_5000nm=0.6956 | 0.0650 | 366.58, 403.07, 564.10, 489.69, 495.01, 283.22, 516.94, 378.82, 531.30, 545.21, 387.21, 367.96, 443.34, 436.43, 212.40, 554.08 |
| `mwir_znszse_12layer_opt__different__7f451bc47d63` | 12-layer ZnS/ZnSe stack with frozen outer/inner quarter-wave segments | 0.0332 | mean_transmittance_8000_11000nm=0.7350; mean_transmittance_3000_5000nm=0.7017 | 0.0943 | 417.00, 455.00, 528.80, 795.75, 862.16, 950.47, 833.15, 978.39, 980.49, 820.79, 417.00, 455.00 |
| `optimize_ge_znse_zns_16layer__baseline` | Control Axis: 16-Layer ZnSe/ZnS Dual-Band Filter on Ge | -0.0119 | mean_transmittance_8000_11000nm=0.7749; mean_transmittance_3000_5000nm=0.7868 | n/a | 760.00, 760.00, 760.00, 760.00, 760.00, 760.00, 760.00, 760.00, 760.00, 760.00, 760.00, 760.00, 760.00, 760.00, 760.00, 760.00 |
| `znse_zns_16l_ir_optimization__grad__44272faeb678` | Local exploitation around best 16-layer candidate with bounded perturbations | -0.0120 | mean_transmittance_8000_11000nm=0.6574; mean_transmittance_3000_5000nm=0.6693 | 0.1269 | 467.00, 259.00, 401.00, 906.00, 999.00, 1174.00, 613.00, 449.00, 386.00, 569.00, 345.00, 611.00, 734.00, 498.00, 263.00, 513.00 |
| `lwir_swrf_thickness_opt__baseline` | Aperiodic 16-layer fully-optimized stack | -0.0149 | mean_transmittance_8000_11000nm=0.7728; mean_transmittance_3000_5000nm=0.7878 | 0.2245 | 750.00, 750.00, 750.00, 750.00, 750.00, 750.00, 750.00, 750.00, 750.00, 750.00, 750.00, 750.00, 750.00, 750.00, 750.00, 750.00 |
| `znzs_znze_ge_opt_ir__baseline` | 12-layer ZnS/ZnSe with frozen outer layers and reduced step size | -0.0209 | mean_transmittance_8000_11000nm=0.8204; mean_transmittance_3000_5000nm=0.8413 | 0.0568 | 417.00, 455.00, 750.00, 750.00, 750.00, 750.00, 750.00, 750.00, 750.00, 750.00, 417.00, 455.00 |
| `lwir_swrf_thickness_opt__different__2ef898c2f7f8` | Aperiodic 16-layer fully-optimized stack | -0.0414 | mean_transmittance_8000_11000nm=0.7196; mean_transmittance_3000_5000nm=0.7610 | 0.2307 | 1885.71, 2255.49, 2195.14, 1745.31, 1583.67, 1294.85, 758.87, 2109.51, 2293.74, 504.49, 2435.91, 1238.71, 2478.21, 2182.86, 1423.93, 2039.17 |
| `opt_zns_znse_ge_mir__differential___68f033378384` | Central-layer gradient optimization with 7% jitter | -0.0415 | mean_transmittance_8000_11000nm=0.7082; mean_transmittance_3000_5000nm=0.7497 | 0.0643 | 417.00, 455.00, 500.00, 687.46, 1052.65, 1068.96, 584.40, 527.00, 561.42, 501.00, 417.00, 455.00 |

## Manufacturing uncertainty

- `mwir_znszse_12layer_opt__gradient_thickness__01`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_T mean=0.972251 ± 0.000536; target_domain_mean_T mean=0.709140 ± 0.000082; target_domain_min_T mean=0.420240 ± 0.000491.
- `znse_zns_16l_ir_optimization__grad__44272faeb678`: model=relative_uniform, sigma_nm=1.000, relative_fraction=0.0100, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_T mean=0.977888 ± 0.000436; target_domain_mean_T mean=0.662243 ± 0.000121; target_domain_min_T mean=0.439990 ± 0.003082.
- `opt_zns_znse_ge_mir__gradient_thickness__01`: model=relative_uniform, sigma_nm=1.000, relative_fraction=0.0700, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_T mean=0.991949 ± 0.006319; target_domain_mean_T mean=0.725102 ± 0.000987; target_domain_min_T mean=0.533745 ± 0.009779.
- `opt_8l_znszse_ge_tmm__differential__5c0107ba5ab7`: model=relative_uniform, sigma_nm=0.000, relative_fraction=0.0100, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_T mean=0.974107 ± 0.000477; target_domain_mean_T mean=0.756220 ± 0.001132; target_domain_min_T mean=0.556710 ± 0.001492.
- `optimize_znse_zns_germanium_ir_fil__8074c6473d8e`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_T mean=0.982630 ± 0.000137; target_domain_mean_T mean=0.682420 ± 0.000164; target_domain_min_T mean=0.503718 ± 0.000549.
- `opt_8l_znszse_ge_tmm__gradient_thickness__01`: model=relative_uniform, sigma_nm=0.000, relative_fraction=0.0100, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_T mean=0.981566 ± 0.000658; target_domain_mean_T mean=0.737563 ± 0.000341; target_domain_min_T mean=0.570466 ± 0.001957.
- `znss_znse_optimization__gradient_thickness__01`: model=absolute_uniform, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_T mean=0.965986 ± 0.001243; target_domain_mean_T mean=0.730361 ± 0.000017; target_domain_min_T mean=0.560136 ± 0.000059.
- `opt_znse_zns_on_ge_ir_pass__gradie__bd7fb55d9fd9`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_T mean=0.971187 ± 0.000110; target_domain_mean_T mean=0.677927 ± 0.000051; target_domain_min_T mean=0.562201 ± 0.000284.
- `znss_znse_optimization__gradient_thickness__04`: model=absolute_uniform, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_T mean=0.976105 ± 0.000051; target_domain_mean_T mean=0.734037 ± 0.000026; target_domain_min_T mean=0.556570 ± 0.000065.
- `optimize_ge_znse_zns_16layer__diff__951a1e9bc73c`: model=absolute_normal, sigma_nm=10.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_T mean=0.979385 ± 0.007663; target_domain_mean_T mean=0.660582 ± 0.000522; target_domain_min_T mean=0.429896 ± 0.003033.
- `optimize_znse_zns_germanium_ir_fil__e0f9cd472faa`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_T mean=0.952534 ± 0.000097; target_domain_mean_T mean=0.659122 ± 0.000034; target_domain_min_T mean=0.585834 ± 0.000180.
- `opt_8layer_zns_znse_ge_midir__grad__0ca12d10b4eb`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_T mean=0.966529 ± 0.000095; target_domain_mean_T mean=0.663637 ± 0.000059; target_domain_min_T mean=0.586602 ± 0.000219.
- `opt_8layer_zns_znse_ge_midir__grad__03c622a69e08`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_T mean=0.966480 ± 0.000094; target_domain_mean_T mean=0.663605 ± 0.000059; target_domain_min_T mean=0.586703 ± 0.000216.
- `opt_8layer_znse_zns_on_ge__gradien__2ff736baa113`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_T mean=0.966302 ± 0.000084; target_domain_mean_T mean=0.663385 ± 0.000039; target_domain_min_T mean=0.586706 ± 0.000169.
- `opt_8layer_znse_zns_on_ge__gradien__1ee4bc0ff3b4`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_T mean=0.966302 ± 0.000084; target_domain_mean_T mean=0.663384 ± 0.000039; target_domain_min_T mean=0.586705 ± 0.000168.
- `znse_zns_8layer_ge_optimize__gradi__35b6e2009cc9`: model=absolute_normal, sigma_nm=0.500, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_T mean=0.966434 ± 0.000030; target_domain_mean_T mean=0.663412 ± 0.000022; target_domain_min_T mean=0.586694 ± 0.000083.
- `znse_zns_8layer_ge_optimize__gradi__d7cab6445667`: model=absolute_normal, sigma_nm=0.500, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_T mean=0.966429 ± 0.000032; target_domain_mean_T mean=0.663398 ± 0.000017; target_domain_min_T mean=0.586748 ± 0.000080.
- `opt_8layer_znse_zns_ge_dualband__g__8db4a9595994`: model=relative_uniform, sigma_nm=0.000, relative_fraction=0.0150, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_T mean=0.965909 ± 0.000145; target_domain_mean_T mean=0.663041 ± 0.000099; target_domain_min_T mean=0.586882 ± 0.000356.
- `znse_zns_ir_filter_opt__gradient_thickness__01`: model=absolute_normal, sigma_nm=4.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_T mean=0.974021 ± 0.003914; target_domain_mean_T mean=0.652569 ± 0.000078; target_domain_min_T mean=0.453374 ± 0.001060.
- `znse_zns_ir_filter_opt__gradient_thickness__02`: model=absolute_normal, sigma_nm=4.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_T mean=0.974267 ± 0.003945; target_domain_mean_T mean=0.652569 ± 0.000079; target_domain_min_T mean=0.453380 ± 0.001049.
- `znse_zns_dbr_opt__gradient_thickness__01`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_T mean=0.984702 ± 0.000133; target_domain_mean_T mean=0.687156 ± 0.000035; target_domain_min_T mean=0.424663 ± 0.000242.
- `opt_16layer_znse_zns_ge__gradient_thickness__01`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_T mean=0.974497 ± 0.000150; target_domain_mean_T mean=0.653844 ± 0.000024; target_domain_min_T mean=0.453409 ± 0.000466.
- `lwir_swrf_thickness_opt__gradient_thickness__01`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_T mean=0.998414 ± 0.000083; target_domain_mean_T mean=0.676816 ± 0.000050; target_domain_min_T mean=0.481975 ± 0.000229.
- `opt_10layer_znsez_se_ge_ir_filter___64572286772d`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_T mean=0.975077 ± 0.000109; target_domain_mean_T mean=0.682826 ± 0.000061; target_domain_min_T mean=0.477828 ± 0.000217.
- `opt_znse_zns_ge_midir__gradient_thickness__01`: model=absolute_normal, sigma_nm=40.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_T mean=0.980644 ± 0.007743; target_domain_mean_T mean=0.654558 ± 0.001781; target_domain_min_T mean=0.447328 ± 0.009481.
- `znse_zns_ge_midir_opt__gradient_thickness__05`: model=absolute_normal, sigma_nm=40.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_T mean=0.990151 ± 0.006223; target_domain_mean_T mean=0.637155 ± 0.003789; target_domain_min_T mean=0.285570 ± 0.017307.
- `optimize_16l_znse_zns_on_ge__gradi__765c82311956`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_T mean=0.995614 ± 0.000453; target_domain_mean_T mean=0.632417 ± 0.000059; target_domain_min_T mean=0.302436 ± 0.000615.
- `opt_16layer_znse_zns_on_ge__gradie__ba81ff4e29bd`: model=absolute_normal, sigma_nm=1.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_T mean=0.990441 ± 0.001478; target_domain_mean_T mean=0.619565 ± 0.000024; target_domain_min_T mean=0.269992 ± 0.000250.
- `znzs_znze_ge_opt_ir__differential___f378c03a1d9a`: model=absolute_normal, sigma_nm=5.000, relative_fraction=0.0000, common_angle_bound_deg=0.000, samples=16, failed=0; target_domain_max_T mean=0.978440 ± 0.001699; target_domain_mean_T mean=0.704839 ± 0.000525; target_domain_min_T mean=0.432567 ± 0.001596.

## Feedback and stopping decision

The loop stopped with `stop_completed`: Route route_03 left the race as stopped_llm_advice: LLM explicitly assessed no further benefit after 9 executed rounds (stop_basis=physically_infeasible)

## Limitations

- Incidence angle not explicitly specified
- Polarization state not explicitly specified
- ZnSe is not in the guaranteed material catalog; local resolution will determine if it maps to exactly one dataset. If resolution fails, routes must be repaired with an alternative name from the rejection list
- Incidence angle not specified by user; routes assume normal incidence (0 degrees) as default for window applications
- Polarization state not specified; routes assume unpolarized or average of TE/TM as typical for detector windows
- Some planned routes produced recorded failures or capability limits; see the iteration artifacts.

## Literature provenance

- [s2-chunk:CorpusId:253920409:s2chunk:253920409:14121:16260:85ad0582554fb615] Research on Optical and Mechanical Compatible Design Technology of Multilayer Films (n.d.; CorpusId:253920409); use=method_guidance, source=s2_snippet_search, depth=s2_snippet.
- [s2-chunk:CorpusId:268014808:s2chunk:268014808:15112:17070:581c60d401e53151] NIR to LWIR Dichroic Beamsplitter Designed and Manufactured for Space Optical Remote Sensor (n.d.; CorpusId:268014808); use=method_guidance, source=s2_snippet_search, depth=s2_snippet.
- [s2-chunk:CorpusId:214638277:s2chunk:214638277:6515:7982:4191339905722ba3] Effect of Thickness on Structural, Morphological, and Optical Properties of Copper (Cu) Doped Zinc Selenide (ZnSe) Thin Films by Vacuum Evaporation Method (n.d.; CorpusId:214638277); use=method_guidance, source=s2_snippet_search, depth=s2_snippet.
- [s2-chunk:CorpusId:258407986:s2chunk:258407986:3902:5725:e5a3c3c3db7fd735] Temperature resistant anti-reflective coating on Si-wafer for long-wave infra-red imaging (n.d.; CorpusId:258407986); use=method_guidance, source=s2_snippet_search, depth=s2_snippet.
