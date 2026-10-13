from dataclasses import replace
import numpy as np
import pytest
from tmm_engine import (LayerSpec, MediumSpec, StackSpec, SimulationTask, SpectralGrid,
                        IlluminationSpec, OptimizationTask, SpectralTarget, OptimizerSpec)
from optomind_optics.harness.science.terminal_optimization import (
    TerminalConstraints, TerminalObjective, build_constrained_pool, optimize_common_pool, rescore_pool_union)
from optomind_optics.harness.science.evsi import exact_evsi
from optomind_optics.harness.science.compute_ledger import ComputeLedger, active_compute_ledger, computational_event

CTX={k:'v1' for k in ('task','objective','model','sample','material','solver','fidelity','loss','split')}
OBJ=TerminalObjective('v1','task:objective')

def task():
    return OptimizationTask(simulation=SimulationTask(stack=StackSpec(layers=(
        LayerSpec(None,100.,constant_n=2.,min_thickness_nm=50.,max_thickness_nm=150.),
        LayerSpec(None,60.,constant_n=1.45,optimizable=False)),incident=MediumSpec.air(),exit=MediumSpec(constant_n=1.5)),
        spectrum=SpectralGrid(start_nm=500.,stop_nm=600.,points=5),
        illumination=IlluminationSpec((0.,),('unpolarized',))),
        targets=(SpectralTarget('R',.1,500.,600.),),optimizer=OptimizerSpec(max_steps=6,starts=1,seed=23))


def test_explicit_manufacturing_feasibility_and_unknown_empty():
    c=TerminalConstraints('v1',('manufacturing:spec',),(50.,),(150.,),(5.,),145.)
    r=build_constrained_pool(('w',),{'bad-low':(50.,),'bad-total':(144.,),'safe':(100.,)},
        lambda w,d: -100 if d[0]!=100 else 1,CTX,'loss',c,OBJ,(1.,))
    assert r.problem.design_ids==('safe',)
    assert set(r.receipt['rejected_design_ids'])=={'bad-low','bad-total'}
    assert r.solution.risk==1
    assert r.receipt['solution']['feasibility']['feasible']
    e=build_constrained_pool(('w',),{'bad':(150.,)},lambda w,d:0,CTX,'loss',c,OBJ,(1.,))
    assert e.problem is None and e.solution is None
    assert e.receipt['status']=='unknown_no_feasible_design'


def test_nonlinear_expected_utility_changes_executable_continuation():
    c=TerminalConstraints('v1',('spec',),(0.,),(10.,))
    losses={'w0':{1.:0.,2.:1.1},'w1':{1.:2.,2.:1.1}}
    f=lambda w,d: losses[w][d[0]]
    kwargs=dict(world_ids=('w0','w1'),designs={'risky':(1.,),'safe':(2.,)},world_loss=f,context=CTX,
                units='utility_loss',constraints=c,probabilities=(.5,.5))
    plain=build_constrained_pool(**kwargs,objective=OBJ)
    risk=build_constrained_pool(**kwargs,objective=TerminalObjective('v2','task:risk','exponential',2.))
    assert plain.solution.decision_id=='risky'
    assert risk.solution.decision_id=='safe'
    assert risk.problem.cache_key!=plain.problem.cache_key
    report=exact_evsi(risk.problem,(.5,.5),np.eye(2),('lo','hi'))
    assert report.policy.design_ids==('risky','safe')
    assert report.posterior_risk < report.prior_risk


def test_versions_source_cost_identity_and_union_recomputes_both_risks():
    c=TerminalConstraints('v1',('spec',),(0.,),(10.,))
    f=lambda w,d: 1. if d[0]==3 else (0. if d[0]==w else 100.)
    kwargs=dict(world_ids=(1.,2.),world_loss=f,context=CTX,units='loss',constraints=c,objective=OBJ,
                probabilities=(.5,.5),kernel=np.eye(2),observation_ids=('a','b'))
    small,s= rescore_pool_union(pools=({'a':(1.,),'b':(2.,)},),**kwargs)
    big,b= rescore_pool_union(pools=({'a':(1.,),'b':(2.,)},{'safe':(3.,)}),**kwargs)
    assert (s.prior_risk,s.posterior_risk)==(50.,0.)
    assert (b.prior_risk,b.posterior_risk)==(1.,0.)
    assert big.receipt['cost_vector']['world_loss_evaluations']==6
    changed,_=rescore_pool_union(pools=({'a':(1.,),'b':(2.,)},),**dict(kwargs,constraints=replace(c,version='v2')))
    assert changed.problem.cache_key!=small.problem.cache_key
    changed,_=rescore_pool_union(pools=({'a':(1.,),'b':(2.,)},),**dict(kwargs,constraints=replace(c,source_refs=('spec2',))))
    assert changed.problem.cache_key!=small.problem.cache_key
    with pytest.raises(ValueError,match='identity reused'):
        rescore_pool_union(pools=({'a':(1.,)},{'a':(2.,)}),**kwargs)


def test_existing_multilayer_optimizer_actual_posterior_callback_budget_and_cost(tmp_path):
    c=TerminalConstraints('v1',('manufacturing',),(50.,60.),(150.,60.),max_total_nm=195.)
    count=[]
    def loss(w,d):
        with computational_event('model_predictions'):
            count.append((w,d))
            return ((d[0]-({'w0':80.,'w1':120.}[w]))/100.)**2
    ledger=ComputeLedger(tmp_path/'cost.sqlite',{'model_predictions':1000})
    with active_compute_ledger(ledger):
        r=optimize_common_pool(task=task(),world_ids=('w0','w1'),probabilities=(.8,.2),world_loss=loss,
            context=CTX,units='loss',constraints=c,objective=TerminalObjective('v1','risk','exponential'),
            incumbent_designs={'incumbent':(100.,60.),'bad-incumbent':(150.,60.)},budget=25)
    assert r.receipt['optimizer']['audit']['backend']=='scipy.optimize.differential_evolution'
    assert r.receipt['cost_vector']['optimizer_objective_evaluations'] <= 25
    assert ledger.snapshot()['usage']['model_predictions']==len(count)==r.receipt['cost_vector']['world_loss_evaluations']
    assert r.solution.risk <= np.expm1(.04) + 1e-12
    assert all(d[1]==60. and sum(d)<=195. for d in r.designs.values())
    assert r.receipt['continuous_design_error_bound'] is None
    assert 'unknown' in r.receipt['refinement_status']
    assert r.receipt['rejected_design_ids']
    ledger.close()


def test_invalid_objective_and_constraint_rejected():
    with pytest.raises(ValueError): TerminalConstraints('v1',(),(0.,),(1.,))
    with pytest.raises(ValueError): TerminalObjective('v1','source','cvar')
    with pytest.raises(ValueError): TerminalObjective('v1','source','exponential').loss(10000)


def test_pool_expansion_can_increase_value_and_joint_unit_conversion():
    c=TerminalConstraints('v1',('spec',),(0.,),(10.,))
    losses={'w0':{1.:2.,2.:0.,3.:4.},'w1':{1.:2.,2.:4.,3.:0.}}
    def value(designs,scale,units):
        return rescore_pool_union(pools=(designs,),world_ids=('w0','w1'),
            world_loss=lambda w,d:scale*losses[w][d[0]],context=dict(CTX,loss=f'scale:{scale}'),units=units,
            constraints=c,objective=OBJ,probabilities=(.5,.5),kernel=np.eye(2),observation_ids=('a','b'),cost=.1*scale)
    old,a=value({'safe':(1.,)},1.,'loss')
    new,b=value({'safe':(1.,),'a':(2.,),'b':(3.,)},1.,'loss')
    converted,z=value({'safe':(1.,),'a':(2.,),'b':(3.,)},1000.,'milliloss')
    assert a.value_interval==(0.,0.) and b.value_interval==(2.,2.)
    assert z.net_value_interval==pytest.approx(np.asarray(b.net_value_interval)*1000)
    assert converted.problem.cache_key!=new.problem.cache_key


def test_failed_actual_evaluation_is_consumed_in_shared_meter(tmp_path):
    c=TerminalConstraints('v1',('spec',),(0.,),(10.,))
    ledger=ComputeLedger(tmp_path/'failed.sqlite',{'model_predictions':10})
    def fail(w,d):
        with computational_event('model_predictions'): raise RuntimeError('physical forward failed')
    with active_compute_ledger(ledger),pytest.raises(RuntimeError):
        build_constrained_pool(('w',),{'a':(1.,)},fail,CTX,'loss',c,OBJ,(1.,))
    assert ledger.snapshot()['usage']['model_predictions']==1
    assert ledger.snapshot()['events_by_status']=={'failed_consumed':1}
    ledger.close()


def test_real_multilayer_physics_posterior_and_equal_total_forward_reference():
    from optomind_optics.harness.optimizer_registry import (
        DifferentialEvolutionThicknessAdapter, _simulation_with_thicknesses, _objective_from_forward)
    from tmm_engine import TMMWorkbench, MaterialRegistry
    t=task(); bench=TMMWorkbench(MaterialRegistry()); calls=[]
    def physical(world,design):
        simulation=_simulation_with_thicknesses(t,design)
        first=replace(simulation.stack.layers[0],constant_n={'low':1.7,'high':2.3}[world])
        simulation=replace(simulation,stack=replace(simulation.stack,layers=(first,*simulation.stack.layers[1:])))
        calls.append((world,design))
        loss,_=_objective_from_forward(t,bench.simulate(simulation))
        return loss
    objective=TerminalObjective('v1','task:exponential-risk','exponential',5.)
    r=optimize_common_pool(task=t,world_ids=('low','high'),probabilities=(.5,.5),world_loss=physical,
        context=CTX,units='utility_loss',constraints=TerminalConstraints('v1',('spec',),(50.,60.),(150.,60.)),
        objective=objective,incumbent_designs={'incumbent':(100.,60.)},budget=10)
    total=r.receipt['cost_vector']['world_loss_evaluations']
    assert total==len(calls)
    baseline=DifferentialEvolutionThicknessAdapter(max_iterations=100).optimize(t,maximum_forward_evaluations=total)
    assert baseline.evaluation_count==total
    # Independent comparison evaluation is the same two additional forwards for each policy.
    proposed=r.designs[r.solution.decision_id]
    proposed_risk=np.mean([objective.loss(physical(w,proposed)) for w in ('low','high')])
    baseline_risk=np.mean([objective.loss(physical(w,baseline.best_thicknesses_nm)) for w in ('low','high')])
    assert proposed_risk==pytest.approx(r.solution.risk)
    assert np.isfinite(baseline_risk)
    # A measured finite reference difference is not global regret or evidence of superiority.
    assert r.receipt['continuous_design_optimality']=='unknown'


def test_weight_updates_reuse_matrix_but_not_solution_receipt_identity():
    c=TerminalConstraints('v1',('spec',),(0.,),(10.,))
    def build(q):
        return build_constrained_pool(('left','right'),{'a':(1.,),'b':(2.,)},
            lambda w,d:float((w=='left')!=(d[0]==1.)),CTX,'loss',c,OBJ,q)
    left,right=build((.9,.1)),build((.1,.9))
    assert left.problem.cache_key==right.problem.cache_key
    assert left.solution.decision_id!=right.solution.decision_id
    assert left.receipt['posterior_ref']!=right.receipt['posterior_ref']


def test_exponential_objective_unit_change_rescales_inverse_loss_aversion():
    c=TerminalConstraints('v1',('spec',),(0.,),(10.,))
    def build(scale):
        return build_constrained_pool(('w0','w1'),{'a':(1.,),'b':(2.,)},
            lambda w,d:scale*({'w0':{1.:0.,2.:1.1},'w1':{1.:2.,2.:1.1}}[w][d[0]]),
            CTX,'loss' if scale==1 else 'milliloss',c,
            TerminalObjective('v1','source','exponential',2./scale),(.5,.5))
    a,b=build(1.),build(1000.)
    assert a.solution.decision_id==b.solution.decision_id
    assert b.solution.risk==pytest.approx(a.solution.risk*1000.)
    assert b.receipt['objective_units']['risk_aversion']=='1/(milliloss)'
