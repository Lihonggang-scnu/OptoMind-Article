import copy
import numpy as np
from optomind_eval import manufacturing_tasks as mt
from optomind_eval.manufacturing_sensitivity import independent_all_design_metrics


def test_all_design_oracle_matches_actual_policy_future_distribution():
    cfg=copy.deepcopy(mt.FROZEN_PLAN)
    cfg['design_axes_nm']=[[80.,110.],[60.],[50.]]
    cfg['residual_support_nm']=[-2.,2.];cfg['residual_probabilities']=[.5,.5]
    cfg['scoring_grid_nm']['count']=5
    numerical=mt.build_numerical_model(cfg)
    independent=independent_all_design_metrics(cfg,numerical)
    assert independent.shape==(27,2,8)
    assert np.allclose(independent,numerical['future_metrics'],atol=1e-12,rtol=0)
    for threshold in cfg['thresholds']:
        losses=(independent>threshold)@numerical['future_probabilities']
        assert np.all(losses.min(axis=1)<=losses[:,0])
