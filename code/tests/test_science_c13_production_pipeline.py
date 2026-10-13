"""Actual production Article factory → full loop → canonical authoring consumer."""
import json
import pytest
from test_science_c22_release_archive import make_factory,assemble_real,release,fake_runtime
from optomind_optics.harness.science.open_research_cli import build_offline_loop
from optomind_optics.harness.science.contracts import Observation,canonical_hash


def assemble(tmp_path,*,fault_hook=None,unknown_status=False):
    factory=make_factory(tmp_path)
    calls=[];built=[]
    def builder(work_dir):
        loop,resources=build_offline_loop(work_dir,{'family':'F0','config':{
            'run_id':factory.request.run_id,'max_iterations':1,'unknown_exploration_limit':0,
            'fit_starts':2,'max_nfev':40}},fault_hook=fault_hook)
        if unknown_status:resources[-1].status=lambda key:{'state':'unknown_outcome'}
        built.append(loop)
        return loop,resources
    assembly=assemble_real(factory,calls,open_research_builder=builder)
    assert built==[]
    return assembly,calls,built


def test_real_factory_full_loop_stage_and_actual_canonical_authoring_consumer(tmp_path):
    assembly,calls,built=assemble(tmp_path)
    policy=assembly.pipeline.science_policy
    try:
        result=assembly.run()
        assert next(r for r in result.receipts if r.stage=='execution').status=='completed',result.validation_errors
        assert not calls and result.execution_count==0
        loop=built[0];selected=policy.selected_result
        assert assembly.coordinator.science_repository is loop.repository
        assert assembly.coordinator.science_task_ref==loop.task.ref()
        assert assembly.coordinator.execution_gate in loop.runner.gates.values()
        observations=[loop.repository.load(ref) for ref in loop.repository.latest_snapshot().object_refs if ref.object_id.startswith('observation:')]
        assert len(observations)>=2 and all(isinstance(o,Observation) for o in observations)
        assert loop.runner.gates['R'].usage()['calls']>=2
        source=assembly.scientific_authoring_source()
        assert source.source_kind=='scientific_result'
        assert source.result.selected_result_ref==selected.ref()
        assert source.executions==() and source.assets and source.assets[0].candidates
        assert source.assets[0].candidates[0].source_ref==selected.candidate_ref
        before=loop.runner.gates['R'].usage()
        assert assembly.resume().result_id==result.result_id
        assert loop.runner.gates['R'].usage()==before
        handoff_path=tmp_path/'run'/'SCIENCE_OPEN_PIPELINE_HANDOFF.json'
        handoff=json.loads(handoff_path.read_text());handoff['legacy_optimizer_certificates']=True
        handoff_path.write_text(json.dumps(handoff))
        status_path=tmp_path/'run'/'SCIENCE_OPEN_PIPELINE_STATUS.json'
        status=json.loads(status_path.read_text());status['handoff_hash']=canonical_hash(handoff);status_path.write_text(json.dumps(status))
        with pytest.raises(ValueError,match='canonical'):assembly.scientific_authoring_source()
    finally:policy.close()


def test_actual_pipeline_interruption_queries_existing_purchase_and_resumes(tmp_path):
    fired=[]
    def interrupt(stage):
        if stage=='after_provider_return' and not fired:
            fired.append(True);raise KeyboardInterrupt('purchase returned before local receipt')
    assembly,calls,built=assemble(tmp_path,fault_hook=interrupt)
    try:
        with pytest.raises(KeyboardInterrupt):assembly.run()
        loop=built[0];gate=loop.runner.gates['R']
        assert gate.usage()['calls']==1
        assert any(r[0] for r in gate.db.execute("SELECT key FROM jobs WHERE receipt LIKE '%unknown_outcome%'"))
        result=assembly.resume()
        assert next(r for r in result.receipts if r.stage=='execution').status=='completed',result.validation_errors
        provider=assembly.pipeline.science_policy._resources[-1]
        count=provider.db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0]
        assert count==gate.usage()['calls'] and count>=2
        assert assembly.scientific_authoring_source().result.selected_result_ref==assembly.pipeline.science_policy.selected_result.ref()
        assert not calls
    finally:assembly.pipeline.science_policy.close()


def test_unresolved_actual_purchase_is_not_repeated_by_pipeline_resume(tmp_path):
    fired=[]
    def interrupt(stage):
        if stage=='after_provider_return' and not fired:
            fired.append(True);raise KeyboardInterrupt('lost response')
    assembly,calls,built=assemble(tmp_path,fault_hook=interrupt,unknown_status=True)
    try:
        with pytest.raises(KeyboardInterrupt):assembly.run()
        result=assembly.resume()
        assert next(r for r in result.receipts if r.stage=='execution').status=='failed'
        gate=built[0].runner.gates['R'];provider=assembly.pipeline.science_policy._resources[-1]
        assert gate.usage()['calls']==1 and provider.db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0]==1
        assert not (tmp_path/'run'/'SCIENCE_OPEN_PIPELINE_HANDOFF.json').exists() and not calls
    finally:assembly.pipeline.science_policy.close()


def test_fresh_factory_recovers_durable_purchase_then_completed_handoff(tmp_path):
    fired=[]
    def interrupt(stage):
        if stage=='after_provider_return' and not fired:
            fired.append(True);raise KeyboardInterrupt('fresh-process recovery boundary')
    first,_,built=assemble(tmp_path,fault_hook=interrupt)
    with pytest.raises(KeyboardInterrupt):first.run()
    gate=built[0].runner.gates['R']
    original_key=gate.db.execute('SELECT key FROM jobs').fetchone()[0]
    provider=first.pipeline.science_policy._resources[-1]
    original_receipt=provider.db.execute('SELECT result FROM receipts WHERE key=?',(original_key,)).fetchone()[0]
    first.pipeline.science_policy.close()
    second,calls,rebuilt=assemble(tmp_path)
    try:
        result=second.resume()
        assert next(r for r in result.receipts if r.stage=='execution').status=='completed',result.validation_errors
        new_gate=rebuilt[0].runner.gates['R'];new_provider=second.pipeline.science_policy._resources[-1]
        assert len(new_gate.receipt(original_key).attempts)==1
        assert new_provider.db.execute('SELECT result FROM receipts WHERE key=?',(original_key,)).fetchone()[0]==original_receipt
        assert new_provider.db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0]==new_gate.usage()['calls']
        selected=second.scientific_authoring_source().result.selected_result_ref
        # Re-finalizing the loop creates no new handoff revision or purchases.
        before=rebuilt[0].repository.latest_snapshot().snapshot_id
        second.pipeline.science_policy.finalize([],tmp_path/'run')
        assert rebuilt[0].repository.latest_snapshot().snapshot_id==before
        assert not calls
    finally:second.pipeline.science_policy.close()
    third,calls,built=assemble(tmp_path)
    assert third.resume().result_id==result.result_id
    assert built==[] and not calls
    assert third.scientific_authoring_source().result.selected_result_ref==selected
    assert built==[]  # Completed authoring reads never construct a fresh loop.


def test_release_legacy_shadow_disable_lazy_builder_active_allows_it(tmp_path,fake_runtime):
    from optomind_optics.harness.science.release_manifest import ReleaseManager
    manager=ReleaseManager(tmp_path/'release')
    manifest=release(tmp_path/'bundle')
    try:
        for sequence,mode in enumerate(('legacy','shadow','active')):
            manager.activate(manifest,tmp_path/'bundle',mode,expected_sequence=sequence,reason='offline mode contract')
            calls=[];invoked=[]
            def builder(path):
                invoked.append(True);raise AssertionError('assembly must not open loop')
            factory=make_factory(tmp_path/mode,tmp_path/'release')
            assembly=assemble_real(factory,calls,open_research_builder=builder)
            if mode=='active':assert assembly.pipeline.science_policy is not None
            else:assert assembly.pipeline.science_policy is None
            assert invoked==[] and not calls
    finally:manager.close()
