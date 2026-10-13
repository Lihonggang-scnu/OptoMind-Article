from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
import hashlib
import json
import numpy as np
from scipy.stats import norm
from tmm import coh_tmm

from optomind_optics.harness.science.contracts import (AcquisitionIdentity,DecisionContext,ExperimentProgram,
    ModelCandidate,Observation,PredictionEvaluation,PredictionSpec,ResearchTask,canonical_hash)
from optomind_optics.harness.science.diagnostic_design import (DiagnosticConfig,DiagnosticDatum,DiagnosticForward,
    DiagnosticNoise,MechanismFamily,refit_competition)
from optomind_optics.harness.science.jobs import ExecutionGate,ToolCapability
from optomind_optics.harness.science.method_assets import MethodRegistry
from optomind_optics.harness.science.method_lifecycle import method_completion_status
from optomind_optics.harness.science.method_policy import MethodGenerationPolicy,MethodPolicyRun,generate_method_plan
from optomind_optics.harness.science.method_runtime import FrozenMethodProvider,diagnostic_action_from_method
from optomind_optics.harness.science.model_ir import IRNode,ModelIR,ParameterSpec
from optomind_optics.harness.science.prediction import commit_prediction,evaluate_prediction
from optomind_optics.harness.science.program_runner import ProgramRunner
from optomind_optics.harness.science.repository import ScienceRepository
from optomind_research.runtime.artifact_store import atomic_write_json


@dataclass(frozen=True)
class TransferCase:
    case_id: str
    indices: tuple[float,float]
    layers: int
    seed: int
    noise_std: float = .003
    fit_starts: int = 3
    max_nfev: int = 90


def mechanism_families(case):
    families=[]
    for index,n in enumerate(case.indices):
        nodes=[IRNode('high','constant_nk',attributes=(('n',n),)),
               IRNode('low','constant_nk',attributes=(('n',1.45),)),
               IRNode('sub','constant_nk',attributes=(('n',1.5),))]
        layer_ids=[]
        for i in range(case.layers):
            name='layer'+str(i);layer_ids.append(name)
            nodes.append(IRNode(name,'layer',('high' if i%2==0 else 'low',),
                (('thickness','$d' if i==0 else float(55+20*i)),),unit='nm'))
        nodes.extend((IRNode('stack','stack',(*layer_ids,'sub'),unit='nm'),IRNode('R','tmm',('stack',))))
        families.append(MechanismFamily('family-'+str(index),ModelIR(tuple(nodes),'R',
            (ParameterSpec('d',60.,190.,115.,'nm'),),(400.,900.))))
    return tuple(families)


class IndependentMethodProvider:
    def __init__(self,case,action):
        self.case=case;self.action=action;self.receipts={};self.datum=None
        rng=np.random.default_rng(case.seed)
        self.selected=int(rng.integers(0,2));self.thickness=float(rng.uniform(75.,170.))

    def _mean(self,action):
        indices=[1.,*[complex(layer.n,layer.k) for layer in action.frontend.layers]]
        indices.extend(self.case.indices[self.selected] if i%2==0 else 1.45 for i in range(self.case.layers))
        indices.append(1.5)
        ds=[np.inf,*[layer.thickness_nm for layer in action.frontend.layers],self.thickness,
            *[float(55+20*i) for i in range(1,self.case.layers)],np.inf]
        if any(layer.coherence!='coherent' for layer in action.frontend.layers):
            raise ValueError('independent method diagnostic currently supports coherent front ends')
        offsets=(0.,) if not action.bandwidth_nm else (-action.bandwidth_nm/2,0.,action.bandwidth_nm/2)
        weights=(1.,) if len(offsets)==1 else (1/6,2/3,1/6)
        angles=(action.angle_deg,) if not action.angular_spread_deg else tuple(action.angle_deg+x for x in (-action.angular_spread_deg,0.,action.angular_spread_deg))
        pols=('s','p') if action.polarization=='unpolarized' else (action.polarization,)
        return np.asarray([sum(weight*sum(coh_tmm(pol,indices,ds,np.deg2rad(angle),float(w+offset))[action.channel]
                    for angle in angles for pol in pols)/(len(angles)*len(pols)) for offset,weight in zip(offsets,weights)) for w in action.wavelengths_nm])

    def submit(self,key,payload):
        if tuple(payload['arguments']['wavelengths_nm'])!=self.action.wavelengths_nm or payload['sample_id']!=self.action.sample_id:
            raise ValueError('method adapter received a different instantiated action')
        if key in self.receipts:
            return self.receipts[key]
        rng=np.random.default_rng(self.case.seed+982451653)
        mean=self._mean(self.action)
        y=mean+rng.normal(0,self.case.noise_std,len(mean))
        cov=np.eye(len(y))*self.case.noise_std**2
        acquisition=canonical_hash({'case':asdict(self.case),'request':key})
        self.datum=DiagnosticDatum(acquisition,self.action,tuple(map(float,y)),tuple(tuple(map(float,row)) for row in cov))
        result={'state':'completed','cost':{'calls':1.,'cost':self.action.cost},'values':list(map(float,y)),
                'independent_solver':'Byrnes coherent TMM','acquisition_id':acquisition,
                'coordinate':list(self.action.wavelengths_nm)}
        self.receipts[key]=result
        return result

    def status(self,key):
        return self.receipts.get(key,{'state':'unknown_outcome'})


def execute_transfer_case(library, case, output, *, policy=None):
    output=Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('fresh task directory required; no completed replay scoring')
    output.mkdir(parents=True,exist_ok=True)
    source=ScienceRepository(library);target=ScienceRepository(output/'science')
    run_id=case.case_id+':'+(policy.version_hash[:12] if policy else 'exact-replay')+':'+canonical_hash(str(output.resolve()))[:12]
    families=mechanism_families(case)
    config=DiagnosticConfig(seed=case.seed+17,fit_starts=case.fit_starts,max_nfev=case.max_nfev,
        max_prediction_calls=16000,max_solver_points=800000,measurement_budget=2.,
        noise=DiagnosticNoise(measurement_std=case.noise_std,offset_std=0.,gain_std=0.))
    task=ResearchTask(object_id=run_id+':task',source_run_id=run_id,
        question='Identify an unknown planar-film material mechanism using a frozen transferred measurement method',
        objective={'kind':'bounded_mechanism_classification'},
        constraints={'scope':{'indices':case.indices,'layers':case.layers,'noise_std':case.noise_std,
                     'fit_starts':case.fit_starts,'max_nfev':case.max_nfev},'paid_network_calls':0,'evidence_tier':'V1_synthetic'})
    for repository in (source,target):
        repository.commit([task],task_ref=task.ref(),evidence_cutoff='before-new-task-data')
    method=FrozenMethodProvider(MethodRegistry(source),task_ref=task.ref(),facts={'domain':'planar'},allow_task_validated=True,max_cost=2.)
    forward=DiagnosticForward(config)
    public_grid=tuple(map(float,np.linspace(420.,860.,81)))
    available=method.prepare('program')
    if len(available)!=1:
        raise ValueError('frozen one-template generation comparison requires exactly one method')
    public_action,_,_=diagnostic_action_from_method(method,available[0].asset_ref,program_id=run_id+':public-grid',
        sample_id=run_id+':sample',session_id=run_id+':session')
    public_action=replace(public_action,wavelengths_nm=public_grid)
    prediction_sets=[np.asarray([forward.predict(family,{'d':float(d)},
                     public_action) for d in np.linspace(60.,190.,13)]) for family in families]
    planning_ledger=forward.ledger()
    plan=None
    if policy is not None:
        plan=generate_method_plan(method,policy,wavelengths_nm=public_grid,prediction_sets=prediction_sets,
            noise_std=case.noise_std,available_budget=2.)
        asset_ref=plan.asset_ref;wavelengths=plan.wavelengths_nm
    else:
        asset_ref=method.prepare('program')[0].asset_ref;wavelengths=None
    action,program,instantiation=diagnostic_action_from_method(method,asset_ref,program_id=run_id+':program',
        sample_id=run_id+':sample',session_id=run_id+':session',wavelengths_nm=wavelengths)
    provider=IndependentMethodProvider(case,action)
    capability=ToolCapability(effect_class='external_measurement',domain='independent_coherent_planar_TMM',channels=(action.channel,),units=('1',),supports_status=True)
    gate=ExecutionGate(output/'jobs.sqlite',limits={'calls':1.,'cost':2.},capability=capability)
    runner=ProgramRunner(output/'programs.sqlite',gates={action.channel:gate},providers={action.channel:provider})
    commits=[];candidates=[]
    def put(obj):
        target.commit([obj],task_ref=task.ref(),evidence_cutoff=obj.object_id)
        return obj
    def freeze(program):
        frontend=put(DecisionContext(object_id=run_id+':frontend',source_run_id=run_id,
            summary={'frontend':asdict(action.frontend)}))
        artifact=put(ExperimentProgram(object_id=run_id+':program-object',source_run_id=run_id,objective_ref=task.ref(),protocol=asdict(program)))
        identity=AcquisitionIdentity(program_ref=artifact.ref(),node_id=program.nodes[0].node_id,sample_id=action.sample_id,
            session_id=action.session_id,frontend_ref=frontend.ref(),conditions={'angle_deg':action.angle_deg,'polarization':action.polarization,'coordinate_units':'nm'})
        for family in families:
            candidate=put(ModelCandidate(object_id=run_id+':'+family.family_id,source_run_id=run_id,
                model_ir=asdict(family.model),parameter_values={'d':115.}))
            candidates.append(candidate)
            values=np.asarray([forward.predict(family,{'d':float(d)},action) for d in np.linspace(60.,190.,13)])
            radius=norm.ppf(1-config.alpha/(2*len(action.wavelengths_nm)))*case.noise_std
            spec=PredictionSpec(object_id=candidate.object_id+':prediction',source_run_id=run_id,
                candidate_ref=candidate.ref(),program_ref=artifact.ref(),input_refs=(candidate.ref(),),
                snapshot_id=target.latest_snapshot().snapshot_id,raw_channel=action.channel,units='1',statistic='simultaneous_envelope',
                tolerance_source='frozen finite public parameter-grid envelope plus declared Gaussian noise; no global coverage claim',
                observable='reflectance',coordinate=action.wavelengths_nm,lower=tuple(map(float,values.min(axis=0)-radius)),
                upper=tuple(map(float,values.max(axis=0)+radius)),grid={'acquisition_identity':identity.model_dump(mode='json')},
                distribution={'mean':list(map(float,values.mean(axis=0)))})
            commits.append(commit_prediction(target,spec,task_ref=task.ref(),evidence_cutoff='before-method-acquisition'))
        freeze.identity=identity
        return {program.nodes[0].node_id:{'predictions':[c.prediction_ref.model_dump(mode='json') for c in commits],
            'program_ref':artifact.ref().model_dump(mode='json')}}
    try:
        outcome,receipt=method.execute_program(asset_ref,program_id=program.program_id,sample_bindings={'sample':action.sample_id},
            parameter_bindings={'wavelengths_nm':action.wavelengths_nm} if policy else None,
            capabilities={action.channel:capability},limits={'calls':1.,'cost':2.},runner=runner,freeze_predictions=freeze)
        if outcome['status']!='executed' or provider.datum is None:
            raise ValueError('transferred program did not execute: '+outcome['status'])
        datum=provider.datum
        observed=put(Observation(object_id=run_id+':observation',source_run_id=run_id,acquisition_id=datum.acquisition_id,
            objective_ref=task.ref(),binding={'program_ref':freeze.identity.program_ref.model_dump(mode='json'),
                'acquisition_identity':freeze.identity.model_dump(mode='json')},
            raw_blocks=({'block_id':action.channel,'channel':action.channel,'wavelengths_nm':action.wavelengths_nm,
                'coordinate':action.wavelengths_nm,'coordinate_units':'nm','units':'1','angle_deg':action.angle_deg,
                'polarization':action.polarization,'values':datum.values,'covariance':datum.covariance,
                'calibration_group':action.session_id,'calibration_std':0.,'gaussian_validated':True},),
            quality={'execution_status':'completed','numerical_valid':True,'noise_validated':True,'evidence_tier':'V1_synthetic'}))
        evaluations=[]
        for commitment in commits:
            verdict=evaluate_prediction(target,commitment,observed.ref())
            evaluations.append(put(PredictionEvaluation(object_id=commitment.prediction_ref.object_id+':evaluation',source_run_id=run_id,
                prediction_ref=commitment.prediction_ref,observation_ref=observed.ref(),metrics=asdict(verdict))))
        competition=refit_competition(families,(datum,),forward,config)
        refit_context=put(DecisionContext(object_id=run_id+':after-refit',source_run_id=run_id,
            input_refs=(observed.ref(),*(e.ref() for e in evaluations)),summary={'competition':asdict(competition),
                'method_receipt':method.receipt_payload(receipt)}))
        expected='family-'+str(provider.selected)
        passed=competition.retained_families==(expected,) and competition.selected_family==expected
        report={'case':asdict(case),'run_id':run_id,'library_snapshot':method.library_snapshot,
            'method_asset_ref':asset_ref.model_dump(mode='json'),'method_receipt':receipt.model_dump(mode='json'),
            'policy':policy.model_dump(mode='json') if policy else None,'generated_plan':asdict(plan) if plan else None,
            'action':asdict(action),'executed_program':asdict(program),'executed_program_hash':program.content_hash,
            'competition':asdict(competition),'evaluation_refs':[e.ref().model_dump(mode='json') for e in evaluations],
            'completion_branch':method_completion_status(target,evaluation_refs=[e.ref() for e in evaluations],competition_refs=(refit_context.ref(),)),
            'evaluation':{'passed':passed,'true_family':expected,'selected_family':competition.selected_family,
                          'score':1. if passed else 0.},'measurement_cost':gate.usage()['cost'],
            'compute':forward.ledger(),'bounded_resource_limits':{'measurement_cost':2.,'calls':1.,'prediction_calls':16000,'solver_points':800000},
            'planning_compute':{'prediction_calls':planning_ledger['prediction_calls'],
                'solver_points':planning_ledger['solver_points'],'subset_evaluations':plan.evaluated_subsets if plan else 0},
            'source_of_truth':'independent evaluator only; not available to method generation',
            'claim':'small synthetic diagnostic; not population accuracy or hardware validation'}
        if report['generated_plan']:
            report['generated_plan']['asset_ref']=asset_ref.model_dump(mode='json')
        atomic_write_json(output/'METHOD_TRANSFER_RESULT.json',report)
        return report
    finally:
        runner.close();gate.close();target.close();source.close()


def _bare_action(wavelengths):
    from optomind_optics.harness.science.diagnostic_design import DiagnosticAction,DiagnosticFrontEnd
    return DiagnosticAction('public-prediction-grid',DiagnosticFrontEnd('bare'),wavelengths)


def forward_policy_row(task,policy,*,library,case,output):
    report=execute_transfer_case(library,case,output,policy=policy)
    return MethodPolicyRun(task_id=task.task_id,run_id=report['run_id'],policy_hash=policy.version_hash,
        library_snapshot=report['library_snapshot'],executed_program_hash=report['executed_program_hash'],
        score=report['evaluation']['score'],actual_cost=report['measurement_cost'],valid=True,
        output={'result_path':str(Path(output)/'METHOD_TRANSFER_RESULT.json'),'passed':report['evaluation']['passed'],
                'compute':report['compute'],'planning_compute':report['planning_compute'],
                'wavelengths_nm':report['action']['wavelengths_nm']})
