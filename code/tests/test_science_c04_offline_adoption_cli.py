"""Official explicit pre-freeze task adoption; synthetic sources and effects only."""
import json
import pytest
from test_science_c04_prefreeze_task_adoption import brief
from optomind_optics.harness.science.open_research_cli import main,parse_offline_config,build_offline_loop
from optomind_optics.harness.science.prefreeze_task_adoption import prefreeze_task_ref
from optomind_optics.harness.science.repository import ScienceRepository
from optomind_optics.harness.science.contracts import Observation


def inputs(tmp_path):
    payload={'family':'F0','config':{'run_id':'prefreeze-cli','max_iterations':1,'unknown_exploration_limit':0,'fit_starts':2,'max_nfev':40}}
    cfg=parse_offline_config(payload)[0]
    source=brief(cfg).model_dump(mode='json')
    config=tmp_path/'config.json';config.write_text(json.dumps(payload))
    document=tmp_path/'brief.json';document.write_text(json.dumps(source))
    return payload,cfg,source,config,document


def test_official_preview_exposes_draft_and_actual_adopted_fields_without_output(tmp_path,capsys):
    payload,cfg,source,config,document=inputs(tmp_path)
    output=tmp_path/'preview'
    assert main(['--offline','--preview','--config',str(config),'--output',str(output)])==0
    before=json.loads(capsys.readouterr().out)
    assert before['draft_task_ref']==prefreeze_task_ref(cfg).model_dump(mode='json')
    assert before['task_adoption'] is None
    assert main(['--offline','--preview','--config',str(config),'--adoption-brief',str(document),'--adopt-task-claim','source-claim','--output',str(output)])==0
    after=json.loads(capsys.readouterr().out)
    assert after['draft_task_ref']==before['draft_task_ref']
    assert after['configuration']['objective_target']==.2
    assert after['configuration']['terminal_lower_nm']==80.
    assert after['task_adoption']['receipts']
    assert not output.exists() and json.loads(document.read_text())==source


def test_official_adoption_executes_new_frozen_objective_constraints_and_resumes(tmp_path,capsys):
    payload,cfg,source,config,document=inputs(tmp_path)
    output=tmp_path/'actual'
    assert main(['--offline','--config',str(config),'--adoption-brief',str(document),'--adopt-task-claim','source-claim','--output',str(output)])==0
    first=json.loads(capsys.readouterr().out)
    saved=json.loads((output/'OFFLINE_RUN_CONFIG.json').read_text())
    assert saved['task_adoption']=={'brief':source,'adopted_claim_ids':['source-claim']}
    repo=ScienceRepository(output/'science',read_only=True)
    try:
        objects=[repo.load(ref) for ref in repo.latest_snapshot().object_refs]
        adoption=next(o for o in objects if o.object_id=='prefreeze-cli:prefreeze-task-adoption')
        task=next(o for o in objects if o.object_id=='prefreeze-cli:task')
        commitment=next(o for o in objects if o.object_id=='prefreeze-cli:terminal-commit')
        assert task.objective['wavelength_nm']==600. and task.objective['target_R']==.2
        value=commitment.summary['qualified_values']['selected_thickness_nm']['value']
        assert 82.<=value<=178.
        observations=[o for o in objects if isinstance(o,Observation)]
        assert observations and all(repo.object_commit_sequence(adoption.ref())<repo.object_commit_sequence(o.ref()) for o in observations)
        assert all(r['evidence_type']=='source_author_claim_explicitly_adopted_not_measurement' for r in adoption.summary['receipts'])
    finally:repo.close()
    assert main(['--offline','--resume','--output',str(output)])==0
    assert json.loads(capsys.readouterr().out)['selected_result']==first['selected_result']


def test_invalid_or_stale_adoption_and_live_evidence_fail_before_output(tmp_path):
    payload,cfg,source,config,document=inputs(tmp_path)
    bad=[{**payload,'config':{**payload['config'],'budget':11.},'task_adoption':{'brief':source,'adopted_claim_ids':['source-claim']}},
         {**payload,'task_adoption':{'brief':source,'adopted_claim_ids':[]}},
         {**payload,'evidence_service':{'mode':'live'}},
         {**payload,'evidence_service':['bad']}]
    for i,value in enumerate(bad):
        output=tmp_path/f'invalid-{i}'
        with pytest.raises(ValueError):build_offline_loop(output,value)
        assert not output.exists()
