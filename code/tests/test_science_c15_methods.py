from dataclasses import asdict
import pytest
from optomind_optics.harness.science.contracts import ResearchTask
from optomind_optics.harness.science.repository import ScienceRepository
from optomind_optics.harness.science.method_assets import MethodAsset,MethodRegistry,consume_asset
from optomind_optics.harness.science.method_retriever import retrieve
from optomind_optics.harness.science.promotion import TransferEvaluation,promote
from optomind_optics.harness.science.trace_extractor import DecisionTrace,extract_asset,finite_domain_equivalent
from optomind_optics.harness.science.program_runner import ProgramRunner


def setup(tmp_path):
    repo=ScienceRepository(tmp_path); task=ResearchTask(object_id='task',source_run_id='run',question='q')
    repo.commit([task],task_ref=task.ref(),evidence_cutoff='0')
    asset=MethodAsset(object_id='policy',source_run_id='run',kind='policy',payload={'depth':1},state='task_validated',validation_scope=('original',),applicability_predicates={'material':'glass'})
    registry=MethodRegistry(repo);registry.put(asset,task_ref=task.ref(),evidence_cutoff='0')
    return repo,task,asset,registry


def test_registry_unknown_retirement_and_history(tmp_path):
    repo,task,asset,registry=setup(tmp_path)
    assert not retrieve(registry,{}).selected
    assert retrieve(registry,{'material':'glass'}).selected==(asset.ref(),)
    retired=MethodAsset(**{**asset.model_dump(), 'revision':2,'content_hash':'','state':'retired'})
    registry.put(retired,task_ref=task.ref(),evidence_cutoff='1')
    assert not retrieve(registry,{'material':'glass'}).selected
    assert repo.load(asset.ref())==asset
    assert registry.rebuild_index()['policy'].state=='retired'
    repo.close()


def test_forward_promotion_and_negative_evidence(tmp_path):
    repo,task,asset,registry=setup(tmp_path)
    evaluation=TransferEvaluation(object_id='eval',source_run_id='run',asset_ref=asset.ref(),task_id='new',original_task_id='original',split='P',forward_run_id='real-forward',passed=True,cost=1,scope={'material':'glass'})
    repo.commit([evaluation],task_ref=task.ref(),evidence_cutoff='1')
    promoted,decision=promote(registry,asset,evaluation,task_ref=task.ref(),evidence_cutoff='1')
    assert promoted.state=='promoted'
    bad=TransferEvaluation(object_id='bad',source_run_id='run',asset_ref=promoted.ref(),task_id='bad-task',original_task_id='original',split='P',forward_run_id='bad-forward',passed=False,negative_transfer=True,cost=1,scope={'material':'glass'})
    repo.commit([bad],task_ref=task.ref(),evidence_cutoff='2')
    quarantined,_=promote(registry,promoted,bad,task_ref=task.ref(),evidence_cutoff='2')
    assert quarantined.negative_evidence==(bad.ref(),)
    assert quarantined.positive_evidence==(evaluation.ref(),)
    assert not retrieve(registry,{'material':'glass'}).selected


def test_program_executes_and_rechecks_budget(tmp_path):
    asset=MethodAsset(object_id='program',source_run_id='run',kind='program',payload={'program_id':'p','nodes':[{'node_id':'c','kind':'computation','estimate':[['cpu',2]]}]},state='task_validated')
    runner=ProgramRunner(tmp_path/'runner',gates={},providers={})
    with pytest.raises(ValueError,match='budget'):consume_asset(asset,facts={},capabilities={},limits={'cpu':1},runner=runner,prediction_commits={})
    receipt=consume_asset(asset,facts={},capabilities={},limits={'cpu':2},runner=runner,prediction_commits={})
    assert receipt.nodes['c']['state']=='completed'
    runner.close()


def test_trace_refuses_future_and_payload_injection(tmp_path):
    repo,task,asset,registry=setup(tmp_path)
    trace=DecisionTrace(decision_ref=task.ref(),prediction_ref=task.ref(),action_ref=asset.ref(),return_ref=asset.ref(),changed_decision_ref=asset.ref(),decision_sequence=1,permitted_resources=(),requested_resources=())
    with pytest.raises(ValueError,match='future'):extract_asset(repo,trace,kind='policy',payload={'depth':1},object_id='a',source_run_id='r',predicates={})
    with pytest.raises(ValueError):MethodAsset(object_id='a',source_run_id='r',kind='policy',payload={'depth':1,'target':'answer'})


def test_model_asset_runs_optical_solver_and_premises_do_not_merge():
    from optomind_optics.harness.science.model_ir import ModelIR,IRNode
    ir=ModelIR((IRNode('n','constant_nk',attributes=(('n',1.5),)),IRNode('s','stack',inputs=('n',),unit='nm'),IRNode('t','tmm',inputs=('s',))), 't')
    a=MethodAsset(object_id='a',source_run_id='r',kind='model_fragment',payload=asdict(ir),state='task_validated',applicability_predicates={'coherence':'coherent'})
    result=consume_asset(a,facts={'coherence':'coherent'},wavelengths_nm=[500,600])
    assert result==pytest.approx([.04,.04])
    b=MethodAsset(**{**a.model_dump(),'object_id':'b','content_hash':'','applicability_predicates':{'coherence':'incoherent'}})
    assert not finite_domain_equivalent(a,b,wavelengths_nm=[500,600])


def test_composition_rechecks_total_budget_and_trial():
    from optomind_optics.harness.science.method_assets import compose_program_assets,trial_asset
    asset=MethodAsset(object_id='p',source_run_id='r',kind='program',payload={'program_id':'p','nodes':[{'node_id':'c','kind':'computation','estimate':[['cpu',2]]}]},state='task_validated')
    with pytest.raises(ValueError,match='budget'):compose_program_assets([asset,asset],facts={},capabilities={},limits={'cpu':3},program_id='joint')
    plan=compose_program_assets([asset,asset],facts={},capabilities={},limits={'cpu':4},program_id='joint')
    assert dict(plan.resource_bounds)=={'cpu':4}
    assert not trial_asset(asset,facts={},evidence_check=lambda a:True,trial=lambda a,b:(True,b+1),budget=1)['allowed']


def test_extract_committed_policy_then_replay_controller(tmp_path):
    from optomind_optics.harness.science.contracts import DecisionContext,PredictionSpec,ExperimentProgram,Observation,PredictionEvaluation
    from optomind_optics.harness.science.terminal_problem import TerminalDecisionProblem
    from optomind_optics.harness.science.controller import MeasurementAction
    import numpy as np
    repo=ScienceRepository(tmp_path)
    task=ResearchTask(object_id='task',source_run_id='r',question='q')
    repo.commit([task],task_ref=task.ref(),evidence_cutoff='0')
    action=ExperimentProgram(object_id='action',source_run_id='r',protocol={'program_id':'p','nodes':[{'node_id':'c','kind':'computation'}]})
    decision=DecisionContext(object_id='before',source_run_id='r',summary={'experiment_policy':{'depth':1}})
    prediction=PredictionSpec(object_id='prediction',source_run_id='r',program_ref=action.ref(),observable='R')
    repo.commit([action,decision,prediction],task_ref=task.ref(),evidence_cutoff='1')
    observation=Observation(object_id='return',source_run_id='r',acquisition_id='acquired',raw_blocks=({'block_id':'raw','values':[.04]},))
    repo.commit([observation],task_ref=task.ref(),evidence_cutoff='2')
    evaluated=PredictionEvaluation(object_id='evaluated',source_run_id='r',prediction_ref=prediction.ref(),observation_ref=observation.ref(),metrics={'state':'fixture'})
    repo.commit([evaluated],task_ref=task.ref(),evidence_cutoff='2')
    changed=DecisionContext(object_id='after',source_run_id='r',observation_refs=(observation.ref(),))
    repo.commit([changed],task_ref=task.ref(),evidence_cutoff='3')
    trace=DecisionTrace(decision_ref=decision.ref(),prediction_ref=prediction.ref(),action_ref=action.ref(),return_ref=observation.ref(),changed_decision_ref=changed.ref(),decision_sequence=2,permitted_resources=(),requested_resources=(),evaluation_ref=evaluated.ref())
    extracted=extract_asset(repo,trace,kind='policy',payload={'depth':1},object_id='extracted',source_run_id='r',predicates={})
    with pytest.raises(ValueError,match='pre-action'):extract_asset(repo,trace,kind='policy',payload={'depth':2},object_id='injected',source_run_id='r',predicates={})
    asset=MethodAsset(**{**extracted.model_dump(),'state':'task_validated','content_hash':''})
    context={k:'x' for k in ('task','objective','model','sample','material','solver','fidelity','loss','split')}
    problem=TerminalDecisionProblem(('w0','w1'),('d0','d1'),np.array([[0,10],[10,0]]),context,'loss')
    measured=MeasurementAction('measure',('o0','o1'),((1,0),(0,1)),.1,1)
    result=consume_asset(asset,facts={},problem=problem,probabilities=(.5,.5),actions=(measured,),available_budget=3,final_validation_reserve=1)
    assert result.selected_actions==('measure',)


def test_dedup_normalizes_units_node_and_parameter_names():
    from optomind_optics.harness.science.model_ir import ModelIR,IRNode,ParameterSpec
    from optomind_optics.harness.science.trace_extractor import equivalence_key
    def make(prefix,unit,factor):
        ir=ModelIR((IRNode(prefix+'n','constant_nk',attributes=(('n',1.5),)),IRNode(prefix+'l','layer',inputs=(prefix+'n',),attributes=(('thickness','$'+prefix+'d'),),unit=unit),IRNode(prefix+'s','stack',inputs=(prefix+'l',prefix+'n'),unit='nm'),IRNode(prefix+'t','tmm',inputs=(prefix+'s',))),prefix+'t',parameters=(ParameterSpec(prefix+'d',10*factor,200*factor,100*factor,unit),))
        return MethodAsset(object_id=prefix,source_run_id='r',kind='model_fragment',payload=asdict(ir))
    assert equivalence_key(make('a','nm',1.))==equivalence_key(make('b','um',.001))


def test_single_task_success_not_promoted_and_unknown_scope_refused(tmp_path):
    repo,task,asset,registry=setup(tmp_path)
    ev=TransferEvaluation(object_id='e',source_run_id='r',asset_ref=asset.ref(),task_id='original',original_task_id='original',split='P',forward_run_id='forward',passed=True,cost=1,scope={'material':'glass'})
    repo.commit([ev],task_ref=task.ref(),evidence_cutoff='1')
    updated,_=promote(registry,asset,ev,task_ref=task.ref(),evidence_cutoff='1')
    assert updated.state=='task_validated' and updated.positive_evidence==(ev.ref(),)
    ev2=TransferEvaluation(object_id='e2',source_run_id='r',asset_ref=updated.ref(),task_id='new',original_task_id='original',split='P',forward_run_id='forward2',passed=True,cost=1,scope={})
    repo.commit([ev2],task_ref=task.ref(),evidence_cutoff='2')
    with pytest.raises(ValueError,match='scope'):promote(registry,updated,ev2,task_ref=task.ref(),evidence_cutoff='2')


def test_model_asset_preserves_oblique_angle_channel_and_ir_polarization():
    import numpy as np
    from tmm import coh_tmm
    from optomind_optics.harness.science.model_ir import ModelIR,IRNode
    for fraction in (0.,1.):
        ir=ModelIR((IRNode('n','constant_nk',attributes=(('n',1.5),)),IRNode('s','stack',inputs=('n',),unit='nm'),IRNode('t','tmm',inputs=('s',),attributes=(('s_fraction',fraction),))), 't')
        asset=MethodAsset(object_id='oblique',source_run_id='r',kind='model_fragment',payload=asdict(ir),state='task_validated')
        for channel in ('R','T'):
            result=consume_asset(asset,facts={},wavelengths_nm=[500.,600.],angle_deg=65.,channel=channel)
            independent=coh_tmm('s' if fraction else 'p',[1.,1.5],[np.inf,np.inf],np.deg2rad(65.),500.)[channel]
            assert result==pytest.approx([independent,independent])
        with pytest.raises(ValueError,match='unsupported model acquisition context'):
            consume_asset(asset,facts={},wavelengths_nm=[500.,600.],polarization='s')


def test_mutable_asset_payload_cannot_reuse_old_content_identity():
    from optomind_optics.harness.science.method_assets import compose_program_assets,trial_asset
    asset=MethodAsset(object_id='p',source_run_id='r',kind='program',payload={'program_id':'p','nodes':[{'node_id':'c','kind':'computation'}]},state='task_validated')
    asset.payload['nodes'][0]['node_id']='changed-after-hash'
    with pytest.raises(ValueError,match='content_hash mismatch'):
        consume_asset(asset,facts={},capabilities={},limits={},runner=None,prediction_commits={})
    with pytest.raises(ValueError,match='content_hash mismatch'):
        compose_program_assets([asset],facts={},capabilities={},limits={},program_id='composed')
    with pytest.raises(ValueError,match='content_hash mismatch'):
        trial_asset(asset,facts={},evidence_check=lambda _:True,trial=lambda a,b:(True,0.),budget=1.)


def test_committed_calibration_binds_exact_asset_task_conditions_and_composition(tmp_path):
    from optomind_optics.harness.science.method_assets import AssetCalibrationReceipt,compose_program_assets
    from optomind_optics.harness.science.contracts import Observation,canonical_hash
    repo=ScienceRepository(tmp_path/'repo')
    task=ResearchTask(object_id='task',source_run_id='r',question='target')
    other=ResearchTask(object_id='other-task',source_run_id='r',question='different target')
    asset=MethodAsset(object_id='p',source_run_id='r',kind='program',payload={'program_id':'p','nodes':[{'node_id':'c','kind':'computation'}]},state='task_validated',recalibration='recalibrate')
    evidence=Observation(object_id='calibration-observation',source_run_id='r',acquisition_id='calibration')
    repo.commit([task,other,asset,evidence],task_ref=task.ref(),evidence_cutoff='0')
    facts={'instrument':'one'}
    calibration=AssetCalibrationReceipt(object_id='calibration',source_run_id='r',asset_ref=asset.ref(),target_task_ref=task.ref(),conditions_hash=canonical_hash(facts),calibration_kind='recalibrate',status='passed',evidence_refs=(evidence.ref(),))
    repo.commit([calibration],task_ref=task.ref(),evidence_cutoff='1')
    runner=ProgramRunner(tmp_path/'runner',gates={},providers={})
    context=dict(capabilities={},limits={},runner=runner,prediction_commits={})
    with pytest.raises(ValueError,match='committed calibration'):
        consume_asset(asset,facts=facts,calibrated_asset_hash=asset.content_hash,**context)
    with pytest.raises(ValueError,match='committed calibration'):
        compose_program_assets([asset],facts=facts,capabilities={},limits={},program_id='joint')
    bound=dict(repository=repo,calibration_ref=calibration.ref(),target_task_ref=task.ref())
    assert consume_asset(asset,facts=facts,**context,**bound).state=='completed'
    assert compose_program_assets([asset],facts=facts,capabilities={},limits={},program_id='joint',repository=repo,calibration_refs={asset.content_hash:calibration.ref()},target_task_ref=task.ref()).program.program_id=='joint'
    for changed_facts,changed_task in (({'instrument':'two'},task.ref()),(facts,other.ref())):
        with pytest.raises(ValueError,match='does not bind'):
            consume_asset(asset,facts=changed_facts,**context,**{**bound,'target_task_ref':changed_task})
    changed=MethodAsset(**{**asset.model_dump(),'revision':2,'content_hash':''})
    repo.commit([changed],task_ref=task.ref(),evidence_cutoff='2')
    with pytest.raises(ValueError,match='does not bind'):
        consume_asset(changed,facts=facts,**context,**bound)
    failed=AssetCalibrationReceipt(**{**calibration.model_dump(),'object_id':'failed','content_hash':'','status':'failed'})
    repo.commit([failed],task_ref=task.ref(),evidence_cutoff='3')
    with pytest.raises(ValueError,match='does not bind'):
        consume_asset(asset,facts=facts,**context,**{**bound,'calibration_ref':failed.ref()})


def test_dependency_execution_needs_repository_and_resolvable_reference(tmp_path):
    from optomind_optics.harness.science.contracts import ObjectRef
    asset=MethodAsset(object_id='p',source_run_id='r',kind='program',payload={'program_id':'p','nodes':[{'node_id':'c','kind':'computation'}]},state='task_validated',dependencies=(ObjectRef(object_id='absent',content_hash='a'*64),))
    context=dict(capabilities={},limits={},runner=None,prediction_commits={})
    with pytest.raises(ValueError,match='dependencies require'):
        consume_asset(asset,facts={},**context)
    with pytest.raises(ValueError,match='uncommitted'):
        consume_asset(asset,facts={},repository=ScienceRepository(tmp_path),**context)


def test_promotion_refuses_mutated_negative_evaluation(tmp_path):
    repo,task,asset,registry=setup(tmp_path)
    evaluation=TransferEvaluation(object_id='negative',source_run_id='r',asset_ref=asset.ref(),task_id='new',original_task_id='original',split='P',forward_run_id='failed-forward',passed=False,negative_transfer=True,cost=1,scope={'material':'glass'})
    repo.commit([evaluation],task_ref=task.ref(),evidence_cutoff='1')
    forged=evaluation.model_copy(update={'passed':True,'negative_transfer':False})
    with pytest.raises(ValueError,match='content_hash mismatch'):
        promote(registry,asset,forged,task_ref=task.ref(),evidence_cutoff='2')


def test_calibration_receipt_parses_on_cold_process_without_registration_import():
    import subprocess,sys
    from optomind_optics.harness.science.method_assets import AssetCalibrationReceipt
    from optomind_optics.harness.science.contracts import ObjectRef,canonical_hash
    ref=ObjectRef(object_id='fixture',content_hash='a'*64)
    receipt=AssetCalibrationReceipt(object_id='calibration',source_run_id='r',asset_ref=ref,target_task_ref=ref,conditions_hash=canonical_hash({}),calibration_kind='refit',status='passed',evidence_refs=(ref,))
    code='import json,sys; from optomind_optics.harness.science.contracts import parse_object; obj=parse_object(json.loads(sys.stdin.read())); print(obj.schema_version)'
    child=subprocess.run([sys.executable,'-c',code],input=receipt.model_dump_json(),text=True,capture_output=True,check=True)
    assert child.stdout.strip()=='asset-calibration-receipt.v1'
