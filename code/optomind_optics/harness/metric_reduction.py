"""Preserve an explicitly requested mean without changing unspecified means."""

from __future__ import annotations

import re
from typing import Any, Mapping

from tmm_engine.spectral_reduction import MeanReduction

from .design_task import MEAN_OBJECTIVE_METRICS


_PATTERNS = {
    "sample_mean": re.compile(
        r"\b(?:arithmetic|sample|equal[ -]weight(?:ed)?)\s+(?:mean|average)s?\b|算术平均",
        re.IGNORECASE,
    ),
    "wavelength_integral_mean": re.compile(
        r"\b(?:wavelength[ -](?:integral|weighted)|trapezoidal)\s+(?:mean|average)s?\b",
        re.IGNORECASE,
    ),
}
_CLAUSE_BOUNDARY = re.compile(
    r"(?<!\d)[.;\n](?!\d)|(?:,\s*|\band\s+)"
    r"(?=(?:(?:use|the|compute|report|minimi[sz]e|maximi[sz]e)\s+)*"
    r"(?:arithmetic|sample|equal[ -]weight(?:ed)?|wavelength[ -](?:integral|weighted)|trapezoidal)"
    r"\s+(?:mean|average))",
    re.IGNORECASE,
)
_OBSERVABLES = {
    "R": re.compile(r"reflectance|reflectivity|反射率", re.IGNORECASE),
    "T": re.compile(r"transmittance|transmission|透射率", re.IGNORECASE),
    "A": re.compile(r"absorpt(?:ance|ion)|emissivity|吸收率|发射率", re.IGNORECASE),
}
_BAND = re.compile(
    r"(?<!\d)(\d+(?:\.\d+)?)\s*(nm|um|µm|μm)?\s*(?:-|–|—|to)\s*"
    r"(\d+(?:\.\d+)?)\s*(nm|um|µm|μm)\b", re.IGNORECASE,
)


def requested_mean_reduction(
    question: str, metric: str, region: Mapping[str, Any]
) -> MeanReduction | None:
    if metric not in MEAN_OBJECTIVE_METRICS:
        return None
    observable = (
        "R" if "reflectance" in metric else "T" if "transmittance" in metric
        else "A" if any(word in metric for word in ("absorption", "emissivity")) else None
    )
    expected = [
        tuple(float(value) for value in region[key])
        for key in ("wavelength_nm", "preferred_wavelength_nm", "suppressed_wavelength_nm")
        if isinstance(region.get(key), (list, tuple)) and len(region[key]) == 2
    ]
    found: set[str] = set()
    for clause in _CLAUSE_BOUNDARY.split(str(question or "")):
        reductions = {name for name, pattern in _PATTERNS.items() if pattern.search(clause)}
        if not reductions:
            continue
        named = {name for name, pattern in _OBSERVABLES.items() if pattern.search(clause)}
        if named and observable is not None and observable not in named:
            continue
        bands = []
        for match in _BAND.finditer(clause):
            lower, lower_unit, upper, upper_unit = match.groups()
            lo = float(lower) * (1.0 if (lower_unit or upper_unit).lower() == "nm" else 1000.0)
            hi = float(upper) * (1.0 if upper_unit.lower() == "nm" else 1000.0)
            bands.append((min(lo, hi), max(lo, hi)))
        if bands and expected and not any(band in expected for band in bands):
            continue
        found.update(reductions)
    if len(found) > 1:
        raise ValueError(f"conflicting explicit mean reductions for {metric}")
    return next(iter(found)) if found else None
