"""Canonical, versioned scientific objects. Legacy cards are projections only."""
from __future__ import annotations
import hashlib
import json
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


def canonical_hash(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ObjectRef(StrictModel):
    object_id: str = Field(min_length=1)
    revision: int = Field(default=1, ge=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ScientificObject(StrictModel):
    object_id: str = Field(min_length=1)
    revision: int = Field(default=1, ge=1)
    content_hash: str = ""
    source_run_id: str = Field(min_length=1)
    input_refs: tuple[ObjectRef, ...] = ()
    unconsumed_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def validate_consumed_nested_fields(cls, value):
        if isinstance(value, dict):
            from .nested_contracts import validate_nested_payload
            schema = value.get("schema_version", cls.model_fields.get("schema_version").default if "schema_version" in cls.model_fields else None)
            validate_nested_payload(schema, value)
        return value

    @model_validator(mode="after")
    def verify_hash(self):
        payload = self.model_dump(mode="json", exclude={"content_hash"})
        digest = canonical_hash(payload)
        if self.content_hash and self.content_hash != digest:
            raise ValueError("scientific object content_hash mismatch")
        object.__setattr__(self, "content_hash", digest)
        return self

    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        schema = super().model_json_schema(*args, **kwargs)
        from .nested_contracts import consumed_json_schemas
        version = cls.model_fields.get("schema_version")
        if version is not None:
            schema['properties'].update(consumed_json_schemas(version.default))
        return schema

    def ref(self) -> ObjectRef:
        return ObjectRef(object_id=self.object_id, revision=self.revision, content_hash=self.content_hash)


class ResearchTask(ScientificObject):
    schema_version: Literal["research-task.v1"] = "research-task.v1"
    question: str
    objective: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)


class ModelCandidate(ScientificObject):
    schema_version: Literal["model-candidate.v1"] = "model-candidate.v1"
    model_ir: dict[str, Any]
    parameter_values: dict[str, Any] = Field(default_factory=dict)
    domain: dict[str, Any] = Field(default_factory=dict)


class ExperimentProgram(ScientificObject):
    schema_version: Literal["experiment-program.v1"] = "experiment-program.v1"
    candidate_ref: ObjectRef | None = None
    objective_ref: ObjectRef | None = None
    protocol: dict[str, Any]


class Observation(ScientificObject):
    schema_version: Literal["observation.v2"] = "observation.v2"
    acquisition_id: str
    candidate_ref: ObjectRef | None = None
    objective_ref: ObjectRef | None = None
    sample_ref: ObjectRef | None = None
    binding: dict[str, Any] = Field(default_factory=dict)
    raw_blocks: tuple[dict[str, Any], ...] = ()
    derived_features: dict[str, Any] = Field(default_factory=dict)
    quality: dict[str, Any] = Field(default_factory=dict)
    cost_refs: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    not_recorded_fields: tuple[str, ...] = ()

    @model_validator(mode="after")
    def distinct_raw_blocks(self):
        ids = [block.get("block_id") for block in self.raw_blocks]
        if None in ids or len(set(ids)) != len(ids):
            raise ValueError("raw blocks require unique block_id values")
        return self


class InferenceState(ScientificObject):
    schema_version: Literal["inference-state.v1"] = "inference-state.v1"
    candidate_refs: tuple[ObjectRef, ...] = ()
    observation_refs: tuple[ObjectRef, ...] = ()
    representation: Literal["conditional_bayes", "predictive_ensemble", "scenario_set", "ambiguity_set", "not_recorded"] = "not_recorded"
    weights_semantics: str = "not_recorded"
    calibration_scope: dict[str, Any] = Field(default_factory=dict)
    noise_model: dict[str, Any] = Field(default_factory=dict)
    evidence_uses: tuple[ObjectRef, ...] = ()
    posterior: dict[str, Any] = Field(default_factory=dict)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    belief_scope: Literal["model_pool_conditional", "scenario", "ambiguity_set", "not_recorded"] = "not_recorded"


class PredictionSpec(ScientificObject):
    schema_version: Literal["prediction-spec.v1"] = "prediction-spec.v1"
    candidate_ref: ObjectRef | None = None
    snapshot_id: str = "not_recorded"
    program_ref: ObjectRef | None = None
    raw_channel: str = "not_recorded"
    units: str = "not_recorded"
    statistic: str = "not_recorded"
    tolerance_source: str = "not_recorded"
    lower: tuple[float, ...] = ()
    upper: tuple[float, ...] = ()
    coordinate: tuple[float, ...] = ()
    invalid_conditions: tuple[str, ...] = ()
    observable: str
    grid: dict[str, Any] = Field(default_factory=dict)
    distribution: dict[str, Any] = Field(default_factory=dict)


class PredictionEvaluation(ScientificObject):
    schema_version: Literal["prediction-evaluation.v1"] = "prediction-evaluation.v1"
    prediction_ref: ObjectRef
    observation_ref: ObjectRef
    metrics: dict[str, Any]


class EvidenceUse(ScientificObject):
    schema_version: Literal["evidence-use.v1"] = "evidence-use.v1"
    observation_ref: ObjectRef
    purpose: str
    raw_block_ids: tuple[str, ...] = ()
    derived_view: str | None = None
    likelihood_count: int = Field(default=0, ge=0, le=1)

    @model_validator(mode="after")
    def derived_not_independent(self):
        if self.derived_view is not None and self.likelihood_count:
            raise ValueError("derived views are not independent acquisitions")
        return self


class DecisionContext(ScientificObject):
    schema_version: Literal["decision-context.v1"] = "decision-context.v1"
    observation_refs: tuple[ObjectRef, ...] = ()
    summary: dict[str, Any] = Field(default_factory=dict)
    feature_view: dict[str, Any] = Field(default_factory=dict)
    raw_view: dict[str, Any] = Field(default_factory=dict)
    actual_messages: tuple[dict[str, Any], ...] = ()
    context_hash: str = ""

    @model_validator(mode="after")
    def check_context(self):
        expected = canonical_hash(self.actual_messages)
        if self.context_hash and self.context_hash != expected:
            raise ValueError("message context hash mismatch")
        # Context hash is derived before the outer content hash on reconstruction.
        if not self.context_hash:
            object.__setattr__(self, "context_hash", expected)
            object.__setattr__(self, "content_hash", canonical_hash(self.model_dump(mode="json", exclude={"content_hash"})))
        return self


class ScientificClaim(ScientificObject):
    schema_version: Literal["scientific-claim.v1"] = "scientific-claim.v1"
    statement: str
    evidence_refs: tuple[ObjectRef, ...] = ()
    scope: dict[str, Any] = Field(default_factory=dict)
    status: Literal["proposed", "supported", "refuted", "unknown"] = "unknown"


class SelectedResearchResult(ScientificObject):
    schema_version: Literal["selected-research-result.v1"] = "selected-research-result.v1"
    candidate_ref: ObjectRef
    inference_ref: ObjectRef | None = None
    claim_refs: tuple[ObjectRef, ...] = ()
    candidate_roles: dict[str, Any] = Field(default_factory=dict)
    prediction_refs: tuple[ObjectRef, ...] = ()
    negative_result_refs: tuple[ObjectRef, ...] = ()
    equivalent_explanations: tuple[str, ...] = ()
    cost_refs: tuple[ObjectRef, ...] = ()
    asset_refs: tuple[ObjectRef, ...] = ()
    claim_strength: Literal["design", "prediction", "mechanism"] = "design"


class ScienceSnapshot(StrictModel):
    schema_version: Literal["science-snapshot.v1", "science-snapshot.v2"] = "science-snapshot.v2"
    snapshot_id: str
    committed_sequence: int = Field(ge=1)
    task_ref: ObjectRef
    object_refs: tuple[ObjectRef, ...]
    evidence_cutoff: str

    @model_validator(mode="after")
    def verify_snapshot(self):
        ids = [ref.object_id for ref in self.object_refs]
        if len(ids) != len(set(ids)) or self.task_ref not in self.object_refs:
            raise ValueError("snapshot refs must be unique and contain task_ref")
        identity = {"sequence":self.committed_sequence,"refs":[ref.model_dump() for ref in self.object_refs],"cutoff":self.evidence_cutoff}
        if self.schema_version == "science-snapshot.v2":
            identity.update(schema_version=self.schema_version, task_ref=self.task_ref.model_dump())
        expected = canonical_hash(identity)
        if self.snapshot_id != expected: raise ValueError("snapshot identity mismatch")
        return self

    @property
    def task_identity_bound(self) -> bool:
        """Historical v1 hashes did not bind task_ref; never claim that assurance."""
        return self.schema_version == "science-snapshot.v2"


OBJECT_TYPES = {cls.model_fields["schema_version"].default: cls for cls in (ResearchTask, ModelCandidate, ExperimentProgram, Observation, InferenceState, PredictionSpec, PredictionEvaluation, EvidenceUse, DecisionContext, ScientificClaim, SelectedResearchResult)}


def parse_object(payload: dict) -> ScientificObject:
    if payload.get("schema_version") in {"method-asset.v1", "program-template-asset.v1", "asset-calibration-receipt.v1"}:
        from . import method_assets
    if payload.get("schema_version") == "transfer-evaluation.v1":
        from . import promotion
    try:
        cls = OBJECT_TYPES[payload.get("schema_version")]
    except KeyError as exc:
        raise ValueError("unsupported scientific schema version") from exc
    return cls.model_validate(payload)
