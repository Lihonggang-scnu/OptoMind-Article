import json
from pathlib import Path
import pytest
from optomind_optics.harness.science.contracts import canonical_hash
from optomind_optics.harness.science.open_research_cli import build_offline_loop
from optomind_optics.harness.science.method_assets import PolicyPayload
from optomind_optics.harness.science.learning.learning_runner import ActionInput, LearningTask, LearningRunner, PolicyVersion
from optomind_optics.harness.science.learning.research_forward import ResearchLoopForwardRunner, ResearchLoopInstance, initial_state_fingerprint, library_fingerprint


def test_fresh_real_research_loop_policy_candidates_and_actual_resource_cost(tmp_path):
    payload={'family':'F0','world_seed':13,'config':{'measurement_std':.004,'objective_target':.1,
             'action_loss_cost':0.,'design_thicknesses_nm':[135+i*.1 for i in range(121)],
             'max_iterations':2,'unknown_exploration_limit':0}}
    base=PolicyPayload(depth=1,action_ids=('new_independent_session',))
    template,resources=build_offline_loop(tmp_path/'template',payload,experiment_policy=base)
    initial=initial_state_fingerprint(template);library=library_fingerprint(template)
    for resource in resources: resource.close()
    view=ActionInput(task_id='T',initial_snapshot=initial,library_snapshot=library,available_budget=10,final_validation_reserve=1)
    created=[]
    def factory(view,policy):
        output=tmp_path/f'candidate-{len(created)}'
        loop,resources=build_offline_loop(output,payload,experiment_policy=policy)
        created.append(output)
        return ResearchLoopInstance(loop,output,resources)
    def score(view,result,loop):
        # This is an engineering behavior score, not a quality/superiority claim.
        return float(len(result.prediction_refs))
    forward=ResearchLoopForwardRunner(factory,score,compute_cost_rates={'model_predictions':1e-6,'solver_points':1e-9,'valuation_samples':1e-6,'milp_solves':1e-5})
    runner=LearningRunner([LearningTask(task_id='T',provenance_group='T',split='T',action_input=view)],forward,budget=100)
    candidates=[PolicyVersion(experiment_policy=base.model_dump_json()),
                PolicyVersion(experiment_policy=PolicyPayload(depth=2,action_ids=('new_independent_session_1','new_independent_session_2'),continuation_mode='adaptive').model_dump_json())]
    rows=[runner.evaluate(['T'],p)[0] for p in candidates]
    assert all(row.valid for row in rows)
    assert created[0]!=created[1] and len(forward.attempts)==2
    for attempt,path in zip(forward.attempts,created):
        assert attempt['resources']['measurement']>=2
        assert attempt['resources']['model_predictions']>0 and attempt['resources']['solver_points']>0
        assert json.loads((path/'LEARNING_FORWARD_RECEIPT.json').read_text())['status']=='completed'
        assert (path/'SCIENCE_OPEN_TRAJECTORY.json').exists()
    assert runner.cost.total==pytest.approx(2+sum(a['charged_cost'] for a in forward.attempts))
    assert rows[0].output['policy']!=rows[1].output['policy']
    adaptive=json.loads((created[1]/'SCIENCE_OPEN_TRAJECTORY.json').read_text())
    assert 'adaptive' in json.dumps(adaptive)
    verify_real_trace_extraction_and_next_task(created[0], tmp_path/'reuse-target')


def test_full_loop_initial_snapshot_mismatch_fails_before_acquisition(tmp_path):
    policy=PolicyPayload(depth=1,action_ids=('new_independent_session',))
    paths=[]
    def factory(view,p):
        path=tmp_path/f'candidate-{len(paths)}';paths.append(path)
        loop,resources=build_offline_loop(path,{'family':'F0','config':{'max_iterations':1,'unknown_exploration_limit':0}},experiment_policy=p)
        return ResearchLoopInstance(loop,path,resources)
    forward=ResearchLoopForwardRunner(factory,lambda *args:1,compute_cost_rates={'model_predictions':1e-6,'solver_points':1e-9,'valuation_samples':1e-6,'milp_solves':1e-5})
    view=ActionInput(task_id='T',initial_snapshot='wrong',library_snapshot=canonical_hash([]),available_budget=10,final_validation_reserve=1)
    runner=LearningRunner([LearningTask(task_id='T',provenance_group='T',split='T',action_input=view)],forward,budget=10)
    result=runner.evaluate(['T'],PolicyVersion(experiment_policy=policy.model_dump_json()))[0]
    assert not result.valid and forward.attempts[0]['resources']['measurement']==0
    assert runner.cost.total==1


@pytest.mark.parametrize('failure', ['outer_budget','compute_after_purchase'])
def test_forward_budget_preflight_and_actual_failed_attempt_cost(tmp_path,failure):
    policy=PolicyPayload(depth=1,action_ids=('new_independent_session',))
    config={'max_iterations':1,'unknown_exploration_limit':0,'fit_starts':1,'max_nfev':2}
    if failure=='compute_after_purchase':config['compute_prediction_limit']=1
    payload={'family':'F0','config':config}
    template,resources=build_offline_loop(tmp_path/'template',payload,experiment_policy=policy)
    fingerprint=initial_state_fingerprint(template)
    for resource in resources:resource.close()
    def factory(view,p):
        path=tmp_path/'attempt'
        loop,resources=build_offline_loop(path,payload,experiment_policy=p)
        return ResearchLoopInstance(loop,path,resources)
    forward=ResearchLoopForwardRunner(factory,lambda *a:1,compute_cost_rates={'model_predictions':1e-6,'solver_points':1e-9,'valuation_samples':1e-6,'milp_solves':1e-5})
    view=ActionInput(task_id='T',initial_snapshot=fingerprint,library_snapshot=canonical_hash([]),available_budget=10,final_validation_reserve=1)
    runner=LearningRunner([LearningTask(task_id='T',provenance_group='T',split='T',action_input=view)],forward,budget=1.1 if failure=='outer_budget' else 100)
    result=runner.evaluate(['T'],PolicyVersion(experiment_policy=policy.model_dump_json()))[0]
    assert not result.valid
    attempt=forward.attempts[0]
    assert attempt['status']=='failed'
    if failure=='outer_budget':
        assert attempt['resources']['measurement']==0 and runner.cost.total==1
    else:
        assert attempt['resources']['measurement']>=1 and runner.cost.total>2
        assert attempt['resources']['model_predictions']>=1
    assert (tmp_path/'attempt'/'LEARNING_FORWARD_RECEIPT.json').exists()


def verify_real_trace_extraction_and_next_task(source_path,target_path):
    """Actual optical source trace, canonical asset lifecycle, actual new task."""
    from optomind_optics.harness.science.repository import ScienceRepository
    from optomind_optics.harness.science.contracts import DecisionContext, ExperimentProgram, PredictionSpec, PredictionEvaluation, ResearchTask, SelectedResearchResult
    from optomind_optics.harness.science.method_assets import MethodRegistry
    from optomind_optics.harness.science.method_runtime import FrozenMethodProvider
    from optomind_optics.harness.science.trace_extractor import DecisionTrace, extract_asset, extract_program_template
    from optomind_optics.harness.science.promotion import TransferEvaluation, promote
    source=ScienceRepository(source_path/'science')
    objects=[source.load(r) for r in source.latest_snapshot().object_refs]
    by_id={o.object_id:o for o in objects}
    origin=by_id['offline-open-research:task']
    before=by_id['offline-open-research:iteration:0:decision']
    after=by_id['offline-open-research:iteration:0:selection']
    program=next(o for o in objects if isinstance(o,ExperimentProgram) and o.protocol['program_id']=='offline-open-research:iteration:0')
    prediction=next(o for o in objects if isinstance(o,PredictionSpec) and o.grid.get('program')==program.object_id.removeprefix('program:'))
    evaluation=next(o for o in objects if isinstance(o,PredictionEvaluation) and o.prediction_ref==prediction.ref())
    observed=source.load(evaluation.observation_ref)
    model=source.load(prediction.input_refs[0])
    selected=next(o for o in objects if isinstance(o,SelectedResearchResult))
    def trace(action):
        return DecisionTrace(decision_ref=before.ref(),prediction_ref=prediction.ref(),action_ref=action.ref(),
            return_ref=observed.ref(),changed_decision_ref=after.ref(),
            decision_sequence=max(source.object_commit_sequence(r) for r in (before.ref(),prediction.ref(),action.ref())),
            permitted_resources=('R',),requested_resources=('R',),evaluation_ref=evaluation.ref())
    registry=MethodRegistry(source)
    assets=[extract_asset(source,trace(program),kind='policy',payload=before.summary['experiment_policy'],object_id='learned-policy',source_run_id=origin.source_run_id,predicates={'domain':'planar'}),
            extract_asset(source,trace(model),kind='model_fragment',payload=model.model_ir,object_id='learned-model',source_run_id=origin.source_run_id,predicates={'domain':'planar'}),
            extract_program_template(source,trace(program),object_id='learned-program',source_run_id=origin.source_run_id,predicates={'domain':'planar'},
                                     sample_slots={s:'sample' for s,_ in program.protocol['initial_samples']})]
    for asset in assets:
        assert asset.state=='proposed'
        registry.put(asset,task_ref=origin.ref(),evidence_cutoff='after-source-run')
        evidence=TransferEvaluation(object_id=asset.object_id+':source-replay',source_run_id=origin.source_run_id,
            input_refs=(selected.ref(),evaluation.ref()),asset_ref=asset.ref(),task_id=origin.object_id,original_task_id=origin.object_id,
            split='T',forward_run_id=origin.source_run_id,passed=True,cost=json.loads((source_path/'LEARNING_FORWARD_RECEIPT.json').read_text())['charged_cost'],scope={'domain':'planar'})
        source.commit([evidence],task_ref=origin.ref(),evidence_cutoff='actual-source-run')
        validated,_=promote(registry,asset,evidence,task_ref=origin.ref(),evidence_cutoff='actual-source-run')
        assert validated.state=='task_validated'  # One source task never promotes.
    providers=[]
    def methods(task):
        source.commit([task],task_ref=task.ref(),evidence_cutoff='new-task-before-data')
        provider=FrozenMethodProvider(registry,task_ref=task.ref(),facts={'domain':'planar'},allow_task_validated=True)
        providers.append(provider)
        return provider
    payload={'family':'F3','config':{'run_id':'method-reuse-target','max_iterations':1,'unknown_exploration_limit':1,
             'fit_starts':1,'max_nfev':60,'max_candidates':2,'grammar':['cauchy']}}
    loop,resources=build_offline_loop(target_path,payload,method_provider_factory=methods)
    try:
        result=loop.run(target_path)
        assert result.ref()!=selected.ref()
        executed=loop._find('method-reuse-target:iteration:0:program-method-executed')
        assert executed.summary['status']=='executed' and executed.summary['observation_refs']
        contexts=[loop.repository.load(r) for r in loop.repository.latest_snapshot().object_refs]
        assert any(isinstance(o,DecisionContext) and o.summary.get('role')=='executed and fitted candidate; not automatic selection' for o in contexts)
        # In this mismatched F3 task valuation may be unavailable; do not
        # fabricate a policy execution receipt for an unvalued action.
        assert any(r.kind=='model_fragment' and r.status=='executed' for r in providers[0].receipts)
        assert loop.runner.gates['R'].usage()['calls']>=3
        # A new task's successful execution still does not self-promote its assets.
        assert all(source.load(r).state=='task_validated' for r in registry.snapshot().asset_refs)
    finally:
        for resource in resources:resource.close()
    policy_payload={'family':'F0','world_seed':13,'config':{'run_id':'policy-reuse-target','measurement_std':.004,
        'objective_target':.1,'action_loss_cost':0.,'design_thicknesses_nm':[135+i*.1 for i in range(121)],
        'max_iterations':1,'unknown_exploration_limit':0}}
    policy_path=target_path.parent/'policy-reuse-target'
    policy_loop,policy_resources=build_offline_loop(policy_path,policy_payload,method_provider_factory=methods)
    try:
        policy_loop.run(policy_path)
        assert any(r.kind=='policy' and r.status=='executed' for r in providers[-1].receipts)
        assert policy_loop._find('policy-reuse-target:iteration:0:continuation') is not None
    finally:
        for resource in policy_resources:resource.close()
        source.close()
