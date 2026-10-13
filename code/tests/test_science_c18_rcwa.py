import numpy as np
import pytest
pytest.importorskip('grcwa')
from optomind_optics.harness.tools.rcwa_adapter import RCWAAdapter


def slab(**kwargs):
    p = dict(wavelength_nm=600., period_nm=[450., 450.], layers=[dict(thickness_nm=100., epsilon=2.25)], fourier_order=21)
    p.update(kwargs)
    return p


def test_uniform_limit_analytic_and_durable(tmp_path):
    adapter = RCWAAdapter(tmp_path)
    request = slab()
    r = adapter.submit('uniform', request)
    # Quarter-wave n=1.5 layer in vacuum: analytic Fresnel/Airy limit.
    expected = ((2.25 - 1)/(2.25 + 1))**2
    assert r['result']['R'] == pytest.approx(expected, abs=1e-11)
    assert r['result']['energy_balance_error'] < 1e-11
    assert adapter.submit('uniform', request) == r
    assert RCWAAdapter(tmp_path).replay('uniform')['ok']
    assert not adapter.cancel('uniform')['supported']
    with pytest.raises(ValueError, match='identity'):
        adapter.submit('uniform', slab(wavelength_nm=601.))


def test_periodic_orders_and_convergence():
    x, y = np.meshgrid(np.arange(64), np.arange(64), indexing='ij')
    grid = 1.8 + 0.25*np.cos(2*np.pi*x/64)*np.cos(2*np.pi*y/64)
    p = slab(period_nm=[850., 850.], layers=[dict(thickness_nm=160., epsilon_grid=grid.tolist())])
    results = [RCWAAdapter().solve(dict(p, fourier_order=n)) for n in (13, 29, 49)]
    for r in results:
        assert r['energy_balance_error'] < 1e-10
        assert sum(r['R_orders']) == pytest.approx(r['R'])
        assert len(r['reflected_amplitudes']) == 2*r['actual_fourier_order']
    assert abs(results[-1]['R']-results[-2]['R']) < abs(results[1]['R']-results[0]['R'])
    assert abs(results[-1]['R']-results[-2]['R']) < 1e-5
    assert sum(v for order,v in zip(results[-1]['orders'], results[-1]['T_orders']) if order != [0,0]) > 1e-5


@pytest.mark.parametrize('change', [dict(length_unit='m'), dict(theta_deg=90),dict(polarization='mixed'),dict(fourier_order=1000),dict(layers=[dict(thickness_nm=10,epsilon=-2)])])
def test_reject_domain(change):
    assert not RCWAAdapter().preflight(slab(**change))['ok']


@pytest.mark.parametrize('pol', ['s','p'])
@pytest.mark.parametrize('angle', [0.,37.])
def test_actual_veritmm_uniform_overlap(pol,angle):
    from tmm_engine.tmm_solver import TMM, TMMConfig
    R,T=TMM(TMMConfig(wavelength_unit='m')).rt_spectrum(
        [np.array([1.5]),np.array([1.])],[100e-9,0.],np.array([600e-9]),angle,pol,theta_unit='deg')
    rc=RCWAAdapter().solve(slab(theta_deg=angle,polarization=pol))
    assert rc['R']==pytest.approx(float(R[0]),abs=1e-10)
    assert rc['T']==pytest.approx(float(T[0]),abs=1e-10)


def test_explicit_registry_and_job_gate(tmp_path):
    from optomind_optics.harness.capability_registry import CapabilityRegistry
    from optomind_optics.harness.tools.independent_registry import register_independent_tools
    from optomind_optics.harness.science.jobs import ExecutionGate
    registry=CapabilityRegistry()
    adapter,_=register_independent_tools(registry,tmp_path/'receipts')
    assert registry.resolve(adapter.capability_id) is adapter
    assert registry.resolve(adapter.capability_id,scope='literature-only') is None
    gate=ExecutionGate(tmp_path/'gate.db',limits={'cpu_seconds':30.},capability=adapter.capability())
    receipt=gate.execute(adapter,'gate-rcwa',slab(),estimate={'cpu_seconds':10.},prediction_commit={'prediction_id':'quarter-wave-analytic'})
    assert receipt.state=='completed'
    assert gate.usage()['cpu_seconds'] >= 0
    gate.close()


def test_rayleigh_boundary_rejected_and_near_boundary_stable():
    a=RCWAAdapter()
    assert not a.preflight(slab(period_nm=[600.,450.]))['ok']
    for period in (599.,601.):
        r=a.solve(slab(period_nm=[period,450.]))
        assert r['energy_balance_error'] < 1e-10


def test_artifact_tamper_and_failed_call_not_retried(tmp_path):
    import json
    a=RCWAAdapter(tmp_path)
    a.submit('bound',slab())
    path=a._path('bound')
    data=json.loads(path.read_text());data['result']['R']=0.;path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='integrity'):
        RCWAAdapter(tmp_path).replay('bound')
    a._store('failed',{'state':'failed','handle_id':'failed','payload_hash':'x'})
    with pytest.raises(ValueError,match='blindly'):
        a.submit('failed',slab())
