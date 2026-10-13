import json
import pytest
from optomind_optics.harness.science.contracts import Observation
from optomind_optics.harness.science.observation_adapter import evidence_binding, require_binding
from optomind_optics.harness.route_reflection import reflect_on_route

def test_binding_rejects_each_identity_swap():
    identities=dict(candidate="c",sample="s",objective="o",objective_version="v",grid=[1,2],polarization="s",materials="SiO2",tool_version="1")
    binding=evidence_binding(**identities)
    obs=Observation(object_id="o",source_run_id="r",acquisition_id="a",binding=binding)
    assert require_binding(obs,binding)==obs
    for key in identities:
        changed=evidence_binding(**{**identities,key:"swapped"})
        with pytest.raises(ValueError):require_binding(obs,changed)

def test_actual_reflection_consumer_preserves_discriminative_evidence():
    seen=[]; records=[]
    class Client:
        def call(self,messages,**kwargs):
            seen.append(messages)
            evidence=json.loads(messages[1]["content"])["scientific_evidence"]
            shape=evidence["derived_features"]["violation_intervals"]
            return {"content":json.dumps(dict(observed_vs_expected="shape",deviation_mechanism="localized",continue_recommended=True,continue_rationale="test",insight_for_next=str(shape),insight_grounding="observed"))}
    results=[]
    for shape in ([[400,450]],[[650,700]]):
        obs={"best_target_score":.8,"derived_features":{"violation_intervals":shape,"missing":None},"gradient_summary":{"status":"unavailable"}}
        results.append(reflect_on_route(Client(),pre_declarations={},observation=obs,score_history=[.8],context_sink=records.append))
    assert results[0].insight_for_next != results[1].insight_for_next
    assert records[0]["actual_messages"]==seen[0]
    assert records[0]["context_hash"]!=records[1]["context_hash"]
    assert json.loads(seen[0][1]["content"])["scientific_evidence"]["derived_features"]["missing"] is None

def test_gradient_never_defaults_or_reuses_wrong_candidate(monkeypatch):
    from optomind_optics.harness.research_orchestrator import _compute_gradient_summary
    from optomind_optics.harness.science.observation_adapter import bound_gradient
    from optomind_optics.harness.science.contracts import ResearchTask
    assert _compute_gradient_summary({"experiments":[{"tmm_task":{"stack":{}}}]})["status"]=="unavailable"
    candidate=ResearchTask(object_id="candidate",source_run_id="r",question="bound")
    objective=ResearchTask(object_id="objective",source_run_id="r",question="frozen")
    binding=evidence_binding(candidate={"stack":"adopted"},sample="s",objective="mean_T",objective_version=1,grid=[500],polarization="s",materials="SiO2",tool_version="1")
    with pytest.raises(ValueError):
        bound_gradient({"stack":"wrong"},candidate_ref=candidate.ref(),objective_ref=objective.ref(),objective="mean_T",binding=binding)
