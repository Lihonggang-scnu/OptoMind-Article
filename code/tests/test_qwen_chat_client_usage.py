import json

from llm import qwen_chat_client


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _config() -> dict:
    return {
        "api_key": "test-key",
        "api_key_candidates": [
            {
                "api_key": "test-key",
                "api_key_source": "test",
                "api_key_masked": "tes***key",
            }
        ],
        "base_url": "https://example.invalid/v1",
        "model": "qwen3.7-flash",
        "fallback_models": [],
        "mock_llm": False,
    }


def test_provider_token_usage_is_preserved(monkeypatch) -> None:
    monkeypatch.setattr(
        qwen_chat_client, "get_qwen_client_config", lambda _: _config()
    )
    monkeypatch.setattr(
        qwen_chat_client.urllib.request,
        "urlopen",
        lambda *args, **kwargs: _Response(
            {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {
                    "prompt_tokens": 123,
                    "completion_tokens": 45,
                    "total_tokens": 168,
                },
            }
        ),
    )

    result = qwen_chat_client.call_qwen_chat(
        "usage-test",
        [{"role": "user", "content": "hello"}],
        model_tier="b_plus_model",
        max_retries=0,
        force_mock=False,
    )

    usage = result["_llm_usage"]
    assert usage["input_tokens"] == 123
    assert usage["output_tokens"] == 45
    assert usage["total_tokens"] == 168
    assert usage["token_counts_source"] == "provider"
    assert usage["estimated_input_tokens"] > 0
    assert usage["estimated_output_tokens"] > 0


def test_missing_provider_usage_keeps_estimate_fallback(monkeypatch) -> None:
    monkeypatch.setattr(
        qwen_chat_client, "get_qwen_client_config", lambda _: _config()
    )
    monkeypatch.setattr(
        qwen_chat_client.urllib.request,
        "urlopen",
        lambda *args, **kwargs: _Response(
            {"choices": [{"message": {"content": "ok"}}]}
        ),
    )

    result = qwen_chat_client.call_qwen_chat(
        "usage-test",
        [{"role": "user", "content": "hello"}],
        model_tier="b_plus_model",
        max_retries=0,
        force_mock=False,
    )

    usage = result["_llm_usage"]
    assert usage["input_tokens"] == 0
    assert usage["output_tokens"] == 0
    assert usage["total_tokens"] == 0
    assert usage["token_counts_source"] == "estimated"
    assert usage["estimated_input_tokens"] > 0
    assert usage["estimated_output_tokens"] > 0
