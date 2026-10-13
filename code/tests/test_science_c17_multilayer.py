import json
import numpy as np
from optomind_eval.multilayer_tasks import multilayer_model,byrnes_prediction,run_multilayer_evaluation

def test_independent_three_layer_forward_agreement():
    regime={'high_n':2.4,'thickness_scale':1.08};design=[80.,60.,110.];wl=np.linspace(450,750,41)
    model=multilayer_model()
    actual=model.predict(dict(n=regime['high_n'],**{f'd{i}':d*regime['thickness_scale'] for i,d in enumerate(design)}),wl)
    assert np.max(abs(actual-byrnes_prediction(regime,design,wl)))<1e-12


def test_actual_coating_decisions_forward_and_sealed(tmp_path):
    report=run_multilayer_evaluation(tmp_path,tasks_per_case=2,seed_base=951000)
    assert sum(report['failures'].values())==0
    assert len(json.loads((tmp_path/'results.json').read_text())['rows'])==36
    designs=report['decision_relevance']['per_regime_optimal_designs_nm']
    assert len({tuple(d) for d in designs})>1
    from optomind_eval.audit_artifacts import verify_multilayer_artifacts
    audit=verify_multilayer_artifacts(tmp_path)
    assert audit['task_arms']==36
    assert audit['max_all_192_design_regime_loss_error']<1e-12
