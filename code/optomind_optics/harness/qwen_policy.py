"""Constrained Qwen policy for TMM strategy choices.

The policy may choose among program-provided actions. It cannot run solvers,
certify physics, mutate the experiment graph directly, or switch model families.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from config.qwen_config import get_model_name
from llm.qwen_chat_client import call_qwen_chat

from .contracts import ActionType


QWEN_POLICY_TIER = "b_plus_model"
QWEN_POLICY_MODEL = "qwen3.7-flash"
DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "optical_harness"
    / "TMM Strategy Policy.txt"
)


class QwenPolicyError(RuntimeError):
    pass


class TMMPolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: ActionType
    parameters: Dict[str, Any] = Field(default_factory=dict)
    rationale: str
    expected_information_gain: float = 0.0

    @field_validator("expected_information_gain")
    @classmethod
    def _unit_interval(cls, value: float) -> float:
        if not 0.0 <= float(value) <= 1.0:
            raise ValueError("expected_information_gain must be in [0, 1]")
        return float(value)


def _safe_json(text: str) -> Dict[str, Any]:
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
                return {}
    return {}


class QwenFlashOnlyClient:
    """Fail closed if the configured tier no longer resolves to qwen3.7-flash."""

    model_tier = QWEN_POLICY_TIER
    model_name = QWEN_POLICY_MODEL

    def __init__(self, *, agent_name: str = "TMMHarnessPolicy") -> None:
        self.agent_name = str(agent_name or "TMMHarnessPolicy")

    def call(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 900,
        force_mock: bool | None = None,
    ) -> Dict[str, Any]:
        resolved = str(get_model_name(self.model_tier))
        if resolved != self.model_name:
            raise QwenPolicyError(
                f"Optical Harness model lock violation: {self.model_tier} resolved to {resolved!r}"
            )
        result = call_qwen_chat(
            self.agent_name,
            messages,
            model_tier=self.model_tier,
            max_retries=1,
            temperature=0.0,
            force_mock=force_mock,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            allow_model_fallback=False,
            accept_partial_stream=False,
            enable_thinking=False,
        )
        usage = dict(result.get("_llm_usage") or {})
        actual = str(usage.get("model_name") or self.model_name)
        if not usage.get("mock_llm") and actual != self.model_name:
            raise QwenPolicyError(f"Provider returned disallowed model: {actual!r}")
        return result


class QwenTMMPolicy:
    def __init__(
        self,
        *,
        client: QwenFlashOnlyClient | None = None,
        prompt_path: str | Path = DEFAULT_PROMPT_PATH,
    ) -> None:
        self.client = client or QwenFlashOnlyClient()
        self.prompt_path = Path(prompt_path)

    def propose(
        self,
        compact_state: Dict[str, Any],
        allowed_actions: Iterable[ActionType | str],
        *,
        force_mock: bool | None = None,
    ) -> tuple[TMMPolicyDecision, Dict[str, Any]]:
        allowed = [
            item.value if isinstance(item, ActionType) else ActionType(str(item)).value
            for item in allowed_actions
        ]
        if not allowed:
            raise QwenPolicyError("No allowed actions were supplied")
        system_prompt = self.prompt_path.read_text(encoding="utf-8")
        payload = {
            "compact_state": compact_state,
            "allowed_actions": allowed,
            "model_constraint": QWEN_POLICY_MODEL,
        }
        response = self.client.call(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            force_mock=force_mock,
        )
        parsed = _safe_json(str(response.get("content") or ""))
        try:
            decision = TMMPolicyDecision.model_validate(parsed)
        except ValidationError as exc:
            raise QwenPolicyError(f"Invalid qwen3.7-flash policy JSON: {exc}") from exc
        if decision.action.value not in allowed:
            raise QwenPolicyError(
                f"qwen3.7-flash proposed action outside allowlist: {decision.action.value}"
            )
        return decision, dict(response.get("_llm_usage") or {})


__all__ = [
    "DEFAULT_PROMPT_PATH",
    "QWEN_POLICY_MODEL",
    "QWEN_POLICY_TIER",
    "QwenFlashOnlyClient",
    "QwenPolicyError",
    "QwenTMMPolicy",
    "TMMPolicyDecision",
]
