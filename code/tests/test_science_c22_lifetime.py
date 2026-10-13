import json
import pytest
from optomind_optics.harness.science.lifetime_evaluator import (
    LifetimeTask, LifetimeProtocol, LifetimeEvaluator, ForwardReceipt,
    nearest_history_forward,
)
from optomind_optics.harness.science.lifetime_cli import main


def protocol(**changes):
    data = dict(protocol_id='chronological', cost_unit='cpu_seconds', tasks=(
        LifetimeTask(task_id='t0',family='f',provenance_group='g0',inputs=(1.,),target=2.),
        LifetimeTask(task_id='t1',family='f',provenance_group='g1',inputs=(2.,),target=3.,legal_feedback=False),
        LifetimeTask(task_id='t2',family='f',provenance_group='g2',inputs=(3.,),target=0.),
    ), frozen_ability=(LifetimeTask(task_id='a0',family='f',provenance_group='ability',inputs=(1.,),target=2.),))
    data.update(changes)
    return LifetimeProtocol(**data)


def receipt(prediction=0):
    return ForwardReceipt(prediction=prediction, costs=dict(evaluation=1.,learning=.2,maintenance=.1,revalidation=.1),external_receipt='verified-local')


def test_forward_chronology_equal_legal_history_frozen_and_negative_transfer(tmp_path):
    views=[]
    def forward(view):
        assert not hasattr(view.task,'target')
        views.append(view)
        return receipt(view.history[-1].target if view.history else 0)
    runner=LifetimeEvaluator(tmp_path,protocol(),evaluator_id='trusted-v1')
    report=runner.run(forward)
    assert report.complete and report.negative_transfer['text']==1
    for row in report.records:
        if row['stage']=='forward':
            assert all(k < row['step'] for k in row['history_steps'])
            assert 1 not in row['history_steps']
    for step in range(3):
        warm=[r for r in report.records if r['stage']=='forward' and r['step']==step and r['arm']!='cold']
        assert len({r['snapshot_id'] for r in warm})==1
    assert all(not v.history for v in views if v.representation=='cold')
    assert all(h.task.task_id!='a0' for v in views for h in v.history)
    assert report.total_cost['cold']==pytest.approx(7.)
    assert report.storage>0 and report.old_capability['text']==0
    before=len(views)
    assert runner.replay().records==report.records
    assert len(views)==before
    runner.close()


def test_pending_callback_is_never_automatically_repeated(tmp_path):
    p=protocol(frozen_ability=())
    runner=LifetimeEvaluator(tmp_path,p,evaluator_id='trusted-v1')
    calls=[]
    def interrupted(view):
        calls.append(view)
        raise ConnectionError('external completion unknown')
    with pytest.raises(ConnectionError): runner.run(interrupted)
    runner.close()
    runner=LifetimeEvaluator(tmp_path,p,evaluator_id='trusted-v1')
    with pytest.raises(RuntimeError,match='reconciliation'): runner.run(interrupted)
    assert len(calls)==1
    key=runner.preview()['pending'][0]
    runner.reconcile(key,receipt(),elapsed_seconds=.01)
    assert runner.run(nearest_history_forward).complete
    assert not runner.preview()['pending']
    runner.close()


def test_future_changes_invalidate_resume_and_frozen_overlap(tmp_path):
    p=protocol()
    runner=LifetimeEvaluator(tmp_path,p,evaluator_id='trusted-v1'); runner.close()
    with pytest.raises(ValueError,match='mismatch'):
        LifetimeEvaluator(tmp_path,p,evaluator_id='changed-v2')
    with pytest.raises(ValueError,match='overlaps'):
        protocol(frozen_ability=(p.tasks[0].model_copy(update={'task_id':'different'}),))


def test_future_outcomes_do_not_change_earlier_predictions(tmp_path):
    p=protocol(frozen_ability=())
    changed=protocol(frozen_ability=(),tasks=p.tasks[:-1]+(p.tasks[-1].model_copy(update={'target':999}),))
    snapshots=[]
    for i,stream in enumerate((p,changed)):
        runner=LifetimeEvaluator(tmp_path/str(i),stream,evaluator_id='reference')
        report=runner.run(nearest_history_forward)
        snapshots.append([(r['snapshot_id'],r['loss']) for r in report.records if r['step']<2])
        runner.close()
    assert snapshots[0]==snapshots[1]


def test_partial_replay_is_read_only_and_invalid_receipt_stays_pending(tmp_path):
    runner=LifetimeEvaluator(tmp_path,protocol(),evaluator_id='trusted-v1')
    with pytest.raises(RuntimeError,match='incomplete'): runner.replay()
    assert runner.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0]==0
    with pytest.raises(ValueError): runner.run(lambda v: {'prediction':0,'costs':{},'external_receipt':'bad'})
    assert len(runner.preview()['pending'])==1
    with pytest.raises(ValueError): runner.reconcile(runner.preview()['pending'][0],receipt(),elapsed_seconds=float('nan'))
    runner.close()


def test_cli_preview_run_replay_resume(tmp_path,capsys):
    path=tmp_path/'protocol.json'; path.write_text(protocol().model_dump_json())
    args=['--root',str(tmp_path/'run'),'--protocol',str(path)]
    main(['preview',*args]); assert json.loads(capsys.readouterr().out)['tasks']==3
    main(['run',*args]); first=json.loads(capsys.readouterr().out)
    main(['replay',*args]); replay=json.loads(capsys.readouterr().out)
    assert replay['records']==first['records']
    main(['resume',*args]); assert json.loads(capsys.readouterr().out)['complete']
    with pytest.raises(ValueError,match='existing'): main(['run',*args])


def test_existing_scientific_controller_runs_through_lifetime_boundary(tmp_path):
    import numpy as np
    from optomind_optics.harness.science.lifetime_evaluator import ScientificLifetimeAdapter
    from optomind_optics.harness.science.learning.offline_forward import ScientificForwardRunner
    from optomind_optics.harness.science.terminal_problem import TerminalDecisionProblem
    from optomind_optics.harness.science.controller import MeasurementAction
    context={k:'x' for k in ('task','objective','model','sample','material','solver','fidelity','loss','split')}
    problem=lambda view:TerminalDecisionProblem(('w0','w1'),('d0','d1'),np.array([[0,10],[10,0]]),context,'loss')
    actions=lambda view:(MeasurementAction('good',('o0','o1'),((1,0),(0,1)),.1,1),)
    acquisitions=[]
    def acquire(view):
        acquisitions.append(view.task_id)
        return lambda action:'o1'
    scientific=ScientificForwardRunner(problem,actions,(.5,.5),acquire,lambda view,receipt:float(receipt.selected_design_id=='d1'))
    policy_views=[]
    def policy(view,meter):
        policy_views.append(view)
        assert not hasattr(view.task,'target')
        meter.charge('learning',.25)
        return {'depth':1,'action_ids':['good']}
    adapter=ScientificLifetimeAdapter(scientific,policy,available_budget=10,final_validation_reserve=1,initial_snapshot='frozen',operation_budget=10,attempt_cost=1)
    p=protocol(frozen_ability=(),cost_unit='resource_units')
    runner=LifetimeEvaluator(tmp_path,p,evaluator_id='c04-scientific-v1')
    report=runner.run(adapter)
    assert len(acquisitions)==12 and len(policy_views)==12
    assert report.total_cost['cold']==pytest.approx(6.75)
    assert all(r['external_receipt'].startswith('scientific:') for r in report.records)
    assert report.records[4]['history_steps']==[]
    runner.replay()
    assert len(acquisitions)==12
    runner.close()


def test_legal_feedback_advances_real_registry_only_after_all_forward_arms(tmp_path):
    from optomind_optics.harness.science.contracts import ResearchTask
    from optomind_optics.harness.science.repository import ScienceRepository
    from optomind_optics.harness.science.method_assets import MethodAsset,MethodRegistry
    repo=ScienceRepository(tmp_path/'repo')
    task=ResearchTask(object_id='lifetime',source_run_id='run',question='legal update')
    repo.commit([task],task_ref=task.ref(),evidence_cutoff='initial')
    registry=MethodRegistry(repo)
    p=protocol(frozen_ability=(),learning_enabled=True,initial_library_snapshot=registry.snapshot().snapshot_id,initial_library_cost=0.)
    runner=LifetimeEvaluator(tmp_path/'run',p,evaluator_id='registry-update-v1')
    seen=[]; updates=[]
    def forward(view):
        seen.append(view)
        assert view.representation=='cold' or view.library_snapshot==registry.snapshot().snapshot_id
        return receipt()
    def learn(view):
        k=view.history[-1].step
        assert len(seen)==4*(k+1)
        assert [h.step for h in view.history]==([0] if k==0 else [0,2])
        asset=MethodAsset(object_id=f'policy-{k}',source_run_id='run',kind='policy',payload={'depth':1},state='proposed')
        registry.put(asset,task_ref=task.ref(),evidence_cutoff=str(k))
        updates.append(k)
        return ForwardReceipt(prediction=0,costs=dict(evaluation=0,learning=2,maintenance=1,revalidation=0),external_receipt=f'commit:{k}',library_snapshot=registry.snapshot().snapshot_id,library_storage_bytes=sum(p.stat().st_size for p in repo.root.rglob('*') if p.is_file()))
    report=runner.run(forward,learn=learn)
    assert updates==[0,2] and report.total_cost['shared_learning']==6
    assert report.comparison_total_cost['text']==report.total_cost['text']+6
    assert report.comparison_total_cost['cold']==report.total_cost['cold']
    assert seen[1].library_snapshot==p.initial_library_snapshot
    assert seen[5].library_snapshot!=p.initial_library_snapshot
    assert runner.replay().records==report.records and updates==[0,2]
    runner.close();repo.close()


def test_interrupted_learning_is_not_repeated(tmp_path):
    runner=LifetimeEvaluator(tmp_path,protocol(frozen_ability=(),learning_enabled=True),evaluator_id='registry-update-v1')
    attempts=[]
    def learn(view): attempts.append(view); raise ConnectionError('commit outcome unknown')
    with pytest.raises(ConnectionError): runner.run(nearest_history_forward,learn=learn)
    with pytest.raises(RuntimeError,match='reconciliation'): runner.run(nearest_history_forward,learn=learn)
    assert len(attempts)==1 and runner.preview()['pending']==['learning_update:0:t0']
    runner.close()


def test_real_equal_library_representations_execute_all_three_payload_kinds(tmp_path):
    import time
    import numpy as np
    from dataclasses import asdict
    from optomind_optics.harness.science.lifetime_evaluator import MethodLibraryLifetimeAdapter
    from optomind_optics.harness.science.contracts import ResearchTask
    from optomind_optics.harness.science.repository import ScienceRepository
    from optomind_optics.harness.science.method_assets import MethodAsset,MethodRegistry,consume_asset
    from optomind_optics.harness.science.model_ir import ModelIR,IRNode
    from optomind_optics.harness.science.program_runner import ProgramRunner
    from optomind_optics.harness.science.terminal_problem import TerminalDecisionProblem
    from optomind_optics.harness.science.controller import MeasurementAction
    started=time.process_time()
    repo=ScienceRepository(tmp_path/'registry')
    task=ResearchTask(object_id='library-task',source_run_id='fixture',question='bounded representation comparison')
    ir=ModelIR((IRNode('n','constant_nk',attributes=(('n',1.5),)),IRNode('s','stack',inputs=('n',),unit='nm'),IRNode('t','tmm',inputs=('s',))),'t')
    assets=[MethodAsset(object_id=kind,source_run_id='fixture',kind=kind,payload=payload,state='task_validated') for kind,payload in (
        ('model_fragment',asdict(ir)),('program',{'program_id':'local-computation','nodes':[{'node_id':'compute','kind':'computation','estimate':[['cpu',1]]}]}),('policy',{'depth':1}))]
    repo.commit([task,*assets],task_ref=task.ref(),evidence_cutoff='before-stream')
    initial_cost=time.process_time()-started
    registry=MethodRegistry(repo)
    p=LifetimeProtocol(protocol_id='three-payload-offline',cost_unit='cpu_seconds',initial_library_snapshot=registry.snapshot().snapshot_id,initial_library_cost=initial_cost,
        tasks=tuple(LifetimeTask(task_id=a.kind,family=a.kind,provenance_group=a.kind,inputs=(),target=.04 if a.kind=='model_fragment' else 1,legal_feedback=False) for a in assets))
    context={k:'fixture' for k in ('task','objective','model','sample','material','solver','fidelity','loss','split')}
    problem=TerminalDecisionProblem(('w0','w1'),('d0','d1'),np.array([[0,10],[10,0]]),context,'loss')
    actions=(MeasurementAction('measure',('o0','o1'),((1,0),(0,1)),.1,1),)
    representations={}
    def consume(view,library):
        start=time.process_time(); runner=None
        if isinstance(library,str):
            records=json.loads(library); representations[(view.task.family,'text')]=records
            asset=MethodAsset.model_validate(next(a for a in records if a['kind']==view.task.family))
            execute=lambda **kw:consume_asset(asset,facts={},**kw)
        elif isinstance(library,list):
            representations[(view.task.family,'structure')]=library
            asset=MethodAsset.model_validate(next(a for a in library if a['kind']==view.task.family))
            execute=lambda **kw:consume_asset(asset,facts={},**kw)
        else:
            asset=next(a for a in assets if a.kind==view.task.family)
            execute=lambda **kw:library.consume(asset.ref(),**kw)[0]
        if view.task.family=='model_fragment':
            score=float(execute(wavelengths_nm=[550])[0])
        elif view.task.family=='program':
            runner=ProgramRunner(tmp_path/f'program-{view.representation}',gates={},providers={})
            score=float(execute(capabilities={},limits={'cpu':1},runner=runner,prediction_commits={}).nodes['compute']['state']=='completed')
        else:
            score=float(execute(problem=problem,probabilities=(.5,.5),actions=actions,available_budget=2,final_validation_reserve=1).selected_actions==('measure',))
        if runner: runner.close()
        return ForwardReceipt(prediction=score,costs=dict(evaluation=time.process_time()-start,learning=0,maintenance=0,revalidation=0),external_receipt='actual-local:'+view.task.task_id)
    def cold(view,library):
        start=time.process_time()
        return ForwardReceipt(prediction=0,costs=dict(evaluation=time.process_time()-start,learning=0,maintenance=0,revalidation=0),external_receipt='actual-local-cold')
    adapter=MethodLibraryLifetimeAdapter(registry,task_ref_for_view=lambda view:task.ref(),facts_for_view=lambda view:{},consumers={'cold':cold,'text':consume,'structure':consume,'executable':consume},preparation_cost_per_cpu_second=1,allow_task_validated=True)
    runner=LifetimeEvaluator(tmp_path/'lifetime',p,evaluator_id='all-payload-fixture-v1')
    report=runner.run(adapter)
    for kind in ('model_fragment','program','policy'):
        assert representations[(kind,'text')]==representations[(kind,'structure')]
    executed=[receipt for row in report.records if row['arm']=='executable' for receipt in row['method_consumption_receipts'] if receipt['status']=='executed']
    assert {r['kind'] for r in executed}=={'model_fragment','program','policy'}
    assert all(r['output_hash'] for r in executed)
    assert all(row['loss']==pytest.approx(0) for row in report.records if row['arm']!='cold')
    assert all(row['library_storage_bytes']>0 for row in report.records if row['arm']!='cold')
    assert report.total_cost['shared_learning']==initial_cost
    runner.close();repo.close()


def test_library_adapter_applies_gates_and_rejects_future_registry_changes(tmp_path):
    from optomind_optics.harness.science.lifetime_evaluator import MethodLibraryLifetimeAdapter,LifetimeView,PublicTask
    from optomind_optics.harness.science.contracts import ResearchTask
    from optomind_optics.harness.science.repository import ScienceRepository
    from optomind_optics.harness.science.method_assets import MethodAsset,MethodRegistry
    repo=ScienceRepository(tmp_path)
    task=ResearchTask(object_id='task',source_run_id='run',question='bounded')
    asset=MethodAsset(object_id='glass-only',source_run_id='run',kind='policy',payload={'depth':1},state='task_validated',applicability_predicates={'material':'glass'})
    repo.commit([task,asset],task_ref=task.ref(),evidence_cutoff='0');registry=MethodRegistry(repo)
    view=LifetimeView(task=PublicTask(task_id='current',family='policy',inputs=()),history=(),representation='text',snapshot_id='legal-history',library_snapshot=registry.snapshot().snapshot_id)
    seen=[]
    def consume(view,library): seen.append(library);return receipt()
    adapter=MethodLibraryLifetimeAdapter(registry,task_ref_for_view=lambda view:task.ref(),facts_for_view=lambda view:{'material':'water'},consumers={name:consume for name in ('cold','text','structure','executable')},preparation_cost_per_cpu_second=1,allow_task_validated=True)
    adapter(view)
    assert seen==['[]']  # Inapplicable assets cannot reach even textual reuse.
    future=MethodAsset(object_id='future',source_run_id='later',kind='policy',payload={'depth':2},state='task_validated')
    registry.put(future,task_ref=task.ref(),evidence_cutoff='future')
    with pytest.raises(ValueError,match='future or stale'):
        adapter(view)
    assert len(seen)==1
    repo.close()


def test_learning_reconciliation_requires_committed_snapshot_and_storage(tmp_path):
    runner=LifetimeEvaluator(tmp_path,protocol(frozen_ability=(),learning_enabled=True),evaluator_id='learning-recovery-v1')
    def fail(view): raise ConnectionError('unknown commit outcome')
    with pytest.raises(ConnectionError): runner.run(nearest_history_forward,learn=fail)
    key=runner.preview()['pending'][0]
    with pytest.raises(ValueError,match='committed library snapshot'):
        runner.reconcile(key,receipt(),elapsed_seconds=1)
    assert runner.preview()['pending']==[key]
    runner.close()


def test_chronological_runner_executes_fresh_full_optical_research_loops(tmp_path):
    from optomind_optics.harness.science.lifetime_evaluator import ScientificLifetimeAdapter
    from optomind_optics.harness.science.open_research_cli import build_offline_loop
    from optomind_optics.harness.science.method_assets import PolicyPayload
    from optomind_optics.harness.science.learning.research_forward import ResearchLoopForwardRunner,ResearchLoopInstance,initial_state_fingerprint
    base={'family':'F0','config':{'max_iterations':1,'unknown_exploration_limit':0,'fit_starts':1,'max_nfev':2,'max_candidates':2}}
    policy=PolicyPayload(depth=1,action_ids=('new_independent_session',))
    template,resources=build_offline_loop(tmp_path/'template',base,experiment_policy=policy)
    initial=initial_state_fingerprint(template)
    rates={'model_predictions':1e-6,'solver_points':1e-9,'valuation_samples':1e-6,'milp_solves':1e-5}
    assert set(template.compute_ledger.snapshot()['limits']) <= rates.keys()
    for resource in resources:resource.close()
    created=[]
    def factory(view,candidate):
        path=tmp_path/f'actual-forward-{len(created)}';created.append(path)
        payload={**base,'world_seed':7 if view.task_id=='earlier' else 13}
        loop,resources=build_offline_loop(path,payload,experiment_policy=candidate)
        return ResearchLoopInstance(loop,path,resources)
    # This score is only a completed-run engineering check. It is explicitly
    # not an optical quality, superiority or longitudinal benefit estimate.
    forward=ResearchLoopForwardRunner(factory,lambda view,result,loop:float(bool(result.prediction_refs)),compute_cost_rates=rates)
    adapter=ScientificLifetimeAdapter(forward,lambda view,meter:policy,available_budget=10,final_validation_reserve=1,initial_snapshot=initial,operation_budget=100,attempt_cost=1)
    p=LifetimeProtocol(protocol_id='actual-optical-chronology',cost_unit='declared_resource_units',representations=('executable',),tasks=tuple(LifetimeTask(task_id=name,family='optical',provenance_group=name,inputs=(),target=1.) for name in ('earlier','later')))
    runner=LifetimeEvaluator(tmp_path/'chronology',p,evaluator_id='fresh-full-optical-v1')
    report=runner.run(adapter)
    assert len(created)==4 and len(set(created))==4
    assert all(attempt['status']=='completed' for attempt in forward.attempts)
    assert all(attempt['resources']['measurement']>=1 for attempt in forward.attempts)
    assert all((path/'SCIENCE_OPEN_TRAJECTORY.json').exists() for path in created)
    assert report.records[3]['history_steps']==[0]
    assert report.records[2]['history_steps']==[]
    assert report.total_cost['cold']>2 and report.total_cost['executable']>2
    runner.replay()
    assert len(created)==4
    runner.close()


def test_corrupt_journal_receipt_cannot_silently_change_results_or_repeat_effects(tmp_path):
    runner=LifetimeEvaluator(tmp_path,protocol(frozen_ability=()),evaluator_id='journal-integrity-v1')
    calls=[]
    def forward(view):calls.append(view);return receipt()
    runner.run(forward)
    key='forward:0:t0:cold'
    original,elapsed=runner.db.execute('SELECT result,elapsed FROM operations WHERE key=?',(key,)).fetchone()
    with pytest.raises(ValueError,match='cannot be overwritten'):
        runner.repair_receipt(key,ForwardReceipt.model_validate_json(original),elapsed_seconds=elapsed)
    damaged=json.loads(original);damaged['prediction']=999
    with runner.db:runner.db.execute('UPDATE operations SET result=? WHERE key=?',(json.dumps(damaged),key))
    with pytest.raises(ValueError,match='integrity mismatch'):runner.run(forward)
    assert len(calls)==12
    runner.repair_receipt(key,ForwardReceipt.model_validate_json(original),elapsed_seconds=elapsed)
    assert runner.replay().complete and len(calls)==12
    runner.close()
