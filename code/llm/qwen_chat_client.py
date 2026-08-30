"""Qwen conversational chat client — natural language, non-JSON mode.

Separate from call_qwen_json; this is for research chat / answer synthesis.
Uses the A/B/C/D model policy in config/model_policy.yaml.
"""

from __future__ import annotations

import json, os, time, urllib.error, urllib.request
from typing import Any, Dict, Optional

from config.model_router import select_model_tier
from config.qwen_config import get_model_name, get_qwen_client_config
from config.secret_pool import (
    attach_provider_error_detail,
    is_key_level_http_error,
    is_model_scoped_allocation_error,
)


def _http_timeout_seconds() -> float:
    try:
        return max(5.0, float(os.environ.get("QWEN_HTTP_TIMEOUT_SEC", "180")))
    except ValueError:
        return 60.0


def _max_key_candidates() -> int:
    try:
        return max(0, int(os.environ.get("QWEN_MAX_KEY_CANDIDATES", "0")))
    except ValueError:
        return 0


def _max_transport_key_candidates() -> int:
    """Limit pointless key rotation for endpoint/network failures.

    Authentication, quota, and rate-limit errors still rotate through the full
    pool. A timeout, disconnected socket, or model-level 4xx is not repaired by
    trying every secret against the same endpoint.
    """
    try:
        return max(1, int(os.environ.get("QWEN_MAX_TRANSPORT_KEY_CANDIDATES", "2")))
    except ValueError:
        return 2


def _usage(agent_name, model_tier, model_name, task_type, mock, inp, out, success, error, **extra):
    return {
        "module": agent_name, "agent_name": agent_name,
        "model_tier": model_tier, "model_name": model_name,
        "task_type": task_type, "mock_llm": mock,
        "estimated_input_tokens": max(1, len(str(inp)) // 4),
        "estimated_output_tokens": max(1, len(str(out or "")) // 4),
        "success": success, "failure": not success,
        "error_type": error or "", "fallback_used": bool(error),
        **extra,
    }


def _error_name(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTPError_{exc.code}"
    return type(exc).__name__


def _attach_http_error_detail(exc: urllib.error.HTTPError) -> str:
    """Read and cache provider error metadata for safe failure routing.

    ``str(HTTPError)`` contains only the HTTP status.  DashScope returns
    account/quota causes (for example ``Arrearage`` or
    ``AllocationQuota.FreeTierOnly``) in the response body.  Without reading
    that body a key failure is mistaken for a model-contract failure and the
    client tries several models against the same unusable account.
    """

    return attach_provider_error_detail(exc)


def call_qwen_chat(
    agent_name: str,
    messages: list,
    model_tier: str = "standard_model",
    max_retries: int = 2,
    temperature: float = 0.3,
    force_mock: bool | None = None,
    max_tokens: int = 4096,
    response_format: dict | None = None,
    stream: bool = False,
    timeout_seconds: float | None = None,
    max_transport_key_candidates: int | None = None,
    max_key_candidates: int | None = None,
    allow_model_fallback: bool = True,
    accept_partial_stream: bool = True,
    enable_thinking: bool | None = None,
) -> dict:
    """Call Qwen in conversational mode. Returns {"content": str, "_llm_usage": dict}."""

    tier = model_tier
    cfg = get_qwen_client_config(tier)
    model_name = str(cfg.get("model") or get_model_name(tier))
    fallback_models = [str(item).strip() for item in (cfg.get("fallback_models") or []) if str(item).strip()]
    if not fallback_models:
        fallback_model = str(cfg.get("fallback_model") or "").strip()
        fallback_models = [fallback_model] if fallback_model else []
    model_candidates: list[tuple[str, bool]] = [(model_name, False)]
    if allow_model_fallback:
        seen_models = {model_name}
        for fallback_model in fallback_models:
            if fallback_model not in seen_models:
                seen_models.add(fallback_model)
                model_candidates.append((fallback_model, True))

    if force_mock is True or bool(cfg.get("mock_llm", True)):
        return {
            "content": "[mock] Qwen chat mode requires API connection.",
            "_llm_usage": _usage(agent_name, tier, model_name, "research_chat", True, str(messages), "", False, "MockMode"),
        }

    key_candidates = list(cfg.get("api_key_candidates") or [])
    if not key_candidates and cfg.get("api_key"):
        key_candidates = [
            {
                "api_key": str(cfg.get("api_key") or ""),
                "api_key_source": str(cfg.get("api_key_source") or ""),
                "api_key_masked": str(cfg.get("api_key_masked") or ""),
            }
        ]
    key_candidate_limit = (
        max(1, int(max_key_candidates))
        if max_key_candidates is not None
        else _max_key_candidates()
    )
    if key_candidate_limit:
        key_candidates = key_candidates[:key_candidate_limit]
    base_url = str(cfg.get("base_url") or "")
    timeout_sec = (
        max(5.0, float(timeout_seconds))
        if timeout_seconds is not None
        else _http_timeout_seconds()
    )
    transport_key_limit = (
        max(1, int(max_transport_key_candidates))
        if max_transport_key_candidates is not None
        else _max_transport_key_candidates()
    )

    last_error = ""
    key_failures: list[str] = []
    attempted_models: list[str] = []
    request_attempts = 0
    for model_index, (candidate_model, used_model_fallback) in enumerate(model_candidates):
        attempted_models.append(candidate_model)
        transport_key_failures = 0
        stop_key_rotation = False
        body = {
            "model": candidate_model,
            "temperature": temperature,
            "messages": messages,
            "max_tokens": max(64, int(max_tokens)),
        }
        if response_format:
            body["response_format"] = response_format
        # Qwen 3.7/3.6/3.5 are hybrid-thinking models.  Keep this opt-in at
        # the shared client boundary: reasoning-heavy agents can retain the
        # provider default, while deterministic tagging/extraction jobs may
        # explicitly disable thinking to reduce latency and token cost.
        if enable_thinking is not None:
            body["enable_thinking"] = bool(enable_thinking)
        if stream:
            body["stream"] = True
        for key_index, key_info in enumerate(key_candidates, 1):
            api_key = str(key_info.get("api_key") or "")
            if not api_key:
                continue
            for attempt in range(max(int(max_retries), 0) + 1):
                try:
                    request_attempts += 1
                    request_started = time.monotonic()
                    stream_interrupted_error = ""
                    provider_usage: dict[str, Any] = {}
                    req = urllib.request.Request(
                        url=base_url.rstrip("/") + "/chat/completions",
                        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                        if stream:
                            pieces: list[str] = []
                            try:
                                for raw_line in resp:
                                    if time.monotonic() - request_started > timeout_sec:
                                        raise TimeoutError(
                                            f"Qwen stream exceeded total wall-clock limit of {timeout_sec:.1f}s"
                                        )
                                    line = raw_line.decode("utf-8", errors="replace").strip()
                                    if not line:
                                        continue
                                    if line.startswith("data:"):
                                        line = line[5:].strip()
                                    if line == "[DONE]":
                                        break
                                    try:
                                        event = json.loads(line)
                                    except json.JSONDecodeError:
                                        continue
                                    if isinstance(event, dict) and isinstance(
                                        event.get("usage"), dict
                                    ):
                                        provider_usage = dict(event["usage"])
                                    choices = event.get("choices") if isinstance(event, dict) else None
                                    if not choices:
                                        continue
                                    choice = choices[0] if isinstance(choices[0], dict) else {}
                                    delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
                                    value = delta.get("content")
                                    if value is None and isinstance(choice.get("message"), dict):
                                        value = choice["message"].get("content")
                                    if value:
                                        pieces.append(str(value))
                            except Exception as stream_exc:
                                stream_interrupted_error = _error_name(stream_exc)
                                # A proxy may close an SSE stream after most of
                                # the answer was delivered. Return the partial
                                # content so the caller's JSON repair/validation
                                # layer can decide whether it is usable.
                                if not pieces or not accept_partial_stream:
                                    raise
                            content = "".join(pieces)
                            if not content:
                                raise ValueError("Qwen stream returned no content")
                        else:
                            data = json.loads(resp.read().decode("utf-8"))
                            content = data["choices"][0]["message"]["content"]
                            if isinstance(data.get("usage"), dict):
                                provider_usage = dict(data["usage"])
                    input_tokens = int(
                        provider_usage.get("prompt_tokens")
                        or provider_usage.get("input_tokens")
                        or 0
                    )
                    output_tokens = int(
                        provider_usage.get("completion_tokens")
                        or provider_usage.get("output_tokens")
                        or 0
                    )
                    total_tokens = int(
                        provider_usage.get("total_tokens")
                        or input_tokens + output_tokens
                    )
                    return {
                        "content": content,
                        "_llm_usage": _usage(
                            agent_name,
                            tier,
                            candidate_model,
                            "research_chat",
                            False,
                            str(messages),
                            content,
                            True,
                            last_error if used_model_fallback or key_failures else "",
                            api_key_source=key_info.get("api_key_source", ""),
                            api_key_masked=key_info.get("api_key_masked", ""),
                            api_key_candidate_count=len(key_candidates),
                            api_key_rotation_count=max(0, key_index - 1),
                            key_failures=key_failures[-5:],
                            fallback_used=used_model_fallback,
                            model_fallback_used=used_model_fallback,
                            fallback_chain=[name for name, _ in model_candidates[1:]],
                            attempted_models=list(attempted_models),
                            selected_model_index=model_index,
                            partial_stream=bool(stream_interrupted_error),
                            stream_interrupted_error=stream_interrupted_error,
                            request_attempt_count=request_attempts,
                            retry_count=max(0, request_attempts - 1),
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            total_tokens=total_tokens,
                            token_counts_source=(
                                "provider"
                                if input_tokens or output_tokens or total_tokens
                                else "estimated"
                            ),
                        ),
                    }
                except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
                    provider_detail = (
                        _attach_http_error_detail(exc)
                        if isinstance(exc, urllib.error.HTTPError)
                        else ""
                    )
                    provider_code = (
                        provider_detail.split(" ", 1)[0]
                        if provider_detail
                        else ""
                    )
                    last_error = _error_name(exc) + (
                        f"_{provider_code}" if provider_code else ""
                    )
                    if is_key_level_http_error(exc):
                        key_failures.append(f"{key_info.get('api_key_masked','') or 'key'}:{last_error}")
                        break
                    if is_model_scoped_allocation_error(exc):
                        # This key is valid, but the requested model allocation
                        # is unavailable. Try the configured model fallback on
                        # the same key instead of rotating unrelated accounts.
                        stop_key_rotation = True
                        break
                    # A rejected request/model contract is independent of the
                    # secret. Move to the configured fallback model immediately.
                    if isinstance(exc, urllib.error.HTTPError) and exc.code in {400, 404, 405, 415, 422}:
                        stop_key_rotation = True
                    if attempt < max(int(max_retries), 0):
                        time.sleep(0.5 * (attempt + 1))
                        continue
                    transport_key_failures += 1
                    if transport_key_failures >= transport_key_limit:
                        stop_key_rotation = True
                    break
                except Exception as exc:
                    last_error = type(exc).__name__
                    if is_key_level_http_error(exc):
                        key_failures.append(f"{key_info.get('api_key_masked','') or 'key'}:{last_error}")
                        break
                    # http.client.RemoteDisconnected, ConnectionResetError and
                    # similar socket failures arrive through this broad branch
                    # rather than urllib.error.URLError.  They are commonly
                    # transient, so honour the caller's retry budget before
                    # rotating models or returning a deterministic fallback.
                    if attempt < max(int(max_retries), 0):
                        time.sleep(0.5 * (attempt + 1))
                        continue
                    transport_key_failures += 1
                    if transport_key_failures >= transport_key_limit:
                        stop_key_rotation = True
                    break
            if stop_key_rotation:
                break

    return {
        "content": f"[fallback] Qwen chat failed: {last_error}. Proceeding with deterministic answer.",
        "_llm_usage": _usage(
            agent_name,
            tier,
            model_name,
            "research_chat",
            False,
            "",
            "",
            False,
            last_error or "QwenChatFailed",
            api_key_candidate_count=len(key_candidates),
            key_failures=key_failures[-5:],
            fallback_used=True,
            model_fallback_used=bool(len(attempted_models) > 1),
            fallback_chain=[name for name, _ in model_candidates[1:]],
            attempted_models=attempted_models,
            request_attempt_count=request_attempts,
            retry_count=max(0, request_attempts - 1),
        ),
    }
