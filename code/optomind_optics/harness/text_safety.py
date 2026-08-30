"""Repair JSON control-character damage in model-authored scientific text."""

from __future__ import annotations

import re
from typing import Any, List, Mapping


_JSON_LATEX_REPAIRS = {
    "\theta": r"\theta",
    "\text": r"\text",
    "\tau": r"\tau",
    "\times": r"\times",
    "\nu": r"\nu",
    "\rho": r"\rho",
    "\beta": r"\beta",
    "\frac": r"\frac",
}


def repair_scientific_text(value: str) -> str:
    """Restore common LaTeX commands that JSON decoded as controls."""

    text = str(value or "")
    for damaged, repaired in _JSON_LATEX_REPAIRS.items():
        text = text.replace(damaged, repaired)
    # No model-authored control character is allowed to reach a contract or a
    # publication artifact. Unknown controls are spacing, never instructions.
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text)
    return text


def repair_scientific_payload(value: Any) -> Any:
    if isinstance(value, str):
        return repair_scientific_text(value)
    if isinstance(value, Mapping):
        return {key: repair_scientific_payload(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(repair_scientific_payload(item) for item in value)
    if isinstance(value, list):
        return [repair_scientific_payload(item) for item in value]
    return value


def normalize_text_sequence(value: Any) -> Any:
    """Coerce a model-authored "list of statements" field into a real list.

    A bare string is the one malformation that must NOT be passed through to a
    ``list(...)`` call or a ``Tuple[str, ...]`` contract. ``list("abc")`` yields
    ``['a', 'b', 'c']``, so a single sentence silently becomes dozens of
    one-character "statements" -- and where the field is popped out before
    contract validation (pre-declarations) nothing rejects it, so the route is
    admitted carrying per-character noise as its scientific grounding.

    Only the scalar-string case is repaired, and only into a ONE-element list.
    Lists/tuples pass through untouched, so this is a no-op for every payload
    that was already well-formed. Anything else (dict, int, None) is returned
    unchanged for the contract to reject -- guessing a shape for those would be
    inventing content the model never wrote.
    """
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    return value


def normalize_text_sequence_list(value: Any) -> List[str]:
    """``normalize_text_sequence`` plus an unconditional list-of-str cast.

    For the pre-declaration sidecars, which are plain lists rather than
    contract-validated tuples and therefore have nothing downstream to reject a
    wrong shape.
    """
    normalized = normalize_text_sequence(value)
    if normalized is None:
        return []
    if isinstance(normalized, (list, tuple)):
        return [str(item) for item in normalized]
    # Not a sequence at all (dict/int/...): keep the content visible as one
    # entry rather than dropping it or letting list() explode it.
    return [str(normalized)]


__all__ = [
    "normalize_text_sequence",
    "normalize_text_sequence_list",
    "repair_scientific_payload",
    "repair_scientific_text",
]
