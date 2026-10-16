import copy
import hashlib
import json
from pathlib import Path
import pytest
from focused_research import task_sheet as ts, task_sheet_recovery as rec, search_stage as st
FIXTURES=json.loads((Path(__file__).parent/'fixtures/t029_recorded_sheets.json').read_text())

@pytest.mark.parametrize('fixture',FIXTURES,ids=[x['case_id'] for x in FIXTURES])
def test_recorded_second_response_recovers_without_model_or_metric_edits(fixture):
    original=copy.deepcopy(fixture['analysis'])
    sheet,audit=rec.prepare_recovered_analysis(fixture['case_id'],fixture['question'],original)
    assert original==fixture['analysis']
    assert [{k: v for k, v in g.items() if k != 'quote_status'}
            for g in sheet['subgoals']] == original['subgoals']
    assert sheet['explicit_constraints']==original['explicit_constraints']
    assert sheet['original_question']==fixture['question']
    assert sheet['readiness']=='ready'
    assert ts.validate_adapter(ts.adapt(sheet))
    if fixture['case_id']==rec.AR:
        assert len(sheet['subgoals'])==2
        assert audit['report_only_capability_reclassification']['source_rows']
        assert sheet['application_intents'][-1]['id']=='ReportTIntoSilicon'


def test_recovery_requires_exact_source_question_and_keeps_other_unsupported():
    f=next(x for x in FIXTURES if x['case_id']==rec.AR)
    with pytest.raises(ValueError,match='pinned'):
        rec.prepare_recovered_analysis(f['case_id'],f['question']+' changed',f['analysis'])
    a=copy.deepcopy(f['analysis'])
    a['unsupported_requirements'].append({'id':'UR_OTHER','requirement':'Unsupported arbitrary requirement','original_span':'硅作为半无限出射基底','reason':'A distinct unsupported requirement must not be silently dropped.'})
    sheet,audit=rec.prepare_recovered_analysis(f['case_id'],f['question'],a)
    assert sheet['readiness']=='needs_revision'
    assert [r['id'] for r in sheet['unsupported_requirements']]==['UR_OTHER']


def test_recover_file_binds_saved_response_and_rejects_changed_source(tmp_path):
    f=FIXTURES[0];p=tmp_path/'old/planning.json';p.parent.mkdir()
    data={'rounds':[{'round':2,'raw_response':json.dumps(f['analysis'],ensure_ascii=False)}]}
    p.write_text(json.dumps(data));digest=hashlib.sha256(p.read_bytes()).hexdigest()
    b=rec.recover_planning(f['case_id'],f['question'],p,expected_sha256=digest,output=tmp_path/'new')
    assert b['new_model_calls']==0 and not b['physics_executed']
    assert b['task_sheet_sha256']==st.digest(st.load(tmp_path/'new/task_sheet.json'))
    assert 'normalization_audit.json' in st.load(tmp_path/'new/manifest.json')
    with pytest.raises(ValueError,match='hash mismatch'):
        rec.recover_planning(f['case_id'],f['question'],p,expected_sha256='0'*64,output=tmp_path/'bad')
    assert not (tmp_path/'bad').exists()
