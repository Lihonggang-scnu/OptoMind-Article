import numpy as np
import pytest
pytest.importorskip('ceviche')
from optomind_optics.harness.tools.fullwave_adapter import FullwaveAdapter


def slab(dx=10., thickness=100., npml_nm=200., amplitude=1.):
    eps=np.ones((round(2000/dx), 4)); eps[round(900/dx):round((900+thickness)/dx)] = 2.25
    return dict(wavelength_nm=600.,dx_nm=dx,epsilon_grid=eps.tolist(),npml=round(npml_nm/dx),source_x=round(400/dx),reflection_x=round(600/dx),transmission_x=round(1400/dx),source_amplitude=amplitude)


def test_grid_convergence_and_independent_uniform_limit():
    results=[FullwaveAdapter().solve(slab(dx,npml_nm=300)) for dx in (20.,10.,5.)]
    exact=((2.25-1)/(2.25+1))**2
    errors=[abs(r['R']-exact) for r in results]
    assert errors[2] < errors[1] < errors[0]
    assert errors[-1] < 3e-4
    for r in results:
        assert r['linear_residual'] < 1e-10
        assert r['energy_balance_error'] < 2e-5
        assert r['reference_backreflection'] < 1e-8


def test_source_normalization_and_pml_convergence():
    a=FullwaveAdapter().solve(slab())
    b=FullwaveAdapter().solve(slab(amplitude=7))
    c=FullwaveAdapter().solve(slab(npml_nm=300))
    assert a['R'] == pytest.approx(b['R'],abs=1e-10)
    assert abs(a['R']-c['R']) < 2e-5
    assert c['reference_backreflection'] < a['reference_backreflection']


def test_reject_unsupported_angles_units_and_boundary():
    a=FullwaveAdapter()
    for change in [dict(theta_deg=20),dict(length_unit='um'),dict(polarization='Hz'),dict(npml=1),dict(dx_nm=100)]:
        assert not a.preflight(dict(slab(), **change))['ok']


def test_independent_periodic_overlap_with_rcwa():
    pytest.importorskip('grcwa')
    from optomind_optics.harness.tools.rcwa_adapter import RCWAAdapter
    # RCWA x lateral = FDFD y, RCWA z propagation = FDFD x, s = Ez.
    y=np.arange(128)+.5
    grid=np.repeat((1.8+.25*np.cos(2*np.pi*y/128))[:,None],32,axis=1)
    rc=RCWAAdapter().solve(dict(wavelength_nm=600.,period_nm=[800.,100.],
        layers=[dict(thickness_nm=160.,epsilon_grid=grid.tolist())],fourier_order=29,polarization='s'))
    results=[]
    for dx in (20.,10.):
        ny=round(800/dx)
        eps=np.ones((round(2000/dx),ny))
        eps[round(900/dx):round(1060/dx)] = (1.8+.25*np.cos(2*np.pi*(np.arange(ny)+.5)/ny))[None,:]
        p=slab(dx,npml_nm=300)
        p['epsilon_grid']=eps.tolist()
        results.append(FullwaveAdapter().solve(p))
    assert abs(results[1]['R']-rc['R']) < abs(results[0]['R']-rc['R'])/3
    assert abs(results[1]['R']-rc['R']) < 3e-4
    assert all(r['energy_balance_error'] < 1e-5 for r in results)
    assert len(results[1]['orders']) == 3


def test_ranking_and_constraint_ambiguity_escalate():
    from optomind_optics.harness.tools.fidelity_policy import choose_fidelity, SURROGATE_POLICY
    kwargs=dict(domain='lossless-Ez',comparison_domain='lossless-Ez',precision='coarse',observed_error=.01,objective_gap=.015,constraint_margin=.1)
    assert choose_fidelity(**kwargs).direct_recheck_required
    assert choose_fidelity(**dict(kwargs,objective_gap=.1,constraint_margin=.005)).direct_recheck_required
    assert choose_fidelity(**dict(kwargs,comparison_domain='absorbing-3D')).error_bound is None
    assert not SURROGATE_POLICY['enabled']


def test_domain_extension_and_transmitted_phase():
    p=slab(10.,npml_nm=300)
    a=FullwaveAdapter().solve(p)
    eps=np.asarray(p['epsilon_grid'])
    extended=np.pad(eps,((20,20),(0,0)),constant_values=1)
    q=dict(p,epsilon_grid=extended.tolist(),source_x=p['source_x']+20,
           reflection_x=p['reflection_x']+20,transmission_x=p['transmission_x']+20)
    b=FullwaveAdapter().solve(q)
    assert abs(a['R']-b['R']) < 2e-6
    assert abs(a['T']-b['T']) < 2e-6
    # Global phase reference anchored to incoming wave at reflection monitor.
    ta=complex(*a['transmitted_amplitudes'][0]);tb=complex(*b['transmitted_amplitudes'][0])
    assert abs(np.angle(ta/tb)) < 1e-5


def test_empty_domain_complex_phase_normalization():
    p=slab(npml_nm=300)
    p['epsilon_grid']=np.ones_like(p['epsilon_grid']).tolist()
    r=FullwaveAdapter().solve(p)
    q=2*np.arcsin(np.pi*p['dx_nm']/p['wavelength_nm'])
    expected=np.exp(-1j*q*(p['transmission_x']-p['reflection_x']))
    actual=complex(*r['transmitted_amplitudes'][0])
    assert actual==pytest.approx(expected,abs=1e-7)
    assert r['R'] < 1e-20
    assert r['T'] == pytest.approx(1.,abs=1e-8)
