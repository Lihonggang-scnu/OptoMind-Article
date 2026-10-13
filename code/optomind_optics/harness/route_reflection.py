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
from .text_safety import repair_scientific_payload


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


class RouteReflection(BaseModel):
    """Structured LLM reflection on a completed route."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_max_length=8000)

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
    return {
        "immutable_research_charter": dict(charter or {}),
        "executed_history": json.loads(json.dumps(executed_history or [])),
        "execution_evidence": observation.get("execution_evidence", {}),
        "history_interpretation": (
            "Execution records are immutable measured history. Equal execution signatures "
            "are deterministic repeats, not independent evidence of search exhaustion. "
            "Nominal budget limits are not optimizer allocations. A budget termination "
            "does not establish convergence or a local optimum."
        ),
        "scientific_evidence": reflection_evidence(observation),
        "pre_declarations": {
            "expected_observations": list(pre_declarations.get("expected_observations") or []),
            "stop_conditions": list(pre_declarations.get("stop_conditions") or []),
        },
        "observed_metrics": {
            "decision_score": decision_score(observation),
            "score_basis": observation.get("score_basis", "legacy_target"),
            "score_basis_fingerprint": observation.get("score_basis_fingerprint"),
            "best_frozen_score": observation.get("best_frozen_score"),
            "score_available": decision_score(observation) is not None,
            "legacy_target_score_diagnostic": observation.get("best_target_score"),
            "valid_candidates": observation.get("physically_valid_candidate_count"),
            "run_status": observation.get("run_status"),
            "rounds_executed": observation.get("rounds_executed", len(score_history)),
            "tightest_margin": observation.get("best_robustness_score"),
            "candidate_thicknesses_nm": [
                c.get("thicknesses_nm")
                for c in observation.get("candidate_summaries", [])
                if isinstance(c, Mapping) and c.get("thicknesses_nm") is not None
            ][:4],
        },
        "score_history": list(score_history),
        "score_history_basis": observation.get("score_basis", "legacy_target"),
        "score_history_basis_fingerprint": observation.get("score_basis_fingerprint"),
        "reference_epsilon": epsilon,
    }


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
        return reflection
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
