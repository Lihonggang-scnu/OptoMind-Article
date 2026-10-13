"""Versioned executable assets; canonical repository is the only writer.

Legacy article memory remains a textual projection, never execution authority.
"""
from typing import Any, Literal
from pydantic import Field, model_validator
from .contracts import ScientificObject, StrictModel, ObjectRef, OBJECT_TYPES, canonical_hash

class MethodAsset(ScientificObject):
    schema_version: Literal["method-asset.v1"] = "method-asset.v1"
    kind: Literal["model_fragment", "program", "policy"]
    payload: dict[str, Any]
    dependencies: tuple[ObjectRef, ...] = ()
    applicability_predicates: dict[str, Any] = Field(default_factory=dict)
    recalibration: Literal["none", "refit", "recalibrate"] = "none"
    positive_evidence: tuple[ObjectRef, ...] = ()
    negative_evidence: tuple[ObjectRef, ...] = ()
    cost: float = Field(default=0, ge=0, allow_inf_nan=False)
    validation_scope: tuple[str, ...] = ()
    state: Literal["proposed", "task_validated", "promoted", "quarantined", "retired"] = "proposed"
    transfer_mode: Literal["structure", "initialization", "hierarchical_prior", "surrogate_weights"] = "structure"
    fallback: ObjectRef | None = None

    @model_validator(mode="after")
    def executable(self):
        compile_payload(self.kind, self.payload)
        if self.state == "promoted" and len(set(self.validation_scope)) < 2:
            raise ValueError("promotion requires new-task validation")
        return self

class ProgramTemplateAsset(MethodAsset):
    """Versioned additive schema; original method-asset.v1 hashes stay unchanged."""
    schema_version: Literal["program-template-asset.v1"] = "program-template-asset.v1"
    kind: Literal["program"] = "program"
    template_source_ref: ObjectRef
    template_sample_slots: tuple[tuple[str, str], ...]

    @model_validator(mode="after")
    def template_provenance(self):
        if (not self.template_sample_slots or self.template_source_ref not in self.input_refs
                or len(dict(self.template_sample_slots))!=len(self.template_sample_slots)):
            raise ValueError("program template requires its committed source and exact distinct slot provenance")
        return self

class AssetCalibrationReceipt(ScientificObject):
    """Committed task-specific evidence gate, not a caller's claimed hash."""
    schema_version: Literal["asset-calibration-receipt.v1"] = "asset-calibration-receipt.v1"
    asset_ref: ObjectRef
    target_task_ref: ObjectRef
    conditions_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    calibration_kind: Literal["refit", "recalibrate"]
    status: Literal["passed", "failed"]
    evidence_refs: tuple[ObjectRef, ...] = Field(min_length=1)


def _verify_execution_evidence(asset, facts, *, repository=None, calibration_ref=None, target_task_ref=None):
    if asset.transfer_mode not in ("structure", "initialization"):
        raise ValueError("unsupported executable transfer mode: " + asset.transfer_mode)
    if isinstance(asset, ProgramTemplateAsset):
        if repository is None:
            raise ValueError("normalized program template needs its canonical source repository")
        from .contracts import ExperimentProgram
        from .trace_extractor import normalize_program_template
        source=repository.load(asset.template_source_ref)
        if not isinstance(source,ExperimentProgram):
            raise ValueError("program template source must be an ExperimentProgram")
        normalized=normalize_program_template(source.protocol,dict(asset.template_sample_slots))
        from dataclasses import asdict
        if canonical_hash(asdict(normalized))!=canonical_hash(asset.payload):
            raise ValueError("program template changed non-identity source semantics")
    if asset.dependencies and repository is None:
        raise ValueError("asset dependencies require an authoritative repository")
    for ref in asset.dependencies:
        dependency = repository.load(ref)
        if isinstance(dependency, MethodAsset):
            from .applicability import check_applicability
            snapshot=repository.latest_snapshot()
            current={r.object_id:r for r in snapshot.object_refs} if snapshot else {}
            if current.get(ref.object_id)!=ref or not check_applicability(dependency, facts).allowed:
                raise ValueError("asset dependency is inactive, superseded or inapplicable")
    if asset.recalibration == "none":
        return
    if repository is None or calibration_ref is None or target_task_ref is None:
        raise ValueError("committed calibration/refit receipt and target task are required")
    from .contracts import ResearchTask, InferenceState, PredictionEvaluation, Observation
    if not isinstance(target_task_ref, ObjectRef):
        target_task_ref = ObjectRef.model_validate(target_task_ref)
    if not isinstance(calibration_ref, ObjectRef):
        calibration_ref = ObjectRef.model_validate(calibration_ref)
    task = repository.load(target_task_ref)
    receipt = repository.load(calibration_ref)
    if not isinstance(task, ResearchTask) or not isinstance(receipt, AssetCalibrationReceipt):
        raise ValueError("typed task and asset calibration receipt required")
    if (receipt.asset_ref != asset.ref() or receipt.target_task_ref != target_task_ref
        or receipt.conditions_hash != canonical_hash(facts)
        or receipt.calibration_kind != asset.recalibration or receipt.status != "passed"):
        raise ValueError("calibration receipt does not bind this asset version, task, conditions and passed status")
    repository.load(asset.ref())
    for evidence_ref in receipt.evidence_refs:
        if not isinstance(repository.load(evidence_ref), (InferenceState, PredictionEvaluation, Observation)):
            raise ValueError("calibration receipt requires committed inference, prediction or observation evidence")


class MethodLibrarySnapshot(StrictModel):
    snapshot_id: str
    asset_refs: tuple[ObjectRef, ...]

class PolicyPayload(StrictModel):
    depth: int = Field(default=1, ge=1, le=2)
    action_ids: tuple[str, ...] = ()
    continuation_mode: Literal["fixed_bundle", "adaptive"] = "fixed_bundle"


def compile_payload(kind, payload):
    if kind == "model_fragment":
        from .model_ir import model_ir_from_dict
        from .model_compiler import compile_model
        return compile_model(model_ir_from_dict(payload))
    if kind == "policy": return PolicyPayload.model_validate(payload)
    if kind == "program":
        from .program_ir import ProgramIR, ProgramNode
        if set(payload) - {"program_id", "nodes", "initial_samples", "revision"}: raise ValueError("unknown program field")
        def freeze(value):
            return tuple(freeze(v) for v in value) if isinstance(value,(list,tuple)) else value
        nodes=[]
        for raw in payload["nodes"]:
            node=dict(raw)
            for key in ("inputs","arguments","condition","estimate"):
                if key in node: node[key]=freeze(node[key])
            nodes.append(ProgramNode(**node))
        return ProgramIR(program_id=payload["program_id"], nodes=tuple(nodes), initial_samples=tuple(tuple(x) for x in payload.get("initial_samples", ())), revision=payload.get("revision",1))
    raise ValueError("unknown executable kind")


def consume_asset(asset, *, facts, **context):
    # Frozen outer objects still contain mutable payload dictionaries. Recheck
    # content identity before an executable asset crosses the consumer boundary.
    asset = type(asset).model_validate(asset.model_dump(mode="json"))
    from .applicability import check_applicability
    check = check_applicability(asset, facts)
    if not check.allowed: raise ValueError("asset inapplicable: " + ",".join(check.reasons))
    _verify_execution_evidence(asset, facts, repository=context.get("repository"),
                               calibration_ref=context.get("calibration_ref"), target_task_ref=context.get("target_task_ref"))
    if asset.transfer_mode not in ("structure", "initialization"):
        raise ValueError("unsupported executable transfer mode: " + asset.transfer_mode)
    compiled = compile_payload(asset.kind, asset.payload)
    if asset.kind == "model_fragment":
        unknown = set(context) - {"parameters", "wavelengths_nm", "channel", "angle_deg", "calibrated_asset_hash", "repository", "calibration_ref", "target_task_ref"}
        if unknown:
            raise ValueError("unsupported model acquisition context: " + ",".join(sorted(unknown)))
        return compiled.predict(context.get("parameters", {}), context["wavelengths_nm"],
                                channel=context.get("channel", "R"), angle_deg=context.get("angle_deg", 0.))
    if asset.kind == "program":
        from .program_compiler import compile_program
        plan = compile_program(compiled, capabilities=context["capabilities"], limits=context["limits"])
        return context["runner"].run(plan, prediction_commits=context["prediction_commits"])
    from .controller import decide_next
    actions = context["actions"]
    if compiled.action_ids:
        if set(compiled.action_ids) - {a.action_id for a in actions}: raise ValueError("unknown policy action")
        actions = [a for a in actions if a.action_id in compiled.action_ids]
    if compiled.continuation_mode == "adaptive":
        from .rollout import plan_adaptive
        return plan_adaptive(context["problem"], context["probabilities"], actions, available_budget=context["available_budget"], final_validation_reserve=context["final_validation_reserve"], depth=compiled.depth)
    return decide_next(context["problem"], context["probabilities"], actions, available_budget=context["available_budget"], final_validation_reserve=context["final_validation_reserve"], depth=compiled.depth)


class MethodRegistry:
    def __init__(self, repository): self.repository = repository
    def put(self, asset, *, task_ref, evidence_cutoff, expected_sequence=None):
        return self.repository.commit([asset], task_ref=task_ref, evidence_cutoff=evidence_cutoff, expected_sequence=expected_sequence)
    def snapshot(self):
        snapshot = self.repository.latest_snapshot()
        if snapshot is None: return MethodLibrarySnapshot(snapshot_id=canonical_hash([]), asset_refs=())
        refs = tuple(ref for ref in snapshot.object_refs if isinstance(self.repository.load(ref), MethodAsset))
        return MethodLibrarySnapshot(snapshot_id=snapshot.snapshot_id, asset_refs=refs)
    def rebuild_index(self):
        return {r.object_id: self.repository.load(r) for r in self.snapshot().asset_refs}

OBJECT_TYPES["method-asset.v1"] = MethodAsset
OBJECT_TYPES["program-template-asset.v1"] = ProgramTemplateAsset
OBJECT_TYPES["asset-calibration-receipt.v1"] = AssetCalibrationReceipt


def compose_program_assets(assets, *, facts, capabilities, limits, program_id, repository=None, calibration_refs=None, target_task_ref=None):
    """Namespace node IDs, preserve shared samples/references, recompile total cost."""
    from dataclasses import replace
    from .applicability import check_applicability
    from .program_ir import ProgramIR
    from .program_compiler import compile_program
    nodes=[]; samples={}
    for index, asset in enumerate(assets):
        asset = type(asset).model_validate(asset.model_dump(mode="json"))
        if asset.kind != "program" or not check_applicability(asset,facts).allowed: raise ValueError("inapplicable program component")
        _verify_execution_evidence(asset, facts, repository=repository,
                                   calibration_ref=(calibration_refs or {}).get(asset.content_hash), target_task_ref=target_task_ref)
        program=compile_payload(asset.kind,asset.payload)
        for key,value in program.initial_samples:
            if key in samples and samples[key]!=value: raise ValueError("shared sample state conflict")
            samples[key]=value
        prefix=f"component{index}:"
        for node in program.nodes:
            nodes.append(replace(node,node_id=prefix+node.node_id,inputs=tuple(prefix+i for i in node.inputs),condition=(prefix+node.condition[0],node.condition[1]) if node.condition else None))
    return compile_program(ProgramIR(program_id,tuple(nodes),tuple(sorted(samples.items()))),capabilities=capabilities,limits=limits)


def trial_asset(asset, *, facts, evidence_check, trial, budget):
    """Mandatory caller-controlled evidence check and bounded trial before reuse.

Callbacks must return measured costs; this is an orchestration gate, not an OS
resource sandbox. A failed/over-budget trial never authorizes reuse.
"""
    import math
    from .applicability import check_applicability
    asset = type(asset).model_validate(asset.model_dump(mode="json"))
    if not math.isfinite(budget) or budget<0: raise ValueError("invalid trial budget")
    check=check_applicability(asset,facts)
    if not check.allowed: return {"allowed":False,"reason":"premises","cost":0.}
    compile_payload(asset.kind,asset.payload)
    if not evidence_check(asset): return {"allowed":False,"reason":"existing_evidence","cost":0.}
    passed,cost=trial(asset,budget)
    if not math.isfinite(cost) or cost<0: raise ValueError("trial must report finite actual cost")
    return {"allowed":bool(passed and cost<=budget),"reason":"validated" if passed and cost<=budget else "trial_failed_or_over_budget","cost":cost,"asset_hash":asset.content_hash}
