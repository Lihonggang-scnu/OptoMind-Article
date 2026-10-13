from concurrent.futures import ThreadPoolExecutor
import pytest
from optomind_optics.harness.science.jobs import ExecutionGate, ToolCapability
CAP=ToolCapability(effect_class="compute",domain="TMM",supports_status=True,supports_cancel=True)
class Provider:
    def __init__(self):self.calls=0;self.results={}
    def submit(self,key,payload):
        self.calls+=1; self.results[key]={"state":"completed","cost":{"forward":3}};return self.results[key]
    def status(self,key):return self.results.get(key,{"state":"unknown_outcome"})
    def cancel(self,key):return {"state":"cancelled","cost":{"forward":2}}

def test_unknown_outcome_query_first_no_repeat_and_cost_retained(tmp_path):
    def fault(stage):
        if stage=="after_provider_return":raise RuntimeError("crash")
    gate=ExecutionGate(tmp_path/"jobs.db",limits={"forward":10},capability=CAP,fault_hook=fault); provider=Provider()
    with pytest.raises(RuntimeError):gate.execute(provider,"key",{},estimate={"forward":8},prediction_commit={"hash":"frozen"})
    assert gate.receipt("key").state=="unknown_outcome" and gate.usage()["forward"]==8
    gate.close(); gate=ExecutionGate(tmp_path/"jobs.db",limits={"forward":10},capability=CAP)
    assert gate.execute(provider,"key",{},estimate={"forward":8},prediction_commit={"hash":"frozen"}).state=="completed"
    assert provider.calls==1 and gate.usage()["forward"]==3
    with pytest.raises(ValueError):gate.execute(provider,"key",{"changed":1},estimate={"forward":8},prediction_commit={"hash":"frozen"})

def test_cancel_preserves_consumed_and_deduplicates(tmp_path):
    gate=ExecutionGate(tmp_path/"jobs.db",limits={"forward":10},capability=CAP)
    gate.reserve("a",{},estimate={"forward":8},prediction_commit={"x":1})
    gate.cancel(Provider(),"a");gate.cancel(Provider(),"a")
    assert gate.usage()["forward"]==2
    assert gate.db.execute("SELECT count(*) FROM costs").fetchone()[0]==2

def test_parallel_hard_budget(tmp_path):
    path=tmp_path/"jobs.db";ExecutionGate(path,limits={"forward":10},capability=CAP).close()
    def reserve(i):
        gate=ExecutionGate(path,limits={"forward":10},capability=CAP)
        try:gate.reserve(str(i),{},estimate={"forward":6},prediction_commit={"x":1});return True
        except ValueError:return False
        finally:gate.close()
    with ThreadPoolExecutor(2) as pool: assert sum(pool.map(reserve,range(2)))==1

def test_unknown_without_status_never_retries(tmp_path):
    cap=CAP.model_copy(update={"supports_status":False})
    gate=ExecutionGate(tmp_path/"jobs.db",limits={"forward":10},capability=cap)
    old=gate.reserve("a",{},estimate={"forward":8},prediction_commit={"x":1})
    gate._update(old.model_copy(update={"state":"unknown_outcome"}))
    provider=Provider(); assert gate.recover(provider,"a").state=="unknown_outcome"
    assert provider.calls==0 and gate.usage()["forward"]==8

def test_real_article_coordinator_consumes_gate_and_science_repository(tmp_path):
    from test_article_execution import _request, _run_result, _stack, _task, FakeHarness
    from optomind_optics.harness.article_execution import ArticleExecutionCoordinator
    from optomind_optics.harness.science.repository import ScienceRepository
    from optomind_optics.harness.science.contracts import ResearchTask
    request=_request();harness=FakeHarness(_run_result());stack=_stack(tmp_path,harness=harness)
    stack["registry"].register(request.task_hash,_task())
    gate=ExecutionGate(tmp_path/"gate.db",limits=dict(request.requested_budget),capability=CAP)
    repo=ScienceRepository(tmp_path/"science")
    task=ResearchTask(object_id="task",source_run_id=request.run_id,question="coating")
    repo.commit([task],task_ref=task.ref(),evidence_cutoff="before")
    coordinator=ArticleExecutionCoordinator(gateway=stack["gateway"],adapter=stack["adapter"],execution_gate=gate,prediction_commit={"expected_discriminator":dict(request.experiment.expected_discriminator)},science_repository=repo,science_task_ref=task.ref())
    first=coordinator.execute(request);second=coordinator.execute(request)
    assert first.observation==second.observation
    assert len(harness.calls)==1
    assert gate.usage()["forward_evaluations"]==40
    assert repo.latest_snapshot().committed_sequence==2

def test_actual_article_provider_return_crash_recovers_without_rerun(tmp_path):
    from test_article_execution import _request, _run_result, _stack, _task, FakeHarness
    request=_request();harness=FakeHarness(_run_result());stack=_stack(tmp_path,harness=harness)
    stack["registry"].register(request.task_hash,_task())
    def crash(stage):
        if stage=="after_provider_return": raise RuntimeError("provider succeeded before gate persisted")
    gate=ExecutionGate(tmp_path/"durable.db",limits=dict(request.requested_budget),capability=CAP,fault_hook=crash)
    with pytest.raises(RuntimeError):stack["gateway"].execute_durable(request,stack["adapter"],gate=gate,prediction_commit={"frozen":1})
    gate.close();gate=ExecutionGate(tmp_path/"durable.db",limits=dict(request.requested_budget),capability=CAP)
    receipt=stack["gateway"].execute_durable(request,stack["adapter"],gate=gate,prediction_commit={"frozen":1})
    assert receipt.state=="completed" and len(harness.calls)==1
    assert gate.usage()["forward_evaluations"]==40
