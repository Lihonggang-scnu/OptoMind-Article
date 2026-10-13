from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import time

import pytest
from optomind_optics.harness.tools.external_measurement import (
    ProspectiveProtocol, FrozenPrediction, ManualReturn, freeze_protocol, import_manual, human_protocol, main,
)
from optomind_optics.harness.science.dataset_contracts import DatasetCard, DatasetSplitManifest
from optomind_optics.harness.tools.measured_data_adapter import RecordedMeasurement
from test_science_c08_prediction import setup_prediction


def fixture(tmp_path):
    repo, task, spec, commit = setup_prediction(tmp_path/'science')
    protocol = ProspectiveProtocol(protocol_id='thin-film-manual', task=task.ref(), prediction_commit=FrozenPrediction(**asdict(commit)),
        samples=(('s1','batch1'),('s2','batch2')), calibration='cal-1',
        calibration_expires_at=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),
        randomized_order=('s2','s1'), randomization_basis='seed 31, frozen permutation', repeats=2,
        repeats_basis='two repeated readings for technical precision, not batch replication', operator='unverified operator',
        environment=(('temperature_C','20'),), failure_rules=('stop on unsafe conditions or calibration failure',),
        budget=(('human_minutes',30.),('fabrication_cost',20.),('readings',6.)), holdout=('s2',),
        holdout_basis='independent audit only', channel='R')
    frozen=tmp_path/'frozen'; _,receipt=freeze_protocol(repo,protocol,frozen)
    raw=tmp_path/'raw.csv'; raw.write_text('500,0.5\n600,0.6\n')
    sha=hashlib.sha256(raw.read_bytes()).hexdigest()
    card=DatasetCard('manual','https://example.org/operator-return','operator-supplied',(('raw.csv',sha),),
                     (('s1','batch1'),('s2','batch2')),(('angle_deg',None),),'cal-1',None,'raw_measured',('engineering_import',),('uncertainty',))
    split=DatasetSplitManifest(card.content_hash,(('batch1','train'),('batch2','audit')))
    record=RecordedMeasurement('reading','s1','raw.csv','R','dimensionless',(500,600),(.5,.6),(('angle_deg',None),))
    returned=ManualReturn(protocol_hash=protocol.content_hash, preexecution_receipt_hash=receipt['receipt_hash'],
        prediction_hash=spec.content_hash,sample_id='s1',batch_id='batch1',calibration='cal-1',operator=protocol.operator,
        acquired_at=datetime.now(timezone.utc).isoformat(),raw_sha256=sha,action_id='reading',control_evidence_refs=())
    return repo,protocol,frozen,returned,card,split,record


def test_manual_import_canonical_pending_audit_and_duplicate_rejected(tmp_path):
    repo,protocol,frozen,returned,card,split,record=fixture(tmp_path)
    result=import_manual(repo,frozen,returned,card,split,record,tmp_path)
    assert result['prediction_verdict']=='inconclusive'
    assert result['quality_audit']=='pending_independent_review'
    projection=repo.rebuild_projection()
    obs=projection[result['observation_ref']['object_id']]
    assert obs['quality']['V4_accepted'] is False and obs['quality']['new_physical_measurement'] is False
    with pytest.raises(ValueError,match='duplicate acquisition'):
        import_manual(repo,frozen,returned,card,split,record,tmp_path)
    repo.close()


@pytest.mark.parametrize('field,value', [('sample_id','wrong'),('batch_id','wrong'),('prediction_hash','x'),('preexecution_receipt_hash','x'),('calibration','wrong'),('operator','wrong'),('raw_sha256','x')])
def test_mismatched_manual_identity_rejected(tmp_path,field,value):
    repo,p,f,r,c,s,rec=fixture(tmp_path)
    with pytest.raises(ValueError):
        import_manual(repo,f,r.model_copy(update={field:value}),c,s,rec,tmp_path)
    assert repo.latest_snapshot().committed_sequence==2
    repo.close()


def test_postexecution_edit_and_raw_tamper_rejected(tmp_path):
    repo,p,f,r,c,s,rec=fixture(tmp_path)
    with pytest.raises(ValueError,match='cannot change'):
        freeze_protocol(repo,p.model_copy(update={'operator':'different'}),f)
    (tmp_path/'raw.csv').write_text('500,.9\n600,.9\n')
    with pytest.raises(ValueError,match='modified'):
        import_manual(repo,f,r,c,s,rec,tmp_path)
    repo.close()


def test_preexisting_and_expired_calibration_rejected(tmp_path):
    repo,p,f,r,c,s,rec=fixture(tmp_path)
    with pytest.raises(ValueError,match='must follow'):
        import_manual(repo,f,r.model_copy(update={'acquired_at':'2000-01-01T00:00:00Z'}),c,s,rec,tmp_path)
    repo.close()


def test_cli_preview_export_import_local_only(tmp_path,capsys):
    repo,p,f,r,c,s,rec=fixture(tmp_path)
    protocol_file=tmp_path/'input.json';protocol_file.write_text(p.model_dump_json())
    main(['preview','--protocol',str(protocol_file)])
    assert 'no hardware command' in capsys.readouterr().out
    main(['export','--protocol',str(protocol_file),'--repository',str(tmp_path/'science'),'--output',str(f)])
    assert json.loads(capsys.readouterr().out)['hardware_executed'] is False
    bundle=tmp_path/'return.json';bundle.write_text(json.dumps({'return':r.model_dump(mode='json'),'card':asdict(c),'split':asdict(s),'record':asdict(rec)}))
    main(['import','--frozen',str(f),'--repository',str(tmp_path/'science'),'--bundle',str(bundle),'--source-root',str(tmp_path)])
    assert json.loads(capsys.readouterr().out)['quality_audit']=='pending_independent_review'
    assert (f/'operator_protocol.txt').exists()
    repo.close()


@pytest.mark.parametrize('status',['partial','failed'])
def test_partial_measurements_preserved_not_evaluable(tmp_path,status):
    repo,p,f,r,c,s,rec=fixture(tmp_path)
    result=import_manual(repo,f,r.model_copy(update={'execution_status':status}),c,s,rec,tmp_path)
    assert result['prediction_verdict']=='not_evaluable'
    repo.close()


def test_frozen_receipt_rehash_cannot_bypass_durable_identity(tmp_path):
    from optomind_optics.harness.science.contracts import canonical_hash
    repo,p,f,r,c,s,rec=fixture(tmp_path)
    receipt=json.loads((f/'preexecution.json').read_text())
    receipt['frozen_at']='1999-01-01T00:00:00Z'
    receipt['receipt_hash']=canonical_hash({k:v for k,v in receipt.items() if k!='receipt_hash'})
    (f/'preexecution.json').write_text(json.dumps(receipt))
    with pytest.raises(ValueError,match='durable preexecution'):
        import_manual(repo,f,r,c,s,rec,tmp_path)
    repo.close()


def test_expired_calibration_and_holdout_cannot_be_imported(tmp_path):
    repo,p,f,r,c,s,rec=fixture(tmp_path)
    old=p.model_copy(update={'protocol_id':'expired','calibration_expires_at':'2000-01-01T00:00:00Z'})
    frozen=tmp_path/'expired';_,receipt=freeze_protocol(repo,old,frozen)
    returned=r.model_copy(update={'protocol_hash':old.content_hash,'preexecution_receipt_hash':receipt['receipt_hash'],
                                  'acquired_at':datetime.now(timezone.utc).isoformat()})
    with pytest.raises(ValueError,match='calibration expired'):
        import_manual(repo,frozen,returned,c,s,rec,tmp_path)
    returned=r.model_copy(update={'sample_id':'s2','batch_id':'batch2'})
    with pytest.raises(ValueError,match='holdout is sealed'):
        import_manual(repo,f,returned,c,s,replace(rec,sample_id='s2'),tmp_path)
    repo.close()


def test_historical_source_cannot_be_laundered_as_prospective(tmp_path):
    from optomind_optics.harness.tools.measured_data_adapter import MeasuredObservationAdapter
    repo,p,f,r,c,s,rec=fixture(tmp_path)
    historical=MeasuredObservationAdapter(c,s,(rec,),tmp_path,accessible_split='train').read('reading',run_id='historical')
    repo.commit([historical],task_ref=p.task,evidence_cutoff='old source')
    with pytest.raises(ValueError,match='historical evidence'):
        import_manual(repo,f,r,c,s,rec,tmp_path)
    repo.close()


def test_budget_and_nested_immutability(tmp_path):
    repo,p,f,r,c,s,rec=fixture(tmp_path)
    with pytest.raises(ValueError,match='resource bound'):
        ProspectiveProtocol.model_validate({**p.model_dump(mode='json'),'repeats':3})
    with pytest.raises(Exception):
        p.samples[0]=('other','batch')
    assert len(human_protocol(p))>500
    repo.close()
