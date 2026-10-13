import json,sqlite3
from pathlib import Path
import pytest
from optomind_optics.harness.science.jobs import ExecutionGate,ToolCapability
from optomind_optics.harness.science.compute_ledger import ComputeLedger,active_compute_ledger,compute_scope,computational_event
from optomind_optics.harness.science.program_ir import ProgramIR,ProgramNode
from optomind_optics.harness.science.program_compiler import compile_program
from optomind_optics.harness.science.program_runner import ProgramRunner

class Provider:
    def __init__(self):self.calls=0
    def submit(self,key,payload):
        self.calls+=1
        return {'state':'completed','cost':{'calls':1.},'values':[1.,3.]}
    def status(self,key):return {'state':'unknown_outcome'}


def restrict_database_pages(db):
    pages=db.execute('PRAGMA page_count').fetchone()[0]
    assert db.execute(f'PRAGMA max_page_count={pages}').fetchone()[0]==pages


def test_actual_sqlite_full_reservation_never_calls_provider(tmp_path):
    gate=ExecutionGate(tmp_path/'gate.sqlite',limits={'calls':2.},capability=ToolCapability(effect_class='external_measurement',domain='test',channels=('R',),supports_status=True))
    provider=Provider();restrict_database_pages(gate.db)
    try:
        with pytest.raises(sqlite3.OperationalError,match='full'):
            gate.execute(provider,'request',{'channel':'R'},estimate={'calls':1.},prediction_commit={'frozen':'x'*1000000})
        assert provider.calls==0
        assert gate.receipt('request') is None and gate.usage()['calls']==0
    finally:gate.close()


def test_actual_sqlite_full_compute_reservation_never_enters_work(tmp_path):
    ledger=ComputeLedger(tmp_path/'compute.sqlite',{'model_predictions':2});restrict_database_pages(ledger.db);entered=[]
    try:
        with pytest.raises(sqlite3.OperationalError,match='full'):
            with active_compute_ledger(ledger),compute_scope('x'*1000000),computational_event('model_predictions'):
                entered.append('physical work')
        assert entered==[] and ledger.snapshot()['usage']=={}
    finally:ledger.close()


class CommitFailure:
    def __init__(self,db,fail_at):self.db=db;self.count=0;self.fail_at=fail_at
    def __getattr__(self,name):return getattr(self.db,name)
    def commit(self):
        self.count+=1
        if self.count==self.fail_at:raise sqlite3.OperationalError('injected database or disk is full')
        return self.db.commit()


@pytest.mark.parametrize('fail_at',[1,2])
def test_gate_reservation_or_submitting_commit_failure_never_calls_provider(tmp_path,fail_at):
    gate=ExecutionGate(tmp_path/'gate.sqlite',limits={'calls':2.},capability=ToolCapability(effect_class='external_measurement',domain='test',channels=('R',),supports_status=True))
    provider=Provider();real=gate.db;gate.db=CommitFailure(real,fail_at)
    try:
        with pytest.raises(sqlite3.OperationalError,match='full'):
            gate.execute(provider,'request',{},estimate={'calls':1.},prediction_commit={'frozen':True})
        assert provider.calls==0
    finally:gate.close()


def test_program_runner_derived_receipt_corruption_is_rejected(tmp_path):
    gate=ExecutionGate(tmp_path/'gate.sqlite',limits={'calls':2.},capability=ToolCapability(effect_class='external_measurement',domain='test',channels=('R',),supports_status=True))
    provider=Provider();runner=ProgramRunner(tmp_path/'program.sqlite',gates={'R':gate},providers={'R':provider})
    program=ProgramIR('derived-cache',(ProgramNode('raw','new_evidence',channel='R',sample_id='s',estimate=(('calls',1.),)),ProgramNode('mean','derived_view',('raw',),arguments=(('statistic','mean'),))),(('s','ready'),))
    plan=compile_program(program,capabilities={'R':gate.capability},limits={'calls':2.})
    try:
        first=runner.run(plan,prediction_commits={'raw':{'frozen':True}})
        assert first.nodes['mean']['value']==2.
        bad=dict(first.nodes['mean']);bad['value']=999.
        with runner.db:runner.db.execute('UPDATE program_nodes SET receipt=? WHERE node=?',(json.dumps(bad),'mean'))
        # Safety expectation: reject or reconstruct from committed raw values.
        with pytest.raises(ValueError,match='cached program receipt'):
            runner.run(plan,prediction_commits={'raw':{'frozen':True}})
        assert provider.calls==1
    finally:runner.close();gate.close()


@pytest.mark.parametrize('corrupt',['raw','branch','sample_state','prediction'])
def test_cached_prefix_tamper_rejects_before_pending_purchase(tmp_path,corrupt):
    gate=ExecutionGate(tmp_path/'gate.sqlite',limits={'calls':3.},capability=ToolCapability(effect_class='external_measurement',domain='test',channels=('R',),supports_status=True))
    provider=Provider();runner=ProgramRunner(tmp_path/'program.sqlite',gates={'R':gate},providers={'R':provider})
    nodes=(ProgramNode('raw','new_evidence',channel='R',sample_id='s',estimate=(('calls',1.),)),
        ProgramNode('mean','derived_view',('raw',)),
        ProgramNode('branch','branch',('mean',),arguments=(('comparison','gt'),('threshold',1.))),
        ProgramNode('next','new_evidence',('raw',),channel='R',sample_id='s',condition=('branch',True),estimate=(('calls',1.),)))
    program=ProgramIR('prefix',nodes,(('s','ready'),));plan=compile_program(program,capabilities={'R':gate.capability},limits={'calls':3.})
    commits={'raw':{'frozen':True},'next':{'frozen':True}}
    def stop(stage):
        if stage=='before_submit' and provider.calls==1:raise RuntimeError('interrupt before second purchase')
    gate.fault_hook=stop
    try:
        with pytest.raises(RuntimeError,match='interrupt'):runner.run(plan,prediction_commits=commits)
        assert provider.calls==1
        gate.fault_hook=lambda stage:None
        if corrupt in {'raw','branch'}:
            receipt=json.loads(runner.db.execute('SELECT receipt FROM program_nodes WHERE node=?',(corrupt,)).fetchone()[0])
            if corrupt=='raw':receipt['values']=[99.,3.]
            else:receipt['value']=False
            with runner.db:runner.db.execute('UPDATE program_nodes SET receipt=? WHERE node=?',(json.dumps(receipt),corrupt))
        elif corrupt=='sample_state':
            with runner.db:runner.db.execute('UPDATE programs SET samples=?',(json.dumps({'s':'destroyed'}),))
        else:commits['raw']={'frozen':'changed'}
        with pytest.raises(ValueError,match='cached'):runner.run(plan,prediction_commits=commits)
        assert provider.calls==1
    finally:runner.close();gate.close()


def test_legacy_payload_journal_replays_without_new_purchase(tmp_path):
    from optomind_optics.harness.science.contracts import canonical_hash
    gate=ExecutionGate(tmp_path/'gate.sqlite',limits={'calls':2.},capability=ToolCapability(effect_class='external_measurement',domain='test',channels=('R',),supports_status=True))
    provider=Provider();runner=ProgramRunner(tmp_path/'program.sqlite',gates={'R':gate},providers={'R':provider})
    node=ProgramNode('raw','new_evidence',channel='R',sample_id='s',estimate=(('calls',1.),))
    program=ProgramIR('legacy',(node,),(('s','ready'),));plan=compile_program(program,capabilities={'R':gate.capability},limits={'calls':2.})
    try:
        runner.run(plan,prediction_commits={'raw':{'frozen':True}})
        # Prior payload schema omitted state fields; its original hash still binds all actual inputs.
        legacy={k:v for k,v in runner._payload(program,node).items() if k not in {'required_state','resulting_state'}}
        with gate.db:gate.db.execute('UPDATE jobs SET payload_hash=?',(canonical_hash(legacy),))
        result=runner.run(plan,prediction_commits={'raw':{'frozen':True}})
        assert result.state=='completed' and provider.calls==1
    finally:runner.close();gate.close()


def test_program_registration_sqlite_full_prevents_all_purchases(tmp_path):
    gate=ExecutionGate(tmp_path/'gate.sqlite',limits={'calls':2.},capability=ToolCapability(effect_class='external_measurement',domain='test',channels=('R',),supports_status=True))
    provider=Provider();runner=ProgramRunner(tmp_path/'program.sqlite',gates={'R':gate},providers={'R':provider})
    node=ProgramNode('raw','new_evidence',channel='R',sample_id='s',estimate=(('calls',1.),))
    program=ProgramIR('p'*1000000,(node,),(('s','ready'),));plan=compile_program(program,capabilities={'R':gate.capability},limits={'calls':2.})
    restrict_database_pages(runner.db)
    try:
        with pytest.raises(sqlite3.OperationalError,match='full'):runner.run(plan,prediction_commits={'raw':{'frozen':True}})
        assert provider.calls==0 and gate.usage()['calls']==0
    finally:runner.close();gate.close()


def test_repository_sqlite_full_never_publishes_partial_snapshot(tmp_path):
    from optomind_optics.harness.science.repository import ScienceRepository
    from optomind_optics.harness.science.contracts import ResearchTask
    repo=ScienceRepository(tmp_path/'repository');restrict_database_pages(repo.db)
    task=ResearchTask(object_id='t'*1000000,source_run_id='full-db-test',question='source-bound fixture')
    try:
        with pytest.raises(sqlite3.OperationalError,match='full'):repo.commit([task],task_ref=task.ref(),evidence_cutoff='before')
        assert repo.latest_snapshot() is None and repo.rebuild_projection()=={}
        with pytest.raises(ValueError,match='uncommitted'):repo.load(task.ref())
    finally:repo.close()
