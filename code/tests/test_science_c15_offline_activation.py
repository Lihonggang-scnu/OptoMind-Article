import json
import hashlib
import pytest
from optomind_optics.harness.science.contracts import ResearchTask, canonical_hash
from optomind_optics.harness.science.repository import ScienceRepository
from optomind_optics.harness.science.method_assets import MethodAsset,MethodRegistry
from optomind_optics.harness.science.open_research_cli import main,build_offline_loop


def library(tmp_path):
    root=tmp_path/'library'
    repo=ScienceRepository(root)
    task=ResearchTask(object_id='source-task',source_run_id='source',question='source')
    repo.commit([task],task_ref=task.ref(),evidence_cutoff='0')
    asset=MethodAsset(object_id='policy',source_run_id='source',kind='policy',payload={'depth':1,'action_ids':['new_independent_session']},state='task_validated',validation_scope=('source-task',),applicability_predicates={'domain':'planar'})
    registry=MethodRegistry(repo);registry.put(asset,task_ref=task.ref(),evidence_cutoff='1')
    digest=canonical_hash([ref.model_dump(mode='json') for ref in registry.snapshot().asset_refs])
    repo.close()
    return {'repository_root':str(root),'snapshot_id':digest,'facts':{'domain':'planar'},'allow_task_validated':True}


def hashes(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


def test_official_preview_validates_frozen_local_activation_without_writes(tmp_path,capsys):
    config={'method_library':library(tmp_path),'experiment_policy':{'depth':1,'action_ids':['new_independent_session']}}
    path=tmp_path/'config.json';path.write_text(json.dumps(config))
    before=hashes(tmp_path/'library')
    output=tmp_path/'preview-output'
    assert main(['--offline','--preview','--config',str(path),'--output',str(output)])==0
    preview=json.loads(capsys.readouterr().out)
    assert preview['activation']['method_library']['snapshot_id']==config['method_library']['snapshot_id']
    assert preview['activation']['experiment_policy']['action_ids']==['new_independent_session']
    assert not output.exists() and hashes(tmp_path/'library')==before


def test_stale_or_malformed_activation_fails_before_run_files(tmp_path):
    lib=library(tmp_path)
    before=hashes(tmp_path/'library')
    bad=[{'method_library':{**lib,'snapshot_id':'0'*64}},
         {'experiment_policy':{'depth':1,'ground_truth':3}},
         {'method_library':{**lib,'allow_task_validated':'false'}},
         {'method_library':{**lib,'repository_root':str(tmp_path/'missing')}}]
    for i,payload in enumerate(bad):
        path=tmp_path/f'bad-{i}.json';path.write_text(json.dumps(payload))
        output=tmp_path/f'run-{i}'
        with pytest.raises(ValueError):main(['--offline','--config',str(path),'--output',str(output)])
        assert not output.exists()
    assert hashes(tmp_path/'library')==before


def test_official_cli_executes_library_policy_and_exact_resume(tmp_path,capsys):
    lib=library(tmp_path)
    config={'family':'F0','world_seed':13,'method_library':lib,
            'experiment_policy':{'depth':1,'action_ids':['new_independent_session']},
            'config':{'run_id':'cli-method-target','measurement_std':.004,'objective_target':.1,
                      'action_loss_cost':0.,'design_thicknesses_nm':[135+i*.1 for i in range(121)],
                      'max_iterations':1,'unknown_exploration_limit':0}}
    path=tmp_path/'config.json';path.write_text(json.dumps(config))
    output=tmp_path/'actual-run'
    assert main(['--offline','--config',str(path),'--output',str(output)])==0
    first=json.loads(capsys.readouterr().out)
    repo=ScienceRepository(output/'science',read_only=True)
    objects=[repo.load(ref) for ref in repo.latest_snapshot().object_refs]
    decision=next(o for o in objects if o.object_id=='cli-method-target:iteration:0:decision')
    protocol=next(o for o in objects if o.object_id=='cli-method-target:protocol')
    assert protocol.summary['method_library_snapshot']==lib['snapshot_id']
    consumed=[o for o in objects if o.object_id.startswith('cli-method-target:policy-consumption:')]
    assert consumed and all(o.summary['receipt']['status']=='executed' for o in consumed)
    repo.close()
    assert main(['--offline','--resume','--output',str(output)])==0
    second=json.loads(capsys.readouterr().out)
    assert first['selected_result']==second['selected_result']


def test_manifest_command_exports_exact_pin_without_source_changes(tmp_path,capsys):
    from optomind_optics.harness.science.offline_method_config import main as manifest
    config=library(tmp_path)
    before=hashes(tmp_path/'library')
    assert manifest(['--repository',config['repository_root']])==0
    result=json.loads(capsys.readouterr().out)
    assert result['snapshot_id']==config['snapshot_id'] and len(result['asset_refs'])==1
    assert hashes(tmp_path/'library')==before


def test_profile_config_json_is_frozen_and_validated_before_output(tmp_path):
    from optomind_optics.harness.science.open_research_cli import parse_offline_config
    cfg,_,_,_=parse_offline_config({'config':{'profile_config':{'starts':1,'grid_points':3}}})
    assert cfg.profile_config==(('grid_points',3),('starts',1))
    with pytest.raises(ValueError):
        build_offline_loop(tmp_path/'invalid-profile',{'config':{'profile_config':{'grid_points':2}}})
    assert not (tmp_path/'invalid-profile').exists()
