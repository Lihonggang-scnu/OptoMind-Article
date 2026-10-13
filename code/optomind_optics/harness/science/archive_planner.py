"""Non-destructive method retirement and rebuild hooks over MethodRegistry.

Old revisions and their evidence remain loadable. This module never deletes
canonical scientific objects, even when a retirement recommendation is accepted.
"""
from .contracts import StrictModel, ObjectRef, canonical_hash
from .method_assets import MethodAsset


class ArchiveDecision(StrictModel):
    asset_ref: ObjectRef
    action: str
    reason: str


class ArchivePlan(StrictModel):
    snapshot_id: str
    decisions: tuple[ArchiveDecision, ...]
    retained_refs: tuple[ObjectRef, ...]


def plan_archive(registry, *, negative_transfer_ids=(), retire_ids=()):
    index = registry.rebuild_index()
    selected = set(negative_transfer_ids) | set(retire_ids)
    if selected - index.keys():
        raise ValueError("archive plan names unknown method assets")
    decisions = tuple(ArchiveDecision(asset_ref=index[key].ref(), action="retired" if key in retire_ids else "quarantined", reason="explicit_retirement" if key in retire_ids else "negative_transfer") for key in sorted(selected))
    return ArchivePlan(snapshot_id=registry.snapshot().snapshot_id, decisions=decisions, retained_refs=tuple(a.ref() for key,a in sorted(index.items()) if key not in selected))


def apply_archive(registry, plan, *, task_ref, evidence_cutoff, evidence_refs):
    """Require committed evidence and CAS; preserve all historical revisions."""
    plan = ArchivePlan.model_validate_json(plan.model_dump_json())
    snapshot = registry.repository.latest_snapshot()
    if snapshot is None or snapshot.snapshot_id != plan.snapshot_id:
        raise ValueError("archive snapshot changed; replan")
    if not evidence_refs:
        raise ValueError("archive lifecycle change requires committed evidence")
    for ref in evidence_refs:
        registry.repository.load(ref)
    updated = []
    for decision in plan.decisions:
        if decision.action not in {"retired", "quarantined"}:
            raise ValueError("unsupported archive lifecycle change")
        asset = registry.repository.load(decision.asset_ref)
        if not isinstance(asset, MethodAsset):
            raise ValueError("archive decisions must bind method assets")
        from .promotion import TransferEvaluation
        evaluations=[registry.repository.load(ref) for ref in evidence_refs]
        negative=tuple(e.ref() for e in evaluations if isinstance(e,TransferEvaluation) and e.asset_ref==asset.ref() and (e.negative_transfer or not e.passed))
        if decision.action == "quarantined" and not negative:
            raise ValueError("quarantine requires a negative transfer evaluation for this asset version")
        data = asset.model_dump()
        metadata=dict(asset.unconsumed_metadata)
        metadata["archive_decision"]={"action":decision.action,"reason":decision.reason}
        data.update(revision=asset.revision+1, content_hash="", state=decision.action,
                    input_refs=tuple(dict.fromkeys((*asset.input_refs,*evidence_refs))),unconsumed_metadata=metadata,
                    negative_evidence=tuple(dict.fromkeys((*asset.negative_evidence,*negative))))
        updated.append(MethodAsset.model_validate(data))
    if not updated:
        return snapshot
    return registry.repository.commit(updated, task_ref=task_ref, evidence_cutoff=evidence_cutoff, expected_sequence=snapshot.committed_sequence)


def rebuild_and_verify(registry, *, historical_snapshot_ids=()):
    """Fail closed on corrupt objects, dangling refs or incompatible schemas."""
    index = registry.rebuild_index()
    historical = {}
    for snapshot_id in historical_snapshot_ids:
        snapshot = registry.repository.snapshot(snapshot_id)
        historical[snapshot_id] = len([registry.repository.load(ref) for ref in snapshot.object_refs])
    return {"snapshot_id": registry.snapshot().snapshot_id, "index_hash": canonical_hash({key:asset.content_hash for key,asset in sorted(index.items())}), "active_ids": sorted(key for key,asset in index.items() if asset.state in {"promoted", "task_validated"}), "inactive_ids": sorted(key for key,asset in index.items() if asset.state not in {"promoted", "task_validated"}), "historical_objects_verified": historical}


def narrow_after_counterexample(registry, asset_ref, predicates, *, evaluation_ref, task_ref, evidence_cutoff):
    """Add conditions, never silently widen a method after negative transfer."""
    from .promotion import TransferEvaluation
    asset=registry.repository.load(asset_ref)
    evaluation=registry.repository.load(evaluation_ref)
    if not isinstance(asset,MethodAsset) or not isinstance(evaluation,TransferEvaluation):
        raise ValueError('typed method and transfer evaluation required')
    if evaluation.asset_ref != asset_ref or not (evaluation.negative_transfer or not evaluation.passed):
        raise ValueError('counterexample must bind this exact method version')
    if any(key not in predicates or predicates[key] != value for key,value in asset.applicability_predicates.items()):
        raise ValueError('counterexample narrowing cannot widen or change prior premises')
    if predicates == asset.applicability_predicates:
        raise ValueError('narrowing must add at least one applicability premise')
    snapshot=registry.repository.latest_snapshot()
    if asset_ref not in snapshot.object_refs:
        raise ValueError('cannot narrow a stale asset version')
    data=asset.model_dump()
    # A changed scope needs fresh validation; old positive evidence is historical
    # context, not permission to execute the newly narrowed hypothesis.
    data.update(revision=asset.revision+1,content_hash='',state='proposed',
                applicability_predicates=dict(predicates),negative_evidence=(*asset.negative_evidence,evaluation_ref))
    narrowed=MethodAsset.model_validate(data)
    registry.put(narrowed,task_ref=task_ref,evidence_cutoff=evidence_cutoff,expected_sequence=snapshot.committed_sequence)
    return narrowed
