# Introduction

Planar multilayer coatings modify spectral reflection, transmission, and absorption through interference among waves returned by successive interfaces. A design question spanning wavelengths, angles, or polarizations is therefore a multi-condition problem in which nominal response, structural complexity, and sensitivity to parameter errors may rank candidates differently. The present study frames the declared optical task as a bounded comparison under one common objective. This framing permits the calculated trade-offs to be reported without treating unsampled structures or experimental feasibility as established results.

# Methods

Forward calculations used the scattering-matrix (S-matrix) formulation over 1001 wavelength samples and 6 angle-polarization channels; the selected design carried a passing physics-acceptance certificate. The bounded comparison completed 3 structural routes with layer counts 4, 7, 10 and produced 27 physically valid candidates. Candidate responses were recomputed with the same forward solver after search, and only records that passed the solver's physical audit were retained for tabulation. The bounded route formulation and perturbation interpretation follow methodological ideas from published multilayer design and tolerance studies [REF:CorpusId:120897377] [REF:CorpusId:284130796] [REF:CorpusId:283155301]. Those sources guide route formulation and tolerance interpretation; they do not certify the simulated performance of the new candidates.

# Results

The best-performance candidate contained 7 finite layers in the sequence MgF2 (103.49 nm) / TiO2 (111.72 nm) / MgF2 (225.78 nm) / TiO2 (32.70 nm) / MgF2 (38.86 nm) / TiO2 (26.19 nm) / MgF2 (228.24 nm). For the best-performance candidate, mean R was 0.458%-1.699% across the assessed channels (soft target at most 0.800%); worst case R was 0.784%-3.697% across the assessed channels (soft target at most 3.000%). The simplest retained candidate used 4 finite layers (MgF2 (80.00 nm) / SiO2 (29.00 nm) / Ta2O5 (127.00 nm) / MgF2 (31.00 nm)) with target, robustness, and simplicity scores of 0.6675, 0.6612, and 0.9802, respectively. The retained portfolio is reported as a comparison among the candidates that were actually evaluated. The ranking distinguishes nominal target fit, perturbation performance, and structural simplicity; these scores answer different decision questions and should not be collapsed into a claim of universal superiority. The spectral plots and tables contain the calculated response used for that ranking. No trend outside the sampled routes or the declared optical conditions is inferred from this comparison.

## Best-performance layer prescription

| Layer from incident side | Material | Thickness (nm) |
|---:|---|---:|
| 1 | MgF2 | 103.494 |
| 2 | TiO2 | 111.719 |
| 3 | MgF2 | 225.783 |
| 4 | TiO2 | 32.704 |
| 5 | MgF2 | 38.859 |
| 6 | TiO2 | 26.187 |
| 7 | MgF2 | 228.244 |

## Verified objective values

| Channel | Observable | Aggregation | Constraint | Target | Observed |
|---|---|---|---|---:|---:|
| 0 degrees, TE | R | mean | at most | 0.800% | 0.832% |
| 0 degrees, TM | R | mean | at most | 0.800% | 0.832% |
| 30 degrees, TE | R | mean | at most | 0.800% | 0.514% |
| 30 degrees, TM | R | mean | at most | 0.800% | 0.458% |
| 45 degrees, TE | R | mean | at most | 0.800% | 1.699% |
| 45 degrees, TM | R | mean | at most | 0.800% | 0.943% |
| 0 degrees, TE | R | worst case | at most | 3.000% | 1.962% |
| 0 degrees, TM | R | worst case | at most | 3.000% | 1.962% |
| 30 degrees, TE | R | worst case | at most | 3.000% | 1.189% |
| 30 degrees, TM | R | worst case | at most | 3.000% | 0.784% |
| 45 degrees, TE | R | worst case | at most | 3.000% | 3.029% |
| 45 degrees, TM | R | worst case | at most | 3.000% | 3.697% |

## Candidate portfolio

| Portfolio role | Layers | Target | Robustness | Simplicity | Clauses |
|---|---:|---:|---:|---:|---:|
| Performance + Robustness | 7 | 0.7860 | 0.7455 | 0.8696 | 6/12 |
| Simplicity | 4 | 0.6675 | 0.6612 | 0.9802 | 3/12 |

# Robustness under Manufacturing Uncertainty

Manufacturing sensitivity was evaluated with 16 perturbations using absolute normal, a thickness scale of 2.000 nm, and a common incidence-angle bound of 1.000 degrees. The retained robust candidate had a robustness score of 0.7455; target domain max R had mean 4.876% and standard deviation 1.199%; target domain mean R had mean 1.017% and standard deviation 0.201%; target domain min R had mean 0.015% and standard deviation 0.030%. The perturbation ensemble quantifies sensitivity only under the declared numerical error model. Its distribution can be used to compare the sampled candidates under that model, but it does not estimate fabrication yield, process drift, or long-term reliability. Accordingly, robustness is interpreted as a computational ranking dimension rather than evidence of manufacturability. The nominal spectrum and the perturbed score distribution must therefore be read together, because each describes a different part of the bounded numerical evaluation.

# Discussion

The best-performance design satisfied 6 of 12 soft target clauses. The result is therefore reported as a verified best-effort trade-off rather than exact fulfillment of every requested goal. The result supports a bounded design decision: the performance-oriented candidate and the simplicity-oriented candidate occupy different positions under the same scoring contract. Their comparison identifies the consequences observed within this search without asserting that layer count or a particular material sequence causes a general performance trend. Because some soft clauses remain unmet, the portfolio is most useful as a reproducible starting point for subsequent route expansion or fabrication-aware refinement. This interpretation keeps unresolved target conflicts visible while retaining candidates that may be valuable under different priorities.

# Limitations

The calculations do not establish deposition stress, adhesion, surface roughness, environmental durability, or process-dependent optical constants; these quantities require fabrication-specific characterization before experimental use. The conclusions are restricted to the declared material datasets, optical conditions, structural routes, search budget, and numerical perturbations. The calculations cannot determine process compatibility or experimental repeatability. Configurations outside the sampled routes may yield different trade-offs, so the ranking is local to the documented computational study.

# Conclusion

Within the declared routes and search budget, the study identifies a physics-checked candidate portfolio and preserves the difference between nominal performance, numerical robustness, and simplicity. The outcome is a verified best-effort design rather than a declaration that every requested target was achieved. Any transition from this computational result to fabrication requires process-specific material characterization and an independent experimental plan.
