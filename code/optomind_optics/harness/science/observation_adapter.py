"""Identity-bound evidence views shared by execution and reflection."""
from typing import Any, Mapping
from .contracts import Observation, ObjectRef, DecisionContext, canonical_hash

BINDING_FIELDS = ("candidate", "sample", "objective", "objective_version", "grid", "polarization", "materials", "tool_version")


def evidence_binding(**identities):
    missing = set(BINDING_FIELDS) - identities.keys()
    if missing: raise ValueError(f"missing evidence identities: {sorted(missing)}")
    return {key: canonical_hash(identities[key]) for key in BINDING_FIELDS}


def require_binding(observation: Observation, expected: Mapping[str, str]):
    if set(expected) != set(BINDING_FIELDS) or any(observation.binding.get(key) != expected[key] for key in BINDING_FIELDS):
        raise ValueError("evidence identity mismatch or incomplete provenance")
    return observation


def decision_context(observation: Observation, messages=()):
    # The raw view contains artifact handles, not fabricated or compressed arrays.
    return DecisionContext(object_id=f"context:{observation.object_id}", source_run_id=observation.source_run_id, input_refs=(observation.ref(),), observation_refs=(observation.ref(),), summary={"quality":observation.quality,"acquisition_id":observation.acquisition_id}, feature_view=observation.derived_features, raw_view={"blocks":list(observation.raw_blocks),"artifact_refs":list(observation.artifact_refs)}, actual_messages=tuple(messages))


def reflection_evidence(observation: Mapping[str, Any]):
    return {
        key: observation[key]
        for key in (
            "binding",
            "raw_blocks",
            "derived_features",
            "quality",
            "artifact_refs",
            "gradient_summary",
            "energy_accounting_summary",
            "verification",
            "verification_partial",
            "evidence_coverage",
            "verify_run_error",
            "candidate_summaries",
            "compilation_status",
            "compilation_rationale",
            "compilation_errors",
            "failure_categories",
            "run_status",
        )
        if key in observation
    }


def bound_gradient(simulation: dict, *, candidate_ref: ObjectRef, objective_ref: ObjectRef, objective, binding: dict, provenance=None):
    """Differentiate only a verified adopted candidate and explicitly frozen loss."""
    from .gradient_binding import compute_bound_gradient
    return compute_bound_gradient(simulation, candidate_ref=candidate_ref, objective_ref=objective_ref,
                                  objective=objective, binding=binding, provenance=provenance)
