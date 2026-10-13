import hashlib
import pytest
from optomind_optics.harness.science.dataset_contracts import DatasetCard,DatasetSplitManifest
from optomind_optics.harness.tools.measured_data_adapter import MeasuredObservationAdapter,RecordedMeasurement


def test_whole_sample_split_and_no_unrecorded_actions(tmp_path):
    raw=tmp_path/'data.csv'; raw.write_text('500,0.5,0.5\n600,0.6,0.6\n')
    sha=hashlib.sha256(raw.read_bytes()).hexdigest()
    card=DatasetCard('fixture','https://example.org/fixture','CC0',(('data.csv',sha),),(('s1','batch1'),('s2','batch2')),
                     (('angle',None),),None,None,'raw_measured',('engineering_import_test',),('angle','calibration','uncertainty'))
    split=DatasetSplitManifest(card.content_hash,(('batch1','train'),('batch2','audit')))
    rows=tuple(RecordedMeasurement(s,s,'data.csv','R','dimensionless',(500,600),(.5,.6),(('angle',None),), value_column=i+1) for i,s in enumerate(('s1','s2')))
    adapter=MeasuredObservationAdapter(card,split,rows,tmp_path,accessible_split='train')
    obs=adapter.read('s1',run_id='test')
    assert not obs.quality['new_physical_measurement']
    assert not obs.quality['noise_validated']
    with pytest.raises(ValueError,match='not recorded'):
        adapter.read('s2',run_id='test')
    with pytest.raises(ValueError,match='unknown or mismatched'):
        adapter.read('s1',run_id='test',required_conditions={'angle':'0'})
    raw.write_text('changed')
    with pytest.raises(ValueError,match='modified'):
        card.verify_files(tmp_path)


def test_source_values_and_acquisition_split_cannot_be_relabelled(tmp_path):
    from dataclasses import replace
    raw=tmp_path/'raw.csv';raw.write_text('500,0.5\n600,0.6\n')
    card=DatasetCard('fixture','https://example.org/fixture','CC0',(('raw.csv',hashlib.sha256(raw.read_bytes()).hexdigest()),),
                     (('a','one'),('b','two')),(('angle_deg',None),),None,None,'raw_measured',('read',),('angle_deg',))
    split=DatasetSplitManifest(card.content_hash,(('one','train'),('two','audit')))
    record=RecordedMeasurement('a','a','raw.csv','R','dimensionless',(500,600),(.5,.6),(('angle_deg',None),))
    with pytest.raises(ValueError,match='source-bound'):
        MeasuredObservationAdapter(card,split,(replace(record,values=(.9,.9)),),tmp_path,accessible_split='train')
    with pytest.raises(ValueError,match='cannot cross'):
        MeasuredObservationAdapter(card,split,(record,replace(record,action_id='b',sample_id='b')),tmp_path,accessible_split='train')


def test_unknown_conditions_cannot_qualify_quantitative_inference(tmp_path):
    with pytest.raises(ValueError,match='physical measurement conditions'):
        DatasetCard('fixture','https://example.org/fixture','CC0',(('raw.csv','a'*64),),(('a','one'),),
                    (('angle_deg',None),),'claimed calibration','claimed noise','raw_measured',('quantitative_inference',),())


def test_wavelength_windows_cannot_split_one_acquisition(tmp_path):
    raw = tmp_path / 'raw.csv'
    raw.write_text('500,0.5\n600,0.6\n')
    card = DatasetCard('fixture', 'https://example.org/fixture', 'CC0',
        (('raw.csv', hashlib.sha256(raw.read_bytes()).hexdigest()),),
        (('a', 'one'), ('b', 'two')), (('angle_deg', None),), None, None,
        'raw_measured', ('read',), ('angle_deg',))
    split = DatasetSplitManifest(card.content_hash, (('one', 'train'), ('two', 'audit')))
    first = RecordedMeasurement('a', 'a', 'raw.csv', 'R', 'dimensionless',
        (500,), (.5,), (('angle_deg', None),), max_rows=1)
    second = RecordedMeasurement('b', 'b', 'raw.csv', 'R', 'dimensionless',
        (600,), (.6,), (('angle_deg', None),), skip_rows=1)
    with pytest.raises(ValueError, match='cannot cross'):
        MeasuredObservationAdapter(card, split, (first, second), tmp_path, accessible_split='train')
