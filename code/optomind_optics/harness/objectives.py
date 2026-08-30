"""Continuous target scoring and deterministic robustness evaluation.

Scientific targets rank candidates.  They do not accept or reject physics.
"""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Sequence

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from optomind_research.runtime.artifact_store import atomic_write_json
from tmm_engine import OptimizationTask, SimulationTask, TMMWorkbench
from tmm_engine.workbench import ForwardSimulationResult

from .design_task import ObjectivePreference


_BAND_RTA_METRICS = frozenset(
    {
        "mean_reflectance",
        "band_reflectance",
        "mean_transmittance",
        "mean_absorption",
        "worst_case_reflectance",
        "worst_case_transmittance",
        "worst_case_absorption",
    }
)


class ObjectiveReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "tmm-objective-report.v1"
    aggregate_soft_score: float
    weighted_directional_loss: float
    target_attainment: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    admission_role: str = "ranking_only"


class RobustnessReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "tmm-robustness-report.v1"
    candidate_id: str
    perturbation_model: Dict[str, Any]
    nominal_soft_score: float
    sample_soft_scores: tuple[float, ...]
    sample_angle_offsets_deg: tuple[float, ...] = ()
    nominal_spectral_metrics: Dict[str, float] = Field(default_factory=dict)
    sample_spectral_metrics: tuple[Dict[str, float], ...] = ()
    spectral_metric_summary: Dict[str, Dict[str, float]] = Field(default_factory=dict)
    mean_soft_score: float
    worst_soft_score: float
    p10_soft_score: float
    robustness_score: float
    failed_simulations: int = 0
    sample_failure_reasons: tuple[str, ...] = ()
    admission_role: str = "ranking_only"


def _target_name(target: Any, index: int) -> str:
    return str(target.name or f"{target.observable}_{index:02d}")


def _continuous_score(observed: float, target: float, constraint: str, tolerance: float | None) -> float:
    scale = max(abs(float(target)), abs(float(tolerance or 0.0)), 0.05)
    if constraint == "at_least":
        return float(max(0.0, min(1.0, observed / (observed + scale))))
    if constraint == "at_most":
        return float(max(0.0, min(1.0, scale / (max(observed, 0.0) + scale))))
    return float(math.exp(-abs(observed - target) / scale))


def _channel_parts(channel: str) -> dict[str, str]:
    return {
        key: value
        for part in str(channel).split("|")
        if "=" in part
        for key, value in [part.split("=", 1)]
    }


def _selected_channels(
    result: ForwardSimulationResult,
    region: Dict[str, Any],
) -> list[tuple[str, Dict[str, Any]]]:
    angle_filter = region.get("angle_deg")
    if angle_filter is None:
        angles: set[float] | None = None
    elif isinstance(angle_filter, (list, tuple)):
        angles = {float(value) for value in angle_filter}
    else:
        angles = {float(angle_filter)}
    polarization = str(region.get("polarization") or "").casefold()
    selected: list[tuple[str, Dict[str, Any]]] = []
    for channel, payload in sorted(result.channels.items()):
        parts = _channel_parts(channel)
        if angles is not None:
            try:
                angle = float(parts.get("angle", "nan"))
            except ValueError:
                continue
            # Workbench channel labels use ``%g`` and therefore retain about six
            # significant digits.  Uncertainty sweeps create non-round angles,
            # so comparing a parsed label with the original float at 1e-9 can
            # reject the very channel that produced the label.  Prefer the same
            # canonical representation and keep a small numerical fallback for
            # externally produced ForwardSimulationResult objects.
            if not any(
                parts.get("angle", "") == f"{allowed:g}"
                or math.isclose(angle, allowed, rel_tol=5e-6, abs_tol=1e-8)
                for allowed in angles
            ):
                continue
        if polarization and parts.get("pol", "").casefold() != polarization:
            continue
        selected.append((channel, payload))
    if not selected:
        raise ValueError("objective channel selection matched no simulation channels")
    return selected


def _band_mean(
    wavelengths: np.ndarray,
    values: Any,
    interval: Sequence[float],
) -> float:
    lo, hi = float(interval[0]), float(interval[1])
    vector = np.asarray(values, dtype=np.float64)
    mask = (wavelengths >= lo) & (wavelengths <= hi)
    selected_wavelengths = wavelengths[mask]
    selected_values = vector[mask]
    if selected_values.size == 0 or not np.all(np.isfinite(selected_values)):
        raise ValueError(f"objective interval {lo:g}-{hi:g} nm has no finite samples")
    if selected_values.size == 1:
        return float(selected_values[0])
    covered = float(selected_wavelengths[-1] - selected_wavelengths[0])
    if covered <= 0:
        return float(np.mean(selected_values))
    return float(np.trapezoid(selected_values, selected_wavelengths) / covered)


def _band_extremum(
    wavelengths: np.ndarray,
    values: Any,
    interval: Sequence[float],
    constraint: str,
    target: float | None,
) -> float:
    """Reduce a band to the executable worst-case sample for a direction."""

    lo, hi = float(interval[0]), float(interval[1])
    vector = np.asarray(values, dtype=np.float64)
    mask = (wavelengths >= lo) & (wavelengths <= hi)
    selected = vector[mask]
    if selected.size == 0 or not np.all(np.isfinite(selected)):
        raise ValueError(f"objective interval {lo:g}-{hi:g} nm has no finite samples")
    if constraint == "at_least":
        return float(np.min(selected))
    if constraint == "at_most":
        return float(np.max(selected))
    if target is None:
        raise ValueError("match worst-case objectives require a target")
    return float(selected[np.argmax(np.abs(selected - float(target)))])


def _preference_observable(metric: str) -> str | None:
    if metric in {
        "mean_reflectance",
        "band_reflectance",
        "reflectance_stopband",
        "worst_case_reflectance",
    }:
        return "R"
    if metric in {"mean_transmittance", "worst_case_transmittance"}:
        return "T"
    if metric in {
        "mean_absorption",
        "worst_case_absorption",
        "mean_emissivity",
        "emissivity_spectrum",
        "band_emissivity_contrast",
    }:
        return "A"
    return None


def _constraint_from_sense(sense: str) -> str:
    if sense == "maximize":
        return "at_least"
    if sense == "minimize":
        return "at_most"
    return "match"


def _preference_score(preference: ObjectivePreference, observed: float) -> tuple[float | None, float]:
    if preference.sense == "report":
        return None, 0.0
    if preference.sense == "maximize":
        return float(np.clip(observed, 0.0, 1.0)), float((1.0 - observed) ** 2)
    if preference.sense == "minimize":
        return float(np.clip(1.0 - observed, 0.0, 1.0)), float(observed**2)
    assert preference.target is not None
    score = _continuous_score(observed, float(preference.target), "match", None)
    return score, float((observed - float(preference.target)) ** 2)


def evaluate_declared_objectives(
    preferences: Sequence[ObjectivePreference],
    result: ForwardSimulationResult,
) -> ObjectiveReport:
    """Materialize every declared forward-analysis objective deterministically.

    The language model selects only a bounded metric vocabulary.  This function
    is the executable counterpart of that vocabulary and fails closed rather
    than silently dropping a requested scientific output.
    """

    wavelengths = np.asarray(result.wavelengths_nm, dtype=np.float64)
    attainment: Dict[str, Dict[str, Any]] = {}
    weighted_score = 0.0
    weighted_loss = 0.0
    score_weight = 0.0
    for preference in preferences:
        channels = _selected_channels(result, preference.region)
        metric = preference.metric
        observable = _preference_observable(metric)
        worst_case = metric in {
            "worst_case_reflectance",
            "worst_case_transmittance",
            "worst_case_absorption",
        }
        constraint = _constraint_from_sense(preference.sense)
        channel_observations: Dict[str, Any] = {}
        scalar_values: list[float] = []
        if metric == "band_emissivity_contrast":
            preferred = preference.region["preferred_wavelength_nm"]
            suppressed = preference.region["suppressed_wavelength_nm"]
            for channel, payload in channels:
                high = _band_mean(wavelengths, payload["A"], preferred)
                low = _band_mean(wavelengths, payload["A"], suppressed)
                contrast = high - low
                scalar_values.append(contrast)
                channel_observations[channel] = {
                    "preferred_band_mean": high,
                    "suppressed_band_mean": low,
                    "difference": contrast,
                    "ratio": high / max(low, 1e-12),
                }
        elif observable is not None:
            interval = preference.region["wavelength_nm"]
            for channel, payload in channels:
                channel_value = (
                    _band_extremum(
                        wavelengths,
                        payload[observable],
                        interval,
                        constraint,
                        preference.target,
                    )
                    if worst_case
                    else _band_mean(wavelengths, payload[observable], interval)
                )
                scalar_values.append(channel_value)
                channel_observations[channel] = channel_value
        elif metric == "mixed_coherence_RTA" or metric == "opaque_stack_rta":
            interval = preference.region["wavelength_nm"]
            for channel, payload in channels:
                summaries = {
                    name: _band_mean(wavelengths, payload[name], interval)
                    for name in ("R", "T", "A")
                    if name in payload
                }
                if len(summaries) != 3:
                    raise ValueError(f"{metric} requires R, T, and A")
                channel_observations[channel] = summaries
            scalar_values = [
                float(values["A"])
                for values in channel_observations.values()
            ]
        elif metric in {
            "resonance_q_phase",
            "polarization_splitting",
            "phase_group_delay_gdd",
            "layer_absorption",
        }:
            # These are report-only compound observables.  Preserve the
            # available solver channels/extras and let ANALYSIS_REPORT provide
            # the detailed deterministic feature extraction.
            channel_observations = {
                channel: sorted(str(key) for key in payload)
                for channel, payload in channels
            }
            scalar_values = [0.0]
        else:  # pragma: no cover - guarded by ObjectivePreference validation
            raise ValueError(f"objective metric is not executable: {metric}")
        if worst_case:
            if constraint == "at_most":
                observed = float(np.max(scalar_values))
            elif constraint == "at_least":
                observed = float(np.min(scalar_values))
            else:
                assert preference.target is not None
                observed = float(
                    max(
                        scalar_values,
                        key=lambda value: abs(float(value) - float(preference.target)),
                    )
                )
        else:
            observed = float(np.mean(scalar_values))
        soft_score, directional_loss = _preference_score(preference, observed)
        row: Dict[str, Any] = {
            "metric": metric,
            "observed": observed,
            "target": preference.target,
            "sense": preference.sense,
            "weight": float(preference.weight),
            "soft_score": soft_score,
            "role": "report_only" if soft_score is None else "soft_scoring_objective",
            "region": dict(preference.region),
            "channel_observations": channel_observations,
        }
        if metric in _BAND_RTA_METRICS:
            row["aggregation"] = "worst_case" if worst_case else "mean"
        attainment[preference.objective_id] = row
        if soft_score is not None:
            weighted_score += float(preference.weight) * soft_score
            weighted_loss += float(preference.weight) * directional_loss
            score_weight += float(preference.weight)
    return ObjectiveReport(
        aggregate_soft_score=float(weighted_score / score_weight) if score_weight else 0.0,
        weighted_directional_loss=float(weighted_loss / score_weight) if score_weight else 0.0,
        target_attainment=attainment,
    )


def evaluate_optimization_objectives(
    task: OptimizationTask,
    result: ForwardSimulationResult,
) -> ObjectiveReport:
    wavelengths = np.asarray(result.wavelengths_nm, dtype=np.float64)
    weighted_score = 0.0
    weighted_loss = 0.0
    total_weight = 0.0
    attainment: Dict[str, Dict[str, Any]] = {}
    for index, target in enumerate(task.targets):
        values = np.asarray(
            result.channel(float(target.angle_deg), str(target.polarization))[target.observable],
            dtype=np.float64,
        )
        mask = (wavelengths >= float(target.wavelength_min_nm)) & (
            wavelengths <= float(target.wavelength_max_nm)
        )
        selected = values[mask]
        if selected.size == 0 or not np.all(np.isfinite(selected)):
            raise ValueError(f"Objective {_target_name(target, index)} has no finite samples")
        if target.aggregation == "worst_case":
            if target.constraint == "at_least":
                observed = float(np.min(selected))
            elif target.constraint == "at_most":
                observed = float(np.max(selected))
            else:
                observed = float(selected[np.argmax(np.abs(selected - float(target.target)))])
        else:
            observed = float(np.mean(selected))
        if target.constraint == "match":
            loss = float(np.mean((selected - float(target.target)) ** 2))
        elif target.constraint == "at_least":
            loss = float(np.mean((1.0 - selected) ** 2))
        else:
            loss = float(np.mean(selected**2))
        score = _continuous_score(observed, float(target.target), str(target.constraint), target.tolerance)
        name = _target_name(target, index)
        attainment[name] = {
            "observable": str(target.observable),
            "observed": observed,
            "target": float(target.target),
            "constraint": str(target.constraint),
            "aggregation": str(target.aggregation),
            "weight": float(target.weight),
            "tolerance": target.tolerance,
            "soft_score": score,
            "role": "soft_scoring_objective",
        }
        weighted_score += float(target.weight) * score
        weighted_loss += float(target.weight) * loss
        total_weight += float(target.weight)
    return ObjectiveReport(
        aggregate_soft_score=float(weighted_score / max(total_weight, 1e-30)),
        weighted_directional_loss=float(weighted_loss / max(total_weight, 1e-30)),
        target_attainment=attainment,
    )


def simulation_with_thicknesses(
    simulation: SimulationTask,
    thicknesses_nm: Sequence[float],
) -> SimulationTask:
    values = np.asarray(thicknesses_nm, dtype=np.float64)
    if values.shape != (len(simulation.stack.layers),):
        raise ValueError("thickness vector length does not match stack layers")
    layers = tuple(
        replace(layer, thickness_nm=float(value))
        for layer, value in zip(simulation.stack.layers, values)
    )
    return replace(simulation, stack=replace(simulation.stack, layers=layers))


def _passband_metrics(
    task: OptimizationTask,
    result: ForwardSimulationResult,
) -> Dict[str, float]:
    transmission_targets = [
        target
        for target in task.targets
        if target.observable == "T" and target.constraint in {"at_least", "match"}
    ]
    if not transmission_targets:
        return {}
    target = min(
        transmission_targets,
        key=lambda item: float(item.wavelength_max_nm) - float(item.wavelength_min_nm),
    )
    center = 0.5 * (
        float(target.wavelength_min_nm) + float(target.wavelength_max_nm)
    )
    selected = _selected_channels(
        result,
        {"angle_deg": target.angle_deg, "polarization": target.polarization},
    )
    if not selected:
        return {}
    _, payload = selected[0]
    values = np.asarray(payload.get("T"), dtype=np.float64)
    wavelengths = np.asarray(result.wavelengths_nm, dtype=np.float64)
    if values.shape != wavelengths.shape or not np.all(np.isfinite(values)):
        return {}
    center_t = float(np.interp(center, wavelengths, values))
    local_half_span = max(20.0, 0.12 * float(wavelengths[-1] - wavelengths[0]))
    local = np.where(np.abs(wavelengths - center) <= local_half_span)[0]
    if not local.size:
        return {"center_wavelength_nm": center, "center_transmittance": center_t}
    peak_index = int(local[np.argmax(values[local])])
    baseline = float(np.min(values))
    half_level = baseline + 0.5 * (float(values[peak_index]) - baseline)
    left = peak_index
    while left > 0 and float(values[left]) >= half_level:
        left -= 1
    right = peak_index
    while right < values.size - 1 and float(values[right]) >= half_level:
        right += 1
    width = (
        float(wavelengths[right] - wavelengths[left])
        if left > 0 and right < values.size - 1 and right > left
        else float("nan")
    )
    metrics = {
        "center_wavelength_nm": center,
        "center_transmittance": center_t,
        "passband_peak_wavelength_nm": float(wavelengths[peak_index]),
        "passband_peak_transmittance": float(values[peak_index]),
    }
    if np.isfinite(width) and width > 0:
        metrics["passband_fwhm_nm"] = width
    return metrics


def _target_domain_metrics(
    task: OptimizationTask,
    result: ForwardSimulationResult,
) -> Dict[str, float]:
    """Summarize R/T/A over the exact target channels and wavelength bands."""

    by_observable: dict[str, list[np.ndarray]] = {}
    wavelengths = np.asarray(result.wavelengths_nm, dtype=np.float64)
    for target in task.targets:
        selected = _selected_channels(
            result,
            {"angle_deg": target.angle_deg, "polarization": target.polarization},
        )
        mask = (wavelengths >= float(target.wavelength_min_nm)) & (
            wavelengths <= float(target.wavelength_max_nm)
        )
        if not np.any(mask):
            continue
        for _, payload in selected:
            values = np.asarray(payload.get(target.observable), dtype=np.float64)
            band = values[mask]
            if band.size and np.all(np.isfinite(band)):
                by_observable.setdefault(str(target.observable), []).append(band)
    metrics: Dict[str, float] = {}
    for observable, arrays in sorted(by_observable.items()):
        if not arrays:
            continue
        values = np.concatenate(arrays)
        metrics[f"target_domain_mean_{observable}"] = float(np.mean(values))
        metrics[f"target_domain_max_{observable}"] = float(np.max(values))
        metrics[f"target_domain_min_{observable}"] = float(np.min(values))
    return metrics


def _metric_summary(rows: Sequence[Dict[str, float]]) -> Dict[str, Dict[str, float]]:
    names = sorted({name for row in rows for name in row})
    output: Dict[str, Dict[str, float]] = {}
    for name in names:
        values = np.asarray(
            [float(row[name]) for row in rows if name in row and np.isfinite(row[name])],
            dtype=np.float64,
        )
        if not values.size:
            continue
        output[name] = {
            "mean": float(np.mean(values)),
            "standard_deviation": float(np.std(values)),
            "minimum": float(np.min(values)),
            "maximum": float(np.max(values)),
            "p05": float(np.quantile(values, 0.05)),
            "p95": float(np.quantile(values, 0.95)),
        }
    return output


class TMMRobustnessEvaluator:
    def __init__(self, workbench: TMMWorkbench) -> None:
        self.workbench = workbench

    def evaluate(
        self,
        task: OptimizationTask,
        thicknesses_nm: Sequence[float],
        *,
        candidate_id: str,
        sigma_nm: float,
        thickness_error_model: str = "absolute_normal",
        relative_fraction: float = 0.0,
        samples: int,
        random_seed: int,
        angle_perturbation_deg: float = 0.0,
        work_dir: str | Path | None = None,
    ) -> RobustnessReport:
        if (
            float(sigma_nm) < 0
            or float(angle_perturbation_deg) < 0
            or int(samples) < 1
        ):
            raise ValueError(
                "uncertainty magnitudes must be non-negative and samples must be positive"
            )
        nominal_values = np.asarray(thicknesses_nm, dtype=np.float64)
        nominal_simulation = simulation_with_thicknesses(task.simulation, nominal_values)
        nominal_forward = self.workbench.simulate(nominal_simulation)
        nominal = evaluate_optimization_objectives(
            task, nominal_forward
        ).aggregate_soft_score
        nominal_metrics = {
            **_passband_metrics(task, nominal_forward),
            **_target_domain_metrics(task, nominal_forward),
        }
        rng = np.random.default_rng(int(random_seed))
        scores: list[float] = []
        angle_offsets: list[float] = []
        spectral_metrics: list[Dict[str, float]] = []
        failed = 0
        failure_reasons: list[str] = []
        for _ in range(int(samples)):
            if thickness_error_model == "absolute_uniform":
                perturbed = nominal_values + rng.uniform(
                    -float(sigma_nm),
                    float(sigma_nm),
                    size=nominal_values.shape,
                )
            elif thickness_error_model == "relative_uniform":
                perturbation = rng.uniform(
                    -float(relative_fraction),
                    float(relative_fraction),
                    size=nominal_values.shape,
                )
                perturbed = nominal_values * (1.0 + perturbation)
            elif thickness_error_model == "relative_normal":
                perturbed = nominal_values * (
                    1.0
                    + rng.normal(
                        0.0, float(relative_fraction), size=nominal_values.shape
                    )
                )
            else:
                perturbed = nominal_values + rng.normal(
                    0.0, float(sigma_nm), size=nominal_values.shape
                )
            for index, layer in enumerate(task.simulation.stack.layers):
                lo, hi = layer.bounds_nm(task.optimizer.thickness_window_nm)
                perturbed[index] = np.clip(perturbed[index], lo, hi)
            angle_offset = (
                float(
                    rng.uniform(
                        -float(angle_perturbation_deg),
                        float(angle_perturbation_deg),
                    )
                )
                if float(angle_perturbation_deg) > 0
                else 0.0
            )
            angle_offsets.append(angle_offset)
            try:
                perturbed_simulation = simulation_with_thicknesses(
                    task.simulation, perturbed
                )
                shifted_angles = tuple(
                    float(np.clip(float(angle) + angle_offset, 0.0, 89.999999))
                    for angle in perturbed_simulation.illumination.angles_deg
                )
                perturbed_simulation = replace(
                    perturbed_simulation,
                    illumination=replace(
                        perturbed_simulation.illumination,
                        angles_deg=shifted_angles,
                    ),
                )
                angle_map = {
                    float(original): shifted
                    for original, shifted in zip(
                        task.simulation.illumination.angles_deg,
                        shifted_angles,
                    )
                }
                shifted_task = replace(
                    task,
                    simulation=perturbed_simulation,
                    targets=tuple(
                        replace(
                            target,
                            angle_deg=(
                                angle_map[float(target.angle_deg)]
                                if target.angle_deg is not None
                                else None
                            ),
                        )
                        for target in task.targets
                    ),
                )
                forward = self.workbench.simulate(perturbed_simulation)
                spectral_metrics.append(
                    {
                        **_passband_metrics(shifted_task, forward),
                        **_target_domain_metrics(shifted_task, forward),
                    }
                )
                scores.append(
                    evaluate_optimization_objectives(
                        shifted_task, forward
                    ).aggregate_soft_score
                )
            except Exception as exc:
                failed += 1
                failure_reasons.append(
                    f"sample={len(scores)} {type(exc).__name__}: {str(exc)[:300]}"
                )
                scores.append(0.0)
        values = np.asarray(scores, dtype=np.float64)
        report = RobustnessReport(
            candidate_id=str(candidate_id),
            perturbation_model={
                "distribution": str(thickness_error_model),
                "sigma_nm": float(sigma_nm),
                "relative_fraction": float(relative_fraction),
                "angle_distribution": "uniform_common_incidence_offset",
                "angle_perturbation_deg": float(angle_perturbation_deg),
                "samples": int(samples),
                "random_seed": int(random_seed),
                "bounds_policy": "clip_to_declared_layer_bounds",
            },
            nominal_soft_score=float(nominal),
            sample_soft_scores=tuple(float(value) for value in values),
            sample_angle_offsets_deg=tuple(angle_offsets),
            nominal_spectral_metrics=nominal_metrics,
            sample_spectral_metrics=tuple(spectral_metrics),
            spectral_metric_summary=_metric_summary(spectral_metrics),
            mean_soft_score=float(np.mean(values)),
            worst_soft_score=float(np.min(values)),
            p10_soft_score=float(np.quantile(values, 0.10)),
            robustness_score=float(np.mean(values)),
            failed_simulations=failed,
            sample_failure_reasons=tuple(failure_reasons),
        )
        if work_dir is not None:
            atomic_write_json(
                Path(work_dir) / "ROBUSTNESS.json",
                report.model_dump(mode="json"),
            )
        return report


__all__ = [
    "ObjectiveReport",
    "RobustnessReport",
    "TMMRobustnessEvaluator",
    "evaluate_declared_objectives",
    "evaluate_optimization_objectives",
    "simulation_with_thicknesses",
]
