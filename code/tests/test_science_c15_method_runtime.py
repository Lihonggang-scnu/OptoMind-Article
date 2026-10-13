from dataclasses import asdict
import pytest
from optomind_optics.harness.science.contracts import ResearchTask
from optomind_optics.harness.science.repository import ScienceRepository
from optomind_optics.harness.science.method_assets import MethodAsset, MethodRegistry
from optomind_optics.harness.science.method_runtime import FrozenMethodProvider
from optomind_optics.harness.science.promotion import TransferEvaluation, promote
from optomind_optics.harness.science.program_runner import ProgramRunner
from test_science_c13_adaptive_rollout import fixture


def setup(tmp_path,kind='policy',payload=None):
    repo=ScienceRepository(tmp_path/'repo')
    task=ResearchTask(object_id='next-task',source_run_id='r',question='legal question')
    repo.commit([task],task_ref=task.ref(),evidence_cutoff='0')
    asset=MethodAsset(object_id='method',source_run_id='r',kind=kind,
                      payload=payload or {'depth':2,'continuation_mode':'adaptive'},
                      state='task_validated',validation_scope=('origin',),applicability_predicates={'material':'glass'})
    registry=MethodRegistry(repo);registry.put(asset,task_ref=task.ref(),evidence_cutoff='0')
    return repo,task,asset,registry


def test_bounded_trial_frozen_compiled_policy_and_actual_valuation(tmp_path):
    repo,task,asset,registry=setup(tmp_path)
    default=FrozenMethodProvider(registry,task_ref=task.ref(),facts={'material':'glass'})
    assert not default.prepare('policy')
    provider=FrozenMethodProvider(registry,task_ref=task.ref(),facts={'material':'glass'},allow_task_validated=True)
    prepared=provider.prepare('policy')[0]
    assert prepared.compiled.continuation_mode=='adaptive'
    assert prepared.receipt.status=='compiled_not_executed'
    problem,q,actions=fixture()
    plan,receipt=provider.consume(asset.ref(),problem=problem,probabilities=q,actions=actions,available_budget=3,final_validation_reserve=1)
    assert plan.root.action_id=='screen'
    assert receipt.status=='executed' and len(receipt.output_hash)==64
    assert provider.library_snapshot==receipt.library_snapshot
    with pytest.raises(ValueError,match='authority'):
        provider.consume(asset.ref(),repository=repo)


def test_default_requires_actual_promotion_and_rejects_forged_payload(tmp_path):
    repo,task,asset,registry=setup(tmp_path)
    evaluation=TransferEvaluation(object_id='eval',source_run_id='r',asset_ref=asset.ref(),task_id='heldout-P',original_task_id='origin',split='P',forward_run_id='actual-forward',passed=True,cost=2,scope={'material':'glass'})
    repo.commit([evaluation],task_ref=task.ref(),evidence_cutoff='1')
    asset,_=promote(registry,asset,evaluation,task_ref=task.ref(),evidence_cutoff='1')
    provider=FrozenMethodProvider(registry,task_ref=task.ref(),facts={'material':'glass'})
    assert provider.prepare('policy')[0].asset_ref==asset.ref()
    modified=MethodAsset(**{**asset.model_dump(),'revision':asset.revision+1,'payload':{'depth':1},'content_hash':''})
    registry.put(modified,task_ref=task.ref(),evidence_cutoff='2')
    with pytest.raises(ValueError,match='changed or retired'):
        provider.prepare('policy')
    with pytest.raises(ValueError,match='promotion evidence'):
        FrozenMethodProvider(registry,task_ref=task.ref(),facts={'material':'glass'}).prepare('policy')


def test_retirement_and_no_future_answer_or_unsupported_transfer(tmp_path):
    repo,task,asset,registry=setup(tmp_path)
    provider=FrozenMethodProvider(registry,task_ref=task.ref(),facts={'material':'glass'},allow_task_validated=True)
    retired=MethodAsset(**{**asset.model_dump(),'revision':2,'state':'retired','content_hash':''})
    registry.put(retired,task_ref=task.ref(),evidence_cutoff='1')
    with pytest.raises(ValueError,match='retired'):
        provider.prepare('policy')
    assert not FrozenMethodProvider(registry,task_ref=task.ref(),facts={'material':'glass'},allow_task_validated=True).prepare('policy')
    for facts in ({'ground_truth':1},{'future_feedback':'answer'},{'material':{'secret':3}}):
        with pytest.raises(ValueError,match='forbidden'):
            FrozenMethodProvider(registry,task_ref=task.ref(),facts=facts)
    unsupported=MethodAsset(**{**asset.model_dump(),'revision':3,'transfer_mode':'hierarchical_prior','content_hash':''})
    registry.put(unsupported,task_ref=task.ref(),evidence_cutoff='2')
    receipt=FrozenMethodProvider(registry,task_ref=task.ref(),facts={'material':'glass'},allow_task_validated=True).retrieval
    assert not receipt.selected and 'unsupported_transfer_consumer' in str(receipt.rejected)


def test_program_and_model_preparation_have_real_consumers(tmp_path):
    repo,task,asset,registry=setup(tmp_path,kind='program',payload={'program_id':'p','nodes':[{'node_id':'compute','kind':'computation','estimate':[['cpu',1]]}]})
    provider=FrozenMethodProvider(registry,task_ref=task.ref(),facts={'material':'glass'},allow_task_validated=True)
    assert provider.prepare('program')[0].compiled.nodes[0].node_id=='compute'
    runner=ProgramRunner(tmp_path/'runner',gates={},providers={})
    result,receipt=provider.consume(asset.ref(),capabilities={},limits={'cpu':1},runner=runner,prediction_commits={})
    assert result.nodes['compute']['state']=='completed' and receipt.status=='executed'
    runner.close()
    from optomind_optics.harness.science.model_ir import ModelIR, IRNode
    model=ModelIR((IRNode('n','constant_nk',attributes=(('n',1.5),)),IRNode('stack','stack',inputs=('n',),unit='nm'),IRNode('R','tmm',inputs=('stack',))),'R')
    m=MethodAsset(object_id='optical',source_run_id='r',kind='model_fragment',payload=asdict(model),state='task_validated',applicability_predicates={'material':'glass'})
    registry.put(m,task_ref=task.ref(),evidence_cutoff='2')
    provider=FrozenMethodProvider(registry,task_ref=task.ref(),facts={'material':'glass'},allow_task_validated=True)
    assert provider.prepare('model_fragment')[0].compiled.ir==model
    values,receipt=provider.consume(m.ref(),wavelengths_nm=[500,600])
    assert values==pytest.approx([.04,.04]) and receipt.status=='executed'


def test_program_instantiation_preserves_shared_reference_semantics(tmp_path):
    from optomind_optics.harness.science.program_search import adaptive_measurement_program
    program=adaptive_measurement_program(program_id='old',channel='R',reference_channel='R',coarse_grid=(500,600),fine_grid=(500,550,600),threshold=.02,sample_id='sample',reference_id='reference',expected_reference=.04)
    repo,task,asset,registry=setup(tmp_path,kind='program',payload=asdict(program))
    provider=FrozenMethodProvider(registry,task_ref=task.ref(),facts={'material':'glass'},allow_task_validated=True)
    mapped,receipt=provider.instantiate_program(asset.ref(),program_id='new-run:program',sample_bindings={'sample':'session-2','reference':'reference-2'})
    assert mapped.program_id!='old' and dict(mapped.initial_samples)=={'session-2':'ready','reference-2':'ready'}
    assert mapped.nodes[0].sample_id=='reference-2' and mapped.nodes[1].sample_id=='session-2'
    assert mapped.nodes[0].correlation_group==mapped.nodes[1].correlation_group!=program.nodes[0].correlation_group
    assert mapped.nodes[1].arguments==program.nodes[1].arguments
    assert mapped.nodes[4].condition==program.nodes[4].condition
    assert receipt.status=='instantiated_not_executed' and receipt.compiled_identity==mapped.content_hash
    for bindings in ({'sample':'session-2'}, {'sample':'same','reference':'same'}, {'sample':'x','reference':'y','validation':'z'}):
        with pytest.raises(ValueError):
            provider.instantiate_program(asset.ref(),program_id='new',sample_bindings=bindings)
    with pytest.raises(ValueError,match='fresh execution identity'):
        provider.instantiate_program(asset.ref(),program_id='old',sample_bindings={'sample':'x','reference':'y'})


def test_retired_dependency_cannot_be_revived_by_historical_ref(tmp_path):
    repo,task,dependency,registry=setup(tmp_path)
    asset=MethodAsset(object_id='dependent',source_run_id='r',kind='policy',payload={'depth':1},
                      state='task_validated',dependencies=(dependency.ref(),),applicability_predicates={'material':'glass'})
    registry.put(asset,task_ref=task.ref(),evidence_cutoff='1')
    provider=FrozenMethodProvider(registry,task_ref=task.ref(),facts={'material':'glass'},allow_task_validated=True)
    assert len(provider.prepare('policy'))==2
    retired=MethodAsset(**{**dependency.model_dump(),'revision':2,'state':'retired','content_hash':''})
    registry.put(retired,task_ref=task.ref(),evidence_cutoff='2')
    with pytest.raises(ValueError,match='dependency'):
        provider.consume(asset.ref())


def test_template_provenance_rederivation_rejects_changed_science(tmp_path):
    from optomind_optics.harness.science.contracts import DecisionContext, ExperimentProgram, PredictionSpec, Observation, PredictionEvaluation
    from optomind_optics.harness.science.trace_extractor import DecisionTrace, extract_program_template
    repo,task,_,registry=setup(tmp_path)
    protocol={'program_id':'old-run:action','initial_samples':[['old-specimen','ready']],
              'nodes':[{'node_id':'m','kind':'new_evidence','sample_id':'old-specimen','channel':'R',
                        'arguments':[['wavelengths_nm',[500,600]]],'estimate':[['calls',1],['cost',1]],'correlation_group':'old-session'}]}
    action=ExperimentProgram(object_id='actual-action',source_run_id='r',protocol=protocol)
    decision=DecisionContext(object_id='actual-before',source_run_id='r',summary={})
    prediction=PredictionSpec(object_id='actual-prediction',source_run_id='r',program_ref=action.ref(),observable='R')
    repo.commit([action,decision,prediction],task_ref=task.ref(),evidence_cutoff='before')
    cutoff=repo.latest_snapshot().committed_sequence
    observed=Observation(object_id='actual-observed',source_run_id='r',acquisition_id='acquisition',raw_blocks=({'block_id':'raw','values':[.04,.04]},))
    repo.commit([observed],task_ref=task.ref(),evidence_cutoff='returned')
    evaluated=PredictionEvaluation(object_id='actual-evaluated',source_run_id='r',prediction_ref=prediction.ref(),observation_ref=observed.ref(),metrics={'state':'fixture'})
    repo.commit([evaluated],task_ref=task.ref(),evidence_cutoff='evaluated')
    changed=DecisionContext(object_id='actual-after',source_run_id='r',observation_refs=(observed.ref(),))
    repo.commit([changed],task_ref=task.ref(),evidence_cutoff='changed')
    trace=DecisionTrace(decision_ref=decision.ref(),prediction_ref=prediction.ref(),action_ref=action.ref(),return_ref=observed.ref(),changed_decision_ref=changed.ref(),decision_sequence=cutoff,permitted_resources=('R',),requested_resources=('R',),evaluation_ref=evaluated.ref())
    template=extract_program_template(repo,trace,object_id='template',source_run_id='r',predicates={'material':'glass'},sample_slots={'old-specimen':'sample'})
    assert template.state=='proposed'
    assert template.template_source_ref==action.ref()
    # Explicit unit-fixture validation, not real scientific promotion.
    template=type(template)(**{**template.model_dump(),'state':'task_validated','content_hash':''})
    registry.put(template,task_ref=task.ref(),evidence_cutoff='template')
    provider=FrozenMethodProvider(registry,task_ref=task.ref(),facts={'material':'glass'},allow_task_validated=True)
    mapped,_=provider.instantiate_program(template.ref(),program_id='new-action',sample_bindings={'sample':'new-specimen'})
    assert mapped.nodes[0].sample_id=='new-specimen'
    bad_payload=template.model_dump(mode='json')['payload']
    bad_payload['nodes'][0]['arguments']=[['wavelengths_nm',[700,800]]]
    changed=type(template)(**{**template.model_dump(),'object_id':'tampered-template','payload':bad_payload,'content_hash':''})
    registry.put(changed,task_ref=task.ref(),evidence_cutoff='tampered')
    with pytest.raises(ValueError,match='non-identity source semantics'):
        FrozenMethodProvider(registry,task_ref=task.ref(),facts={'material':'glass'},allow_task_validated=True).prepare('program')


def test_original_v1_asset_hash_is_unchanged_by_template_schema():
    from optomind_optics.harness.science.contracts import canonical_hash, parse_object
    original={'schema_version':'method-asset.v1','object_id':'historic','revision':1,'source_run_id':'r',
              'input_refs':[],'unconsumed_metadata':{},'kind':'policy','payload':{'depth':1},'dependencies':[],
              'applicability_predicates':{},'recalibration':'none','positive_evidence':[],'negative_evidence':[],
              'cost':0.0,'validation_scope':[],'state':'proposed','transfer_mode':'structure','fallback':None}
    original['content_hash']=canonical_hash(original)
    asset=parse_object(original)
    assert asset.content_hash==original['content_hash']
    assert 'template_source_ref' not in asset.model_dump()


def test_cross_repository_receipt_qualifies_only_external_asset(tmp_path):
    from optomind_optics.harness.science.contracts import DecisionContext,canonical_hash
    repo,task,asset,registry=setup(tmp_path)
    target=ScienceRepository(tmp_path/'target')
    try:
        target.commit([task],task_ref=task.ref(),evidence_cutoff='0')
        provider=FrozenMethodProvider(registry,task_ref=task.ref(),facts={'material':'glass'},allow_task_validated=True)
        receipt=provider.prepare('policy')[0].receipt
        payload=provider.receipt_payload(receipt)
        external=payload['asset_ref']['external_scientific_ref']
        assert external['repository_id']==provider.repository_id
        assert external['snapshot_id']==provider.library_snapshot
        assert external['object_ref']==asset.ref().model_dump(mode='json')
        assert payload['target_task_ref']==task.ref().model_dump(mode='json')
        context=DecisionContext(object_id='cross-repository-receipt',source_run_id='r',summary={'receipt':payload})
        target.commit([context],task_ref=task.ref(),evidence_cutoff='1')
        assert target.load(context.ref()).summary['receipt']==payload
        with pytest.raises(ValueError,match='uncommitted'):
            target.load(asset.ref())
        with pytest.raises(ValueError,match='authority'):
            provider.receipt_payload(receipt.model_copy(update={'library_snapshot':'0'*64}))
    finally:target.close();repo.close()
