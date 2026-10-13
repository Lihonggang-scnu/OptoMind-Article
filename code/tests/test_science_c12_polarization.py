from dataclasses import asdict
import numpy as np
import pytest
from test_science_c06_model_compiler import film_model
from optomind_optics.harness.science.program_search import polarization_measurement_program
from optomind_optics.harness.science.program_compiler import compile_program
from optomind_optics.harness.science.program_runner import ProgramRunner
from optomind_optics.harness.science.polarization_provider import SyntheticPolarizationProvider
from optomind_optics.harness.science.jobs import ToolCapability,ExecutionGate
from optomind_optics.harness.science.compute_ledger import ComputeLedger,active_compute_ledger
from optomind_optics.harness.science.contracts import canonical_hash


def reference_s(wl,angle):
    # Independent oblique-incidence Airy amplitude, no solver calls.
    sine=np.sin(np.deg2rad(angle));c0=np.cos(np.deg2rad(angle));c1=np.sqrt(1-(sine/2.)**2);c2=np.sqrt(1-(sine/1.5)**2)
    r01=(c0-2*c1)/(c0+2*c1);r12=(2*c1-1.5*c2)/(2*c1+1.5*c2)
    phase=np.exp(4j*np.pi*2*100*c1/np.asarray(wl))
    return np.abs((r01+r12*phase)/(1+r01*r12*phase))**2


@pytest.mark.parametrize('threshold,expected_calls,refines',[(0.,5,True),(2.,3,False)])
def test_generated_calibrate_polarization_coarse_refine_or_stop_recovers(tmp_path,threshold,expected_calls,refines):
    coarse=(450.,600.,800.);fine=tuple(np.linspace(500.,700.,9));angle=35.
    program=polarization_measurement_program(program_id='polarized',coarse_grid=coarse,fine_grid=fine,threshold=threshold,expected_reference=tuple(reference_s(coarse,angle)))
    caps={channel:ToolCapability(effect_class='external_measurement',domain='explicit synthetic s/p optical fixture',channels=(channel,),supports_status=True) for channel in ('R_s','R_p')}
    with pytest.raises(ValueError,match='unregistered'):compile_program(program,capabilities={'R_s':caps['R_s']},limits={'calls':5.,'cost':5.})
    plan=compile_program(program,capabilities=caps,limits={'calls':5.,'cost':5.})
    assert dict(plan.resource_bounds)=={'calls':5.,'cost':5.}
    samples={'sample':(film_model(),{'d':120.}),'reference':(film_model(),{'d':100.})}
    provider=SyntheticPolarizationProvider(tmp_path/'provider.sqlite',samples=samples,angle_deg=angle,detector_offset=.07)
    gates={c:ExecutionGate(tmp_path/(c+'.sqlite'),limits={'calls':5.,'cost':5.},capability=cap) for c,cap in caps.items()}
    runner=ProgramRunner(tmp_path/'program.sqlite',gates=gates,providers={c:provider for c in caps})
    ledger=ComputeLedger(tmp_path/'compute.sqlite',{'model_predictions':30,'solver_points':300})
    try:
        with active_compute_ledger(ledger):
            predictions={n.node_id:{'model_hash':provider.models[n.sample_id,n.channel].model_id,'parameters':samples[n.sample_id][1],
                'values':provider.models[n.sample_id,n.channel].predict(samples[n.sample_id][1],dict(n.arguments)['wavelengths_nm'],angle_deg=angle).tolist(),
                'channel':n.channel,'angle_deg':angle} for n in program.nodes if n.kind=='new_evidence'}
            first=runner.run(plan,prediction_commits=predictions)
        assert first.nodes['refine_needed']['value'] is refines
        assert first.nodes['refine_s']['state']==('completed' if refines else 'skipped')
        assert first.nodes['refine_p']['state']==('completed' if refines else 'skipped')
        assert first.nodes['stop']['state']==('skipped' if refines else 'completed')
        assert np.allclose(first.nodes['corrected_s']['values'],predictions['coarse_s']['values'],atol=1e-13)
        assert np.allclose(first.nodes['corrected_p']['values'],predictions['coarse_p']['values'],atol=1e-13)
        assert first.nodes['contrast']['new_evidence'] is False
        assert len(set(first.nodes['contrast']['shared_reference_ids']))==1
        assert sum(g.usage()['calls'] for g in gates.values())==expected_calls
        usage=ledger.snapshot()['usage'].copy()
        runner.close();provider.close()
        provider=SyntheticPolarizationProvider(tmp_path/'provider.sqlite',samples=samples,angle_deg=angle,detector_offset=.07)
        runner=ProgramRunner(tmp_path/'program.sqlite',gates=gates,providers={c:provider for c in caps})
        with active_compute_ledger(ledger):replay=runner.run(plan,prediction_commits=predictions)
        assert canonical_hash(asdict(first))==canonical_hash(asdict(replay))
        assert ledger.snapshot()['usage']==usage
        # Explicitly distinguish coordinate, topology, and registered-channel changes.
        assert len(fine)>len(coarse) and set(plan.channels)=={'R_s','R_p'}
        assert {n.kind for n in program.nodes}>={'branch','terminate','derived_view','new_evidence'}
    finally:
        runner.close();provider.close();ledger.close()
        for gate in gates.values():gate.close()
