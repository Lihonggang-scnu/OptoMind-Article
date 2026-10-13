from __future__ import annotations

import json
from typing import Literal

from .contracts import (DecisionContext, ExperimentProgram, InferenceState, ModelCandidate,
                        ObjectRef, Observation, PredictionEvaluation, PredictionSpec,
                        ResearchTask, SelectedResearchResult, StrictModel, canonical_hash)
from .method_assets import MethodRegistry, ProgramParameterSlot, ResearchMethodAsset, compile_payload
from .promotion import TransferEvaluation, promote
from .trace_extractor import DecisionTrace, extract_program_template


class MethodLifecycleReceipt(StrictModel):
    status: Literal['registered_bounded_trial', 'not_qualified']
    result_ref: ObjectRef
    asset_refs: tuple[ObjectRef, ...] = ()
    reasons: tuple[str, ...] = ()
    qualification_refs: tuple[ObjectRef, ...] = ()
    transfer_claim: str = 'unvalidated outside source task; explicit bounded trial required'


def _current(repository, object_id):
    snapshot=repository.latest_snapshot()
    ref=next((r for r in snapshot.object_refs if r.object_id==object_id),None) if snapshot else None
    return repository.load(ref) if ref else None


def _commit_once(repository, obj, task_ref):
    try:
        old=repository.load(obj.ref())
    except ValueError:
        repository.commit([obj],task_ref=task_ref,evidence_cutoff=obj.object_id,
                          expected_sequence=repository.latest_snapshot().committed_sequence)
        return obj
    return old


def _sequence_index(repository):
    result={}
    for sequence,payload in repository.db.execute('SELECT sequence,payload FROM snapshots ORDER BY sequence'):
        for ref in json.loads(payload)['object_refs']:
            result.setdefault(ref['content_hash'],sequence)
    return result


def derive_program_domain(repository, prediction, program, task):
    model_refs=tuple(dict.fromkeys((prediction.candidate_ref,) if prediction.candidate_ref else ()))+prediction.input_refs
    models=[repository.load(ref) for ref in model_refs]
    model=next((obj for obj in models if isinstance(obj,ModelCandidate)),None)
    if model is None:
        raise ValueError('no pre-action executable model to bound method domain')
    from .model_ir import model_ir_from_dict
    ir=model_ir_from_dict(model.model_ir)
    if not any(node.operator=='tmm' for node in ir.nodes):
        raise ValueError('source does not establish a planar optical acquisition domain')
    compiled=compile_payload('program',program.protocol)
    measurements=[n for n in compiled.nodes if n.kind=='new_evidence']
    if not measurements or any(n.kind=='state_transition' for n in compiled.nodes):
        raise ValueError('only read-only sample-independent measurement topology is transferable')
    allowed_arguments={'wavelengths_nm','angle_deg','polarization','frontend_hash'}
    if any(n.channel not in {'R','T','A'} or 'wavelengths_nm' not in dict(n.arguments)
           or set(dict(n.arguments))-allowed_arguments for n in measurements):
        raise ValueError('source program has unsupported material-dependent acquisition arguments')
    operator_refs=()
    identity=prediction.grid.get('acquisition_identity') or {}
    if any('frontend_hash' in dict(n.arguments) for n in measurements):
        if not identity.get('frontend_ref'):
            raise ValueError('diagnostic method requires its committed observation operator')
        operator_ref=ObjectRef.model_validate(identity['frontend_ref'])
        operator=repository.load(operator_ref)
        if not isinstance(operator,DecisionContext) or 'frontend' not in operator.summary:
            raise ValueError('missing canonical diagnostic front end')
        from .diagnostic_design import DiagnosticFrontEnd,ReferenceLayer
        raw=operator.summary['frontend']
        frontend=DiagnosticFrontEnd(**{**raw,'layers':tuple(ReferenceLayer(**layer) for layer in raw['layers'])})
        if any(dict(n.arguments).get('frontend_hash')!=frontend.content_hash for n in measurements):
            raise ValueError('diagnostic front end differs from committed program')
        operator_refs=(operator_ref,)
    layer_count=sum(node.operator in {'layer','linear_profile'} for node in ir.nodes)
    if not layer_count:
        raise ValueError('source lacks a declared layered specimen')
    cfg=task.constraints.get('open_research_config',{})
    reference=cfg.get('reference_session','reference')
    names={sample for sample,_ in compiled.initial_samples}
    specimen=names-{reference}
    if len(specimen)!=1 or len(names)>2:
        raise ValueError('one specimen and optional reference are required')
    sample_slots={name:'reference' if name==reference else 'sample' for name in names}
    margin=float(identity.get('conditions',{}).get('bandwidth_nm',0.))/2
    slot=ProgramParameterSlot(name='wavelengths_nm',node_ids=tuple(n.node_id for n in measurements),
        lower=float(ir.domain_nm[0])+margin,upper=float(ir.domain_nm[1])-margin,
        max_points=max(len(dict(n.arguments)['wavelengths_nm']) for n in measurements))
    return {'predicates':{'domain':'planar'},'sample_slots':sample_slots,'parameter_slots':(slot,),
            'domain_evidence_refs':(model.ref(),program.ref(),*operator_refs),'source_layer_count':layer_count}


def discover_completed_traces(repository, *, task_ref, result_ref):
    task=repository.load(task_ref);result=repository.load(result_ref)
    if not isinstance(task,ResearchTask) or not isinstance(result,SelectedResearchResult) or task.source_run_id!=result.source_run_id:
        raise ValueError('completed result must bind the canonical source task')
    objects=[repository.load(ref) for ref in repository.latest_snapshot().object_refs]
    objects=[obj for obj in objects if obj.source_run_id==result.source_run_id]
    sequence=_sequence_index(repository)
    seq=lambda obj:sequence[obj.content_hash]
    contexts=sorted((obj for obj in objects if isinstance(obj,DecisionContext)),key=seq)
    programs=[obj for obj in objects if isinstance(obj,ExperimentProgram)]
    found=[]
    for evaluation in (obj for obj in objects if isinstance(obj,PredictionEvaluation)):
        if evaluation.metrics.get('state') not in {'supported','violated'}:
            continue
        prediction=repository.load(evaluation.prediction_ref)
        returned=repository.load(evaluation.observation_ref)
        if not isinstance(prediction,PredictionSpec) or not isinstance(returned,Observation):
            continue
        program=next((p for p in programs if prediction.program_ref==p.ref() or
                      prediction.grid.get('program')==compile_payload('program',p.protocol).content_hash),None)
        if program is None:
            continue
        before=[ctx for ctx in contexts if seq(ctx)<=min(seq(program),seq(prediction))]
        after=[ctx for ctx in contexts if seq(evaluation)<=seq(ctx)<=seq(result)
               and returned.ref() in (*ctx.input_refs,*ctx.observation_refs)]
        if not before or not after:
            continue
        try:
            domain=derive_program_domain(repository,prediction,program,task)
            channels=tuple(sorted({n.channel for n in compile_payload('program',program.protocol).nodes if n.channel}))
            trace=DecisionTrace(decision_ref=before[-1].ref(),prediction_ref=prediction.ref(),action_ref=program.ref(),
                return_ref=returned.ref(),changed_decision_ref=after[0].ref(),
                decision_sequence=max(seq(before[-1]),seq(prediction),seq(program)),
                permitted_resources=channels,requested_resources=channels,evaluation_ref=evaluation.ref())
            found.append((trace,domain,len(compile_payload('program',program.protocol).nodes),seq(evaluation)))
        except (ValueError,KeyError,TypeError):
            continue
    return tuple((trace,domain) for trace,domain,_,_ in sorted(found,key=lambda row:(-row[2],-row[3])))


def finalize_completed_research(repository, *, task_ref, result_ref):
    task=repository.load(task_ref);result=repository.load(result_ref)
    if not isinstance(task,ResearchTask) or not isinstance(result,SelectedResearchResult):
        raise ValueError('canonical completed research task and result required')
    receipt_id=result.object_id+':method-lifecycle'
    old=_current(repository,receipt_id)
    if old is not None:
        receipt=MethodLifecycleReceipt.model_validate(old.summary)
        if receipt.result_ref!=result_ref:
            raise ValueError('lifecycle completion changed its source result')
        return receipt
    roles=result.candidate_roles
    reasons=[]
    diagnostic=roles.get('adoption_status')=='supported_within_bounded_mechanism_set'
    outcome=roles.get('research_outcome',{})
    if diagnostic:
        if any(outcome.get(k)!=v for k,v in {'mechanism_state':'distinguished','local_prediction_state':'supported','numerical_state':'valid','noise_state':'calibrated'}.items()):
            reasons.append('diagnostic_outcome_not_confirmed')
        competitions=[repository.load(ref) for ref in result.input_refs]
        competitions=[obj for obj in competitions if isinstance(obj,DecisionContext)]
        if not any((obj.summary.get('after_all_family_refits',obj.summary).get('state')=='one_family_retained_within_search'
                    and len(obj.summary.get('after_all_family_refits',obj.summary).get('retained_families',()))==1) for obj in competitions):
            reasons.append('missing_all_family_refit_evidence')
    elif roles.get('adoption_status')!='proposed_design':
        reasons.append('source_adoption_withheld')
    if not diagnostic and roles.get('validation_state')!='supported_within_frozen_noise_envelope':
        reasons.append('independent_source_validation_not_supported')
    if roles.get('unresolved_model_failure') or roles.get('model_set_inadequate'):
        reasons.append('unresolved_source_model_failure')
    evaluation_refs=[]
    for ref in result.asset_refs:
        obj=repository.load(ref)
        if isinstance(obj,PredictionEvaluation):
            evaluation_refs.append(ref)
            if obj.metrics.get('state')!='supported':
                reasons.append('final_prediction_not_supported')
    if not evaluation_refs:
        reasons.append('missing_final_prediction_evaluation')
    if not diagnostic and (result.inference_ref is None or not isinstance(repository.load(result.inference_ref),InferenceState)):
        reasons.append('missing_source_inference')
    assets=[]
    if not reasons:
        traces=discover_completed_traces(repository,task_ref=task_ref,result_ref=result_ref)
        if not traces:
            reasons.append('no_eligible_forward_ordered_program_trace')
        for trace,domain in traces:
            try:
                template=extract_program_template(repository,trace,
                    object_id=result.object_id+':method:'+trace.action_ref.content_hash[:20],source_run_id=result.source_run_id,
                    predicates=domain['predicates'],sample_slots=domain['sample_slots'])
                values=template.model_dump(exclude={'content_hash','schema_version'})
                asset=ResearchMethodAsset(**values,parameter_slots=domain['parameter_slots'],
                    domain_evidence_refs=domain['domain_evidence_refs'],source_layer_count=domain['source_layer_count'],
                    failure_branches=('outside_domain','budget_exhausted','execution_unresolved','prediction_not_evaluable','model_set_inadequate'))
            except ValueError:
                continue
            registry=MethodRegistry(repository)
            _commit_once(repository,asset,task_ref)
            evidence=TransferEvaluation(object_id=asset.object_id+':source-validation',source_run_id=result.source_run_id,
                input_refs=(result_ref,trace.evaluation_ref,*evaluation_refs),asset_ref=asset.ref(),
                task_id=task.object_id,original_task_id=task.object_id,split='T',forward_run_id=result.source_run_id,
                passed=True,cost=asset.cost,scope=asset.applicability_predicates)
            _commit_once(repository,evidence,task_ref)
            latest=_current(repository,asset.object_id)
            if latest.ref()==asset.ref():
                latest,_=promote(registry,asset,evidence,task_ref=task_ref,evidence_cutoff=evidence.object_id)
            if latest.state!='task_validated' or evidence.ref() not in latest.positive_evidence:
                raise ValueError('source method lifecycle was changed after qualification')
            assets.append(latest.ref())
            break
        if not assets:
            reasons.append('no_verified_extractable_program')
    receipt=MethodLifecycleReceipt(status='registered_bounded_trial' if assets else 'not_qualified',
        result_ref=result_ref,asset_refs=tuple(assets),reasons=tuple(dict.fromkeys(reasons)),
        qualification_refs=tuple(evaluation_refs))
    _commit_once(repository,DecisionContext(object_id=receipt_id,source_run_id=result.source_run_id,
        input_refs=(result_ref,*assets,*evaluation_refs),summary=receipt.model_dump(mode='json')),task_ref)
    return receipt


def method_completion_status(repository, *, evaluation_refs, inference_refs=(), competition_refs=()):
    evaluations=[repository.load(ref) for ref in evaluation_refs]
    if not evaluations or any(not isinstance(obj,PredictionEvaluation) for obj in evaluations):
        return 'prediction_not_evaluable'
    states={obj.metrics.get('state') for obj in evaluations}
    if states-{'supported','violated'}:
        return 'prediction_not_evaluable'
    inferences=[repository.load(ref) for ref in inference_refs]
    if any(not isinstance(obj,InferenceState) for obj in inferences):
        return 'prediction_not_evaluable'
    competitions=[repository.load(ref) for ref in competition_refs]
    if any(not isinstance(obj,DecisionContext) for obj in competitions):
        return 'prediction_not_evaluable'
    inadequate=any(obj.summary.get('competition',obj.summary.get('after_all_family_refits',obj.summary)).get('state')
                   =='model_family_inadequate' for obj in competitions)
    if inadequate or any(obj.diagnostics.get('model_set_inadequate') for obj in inferences):
        return 'model_set_inadequate'
    return 'independent_validation_required'
