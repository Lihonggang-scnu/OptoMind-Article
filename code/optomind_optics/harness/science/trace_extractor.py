"""Extraction takes committed pre-action references, never narrative answers."""
from .contracts import StrictModel, ObjectRef, canonical_hash
from .method_assets import MethodAsset, compile_payload

class DecisionTrace(StrictModel):
    decision_ref: ObjectRef
    prediction_ref: ObjectRef
    action_ref: ObjectRef
    return_ref: ObjectRef
    changed_decision_ref: ObjectRef
    decision_sequence: int
    permitted_resources: tuple[str,...]
    requested_resources: tuple[str,...]
    evaluation_ref: ObjectRef | None = None

def extract_asset(repository, trace, *, kind, payload, object_id, source_run_id, predicates):
    refs=(trace.decision_ref,trace.prediction_ref,trace.action_ref,trace.return_ref,trace.changed_decision_ref)
    sequences=[repository.object_commit_sequence(ref) for ref in refs]
    if any(s > trace.decision_sequence for s in sequences[:3]): raise ValueError("future information in decision inputs")
    if sequences[3] <= trace.decision_sequence or sequences[4] < sequences[3]: raise ValueError("trajectory is not forward ordered")
    if set(trace.requested_resources)-set(trace.permitted_resources): raise ValueError("unlicensed resources")
    from .contracts import DecisionContext, PredictionSpec, ExperimentProgram, ModelCandidate, Observation
    decision,prediction,action,returned,changed=[repository.load(ref) for ref in refs]
    if not isinstance(decision,DecisionContext) or not isinstance(prediction,PredictionSpec) or not isinstance(returned,Observation) or not isinstance(changed,DecisionContext):
        raise ValueError("complete typed decision/prediction/observation trajectory required")
    from .contracts import PredictionEvaluation
    if trace.evaluation_ref is None:
        raise ValueError("committed prediction/observation evaluation required for extraction")
    evaluation=repository.load(trace.evaluation_ref)
    if (not isinstance(evaluation,PredictionEvaluation) or evaluation.prediction_ref!=prediction.ref()
            or evaluation.observation_ref!=returned.ref()
            or not sequences[3]<=repository.object_commit_sequence(evaluation.ref())<=sequences[4]):
        raise ValueError("trace prediction and returned observation are not bound by its evaluation")
    if returned.ref() not in (*changed.observation_refs,*changed.input_refs):
        raise ValueError("changed decision does not consume the returned observation")
    if isinstance(action,ExperimentProgram):
        compiled_action=compile_payload("program",action.protocol)
        if prediction.program_ref!=action.ref() and prediction.grid.get("program")!=compiled_action.content_hash:
            raise ValueError("prediction does not bind the pre-action program")
        channels={n.channel for n in compiled_action.nodes if n.kind in ("new_evidence","state_transition")}
        if channels-set(trace.requested_resources):
            raise ValueError("program resources are missing from the trace permission declaration")
    elif isinstance(action,ModelCandidate):
        if prediction.candidate_ref!=action.ref() and action.ref() not in prediction.input_refs:
            raise ValueError("prediction does not bind the pre-action model")
    if kind == "program" and isinstance(action,ExperimentProgram): source_payload=action.protocol
    elif kind == "model_fragment" and isinstance(action,ModelCandidate): source_payload=action.model_ir
    elif kind == "policy": source_payload=decision.summary.get("experiment_policy")
    else: raise ValueError("action is not the executable source kind")
    if source_payload is None or canonical_hash(payload)!=canonical_hash(source_payload):
        raise ValueError("payload must be present in the committed pre-action state")
    # Payload grammar rejects answer fields, paths, arbitrary code and unknown keys.
    compile_payload(kind,payload)
    declared_cost=sum(dict(n.estimate).get("cost",0.) for n in compile_payload("program",payload).nodes) if kind=="program" else 0.
    return MethodAsset(object_id=object_id,source_run_id=source_run_id,kind=kind,payload=payload,input_refs=(*refs,evaluation.ref()),applicability_predicates=predicates,cost=declared_cost)

def equivalence_key(asset):
    compiled=compile_payload(asset.kind,asset.payload)
    structure=asset.payload
    if asset.kind=="model_fragment":
        from dataclasses import replace
        ir=compiled.ir
        parameters=tuple(replace(p,lower=p.lower*1000,upper=p.upper*1000,initial=p.initial*1000,unit="nm") if p.unit=="um" else p for p in ir.parameters)
        nodes=[]
        for node in ir.nodes:
            if node.operator in ("layer","linear_profile") and node.unit=="um":
                attrs=tuple((key,value*1000 if key in ("thickness","shared_bias") and isinstance(value,(int,float)) else value) for key,value in node.attributes)
                node=replace(node,unit="nm",attributes=attrs)
            nodes.append(node)
        normalized=replace(ir,nodes=tuple(nodes),parameters=parameters)
        structure={"structure":normalized.structural_hash}
        if asset.transfer_mode!="structure": structure["initial_values"]=tuple(p.initial for p in parameters)
    return canonical_hash({"kind":asset.kind,"structure":structure,"premises":asset.applicability_predicates,"transfer_mode":asset.transfer_mode})

def finite_domain_equivalent(left,right,*,wavelengths_nm,atol=1e-8):
    import numpy as np
    if left.kind != "model_fragment" or right.kind != left.kind or left.applicability_predicates != right.applicability_predicates: return False
    a=compile_payload(left.kind,left.payload); b=compile_payload(right.kind,right.payload)
    return bool(np.allclose(a.predict({},wavelengths_nm),b.predict({},wavelengths_nm),rtol=0,atol=atol))


def normalize_program_template(payload, sample_slots):
    """Deterministic identity-only transformation, replayable from source bytes."""
    from dataclasses import asdict, replace
    program=compile_payload("program",payload)
    declared={name for name,_ in program.initial_samples}
    if set(sample_slots)!=declared or len(set(sample_slots.values()))!=len(sample_slots):
        raise ValueError("template mapping must cover all distinct declared sample slots")
    if any(not isinstance(v,str) or not v or v not in {"sample","reference"} for v in sample_slots.values()):
        raise ValueError("only explicit sample/reference template roles are supported")
    if any(node.sample_id and node.sample_id not in declared for node in program.nodes):
        raise ValueError("program uses an undeclared sample identity")
    groups={g:f"template-correlation:{i}" for i,g in enumerate(sorted({n.correlation_group for n in program.nodes if n.correlation_group}))}
    nodes=tuple(replace(n,sample_id=sample_slots[n.sample_id] if n.sample_id else "",
                        correlation_group=groups.get(n.correlation_group,"")) for n in program.nodes)
    samples=tuple((sample_slots[name],state) for name,state in program.initial_samples)
    identity=canonical_hash({"nodes":[asdict(n) for n in nodes],"samples":samples,"revision":program.revision})
    return replace(program,program_id="method-template:"+identity,nodes=nodes,initial_samples=samples)


def extract_program_template(repository, trace, *, object_id, source_run_id, predicates, sample_slots):
    """Extract from a real pre-action protocol, then remove incidental identities.

    No values, costs, controls or branch logic are learned from the returned
    observation. The canonical source and reversible slot map remain attached,
    and every execution rederives the exact normalized payload from that source.
    The new asset remains proposed until separately validated.
    """
    from dataclasses import asdict
    from .contracts import ExperimentProgram
    source=repository.load(trace.action_ref)
    if not isinstance(source,ExperimentProgram):
        raise ValueError("program extraction requires a canonical ExperimentProgram")
    asset=extract_asset(repository,trace,kind="program",payload=source.protocol,
                        object_id=object_id,source_run_id=source_run_id,predicates=predicates)
    template=normalize_program_template(source.protocol,sample_slots)
    from .method_assets import ProgramTemplateAsset
    return ProgramTemplateAsset(**{**asset.model_dump(),"schema_version":"program-template-asset.v1","content_hash":"","payload":asdict(template),
                          "template_source_ref":source.ref(),"template_sample_slots":tuple(sorted(sample_slots.items()))})
