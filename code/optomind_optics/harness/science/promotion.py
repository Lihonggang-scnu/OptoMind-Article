from typing import Literal
from pydantic import Field
from .contracts import ScientificObject, ObjectRef, OBJECT_TYPES, StrictModel

class TransferEvaluation(ScientificObject):
    schema_version: Literal["transfer-evaluation.v1"] = "transfer-evaluation.v1"
    asset_ref: ObjectRef
    task_id: str
    original_task_id: str
    split: Literal["T", "V", "P", "A"]
    forward_run_id: str = Field(min_length=1)
    passed: bool
    cost: float = Field(ge=0, allow_inf_nan=False)
    negative_transfer: bool = False
    scope: dict

class PromotionDecision(StrictModel):
    asset_ref: ObjectRef
    state: str
    evidence_ref: ObjectRef
    reason: str

def promote(registry, asset, evaluation, *, task_ref, evidence_cutoff):
    asset = type(asset).model_validate(asset.model_dump(mode="json"))
    evaluation = type(evaluation).model_validate(evaluation.model_dump(mode="json"))
    registry.repository.load(asset.ref())
    if evaluation.asset_ref != asset.ref(): raise ValueError("evaluation binds another asset version")
    registry.repository.load(evaluation.ref())
    if any(key not in evaluation.scope or evaluation.scope[key]!=value for key,value in asset.applicability_predicates.items()):
        raise ValueError("transfer evidence does not establish applicability scope")
    novel=evaluation.task_id != evaluation.original_task_id and evaluation.task_id not in asset.validation_scope
    passed=evaluation.passed and not evaluation.negative_transfer
    state="promoted" if passed and novel and evaluation.split=="P" else "task_validated" if passed else "quarantined"
    data=asset.model_dump(); data.update(revision=asset.revision+1,content_hash="",state=state)
    key="positive_evidence" if passed else "negative_evidence"
    data[key]=(*getattr(asset,key),evaluation.ref())
    data["validation_scope"]=tuple(dict.fromkeys((*asset.validation_scope,evaluation.original_task_id,evaluation.task_id)))
    updated=type(asset).model_validate(data)
    registry.put(updated,task_ref=task_ref,evidence_cutoff=evidence_cutoff)
    return updated,PromotionDecision(asset_ref=updated.ref(),state=state,evidence_ref=evaluation.ref(),reason="new_task_forward_validation" if state=="promoted" else "limited_or_negative_transfer")

OBJECT_TYPES["transfer-evaluation.v1"]=TransferEvaluation
