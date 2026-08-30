from __future__ import annotations

import json

import pytest

from optomind_optics.harness import ActionType
from optomind_optics.harness import qwen_policy as module


def test_client_forces_exact_model_and_disables_fallback(monkeypatch) -> None:
    observed = {}
    monkeypatch.setattr(module, "get_model_name", lambda tier: "qwen3.7-flash")

    def fake_call(agent_name, messages, **kwargs):
        observed.update(kwargs)
        return {
            "content": "{}",
            "_llm_usage": {"model_name": "qwen3.7-flash", "mock_llm": False},
        }

    monkeypatch.setattr(module, "call_qwen_chat", fake_call)
    module.QwenFlashOnlyClient().call([{"role": "user", "content": "x"}])
    assert observed["model_tier"] == "b_plus_model"
    assert observed["allow_model_fallback"] is False
    assert observed["enable_thinking"] is False


def test_client_fails_closed_if_tier_mapping_changes(monkeypatch) -> None:
    monkeypatch.setattr(module, "get_model_name", lambda tier: "qwen3.6-flash")
    with pytest.raises(module.QwenPolicyError, match="model lock"):
        module.QwenFlashOnlyClient().call([])


def test_policy_rejects_action_outside_program_allowlist(tmp_path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Return JSON.", encoding="utf-8")

    class FakeClient:
        def call(self, messages, **kwargs):
            return {
                "content": json.dumps(
                    {
                        "action": "run_optimizer",
                        "parameters": {},
                        "rationale": "Try optimization.",
                        "expected_information_gain": 0.5,
                    }
                ),
                "_llm_usage": {"model_name": "qwen3.7-flash"},
            }

    policy = module.QwenTMMPolicy(client=FakeClient(), prompt_path=prompt)
    with pytest.raises(module.QwenPolicyError, match="outside allowlist"):
        policy.propose({}, [ActionType.stop])


def test_policy_accepts_valid_typed_action(tmp_path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Return JSON.", encoding="utf-8")

    class FakeClient:
        def call(self, messages, **kwargs):
            return {
                "content": '{"action":"run_convergence_audit","parameters":{},"rationale":"Refine the spectrum.","expected_information_gain":0.8}',
                "_llm_usage": {"model_name": "qwen3.7-flash"},
            }

    decision, usage = module.QwenTMMPolicy(client=FakeClient(), prompt_path=prompt).propose(
        {"latest_failure": "spectral_convergence_failure"},
        [ActionType.run_convergence_audit, ActionType.stop],
    )
    assert decision.action == ActionType.run_convergence_audit
    assert usage["model_name"] == "qwen3.7-flash"
