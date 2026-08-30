"""Deterministic, task-agnostic analysis of TMM forward observations.

The evaluator deliberately reports measurements rather than declaring that a
design has met a scientific target.  Target preferences are scored elsewhere;
physics acceptance remains the responsibility of the verifier.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, Iterable

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from optomind_research.runtime.artifact_store import atomic_write_json
from tmm_engine.workbench import ForwardSimulationResult


class SpectralFeature(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    channel: str
    observable: str
    feature_type: str
    wavelength_nm: float
    value: float
    prominence: float
    fwhm_nm: float | None = None
    q_estimate: float | None = None


class TMMAnalysisReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "tmm-analysis-report.v1"
    spectral_range_nm: tuple[float, float]
    spectral_points: int
    channel_statistics: Dict[str, Dict[str, Dict[str, float]]] = Field(default_factory=dict)
    energy_conservation: Dict[str, float] = Field(default_factory=dict)
    polarization_splitting: Dict[str, Dict[str, Dict[str, float]]] = Field(default_factory=dict)
    spectral_features: tuple[SpectralFeature, ...] = ()
    extra_summaries: Dict[str, Any] = Field(default_factory=dict)
    notes: tuple[str, ...] = (
        "Reported extrema and Q values are deterministic estimates, not physics admission gates.",
    )


def _real_vector(value: Any, expected: int) -> np.ndarray | None:
    array = np.asarray(value)
    if array.ndim != 1 or array.size != expected or np.iscomplexobj(array):
        return None
    try:
        result = np.asarray(array, dtype=np.float64)
    except (TypeError, ValueError):
        return None
    return result if np.all(np.isfinite(result)) else None


def _statistics(values: np.ndarray, wavelengths: np.ndarray) -> Dict[str, float]:
    minimum = int(np.argmin(values))
    maximum = int(np.argmax(values))
    return {
        "minimum": float(values[minimum]),
        "minimum_wavelength_nm": float(wavelengths[minimum]),
        "maximum": float(values[maximum]),
        "maximum_wavelength_nm": float(wavelengths[maximum]),
        "mean": float(np.mean(values)),
        "rms": float(np.sqrt(np.mean(values * values))),
        "trapezoidal_mean": float(np.trapezoid(values, wavelengths) / max(wavelengths[-1] - wavelengths[0], 1e-30)),
    }


def _half_width(
    wavelengths: np.ndarray,
    values: np.ndarray,
    index: int,
    *,
    peak: bool,
    baseline: float,
) -> float | None:
    value = float(values[index])
    level = baseline + 0.5 * (value - baseline)
    condition = (lambda x: x <= level) if peak else (lambda x: x >= level)
    left = index
    while left > 0 and not condition(float(values[left])):
        left -= 1
    right = index
    while right < values.size - 1 and not condition(float(values[right])):
        right += 1
    if left == 0 or right == values.size - 1 or right <= left:
        return None
    width = float(wavelengths[right] - wavelengths[left])
    return width if width > 0 else None


def _features(
    channel: str,
    observable: str,
    wavelengths: np.ndarray,
    values: np.ndarray,
    *,
    maximum_features: int = 4,
) -> Iterable[SpectralFeature]:
    if values.size < 3:
        return ()
    span = float(np.max(values) - np.min(values))
    if not math.isfinite(span) or span <= 1e-12:
        return ()
    # Suppress floating-point chatter in reporting.  This is relative to the
    # measured dynamic range and is never used as a physics/design gate.
    prominence_floor = max(1e-9, span * 1e-4)
    candidates: list[tuple[float, int, bool]] = []
    for index in range(1, values.size - 1):
        center = float(values[index])
        left = float(values[index - 1])
        right = float(values[index + 1])
        if center >= left and center > right:
            prominence = min(center - left, center - right)
            if prominence >= prominence_floor:
                candidates.append((prominence, index, True))
        elif center <= left and center < right:
            prominence = min(left - center, right - center)
            if prominence >= prominence_floor:
                candidates.append((prominence, index, False))
    selected = sorted(candidates, key=lambda item: (-item[0], item[1]))[:maximum_features]
    output: list[SpectralFeature] = []
    for prominence, index, peak in selected:
        baseline = float(np.min(values) if peak else np.max(values))
        width = _half_width(wavelengths, values, index, peak=peak, baseline=baseline)
        wavelength = float(wavelengths[index])
        output.append(
            SpectralFeature(
                channel=channel,
                observable=observable,
                feature_type="local_maximum" if peak else "local_minimum",
                wavelength_nm=wavelength,
                value=float(values[index]),
                prominence=float(prominence),
                fwhm_nm=width,
                q_estimate=None if width is None else float(wavelength / width),
            )
        )
    return output


def _angle_pol(channel: str) -> tuple[str, str] | None:
    parts = dict(part.split("=", 1) for part in channel.split("|") if "=" in part)
    angle = parts.get("angle")
    pol = parts.get("pol")
    return (angle, pol) if angle is not None and pol is not None else None


def _summarize_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _summarize_nested(item) for key, item in value.items()}
    array = np.asarray(value)
    if array.ndim >= 1 and array.size and np.issubdtype(array.dtype, np.number):
        if np.iscomplexobj(array):
            array = np.abs(array)
        array = np.asarray(array, dtype=np.float64)
        finite = array[np.isfinite(array)]
        if finite.size:
            return {
                "shape": list(array.shape),
                "minimum": float(np.min(finite)),
                "maximum": float(np.max(finite)),
                "mean": float(np.mean(finite)),
            }
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return {"type": type(value).__name__}


class TMMResultEvaluator:
    def evaluate(
        self,
        result: ForwardSimulationResult,
        *,
        work_dir: str | Path | None = None,
    ) -> TMMAnalysisReport:
        wavelengths = np.asarray(result.wavelengths_nm, dtype=np.float64)
        if wavelengths.ndim != 1 or wavelengths.size < 1 or not np.all(np.isfinite(wavelengths)):
            raise ValueError("result wavelengths must be a finite one-dimensional grid")
        channel_statistics: Dict[str, Dict[str, Dict[str, float]]] = {}
        energy_errors: list[float] = []
        spectral_features: list[SpectralFeature] = []
        usable: Dict[str, Dict[str, np.ndarray]] = {}
        for channel, payload in sorted(result.channels.items()):
            stats: Dict[str, Dict[str, float]] = {}
            vectors: Dict[str, np.ndarray] = {}
            for observable, raw in sorted(payload.items()):
                vector = _real_vector(raw, wavelengths.size)
                if vector is None:
                    continue
                vectors[observable] = vector
                stats[observable] = _statistics(vector, wavelengths)
                if observable in {"R", "T", "A", "E_system"}:
                    spectral_features.extend(
                        _features(channel, observable, wavelengths, vector)
                    )
            if all(name in vectors for name in ("R", "T", "A")):
                energy_errors.extend(
                    np.abs(vectors["R"] + vectors["T"] + vectors["A"] - 1.0).tolist()
                )
            channel_statistics[channel] = stats
            usable[channel] = vectors

        by_angle: Dict[str, Dict[str, Dict[str, np.ndarray]]] = {}
        for channel, vectors in usable.items():
            parsed = _angle_pol(channel)
            if parsed is None:
                continue
            angle, pol = parsed
            by_angle.setdefault(angle, {})[pol] = vectors
        splitting: Dict[str, Dict[str, Dict[str, float]]] = {}
        for angle, polarizations in sorted(by_angle.items()):
            if "s" not in polarizations or "p" not in polarizations:
                continue
            per_observable: Dict[str, Dict[str, float]] = {}
            common = set(polarizations["s"]) & set(polarizations["p"])
            for observable in sorted(common & {"R", "T", "A", "E_system"}):
                delta = np.abs(polarizations["s"][observable] - polarizations["p"][observable])
                index = int(np.argmax(delta))
                per_observable[observable] = {
                    "maximum_absolute_difference": float(delta[index]),
                    "wavelength_nm": float(wavelengths[index]),
                    "mean_absolute_difference": float(np.mean(delta)),
                }
            splitting[angle] = per_observable

        report = TMMAnalysisReport(
            spectral_range_nm=(float(wavelengths[0]), float(wavelengths[-1])),
            spectral_points=int(wavelengths.size),
            channel_statistics=channel_statistics,
            energy_conservation={
                "maximum_absolute_residual": float(max(energy_errors, default=0.0)),
                "mean_absolute_residual": float(np.mean(energy_errors)) if energy_errors else 0.0,
            },
            polarization_splitting=splitting,
            spectral_features=tuple(spectral_features),
            extra_summaries={str(key): _summarize_nested(value) for key, value in sorted(result.extras.items())},
        )
        if work_dir is not None:
            atomic_write_json(Path(work_dir) / "ANALYSIS_REPORT.json", report.model_dump(mode="json"))
        return report


__all__ = ["SpectralFeature", "TMMAnalysisReport", "TMMResultEvaluator"]
