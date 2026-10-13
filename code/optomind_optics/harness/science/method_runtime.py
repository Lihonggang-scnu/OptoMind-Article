"""Opt-in frozen executable-library consumer for real research runtimes.

No parallel store and no automatic promotion: this resolves existing canonical
MethodAssets, recompiles their actual payloads and emits receipts distinguishing
preparation from execution. Callers choose model/program candidates from the
returned typed objects and retain the receipt in their decision trajectory.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass, is_dataclass, replace
import json

from .contracts import ObjectRef, ResearchTask, StrictModel, canonical_hash
from .method_assets import MethodLibrarySnapshot, compile_payload, consume_asset, _verify_execution_evidence
from .method_retriever import RetrievalReceipt, retrieve
from .applicability import check_applicability


class MethodConsumptionReceipt(StrictModel):
    library_snapshot: str
    target_task_ref: ObjectRef
    asset_ref: ObjectRef
    kind: str
    transfer_mode: str
    facts_hash: str
    payload_hash: str
    compiled_identity: str
    status: str
    output_hash: str = ''
    sample_bindings: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class PreparedMethod:
    asset_ref: ObjectRef
    kind: str
    compiled: object
    receipt: MethodConsumptionReceipt


def _public_facts(facts):
    # Applicability facts are scientific premises, not arbitrary evaluator or
    # reflection documents. Prevent convenient back doors into a policy view.
    blocked = {'ground_truth','target','answer','future','future_feedback','feedback','credentials','password','api_key','secret'}
    if not isinstance(facts, dict):
        raise ValueError('applicability facts must be a scalar premise map')
    for key, value in facts.items():
        if not isinstance(key, str) or key.lower() in blocked or not isinstance(value, (str, int, float, bool, type(None))):
            raise ValueError('applicability facts contain forbidden or non-scalar fields')
    return json.loads(json.dumps(facts, allow_nan=False))


def _jsonable(value):
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if hasattr(value, 'tolist'):
        return value.tolist()
    if isinstance(value, (tuple, list)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k,v in value.items()}
    return value


class FrozenMethodProvider:
    """Freeze retrieval before task decisions; reject subsequent asset retirement.

    `allow_task_validated` is for explicit bounded trial/reuse, not promotion.
    Production policy loading requires promoted canonical P evidence by default.
    Recalibration is supplied as an already committed, task-bound receipt.
    """
    def __init__(self, registry, *, task_ref, facts, max_cost=float('inf'),
                 allow_task_validated=False, calibration_refs=None):
        task = registry.repository.load(task_ref)
        if not isinstance(task, ResearchTask):
            raise ValueError('canonical target ResearchTask required')
        self.registry = registry
        self.task_ref = task_ref
        self._facts_json = json.dumps(_public_facts(facts), sort_keys=True, allow_nan=False)
        if type(allow_task_validated) is not bool:
            raise ValueError("bounded-trial activation must be an explicit boolean")
        self.allow_task_validated = allow_task_validated
        self.calibration_refs = dict(calibration_refs or {})
        snapshot = registry.snapshot()
        # Bind actual asset versions, not a mutable repository head that also
        # advances when unrelated observation receipts are committed.
        self.snapshot = MethodLibrarySnapshot(snapshot_id=canonical_hash([r.model_dump(mode='json') for r in snapshot.asset_refs]),
                                              asset_refs=snapshot.asset_refs)
        raw = retrieve(registry, self.facts, max_cost=max_cost)
        selected=[]; rejected=list(raw.rejected)
        for ref in raw.selected:
            asset=registry.repository.load(ref)
            reasons=[]
            if asset.state != 'promoted' and not self.allow_task_validated:
                reasons.append('bounded_trial_required_before_production_use')
            if asset.transfer_mode not in ('structure','initialization'):
                reasons.append('unsupported_transfer_consumer:'+asset.transfer_mode)
            if reasons: rejected.append((ref,tuple(reasons)))
            else: selected.append(ref)
        self.retrieval = RetrievalReceipt(library_snapshot=self.snapshot.snapshot_id,query_hash=raw.query_hash,
                                          selected=tuple(selected),rejected=tuple(rejected))
        self.receipts=[]

    @property
    def facts(self):
        return json.loads(self._facts_json)

    @property
    def library_snapshot(self):
        return self.snapshot.snapshot_id

    @property
    def repository_id(self):
        # An explicit local library-instance namespace, separate from its frozen
        # content pin. Moving a library is a new binding, never an implicit alias.
        return 'method-library:'+canonical_hash({'canonical_repository_root':str(self.registry.repository.root.resolve())})

    def external_asset_ref(self, ref):
        if ref not in self.snapshot.asset_refs:
            raise ValueError('external asset reference is outside the frozen library')
        self.registry.repository.load(ref)
        return {'external_scientific_ref':{'repository_id':self.repository_id,
                'snapshot_id':self.library_snapshot,'object_ref':ref.model_dump(mode='json')}}

    def receipt_payload(self, receipt):
        if receipt.library_snapshot!=self.library_snapshot or receipt.target_task_ref!=self.task_ref:
            raise ValueError('method receipt differs from frozen provider authority')
        payload=receipt.model_dump(mode='json')
        payload['asset_ref']=self.external_asset_ref(receipt.asset_ref)
        return payload

    def _asset(self, ref):
        if ref not in self.retrieval.selected:
            raise ValueError('asset was not selected in the frozen retrieval')
        current={r.object_id:r for r in self.registry.snapshot().asset_refs}
        if current.get(ref.object_id) != ref:
            raise ValueError('asset version changed or retired after frozen retrieval')
        asset=self.registry.repository.load(ref)
        asset=type(asset).model_validate(asset.model_dump(mode='json'))
        if not check_applicability(asset,self.facts).allowed:
            raise ValueError('frozen asset no longer applicable')
        _verify_execution_evidence(asset,self.facts,repository=self.registry.repository,
                                   calibration_ref=self.calibration_refs.get(ref.content_hash),target_task_ref=self.task_ref)
        if asset.state=='promoted':
            from .promotion import TransferEvaluation
            evidence=[self.registry.repository.load(r) for r in asset.positive_evidence]
            def supports(e):
                if not (isinstance(e,TransferEvaluation) and e.passed and not e.negative_transfer and e.split=='P'
                        and e.task_id!=e.original_task_id and e.asset_ref.object_id==asset.object_id
                        and all(k in e.scope and e.scope[k]==v for k,v in asset.applicability_predicates.items())):
                    return False
                source=self.registry.repository.load(e.asset_ref)
                return (source.kind==asset.kind and source.payload==asset.payload
                        and source.transfer_mode==asset.transfer_mode
                        and source.applicability_predicates==asset.applicability_predicates)
            if not any(supports(e) for e in evidence):
                raise ValueError('production asset requires canonical new-task promotion evidence')
        return asset

    def prepare(self, kind):
        if kind not in ('model_fragment','program','policy'):
            raise ValueError('unknown method kind')
        prepared=[]
        for ref in self.retrieval.selected:
            if self.registry.repository.load(ref).kind != kind:
                continue
            asset=self._asset(ref)
            compiled=compile_payload(kind,asset.payload)
            identity=(compiled.ir.structural_hash if kind=='model_fragment' else
                      canonical_hash(_jsonable(compiled)))
            receipt=MethodConsumptionReceipt(library_snapshot=self.library_snapshot,target_task_ref=self.task_ref,
                asset_ref=ref,kind=kind,transfer_mode=asset.transfer_mode,facts_hash=canonical_hash(self.facts),
                payload_hash=canonical_hash(asset.payload),compiled_identity=identity,status='compiled_not_executed')
            self.receipts.append(receipt)
            prepared.append(PreparedMethod(ref,kind,compiled,receipt))
        return tuple(prepared)

    def instantiate_program(self, asset_ref, *, program_id, sample_bindings, parameter_bindings=None):
        """Rename explicit sample slots and execution/correlation identity only.

        Node topology, channel, coordinates, costs, state and branch logic stay
        byte-equivalent after inverse slot renaming. The host still recompiles
        the returned program against actual capabilities and remaining budget.
        """
        asset=self._asset(asset_ref)
        if asset.kind!='program' or not isinstance(program_id,str) or not program_id:
            raise ValueError('program asset and fresh execution identity required')
        program=compile_payload('program',asset.payload)
        if program_id==program.program_id:
            raise ValueError('a reused program needs a fresh execution identity')
        declared={sample for sample,_ in program.initial_samples}
        used={node.sample_id for node in program.nodes if node.sample_id}
        if not used.issubset(declared) or set(sample_bindings)!=declared:
            raise ValueError('sample bindings must cover exactly all declared program slots')
        if any(not isinstance(v,str) or not v for v in sample_bindings.values()) or len(set(sample_bindings.values()))!=len(sample_bindings):
            raise ValueError('distinct sample slots require distinct nonempty bound identities')
        groups={g:f'{program_id}:asset-correlation:{i}' for i,g in enumerate(sorted({n.correlation_group for n in program.nodes if n.correlation_group}))}
        nodes=tuple(replace(node,sample_id=sample_bindings[node.sample_id] if node.sample_id else '',
                            correlation_group=groups.get(node.correlation_group,'')) for node in program.nodes)
        instantiated=replace(program,program_id=program_id,nodes=nodes,
                             initial_samples=tuple((sample_bindings[s],state) for s,state in program.initial_samples))
        if parameter_bindings:
            from .method_assets import ResearchMethodAsset
            import math
            if not isinstance(asset, ResearchMethodAsset):
                raise ValueError('parameter bindings require a qualified research method')
            slots={slot.name:slot for slot in asset.parameter_slots}
            if set(parameter_bindings)-set(slots):
                raise ValueError('undeclared method parameter slot')
            for name,values in parameter_bindings.items():
                slot=slots[name]
                if (not isinstance(values,(list,tuple)) or not 1<=len(values)<=slot.max_points
                        or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v)
                               or not slot.lower<=v<=slot.upper for v in values)
                        or any(a>=b for a,b in zip(values,values[1:]))):
                    raise ValueError('method parameter binding outside declared domain')
            nodes=[]
            for node in instantiated.nodes:
                arguments=dict(node.arguments)
                for name,values in parameter_bindings.items():
                    if node.node_id in slots[name].node_ids:
                        arguments[name]=tuple(float(v) for v in values)
                nodes.append(replace(node,arguments=tuple(arguments.items())))
            instantiated=replace(instantiated,nodes=tuple(nodes))
        receipt=MethodConsumptionReceipt(library_snapshot=self.library_snapshot,target_task_ref=self.task_ref,
            asset_ref=asset_ref,kind='program',transfer_mode=asset.transfer_mode,facts_hash=canonical_hash(self.facts),
            payload_hash=canonical_hash(asset.payload),compiled_identity=instantiated.content_hash,
            status='instantiated_not_executed',sample_bindings=tuple(sorted(sample_bindings.items())))
        self.receipts.append(receipt)
        return instantiated,receipt

    def execute_program(self, asset_ref, *, program_id, sample_bindings, capabilities,
                        limits, runner, freeze_predictions, parameter_bindings=None):
        from .program_compiler import compile_program
        from .method_assets import ResearchMethodAsset
        asset=self._asset(asset_ref)
        if not isinstance(asset,ResearchMethodAsset):
            raise ValueError('qualified research method required')
        try:
            program,prepared=self.instantiate_program(asset_ref,program_id=program_id,
                sample_bindings=sample_bindings,parameter_bindings=parameter_bindings)
        except ValueError as exc:
            return {'status':'outside_domain','error':str(exc),'executed':False},None
        try:
            plan=compile_program(program,capabilities=capabilities,limits=limits)
        except ValueError as exc:
            reason='budget_exhausted' if 'budget' in str(exc) or 'resource' in str(exc) else 'outside_domain'
            return {'status':reason,'error':str(exc),'executed':False},None
        commits=freeze_predictions(program)
        executed=runner.run(plan,prediction_commits=commits)
        status='executed' if executed.state=='completed' else 'execution_unresolved'
        receipt=prepared.model_copy(update={'status':status,'output_hash':canonical_hash(_jsonable(executed))})
        self.receipts.append(receipt)
        return {'status':status,'executed':True,'program':program,'execution':executed},receipt

    def consume(self, asset_ref, **context):
        asset=self._asset(asset_ref)
        # The canonical repository/task/calibration identity cannot be replaced
        # through a caller's consumer context.
        forbidden={'repository','target_task_ref','calibration_ref','facts'} & set(context)
        if forbidden:
            raise ValueError('caller cannot replace frozen method authority')
        result=consume_asset(asset,facts=self.facts,repository=self.registry.repository,target_task_ref=self.task_ref,
                             calibration_ref=self.calibration_refs.get(asset_ref.content_hash),**context)
        prepared=next(x for x in self.prepare(asset.kind) if x.asset_ref==asset_ref)
        receipt=prepared.receipt.model_copy(update={'status':'executed','output_hash':canonical_hash(_jsonable(result))})
        self.receipts.append(receipt)
        return result,receipt


def diagnostic_action_from_method(provider, asset_ref, *, program_id, sample_id, session_id, wavelengths_nm=None):
    from .contracts import PredictionSpec
    from .diagnostic_design import DiagnosticAction,DiagnosticFrontEnd,ReferenceLayer
    asset=provider._asset(asset_ref)
    program,receipt=provider.instantiate_program(asset_ref,program_id=program_id,
        sample_bindings={'sample':sample_id},parameter_bindings={'wavelengths_nm':tuple(wavelengths_nm)} if wavelengths_nm is not None else None)
    measured=[node for node in program.nodes if node.kind=='new_evidence']
    if len(measured)!=1 or any(node.kind=='state_transition' for node in program.nodes):
        raise ValueError('diagnostic adapter requires one non-destructive sample acquisition')
    node=measured[0];arguments=dict(node.arguments)
    source_prediction=next((provider.registry.repository.load(ref) for ref in asset.input_refs
                           if isinstance(provider.registry.repository.load(ref),PredictionSpec)),None)
    identity=source_prediction.grid.get('acquisition_identity',{}) if source_prediction else {}
    conditions=identity.get('conditions',{})
    frontend=DiagnosticFrontEnd('bare')
    if 'frontend_hash' in arguments:
        if not identity.get('frontend_ref'):
            raise ValueError('diagnostic method lacks its source front end')
        source=provider.registry.repository.load(ObjectRef.model_validate(identity['frontend_ref']))
        raw=source.summary['frontend']
        frontend=DiagnosticFrontEnd(**{**raw,'layers':tuple(ReferenceLayer(**layer) for layer in raw['layers'])})
        if frontend.content_hash!=arguments['frontend_hash']:
            raise ValueError('diagnostic method front-end identity mismatch')
    total=dict(node.estimate).get('cost',0.)
    action=DiagnosticAction(program_id+':'+node.node_id,frontend,tuple(arguments['wavelengths_nm']),
        float(arguments.get('angle_deg',0.)),arguments.get('polarization','s'),
        float(conditions.get('bandwidth_nm',0.)),float(conditions.get('angular_spread_deg',0.)),
        sample_id,session_id,total-frontend.mounting_cost,node.channel)
    return action,program,receipt
