import pytest
from optomind_optics.harness.science import adaptive_calibration as calibration


def test_full_dependency_fingerprint_includes_evaluator_and_solver():
    paths=calibration.runtime_source_fingerprints()
    assert any('/optomind_eval/' in p for p in paths)
    assert any('/veritmm/' in p for p in paths)
    assert any('/optomind_research/' in p for p in paths)


def test_final_failed_case_source_drift_cannot_publish_report(tmp_path,monkeypatch):
    calls=[]
    def fingerprints():
        calls.append(1)
        return {'source':'old' if len(calls)<=2 else 'changed'}
    monkeypatch.setattr(calibration,'runtime_source_fingerprints',fingerprints)
    def failed_factory(*args):raise ValueError('record this failed attempt')
    with pytest.raises(RuntimeError,match='during calibration case'):
        calibration.full_adaptive_audit(tmp_path,base_payload={},calibration_seeds=(1,),null_audit_seeds=(2,),loop_factory=failed_factory)
    assert not (tmp_path/'report.json').exists()
    assert (tmp_path/'protocol.json').exists()
