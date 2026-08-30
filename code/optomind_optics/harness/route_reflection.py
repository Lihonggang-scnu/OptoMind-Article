"""Route reflection: LLM-driven post-execution analysis with deterministic cross-check.

This module implements the "Phase B" of R-04: after a route executes, the LLM
reflects on actual observations vs pre-execution declarations, explains
deviations physically, and recommends continue/stop with grounded insight.

The reflection is written to ROUTE.REFLECTION.json (sidecar) and bound to the
pre-execution attestation via attestation_sha256. A deterministic dual-gate
then cross-checks the LLM recommendation against hard criteria.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol

from pydantic import BaseModel, ConfigDict, Field

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
REFLECTION_MODEL = "qwen3.5-flash"


class RouteReflection(BaseModel):
    """Structured LLM reflection on a completed route."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    observed_vs_expected: str
    deviation_mechanism: str
    continue_recommended: bool
    continue_rationale: str
    insight_for_next: str
    insight_grounding: str
    # A stop vote is not actionable unless the model states which of the two
    # scientifically different claims it is making.  The empty default keeps
    # old six-field reflection artifacts readable; the new prompt requires a
    # non-empty value whenever continue_recommended is false.
    stop_basis: Literal["", "physically_infeasible", "marginal_gains_too_low"] = ""
    # D-3 (R-04-FIX): explicit degradation marker — empty on the normal path,
    # filled by degraded(). Must keep a default so model_validate() still
    # accepts the six-key JSON emitted by the LLM (extra="forbid" rejects
    # unknown keys, but an absent defaulted field is fine). RouteReflection
    # only ever reaches sidecar files (ROUTE.REFLECTION.json), never
    # ArticlePipelineResult, so the hash contract (red line 5) is untouched.
    degraded_reason: str = ""

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
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                value = json.loads(text[start : end + 1])
                return value if isinstance(value, dict) else {}
            except json.JSONDecodeError:
                pass
    return {}


def _build_reflection_payload(
    pre_declarations: Mapping[str, Any],
    observation: Mapping[str, Any],
    score_history: list[float],
    epsilon: float,
) -> dict[str, Any]:
    """Build the user payload for the reflection prompt."""
    return {
        "pre_declarations": {
            "expected_observations": list(pre_declarations.get("expected_observations") or []),
            "stop_conditions": list(pre_declarations.get("stop_conditions") or []),
        },
        "observed_metrics": {
            "best_target_score": observation.get("best_target_score"),
            "valid_candidates": observation.get("physically_valid_candidate_count"),
            "run_status": observation.get("run_status"),
            "rounds_executed": len(score_history),
            "tightest_margin": observation.get("best_robustness_score"),
            "candidate_thicknesses_nm": [
                c.get("thicknesses_nm")
                for c in observation.get("candidate_summaries", [])
                if isinstance(c, Mapping) and c.get("thicknesses_nm") is not None
            ][:4],
        },
        "score_history": list(score_history),
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
) -> RouteReflection:
    """Call LLM to reflect on a completed route.

    Args:
        client: LLM client conforming to ReflectionClient protocol.
        pre_declarations: Dict with expected_observations and stop_conditions
            from the pre-execution attestation (sidecar mapping).
        observation: Compressed view of ResearchIterationObservation.
        score_history: Historical best_target_score sequence for this route.
        epsilon: Reference epsilon for stagnation reasoning (not a hard gate).
        force_mock: Override mock mode.

    Returns:
        RouteReflection with the reflection fields and typed stop basis, or a degraded instance
        if the LLM response is invalid (never raises).
    """
    system_prompt = DEFAULT_REFLECTION_PROMPT.read_text(encoding="utf-8")
    user_payload = _build_reflection_payload(pre_declarations, observation, score_history, epsilon)

    try:
        response = client.call(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            max_tokens=4000,
            force_mock=force_mock,
        )
    except Exception as exc:
        return RouteReflection.degraded(f"LLM call failed: {type(exc).__name__}")

    # Record token usage — accept both DashScope and OpenAI key spellings.
    usage = dict(response.get("_llm_usage") or {})
    input_tokens = int(
        usage.get("input_tokens")
        or usage.get("prompt_tokens")
        or usage.get("estimated_input_tokens")
        or 0
    )
    output_tokens = int(
        usage.get("output_tokens")
        or usage.get("completion_tokens")
        or usage.get("estimated_output_tokens")
        or 0
    )
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
        return RouteReflection.model_validate(parsed)
    except Exception as exc:
        return RouteReflection.degraded(f"Validation failed: {type(exc).__name__}")


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
