from dataclasses import asdict, replace
import hashlib
import json

import pytest

from test_science_c21_manual_import import fixture
from optomind_optics.harness.tools.manual_audit import IndependentQualityAudit, audit_manual_holdout, main
from optomind_optics.harness.tools.external_measurement import import_manual


def setup(tmp_path):
    repo, protocol, frozen, returned, card, split, record = fixture(tmp_path)
    returned = returned.model_copy(update={'sample_id':'s2','batch_id':'batch2','control_evidence_refs':('reference-raw','dark-raw')})
    record = replace(record,sample_id='s2')
    (tmp_path/'external_report.txt').write_bytes(b'recorded external report')
    audit = IndependentQualityAudit(protocol_hash=protocol.content_hash,prediction_hash=returned.prediction_hash,
        source_hash=returned.raw_sha256,sample_id='s2',batch_id='batch2',calibration=protocol.calibration,
        reviewer_id='external-reviewer',report_source_hash=hashlib.sha256(b'recorded external report').hexdigest(),
        status='inconclusive',controls='passed',numerical='passed',uncertainty='unverified',
        source_evidence_refs=('external-report',),independent_scope='cross_batch',limitations=('Noise unknown',))
    return repo,protocol,frozen,returned,card,split,record,audit


def test_holdout_evaluated_without_releasing_or_promoting_physical_claim(tmp_path):
    repo,p,f,r,c,s,rec,audit = setup(tmp_path)
    try:
        before = repo.latest_snapshot().snapshot_id
        out = tmp_path/'independent_audit.json'
        result = audit_manual_holdout(repo,f,r,c,s,rec,tmp_path,audit,out,review_report=tmp_path/'external_report.txt')
        assert result['prediction_verdict']['state'] == 'inconclusive'
        assert result['raw_data_released_to_research'] is False
        assert result['V4_accepted'] is False and result['hardware_executed'] is False
        assert repo.latest_snapshot().snapshot_id == before
        assert audit_manual_holdout(repo,f,r,c,s,rec,tmp_path,audit,out,review_report=tmp_path/'external_report.txt')['report_hash'] == result['report_hash']
        with pytest.raises(ValueError,match='holdout is sealed'): import_manual(repo,f,r,c,s,rec,tmp_path)
    finally: repo.close()


@pytest.mark.parametrize('change', [{'source_hash':'a'*64},{'prediction_hash':'b'*64},{'sample_id':'s1'},{'reviewer_id':'unverified operator'}])
def test_wrong_binding_or_nonindependent_reviewer_rejected(tmp_path,change):
    repo,p,f,r,c,s,rec,audit = setup(tmp_path)
    try:
        with pytest.raises(ValueError): audit_manual_holdout(repo,f,r,c,s,rec,tmp_path,audit.model_copy(update=change),tmp_path/'audit.json',review_report=tmp_path/'external_report.txt')
    finally: repo.close()


def test_forged_pass_does_not_supply_missing_uncertainty(tmp_path):
    repo,p,f,r,c,s,rec,audit = setup(tmp_path)
    try:
        audit = audit.model_copy(update={'status':'passed','uncertainty':'passed'})
        result = audit_manual_holdout(repo,f,r,c,s,rec,tmp_path,audit,tmp_path/'audit.json',review_report=tmp_path/'external_report.txt')
        assert result['prediction_verdict']['state'] == 'inconclusive'
        assert result['V4_accepted'] is False
    finally: repo.close()


def test_audit_report_cannot_overwrite_or_leak_into_research_store(tmp_path):
    repo,p,f,r,c,s,rec,audit = setup(tmp_path)
    try:
        with pytest.raises(ValueError,match='outside'): audit_manual_holdout(repo,f,r,c,s,rec,tmp_path,audit,repo.root/'audit.json',review_report=tmp_path/'external_report.txt')
        out = tmp_path/'audit.json'
        audit_manual_holdout(repo,f,r,c,s,rec,tmp_path,audit,out,review_report=tmp_path/'external_report.txt')
        changed = json.loads(out.read_text());changed['V4_accepted'] = True;out.write_text(json.dumps(changed))
        with pytest.raises(ValueError,match='identity changed'): audit_manual_holdout(repo,f,r,c,s,rec,tmp_path,audit,out,review_report=tmp_path/'external_report.txt')
    finally: repo.close()


def test_formal_cli_uses_audit_consumer_without_new_measurement(tmp_path,capsys):
    repo,p,f,r,c,s,rec,audit = setup(tmp_path)
    try:
        bundle = tmp_path/'bundle.json';bundle.write_text(json.dumps({'return':r.model_dump(mode='json'),'card':asdict(c),'split':asdict(s),'record':asdict(rec)}))
        decision = tmp_path/'quality.json';decision.write_text(audit.model_dump_json())
        main(['--repository',str(repo.root),'--frozen',str(f),'--bundle',str(bundle),'--quality-audit',str(decision),
              '--source-root',str(tmp_path),'--review-report',str(tmp_path/'external_report.txt'),'--output',str(tmp_path/'audit.json')])
        result = json.loads(capsys.readouterr().out)
        assert result['raw_data_released_to_research'] is False
    finally: repo.close()


def test_source_qualified_numerical_audit_uses_existing_prediction_judge(tmp_path):
    from optomind_optics.harness.science.dataset_contracts import DatasetSplitManifest
    repo,p,f,r,c,s,rec,audit = setup(tmp_path)
    try:
        conditions = (('angle_deg','0'),('polarization','s'),('wavelength_units','nm'),('sample_geometry','fixture thin film'))
        qualified = replace(c,conditions=conditions,uncertainty='fixture supplied diagonal covariance',
                            permitted_uses=('quantitative_inference',),missingness=())
        split = DatasetSplitManifest(qualified.content_hash,s.group_splits)
        measured = replace(rec,conditions=conditions,covariance=((.01,0.),(0.,.01)))
        passed = audit.model_copy(update={'status':'passed','uncertainty':'passed'})
        result = audit_manual_holdout(repo,f,r,qualified,split,measured,tmp_path,passed,tmp_path/'audit.json',
                                      review_report=tmp_path/'external_report.txt')
        assert result['prediction_verdict']['state'] == 'supported'
        assert result['V4_accepted'] is False
        with pytest.raises(ValueError,match='conditions differ'):
            audit_manual_holdout(repo,f,r,qualified,split,rec,tmp_path,passed,tmp_path/'other.json',review_report=tmp_path/'external_report.txt')
    finally: repo.close()


def test_revealed_raw_and_changed_review_source_rejected(tmp_path):
    repo,p,f,r,c,s,rec,audit = setup(tmp_path)
    try:
        (tmp_path/'external_report.txt').write_text('modified review')
        with pytest.raises(ValueError,match='report source hash'):
            audit_manual_holdout(repo,f,r,c,s,rec,tmp_path,audit,tmp_path/'audit.json',review_report=tmp_path/'external_report.txt')
        (tmp_path/'external_report.txt').write_bytes(b'recorded external report')
        dev = r.model_copy(update={'sample_id':'s1','batch_id':'batch1'})
        import_manual(repo,f,dev,c,s,replace(rec,sample_id='s1'),tmp_path)
        with pytest.raises(ValueError,match='already revealed'):
            audit_manual_holdout(repo,f,r,c,s,rec,tmp_path,audit,tmp_path/'audit.json',review_report=tmp_path/'external_report.txt')
    finally: repo.close()


def test_development_quality_revision_preserves_acquisition_and_raw_evidence(tmp_path):
    from optomind_optics.harness.science.dataset_contracts import DatasetSplitManifest
    from optomind_optics.harness.tools.manual_audit import audit_manual_development
    repo,p,f,r,c,s,rec,audit = setup(tmp_path)
    try:
        conditions = (('angle_deg','0'),('polarization','s'),('wavelength_units','nm'),('sample_geometry','fixture thin film'))
        qualified = replace(c,conditions=conditions,uncertainty='fixture diagonal covariance',permitted_uses=('quantitative_inference',),missingness=())
        split = DatasetSplitManifest(qualified.content_hash,s.group_splits)
        measured = replace(rec,sample_id='s1',conditions=conditions,covariance=((.01,0.),(0.,.01)))
        dev = r.model_copy(update={'sample_id':'s1','batch_id':'batch1'})
        review = audit.model_copy(update={'sample_id':'s1','batch_id':'batch1','status':'passed','uncertainty':'passed',
                                         'independent_scope':'same_sample_new_conditions'})
        original = import_manual(repo,f,dev,qualified,split,measured,tmp_path)
        assert original['prediction_verdict'] == 'inconclusive'
        result = audit_manual_development(repo,f,dev,qualified,split,measured,tmp_path,review,review_report=tmp_path/'external_report.txt')
        assert result['prediction_verdict'] == 'supported'
        assert result['observation_ref']['revision'] == 2 and result['V4_accepted'] is False
        sequence = repo.latest_snapshot().committed_sequence
        assert audit_manual_development(repo,f,dev,qualified,split,measured,tmp_path,review,review_report=tmp_path/'external_report.txt') == result
        assert repo.latest_snapshot().committed_sequence == sequence
        assert repo.acquisition_commit_sequence('manual-raw:'+r.raw_sha256) == sequence-1
        objects = [repo.load(ref) for ref in repo.latest_snapshot().object_refs if ref.object_id == result['observation_ref']['object_id']]
        assert tuple(objects[0].raw_blocks[0]['values']) == measured.values
    finally: repo.close()
