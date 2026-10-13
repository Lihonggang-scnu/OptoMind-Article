import hashlib
import json
import pytest
from optomind_optics.harness.science.contracts import ResearchTask
from optomind_optics.harness.science.method_assets import MethodAsset,MethodRegistry
from optomind_optics.harness.science.repository import ScienceRepository
from optomind_optics.harness.science.archive_planner import plan_archive,apply_archive,rebuild_and_verify
from optomind_optics.harness.science.release_manifest import ReleaseManifest,ReleaseManager,SnapshotArtifact
from optomind_optics.harness.science.lifetime_cli import main


def release(root, release_id='r1', revision='abc'):
    root.mkdir(parents=True,exist_ok=True)
    artifacts=[]
    for role in ('methods','policy','data','tools','reproduction','human_delivery','coverage'):
        path=root/f'{role}.json'
        path.write_text(json.dumps({'role':role,'engineering_regression':'passed' if role=='coverage' else None}))
        artifacts.append(dict(role=role,path=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),license_id='CC0-1.0'))
    return ReleaseManifest(release_id=release_id,products=tuple(dict(product=p,revision=revision,schema_version='v1',entrypoints=('legacy',),source_sha256='a'*64) for p in ('Article','Review','VeriTMM')),compatibility=(dict(versions={p:revision for p in ('Article','Review','VeriTMM')},modes=('legacy','shadow','active'),regression_receipt='coverage.json'),),artifacts=tuple(artifacts),allowed_modes=('legacy','shadow','active'),engineering_status='passed',real_quality_status='not_run',unsupported_claims=('Longitudinal real-world efficacy not evaluated.',))


def test_release_modes_cas_recovery_rollback_and_corruption(tmp_path):
    manifest=release(tmp_path/'bundle')
    manager=ReleaseManager(tmp_path/'state')
    with pytest.raises(ValueError,match='follow shadow'): manager.preview(manifest,tmp_path/'bundle','active')
    assert manager.current() is None
    first=manager.activate(manifest,tmp_path/'bundle','legacy',expected_sequence=0,reason='legacy baseline')
    with pytest.raises(ValueError,match='sequence conflict'):
        manager.activate(manifest,tmp_path/'bundle','shadow',expected_sequence=0,reason='stale')
    manager.activate(manifest,tmp_path/'bundle','shadow',expected_sequence=1,reason='offline shadow')
    manager.activate(manifest,tmp_path/'bundle','active',expected_sequence=2,reason='local release selection')
    manager.close()
    manager=ReleaseManager(tmp_path/'state')
    assert manager.current()['mode']=='active'
    recovered=manager.rollback(first['sequence'],expected_sequence=3,reason='regression')
    assert recovered['mode']=='legacy' and recovered['rollback_of']==3
    assert len(manager.history())==4
    (tmp_path/'bundle'/'methods.json').write_text('corrupt')
    with pytest.raises(ValueError,match='checksum'):
        manager.rollback(2,expected_sequence=4,reason='corrupt rollback target')
    assert manager.current()['sequence']==4
    manager.close()


def test_release_rejects_version_mismatch_paths_rebinding_and_unsupported_mode(tmp_path):
    manifest=release(tmp_path/'bundle')
    data=manifest.model_dump(); data['products'][0]['revision']='unknown'
    mismatch=ReleaseManifest.model_validate(data)
    with pytest.raises(ValueError,match='compatibility'): mismatch.verify(tmp_path/'bundle','shadow')
    with pytest.raises(ValueError,match='inside bundle'):
        SnapshotArtifact(role='data',path='../outside',sha256='0'*64,license_id='CC0')
    manager=ReleaseManager(tmp_path/'state')
    manager.activate(manifest,tmp_path/'bundle','legacy',expected_sequence=0,reason='baseline')
    modified=manifest.model_copy(update={'unsupported_claims':('changed',)})
    with pytest.raises(ValueError,match='rebound'):
        manager.activate(modified,tmp_path/'bundle','shadow',expected_sequence=1,reason='attempt rebinding')
    limited=manifest.model_copy(update={'allowed_modes':('legacy',)})
    with pytest.raises(ValueError,match='not allowed'): limited.verify(tmp_path/'bundle','shadow')
    manager.close()


def test_release_verifies_all_artifacts_and_explicit_engineering_gate(tmp_path):
    manifest=release(tmp_path/'bundle')
    partial=manifest.model_copy(update={'engineering_status':'partial'})
    with pytest.raises(ValueError,match='engineering'): partial.verify(tmp_path/'bundle','active')
    data=manifest.model_dump(); data['compatibility'][0]['regression_receipt']='unbundled.json'
    with pytest.raises(ValueError,match='not in verified'): ReleaseManifest.model_validate(data).verify(tmp_path/'bundle','legacy')
    (tmp_path/'bundle'/'tools.json').unlink()
    with pytest.raises(ValueError,match='missing'): manifest.verify(tmp_path/'bundle','shadow')


def registry(root):
    repo=ScienceRepository(root)
    task=ResearchTask(object_id='task',source_run_id='run',question='test')
    asset=MethodAsset(object_id='policy',source_run_id='run',kind='policy',payload={'depth':1},state='task_validated')
    original=repo.commit([task,asset],task_ref=task.ref(),evidence_cutoff='2026-10-12')
    return repo,MethodRegistry(repo),task,asset,original


def test_archive_retirement_preserves_old_versions_and_rebuild(tmp_path):
    repo,reg,task,asset,original=registry(tmp_path)
    plan=plan_archive(reg,retire_ids=('policy',))
    with pytest.raises(ValueError,match='evidence'):
        apply_archive(reg,plan,task_ref=task.ref(),evidence_cutoff='2026-10-12',evidence_refs=())
    apply_archive(reg,plan,task_ref=task.ref(),evidence_cutoff='2026-10-12',evidence_refs=(task.ref(),))
    assert repo.load(asset.ref()).state=='task_validated'
    assert reg.rebuild_index()['policy'].negative_evidence==()  # Administrative retirement is not a scientific counterexample.
    rebuilt=rebuild_and_verify(reg,historical_snapshot_ids=(original.snapshot_id,))
    assert rebuilt['inactive_ids']==['policy'] and rebuilt['active_ids']==[]
    assert rebuilt['historical_objects_verified'][original.snapshot_id]==2
    with pytest.raises(ValueError,match='changed'):
        apply_archive(reg,plan,task_ref=task.ref(),evidence_cutoff='2026-10-12',evidence_refs=(task.ref(),))
    repo.close()


def test_archive_index_fails_closed_on_corruption(tmp_path):
    repo,reg,task,asset,original=registry(tmp_path)
    (tmp_path/'objects'/f'{asset.content_hash}.json').write_text('{}')
    with pytest.raises((ValueError,KeyError)): rebuild_and_verify(reg)
    repo.close()


def test_release_and_archive_cli_consumers(tmp_path,capsys):
    bundle=tmp_path/'bundle'; manifest=release(bundle)
    manifest_path=tmp_path/'manifest.json'; manifest_path.write_text(manifest.model_dump_json())
    args=['--root',str(tmp_path/'state'),'--manifest',str(manifest_path),'--bundle',str(bundle),'--mode','legacy']
    main(['release-preview',*args]); assert json.loads(capsys.readouterr().out)['real_quality_status']=='not_run'
    main(['release-activate',*args,'--expected-sequence','0','--reason','test'])
    assert json.loads(capsys.readouterr().out)['sequence']==1
    main(['release-history','--root',str(tmp_path/'state')]); assert len(json.loads(capsys.readouterr().out))==1
    repo,reg,task,asset,original=registry(tmp_path/'repo'); repo.close()
    main(['rebuild-index','--repository',str(tmp_path/'repo'),'--historical-snapshot',original.snapshot_id])
    assert json.loads(capsys.readouterr().out)['active_ids']==['policy']
    main(['archive-preview','--repository',str(tmp_path/'repo'),'--retire','policy'])
    assert json.loads(capsys.readouterr().out)['decisions'][0]['action']=='retired'


def make_factory(root, release_root=None):
    from optomind_optics.harness.article_pipeline_factory import ProductionArticlePipelineFactory,ProductionAssemblyConfig
    from optomind_optics.harness.article_proposals import ArticleCompilationAuthority
    import test_article_pipeline_factory as legacy
    config=ProductionAssemblyConfig(work_root=str(root/'work'),release_state_root=str(release_root) if release_root else None,release_runtime_versions={p:'abc' for p in ('Article','Review','VeriTMM')} if release_root else {})
    return ProductionArticlePipelineFactory(request=legacy._request(str(root/'run')),authority=ArticleCompilationAuthority(legacy.KEY),config=config)


def assemble_real(factory, calls, **options):
    import test_article_pipeline_factory as legacy
    from optomind_optics.harness.problem_analyzer import analyze_optical_problem
    analysis_client=legacy._FakeClient(legacy._analysis().model_dump(mode='json'))
    analysis=analyze_optical_problem(legacy.QUESTION,client=analysis_client,force_mock=None).analysis
    return factory.assemble(problem_analyzer_client=analysis_client,
        strategy_client=legacy._FakeClient(legacy._canned_strategy(analysis.problem_id,('route_01',)).model_dump(mode='json')),
        director_client=legacy._FakeClient(legacy._canned_director_plan(analysis)),
        task_compiler=legacy._FakeTaskCompiler([legacy.build_dev_optical_design_task('DEV02')]),
        planner_client=legacy._FakeClient(legacy._canned_planner_rows(('R01',))),
        harness_factory=legacy._harness_factory(calls),**options)


def test_production_factory_shadow_executes_only_legacy_with_pure_comparison(tmp_path, fake_runtime):
    import numpy as np
    from optomind_optics.harness.science.terminal_problem import TerminalDecisionProblem
    from optomind_optics.harness.science.controller import MeasurementAction
    manifest=release(tmp_path/'bundle')
    manager=ReleaseManager(tmp_path/'state')
    manager.activate(manifest,tmp_path/'bundle','shadow',expected_sequence=0,reason='shadow')
    class ForbiddenPolicy:
        def plan(self,*a): raise AssertionError('shadow must not plan through execution policy')
        def execute(self,*a): raise AssertionError('shadow must not execute')
        def finalize(self,*a): raise AssertionError('shadow must not publish')
    context={k:'x' for k in ('task','objective','model','sample','material','solver','fidelity','loss','split')}
    spec=dict(problem=TerminalDecisionProblem(('w0','w1'),('d0','d1'),np.array([[0,10],[10,0]]),context,'loss'),probabilities=(.5,.5),actions=(MeasurementAction('a',('o0','o1'),((1,0),(0,1)),.1,1),),available_budget=10,final_validation_reserve=1)
    legacy_calls=[]; shadow_calls=[]
    baseline=assemble_real(make_factory(tmp_path/'baseline'),legacy_calls).run()
    assembly=assemble_real(make_factory(tmp_path/'shadow',tmp_path/'state'),shadow_calls,science_policy=ForbiddenPolicy(),science_shadow_spec=spec)
    result=assembly.run()
    assert assembly.pipeline.science_policy is None
    assert len(legacy_calls)==len(shadow_calls)==1
    assert result.status==baseline.status and result.execution_count==baseline.execution_count
    assert [r.status for r in result.receipts]==[r.status for r in baseline.receipts]
    comparison=json.loads((tmp_path/'shadow'/'run'/'SCIENCE_SHADOW_COMPARISON.json').read_text())
    assert comparison['state']=='evaluated' and comparison['external_actions']==0
    assert comparison['candidate_published'] is False
    assert comparison['legacy_result_id']==result.result_id
    assert not (tmp_path/'shadow'/'run'/'SELECTED_RESEARCH_RESULT.json').exists()
    assert assembly.resume().result_id==result.result_id
    assert len(shadow_calls)==1
    manager.close()


def test_active_factory_dispatch_and_stale_release_stop_before_effects(tmp_path, fake_runtime):
    manifest=release(tmp_path/'bundle')
    manager=ReleaseManager(tmp_path/'state')
    manager.activate(manifest,tmp_path/'bundle','shadow',expected_sequence=0,reason='shadow')
    manager.activate(manifest,tmp_path/'bundle','active',expected_sequence=1,reason='active')
    from optomind_optics.harness.article_pipeline_factory import PipelineAssemblyIntegrityError
    factory=make_factory(tmp_path/'active',tmp_path/'state')
    with pytest.raises(PipelineAssemblyIntegrityError,match='explicit scientific'):
        factory.assemble()
    class Policy:
        def __init__(self): self.calls=[]; self.coordinator=None
        def plan(self,requests,work_dir):
            self.calls.append('plan'); return [r.request_id for r in requests]
        def execute(self,request):
            self.calls.append('execute'); return self.coordinator.execute(request)
        def finalize(self,executions,work_dir): self.calls.append('finalize')
    policy=Policy();calls=[]
    assembly=assemble_real(factory,calls,science_policy=policy)
    policy.coordinator=assembly.coordinator
    result=assembly.run()
    assert result.execution_count==1 and policy.calls==['plan','execute','finalize']
    stale=assemble_real(make_factory(tmp_path/'stale',tmp_path/'state'),[],science_policy=Policy())
    manager.rollback(1,expected_sequence=2,reason='rollback to shadow')
    with pytest.raises(ValueError,match='changed after assembly'): stale.run()
    with pytest.raises(ValueError,match='changed after assembly'): assembly.resume()
    manager.close()


def test_factory_rejects_incompatible_runtime_before_execution(tmp_path):
    from optomind_optics.harness.science.release_manifest import load_release_selection
    manifest=release(tmp_path/'bundle'); manager=ReleaseManager(tmp_path/'state')
    manager.activate(manifest,tmp_path/'bundle','legacy',expected_sequence=0,reason='legacy')
    with pytest.raises(ValueError,match='running product versions'):
        load_release_selection(tmp_path/'state',runtime_versions={'Article':'wrong'})
    manager.close()


def test_counterexample_narrowing_needs_revalidation_and_preserves_old_revision(tmp_path):
    from optomind_optics.harness.science.archive_planner import narrow_after_counterexample
    from optomind_optics.harness.science.promotion import TransferEvaluation
    repo,reg,task,asset,original=registry(tmp_path)
    negative=TransferEvaluation(object_id='negative',source_run_id='run',asset_ref=asset.ref(),task_id='new',original_task_id='old',split='P',forward_run_id='actual-fixture',passed=False,negative_transfer=True,cost=1,scope={})
    repo.commit([negative],task_ref=task.ref(),evidence_cutoff='1')
    narrowed=narrow_after_counterexample(reg,asset.ref(),{'material':'glass'},evaluation_ref=negative.ref(),task_ref=task.ref(),evidence_cutoff='2')
    assert narrowed.state=='proposed' and narrowed.applicability_predicates=={'material':'glass'}
    assert repo.load(asset.ref()).state=='task_validated'
    assert rebuild_and_verify(reg)['active_ids']==[]
    with pytest.raises(ValueError,match='exact method'):
        narrow_after_counterexample(reg,narrowed.ref(),{},evaluation_ref=negative.ref(),task_ref=task.ref(),evidence_cutoff='3')
    repo.close()


def test_quarantine_requires_specific_negative_transfer_evidence(tmp_path):
    from optomind_optics.harness.science.promotion import TransferEvaluation
    repo,reg,task,asset,original=registry(tmp_path)
    plan=plan_archive(reg,negative_transfer_ids=('policy',))
    with pytest.raises(ValueError,match='negative transfer'):
        apply_archive(reg,plan,task_ref=task.ref(),evidence_cutoff='1',evidence_refs=(task.ref(),))
    evaluation=TransferEvaluation(object_id='negative',source_run_id='run',asset_ref=asset.ref(),task_id='new',original_task_id='old',split='P',forward_run_id='forward',passed=False,cost=1,negative_transfer=True,scope={})
    repo.commit([evaluation],task_ref=task.ref(),evidence_cutoff='1')
    plan=plan_archive(reg,negative_transfer_ids=('policy',))
    apply_archive(reg,plan,task_ref=task.ref(),evidence_cutoff='2',evidence_refs=(evaluation.ref(),))
    assert reg.rebuild_index()['policy'].state=='quarantined'
    repo.close()


def test_supported_integration_forwards_release_and_shadow_without_changing_defaults(tmp_path):
    import test_article_pipeline_integration as integration
    from optomind_optics.harness.article_pipeline_integration import ArticleIntegrationOptions,execute_article_pipeline_integration,AUTHORITY_ENVIRONMENT_VARIABLE
    context={k:'x' for k in ('task','objective','model','sample','material','solver','fidelity','loss','split')}
    spec={'problem':{'world_ids':['w'],'design_ids':['d'],'losses':[[1]],'context':context,'units':'loss'},'probabilities':[1.],'actions':[],'available_budget':2,'final_validation_reserve':1}
    integration._Factory.instances=[];integration._Factory.calls=[];integration._Factory.result=integration._result()
    options=ArticleIntegrationOptions(question=integration.QUESTION,run_id='run-integration',work_dir=str(tmp_path/'run'),execution_root=str(tmp_path/'work'),release_state_root=str(tmp_path/'release'),release_runtime_versions={p:'abc' for p in ('Article','Review','VeriTMM')},science_shadow_spec=spec)
    result=execute_article_pipeline_integration(options,environment={AUTHORITY_ENVIRONMENT_VARIABLE:'fixture-authority'},factory_type=integration._Factory,synthesizer_factory=integration._Synthesizer,online_client_factory=integration._OnlineClient,clock=iter((0.,1.)).__next__)
    captured=integration._Factory.instances[0]
    assert captured.config.release_state_root==str(tmp_path/'release')
    assert captured.config.release_runtime_versions==options.release_runtime_versions
    assert captured.assemble_kwargs['science_shadow_spec']['problem'].world_ids==('w',)
    assert result.result==integration._Factory.result


def test_compatibility_cli_accepts_release_flags_and_bounded_shadow_data():
    import importlib.util
    from pathlib import Path
    from optomind_optics.harness.science.release_manifest import compile_shadow_spec
    path=Path(__file__).parents[1]/'scripts'/'run_article_pipeline_integration.py'
    spec=importlib.util.spec_from_file_location('c22_pipeline_cli',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    args=module.build_parser().parse_args(['--question','test','--run-id','run','--work-dir','run','--execution-root','work','--release-state-root','releases','--release-runtime-versions','versions.json','--science-shadow-spec','shadow.json'])
    assert str(args.release_state_root)=='releases' and str(args.science_shadow_spec)=='shadow.json'
    with pytest.raises(ValueError,match='fields'):
        compile_shadow_spec({'code':'untrusted'})


@pytest.fixture
def fake_runtime(monkeypatch):
    # Explicit test-only resolver injection. Production CLI/config has no bypass.
    from optomind_optics.harness.science import release_manifest
    identities={p:{'revision':'abc','source_sha256':'a'*64,'package_version':None,'module_origin':'fixture'} for p in ('Article','Review','VeriTMM')}
    monkeypatch.setattr(release_manifest,'resolve_runtime_identities',lambda:identities)
    return identities


def test_caller_version_json_cannot_override_actual_source_identity(tmp_path,fake_runtime):
    from optomind_optics.harness.science.release_manifest import load_release_selection
    manifest=release(tmp_path/'bundle');manager=ReleaseManager(tmp_path/'state')
    manager.activate(manifest,tmp_path/'bundle','legacy',expected_sequence=0,reason='fixture')
    claimed={p:'abc' for p in ('Article','Review','VeriTMM')}
    fake_runtime['Article']['revision']='actual-other-revision'
    with pytest.raises(ValueError,match='actual runtime revision/source'):
        load_release_selection(tmp_path/'state',runtime_versions=claimed)
    fake_runtime['Article']['revision']='abc';fake_runtime['Article']['source_sha256']='b'*64
    with pytest.raises(ValueError,match='actual runtime revision/source'):
        load_release_selection(tmp_path/'state',runtime_versions=claimed)
    manager.close()


def test_release_inspection_preserves_database_and_sidecar_inventory(tmp_path,fake_runtime):
    from optomind_optics.harness.science.release_manifest import load_release_selection
    manifest=release(tmp_path/'bundle');manager=ReleaseManager(tmp_path/'state')
    manager.activate(manifest,tmp_path/'bundle','legacy',expected_sequence=0,reason='fixture');manager.close()
    def state():return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (tmp_path/'state').iterdir() if p.is_file()}
    before=state()
    assert load_release_selection(tmp_path/'state')['runtime_identities']==fake_runtime
    assert load_release_selection(tmp_path/'state')['runtime_versions']=={p:'abc' for p in fake_runtime}
    assert state()==before


def test_actual_runtime_resolver_uses_real_modules_not_test_injection():
    import os,subprocess,sys
    from pathlib import Path
    article=Path(__file__).resolve().parents[2]
    env=dict(os.environ)
    env['PYTHONPATH']=os.pathsep.join([str(article/'code'),str(article.parent/'OptoMInd-Review'),str(article.parent/'VeriTMM')])
    code='import json;from optomind_optics.harness.science.release_manifest import resolve_runtime_identities;print(json.dumps(resolve_runtime_identities()))'
    result=subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True,check=True)
    identities=json.loads(result.stdout)
    assert set(identities)=={'Article','Review','VeriTMM'}
    for identity in identities.values():
        assert len(identity['source_sha256'])==64 and identity['fingerprinted_files']>0
        assert Path(identity['module_origin']).is_file()
        actual=subprocess.check_output(['git','-C',identity['package_root'],'rev-parse','HEAD'],text=True).strip()
        assert identity['revision']==actual


@pytest.mark.parametrize('command', ['archive-preview', 'rebuild-index'])
def test_readonly_archive_cli_does_not_create_source_sqlite_sidecars(tmp_path, capsys, command):
    from optomind_optics.harness.science.lifetime_cli import main
    repo, _, _, _, _ = registry(tmp_path/'source')
    repo.close()
    def inventory():
        return {str(p.relative_to(tmp_path/'source')):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (tmp_path/'source').rglob('*') if p.is_file()}
    before = inventory()
    assert main([command, '--repository', str(tmp_path/'source')]) == 0
    assert json.loads(capsys.readouterr().out)
    assert inventory() == before


@pytest.mark.parametrize('command', ['archive-preview', 'rebuild-index'])
def test_readonly_archive_cli_missing_repository_creates_nothing(tmp_path, command):
    import sqlite3
    from optomind_optics.harness.science.lifetime_cli import main
    missing = tmp_path/'absent-source'
    with pytest.raises(sqlite3.OperationalError):
        main([command, '--repository', str(missing)])
    assert not missing.exists()
