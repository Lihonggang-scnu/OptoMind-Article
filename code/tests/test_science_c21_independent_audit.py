"""Independent adversarial consumer checks, synthetic fixtures only."""
from dataclasses import replace
import json
import io
import numpy as np
import pytest

from test_science_c21_holdout_audit import setup
from optomind_optics.harness.science.contracts import Observation, PredictionSpec, canonical_hash
from optomind_optics.harness.science.prediction import compare_bound_observation
from optomind_optics.harness.tools.external_measurement import import_manual
from optomind_optics.harness.tools.manual_audit import audit_manual_holdout, audit_manual_development
from optomind_optics.harness.tools.measured_data_adapter import MeasuredObservationAdapter


def test_rehashed_cached_holdout_cannot_change_verdict_or_physical_scope(tmp_path):
    repo,p,f,r,c,s,record,audit=setup(tmp_path)
    try:
        before=repo.latest_snapshot().snapshot_id
        output=tmp_path/'audit.json'
        report=audit_manual_holdout(repo,f,r,c,s,record,tmp_path,audit,output,review_report=tmp_path/'external_report.txt')
        report['V4_accepted']=True
        report['prediction_verdict']['state']='supported'
        report['report_hash']=canonical_hash({k:v for k,v in report.items() if k!='report_hash'})
        output.write_text(json.dumps(report))
        with pytest.raises(ValueError,match='identity changed'):
            audit_manual_holdout(repo,f,r,c,s,record,tmp_path,audit,output,review_report=tmp_path/'external_report.txt')
        assert repo.latest_snapshot().snapshot_id==before
    finally:repo.close()


@pytest.mark.parametrize('existing',['not-json','{"other":"old output"}'])
def test_development_output_conflict_precedes_canonical_quality_revision(tmp_path,existing):
    repo,p,f,r,c,s,record,audit=setup(tmp_path)
    try:
        r=r.model_copy(update={'sample_id':'s1','batch_id':'batch1'})
        record=replace(record,sample_id='s1')
        audit=audit.model_copy(update={'sample_id':'s1','batch_id':'batch1','independent_scope':'same_sample_new_conditions'})
        imported=import_manual(repo,f,r,c,s,record,tmp_path)
        before=repo.latest_snapshot().snapshot_id
        output=tmp_path/'existing.json';output.write_text(existing)
        with pytest.raises(ValueError):
            audit_manual_development(repo,f,r,c,s,record,tmp_path,audit,review_report=tmp_path/'external_report.txt',output=output)
        assert repo.latest_snapshot().snapshot_id==before and output.read_text()==existing
        assert imported['observation_ref']['revision']==1
    finally:repo.close()


@pytest.mark.parametrize('conditions,angle,expected',[
    ({'angle_deg':'0','polarization':'s'},0.,'supported'),
    ({'angle_deg':'60','polarization':'p'},60.,'not_evaluable'),
    ({'angle_deg':'0','polarization':'p'},0.,'not_evaluable'),
    ({'polarization':'s'},None,'not_evaluable'),
])
def test_explicit_physical_prediction_conditions_bind_raw_source(conditions,angle,expected):
    spec=PredictionSpec(object_id='p',source_run_id='fixture',raw_channel='R',units='1',statistic='simultaneous_envelope',
        tolerance_source='fixture',coordinate=(500.,),lower=(.4,),upper=(.6,),observable='reflectance',
        grid={'angle_deg':0.,'polarization':'s','coordinate_units':'nm'})
    block={'block_id':'raw','channel':'R','units':'1','coordinate':[500.],'coordinate_units':'nm','values':[.5]}
    if angle is not None:block['angle_deg']=angle
    observation=Observation(object_id='o',source_run_id='fixture',acquisition_id='fixture',binding={'conditions':conditions},
        raw_blocks=(block,),quality={'execution_status':'completed','numerical_valid':True,'noise_validated':True})
    assert compare_bound_observation(spec,observation).state==expected


def test_source_change_between_card_validation_and_parse_is_rejected(tmp_path,monkeypatch):
    repo,p,f,r,c,s,record,audit=setup(tmp_path)
    try:
        original=type(c).verify_files
        def race(card,root):
            original(card,root)
            (tmp_path/'raw.csv').write_text('500,0.9\n600,0.9\n')
        monkeypatch.setattr(type(c),'verify_files',race)
        with pytest.raises(ValueError,match='changed before parsing'):
            MeasuredObservationAdapter(c,s,(record,),tmp_path,accessible_split='audit')
    finally:repo.close()


def test_parser_reads_exact_hashed_bytes_even_if_path_changes(tmp_path,monkeypatch):
    repo,p,f,r,c,s,record,audit=setup(tmp_path)
    try:
        original=np.loadtxt
        called=[]
        def replace_path_after_capture(source,*args,**kwargs):
            assert isinstance(source,io.BytesIO)
            called.append(True)
            (tmp_path/'raw.csv').write_text('500,0.9\n600,0.9\n')
            return original(source,*args,**kwargs)
        monkeypatch.setattr(np,'loadtxt',replace_path_after_capture)
        adapter=MeasuredObservationAdapter(c,s,(record,),tmp_path,accessible_split='audit')
        assert called
        with pytest.raises(ValueError,match='modified'):
            adapter.read(record.action_id,run_id='fixture')
    finally:repo.close()
