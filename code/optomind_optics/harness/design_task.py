"""Immutable, TMM-only task contract for the optical experiment Harness."""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Dict, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tmm_engine.task_io import optimization_task_from_dict, simulation_task_from_dict


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")

SUPPORTED_OBJECTIVE_METRICS = frozenset(
    {
        "mean_reflectance",
        "band_reflectance",
        "mean_transmittance",
        "mean_absorption",
        "worst_case_reflectance",
        "worst_case_transmittance",
        "worst_case_absorption",
        "mean_emissivity",
        "reflectance_stopband",
        "resonance_q_phase",
        "mixed_coherence_RTA",
        "emissivity_spectrum",
        "band_emissivity_contrast",
        "polarization_splitting",
        "phase_group_delay_gdd",
        "layer_absorption",
        "opaque_stack_rta",
    }
)
_BAND_METRICS = SUPPORTED_OBJECTIVE_METRICS - {"band_emissivity_contrast"}
_REPORT_ONLY_METRICS = frozenset(
    {
        "resonance_q_phase",
        "mixed_coherence_RTA",
        "emissivity_spectrum",
        "polarization_splitting",
        "phase_group_delay_gdd",
        "layer_absorption",
        "opaque_stack_rta",
    }
)


class EngineMode(str, Enum):
    simulate = "simulate"
    optimize = "optimize"


class ObjectivePreference(BaseModel):
    """A ranking preference; never an admission or physics-validity condition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    objective_id: str
    metric: str
    sense: Literal["minimize", "maximize", "match", "report"]
    weight: float = 1.0
    target: Optional[float] = None
    region: Dict[str, Any] = Field(default_factory=dict)
    admission_role: Literal["score_only"] = "score_only"

    @field_validator("objective_id")
    @classmethod
    def _objective_id(cls, value: str) -> str:
        value = str(value).strip()
        if not _SAFE_ID.fullmatch(value):
            raise ValueError("objective_id must be a safe stable identifier")
        return value

    @field_validator("metric")
    @classmethod
    def _supported_metric(cls, value: str) -> str:
        metric = str(value or "").strip()
        if metric not in SUPPORTED_OBJECTIVE_METRICS:
            allowed = ", ".join(sorted(SUPPORTED_OBJECTIVE_METRICS))
            raise ValueError(f"unsupported objective metric {metric!r}; choose one of: {allowed}")
        return metric

    @field_validator("weight")
    @classmethod
    def _positive_weight(cls, value: float) -> float:
        if float(value) <= 0:
            raise ValueError("objective weight must be positive")
        return float(value)

    @model_validator(mode="after")
    def _validate_semantics(self) -> "ObjectivePreference":
        if self.sense == "match" and self.target is None:
            raise ValueError("match objectives require a target")
        if self.metric in _REPORT_ONLY_METRICS and self.sense != "report":
            raise ValueError(f"{self.metric} is a report-only metric")
        if self.metric in _BAND_METRICS:
            _validate_band(self.region.get("wavelength_nm"), "wavelength_nm")
        elif self.metric == "band_emissivity_contrast":
            _validate_band(
                self.region.get("preferred_wavelength_nm"),
                "preferred_wavelength_nm",
            )
            _validate_band(
                self.region.get("suppressed_wavelength_nm"),
                "suppressed_wavelength_nm",
            )
        return self


def _validate_band(value: Any, field_name: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{field_name} must be a two-value wavelength interval")
    lo, hi = float(value[0]), float(value[1])
    if not (0.0 < lo <= hi):
        raise ValueError(f"{field_name} must satisfy 0 < lower <= upper")
    return lo, hi


class PhysicsVerificationPolicy(BaseModel):
    """Numerical and physical truth checks only; no performance thresholds."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    require_spectral_convergence: bool = True
    require_independent_solver: bool = True
    check_passivity: Literal[True] = True
    check_energy_conservation: Literal[True] = True
    forbid_material_extrapolation: bool = True


class PortfolioPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    maximum_candidates: int = 8
    include_best_target_score: bool = True
    include_most_robust: bool = True
    include_simplest_fabrication: bool = True
    include_structurally_distinctive: bool = True
    include_pareto_front: bool = True

    @field_validator("maximum_candidates")
    @classmethod
    def _candidate_count(cls, value: int) -> int:
        if not 1 <= int(value) <= 32:
            raise ValueError("maximum_candidates must be in [1, 32]")
        return int(value)


class UncertaintyPolicy(BaseModel):
    """Reproducible perturbation scenarios; never a performance admission rule."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    thickness_sigma_nm: float = 1.0
    thickness_error_model: Literal[
        "absolute_normal", "absolute_uniform", "relative_uniform", "relative_normal"
    ] = "absolute_normal"
    thickness_relative_fraction: float = 0.0
    thickness_samples: int = 16
    angle_perturbation_deg: float = 0.0
    material_dataset_policy: Literal["resolved_only", "evaluate_all_eligible"] = "resolved_only"
    maximum_material_scenarios: int = 8
    random_seed: int = 42

    @field_validator(
        "thickness_sigma_nm", "thickness_relative_fraction", "angle_perturbation_deg"
    )
    @classmethod
    def _non_negative_uncertainty(cls, value: float) -> float:
        if float(value) < 0:
            raise ValueError("uncertainty magnitudes must be non-negative")
        return float(value)

    @model_validator(mode="after")
    def _relative_model_has_fraction(self) -> "UncertaintyPolicy":
        if self.thickness_error_model.startswith("relative") and not (
            0.0 < self.thickness_relative_fraction <= 1.0
        ):
            raise ValueError(
                "relative thickness error models require thickness_relative_fraction in (0, 1]"
            )
        return self

    @field_validator("thickness_samples")
    @classmethod
    def _sample_count(cls, value: int) -> int:
        if not 1 <= int(value) <= 10_000:
            raise ValueError("thickness_samples must be in [1, 10000]")
        return int(value)

    @field_validator("maximum_material_scenarios")
    @classmethod
    def _material_scenario_count(cls, value: int) -> int:
        if not 1 <= int(value) <= 32:
            raise ValueError("maximum_material_scenarios must be in [1, 32]")
        return int(value)


class HarnessBudgetPolicy(BaseModel):
    """Operational limits only; scientific target values are forbidden here."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    wall_time_seconds: float = 900.0
    maximum_forward_evaluations: int = 20_000
    maximum_optimizer_runs: int = 2
    maximum_qwen_calls: int = 2
    maximum_qwen_input_tokens: int = 12_000
    maximum_qwen_output_tokens: int = 2_000
    maximum_qwen_cost_cny: float = 2.0

    @field_validator("wall_time_seconds", "maximum_qwen_cost_cny")
    @classmethod
    def _positive_float_limit(cls, value: float) -> float:
        if float(value) <= 0:
            raise ValueError("budget limits must be positive")
        return float(value)

    @field_validator(
        "maximum_forward_evaluations",
        "maximum_optimizer_runs",
        "maximum_qwen_calls",
        "maximum_qwen_input_tokens",
        "maximum_qwen_output_tokens",
    )
    @classmethod
    def _positive_integer_limit(cls, value: int) -> int:
        if int(value) <= 0:
            raise ValueError("budget limits must be positive")
        return int(value)


class TMMExperimentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_id: str
    mode: EngineMode
    tmm_task: Dict[str, Any]
    objectives: Tuple[ObjectivePreference, ...] = ()
    tags: Tuple[str, ...] = ()

    @field_validator("experiment_id")
    @classmethod
    def _experiment_id(cls, value: str) -> str:
        value = str(value).strip()
        if not _SAFE_ID.fullmatch(value):
            raise ValueError("experiment_id must be a safe stable identifier")
        return value

    @model_validator(mode="after")
    def _validate_tmm_domain(self) -> "TMMExperimentSpec":
        if self.mode == EngineMode.simulate:
            task = simulation_task_from_dict(self.tmm_task)
        else:
            task = optimization_task_from_dict(self.tmm_task)
        simulation = task if self.mode == EngineMode.simulate else task.simulation
        physics = simulation.physics
        if physics.geometry_class != "layered_planar":
            raise ValueError("TMM Harness accepts layered_planar geometry only")
        if physics.material_class != "isotropic":
            raise ValueError("TMM Harness accepts isotropic materials only")
        if physics.excitation_class != "plane_wave" or physics.time_domain_required:
            raise ValueError("TMM Harness accepts frequency-domain plane-wave tasks only")
        spectral_lo = float(simulation.spectrum.start_nm)
        spectral_hi = float(simulation.spectrum.stop_nm)
        for objective in self.objectives:
            interval_names = (
                ("preferred_wavelength_nm", "suppressed_wavelength_nm")
                if objective.metric == "band_emissivity_contrast"
                else ("wavelength_nm",)
            )
            for interval_name in interval_names:
                lo, hi = _validate_band(objective.region[interval_name], interval_name)
                if lo < spectral_lo or hi > spectral_hi:
                    raise ValueError(
                        f"objective {objective.objective_id} interval {lo:g}-{hi:g} nm "
                        f"falls outside simulation grid {spectral_lo:g}-{spectral_hi:g} nm"
                    )
            angle_filter = objective.region.get("angle_deg")
            requested_angles = (
                []
                if angle_filter is None
                else list(angle_filter)
                if isinstance(angle_filter, (list, tuple))
                else [angle_filter]
            )
            for angle in requested_angles:
                if not any(
                    abs(float(angle) - float(available)) <= 1e-9
                    for available in simulation.illumination.angles_deg
                ):
                    raise ValueError(
                        f"objective {objective.objective_id} requests an unsimulated angle"
                    )
            polarization = objective.region.get("polarization")
            if polarization is not None and str(polarization) not in simulation.illumination.polarizations:
                raise ValueError(
                    f"objective {objective.objective_id} requests an unsimulated polarization"
                )
        return self


class OpticalDesignTask(BaseModel):
    """Top-level immutable contract after natural-language normalization."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["optical-design-task.tmm.v1"] = "optical-design-task.tmm.v1"
    task_id: str
    user_request_original: str
    normalized_request_english: str
    experiments: Tuple[TMMExperimentSpec, ...]
    verification: PhysicsVerificationPolicy = Field(default_factory=PhysicsVerificationPolicy)
    portfolio: PortfolioPolicy = Field(default_factory=PortfolioPolicy)
    uncertainty: UncertaintyPolicy = Field(default_factory=UncertaintyPolicy)
    budget: HarnessBudgetPolicy = Field(default_factory=HarnessBudgetPolicy)
    benchmark_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("task_id")
    @classmethod
    def _task_id(cls, value: str) -> str:
        value = str(value).strip()
        if not _SAFE_ID.fullmatch(value):
            raise ValueError("task_id must be a safe stable identifier")
        return value

    @field_validator("normalized_request_english")
    @classmethod
    def _english_request(cls, value: str) -> str:
        value = str(value).strip()
        if not value:
            raise ValueError("normalized_request_english is required")
        if any("\u4e00" <= char <= "\u9fff" for char in value):
            raise ValueError("normalized_request_english must not contain CJK text")
        return value

    @model_validator(mode="after")
    def _unique_experiments(self) -> "OpticalDesignTask":
        if not self.experiments:
            raise ValueError("at least one TMM experiment is required")
        ids = [item.experiment_id for item in self.experiments]
        if len(ids) != len(set(ids)):
            raise ValueError("experiment_id values must be unique")
        if self.verification.forbid_material_extrapolation:
            for experiment in self.experiments:
                parsed = (
                    simulation_task_from_dict(experiment.tmm_task)
                    if experiment.mode == EngineMode.simulate
                    else optimization_task_from_dict(experiment.tmm_task)
                )
                simulation = (
                    parsed
                    if experiment.mode == EngineMode.simulate
                    else parsed.simulation
                )
                if simulation.allow_material_extrapolation:
                    raise ValueError(
                        "forbid_material_extrapolation conflicts with an experiment "
                        "that enables material extrapolation"
                    )
        return self


__all__ = [
    "EngineMode",
    "HarnessBudgetPolicy",
    "ObjectivePreference",
    "OpticalDesignTask",
    "PhysicsVerificationPolicy",
    "PortfolioPolicy",
    "TMMExperimentSpec",
    "UncertaintyPolicy",
    "SUPPORTED_OBJECTIVE_METRICS",
]
