# E10 result and failure diagnosis

Actual model-generated production winner: arithmetic mean R=0.035362408656420617;
original target<0.03 remains unmet. Its valid candidate is retained.
An independent bounded diagnostic search reached R=0.01800652857383819
in 399 total extra audit evaluations. This reference was never
provided to the model and is not a replacement production result or a global
certificate. No repair/ablation benefit is inferred.

Strengthen route refinement and make optimizer-surrogate choice explicit
relative to the frozen objective before any larger efficiency claim. Current
`tmm_engine/optimization.py` explicitly labels its squared-spectrum surrogate;
this is a pre-existing optimization limitation, not evidence of a new R2
ranking direction failure. Both restored rounds compiled the same task.

The 16 synthetic1nm perturbations use seed17. Their mean reflectance is
0.034974339897041795; population standard deviation 0.0021149456692497598;
0/16 meet the original target. This is not manufacturing
yield or experimental validation. Complete spectra/calls are retained locally.
