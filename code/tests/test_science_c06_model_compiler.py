from dataclasses import replace
import numpy as np
import pytest
from optomind_optics.harness.science.model_ir import IRNode, ModelIR, ParameterSpec, CompileError
from optomind_optics.harness.science.model_compiler import compile_model


def film_model(parameter="d", unit="nm", extras=()):
    scale = 1 if unit == "nm" else .001
    nodes = (IRNode("film", "constant_nk", attributes=(("n", 2.),)), IRNode("sub", "constant_nk", attributes=(("n", 1.5),)), IRNode("layer", "layer", ("film",), (("thickness", "$"+parameter),), unit=unit), IRNode("stack", "stack", ("layer", "sub"), unit="nm"), IRNode("solver", "tmm", ("stack",))) + tuple(extras)
    return ModelIR(nodes, extras[-1].node_id if extras else "solver", (ParameterSpec(parameter, 50*scale, 200*scale, 110*scale, unit),))


def test_airy_independent_reference_and_energy():
    wl = np.linspace(400, 900, 101)
    model = compile_model(film_model())
    phase = np.exp(4j*np.pi*2*100/wl)
    r = (-1/3 + (1/7)*phase)/(1 - phase/21)
    assert np.allclose(model.predict({"d":100}, wl), abs(r)**2, atol=2e-14)
    assert np.allclose(model.predict({"d":100}, wl, channel="R") + model.predict({"d":100}, wl, channel="T"), 1, atol=1e-12)


def test_alpha_rename_and_unit_invariance():
    assert film_model().structural_hash == film_model("renamed").structural_hash
    wl = np.linspace(400, 900, 80)
    assert np.allclose(compile_model(film_model()).predict({"d":100}, wl), compile_model(film_model(unit="um")).predict({"d":.1}, wl))


def test_reject_coordinate_units_types_and_cycles():
    ir = film_model()
    for node, expected in ((replace(ir.nodes[-1], coordinate="internal"), "ANGLE_COORDINATE"), (replace(ir.nodes[-1], unit="nm"), "UNIT_MISMATCH"), (replace(ir.nodes[-1], inputs=("film",)), "PHYSICAL_TYPE"), (replace(ir.nodes[-1], inputs=("solver",)), "INVALID_DAG")):
        with pytest.raises(CompileError, match=expected): compile_model(replace(ir, nodes=ir.nodes[:-1]+(node,)))


def test_nontrivial_composition_resolution_drift():
    extras = (IRNode("resolution", "resolution", ("solver",), (("sigma_nm", 8.),)), IRNode("drift", "drift", ("resolution",), (("gain", .98), ("offset", .01))))
    model = compile_model(film_model(extras=extras))
    coarse = np.linspace(400, 900, 201); dense = np.linspace(400, 900, 2001)
    a=model.predict({"d":100},coarse); b=model.predict({"d":100},dense)
    assert np.all(np.isfinite(a))
    assert np.max(np.abs(a[10:-10] - b[::10][10:-10])) < 2e-5


def test_profile_constant_limit_and_grid_refinement():
    base=film_model(); wl=np.linspace(400,900,51)
    def profile(n_end,segments):
        nodes=base.nodes[:2]+(IRNode("end","constant_nk",attributes=(("n",n_end),)),replace(base.nodes[2],operator="linear_profile",inputs=("film","end"),attributes=(("thickness","$d"),("segments",segments))))+base.nodes[3:]
        return compile_model(replace(base,nodes=nodes))
    ordinary=compile_model(base).predict({"d":120},wl)
    assert np.allclose(profile(2,16).predict({"d":120},wl),ordinary,atol=1e-12)
    a=profile(2.5,16).predict({"d":120},wl); b=profile(2.5,32).predict({"d":120},wl); c=profile(2.5,64).predict({"d":120},wl)
    assert np.linalg.norm(c-b)<np.linalg.norm(b-a)/3


def test_incoherent_layer_matches_intensity_geometric_series():
    ir=film_model(); nodes=list(ir.nodes); nodes[2]=replace(nodes[2],attributes=nodes[2].attributes+(("coherence","incoherent"),))
    result=compile_model(replace(ir,nodes=tuple(nodes))).predict({"d":120},[500.,600.])
    r01=1/9; r12=1/49
    expected=r01+(1-r01)**2*r12/(1-r01*r12)
    assert np.allclose(result,expected,atol=1e-12)


def test_aperture_polarization_contract_limits_and_refinement():
    base=film_model();wl=np.linspace(440,820,31)
    def forward(aperture,points,fraction):
        solver=replace(base.nodes[-1],attributes=(('aperture_deg',aperture),('quadrature_points',points),('s_fraction',fraction)))
        return compile_model(replace(base,nodes=base.nodes[:-1]+(solver,))).predict({'d':120},wl,angle_deg=35.)
    s=forward(0,1,1.);p=forward(0,1,0.)
    assert np.allclose(forward(0,8,.3),.3*s+.7*p,atol=1e-14)
    a,b,c=forward(12,2,.5),forward(12,4,.5),forward(12,8,.5)
    assert np.linalg.norm(c-b)<np.linalg.norm(b-a)/10
    with pytest.raises(CompileError,match='RESOURCE_LIMIT'):forward(1,65,.5)


def test_cauchy_nested_limit_shared_bias_and_normalization_reference():
    base=film_model();wl=np.linspace(450,800,20)
    nodes=list(base.nodes);nodes[0]=replace(nodes[0],operator='cauchy_nk',attributes=(('A',2.),('B_um2',0.),('C_um4',0.)))
    assert np.allclose(compile_model(replace(base,nodes=tuple(nodes))).predict({'d':110},wl),compile_model(base).predict({'d':110},wl))
    nodes=list(base.nodes);nodes[2]=replace(nodes[2],attributes=nodes[2].attributes+(('shared_bias',10.),))
    biased=compile_model(replace(base,nodes=tuple(nodes))).predict({'d':100},wl)
    assert np.allclose(biased,compile_model(base).predict({'d':110},wl))
    normalized=film_model(extras=(IRNode('normalize','normalize',('solver',),(('reference',.5),)),))
    assert np.allclose(compile_model(normalized).predict({'d':110},wl),biased/.5)
