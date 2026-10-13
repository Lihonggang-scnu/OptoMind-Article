from dataclasses import replace
import pytest
from optomind_optics.harness.science.jobs import ExecutionGate,ToolCapability
from optomind_optics.harness.science.program_ir import ProgramIR,ProgramNode
from optomind_optics.harness.science.program_compiler import compile_program
from optomind_optics.harness.science.program_runner import ProgramRunner
from optomind_optics.harness.science.program_search import adaptive_measurement_program


class Provider:
    def __init__(self,flat=False):self.calls=0;self.results={};self.flat=flat
    def submit(self,key,payload):
        self.calls+=1
        result={"state":"completed","values":[.1,.1 if self.flat else .8],"cost":{"calls":1.,"cost":1.}}
        self.results[key]=result;return result
    def status(self,key):return self.results[key]


def setup(tmp_path,flat=False,fault_hook=None):
    cap=ToolCapability(effect_class="external_measurement",domain="synthetic",channels=("R",),supports_status=True)
    limits={"calls":3.,"cost":3.}
    program=adaptive_measurement_program(program_id="adaptive",channel="R",reference_channel="R",coarse_grid=[500,600],fine_grid=[500,550,600],threshold=.2)
    plan=compile_program(program,capabilities={"R":cap},limits=limits)
    gate=ExecutionGate(tmp_path/"jobs.sqlite",limits=limits,capability=cap,fault_hook=fault_hook)
    provider=Provider(flat);runner=ProgramRunner(tmp_path/"program.sqlite",gates={"R":gate},providers={"R":provider})
    return plan,gate,provider,runner


@pytest.mark.parametrize("flat,calls",[(False,3),(True,2)])
def test_all_adaptive_branches_preserve_shared_reference_and_resume(tmp_path,flat,calls):
    plan,gate,provider,runner=setup(tmp_path,flat)
    predictions={key:{"prediction_id":key} for key in ("calibrate","coarse","refine")}
    receipt=runner.run(plan,prediction_commits=predictions)
    assert receipt.state=="completed" and provider.calls==calls
    assert not receipt.nodes["variation"]["new_evidence"]
    assert receipt.nodes["calibrate"]["correlation_group"]==receipt.nodes["coarse"]["correlation_group"]
    runner.close();runner=ProgramRunner(tmp_path/"program.sqlite",gates={"R":gate},providers={"R":provider})
    assert runner.run(plan,prediction_commits=predictions).state=="completed" and provider.calls==calls


def test_unknown_outcome_queries_provider_without_repeating_measurement(tmp_path):
    fired=[]
    def fault(stage):
        if stage=="after_provider_return" and not fired:fired.append(1);raise RuntimeError("interruption")
    plan,gate,provider,runner=setup(tmp_path,fault_hook=fault)
    predictions={key:{"prediction_id":key} for key in ("calibrate","coarse","refine")}
    with pytest.raises(RuntimeError):runner.run(plan,prediction_commits=predictions)
    assert provider.calls==1
    assert runner.run(plan,prediction_commits=predictions).state=="completed" and provider.calls==3


def test_unregistered_channel_budget_and_destroyed_sample_fail_closed(tmp_path):
    plan,gate,provider,runner=setup(tmp_path)
    with pytest.raises(ValueError,match="unregistered"):compile_program(plan.program,capabilities={},limits={"cost":3,"calls":3})
    with pytest.raises(ValueError,match="resource bound"):compile_program(plan.program,capabilities={"R":gate.capability},limits={"cost":1,"calls":1})
    destroy=ProgramNode("destroy","state_transition",channel="R",sample_id="s",resulting_state="destroyed",estimate=(("calls",1.),))
    measure=ProgramNode("measure","new_evidence",("destroy",),channel="R",sample_id="s",estimate=(("calls",1.),))
    with pytest.raises(ValueError,match="state precondition"):compile_program(ProgramIR("bad",(destroy,measure),(("s","ready"),)),capabilities={"R":gate.capability},limits={"calls":3})


def test_conditional_destruction_cannot_hide_illegal_reuse(tmp_path):
    plan,gate,provider,runner=setup(tmp_path)
    nodes=plan.program.nodes[:4]+(ProgramNode("destroy","state_transition",channel="R",sample_id="sample",resulting_state="destroyed",condition=("refine_needed",True),estimate=(("calls",1.),)),ProgramNode("after","new_evidence",channel="R",sample_id="sample",estimate=(("calls",1.),)))
    with pytest.raises(ValueError,match="legal branch"):
        compile_program(replace(plan.program,nodes=nodes),capabilities={"R":gate.capability},limits={"calls":10.,"cost":10.})


def test_adaptive_program_executes_actual_purchased_optical_world(tmp_path):
    from optomind_eval.worlds import make_world
    from optomind_eval.service import MeasurementService
    from optomind_optics.harness.science.world_client import WorldClient,SyntheticMeasurementProvider
    plan,gate,_,runner=setup(tmp_path)
    worlds=[make_world("F0",task_id="reference",split="V",seed=31),make_world("F0",task_id="sample",split="V",seed=32)]
    client=WorldClient(MeasurementService(worlds,budget=3).handle)
    provider=SyntheticMeasurementProvider(client,sample_tasks={"reference":"reference","sample":"sample"})
    runner.providers={"R":provider}
    predictions={key:{"prediction_id":key} for key in ("calibrate","coarse","refine")}
    receipt=runner.run(plan,prediction_commits=predictions)
    assert receipt.state=="completed"
    assert receipt.nodes["coarse"]["provider_receipt"]["measurement_receipt"]["task_id"]=="sample"
