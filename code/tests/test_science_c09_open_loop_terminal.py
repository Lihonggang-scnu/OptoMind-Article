from dataclasses import asdict, replace
import pytest
from tmm_engine import (LayerSpec, MediumSpec, StackSpec, SimulationTask, SpectralGrid,
                        IlluminationSpec, OptimizationTask, SpectralTarget, OptimizerSpec)
from optomind_optics.harness.science.open_research_cli import build_offline_loop
from optomind_optics.harness.science.open_research_loop import OpenResearchLoop
from optomind_optics.harness.science.contracts import ModelCandidate
from optomind_optics.harness.science.inference import FitResult
from optomind_optics.harness.science.identifiability import IdentifiabilityReport
from optomind_optics.harness.science.compute_ledger import active_compute_ledger


def test_actual_open_loop_terminal_uses_existing_de_and_rejects_manufacturing_boundary(tmp_path):
    base,resources=build_offline_loop(tmp_path,{'config':{'design_thicknesses_nm':[50.,100.,150.]}})
    cfg=replace(base.config,run_id='constrained-terminal',terminal_optimizer_budget=8,
                terminal_lower_nm=50.,terminal_upper_nm=150.,terminal_tolerance_nm=5.,
                terminal_loss_transform='exponential',terminal_risk_aversion=2.)
    task=OptimizationTask(simulation=SimulationTask(stack=StackSpec(layers=(
        LayerSpec(None,100.,constant_n=1.8,min_thickness_nm=50.,max_thickness_nm=150.),),
        incident=MediumSpec.air(),exit=MediumSpec(constant_n=1.5)),
        spectrum=SpectralGrid(start_nm=500.,stop_nm=600.,points=5),
        illumination=IlluminationSpec((0.,),('unpolarized',))),targets=(SpectralTarget('R',0.,500.,600.),),
        optimizer=OptimizerSpec(max_steps=10,starts=1,seed=23))
    loop=OpenResearchLoop(repository=base.repository,runner=base.runner,config=cfg,terminal_optimizer_task=task)
    try:
        candidate=loop._put(ModelCandidate(object_id='terminal-candidate',source_run_id=cfg.run_id,model_ir=asdict(loop.initial_model)))
        samples=({'film.n':1.7,'film.thickness_nm':100.},{'film.n':2.2,'film.thickness_nm':100.})
        fit=FitResult('model',samples[0],0.,True,'finite_posterior','declared finite test posterior',samples,(.5,.5),None,{},
                      IdentifiabilityReport(2,2,(1.,1.),(),(),'test finite support'),())
        with active_compute_ledger(loop.compute_ledger): problem,solution,_=loop._terminal(candidate,fit)
        assert 'thickness:50' not in problem.design_ids and 'thickness:150' not in problem.design_ids
        assert solution.decision_id in loop._terminal_designs
        receipts=[loop.repository.load(ref) for ref in loop.repository.latest_snapshot().object_refs]
        receipt=next(obj.summary for obj in receipts if hasattr(obj,'summary') and obj.summary.get('optimizer'))
        assert receipt['optimizer']['id']=='differential_evolution_thickness'
        assert receipt['optimizer']['evaluation_count']==8
        assert receipt['source_refs'] and receipt['constraint_ref']
        assert loop.compute_ledger.snapshot()['usage']['model_predictions']==receipt['cost_vector']['world_loss_evaluations']
        # A new posterior keeps a coherent reusable matrix but a distinct solution receipt.
        with active_compute_ledger(loop.compute_ledger): loop._terminal(candidate,replace(fit,weights=(.9,.1)))
        before=loop.compute_ledger.snapshot()['usage']['model_predictions']
        bad=replace(fit,samples=({'film.n':100.,'film.thickness_nm':100.},),weights=(1.,))
        with active_compute_ledger(loop.compute_ledger),pytest.raises((ValueError,RuntimeError)): loop._terminal(candidate,bad)
        assert loop.compute_ledger.snapshot()['usage']['model_predictions']==before+1
        assert loop.compute_ledger.snapshot()['events_by_status']['failed_consumed']>=1
    finally:
        loop.compute_ledger.close()
        for resource in resources:resource.close()
