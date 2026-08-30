"""Stage 14 abstract/conclusion translation (T-15).

Translates the English abstract and conclusion into Chinese through the
PLUS tier, then verifies -- programmatically, zero trust in the model --
that every numeric literal survived unchanged. Material ids are normalized
through MATERIAL_DISPLAY_NAMES. Any failure is swallowed into
TranslationResult(skipped=True) so the harness keeps running.
"""

from __future__ import annotations

import re
import warnings
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from config.qwen_config import get_cost_tracker
from .article_publication import MATERIAL_DISPLAY_NAMES

NUMBER_LITERAL_RE = re.compile(r"\d+\.?\d*")
_TRANSLATION_PROMPT = (
    "请将以下文本翻译为中文，所有数字、公式、材料名称（如 SiO2、TiO2）"
    "保持英文原样，不要翻译或转换。\n\n{text}"
)


@dataclass
class TranslationResult:
    abstract_zh: str | None = None
    conclusion_zh: str | None = None
    skipped: bool = False              # True when translation failed silently
    mismatch_values: list = field(default_factory=list)


def _default_plus_client():
    # Lazy import keeps this module light and cycle-safe.
    from .problem_analyzer import ArticlePlusQwenClient

    return ArticlePlusQwenClient()


def _qwen_total_tokens(usage: Any) -> int:
    usage = dict(usage or {})
    for key in ("total_tokens", "totalTokens"):
        if usage.get(key) is not None:
            return int(usage[key])
    input_tokens = (
        usage.get("input_tokens") or usage.get("inputTokens")
        or usage.get("prompt_tokens") or 0
    )
    output_tokens = (
        usage.get("output_tokens") or usage.get("outputTokens")
        or usage.get("completion_tokens") or 0
    )
    return int(input_tokens) + int(output_tokens)


def _normalized_numbers(text: str) -> Counter:
    """Multiset of numeric literals, trailing zeros stripped (float:g)."""
    values: Counter = Counter()
    for raw in NUMBER_LITERAL_RE.findall(str(text or "")):
        try:
            values[f"{float(raw):g}"] += 1
        except ValueError:  # pragma: no cover - regex guarantees parseable
            continue
    return values


def _section_mismatches(section: str, en_text: str, zh_text: str) -> list:
    en_values = _normalized_numbers(en_text)
    zh_values = _normalized_numbers(zh_text)
    mismatches: list = []
    for value, count in (en_values - zh_values).items():
        mismatches.extend([f"{section}: missing {value}"] * count)
    for value, count in (zh_values - en_values).items():
        mismatches.extend([f"{section}: unexpected {value}"] * count)
    return mismatches


def _apply_material_names(zh_text: str) -> str:
    """Normalize any-casing material ids to their canonical display form."""
    translated = str(zh_text or "")
    for material_id, display_name in MATERIAL_DISPLAY_NAMES.items():
        translated = re.sub(
            re.escape(material_id),
            display_name,
            translated,
            flags=re.IGNORECASE,
        )
    return translated


def _translate_one(active_client, text: str) -> str:
    messages = [
        {
            "role": "system",
            "content": (
                "You are a precise scientific translator. Numbers, formulas "
                "and material names must stay exactly as written."
            ),
        },
        {"role": "user", "content": _TRANSLATION_PROMPT.format(text=text)},
    ]
    response = active_client.call(messages, max_tokens=4000)
    get_cost_tracker().record_qwen_usage(
        "plus", _qwen_total_tokens(response.get("_llm_usage"))
    )
    return str(response.get("content") or "").strip()


def translate_sections(
    abstract_en: str = "",
    conclusion_en: str = "",
    ledger: Any = None,
    *,
    client: Any = None,
) -> TranslationResult:
    """Translate abstract + conclusion to Chinese; never raises.

    ledger is accepted for signature compatibility with the architecture
    description; numeric ground truth is the English source itself, so the
    consistency check compares EN vs ZH literals directly.
    """
    try:
        active_client = client if client is not None else _default_plus_client()
        abstract_zh = None
        conclusion_zh = None
        mismatch_values: list = []
        if str(abstract_en or "").strip():
            abstract_zh = _apply_material_names(
                _translate_one(active_client, abstract_en)
            )
            mismatch_values.extend(
                _section_mismatches("abstract", abstract_en, abstract_zh)
            )
        if str(conclusion_en or "").strip():
            conclusion_zh = _apply_material_names(
                _translate_one(active_client, conclusion_en)
            )
            mismatch_values.extend(
                _section_mismatches("conclusion", conclusion_en, conclusion_zh)
            )
        if mismatch_values:
            warnings.warn(
                "TranslationNumberMismatchWarning: numeric drift detected: "
                + "; ".join(mismatch_values)
            )
        return TranslationResult(
            abstract_zh=abstract_zh,
            conclusion_zh=conclusion_zh,
            skipped=False,
            mismatch_values=mismatch_values,
        )
    except Exception as exc:  # API timeout / parse failure / anything else
        warnings.warn(f"TranslationSkippedWarning: translation skipped ({exc})")
        return TranslationResult(
            abstract_zh=None,
            conclusion_zh=None,
            skipped=True,
            mismatch_values=[],
        )


__all__ = [
    "MATERIAL_DISPLAY_NAMES",
    "TranslationResult",
    "translate_sections",
]
