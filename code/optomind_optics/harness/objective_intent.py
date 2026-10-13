from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from .metric_catalog import METRIC_CATALOG
from .metric_reduction import requested_mean_reduction

_DIRECTION = re.compile(r"\b(maximi[sz]e|minimi[sz]e)\b|最大化|最小化", re.I)
_SENTENCE = re.compile(r"(?<!\d)\.(?!\d)|[;\n。；]")
_BAND = re.compile(
    r"(?<!\d)(\d+(?:\.\d+)?)\s*(nm|um|µm|μm)?\s*(?:[-–—]|to|至)\s*"
    r"(\d+(?:\.\d+)?)\s*(nm|um|µm|μm)\b", re.I,
)
_OBSERVABLES = {
    "R": re.compile(r"\b(?:reflectance|reflectivity|(?-i:R))\b|反射率", re.I),
    "T": re.compile(r"\b(?:transmittance|transmission|(?-i:T))\b|透射率", re.I),
    "A": re.compile(r"\b(?:absorptance|absorption|emissivity|(?-i:A))\b|吸收率|发射率", re.I),
}


def _bands(text: str) -> list[list[float]]:
    bands = []
    for match in _BAND.finditer(text):
        low, low_unit, high, high_unit = match.groups()
        prefix = text[max(0, match.start() - 60):match.start()]
        if re.search(r"thickness[^.;]*$|厚度[^。；]*$", prefix, re.I) and not re.search(r"wavelength|spectral|波长", prefix, re.I):
            continue
        low_scale = 1.0 if (low_unit or high_unit).lower() == "nm" else 1000.0
        high_scale = 1.0 if high_unit.lower() == "nm" else 1000.0
        band = sorted([float(low) * low_scale, float(high) * high_scale])
        if band not in bands:
            bands.append(band)
    return bands


def _conditions(text: str) -> dict[str, list[Any]]:
    angles = [float(value) for value in re.findall(
        r"(?:\bat\s+|\bincidence(?:[- ]angle)?\s*(?:of\s+|=\s*|:\s*)?)(\d+(?:\.\d+)?)\s*(?:degrees?\b|deg\b|°)",
        text, re.I,
    )]
    if re.search(r"\bnormal\s+incidence\b|正入射|垂直入射", text, re.I):
        angles.append(0.0)
    polarizations = re.findall(r"\b([sp])(?:[ -]polariz(?:ed|ation)|\s+polarization)\b", text, re.I)
    if re.search(r"\bunpolarized\b|非偏振", text, re.I):
        polarizations.append("unpolarized")
    return {
        "angle_deg": list(dict.fromkeys(angles)),
        "polarization": list(dict.fromkeys(value.lower() for value in polarizations)),
    }


def explicit_objective_intents(question: str) -> tuple[dict[str, Any], ...]:
    source = str(question or "")
    global_bands = _bands(source)
    global_conditions = _conditions(source)
    intents = []
    for sentence in _SENTENCE.split(source):
        directions = list(_DIRECTION.finditer(sentence))
        for index, direction in enumerate(directions):
            prefix = sentence[:direction.start()]
            if re.search(r"(?:\b(?:not|never)\s+(?:to\s+)?|不要|不应)\s*$", prefix, re.I):
                continue
            start = 0 if index == 0 else direction.start()
            end = directions[index + 1].start() if index + 1 < len(directions) else len(sentence)
            clause = re.split(r"\b(?:and|but)\s+(?:report|measure)\b", sentence[start:end], maxsplit=1, flags=re.I)[0]
            objective_text = clause[clause.find(direction.group()) + len(direction.group()):]
            if re.search(r"\b(?:variance|error|deviation|difference|uncertainty|fluctuation|mismatch|residual|contrast)\b|方差|误差|差异|不确定度", objective_text, re.I):
                continue
            named = [name for name, pattern in _OBSERVABLES.items() if pattern.search(objective_text)]
            if not named:
                continue
            bands = _bands(clause)
            if not bands and len(global_bands) == 1:
                bands = global_bands
            conditions = _conditions(clause)
            for key, values in global_conditions.items():
                if not conditions[key] and len(values) == 1:
                    conditions[key] = values
            statistic = (
                "band_worst_case" if re.search(r"worst[ -]case|最坏", clause, re.I)
                else "band_mean" if re.search(r"\b(?:mean|average)\b|平均", clause, re.I)
                else None
            )
            sense = "maximize" if direction.group().lower().startswith("max") or direction.group() == "最大化" else "minimize"
            for observable in named:
                for band in bands or [None]:
                    for angle in conditions["angle_deg"] or [None]:
                        for polarization in conditions["polarization"] or [None]:
                            region = {}
                            for key, value in (("wavelength_nm", band), ("angle_deg", angle), ("polarization", polarization)):
                                if value is not None:
                                    region[key] = value
                            metric = {"R": "mean_reflectance", "T": "mean_transmittance", "A": "mean_absorption"}[observable]
                            reduction = requested_mean_reduction(clause, metric, region) if statistic == "band_mean" else None
                            intent = {"observable": observable, "sense": sense, "statistic": statistic, "region": region, "mean_reduction": reduction}
                            if intent not in intents:
                                intents.append(intent)
    return tuple(intents)


def _same_identity(row: Mapping[str, Any], intent: Mapping[str, Any]) -> bool:
    spec = METRIC_CATALOG.get(str(row.get("metric") or ""))
    if spec is None or spec.observables != (intent["observable"],):
        return False
    if intent.get("statistic") and spec.reduction != intent["statistic"]:
        return False
    region = row.get("region") or {}
    for key, value in intent["region"].items():
        actual = region.get(key)
        if key == "wavelength_nm":
            if not isinstance(actual, (list, tuple)) or tuple(actual) != tuple(value):
                return False
        elif actual != value:
            return False
    return not intent.get("mean_reduction") or row.get("mean_reduction") == intent["mean_reduction"]


def objective_intent_errors(
    question: str,
    metrics: Sequence[Mapping[str, Any]],
    *,
    require_coverage: bool = True,
) -> tuple[str, ...]:
    errors = []
    for intent in explicit_objective_intents(question):
        matched = [row for row in metrics if _same_identity(row, intent)]
        label = f"{intent['sense']} {intent.get('statistic') or ''} {intent['observable']} {intent['region']}"
        if require_coverage and not any(row.get("sense") == intent["sense"] for row in matched):
            errors.append(f"original objective is missing, report-only, or its statistic/region/conditions changed: {label}")
        if any(row.get("sense") not in (intent["sense"], "report") for row in matched):
            errors.append(f"metric direction contradicts the original request: {label}")
    return tuple(errors)
