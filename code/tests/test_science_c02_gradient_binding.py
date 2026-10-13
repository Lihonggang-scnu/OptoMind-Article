import copy
import json
import numpy as np
import pytest
from optomind_optics.harness.science.contracts import ModelCandidate, ResearchTask
from optomind_optics.harness.science.gradient_binding import make_gradient_binding
from optomind_optics.harness.research_orchestrator import _compute_gradient_summary


def fixture():
    simulation={"stack":{"incident":{"constant_n":1.},"exit":{"constant_n":1.52},
        "layers":[{"constant_n":n,"thickness_nm":d,"optimizable":True} for n,d in [(2.2,83.),(1.45,127.),(2.1,51.)]]},
        "spectrum":{"values_nm":[510.,550.,610.,670.]},
        "illumination":{"angles_deg":[0.,35.],"polarizations":["s","p"]},"solver":"smatrix"}
    targets=[{"observable":"R","target":.02,"wavelength_min_nm":510.,"wavelength_max_nm":670.,
              "weight":1.,"angle_deg":0.,"polarization":"s","constraint":"at_most","aggregation":"mean"},
             {"observable":"T","target":.8,"wavelength_min_nm":550.,"wavelength_max_nm":670.,
              "weight":3.,"angle_deg":35.,"polarization":"p","constraint":"match","aggregation":"worst_case"}]
    candidate=ModelCandidate(object_id="adopted",source_run_id="r",model_ir={"simulation":simulation})
    task=ResearchTask(object_id="task",source_run_id="r",question="frozen multi-target objective",objective={"kind":"veritmm_nominal_loss","targets":targets},constraints={"max_total_nm":400.})
    return simulation,candidate,task


def request():
    pytest.importorskip("torch")
    simulation,candidate,task=fixture()
    return make_gradient_binding(simulation,candidate=candidate,objective_task=task,sample={"id":"sample-actual","state":"nominal"})


def numpy_loss(simulation,targets):
    from tmm_engine.workbench import TMMWorkbench
    from tmm_engine.material_registry import MaterialRegistry
    from tmm_engine.task_io import simulation_task_from_dict
    result=TMMWorkbench(MaterialRegistry()).simulate(simulation_task_from_dict(simulation))
    terms=[]
    for target in targets:
        mask=(result.wavelengths_nm>=target['wavelength_min_nm'])&(result.wavelengths_nm<=target['wavelength_max_nm'])
        values=result.channel(target['angle_deg'],target['polarization'])[target['observable']][mask]
        errors=(values-target['target'])**2 if target['constraint']=='match' else (1-values)**2 if target['constraint']=='at_least' else values**2
        terms.append(target['weight']*(np.max(errors) if target['aggregation']=='worst_case' else np.mean(errors)))
    return sum(terms)/sum(t['weight'] for t in targets)


def test_actual_multilayer_gradient_matches_independent_numpy_fd_and_frozen_loss():
    req=request(); result=_compute_gradient_summary({'gradient_binding':req})
    assert result['status']=='available',result.get('reason',result)
    assert result['check_ref'] and result['verification']['passed']
    assert result['constraints']=={'max_total_nm':400.}
    sim=req['simulation']; targets=req['objective']['targets']
    assert result['metric_value']==pytest.approx(numpy_loss(sim,targets),abs=1e-10)
    for row in result['layer_gradients']:
        i=row['layer_index']; h=.005
        plus=copy.deepcopy(sim);minus=copy.deepcopy(sim)
        plus['stack']['layers'][i]['thickness_nm']+=h;minus['stack']['layers'][i]['thickness_nm']-=h
        fd=(numpy_loss(plus,targets)-numpy_loss(minus,targets))/(2*h)
        assert row['gradient_per_nm']==pytest.approx(fd,rel=1e-4,abs=1e-8)
        assert row['sensitivity_per_10nm']==pytest.approx(10*row['gradient_per_nm'])
    assert any(r['gradient_per_nm']<0 for r in result['layer_gradients'])
    assert any(r['gradient_per_nm']>0 for r in result['layer_gradients'])


@pytest.mark.parametrize('field',['sample','objective_version','grid','polarization','materials','tool_version'])
def test_identity_swaps_unavailable(field):
    req=request(); req['binding'][field]='0'*64
    result=_compute_gradient_summary({'gradient_binding':req})
    assert result['status']=='unavailable'
    assert 'identity mismatch' in result['reason']


def test_candidate_reference_and_missing_provenance_do_not_compute():
    req=request();req['candidate_ref']['object_id']='other'
    assert _compute_gradient_summary({'gradient_binding':req})['status']=='unavailable'
    req=request();del req['provenance']
    assert _compute_gradient_summary({'gradient_binding':req})['status']=='unavailable'


def test_unsupported_objective_has_no_mean_fallback():
    simulation,candidate,task=fixture()
    unsupported=ResearchTask(object_id='unknown',source_run_id='r',question='unknown',objective={'kind':'robust_yield'})
    with pytest.raises(ValueError,match='objective_unsupported'):
        make_gradient_binding(simulation,candidate=candidate,objective_task=unsupported,sample={'id':'s'})


def test_reflection_consumes_verified_signed_gradient():
    from optomind_optics.harness.route_reflection import reflect_on_route
    result=_compute_gradient_summary({'gradient_binding':request()});seen=[]
    class Client:
        def call(self,messages,**kwargs):
            evidence=json.loads(messages[1]['content'])['scientific_evidence'];seen.append(evidence)
            return {'content':json.dumps(dict(observed_vs_expected='nominal loss',deviation_mechanism='layer sensitivity',continue_recommended=True,continue_rationale='FD passed',insight_for_next=str(evidence['gradient_summary']['layer_gradients']),insight_grounding='observed'))}
    reflect_on_route(Client(),pre_declarations={},observation={'gradient_summary':result},score_history=[])
    assert seen[0]['gradient_summary']==result


def test_actual_soft_score_matches_existing_harness_and_independent_fd():
    from tmm_engine.task_io import optimization_task_from_dict
    from tmm_engine.material_registry import MaterialRegistry
    from tmm_engine.workbench import TMMWorkbench
    from optomind_optics.harness.objectives import evaluate_optimization_objectives
    simulation,candidate,task=fixture()
    objective={**task.objective,'kind':'optomind_soft_score'}
    task=ResearchTask(object_id='soft-score',source_run_id='r',question='actual score',objective=objective,constraints=task.constraints)
    req=make_gradient_binding(simulation,candidate=candidate,objective_task=task,sample={'id':'s'})
    result=_compute_gradient_summary({'gradient_binding':req})
    assert result['status']=='available',result.get('reason',result)
    def score(sim):
        task=optimization_task_from_dict({'simulation':sim,'targets':objective['targets']})
        return evaluate_optimization_objectives(task,TMMWorkbench(MaterialRegistry()).simulate(task.simulation)).aggregate_soft_score
    assert result['metric_value']==pytest.approx(score(simulation),abs=1e-10)
    for row in result['layer_gradients']:
        plus=copy.deepcopy(simulation);minus=copy.deepcopy(simulation);i=row['layer_index'];h=.003
        plus['stack']['layers'][i]['thickness_nm']+=h;minus['stack']['layers'][i]['thickness_nm']-=h
        assert row['gradient_per_nm']==pytest.approx((score(plus)-score(minus))/(2*h),rel=1e-4,abs=1e-8)


def test_adopted_producer_uses_certified_selected_candidate_not_first_stack(tmp_path):
    from optomind_optics.harness.science.gradient_binding import adopted_gradient_summary
    from tmm_engine.task_io import optimization_task_from_dict
    from tmm_engine.schemas import dataclass_to_dict
    from tmm_engine.hashing import stable_sha256
    from tmm_engine.material_registry import MaterialRegistry
    from tmm_engine.workbench import TMMWorkbench
    from optomind_optics.harness.objectives import evaluate_optimization_objectives
    simulation,candidate,task=fixture();actual=copy.deepcopy(simulation)
    actual['stack']['layers'][0]['thickness_nm']=97.
    native={'simulation':simulation,'targets':task.objective['targets']}
    parsed=optimization_task_from_dict({**native,'simulation':actual})
    score=evaluate_optimization_objectives(parsed,TMMWorkbench(MaterialRegistry()).simulate(parsed.simulation)).aggregate_soft_score
    from tmm_engine.acceptance import certify_simulation, AcceptanceSettings
    certificate=certify_simulation(TMMWorkbench(MaterialRegistry()),parsed.simulation,AcceptanceSettings(require_spectral_convergence=False)).certificate
    assert certificate['accepted'],certificate
    artifact='adopted/PHYSICS_ACCEPTANCE_CERTIFICATE.json';(tmp_path/'adopted').mkdir();(tmp_path/artifact).write_text(json.dumps(certificate))
    row={'candidate_id':'adopted','physically_admissible':True,'target_score':score,'certificate_id':certificate['certificate_id'],
         'artifact_ids':[artifact],'metadata':{'thicknesses_nm':[97.,127.,51.]}}
    compiled={'task_id':'actual-task','experiments':[{'experiment_id':'wrong-first','mode':'simulate','tmm_task':{}},{'experiment_id':'right','mode':'optimize','tmm_task':native}]}
    run={'run_id':'r','experiment_results':[{'experiment_id':'right','portfolio':{'selected_roles':{'best_target_score':'adopted'},'candidates':[row]}}]}
    result=adopted_gradient_summary(compiled,run,tmp_path)
    assert result['status']=='available',result.get('reason',result)
    assert result['candidate_id']=='adopted'
    assert result['layer_gradients'][0]['thickness_nm']==97.
    row['metadata']['thicknesses_nm'][0]=96.
    assert adopted_gradient_summary(compiled,run,tmp_path)['status']=='unavailable'


def test_planning_ledger_consumes_signed_derivative_and_qualified_unavailable():
    from optomind_optics.harness.action_ledger import _gradient_basis_text
    result=_compute_gradient_summary({'gradient_binding':request()})
    text=_gradient_basis_text(result)
    assert 'signed derivative' in text and '/nm' in text and 'minimize' in text
    assert 'gradient unavailable: stale candidate' == _gradient_basis_text({'status':'unavailable','reason':'stale candidate','top_influential_layers':result['top_influential_layers']})


def test_score_kink_is_qualified_unavailable():
    from tmm_engine.task_io import simulation_task_from_dict
    from tmm_engine.workbench import TMMWorkbench
    from tmm_engine.material_registry import MaterialRegistry
    simulation,candidate,task=fixture()
    observed=float(TMMWorkbench(MaterialRegistry()).simulate(simulation_task_from_dict(simulation)).channel(0.,'s')['R'].mean())
    target={**task.objective['targets'][0],'constraint':'match','target':observed}
    task=ResearchTask(object_id='kink',source_run_id='r',question='nondifferentiable exact match',objective={'kind':'optomind_soft_score','targets':[target]})
    req=make_gradient_binding(simulation,candidate=candidate,objective_task=task,sample={'id':'s'})
    result=_compute_gradient_summary({'gradient_binding':req})
    assert result['status']=='unavailable' and 'nondifferentiable_objective' in result['reason']


def test_failed_fd_never_reaches_available_status(monkeypatch):
    req=request()
    from tmm_engine import gradient_api
    original=gradient_api._finite_difference_cross_check
    def wrong(*args):
        rows,max_relative=original(*args)
        rows[0]['finite_difference_per_nm']+=1.
        return rows,max_relative
    monkeypatch.setattr(gradient_api,'_finite_difference_cross_check',wrong)
    result=_compute_gradient_summary({'gradient_binding':req})
    assert result['status']=='unavailable' and result['reason']=='finite_difference_verification_failed'



def test_gradient_actual_forward_resources_use_common_ledger(tmp_path):
    from optomind_optics.harness.science.compute_ledger import ComputeLedger, active_compute_ledger
    req = request()
    ledger = ComputeLedger(tmp_path / 'compute.sqlite', {'model_predictions':100, 'solver_points':1000})
    try:
        with active_compute_ledger(ledger):
            result = _compute_gradient_summary({'gradient_binding':req})
        assert result['status'] == 'available', result
        assert result['compute_cost']['differentiable_forward_calls'] == 16
        assert result['compute_cost']['solver_points'] == 64
        snapshot = ledger.snapshot()
        assert snapshot['usage'] == {'model_predictions':16, 'solver_points':64}
        assert snapshot['events_by_status'] == {'settled':32}
        assert all(row['scope'] == 'bound_gradient' for row in snapshot['by_scope'])
    finally:
        ledger.close()


def test_gradient_budget_and_failed_forward_keep_consumed_work(tmp_path, monkeypatch):
    from optomind_optics.harness.science.compute_ledger import ComputeLedger, active_compute_ledger
    req = request()
    ledger = ComputeLedger(tmp_path / 'limited.sqlite', {'model_predictions':3, 'solver_points':1000})
    try:
        with active_compute_ledger(ledger):
            result = _compute_gradient_summary({'gradient_binding':req})
        assert result['status'] == 'unavailable' and 'ComputeBudgetExceeded' in result['reason']
        assert result['compute_cost']['differentiable_forward_calls'] == 3
        assert ledger.snapshot()['usage'] == {'model_predictions':3, 'solver_points':12}
    finally:
        ledger.close()
    from tmm_engine.optimization import DifferentiableThicknessOptimizer
    from optomind_optics.harness.science.gradient_binding import _engine
    _, _, _, _, _, registry_cls = _engine()
    optimizer = DifferentiableThicknessOptimizer(registry_cls(), device='cpu')
    solver_class = optimizer._solver_class
    def fail(*args, **kwargs):
        raise RuntimeError('actual differentiable solver failed')
    monkeypatch.setattr(solver_class, 'forward', fail)
    ledger = ComputeLedger(tmp_path / 'failed.sqlite', {'model_predictions':100, 'solver_points':1000})
    try:
        with active_compute_ledger(ledger):
            result = _compute_gradient_summary({'gradient_binding':req})
        assert result['status'] == 'unavailable' and 'actual differentiable solver failed' in result['reason']
        assert ledger.snapshot()['usage'] == {'model_predictions':1, 'solver_points':4}
        assert ledger.snapshot()['events_by_status'] == {'failed_consumed':2}
        assert result['compute_cost']['differentiable_forward_calls'] == 1
    finally:
        ledger.close()



def test_legacy_gradient_consumer_persists_shared_budget_on_resume(tmp_path):
    from optomind_optics.harness.research_orchestrator import _compute_budgeted_gradient
    req = request()
    path = tmp_path / 'GRADIENT_COMPUTE.sqlite'
    limits = {'model_predictions':20, 'solver_points':1000}
    first = _compute_budgeted_gradient({'gradient_binding':req}, {}, tmp_path, path, limits)
    assert first['status'] == 'available'
    assert first['compute_ledger']['usage']['model_predictions'] == 16
    second = _compute_budgeted_gradient({'gradient_binding':req}, {}, tmp_path, path, limits)
    assert second['status'] == 'unavailable' and 'ComputeBudgetExceeded' in second['reason']
    assert second['compute_ledger']['usage'] == {'model_predictions':20,'solver_points':80}
    with pytest.raises(ValueError, match='recovery limits changed'):
        _compute_budgeted_gradient({'gradient_binding':req}, {}, tmp_path, path, {'model_predictions':40,'solver_points':1000})
