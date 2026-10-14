"""Opt-in finite scientific policy for the existing Article execution stage.

The policy selects only attested ready requests. All actual work stays behind the
existing coordinator/gateway; finite-world valuation never executes a solver.
"""
from dataclasses import asdict
import json
import inspect
import types
from pathlib import Path
from .contracts import DecisionContext, PredictionSpec, SelectedResearchResult, ModelCandidate, InferenceState, canonical_hash
from .controller import decide_next, execute_round
from optomind_research.runtime.artifact_store import atomic_write_json


def classifier_fingerprint(callback, protocol_id, version):
    """Stable declared protocol fingerprint, never repr/object memory address.

    Classifiers must be Python functions with JSON-serializable captured config.
    Hidden mutable globals are fingerprinted too; modules/helpers must instead be
    represented by an explicitly versioned pure adapter at this boundary.
    """
    if not protocol_id or not version or not isinstance(callback, types.FunctionType):
        raise ValueError("classifier needs a registered protocol ID/version and pure Python function")
    def code_payload(code):
        constants=[]
        for value in code.co_consts:
            if isinstance(value, types.CodeType): constants.append(code_payload(value))
            elif isinstance(value, bytes): constants.append({"bytes":value.hex()})
            else: constants.append(value)
        return {"bytecode":code.co_code.hex(),"constants":constants,"names":code.co_names,"arguments":code.co_varnames,"argcount":code.co_argcount}
    captured=inspect.getclosurevars(callback)
    try:
        return canonical_hash({"protocol_id":protocol_id,"version":version,"code":code_payload(callback.__code__),"defaults":callback.__defaults__,"kwdefaults":callback.__kwdefaults__,"nonlocals":captured.nonlocals,"globals":captured.globals})
    except (TypeError,ValueError) as exc:
        raise ValueError("classifier captured configuration must be explicitly serializable") from exc


_CLASSIFIER_PROTOCOLS = {}

def register_outcome_classifier(protocol_id, version, callback):
    digest=classifier_fingerprint(callback,protocol_id,version)
    key=(protocol_id,version)
    previous=_CLASSIFIER_PROTOCOLS.setdefault(key,digest)
    if previous != digest:
        raise ValueError("classifier protocol version already registered with different implementation/configuration")
    return digest


class SciencePipelinePolicy:
    def __init__(self, *, problem, probabilities, actions, action_request_ids,
                 design_candidate_refs, coordinator, classify_outcome,
                 available_budget, final_validation_reserve, depth=2,
                 unknown_action_ids=(), inference_ref=None,
                 classifier_protocol_id=None, classifier_protocol_version=None):
        if coordinator.execution_gate is None or coordinator.science_repository is None:
            raise ValueError("science policy requires durable execution gate and authoritative repository")
        if set(action_request_ids) != {a.action_id for a in actions}:
            raise ValueError("every valued action must bind exactly one compiled request")
        if len(set(action_request_ids.values())) != len(action_request_ids):
            raise ValueError("different acquisitions require distinct request identities")
        if set(design_candidate_refs) != set(problem.design_ids):
            raise ValueError("terminal design pool must bind real scientific candidates")
        self.problem=problem;self.probabilities=tuple(probabilities);self.actions=tuple(actions)
        self.action_request_ids=dict(action_request_ids);self.design_candidate_refs=dict(design_candidate_refs)
        self.coordinator=coordinator;self.classify_outcome=classify_outcome
        self.classifier_protocol_id=classifier_protocol_id
        self.classifier_protocol_version=classifier_protocol_version
        register_outcome_classifier(classifier_protocol_id,classifier_protocol_version,classify_outcome)
        self.available_budget=available_budget;self.final_validation_reserve=final_validation_reserve
        self.depth=depth;self.unknown_action_ids=tuple(unknown_action_ids);self.inference_ref=inference_ref
        self.decision=None;self.context=None;self.selected_result=None
        self.execution_requests={}
        self.prediction_refs=()
        self._expected_snapshot_id=None
        self._frozen_binding=None
        self._executed_hashes={}

    def _binding(self):
        return {"problem":self.problem.cache_key,"probabilities":self.probabilities,"actions":[asdict(a) for a in self.actions],"action_request_ids":self.action_request_ids,"design_candidate_refs":{key:ref.model_dump(mode="json") for key,ref in self.design_candidate_refs.items()},"inference_ref":self.inference_ref.model_dump(mode="json") if self.inference_ref else None,"classifier_protocol_id":self.classifier_protocol_id,"classifier_protocol_version":self.classifier_protocol_version,"classifier_hash":classifier_fingerprint(self.classify_outcome,self.classifier_protocol_id,self.classifier_protocol_version),"available_budget":self.available_budget,"validation_reserve":self.final_validation_reserve,"depth":self.depth,"unknown_action_ids":self.unknown_action_ids}

    def _check_frozen(self):
        if self._frozen_binding is None or canonical_hash(self._binding()) != self._frozen_binding:
            raise ValueError("scientific policy or classifier binding changed after commitment")
        latest=self.coordinator.science_repository.latest_snapshot()
        if latest.snapshot_id != self._expected_snapshot_id:
            raise ValueError("scientific snapshot changed; revaluation required before execution")

    def plan(self, requests, work_dir):
        repo=self.coordinator.science_repository
        for ref in self.design_candidate_refs.values():
            if not isinstance(repo.load(ref),ModelCandidate):
                raise ValueError("terminal design reference must resolve to a ModelCandidate")
        if self.inference_ref is not None and not isinstance(repo.load(self.inference_ref),InferenceState):
            raise ValueError("inference reference must resolve to an InferenceState")
        ready={r.request_id:r for r in requests}
        missing=set(self.action_request_ids.values())-ready.keys()
        if missing: raise ValueError(f"valued actions lack ready authorized requests: {sorted(missing)}")
        self.decision=decide_next(self.problem,self.probabilities,self.actions,available_budget=self.available_budget,final_validation_reserve=self.final_validation_reserve,depth=self.depth,unknown_action_ids=self.unknown_action_ids)
        selected_ids=tuple(self.action_request_ids[key] for key in self.decision.selected_actions)
        self.execution_requests={key:ready[key] for key in selected_ids}
        # Freeze exact compiled request hashes and finite prediction kernels before any effect.
        frozen_binding=self._binding()
        self._frozen_binding=canonical_hash(frozen_binding)
        prediction={"frozen_binding":frozen_binding,"decision":asdict(self.decision),"requests":{key:canonical_hash(ready[key].model_dump(mode="json")) for key in selected_ids},"actions":[asdict(a) for a in self.actions],"commitment_mode":"bayesian_precommit","final_validation_reserve":self.final_validation_reserve}
        self.context=DecisionContext(object_id=f"decision:{self.decision.context_hash}",source_run_id=self.coordinator.adapter.run_id,input_refs=tuple(self.design_candidate_refs.values()),summary=prediction,actual_messages=())
        latest=repo.latest_snapshot()
        existing=next((ref for ref in latest.object_refs if ref.object_id==self.context.object_id),None)
        if existing:
            if existing != self.context.ref(): raise ValueError("decision recovery identity mismatch")
        else:
            repo.commit([self.context],task_ref=self.coordinator.science_task_ref,evidence_cutoff="before:"+self.context.object_id,expected_sequence=latest.committed_sequence)
        prediction_refs=[]
        for action in self.actions:
            if action.action_id not in self.decision.selected_actions: continue
            pid=f"prediction:{self.decision.context_hash}:{action.action_id}"
            latest=repo.latest_snapshot()
            existing=next((ref for ref in latest.object_refs if ref.object_id==pid),None)
            if existing:
                spec=repo.load(existing)
                if spec.distribution != {"world_ids":list(self.problem.world_ids),"kernel":[list(row) for row in action.kernel]}:
                    raise ValueError("frozen action prediction differs on recovery")
            else:
                spec=PredictionSpec(object_id=pid,source_run_id=self.coordinator.adapter.run_id,input_refs=(self.context.ref(),),snapshot_id=latest.snapshot_id,observable="categorical_action_outcome",raw_channel=action.action_id,units="category",statistic="categorical_kernel",tolerance_source="declared_finite_world_kernel",grid={"support":list(action.observation_ids)},distribution={"world_ids":list(self.problem.world_ids),"kernel":[list(row) for row in action.kernel]})
                repo.commit([spec],task_ref=self.coordinator.science_task_ref,evidence_cutoff="before:"+pid,expected_sequence=latest.committed_sequence)
            prediction_refs.append(spec.ref())
        self.prediction_refs=tuple(prediction_refs)
        self.coordinator.prediction_commit={"decision_context_ref":self.context.ref().model_dump(mode="json"),"prediction_refs":[ref.model_dump(mode="json") for ref in self.prediction_refs],"prediction_hash":canonical_hash(prediction)}
        atomic_write_json(Path(work_dir)/"SCIENCE_DECISION.json",{**prediction,"decision_context_ref":self.context.ref().model_dump(mode="json"),"policy_selected_request_ids":list(selected_ids),"policy_omitted_request_ids":sorted(set(ready)-set(selected_ids)),"outcome":"pending_unknown_value" if not selected_ids and self.unknown_action_ids else ("selected" if selected_ids else "stopped")})
        self._expected_snapshot_id=repo.latest_snapshot().snapshot_id
        return selected_ids

    def execute(self, request):
        self._check_frozen()
        if self.context is None or request.request_id not in self.execution_requests:
            raise ValueError("request was not selected by committed scientific policy")
        if request != self.execution_requests[request.request_id]:
            raise ValueError("compiled request changed after prediction commitment")
        before=self.coordinator.science_repository.latest_snapshot()
        result=self.coordinator.execute(request)
        after=self.coordinator.science_repository.latest_snapshot()
        if after.snapshot_id != before.snapshot_id:
            prior={ref.object_id:ref for ref in before.object_refs}
            changed=[ref for ref in after.object_refs if prior.get(ref.object_id)!=ref]
            if after.committed_sequence != before.committed_sequence+1 or len(changed)!=1 or changed[0].object_id!=result.observation.observation_id:
                raise ValueError("unexpected scientific state update during acquisition")
        self._expected_snapshot_id=after.snapshot_id
        self._executed_hashes[request.request_id]=canonical_hash(result.model_dump(mode="json"))
        return result

    def finalize(self, executions, work_dir):
        self._check_frozen()
        if self.decision is None: raise ValueError("policy has not been committed")
        by_request={request.request_id:result for request,result in executions}
        classified={}
        def acquire(action):
            request_id=self.action_request_ids[action.action_id]
            if request_id not in by_request: raise ValueError("selected acquisition has no execution receipt")
            result=by_request[request_id]
            if self._executed_hashes.get(request_id) != canonical_hash(result.model_dump(mode="json")):
                raise ValueError("acquisition result changed after actual execution")
            if result.request_id != request_id or result.task_hash != self.execution_requests[request_id].task_hash:
                raise ValueError("acquisition receipt identity mismatch")
            if result.receipt.get("status") != "adapter_completed" or result.observation.status.value not in ("completed","physically_valid"):
                raise ValueError("failed acquisition cannot select a committed continuation")
            outcome=self.classify_outcome(action,result)
            classified[action.action_id]={"outcome":outcome,"request_id":request_id,"receipt_hash":canonical_hash(result.receipt),"observation_id":result.observation.observation_id}
            return outcome
        receipt=execute_round(self.problem,self.probabilities,self.actions,self.decision,acquire,current_context_hash=self.decision.context_hash)
        atomic_write_json(Path(work_dir)/"SCIENCE_ITERATION_RECEIPT.json",{**asdict(receipt),"classified_receipts":classified,"prediction_evaluation":{"state":"not_evaluable","reason":"categorical finite-world predictions are frozen; legacy Article observations lack calibrated raw channels for C08 numerical adequacy testing"}})
        if receipt.outcome == "pending_unknown_value": return None
        observed_ids={row[1].observation.observation_id for row in executions if row[0].request_id in self.execution_requests}
        observation_refs=tuple(ref for ref in self.coordinator.science_repository.latest_snapshot().object_refs if ref.object_id in observed_ids)
        selected=SelectedResearchResult(object_id=f"selected:{self.decision.context_hash}",source_run_id=self.coordinator.adapter.run_id,input_refs=(self.context.ref(),*observation_refs),candidate_ref=self.design_candidate_refs[receipt.selected_design_id],inference_ref=self.inference_ref,prediction_refs=self.prediction_refs,claim_strength="design",candidate_roles={"terminal_choice":receipt.selected_design_id,"decision_context":self.context.object_id})
        repo=self.coordinator.science_repository;latest=repo.latest_snapshot()
        existing=next((ref for ref in latest.object_refs if ref.object_id==selected.object_id),None)
        if existing:
            if existing!=selected.ref(): raise ValueError("selected result recovery identity mismatch")
        else:
            repo.commit([selected],task_ref=self.coordinator.science_task_ref,evidence_cutoff=latest.evidence_cutoff,expected_sequence=latest.committed_sequence)
        self._expected_snapshot_id=repo.latest_snapshot().snapshot_id
        self.selected_result=selected
        atomic_write_json(Path(work_dir)/"SELECTED_RESEARCH_RESULT.json",selected.model_dump(mode="json"))
        return selected


class OpenResearchPipelinePolicy:
    """Existing Article stage-7 lifecycle delegates the actual research loop.

    A production builder is lazy: Article owns creation of its write-once run
    directory, and no science store/acquisition is opened at factory assembly.
    Legacy requests are explicitly omitted, never relabelled as research effects.
    """
    def __init__(self, *, coordinator, loop=None, loop_builder=None,
                 article_request=None, bootstrap_policy=None):
        if (loop is None)==(loop_builder is None):
            raise ValueError("supply exactly one existing loop or lazy loop builder")
        if loop_builder is not None and (not callable(loop_builder) or bootstrap_policy is not None):
            raise ValueError("lazy research requires a callable builder and no implicit legacy bootstrap")
        self.loop=loop;self.coordinator=coordinator;self.loop_builder=loop_builder
        self.article_request=article_request;self.bootstrap_policy=bootstrap_policy
        self.selected_result=None;self._resources=();self._plan=None;self._work_dir=None
        if loop is not None:self._verify_binding()

    def _verify_binding(self):
        from .open_research_loop import OpenResearchLoop
        from .entry_spec import DiagnosticPipelineLoopAdapter
        if not isinstance(self.loop,(OpenResearchLoop,DiagnosticPipelineLoopAdapter)):
            raise ValueError("actual supported scientific loop required")
        if isinstance(self.loop,DiagnosticPipelineLoopAdapter):
            self.loop.verify_contract(self.article_request)
        if self.coordinator.science_repository is not self.loop.repository:
            raise ValueError("open research must use Article's authoritative science repository")
        if self.coordinator.science_task_ref != self.loop.task.ref():
            raise ValueError("open research task must match Article's frozen scientific task")
        if self.coordinator.execution_gate not in self.loop.runner.gates.values():
            raise ValueError("open research must share Article's durable execution gate")

    def _ensure_loop(self, work_dir):
        root=Path(work_dir).resolve()
        if self._work_dir is not None and root!=self._work_dir:raise ValueError("research pipeline work directory changed")
        self._work_dir=root
        if self.loop is None:
            from .open_research_loop import OpenResearchLoop
            from .entry_spec import DiagnosticPipelineLoopAdapter
            loop,resources=self.loop_builder(root)
            try:
                if not isinstance(loop,(OpenResearchLoop,DiagnosticPipelineLoopAdapter)):
                    raise ValueError("builder must return actual supported scientific loop")
                if isinstance(loop,DiagnosticPipelineLoopAdapter):
                    loop.verify_contract(self.article_request)
                if loop.repository.root.resolve()!=Path(self.coordinator.science_root).resolve():
                    raise ValueError("research repository must be the factory's canonical science root")
                if loop.task.source_run_id!=self.coordinator.adapter.run_id:
                    raise ValueError("research source run must match the Article run")
                if self.coordinator.science_repository is not None or self.coordinator.execution_gate is not None:
                    raise ValueError("cannot replace an already bound Article scientific runtime")
                if not loop.runner.gates:raise ValueError("research needs a durable acquisition gate")
                self.loop=loop;self._resources=tuple(resources)
                self.coordinator.science_repository=loop.repository
                self.coordinator.science_task_ref=loop.task.ref()
                self.coordinator.execution_gate=next(iter(loop.runner.gates.values()))
            except BaseException:
                for resource in resources:resource.close()
                raise
        self._verify_binding()
        return self.loop

    @staticmethod
    def _freeze(path,payload):
        if path.exists():
            if canonical_hash(json.loads(path.read_text()))!=canonical_hash(payload):
                raise ValueError("research pipeline persisted binding changed")
        else:atomic_write_json(path,payload)

    def plan(self, requests, work_dir):
        loop=self._ensure_loop(work_dir)
        if self.bootstrap_policy is not None:return self.bootstrap_policy.plan(requests,work_dir)
        payload={"mode":"open_research","protocol_ref":loop._find(f"{loop.config.run_id}:protocol").ref().model_dump(mode="json"),
            "article_request_hash":canonical_hash(self.article_request.model_dump(mode="json")) if self.article_request is not None else None,
            "legacy_request_hashes":{r.request_id:canonical_hash(r.model_dump(mode="json")) for r in requests},
            "policy_selected_request_ids":[],"policy_omitted_request_ids":[r.request_id for r in requests],
            "repository_root":str(loop.repository.root.resolve()),
            "execution_boundary":"existing OpenResearchLoop/C12 ProgramRunner/shared C03 ExecutionGate"}
        self._freeze(Path(work_dir)/"SCIENCE_DECISION.json",payload)
        self._plan=payload
        return ()

    def execute(self, request):
        if self.bootstrap_policy is None:raise ValueError("open research executes precommitted typed programs, not legacy optimizer requests")
        return self.bootstrap_policy.execute(request)

    def finalize(self, executions, work_dir):
        loop=self._ensure_loop(work_dir)
        if self._plan is None and self.bootstrap_policy is None:raise ValueError("research pipeline plan must precede effects")
        if self.bootstrap_policy is not None:self.bootstrap_policy.finalize(executions,work_dir)
        elif executions:raise ValueError("legacy executions cannot be silently mixed into research selection")
        status_path=Path(work_dir)/"SCIENCE_OPEN_PIPELINE_STATUS.json"
        atomic_write_json(status_path,{"state":"running","recovery":"existing gates query durable receipts; unknown effects are never blindly resubmitted"})
        try:
            self.selected_result=loop.run(work_dir)
            from .authoring_source import prepare_scientific_authoring_source
            selection=prepare_scientific_authoring_source(loop.repository.root,self.selected_result.ref(),Path(work_dir)/"scientific_authoring")
            handoff={"schema_version":"open-research-pipeline-handoff/1","selected_result_ref":self.selected_result.ref().model_dump(mode="json"),
                "repository_root":str(loop.repository.root.resolve()),"selection":selection.model_dump(mode="json"),
                "source_dir":str((Path(work_dir)/"scientific_authoring").resolve()),
                "source_kind":"scientific_result","legacy_optimizer_certificates":False,
                "protocol_ref":loop._find(f"{loop.config.run_id}:protocol").ref().model_dump(mode="json")}
            context=loop._context("pipeline-handoff",handoff,(self.selected_result.ref(),))
            handoff={**handoff,"canonical_handoff_ref":context.ref().model_dump(mode="json")}
            self._freeze(Path(work_dir)/"SCIENCE_OPEN_PIPELINE_HANDOFF.json",handoff)
            atomic_write_json(status_path,{"state":"completed","handoff_hash":canonical_hash(handoff),
                "compute":loop.compute_ledger.snapshot(),"measurement_usage":{k:g.usage() for k,g in loop.runner.gates.items()}})
            return self.selected_result
        except BaseException as exc:
            atomic_write_json(status_path,{"state":"interrupted_or_failed","error_type":type(exc).__name__,
                "compute":loop.compute_ledger.snapshot(),"measurement_usage":{k:g.usage() for k,g in loop.runner.gates.items()},
                "recovery":"query existing durable gates; no automatic repurchase of unknown effects"})
            raise

    def authoring_source(self, work_dir):
        from .authoring_selection import AuthoringSelection
        from .authoring_source import load_scientific_authoring_source
        root=Path(work_dir).resolve();path=root/"SCIENCE_OPEN_PIPELINE_HANDOFF.json"
        handoff=json.loads(path.read_text())
        status=json.loads((root/"SCIENCE_OPEN_PIPELINE_STATUS.json").read_text())
        if status.get("state")!="completed" or status.get("handoff_hash")!=canonical_hash(handoff):
            raise ValueError("research handoff is incomplete or changed")
        from .contracts import ObjectRef
        from .repository import ScienceRepository
        repository=ScienceRepository(handoff["repository_root"],read_only=True)
        try:
            ref=ObjectRef.model_validate(handoff["canonical_handoff_ref"])
            canonical=repository.load(ref)
            if canonical.summary!={k:v for k,v in handoff.items() if k!="canonical_handoff_ref"}:
                raise ValueError("research handoff differs from canonical committed selection")
        finally:repository.close()
        selection=AuthoringSelection.model_validate(handoff["selection"])
        if selection.selected_result_ref.model_dump(mode="json")!=handoff["selected_result_ref"] or selection.repository_root!=handoff["repository_root"]:
            raise ValueError("research selected identity differs from authoring handoff")
        if Path(handoff["source_dir"]).resolve()!=root/"scientific_authoring":raise ValueError("authoring handoff directory changed")
        return load_scientific_authoring_source(handoff["source_dir"],selection)

    def close(self):
        for resource in self._resources:resource.close()
        self._resources=()
