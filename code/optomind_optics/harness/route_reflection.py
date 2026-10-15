"""Route reflection: LLM-driven post-execution analysis with deterministic cross-check.

This module implements the "Phase B" of R-04: after a route executes, the LLM
reflects on actual observations vs pre-execution declarations, explains
deviations with explicit uncertainty, and recommends continue/stop with grounded insight.

The reflection is written to ROUTE.REFLECTION.json (sidecar) and bound to the
pre-execution attestation via attestation_sha256. A deterministic dual-gate
then cross-checks the LLM recommendation against hard criteria.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from config.qwen_config import get_cost_tracker


DEFAULT_REFLECTION_PROMPT = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "optical_harness"
    / "TMM Route Reflection.txt"
)

# Reflection is an analysis-class lightweight task: route through the flash tier.
# Do NOT use plus — reflection calls are the most frequent in the tournament
# (up to MAX_ROUTES * MAX_ROUNDS_PER_ROUTE), cost-sensitive.
#
# D-2 (R-04-FIX): REFLECTION_MODEL is the *pricing-table* model name (see
# config/model_pricing.json). The argument of CostTracker.record_qwen_usage()
# is the *budget bucket key*, whose existing vocabulary is {"plus", "turbo"}
# (config/qwen_config.py). Reflection meters into the "turbo" bucket — see
# reflect_on_route(). Never pass a model name as that key: record_qwen_usage
# auto-creates unknown buckets via dict.get(key, 0), so a wrong key silently
# escapes every existing budget summary.
REFLECTION_MODEL = "qwen3.7-flash"


class FactualAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    fact_id: str
    value: Any


class NextDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_max_length=2000)
    observation_ids: list[str] = Field(min_length=1, max_length=16)
    unresolved_question: str = Field(min_length=1)
    intervention: str = Field(min_length=1)
    fixed_conditions: list[str] = Field(min_length=1, max_length=16)
    predicted_observable_change: str = Field(min_length=1)
    basis: str = Field(min_length=1)
    alternative: str = Field(min_length=1)
    outcome_changing_decision: str = Field(min_length=1)


class RouteReflection(BaseModel):
    """Structured LLM reflection on a completed route."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_max_length=8000)

    fact_contract_version: Literal[0, 1] = 0
    factual_assertions: list[FactualAssertion] = Field(default_factory=list, max_length=64)
    factual_validation: Literal["unavailable_legacy", "validated_structured"] = "unavailable_legacy"
    factual_limitations: str = "Legacy prose has no machine-checkable factual assertions."
    next_decision: NextDecision | None = None
    observed_vs_expected: str
    deviation_mechanism: str
    continue_recommended: bool = Field(strict=True)
    continue_rationale: str
    insight_for_next: str
    insight_grounding: str
    stop_basis: Literal["", "search_exhausted", "unresolved", "marginal_gains_too_low"] = ""
    reported_stop_basis: str = ""
    stop_basis_diagnostic: str = ""
    # D-3 (R-04-FIX): explicit degradation marker — empty on the normal path,
    # filled by degraded(). Must keep a default so model_validate() still
    # accepts the six-key JSON emitted by the LLM (extra="forbid" rejects
    # unknown keys, but an absent defaulted field is fine). RouteReflection
    # only ever reaches sidecar files (ROUTE.REFLECTION.json), never
    # ArticlePipelineResult, so the hash contract (red line 5) is untouched.
    degraded_reason: str = ""

    @model_validator(mode="before")
    @classmethod
    def normalize_unsupported_infeasibility(cls, value: Any) -> Any:
        if isinstance(value, Mapping) and value.get("stop_basis") == "physically_infeasible":
            return {
                **value,
                "stop_basis": "unresolved",
                "reported_stop_basis": "physically_infeasible",
                "stop_basis_diagnostic": (
                    "Unsupported physical-infeasibility claim: no machine-checkable "
                    "infeasibility certificate is supported by this reflection contract. "
                    "Finite optimizer search and model prose do not prove infeasibility."
                ),
            }
        return value

    @model_validator(mode="after")
    def validate_decision_consistency(self) -> "RouteReflection":
        if self.degraded_reason:
            return self
        if not self.continue_rationale.strip():
            raise ValueError("a recommendation requires its rationale")
        if self.continue_recommended:
            if self.stop_basis:
                raise ValueError("continuation cannot carry a stop basis")
            if not self.insight_for_next.strip() or not self.insight_grounding.strip():
                raise ValueError("continuation requires an executable insight and its grounding")
        elif self.insight_for_next.strip():
            raise ValueError("a stop recommendation cannot also propose a next action")
        return self

    @classmethod
    def degraded(cls, reason: str) -> "RouteReflection":
        """Fallback when LLM output is invalid — never crash the tournament."""
        return cls(
            observed_vs_expected=f"Reflection unavailable: {reason}",
            deviation_mechanism="",
            continue_recommended=False,
            continue_rationale="Reflection degraded; deterministic gates will decide.",
            insight_for_next="",
            insight_grounding="",
            stop_basis="",
            degraded_reason=str(reason or ""),
        )


class ReflectionClient(Protocol):
    def call(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 4000,
        force_mock: bool | None = None,
    ) -> dict[str, Any]: ...


def _safe_json(text: str) -> dict[str, Any]:
    text = str(text or "").strip()
    if len(text) > 65536:
        return {}
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except (ValueError, RecursionError):
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                value = json.loads(text[start : end + 1])
                return value if isinstance(value, dict) else {}
            except (ValueError, RecursionError):
                pass
    return {}


def _bounded_evidence(value: Any, *, max_chars: int = 10000) -> Any:
    remaining = max_chars

    def omitted(raw: Any, path: str, reason: str) -> dict[str, Any]:
        encoded = json.dumps(raw, sort_keys=True, ensure_ascii=False, default=str)
        return {"unavailable_in_projection": reason, "raw_ref": path,
                "source_sha256": hashlib.sha256(encoded.encode()).hexdigest()}

    def visit(raw: Any, path: str, depth: int) -> Any:
        nonlocal remaining
        encoded = json.dumps(raw, ensure_ascii=False, default=str)
        if depth > 10 or remaining < 200:
            return omitted(raw, path, "projection_budget")
        if isinstance(raw, Mapping):
            result = {}
            for index, (key, child) in enumerate(raw.items()):
                if index >= 24 or remaining < 200:
                    result["omitted_remainder"] = omitted(raw, path, "mapping_limit")
                    break
                remaining -= len(str(key)) + 8
                child_path = path + "/" + str(key).replace("~", "~0").replace("/", "~1")
                if key in {"spectrum", "spectra", "wavelengths_nm", "values_nm", "raw_blocks"}:
                    result[key] = omitted(child, child_path, "raw_array")
                    remaining -= 180
                else:
                    result[key] = visit(child, child_path, depth + 1)
            return result
        if isinstance(raw, (list, tuple)):
            result = []
            for index, child in enumerate(raw[:12]):
                if remaining < 200:
                    break
                result.append(visit(child, path + "/" + str(index), depth + 1))
            if len(result) < len(raw):
                result.append({**omitted(raw, path, "record_limit"), "source_count": len(raw)})
                remaining -= 200
            return result
        if len(encoded) > min(2000, remaining):
            remaining -= 180
            return omitted(raw, path, "whole_value_omitted")
        remaining -= len(encoded)
        return raw

    return visit(value, "#", 0)


def _mechanism_evidence(update: Any) -> dict[str, Any]:
    if not isinstance(update, Mapping):
        return {"status": "unavailable", "reason": "No mechanism update supplied.",
                "posterior": None, "predictive_support": None, "remaining_uncertainty": None}
    keys = (
        "status", "updated", "reason", "belief_scope", "belief_id", "belief_binding",
        "intervention_id", "condition_id", "hypothesis_ids", "prior", "posterior",
        "remaining_uncertainty", "predictive_support", "conditional_independence_group",
        "consumed_observation_ids", "observation_bindings", "prediction_hash",
        "mechanism_claim", "empirical_action_utility", "next_action_value",
    )
    selected = {"raw_ref": "observation.mechanism_update",
                "source_sha256": hashlib.sha256(json.dumps(update, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest(),
                **{key: update[key] for key in keys if key in update}}
    return _bounded_evidence(selected, max_chars=7000)


def _visible_facts(payload: Mapping[str, Any]) -> dict[str, Any]:
    facts = {}

    def visit(value: Any, path: str):
        if isinstance(value, Mapping):
            if "unavailable_in_projection" in value:
                return
            for key, child in value.items():
                visit(child, path + "/" + str(key).replace("~", "~0").replace("/", "~1"))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, path + "/" + str(index))
        else:
            facts[path] = value

    for key in ("execution_facts", "execution_evidence", "observed_metrics", "scientific_evidence", "execution_identity", "semantic_facts", "executed_history", "mechanism_evidence"):
        visit(payload.get(key), "/" + key)
    return facts


def _validate_factual_assertions(reflection: RouteReflection, payload: Mapping[str, Any]) -> RouteReflection:
    if reflection.fact_contract_version == 0:
        if payload.get("execution_facts", {}).get("schema_version") == "execution-facts.v1":
            raise ValueError("versioned execution facts require fact_contract_version=1")
        return reflection.model_copy(update={
            "factual_validation": "unavailable_legacy",
            "factual_limitations": "Legacy prose is preserved but not semantically verified; structured factual assertions unavailable.",
        })
    facts = _visible_facts(payload)
    assertions = {item.fact_id: item.value for item in reflection.factual_assertions}
    if len(assertions) != len(reflection.factual_assertions):
        raise ValueError("duplicate factual assertion IDs")
    required = {"/semantic_facts/optimization_occurred", "/semantic_facts/physical_hypothesis_refuted", "/semantic_facts/local_optimum_established", "/semantic_facts/prior_optimization_occurred"}
    if not required.issubset(assertions):
        raise ValueError("missing required execution and physical-evidence assertions")
    for fact_id, value in assertions.items():
        if fact_id not in facts:
            raise ValueError(f"assertion references unavailable fact: {fact_id}")
        if json.dumps(value, sort_keys=True) != json.dumps(facts[fact_id], sort_keys=True):
            raise ValueError(f"assertion contradicts visible evidence: {fact_id}")
    if reflection.next_decision is not None:
        if not reflection.continue_recommended:
            raise ValueError("stop recommendation cannot propose a next decision")
        if any(ref not in facts for ref in reflection.next_decision.observation_ids):
            raise ValueError("next decision references unavailable observation facts")
    return reflection.model_copy(update={
        "factual_validation": "validated_structured",
        "factual_limitations": "Exact cited structured facts checked; free-text mechanisms and predictions remain hypotheses, not verified facts.",
    })


def _build_reflection_payload(
    pre_declarations: Mapping[str, Any],
    observation: Mapping[str, Any],
    score_history: list[float],
    epsilon: float,
    charter: Mapping[str, Any] | None = None,
    executed_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the user payload for the reflection prompt."""
    from .research_feedback import decision_score
    from .science.observation_adapter import reflection_evidence
    from .execution_facts import project_execution_history
    current_id = observation.get("iteration_id")
    history = executed_history or []
    history_view = project_execution_history(history, current_iteration_id=current_id,
        current_status=observation.get("run_status"), attempted_round_count=observation.get("attempted_round_count"))
    prior = history_view["prior_executions"]
    execution_facts = observation.get("execution_facts") or {"availability": "unavailable", "reason": "No execution fact record supplied."}
    evidence = observation.get("execution_evidence") or {}
    optimizers = evidence.get("optimizers") if isinstance(evidence, Mapping) else None
    optimization_occurred = True if any(
        isinstance(item, Mapping) and isinstance(item.get("evaluation_count"), (int, float))
        and item["evaluation_count"] > 0 for item in (optimizers or [])
    ) else None
    if isinstance(execution_facts, Mapping):
        explicit = execution_facts.get("optimizer_performed")
        if isinstance(explicit, bool):
            optimization_occurred = explicit
    payload = {
        "immutable_research_charter": _bounded_evidence(dict(charter or {}), max_chars=5000),
        "executed_history": _bounded_evidence(prior[-4:], max_chars=6000),
        "history_projection": _bounded_evidence({key: value for key, value in history_view.items() if key not in {"prior_executions", "current_execution"}}, max_chars=2000),
        "execution_identity": {"current_iteration_id": current_id, "current_run_status": observation.get("run_status"),
            "attempted_round_count": observation.get("attempted_round_count"),
            "executed_round_count": observation.get("physics_executed_round_count", observation.get("executed_round_count", observation.get("rounds_executed"))),
            "scored_round_count": len(score_history)},
        "execution_facts": _bounded_evidence(execution_facts, max_chars=10000),
        "mechanism_evidence": _mechanism_evidence(observation.get("mechanism_update")),
        "execution_evidence": _bounded_evidence(evidence, max_chars=6000),
        "semantic_facts": {"optimization_occurred": optimization_occurred,
            "physical_hypothesis_refuted": False, "local_optimum_established": False,
            "prior_optimization_occurred": True if any(row.get("optimizer_performed") is True for row in prior) else None},
        "history_interpretation": (
            "Execution records are immutable measured history. Equal execution signatures "
            "are deterministic repeats, not independent evidence of search exhaustion. "
            "Nominal budget limits are not optimizer allocations. A budget termination "
            "does not establish convergence or a local optimum."
        ),
        "scientific_evidence": _bounded_evidence(reflection_evidence(observation), max_chars=8000),
        "pre_declarations": _bounded_evidence({
            "expected_observations": list(pre_declarations.get("expected_observations") or []),
            "stop_conditions": list(pre_declarations.get("stop_conditions") or []),
        }, max_chars=4000),
        "observed_metrics": {
            "decision_score": decision_score(observation),
            "score_basis": observation.get("score_basis", "legacy_target"),
            "score_basis_fingerprint": observation.get("score_basis_fingerprint"),
            "best_frozen_score": observation.get("best_frozen_score"),
            "score_available": decision_score(observation) is not None,
            "legacy_target_score_diagnostic": observation.get("best_target_score"),
            "valid_candidates": observation.get("physically_valid_candidate_count"),
            "run_status": observation.get("run_status"),
            "rounds_executed": observation.get("rounds_executed"),
            "tightest_margin": observation.get("best_robustness_score"),
            "candidate_thicknesses_nm": [
                c.get("thicknesses_nm")
                for c in observation.get("candidate_summaries", [])
                if isinstance(c, Mapping) and c.get("thicknesses_nm") is not None
            ][:4],
        },
        "score_history": list(score_history[-32:]),
        "score_history_total_count": len(score_history),
        "score_history_basis": observation.get("score_basis", "legacy_target"),
        "score_history_basis_fingerprint": observation.get("score_basis_fingerprint"),
        "reference_epsilon": epsilon,
    }
    payload["observed_metrics"] = _bounded_evidence(payload["observed_metrics"], max_chars=5000)
    payload["fact_contract"] = {
        "version": 1, "fact_id_format": "JSON pointer into this payload; scalar leaves only",
        "required_assertions": ["/semantic_facts/optimization_occurred", "/semantic_facts/physical_hypothesis_refuted", "/semantic_facts/local_optimum_established", "/semantic_facts/prior_optimization_occurred"],
        "limitations": "Omitted evidence is unavailable, not negative evidence. Null means unknown. No physical-refutation or local-optimum certificate is implemented.",
    }
    return payload


def reflect_on_route(
    client: ReflectionClient,
    *,
    pre_declarations: Mapping[str, Any],
    observation: Mapping[str, Any],
    score_history: list[float],
    epsilon: float = 1e-4,
    force_mock: bool | None = None,
    context_sink=None,
    charter: Mapping[str, Any] | None = None,
    executed_history: list[dict[str, Any]] | None = None,
) -> RouteReflection:
    """Call LLM to reflect on a completed route.

    Args:
        client: LLM client conforming to ReflectionClient protocol.
        pre_declarations: Dict with expected_observations and stop_conditions
            from the pre-execution attestation (sidecar mapping).
        observation: Compressed view of ResearchIterationObservation.
        score_history: Historical decision-score sequence on this route's score basis.
        epsilon: Reference epsilon for stagnation reasoning (not a hard gate).
        force_mock: Override mock mode.

    Returns:
        RouteReflection with the reflection fields and typed stop basis, or a degraded instance
        if the LLM response is invalid (never raises).
    """
    system_prompt = DEFAULT_REFLECTION_PROMPT.read_text(encoding="utf-8")
    system_prompt += "\nReturn a JSON object satisfying this schema; do not nest prose fields: " + json.dumps(RouteReflection.model_json_schema())
    user_payload = _build_reflection_payload(pre_declarations, observation, score_history, epsilon, charter, executed_history)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]
    if context_sink is not None:
        from .science.contracts import canonical_hash
        context_sink({"actual_messages": messages, "context_hash": canonical_hash(messages)})
    try:
        response = client.call(
            messages,
            max_tokens=4000,
            force_mock=force_mock,
        )
    except Exception as exc:
        return RouteReflection.degraded(f"LLM call failed: {type(exc).__name__}")

    # Record token usage — accept both DashScope and OpenAI key spellings.
    if not isinstance(response, Mapping):
        return RouteReflection.degraded("Invalid response envelope")
    usage = response.get("_llm_usage") or {}
    if not isinstance(usage, Mapping):
        return RouteReflection.degraded("Invalid usage envelope")
    def token_count(*keys):
        for key in keys:
            value = usage.get(key)
            if value is not None:
                try:
                    return max(0, int(value))
                except (TypeError, ValueError, OverflowError):
                    return 0
        return 0
    input_tokens = token_count("input_tokens", "prompt_tokens", "estimated_input_tokens")
    output_tokens = token_count("output_tokens", "completion_tokens", "estimated_output_tokens")
    if input_tokens or output_tokens:
        # D-2 (R-04-FIX): meter into the existing "turbo" budget bucket (the
        # feedback/compilation light-task tier per qwen_config.py). The first
        # argument of record_qwen_usage is the bucket key, NOT a model name;
        # passing REFLECTION_MODEL here silently created a third bucket that
        # no run-level budget summary aggregates.
        get_cost_tracker().record_qwen_usage("turbo", input_tokens + output_tokens)

    content = str(response.get("content") or "")
    parsed = _safe_json(content)
    if not parsed:
        return RouteReflection.degraded("Empty or non-JSON response")

    try:
        reflection = RouteReflection.model_validate(parsed)
        history = executed_history or []
        signatures = [row.get("execution_signature") for row in history if row.get("execution_signature")]
        if (not reflection.continue_recommended
                and reflection.stop_basis in {"search_exhausted", "marginal_gains_too_low"}
                and len(signatures) >= 2 and len(set(signatures)) == 1):
            raise ValueError("deterministic repeats do not establish independent search exhaustion")
        prose = " ".join((reflection.observed_vs_expected, reflection.deviation_mechanism,
                          reflection.continue_rationale)).lower()
        optimum_claim = re.search(r"(?:reached|converged to|is at|at a) (?:a |the )?local (?:optimum|minimum)", prose)
        if optimum_claim:
            raise ValueError("local-optimum claims require evidence outside the reflection contract")
        return _validate_factual_assertions(reflection, user_payload)
    except Exception as exc:
        return RouteReflection.degraded(f"Validation failed: {type(exc).__name__}: {str(exc)[:500]}")


def write_reflection_sidecar(
    iteration_dir: Path,
    reflection: RouteReflection,
    attestation_path: Path,
    observed_metrics: dict[str, Any],
    reflection_available: bool,
) -> Path:
    """Write ROUTE.REFLECTION.json with binding to pre-execution attestation."""
    attestation_bytes = attestation_path.read_bytes()
    attestation_sha256 = hashlib.sha256(attestation_bytes).hexdigest()

    from datetime import datetime, timezone

    payload = {
        **reflection.model_dump(mode="json"),
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "attestation_sha256": attestation_sha256,
        "observed_metrics": observed_metrics,
        "reflection_available": reflection_available,
    }
    target = iteration_dir / "ROUTE.REFLECTION.json"
    from optomind_research.runtime.artifact_store import atomic_write_json
    atomic_write_json(target, payload)
    return target


__all__ = [
    "DEFAULT_REFLECTION_PROMPT",
    "REFLECTION_MODEL",
    "RouteReflection",
    "ReflectionClient",
    "reflect_on_route",
    "write_reflection_sidecar",
]
