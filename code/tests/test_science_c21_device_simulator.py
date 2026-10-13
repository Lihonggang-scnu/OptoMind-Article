import pytest
from optomind_optics.harness.science.jobs import ExecutionGate,ToolCapability
from optomind_optics.harness.tools.instrument_adapter import InMemoryTestDriver, UnsupportedRealDriver


@pytest.mark.parametrize('fault',['timeout','disconnect'])
def test_unknown_never_means_stopped_or_automatic_retry(tmp_path,fault):
    driver=InMemoryTestDriver(fault)
    cap=ToolCapability(effect_class='external_measurement',domain='simulator_only',supports_status=True)
    gate=ExecutionGate(tmp_path/'jobs',limits={'readings':3},capability=cap)
    for _ in range(2):
        result=gate.execute(driver,'same',{'command':'measure'},estimate={'readings':1},prediction_commit={'fixture_only':True})
        assert result.state=='unknown_outcome'
    assert driver.calls==1 and not driver.poll('same').stopped_confirmed
    assert driver.cancel('same')['state']=='unsupported'
    gate.close()


@pytest.mark.parametrize('fault',['partial','emergency_stop'])
def test_fault_never_claims_verified_stop(fault):
    driver=InMemoryTestDriver(fault)
    result=driver.submit('id',{'command':'measure'})
    assert result['state']=='failed' and result['stopped_confirmed'] is False
    assert result['evidence_scope']=='simulator_only'


def test_driver_boundary_whitelist_expired_calibration_and_idempotency():
    with pytest.raises(NotImplementedError,match='real device unsupported'):
        UnsupportedRealDriver(approval='approve')
    driver=InMemoryTestDriver()
    with pytest.raises(ValueError,match='whitelisted'):driver.submit('bad',{'command':'destroy'})
    driver.submit('one',{'command':'measure'});driver.submit('one',{'command':'measure'})
    assert driver.calls==1
    with pytest.raises(ValueError,match='identity'):driver.submit('one',{'command':'measure','sample':'changed'})
    expired=InMemoryTestDriver('expired_calibration')
    with pytest.raises(ValueError,match='expired'):expired.submit('one',{'command':'measure'})
    assert expired.calls==0
