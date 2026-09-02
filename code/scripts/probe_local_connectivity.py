"""Perform minimal live Qwen and Semantic Scholar probes without printing keys."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def _keys(path_text: str) -> list[str]:
    path = Path(path_text).expanduser().resolve()
    candidates: list[str] = []
    seen: set[str] = set()
    raw_text = path.read_text(encoding="utf-8-sig", errors="replace")
    for raw in re.split(r"[\r\n,;]+", raw_text):
        value = raw.strip()
        if not value or value.startswith("#"):
            continue
        if "=" in value and not value.startswith(("sk-", "sk_")):
            value = value.split("=", 1)[1].strip()
        if value and value not in seen:
            seen.add(value)
            candidates.append(value)
    if not candidates:
        raise RuntimeError(f"密钥文件没有可用内容：{path.name}")
    return candidates[:8]


def _result(check_id: str, label: str, status: str, detail: str) -> dict[str, str]:
    return {"id": check_id, "label": label, "status": status, "detail": detail}


def _http_error(prefix: str, exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"{prefix}返回 HTTP {exc.code}。"
    if isinstance(exc, urllib.error.URLError):
        return f"{prefix}连接失败：{type(exc.reason).__name__}。"
    return f"{prefix}连接失败：{type(exc).__name__}。"


def _may_try_next_key(exc: BaseException) -> bool:
    return isinstance(exc, urllib.error.HTTPError) and exc.code in {401, 403, 429}


def probe_qwen() -> dict[str, str]:
    try:
        keys = _keys(os.environ["QWEN_API_KEY_FILE"])
        configured = os.environ.get(
            "OPTOMIND_QWEN_PROBE_MODELS",
            "qwen3.5-plus,qwen3.7-flash,qwen3.5-flash",
        )
        models = list(dict.fromkeys(item.strip() for item in configured.split(",") if item.strip()))
        if not models:
            raise RuntimeError("没有配置需要检查的 Qwen 模型")
        for model in models:
            payload = json.dumps(
                {
                    "model": model,
                    "messages": [
                        {
                            "role": "user",
                            "content": "这是连通性检查。请只回复 OK。",
                        }
                    ],
                    "max_tokens": 8,
                    "temperature": 0,
                    "enable_thinking": False,
                },
                ensure_ascii=False,
            ).encode("utf-8")
            last_error: BaseException | None = None
            for key in keys:
                request = urllib.request.Request(
                    "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                    data=payload,
                    method="POST",
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                        "User-Agent": "OptoMind-Article-Local-Probe/1.0",
                    },
                )
                try:
                    with urllib.request.urlopen(request, timeout=45) as response:
                        body: Any = json.loads(response.read().decode("utf-8"))
                    if not isinstance(body, dict) or not body.get("choices"):
                        raise RuntimeError("响应中没有 choices")
                    last_error = None
                    break
                except BaseException as exc:
                    last_error = exc
                    if not _may_try_next_key(exc):
                        raise
            if last_error is not None:
                raise last_error
        return _result(
            "qwen",
            "Qwen 模型服务",
            "passed",
            f"{', '.join(models)} 均已完成最小真实响应，密钥池可用。",
        )
    except BaseException as exc:
        return _result("qwen", "Qwen 模型服务", "failed", _http_error("Qwen", exc))


def probe_semantic_scholar() -> dict[str, str]:
    try:
        keys = _keys(os.environ["SEMANTIC_SCHOLAR_API_KEYS_FILE"])
        query = urllib.parse.urlencode(
            {"query": "optical thin film", "limit": 1, "fields": "paperId,title"}
        )
        last_error: BaseException | None = None
        for key in keys:
            request = urllib.request.Request(
                f"https://api.semanticscholar.org/graph/v1/paper/search?{query}",
                headers={
                    "x-api-key": key,
                    "Accept": "application/json",
                    "User-Agent": "OptoMind-Article-Local-Probe/1.0",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=45) as response:
                    body: Any = json.loads(response.read().decode("utf-8"))
                if not isinstance(body, dict) or not isinstance(body.get("data"), list):
                    raise RuntimeError("响应中没有论文列表")
                return _result(
                    "semantic_scholar",
                    "Semantic Scholar 文献服务",
                    "passed",
                    "论文检索接口已返回结构化结果，密钥池可用。",
                )
            except BaseException as exc:
                last_error = exc
                if not _may_try_next_key(exc):
                    raise
        if last_error is not None:
            raise last_error
        raise RuntimeError("没有可测试的 Semantic Scholar 密钥")
    except BaseException as exc:
        return _result(
            "semantic_scholar",
            "Semantic Scholar 文献服务",
            "failed",
            _http_error("Semantic Scholar", exc),
        )


def main() -> int:
    checks = [probe_qwen(), probe_semantic_scholar()]
    ready = all(item["status"] == "passed" for item in checks)
    print(json.dumps({"ready": ready, "checks": checks}, ensure_ascii=False))
    return 0 if ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
