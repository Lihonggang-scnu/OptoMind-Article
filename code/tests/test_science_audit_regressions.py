from dataclasses import asdict, replace

import numpy as np

import pytest

from optomind_optics.harness.science.contracts import ResearchTask, Observation, PredictionSpec, ModelCandidate

from optomind_optics.harness.science.repository import ScienceRepository

from optomind_optics.harness.science.prediction import commit_prediction, evaluate_prediction

from optomind_optics.harness.science.model_ir import IRNode, ModelIR, ParameterSpec

from optomind_optics.harness.science.model_compiler import compile_candidate, compile_model

from optomind_optics.harness.science.inference import fit_model, fit_result_to_state

from optomind_optics.harness.science.likelihood_blocks import blocks_from_observations

def candidate(parameter='gain'):
    ir=ModelIR((IRNode('sub','constant_nk',attributes=(('n',1.5),)),IRNode('stack','stack',('sub',),unit='nm'),IRNode('tmm','tmm',('stack',)),IRNode('gain','drift',('tmm',),(('gain','$'+parameter),))), 'gain',(ParameterSpec(parameter,.1,4.,1.),))
    return ModelCandidate(object_id='candidate-'+parameter,source_run_id='audit',model_ir=asdict(ir))

def obs(values):
    return Observation(object_id='obs',source_run_id='audit',acquisition_id='acq',raw_blocks=({'block_id':'raw','channel':'R','wavelengths_nm':[500.,600.,700.],'values':values,'covariance':(np.eye(3)*1e-6).tolist(),'gaussian_validated':True},))

def test_fit_cannot_be_bound_to_different_raw_values_with_same_ids():
    c=candidate(); original=obs([.08,.08,.08]); substituted=obs([.001,.001,.001])
    fit=fit_model(compile_candidate(c),blocks_from_observations([original]),starts=2)
    assert abs(fit.parameters['gain']-2.)<1e-6
    with pytest.raises(ValueError):
        fit_result_to_state(fit,c,[substituted],object_id='state',source_run_id='audit')

def test_alpha_renamed_candidate_requires_parameter_remapping():
    c=candidate(); renamed=candidate('renamed'); observation=obs([.08,.08,.08])
    fit=fit_model(compile_candidate(c),blocks_from_observations([observation]),starts=2)
    with pytest.raises(ValueError, match='parameter identities'):
        fit_result_to_state(fit,renamed,[observation],object_id='state',source_run_id='audit')
    with pytest.raises(ValueError, match='unknown model parameters'):
        compile_candidate(renamed).predict(fit.parameters,[500.,600.,700.])


def test_unknown_model_ir_version_is_rejected():
    c=candidate(); from optomind_optics.harness.science.model_ir import model_ir_from_dict
    with pytest.raises(ValueError):
        compile_model(model_ir_from_dict({**c.model_ir,'version':'optical-ir/999'}))


def test_acquisition_cannot_be_rewrapped_after_prediction(tmp_path):
    repo=ScienceRepository(tmp_path)
    task=ResearchTask(object_id='task', source_run_id='audit', question='precommit')
    old=Observation(object_id='old', source_run_id='audit', acquisition_id='same-physical-acquisition', raw_blocks=({'block_id':'raw','channel':'R','units':'1','coordinate':[500.,600.],'values':[.5,.5]},),quality={'execution_status':'completed','numerical_valid':True,'noise_validated':True})
    snap=repo.commit([task,old],task_ref=task.ref(),evidence_cutoff='old')
    pred=PredictionSpec(object_id='pred',source_run_id='audit',observable='R',snapshot_id=snap.snapshot_id,raw_channel='R',units='1',statistic='simultaneous_envelope',tolerance_source='posthoc data',coordinate=(500.,600.),lower=(.49,.49),upper=(.51,.51))
    receipt=commit_prediction(repo,pred,task_ref=task.ref(),evidence_cutoff='old')
    new=Observation(**{**old.model_dump(exclude={'content_hash'}),'object_id':'rewrapped'})
    # A new projection of the same old acquisition must not be treated as held-out.
    with pytest.raises(ValueError):
        repo.commit([new],task_ref=task.ref(),evidence_cutoff='new')
        evaluate_prediction(repo,receipt,new.ref())


def test_purchased_raw_observation_is_prediction_evaluable(tmp_path):
    from optomind_eval.worlds import make_world
    from optomind_eval.service import MeasurementService
    from optomind_optics.harness.science.world_client import WorldClient,receipt_to_observation
    repo=ScienceRepository(tmp_path)
    task=ResearchTask(object_id='task',source_run_id='audit',question='new acquisition')
    snap=repo.commit([task],task_ref=task.ref(),evidence_cutoff='0')
    pred=PredictionSpec(object_id='pred',source_run_id='audit',observable='R',snapshot_id=snap.snapshot_id,raw_channel='R',units='1',statistic='simultaneous_envelope',tolerance_source='broad bound',coordinate=(500.,600.),lower=(-1.,-1.),upper=(2.,2.))
    commit=commit_prediction(repo,pred,task_ref=task.ref(),evidence_cutoff='0')
    raw=WorldClient(MeasurementService([make_world('F0',task_id='t',split='V',seed=10)],budget=1).handle).purchase('t','fresh','R',[500.,600.])
    observation=receipt_to_observation(raw,object_id='obs',source_run_id='audit')
    repo.commit([observation],task_ref=task.ref(),evidence_cutoff='1')
    verdict=evaluate_prediction(repo,commit,observation.ref())
    assert verdict.state == 'supported', verdict


def test_legal_nested_skipped_branch_finishes(tmp_path):
    from optomind_optics.harness.science.jobs import ExecutionGate,ToolCapability
    from optomind_optics.harness.science.program_ir import ProgramIR,ProgramNode
    from optomind_optics.harness.science.program_compiler import compile_program
    from optomind_optics.harness.science.program_runner import ProgramRunner
    cap=ToolCapability(effect_class='external_measurement',domain='synthetic',channels=('R',))
    nodes=(ProgramNode('raw','new_evidence',channel='R',estimate=(('calls',1.),)),ProgramNode('mean','derived_view',('raw',)),ProgramNode('b1','branch',('mean',),arguments=(('threshold',1.),('comparison','gt'))),ProgramNode('b2','branch',('mean',),arguments=(('threshold',2.),('comparison','gt')),condition=('b1',True)),ProgramNode('end','terminate',condition=('b2',True)))
    plan=compile_program(ProgramIR('nested',nodes),capabilities={'R':cap},limits={'calls':1})
    class Provider:
        def submit(self,key,payload): return {'state':'completed','values':[0.,0.],'cost':{'calls':1}}
    gate=ExecutionGate(tmp_path/'gate.sqlite',limits={'calls':1},capability=cap)
    runner=ProgramRunner(tmp_path/'program.sqlite',gates={'R':gate},providers={'R':Provider()})
    result=runner.run(plan,prediction_commits={'raw':{'prediction_id':'p'}})
    assert result.state == 'completed'
    assert result.nodes['end']['state'] == 'skipped'


def test_job_stale_poll_cannot_overwrite_completed_receipt(tmp_path):
    import threading
    from optomind_optics.harness.science.jobs import ExecutionGate,ToolCapability
    cap=ToolCapability(effect_class='compute',domain='audit',supports_status=True)
    db=tmp_path/'jobs.sqlite'
    gate=ExecutionGate(db,limits={'calls':1},capability=cap)
    gate.reserve('job',{},estimate={'calls':1},prediction_commit={'id':'pred'})
    gate._update(gate.receipt('job').model_copy(update={'state':'running'}))
    fetched=threading.Event(); resume=threading.Event(); errors=[]
    class Provider:
        def status(self,key): return {'state':'running'}
    def hook(stage):
        if stage=='before_settlement': fetched.set(); assert resume.wait(5)
    def stale_poll():
        try:
            worker=ExecutionGate(db,limits={'calls':1},capability=cap,fault_hook=hook)
            worker.recover(Provider(),'job');worker.close()
        except Exception as exc: errors.append(exc)
    t=threading.Thread(target=stale_poll);t.start();assert fetched.wait(5)
    gate._collect('job',{'state':'completed','cost':{'calls':.5}})
    resume.set();t.join(5); assert not errors
    final=gate.receipt('job')
    assert final.state == 'completed', final
    assert 'job:settled:calls' in final.cost_refs


@pytest.mark.parametrize('covariance', [[[1.,100.],[0.,1.]], [[float('nan'),0.],[0.,1.]]])
def test_gaussian_observation_kernel_rejects_invalid_covariance(covariance):
    from optomind_optics.harness.science.inference import gaussian_observation_kernel
    class Constant:
        def predict(self,p,x,**kwargs): return np.zeros(len(x))
    with pytest.raises(ValueError):
        gaussian_observation_kernel(Constant(),[{'parameters':{}}],[500.,600.],covariance)


@pytest.mark.parametrize('std',[float('nan'),float('inf')])
def test_shared_calibration_requires_finite_std(std):
    from optomind_optics.harness.science.likelihood_blocks import LikelihoodBlock
    with pytest.raises(ValueError):
        LikelihoodBlock('x',(500.,),(1.,),((1.,),),calibration_group='ref',calibration_std=std)


def test_absorbing_oblique_tmm_matches_independent_transfer_matrix():
    from tmm import coh_tmm
    nodes=(IRNode('film','constant_nk',attributes=(('n',2.),('k',.2))),IRNode('sub','constant_nk',attributes=(('n',1.5),)),IRNode('layer','layer',('film',),(('thickness',110.),),unit='nm'),IRNode('stack','stack',('layer','sub'),unit='nm'),IRNode('solver','tmm',('stack',),(('s_fraction',.3),)))
    model=compile_model(ModelIR(nodes,'solver'))
    wl=np.array([420.,530.,780.])
    for angle in [0.,35.,70.]:
        expected={channel:[] for channel in ('R','T','A')}
        for wavelength in wl:
            data=[coh_tmm(pol,[1.,2.+.2j,1.5],[np.inf,110.,np.inf],np.deg2rad(angle),wavelength) for pol in ('s','p')]
            r=.3*data[0]['R']+.7*data[1]['R'];t=.3*data[0]['T']+.7*data[1]['T']
            for channel,value in [('R',r),('T',t),('A',1-r-t)]: expected[channel].append(value)
        for channel in expected:
            assert np.allclose(model.predict({},wl,angle_deg=angle,channel=channel),expected[channel],atol=1e-11)


def test_minimax_precommit_matches_exhaustive_policy_search():
    from itertools import product
    from optomind_optics.harness.science.terminal_problem import TerminalDecisionProblem
    from optomind_optics.harness.science.robust_voi import robust_voi
    rng=np.random.default_rng(319)
    context={key:'audit' for key in ('task','objective','model','sample','material','solver','fidelity','loss','split')}
    for _ in range(12):
        losses=rng.uniform(-2,3,(3,3));joint=rng.dirichlet(np.ones(9),size=4).reshape(4,3,3)
        p=TerminalDecisionProblem(('w0','w1','w2'),('d0','d1','d2'),losses,context,'loss')
        report=robust_voi(p,joint,('o0','o1','o2'))
        brute=min(max(sum(joint[s,:,y] @ losses[:,d] for y,d in enumerate(policy)) for s in range(4)) for policy in product(range(3),repeat=3))
        assert abs(report.posterior_risk-brute)<1e-8
        assert abs(report.value_interval[0]-report.value_interval[1])<1e-8


def test_same_parameter_names_with_swapped_operator_roles_cannot_rebind_fit():
    base=candidate()
    nodes=tuple(IRNode(**node) for node in base.model_ir['nodes'])
    def make(swapped):
        attrs=(('gain','$b' if swapped else '$a'),('offset','$a' if swapped else '$b'))
        ir=ModelIR(nodes[:-1]+(replace(nodes[-1],attributes=attrs),),'gain',(ParameterSpec('a',.1,4.,1.),ParameterSpec('b',.1,4.,1.)))
        return ModelCandidate(object_id='swapped' if swapped else 'original',source_run_id='audit',model_ir=asdict(ir))
    original,swapped=make(False),make(True)
    observation=obs([.2,.2,.2])
    fit=fit_model(compile_candidate(original),blocks_from_observations([observation]),starts=2)
    assert compile_candidate(original).model_id == compile_candidate(swapped).model_id
    assert set(fit.parameters) == {'a','b'}
    with pytest.raises(ValueError,match='parameter-to-operator binding'):
        fit_result_to_state(fit,swapped,[observation],object_id='state',source_run_id='audit')
