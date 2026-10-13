# E10 independent failure diagnosis

The production result is already known: mean R≈0.03536, original target<0.03.
This is a post-result diagnosis, not preregistration of the agent experiment.
Never inject reference solutions into model prompts or replace production
outputs. Preserve its two layers, indices1.46/2.4 (one each, both orders),
air1/glass1.52, thickness[40,160]nm, normal s,450..700/5nm/51 points.

Compare an independent direct arithmetic-mean search against existing
production candidates and the declared squared-spectrum optimizer surrogate.
Fixed starts (nm):[80,80],[40,40],[160,160],[40,160],[160,40]. Both layer
orders; L-BFGS-B finite differences, at most64 independent forward evaluations
per start, retain best candidate when this separate diagnostic cap is reached.
No optimality/infeasibility certificate. Reference solver work is additional
offline audit effort, never counted as production's budget or an agent win.

Reproduce the production winner's declared absolute-normal1nm perturbations:
16 draws, seed17, no angle/index perturbation. Compare distributions to stored
statistics when the production sampling protocol is identical; otherwise
report protocol differences rather than loosen tolerances. Do not interpret
synthetic parameter perturbations as manufacturing yield. Do not modify any
scientific target, seed, production code, stop rule or test expectation. Cost0.
