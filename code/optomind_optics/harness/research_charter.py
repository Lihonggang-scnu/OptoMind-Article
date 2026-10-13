"""Original-request constraints shared by planning and solver admission."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
import re
from typing import Any, Mapping

_NUMBER = r"\d+(?:\.\d+)?"
_COUNTS = {"one": 1, "single": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_COUNT = r"(?:\d+|" + "|".join(_COUNTS) + r")"


def _count(value: str) -> int:
    return _COUNTS[value.lower()] if value.lower() in _COUNTS else int(value)


def build_research_charter(question: str, problem: Mapping[str, Any]) -> dict[str, Any]:
    from .task_compiler import _requested_illumination, _requested_materials, _wavelength_ranges
    from .strategy_planner import _declared_layer_count_range

    source = str(question)
    wavelengths = sorted([low * (1000.0 if unit in {"um", "μm", "µm"} else 1.0), high * (1000.0 if unit in {"um", "μm", "µm"} else 1.0)]
                         for low, high, unit in _wavelength_ranges(source))
    angles, polarizations = _requested_illumination(source)
    if re.search(r"\bnormal\s+incidence\b", source, re.I) and not angles:
        angles = [0.0]
    count_matches = list(re.finditer(rf"\b({_COUNT})[- ]layers?\b(?!\s+of\b)", source, re.I))
    counts = {_count(match.group(1)) for match in count_matches}
    bounds = _declared_layer_count_range({"design_variables": [source]})
    if bounds is None and len(counts) == 1:
        count = next(iter(counts))
        prefixes = [source[max(0, match.start() - 30):match.start()] for match in count_matches]
        if any(re.search(r"(?:at most|up to|no more than|maximum(?: of)?)\s*$", prefix, re.I) for prefix in prefixes):
            bounds = (1, count)
        elif not any(re.search(r"(?:at least|minimum(?: of)?)\s*$", prefix, re.I) for prefix in prefixes):
            bounds = (count, count)
    material_indices = {
        match.group(1).casefold(): float(match.group(2))
        for match in re.finditer(
            rf"\b([A-Z][a-zA-Z0-9]*)\s*\(\s*n\s*=\s*({_NUMBER})", source)
    }
    materials = sorted(_requested_materials(source))
    material_counts = {}
    each_match = re.search(rf"\b({_COUNT})\s+layers?\s+of\s+each\b", source, re.I)
    if each_match:
        material_counts = {name: _count(each_match.group(1)) for name in materials}
    for match in re.finditer(rf"\b({_COUNT})\s+layers?\s+of\s+([A-Z][a-zA-Z0-9]*)\b", source):
        material_counts[match.group(2).casefold()] = _count(match.group(1))
    if material_counts and bounds is None:
        total = sum(material_counts.values())
        bounds = (total, total)
    thickness_match = re.search(
        rf"(?:each\s+layer\s+thickness|layer\s+thickness(?:es)?)\b[^.;]{{0,70}}?"
        rf"(?:between\s+|in\s*\[)?({_NUMBER})\s*(?:nm\s*)?(?:and|to|[-–,])\s*({_NUMBER})\s*nm",
        source, re.I)
    thickness_bounds = list(map(float, thickness_match.groups())) if thickness_match else None
    step_match = re.search(
        rf"(?:sampled\s+(?:every|at)\s+({_NUMBER})\s*nm|(?:at\s+)?({_NUMBER})\s*nm\s+(?:spacing|steps?))",
        source, re.I)
    step = float(next(value for value in step_match.groups() if value is not None)) if step_match else None
    media = {}
    medium_material_names = []
    for name, pattern in (
        ("incident", r"(?:air\s+incidence|incident\s+(?:medium|air))"),
        ("exit", r"(?:glass\s+substrate|substrate(?:\s+glass)?)"),
    ):
        match = re.search(pattern + r"\s*\(([^)]*)\)", source, re.I)
        if match:
            for label in ("air", "glass"):
                if re.search(rf"\b{label}\b", match.group(0), re.I):
                    medium_material_names.append(label)
            index = re.search(rf"(?:\bn\s*=|(?:refractive\s+)?index\s*(?:of\s+|=\s*)?)\s*({_NUMBER})", match.group(1), re.I)
            extinction = re.search(rf"\bk\s*=\s*({_NUMBER})", match.group(1), re.I)
            if index:
                media[name] = {"constant_n": float(index.group(1)), "constant_k": float(extinction.group(1)) if extinction else 0.0}
    material_pattern = "(?:" + "|".join(re.escape(name) for name in materials) + ")" if materials else r"(?!)"
    exclusive_materials = bool(re.search(
        rf"\b(?:only|exclusively)\s+(?:(?:lossless|constant[- ]index|dielectric|isotropic)\s+)*{material_pattern}\b|\b{material_pattern}\s+only\b",
        source, re.I))
    lossless_layers = bool(re.search(
        rf"\blossless\s+(?:constant[- ]index\s+)?(?:layers?|coatings?|{material_pattern})\b",
        source, re.I))
    evaluation_cap = re.search(r"\b(?:at most|max(?:imum)?(?: of)?)\s+(\d+)\s+(?:(?:solver|forward)\s+)?evaluations\s+per\s+(?:research\s+)?round\b", source, re.I)
    seed_match = re.search(r"\bseed\s*(?:=|:)?\s*(\d+)\b", source, re.I)
    round_cap = re.search(rf"\b(?:at most|max(?:imum)?(?: of)?)\s+({_COUNT})\s+(?:executed\s+)?research\s+rounds\b", source, re.I)
    frozen = {
        "maximum_forward_evaluations_per_round": int(evaluation_cap.group(1)) if evaluation_cap else None,
        "optimizer_seed": int(seed_match.group(1)) if seed_match else None,
        "maximum_research_rounds": _count(round_cap.group(1)) if round_cap else None,
        "material_indices": material_indices,
        "material_counts": material_counts,
        "lossless_layers": lossless_layers,
        "thickness_bounds_nm": thickness_bounds,
        "media": media,
        "wavelength_intervals_nm": wavelengths,
        "wavelength_step_nm": step,
        "angles_deg": angles,
        "polarizations": polarizations,
    }
    return {
        "question": source,
        "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "wavelength_range_nm": [min(pair[0] for pair in wavelengths), max(pair[1] for pair in wavelengths)] if wavelengths else None,
        "angle_range_deg": [min(angles), max(angles)] if angles else None,
        "polarization": polarizations,
        "objectives": list(problem.get("preferred_behaviors") or []),
        "material_whitelist": materials if exclusive_materials else [],
        "bounding_medium_materials": medium_material_names,
        "layer_count_bounds": list(bounds) if bounds else None,
        "frozen_constraints": frozen,
    }


def _equal(left: Any, right: Any) -> bool:
    try:
        return math.isclose(float(left), float(right), rel_tol=1e-10, abs_tol=1e-10)
    except (TypeError, ValueError):
        return False


def check_frozen_task_constraints(task: Mapping[str, Any], charter: Mapping[str, Any]) -> None:
    constraints = charter.get("frozen_constraints") or {}
    if not constraints:
        return
    indices = constraints.get("material_indices") or {}
    expected_counts = constraints.get("material_counts") or {}
    whitelist = set(charter.get("material_whitelist") or [])
    cap = constraints.get("maximum_forward_evaluations_per_round")
    if cap is not None:
        actual_cap = (task.get("budget") or {}).get("maximum_forward_evaluations")
        if actual_cap is None or float(actual_cap) > cap:
            raise ValueError("CHARTER_DRIFT_ERROR: solver evaluation budget exceeds original per-round cap")
    for experiment in task.get("experiments") or []:
        raw = experiment.get("tmm_task") or {}
        simulation = raw.get("simulation") or raw
        seed = constraints.get("optimizer_seed")
        if seed is not None and experiment.get("mode") == "optimize" and (raw.get("optimizer") or {}).get("seed") != seed:
            raise ValueError("CHARTER_DRIFT_ERROR: optimizer seed differs from original request")
        stack = simulation.get("stack") or {}
        layers = stack.get("layers") or []
        counts = Counter()
        for layer in layers:
            name = str(layer.get("material") or "").casefold()
            matches = [key for key, index in indices.items() if _equal(layer.get("constant_n"), index)]
            if layer.get("constant_n") is not None and not name and len(matches) == 1:
                name = matches[0]
            if whitelist and name not in whitelist:
                raise ValueError("CHARTER_DRIFT_ERROR: layer material or constant_n outside original whitelist")
            if name in indices and not _equal(layer.get("constant_n"), indices[name]):
                raise ValueError("CHARTER_DRIFT_ERROR: constant-index layer differs from original request")
            counts[name] += 1
            if constraints.get("lossless_layers") and layer.get("constant_n") is not None and not _equal(layer.get("constant_k") or 0.0, 0.0):
                raise ValueError("CHARTER_DRIFT_ERROR: lossless layer acquired absorption")
            bounds = constraints.get("thickness_bounds_nm")
            if bounds:
                low, high = bounds
                thickness = layer.get("thickness_nm")
                if thickness is None or not low <= float(thickness) <= high:
                    raise ValueError("CHARTER_DRIFT_ERROR: layer thickness outside original bounds")
                if layer.get("optimizable", False) and (
                    not _equal(layer.get("min_thickness_nm"), low)
                    or not _equal(layer.get("max_thickness_nm"), high)
                ):
                    raise ValueError("CHARTER_DRIFT_ERROR: optimizer thickness bounds differ from original request")
        if expected_counts and counts != Counter(expected_counts):
            raise ValueError("CHARTER_DRIFT_ERROR: original material multiplicity changed")
        for name, expected in (constraints.get("media") or {}).items():
            actual = stack.get(name) or {}
            for key, value in expected.items():
                if value is not None and not _equal(actual.get(key, 0.0 if key == "constant_k" else None), value):
                    raise ValueError(f"CHARTER_DRIFT_ERROR: original {name} medium changed")
        illumination = simulation.get("illumination") or {}
        for key in ("angles_deg", "polarizations"):
            expected = constraints.get(key)
            if expected and set(illumination.get(key) or []) != set(expected):
                raise ValueError(f"CHARTER_DRIFT_ERROR: original {key} changed")
        step = constraints.get("wavelength_step_nm")
        intervals = constraints.get("wavelength_intervals_nm") or []
        if step and len(intervals) == 1:
            low, high = intervals[0]
            expected = [low + index * step for index in range(round((high - low) / step) + 1)]
            spectrum = simulation.get("spectrum") or {}
            values = spectrum.get("wavelengths_nm")
            if values is None:
                points = int(spectrum.get("points") or 0)
                if points > 1 and spectrum.get("start_nm") is not None and spectrum.get("stop_nm") is not None:
                    start, stop = float(spectrum["start_nm"]), float(spectrum["stop_nm"])
                    values = [start + index * (stop - start) / (points - 1) for index in range(points)]
            if not values or len(values) != len(expected) or any(not _equal(a, b) for a, b in zip(values, expected)):
                raise ValueError("CHARTER_DRIFT_ERROR: original wavelength sampling changed")


def charter_digest(charter: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(charter, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
