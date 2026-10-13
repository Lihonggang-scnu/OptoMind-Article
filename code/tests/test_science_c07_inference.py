from dataclasses import dataclass
import numpy as np
import pytest
from optomind_optics.harness.science.model_ir import ParameterSpec
from optomind_optics.harness.science.inference import fit_model, gaussian_observation_kernel
from optomind_optics.harness.science.likelihood_blocks import LikelihoodBlock, JointLikelihood


class LinearModel:
    model_id="linear"
    parameter_specs=(ParameterSpec("slope", -10, 10, 0), ParameterSpec("intercept", -10, 10, 0))
    def predict(self, p, wavelengths_nm, **kwargs):
        return p["slope"]*np.asarray(wavelengths_nm) + p["intercept"]


def block(values, obs="one", **kwargs):
    n=len(values)
    return LikelihoodBlock(obs,tuple(np.linspace(1,2,n)),tuple(values),tuple(map(tuple,np.eye(n)*.01)), gaussian_validated=True, **kwargs)


def test_linear_fit_laplace_covariance_matches_exact():
    x=np.linspace(1,2,30); model=LinearModel(); fit=fit_model(model,[block(2*x+1)],starts=4,bounded_uniform_prior={p.name:(p.lower,p.upper) for p in model.parameter_specs})
    assert fit.success and fit.representation == "conditional_bayes"
    assert fit.parameters == pytest.approx({"slope":2,"intercept":1},abs=1e-8)
    X=np.column_stack([x,np.ones(len(x))])
    assert np.allclose(fit.covariance, np.linalg.inv(X.T@X/.01),rtol=1e-5)
    worlds,weights=fit.finite_worlds(); assert len(worlds)==256 and sum(weights)==pytest.approx(1)
    sample,logp=gaussian_observation_kernel(model,worlds,x,np.eye(len(x))*.01)
    assert np.isfinite(logp(sample(0,np.random.default_rng(2)))).all()


def test_shared_calibration_and_derived_view_do_not_add_samples():
    raw=block([1,2],calibration_group="ref",calibration_std=.3)
    derived=block([1,2],obs="feature",derived_from=("one",))
    repeat=block([1,2],obs="two",calibration_group="ref",calibration_std=.3)
    j=JointLikelihood([raw,derived,repeat]); assert len(j.values)==4
    assert j.covariance[0,3]==pytest.approx(.09)
    with pytest.raises(ValueError): JointLikelihood([raw,raw])


def test_multimodal_and_nonidentifiable_remain_scenarios():
    class Square:
        model_id="square"; parameter_specs=(ParameterSpec("a",-3,3,.3),)
        def predict(self,p,x,**kwargs): return np.full(len(x),p["a"]**2)
    fit=fit_model(Square(),[block([1]*10)],starts=16)
    assert fit.representation=="scenario_set" and fit.weights is None
    assert fit.diagnostics["basin_count"]==2
    assert "multiple_basins" in fit.diagnostics["escalation_reasons"]
    class Confounded(LinearModel):
        def predict(self,p,x,**kwargs): return np.full(len(x),p["slope"]+p["intercept"])
    f=fit_model(Confounded(),[block([2]*10)],starts=4)
    assert f.identifiability.rank==1 and f.representation=="scenario_set"


def test_boundary_and_nonconvergence_are_not_posterior_claims():
    fit=fit_model(LinearModel(),[block(np.linspace(100,200,30))],starts=2)
    assert fit.representation=="scenario_set" and "boundary_solution" in fit.diagnostics["escalation_reasons"]
    fit=fit_model(LinearModel(),[block(np.linspace(1,5,30))],starts=1,max_nfev=1)
    assert not fit.success and "optimizer_not_converged" in fit.diagnostics["escalation_reasons"]


def test_paid_world_to_canonical_observation_to_compiled_optical_fit():
    from dataclasses import asdict, replace
    from optomind_eval.worlds import make_world
    from optomind_eval.service import MeasurementService
    from optomind_optics.harness.science.world_client import WorldClient, receipt_to_observation
    from optomind_optics.harness.science.model_ir import IRNode, ModelIR
    from optomind_optics.harness.science.model_compiler import compile_candidate
    from optomind_optics.harness.science.contracts import ModelCandidate
    from optomind_optics.harness.science.likelihood_blocks import blocks_from_observations
    from optomind_optics.harness.science.inference import fit_result_to_state
    world=replace(make_world("F0",task_id="fit",split="V",seed=22),film_n=2.,thickness_nm=120.,independent_noise_std=.0001,shared_calibration_std=.0001)
    client=WorldClient(MeasurementService([world],budget=1).handle)
    receipt=client.purchase("fit","purchase","R",np.linspace(420,880,51))
    observation=receipt_to_observation(receipt,object_id="observed",source_run_id="run")
    ir=ModelIR((IRNode("n","constant_nk",attributes=(("n",2.),)), IRNode("sub","constant_nk",attributes=(("n",1.5),)), IRNode("film","layer",("n",),(("thickness","$d"),),unit="nm"), IRNode("stack","stack",("film","sub"),unit="nm"), IRNode("solver","tmm",("stack",)), IRNode("drift","drift",("solver",),(("gain",1.),))), "drift", (ParameterSpec("d",80,160,110,"nm"),))
    candidate=ModelCandidate(object_id="candidate",source_run_id="run",model_ir=asdict(ir))
    result=fit_model(compile_candidate(candidate),blocks_from_observations([observation]),starts=5)
    assert abs(result.parameters["d"]-120)<.1
    state=fit_result_to_state(result,candidate,[observation],object_id="fit",source_run_id="run")
    assert state.observation_refs==(observation.ref(),)
    changed=ModelCandidate(object_id="changed",source_run_id="run",model_ir=asdict(replace(ir,nodes=ir.nodes[:-1]+(replace(ir.nodes[-1],attributes=(("gain",.9),)),))))
    with pytest.raises(ValueError,match="refitting"): fit_result_to_state(result,changed,[observation],object_id="bad",source_run_id="run")
