import numpy as np
import pytest
from test_science_c06_model_compiler import film_model
from optomind_optics.harness.science.model_compiler import compile_model
from optomind_optics.harness.science.model_search import propose_extensions,append_registered_operator
from optomind_optics.harness.science.operator_registry import OperatorRegistry
from optomind_optics.harness.science.operator_sandbox import evaluate_expression
from optomind_optics.harness.science.model_equivalence import deduplicate_models


def test_classical_grammar_composes_typed_operators_and_deduplicates():
    parent=film_model(); proposals=propose_extensions(parent,mismatch="spectral smoothing with baseline shift",max_depth=2,max_candidates=8)
    assert any(len(p.edit)==2 for p in proposals)
    models=[compile_model(p.model_ir) for p in proposals]
    for m in models: assert np.isfinite(m.predict({},np.linspace(400,900,21))).all()
    representatives,groups=deduplicate_models([compile_model(parent),compile_model(film_model("other"))],wavelengths_nm=np.linspace(400,900,21))
    assert len(representatives)==1


def test_restricted_new_nonlinear_operator_runs_and_cannot_reuse_old_evidence():
    registry=OperatorRegistry(); x=np.array([0,.2,.8]); wl=[500,600,700]
    report=registry.register_expression("x/(1+x)",source_refs=("controlled-detector-saturation",),domain=(400,900),reference_cases=[(x,wl,[0,1/6,4/9],1e-12)],limit_cases=[([0,0], [500,600], [0,0],0)])
    assert report.status=="tentative_executable"
    proposal=append_registered_operator(film_model(),registry,report.operator_id,source_refs=("controlled-detector-saturation",))
    parent=compile_model(film_model()).predict({},wl);actual=compile_model(proposal.model_ir).predict({},wl)
    assert np.allclose(actual,parent/(1+parent))
    with pytest.raises(ValueError,match="old evidence"): registry.support_on_fresh_prediction(report.operator_id,prediction_frozen_before_observation=True,observation_id="training",training_observation_ids=["training"],passed=True)


@pytest.mark.parametrize("expression",["__import__('os').system('id')","x.__class__", "[v for v in x]", "x**999999", "open('/etc/passwd')", "exp(1e12)"])
def test_operator_sandbox_rejects_io_unbounded_and_nonfinite(expression):
    with pytest.raises((ValueError,SyntaxError,FloatingPointError)):
        evaluate_expression(expression,[.1,.2],[500,600])


def test_new_operator_units_must_close():
    with pytest.raises(ValueError,match="dimensional"):
        evaluate_expression("x+wavelength_nm",[.1,.2],[500,600])
    with pytest.raises(ValueError,match="dimensionless"):
        evaluate_expression("sin(wavelength_nm)",[.1,.2],[500,600])
