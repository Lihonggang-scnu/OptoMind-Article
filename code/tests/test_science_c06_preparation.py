import numpy as np
import pytest
from test_science_c06_model_compiler import film_model
from optomind_optics.harness.science.model_compiler import compile_model
from optomind_optics.harness.science.preparation_effects import PreparationEffect,SimulationPreparationProvider
from optomind_optics.harness.science.program_ir import ProgramIR,ProgramNode
from optomind_optics.harness.science.program_compiler import compile_program
from optomind_optics.harness.science.program_runner import ProgramRunner
from optomind_optics.harness.science.jobs import ToolCapability,ExecutionGate
from optomind_optics.harness.science.compute_ledger import ComputeLedger,active_compute_ledger


def test_registered_preparation_changes_actual_forward_and_replays_once(tmp_path):
    model=compile_model(film_model());wl=(500.,600.,700.)
    effect=PreparationEffect('deposit','synthetic/1',('declared-fixture-law',),'ready','coated','d','nm',20.,(50.,180.),(50.,200.))
    provider=SimulationPreparationProvider(tmp_path/'provider.sqlite',model=model,initial_samples={'sample':('ready',{'d':100.})},effects=(effect,),wavelengths_nm=wl)
    caps={'prepare':ToolCapability(effect_class='compute',domain='synthetic',channels=('prepare',),supports_status=True),
          'R':ToolCapability(effect_class='external_measurement',domain='synthetic',channels=('R',),supports_status=True)}
    gates={c:ExecutionGate(tmp_path/(c+'.sqlite'),limits={'calls':3.,'cost':1.},capability=cap) for c,cap in caps.items()}
    runner=ProgramRunner(tmp_path/'runner.sqlite',gates=gates,providers={c:provider for c in caps})
    ledger=ComputeLedger(tmp_path/'compute.sqlite',{'model_predictions':10,'solver_points':30})
    nodes=(ProgramNode('before','new_evidence',channel='R',sample_id='sample',estimate=(('calls',1.),('cost',0.))),
        ProgramNode('prepare','state_transition',('before',),channel='prepare',sample_id='sample',resulting_state='coated',arguments=(('effect_id','deposit'),),estimate=(('calls',1.),('cost',0.))),
        ProgramNode('after','new_evidence',('prepare',),channel='R',sample_id='sample',required_state='coated',estimate=(('calls',1.),('cost',0.))))
    program=ProgramIR('preparation',nodes,(('sample','ready'),));plan=compile_program(program,capabilities=caps,limits={'calls':3.,'cost':1.})
    try:
        with active_compute_ledger(ledger):
            receipt=runner.run(plan,prediction_commits={n.node_id:{'frozen':True} for n in nodes})
            replay=runner.run(plan,prediction_commits={n.node_id:{'frozen':True} for n in nodes})
        from dataclasses import asdict
        from optomind_optics.harness.science.contracts import canonical_hash
        assert canonical_hash(asdict(receipt))==canonical_hash(asdict(replay))
        assert not np.allclose(receipt.nodes['before']['values'],receipt.nodes['after']['values'])
        assert np.allclose(receipt.nodes['after']['values'],model.predict({'d':120.},wl))
        assert receipt.sample_states=={'sample':'coated'}
        assert receipt.nodes['prepare']['new_evidence'] is False
        assert ledger.snapshot()['usage']['model_predictions']==2
        assert sum(g.usage()['calls'] for g in gates.values())==3
        with pytest.raises(ValueError,match='unregistered'):
            provider.submit('unknown',{'channel':'prepare','sample_id':'sample','arguments':{'effect_id':'unknown'}})
    finally:
        runner.close();provider.close();ledger.close()
        for gate in gates.values():gate.close()


def test_preparation_unit_domain_and_unknown_sample_fail_closed(tmp_path):
    from dataclasses import replace
    model=compile_model(film_model())
    effect=PreparationEffect('deposit','1',('fixture',),'ready','coated','d','nm',20.,(50.,180.),(50.,200.))
    for invalid in (replace(effect,unit='um'),replace(effect,output_bounds=(50.,201.))):
        with pytest.raises(ValueError):SimulationPreparationProvider(tmp_path/'bad.sqlite',model=model,initial_samples={'s':('ready',{'d':100.})},effects=(invalid,),wavelengths_nm=(500.,600.))
    provider=SimulationPreparationProvider(tmp_path/'good.sqlite',model=model,initial_samples={'s':('ready',{'d':190.})},effects=(effect,),wavelengths_nm=(500.,600.))
    try:
        with pytest.raises(ValueError,match='domain'):provider.submit('bad',{'channel':'prepare','sample_id':'s','required_state':'ready','resulting_state':'coated','arguments':{'effect_id':'deposit'}})
        with pytest.raises(ValueError,match='unknown'):provider.submit('missing',{'channel':'R','sample_id':'missing','required_state':'ready','arguments':{}})
        assert provider.status('bad')['state']=='unknown_outcome'
    finally:provider.close()


def test_contradictory_preparation_receipt_blocks_downstream_measurement(tmp_path):
    class Contradictory:
        calls=0
        def submit(self,key,payload):
            self.calls+=1
            return {'state':'completed','cost':{'calls':1.},'sample_id':'s','resulting_state':'destroyed','effect_ref':'test-law'}
        def status(self,key):return {'state':'unknown_outcome'}
    provider=Contradictory();cap=ToolCapability(effect_class='compute',domain='synthetic',channels=('prepare',),supports_status=True)
    gate=ExecutionGate(tmp_path/'gate.sqlite',limits={'calls':2.},capability=cap)
    runner=ProgramRunner(tmp_path/'runner.sqlite',gates={'prepare':gate},providers={'prepare':provider})
    node=ProgramNode('prepare','state_transition',channel='prepare',sample_id='s',resulting_state='coated',estimate=(('calls',1.),))
    plan=compile_program(ProgramIR('bad-receipt',(node,),(('s','ready'),)),capabilities={'prepare':cap},limits={'calls':1.})
    try:
        with pytest.raises(ValueError,match='postcondition'):runner.run(plan,prediction_commits={'prepare':{'frozen':True}})
        assert provider.calls==1 and gate.usage()['calls']==1
        with pytest.raises(ValueError,match='postcondition'):runner.run(plan,prediction_commits={'prepare':{'frozen':True}})
        assert provider.calls==1
    finally:runner.close();gate.close()
