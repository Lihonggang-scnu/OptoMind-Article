"""Engineering fixtures only; frozen 144-task outcomes are not used for tuning."""
import copy
import json
from dataclasses import replace
import numpy as np
import pytest
from optomind_eval import manufacturing_tasks as mt
from optomind_eval.manufacturing_baselines import grouped_information_gain, information_labels, run_policy, ARMS7
from optomind_optics.harness.science.terminal_problem import TerminalDecisionProblem
from optomind_optics.harness.science.controller import MeasurementAction


@pytest.fixture(scope='module')
def tiny():
    config=copy.deepcopy(mt.FROZEN_PLAN)
    config['design_axes_nm']=[[50.,110.],[30.,90.],[50.]]
    config['residual_support_nm']=[-2.,2.]
    config['residual_probabilities']=[.5,.5]
    config['scoring_grid_nm']['count']=5
    return config,mt.build_numerical_model(config)


def test_forward_crosscheck_material_angle_polarization():
    process={'high_n':2.4,'low_bias_nm':4.,'high_bias_nm':-5.}
    for materials,thickness in [(('low',),(80.,)),(('high',),(95.,)),(('low','high','low'),(95.,75.,95.))]:
        for angle,polarization in [(0.,'s'),(55.,'p'),(55.,'s')]:
            grid=np.linspace(450,750,9)
            assert np.allclose(mt.policy_spectrum(process,materials,thickness,grid,angle=angle,polarization=polarization),
                               mt.independent_spectrum(process,materials,thickness,grid,angle=angle,polarization=polarization),atol=2e-12,rtol=0)


def test_hierarchy_and_null_cases(tiny):
    config,numerical=tiny
    problem,prior,actions,worlds=mt.make_problem(config,numerical,'mixed',.015,'fixture')
    assert len(worlds)==81 and np.isclose(prior.sum(),1)
    assert np.array_equal(problem.losses[0],problem.losses[1])
    assert np.array_equal(problem.losses[1],problem.losses[2])
    for action in actions:
        assert np.allclose(np.sum(action.kernel,axis=1),1)
    assert any(not np.allclose(actions[0].kernel,a.kernel) for a in actions[1:])
    known,q,known_actions,known_worlds=mt.make_problem(config,numerical,'known_process',.015,'known')
    assert len(known_worlds)==3 and np.allclose(known.losses,known.losses[0])
    for arm in ('fixed_bayes','fixed_robust','entropy_cost_gated','qoi_information','optimal_design_information','task_evsi'):
        def forbidden(action):
            raise AssertionError('zero-value task measured')
        _,trace=run_policy(arm,known,q,known_actions,forbidden,budget=2)
    null,q,null_actions,_=mt.make_problem(config,numerical,'uninformative',.015,'null')
    for action in null_actions:
        assert np.allclose(action.kernel,np.asarray(action.kernel)[0])
    aligned,q,_,worlds=mt.make_problem(config,numerical,'aligned',.015,'aligned')
    assert len(worlds)==3


def test_mixture_kernel_manual(tiny):
    config,numerical=tiny
    _,_,actions,worlds=mt.make_problem(config,numerical,'mixed',.015,'fixture')
    edges=np.r_[-np.inf,mt._grid(config['bin_interior_edges']),np.inf]
    action=actions[0];world=worlds[0]
    expected=np.zeros(len(edges)-1)
    for mean,weight in zip(numerical['coupon_means'][action.action_id][world['process_index']],numerical['coupon_probabilities'][action.action_id]):
        expected+=weight*np.diff(mt.ndtr((edges-mean-world['instrument_offset'])/.003))
    assert np.allclose(expected,np.asarray(action.kernel)[0],atol=1e-15)


def test_coupon_draw_not_reused_and_receipt_binding(tiny):
    config,numerical=tiny
    _,_,_,worlds=mt.make_problem(config,numerical,'mixed',.015,'fixture')
    spec=config['actions'][2]
    first=mt.ManufacturingService(config,numerical,'fixture',123,'mixed',.015,worlds[0])
    second=mt.ManufacturingService(config,numerical,'fixture',123,'mixed',.015,worlds[0])
    receipt=first.purchase(spec['id'],'request',spec)
    assert receipt==second.purchase(spec['id'],'request',spec)
    assert receipt.angle_deg==55 and receipt.polarization=='p'
    assert receipt.action_manifest_hash==mt.digest(spec)
    with pytest.raises(PermissionError): first.purchase(spec['id'],'request2',spec)
    third=mt.ManufacturingService(config,numerical,'fixture',456,'mixed',.015,worlds[0])
    bad=dict(spec,angle_deg=0)
    with pytest.raises(PermissionError): third.purchase(spec['id'],'request',bad)
    first.submit('d0',{});third.submit('d0',{})
    assert first.evaluator_score()==third.evaluator_score()
    assert first.evaluator_spent()==2


def test_grouped_information_and_labels():
    q=[.1,.2,.7,0.];kernel=np.eye(4)
    labels=np.array([[0,1],[0,1],[1,1],[2,2]])
    assert grouped_information_gain(q,kernel,labels)==pytest.approx(-.3*np.log(.3)-.7*np.log(.7))
    assert grouped_information_gain(q,kernel,np.ones(4))==pytest.approx(0)
    context={key:'fixture' for key in ('task','objective','model','sample','material','solver','fidelity','loss','split')}
    problem=TerminalDecisionProblem(('a','b','c'),('d0','d1'),np.array([[.1,.9],[.1,.9],[.9,.1]]),context,'spec_failure_probability')
    assert information_labels(problem,'optimal_design_information').tolist()==[0,0,1]
    assert np.array_equal(information_labels(problem,'qoi_information')[0],information_labels(problem,'qoi_information')[1])
    action=MeasurementAction('identity',('y0','y1','y2'),tuple(map(tuple,np.eye(3))),.01,1)
    for arm in ('qoi_information','optimal_design_information'):
        selected,trajectory=run_policy(arm,problem,[.2,.3,.5],[action],lambda a:'y2',budget=1)
        assert selected=='d1'


def test_nonfinite_independent_cannot_pass(tiny,monkeypatch):
    config,numerical=tiny
    _,_,_,worlds=mt.make_problem(config,numerical,'mixed',.015,'fixture')
    service=mt.ManufacturingService(config,numerical,'fixture',123,'mixed',.015,worlds[0])
    monkeypatch.setattr(mt,'independent_spectrum',lambda *a,**k:np.array([np.nan]))
    with pytest.raises(ValueError):service.purchase(config['actions'][0]['id'],'request',config['actions'][0])
    assert service.evaluator_spent()==1
    with pytest.raises(ValueError):service.submit('d0',{})


def test_caps_and_complete_fixture(tmp_path,tiny):
    config,numerical=tiny
    too_small=copy.deepcopy(config);too_small['compute_caps']['policy_spectra']=1
    with pytest.raises(ValueError):mt.build_numerical_model(too_small)
    report=mt.run_manufacturing_evaluation(tmp_path,tasks_per_case=2,seed_base=999,config_override=config)
    assert sum(report['failures'].values())==0
    assert report['scope']=='engineering_fixture_not_frozen_audit'
    results=json.loads((tmp_path/'results.json').read_text())
    assert len(results['rows'])==6*2*len(ARMS7)
    assert report['comparisons']['task_evsi - qoi_information']['method']=='paired_stratified_task_percentile_bootstrap'
    for arm in ('entropy_cost_gated','qoi_information','optimal_design_information','task_evsi'):
        assert report['case_descriptives']['expensive'][arm]['measurement_rate']==0
    frozen=json.loads((tmp_path/'frozen_config.json').read_text())
    assert frozen['runtime_source_hashes'] and frozen['plan_file_sha256']==mt.FROZEN_PLAN_SHA256


def test_successful_purchase_then_bad_receipt_retains_cost(tmp_path,tiny,monkeypatch):
    config,_=tiny
    config=copy.deepcopy(config);config['cases']=['expensive']
    original=mt.ManufacturingService.purchase
    def forged(self,*args,**kwargs):
        return replace(original(self,*args,**kwargs),polarization='wrong')
    monkeypatch.setattr(mt.ManufacturingService,'purchase',forged)
    report=mt.run_manufacturing_evaluation(tmp_path,tasks_per_case=2,seed_base=998,config_override=config)
    rows=json.loads((tmp_path/'results.json').read_text())['rows']
    failed=[row for row in rows if row['arm']=='fixed_measure_fit']
    assert len(failed)==2
    assert all(row['status']=='failed' and row['resource_cost']==1 and row['declared_loss_cost']==2 for row in failed)
    assert report['case_descriptives']['expensive']['fixed_measure_fit']['mean_total_loss']==3
