from pathlib import Path

import pytest

from optomind_optics.harness.science.contracts import DecisionContext,ResearchTask,canonical_hash
from optomind_optics.harness.science.method_assets import MethodRegistry,ResearchMethodAsset,_verify_execution_evidence
from optomind_optics.harness.science.method_lifecycle import finalize_completed_research,method_completion_status
from optomind_optics.harness.science.method_runtime import FrozenMethodProvider
from optomind_optics.harness.science.method_policy import BoundedMethodPolicySearch,MethodGenerationPolicy,MethodPolicyTask
from optomind_optics.harness.science.open_research_cli import build_offline_loop
from optomind_optics.harness.science.repository import ScienceRepository


@pytest.fixture(scope='module')
def source(tmp_path_factory):
    output=tmp_path_factory.mktemp('qualified-source')
    payload={'family':'F0','world_seed':13,'config':{'run_id':'qualified-method-test','measurement_std':.004,
             'max_iterations':1,'unknown_exploration_limit':0,'fit_starts':8,'max_nfev':160}}
    loop,resources=build_offline_loop(output,payload)
    try:
        result=loop.run(output)
        receipt=finalize_completed_research(loop.repository,task_ref=loop.task.ref(),result_ref=result.ref())
        assert receipt.status=='registered_bounded_trial'
        return output/'science',loop.task.ref(),result.ref(),receipt
    finally:
        for resource in resources:
            resource.close()


def _provider(repository,name):
    task=ResearchTask(object_id=name,source_run_id=name,question='Execute bounded method trial')
    repository.commit([task],task_ref=task.ref(),evidence_cutoff='fresh-task')
    return FrozenMethodProvider(MethodRegistry(repository),task_ref=task.ref(),facts={'domain':'planar'},allow_task_validated=True)


def test_actual_completed_source_automatically_registers_and_is_idempotent(source):
    path,task,result,receipt=source
    repository=ScienceRepository(path)
    try:
        before=repository.latest_snapshot().committed_sequence
        assert finalize_completed_research(repository,task_ref=task,result_ref=result)==receipt
        assert repository.latest_snapshot().committed_sequence==before
        asset=repository.load(receipt.asset_refs[0])
        assert isinstance(asset,ResearchMethodAsset) and asset.state=='task_validated'
        assert asset.parameter_slots[0].name=='wavelengths_nm'
        assert asset.source_layer_count==1 and len(asset.failure_branches)==5
        assert asset.template_source_ref in asset.input_refs
        _verify_execution_evidence(asset,{'domain':'planar'},repository=repository)
    finally:
        repository.close()


def test_inferred_domain_cannot_be_widened_by_forging_asset_fields(source):
    path,_,_,receipt=source
    repository=ScienceRepository(path)
    try:
        asset=repository.load(receipt.asset_refs[0])
        data=asset.model_dump();data['content_hash']='';data['parameter_slots'][0]['upper']+=10
        forged=ResearchMethodAsset.model_validate(data)
        with pytest.raises(ValueError,match='domain differs'):
            _verify_execution_evidence(forged,{'domain':'planar'},repository=repository)
    finally:
        repository.close()


def test_preflight_failure_branches_have_no_acquisition_side_effect(source):
    path,_,_,receipt=source
    repository=ScienceRepository(path)
    try:
        provider=_provider(repository,'preflight-test')
        asset_ref=receipt.asset_refs[0]
        def forbidden(*args,**kwargs):
            raise AssertionError('preflight failure reached acquisition')
        class Runner:
            run=forbidden
        kwargs={'program_id':'outside','sample_bindings':{'sample':'new'},'capabilities':{},
                'limits':{'calls':0.,'cost':0.},'runner':Runner(),'freeze_predictions':forbidden}
        result,_=provider.execute_program(asset_ref,parameter_bindings={'wavelengths_nm':(1.,2.)},**kwargs)
        assert result['status']=='outside_domain' and not result['executed']
        from optomind_optics.harness.science.jobs import ToolCapability
        kwargs['capabilities']={'R':ToolCapability(effect_class='external_measurement',domain='test',channels=('R',))}
        result,_=provider.execute_program(asset_ref,**kwargs)
        assert result['status']=='budget_exhausted' and not result['executed']
        assert method_completion_status(repository,evaluation_refs=())=='prediction_not_evaluable'
    finally:
        repository.close()


def test_five_actual_transfers_cover_material_and_layer_changes(source,tmp_path):
    from optomind_eval.method_transfer import TransferCase,execute_transfer_case
    path,_,_,_=source
    reports=[]
    for i,(indices,layers) in enumerate((((1.65,2.2),1),((1.95,2.65),1),((1.7,2.35),2),((1.8,2.45),3),((2.,2.7),3))):
        reports.append(execute_transfer_case(path,TransferCase('pytest-transfer-'+str(i),indices,layers,8200+i),tmp_path/str(i)))
    assert all(r['method_receipt']['status']=='executed' and r['measurement_cost']>0 for r in reports)
    assert len({r['library_snapshot'] for r in reports})==1
    assert sum(r['evaluation']['passed'] for r in reports)>=4
    assert all(len(r['competition']['fits'])==2 and all(f['starts'] for f in r['competition']['fits']) for r in reports)
    assert all(r['compute']['prediction_calls']>0 for r in reports)


def test_same_library_policy_search_executes_changes_and_freezes_before_holdout(source,tmp_path):
    from optomind_eval.method_transfer import TransferCase,forward_policy_row
    path,_,_,_=source
    tasks=[MethodPolicyTask(task_id=s,provenance_group=s,split=s,budget=2.) for s in 'TVPA']
    repository=ScienceRepository(path,read_only=True)
    library=canonical_hash([r.model_dump(mode='json') for r in MethodRegistry(repository).snapshot().asset_refs]);repository.close()
    calls=[]
    def forward(task,policy):
        destination=tmp_path/str(len(calls));calls.append((task.task_id,policy.version_hash))
        return forward_policy_row(task,policy,library=path,
            case=TransferCase('policy-test-'+task.task_id,(1.8,2.4),1,8900+'TVPA'.index(task.task_id)),output=destination)
    baseline=MethodGenerationPolicy(points=2)
    candidate=MethodGenerationPolicy(points=2,grid_rule='worst_pair_greedy')
    search=BoundedMethodPolicySearch(tasks,forward,library_snapshot=library,total_budget=30.,max_forward_calls=12)
    with pytest.raises(ValueError,match='holdout'):
        search.compare_holdout(baseline,candidate,task_ids=['P'])
    selected=search.select(baseline,(candidate,),train_ids=['T'],validation_ids=['V'])
    frozen=search.frozen
    holdout=search.compare_holdout(baseline,selected,task_ids=['P'])
    assert search.frozen==frozen
    assert all(row.library_snapshot==library for rows in holdout.values() for row in rows)
    assert len({row.executed_program_hash for _,row in search.rows})==len(search.rows)
    training=[row for split,row in search.rows if split=='T']
    assert len({tuple(row.output['wavelengths_nm']) for row in training})==2
    assert search.meter.total==sum(row.actual_cost for _,row in search.rows)


def test_split_provenance_overlap_is_rejected():
    tasks=[MethodPolicyTask(task_id=s,provenance_group='same',split=s,budget=1.) for s in 'TP']
    with pytest.raises(ValueError,match='provenance overlap'):
        BoundedMethodPolicySearch(tasks,None,library_snapshot='frozen',total_budget=2.,max_forward_calls=2)


def test_unsuccessful_source_never_becomes_method_asset(source):
    path,task,result,_=source
    repository=ScienceRepository(path)
    try:
        original=repository.load(result)
        payload=original.model_dump();payload.update(object_id='failed-method-source',content_hash='')
        payload['candidate_roles']={**payload['candidate_roles'],'adoption_status':'withheld_after_failed_validation','validation_state':'not_supported'}
        failed=type(original).model_validate(payload)
        repository.commit([failed],task_ref=task,evidence_cutoff='failure-regression')
        count=len(MethodRegistry(repository).snapshot().asset_refs)
        receipt=finalize_completed_research(repository,task_ref=task,result_ref=failed.ref())
        assert receipt.status=='not_qualified' and not receipt.asset_refs
        assert len(MethodRegistry(repository).snapshot().asset_refs)==count
    finally:
        repository.close()


def test_prequential_violation_is_not_model_set_failure(tmp_path):
    from optomind_optics.harness.science.contracts import Observation,PredictionEvaluation,PredictionSpec
    repository=ScienceRepository(tmp_path/'routing')
    try:
        task=ResearchTask(object_id='routing-task',source_run_id='routing',question='Check outcome routing')
        prediction=PredictionSpec(object_id='routing-prediction',source_run_id='routing',observable='R')
        observation=Observation(object_id='routing-observation',source_run_id='routing',acquisition_id='fresh')
        evaluation=PredictionEvaluation(object_id='routing-evaluation',source_run_id='routing',
            prediction_ref=prediction.ref(),observation_ref=observation.ref(),metrics={'state':'violated'})
        refit=DecisionContext(object_id='routing-refit',source_run_id='routing',
            input_refs=(observation.ref(),),summary={'competition':{'state':'model_family_inadequate'}})
        repository.commit([task,prediction,observation,evaluation,refit],task_ref=task.ref(),evidence_cutoff='routing-test')
        assert method_completion_status(repository,evaluation_refs=(evaluation.ref(),))=='independent_validation_required'
        assert method_completion_status(repository,evaluation_refs=(evaluation.ref(),),competition_refs=(refit.ref(),))=='model_set_inadequate'
    finally:
        repository.close()
