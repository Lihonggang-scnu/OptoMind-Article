"""Explicit, non-destructive legacy projections. Never invent historical beliefs."""
from typing import Any, Literal
from copy import deepcopy
from pydantic import Field
from .contracts import StrictModel, Observation, canonical_hash

class MigrationReport(StrictModel):
    schema_version: Literal["migration-report.v1"] = "migration-report.v1"
    source_hash: str
    mapped_fields: tuple[str, ...]
    not_recorded_fields: tuple[str, ...]
    aliases: dict[str, str] = Field(default_factory=dict)
    created_derived_artifacts: tuple[str, ...] = ()


def migrate_observation_v1(payload: dict[str, Any], *, source_run_id: str):
    version = payload.get("schema_version")
    if version not in (None, "observation-card.v1", "research-iteration-observation.v1"):
        raise ValueError("unsupported legacy observation version")
    missing = tuple(key for key in ("candidate_ref", "objective_ref", "sample_ref", "raw_blocks", "binding") if key not in payload)
    oid = str(payload.get("observation_id") or payload.get("iteration_id") or canonical_hash(payload))
    explicit = {key: deepcopy(payload[key]) for key in ("candidate_ref", "objective_ref", "sample_ref", "raw_blocks", "binding", "cost_refs") if key in payload}
    features = deepcopy(payload.get("derived_features") or {})
    features["legacy_projection"] = deepcopy(payload)
    quality = deepcopy(payload.get("quality") or {"execution":payload.get("status", payload.get("run_status", "not_recorded")), "physics":"not_recorded", "objective":"not_recorded", "explanation":"not_recorded"})
    obs = Observation(object_id=oid, source_run_id=source_run_id, acquisition_id=str(payload.get("acquisition_id") or payload.get("experiment_id") or oid), derived_features=features, quality=quality, artifact_refs=tuple(payload.get("artifact_refs") or payload.get("artifact_ids") or ()), not_recorded_fields=missing, **explicit)
    return obs, MigrationReport(source_hash=canonical_hash(payload), mapped_fields=tuple(sorted(payload)), not_recorded_fields=missing, aliases={"observation_id":"object_id"})


def legacy_observation_projection(repository, ref):
    """Rebuild the old consumer contract only from validated committed authority."""
    observation = repository.load(ref)
    if not isinstance(observation, Observation):
        raise ValueError("legacy projection requires committed Observation")
    payload = observation.derived_features.get("legacy_projection")
    if not isinstance(payload, dict):
        raise ValueError("no recorded legacy observation projection")
    return deepcopy(payload)


def commit_legacy_observation(root, *, source_run_id, question, payload):
    """Single persisted authority for live legacy consumers, without semantic rewriting."""
    from .contracts import ResearchTask
    from .repository import ScienceRepository
    repository = ScienceRepository(root)
    try:
        task = ResearchTask(object_id='legacy-task:'+source_run_id, source_run_id=source_run_id,
                            question=question, constraints={'not_recorded':['canonical_objective']})
        observation, report = migrate_observation_v1(payload, source_run_id=source_run_id)
        latest = repository.latest_snapshot()
        pending=[]
        if latest is None or task.ref() not in latest.object_refs:pending.append(task)
        if latest is None or observation.ref() not in latest.object_refs:pending.append(observation)
        if pending:
            latest=repository.commit(pending,task_ref=task.ref(),evidence_cutoff=observation.object_id,
                                      expected_sequence=latest.committed_sequence if latest else 0)
        return legacy_observation_projection(repository,observation.ref()),observation.ref(),latest,report
    finally:repository.close()


def commit_legacy_context(root, *, observation_ref, source_run_id, payload):
    from .contracts import DecisionContext
    from .repository import ScienceRepository
    repository=ScienceRepository(root)
    try:
        observation=repository.load(observation_ref)
        context=DecisionContext(object_id=observation.object_id+':context:'+canonical_hash(payload)[:20],
            source_run_id=source_run_id,observation_refs=(observation_ref,),
            actual_messages=tuple(payload['actual_messages']), context_hash=payload['context_hash'])
        latest=repository.latest_snapshot()
        if context.ref() not in latest.object_refs:
            repository.commit([context],task_ref=latest.task_ref,evidence_cutoff=latest.evidence_cutoff,
                              expected_sequence=latest.committed_sequence)
        committed=repository.load(context.ref())
        return {'actual_messages':list(committed.actual_messages),'context_hash':committed.context_hash}
    finally:repository.close()
